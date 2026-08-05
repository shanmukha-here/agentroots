from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
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
    experiment_id: str = ""
    run_name: str = ""
    start_time: int | None = None
    end_time: int | None = None
    artifact_uri: str = ""
    tags: dict[str, str] = field(default_factory=dict)
    datasets: tuple[dict[str, Any], ...] = ()
    artifacts: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def provenance_hash(self) -> str:
        """Hash the run snapshot used as evidence without downloading artifacts."""
        payload = self.to_dict()
        payload.pop("uri", None)
        payload.pop("summary", None)
        payload["datasets"] = sorted(payload["datasets"], key=lambda item: json.dumps(item, sort_keys=True))
        payload["artifacts"] = sorted(
            payload["artifacts"], key=lambda item: json.dumps(item, sort_keys=True)
        )
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()


class Adapter(Protocol):
    """Read-only adapter. Implementations must never execute stored commands."""

    def get_run(self, run_id: str) -> ExternalRun: ...
    def compare_runs(self, run_ids: list[str]) -> list[ExternalRun]: ...
