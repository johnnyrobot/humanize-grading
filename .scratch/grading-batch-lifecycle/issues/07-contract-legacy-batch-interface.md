# 07 — Contract the legacy batch interface

**What to build:** Complete the migration to the deep grading-batch lifecycle by removing the old shallow survey interface and redundant tests after every production caller uses the new seam.

**Blocked by:** 02 — Make verification authoritative and whole-batch; 04 — Run consistency through the lifecycle seam; 05 — Publish through the verified-batch capability

**Status:** ready-for-agent

- [ ] Status, verification, consistency, and publication callers use the deep lifecycle interface.
- [ ] No production caller reconstructs batch membership, pairing, progress, or receipt eligibility independently.
- [ ] The old survey interface and obsolete progress constants are removed once no caller needs them.
- [ ] Tests that only pin the old survey data shape are removed or replaced with lifecycle behavior tests.
- [ ] Focused tests for the verifier, rubric, profile, density, grade records, and consistency analysis remain.
- [ ] Thin command tests continue protecting arguments, important messages, and exit meanings.
- [ ] The end-to-end chain exercises the deep lifecycle from redacted submissions through rehydrated drafts.
- [ ] The invariant test proves that no grading module imports or loads the token map and that identity-bearing output stays behind the renderer seam.
- [ ] The full test suite passes after the contraction.
