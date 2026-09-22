from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from agentroots import __version__, onboarding
from agentroots.cli import _human_result, parser
from agentroots.db import Database


@pytest.fixture(autouse=True)
def isolated_project_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))


def test_detect_harnesses_uses_commands_or_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    codex_history = tmp_path / "codex"
    codex_history.mkdir()
    monkeypatch.setattr(
        onboarding,
        "_known_sources",
        lambda: {"codex": [codex_history], "opencode": [tmp_path / "missing.db"]},
    )
    monkeypatch.setattr(
        onboarding.shutil, "which", lambda name: "/bin/opencode" if name == "opencode" else None
    )
    detected = onboarding.detect_harnesses()
    assert [item["name"] for item in detected] == ["codex", "opencode"]
    assert detected[0]["history"] == [str(codex_history)]


def test_setup_is_idempotent_and_history_requires_explicit_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "db_path", lambda: tmp_path / "state.sqlite3")
    monkeypatch.setattr(onboarding, "settings_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(onboarding, "detect_harnesses", list)
    monkeypatch.setattr(onboarding, "start_daemon", lambda database=None: None)
    monkeypatch.setattr(
        onboarding, "resource_status", lambda db, **kwargs: {"database": str(db.path)}
    )
    monkeypatch.setattr(onboarding, "save_settings", lambda value: tmp_path / "config.json")
    monkeypatch.setattr(onboarding, "load_settings", dict)
    monkeypatch.setattr(onboarding, "_start_backfill_worker", lambda: None)
    monkeypatch.setenv("AGENTROOTS_DISABLE_DAEMON", "1")
    first = onboarding.setup(history=False, configure_clients=True)
    second = onboarding.setup(history=False, configure_clients=True)
    assert first["ready"] is True
    assert second["history"] == {
        "approved": False,
        "state": "disabled",
        "projects": "all-discovered",
        "reasoning_included": False,
    }


def test_setup_is_not_ready_when_detected_client_configuration_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "db_path", lambda: tmp_path / "state.sqlite3")
    monkeypatch.setattr(onboarding, "settings_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(
        onboarding,
        "detect_harnesses",
        lambda: [{"name": "codex", "command": "/bin/codex", "history": []}],
    )
    monkeypatch.setattr(
        onboarding,
        "configure_codex",
        lambda: {"configured": False, "reason": "Node.js is required for Codex hooks"},
    )
    monkeypatch.setattr(
        onboarding, "resource_status", lambda db, **kwargs: {"database": str(db.path)}
    )
    monkeypatch.setattr(onboarding, "save_settings", lambda value: tmp_path / "config.json")
    monkeypatch.setattr(onboarding, "load_settings", dict)
    monkeypatch.setenv("AGENTROOTS_DISABLE_DAEMON", "1")

    result = onboarding.setup(history=False, configure_clients=True)

    assert result["ready"] is False
    assert result["configured"][0]["reason"] == "Node.js is required for Codex hooks"
    rendered = _human_result("setup", result)
    assert rendered.startswith("AgentRoots setup incomplete")
    assert "Needs attention: codex: Node.js is required for Codex hooks" in rendered


