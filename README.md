<img src="docs/assets/brand/agentroots-mark.svg" alt="AgentRoots logo" width="170" align="left">

# AgentRoots

### Different agents. Same roots.

Shared, evidence-backed project knowledge across agents, sessions, and harnesses.

Built by Shanmukha Vellamcheti and Codex, an AI coding collaborator.

<br clear="left">

Your next agent should inherit the work, not repeat the investigation. AgentRoots keeps what
you tried, what you know, why it matters, and what remains to do in one durable project graph.
A fresh agent gets compact, relevant pointers and opens the evidence only when needed.

**Past attempts. Present understanding. Future intent. One project's shared roots.**

![AgentRoots connects past attempts, present evidence, and future work through shared roots](docs/assets/agentroots-past-present-future-v2.jpg)

AgentRoots is a local-first, open-source MCP server for engineering, research, and long-running
agent work. A smaller agent can investigate and propose findings; another agent can review and
reuse them later. Optional, read-only conversation backfill also recovers leads that never made
it into a handoff or source file. History stays untrusted until conclusions pass review.

No required model API, cloud account, or agent orchestrator. Works with one agent across sessions
or several agents sharing a project. It helps reduce repeated exploration, not eliminate every
file read or replace verification.

## Start here

Requires Python 3.11+ and Git. Use a Python virtual environment if your OS manages its Python
installation. Codex proactive hooks also require Node.js.

```bash
python -m pip install "agentroots @ git+https://github.com/shanmukha-here/agentroots.git@v0.2.0"
agentroots setup
```

Setup detects supported clients, asks before changing their configuration, and separately asks
whether it may read existing conversations. You can decline history or select particular projects.
Codex requires an additional `/hooks` review. Downloads and approved backfill continue in the
background; lexical search works while the semantic model warms up. Setup is short, but installation
and large history imports depend on your connection and machine.

Then use your agents normally. On supported hook paths, AgentRoots recalls compact project context
at prompt and tool boundaries and queues new findings for review. It stays quiet when there is
nothing useful to add. Notification presentation depends on the host; not every client shows toasts.

```bash
agentroots status          # backends, RAM, storage, and indexing progress
agentroots doctor          # configuration and integration checks
agentroots project-current # the project this checkout resolves to
```

**v0.2.0 is an alpha, not a universal background-memory service.** Codex has a validated MCP and
proactive hook path. OpenCode automation is experimental. Claude and other stdio MCP clients can
call tools, but do not get automatic hooks from this package. DeepSeek support depends on its
harness, not its model name. See the [support matrix](docs/support-matrix.md).

