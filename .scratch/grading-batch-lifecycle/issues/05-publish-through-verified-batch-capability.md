# 05 — Publish through the verified-batch capability

**What to build:** Allow rehydration only from a verified-batch capability, keep identity substitution outside grading modules, stage the whole grading batch, and publish a complete first-time destination only when every draft succeeds.

**Blocked by:** 03 — Issue and validate verification receipts

**Status:** ready-for-agent

- [ ] An ordinary grading batch cannot request publication.
- [ ] A verified-batch capability comes only from successful persisted verification or current receipt revalidation.
- [ ] Missing, stale, malformed, or unknown receipts block publication with a clear verification instruction.
- [ ] Receipt validity is checked again immediately before staging begins.
- [ ] The local renderer adapter is the only participant that loads the token map.
- [ ] The renderer writes identity-bearing output directly into staging and returns only identity-free findings.
- [ ] The renderer's grading-batch identity must match the verified grading batch.
- [ ] Every draft is rendered before the publication target changes.
- [ ] Unmapped tokens and rendering failures are aggregated across the grading batch.
- [ ] Any blocking finding removes staging and leaves the destination unchanged.
- [ ] A successful first publication contains every expected rehydrated draft and an identity-free versioned publication marker.
- [ ] Rehydration keeps its existing command arguments and success or failure exit meanings.
