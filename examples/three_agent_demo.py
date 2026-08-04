import hashlib
from pathlib import Path
from tempfile import TemporaryDirectory

from research_state_mcp.db import Database
from research_state_mcp.models import EvidenceLink
from research_state_mcp.service import ResearchService


def main() -> None:
    with TemporaryDirectory() as tmp:
        service = ResearchService(Database(Path(tmp) / "demo.sqlite3"))
        hypothesis = service.propose(
            project="demo",
            type="hypothesis",
            title="Cache improves reads",
            body="Candidate from Claude-style agent.",
            creator="claude",
            mode="preregistered",
        )
        service.review(hypothesis["id"], actor="worker", verdict="provisional")
        code = Path(tmp) / "cache.py"
        code.write_text("CACHE_SIZE = 1024\n", encoding="utf-8")
        digest = hashlib.sha256(code.read_bytes()).hexdigest()
        service.link_evidence(
            EvidenceLink(
                hypothesis["id"], "cache.py", "git-file", "Reviewed implementation", digest
            ),
            actor="worker",
        )
        service.review(hypothesis["id"], actor="codex", verdict="accepted")
        failed = service.propose(
            project="demo",
            type="observation",
            title="Oversized cache failed",
            body="OOM at 8 GiB; do not repeat.",
            creator="worker",
            mode="exploratory",
            metadata={"failed": True},
        )
        service.link_evidence(
            EvidenceLink(
                failed["id"],
                "mlflow://runs/abc123",
                "mlflow-run",
                "OOM, no score",
                metadata={"run_id": "abc123"},
            ),
            actor="worker",
        )
        conflict = service.propose(
            project="demo",
            type="finding",
            title="Cache regresses writes",
            body="DeepSeek-style worker found conflicting write-path evidence.",
            creator="deepseek",
        )
        service.link(conflict["id"], hypothesis["id"], "contradicts", "deepseek")
        code.write_text("CACHE_SIZE = 2048\n", encoding="utf-8")
        assert service.check_git_staleness("demo", Path(tmp)) == [hypothesis["id"]]
        packet = service.context("demo", token_budget=1500)
        assert hypothesis["id"] not in packet["record_ids"]
        print(packet)


if __name__ == "__main__":
    main()
