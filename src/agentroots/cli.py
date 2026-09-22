from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .backfill import (
    ConversationSource,
    discover_codex,
    discover_opencode,
    discovery_manifest,
    export_sources,
)
from .config import db_path
from .db import Database
from .episodes import EpisodeStore, write_opencode_export
from .graph import write_graph_html
from .hooks import (
    extract_episode_backfill,
    extraction_candidates,
    hook_status,
    preferred_extractor,
    process_hook,
    start_daemon,
)
from .models import EvidenceLink, Mode, RecordType, Status
from .onboarding import (
    cleanup_preview,
    configure,
    detect_harnesses,
    doctor,
    format_bytes,
    history_preview,
    resource_status,
    setup,
)
from .project_identity import (
    list_project_bindings,
    resolve_current_project,
    resolve_project_identity,
)
from .service import RELATIONS, ResearchService


def _progress(message: str) -> None:
    print(f"[agentroots] {message}", file=sys.stderr, flush=True)


def _candidate_for_project(
    service: ResearchService, project: str, candidate_id: str
) -> dict[str, Any]:
    candidate = service.get_candidate(candidate_id)
    if candidate["project"] != project:
        raise KeyError(candidate_id)
    return candidate


def _candidate_list(
    service: ResearchService,
    project: str,
    status: str,
    limit: int,
    *,
    include_risky: bool,
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


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentroots")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    p.add_argument("--db", type=Path, default=db_path())
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("setup", help="connect AgentRoots to detected local agent apps")
    q.add_argument("--yes", action="store_true", help="accept detected client configuration")
    history = q.add_mutually_exclusive_group()
    history.add_argument("--history", action="store_true", help="approve read-only history import")
    history.add_argument("--no-history", action="store_true", help="do not read existing conversations")
    q.add_argument(
        "--history-project",
        action="append",
        default=[],
        help="approved project ID to import; repeat to select several",
    )
    q.add_argument("--no-clients", action="store_true", help="do not change client configuration")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("project-bind", help="bind a stable project name to a repository root")
    q.add_argument("project")
    q.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    q = sub.add_parser("project-current", help="show the current repository identity")
    q.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    sub.add_parser("project-list", help="list known project IDs and aliases")
    q = sub.add_parser("status", help="show health, backends, memory, and storage")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("doctor", help="check the local installation")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("config", help="read or change one simple setting")
    q.add_argument("key")
    q.add_argument("value", nargs="?")
    q = sub.add_parser("cleanup", help="preview safely reclaimable generated data")
    q.add_argument("--json", action="store_true")
    q = sub.add_parser("propose", help="create an untrusted candidate record")
    q.add_argument("project")
    q.add_argument("type", choices=tuple(item.value for item in RecordType))
    q.add_argument("title")
    q.add_argument("body")
    q.add_argument("--actor", required=True)
    q.add_argument(
        "--mode",
        choices=tuple(item.value for item in Mode),
        default=Mode.EXPLORATORY.value,
    )
    q.add_argument(
        "--metadata",
        default="{}",
        help="JSON object; accepted decisions require alternatives and rationale",
    )
    q = sub.add_parser(
        "review",
        help="advance candidate -> provisional -> accepted, or record another lifecycle verdict",
    )
    q.add_argument("id", help="full record UUID")
    q.add_argument("verdict", choices=tuple(item.value for item in Status if item != Status.CANDIDATE))
    q.add_argument("--actor", required=True)
    q.add_argument("--comment", default="")
    q.add_argument("--revision", type=int)
    q.add_argument("--idempotency-key")
    q.add_argument(
        "--resolves",
        action="append",
        default=[],
        metavar="GOAL_ID",
        help="goal resolved by an accepted record; repeat for multiple goals",
    )
    q = sub.add_parser("revise", help="append a content or metadata revision")
    q.add_argument("id")
    q.add_argument("--actor", required=True)
    q.add_argument("--title")
    q.add_argument("--body")
    q.add_argument("--metadata", default="{}", help="JSON object merged into existing metadata")
    q.add_argument("--revision", type=int)
    q = sub.add_parser("query", help="search governed project records")
    q.add_argument("project")
    q.add_argument("text", nargs="?", default="")
    q = sub.add_parser("context", help="build and audit a full context packet")
    q.add_argument("project")
    q.add_argument("query", nargs="?", default="")
    q.add_argument("--tokens", type=int, default=2000)
    q = sub.add_parser("frontier", help="show unresolved project work")
    q.add_argument("project")
    q = sub.add_parser("get", help="open a record by full UUID or unique eight-character prefix")
    q.add_argument("id", help="full UUID or unique eight-character lowercase prefix")
    q = sub.add_parser("validate", help="check governance and evidence integrity")
    q.add_argument("project")
    q.add_argument(
        "--project-root",
        type=Path,
        help="repository root trusted for Git and local-file verification",
    )
    q.add_argument("--update-stale", action="store_true")
    q = sub.add_parser("link", help="link two governed records")
    q.add_argument("source_id")
    q.add_argument("target_id")
    q.add_argument("relation", choices=tuple(sorted(RELATIONS)))
    q.add_argument("--actor", required=True)
    q.add_argument("--source-revision", type=int)
    q.add_argument("--target-revision", type=int)
    q.add_argument("--idempotency-key")
    q = sub.add_parser("evidence", help="attach and classify an evidence reference")
    q.add_argument("record_id")
    q.add_argument("uri")
    q.add_argument(
        "kind",
        help=(
            "evidence kind; file, artifact-file, git-file, test-receipt, and mlflow-run support "
            "mechanical checks; file kinds accept a local path or file URI; unknown kinds "
            "remain reference-only"
        ),
    )
    q.add_argument("--actor", required=True)
    q.add_argument("--summary", default="")
    q.add_argument("--content-hash")
    q.add_argument("--metadata", default="{}", help="JSON object")
    q.add_argument(
        "--project-root",
        type=Path,
        help="repository root trusted for Git and local-file verification",
    )
    q = sub.add_parser("candidate", help="review extracted conversation candidates")
    q.add_argument("operation", choices=("list", "get", "promote", "reject", "merge"))
    q.add_argument("candidate_id", nargs="?")
    q.add_argument("--project", required=True)
    q.add_argument("--actor", default="agentroots-reviewer")
    q.add_argument("--status", default="candidate")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--record-id")
    q.add_argument("--record-type")
    q.add_argument("--title")
    q.add_argument("--body")
    q.add_argument("--mode", default="exploratory")
    q.add_argument("--reason", default="")
    q.add_argument("--metadata", default="{}", help="JSON object")
    q.add_argument(
        "--include-risky",
        action="store_true",
        help="include prompt-injection-risk candidates in list output",
    )
    q = sub.add_parser("export", help="write a project event stream as JSONL")
    q.add_argument("project")
    q.add_argument("path", type=Path)
    q = sub.add_parser("import", help="import and validate a JSONL event stream")
    q.add_argument("path", type=Path)
    q.add_argument("--project-root", type=Path)
    q = sub.add_parser("backup", help="copy complete local state to a SQLite backup")
    q.add_argument("path", type=Path)
    q = sub.add_parser("restore", help="replace local state from a SQLite backup")
    q.add_argument("path", type=Path)
    q = sub.add_parser("graph", help="write an interactive project knowledge map")
    q.add_argument("project")
    q.add_argument("path", type=Path)
    q = sub.add_parser("opencode-export", help="export one OpenCode session tree read-only")
    q.add_argument("source_db", type=Path)
    q.add_argument("root_session_id")
    q.add_argument("path", type=Path)
    q.add_argument("--source-host", default="local")
    q.add_argument("--include-reasoning", action="store_true")
    q = sub.add_parser("episodes-import", help="import an OpenCode JSONL export")
    q.add_argument("project")
    q.add_argument("path", type=Path)
    q.add_argument("--include-reasoning", action="store_true")
    q = sub.add_parser("episodes-search", help="search untrusted conversation episodes")
    q.add_argument("project")
    q.add_argument("query")
    q.add_argument("--limit", type=int, default=8)
    q = sub.add_parser("backfill-discover", help="discover conversation sources using metadata only")
    q.add_argument("path", type=Path, help="write the unapproved discovery manifest")
    q.add_argument("--codex-root", type=Path, action="append", default=[])
    q.add_argument("--opencode-db", type=Path, action="append", default=[])
    q.add_argument("--include-dir", action="append", default=[])
    q.add_argument("--exclude-dir", action="append", default=[])
    q.add_argument("--source-host", default="local")
    q = sub.add_parser("backfill-export", help="export an approved project read-only")
    q.add_argument("manifest", type=Path)
    q.add_argument("project")
    q.add_argument("path", type=Path)
    q.add_argument("--approve", action="store_true", help="confirm permission to read conversations")
    q.add_argument("--include-reasoning", action="store_true")
    q = sub.add_parser("backfill-import", help="import a normalized conversation bundle")
    q.add_argument("project")
    q.add_argument("path", type=Path)
    q.add_argument("--project-map", type=Path, help="JSON map of project IDs to path aliases")
    q.add_argument("--include-reasoning", action="store_true")
    q = sub.add_parser("backfill-extract", help="extract candidates from backfilled history")
    q.add_argument("project")
    q.add_argument("--limit", type=int, default=500)
    q.add_argument("--extractor", choices=("auto", "qwen", "gliner", "off"), default="auto")
    q = sub.add_parser("hook", help="process one harness hook payload from stdin")
    q.add_argument("--event")
    sub.add_parser("hook-daemon-start", help="start the local hook daemon if needed")
    sub.add_parser("hook-status", help="show daemon and hook audit status")
    q = sub.add_parser("hook-candidates", help="list unreviewed extracted candidates")
    q.add_argument("project")
    q.add_argument("--limit", type=int, default=50)
    q.add_argument("--include-risky", action="store_true")
    return p


def execute(args: argparse.Namespace, service: ResearchService) -> Any:
    c = args.command
    if c == "project-bind":
        if not args.path.is_dir():
            raise ValueError(f"project root is not a directory: {args.path}")
        identity = resolve_project_identity(args.path, configured=args.project)
        return {
            "project_id": identity.project_id,
            "aliases": list(identity.aliases),
            "bound": True,
        }
    if c == "project-current":
        identity = resolve_current_project(args.path, persist=False)
        return {
            "project_id": identity.project_id,
            "aliases": list(identity.aliases),
            "git_remote_bound": bool(identity.remote),
        }
    if c == "project-list":
        bindings = {item["project_id"]: item for item in list_project_bindings()}
        with service.db.connect() as connection:
            stored = [
                str(row[0])
                for row in connection.execute(
                    "SELECT project FROM records UNION SELECT project FROM episodes ORDER BY project"
                )
            ]
        for project_id in stored:
            bindings.setdefault(project_id, {"project_id": project_id, "aliases": [project_id]})
        return list(bindings.values())
    if c == "setup":
        configure_clients = not args.no_clients
        history = bool(args.history)
        history_projects = list(args.history_project)
        if not args.yes and sys.stdin.isatty():
            detected = ", ".join(item["name"].title() for item in detect_harnesses()) or "none"
            current = resource_status(service.db)
            print("AgentRoots\nDifferent agents, same roots.\n")
            print(f"Detected agent apps: {detected}")
            print(f"Current local storage: {format_bytes(current['storage']['total_bytes'])}")
            print("Source conversations are always read-only.\n")
            answer = input("Enable AgentRoots for detected agent apps? [Y/n] ").strip().lower()
            configure_clients = answer not in {"n", "no"}
            preview = history_preview()
            if preview["projects"]:
                print("\nHistory available, metadata only:")
                for item in preview["projects"]:
                    harnesses = ",".join(item["harnesses"])
                    print(f"  {item['project']}  sessions={item['sessions']}  {harnesses}")
            answer = input(
                "Read approved existing conversations to recover project history? [y/N] "
            ).strip().lower()
            history = answer in {"y", "yes"}
            if history and preview["projects"]:
                selection = input(
                    "Projects to import [all or comma-separated project IDs]: "
                ).strip()
                if selection and selection.lower() != "all":
                    history_projects = [item.strip() for item in selection.split(",") if item.strip()]
                    known = {item["project"] for item in preview["projects"]}
                    unknown = set(history_projects) - known
                    if unknown:
                        raise ValueError("unknown history project: " + ", ".join(sorted(unknown)))
        return setup(
            history=history,
            configure_clients=configure_clients,
            history_projects=history_projects,
            database=service.db,
        )
    if c == "status":
        return resource_status(service.db)
    if c == "doctor":
        return doctor(service.db)
    if c == "config":
        return configure(args.key, args.value)
    if c == "cleanup":
        return cleanup_preview()
    if c == "propose":
        metadata = json.loads(args.metadata)
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object")
        return service.propose(
            project=args.project,
            type=args.type,
            title=args.title,
            body=args.body,
            creator=args.actor,
            mode=args.mode,
            metadata=metadata,
        )
    if c == "review":
        return service.review(
            args.id,
            actor=args.actor,
            verdict=args.verdict,
            comment=args.comment,
            expected_revision=args.revision,
            resolves_record_ids=args.resolves,
            idempotency_key=args.idempotency_key,
        )
    if c == "revise":
        metadata = json.loads(args.metadata)
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object")
        return service.revise(
            args.id,
            actor=args.actor,
            title=args.title,
            body=args.body,
            metadata=metadata,
            expected_revision=args.revision,
        )
    if c == "query":
        return service.query(args.project, args.text)
    if c == "context":
        return service.context(args.project, args.query, args.tokens)
    if c == "frontier":
        return service.frontier(args.project)
    if c == "get":
        return service.get_record_ref(args.id)
    if c == "validate":
        revalidation = (
            service.revalidate_evidence(
                args.project, project_root=args.project_root, mark_stale=True
            )
            if args.update_stale
            else None
        )
        return {
            "validation": service.validate(args.project, args.project_root),
            "revalidation": revalidation,
        }
    if c == "link":
        return service.link(
            args.source_id,
            args.target_id,
            args.relation,
            args.actor,
            expected_source_revision=args.source_revision,
            expected_target_revision=args.target_revision,
            idempotency_key=args.idempotency_key,
        )
    if c == "evidence":
        metadata = json.loads(args.metadata)
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object")
        uri = args.uri
        if args.kind.casefold() in {"file", "artifact", "artifact-file"}:
            parsed_path = Path(uri).expanduser()
            if "://" not in uri and not uri.casefold().startswith("file:"):
                if not parsed_path.is_absolute() and args.project_root is not None:
                    parsed_path = args.project_root / parsed_path
                uri = parsed_path.resolve().as_uri()
        return service.link_evidence(
            EvidenceLink(
                args.record_id,
                uri,
                args.kind,
                args.summary,
                args.content_hash,
                metadata,
            ),
            actor=args.actor,
            project_root=args.project_root,
        )
    if c == "candidate":
        metadata = json.loads(args.metadata)
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a JSON object")
        if args.operation == "list":
            return _candidate_list(
                service,
                args.project,
                args.status,
                args.limit,
                include_risky=args.include_risky,
            )
        if not args.candidate_id:
            raise ValueError("candidate_id is required")
        candidate = _candidate_for_project(service, args.project, args.candidate_id)
        if args.operation == "get":
            return candidate
        if args.operation == "promote":
            return service.promote_candidate(
                args.candidate_id,
                actor=args.actor,
                record_type=args.record_type,
                title=args.title,
                body=args.body,
                mode=args.mode,
                metadata=metadata,
            )
        if args.operation == "reject":
            if not args.reason.strip():
                raise ValueError("--reason is required")
            return service.reject_candidate(
                args.candidate_id, actor=args.actor, reason=args.reason
            )
        if args.operation == "merge":
            if not args.record_id:
                raise ValueError("--record-id is required")
            return service.merge_candidate(
                args.candidate_id, record_id=args.record_id, actor=args.actor
            )
        raise AssertionError(args.operation)
    if c == "export":
        events = service.sync_export(args.project)
        args.path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        return {"events": len(events), "path": str(args.path)}
    if c == "import":
        return {
            "imported": service.import_events(
                (
                    json.loads(x)
                    for x in args.path.read_text(encoding="utf-8").splitlines()
                    if x
                ),
                project_root=args.project_root,
            )
        }
    if c == "backup":
        return {"path": str(service.backup(args.path))}
    if c == "restore":
        service.restore(args.path)
        return {"restored": str(args.path)}
    if c == "graph":
        path = write_graph_html(service.graph(args.project), args.path)
        return {"project": args.project, "path": str(path)}
    if c == "opencode-export":
        return write_opencode_export(
            args.source_db,
            args.root_session_id,
            args.path,
            args.source_host,
            include_reasoning=args.include_reasoning,
        )
    if c == "episodes-import":
        return EpisodeStore(service.db).import_history_jsonl(
            args.path, args.project, include_reasoning=args.include_reasoning
        )
    if c == "episodes-search":
        return EpisodeStore(service.db).search(args.project, args.query, args.limit)
    if c == "backfill-discover":
        _progress("discovering metadata only; conversation bodies are not being read")
        sources = []
        for root in args.codex_root:
            _progress(f"scanning Codex session metadata: {root}")
            sources.extend(discover_codex(
                root, source_host=args.source_host,
                includes=args.include_dir, excludes=args.exclude_dir,
            ))
        for source_db in args.opencode_db:
            _progress(f"opening OpenCode metadata read-only: {source_db}")
            sources.extend(discover_opencode(
                source_db, source_host=args.source_host,
                includes=args.include_dir, excludes=args.exclude_dir,
            ))
        manifest = discovery_manifest(sources)
        args.path.parent.mkdir(parents=True, exist_ok=True)
        args.path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        _progress(
            f"discovered projects={len(manifest['projects'])} sessions={len(sources)} "
            f"manifest={args.path} approved=false"
        )
        return {"projects": len(manifest["projects"]), "sessions": len(sources), "path": str(args.path)}
    if c == "backfill-export":
        _progress(
            f"exporting project={args.project} approved={str(args.approve).lower()} read_only=true"
        )
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        sources = [
            ConversationSource(**item) for item in manifest.get("sources", [])
            if item.get("project_id") == args.project
        ]
        if not sources:
            raise ValueError(f"project not found in manifest: {args.project}")
        counts = export_sources(
            sources,
            args.path,
            approved=args.approve,
            include_reasoning=args.include_reasoning,
        )
        _progress(
            f"export complete sessions={counts.get('sessions', 0)} "
            f"messages={counts.get('messages', 0)} parts={counts.get('parts', 0)}"
        )
        return {"project": args.project, "path": str(args.path), **counts}
    if c == "backfill-import":
        _progress(f"importing untrusted history project={args.project} source={args.path}")
        aliases = (
            json.loads(args.project_map.read_text(encoding="utf-8")) if args.project_map else None
        )
        result = EpisodeStore(service.db).import_history_jsonl(
            args.path,
            args.project,
            aliases,
            include_reasoning=args.include_reasoning,
        )
        _progress(
            f"import complete imported={result['imported']} deduplicated={result['unchanged']} "
            f"sessions={result['sessions']}"
        )
        return result
    if c == "backfill-extract":
        extractor = preferred_extractor(args.extractor)
        _progress(
            f"extraction policy requested={args.extractor} selected={extractor.name} "
            "candidates_only=true"
        )
        result = extract_episode_backfill(
            service.db, args.project, args.limit, extractor=extractor, progress=_progress
        )
        if args.extractor == "qwen" and result["error"]:
            raise ValueError(f"Qwen extraction failed without fallback: {result['error']}")
        _progress(
            f"extraction complete processed={result['processed']} "
            f"candidates={result['candidates']} remaining={result['remaining']}"
        )
        return result
    if c == "hook":
        try:
            payload = json.loads(sys.stdin.buffer.read() or b"{}")
        except json.JSONDecodeError:
            payload = {}
        return process_hook(payload, args.event)
    if c == "hook-daemon-start":
        start_daemon()
        return {"started": True}
    if c == "hook-status":
        return hook_status(service.db)
    if c == "hook-candidates":
        return extraction_candidates(
            service.db,
            args.project,
            args.limit,
            include_risky=args.include_risky,
        )
    raise AssertionError(c)


def main() -> None:
    args = parser().parse_args()
    selected_database = args.db.expanduser().resolve()
    os.environ["AGENTROOTS_DB"] = str(selected_database)
    service = ResearchService(Database(selected_database))
    try:
        result = execute(args, service)
        if args.command in {"setup", "status", "doctor", "cleanup"} and not getattr(args, "json", False):
            print(_human_result(args.command, result))
        else:
            print(json.dumps(result, indent=2, default=str))
        unsuccessful = (
            args.command in {"setup", "status"} and not result.get("ready", False)
        ) or (args.command == "doctor" and not result.get("healthy", False))
        if unsuccessful:
            raise SystemExit(1)
    except (KeyError, PermissionError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from exc


def _human_result(command: str, result: dict[str, Any]) -> str:
    if command == "setup":
        detected = ", ".join(item["name"].title() for item in result["detected"]) or "none"
        configured = ", ".join(
            item["name"].title() for item in result["configured"] if item.get("configured")
        ) or "none"
        history = "approved" if result["history"]["approved"] else "disabled"
        lines = [
            f"AgentRoots {'ready' if result['ready'] else 'setup incomplete'}",
            "Different agents, same roots.",
            "",
            f"Detected: {detected}",
            f"Connected: {configured}",
            f"Existing conversation history: {history}",
            f"History indexing: {result['history']['state']}",
            "Codex hook trust: open /hooks if Codex reports review needed",
        ]
        if not result["ready"]:
            for item in result["configured"]:
                if not item.get("configured") and item.get("required") is not False:
                    lines.append(f"Needs attention: {item['name']}: {item.get('reason', 'failed')}")
            if not result["daemon"]["ready"]:
                lines.append(f"Needs attention: background service: {result['daemon']['reason']}")
        else:
            lines.extend([
                "",
                "Keep using your agents normally. Run 'agentroots status' for details.",
            ])
        return "\n".join(lines)
    if command == "status":
        hooks = result["hooks"]
        memory = result["memory"]
        storage = result["storage"]
        counts = result["counts"]
        backfill = result["backfill"]
        lines = [
            f"AgentRoots {'ready' if result['ready'] else 'needs attention'}",
            f"Recall: {hooks['semantic_backend']}  Extract: {hooks['extractor']}",
            "",
            "Memory",
            f"  Background service  {format_bytes(memory['daemon_bytes'])}",
            f"  Status command      {format_bytes(memory['command_bytes'])}",
            "",
            "Storage",
            f"  Database            {format_bytes(storage['database_bytes'])}",
            f"  Search indexes       {format_bytes(storage['index_bytes'])}",
            f"  Models              {format_bytes(storage['model_bytes'])}",
            f"  Runtime             {format_bytes(storage['runtime_bytes'])}",
            f"  Managed state       {format_bytes(storage['managed_state_bytes'])}",
            (
                "  Python environment  "
                + (
                    format_bytes(storage["python_environment_bytes"])
                    if storage["python_environment"]["isolated"]
                    else "shared, not counted"
                )
            ),
            f"  Total               {format_bytes(storage['total_bytes'])}",
            "",
            (
                f"Projects {counts['projects']}  Episodes {counts['episodes']}  "
                f"Records {counts['records']}  Candidates {counts['candidates']}"
            ),
            f"History indexing: {backfill.get('state', 'not-started')}",
        ]
        for reason in result.get("readiness", {}).get("reasons", []):
            lines.append(f"Needs attention: {reason}")
        return "\n".join(lines)
    if command == "doctor":
        lines = [f"AgentRoots doctor: {'healthy' if result['healthy'] else 'attention needed'}"]
        lines.extend(
            f"  {'OK' if check['ok'] else '!!'} {check['name']}" for check in result["checks"]
        )
        return "\n".join(lines)
    if command == "cleanup":
        lines = [f"Safe cleanup preview: {format_bytes(result['reclaimable_bytes'])} reclaimable"]
        lines.extend(f"  {format_bytes(item['bytes'])}  {item['path']}" for item in result["items"])
        lines.append("Nothing was deleted. Source conversations and accepted knowledge are protected.")
        return "\n".join(lines)
    return json.dumps(result, indent=2, default=str)


if __name__ == "__main__":
    main()
