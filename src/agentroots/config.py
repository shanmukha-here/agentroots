from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path, user_config_path, user_data_path

from .private_fs import atomic_write_private_text, ensure_private_directory

DEFAULT_SETTINGS: dict[str, Any] = {
    "notifications": "quiet",
    "history_consent": False,
    "configured_harnesses": [],
}


def db_path() -> Path:
    configured = os.environ.get("AGENTROOTS_DB") or os.environ.get("RESEARCH_STATE_DB")
    return Path(configured) if configured else user_data_path("agentroots") / "state.sqlite3"


def data_dir() -> Path:
    return db_path().parent


def cache_dir() -> Path:
    configured = os.environ.get("AGENTROOTS_MODEL_CACHE")
    return Path(configured) if configured else user_cache_path("agentroots")


def settings_path() -> Path:
    configured = os.environ.get("AGENTROOTS_CONFIG")
    return Path(configured) if configured else user_config_path("agentroots") / "config.json"


def load_settings() -> dict[str, Any]:
    path = settings_path()
    if not path.exists():
        return dict(DEFAULT_SETTINGS)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return dict(DEFAULT_SETTINGS)
    return {**DEFAULT_SETTINGS, **(value if isinstance(value, dict) else {})}


def save_settings(settings: dict[str, Any]) -> Path:
    path = settings_path()
    default_parent = user_config_path("agentroots")
    tighten = path.parent == default_parent and "AGENTROOTS_CONFIG" not in os.environ
    ensure_private_directory(path.parent, tighten_existing=tighten)
    atomic_write_private_text(path, json.dumps(settings, indent=2) + "\n")
    return path


def mlflow_url() -> str | None:
    return os.environ.get("AGENTROOTS_MLFLOW_URL")


def mlflow_token() -> str | None:
    return os.environ.get("AGENTROOTS_MLFLOW_TOKEN")
