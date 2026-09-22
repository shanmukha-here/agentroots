from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from tempfile import mkdtemp
from typing import Any


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _entrypoint(python: Path, name: str) -> Path:
    candidates = [python.resolve().parent / name]
    if os.name == "nt":
        candidates.insert(0, python.resolve().parent / f"{name}.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"installed console entry point not found beside {python}: {name}")


def _free_port() -> int:
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        return int(server.getsockname()[1])


def _run_json(
    command: list[str | Path],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin: dict[str, Any] | None = None,
    expect_success: bool = True,
) -> dict[str, Any]:
    result = subprocess.run(
        [str(item) for item in command],
        cwd=cwd,
        env=env,
        input=json.dumps(stdin) if stdin is not None else None,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if expect_success and result.returncode != 0:
        raise RuntimeError(
            f"subprocess failed ({result.returncode}): {' '.join(str(x) for x in command)}\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    stream = result.stdout if result.returncode == 0 else result.stderr
    try:
        payload = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"subprocess did not return JSON: {stream}") from exc
    if not isinstance(payload, dict):
        raise TypeError("subprocess returned a non-object JSON value")
    payload["_returncode"] = result.returncode
    return payload


def _mcp_stdio(
    executable: Path,
    *,
    python: Path,
    cwd: Path,
    env: dict[str, str],
    project: str,
    database: Path,
    record_id: str,
) -> dict[str, Any]:
    client_code = """
import asyncio
import json
import os
import sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    parameters = StdioServerParameters(
        command=sys.argv[1], args=["--db", sys.argv[4]], env=dict(os.environ)
    )
    async with stdio_client(parameters) as (read, write):
        async with ClientSession(read, write) as session:
            initialized = await session.initialize()
            tools = await session.list_tools()
            current_project = await session.call_tool(
                "research_current_project",
                {"project_root": sys.argv[3]},
            )
            context = await session.call_tool(
                "research_get_context",
                {
                    "project": sys.argv[2],
                    "query": "batch 4096 failed prior run",
                    "token_budget": 200,
                    "view": "compact",
                },
            )
            full_context = await session.call_tool(
                "research_get_context",
                {
                    "project": sys.argv[2],
                    "query": "batch 4096 failed prior run",
                    "token_budget": 800,
                    "view": "full",
                },
            )
            record = await session.call_tool(
                "research_get_record",
                {"project": sys.argv[2], "record_id": sys.argv[5][:8]},
            )
            print(json.dumps({
                "initialize": initialized.model_dump(mode="json", by_alias=True),
                "tools": tools.model_dump(mode="json", by_alias=True),
                "current_project": current_project.model_dump(mode="json", by_alias=True),
                "context": context.model_dump(mode="json", by_alias=True),
                "full_context": full_context.model_dump(mode="json", by_alias=True),
                "record": record.model_dump(mode="json", by_alias=True),
            }))

asyncio.run(main())
"""
    result = subprocess.run(
        [
            str(python), "-c", client_code, str(executable), project, str(cwd),
            str(database), record_id,
        ],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"MCP server failed ({result.returncode})\n"
            f"stdout={result.stdout}\nstderr={result.stderr}"
        )
    response: dict[str, Any] | None = None
    for line in reversed(result.stdout.splitlines()):
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(message, dict) and {
            "initialize", "tools", "current_project", "context", "full_context", "record"
        } <= message.keys():
            response = message
            break
    if response is None:
        raise RuntimeError(
            f"MCP client did not return its result\nstdout={result.stdout}\nstderr={result.stderr}"
        )
    tools = response["tools"].get("tools", [])
    context_result = response["context"]
    full_context_result = response["full_context"]
    full_context = full_context_result.get("structuredContent", {})
    sections = full_context.get("sections", {})
    records = full_context.get("records", {})
    current_project = response["current_project"]
    record = response["record"]
    return {
        "transport": "real MCP ClientSession over a spawned stdio server",
        "protocol_version": response["initialize"].get("protocolVersion"),
        "server_version": response["initialize"].get("serverInfo", {}).get("version"),
        "tool_count": len(tools),
        "tool_names": [item.get("name") for item in tools if isinstance(item, dict)],
        "current_project_result": current_project,
        "current_project_matches": project in json.dumps(current_project),
        "context_result": context_result,
        "context_mentions_prior_run": "4096" in json.dumps(context_result),
        "context_is_stateless": '"packet_ref": null' in json.dumps(context_result),
        "full_context_result": full_context_result,
        "full_context_normalized": (
            record_id in records
            and record_id in sections.get("accepted_findings", [])
            and record_id in sections.get("failed_attempts", [])
            and all(
                isinstance(reference, str)
                for references in sections.values()
                for reference in references
            )
        ),
        "full_context_is_stateless": full_context.get("packet_id") is None,
        "short_record_ref_resolved": record_id in json.dumps(record),
        "server_stderr": result.stderr.strip(),
    }


def _runtime_identity(python: Path, *, cwd: Path, env: dict[str, str]) -> dict[str, Any]:
    code = (
        "import importlib.metadata,json,agentroots;"
        "print(json.dumps({'version':importlib.metadata.version('agentroots'),"
        "'module':agentroots.__file__}))"
    )
    return _run_json([python, "-c", code], cwd=cwd, env=env)


def run(output_root: Path, python: Path = Path(sys.executable)) -> dict[str, Any]:
    """Exercise installed entry points without changing any harness configuration."""

    python = python.resolve()
    output_root = output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_root = Path(mkdtemp(prefix="installed-run-", dir=output_root))
    project_root = run_root / "synthetic-project"
    project_root.mkdir()
    project = "installed-demo"
    database = run_root / "agentroots.sqlite3"
    config = run_root / "config.json"
    registry = run_root / "projects.json"
    runtime = run_root / "hook-runtime"
    spool = runtime / "spool"

    cli = _entrypoint(python, "agentroots")
    mcp = _entrypoint(python, "agentroots-mcp")
    env = os.environ.copy()
    env.update(
        {
            "AGENTROOTS_DB": str(database),
            "AGENTROOTS_CONFIG": str(config),
            "AGENTROOTS_PROJECT_REGISTRY": str(registry),
            "AGENTROOTS_PROJECT": project,
            "AGENTROOTS_SEMANTIC": "off",
            "AGENTROOTS_MODEL_CACHE": str(run_root / "model-cache"),
            "AGENTROOTS_DISABLE_DAEMON": "1",
            "AGENTROOTS_HOOK_RUNTIME": str(runtime),
            "AGENTROOTS_HOOK_SPOOL": str(spool),
            "AGENTROOTS_HOOK_PORT": str(_free_port()),
        }
    )

    subprocess.run(
        ["git", "init", "--quiet", str(project_root)],
        check=True,
        capture_output=True,
        text=True,
    )
    setup = _run_json(
        [cli, "--db", database, "setup", "--yes", "--no-history", "--no-clients", "--json"],
        cwd=project_root,
        env=env,
    )

    prior_run = project_root / "prior_run.json"
    prior_run.write_text(
        json.dumps(
            {
                "fixture": True,
                "batch_size": 4096,
                "status": "FAILED",
                "failure": "CUDA_OOM",
                "gpu_memory_gib": 24,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    verifier = project_root / "verify_prior_run.py"
    verifier.write_text(
        "import json\n"
        "from pathlib import Path\n"
        "run = json.loads(Path('prior_run.json').read_text(encoding='utf-8'))\n"
        "assert run['fixture'] is True\n"
        "assert run['batch_size'] == 4096\n"
        "assert run['status'] == 'FAILED'\n"
        "assert run['failure'] == 'CUDA_OOM'\n",
        encoding="utf-8",
    )
    verification_command = "python verify_prior_run.py"
    verification = subprocess.run(
        [str(python), str(verifier)],
        cwd=project_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    receipt = project_root / "verification-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": "agentroots.test-receipt.v1",
                "command": verification_command,
                "exit_code": verification.returncode,
                "stdout": verification.stdout,
                "stderr": verification.stderr,
                "inputs": [{"path": "prior_run.json", "sha256": _sha256(prior_run)}],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if verification.returncode != 0:
        raise RuntimeError("synthetic prior run verifier failed")

    proposed = _run_json(
        [
            cli,
            "--db",
            database,
            "propose",
            project,
            "finding",
            "Batch size 4096 failed in the synthetic prior run",
            (
                "The synthetic demo run used physical batch size 4096 on a 24 GiB GPU. "
                "Its structured fixture records FAILED with CUDA_OOM. "
                "A local verifier checked those exact fields and produced the attached receipt. "
                "A fresh agent should inspect this finding before proposing the same demo run."
            ),
            "--actor",
            "codex-subagent",
        ],
        cwd=project_root,
        env=env,
    )
    record_id = str(proposed["id"])
    _run_json(
        [
            cli,
            "--db",
            database,
            "review",
            record_id,
            "provisional",
            "--actor",
            "codex-subagent",
        ],
        cwd=project_root,
        env=env,
    )
    self_accept = _run_json(
        [
            cli,
            "--db",
            database,
            "review",
            record_id,
            "accepted",
            "--actor",
            "codex-subagent",
        ],
        cwd=project_root,
        env=env,
        expect_success=False,
    )
    receipt_link = _run_json(
        [
            cli,
            "--db",
            database,
            "evidence",
            record_id,
            receipt.resolve().as_uri(),
            "test-receipt",
            "--actor",
            "local-verifier",
            "--summary",
            "Structured verifier receipt for the synthetic prior run fixture",
            "--content-hash",
            _sha256(receipt),
            "--metadata",
            json.dumps(
                {
                    "command": verification_command,
                    "exit_code": verification.returncode,
                    "trace_uri": receipt.resolve().as_uri(),
                }
            ),
            "--project-root",
            project_root,
        ],
        cwd=project_root,
        env=env,
    )
    linked = _run_json(
        [
            cli,
            "--db",
            database,
            "evidence",
            record_id,
            prior_run.resolve().as_uri(),
            "file",
            "--actor",
            "local-verifier",
            "--summary",
            "Exact bytes of the synthetic prior run fixture",
            "--project-root",
            project_root,
        ],
        cwd=project_root,
        env=env,
    )
    file_evidence = next(
        item for item in linked["evidence"] if item["kind"] == "file"
    )
    accepted = _run_json(
        [
            cli,
            "--db",
            database,
            "review",
            record_id,
            "accepted",
            "--actor",
            "codex-main",
        ],
        cwd=project_root,
        env=env,
    )

    hook = _run_json(
        [cli, "--db", database, "hook", "--event", "UserPromptSubmit"],
        cwd=project_root,
        env=env,
        stdin={
            "project_id": project,
            "event_id": "installed-demo-prompt",
            "session_id": "fresh-codex-process",
            "cwd": str(project_root),
            "harness": "codex",
            "prompt": "Should I run physical batch size 4096 next?",
        },
    )
    hook_context = str(hook.get("hookSpecificOutput", {}).get("additionalContext", ""))
    database_hash_before_mcp = _sha256(database)
    registry_hash_before_mcp = _sha256(registry)
    decoy_database = run_root / "mcp-env-decoy.sqlite3"
    mcp_env = dict(env)
    mcp_env["AGENTROOTS_DB"] = str(decoy_database)
    mcp_result = _mcp_stdio(
        mcp,
        python=python,
        cwd=project_root,
        env=mcp_env,
        project=project,
        database=database,
        record_id=record_id,
    )
    mcp_result["database_unchanged"] = _sha256(database) == database_hash_before_mcp
    mcp_result["registry_unchanged"] = _sha256(registry) == registry_hash_before_mcp
    mcp_result["db_override_precedence"] = not decoy_database.exists()
    status = _run_json(
        [cli, "--db", database, "status", "--json"],
        cwd=project_root,
        env=env,
    )

    result: dict[str, Any] = {
        "run_at": datetime.now(UTC).isoformat(),
        "scope": {
            "input": "synthetic local project and prior-run fixture",
            "global_client_configuration_changed": False,
            "source_conversations_read": False,
        },
        "runtime": {
            "python": str(python.resolve()),
            "cli_entrypoint": str(cli),
            "mcp_entrypoint": str(mcp),
            "package": _runtime_identity(python, cwd=project_root, env=env),
        },
        "setup": {
            "ready": setup.get("ready"),
            "clients_configured": False,
            "history_approved": setup.get("history", {}).get("approved"),
            "project": setup.get("current_project", {}).get("id"),
        },
        "cli_workflow": {
            "record": record_id,
            "self_accept_blocked": self_accept.get("_returncode") != 0,
            "self_accept_error": self_accept.get("error"),
            "receipt_status": receipt_link["evidence"][-1]["metadata"]["verification"]
            ["status"],
            "file_evidence_status": file_evidence["metadata"]["verification"]["status"],
            "file_evidence_hash_recorded": file_evidence["content_hash"] == _sha256(prior_run),
            "accepted_status": accepted.get("status"),
        },
        "hook_subprocess": {
            "transport": "JSON payload piped to the installed agentroots hook command",
            "context": hook_context,
            "recalled_prior_run": (
                "4096" in hook_context and "synthetic prior run" in hook_context.lower()
            ),
            "notification": hook.get("agentrootsNotification"),
        },
        "mcp_stdio": mcp_result,
        "resources": {
            "database_bytes": status.get("storage", {}).get("database_bytes"),
            "managed_state_bytes": status.get("storage", {}).get("managed_state_bytes"),
            "python_environment_bytes": status.get("storage", {}).get(
                "python_environment_bytes"
            ),
            "total_bytes": status.get("storage", {}).get("total_bytes"),
            "command_memory_bytes": status.get("memory", {}).get("command_bytes"),
        },
        "artifacts": {
            "run_root": str(run_root.resolve()),
            "database": str(database.resolve()),
            "receipt": str(receipt.resolve()),
        },
    }
    summary = run_root / "installed-flow-summary.json"
    summary.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    result["artifacts"]["summary"] = str(summary.resolve())
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run AgentRoots through installed CLI, MCP stdio, and hook entry points"
    )
    parser.add_argument("--output", type=Path, default=Path("work") / "installed-flow-demo")
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python executable from the isolated environment containing the installed wheel",
    )
    args = parser.parse_args()
    print(json.dumps(run(args.output, args.python), indent=2))


if __name__ == "__main__":
    main()
