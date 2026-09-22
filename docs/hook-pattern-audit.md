# Hook pattern audit

This audit informed the AgentRoots hook design. It does not copy implementation code.

## Reused patterns

- `kunickiaj/codemem`: atomic event spooling, fail-open commands, a resident ingestion process,
  and prompt plus tool boundaries.
- `cogniplex/codemem`: selective tool handling, structured tool payload parsing, focus and failure
  triggers, and cheap read-only database access inside latency-sensitive hooks.
- `yoloshii/clawmem`: session-scoped recent-turn lookback, compaction handling, deduplication, and
  strict latency budgets.
- `riponcm/projectmem`: deterministic advisory warnings when an action resembles a previously failed
  fix.

## AgentRoots differences

Generic memory retrieval is not sufficient for the project thesis. AgentRoots retrieves both
governed graph records and untrusted historical episodes. Accepted state ranks above candidates.
Failed approaches and contradictions remain first-class. Every injection is token bounded and
audited. GLiNER extraction is asynchronous and can only create candidates. Scientific acceptance
still requires evidence and a separate reviewer.

The proactive tool boundary is important. A user prompt may be broad, while a duplicate experiment
only becomes visible after the agent reads code or chooses a command. AgentRoots performs another
small retrieval from that new intent delta and recent session context.
The result is a warning or context suffix only. AgentRoots does not deny the planned tool call.