def test_setup_is_not_ready_when_background_service_does_not_start(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, Any] = {}
    monkeypatch.setattr(onboarding, "db_path", lambda: tmp_path / "state.sqlite3")
    monkeypatch.setattr(onboarding, "settings_path", lambda: tmp_path / "config.json")
    monkeypatch.setattr(onboarding, "detect_harnesses", list)
    monkeypatch.setattr(onboarding, "start_daemon", lambda database=None: None)
    monkeypatch.setattr(onboarding, "hook_status", lambda db: {"daemon": False})
    monkeypatch.setattr(onboarding.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(
        onboarding, "resource_status", lambda db, **kwargs: {"database": str(db.path)}
    )
    monkeypatch.setattr(onboarding, "load_settings", dict)
    monkeypatch.setattr(
        onboarding,
        "save_settings",
        lambda value: stored.update(value) or tmp_path / "config.json",
    )
    monkeypatch.delenv("AGENTROOTS_DISABLE_DAEMON", raising=False)

    result = onboarding.setup(history=False, configure_clients=False)

    assert result["ready"] is False
    assert result["daemon"] == {
        "required": True,
        "ready": False,
        "reason": "background service did not start",
    }
    assert stored["setup_complete"] is False
    rendered = _human_result("setup", result)
    assert rendered.startswith("AgentRoots setup incomplete")
    assert "Needs attention: background service" in rendered


def test_setup_reuses_successful_daemon_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = Database(tmp_path / "selected.sqlite3")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(onboarding, "detect_harnesses", list)
    monkeypatch.setattr(onboarding, "start_daemon", lambda database=None: None)
    monkeypatch.setattr(onboarding, "hook_status", lambda database: {"daemon": True})
    monkeypatch.setattr(onboarding, "load_settings", dict)
    monkeypatch.setattr(onboarding, "save_settings", lambda value: tmp_path / "config.json")

    def status(database: Database, *, hooks_snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        captured["snapshot"] = hooks_snapshot
        return {"ready": bool((hooks_snapshot or {}).get("daemon"))}

    monkeypatch.setattr(onboarding, "resource_status", status)
    monkeypatch.delenv("AGENTROOTS_DISABLE_DAEMON", raising=False)

    result = onboarding.setup(
        history=False,
        configure_clients=False,
        database=selected,
    )

    assert result["ready"] is True
    assert result["resources"]["ready"] is True
    assert captured["snapshot"] == {"daemon": True}


def test_resource_status_counts_storage_without_touching_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = Database(tmp_path / "data" / "state.sqlite3")
    cache = tmp_path / "cache"
    model = cache / "models" / "weights.bin"
    index = cache / "models" / "vector-indexes" / "index.npy"
    index.parent.mkdir(parents=True)
    model.write_bytes(b"m" * 20)
    index.write_bytes(b"i" * 10)
    retained = database.path.parent / "backfill" / "bundles" / "interrupted.jsonl"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"b" * 7)
    monkeypatch.setattr(onboarding, "cache_dir", lambda: cache)
    monkeypatch.setattr(onboarding, "data_dir", lambda: database.path.parent)
    monkeypatch.setattr(
        onboarding,
        "load_settings",
        lambda: {"notifications": "quiet", "setup_complete": True},
    )
    monkeypatch.setattr(onboarding, "_rss_bytes", lambda: 1234)
    monkeypatch.setattr(
        onboarding,
        "_python_environment_status",
        lambda: {"isolated": True, "path": str(tmp_path / "venv"), "bytes": 99},
    )
    monkeypatch.setattr(
        onboarding,
        "hook_status",
        lambda db: {
            "daemon": True,
            "semantic_backend": "bge_hybrid",
            "extractor": "qwen",
            "process_memory_bytes": 5678,
        },
    )
    result = onboarding.resource_status(database)
    assert result["memory"] == {"command_bytes": 1234, "daemon_bytes": 5678}
    assert result["storage"]["model_bytes"] == 20
    assert result["storage"]["index_bytes"] == 10
    assert result["storage"]["backfill_bundle_bytes"] == 7
    assert result["storage"]["python_environment_bytes"] == 99
    assert result["storage"]["total_bytes"] == result["storage"]["managed_state_bytes"] + 99
    assert result["retained_backfill_bundles"] == {"count": 1, "bytes": 7}
    assert result["ready"] is True


def test_setup_uses_explicit_database_for_resources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = Database(tmp_path / "selected.sqlite3")
    decoy = tmp_path / "decoy.sqlite3"
    monkeypatch.setattr(onboarding, "db_path", lambda: decoy)
    monkeypatch.setattr(onboarding, "detect_harnesses", list)
    monkeypatch.setattr(onboarding, "load_settings", dict)
    monkeypatch.setattr(onboarding, "save_settings", lambda value: tmp_path / "config.json")
    monkeypatch.setattr(
        onboarding,
        "resource_status",
        lambda database, **kwargs: {"database": str(database.path), "ready": True},
    )
    monkeypatch.setenv("AGENTROOTS_DISABLE_DAEMON", "1")

    result = onboarding.setup(
        history=False,
        configure_clients=False,
        database=selected,
    )

    assert result["resources"] == {"database": str(selected.path), "ready": True}
    assert not decoy.exists()


def test_cli_database_override_applies_to_all_onboarding_paths(tmp_path: Path) -> None:
    selected = tmp_path / "selected" / "state.sqlite3"
    decoy = tmp_path / "decoy" / "state.sqlite3"
    project = tmp_path / "project"
    project.mkdir()
    environment = os.environ.copy()
    environment.update({
        "AGENTROOTS_DB": str(decoy),
        "AGENTROOTS_CONFIG": str(tmp_path / "config.json"),
        "AGENTROOTS_PROJECT_REGISTRY": str(tmp_path / "projects.json"),
        "AGENTROOTS_HOOK_RUNTIME": str(tmp_path / "hooks"),
        "AGENTROOTS_DISABLE_DAEMON": "1",
        "AGENTROOTS_SEMANTIC": "off",
    })
    setup_result = subprocess.run(
        [
            sys.executable, "-m", "agentroots.cli", "--db", str(selected), "setup",
            "--yes", "--no-history", "--no-clients", "--json",
        ],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert setup_result.returncode == 0, setup_result.stderr
    setup_payload = json.loads(setup_result.stdout)
    assert Path(setup_payload["resources"]["database"]) == selected.resolve()
    assert setup_payload["resources"]["ready"] is True
    assert selected.exists()
    assert not decoy.exists()

    doctor_result = subprocess.run(
        [sys.executable, "-m", "agentroots.cli", "--db", str(selected), "doctor", "--json"],
        cwd=project,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert doctor_result.returncode == 0, doctor_result.stderr
    doctor_payload = json.loads(doctor_result.stdout)
    storage = next(
        item for item in doctor_payload["checks"] if item["name"] == "storage directory"
    )
    assert Path(storage["detail"]) == selected.parent.resolve()


def test_cleanup_is_preview_only_and_protects_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = tmp_path / "data"
    cache = tmp_path / "cache"
    spool = data / "hooks" / "spool" / "event.json"
    spool.parent.mkdir(parents=True)
    spool.write_text("temporary", encoding="utf-8")
    monkeypatch.setattr(onboarding, "data_dir", lambda: data)
    monkeypatch.setattr(onboarding, "cache_dir", lambda: cache)
    monkeypatch.setattr(onboarding, "db_path", lambda: data / "state.sqlite3")
    result = onboarding.cleanup_preview()
    assert result["preview"] is True
    assert result["reclaimable_bytes"] == len("temporary")
    assert spool.exists()
    assert str(data / "state.sqlite3") in result["protected"]


def test_notification_configuration_is_validated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, Any] = {}
    monkeypatch.setattr(onboarding, "load_settings", lambda: dict(stored))
    monkeypatch.setattr(
        onboarding,
        "save_settings",
        lambda value: stored.update(value) or tmp_path / "config.json",
    )
    assert onboarding.configure("notifications", "detailed")["value"] == "detailed"
    with pytest.raises(ValueError, match="notifications"):
        onboarding.configure("notifications", "loud")
    with pytest.raises(ValueError, match="unknown setting"):
        onboarding.configure("memory-limit", "1 GB")


def test_opencode_plugin_install_is_atomic_and_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Path(onboarding.__file__).resolve().parents[2] / "plugins" / "opencode" / "agentroots.js"
    assert source.exists()
    monkeypatch.setenv("OPENCODE_CONFIG_DIR", str(tmp_path / "opencode"))
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: "/bin/opencode")
    monkeypatch.setattr(onboarding, "_run", lambda command: (True, "1.2.3"))
    first = onboarding.configure_opencode()
    second = onboarding.configure_opencode()
    target = tmp_path / "opencode" / "plugins" / "agentroots.js"
    assert first["configured"] is True
    assert second["configured"] is True
    assert second["existing"] is True
    assert second["dependency"]["version"] == "1.2.3"
    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    package = json.loads((tmp_path / "opencode" / "package.json").read_text(encoding="utf-8"))
    assert package["dependencies"]["@opencode-ai/plugin"] == "1.2.3"
    runtime = json.loads(
        (tmp_path / "opencode" / "agentroots-runtime.json").read_text(encoding="utf-8")
    )
    assert runtime["python"] == str(Path(sys.executable).resolve())
    assert first["runtime"].endswith("agentroots-runtime.json")


def test_opencode_configuration_reports_missing_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: None)
    assert onboarding.configure_opencode() == {
        "configured": False,
        "reason": "OpenCode command not found",
    }


