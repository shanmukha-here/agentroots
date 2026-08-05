from pathlib import Path

from agentroots.cli import execute, parser
from agentroots.db import Database
from agentroots.graph import write_graph_html
from agentroots.service import ResearchService


def test_graph_projection_and_self_contained_html(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.db"))
    goal = service.propose(project="p", type="goal", title="Ship map", body="Visible", creator="a")
    finding = service.propose(
        project="p", type="finding", title="Graph works", body="Navigable", creator="b"
    )
    service.link(goal["id"], finding["id"], "depends_on", actor="a")
    graph = service.graph("p")
    assert graph["graph_version"] == 3
    assert {node["id"] for node in graph["nodes"]} == {goal["id"], finding["id"]}
    assert graph["edges"][0]["relation"] == "depends_on"

    output = write_graph_html(graph, tmp_path / "map.html")
    document = output.read_text(encoding="utf-8")
    assert 'id="agentroots-viewer"' in document
    assert "ReactFlowProvider" in document
    assert "Copy ID" in document
    assert goal["id"] in document
    assert "fetch(" not in document


def test_graph_cli_writes_requested_file(tmp_path: Path) -> None:
    service = ResearchService(Database(tmp_path / "state.db"))
    service.propose(project="p", type="question", title="Why?", body="Inspect", creator="a")
    output = tmp_path / "project-map.html"
    args = parser().parse_args(["graph", "p", str(output)])
    result = execute(args, service)
    assert result["path"] == str(output)
    assert output.exists()
