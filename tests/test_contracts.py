import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).parents[1]


def test_all_json_schemas_are_valid() -> None:
    for path in (ROOT / "schemas").glob("*.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_scale_fixture_has_1000_experiments() -> None:
    path = ROOT / "examples" / "synthetic_1000_experiments.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == 1000
    assert all(row["kind"] == "E" and row["run"]["uri"].startswith("mlflow://") for row in rows)
