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
python -m pip install -e .
agentroots propose demo hypothesis "Caching helps" "Latency should fall." --actor codex
agentroots-mcp
```

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

Lifecycle: candidate to provisional to accepted, plus disputed, rejected, superseded, and stale.
Creators cannot accept their own proposals by default. Acceptance requires resolvable evidence.
Mutations emit append-only events. Stored text is always treated as untrusted data.

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
