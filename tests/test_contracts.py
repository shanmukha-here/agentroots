import json
from pathlib import Path

from jsonschema import Draft202012Validator

from agentroots.server import mcp

ROOT = Path(__file__).parents[1]


def test_all_json_schemas_are_valid() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_scale_fixture_has_1000_experiments() -> None:
    path = ROOT / "examples" / "synthetic_1000_experiments.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1000
    assert all(row["kind"] == "E" and row["run"]["uri"].startswith("mlflow://") for row in rows)


def test_mcp_discovery_is_self_describing() -> None:
    tools = mcp._tool_manager.list_tools()
    assert len(tools) == 11
    assert all(tool.description for tool in tools)
    assert mcp.instructions and "never transcripts" in mcp.instructions
