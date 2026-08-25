# 07 — Contract the legacy batch interface

**What to build:** Complete the migration to the deep grading-batch lifecycle by removing the old shallow survey interface and redundant tests after every production caller uses the new seam.

**Blocked by:** 02 — Make verification authoritative and whole-batch; 04 — Run consistency through the lifecycle seam; 05 — Publish through the verified-batch capability

**Status:** resolved

- [x] Status, verification, consistency, and publication callers use the deep lifecycle interface.
- [x] No production caller reconstructs batch membership, pairing, progress, or receipt eligibility independently.
- [x] The old survey interface and obsolete progress constants are removed once no caller needs them.
- [x] Tests that only pin the old survey data shape are removed or replaced with lifecycle behavior tests.
- [x] Focused tests for the verifier, rubric, profile, density, grade records, and consistency analysis remain.
- [x] Thin command tests continue protecting arguments, important messages, and exit meanings.
- [x] The end-to-end chain exercises the deep lifecycle from redacted submissions through rehydrated drafts.
- [x] The invariant test proves that no grading module imports or loads the token map and that identity-bearing output stays behind the renderer seam.
- [x] The full test suite passes after the contraction.

## Answer

All production commands and the end-to-end chain now use the deep grading-batch lifecycle for status, verification, consistency, receipt eligibility, and publication. The temporary survey interface, mixed state object, summary helper, obsolete constants, and redundant tests are removed. Status owns display states and counts, while focused module and CLI tests remain.

Verification: 383 tests passed, bytecode compilation passed, the package validator passed, and the final Standards and Spec reviews reported no findings.
