# Architecture

`server/CLI -> ResearchService -> SQLite event ledger + projections`

- Domain validates enums and lifecycle.
- Service owns transactions, governance, redaction, context, sync, validation.
- SQLite uses WAL, FTS5, append-only events, projection tables, evidence references.
- Adapters read external trackers and return compact references.
- Bulk data stays external; only URI, IDs, selected summary, and hashes enter state.

MLflow remains the system of record for runs and artifacts. AgentRoots reads the REST API, builds a
bounded deterministic snapshot, and links its hash to a research record. The snapshot includes
parameters, latest metrics, dataset digests, Git source tags, and optional artifact paths. It never
downloads artifacts or writes to MLflow. Revalidation compares snapshots and can mark an accepted
record stale without rewriting the original evidence event.

PostgreSQL team backend needs parity, concurrency, and security tests before support. No embeddings
are required for the MVP.
