from __future__ import annotations

import copy
import hashlib
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from agentroots.db import Database
from agentroots.hooks import HeuristicExtractor, HookEngine
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.service import GovernanceError, ResearchService


def bind_project(monkeypatch: pytest.MonkeyPatch, root: Path, project: str = "p") -> None:
    registry = root / "projects.json"
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(registry))
    resolve_project_identity(root, configured=project, path=registry)


def test_history_candidate_promotes_with_exact_span(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    engine = HookEngine(database, HeuristicExtractor(), allow_semantic=False)
    sentence = "The cosine loss experiment failed on rare-class recall with fixed seeds."
    engine.handle(
        {
            "project_id": "paper",
            "hook_event_name": "UserPromptSubmit",
            "event_id": "old-message",
            "session_id": "opencode-old",
            "prompt": sentence,
            "harness": "opencode",
        },
        extract=True,
    )

    service = ResearchService(database)
    candidates = service.list_candidates("paper")
    assert len(candidates) == 1
    promoted = service.promote_candidate(candidates[0]["id"], actor="codex-main")
    evidence = promoted["record"]["evidence"][0]
    assert evidence["kind"] == "episode-span"
    assert evidence["metadata"]["verification"]["status"] == "reference_valid"
    assert promoted["candidate"]["status"] == "promoted"
    assert promoted["record"]["creator"] == "codex-main"

    provisional = service.review(
        promoted["record"]["id"], actor="codex-main", verdict="provisional"
    )
    with pytest.raises(GovernanceError, match="creator cannot accept"):
        service.review(provisional["id"], actor="codex-main", verdict="accepted")
    artifact = tmp_path / "independent-check.json"
    artifact.write_text('{"rare_class_recall":"regressed"}', encoding="utf-8")
    service.link_evidence(
        EvidenceLink(
            provisional["id"],
            artifact.resolve().as_uri(),
            "artifact",
            content_hash=hashlib.sha256(artifact.read_bytes()).hexdigest(),
        ),
        actor="independent-reviewer",
    )
    accepted = service.review(
        provisional["id"], actor="independent-reviewer", verdict="accepted"
    )
    assert accepted["status"] == "accepted"


def test_candidate_reject_and_merge_preserve_resolution(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    engine = HookEngine(database, HeuristicExtractor(), allow_semantic=False)
    for event_id, sentence in (
        ("one", "The first experiment failed because the dataset digest was wrong."),
        ("two", "The second experiment failed because the cache was stale."),
    ):
        engine.handle(
            {
                "project_id": "p",
                "hook_event_name": "UserPromptSubmit",
                "event_id": event_id,
                "session_id": "old",
                "prompt": sentence,
            },
            extract=True,
        )
    service = ResearchService(database)
    candidates = service.list_candidates("p")
    target = service.propose(
        project="p", type="finding", title="Known failure", body="Existing record", creator="worker"
    )
    merged = service.merge_candidate(candidates[0]["id"], record_id=target["id"], actor="parent")
    assert merged["candidate"]["status"] == "merged"
    assert (
        merged["record"]["evidence"][0]["metadata"]["verification"]["status"]
        == "reference_valid"
    )
    rejected = service.reject_candidate(
        candidates[1]["id"], actor="parent", reason="Duplicate wording without a new result."
    )
    assert rejected["status"] == "rejected"


def test_abandoned_candidate_resolution_lease_recovers(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    engine = HookEngine(database, HeuristicExtractor(), allow_semantic=False)
    engine.handle(
        {
            "project_id": "p",
            "hook_event_name": "UserPromptSubmit",
            "event_id": "old",
            "session_id": "old-session",
            "prompt": "The experiment failed because the cached labels were stale.",
        },
        extract=True,
    )
    service = ResearchService(database)
    candidate = service.list_candidates("p")[0]
    expired = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    with database.connect() as conn:
        conn.execute(
            "UPDATE extraction_candidates SET status='promoting',"
            "resolution_started_at=?,resolution_actor='crashed-agent' WHERE id=?",
            (expired, candidate["id"]),
        )
    promoted = service.promote_candidate(candidate["id"], actor="retrying-agent")
    assert promoted["candidate"]["status"] == "promoted"
    assert promoted["candidate"]["resolution_started_at"] is None


def test_frontier_is_derived_from_missing_graph_steps(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    hypothesis = service.propose(
        project="p", type="hypothesis", title="H", body="A testable hypothesis", creator="a"
    )
    reasons = service.frontier("p")[0]["frontier"]["reasons"]
    assert "untested_hypothesis" in reasons

    experiment = service.propose(
        project="p", type="experiment", title="E", body="Test H", creator="a"
    )
    service.link(experiment["id"], hypothesis["id"], "tests", "a")
    by_id = {item["id"]: item for item in service.frontier("p")}
    assert "untested_hypothesis" not in by_id[hypothesis["id"]]["frontier"]["reasons"]
    assert "experiment_without_run" in by_id[experiment["id"]]["frontier"]["reasons"]

    run = service.propose(project="p", type="run_ref", title="R", body="Run", creator="a")
    service.link(experiment["id"], run["id"], "produced", "a")
    by_id = {item["id"]: item for item in service.frontier("p")}
    assert "experiment_without_run" not in by_id[experiment["id"]]["frontier"]["reasons"]
    assert "run_without_observation" in by_id[run["id"]]["frontier"]["reasons"]

    observation = service.propose(
        project="p", type="observation", title="O", body="Observed result", creator="a"
    )
    service.link(observation["id"], run["id"], "derived_from", "a")
    by_id = {item["id"]: item for item in service.frontier("p")}
    assert "run_without_observation" not in by_id[run["id"]]["frontier"]["reasons"]
    assert "observation_without_finding" in by_id[observation["id"]]["frontier"]["reasons"]


def test_sync_rejects_accepted_proposal_and_rolls_back_batch(tmp_path: Path) -> None:
    source = ResearchService(Database(tmp_path / "source.sqlite3"))
    record = source.propose(
        project="p", type="claim", title="Unsafe", body="No evidence", creator="worker"
    )
    proposal = copy.deepcopy(source.sync_export("p")[0])
    proposal["payload"]["status"] = "accepted"
    destination = ResearchService(Database(tmp_path / "destination.sqlite3"))
    with pytest.raises(GovernanceError, match="candidate invariants"):
        destination.import_events([proposal], expected_project="p")
    assert destination.query("p") == []

    valid_proposal = copy.deepcopy(source.sync_export("p")[0])
    provisional_review = {
        "event_id": "valid-provisional-review",
        "project": "p",
        "record_id": record["id"],
        "revision": 2,
        "event_type": "reviewed",
        "actor": "reviewer",
        "at": datetime.now(UTC).isoformat(),
        "payload": {"verdict": "provisional", "comment": "reviewing"},
        "idempotency_key": None,
    }
    self_review = {
        "event_id": "malicious-review",
        "project": "p",
        "record_id": record["id"],
        "revision": 3,
        "event_type": "reviewed",
        "actor": "worker",
        "at": datetime.now(UTC).isoformat(),
        "payload": {"verdict": "accepted", "comment": "trust me"},
        "idempotency_key": None,
    }
    with pytest.raises(GovernanceError, match="creator cannot accept"):
        destination.import_events(
            [valid_proposal, provisional_review, self_review], expected_project="p"
        )
    assert destination.query("p") == []


def test_project_isolation_and_idempotency_are_scoped(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    first = service.propose(
        project="alpha", type="goal", title="A", body="a", creator="worker", idempotency_key="same"
    )
    second = service.propose(
        project="beta", type="goal", title="B", body="b", creator="worker", idempotency_key="same"
    )
    assert first["id"] != second["id"]
    with pytest.raises(GovernanceError, match="same project"):
        service.link(first["id"], second["id"], "depends_on", "worker")
    with service.db.connect() as conn, pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO links(source_id,target_id,relation,metadata) VALUES(?,?,?,?)",
            (first["id"], second["id"], "depends_on", "{}"),
        )


def test_git_evidence_verifies_then_marks_accepted_record_stale(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "fact.py"
    source.write_text("result = 1\n", encoding="utf-8")
    bind_project(monkeypatch, tmp_path)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    finding = service.propose(
        project="p", type="finding", title="Code fact", body="The result is one.", creator="worker"
    )
    service.review(finding["id"], actor="reviewer", verdict="provisional")
    linked = service.link_evidence(
        EvidenceLink(finding["id"], "fact.py", "git-file", content_hash=digest),
        actor="reviewer",
        project_root=tmp_path,
    )
    assert linked["evidence"][0]["metadata"]["verification"]["status"] == "verified"
    service.review(finding["id"], actor="reviewer", verdict="accepted")
    source.write_text("result = 2\n", encoding="utf-8")
    result = service.revalidate_evidence("p", project_root=tmp_path, mark_stale=True)
    assert result["stale_record_ids"] == [finding["id"]]
    assert service.get_record(finding["id"])["status"] == "stale"


def test_generic_uri_does_not_satisfy_verified_claim_gate(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    claim = service.propose(project="p", type="claim", title="C", body="Claim", creator="worker")
    service.review(claim["id"], actor="reviewer", verdict="provisional")
    linked = service.link_evidence(
        EvidenceLink(claim["id"], "https://example.invalid/result", "url"), actor="reviewer"
    )
    assert linked["evidence"][0]["metadata"]["verification"]["status"] == "reference_valid"
    with pytest.raises(GovernanceError, match="mechanically verified"):
        service.review(claim["id"], actor="reviewer", verdict="accepted")
