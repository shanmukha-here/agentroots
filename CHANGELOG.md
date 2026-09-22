# Changelog

## 0.2.0 - 2026-09-22

Shared project continuity alpha. This release expands the original local research-state core
into project-scoped continuity across sessions and supported harnesses. It is not a stable 1.0
or a hosted multi-agent platform.

The MCP SDK compatibility floor is now 1.29, matching the release-tested tool and resource APIs.
An isolated install was also exercised with SDK 1.30. Python remains 3.11 or newer.
See the [release validation and its limits](docs/releases/0.2.0.md).

### Added

- Guided setup with separate client-configuration and read-only history consent, project selection,
  background indexing, and visible backend, RAM, and storage status.
- Canonical project identity from explicit metadata, normalized Git remotes, and aliases.
  Approved Codex and OpenCode backfill deduplicates shared content while preserving provenance.
- Separate untrusted conversation episodes and reviewable extraction candidates. Candidate
  inspection, correction, promotion, rejection, and merging are available through MCP.
- Proactive Codex hooks at session, prompt, tool, subagent, and compaction boundaries, with a
  resident loopback daemon, bounded recall, delivery recovery, and asynchronous extraction.
- Experimental OpenCode plugin. Generic stdio MCP clients retain tool-based access without
  automatic lifecycle hooks.
- Default local BGE hybrid retrieval with FTS fallback, asynchronous model warmup, revision-keyed
  vector caches, and optional GLiNER or configured Qwen extraction. No Qwen weights are included.
- Compact MCP context with short record references, read-only MCP annotations, current-project
  discovery, graph relationships, and a 16-tool surface.
- Synthetic installed-package and full-workflow demos, cross-harness regression coverage,
  package integration checks, and a documented support matrix.

### Strengthened

- Mechanically verified evidence requirements for accepted claims, findings, observations, and
  decisions. Arbitrary URIs and caller-written test receipts do not count as verified proof.
- Local file evidence is restricted to trusted roots and records hashes. Git and MLflow snapshot
  revalidation preserve evidence history and can stale affected accepted findings.
- Same-project relationship validation, optimistic concurrency, idempotency, candidate scope,
  strict atomic event import, and secret/prompt-injection handling.
- Explicit goal/question resolution, failed-attempt recall, contradictions, and unresolved-work
  computation across the research lifecycle.
- Stateless MCP reads and normalized full packets budgeted against the serialized response.
- Failed semantic inference now opens a circuit breaker instead of retrying on every hook.
  Explicit semantic opt-out is respected even after warmup. Restart the process after repairing
  the model or changing this setting to re-enable semantic retrieval.
- Daemon health exposes its version. Setup and status flag an older running daemon rather than
  claiming an upgrade is ready.

### Compatibility and limits

The package version is 0.2.0; JSON Schemas remain under schema v1. Existing `research_*` tools
and `research://` resource names are retained. New tools and stricter trust validation are not
a promise of backward compatibility with every prototype event stream.

OpenCode automation and Qwen extraction are experimental. Trackio is an adapter interface.
Team authentication, remote serving, automatic cross-machine synchronization, and direct graph
editing are not shipped. Local actor labels are not authenticated identities. Hook advice does
not block actions or guarantee duplicate-work prevention. No demo video is published in this release.

### Upgrading from 0.1.0

1. Back up your existing state outside the repository before upgrading:

   ```bash
   agentroots backup /path/to/backups/agentroots-before-020.sqlite3
   ```

2. Install the tagged version in the same Python environment:

   ```bash
   python -m pip install --upgrade "agentroots @ git+https://github.com/shanmukha-here/agentroots.git@v0.2.0"
   ```

3. If you used a development build with a resident daemon, inspect `agentroots hook-status`.
   When `restart_required` is true, stop only the reported AgentRoots daemon process using your
   OS process manager. Do not terminate unrelated Python processes. Then run `agentroots setup`,
   restart connected agent clients, and review changed Codex hooks through `/hooks`.
4. Run `agentroots doctor` and `agentroots validate PROJECT_ID`. Inspect existing accepted
   records under the stronger evidence rules. Stricter JSONL imports can reject older events
   whose evidence cannot be verified locally; keep the backup rather than bypassing validation.

Source conversations and external runs are never part of the upgrade's write targets.
