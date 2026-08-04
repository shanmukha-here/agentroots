# Research State MCP

Open-source cross-harness research provenance/state protocol. Codex, Claude, DeepSeek, and
generic MCP clients resume evolving research from compact evidence-backed state, not transcript replay.

Owns research semantics, governance, context assembly, validation, and read-only adapters.
Does not spawn agents, route models, execute commands, schedule work, manage worktrees/training,
store large artifacts, or act as generic vector memory.

## Quick start

```bash
python -m pip install -e .
research-state propose demo hypothesis "Caching helps" "Latency should fall." --actor claude
research-state-mcp
```

State defaults to OS/XDG user data directory. Override with `RESEARCH_STATE_DB` or `--db`.
SQLite runs WAL. Repo receives no generated state.

Tools: `research_get_context`, `research_get_frontier`, `research_query`,
`research_get_record`, `research_propose`, `research_review`,
`research_link_evidence`, `research_sync`, `research_validate`.

Lifecycle: candidate → provisional → accepted, plus disputed/rejected/superseded/stale.
Creator cannot accept own proposal. Mutations emit append-only events. Text gets secret
redaction and prompt-injection flags. Stored text is always untrusted data.

SQLite MVP, CLI/MCP, sectioned/audited context packets, exact JSONL event sync, backup/restore,
Git staleness, and read-only
MLflow/Trackio adapters implemented. Flowcept/AiiDA/PostgreSQL are roadmap interfaces only.
See [specification](docs/specification.md), [architecture](docs/architecture.md),
[integrations](docs/integrations.md), [roadmap](docs/roadmap.md),
[evaluation](docs/evaluation.md), and [demo](examples/three_agent_demo.py).

Apache-2.0.
