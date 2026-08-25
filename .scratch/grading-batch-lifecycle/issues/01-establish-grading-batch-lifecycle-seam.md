# 01 — Establish the grading-batch lifecycle seam

**What to build:** Make status run through a deep grading-batch interface that derives progress from authoritative redacted submissions and reports incomplete or orphaned artifacts as blocking findings. Keep the legacy survey available until its remaining callers migrate.

**Blocked by:** None — can start immediately

**Status:** ready-for-agent

- [ ] The grading-batch interface derives pending and drafted progress from current artifacts.
- [ ] Progress remains separate from blocking findings.
- [ ] Incomplete draft and grade-record pairs produce blocking findings without inventing a new progress stage.
- [ ] Drafts or grade records without a redacted submission produce orphan findings.
- [ ] Status inspects the whole grading batch and reports every finding it can establish.
- [ ] The status command keeps its current arguments, output meaning, and successful exit behavior.
- [ ] Interface-level tests use temporary filesystem trees and do not test private helpers.
- [ ] Existing production callers that are not part of this ticket continue to work.
