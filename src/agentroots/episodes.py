from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from rapidfuzz import fuzz
from rapidfuzz.process import extract

from .db import Database
from .private_fs import private_text_writer
from .retrieval import SemanticRetriever
from .security import scan_text, scan_value


def _fts_query(query: str) -> str:
    terms = [term.replace('"', '""') for term in query.split() if term.strip()]
    return " OR ".join(f'"{term}"' for term in terms[:24])


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunks(text: str, size: int = 6000, overlap: int = 600) -> list[str]:
    if len(text) <= size:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            boundary = text.rfind("\n", start + size // 2, end)
            if boundary > start:
                end = boundary
        out.append(text[start:end])
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return out


def _normalized_source_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    windows_path = bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith("//")
    return normalized.casefold() if windows_path else normalized


def _route_project(
    directory: str, default: str, aliases: dict[str, list[str]] | None
) -> str:
    """Route only from trusted session metadata, never conversation content."""
    if not aliases:
        return default
    normalized = _normalized_source_path(directory)
    matches: list[tuple[int, str]] = []
    for project, paths in aliases.items():
        for alias in paths:
            path_form = _normalized_source_path(alias)
            if path_form and (
                normalized == path_form or normalized.startswith(path_form + "/")
            ):
                matches.append((len(path_form), project))
    return max(matches)[1] if matches else default


def _source_uri(
    harness: str,
    project: str,
    host: str,
    session_id: str,
    message_id: str,
    chunk_index: int | None = None,
) -> str:
    scheme = re.sub(r"[^a-z0-9+.-]", "-", harness.casefold()) or "harness"
    components = "/".join(
        quote(value, safe="") for value in (host, project, session_id, message_id)
    )
    suffix = f"#{chunk_index}" if chunk_index is not None else ""
    return f"{scheme}://{components}{suffix}"


class EpisodeStore:
    """Searchable, untrusted conversation archive separate from governed records."""

    def __init__(self, db: Database):
        self.db = db
        self._semantic_cache: dict[str, tuple[tuple[int, str], list[dict[str, Any]]]] = {}
        self._cache_lock = threading.Lock()

    def store_live(
        self,
        *,
        project: str,
        harness: str,
        session_id: str,
        event_id: str,
        role: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Persist one sanitized live conversation delta as untrusted searchable history."""
        if not text.strip():
            return False
        scanned = scan_text(text)
        metadata_scan = scan_value(metadata or {})
        source_uri = _source_uri(harness, project, "live", session_id, event_id)
        digest = _hash(scanned.text)
        episode_id = "ep_" + uuid.uuid5(uuid.NAMESPACE_URL, source_uri).hex
        now = datetime.now(UTC)
        now_iso = now.isoformat()
        now_ms = int(now.timestamp() * 1000)
        with self.db.connect() as con:
            existing = con.execute(
                "SELECT project,content_hash FROM episodes WHERE source_uri=?", (source_uri,)
            ).fetchone()
            if existing and existing["project"] != project:
                raise ValueError("episode source URI is already bound to another project")
            if existing and existing["content_hash"] == digest:
                return False
            con.execute(
                """INSERT INTO episodes(
                id,project,source_uri,harness,session_id,parent_session_id,message_id,
                part_ids,role,text,content_hash,metadata,redacted,injection_risk,
                source_created_at,source_updated_at,imported_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_uri) DO UPDATE SET
                text=excluded.text,content_hash=excluded.content_hash,
                metadata=excluded.metadata,redacted=excluded.redacted,
                injection_risk=excluded.injection_risk,
                source_updated_at=excluded.source_updated_at,imported_at=excluded.imported_at""",
                (
                    episode_id, project, source_uri, harness, session_id, None, event_id,
                    "[]", role, scanned.text, digest, json.dumps(metadata_scan.value),
                    int(scanned.redacted), int(scanned.injection_risk),
                    now_ms, now_ms, now_iso,
                ),
            )
        with self._cache_lock:
            self._semantic_cache.pop(project, None)
        return True

    def import_history_jsonl(
        self,
        path: Path,
        project: str,
        project_aliases: dict[str, list[str]] | None = None,
        *,
        include_reasoning: bool = False,
    ) -> dict[str, Any]:
        imported = unchanged = 0
        now = datetime.now(UTC).isoformat()
        with self.db.connect() as con:
            # Normalized histories can be much larger than the governed state. Stage the
            # JSONL stream in file-backed SQLite temp tables so Python memory is bounded by
            # one message and its parts instead of the complete conversation archive.
            con.execute("PRAGMA temp_store=FILE")
            con.executescript(
                """
                CREATE TEMP TABLE history_import_sessions(
                  id TEXT PRIMARY KEY, row_order INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE TEMP TABLE history_import_messages(
                  id TEXT PRIMARY KEY, row_order INTEGER NOT NULL, payload TEXT NOT NULL
                );
                CREATE TEMP TABLE history_import_parts(
                  row_order INTEGER PRIMARY KEY, message_id TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE INDEX history_import_parts_message
                  ON history_import_parts(message_id, row_order);
                """
            )
            with path.open(encoding="utf-8-sig") as stream:
                for row_order, line in enumerate(stream):
                    if not line.strip():
                        continue
                    row = json.loads(line)
                    kind = row.get("kind")
                    payload = json.dumps(row)
                    if kind == "session":
                        con.execute(
                            """INSERT INTO history_import_sessions(id,row_order,payload)
                            VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                            (row["id"], row_order, payload),
                        )
                    elif kind == "message":
                        con.execute(
                            """INSERT INTO history_import_messages(id,row_order,payload)
                            VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload""",
                            (row["id"], row_order, payload),
                        )
                    elif kind == "part":
                        con.execute(
                            """INSERT INTO history_import_parts(row_order,message_id,payload)
                            VALUES(?,?,?)""",
                            (row_order, row["message_id"], payload),
                        )

            session_count = int(
                con.execute("SELECT count(*) FROM history_import_sessions").fetchone()[0]
            )
            message_rows = con.execute(
                "SELECT payload FROM history_import_messages ORDER BY row_order"
            )
            for message_row in message_rows:
                message = json.loads(message_row["payload"])
                message_id = str(message["id"])
                parsed_message = json.loads(message["data"])
                role = str(parsed_message.get("role", "unknown"))
                source_parts = [
                    json.loads(row["payload"])
                    for row in con.execute(
                        "SELECT payload FROM history_import_parts WHERE message_id=?",
                        (message_id,),
                    )
                ]
                source_parts.sort(
                    key=lambda row: (row.get("time_created") or 0, row["id"])
                )
                texts: list[str] = []
                part_ids: list[str] = []
                tool_names: list[str] = []
                for part in source_parts:
                    data = json.loads(part["data"])
                    part_type = data.get("type")
                    allowed_parts = {"text", "reasoning"} if include_reasoning else {"text"}
                    if part_type in allowed_parts and data.get("text"):
                        label = "message" if part_type == "text" else "reasoning"
                        texts.append(f"[{label}]\n{data['text']}")
                        part_ids.append(part["id"])
                    elif part_type == "tool":
                        tool_names.append(str(data.get("tool", "unknown")))
                combined = "\n\n".join(texts).strip()
                if not combined:
                    continue
                session_row = con.execute(
                    "SELECT payload FROM history_import_sessions WHERE id=?",
                    (message["session_id"],),
                ).fetchone()
                if session_row is None:
                    raise KeyError(message["session_id"])
                session = json.loads(session_row["payload"])
                harness = str(session.get("source_harness", "opencode"))
                routed_project = _route_project(
                    str(session.get("directory", "")), project, project_aliases
                )
                chunks = _chunks(combined)
                for index, chunk in enumerate(chunks):
                    scanned = scan_text(chunk)
                    source_uri = _source_uri(
                        harness,
                        routed_project,
                        str(session.get("source_host", "local")),
                        str(message["session_id"]),
                        message_id,
                        index,
                    )
                    digest = _hash(chunk)
                    episode_id = "ep_" + uuid.uuid5(uuid.NAMESPACE_URL, source_uri).hex
                    metadata = {
                        "session_title": session.get("title", ""),
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "tool_names": sorted(set(tool_names)),
                        "source_content_hash": digest,
                        "source_path_hash": _hash(str(session.get("source_path", ""))),
                        "source_directory_hash": _hash(str(session.get("directory", ""))),
                        "source_model": session.get("model", ""),
                        "reasoning_included": include_reasoning,
                    }
                    metadata_scan = scan_value(metadata)
                    existing = con.execute(
                        "SELECT project,content_hash FROM episodes WHERE source_uri=?", (source_uri,)
                    ).fetchone()
                    if existing and existing["project"] != routed_project:
                        raise ValueError("episode source URI is already bound to another project")
                    if existing and existing["content_hash"] == digest:
                        unchanged += 1
                        continue
                    duplicate = con.execute(
                        "SELECT id,metadata FROM episodes WHERE project=? AND content_hash=? LIMIT 1",
                        (routed_project, digest),
                    ).fetchone()
                    if duplicate:
                        duplicate_metadata = json.loads(duplicate["metadata"])
                        aliases = set(duplicate_metadata.get("source_aliases", []))
                        aliases.add(source_uri)
                        duplicate_metadata["source_aliases"] = sorted(aliases)
                        con.execute(
                            "UPDATE episodes SET metadata=?,imported_at=? WHERE id=?",
                            (json.dumps(duplicate_metadata), now, duplicate["id"]),
                        )
                        unchanged += 1
                        continue
                    con.execute(
                        """INSERT INTO episodes(
                        id,project,source_uri,harness,session_id,parent_session_id,message_id,
                        part_ids,role,text,content_hash,metadata,redacted,injection_risk,
                        source_created_at,source_updated_at,imported_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source_uri) DO UPDATE SET
                        text=excluded.text,content_hash=excluded.content_hash,
                        metadata=excluded.metadata,redacted=excluded.redacted,
                        injection_risk=excluded.injection_risk,
                        source_updated_at=excluded.source_updated_at,imported_at=excluded.imported_at""",
                        (
                            episode_id, routed_project, source_uri, harness, message["session_id"],
                            session.get("parent_id"), message_id, json.dumps(part_ids), role,
                            scanned.text, digest, json.dumps(metadata_scan.value), int(scanned.redacted),
                            int(scanned.injection_risk), message.get("time_created"),
                            message.get("time_updated"), now,
                        ),
                    )
                    imported += 1
        return {"imported": imported, "unchanged": unchanged, "sessions": session_count}

    def import_opencode_jsonl(self, path: Path, project: str) -> dict[str, Any]:
        """Backward-compatible alias for the normalized history importer."""
        return self.import_history_jsonl(path, project)

    def search(self, project: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
        if not query.strip():
            return []
        with self.db.connect() as con:
            try:
                rows = con.execute(
                    """SELECT e.* FROM episodes_fts f JOIN episodes e ON e.rowid=f.rowid
                    WHERE episodes_fts MATCH ? AND e.project=?
                    ORDER BY bm25(episodes_fts) LIMIT ?""",
                    (_fts_query(query), project, limit * 3),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = []
            if not rows:
                candidates = con.execute(
                    "SELECT * FROM episodes WHERE project=? "
                    "ORDER BY source_created_at DESC,id LIMIT 2000",
                    (project,),
                ).fetchall()
                choices = {index: row["text"] for index, row in enumerate(candidates)}
                ranked = [
                    candidates[index]
                    for _, _, index in extract(
                        query,
                        choices,
                        scorer=fuzz.token_set_ratio,
                        limit=limit,
                        score_cutoff=35,
                    )
                ]
                known = {row["id"] for row in rows}
                rows.extend(row for row in ranked if row["id"] not in known)
        out = []
        for row in rows[:limit]:
            item = dict(row)
            text = item.pop("text")
            item["snippet"] = text[:1200]
            item["part_ids"] = json.loads(item["part_ids"])
            item["metadata"] = json.loads(item["metadata"])
            item["untrusted"] = True
            out.append(item)
        return out

    def semantic_search(
        self, project: str, query: str, retriever: SemanticRetriever, limit: int = 8
    ) -> list[dict[str, Any]]:
        """Rank conversation episodes with the same resident BGE model as records."""
        if not query.strip():
            return []
        with self.db.connect() as con:
            state = con.execute(
                "SELECT count(*),COALESCE(max(imported_at),'') FROM episodes WHERE project=?",
                (project,),
            ).fetchone()
            signature = (int(state[0]), str(state[1]))
            cached = self._semantic_cache.get(project)
            if cached is None or cached[0] != signature:
                rows = con.execute(
                    "SELECT * FROM episodes WHERE project=? ORDER BY source_created_at DESC,id",
                    (project,),
                ).fetchall()
                candidates = []
                for row in rows:
                    item = dict(row)
                    candidates.append(
                        {
                            **item,
                            "revision": 1,
                            "type": "episode",
                            "status": "untrusted",
                            "title": json.loads(item["metadata"]).get(
                                "session_title", "conversation"
                            ),
                            "body": item["text"],
                        }
                    )
                with self._cache_lock:
                    self._semantic_cache[project] = (signature, candidates)
            else:
                candidates = cached[1]
        ranked = retriever.rank(candidates, query, limit)
        if ranked is None:
            return []
        out = []
        for item in ranked:
            result = dict(item)
            text = result.pop("text")
            result["snippet"] = text[:1200]
            result["part_ids"] = json.loads(result["part_ids"])
            result["metadata"] = json.loads(result["metadata"])
            result["untrusted"] = True
            for key in ("revision", "type", "status", "title", "body"):
                result.pop(key, None)
            out.append(result)
        return out


def write_opencode_export(
    source_db: Path,
    root_session_id: str,
    output: Path,
    source_host: str = "local",
    *,
    include_reasoning: bool = False,
) -> dict[str, int]:
    """Export one root session tree from a local OpenCode DB opened read-only."""
    uri = f"file:{source_db.as_posix()}?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    root = con.execute("SELECT * FROM session WHERE id=?", (root_session_id,)).fetchone()
    if root is None:
        raise KeyError(root_session_id)
    directory = root["directory"]
    ids = {root_session_id}
    while True:
        placeholders = ",".join("?" * len(ids))
        rows = con.execute(
            f"SELECT id FROM session WHERE directory=? AND parent_id IN ({placeholders})",
            (directory, *ids),
        ).fetchall()
        new_ids = {row["id"] for row in rows} - ids
        if not new_ids:
            break
        ids |= new_ids
    placeholders = ",".join("?" * len(ids))
    counts = {"sessions": 0, "messages": 0, "parts": 0}
    with private_text_writer(output) as stream:
        for row in con.execute(
            f"SELECT * FROM session WHERE id IN ({placeholders}) ORDER BY time_created,id",
            tuple(ids),
        ):
            item = dict(row)
            keep = {k: item.get(k) for k in ("id", "parent_id", "title", "time_created", "time_updated")}
            stream.write(json.dumps({"kind": "session", "source_host": source_host, **keep}) + "\n")
            counts["sessions"] += 1
        for table in ("message", "part"):
            for row in con.execute(
                f"SELECT * FROM {table} WHERE session_id IN ({placeholders}) ORDER BY time_created,id",
                tuple(ids),
            ):
                item = dict(row)
                if table == "part":
                    data = json.loads(item["data"])
                    part_type = data.get("type")
                    if part_type == "tool":
                        data = {"type": "tool", "tool": data.get("tool", "unknown")}
                    elif part_type != "text" and not (
                        part_type == "reasoning" and include_reasoning
                    ):
                        continue
                    item["data"] = json.dumps(data)
                stream.write(json.dumps({"kind": table, **item}) + "\n")
                counts[table + "s"] += 1
    con.close()
    return counts
