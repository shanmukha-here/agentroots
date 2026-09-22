from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from rapidfuzz.fuzz import WRatio

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agentroots.db import Database
from agentroots.models import EvidenceLink
from agentroots.project_identity import resolve_project_identity
from agentroots.retrieval import SemanticRetriever
from agentroots.service import GovernanceError, ResearchService


@dataclass(frozen=True)
class Case:
    name: str
    project: str
    event: str
    query: str
    relevant: tuple[str, ...]
    forbidden: tuple[str, ...] = ()


SPECS: dict[str, list[tuple[str, str, str, str, str]]] = {
    "agentroots": [
        ("ar.origin", "origin", "accepted", "Why AgentRoots exists", "Agents repeatedly rebuild project context across sessions and workers."),
        ("ar.goal", "goal", "accepted", "Resume from governed project state", "A fresh agent should continue from evidence backed state without replaying transcripts."),
        ("ar.packet", "finding", "accepted", "Reviewed packets prevent repeated repository reads", "Accepted findings and failed attempts let workers skip repository discovery already completed."),
        ("ar.failed_replay", "observation", "accepted", "Full transcript replay wastes context", "The replay run consumed 6842 tokens without improving task accuracy. Do not repeat it."),
        ("ar.frontier", "question", "candidate", "How should stale code findings be refreshed", "Git changes can invalidate code dependent claims and require targeted review."),
        ("ar.hypothesis", "hypothesis", "candidate", "Graph expansion improves frontier recall", "Structured neighbors may recover relevant decisions that lexical matching misses."),
        ("ar.experiment", "experiment", "candidate", "Compare lexical vector and graph retrieval", "Measure recall leakage latency and useful evidence per injected token."),
        ("ar.run", "run_ref", "accepted", "Referenced retrieval benchmark run 004", "Synthetic run pointer used only by this frozen retrieval corpus. No external adapter validation is claimed."),
        ("ar.decision", "decision", "accepted", "Keep project scope as a hard filter", "Cross workspace memories must never enter normal prompt injection."),
        ("ar.stale", "finding", "stale", "Old packet format uses a flat records list", "This code fact became stale after context packets moved to typed sections."),
        ("ar.secret", "finding", "rejected", "Store raw environment output in memory", "Rejected because logs may contain credentials and prompt injection text."),
        ("ar.artifact", "artifact_ref", "candidate", "Retrieval evaluation report", "External benchmark report contains per query rankings and latency distributions."),
    ],
    "portfolio": [
        ("web.origin", "origin", "accepted", "Why the research portfolio exists", "Present research engineering and publications through an interactive personal website."),
        ("web.goal", "goal", "accepted", "Publish accurate portfolio content", "Replace placeholders with verified project descriptions and publication metadata."),
        ("web.cache", "finding", "accepted", "Image caching reduces repeated network reads", "The website image loader avoids duplicate fetches when navigating between project pages."),
        ("web.context", "decision", "accepted", "Use concise project context cards", "Visitors should understand each project without reading long transcripts or lab notes."),
        ("web.graph", "finding", "accepted", "Knowledge graph needs human readable labels", "Graph cards require complete titles and descriptions to remain useful at a glance."),
        ("web.failed", "observation", "accepted", "Cream background reduced text contrast", "The visual test showed muted contrast and the palette was changed to a neutral white."),
        ("web.question", "question", "candidate", "Which content still needs replacement", "Several publication and biography placeholders require source backed final text."),
        ("web.deploy", "experiment", "candidate", "Test static deployment caching", "Compare cache headers and image loading behavior on the deployed portfolio."),
        ("web.run", "run_ref", "candidate", "Lighthouse performance run", "External browser performance trace for the portfolio build."),
        ("web.stale", "finding", "stale", "The homepage uses the old cream theme", "This styling claim is stale after the neutral background update."),
    ],
    "robot": [
        ("bot.origin", "origin", "accepted", "Build an embodied personal assistant", "Use existing phone and laptop hardware for a stationary sensing and planning assistant."),
        ("bot.goal", "goal", "accepted", "Demonstrate a zero purchase stationary workflow", "Coordinate perception and actions without buying new hardware."),
        ("bot.camera", "finding", "accepted", "OnePlus camera can provide visual observations", "The existing phone can act as a network camera for the assistant."),
        ("bot.laptop", "finding", "accepted", "Dell laptop can coordinate local services", "The existing Windows laptop is adequate for orchestration after performance diagnosis."),
        ("bot.failed", "observation", "accepted", "Blind Linux installation is premature", "Changing the operating system before diagnosing disk memory and thermals adds avoidable risk."),
        ("bot.question", "question", "candidate", "Is the hard disk the primary bottleneck", "Measure disk queue memory pressure and thermal throttling before changing software."),
        ("bot.experiment", "experiment", "candidate", "Record a stationary camera task", "Test phone sensing laptop coordination and a reversible notification action."),
        ("bot.decision", "decision", "accepted", "Start with existing hardware", "The first demonstration must remain reversible and require no purchases."),
        ("bot.run", "run_ref", "candidate", "Camera latency measurement", "External timing run for phone frames reaching the laptop."),
        ("bot.stale", "finding", "superseded", "Buy a dedicated edge GPU first", "Superseded after the zero purchase design boundary was accepted."),
    ],
}

