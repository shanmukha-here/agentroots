# Architecture

`server/CLI -> ResearchService -> SQLite event ledger + projections`

- Domain validates enums and lifecycle.
- Service owns transactions, governance, redaction, context, sync, validation.
- Harness adapters append changing context after stable prompt history. They never prepend current
  state into a cache-sensitive prefix.
- SQLite uses WAL, FTS5, append-only events, projection tables, evidence references.
- Evidence reference presence is a governance rule. Resolution is core-verifier or adapter-specific
  and is recorded separately with method, time, and hashes when available.
- Adapters read external trackers and return compact references.
- Bulk data stays external; only URI, IDs, selected summary, and hashes enter state.
- Historical conversation episodes use a separate FTS5 projection. They remain untrusted and
  never become accepted graph records automatically. Imports retain harness, session, message,
  part, timestamp, hash, redaction, and injection-risk metadata. Explicitly approved imports may
  retain redacted message text in this archive. Reasoning is excluded by default and requires a
  separate explicit opt-in. Automated backfill never copies a whole transcript into governed
  records or packets. Governed record writes are bounded to 240 title characters, 8,000 body
  characters, and 16,384 metadata bytes. Clients must submit distilled state, not transcript dumps.

MLflow remains the system of record for runs and artifacts. AgentRoots reads the REST API, builds a
bounded deterministic snapshot, and links its hash to a research record. The snapshot includes
parameters, latest metrics, dataset digests, Git source tags, and optional artifact paths. It never
downloads artifacts or writes to MLflow. Revalidation compares snapshots and can mark an accepted
record stale. The original evidence event remains immutable; each evidence update or revalidation
appends a new event and revision to the current projection.

PostgreSQL team backend needs parity, concurrency, and security tests before support. The base
record-search path uses FTS5 and fuzzy matching. The base install adds BGE-small hybrid retrieval
through FastEmbed and NumPy. The resident hook daemon then shares one BGE instance
between records and untrusted conversation episodes. Model loading and initial project indexing run
in the background while hooks remain on lexical fallback. Large vector matrices are revision-keyed,
stored in the external model cache, memory-mapped after restart, and pruned to three revisions per
project. This avoids loading model weights for every hook process, recomputing unchanged projects,
or blocking the first hook.

## Proactive hook path

Codex hooks cover session start, user prompts, pre-tool intent, post-tool results, compaction,
subagent boundaries, and stop. Post-compaction records a checkpoint; the following `SessionStart`
compact event supplies refreshed context. The small Node wrapper always fails open. It forwards a redacted,
bounded payload to a loopback-only daemon protected by a random bearer token. If delivery fails, it
spools the event atomically outside the repository and returns control to Codex. Retrieved warnings
are advisory. They do not deny a tool call, execute a command, or claim that duplicated work was
prevented.

The daemon keeps installed retrieval and extraction backends resident. Retrieval is synchronous
and capped at 180 estimated tokens. When installed, GLiNER warms after retrieval and extracts queued
events in batches of eight. Extracted items remain candidates with their
source event and exact evidence span. They never enter governed state without normal review. The
event audit stores the bounded intent delta plus payload size, hash, tool name, path fingerprint,
and risk flags. It does not retain raw working-directory paths, full hook payloads, or agent
transcripts.

## Harness-independent backfill

Backfill separates discovery, consent, export, import, and extraction. Discovery reads only session
metadata and produces a manifest with `approved: false`. Export refuses to read conversation bodies
without explicit approval. Codex JSONL is streamed read-only; OpenCode SQLite uses URI `mode=ro`
plus `PRAGMA query_only=ON`.

Project identity prefers explicit session metadata or a normalized Git remote. Registered path and
project-name aliases are fallbacks. Named Git projects are registered with
`agentroots project-bind PROJECT_ID [PATH]`. Import may receive a reviewed cross-machine project map.
Message bodies, titles, and extracted episode content never authorize project routing. Ambiguous
sources retain their conservative explicit default project and remain untrusted.

Content hashes deduplicate repeated messages within each project while `source_aliases` preserve
additional provenance URIs. A composite `(project, content_hash)` index keeps large imports linear.
Background extraction prioritizes high-signal episodes, records an audit row, and emits reviewable
candidates only. Imported episode metadata hashes source paths and directories instead of retaining
their raw values.

Candidate extraction automatically suppresses only exact normalized repeats. Paraphrases remain
review candidates because aggressive lexical deduplication can erase contradictions or reverse
scientific roles. Reviewers may merge genuine duplicates without losing source provenance.
