# Proactive hook evaluation

Measured on Windows with Python 3.12, Codex 0.147.0 alpha, BGE small v1.5, and GLiNER2 1.3.2.
Numbers are development evidence collected before the v0.2.0 release, not broad production
guarantees or a fresh benchmark of every dependency version. Model timings and the optional Qwen
training experiment are separate from the release's package and correctness checks.
All hook warnings in this evaluation were advisory. AgentRoots surfaced prior work; Codex decided
whether to proceed. No hook blocked a tool call.

## Current optimized results

- FastEmbed BGE model construction with a fresh model cache: 3.69 seconds; cached startup: 0.96
  seconds. The original Sentence Transformers path took 82.173 seconds when it also had to
  download and initialize the model.
- BGE indexing plus query over 1,000 records: 2.30 seconds, down from 13.431 seconds. Warm query:
  5.7 ms, down from 41 ms. The correct failed experiment was recovered.
- Persistent BGE index over 10,000 records: 24.19 seconds to build once, 0.374 seconds to reload in
  a fresh retriever, and 10.9 ms warm. Vector indexes are external cache files, never repository
  state.
- FTS over 100,000 episodes: exact-match p50 1.27 ms, down from 752 ms. A no-hit fuzzy fallback is
  bounded to the newest 2,000 episodes and measured 10.69 ms p50, down from 745 ms. BGE handles
  older semantic misses.
- Materializing 100,000 episode candidates takes 1.25 seconds once and 20 ms on subsequent
  searches because the resident store caches the source projection until its import revision
  changes.
- Resident daemon over 30 tool events: 55.8 ms median, 220.6 ms p95, 2169.4 ms maximum.
  The maximum included model warmup contention. Cold requests now use FTS while BGE warms in a
  background thread.
- Full Node wrapper p50: 135.7 ms and p95: 156.2 ms over 30 events, down from 497 ms. The wrapper
  now calls the resident daemon directly and starts Python only for fail-open recovery.
- GLiNER cached cold load: 14.25 seconds in a background thread. Warm single extraction: 127 ms,
  down from about 6.1 seconds. Batch-eight throughput: 106 ms per event. Threshold 0.3 produced
  the best relaxed F1 on the held real set.
- Experimental Qwen3.5-0.8B SFT: the selected ontology-corrected model reached 0.708 record F1,
  0.785 exact-example accuracy, and perfect JSON, schema, and evidence validity on the 65-example
  held real set. This is 2.65 times GLiNER's relaxed F1 on that set. A merged model preserved exact
  output parity and processed the set in 9.74 seconds at batch 32. It remains optional because the
  gold set is small and the merged artifact is 1.5 GB.
- Merged Qwen resident benchmark: 1.76 seconds to load, 624 ms for first generation, 224 ms warm
  p50, and 237 ms warm p95 over 20 single-example generations. A fresh one-shot process took 7.55
  to 7.70 seconds, so per-hook process startup is explicitly unsupported.
- Loaded BGE plus GLiNER process working set: 781 MB, down from about 935 MB. BGE alone measured
  208 MB.
- Failed delivery spool: one event written atomically and recovered successfully.
- Fresh Codex process: automatic context warned that the cosine objective had already failed after
  the agent read a plan file. The agent then chose to skip the duplicate run and use focal loss.
- Fresh Codex pre-tool process: before a harmless shell command containing `--loss cosine`, Codex
  reported the AgentRoots warning title `Cosine objective already failed`.
- Hook context in that workflow remained below the 180 estimated-token hard cap.
- Synthetic retrieval policy benchmark: broad retrieval averaged 99.1 reference tokens and 0.828
  recall. Balanced retrieval averaged 83.2 tokens and 0.745 recall. AgentRoots uses the broad
  policy under the hard cap because the measured difference was 15.9 tokens.

## Validation coverage

Tests cover prompt, pre-tool, and post-tool retrieval, failed-result intent, three-event lookback,
idempotency, deduplication, token limits, secret redaction, prompt-injection labels, project isolation,
concurrent events, semantic episode rescue, GLiNER2's real response shape, and candidate-only
extraction. Separate smokes cover the Node wrapper, daemon queue, spool recovery, MCP stdio,
package build, and a context-free Codex process.

## Known limits

- Model setup still requires a download on first use.
- BGE-only and BGE-plus-GLiNER memory samples differ substantially, as reported above. Qwen adds
  its own model allocation. These are measured process samples, not minimum RAM requirements.
  Lexical-only mode is available for constrained machines.
- Collaboration subagents created inside an already-running Codex task do not reload a plugin that
  was installed during that same task. A fresh Codex process did load and exercise it correctly.
- The extraction benchmark contains 65 manually reviewed examples from two source-isolated real
  Codex sessions. It is useful development evidence, not a broad external benchmark.
