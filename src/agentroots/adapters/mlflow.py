from __future__ import annotations

import json
from urllib.request import urlopen

from .base import ExternalRun


class MLflowAdapter:
    """Minimal read-only MLflow REST adapter; caller controls trusted base URL."""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def get_run(self, run_id: str) -> ExternalRun:
        with urlopen(
            f"{self.base_url}/api/2.0/mlflow/runs/get?run_id={run_id}", timeout=10
        ) as response:
            data = json.load(response)["run"]
        info = data["info"]
        metrics = {x["key"]: float(x["value"]) for x in data["data"].get("metrics", [])}
        params = {x["key"]: x["value"] for x in data["data"].get("params", [])}
        return ExternalRun(
            "mlflow",
            run_id,
            f"{self.base_url}/#/experiments/{info['experiment_id']}/runs/{run_id}",
            info["status"],
            metrics,
            params,
        )

    def compare_runs(self, run_ids: list[str]) -> list[ExternalRun]:
        return [self.get_run(x) for x in run_ids]
