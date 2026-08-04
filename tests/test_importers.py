from pathlib import Path

from agentroots.db import Database
from agentroots.importers import import_hef, import_signac_jobs
from agentroots.service import ResearchService


def test_compact_importers_are_idempotent(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.db"))
    hef = [{"id": "h1", "kind": "H", "title": "Hypothesis", "summary": "Compact"}]
    first = import_hef(service, "p", hef, "importer")
    second = import_hef(service, "p", hef, "importer")
    assert first == second
    jobs = [{"id": "j1", "statepoint": {"seed": 1}, "results": {"score": 0.5}}]
    import_signac_jobs(service, "p", jobs, "importer")
    assert {record["type"] for record in service.query("p")} == {"hypothesis", "run_ref"}
