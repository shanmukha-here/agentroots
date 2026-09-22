from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

import agentroots.server as agentroots_server
from agentroots import __version__
from agentroots.cli import execute, parser
from agentroots.db import Database
from agentroots.episodes import EpisodeStore
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.service import ResearchService


def _candidate(
    service: ResearchService,
    *,
    candidate_id: str,
    project: str,
    risky: bool = False,
) -> None:
    with service.db.connect() as con:
        con.execute(
            "INSERT INTO extraction_candidates("
            "id,project,session_id,source_event_id,type,title,body,evidence_span,"
            "confidence,metadata,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate_id,
                project,
                "session",
                f"source-{candidate_id}",
                "finding",
                f"Finding {candidate_id}",
                "A candidate finding with enough context for review.",
                "candidate finding",
                0.8,
                json.dumps({"prompt_injection_risk": risky}),
                "2026-01-01T00:00:00+00:00",
            ),
        )


def test_cli_version_matches_package(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="0"):
        parser().parse_args(["--version"])
    assert capsys.readouterr().out.strip() == f"agentroots {__version__}"


def test_top_level_cli_help_describes_core_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit, match="0"):
        parser().parse_args(["--help"])
    output = capsys.readouterr().out
    for description in (
        "search governed project records",
        "build and audit a full context packet",
        "show unresolved project work",
        "check governance and evidence integrity",
        "write a project event stream as JSONL",
        "import and validate a JSONL event stream",
        "copy complete local state to a SQLite backup",
        "replace local state from a SQLite backup",
    ):
        assert description in output


def test_cli_candidate_actions_require_matching_project(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    _candidate(service, candidate_id="candidate-alpha", project="alpha")

    matching = parser().parse_args(
        ["candidate", "get", "candidate-alpha", "--project", "alpha"]
    )
    wrong = parser().parse_args(
        ["candidate", "promote", "candidate-alpha", "--project", "beta"]
    )

    assert execute(matching, service)["project"] == "alpha"
    with pytest.raises(KeyError, match="candidate-alpha"):
        execute(wrong, service)


def test_cli_decision_metadata_and_local_file_auto_hash(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    artifact = tmp_path / "decision-evidence.txt"
    artifact.write_text("Measured evidence for the selected design.", encoding="utf-8")
    proposed = execute(
        parser().parse_args(
            [
                "propose",
                "demo",
                "decision",
                "Choose the bounded design",
                "The selected design follows the measured result.",
                "--actor",
                "worker",
                "--metadata",
                json.dumps(
                    {
                        "alternatives": ["retain the previous design"],
                        "rationale": "The selected design has stronger local evidence.",
                    }
                ),
            ]
        ),
        service,
    )
    execute(
        parser().parse_args(
            ["review", proposed["id"], "provisional", "--actor", "reviewer"]
        ),
        service,
    )
    linked = execute(
        parser().parse_args(
            [
                "evidence",
                proposed["id"],
                str(artifact),
                "file",
                "--actor",
                "reviewer",
            ]
        ),
        service,
    )
    evidence = linked["evidence"][0]
    assert evidence["uri"] == artifact.as_uri()
    assert evidence["content_hash"] == hashlib.sha256(artifact.read_bytes()).hexdigest()
    assert evidence["metadata"]["verification"]["status"] == "verified"
    accepted = execute(
        parser().parse_args(
            ["review", proposed["id"], "accepted", "--actor", "reviewer"]
        ),
        service,
    )
    assert accepted["status"] == "accepted"


def test_cli_revise_merges_metadata(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    record = service.propose(
        project="demo", type="finding", title="Finding", body="Initial body.", creator="worker"
    )
    revised = execute(
        parser().parse_args(
            [
                "revise",
                record["id"],
                "--actor",
                "reviewer",
                "--metadata",
                '{"failed": true}',
            ]
        ),
        service,
    )
    assert revised["metadata"]["failed"] is True


def test_mcp_candidate_actions_are_scoped_and_risky_list_is_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    _candidate(service, candidate_id="safe", project="alpha")
    _candidate(service, candidate_id="risky", project="alpha", risky=True)
    monkeypatch.setattr(agentroots_server, "service", service)

    visible = agentroots_server.research_candidate("list", "alpha")
    opted_in = agentroots_server.research_candidate(
        "list", "alpha", include_risky=True
    )

    assert [item["id"] for item in visible] == ["safe"]
    assert {item["id"] for item in opted_in} == {"safe", "risky"}
    with pytest.raises(KeyError, match="safe"):
        agentroots_server.research_candidate("get", "beta", "safe")


def test_project_bind_persists_named_root_without_environment_variable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))
    monkeypatch.delenv("AGENTROOTS_PROJECT", raising=False)
    service = ResearchService(Database(tmp_path / "state.sqlite3"))

    result = execute(parser().parse_args(["project-bind", "paper-main", str(root)]), service)

    assert result["project_id"] == "paper-main"
    assert resolve_project_identity(root).project_id == "paper-main"


def test_project_bind_replaces_unreferenced_derived_registry_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))
    monkeypatch.delenv("AGENTROOTS_PROJECT", raising=False)
    derived = resolve_project_identity(root).project_id
    service = ResearchService(Database(tmp_path / "state.sqlite3"))

    execute(parser().parse_args(["project-bind", "paper-main", str(root)]), service)
    projects = execute(parser().parse_args(["project-list"]), service)

    assert [item["project_id"] for item in projects] == ["paper-main"]
    assert derived in projects[0]["aliases"]


