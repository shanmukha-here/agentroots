from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from agentroots.backfill import ConversationSource, export_sources
from agentroots.config import save_settings
from agentroots.db import Database
from agentroots.project_identity import resolve_project_identity


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics only")
def test_sensitive_state_uses_owner_only_posix_permissions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "config" / "config.json"
    monkeypatch.setenv("AGENTROOTS_CONFIG", str(config))
    save_settings({"history_consent": False})
    assert _mode(config.parent) == 0o700
    assert _mode(config) == 0o600

    database_path = tmp_path / "data" / "state.sqlite3"
    database = Database(database_path)
    with database.connect() as connection:
        connection.execute("CREATE TABLE permission_probe(value TEXT)")
        connection.execute("INSERT INTO permission_probe VALUES('private')")
        sidecars = [
            database_path.with_name(database_path.name + "-wal"),
            database_path.with_name(database_path.name + "-shm"),
        ]
        assert all(path.exists() for path in sidecars)
        assert all(_mode(path) == 0o600 for path in sidecars)
    assert _mode(database_path.parent) == 0o700
    assert _mode(database_path) == 0o600

    registry = tmp_path / "registry" / "projects.json"
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(registry))
    resolve_project_identity(tmp_path / "project", configured="private-project", path=registry)
    assert _mode(registry.parent) == 0o700
    assert _mode(registry) == 0o600

    source_path = tmp_path / "source.jsonl"
    source_path.write_text(
        json.dumps({
            "type": "response_item",
            "timestamp": "2026-08-29T00:00:00Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "A private experiment failed."}],
            },
        })
        + "\n",
        encoding="utf-8",
    )
    bundle = tmp_path / "backfill" / "bundles" / "private-project.jsonl"
    export_sources(
        [
            ConversationSource(
                harness="codex",
                source=str(source_path),
                session_id="session",
                directory=str(tmp_path / "project"),
                title="",
                source_host="local",
                project_id="private-project",
            )
        ],
        bundle,
        approved=True,
    )
    assert _mode(bundle.parent) == 0o700
    assert _mode(bundle) == 0o600