def test_unattended_setup_does_not_imply_history_consent() -> None:
    args = parser().parse_args(["setup", "--yes"])
    assert args.history is False
    assert args.no_history is False


def test_codex_registration_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(onboarding, "user_data_path", lambda name: tmp_path / name)
    installed = tmp_path / "agentroots" / "codex-marketplace" / "plugins" / "agentroots"

    def fake_run(command: list[str]) -> tuple[bool, str]:
        if command[1:4] == ["mcp", "get", "agentroots"]:
            return True, (
                "agentroots\n  enabled: true\n  transport: stdio\n"
                f"  command: {sys.executable}\n  args: -m agentroots.server"
            )
        if command[-2:] == ["plugin", "list"]:
            return True, (
                f"agentroots@agentroots  installed, enabled  {__version__}  "
                f"{installed}"
            )
        return True, ""

    monkeypatch.setattr(onboarding, "_run", fake_run)
    result = onboarding.configure_codex()
    assert result["configured"] is True
    assert result["mcp"]["existing"] is True
    assert result["hooks"]["existing"] is True
    assert result["hooks"]["trust"] == "review-in-codex"
    runtime = json.loads((installed / "runtime.json").read_text(encoding="utf-8"))
    assert runtime["python"] == str(Path(sys.executable).resolve())
    assert runtime["version"] == __version__


