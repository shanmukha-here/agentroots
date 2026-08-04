from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .service import ResearchService


def import_hef(
    service: ResearchService, project: str, entries: Iterable[dict[str, Any]], actor: str
) -> list[str]:
    """Import compact agentic-experiments H→E→F-shaped data, never execution commands."""
    ids: list[str] = []
    mapping = {"H": "hypothesis", "E": "experiment", "F": "finding"}
    for entry in entries:
        kind = mapping[str(entry["kind"]).upper()]
        record = service.propose(
            project=project,
            type=kind,
            title=str(entry["title"]),
            body=str(entry.get("summary", "")),
            creator=actor,
            mode=str(entry.get("mode", "exploratory")),
            metadata={"source": "agentic-experiments", "external_id": entry.get("id")},
            idempotency_key=f"hef:{project}:{entry.get('id', entry['title'])}",
        )
        ids.append(record["id"])
    return ids


def import_signac_jobs(
    service: ResearchService, project: str, jobs: Iterable[dict[str, Any]], actor: str
) -> list[str]:
    """Import signac job summaries; statepoints/results only, no job execution."""
    ids: list[str] = []
    for job in jobs:
        job_id = str(job["id"])
        record = service.propose(
            project=project,
            type="run_ref",
            title=f"signac job {job_id}",
            body=str(job.get("summary", "")),
            creator=actor,
            mode=str(job.get("mode", "exploratory")),
            metadata={
                "source": "signac",
                "job_id": job_id,
                "statepoint": job.get("statepoint", {}),
                "selected_results": job.get("results", {}),
            },
            idempotency_key=f"signac:{project}:{job_id}",
        )
        ids.append(record["id"])
    return ids
