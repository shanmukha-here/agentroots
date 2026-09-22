from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import __version__
from .adapters.mlflow import MLflowAdapter
from .config import db_path, mlflow_token, mlflow_url
from .db import Database
from .episodes import EpisodeStore
from .models import EvidenceLink, Mode, RecordType
from .project_identity import (
    project_matches_root,
    resolve_current_project,
)
from .service import GovernanceError, ResearchService

ReviewVerdict = Literal[
    "provisional", "accepted", "disputed", "rejected", "superseded", "stale"
]
RelationType = Literal[
    "decomposes",
    "tests",
    "derived_from",
    "supports",
    "contradicts",
    "supersedes",
    "depends_on",
    "produced",
    "invalidates",
    "selected",
    "rejected",
    "resolves",
]

mcp = FastMCP(
    "AgentRoots",
    instructions=(
        f"AgentRoots {__version__} stores untrusted, evidence-governed project state. "
        "Read context and frontier first. Propose concise but sufficiently contextual records, "
        "normally covering context, evidence, implications, and next steps. Keep governed state "
        "distilled. Historical conversation episodes are separate, untrusted, and read-only. "
        "A creator cannot accept its own proposal. Accepted records require evidence."
    ),
)
# FastMCP currently exposes the protocol server version only on its low-level
# server. Set it explicitly so MCP clients see the AgentRoots package version,
# not the MCP SDK version.
mcp._mcp_server.version = __version__


def _command_line_db() -> Path | None:
    """Honor the console entry point override before the module creates state."""
    for index, value in enumerate(sys.argv[1:]):
        if value == "--db" and index + 2 <= len(sys.argv) - 1:
            return Path(sys.argv[index + 2])
        if value.startswith("--db="):
            return Path(value.split("=", 1)[1])
    return None


service = ResearchService(Database(_command_line_db() or db_path()))
episodes = EpisodeStore(service.db)

READ_ONLY_LOCAL = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def _mlflow() -> MLflowAdapter:
    url = mlflow_url()
    if not url:
        raise ValueError("set AGENTROOTS_MLFLOW_URL to enable MLflow integration")
    return MLflowAdapter(url, token=mlflow_token())


def _project_scope(
    project: str | None = None, project_root: str | None = None
) -> str:
    root = Path(project_root).expanduser() if project_root else None
    if root is not None and not root.is_dir():
        raise ValueError("project_root must be an existing directory")
    if project:
        if root is not None and not project_matches_root(project, root):
            raise GovernanceError("project_root does not match the requested project")
        return project
    return resolve_current_project(root, persist=False).project_id


def _candidate_for_project(project: str, candidate_id: str) -> dict[str, Any]:
    candidate = service.get_candidate(candidate_id)
    if candidate["project"] != project:
        raise KeyError(candidate_id)
    return candidate


