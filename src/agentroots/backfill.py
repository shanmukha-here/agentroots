from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .private_fs import private_text_writer
from .project_identity import resolve_project_id
from .security import scan_text


@dataclass(frozen=True, slots=True)
class ConversationSource:
    harness: str
    source: str
    session_id: str
    directory: str
    title: str
    source_host: str
    project_id: str
    model: str = ""
    parent_session_id: str | None = None


def _normalized_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    windows_path = bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith("//")
    return normalized.casefold() if windows_path else normalized


def _selected(directory: str, includes: Iterable[str], excludes: Iterable[str]) -> bool:
    current = _normalized_path(directory)
    denied = [_normalized_path(item) for item in excludes]
    if any(current == item or current.startswith(item + "/") for item in denied):
        return False
    allowed = [_normalized_path(item) for item in includes]
    return not allowed or any(current == item or current.startswith(item + "/") for item in allowed)


def project_identity(directory: str) -> str:
    """Backward-compatible public wrapper for the canonical resolver."""
    return resolve_project_id(directory)


def discover_codex(
    sessions_root: Path,
    *,
    source_host: str = "local",
    includes: Iterable[str] = (),
    excludes: Iterable[str] = (),
) -> list[ConversationSource]:
    sources: list[ConversationSource] = []
    if not sessions_root.exists():
        return sources
    for path in sorted(sessions_root.rglob("*.jsonl")):
        try:
            with path.open(encoding="utf-8", errors="ignore") as stream:
                first = json.loads(next(stream))
        except (OSError, StopIteration, json.JSONDecodeError):
            continue
        if first.get("type") != "session_meta":
            continue
        payload = first.get("payload", {})
        directory = str(payload.get("cwd", ""))
        if not directory or not _selected(directory, includes, excludes):
            continue
        sources.append(
            ConversationSource(
                harness="codex",
                source=str(path),
                session_id=str(payload.get("id", path.stem)),
                directory=directory,
                title="",
                source_host=source_host,
                project_id=project_identity(directory),
                model=str(payload.get("model", "")),
            )
        )
    return sources


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def discover_opencode(
    source_db: Path,
    *,
    source_host: str = "local",
    includes: Iterable[str] = (),
    excludes: Iterable[str] = (),
) -> list[ConversationSource]:
    if not source_db.exists():
        return []
    con = _readonly_sqlite(source_db)
    try:
        rows = con.execute(
            "SELECT id,parent_id,directory,title FROM session ORDER BY time_created,id"
        ).fetchall()
    finally:
        con.close()
    return [
        ConversationSource(
            harness="opencode",
            source=str(source_db),
            session_id=str(row["id"]),
            parent_session_id=row["parent_id"],
            directory=str(row["directory"]),
            title=str(row["title"] or ""),
            source_host=source_host,
            project_id=project_identity(str(row["directory"])),
        )
        for row in rows
        if row["directory"] and _selected(str(row["directory"]), includes, excludes)
    ]


def discovery_manifest(sources: Iterable[ConversationSource]) -> dict[str, Any]:
    items = list(sources)
    projects: dict[str, dict[str, Any]] = {}
    for source in items:
        project = projects.setdefault(
            source.project_id,
            {"project_id": source.project_id, "directories": set(), "harnesses": Counter(),
             "sessions": 0},
        )
        project["directories"].add(source.directory)
        project["harnesses"][source.harness] += 1
        project["sessions"] += 1
    rendered = []
    for project in projects.values():
        rendered.append({
            **project,
            "directories": sorted(project["directories"]),
            "harnesses": dict(sorted(project["harnesses"].items())),
        })
    return {
        "approved": False,
        "read_only_sources": True,
        "projects": sorted(rendered, key=lambda item: item["project_id"]),
        "sources": [asdict(item) for item in items],
    }


