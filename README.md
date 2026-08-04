# AgentRoots

**Different agents. Same roots.**

Built by Shanmukha Vellamcheti and OpenAI Codex.

AgentRoots is an open-source Agent Continuity MCP. It lets Codex, Claude, DeepSeek, and
generic MCP clients continue from compact, reviewed, evidence-backed state instead of replaying
transcripts or repeating prior work.

One agent can preserve useful state across sessions. Multiple agents can propose, review, and
reuse the same findings. AgentRoots does not spawn, schedule, route, or execute agents.

## Why AgentRoots

AI agents are temporary. Their work should not be. AgentRoots preserves goals, questions,
hypotheses, experiments, observations, findings, decisions, failures, and evidence across
sessions, models, and harnesses.

Branches explore. Roots remember.

## Quick start

```bash
python -m pip install "agentroots @ git+https://github.com/shanmukha-here/agentroots.git"
agentroots propose demo hypothesis "Caching helps" "Latency should fall." --actor codex
agentroots-mcp
```

AgentRoots is not published to PyPI yet. Contributors cloning the repository can instead use
`python -m pip install -e .`. Python 3.11 or newer is required.

State defaults to the OS or XDG user data directory. Override it with `AGENTROOTS_DB` or
`--db`. The legacy `RESEARCH_STATE_DB` variable remains accepted for local migration. SQLite
runs in WAL mode. Generated state stays outside the repository.

## MCP surface

Tools: `research_get_context`, `research_get_frontier`, `research_query`,
`research_get_record`, `research_propose`, `research_review`,
`research_link_evidence`, `research_sync`, and `research_validate`.

Resources: project brief, project frontier, record, and context packet under the
`research://` URI scheme. Protocol names remain research-specific because the initial ontology
models evidence-backed investigative work. AgentRoots branding covers its broader engineering,
research, and long-running agent uses.

Exact resource templates:

- `research://project/{project}/brief`
- `research://project/{project}/frontier`
- `research://record/{record_id}`
- `research://packet/{packet_id}`

Example MCP argument shapes:

```json
{"tool":"research_propose","arguments":{"project":"demo","record_type":"finding","title":"Cache result","body":"Reads improved 12 percent.","creator":"codex"}}
{"tool":"research_review","arguments":{"record_id":"UUID","actor":"reviewer","verdict":"accepted","resolves_record_ids":["GOAL_UUID"]}}
{"tool":"research_link_evidence","arguments":{"record_id":"UUID","uri":"mlflow://runs/123","kind":"mlflow-run","actor":"reviewer","content_hash":"sha256-if-known"}}
{"tool":"research_get_context","arguments":{"project":"demo","query":"cache","token_budget":1500}}
```

`research_sync` imports supplied events, exports current project events, and can mark packet
record IDs as used. CLI `export` and `import` provide file-based JSONL transfer.

CLI query text is positional. Run `agentroots <command> --help` for command-specific arguments:

```bash
agentroots context demo "cache latency" --tokens 1500
agentroots validate demo
agentroots export demo events.jsonl
```

On Windows, prefer these positional CLI commands or MCP tool calls over hand-escaped JSON in
PowerShell. For contributor tests in a clean checkout:

```bash
python -m pip install -e ".[dev]"
python -m pytest
```

Lifecycle: candidate to provisional to accepted, plus disputed, rejected, superseded, and stale.
Creators cannot accept their own proposals by default. Acceptance requires resolvable evidence.
Mutations emit append-only events. Stored text is always treated as untrusted data.
An accepted finding can explicitly resolve one or more goals. A `resolves` link removes those
goals from the active frontier while preserving their full history. `supports` does not close a
goal.

Implemented today:

- SQLite event ledger, revisions, projections, FTS5, and fuzzy lookup
- sectioned, token-budgeted, audited context packets
- review governance, contradictions, failed-attempt recall, and Git staleness
- exact JSONL event sync plus backup and restore
- read-only MLflow and Trackio adapters
- H-E-F and signac importers
- stdio MCP server, CLI, schemas, tests, fixtures, and three-agent demo

Flowcept, AiiDA, PostgreSQL, remote HTTP, ACLs, and UI remain roadmap work. See the
[specification](docs/specification.md), [architecture](docs/architecture.md),
[integrations](docs/integrations.md), [roadmap](docs/roadmap.md),
[evaluation](docs/evaluation.md), and [demo](examples/three_agent_demo.py).

## Contributors

See [CONTRIBUTORS.md](CONTRIBUTORS.md). Contributions are welcome under Apache-2.0.
