from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, ClassVar, cast
from unittest.mock import patch

from agentroots.adapters.mlflow import MLflowAdapter
from agentroots.backfill import discover_opencode, export_sources
from agentroots.db import Database
from agentroots.episodes import EpisodeStore
from agentroots.graph import write_graph_html
from agentroots.hooks import HeuristicExtractor, HookEngine, extract_episode_backfill
from agentroots.models import EvidenceLink
from agentroots.service import GovernanceError, ResearchService


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_opencode_fixture(path: Path, project_root: Path) -> None:
    conversation = (
        "The batch size 4096 experiment failed with CUDA out of memory on a 24 GiB GPU. "
        "We decided to use gradient accumulation with a physical batch size of 256. "
        "The next step is to compare throughput at the same fixed seed."
    )
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE session(
              id TEXT, parent_id TEXT, directory TEXT, title TEXT,
              time_created INTEGER, time_updated INTEGER
            );
            CREATE TABLE message(
              id TEXT, session_id TEXT, time_created INTEGER, time_updated INTEGER, data TEXT
            );
            CREATE TABLE part(
              id TEXT, message_id TEXT, session_id TEXT, time_created INTEGER, data TEXT
            );
            """
        )
        connection.execute(
            "INSERT INTO session VALUES(?,?,?,?,?,?)",
            ("old-opencode", None, str(project_root), "Prior training work", 1, 2),
        )
        connection.execute(
            "INSERT INTO message VALUES(?,?,?,?,?)",
            ("old-message", "old-opencode", 1, 2, json.dumps({"role": "assistant"})),
        )
        connection.execute(
            "INSERT INTO part VALUES(?,?,?,?,?)",
            (
                "old-part",
                "old-message",
                "old-opencode",
                1,
                json.dumps({"type": "text", "text": conversation}),
            ),
        )


class _MLflowFixture(BaseHTTPRequestHandler):
    payload: ClassVar[dict[str, Any]] = {
        "run": {
            "info": {
                "run_id": "oom-4096",
                "experiment_id": "42",
                "status": "FAILED",
                "run_name": "batch-4096-fixed-seed",
                "start_time": 1_800_000_000_000,
                "end_time": 1_800_000_010_000,
                "artifact_uri": "file:///external/mlruns/42/oom-4096",
            },
            "data": {
                "metrics": [
                    {"key": "peak_memory_gib", "value": 24.0},
                    {"key": "completed_steps", "value": 0.0},
                ],
                "params": [
                    {"key": "batch_size", "value": "4096"},
                    {"key": "seed", "value": "17"},
                ],
                "tags": [
                    {"key": "mlflow.runName", "value": "batch-4096-fixed-seed"},
                    {"key": "mlflow.source.git.commit", "value": "demo-commit"},
                ],
            },
        }
    }

    def do_GET(self) -> None:
        if not self.path.startswith("/api/2.0/mlflow/runs/get"):
            self.send_error(404)
            return
        encoded = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _start_mlflow_fixture() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MLflowFixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = cast(tuple[str, int], server.server_address)
    return server, f"http://{host}:{port}"


def _context(result: dict[str, Any]) -> str:
    return str(result.get("hookSpecificOutput", {}).get("additionalContext", ""))


def _run(run_root: Path) -> dict[str, Any]:
    project_root = run_root / "shared-project"
    project_root.mkdir()
    subprocess.run(
        ["git", "init", "--quiet", str(project_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "remote",
            "add",
            "origin",
            "https://example.invalid/shared-agent-project.git",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    source_db = run_root / "opencode-history.sqlite3"
    _write_opencode_fixture(source_db, project_root)
    source_hash_before = _sha256(source_db)
    sources = discover_opencode(source_db)
    project = sources[0].project_id
    bundle = run_root / "approved-history.jsonl"
    export_result = export_sources(sources, bundle, approved=True)

    database = Database(run_root / "agentroots.sqlite3")
    episode_result = EpisodeStore(database).import_history_jsonl(bundle, project)
    extraction_result = extract_episode_backfill(
        database,
        project,
        extractor=HeuristicExtractor(),
    )
    service = ResearchService(database)
    candidates = service.list_candidates(project)
    failed_candidate = next(
        candidate for candidate in candidates if "4096" in candidate["body"]
    )
    promoted = service.promote_candidate(
        failed_candidate["id"],
        actor="codex-main",
        record_type="observation",
        title="Batch size 4096 exhausted GPU memory",
        body=(
            "A fixed-seed batch size 4096 run exhausted the available 24 GiB GPU memory. "
            "The conclusion came from an exact span in the approved prior OpenCode conversation. "
            "This configuration should not be repeated on the same hardware. "
            "The next trial should use physical batch size 256 with gradient accumulation."
        ),
        metadata={"failed": True, "hardware": "24 GiB GPU"},
    )
    observation = promoted["record"]
    service.review(observation["id"], actor="codex-main", verdict="provisional")
    self_accept_blocked = False
    try:
        service.review(
            observation["id"],
            actor=observation["creator"],
            verdict="accepted",
        )
    except GovernanceError:
        self_accept_blocked = True

    mlflow_server, mlflow_url = _start_mlflow_fixture()
    try:
        run = MLflowAdapter(mlflow_url).get_run("oom-4096")
    finally:
        mlflow_server.shutdown()
        mlflow_server.server_close()
    observation_tracker = service.link_external_run(
        observation["id"], run, actor="codex-main"
    )
    observation = service.review(
        observation["id"], actor="independent-reviewer", verdict="accepted"
    )

    origin = service.propose(
        project=project,
        type="origin",
        title="Efficient reproducible model iteration",
        body="Avoid repeated experiments while preserving evidence and the next useful action.",
        creator="codex-main",
    )
    goal = service.propose(
        project=project,
        type="goal",
        title="Find a stable high-throughput training configuration",
        body="Improve throughput without exceeding the available GPU memory.",
        creator="codex-main",
    )
    question = service.propose(
        project=project,
        type="question",
        title="Can accumulation preserve throughput at batch 256?",
        body="Compare effective batch size while holding the dataset, seed, and code fixed.",
        creator="codex-main",
    )
    hypothesis = service.propose(
        project=project,
        type="hypothesis",
        title="Gradient accumulation avoids the 4096 batch OOM",
        body="Physical batch 256 with accumulation should fit memory and preserve useful throughput.",
        creator="codex-main",
    )
    experiment = service.propose(
        project=project,
        type="experiment",
        title="Fixed-seed accumulation comparison",
        body="Run physical batch 256 under the same seed and code revision as the failed run.",
        creator="codex-main",
    )
    service.link(origin["id"], goal["id"], "decomposes", "codex-main")
    service.link(goal["id"], question["id"], "decomposes", "codex-main")
    service.link(question["id"], hypothesis["id"], "decomposes", "codex-main")
    service.link(experiment["id"], hypothesis["id"], "tests", "codex-main")

    finding = service.propose(
        project=project,
        type="finding",
        title="Batch size 4096 is not viable on the current GPU",
        body=(
            "The batch size 4096 configuration is not viable on the current 24 GiB GPU. "
            "An exact prior conversation span and the resolved MLflow run both record the OOM. "
            "Repeating the same run would consume compute without adding evidence. "
            "The active frontier is the batch 256 accumulation comparison."
        ),
        creator="codex-main",
        metadata={"failed": True},
    )
    source_evidence = observation["evidence"][0]
    service.link_evidence(
        EvidenceLink(
            finding["id"],
            source_evidence["uri"],
            source_evidence["kind"],
            source_evidence["summary"],
            source_evidence["content_hash"],
            source_evidence["metadata"],
        ),
        actor="codex-main",
    )
    service.review(finding["id"], actor="codex-main", verdict="provisional")
    tracker = service.import_external_run(
        finding["id"],
        run,
        actor="codex-main",
        experiment_record_id=experiment["id"],
    )
    finding = service.review(
        finding["id"], actor="independent-reviewer", verdict="accepted"
    )
    service.link(finding["id"], observation["id"], "derived_from", "codex-main")
    service.link(finding["id"], hypothesis["id"], "contradicts", "codex-main")

    service.link(observation["id"], tracker["run_ref"]["id"], "derived_from", "codex-main")

    code = project_root / "training_config.py"
    code.write_text("PHYSICAL_BATCH = 256\nACCUMULATION = 16\n", encoding="utf-8")
    code_record = service.propose(
        project=project,
        type="finding",
        title="Training config uses physical batch 256",
        body=(
            "The current training configuration uses physical batch size 256 and accumulation 16. "
            "This statement is bound to the exact content hash of training_config.py. "
            "Agents may rely on it only while that file hash remains unchanged. "
            "Any edit should automatically move the finding out of accepted context."
        ),
        creator="codex-main",
    )
    service.review(code_record["id"], actor="reviewer", verdict="provisional")
    service.link_evidence(
        EvidenceLink(
            code_record["id"],
            "training_config.py",
            "git-file",
            "Current training configuration",
            _sha256(code),
        ),
        actor="reviewer",
        project_root=project_root,
    )
    code_record = service.review(
        code_record["id"], actor="reviewer", verdict="accepted"
    )

    fresh_codex = HookEngine(database, HeuristicExtractor(), allow_semantic=False)
    session_start = fresh_codex.handle(
        {
            "project_id": project,
            "hook_event_name": "SessionStart",
            "event_id": "fresh-codex-start",
            "session_id": "fresh-codex",
            "cwd": str(project_root),
            "harness": "codex",
        },
        extract=False,
    )
    open_prompt = fresh_codex.handle(
        {
            "project_id": project,
            "hook_event_name": "UserPromptSubmit",
            "event_id": "fresh-open-prompt",
            "session_id": "fresh-codex",
            "cwd": str(project_root),
            "harness": "codex",
            "prompt": "Continue from the current frontier and choose the next useful action.",
        },
        extract=False,
    )
    pre_tool = fresh_codex.handle(
        {
            "project_id": project,
            "hook_event_name": "PreToolUse",
            "event_id": "fresh-duplicate-intent",
            "session_id": "fresh-codex",
            "cwd": str(project_root),
            "harness": "codex",
            "tool_name": "shell",
            "tool_input": "python train.py --batch-size 4096 --seed 17",
        },
        extract=False,
    )
    duplicate_context = _context(pre_tool)

    fresh_codex.handle(
        {
            "project_id": project,
            "hook_event_name": "PreToolUse",
            "event_id": "fresh-edit-intent",
            "session_id": "fresh-codex",
            "cwd": str(project_root),
            "harness": "codex",
            "tool_name": "edit",
            "tool_input": "Update training_config.py",
        },
        extract=False,
    )
    status_before_edit = service.get_record(code_record["id"])["status"]
    code.write_text("PHYSICAL_BATCH = 128\nACCUMULATION = 32\n", encoding="utf-8")
    fresh_codex.handle(
        {
            "project_id": project,
            "hook_event_name": "PostToolUse",
            "event_id": "fresh-edit-result",
            "session_id": "fresh-codex",
            "cwd": str(project_root),
            "harness": "codex",
            "tool_name": "edit",
            "tool_input": "Update training_config.py",
            "tool_response": "Updated training_config.py",
        },
        extract=False,
    )
    stale_code_record = service.get_record(code_record["id"])

    frontier = service.frontier(project)
    validation = service.validate(project, project_root)
    graph_path = write_graph_html(service.graph(project), run_root / "project-graph.html")
    backup_path = service.backup(run_root / "agentroots-backup.sqlite3")

    with database.connect() as connection:
        edit_event_ids = {
            str(row[0])
            for row in connection.execute(
                "SELECT event_id FROM hook_events WHERE event_id IN (?,?)",
                ("fresh-edit-intent", "fresh-edit-result"),
            )
        }
        counts = {
            "records": connection.execute("SELECT count(*) FROM records").fetchone()[0],
            "episodes": connection.execute("SELECT count(*) FROM episodes").fetchone()[0],
            "candidates": connection.execute(
                "SELECT count(*) FROM extraction_candidates"
            ).fetchone()[0],
            "injections": connection.execute(
                "SELECT count(*) FROM hook_injections"
            ).fetchone()[0],
        }

    result: dict[str, Any] = {
        "run_at": datetime.now(UTC).isoformat(),
        "project": project,
        "read_only_backfill": {
            "source_kind": "synthetic OpenCode SQLite fixture",
            "access": "read-only export from the fixture; no source conversation mutation",
            "source_unchanged": source_hash_before == _sha256(source_db),
            "export": export_result,
            "episodes": episode_result,
            "extraction": extraction_result,
        },
        "governance": {
            "candidate": failed_candidate["id"],
            "promoted_record": observation["id"],
            "self_accept_blocked": self_accept_blocked,
            "accepted_status": observation["status"],
            "conversation_evidence_status": observation["evidence"][0]["metadata"]
            ["verification"]["status"],
            "tracker_evidence_status": observation_tracker["evidence"][-1]["metadata"]
            ["verification"]["status"],
        },
        "mlflow": {
            "source_kind": "synthetic local MLflow REST fixture",
            "adapter_request": "real HTTP request through the shipped read-only MLflowAdapter",
            "run_id": run.run_id,
            "run_status": run.status,
            "run_ref": tracker["run_ref"]["id"],
            "evidence_status": tracker["record"]["evidence"][-1]["metadata"]["verification"]["status"],
        },
        "fresh_codex": {
            "session_start_context": _context(session_start),
            "open_prompt_context": _context(open_prompt),
            "pre_tool_context": duplicate_context,
            "duplicate_risk_warning_surfaced": (
                "4096" in duplicate_context and "not viable" in duplicate_context.lower()
            ),
            "execution_control": "advisory only; AgentRoots did not execute or block the command",
            "notification": {
                "system_message": pre_tool.get("systemMessage", ""),
                "tokens": max(1, (len(duplicate_context.encode("utf-8")) + 2) // 3),
            },
        },
        "staleness": {
            "record": code_record["id"],
            "before": code_record["status"],
            "pre_tool_status": status_before_edit,
            "after_file_edit": stale_code_record["status"],
            "pre_tool_event_recorded": "fresh-edit-intent" in edit_event_ids,
            "post_tool_event_recorded": "fresh-edit-result" in edit_event_ids,
            "excluded_from_context": code_record["id"]
            not in service.context(project, "training config")["record_ids"],
        },
        "frontier": [
            {
                "id": item["id"],
                "type": item["type"],
                "title": item["title"],
                "reasons": item["frontier"]["reasons"],
            }
            for item in frontier
        ],
        "validation": validation,
        "counts": counts,
        "artifacts": {
            "graph": str(graph_path.resolve()),
            "database": str(database.path.resolve()),
            "backup": str(backup_path.resolve()),
        },
    }
    summary_path = run_root / "summary.json"
    summary_path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["artifacts"]["summary"] = str(summary_path.resolve())
    return result


def run(output_root: Path) -> dict[str, Any]:
    """Run the component flow while keeping all process configuration isolated."""

    output_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(mkdtemp(prefix="run-", dir=output_root))
    environment = {
        "AGENTROOTS_PROJECT_REGISTRY": str(run_root / "projects.json"),
        "AGENTROOTS_SEMANTIC": "off",
    }
    with patch.dict(os.environ, environment, clear=False):
        return _run(run_root)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the real local AgentRoots continuity flow")
    parser.add_argument("--output", type=Path, default=Path("work") / "full-flow-demo")
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2))


if __name__ == "__main__":
    main()