LINKS = [
    ("ar.origin", "ar.goal", "decomposes"),
    ("ar.packet", "ar.goal", "resolves"),
    ("ar.failed_replay", "ar.packet", "derived_from"),
    ("ar.hypothesis", "ar.frontier", "depends_on"),
    ("ar.experiment", "ar.hypothesis", "tests"),
    ("ar.experiment", "ar.run", "produced"),
    ("ar.run", "ar.packet", "supports"),
    ("ar.decision", "ar.secret", "contradicts"),
    ("ar.failed_replay", "ar.secret", "contradicts"),
    ("web.origin", "web.goal", "decomposes"),
    ("web.graph", "web.goal", "resolves"),
    ("web.failed", "web.graph", "derived_from"),
    ("bot.origin", "bot.goal", "decomposes"),
    ("bot.decision", "bot.goal", "selected"),
    ("bot.failed", "bot.stale", "contradicts"),
]

# Same-project and cross-project semantic collisions prevent tiny-corpus saturation.
# These are non-gold records, so every retrieved distractor lowers precision.
DISTRACTORS: dict[str, list[tuple[str, str, str, str, str]]] = {
    "agentroots": [
        ("ar.d01", "finding", "accepted", "SQLite WAL supports concurrent readers", "Local state remains responsive while one agent records a revision."),
        ("ar.d02", "decision", "accepted", "MCP resources stay read only", "Mutations use governed tools while resources expose stable views."),
        ("ar.d03", "question", "candidate", "Which graph colors identify evidence", "Human visualization needs distinct accessible node colors."),
        ("ar.d04", "artifact_ref", "candidate", "Package release checklist", "Build metadata and publication checks for a future release."),
        ("ar.d05", "finding", "accepted", "JSONL preserves event portability", "Export and import move compact state between machines."),
        ("ar.d06", "observation", "candidate", "Large card bodies reduce graph readability", "Collapsed summaries help humans navigate dense projects."),
        ("ar.d07", "hypothesis", "rejected", "Embeddings should become source of truth", "Rejected because similarity cannot establish scientific validity."),
        ("ar.d08", "experiment", "candidate", "Measure SQLite backup duration", "Time backup and restore with many small revisions."),
        ("ar.d09", "run_ref", "candidate", "Graph layout timing run", "Browser trace measures interactive map rendering."),
        ("ar.d10", "finding", "accepted", "Idempotency prevents duplicate proposals", "Repeated tool calls with one key resolve to one record."),
        ("ar.d11", "goal", "candidate", "Improve package onboarding", "Reduce setup steps for generic MCP clients."),
        ("ar.d12", "decision", "superseded", "Search every workspace by default", "Old global search policy was replaced by hard project scope."),
        ("ar.d13", "observation", "accepted", "Compact titles improve scan speed", "Short labels help users inspect a knowledge graph."),
        ("ar.d14", "question", "candidate", "How should packet audit retention work", "Packet hashes may outlive expirable rendered packets."),
        ("ar.d15", "finding", "candidate", "Worker summaries can omit important caveats", "Review must inspect evidence and contradiction links."),
    ],
    "portfolio": [
        ("web.d01", "finding", "accepted", "Project cards avoid repeated page reads", "Concise summaries reduce navigation work for visitors."),
        ("web.d02", "experiment", "candidate", "Compare lexical site search", "Measure typo handling across project descriptions."),
        ("web.d03", "decision", "accepted", "Keep publication filters project scoped", "Search results should not mix unrelated pages."),
        ("web.d04", "run_ref", "candidate", "Browser graph latency run", "Trace interactive graph rendering on a laptop."),
        ("web.d05", "finding", "accepted", "Static JSON feeds the project graph", "The renderer consumes versioned node and edge data."),
        ("web.d06", "question", "candidate", "Can visitors resume a saved graph view", "Persist filters without storing private browsing history."),
        ("web.d07", "observation", "rejected", "Paste raw build logs into project cards", "Rejected because logs are noisy and may leak secrets."),
        ("web.d08", "goal", "candidate", "Improve mobile navigation", "Make research pages readable on narrow screens."),
        ("web.d09", "artifact_ref", "candidate", "Image optimization report", "External report contains cache and byte size measurements."),
        ("web.d10", "finding", "accepted", "Neutral backgrounds preserve logo clarity", "High resolution assets remain crisp on light surfaces."),
        ("web.d11", "hypothesis", "candidate", "Graph neighbors improve project discovery", "Related publications may surface through explicit links."),
        ("web.d12", "decision", "superseded", "Show every project in one flat list", "Dense navigation replaced the old flat layout."),
        ("web.d13", "observation", "accepted", "Full descriptions overflow compact cards", "Expandable details prevent clipped text."),
        ("web.d14", "question", "candidate", "Which claims need source links", "Audit public copy before launch."),
        ("web.d15", "finding", "candidate", "Agent generated copy needs human review", "Draft text may confuse current work with future plans."),
    ],
    "robot": [
        ("bot.d01", "finding", "accepted", "Local cache avoids repeated camera downloads", "Recent frames can be reused during one sensing task."),
        ("bot.d02", "decision", "accepted", "Keep device actions explicitly scoped", "One project must not control unrelated machines."),
        ("bot.d03", "experiment", "candidate", "Benchmark semantic command retrieval", "Compare compact language matching for device tasks."),
        ("bot.d04", "run_ref", "candidate", "Laptop service latency run", "Timing trace covers local coordination."),
        ("bot.d05", "question", "candidate", "How should stale sensor facts expire", "Operational observations need a short validity window."),
        ("bot.d06", "observation", "accepted", "Raw terminal logs can expose credentials", "Store selected diagnostics rather than full environment output."),
        ("bot.d07", "finding", "rejected", "Install every agent tool globally", "Rejected because broad changes reduce reversibility."),
        ("bot.d08", "goal", "candidate", "Reduce repeated device inspection", "Persist verified capabilities for later sessions."),
        ("bot.d09", "artifact_ref", "candidate", "Thermal diagnosis report", "External measurements describe throttling behavior."),
        ("bot.d10", "finding", "accepted", "Structured state helps worker handoff", "A second agent can continue a device test from recorded facts."),
        ("bot.d11", "hypothesis", "candidate", "Graph links improve action planning", "Dependencies may recover safety decisions missed by words alone."),
        ("bot.d12", "decision", "superseded", "Use cloud orchestration first", "Local coordination replaced this earlier direction."),
        ("bot.d13", "observation", "accepted", "Long video history consumes storage", "Retain references and selected summaries."),
        ("bot.d14", "question", "candidate", "Which phone sensors remain available", "Verify APIs before planning mobile actions."),
        ("bot.d15", "finding", "candidate", "Cheap agents can inspect device metadata", "Governed review is still required before acceptance."),
    ],
}

