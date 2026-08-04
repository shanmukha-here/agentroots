# Roadmap and release gates

## v0.1 focused local MVP

SQLite external state; governed research graph; exact event export/import; sectioned 2k-token
packets; Git/MLflow evidence; Codex/Claude/generic MCP setup; three-agent demo; adversarial tests.

Release gate: two MCP clients share state, self-promotion fails, stale code facts disappear,
negative result returns, exact event round-trip passes, repo creates no runtime state.

## v0.2 evaluation and adapters

Trackio and signac hardening; lazy run comparison; 100k-reference benchmark; dataset/config/code
hash tracing; benchmark against no memory, `HANDOFF.md`, generic memory, agentic-experiments.

Metrics: task success, duplicate experiments, repeated code reading, context tokens,
recall per token, stale leakage, unsupported acceptance, evidence resolution, contradiction recall,
retrieval latency, review burden.

## v0.3 only after demonstrated demand

Flowcept/AiiDA adapters, PostgreSQL, project ACLs, Streamable HTTP, minimal review UI, optional
Lore/Remnic interoperability. No embeddings unless lexical plus graph retrieval fails measured eval.

Not planned: orchestration, model routing, shell execution, schedulers, training, worktrees,
artifact storage, transcript ingestion, or generic memory.

