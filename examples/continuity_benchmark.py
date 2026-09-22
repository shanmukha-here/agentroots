import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from tempfile import TemporaryDirectory

from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.service import ResearchService


def main() -> None:
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        db = workspace / "benchmark.sqlite3"
        registry = workspace / "projects.json"
        project_root = workspace / "benchmark-project"
        evidence_root = project_root / "evidence"
        evidence_root.mkdir(parents=True)
        os.environ["AGENTROOTS_PROJECT_REGISTRY"] = str(registry)
        subprocess.run(
            ["git", "init", "--quiet", str(project_root)],
            check=True,
            capture_output=True,
        )
        resolve_project_identity(
            project_root,
            configured="benchmark",
            path=registry,
        )
        writer = ResearchService(Database(db))
        for index in range(100):
            outcome = index % 7
            evidence_file = evidence_root / f"experiment-{index}.json"
            evidence_file.write_text(
                json.dumps(
                    {
                        "schema": "agentroots.continuity-fixture.v1",
                        "configuration": index,
                        "measured_outcome": outcome,
                        "failed": index == 73,
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            record = writer.propose(
                project="benchmark",
                type="finding",
                title=f"Experiment {index}",
                body=f"Configuration {index} produced measured outcome {outcome}.",
                creator="writer",
                metadata={"failed": index == 73},
            )
            writer.link_evidence(
                EvidenceLink(
                    record["id"],
                    f"evidence/experiment-{index}.json",
                    "git-file",
                    f"Frozen synthetic result {index}",
                    hashlib.sha256(evidence_file.read_bytes()).hexdigest(),
                ),
                actor="writer",
                project_root=project_root,
            )
            writer.review(record["id"], actor="reviewer", verdict="provisional")
            writer.review(record["id"], actor="reviewer", verdict="accepted")
        subprocess.run(
            ["git", "-C", str(project_root), "add", "--", "evidence"],
            check=True,
            capture_output=True,
        )

        reader = ResearchService(Database(db))
        cold_started = time.perf_counter()
        packet = reader.context("benchmark", "Configuration 73", token_budget=650)
        cold_ms = (time.perf_counter() - cold_started) * 1000
        warm_started = time.perf_counter()
        packet = reader.context("benchmark", "Configuration 73", token_budget=650)
        warm_ms = (time.perf_counter() - warm_started) * 1000
        target = next(
            packet["records"][record_id]
            for record_id in packet["sections"]["accepted_findings"]
            if packet["records"][record_id]["title"] == "Experiment 73"
        )
        print(
            json.dumps(
                {
                    "records": 100,
                    "corpus": "synthetic Git-bound fixtures",
                    "target_recalled": target["metadata"]["failed"],
                    "estimated_tokens": packet["estimated_tokens"],
                    "token_budget": packet["token_budget"],
                    "cold_latency_ms": round(cold_ms, 3),
                    "warm_latency_ms": round(warm_ms, 3),
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