def test_cli_project_discovery_lists_current_and_stored_projects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    execute(parser().parse_args(["project-bind", "paper-main", str(root)]), service)
    service.propose(
        project="history-only",
        type="goal",
        title="Imported goal",
        body="Goal recovered from a prior approved history import.",
        creator="backfill",
    )

    current = execute(parser().parse_args(["project-current", str(root)]), service)
    projects = execute(parser().parse_args(["project-list"]), service)

    assert current["project_id"] == "paper-main"
    assert {item["project_id"] for item in projects} == {"history-only", "paper-main"}
    assert all("path" not in item for item in projects)


def test_mcp_current_project_resolves_registered_checkout_without_raw_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))
    resolve_project_identity(root, configured="paper-main")

    result = agentroots_server.research_current_project(str(root))

    assert result["project"] == "paper-main"
    assert "paper-main" in result["aliases"]
    assert set(result) == {"project", "aliases", "git_remote_bound"}


def test_mcp_read_tools_do_not_mutate_state_or_project_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    registry = tmp_path / "projects.json"
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(registry))
    monkeypatch.setenv("AGENTROOTS_SEMANTIC", "off")
    resolve_project_identity(root, configured="paper-main")
    registry_before = registry.read_bytes()

    database = Database(tmp_path / "state.sqlite3")
    service = ResearchService(database)
    record = service.propose(
        project="paper-main",
        type="question",
        title="Which configuration should run next?",
        body="Select one bounded next experiment from the accepted evidence.",
        creator="worker",
    )
    monkeypatch.setattr(agentroots_server, "service", service)
    monkeypatch.setattr(agentroots_server, "episodes", EpisodeStore(database))

    with database.connect() as connection:
        rows_before = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in (
                "records", "events", "links", "evidence", "reviews", "packet_audit",
                "episodes", "hook_events", "hook_injections", "extraction_candidates",
            )
        }
    database_before = hashlib.sha256(database.path.read_bytes()).digest()

    assert agentroots_server.research_current_project(str(root))["project"] == "paper-main"
    compact = agentroots_server.research_get_context(
        project_root=str(root), query="configuration"
    )
    full = agentroots_server.research_get_context(
        project_root=str(root), query="configuration", token_budget=500, view="full"
    )
    assert compact["packet_ref"] is None
    assert full["packet_id"] is None
    assert agentroots_server.research_get_record(
        record["id"][:8], project_root=str(root)
    )["id"] == record["id"]
    assert agentroots_server.research_get_frontier(project_root=str(root))
    assert agentroots_server.research_query(
        query="configuration", project_root=str(root)
    )
    assert agentroots_server.research_get_graph(project_root=str(root))["nodes"]
    assert agentroots_server.research_search_history(
        query="configuration", project_root=str(root)
    ) == []

    with database.connect() as connection:
        rows_after = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in rows_before
        }
    assert rows_after == rows_before
    assert hashlib.sha256(database.path.read_bytes()).digest() == database_before
    assert registry.read_bytes() == registry_before


