from __future__ import annotations

import sys
from pathlib import Path

from agentroots import __version__
from examples.installed_flow_demo import run


def test_installed_entrypoint_flow(tmp_path: Path) -> None:
    result = run(tmp_path / "installed-demo", Path(sys.executable))

    assert result["scope"] == {
        "input": "synthetic local project and prior-run fixture",
        "global_client_configuration_changed": False,
        "source_conversations_read": False,
    }
    assert result["setup"] == {
        "ready": True,
        "clients_configured": False,
        "history_approved": False,
        "project": "installed-demo",
    }
    assert result["cli_workflow"]["self_accept_blocked"] is True
    assert result["cli_workflow"]["self_accept_error"] == "creator cannot accept own proposal"
    assert result["cli_workflow"]["receipt_status"] == "reference_valid"
    assert result["cli_workflow"]["file_evidence_status"] == "verified"
    assert result["cli_workflow"]["file_evidence_hash_recorded"] is True
    assert result["cli_workflow"]["accepted_status"] == "accepted"
    assert result["hook_subprocess"]["recalled_prior_run"] is True
    assert result["hook_subprocess"]["notification"]["tokens"] <= 180
    assert result["mcp_stdio"]["tool_count"] == 16
    assert "research_get_context" in result["mcp_stdio"]["tool_names"]
    assert result["mcp_stdio"]["current_project_matches"] is True
    assert result["mcp_stdio"]["context_mentions_prior_run"] is True
    assert result["mcp_stdio"]["context_is_stateless"] is True
    assert result["mcp_stdio"]["full_context_normalized"] is True
    assert result["mcp_stdio"]["full_context_is_stateless"] is True
    assert result["mcp_stdio"]["short_record_ref_resolved"] is True
    assert result["mcp_stdio"]["database_unchanged"] is True
    assert result["mcp_stdio"]["registry_unchanged"] is True
    assert result["mcp_stdio"]["db_override_precedence"] is True
    assert result["mcp_stdio"]["server_version"] == __version__
    assert result["resources"]["database_bytes"] > 0
    assert result["resources"]["managed_state_bytes"] >= result["resources"]["database_bytes"]
    assert result["resources"]["python_environment_bytes"] >= 0
    assert result["resources"]["total_bytes"] >= result["resources"]["database_bytes"]
    assert Path(result["artifacts"]["database"]).is_file()
    assert Path(result["artifacts"]["receipt"]).is_file()
    assert Path(result["artifacts"]["summary"]).is_file()
