import json
from io import BytesIO
from unittest.mock import patch

from agentroots.adapters.mlflow import MLflowAdapter
from agentroots.adapters.trackio import TrackioAdapter


class Response(BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


def test_mlflow_read_only() -> None:
    payload = {
        "run": {
            "info": {"experiment_id": "e", "status": "FINISHED"},
            "data": {"metrics": [{"key": "acc", "value": 0.9}], "params": []},
        }
    }
    with patch(
        "agentroots.adapters.mlflow.urlopen",
        return_value=Response(json.dumps(payload).encode()),
    ):
        run = MLflowAdapter("http://localhost:5000").get_run("r")
    assert run.metrics["acc"] == 0.9


def test_trackio_injected() -> None:
    run = TrackioAdapter(
        lambda _: {"status": "done", "metrics": {"loss": 1.0}}, "http://t"
    ).get_run("r")
    assert run.status == "done"
