# 02 — Make verification authoritative and whole-batch

**What to build:** Make verification use the grading-batch lifecycle to pair every draft and grade record with its authoritative redacted submission, aggregate all blocking findings, and commit located evidence offsets only when the entire grading batch passes.

**Blocked by:** 01 — Establish the grading-batch lifecycle seam

**Status:** resolved

- [x] Verification starts membership and source resolution from the configured redacted submissions.
- [x] Grade-record source metadata is normalized and checked but never selects a file to open.
- [x] Filename token, record token, batch, course, rubric, and profile mismatches produce blocking findings.
- [x] Equivalent normalized rubric and profile locations do not produce false mismatches.
- [x] Malformed or unreadable artifacts do not stop later submissions from being inspected.
- [x] Every blocking finding that can be established safely is returned in one run.
- [x] Located evidence offsets are staged and committed only when every draft passes.
- [x] A failing grading batch leaves every grade record unchanged.
- [x] Existing verification, missing-profile, rubric-drift, malformed-input, and success exit meanings remain intact.
- [x] The grading modules never load the token map or identity-bearing submission text.

## Answer

Verification now runs through the grading-batch lifecycle, uses only configured redacted submissions, checks all grading-context metadata, aggregates structural and draft findings, and persists located offsets only after the whole batch passes. Malformed records, unreadable artifacts, and malformed verification evidence become findings without stopping later submissions.

Verification: 336 tests passed, bytecode compilation passed, the package validator passed, and the final Standards and Spec reviews reported no findings.