def test_codex_registration_replaces_stale_mcp_and_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(onboarding, "user_data_path", lambda name: tmp_path / name)
    commands: list[list[str]] = []
    mcp_current = False
    plugin_current = False
    installed = tmp_path / "agentroots" / "codex-marketplace" / "plugins" / "agentroots"

    def fake_run(command: list[str]) -> tuple[bool, str]:
        nonlocal mcp_current, plugin_current
        commands.append(command)
        if command[1:4] == ["mcp", "get", "agentroots"]:
            if mcp_current:
                return True, (
                    "agentroots\n  enabled: true\n  transport: stdio\n"
                    f"  command: {sys.executable}\n  args: -m agentroots.server"
                )
            return True, (
                "agentroots\n  enabled: true\n  transport: stdio\n"
                "  command: old-agentroots-mcp\n  args:"
            )
        if command[1:4] == ["mcp", "add", "agentroots"]:
            mcp_current = True
            return True, ""
        if command[-2:] == ["plugin", "list"]:
            if plugin_current:
                return True, (
                    f"agentroots@agentroots  installed, enabled  {__version__}  "
                    f"{installed}"
                )
            return True, "agentroots@agentroots  not installed  0.1.0  -"
        if command[-3:] == ["plugin", "add", "agentroots@agentroots"]:
            plugin_current = True
            return True, ""
        return True, ""

    monkeypatch.setattr(onboarding, "_run", fake_run)
    result = onboarding.configure_codex()
    assert result["configured"] is True
    assert ["codex", "mcp", "remove", "agentroots"] in commands
    assert [
        "codex", "mcp", "add", "agentroots", "--",
        sys.executable, "-m", "agentroots.server",
    ] in commands
    assert ["codex", "plugin", "add", "agentroots@agentroots"] in commands


