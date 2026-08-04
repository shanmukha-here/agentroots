# Protocol specification v0.1

Mutable graph nodes: Goal, Question, Hypothesis, Experiment, RunRef, Observation, Claim,
Finding, Decision, ArtifactRef, Evidence, Agent, Session. Each projection has UUID, project, creator, mode,
status, revision, timestamps, text, and metadata. Edges use open relation names.

Events append only and support idempotency keys. Revisions protect projection writes.
JSON Schemas in `schemas/` define exchange payloads. JSONL sync orders events by ledger seq;
imports conservatively materialize unseen proposal events.

Candidate can become provisional or rejected. Provisional can become accepted, disputed, or
rejected. Accepted can become disputed, superseded, or stale. Creator cannot accept own proposal.
An accepted record may explicitly `resolves` one or more same-project goals. Resolved goals stay
in history but leave the frontier and current-goal packet sections. `supports` never implies
completion.

Packets rank accepted then provisional then remaining records, apply FTS5 with typo fallback,
expand graph neighbors, exclude stale/superseded facts, expose required semantic sections, and fit
approximate token budget (`JSON bytes / 4`). Packet hash and supplied/used IDs provide audit.

Transport is MCP stdio. Semantic exchange stays JSON for non-MCP CLI/JSONL clients.
Never ingest full transcripts.
