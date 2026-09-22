# Evaluation protocol

Run identical research tasks with four baselines: no memory, maintained `HANDOFF.md`, generic
memory, and agentic-experiments alone. Randomize order; preserve model/tool budgets; report task
and retrieval metrics from `roadmap.md` with confidence intervals and raw fixture IDs.

Adversarial matrix: prompt-injection record, secret, contradictory workers, disappeared run,
rebased Git reference, concurrent review race, interrupted transaction, adapter outage, nonexistent
evidence. Acceptance criterion: fresh agent reaches correct frontier from ≤2k estimated tokens
without repeating known failed exploration.

The end-to-end release scenario must cross a real harness boundary: import an explicitly approved
OpenCode or Codex conversation read-only, create only untrusted episodes and candidates, begin a
fresh Codex task under the same canonical project identity, surface a known failed path before a
duplicate tool action, let the agent decide whether to skip it, review the candidate independently,
and revalidate its external evidence. Report misses and false injections, not only successful
recall. Hook output is advisory and must never be scored as an execution block.

Do not advertise token/work reduction until benchmark produces reproducible measured values.

Run the deterministic continuity smoke benchmark with:

```bash
python examples/continuity_benchmark.py
```

It creates 100 reviewed findings in temporary external state, starts a fresh service instance,
queries one known failed experiment, and reports recall, estimated packet tokens, configured
budget, and local retrieval latency. This smoke benchmark checks mechanics. It does not measure
task-success improvement against the baselines above.

Retrieval and hook-budget development benchmarks have declared optional dependencies:

```bash
python -m pip install -e ".[benchmarks]"
python benchmarks/retrieval_benchmark.py --workdir /path/outside/the/repository/retrieval
python benchmarks/hook_budget_benchmark.py \
  --workdir /path/outside/the/repository/hooks \
  --cache-dir /path/to/user/model-cache
```

The hook budget uses `cl100k_base` as a stable reference tokenizer. Actual harness tokenization can
differ. Synthetic retrieval fixtures are useful for regression checks, not proof that AgentRoots
reduces real project work.

Evidence reporting must separate reference attachment from verification. Track at least evidence
reference rate, verifier success rate by evidence kind, unresolved-reference rate, stale-evidence
leakage, and time since last verification. A URI-shaped string alone does not count as resolved.
