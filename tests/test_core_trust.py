from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from agentroots.adapters.base import ExternalRun
from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.service import ConflictError, GovernanceError, ResearchService


def bind_project(monkeypatch: pytest.MonkeyPatch, root: Path, project: str = "p") -> None:
    registry = root / "projects.json"
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(registry))
    resolve_project_identity(root, configured=project, path=registry)


def artifact_link(record_id: str, path: Path, *, summary: str = "artifact") -> EvidenceLink:
    return EvidenceLink(
        record_id,
        path.resolve().as_uri(),
        "artifact",
        summary,
        hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def test_evidence_revision_reopens_acceptance_and_replays(tmp_path: Path) -> None:
    artifact = tmp_path / "result.json"
    artifact.write_text('{"score":0.91}', encoding="utf-8")
    source = ResearchService(Database(tmp_path / "source.sqlite3"))
    record = source.propose(
        project="p", type="finding", title="Result", body="Score is stable.", creator="worker"
    )
    source.review(record["id"], actor="reviewer", verdict="provisional")
    source.link_evidence(artifact_link(record["id"], artifact, summary="original"), actor="worker")
    accepted = source.review(record["id"], actor="reviewer", verdict="accepted")

    revised = source.link_evidence(
        artifact_link(record["id"], artifact, summary="corrected interpretation"),
        actor="worker",
    )
    assert revised["status"] == "provisional"
    assert revised["revision"] == accepted["revision"] + 1
    evidence_events = [
        event for event in source.sync_export("p") if event["event_type"].startswith("evidence_")
    ]
    assert [event["event_type"] for event in evidence_events] == [
        "evidence_linked",
        "evidence_revised",
    ]
    assert evidence_events[-1]["payload"]["previous"]["summary"] == "original"
    accepted_event = next(
        event
        for event in source.sync_export("p")
        if event["event_type"] == "reviewed" and event["payload"]["verdict"] == "accepted"
    )
    assert "id" not in accepted_event["payload"]["evidence_snapshot"][0]

    destination = ResearchService(Database(tmp_path / "destination.sqlite3"))
    events = source.sync_export("p")
    assert destination.import_events(events, expected_project="p") == len(events)
    restored = destination.get_record(record["id"])
    assert restored["status"] == "provisional"
    assert restored["revision"] == revised["revision"]
    assert restored["evidence"][0]["summary"] == "corrected interpretation"
    assert destination.sync_export("p") == events


def test_import_rejects_tampered_snapshot_actor_and_duplicate_event(tmp_path: Path) -> None:
    artifact = tmp_path / "proof.json"
    artifact.write_text('{"ok":true}', encoding="utf-8")
    source = ResearchService(Database(tmp_path / "source.sqlite3"))
    record = source.propose(
        project="p", type="claim", title="Claim", body="Supported claim.", creator="worker"
    )
    source.review(record["id"], actor="reviewer", verdict="provisional")
    source.link_evidence(artifact_link(record["id"], artifact), actor="reviewer")
    source.review(record["id"], actor="reviewer", verdict="accepted")
    events = source.sync_export("p")

    actor_forgery = copy.deepcopy(events[0])
    actor_forgery["actor"] = "different-actor"
    empty = ResearchService(Database(tmp_path / "actor.sqlite3"))
    with pytest.raises(GovernanceError, match="candidate invariants"):
        empty.import_events([actor_forgery], expected_project="p")

    tampered = copy.deepcopy(events)
    accepted = next(
        event
        for event in tampered
        if event["event_type"] == "reviewed" and event["payload"]["verdict"] == "accepted"
    )
    accepted["payload"]["evidence_snapshot"][0]["content_hash"] = "0" * 64
    snapshot_destination = ResearchService(Database(tmp_path / "snapshot.sqlite3"))
    with pytest.raises(GovernanceError, match="snapshot does not match"):
        snapshot_destination.import_events(tampered, expected_project="p")
    assert snapshot_destination.query("p") == []

    destination = ResearchService(Database(tmp_path / "duplicate.sqlite3"))
    assert destination.import_events([events[0]], expected_project="p") == 1
    duplicate = copy.deepcopy(events[0])
    duplicate["payload"]["title"] = "different"
    with pytest.raises(ConflictError, match="different content"):
        destination.import_events([duplicate], expected_project="p")
    assert destination.get_record(record["id"])["title"] == "Claim"


def test_idempotency_keys_are_scoped_to_each_record(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    first = service.propose(project="p", type="goal", title="First", body="a", creator="worker")
    second = service.propose(project="p", type="goal", title="Second", body="b", creator="worker")
    first_review = service.review(
        first["id"], actor="reviewer", verdict="provisional", idempotency_key="same"
    )
    second_review = service.review(
        second["id"], actor="reviewer", verdict="provisional", idempotency_key="same"
    )
    assert first_review["id"] == first["id"]
    assert second_review["id"] == second["id"]

    first_revision = service.revise(
        first["id"], actor="worker", body="first revised", idempotency_key="same"
    )
    second_revision = service.revise(
        second["id"], actor="worker", body="second revised", idempotency_key="same"
    )
    assert first_revision["body"] == "first revised"
    assert second_revision["body"] == "second revised"


def test_evidence_has_scoped_idempotency_and_optimistic_revision(
    tmp_path: Path,
) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    first_file = tmp_path / "first.json"
    second_file = tmp_path / "second.json"
    first_file.write_text('{"result":1}', encoding="utf-8")
    second_file.write_text('{"result":2}', encoding="utf-8")
    first = service.propose(
        project="p", type="finding", title="First", body="body", creator="worker"
    )
    second = service.propose(
        project="p", type="finding", title="Second", body="body", creator="worker"
    )

    linked = service.link_evidence(
        artifact_link(first["id"], first_file, summary="original"),
        actor="reviewer",
        expected_record_revision=1,
        expected_evidence_revision=0,
        idempotency_key="same",
    )
    retried = service.link_evidence(
        artifact_link(first["id"], first_file, summary="ignored retry payload"),
        actor="reviewer",
        idempotency_key="same",
    )
    service.link_evidence(
        artifact_link(second["id"], second_file, summary="second"),
        actor="reviewer",
        idempotency_key="same",
    )
    assert linked["evidence"][0]["revision"] == 1
    assert retried["evidence"][0]["summary"] == "original"
    assert service.get_record(second["id"])["evidence"][0]["summary"] == "second"

    with pytest.raises(ConflictError, match="evidence revision conflict"):
        service.link_evidence(
            artifact_link(first["id"], first_file, summary="changed"),
            actor="reviewer",
            expected_evidence_revision=7,
        )
    revised = service.link_evidence(
        artifact_link(first["id"], first_file, summary="changed"),
        actor="reviewer",
        expected_evidence_revision=1,
    )
    assert revised["evidence"][0]["revision"] == 2
    with pytest.raises(ConflictError, match="record revision conflict"):
        service.link_evidence(
            artifact_link(first["id"], first_file, summary="changed again"),
            actor="reviewer",
            expected_record_revision=99,
        )


def test_existing_link_metadata_cannot_change_silently(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    goal = service.propose(
        project="p", type="goal", title="Goal", body="body", creator="worker"
    )
    question = service.propose(
        project="p", type="question", title="Question", body="body", creator="worker"
    )
    linked = service.link(
        goal["id"], question["id"], "decomposes", "worker", metadata={"reason": "first"}
    )
    assert linked["id"] == goal["id"]
    assert service.link(
        goal["id"], question["id"], "decomposes", "worker", metadata={"reason": "first"}
    )["id"] == goal["id"]
    with pytest.raises(ConflictError, match="different metadata"):
        service.link(
            goal["id"],
            question["id"],
            "decomposes",
            "worker",
            metadata={"reason": "changed"},
        )


def test_event_and_review_rows_are_database_append_only(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    record = service.propose(project="p", type="goal", title="Goal", body="body", creator="worker")
    service.review(record["id"], actor="reviewer", verdict="provisional")
    with service.db.connect() as conn, pytest.raises(
        sqlite3.IntegrityError, match="events are append-only"
    ):
        conn.execute("UPDATE events SET actor='forged' WHERE record_id=?", (record["id"],))
    with service.db.connect() as conn, pytest.raises(
        sqlite3.IntegrityError, match="events are append-only"
    ):
        conn.execute("DELETE FROM events WHERE record_id=?", (record["id"],))
    with service.db.connect() as conn, pytest.raises(
        sqlite3.IntegrityError, match="reviews are append-only"
    ):
        conn.execute("UPDATE reviews SET actor='forged' WHERE record_id=?", (record["id"],))
    with service.db.connect() as conn, pytest.raises(
        sqlite3.IntegrityError, match="reviews are append-only"
    ):
        conn.execute("DELETE FROM reviews WHERE record_id=?", (record["id"],))


def test_self_authored_test_receipt_is_not_mechanical_proof(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    record = service.propose(
        project="p", type="claim", title="Tests passed", body="The suite passed.", creator="worker"
    )
    service.review(record["id"], actor="reviewer", verdict="provisional")
    receipt = tmp_path / "receipt.json"
    command = "pytest -q"
    receipt.write_text(
        json.dumps(
            {
                "schema": "agentroots.test-receipt.v1",
                "command": command,
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    linked = service.link_evidence(
        EvidenceLink(
            record["id"],
            "test://caller/receipt",
            "test-receipt",
            content_hash=hashlib.sha256(receipt.read_bytes()).hexdigest(),
            metadata={
                "command": command,
                "exit_code": 0,
                "trace_uri": receipt.resolve().as_uri(),
            },
        ),
        actor="worker",
    )
    assert linked["evidence"][0]["metadata"]["verification"]["status"] == "reference_valid"
    with pytest.raises(GovernanceError, match="mechanically verified"):
        service.review(record["id"], actor="reviewer", verdict="accepted")


def test_tracker_sync_preserves_reference_but_not_source_verification(tmp_path: Path) -> None:
    source = ResearchService(Database(tmp_path / "source.sqlite3"))
    record = source.propose(
        project="p", type="finding", title="Metric", body="Metric improved.", creator="worker"
    )
    source.review(record["id"], actor="reviewer", verdict="provisional")
    source.link_external_run(
        record["id"],
        ExternalRun(
            adapter="mlflow",
            run_id="run-1",
            uri="mlflow://runs/run-1",
            status="FINISHED",
            metrics={"score": 0.9},
        ),
        actor="worker",
    )
    destination = ResearchService(Database(tmp_path / "destination.sqlite3"))
    events = source.sync_export("p")
    assert destination.import_events(events, expected_project="p") == len(events)
    imported = destination.get_record(record["id"])
    assert imported["evidence"][0]["metadata"]["verification"]["status"] == "reference_valid"

    source.review(record["id"], actor="reviewer", verdict="accepted")
    with pytest.raises(GovernanceError, match="snapshot does not match"):
        destination.import_events(source.sync_export("p"), expected_project="p")
    assert destination.get_record(record["id"])["status"] == "provisional"


def test_git_staleness_only_revalidates_git_and_uses_drift_epochs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bind_project(monkeypatch, tmp_path)
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    source = tmp_path / "fact.py"
    source.write_text("value = 1\n", encoding="utf-8")
    artifact = tmp_path / "note.json"
    artifact.write_text('{"note":"stable"}', encoding="utf-8")
    finding = service.propose(
        project="p", type="finding", title="Code fact", body="Value is one.", creator="worker"
    )
    service.review(finding["id"], actor="reviewer", verdict="provisional")
    service.link_evidence(
        EvidenceLink(
            finding["id"],
            "fact.py",
            "git-file",
            content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        ),
        actor="reviewer",
        project_root=tmp_path,
    )
    service.review(finding["id"], actor="reviewer", verdict="accepted")
    note = service.propose(project="p", type="artifact_ref", title="Note", body="Stable", creator="a")
    service.link_evidence(artifact_link(note["id"], artifact), actor="a")
    before_artifact = service.get_record(note["id"])["evidence"][0]["metadata"]

    source.write_text("value = 2\n", encoding="utf-8")
    assert service.check_git_staleness("p", tmp_path) == [finding["id"]]
    assert service.get_record(note["id"])["evidence"][0]["metadata"] == before_artifact

    service.review(finding["id"], actor="reviewer", verdict="provisional")
    service.link_evidence(
        EvidenceLink(
            finding["id"],
            "fact.py",
            "git-file",
            content_hash=hashlib.sha256(source.read_bytes()).hexdigest(),
        ),
        actor="reviewer",
        project_root=tmp_path,
    )
    service.review(finding["id"], actor="reviewer", verdict="accepted")
    source.write_text("value = 3\n", encoding="utf-8")
    assert service.check_git_staleness("p", tmp_path) == [finding["id"]]
    stale_keys = [
        event["idempotency_key"]
        for event in service.sync_export("p")
        if event["event_type"] == "reviewed" and event["payload"]["verdict"] == "stale"
    ]
    assert len(stale_keys) == 2
    assert len(set(stale_keys)) == 2


def test_fuzzy_typo_retrieval_searches_beyond_first_two_hundred(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    for index in range(240):
        service.propose(
            project="p",
            type="finding",
            title=f"Unrelated calibration record {index}",
            body="Routine output without the target terminology.",
            creator="worker",
        )
    target = service.propose(
        project="p",
        type="finding",
        title="Hippocampal contextual relaying",
        body="Agents reuse grounded state across harnesses.",
        creator="worker",
    )
    results = service.query("p", "hipocampel contextul relayng", limit=5)
    assert target["id"] in {record["id"] for record in results}
