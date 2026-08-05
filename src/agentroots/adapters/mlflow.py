from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ..security import scan_text
from .base import ExternalRun


class MLflowAdapter:
    """Read-only MLflow REST adapter with bounded pagination and no artifact downloads."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10,
        max_pages: int = 20,
    ):
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("MLflow URL must use http or https")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_pages = max_pages
        self.headers = {"Accept": "application/json"}
        if token:
            self.headers["Authorization"] = f"Bearer {token}"

    def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/2.0/mlflow/{path}"
        if params:
            url += "?" + urlencode({key: value for key, value in params.items() if value is not None})
        data = None if body is None else json.dumps(body).encode()
        headers = dict(self.headers)
        if data is not None:
            headers["Content-Type"] = "application/json"
        with urlopen(Request(url, data=data, headers=headers), timeout=self.timeout) as response:
            result = json.load(response)
        if not isinstance(result, dict):
            raise TypeError("MLflow returned a non-object response")
        return result

    @staticmethod
    def _pairs(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
        return {str(item["key"]): item.get("value") for item in items if "key" in item}

    @staticmethod
    def _safe_mapping(values: dict[str, Any]) -> dict[str, Any]:
        sensitive = ("secret", "password", "passwd", "token", "api_key", "apikey", "credential")
        return {
            key: (
                "[REDACTED]"
                if any(part in key.lower() for part in sensitive)
                else scan_text(str(value)).text
            )
            for key, value in values.items()
        }

    @staticmethod
    def _datasets(run: dict[str, Any]) -> tuple[dict[str, Any], ...]:
        result = []
        for item in run.get("inputs", {}).get("dataset_inputs", []):
            dataset = item.get("dataset", {})
            result.append(
                {
                    "name": dataset.get("name", ""),
                    "digest": dataset.get("digest", ""),
                    "source_type": dataset.get("source_type", ""),
                    "source": scan_text(str(dataset.get("source", ""))).text,
                    "profile": scan_text(str(dataset.get("profile", ""))).text,
                    "context": next(
                        (
                            tag.get("value", "")
                            for tag in item.get("tags", [])
                            if tag.get("key") == "mlflow.data.context"
                        ),
                        "",
                    ),
                }
            )
        return tuple(result)

    def _external_run(
        self, run: dict[str, Any], artifacts: tuple[dict[str, Any], ...] = ()
    ) -> ExternalRun:
        info = run["info"]
        data = run.get("data", {})
        tags = self._safe_mapping(self._pairs(data.get("tags", [])))
        metrics = {
            key: float(value)
            for key, value in self._pairs(data.get("metrics", [])).items()
            if value is not None
        }
        params = self._safe_mapping(self._pairs(data.get("params", [])))
        run_id = str(info.get("run_id") or info.get("run_uuid"))
        experiment_id = str(info.get("experiment_id", ""))
        return ExternalRun(
            adapter="mlflow",
            run_id=run_id,
            uri=f"{self.base_url}/#/experiments/{experiment_id}/runs/{run_id}",
            status=str(info.get("status", "UNKNOWN")),
            metrics=metrics,
            params=params,
            summary=tags.get("mlflow.note.content", ""),
            experiment_id=experiment_id,
            run_name=str(info.get("run_name") or tags.get("mlflow.runName", "")),
            start_time=info.get("start_time"),
            end_time=info.get("end_time"),
            artifact_uri=str(info.get("artifact_uri", "")),
            tags=tags,
            datasets=self._datasets(run),
            artifacts=artifacts,
        )

    def get_run(self, run_id: str, *, include_artifacts: bool = False) -> ExternalRun:
        run = self._request("runs/get", params={"run_id": run_id})["run"]
        artifacts = self.list_artifacts(run_id) if include_artifacts else ()
        return self._external_run(run, artifacts)

    def search_runs(
        self,
        experiment_ids: list[str],
        *,
        filter_string: str = "",
        order_by: list[str] | None = None,
        max_results: int = 100,
    ) -> list[ExternalRun]:
        if not experiment_ids:
            raise ValueError("at least one MLflow experiment ID is required")
        results: list[ExternalRun] = []
        token: str | None = None
        for _ in range(self.max_pages):
            remaining = max_results - len(results)
            if remaining <= 0:
                break
            body: dict[str, Any] = {
                "experiment_ids": experiment_ids,
                "filter": filter_string,
                "max_results": min(remaining, 1000),
            }
            if order_by:
                body["order_by"] = order_by
            if token:
                body["page_token"] = token
            response = self._request("runs/search", body=body)
            results.extend(self._external_run(run) for run in response.get("runs", []))
            token = response.get("next_page_token")
            if not token:
                break
        return results[:max_results]

    def metric_history(
        self, run_id: str, metric_key: str, *, max_results: int = 1000
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        token: str | None = None
        for _ in range(self.max_pages):
            remaining = max_results - len(results)
            if remaining <= 0:
                break
            response = self._request(
                "metrics/get-history",
                params={
                    "run_id": run_id,
                    "metric_key": metric_key,
                    "max_results": min(remaining, 1000),
                    "page_token": token,
                },
            )
            results.extend(response.get("metrics", []))
            token = response.get("next_page_token")
            if not token:
                break
        return results[:max_results]

    def list_artifacts(
        self, run_id: str, *, path: str = "", max_results: int = 200
    ) -> tuple[dict[str, Any], ...]:
        results: list[dict[str, Any]] = []
        pending = [path]
        while pending and len(results) < max_results:
            current = pending.pop(0)
            token: str | None = None
            for _ in range(self.max_pages):
                response = self._request(
                    "artifacts/list",
                    params={"run_id": run_id, "path": current or None, "page_token": token},
                )
                for item in response.get("files", []):
                    normalized = {
                        "path": scan_text(str(item.get("path", ""))).text,
                        "is_dir": bool(item.get("is_dir", False)),
                        "file_size": item.get("file_size"),
                    }
                    results.append(normalized)
                    if normalized["is_dir"]:
                        pending.append(normalized["path"])
                    if len(results) >= max_results:
                        break
                token = response.get("next_page_token")
                if not token or len(results) >= max_results:
                    break
        return tuple(results[:max_results])

    def compare_runs(self, run_ids: list[str]) -> list[ExternalRun]:
        return [self.get_run(run_id) for run_id in run_ids]
