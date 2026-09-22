from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from platformdirs import user_data_path

from .private_fs import atomic_write_private_text, ensure_private_directory

_REGISTRY_VERSION = 1
_THREAD_LOCK = threading.Lock()
_CACHE: dict[tuple[str, str, str, str, str], tuple[int, ProjectIdentity]] = {}


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return normalized[:48] or "project"


def _normalized_path(value: str) -> str:
    normalized = value.replace("\\", "/").rstrip("/")
    windows_path = bool(re.match(r"^[A-Za-z]:/", normalized)) or normalized.startswith("//")
    if windows_path:
        return normalized.casefold()
    return normalized


def _normalized_remote(value: str) -> str:
    """Normalize common Git transports to one host and path identity.

    HTTPS remains the canonical spelling so IDs already derived from HTTPS remotes
    stay stable. User names, default transport ports, query strings, and fragments
    do not identify a project.
    """

    remote = value.strip()
    if not remote:
        return ""
    host = ""
    path = ""
    port: int | None = None
    scheme = ""
    if "://" in remote:
        try:
            parsed = urlsplit(remote)
            host = parsed.hostname or ""
            path = parsed.path
            port = parsed.port
            scheme = parsed.scheme.lower()
        except ValueError:
            return remote.rstrip("/").removesuffix(".git").casefold()
    else:
        scp = re.fullmatch(r"(?:[^/@:]+@)?([^/:]+):(.+)", remote)
        if scp and not re.match(r"^[A-Za-z]:[\\/]", remote):
            host, path = scp.groups()
            scheme = "ssh"
        else:
            return remote.rstrip("/").removesuffix(".git").casefold()
    clean_path = re.sub(r"/+", "/", path).strip("/")
    clean_path = clean_path.removesuffix(".git")
    default_port = (scheme in {"ssh", "git+ssh"} and port == 22) or (
        scheme == "https" and port == 443
    ) or (scheme == "http" and port == 80)
    authority = host if port is None or default_port else f"{host}:{port}"
    if not authority or not clean_path:
        return remote.rstrip("/").removesuffix(".git").casefold()
    return f"https://{authority}/{clean_path}".casefold()


def _legacy_normalized_remote(value: str) -> str:
    return value.strip().lower().removesuffix(".git")


def _remote_fingerprints(value: str) -> tuple[str, ...]:
    normalized = {_normalized_remote(value), _legacy_normalized_remote(value)} - {""}
    return tuple(sorted(_fingerprint(item) for item in normalized))


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def registry_path() -> Path:
    configured = os.environ.get("AGENTROOTS_PROJECT_REGISTRY")
    if configured:
        return Path(configured)
    database = os.environ.get("AGENTROOTS_DB") or os.environ.get("RESEARCH_STATE_DB")
    if database:
        return Path(database).parent / "projects.json"
    return user_data_path("agentroots") / "projects.json"


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    project_id: str
    aliases: tuple[str, ...]
    root: str
    remote: str


def _git_metadata(directory: str) -> tuple[str, str]:
    path = Path(directory).expanduser()
    root = path
    remote = ""
    if path.exists():
        try:
            root_text = _git_output(path, "rev-parse", "--show-toplevel", check=True)
            root = Path(root_text)
            remote = _git_output(root, "remote", "get-url", "origin")
        except (OSError, subprocess.SubprocessError):
            pass
    return str(root), remote


