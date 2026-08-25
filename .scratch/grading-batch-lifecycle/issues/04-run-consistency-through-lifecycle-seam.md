# 04 — Run consistency through the lifecycle seam

**What to build:** Make consistency analysis receive grade records and draft feedback through the same authoritative grading-batch inventory while keeping every consistency flag advisory.

**Blocked by:** 01 — Establish the grading-batch lifecycle seam

**Status:** ready-for-agent

- [ ] Consistency uses the grading-batch interface instead of reconstructing artifact pairs in its command adapter.
- [ ] Structural blocking findings remain distinct from consistency flags.
- [ ] Existing score-outlier, density-inversion, reused-feedback, unassessed, and too-small-batch behavior remains unchanged.
- [ ] Consistency flags never create or invalidate a verification receipt.
- [ ] Consistency flags never block lifecycle progress inside the grading-batch module.
- [ ] The optional fail-on-findings command behavior preserves its current exit meaning without changing the advisory domain rule.
- [ ] The consistency command keeps its current arguments and user-facing purpose.
- [ ] Interface-level tests cover artifact loading while focused consistency tests continue to cover the analysis itself.