def test_mcp_core_tools_default_to_canonical_current_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    monkeypatch.chdir(root)
    monkeypatch.setenv("AGENTROOTS_PROJECT_REGISTRY", str(tmp_path / "projects.json"))
    monkeypatch.delenv("AGENTROOTS_PROJECT", raising=False)
    resolve_project_identity(root, configured="paper-main")
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    monkeypatch.setattr(agentroots_server, "service", service)

    created = agentroots_server.research_propose(
        record_type="question",
        title="Which configuration should run next?",
        body="The current project needs one bounded next experiment selected from prior evidence.",
        creator="fresh-agent",
    )

    assert created["project"] == "paper-main"
    assert agentroots_server.research_get_frontier()[0]["id"] == created["id"]
    assert agentroots_server.research_query(query="configuration")[0]["id"] == created["id"]


def test_mcp_record_evidence_and_link_operations_enforce_project_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    alpha = service.propose(
        project="alpha", type="finding", title="Alpha", body="Alpha body", creator="worker"
    )
    alpha_target = service.propose(
        project="alpha", type="claim", title="Target", body="Target body", creator="worker"
    )
    beta = service.propose(
        project="beta", type="claim", title="Beta", body="Beta body", creator="worker"
    )
    monkeypatch.setattr(agentroots_server, "service", service)

    assert agentroots_server.research_get_record(alpha["id"], project="alpha")["id"] == alpha["id"]
    with pytest.raises(KeyError, match=alpha["id"]):
        agentroots_server.research_get_record(alpha["id"], project="beta")
    with pytest.raises(KeyError, match=alpha["id"]):
        agentroots_server.research_revise(alpha["id"], "reviewer", project="beta")
    with pytest.raises(KeyError, match=alpha["id"]):
        agentroots_server.research_review(
            alpha["id"], "reviewer", "rejected", project="beta"
        )
    with pytest.raises(KeyError, match=alpha["id"]):
        agentroots_server.research_link_evidence(
            alpha["id"], "human://scope", "human", "reviewer", project="beta"
        )
    with pytest.raises(KeyError, match="record"):
        agentroots_server.research_link_records(
            alpha["id"], alpha_target["id"], "supports", "reviewer", project="beta"
        )
    with pytest.raises(KeyError, match="record"):
        agentroots_server.research_link_records(
            alpha["id"], beta["id"], "supports", "reviewer", project="alpha"
        )

    assert service.get_record(alpha["id"])["revision"] == 1
    assert service.get_record(alpha["id"])["evidence"] == []


def test_service_scoped_record_access_hides_other_projects(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.sqlite3"))
    record = service.propose(
        project="alpha", type="finding", title="Alpha", body="Body", creator="worker"
    )

    assert service.get_record(record["id"], project="alpha")["id"] == record["id"]
    with pytest.raises(KeyError, match=record["id"]):
        service.get_record(record["id"], project="beta")
    with pytest.raises(KeyError, match=record["id"]):
        service.link_evidence(
            EvidenceLink(record["id"], "human://scope", "human"),
            actor="reviewer",
            project="beta",
        )