def _git_output(directory: Path, *arguments: str, check: bool = False) -> str:
    """Run a bounded Git query without pipe-reader deadlocks on Windows stdio servers."""

    with tempfile.TemporaryFile() as output:
        result = subprocess.run(
            ["git", "-C", str(directory), *arguments],
            check=check,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.DEVNULL,
            timeout=3,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode != 0:
            return ""
        output.seek(0)
        return output.read().decode("utf-8", errors="replace").strip()


def _derived_identity(root: str, remote: str) -> tuple[str, str]:
    identity = _normalized_remote(remote) or _normalized_path(root)
    remote_name = _normalized_remote(remote).rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    name = remote_name or Path(root.replace("\\", "/")).name
    project_id = f"{_slug(name)}-{_fingerprint(identity)[:10]}"
    return project_id, _slug(name)


def _empty_registry() -> dict[str, Any]:
    return {"version": _REGISTRY_VERSION, "projects": {}}


def _load_registry(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_registry()
    if not isinstance(value, dict) or not isinstance(value.get("projects"), dict):
        return _empty_registry()
    return value


@contextmanager
def _locked_registry(path: Path) -> Iterator[bool]:
    lock = path.with_suffix(path.suffix + ".lock")
    acquired = False
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(descriptor)
            acquired = True
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 10:
                    lock.unlink()
                    continue
            except OSError:
                pass
            time.sleep(0.02)
        except OSError:
            break
    try:
        yield acquired
    finally:
        if acquired:
            try:
                lock.unlink()
            except OSError:
                pass


def _matching_project(
    registry: dict[str, Any], path_fingerprint: str, remote_fingerprints: tuple[str, ...]
) -> str | None:
    projects = registry.get("projects", {})
    if not isinstance(projects, dict):
        return None
    for project_id, raw in projects.items():
        if not isinstance(raw, dict):
            continue
        paths = raw.get("path_fingerprints", [])
        remotes = raw.get("remote_fingerprints", [])
        if path_fingerprint and path_fingerprint in paths:
            return str(project_id)
        if remote_fingerprints and any(item in remotes for item in remote_fingerprints):
            return str(project_id)
    return None


def _persist(
    path: Path,
    project_id: str,
    aliases: tuple[str, ...],
    path_fingerprint: str,
    remote_fingerprints: tuple[str, ...],
) -> None:
    default = user_data_path("agentroots") / "projects.json"
    ensure_private_directory(path.parent, tighten_existing=path == default)
    with _THREAD_LOCK, _locked_registry(path) as acquired:
        if not acquired:
            return
        registry = _load_registry(path)
        projects = registry.setdefault("projects", {})
        migrated_aliases: set[str] = set()
        empty_projects: list[str] = []
        for other_id, other in list(projects.items()):
            if other_id == project_id or not isinstance(other, dict):
                continue
            if path_fingerprint:
                other["path_fingerprints"] = [
                    item for item in other.get("path_fingerprints", [])
                    if item != path_fingerprint
                ]
            if remote_fingerprints:
                other["remote_fingerprints"] = [
                    item for item in other.get("remote_fingerprints", [])
                    if item not in remote_fingerprints
                ]
            if not other.get("path_fingerprints") and not other.get("remote_fingerprints"):
                migrated_aliases.update(str(item) for item in other.get("aliases", []))
                migrated_aliases.add(str(other_id))
                empty_projects.append(str(other_id))
        for other_id in empty_projects:
            projects.pop(other_id, None)
        raw = projects.setdefault(project_id, {})
        raw["project_id"] = project_id
        raw["aliases"] = sorted(
            {str(item) for item in raw.get("aliases", [])} | set(aliases) | migrated_aliases
        )
        if path_fingerprint:
            raw["path_fingerprints"] = sorted(
                {str(item) for item in raw.get("path_fingerprints", [])} | {path_fingerprint}
            )
        if remote_fingerprints:
            raw["remote_fingerprints"] = sorted(
                {str(item) for item in raw.get("remote_fingerprints", [])}
                | set(remote_fingerprints)
            )
        registry["version"] = _REGISTRY_VERSION
        atomic_write_private_text(
            path, json.dumps(registry, indent=2, sort_keys=True) + "\n"
        )


def resolve_project_identity(
    directory: str | Path,
    *,
    configured: str | None = None,
    persist: bool = True,
    path: Path | None = None,
) -> ProjectIdentity:
    """Resolve one stable project identity across harnesses and history imports.

    The deterministic ID deliberately retains the original AgentRoots hash scheme so
    projects imported before the alias registry existed remain addressable.
    """
    selected = configured or os.environ.get("AGENTROOTS_PROJECT")
    location = path or registry_path()
    root, remote = _git_metadata(str(directory))
    cache_key = (
        _normalized_path(str(directory)),
        selected or "",
        str(location),
        _normalized_path(root),
        _normalized_remote(remote),
    )
    try:
        registry_mtime = location.stat().st_mtime_ns
    except OSError:
        registry_mtime = -1
    with _THREAD_LOCK:
        cached = _CACHE.get(cache_key)
    if cached is not None and cached[0] == registry_mtime:
        return cached[1]

    derived, leaf = _derived_identity(root, remote)
    path_key = _fingerprint(_normalized_path(root)) if root else ""
    remote_keys = _remote_fingerprints(remote)
    if not selected:
        registry = _load_registry(location)
        if remote_keys:
            selected = _matching_project(registry, "", remote_keys) or derived
        else:
            selected = _matching_project(registry, path_key, ()) or derived
    aliases = tuple(sorted({selected, derived, leaf}))
    if persist:
        _persist(location, selected, aliases, path_key, remote_keys)
    identity = ProjectIdentity(selected, aliases, root, remote)
    try:
        registry_mtime = location.stat().st_mtime_ns
    except OSError:
        registry_mtime = -1
    with _THREAD_LOCK:
        _CACHE[cache_key] = (registry_mtime, identity)
    return identity


def resolve_project_id(directory: str | Path, *, persist: bool = True) -> str:
    return resolve_project_identity(directory, persist=persist).project_id


def list_project_bindings(*, path: Path | None = None) -> list[dict[str, Any]]:
    """List public project IDs and aliases without exposing private fingerprints."""
    registry = _load_registry(path or registry_path())
    projects = registry.get("projects", {})
    if not isinstance(projects, dict):
        return []
    return [
        {
            "project_id": str(project_id),
            "aliases": sorted(
                {str(value) for value in raw.get("aliases", [])} | {str(project_id)}
            ),
        }
        for project_id, raw in sorted(projects.items())
        if isinstance(raw, dict)
    ]


def resolve_current_project(
    directory: str | Path | None = None,
    *,
    persist: bool = True,
    path: Path | None = None,
) -> ProjectIdentity:
    """Resolve the current checkout without accepting a project ID from content."""

    root = Path(directory if directory is not None else Path.cwd()).expanduser()
    if not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    return resolve_project_identity(root, persist=persist, path=path)


def project_matches_root(project_id: str, directory: str | Path) -> bool:
    """Check a supplied root against deterministic and previously registered identity."""

    root, remote = _git_metadata(str(directory))
    derived, _ = _derived_identity(root, remote)
    path_key = _fingerprint(_normalized_path(root)) if root else ""
    remote_keys = _remote_fingerprints(remote)
    registry = _load_registry(registry_path())
    registered = (
        _matching_project(registry, "", remote_keys)
        if remote_keys
        else _matching_project(registry, path_key, ())
    )
    return project_id == derived or project_id == registered
