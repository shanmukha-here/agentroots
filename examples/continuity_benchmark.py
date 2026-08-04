import json
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.service import ResearchService


def main() -> None:
    with TemporaryDirectory() as tmp:
        db = Path(tmp) / "benchmark.sqlite3"
        writer = ResearchService(Database(db))
        for index in range(100):
            record = writer.propose(
                project="benchmark",
                type="finding",
                title=f"Experiment {index}",
                body=f"Configuration {index} produced measured outcome {index % 7}.",
                creator="writer",
                metadata={"failed": index == 73},
            )
            writer.link_evidence(
                EvidenceLink(record["id"], f"mlflow://runs/{index}", "mlflow-run"),
                actor="writer",
            )
            writer.review(record["id"], actor="reviewer", verdict="provisional")
            writer.review(record["id"], actor="reviewer", verdict="accepted")

        reader = ResearchService(Database(db))
        started = time.perf_counter()
        packet = reader.context("benchmark", "Configuration 73", token_budget=500)
        elapsed_ms = (time.perf_counter() - started) * 1000
        target = next(
            item
            for item in packet["sections"]["accepted_findings"]
            if item["title"] == "Experiment 73"
        )
        print(
            json.dumps(
                {
                    "records": 100,
                    "target_recalled": target["metadata"]["failed"],
                    "estimated_tokens": packet["estimated_tokens"],
                    "token_budget": packet["token_budget"],
                    "latency_ms": round(elapsed_ms, 3),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
