from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

import agentroots.server as agentroots_server
from agentroots.adapters.mlflow import MLflowAdapter
from agentroots.db import Database
from agentroots.service import GovernanceError, ResearchService


class MLflowHandler(BaseHTTPRequestHandler):
    status = "FINISHED"
    accuracy = 0.91

    def log_message(self, _format: str, *args: object) -> None:
        pass

    def _send(self, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @classmethod
    def run(cls, run_id: str) -> dict[str, Any]:
        return {
            "info": {
                "run_id": run_id,
                "run_name": f"run-{run_id}",
                "experiment_id": "exp-1",
                "status": cls.status,
                "start_time": 1000,
                "end_time": 2000 if cls.status != "RUNNING" else None,
                "artifact_uri": f"s3://bucket/{run_id}",
            },
            "data": {
                "metrics": [{"key": "accuracy", "value": cls.accuracy}],
                "params": [
                    {"key": "batch_size", "value": "32"},
                    {"key": "api_token", "value": "should-not-be-stored"},
                ],
                "tags": [
                    {"key": "mlflow.source.git.commit", "value": "abc123"},
                    {"key": "mlflow.note.content", "value": "candidate result"},
                ],
            },
            "inputs": {
                "dataset_inputs": [
                    {
                        "dataset": {
                            "name": "train",
                            "digest": "dataset-sha",
                            "source_type": "local",
                            "source": "file:///data/train.csv",
                        },
                        "tags": [{"key": "mlflow.data.context", "value": "training"}],
                    }
                ]
            },
        }

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path.endswith("/runs/get"):
            self._send({"run": self.run(query["run_id"][0])})
            return
        if parsed.path.endswith("/metrics/get-history"):
            token = query.get("page_token", [""])[0]
            if not token:
                self._send(
                    {
                        "metrics": [{"key": "accuracy", "value": 0.5, "step": 0}],
                        "next_page_token": "page-2",
                    }
                )
            else:
                self._send({"metrics": [{"key": "accuracy", "value": 0.91, "step": 1}]})
            return
        if parsed.path.endswith("/artifacts/list"):
            path = query.get("path", [""])[0]
            if path == "model":
                self._send(
                    {"root_uri": "s3://bucket/r", "files": [{"path": "model/model.pkl", "file_size": 8}]}
                )
            else:
                self._send(
                    {
                        "root_uri": "s3://bucket/r",
                        "files": [
                            {"path": "metrics.json", "file_size": 4},
                            {"path": "model", "is_dir": True},
                        ],
                    }
                )
            return
        self.send_error(404)

    def do_POST(self) -> None:
        if not self.path.endswith("/runs/search"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if body.get("page_token"):
            self._send({"runs": [self.run("r2")]})
        else:
            self._send({"runs": [self.run("r1")], "next_page_token": "page-2"})


@pytest.fixture
def mlflow_server() -> str:
    MLflowHandler.status = "FINISHED"
    MLflowHandler.accuracy = 0.91
    server = ThreadingHTTPServer(("127.0.0.1", 0), MLflowHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_mlflow_rest_search_history_artifacts_and_comparison(mlflow_server: str) -> None:
    adapter = MLflowAdapter(mlflow_server)
    run = adapter.get_run("r", include_artifacts=True)
    assert run.tags["mlflow.source.git.commit"] == "abc123"
    assert run.datasets[0]["digest"] == "dataset-sha"
    assert {item["path"] for item in run.artifacts} == {
        "metrics.json",
        "model",
        "model/model.pkl",
    }
    assert [item["step"] for item in adapter.metric_history("r", "accuracy")] == [0, 1]
    assert [item.run_id for item in adapter.search_runs(["exp-1"], max_results=2)] == ["r1", "r2"]
    assert len(adapter.compare_runs(["r1", "r2"])) == 2


def test_mlflow_run_becomes_governed_evidence_and_detects_drift(
    tmp_path: Path, mlflow_server: str
) -> None:
    service = ResearchService(Database(tmp_path / "state.db"))
    finding = service.propose(
        project="p", type="finding", title="Model improved", body="Accuracy improved", creator="worker"
    )
    adapter = MLflowAdapter(mlflow_server)
    linked = service.link_external_run(
        finding["id"], adapter.get_run("r", include_artifacts=True), actor="worker"
    )
    evidence = linked["evidence"][0]
    assert evidence["kind"] == "tracker-run"
    assert evidence["metadata"]["params"]["api_token"] == "[REDACTED]"
    assert evidence["metadata"]["git_commit"] == "abc123"
    assert evidence["metadata"]["datasets"][0]["digest"] == "dataset-sha"

    service.review(finding["id"], actor="reviewer", verdict="provisional")
    accepted = service.review(finding["id"], actor="reviewer", verdict="accepted")
    assert accepted["status"] == "accepted"
    assert (
        service.validate_external_run(
            finding["id"], adapter.get_run("r", include_artifacts=True)
        )["matched"]
        is True
    )
    MLflowHandler.accuracy = 0.95
    result = service.validate_external_run(
        finding["id"], adapter.get_run("r", include_artifacts=True)
    )
    assert result["matched"] is False
    assert result["record_status"] == "stale"


def test_running_mlflow_run_cannot_support_accepted_record(
    tmp_path: Path, mlflow_server: str
) -> None:
    service = ResearchService(Database(tmp_path / "state.db"))
    finding = service.propose(project="p", type="finding", title="f", body="b", creator="worker")
    MLflowHandler.status = "RUNNING"
    service.link_external_run(finding["id"], MLflowAdapter(mlflow_server).get_run("r"), actor="worker")
    service.review(finding["id"], actor="reviewer", verdict="provisional")
    with pytest.raises(GovernanceError, match="terminal run"):
        service.review(finding["id"], actor="reviewer", verdict="accepted")


def test_mcp_mlflow_surface_get_compare_link_and_validate(
    tmp_path: Path, mlflow_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = MLflowAdapter(mlflow_server)
    service = ResearchService(Database(tmp_path / "state.db"))
    experiment = service.propose(
        project="p", type="experiment", title="train", body="run model", creator="worker"
    )
    finding = service.propose(project="p", type="finding", title="f", body="b", creator="worker")
    monkeypatch.setattr(agentroots_server, "_mlflow", lambda: adapter)
    monkeypatch.setattr(agentroots_server, "service", service)

    got = agentroots_server.research_mlflow("get", run_id="r")
    assert got["run"]["datasets"][0]["digest"] == "dataset-sha"
    compared = agentroots_server.research_mlflow("compare", run_ids=["r1", "r2"])
    assert compared["metric_matrix"]["accuracy"] == {"r1": 0.91, "r2": 0.91}
    linked = agentroots_server.research_mlflow(
        "link",
        run_id="r",
        record_id=finding["id"],
        experiment_record_id=experiment["id"],
        actor="worker",
    )
    assert linked["record"]["evidence"][0]["metadata"]["adapter"] == "mlflow"
    assert linked["run_ref"]["type"] == "run_ref"
    assert any(link["relation"] == "supports" for link in linked["run_ref"]["links"])
    assert any(
        link["relation"] == "produced" for link in service.get_record(experiment["id"])["links"]
    )
    event_count = len(service.sync_export("p"))
    repeated = agentroots_server.research_mlflow(
        "link", run_id="r", record_id=finding["id"], actor="worker"
    )
    assert repeated["run_ref"]["id"] == linked["run_ref"]["id"]
    assert len(service.sync_export("p")) == event_count
    assert agentroots_server.research_mlflow(
        "validate", run_id="r", record_id=finding["id"]
    )["matched"]
