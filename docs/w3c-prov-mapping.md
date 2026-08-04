# W3C PROV mapping

- Goal, Question, Hypothesis, Claim, Finding, Decision, ArtifactRef, Evidence → `prov:Entity`
- Experiment and external run → `prov:Activity`
- Agent and Session → `prov:Agent` (exporters may also model session as Activity)
- `produced`, `derived_from` → `prov:wasGeneratedBy`, `prov:wasDerivedFrom`
- `tests`, `supports`, `contradicts`, `invalidates` → qualified derivation with local role
- creator/reviewer → `prov:wasAttributedTo` / qualified association
- revision/supersession → specialization plus invalidation timestamps

Local lifecycle and epistemic relations remain richer than core PROV. Exporters must preserve
local relation/status fields rather than flattening away review meaning.
