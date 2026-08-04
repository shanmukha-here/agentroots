from pathlib import Path

from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.service import ResearchService


def test_fresh_agents_share_reviewed_state_without_transcripts(tmp_path: Path) -> None:
    db = tmp_path / "shared.sqlite3"
    first_agent = ResearchService(Database(db))
    finding = first_agent.propose(
        project="continuity",
        type="finding",
        title="Large batch failed",
        body="Batch size 4096 exhausted memory. Do not repeat.",
        creator="codex-main",
        metadata={"failed": True},
    )
    first_agent.link_evidence(
        EvidenceLink(finding["id"], "mlflow://runs/oom-4096", "mlflow-run", "OOM"),
        actor="codex-main",
    )
    first_agent.review(finding["id"], actor="deepseek-reviewer", verdict="provisional")
    first_agent.review(finding["id"], actor="codex-reviewer", verdict="accepted")

    fresh_agent = ResearchService(Database(db))
    packet = fresh_agent.context("continuity", query="batch memory", token_budget=500)

    assert packet["estimated_tokens"] <= 500
    assert finding["id"] in packet["record_ids"]
    assert packet["sections"]["accepted_findings"][0]["title"] == "Large batch failed"
    assert packet["sections"]["failed_attempts"][0]["metadata"]["failed"] is True
    assert "transcript" not in str(packet).lower()
