# Security policy

Use the repository's **Report a vulnerability** link when GitHub private vulnerability reporting is
available. If that link is unavailable, open a public issue containing no sensitive detail and ask
the maintainer for a private reporting channel. Never publish exploit details or secrets in an
issue.

Threat boundary: all stored research text and adapter output is untrusted. Server redacts common
secret patterns, flags prompt injection, never executes stored commands, and keeps adapters read-only.
Local SQLite inherits host filesystem permissions. Remote/team deployments are unsupported.

Tool capture is limited to project-local developer tools by default. Generic MCP, mail, browser,
database, and connector payloads are not stored. Operators can opt in narrowly with
`AGENTROOTS_CAPTURE_TOOL_PREFIXES`.

The local alpha server has no authenticated principals. `creator`, `actor`, and `reviewer` values are
provenance labels supplied by the local client, not security identities. The creator-reviewer check
prevents accidental self-acceptance in cooperative workflows but does not stop a malicious local
client from choosing another label. Do not expose the stdio server or its database to untrusted
users. Authenticated principals and ACLs are required before any remote or team deployment.

MLflow tracking URLs and bearer tokens are server-side environment configuration. Tokens are not
stored in records or returned to agents. Parameter and tag keys that look secret-bearing are
redacted before storage or MCP output. Treat MLflow notes, parameters, tags, dataset metadata, and
artifact paths as untrusted text. Restrict the configured tracking URL to a trusted server.
