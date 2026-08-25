# 05 — Publish through the verified-batch capability

**What to build:** Allow rehydration only from a verified-batch capability, keep identity substitution outside grading modules, stage the whole grading batch, and publish a complete first-time destination only when every draft succeeds.

**Blocked by:** 03 — Issue and validate verification receipts

**Status:** resolved

- [x] An ordinary grading batch cannot request publication.
- [x] A verified-batch capability comes only from successful persisted verification or current receipt revalidation.
- [x] Missing, stale, malformed, or unknown receipts block publication with a clear verification instruction.
- [x] Receipt validity is checked again immediately before staging begins.
- [x] The local renderer adapter is the only participant that loads the token map.
- [x] The renderer writes identity-bearing output directly into staging and returns only identity-free findings.
- [x] The renderer's grading-batch identity must match the verified grading batch.
- [x] Every draft is rendered before the publication target changes.
- [x] Unmapped tokens and rendering failures are aggregated across the grading batch.
- [x] Any blocking finding removes staging and leaves the destination unchanged.
- [x] A successful first publication contains every expected rehydrated draft and an identity-free versioned publication marker.
- [x] Rehydration keeps its existing command arguments and success or failure exit meanings.

## Answer

Persisted verification and receipt revalidation now create the only verified-batch capability that can publish. Publication revalidates immediately, checks the renderer's grading batch, renders every feedback file in staging, aggregates identity-free failures, and changes the destination only after the entire grading batch succeeds. The local renderer adapter alone loads the token map and writes identity-bearing output. Successful first publication includes only rehydrated feedback plus the versioned identity-free publication marker.

Verification: 373 tests passed, bytecode compilation passed, the package validator passed, and the final Standards and Spec reviews reported no findings.
