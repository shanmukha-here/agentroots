from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ExternalRun:
    adapter: str
    run_id: str
    uri: str
    status: str
    metrics: dict[str, float] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    summary: str = ""


class Adapter(Protocol):
    """Read-only adapter. Implementations must never execute stored commands."""

    def get_run(self, run_id: str) -> ExternalRun: ...
    def compare_runs(self, run_ids: list[str]) -> list[ExternalRun]: ...
