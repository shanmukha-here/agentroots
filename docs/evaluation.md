# Evaluation protocol

Run identical research tasks with four baselines: no memory, maintained `HANDOFF.md`, generic
memory, and agentic-experiments alone. Randomize order; preserve model/tool budgets; report task
and retrieval metrics from `roadmap.md` with confidence intervals and raw fixture IDs.

Adversarial matrix: prompt-injection record, secret, contradictory workers, disappeared run,
rebased Git reference, concurrent review race, interrupted transaction, adapter outage, nonexistent
evidence. Acceptance criterion: fresh agent reaches correct frontier from ≤2k estimated tokens
without repeating known failed exploration.

Do not advertise token/work reduction until benchmark produces reproducible measured values.

Run the deterministic continuity smoke benchmark with:

```bash
python examples/continuity_benchmark.py
```

It creates 100 reviewed findings in temporary external state, starts a fresh service instance,
queries one known failed experiment, and reports recall, estimated packet tokens, configured
budget, and local retrieval latency. This smoke benchmark checks mechanics. It does not measure
task-success improvement against the baselines above.
