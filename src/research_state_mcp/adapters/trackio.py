from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .base import ExternalRun


class TrackioAdapter:
    """Dependency-injected read-only Trackio interface; API versions differ."""

    def __init__(self, fetch: Callable[[str], dict[str, Any]], base_url: str):
        self.fetch, self.base_url = fetch, base_url.rstrip("/")

    def get_run(self, run_id: str) -> ExternalRun:
        d = self.fetch(run_id)
        return ExternalRun(
            "trackio",
            run_id,
            f"{self.base_url}/runs/{run_id}",
            str(d.get("status", "unknown")),
            d.get("metrics", {}),
            d.get("params", {}),
            d.get("summary", ""),
        )

    def compare_runs(self, run_ids: list[str]) -> list[ExternalRun]:
        return [self.get_run(x) for x in run_ids]
