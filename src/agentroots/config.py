from __future__ import annotations

import os
from pathlib import Path

from platformdirs import user_data_path


def db_path() -> Path:
    configured = os.environ.get("AGENTROOTS_DB") or os.environ.get("RESEARCH_STATE_DB")
    return Path(configured) if configured else user_data_path("agentroots") / "state.sqlite3"
