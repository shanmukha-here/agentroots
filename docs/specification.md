# Protocol specification: schema v1

This document describes the schema v1 contract shipped by AgentRoots 0.2.0. Protocol schema
versions and package release versions are separate; this release does not rename the MCP tools
or the `research://` resource namespace.

Mutable graph nodes: Origin, Goal, Question, Hypothesis, Experiment, RunRef, Observation, Claim,
Finding, Decision, ArtifactRef, Evidence, Agent, Session. Each projection has UUID, project, creator, mode,
status, revision, timestamps, text, and metadata. The relation set is `decomposes`, `tests`,
`derived_from`, `supports`, `contradicts`, `supersedes`, `depends_on`, `produced`, `invalidates`,
`selected`, `rejected`, and `resolves`. Relation endpoints are type-checked and must share a project.

Origin captures why a project exists, the problem that created it, who it serves, and its broad
definition of success. It precedes concrete goals and remains durable as those goals evolve.

Events append only and support idempotency keys. Revisions protect projection writes.
JSON Schemas in `schemas/` define exchange payloads. Wheels expose the same v1 contracts under
`agentroots/schemas/v1` through `importlib.resources`. JSONL sync orders events by ledger seq;
imports validate the known event grammar, lifecycle, project boundary, evidence, and revisions in
one transaction. The entire batch rolls back on failure. A destination does not inherit verifier
trust from event metadata, so locally bound or tracker-bound evidence must resolve again before an
accepted projection can be recreated. Full-state backup and restore carries the local archive.

Conversation backfill is opt-in. Metadata discovery must not read message bodies. Body export needs
explicit approval and read-only source access. Project is the durable partition; harness, model,
agent, host, session, and path are source provenance. Cross-harness duplicate content is stored once
per project with source aliases. Backfilled text is untrusted and may create candidates, never
accepted records.

Project routing accepts only explicit session or import metadata, normalized Git remotes, and
reviewed registry aliases. Named Git projects are registered with
`agentroots project-bind PROJECT_ID [PATH]`. Stored message content, titles, inferred intent, and
extracted candidates never authorize a project change.

Candidate can become provisional or rejected. Provisional can become accepted, disputed, or
rejected. Accepted can become disputed, superseded, or stale. Creator cannot accept own proposal.
Acceptance requires at least one attached evidence reference. Reference presence is distinct from
verification: a generic URI is unresolved unless a typed adapter has fetched or validated it.
Verification results, hashes, and timestamps belong in evidence metadata and may later trigger
staleness. Claim, Finding, Observation, and Decision acceptance requires mechanically verified
evidence. The protocol must not describe an arbitrary URI as verified evidence.
A caller-written test receipt is reference-valid, not mechanically verified, unless a trusted runner
attests it. Evidence additions and revalidation append revisions and events. Earlier evidence and
verification history stays immutable.
An accepted Claim, Finding, Observation, or Decision may explicitly `resolves` one or more
same-project goals or questions. Resolved items stay in history but leave the active frontier and
matching packet sections. `supports` never implies completion.

The local alpha has no authenticated principals. `creator`, `actor`, and `reviewer` are provenance labels
provided by clients. Creator-reviewer separation prevents accidental self-acceptance in a
cooperative workflow but cannot prevent a malicious local client from spoofing a label.

Packets rank accepted then provisional then remaining records, apply FTS5 with typo fallback,
expand graph neighbors, and exclude stale/superseded facts. Default MCP retrieval returns five
stateless eight-character record references behind a stable prefix. Prefixes must resolve uniquely
inside the selected project. Agents fetch full records only when required. Explicit full packets
expose semantic sections and enforce a conservative budget against serialized output. CLI-created
packets can persist packet hash and supplied/used IDs for audit. MCP read tools do not mutate the
ledger, packet audit, or project registry.

Full packets normalize records once under `records`. Section arrays contain record IDs, so a
finding that is both accepted and a failed approach does not duplicate its body or evidence inside
the token budget.

Transport is MCP stdio. Semantic exchange stays JSON for non-MCP CLI/JSONL clients.
Automated import never copies a full transcript into governed records or context packets. Governed
record writes are bounded to 240 title characters, 8,000 body characters, and 16,384 metadata
bytes. Clients must submit distilled state rather than raw transcript dumps. With explicit approval,
a harness adapter may copy redacted message parts into the separate untrusted episode archive for
search and candidate extraction. Reasoning parts are excluded by default and require a separate
explicit opt-in. Retrieval returns bounded excerpts or source links, never an entire imported
conversation. Source conversations remain read-only.