CASES = [
    Case("session_start", "agentroots", "session_start", "continue the AgentRoots project", ("ar.origin", "ar.goal", "ar.packet", "ar.decision"), ("ar.stale", "ar.secret")),
    Case("semantic_resume", "agentroots", "user_prompt", "avoid making a new worker inspect all the same files again", ("ar.packet", "ar.failed_replay"), ("web.cache",)),
    Case("failed_path", "agentroots", "delegation", "what approach already burned context without helping", ("ar.failed_replay",), ("ar.secret",)),
    Case("retrieval_eval", "agentroots", "user_prompt", "benchmark semantic search against structured expansion", ("ar.experiment", "ar.hypothesis", "ar.run")),
    Case("typo_frontier", "agentroots", "user_prompt", "how shuld stlae cod fndings get refrshed", ("ar.frontier",)),
    Case("run_pointer", "agentroots", "experiment_complete", "where is the referenced retrieval comparison run", ("ar.run", "ar.artifact")),
    Case("scope_policy", "agentroots", "session_start", "should memories from other workspaces be injected", ("ar.decision",)),
    Case("security", "agentroots", "tool_result", "can raw terminal logs be stored as trusted memory", ("ar.secret", "ar.decision")),
    Case("website_graph", "portfolio", "user_prompt", "make the project map readable for humans at a glance", ("web.graph", "web.context"), ("ar.packet",)),
    Case("website_palette", "portfolio", "user_prompt", "why did we remove the creamy visual background", ("web.failed",), ("ar.failed_replay",)),
    Case("website_content", "portfolio", "session_start", "what remains before publishing the website", ("web.goal", "web.question")),
    Case("website_perf", "portfolio", "experiment_complete", "measure deployment image loading and cache headers", ("web.deploy", "web.run", "web.cache")),
    Case("robot_start", "robot", "session_start", "continue the embodied assistant without purchasing devices", ("bot.origin", "bot.goal", "bot.decision")),
    Case("robot_diagnosis", "robot", "user_prompt", "should we install Linux before checking why the laptop is slow", ("bot.failed", "bot.question"), ("bot.stale",)),
    Case("robot_camera", "robot", "delegation", "use the phone for a stationary visual sensing test", ("bot.camera", "bot.experiment", "bot.run")),
    Case("robot_compute", "robot", "user_prompt", "which existing machine can coordinate the services", ("bot.laptop", "bot.decision")),
]

