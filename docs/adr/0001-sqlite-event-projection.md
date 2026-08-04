# ADR 0001: SQLite ledger and projections

Accepted. External SQLite DB in WAL mode. Append mutation events; maintain query-friendly
projections transactionally. PostgreSQL waits for team/concurrency requirements.

