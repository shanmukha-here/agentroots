from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .config import db_path
from .db import Database
from .service import ResearchService


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="agentroots")
    p.add_argument("--db", type=Path, default=db_path())
    sub = p.add_subparsers(dest="command", required=True)
    q = sub.add_parser("propose")
    q.add_argument("project")
    q.add_argument("type")
    q.add_argument("title")
    q.add_argument("body")
    q.add_argument("--actor", required=True)
    q.add_argument("--mode", default="exploratory")
    q = sub.add_parser("review")
    q.add_argument("id")
    q.add_argument("verdict")
    q.add_argument("--actor", required=True)
    q.add_argument("--comment", default="")
    q.add_argument("--revision", type=int)
    q.add_argument(
        "--resolves",
        action="append",
        default=[],
        metavar="GOAL_ID",
        help="goal resolved by an accepted record; repeat for multiple goals",
    )
    q = sub.add_parser("query")
    q.add_argument("project")
    q.add_argument("text", nargs="?", default="")
    q = sub.add_parser("context")
    q.add_argument("project")
    q.add_argument("query", nargs="?", default="")
    q.add_argument("--tokens", type=int, default=2000)
    q = sub.add_parser("frontier")
    q.add_argument("project")
    q = sub.add_parser("get")
    q.add_argument("id")
    q = sub.add_parser("validate")
    q.add_argument("project")
    q = sub.add_parser("export")
    q.add_argument("project")
    q.add_argument("path", type=Path)
    q = sub.add_parser("import")
    q.add_argument("path", type=Path)
    q = sub.add_parser("backup")
    q.add_argument("path", type=Path)
    q = sub.add_parser("restore")
    q.add_argument("path", type=Path)
    return p


def execute(args: argparse.Namespace, service: ResearchService) -> Any:
    c = args.command
    if c == "propose":
        return service.propose(
            project=args.project,
            type=args.type,
            title=args.title,
            body=args.body,
            creator=args.actor,
            mode=args.mode,
        )
    if c == "review":
        return service.review(
            args.id,
            actor=args.actor,
            verdict=args.verdict,
            comment=args.comment,
            expected_revision=args.revision,
            resolves_record_ids=args.resolves,
        )
    if c == "query":
        return service.query(args.project, args.text)
    if c == "context":
        return service.context(args.project, args.query, args.tokens)
    if c == "frontier":
        return service.frontier(args.project)
    if c == "get":
        return service.get_record(args.id)
    if c == "validate":
        return service.validate(args.project)
    if c == "export":
        events = service.sync_export(args.project)
        args.path.write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        return {"events": len(events), "path": str(args.path)}
    if c == "import":
        return {
            "imported": service.import_events(
                json.loads(x) for x in args.path.read_text(encoding="utf-8").splitlines() if x
            )
        }
    if c == "backup":
        return {"path": str(service.backup(args.path))}
    if c == "restore":
        service.restore(args.path)
        return {"restored": str(args.path)}
    raise AssertionError(c)


def main() -> None:
    args = parser().parse_args()
    service = ResearchService(Database(args.db))
    try:
        print(json.dumps(execute(args, service), indent=2, default=str))
    except (KeyError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from exc