def test_codex_registration_rejects_other_enabled_agentroots_plugin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(onboarding, "user_data_path", lambda name: tmp_path / name)

    def fake_run(command: list[str]) -> tuple[bool, str]:
        if command[1:4] == ["mcp", "get", "agentroots"]:
            return True, (
                "agentroots\n  enabled: true\n  transport: stdio\n"
                f"  command: {sys.executable}\n  args: -m agentroots.server"
            )
        if command[-2:] == ["plugin", "list"]:
            return True, (
                "agentroots@agentroots-local  installed, enabled  0.1.0  /old/plugin"
            )
        return True, ""

    monkeypatch.setattr(onboarding, "_run", fake_run)
    result = onboarding.configure_codex()
    assert result["configured"] is False
    assert result["hooks"]["conflicts"] == ["agentroots@agentroots-local"]


def test_codex_registration_requires_node_for_hooks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        onboarding.shutil,
        "which",
        lambda name: "/bin/codex" if name == "codex" else None,
    )

    def fake_run(command: list[str]) -> tuple[bool, str]:
        if command[1:4] == ["mcp", "get", "agentroots"]:
            return True, (
                "agentroots\n  enabled: true\n  transport: stdio\n"
                f"  command: {sys.executable}\n  args: -m agentroots.server"
            )
        return True, ""

    monkeypatch.setattr(onboarding, "_run", fake_run)
    result = onboarding.configure_codex()
    assert result["configured"] is False
    assert result["hooks"]["reason"] == "Node.js is required for Codex hooks"


def test_progress_file_is_atomic_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(onboarding, "data_dir", lambda: tmp_path)
    onboarding._write_progress(state="indexing", imported=12)
    onboarding._write_progress(state="complete", candidates=3)
    value = json.loads((tmp_path / "backfill" / "progress.json").read_text(encoding="utf-8"))
    assert value == {"state": "complete", "imported": 12, "candidates": 3}


def test_backfill_worker_rechecks_consent_before_source_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(onboarding, "load_settings", lambda: {"history_consent": False})

    def forbidden_sources() -> dict[str, list[Path]]:
        raise AssertionError("source discovery must not run without consent")

    monkeypatch.setattr(onboarding, "_known_sources", forbidden_sources)
    onboarding.run_backfill()
    progress = json.loads((tmp_path / "backfill" / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "disabled"
    assert progress["phase"] == "consent-required"


def test_successful_backfill_removes_transient_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agentroots import hooks

    sessions = tmp_path / "sessions"
    sessions.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    source = sessions / "session.jsonl"
    rows = [
        {
            "type": "session_meta",
            "payload": {"id": "session", "cwd": str(project), "model": "fixture"},
        },
        {
            "type": "response_item",
            "timestamp": "2026-08-29T00:00:00Z",
            "payload": {
                "type": "message",
                "role": "user",
                "content": [{"type": "input_text", "text": "The cache trial failed."}],
            },
        },
    ]
    source.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    data = tmp_path / "data"
    monkeypatch.setattr(onboarding, "data_dir", lambda: data)
    monkeypatch.setattr(onboarding, "db_path", lambda: data / "state.sqlite3")
    monkeypatch.setattr(
        onboarding,
        "load_settings",
        lambda: {"history_consent": True, "history_projects": []},
    )
    monkeypatch.setattr(
        onboarding,
        "_known_sources",
        lambda: {"codex": [sessions], "opencode": []},
    )
    monkeypatch.setattr(hooks, "preferred_extractor", lambda: object())
    monkeypatch.setattr(
        hooks,
        "extract_episode_backfill",
        lambda database, project_id, **kwargs: {"candidates": 0},
    )

    onboarding.run_backfill()

    assert not list((data / "backfill" / "bundles").glob("*.jsonl"))
    progress = json.loads((data / "backfill" / "progress.json").read_text(encoding="utf-8"))
    assert progress["state"] == "complete"
    with Database(data / "state.sqlite3").connect() as connection:
        assert connection.execute("SELECT count(*) FROM episodes").fetchone()[0] == 1
