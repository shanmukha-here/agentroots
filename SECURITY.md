# Security policy

Report vulnerabilities privately to project maintainer address listed in future repository security
settings. Until public hosting exists, do not send sensitive reports through public issues.

Threat boundary: all stored research text and adapter output is untrusted. Server redacts common
secret patterns, flags prompt injection, never executes stored commands, and keeps adapters read-only.
Local SQLite inherits host filesystem permissions. Remote/team deployments are unsupported.

