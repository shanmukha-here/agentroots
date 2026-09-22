# Roadmap and release gates

## v0.2.0 shared project continuity alpha

SQLite external state; governed research graph; safe event export/import; sectioned 2k-token
packets; Git and MLflow evidence references; validated Codex setup; experimental OpenCode adapter;
generic stdio MCP setup; three-agent workflow demo; adversarial tests.

Implemented release scope and validation gates:

- one canonical project identity resolves across live hooks, Codex history, and OpenCode history
- untrusted sync cannot materialize accepted records or cross project boundaries
- extracted candidates can be inspected, corrected, promoted, rejected, or merged through MCP
- agents can create validated same-project graph relationships
- self-promotion fails, accepted status requires an evidence reference, and epistemic records require
  a mechanically verified item
- evidence references and core-verifier or adapter-verified evidence are reported separately
- Git and MLflow revalidation can stale affected accepted records through normal user surfaces
- the frontier derives missing next steps across questions, experiments, runs, observations, claims,
  reviews, disputes, and stale work
- approved history stays in a separate untrusted episode archive and source conversations stay
  read-only
- a real OpenCode-history to fresh-Codex scenario surfaces a known failed action and the fresh Codex
  agent chooses to skip it
- exact event round-trip passes when destination verifiers can resolve the same evidence; full-state
  backup and restore, isolated wheel install, MCP smoke, and repo-clean checks pass

## Next: evaluation and adapter hardening

Trackio and signac hardening; lazy run comparison; 100k-reference benchmark; dataset, configuration,
and code hash tracing; benchmark against no memory, `HANDOFF.md`, generic memory, and
agentic-experiments. Publish the actual full-flow demo only after this workflow is reproducible.

Metrics: task success, duplicate experiments, repeated code reading, context tokens,
recall per token, stale leakage, unsupported acceptance, evidence resolution, contradiction recall,
retrieval latency, review burden.

## Later: only after demonstrated demand

Flowcept/AiiDA adapters, PostgreSQL, project ACLs, Streamable HTTP, minimal review UI, optional
Lore/Remnic/CodeMem interoperability, and governed graph editing. Keep default local BGE fail-open
with a usable lexical-only mode. Do not require a generative extractor for basic continuity.

Not planned: orchestration, model routing, shell execution, schedulers, training, worktrees,
artifact storage, wholesale transcript injection into prompts or governed records, or generic
memory. Explicitly approved conversation parts may remain in the separate untrusted episode archive.
