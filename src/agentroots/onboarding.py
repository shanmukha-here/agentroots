from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from platformdirs import user_data_path

from . import __version__
from .config import cache_dir, data_dir, db_path, load_settings, save_settings, settings_path
from .db import Database
from .hooks import _process_memory_bytes, hook_status, start_daemon
from .private_fs import atomic_write_private_text, ensure_private_directory
from .project_identity import registry_path, resolve_project_identity


def _size(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    if path.exists():
        for child in path.rglob("*"):
            if child.is_file():
                try:
                    total += child.stat().st_size
                except OSError:
                    pass
    return total


def _rss_bytes() -> int:
    return _process_memory_bytes()


def _python_environment_status() -> dict[str, Any]:
    """Report an isolated Python environment without charging shared runtimes."""
    prefix = Path(sys.prefix).resolve()
    base_prefix = Path(sys.base_prefix).resolve()
    isolated = prefix != base_prefix
    return {
        "isolated": isolated,
        "path": str(prefix),
        "bytes": _size(prefix) if isolated else 0,
    }


def _known_sources() -> dict[str, list[Path]]:
    home = Path.home()
    opencode = [
        home / ".local" / "share" / "opencode" / "opencode.db",
        Path(os.environ.get("APPDATA", home)) / "opencode" / "opencode.db",
        Path(os.environ.get("LOCALAPPDATA", home)) / "opencode" / "opencode.db",
    ]
    return {
        "codex": [home / ".codex" / "sessions"],
        "opencode": list(dict.fromkeys(opencode)),
    }


def detect_harnesses() -> list[dict[str, Any]]:
    sources = _known_sources()
    detected = []
    for name in ("codex", "opencode"):
        command = shutil.which(name)
        paths = [str(path) for path in sources[name] if path.exists()]
        if command or paths:
            detected.append({"name": name, "command": command, "history": paths})
    return detected


def history_preview() -> dict[str, Any]:
    """Discover history scope from metadata only, before conversation consent."""

    from .backfill import discover_codex, discover_opencode

    sources = []
    known = _known_sources()
    for root in known["codex"]:
        if root.exists():
            sources.extend(discover_codex(root, source_host="local"))
    for source_db in known["opencode"]:
        if source_db.exists():
            sources.extend(discover_opencode(source_db, source_host="local"))
    projects: dict[str, dict[str, Any]] = {}
    for source in sources:
        item = projects.setdefault(
            source.project_id,
            {"project": source.project_id, "sessions": 0, "harnesses": set()},
        )
        item["sessions"] += 1
        item["harnesses"].add(source.harness)
    return {
        "metadata_only": True,
        "projects": [
            {**item, "harnesses": sorted(item["harnesses"])}
            for item in sorted(projects.values(), key=lambda value: value["project"])
        ],
        "sessions": len(sources),
    }


def _run(command: list[str]) -> tuple[bool, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    detail = (result.stderr or result.stdout).strip()
    return result.returncode == 0, detail[-1000:]


def _runtime_descriptor() -> dict[str, str]:
    hook_runtime = Path(
        os.environ.get("AGENTROOTS_HOOK_RUNTIME", user_data_path("agentroots") / "hooks")
    )
    hook_spool = Path(os.environ.get("AGENTROOTS_HOOK_SPOOL", hook_runtime / "spool"))
    return {
        "version": __version__,
        "python": str(Path(sys.executable).resolve()),
        "db": str(db_path().resolve()),
        "config": str(settings_path().resolve()),
        "project_registry": str(registry_path().resolve()),
        "hook_runtime": str(hook_runtime.resolve()),
        "hook_spool": str(hook_spool.resolve()),
        "model_cache": str(cache_dir().resolve()),
    }


def _codex_mcp_ready(output: str) -> bool:
    values = {
        key.strip().lower(): value.strip()
        for line in output.splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    configured = values.get("command", "").strip('"')
    expected = str(Path(sys.executable))
    if os.name == "nt":
        configured = os.path.normcase(configured)
        expected = os.path.normcase(expected)
    return (
        values.get("enabled", "").lower() == "true"
        and values.get("transport", "").lower() == "stdio"
        and configured == expected
        and values.get("args", "").split() == ["-m", "agentroots.server"]
    )


def _plugin_rows(output: str) -> list[dict[str, str]]:
    rows = []
    for line in output.splitlines():
        fields = re.split(r"\s{2,}", line.strip(), maxsplit=3)
        if len(fields) != 4 or "@" not in fields[0]:
            continue
        rows.append(
            {"ref": fields[0], "status": fields[1], "version": fields[2], "path": fields[3]}
        )
    return rows


def _plugin_digest(root: Path) -> str:
    digest = hashlib.sha256()
    try:
        files = sorted(path for path in root.rglob("*") if path.is_file())
    except OSError:
        return ""
    for path in files:
        if path.is_symlink():
            return ""
        if "__pycache__" in path.parts or path.suffix == ".pyc" or path.name == "runtime.json":
            continue
        try:
            digest.update(path.relative_to(root).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        except OSError:
            return ""
    return digest.hexdigest()


def _codex_plugin_ready(output: str, source: Path, version: str) -> bool:
    for row in _plugin_rows(output):
        status = {item.strip().lower() for item in row["status"].split(",")}
        if (
            row["ref"].lower() == "agentroots@agentroots"
            and status == {"installed", "enabled"}
            and row["version"] == version
            and _plugin_digest(Path(row["path"])) == _plugin_digest(source)
        ):
            return True
    return False


def _enabled_agentroots_plugins(output: str) -> list[str]:
    enabled = []
    for row in _plugin_rows(output):
        status = {item.strip().lower() for item in row["status"].split(",")}
        if row["ref"].lower().startswith("agentroots@") and status == {
            "installed",
            "enabled",
        }:
            enabled.append(row["ref"])
    return enabled


def configure_codex() -> dict[str, Any]:
    if not shutil.which("codex"):
        return {"configured": False, "reason": "Codex command not found"}
    inspected, output = _run(["codex", "mcp", "get", "agentroots"])
    if inspected and _codex_mcp_ready(output):
        mcp: dict[str, Any] = {"configured": True, "existing": True}
    else:
        removed = True
        remove_detail = ""
        if inspected:
            removed, remove_detail = _run(["codex", "mcp", "remove", "agentroots"])
        added, add_detail = (False, remove_detail)
        if removed:
            added, add_detail = _run(
                [
                    "codex", "mcp", "add", "agentroots", "--",
                    sys.executable, "-m", "agentroots.server",
                ]
            )
        verified, verify_output = _run(["codex", "mcp", "get", "agentroots"])
        ready = added and verified and _codex_mcp_ready(verify_output)
        mcp = {
            "configured": ready,
            "existing": False,
            "replaced": inspected,
            "detail": "" if ready else (add_detail or verify_output or remove_detail),
        }
    packaged_plugin = Path(__file__).parent / "codex_plugin"
    checkout_plugin = Path(__file__).resolve().parents[2] / "plugins" / "agentroots"
    plugin = packaged_plugin if packaged_plugin.exists() else checkout_plugin
    hook_result: dict[str, Any] = {"configured": False, "reason": "plugin files unavailable"}
    node = shutil.which("node")
    if plugin.exists() and not node:
        hook_result = {
            "configured": False,
            "reason": "Node.js is required for Codex hooks",
        }
    elif plugin.exists():
        plugin_manifest = json.loads(
            (plugin / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        plugin_version = str(plugin_manifest["version"])
        marketplace = user_data_path("agentroots") / "codex-marketplace"
        marketplace_plugin = marketplace / "plugins" / "agentroots"
        if marketplace_plugin.exists():
            if marketplace_plugin.is_symlink():
                marketplace_plugin.unlink()
            else:
                shutil.rmtree(marketplace_plugin)
        shutil.copytree(plugin, marketplace_plugin, dirs_exist_ok=True)
        atomic_write_private_text(
            marketplace_plugin / "runtime.json",
            json.dumps(_runtime_descriptor(), indent=2) + "\n",
        )
        manifest = marketplace / ".agents" / "plugins" / "marketplace.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "name": "agentroots",
            "interface": {"displayName": "AgentRoots"},
            "plugins": [{
                "name": "agentroots",
                "source": {"source": "local", "path": "./plugins/agentroots"},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": "Productivity",
            }],
        }, indent=2) + "\n", encoding="utf-8")
        plugins_listed, plugin_output = _run(["codex", "plugin", "list"])
        enabled = _enabled_agentroots_plugins(plugin_output) if plugins_listed else []
        conflicts = sorted(ref for ref in enabled if ref.lower() != "agentroots@agentroots")
        if conflicts:
            hook_result = {
                "configured": False,
                "reason": "conflicting enabled AgentRoots plugin",
                "conflicts": conflicts,
            }
        elif plugins_listed and _codex_plugin_ready(plugin_output, plugin, plugin_version):
            hook_result = {"configured": True, "existing": True}
        else:
            exact_installed = any(
                row["ref"].lower() == "agentroots@agentroots"
                and "installed" in {
                    item.strip().lower() for item in row["status"].split(",")
                }
                for row in _plugin_rows(plugin_output)
            )
            removed = True
            remove_detail = ""
            if exact_installed:
                removed, remove_detail = _run(
                    ["codex", "plugin", "remove", "agentroots@agentroots"]
                )
            added, add_detail = _run(
                ["codex", "plugin", "marketplace", "add", str(marketplace)]
            )
            installed, install_detail = (False, remove_detail)
            if removed:
                installed, install_detail = _run(
                    ["codex", "plugin", "add", "agentroots@agentroots"]
                )
            verified, verify_output = _run(["codex", "plugin", "list"])
            ready = (
                installed
                and verified
                and _codex_plugin_ready(verify_output, plugin, plugin_version)
                and not [
                    ref for ref in _enabled_agentroots_plugins(verify_output)
                    if ref.lower() != "agentroots@agentroots"
                ]
            )
            hook_result = {
                "configured": ready,
                "marketplace": added,
                "replaced": exact_installed,
                "detail": "" if ready else (install_detail or verify_output or add_detail),
            }
    return {
        "configured": bool(mcp["configured"] and hook_result["configured"]),
        "mcp": mcp,
        "hooks": {
            **hook_result,
            "trust": "review-in-codex",
            "trust_help": "Codex /hooks reviews new or changed hook definitions",
        },
    }


def configure_opencode() -> dict[str, Any]:
    command = shutil.which("opencode")
    if not command:
        return {"configured": False, "reason": "OpenCode command not found"}
    version_ok, version_output = _run([command, "--version"])
    match = re.search(r"(?<!\d)(\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?)(?!\d)", version_output)
    if not version_ok or match is None:
        return {"configured": False, "reason": "could not determine OpenCode version"}
    plugin_version = match.group(1)
    packaged = Path(__file__).parent / "opencode_plugin.js"
    checkout = Path(__file__).resolve().parents[2] / "plugins" / "opencode" / "agentroots.js"
    source = packaged if packaged.exists() else checkout
    if not source.exists():
        return {"configured": False, "reason": "OpenCode plugin files unavailable"}
    configured_root = os.environ.get("OPENCODE_CONFIG_DIR")
    xdg_root = os.environ.get("XDG_CONFIG_HOME")
    root = (
        Path(configured_root)
        if configured_root
        else (Path(xdg_root) if xdg_root else Path.home() / ".config") / "opencode"
    )
    target = root / "plugins" / "agentroots.js"
    content = source.read_text(encoding="utf-8")
    if target.exists():
        existing = target.read_text(encoding="utf-8")
        if not existing.startswith("// AgentRoots OpenCode adapter."):
            return {
                "configured": False,
                "reason": f"refusing to overwrite unrelated plugin: {target}",
            }
    package = root / "package.json"
    package_value: dict[str, Any] = {}
    if package.exists():
        try:
            decoded = json.loads(package.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {"configured": False, "reason": f"invalid OpenCode package file: {package}"}
        if not isinstance(decoded, dict):
            return {"configured": False, "reason": f"invalid OpenCode package file: {package}"}
        package_value = decoded
    dependencies = package_value.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return {"configured": False, "reason": f"invalid dependencies in: {package}"}
    dependency_changed = dependencies.get("@opencode-ai/plugin") != plugin_version
    package_value["dependencies"] = {
        **dependencies,
        "@opencode-ai/plugin": plugin_version,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    existing_plugin = target.exists() and target.read_text(encoding="utf-8") == content
    atomic_write_private_text(target, content)
    atomic_write_private_text(package, json.dumps(package_value, indent=2) + "\n")
    runtime_file = root / "agentroots-runtime.json"
    atomic_write_private_text(
        runtime_file,
        json.dumps(_runtime_descriptor(), indent=2) + "\n",
    )
    return {
        "configured": True,
        "existing": existing_plugin and not dependency_changed,
        "plugin": str(target),
        "runtime": str(runtime_file),
        "dependency": {
            "package": "@opencode-ai/plugin",
            "version": plugin_version,
            "install": "OpenCode startup",
        },
        "restart_required": True,
    }


def resource_status(
    db: Database, *, hooks_snapshot: dict[str, Any] | None = None
) -> dict[str, Any]:
    database_files = [
        db.path,
        db.path.with_name(db.path.name + "-wal"),
        db.path.with_name(db.path.name + "-shm"),
    ]
    models = cache_dir() if os.environ.get("AGENTROOTS_MODEL_CACHE") else cache_dir() / "models"
    indexes = models / "vector-indexes"
    runtime = Path(os.environ.get("AGENTROOTS_HOOK_RUNTIME", data_dir() / "hooks"))
    bundles = data_dir() / "backfill" / "bundles"
    retained_bundles = []
    if bundles.exists():
        retained_bundles = [path for path in bundles.rglob("*.jsonl") if path.is_file()]
    retained_bundle_bytes = sum(_size(path) for path in retained_bundles)
    with db.connect() as con:
        counts = {
            "projects": con.execute(
                "SELECT count(*) FROM (SELECT project FROM records UNION SELECT project FROM episodes)"
            ).fetchone()[0],
            "episodes": con.execute("SELECT count(*) FROM episodes").fetchone()[0],
            "records": con.execute("SELECT count(*) FROM records").fetchone()[0],
            "candidates": con.execute("SELECT count(*) FROM extraction_candidates").fetchone()[0],
        }
    storage: dict[str, Any] = {
        "database_bytes": sum(_size(path) for path in database_files),
        "registry_bytes": _size(registry_path()),
        "model_bytes": max(0, _size(models) - _size(indexes)),
        "index_bytes": _size(indexes),
        "runtime_bytes": _size(runtime),
        "backfill_bundle_bytes": retained_bundle_bytes,
    }
    managed_state_bytes = sum(storage.values())
    python_environment = _python_environment_status()
    storage["managed_state_bytes"] = managed_state_bytes
    storage["python_environment_bytes"] = int(python_environment["bytes"])
    storage["total_bytes"] = managed_state_bytes + int(python_environment["bytes"])
    storage["python_environment"] = python_environment
    hooks = hooks_snapshot if hooks_snapshot is not None else hook_status(db)
    daemon_memory = int(hooks.get("process_memory_bytes", 0))
    progress_path = data_dir() / "backfill" / "progress.json"
    try:
        backfill = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        backfill = {"state": "not-started"}
    settings = load_settings()
    daemon_disabled = os.environ.get("AGENTROOTS_DISABLE_DAEMON", "").lower() in {
        "1", "true", "yes",
    }
    readiness_reasons = []
    if not settings.get("setup_complete", False):
        readiness_reasons.append("setup is incomplete")
    if not hooks["daemon"] and not daemon_disabled:
        readiness_reasons.append(
            "background service needs a version restart; inspect agentroots hook-status"
            if hooks.get("restart_required") else "background service is not running"
        )
    return {
        "ready": not readiness_reasons,
        "readiness": {
            "reasons": readiness_reasons,
            "daemon_required": not daemon_disabled,
            "daemon_ready": bool(hooks["daemon"]),
            "setup_complete": bool(settings.get("setup_complete", False)),
        },
        "database": str(db.path),
        "hooks": hooks,
        "memory": {"command_bytes": _rss_bytes(), "daemon_bytes": daemon_memory},
        "storage": storage,
        "counts": counts,
        "settings": settings,
        "backfill": backfill,
        "retained_backfill_bundles": {
            "count": len(retained_bundles),
            "bytes": retained_bundle_bytes,
        },
    }


def doctor(db: Database) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        with db.connect() as con:
            result = con.execute("PRAGMA integrity_check").fetchone()[0]
        checks.append({"name": "database", "ok": result == "ok", "detail": result})
    except sqlite3.Error as exc:
        checks.append({"name": "database", "ok": False, "detail": str(exc)})
    hooks = hook_status(db)
    daemon_disabled = os.environ.get("AGENTROOTS_DISABLE_DAEMON", "").lower() in {
        "1", "true", "yes",
    }
    checks.append({
        "name": "background service",
        "ok": bool(hooks["daemon"]) or daemon_disabled,
        "detail": {**hooks, "required": not daemon_disabled},
    })
    settings = load_settings()
    checks.append({
        "name": "configuration",
        "ok": settings_path().exists() and bool(settings.get("setup_complete", False)),
        "detail": str(settings_path()),
    })
    checks.append({"name": "storage directory", "ok": data_dir().exists(), "detail": str(data_dir())})
    return {"healthy": all(check["ok"] for check in checks), "checks": checks}


def setup(
    *,
    history: bool,
    configure_clients: bool = True,
    history_projects: list[str] | None = None,
    database: Database | None = None,
) -> dict[str, Any]:
    db = database or Database(db_path())
    current_project = resolve_project_identity(Path.cwd())
    harnesses = detect_harnesses()
    configured = []
    if configure_clients:
        for harness in harnesses:
            if not harness.get("command"):
                configured.append({
                    "name": harness["name"],
                    "configured": False,
                    "required": False,
                    "reason": "history found but command unavailable",
                })
            elif harness["name"] == "codex":
                configured.append({"name": "codex", **configure_codex()})
            elif harness["name"] == "opencode":
                configured.append({"name": "opencode", **configure_opencode()})
            else:
                configured.append({"name": harness["name"], "configured": False, "reason": "history only"})
    settings = load_settings()
    settings["history_consent"] = history
    settings["history_projects"] = sorted(set(history_projects or []))
    settings["configured_harnesses"] = [item["name"] for item in configured if item.get("configured")]
    daemon_disabled = os.environ.get("AGENTROOTS_DISABLE_DAEMON", "").lower() in {
        "1", "true", "yes",
    }
    daemon_ready = daemon_disabled
    daemon_reason = "explicitly disabled" if daemon_disabled else "background service did not start"
    daemon_status: dict[str, Any] | None = None
    if not daemon_disabled:
        try:
            start_daemon(db.path)
            for _ in range(40):
                daemon_status = hook_status(db)
                if daemon_status["daemon"]:
                    daemon_ready = True
                    daemon_reason = "running"
                    break
                time.sleep(0.1)
        except OSError as exc:
            daemon_reason = f"{type(exc).__name__}: {exc}"
    history_state = "disabled"
    if history:
        history_state = "indexing"
        _start_backfill_worker()
    clients_ready = not configure_clients or all(
        item.get("configured") or item.get("required") is False for item in configured
    )
    ready = clients_ready and daemon_ready
    settings["setup_complete"] = ready
    save_settings(settings)
    resources = resource_status(db, hooks_snapshot=daemon_status)
    return {
        "ready": ready,
        "daemon": {
            "required": not daemon_disabled,
            "ready": daemon_ready,
            "reason": daemon_reason,
        },
        "detected": harnesses,
        "configured": configured,
        "history": {
            "approved": history,
            "state": history_state,
            "projects": settings["history_projects"] or "all-discovered",
            "reasoning_included": False,
        },
        "current_project": {
            "id": current_project.project_id,
            "aliases": list(current_project.aliases),
        },
        "resources": resources,
    }


def configure(key: str, value: str | None) -> dict[str, Any]:
    settings = load_settings()
    if value is None:
        return {"key": key, "value": settings.get(key)}
    normalized = key.replace("-", "_")
    allowed = {"notifications"}
    if normalized not in allowed:
        raise ValueError(f"unknown setting: {key}")
    if normalized == "notifications" and value not in {"quiet", "detailed", "off"}:
        raise ValueError("notifications must be quiet, detailed, or off")
    settings[normalized] = value
    save_settings(settings)
    return {"key": normalized, "value": value}


def cleanup_preview() -> dict[str, Any]:
    runtime = Path(os.environ.get("AGENTROOTS_HOOK_RUNTIME", data_dir() / "hooks"))
    models = cache_dir() if os.environ.get("AGENTROOTS_MODEL_CACHE") else cache_dir() / "models"
    roots = [runtime / "spool", models / "vector-indexes"]
    items = [{"path": str(path), "bytes": _size(path)} for path in roots if path.exists()]
    return {
        "preview": True,
        "reclaimable_bytes": sum(item["bytes"] for item in items),
        "items": items,
        "protected": [
            str(db_path()), str(registry_path()), "accepted knowledge", "source conversations",
            "external artifacts",
        ],
    }


def _write_progress(**values: Any) -> None:
    path = data_dir() / "backfill" / "progress.json"
    ensure_private_directory(path.parent, tighten_existing=True)
    existing: dict[str, Any] = {}
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    atomic_write_private_text(path, json.dumps({**existing, **values}, indent=2) + "\n")


def _start_backfill_worker() -> None:
    progress = data_dir() / "backfill" / "progress.json"
    try:
        current = json.loads(progress.read_text(encoding="utf-8"))
        if current.get("state") == "indexing" and time.time() - float(current.get("updated", 0)) < 300:
            return
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    _write_progress(state="indexing", phase="starting", updated=time.time())
    subprocess.Popen(
        [sys.executable, "-m", "agentroots.onboarding", "backfill"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        start_new_session=os.name != "nt",
    )


def run_backfill() -> None:
    from .backfill import discover_codex, discover_opencode, export_sources
    from .episodes import EpisodeStore
    from .hooks import extract_episode_backfill, preferred_extractor

    def approved() -> dict[str, Any] | None:
        settings = load_settings()
        return settings if settings.get("history_consent") is True else None

    try:
        history_settings = approved()
        if history_settings is None:
            _write_progress(state="disabled", phase="consent-required", updated=time.time())
            return
        sources = []
        known = _known_sources()
        for root in known["codex"]:
            if root.exists():
                if approved() is None:
                    _write_progress(state="disabled", phase="consent-revoked", updated=time.time())
                    return
                sources.extend(discover_codex(root, source_host="local"))
        for source_db in known["opencode"]:
            if source_db.exists():
                if approved() is None:
                    _write_progress(state="disabled", phase="consent-revoked", updated=time.time())
                    return
                sources.extend(discover_opencode(source_db, source_host="local"))
        projects = sorted({source.project_id for source in sources})
        selected_projects = set(history_settings.get("history_projects", []))
        if selected_projects:
            projects = [project for project in projects if project in selected_projects]
        _write_progress(
            state="indexing", phase="importing", projects=len(projects), sessions=len(sources),
            completed_projects=0, updated=time.time(),
        )
        database = Database(db_path())
        store = EpisodeStore(database)
        root = data_dir() / "backfill"
        ensure_private_directory(root, tighten_existing=True)
        imported = 0
        candidates = 0
        for index, project in enumerate(projects, 1):
            selected = [source for source in sources if source.project_id == project]
            bundle = root / "bundles" / f"{project}.jsonl"
            ensure_private_directory(bundle.parent, tighten_existing=True)
            if approved() is None:
                _write_progress(state="disabled", phase="consent-revoked", updated=time.time())
                return
            export_sources(selected, bundle, approved=True)
            result = store.import_history_jsonl(bundle, project)
            try:
                bundle.unlink()
            except OSError:
                pass
            imported += int(result["imported"])
            _write_progress(
                state="indexing", phase="extracting", current_project=project,
                completed_projects=index - 1, imported=imported, candidates=candidates,
                updated=time.time(),
            )
            extraction = extract_episode_backfill(
                database, project, limit=500, extractor=preferred_extractor()
            )
            candidates += int(extraction["candidates"])
            _write_progress(
                state="indexing", phase="extracting", current_project=project,
                completed_projects=index, imported=imported, candidates=candidates,
                updated=time.time(),
            )
        _write_progress(
            state="complete", phase="complete", completed_projects=len(projects),
            imported=imported, candidates=candidates, updated=time.time(),
        )
    except (
        OSError, RuntimeError, ValueError, TypeError, ImportError, KeyError,
        sqlite3.Error, json.JSONDecodeError,
    ) as exc:
        _write_progress(state="failed", error=f"{type(exc).__name__}: {exc}"[:1000], updated=time.time())


if __name__ == "__main__" and sys.argv[1:] == ["backfill"]:
    run_backfill()


def format_bytes(value: int) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if amount < 1024 or unit == "TB":
            return f"{amount:.0f} {unit}" if unit in {"B", "KB"} else f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{amount:.2f} TB"