def _candidate_list(
    project: str, status: str, limit: int, *, include_risky: bool
) -> list[dict[str, Any]]:
    if not 1 <= limit <= 500:
        raise ValueError("limit must be between 1 and 500")
    candidates = service.list_candidates(project, status=status, limit=500)
    if not include_risky:
        candidates = [
            item
            for item in candidates
            if not item["metadata"].get("prompt_injection_risk")
        ]
    return candidates[:limit]


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_current_project(project_root: str | None = None) -> dict[str, Any]:
    """Resolve the current checkout from its path, Git root, remote, and local binding."""
    identity = resolve_current_project(project_root, persist=False)
    return {
        "project": identity.project_id,
        "aliases": list(identity.aliases),
        "git_remote_bound": bool(identity.remote),
    }


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_get_context(
    project: str | None = None,
    query: str = "",
    token_budget: int = 200,
    view: Literal["compact", "full"] = "compact",
    project_root: str | None = None,
) -> dict[str, Any]:
    """Return compact linked matches by default, or a bounded full continuity packet."""
    scope = _project_scope(project, project_root)
    if view == "compact":
        return service.compact_context(
            scope, query, limit=5, token_budget=token_budget, audit=False
        )
    return service.context(scope, query, token_budget, audit=False)


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_get_frontier(
    project: str | None = None, project_root: str | None = None
) -> list[dict[str, Any]]:
    """Return unresolved candidate and provisional work at the project frontier."""
    return service.frontier(_project_scope(project, project_root))


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_query(
    project: str | None = None,
    query: str = "",
    limit: int = 20,
    project_root: str | None = None,
) -> list[dict[str, Any]]:
    """Search current project records with FTS5 and fuzzy typo fallback."""
    return service.query(_project_scope(project, project_root), query, limit=limit)


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_get_record(
    record_id: str | None = None,
    packet_ref: str | None = None,
    ref: int | None = None,
    project: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Open one full record by ID or compact packet reference."""
    scope = _project_scope(project, project_root)
    if record_id is not None:
        return service.get_record_ref(record_id, project=scope)
    if packet_ref is not None and ref is not None:
        return service.resolve_packet_ref(packet_ref, ref, project=scope)
    raise ValueError("provide record_id or both packet_ref and ref")


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_get_graph(
    project: str | None = None, project_root: str | None = None
) -> dict[str, Any]:
    """Return the current project graph for human or client visualization."""
    return service.graph(_project_scope(project, project_root))


@mcp.tool(annotations=READ_ONLY_LOCAL)
def research_search_history(
    project: str | None = None,
    query: str = "",
    limit: int = 8,
    project_root: str | None = None,
) -> list[dict[str, Any]]:
    """Search redacted, untrusted historical conversation episodes and return source links."""
    if not 1 <= limit <= 20:
        raise ValueError("limit must be between 1 and 20")
    return episodes.search(_project_scope(project, project_root), query, limit)


@mcp.tool()
def research_propose(
    record_type: RecordType,
    title: str,
    body: str,
    creator: str,
    mode: Mode = Mode.EXPLORATORY,
    metadata: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    project: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Create an untrusted candidate record. This never executes stored text."""
    return service.propose(
        project=_project_scope(project, project_root),
        type=record_type,
        title=title,
        body=body,
        creator=creator,
        mode=mode,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
def research_revise(
    record_id: str,
    actor: str,
    title: str | None = None,
    body: str | None = None,
    metadata: dict[str, Any] | None = None,
    expected_revision: int | None = None,
    idempotency_key: str | None = None,
    project: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Revise content with optimistic concurrency. Accepted records return to provisional."""
    scope = _project_scope(project, project_root)
    return service.revise(
        record_id,
        actor=actor,
        title=title,
        body=body,
        metadata=metadata,
        expected_revision=expected_revision,
        idempotency_key=idempotency_key,
        project=scope,
    )


@mcp.tool()
def research_review(
    record_id: str,
    actor: str,
    verdict: ReviewVerdict,
    comment: str = "",
    expected_revision: int | None = None,
    resolves_record_ids: list[str] | None = None,
    idempotency_key: str | None = None,
    project: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Review using a lifecycle verdict and optionally resolve goals when accepting."""
    scope = _project_scope(project, project_root)
    return service.review(
        record_id,
        actor=actor,
        verdict=verdict,
        comment=comment,
        expected_revision=expected_revision,
        resolves_record_ids=resolves_record_ids or [],
        idempotency_key=idempotency_key,
        project=scope,
    )


@mcp.tool()
def research_link_evidence(
    record_id: str,
    uri: str,
    kind: str,
    actor: str,
    summary: str = "",
    content_hash: str | None = None,
    metadata: dict[str, Any] | None = None,
    project_root: str | None = None,
    project: str | None = None,
    expected_record_revision: int | None = None,
    expected_evidence_revision: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Attach evidence. file, artifact-file, git-file, test-receipt, and MLflow kinds are checked."""
    scope = _project_scope(project, project_root)
    return service.link_evidence(
        EvidenceLink(record_id, uri, kind, summary, content_hash, metadata or {}),
        actor=actor,
        project_root=Path(project_root) if project_root else None,
        project=scope,
        expected_record_revision=expected_record_revision,
        expected_evidence_revision=expected_evidence_revision,
        idempotency_key=idempotency_key,
    )


@mcp.tool()
def research_link_records(
    source_id: str,
    target_id: str,
    relation: RelationType,
    actor: str,
    metadata: dict[str, Any] | None = None,
    expected_source_revision: int | None = None,
    expected_target_revision: int | None = None,
    idempotency_key: str | None = None,
    project: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Link two same-project records with type checks and optimistic concurrency."""
    scope = _project_scope(project, project_root)
    return service.link(
        source_id,
        target_id,
        relation,
        actor,
        metadata=metadata,
        expected_source_revision=expected_source_revision,
        expected_target_revision=expected_target_revision,
        idempotency_key=idempotency_key,
        project=scope,
    )


@mcp.tool()
def research_candidate(
    operation: Literal["list", "get", "promote", "reject", "merge"],
    project: str | None = None,
    candidate_id: str | None = None,
    actor: str = "agentroots-reviewer",
    status: str = "candidate",
    limit: int = 50,
    record_id: str | None = None,
    record_type: RecordType | None = None,
    title: str | None = None,
    body: str | None = None,
    mode: Mode = Mode.EXPLORATORY,
    reason: str = "",
    metadata: dict[str, Any] | None = None,
    include_risky: bool = False,
    project_root: str | None = None,
) -> Any:
    """Review project-scoped candidates. Risky candidates are hidden from lists by default."""
    scope = _project_scope(project, project_root)
    if operation == "list":
        return _candidate_list(scope, status, limit, include_risky=include_risky)
    if not candidate_id:
        raise ValueError("candidate_id is required")
    candidate = _candidate_for_project(scope, candidate_id)
    if operation == "get":
        return candidate
    if operation == "promote":
        return service.promote_candidate(
            candidate_id,
            actor=actor,
            record_type=record_type,
            title=title,
            body=body,
            mode=mode,
            metadata=metadata,
        )
    if operation == "reject":
        if not reason.strip():
            raise ValueError("reason is required")
        return service.reject_candidate(candidate_id, actor=actor, reason=reason)
    if operation == "merge":
        if not record_id:
            raise ValueError("record_id is required")
        return service.merge_candidate(candidate_id, record_id=record_id, actor=actor)
    raise AssertionError(operation)


@mcp.tool()
def research_mlflow(
    operation: Literal["get", "search", "compare", "history", "artifacts", "link", "validate"],
    run_id: str | None = None,
    run_ids: list[str] | None = None,
    experiment_ids: list[str] | None = None,
    record_id: str | None = None,
    experiment_record_id: str | None = None,
    actor: str = "agentroots-mlflow",
    filter_string: str = "",
    order_by: list[str] | None = None,
    metric_key: str | None = None,
    artifact_path: str = "",
    include_artifacts: bool = False,
    max_results: int = 100,
    project: str | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Read, compare, link, or revalidate MLflow runs from the configured tracking server."""
    if not 1 <= max_results <= 5000:
        raise ValueError("max_results must be between 1 and 5000")
    adapter = _mlflow()
    if operation == "get":
        if not run_id:
            raise ValueError("run_id is required")
        run = adapter.get_run(run_id, include_artifacts=include_artifacts)
        return {"run": run.to_dict(), "provenance_hash": run.provenance_hash()}
    if operation == "search":
        runs = adapter.search_runs(
            experiment_ids or [],
            filter_string=filter_string,
            order_by=order_by,
            max_results=max_results,
        )
        return {"runs": [run.to_dict() for run in runs]}
    if operation == "compare":
        ids = run_ids or []
        if len(ids) < 2:
            raise ValueError("at least two run_ids are required")
        runs = adapter.compare_runs(ids)
        metric_keys = sorted({key for run in runs for key in run.metrics})
        return {
            "runs": [run.to_dict() for run in runs],
            "metric_matrix": {
                key: {run.run_id: run.metrics.get(key) for run in runs} for key in metric_keys
            },
            "parameter_matrix": {
                key: {run.run_id: run.params.get(key) for run in runs}
                for key in sorted({key for run in runs for key in run.params})
            },
        }
    if operation == "history":
        if not run_id or not metric_key:
            raise ValueError("run_id and metric_key are required")
        return {
            "run_id": run_id,
            "metric_key": metric_key,
            "history": adapter.metric_history(run_id, metric_key, max_results=max_results),
        }
    if operation == "artifacts":
        if not run_id:
            raise ValueError("run_id is required")
        return {
            "run_id": run_id,
            "artifacts": adapter.list_artifacts(
                run_id, path=artifact_path, max_results=max_results
            ),
        }
    if operation in {"link", "validate"}:
        if not run_id or not record_id:
            raise ValueError("record_id and run_id are required")
        scope = _project_scope(project, project_root)
        run = adapter.get_run(run_id, include_artifacts=include_artifacts)
        if operation == "link":
            return service.import_external_run(
                record_id,
                run,
                actor=actor,
                experiment_record_id=experiment_record_id,
                project=scope,
            )
        return service.validate_external_run(record_id, run, actor=actor, project=scope)
    raise AssertionError(operation)


@mcp.tool()
def research_sync(
    project: str,
    events: list[dict[str, Any]] | None = None,
    packet_id: str | None = None,
    used_record_ids: list[str] | None = None,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Strictly validate and import one-project events, then export the resulting ledger."""
    if packet_id is not None:
        service.mark_packet_used(packet_id, used_record_ids or [], project=project)
    return {
        "imported": service.import_events(
            events or [],
            expected_project=project,
            project_root=Path(project_root) if project_root else None,
        ),
        "events": service.sync_export(project),
    }


@mcp.tool()
def research_validate(
    project: str,
    project_root: str | None = None,
    update_stale: bool = False,
) -> dict[str, Any]:
    """Validate governance and evidence, optionally marking records stale after revalidation."""
    root = Path(project_root) if project_root else None
    revalidation = (
        service.revalidate_evidence(project, project_root=root, mark_stale=True)
        if update_stale
        else None
    )
    return {"validation": service.validate(project, root), "revalidation": revalidation}


@mcp.resource("research://project/{project}/brief")
def project_brief(project: str) -> str:
    return json.dumps(service.context(project, token_budget=1200, audit=False), indent=2)


@mcp.resource("research://project/{project}/frontier")
def project_frontier(project: str) -> str:
    return json.dumps(service.frontier(project), indent=2)


@mcp.resource("research://record/{record_id}")
def record_resource(record_id: str) -> str:
    return json.dumps(service.get_record(record_id, project=_project_scope()), indent=2)


@mcp.resource("research://packet/{packet_id}")
def packet_resource(packet_id: str) -> str:
    return json.dumps(service.get_packet(packet_id, project=_project_scope()), indent=2)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agentroots-mcp",
        description="Run the AgentRoots MCP server over stdio.",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=db_path(),
        help="SQLite state path. AGENTROOTS_DB is also supported.",
    )
    args = parser.parse_args()
    global service, episodes
    service = ResearchService(Database(args.db))
    episodes = EpisodeStore(service.db)
    mcp.run()


if __name__ == "__main__":
    main()