No PyPI publication yet. Existing users: see the [upgrade notes](CHANGELOG.md#upgrading-from-010).
For manual configuration and history selection, see [integrations](docs/integrations.md).

## See the knowledge, not just the conversation

![Full synthetic AgentRoots knowledge graph with goals, findings, evidence, and lifecycle states](docs/assets/agentroots-knowledge-graph.png)

The same versioned state agents query becomes an offline, interactive knowledge map for humans.
Search, filter, trace relationships, inspect evidence, and copy record IDs to request corrections.
The synthetic overview covers all 14 record types and 12 relationship types. It contains no private
project content. Direct editing in the graph is planned; today corrections use the CLI or MCP.

```bash
agentroots graph PROJECT_ID /path/outside/repo/project-map.html
```

## Why I built AgentRoots

My research involves exploring many hypotheses and experimental paths. Coding agents let me try
more of them, faster. But as experiments, conversations, and subagents multiplied, keeping track
of what we had already learned became its own problem.

An orchestrator might read a codebase to plan two tasks, then both subagents read the same files
to get up to speed. That is the same understanding reconstructed three times. Weeks later, after
compaction or a switch to another harness, an agent can suggest an experiment we already discussed
and ruled out. The reason may exist only in an old conversation, not in the code or latest handoff.

I wanted that work to become durable without filling every new prompt with the entire past.
AgentRoots connects the project's origin and intent, previous attempts, current evidence, and open
questions. An agent can inherit that understanding, check what matters for its task, and add to it.
Research motivated it, but the same problem appears in software projects and other long-running work.

This is not a claim that other memory tools only remember the past. Memory, planning, and provenance
systems overlap. AgentRoots focuses on connecting them in a compact, reviewable project graph,
without owning the agents themselves. See the [landscape and boundaries](docs/landscape.md).

## How the roots grow

1. **Capture leads.** Agents propose records, or approved conversation history is indexed read-only.
   Background extraction creates candidates, never accepted facts.
2. **Ground and review.** Link evidence, inspect contradictions, and accept or reject a proposal.
   A creator cannot accept its own proposal by default. Changes append revisions and events.
3. **Recall when relevant.** MCP provides bounded context. Supported hooks also check emerging
   tool intent and results, not just the initial user prompt. They offer advice, not execution gates.
4. **Keep state current.** Revalidate Git and tracker evidence, mark changed findings stale, and
   preserve failed approaches. Accepted results can explicitly resolve goals or questions.

The durable boundary is the **project**, not the agent, model, harness, or conversation. Routing
uses explicit metadata, Git remotes, and registered aliases. Conversation text cannot choose a
different project. Bind a preferred project name once when needed:

```bash
agentroots project-bind PROJECT_ID /path/to/repository
```

Matching project identities does not automatically synchronize separate machines. Share approved
state through export/import or backup/restore; hosted team sync is not shipped.

## Small context, inspectable evidence

The default MCP context response offers up to five matches within a conservative **200 estimated
token** budget, with short record IDs for opening details. Hook injections are capped at **180
estimated tokens**. These are payload budgets, not guarantees about a host's wrapper tokens,
total turn cost, or provider cache hits. Changing hook context is appended at supported event
boundaries, not rewritten into a stable system-prompt prefix.

```json
{"tool":"research_get_context","arguments":{"project":"my-project","query":"previous cache failures"}}
{"tool":"research_get_record","arguments":{"project":"my-project","record_id":"8f2a91c4"}}
```

The record ID above is illustrative. Use an ID returned by your context response. Full packets are
available with `view: "full"`; records are normalized and the serialized response is budgeted.
MCP reads do not write packet-audit rows. Explicit CLI context packets are audited.

Retrieval and extraction do different jobs:

- **BGE + FTS5 + fuzzy search** retrieve existing state. BGE is enabled by default and warms in
  the background. FTS continues serving cold or failed-model requests. Set
  `AGENTROOTS_SEMANTIC=off` for lexical-only operation.
- **Candidate extraction** uses configured Qwen first, optional GLiNER next, and a conservative
  heuristic otherwise. Qwen weights are not bundled, training is not required for normal use,
  and no extraction backend bypasses review.

See [measured latency, token costs, and limitations](docs/hook-evaluation.md). Development results
are not evidence of universal productivity gains or reliable recall for every project.

## What ships

- External SQLite WAL storage, append-only events, revisions, and project-scoped graph queries.
- Candidate review, evidence classification, contradictions, failure recall, and stale-state checks.
- Read-only history backfill for Codex and OpenCode, with separate untrusted episode search.
- Codex proactive hooks, a resident local daemon, and an experimental OpenCode plugin.
- Read-only MLflow run search, comparison, snapshots, and revalidation. Trackio has an adapter
  interface; H-E-F and signac have importers.
- Interactive read-only graph export, JSONL event transfer, and full-state backup/restore.
- CLI, 16 MCP tools, resources, schemas, reproducible synthetic workflows, and regression tests.

The `research_*` tool names retain the original investigative ontology. The project boundary is
broad enough for engineering and general agent work; you do not need to invent a hypothesis or
pretend an exploratory run was preregistered. See the [protocol](docs/specification.md) and
[tool configuration](docs/integrations.md).

## Privacy and trust boundaries

State, model weights, and indexes stay in OS user data/cache directories, not your repository.
Large artifacts stay in their tracker or original storage. Source conversations are read-only;
backfill requires approval. No telemetry or required cloud inference is built in.

Stored text is untrusted. Secret scanning and redaction reduce exposure but are not a guarantee
that every secret or prompt injection is detected. Claims, findings, observations, and decisions
need mechanically verified evidence before acceptance. A checksum verifies the referenced bytes,
not the scientific truth of a conclusion. Arbitrary URIs and caller-written test receipts are
references, not automatically verified proof.

Actor names are local provenance labels, not authenticated identities. Self-review rules prevent
cooperative mistakes, not hostile impersonation. AgentRoots does not expose a production team
authorization boundary. See [SECURITY.md](SECURITY.md).

AgentRoots never owns agent spawning, model routing, stored-command execution, schedulers,
training jobs, worktrees, or artifact storage. PostgreSQL, remote HTTP, ACLs, full Flowcept/AiiDA
integrations, and direct graph editing remain [roadmap work](docs/roadmap.md).

## Contribute or reproduce the workflow

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m examples.full_flow_demo --output /path/outside/repo/agentroots-demo
```

The demo exercises approved synthetic history, review, a local MLflow fixture, duplicate-risk
recall, and Git-induced staleness. It is a reproducible core workflow, not a recording of a live
agent. The polished live-session video is still pending.

[Architecture](docs/architecture.md) · [Evaluation](docs/evaluation.md) ·
[Changelog](CHANGELOG.md) · [Contributors](CONTRIBUTORS.md)

Apache-2.0. Contributions and reports from real projects are welcome.
