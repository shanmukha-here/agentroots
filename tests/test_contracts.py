import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

import agentroots.server as agentroots_server
from agentroots import __version__
from agentroots.db import Database
from agentroots.server import mcp
from agentroots.service import ResearchService

ROOT = Path(__file__).parents[1]
FORMAT_CHECKER = FormatChecker()


@FORMAT_CHECKER.checks("date-time", raises=ValueError)
def _is_iso_datetime(value: object) -> bool:
    if not isinstance(value, str):
        return True
    datetime.fromisoformat(value)
    return True


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _event_validator() -> Draft202012Validator:
    schema = json.loads((ROOT / "schemas" / "event.schema.json").read_text(encoding="utf-8"))
    return Draft202012Validator(schema, format_checker=FORMAT_CHECKER)


def _portable_event(event: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key != "seq"}


def test_all_json_schemas_are_valid() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        schema = json.loads(path.read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)


def test_format_checker_rejects_invalid_contract_formats() -> None:
    validator = _event_validator()
    event = _jsonl(ROOT / "examples" / "synthetic_fixture.jsonl")[0]
    invalid = {**event, "event_id": "not-a-uuid", "at": "not-a-date"}
    errors = list(validator.iter_errors(invalid))
    assert {tuple(error.path) for error in errors} == {("event_id",), ("at",)}


def test_synthetic_jsonl_fixtures_match_event_contract() -> None:
    validator = _event_validator()
    for name in ("synthetic_fixture.jsonl", "synthetic_1000_experiments.jsonl"):
        rows = _jsonl(ROOT / "examples" / name)
        assert rows
        for row in rows:
            validator.validate(row)


def test_scale_fixture_import_and_round_trip(tmp_path: Path) -> None:
    rows = _jsonl(ROOT / "examples" / "synthetic_1000_experiments.jsonl")
    assert len(rows) == 1000

    source = ResearchService(Database(tmp_path / "source.sqlite3"))
    assert source.import_events(rows, expected_project="synthetic-scale") == 1000
    records = source.query("synthetic-scale", limit=1001)
    assert len(records) == 1000
    assert all(record["type"] == "experiment" for record in records)

    exported = source.sync_export("synthetic-scale")
    validator = _event_validator()
    for event in exported:
        validator.validate(event)
    assert [_portable_event(event) for event in exported] == rows

    destination = ResearchService(Database(tmp_path / "destination.sqlite3"))
    assert destination.import_events(exported, expected_project="synthetic-scale") == 1000
    assert destination.sync_export("synthetic-scale") == exported


def test_mcp_discovery_is_self_describing() -> None:
    tools = mcp._tool_manager.list_tools()
    names = {tool.name for tool in tools}
    assert {
        "research_candidate",
        "research_current_project",
        "research_link_records",
        "research_validate",
    } <= names
    assert len(tools) == 16
    assert all(tool.description for tool in tools)
    assert mcp.instructions and "conversation episodes" in mcp.instructions
    assert mcp._mcp_server.version == __version__
    assert f"AgentRoots {__version__}" in mcp.instructions
    assert json.loads((ROOT / "plugins/agentroots/.codex-plugin/plugin.json").read_text())[
        "version"
    ] == __version__


def test_mcp_read_tools_publish_safe_annotations() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    read_tools = {
        "research_current_project",
        "research_get_context",
        "research_get_frontier",
        "research_query",
        "research_get_record",
        "research_get_graph",
        "research_search_history",
    }
    for name in read_tools:
        annotations = tools[name].annotations
        assert annotations is not None
        assert annotations.readOnlyHint is True
        assert annotations.destructiveHint is False
        assert annotations.idempotentHint is True
        assert annotations.openWorldHint is False


def test_mcp_mutation_schemas_publish_governed_enums() -> None:
    tools = {tool.name: tool for tool in mcp._tool_manager.list_tools()}
    propose = tools["research_propose"].parameters
    assert propose["$defs"]["Mode"]["enum"] == [
        "preregistered", "exploratory", "replication", "debugging"
    ]
    assert "finding" in propose["$defs"]["RecordType"]["enum"]
    review = tools["research_review"].parameters
    assert review["properties"]["verdict"]["enum"] == [
        "provisional", "accepted", "disputed", "rejected", "superseded", "stale"
    ]
    links = tools["research_link_records"].parameters
    assert set(links["properties"]["relation"]["enum"]) == {
        "decomposes", "tests", "derived_from", "supports", "contradicts", "supersedes",
        "depends_on", "produced", "invalidates", "selected", "rejected", "resolves",
    }


def test_mcp_entrypoint_help_and_database_override(
    tmp_path: Path, monkeypatch: Any
) -> None:
    help_result = subprocess.run(
        [sys.executable, "-m", "agentroots.server", "--help"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert help_result.returncode == 0
    assert "Run the AgentRoots MCP server over stdio" in help_result.stdout
    assert "--db" in help_result.stdout

    target = tmp_path / "mcp.sqlite3"
    monkeypatch.setattr(agentroots_server, "service", agentroots_server.service)
    monkeypatch.setattr(agentroots_server, "episodes", agentroots_server.episodes)
    monkeypatch.setattr(agentroots_server.mcp, "run", lambda: None)
    monkeypatch.setattr(sys, "argv", ["agentroots-mcp", "--db", str(target)])
    agentroots_server.main()
    assert agentroots_server.service.db.path == target
