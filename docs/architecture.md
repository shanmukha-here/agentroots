# Architecture

`server/CLI → ResearchService → SQLite event ledger + projections`

- Domain validates enums and lifecycle.
- Service owns transactions, governance, redaction, context, sync, validation.
- SQLite uses WAL, FTS5, append-only events, projection tables, evidence references.
- Adapters read external trackers and return compact references.
- Bulk data stays external; only URI, IDs, selected summary, and hashes enter state.

PostgreSQL team backend needs parity/concurrency/security tests before support. No embeddings MVP.