EVENT_TYPES = {
    "session_start": {"origin": 0.18, "goal": 0.16, "decision": 0.12, "finding": 0.10},
    "delegation": {"goal": 0.10, "question": 0.12, "finding": 0.12, "observation": 0.12},
    "experiment_complete": {"experiment": 0.16, "run_ref": 0.16, "observation": 0.12, "artifact_ref": 0.10},
    "tool_result": {"decision": 0.14, "finding": 0.12, "observation": 0.12},
    "user_prompt": {"finding": 0.08, "decision": 0.08, "question": 0.06},
}


def _project_root(path: Path, project: str) -> Path:
    return path.parent / f"{path.stem}-projects" / project


def _prepare_project_evidence(
    path: Path, combined: dict[str, list[tuple[str, str, str, str, str]]]
) -> dict[str, tuple[Path, Path, str]]:
    registry = path.parent / f"{path.stem}-projects.json"
    os.environ["AGENTROOTS_PROJECT_REGISTRY"] = str(registry)
    evidence: dict[str, tuple[Path, Path, str]] = {}
    for project, specs in combined.items():
        root = _project_root(path, project)
        evidence_dir = root / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "--quiet", str(root)],
            check=True,
            capture_output=True,
        )
        resolve_project_identity(root, configured=project, path=registry)
        for key, kind, status, title, body in specs:
            item = evidence_dir / f"{key.replace('.', '_')}.json"
            item.write_text(
                json.dumps(
                    {
                        "schema": "agentroots.retrieval-fixture.v1",
                        "benchmark_key": key,
                        "record_type": kind,
                        "expected_status": status,
                        "title": title,
                        "body": body,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            evidence[key] = (
                root,
                item.relative_to(root),
                hashlib.sha256(item.read_bytes()).hexdigest(),
            )
        subprocess.run(
            ["git", "-C", str(root), "add", "--", "evidence"],
            check=True,
            capture_output=True,
        )
    return evidence


def populate(path: Path) -> tuple[ResearchService, list[dict[str, Any]], dict[str, str]]:
    if path.exists():
        path.unlink()
    service = ResearchService(Database(path))
    ids: dict[str, str] = {}
    combined = {project: specs + DISTRACTORS[project] for project, specs in SPECS.items()}
    evidence = _prepare_project_evidence(path, combined)
    for project, specs in combined.items():
        for key, kind, status, title, body in specs:
            metadata = {"benchmark_key": key, "synthetic_fixture": True}
            if kind == "decision":
                metadata.update(alternatives=["ignore prior state"], rationale="Evidence favors the selected path")
            record = service.propose(project=project, type=kind, title=title, body=body, creator="fixture", metadata=metadata)
            ids[key] = record["id"]
            if status in {"accepted", "stale", "superseded"}:
                service.review(record["id"], actor="reviewer", verdict="provisional")
                root, relative_path, digest = evidence[key]
                service.link_evidence(
                    EvidenceLink(
                        record["id"],
                        relative_path.as_posix(),
                        "git-file",
                        f"Frozen synthetic retrieval fixture for {key}",
                        digest,
                    ),
                    actor="reviewer",
                    project_root=root,
                )
                service.review(record["id"], actor="reviewer", verdict="accepted")
                if status != "accepted":
                    service.review(record["id"], actor="reviewer", verdict=status)
            elif status == "rejected":
                service.review(record["id"], actor="reviewer", verdict="rejected")
    for source, target, relation in LINKS:
        service.link(ids[source], ids[target], relation, "fixture")
    records: list[dict[str, Any]] = []
    for project in combined:
        records.extend(service.query(project, limit=200))
    return service, records, ids


def text(record: dict[str, Any]) -> str:
    return f"{record['type']} {record['status']} {record['title']} {record['body']}"


def key(record: dict[str, Any]) -> str:
    return str(record["metadata"]["benchmark_key"])


def fuzzy_rank(records: list[dict[str, Any]], case: Case, global_scope: bool = False) -> list[dict[str, Any]]:
    pool = records if global_scope else [record for record in records if record["project"] == case.project]
    return sorted(pool, key=lambda record: WRatio(case.query, text(record)), reverse=True)


def service_rank(service: ResearchService, case: Case) -> list[dict[str, Any]]:
    return service.query(case.project, case.query, limit=100)


def lexical_rank(service: ResearchService, case: Case) -> list[dict[str, Any]]:
    return service._lexical_query(case.project, case.query, limit=100)


def graph_rerank(service: ResearchService, ranked: list[dict[str, Any]], project: str) -> list[dict[str, Any]]:
    graph = service.graph(project)
    by_id = {record["id"]: record for record in graph["nodes"]}
    neighbors: dict[str, set[str]] = defaultdict(set)
    for edge in graph["edges"]:
        neighbors[edge["source_id"]].add(edge["target_id"])
        neighbors[edge["target_id"]].add(edge["source_id"])
    scores = {record["id"]: 1 / (10 + position) for position, record in enumerate(ranked, 1)}
    candidates = {record["id"]: record for record in ranked}
    for position, record in enumerate(ranked[:3], 1):
        for neighbor in neighbors[record["id"]]:
            candidates[neighbor] = by_id[neighbor]
            scores[neighbor] = scores.get(neighbor, 0) + 0.055 / position
    return sorted(candidates.values(), key=lambda record: scores.get(record["id"], 0), reverse=True)


def compact_context_rank(
    service: ResearchService, records: list[dict[str, Any]], case: Case
) -> list[dict[str, Any]]:
    by_title = {str(record["title"]): record for record in records}
    if len(by_title) != len(records):
        raise AssertionError("compact-context benchmark requires unique fixture titles")
    packet = service.compact_context(case.project, case.query, limit=5, token_budget=650)
    ranked = []
    for line in str(packet["text"]).splitlines()[1:]:
        _, separator, title = line.partition("] ")
        if not separator or title not in by_title:
            raise AssertionError(f"unrecognized compact-context line: {line}")
        ranked.append(by_title[title])
    return ranked


def full_context_rank(
    service: ResearchService, records: list[dict[str, Any]], case: Case
) -> list[dict[str, Any]]:
    by_id = {record["id"]: record for record in records}
    packet = service.context(case.project, case.query, token_budget=650)
    return [by_id[record_id] for record_id in packet["record_ids"]]


class VectorIndex:
    def __init__(self, model_name: str, records: list[dict[str, Any]], cache_dir: Path, dimensions: int | None) -> None:
        from fastembed import TextEmbedding

        self.model = TextEmbedding(model_name=model_name, cache_dir=str(cache_dir))
        self.records = records
        started = time.perf_counter()
        self.vectors = np.asarray(
            list(self.model.embed([text(record) for record in records], batch_size=256))
        )
        if dimensions:
            self.vectors = self.vectors[:, :dimensions]
            self.vectors /= np.linalg.norm(self.vectors, axis=1, keepdims=True)
        self.index_seconds = time.perf_counter() - started
        self.index_bytes = int(self.vectors.nbytes)

    def rank(self, case: Case, global_scope: bool = False) -> list[dict[str, Any]]:
        query = np.asarray(next(iter(self.model.query_embed(case.query))))
        if query.shape[0] != self.vectors.shape[1]:
            query = query[: self.vectors.shape[1]]
            query /= np.linalg.norm(query)
        scores = self.vectors @ query
        order = np.argsort(-scores)
        return [self.records[index] for index in order if global_scope or self.records[index]["project"] == case.project]


def reciprocal_rank_fusion(rankings: list[list[dict[str, Any]]]) -> list[dict[str, Any]]:
    scores: dict[str, float] = defaultdict(float)
    records: dict[str, dict[str, Any]] = {}
    for ranking in rankings:
        for position, record in enumerate(ranking, 1):
            scores[record["id"]] += 1 / (60 + position)
            records[record["id"]] = record
    return sorted(records.values(), key=lambda record: scores[record["id"]], reverse=True)


def governed(ranked: list[dict[str, Any]], case: Case) -> list[dict[str, Any]]:
    boosts = EVENT_TYPES.get(case.event, {})
    eligible = [record for record in ranked if record["status"] not in {"stale", "superseded"}]
    base = {record["id"]: len(eligible) - index for index, record in enumerate(eligible)}
    return sorted(
        eligible,
        key=lambda record: (
            base[record["id"]] / max(1, len(eligible))
            + boosts.get(record["type"], 0)
            + (0.12 if record["status"] == "accepted" else 0)
            + (0.05 if record.get("metadata", {}).get("failed") else 0)
        ),
        reverse=True,
    )


def score_case(case: Case, ranked: list[dict[str, Any]], limit: int = 5) -> dict[str, float]:
    keys = [key(record) for record in ranked[:limit]]
    relevant = set(case.relevant)
    forbidden = set(case.forbidden)
    hits = [1 if item in relevant else 0 for item in keys]
    recall = sum(hits) / len(relevant)
    precision = sum(hits[:5]) / 5
    reciprocal = next((1 / (index + 1) for index, hit in enumerate(hits) if hit), 0)
    dcg = sum(hit / math.log2(index + 2) for index, hit in enumerate(hits))
    ideal = sum(1 / math.log2(index + 2) for index in range(min(len(relevant), limit)))
    token_estimate = sum(max(1, len(text(record)) // 4) for record in ranked[:limit])
    return {
        "recall_at_5": recall,
        "precision_at_5": precision,
        "mrr": reciprocal,
        "ndcg_at_5": dcg / ideal,
        "forbidden_at_5": float(any(item in forbidden for item in keys)),
        "cross_project_at_5": sum(record["project"] != case.project for record in ranked[:limit]) / limit,
        "relevant_per_1k_tokens": sum(hits) * 1000 / max(1, token_estimate),
    }


def evaluate(name: str, cases: list[Case], ranker: Callable[[Case], list[dict[str, Any]]], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    rows = []
    latencies = []
    for case in cases:
        ranked = ranker(case)  # warm cache and model
        measured = []
        for _ in range(3):
            started = time.perf_counter()
            ranked = ranker(case)
            measured.append((time.perf_counter() - started) * 1000)
        latencies.append(statistics.median(measured))
        rows.append({"case": case.name, "event": case.event, "top": [key(record) for record in ranked[:5]], **score_case(case, ranked)})
    metrics = {field: statistics.mean(row[field] for row in rows) for field in score_case(cases[0], [])}
    metrics["latency_p50_ms"] = statistics.median(latencies)
    metrics["latency_p95_ms"] = sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)]
    metrics["score"] = (
        0.32 * metrics["recall_at_5"]
        + 0.18 * metrics["precision_at_5"]
        + 0.18 * metrics["ndcg_at_5"]
        + 0.12 * metrics["mrr"]
        + 0.10 * min(1.0, metrics["relevant_per_1k_tokens"] / 20)
        - 0.08 * metrics["forbidden_at_5"]
        - 0.30 * metrics["cross_project_at_5"]
    )
    return {"method": name, "metrics": metrics, "metadata": metadata or {}, "cases": rows}


def report(results: list[dict[str, Any]], output: Path) -> None:
    results.sort(key=lambda result: result["metrics"]["score"], reverse=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    columns = ["rank", "method", "score", "recall@5", "precision@5", "nDCG@5", "MRR", "forbidden", "cross-project", "p50 ms", "p95 ms"]
    lines = [
        "# AgentRoots retrieval benchmark",
        "",
        (
            "Results use a frozen synthetic corpus. They compare retrieval behavior and do not "
            "validate the fixture claims as real project facts."
        ),
        (
            "Production compact context measures the actual five-reference hook packet. Full "
            "context measures the richer graph-expanded packet under the same 650-token cap, so "
            "its ranking score also reflects payload density and is not a pure retriever baseline."
        ),
        "",
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] * len(columns)) + "|",
    ]
    for position, result in enumerate(results, 1):
        metric = result["metrics"]
        values = [position, result["method"], metric["score"], metric["recall_at_5"], metric["precision_at_5"], metric["ndcg_at_5"], metric["mrr"], metric["forbidden_at_5"], metric["cross_project_at_5"], metric["latency_p50_ms"], metric["latency_p95_ms"]]
        lines.append("| " + " | ".join(str(value) if isinstance(value, (int, str)) else f"{value:.4f}" for value in values) + " |")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")


def validate_workflow(
    service: ResearchService, workdir: Path, benchmark_results: list[dict[str, Any]]
) -> dict[str, Any]:
    proposal = service.propose(
        project="agentroots",
        type="finding",
        title="Governed retrieval benchmark completed",
        body="The frozen 16-query corpus produced a ranked local comparison.",
        creator="worker-agent",
    )
    self_accept_blocked = False
    try:
        service.review(proposal["id"], actor="worker-agent", verdict="accepted")
    except GovernanceError:
        self_accept_blocked = True
    service.review(proposal["id"], actor="reviewer-agent", verdict="provisional")
    evidence_blocked = False
    try:
        service.review(proposal["id"], actor="reviewer-agent", verdict="accepted")
    except GovernanceError:
        evidence_blocked = True
    project_root = _project_root(workdir / "benchmark.sqlite3", "agentroots")
    result_path = project_root / "evidence" / "retrieval-ranking.json"
    result_path.write_text(
        json.dumps(
            {
                "schema": "agentroots.retrieval-result.v1",
                "query_count": len(CASES),
                "methods": [
                    {"method": row["method"], "metrics": row["metrics"]}
                    for row in benchmark_results
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        ["git", "-C", str(project_root), "add", "--", "evidence/retrieval-ranking.json"],
        check=True,
        capture_output=True,
    )
    service.link_evidence(
        EvidenceLink(
            proposal["id"],
            "evidence/retrieval-ranking.json",
            "git-file",
            "Frozen 16-query local benchmark result",
            hashlib.sha256(result_path.read_bytes()).hexdigest(),
        ),
        actor="reviewer-agent",
        project_root=project_root,
    )
    accepted = service.review(proposal["id"], actor="reviewer-agent", verdict="accepted")
    fresh_packet = service.context(
        "agentroots", "which governed retrieval benchmark was completed", token_budget=650
    )
    service.review(proposal["id"], actor="reviewer-agent", verdict="stale")
    stale_packet = service.context(
        "agentroots", "which governed retrieval benchmark was completed", token_budget=650
    )
    result = {
        "self_accept_blocked": self_accept_blocked,
        "accept_without_evidence_blocked": evidence_blocked,
        "accepted_after_non_creator_review": accepted["status"] == "accepted",
        "fresh_agent_received_new_finding": proposal["id"] in fresh_packet["record_ids"],
        "stale_finding_excluded": proposal["id"] not in stale_packet["record_ids"],
        "packet_within_budget": fresh_packet["estimated_tokens"] <= 650,
        "fresh_packet_tokens": fresh_packet["estimated_tokens"],
        "fresh_packet_records": len(fresh_packet["record_ids"]),
    }
    result["passed"] = sum(bool(value) for key, value in result.items() if key not in {"passed", "fresh_packet_tokens", "fresh_packet_records"})
    result["total"] = 6
    (workdir / "workflow.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--dimensions", type=int)
    parser.add_argument("--cache-dir", type=Path)
    args = parser.parse_args()
    args.workdir.mkdir(parents=True, exist_ok=True)
    service, records, _ = populate(args.workdir / "benchmark.sqlite3")
    if args.model:
        service.semantic = SemanticRetriever(
            args.model[0], args.cache_dir or args.workdir / "models"
        )
    results = [
        evaluate("lexical_fts_fuzzy", CASES, lambda case: lexical_rank(service, case)),
        evaluate("production_query", CASES, lambda case: service_rank(service, case)),
        evaluate("fuzzy_project", CASES, lambda case: fuzzy_rank(records, case)),
        evaluate(
            "production_compact_context",
            CASES,
            lambda case: compact_context_rank(service, records, case),
        ),
        evaluate(
            "full_context_packet_650",
            CASES,
            lambda case: full_context_rank(service, records, case),
        ),
        evaluate(
            "fts_graph",
            CASES,
            lambda case: graph_rerank(service, lexical_rank(service, case), case.project),
        ),
        evaluate(
            "governed_lexical_graph",
            CASES,
            lambda case: governed(
                graph_rerank(
                    service,
                    reciprocal_rank_fusion(
                        [lexical_rank(service, case), fuzzy_rank(records, case)]
                    ),
                    case.project,
                ),
                case,
            ),
        ),
        evaluate("unsafe_global_fuzzy", CASES, lambda case: fuzzy_rank(records, case, global_scope=True)),
    ]
    for model_name in args.model:
        index = VectorIndex(model_name, records, args.cache_dir or args.workdir / "models", args.dimensions)
        vector = lambda case, idx=index: idx.rank(case)
        model_meta = {"index_seconds": index.index_seconds, "index_bytes": index.index_bytes, "dimensions": int(index.vectors.shape[1])}
        results.append(evaluate(f"vector:{model_name}", CASES, vector, model_meta))
        results.append(evaluate(f"unsafe_global_vector:{model_name}", CASES, lambda case, idx=index: idx.rank(case, global_scope=True)))
        results.append(
            evaluate(
                f"hybrid:{model_name}",
                CASES,
                lambda case, idx=index: reciprocal_rank_fusion(
                    [lexical_rank(service, case), fuzzy_rank(records, case), idx.rank(case)]
                ),
            )
        )
        results.append(
            evaluate(
                f"governed_hybrid_graph:{model_name}",
                CASES,
                lambda case, idx=index: governed(
                    graph_rerank(
                        service,
                        reciprocal_rank_fusion(
                            [
                                lexical_rank(service, case),
                                fuzzy_rank(records, case),
                                idx.rank(case),
                            ]
                        ),
                        case.project,
                    ),
                    case,
                ),
            )
        )
    report(results, args.workdir / "report.md")
    validate_workflow(service, args.workdir, results)
    print(args.workdir / "report.md")


if __name__ == "__main__":
    main()
