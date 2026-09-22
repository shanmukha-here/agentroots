import hashlib
import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.service import ResearchService


def main() -> None:
    with TemporaryDirectory() as tmp:
        workspace = Path(tmp)
        project_root = workspace / "demo-project"
        project_root.mkdir()
        registry = workspace / "projects.json"
        os.environ["AGENTROOTS_PROJECT_REGISTRY"] = str(registry)
        subprocess.run(
            ["git", "init", "--quiet", str(project_root)],
            check=True,
            capture_output=True,
        )
        resolve_project_identity(project_root, configured="demo", path=registry)
        service = ResearchService(Database(workspace / "demo.sqlite3"))
        code_finding = service.propose(
            project="demo",
            type="finding",
            title="Cache module uses a 1024 entry limit",
            body="Candidate code fact from the main Codex agent.",
            creator="codex-main",
            mode="debugging",
        )
        service.review(code_finding["id"], actor="deepseek-worker", verdict="provisional")
        code = project_root / "cache.py"
        code.write_text("CACHE_SIZE = 1024\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(project_root), "add", "--", "cache.py"],
            check=True,
            capture_output=True,
        )
        digest = hashlib.sha256(code.read_bytes()).hexdigest()
        service.link_evidence(
            EvidenceLink(
                code_finding["id"],
                "cache.py",
                "git-file",
                "Reviewed implementation",
                digest,
            ),
            actor="deepseek-worker",
            project_root=project_root,
        )
        service.review(code_finding["id"], actor="codex-reviewer", verdict="accepted")
        failed = service.propose(
            project="demo",
            type="observation",
            title="Oversized cache failed",
            body="OOM at 8 GiB; do not repeat.",
            creator="deepseek-worker",
            mode="exploratory",
            metadata={"failed": True},
        )
        trace = workspace / "oom-abc123.log"
        command = "benchmark-cache --size 8GiB"
        trace.write_text(
            json.dumps(
                {
                    "schema": "agentroots.test-receipt.v1",
                    "command": command,
                    "exit_code": 1,
                    "summary": "OOM, no score",
                },
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        service.link_evidence(
            EvidenceLink(
                failed["id"],
                "test://cache/abc123",
                "test",
                "OOM, no score",
                hashlib.sha256(trace.read_bytes()).hexdigest(),
                {
                    "command": command,
                    "exit_code": 1,
                    "trace_uri": trace.resolve().as_uri(),
                },
            ),
            actor="deepseek-worker",
        )
        conflict = service.propose(
            project="demo",
            type="finding",
            title="Cache regresses writes",
            body="Codex subagent found conflicting write-path evidence.",
            creator="codex-subagent",
        )
        service.link(conflict["id"], code_finding["id"], "contradicts", "codex-subagent")
        failed_record = service.get_record(failed["id"])
        failed_evidence_status = failed_record["evidence"][0]["metadata"]["verification"][
            "status"
        ]
        assert failed_evidence_status == "reference_valid"
        code.write_text("CACHE_SIZE = 2048\n", encoding="utf-8")
        assert service.check_git_staleness("demo", project_root) == [code_finding["id"]]
        packet = service.context("demo", token_budget=1500)
        assert code_finding["id"] not in packet["record_ids"]
        warning_surfaced = failed["id"] in packet["record_ids"]
        assert warning_surfaced
        print(
            json.dumps(
                {
                    "scenario": "synthetic three-agent continuity",
                    "accepted_code_finding_became_stale": code_finding["id"]
                    not in packet["record_ids"],
                    "reported_failure_evidence_status": failed_evidence_status,
                    "prior_failure_warning_surfaced": warning_surfaced,
                    "fresh_agent_decision": "skip the previously reported 8 GiB cache attempt",
                    "packet_tokens": packet["estimated_tokens"],
                    "record_ids": packet["record_ids"],
                },
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
