import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.service import ResearchService


def test_fresh_agents_share_reviewed_state_without_transcripts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "shared.sqlite3"
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))
    project_root = tmp_path / "project"
    project_root.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(project_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    resolve_project_identity(project_root, configured="continuity")
    first_agent = ResearchService(Database(db))
    finding = first_agent.propose(
        project="continuity",
        type="finding",
        title="Large batch failed",
        body="Batch size 4096 exhausted memory. Do not repeat.",
        creator="codex-main",
        metadata={"failed": True},
    )
    trace = project_root / "oom-4096.json"
    command = "train --batch-size 4096"
    trace.write_text(
        json.dumps(
            {
                "schema": "agentroots.test-receipt.v1",
                "command": command,
                "exit_code": 1,
                "summary": "CUDA out of memory",
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    first_agent.link_evidence(
        EvidenceLink(
            finding["id"],
            trace.name,
            "git-file",
            "Versioned OOM result",
            hashlib.sha256(trace.read_bytes()).hexdigest(),
            {"command_label": command},
        ),
        actor="codex-main",
        project_root=project_root,
    )
    first_agent.review(finding["id"], actor="deepseek-reviewer", verdict="provisional")
    first_agent.review(finding["id"], actor="codex-reviewer", verdict="accepted")

    fresh_agent = ResearchService(Database(db))
    packet = fresh_agent.context("continuity", query="batch memory", token_budget=500)

    assert packet["estimated_tokens"] <= 500
    assert finding["id"] in packet["record_ids"]
    accepted_id = packet["sections"]["accepted_findings"][0]
    failed_id = packet["sections"]["failed_attempts"][0]
    assert packet["records"][accepted_id]["title"] == "Large batch failed"
    assert packet["records"][failed_id]["metadata"]["failed"] is True
    assert "transcript" not in str(packet).lower()
