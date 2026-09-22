from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from agentroots.db import Database
from agentroots.episodes import EpisodeStore, write_opencode_export


def test_live_episode_is_searchable_across_sessions(tmp_path: Path) -> None:
    store = EpisodeStore(Database(tmp_path / "live.sqlite3"))
    values = {
        "project": "paper",
        "harness": "codex",
        "session_id": "old-session",
        "event_id": "message-one",
        "role": "user",
        "text": "The cosine loss experiment failed and reduced rare-class recall.",
    }
    assert store.store_live(**values)
    assert not store.store_live(**values)
    results = store.search("paper", "failed experiment", limit=3)
    assert len(results) == 1
    assert results[0]["session_id"] == "old-session"
    assert results[0]["untrusted"] is True


def test_live_episode_uri_is_project_scoped_and_rejects_conflicting_owner(
    tmp_path: Path,
) -> None:
    store = EpisodeStore(Database(tmp_path / "live.sqlite3"))
    shared = {
        "harness": "codex",
        "session_id": "session",
        "event_id": "event",
        "role": "user",
        "text": "A durable project observation.",
    }
    assert store.store_live(project="alpha", **shared)
    assert store.store_live(project="beta", **shared)
    with store.db.connect() as con:
        rows = con.execute("SELECT project,source_uri FROM episodes ORDER BY project").fetchall()
        alpha_uri = rows[0]["source_uri"]
        con.execute("UPDATE episodes SET project='wrong-owner' WHERE source_uri=?", (alpha_uri,))
    assert rows[0]["source_uri"] != rows[1]["source_uri"]
    with pytest.raises(ValueError, match="another project"):
        store.store_live(project="alpha", **{**shared, "text": "Changed observation."})


def _source_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE session(id TEXT, parent_id TEXT, directory TEXT, title TEXT,
          time_created INTEGER, time_updated INTEGER);
        CREATE TABLE message(id TEXT, session_id TEXT, time_created INTEGER,
          time_updated INTEGER, data TEXT);
        CREATE TABLE part(id TEXT, message_id TEXT, session_id TEXT,
          time_created INTEGER, time_updated INTEGER, data TEXT);
        """
    )
    con.executemany(
        "INSERT INTO session VALUES(?,?,?,?,?,?)",
        [
            ("root", None, "/paper", "Paper", 1, 2),
            ("child", "root", "/paper", "Worker", 2, 3),
            ("other", None, "/other", "Private", 1, 2),
        ],
    )
    con.executemany(
        "INSERT INTO message VALUES(?,?,?,?,?)",
        [
            ("m1", "root", 10, 11, json.dumps({"role": "user"})),
            ("m2", "child", 12, 13, json.dumps({"role": "assistant"})),
            ("mx", "other", 14, 15, json.dumps({"role": "user"})),
        ],
    )
    con.executemany(
        "INSERT INTO part VALUES(?,?,?,?,?,?)",
        [
            ("p1", "m1", "root", 10, 11, json.dumps({"type": "text", "text": "Try cosine loss."})),
            ("p2", "m2", "child", 12, 13, json.dumps({"type": "reasoning", "text": "Cosine loss already failed on rare classes."})),
            ("p3", "m2", "child", 12, 13, json.dumps({"type": "tool", "tool": "bash", "state": {"input": "token=secret-value"}})),
            ("px", "mx", "other", 14, 15, json.dumps({"type": "text", "text": "must not export"})),
        ],
    )
    con.commit()
    con.close()


def test_opencode_tree_export_import_and_search(tmp_path: Path) -> None:
    source = tmp_path / "opencode.db"
    export = tmp_path / "paper.jsonl"
    reasoning_export = tmp_path / "paper-reasoning.jsonl"
    _source_db(source)

    counts = write_opencode_export(source, "root", export, "research-host")
    assert counts == {"sessions": 2, "messages": 2, "parts": 2}
    assert "must not export" not in export.read_text(encoding="utf-8")
    assert "already failed on rare classes" not in export.read_text(encoding="utf-8")

    store = EpisodeStore(Database(tmp_path / "agentroots.sqlite3"))
    result = store.import_opencode_jsonl(export, "sample-paper")
    assert result == {"imported": 1, "unchanged": 0, "sessions": 2}
    assert store.import_opencode_jsonl(export, "sample-paper")["unchanged"] == 1
    default_hits = store.search("sample-paper", "rare cosine classes")
    assert all("already failed" not in hit["snippet"] for hit in default_hits)

    reasoning_counts = write_opencode_export(
        source, "root", reasoning_export, "research-host", include_reasoning=True
    )
    assert reasoning_counts == {"sessions": 2, "messages": 2, "parts": 3}
    reasoning_store = EpisodeStore(Database(tmp_path / "reasoning.sqlite3"))
    included = reasoning_store.import_history_jsonl(
        reasoning_export, "sample-paper", include_reasoning=True
    )
    assert included == {"imported": 2, "unchanged": 0, "sessions": 2}
    hits = reasoning_store.search("sample-paper", "rare cosine classes")
    assert hits[0]["source_uri"].startswith("opencode://research-host/sample-paper/child/m2")
    assert hits[0]["untrusted"] is True
    assert "secret-value" not in json.dumps(hits)
    assert store.search("sample-paper", "zebra quantum unrelated") == []


def test_episode_secret_redaction(tmp_path: Path) -> None:
    export = tmp_path / "history.jsonl"
    rows = [
        {"kind": "session", "id": "s", "parent_id": None, "title": "T", "source_host": "local"},
        {"kind": "message", "id": "m", "session_id": "s", "time_created": 1, "time_updated": 1, "data": json.dumps({"role": "user"})},
        {"kind": "part", "id": "p", "message_id": "m", "session_id": "s", "time_created": 1, "data": json.dumps({"type": "text", "text": "api_key=abcd1234 do experiment"})},
    ]
    export.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    store = EpisodeStore(Database(tmp_path / "state.sqlite3"))
    store.import_opencode_jsonl(export, "p")
    hit = store.search("p", "experiment")[0]
    assert hit["redacted"] == 1
    assert "abcd1234" not in hit["snippet"]


def test_history_import_streams_jsonl_without_read_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    export = tmp_path / "large-history.jsonl"
    rows: list[dict[str, object]] = [
        {
            "kind": "session",
            "id": "s",
            "parent_id": None,
            "title": "Streaming",
            "source_host": "local",
        }
    ]
    for index in range(250):
        message_id = f"m{index}"
        rows.extend(
            [
                {
                    "kind": "message",
                    "id": message_id,
                    "session_id": "s",
                    "time_created": index,
                    "time_updated": index,
                    "data": json.dumps({"role": "user"}),
                },
                {
                    "kind": "part",
                    "id": f"p{index}",
                    "message_id": message_id,
                    "session_id": "s",
                    "time_created": index,
                    "data": json.dumps(
                        {"type": "text", "text": f"Durable observation {index}."}
                    ),
                },
            ]
        )
    export.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")

    original_read_text = Path.read_text

    def guarded_read_text(path: Path, *args: object, **kwargs: object) -> str:
        if path == export:
            raise AssertionError("history importer must stream instead of calling read_text")
        return original_read_text(path, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "read_text", guarded_read_text)
    result = EpisodeStore(Database(tmp_path / "state.sqlite3")).import_history_jsonl(
        export, "project"
    )

    assert result == {"imported": 250, "unchanged": 0, "sessions": 1}
