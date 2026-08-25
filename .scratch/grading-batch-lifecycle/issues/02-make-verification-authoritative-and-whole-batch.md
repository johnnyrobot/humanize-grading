# 02 — Make verification authoritative and whole-batch

**What to build:** Make verification use the grading-batch lifecycle to pair every draft and grade record with its authoritative redacted submission, aggregate all blocking findings, and commit located evidence offsets only when the entire grading batch passes.

**Blocked by:** 01 — Establish the grading-batch lifecycle seam

**Status:** ready-for-agent

- [ ] Verification starts membership and source resolution from the configured redacted submissions.
- [ ] Grade-record source metadata is normalized and checked but never selects a file to open.
- [ ] Filename token, record token, batch, course, rubric, and profile mismatches produce blocking findings.
- [ ] Equivalent normalized rubric and profile locations do not produce false mismatches.
- [ ] Malformed or unreadable artifacts do not stop later submissions from being inspected.
- [ ] Every blocking finding that can be established safely is returned in one run.
- [ ] Located evidence offsets are staged and committed only when every draft passes.
- [ ] A failing grading batch leaves every grade record unchanged.
- [ ] Existing verification, missing-profile, rubric-drift, malformed-input, and success exit meanings remain intact.
- [ ] The grading modules never load the token map or identity-bearing submission text.
