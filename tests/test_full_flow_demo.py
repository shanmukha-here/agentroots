from pathlib import Path

from examples.full_flow_demo import run


def test_real_full_flow_demo(tmp_path: Path) -> None:
    result = run(tmp_path / "demo")

    assert result["project"].startswith("shared-agent-project-")
    assert result["read_only_backfill"]["source_unchanged"] is True
    assert result["read_only_backfill"]["extraction"]["candidates"] >= 1
    assert result["governance"]["self_accept_blocked"] is True
    assert result["governance"]["accepted_status"] == "accepted"
    assert result["governance"]["conversation_evidence_status"] == "reference_valid"
    assert result["governance"]["tracker_evidence_status"] == "verified"
    assert result["mlflow"]["run_status"] == "FAILED"
    assert result["mlflow"]["evidence_status"] == "verified"
    assert result["fresh_codex"]["duplicate_risk_warning_surfaced"] is True
    assert result["fresh_codex"]["execution_control"].startswith("advisory only")
    assert result["fresh_codex"]["notification"]["tokens"] <= 180
    assert result["fresh_codex"]["notification"]["system_message"].startswith("AgentRoots")
    assert result["staleness"] == {
        "record": result["staleness"]["record"],
        "before": "accepted",
        "pre_tool_status": "accepted",
        "after_file_edit": "stale",
        "pre_tool_event_recorded": True,
        "post_tool_event_recorded": True,
        "excluded_from_context": True,
    }
    assert result["validation"]["ok"] is True
    assert result["validation"]["issues"] == []
    assert Path(result["artifacts"]["graph"]).is_file()
    assert Path(result["artifacts"]["database"]).is_file()
    assert Path(result["artifacts"]["backup"]).is_file()
    assert Path(result["artifacts"]["summary"]).is_file()