def _message_text(payload: dict[str, Any]) -> str:
    content = payload.get("content", [])
    if isinstance(content, str):
        return content
    texts = []
    if isinstance(content, list):
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"input_text", "output_text", "text"} and item.get("text"):
                texts.append(str(item["text"]))
    return "\n".join(texts).strip()


def export_codex(source: ConversationSource, stream: Any) -> dict[str, int]:
    counts = {"sessions": 1, "messages": 0, "parts": 0}
    stream.write(json.dumps({
        "kind": "session", "id": source.session_id, "parent_id": None,
        "title": source.title, "source_host": source.source_host,
        "source_harness": "codex", "source_path": source.source,
        "directory": source.directory, "model": source.model,
    }) + "\n")
    with Path(source.source).open(encoding="utf-8", errors="ignore") as source_stream:
        for index, line in enumerate(source_stream):
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            payload = row.get("payload", {})
            if row.get("type") != "response_item" or payload.get("type") != "message":
                continue
            role = str(payload.get("role", ""))
            if role not in {"user", "assistant"}:
                continue
            text = _message_text(payload)
            if not text:
                continue
            text = scan_text(text).text
            message_id = f"{source.session_id}:m{index}"
            timestamp = row.get("timestamp")
            stream.write(json.dumps({
                "kind": "message", "id": message_id, "session_id": source.session_id,
                "time_created": timestamp, "time_updated": timestamp,
                "data": json.dumps({"role": role}),
            }) + "\n")
            stream.write(json.dumps({
                "kind": "part", "id": f"{message_id}:p0", "message_id": message_id,
                "session_id": source.session_id, "time_created": timestamp,
                "data": json.dumps({"type": "text", "text": text}),
            }) + "\n")
            counts["messages"] += 1
            counts["parts"] += 1
    return counts


def export_sources(
    sources: Iterable[ConversationSource],
    output: Path,
    *,
    approved: bool,
    include_reasoning: bool = False,
) -> dict[str, int]:
    if not approved:
        raise PermissionError("conversation backfill requires explicit approval")
    selected = list(sources)
    totals: Counter[str] = Counter()
    with private_text_writer(output) as stream:
        codex = [item for item in selected if item.harness == "codex"]
        for source in codex:
            totals.update(export_codex(source, stream))
        opencode_groups: dict[tuple[str, str], list[ConversationSource]] = {}
        for source in selected:
            if source.harness == "opencode":
                opencode_groups.setdefault((source.source, source.source_host), []).append(source)
        for (database, source_host), group in opencode_groups.items():
            con = _readonly_sqlite(Path(database))
            try:
                ids = [item.session_id for item in group]
                placeholders = ",".join("?" * len(ids))
                sessions = con.execute(
                    f"SELECT * FROM session WHERE id IN ({placeholders}) ORDER BY time_created,id",
                    ids,
                ).fetchall()
                for row in sessions:
                    item = dict(row)
                    stream.write(json.dumps({
                        "kind": "session", "source_host": source_host,
                        "source_harness": "opencode", "source_path": database,
                        **{key: item.get(key) for key in (
                            "id", "parent_id", "title", "directory", "time_created", "time_updated"
                        )},
                    }) + "\n")
                    totals["sessions"] += 1
                for table in ("message", "part"):
                    for row in con.execute(
                        f"SELECT * FROM {table} WHERE session_id IN ({placeholders}) "
                        "ORDER BY time_created,id", ids,
                    ):
                        item = dict(row)
                        if table == "part":
                            data = json.loads(item["data"])
                            if data.get("type") == "tool":
                                data = {"type": "tool", "tool": data.get("tool", "unknown")}
                            elif data.get("type") == "reasoning" and not include_reasoning:
                                continue
                            elif data.get("type") in {"text", "reasoning"} and data.get("text"):
                                data["text"] = scan_text(str(data["text"])).text
                            else:
                                continue
                            item["data"] = json.dumps(data)
                        stream.write(json.dumps({"kind": table, **item}) + "\n")
                        totals[table + "s"] += 1
            finally:
                con.close()
    return dict(totals)
