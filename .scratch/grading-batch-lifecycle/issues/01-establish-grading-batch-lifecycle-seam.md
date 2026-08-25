# 01 — Establish the grading-batch lifecycle seam

**What to build:** Make status run through a deep grading-batch interface that derives progress from authoritative redacted submissions and reports incomplete or orphaned artifacts as blocking findings. Keep the legacy survey available until its remaining callers migrate.

**Blocked by:** None — can start immediately

**Status:** resolved

- [x] The grading-batch interface derives pending and drafted progress from current artifacts.
- [x] Progress remains separate from blocking findings.
- [x] Incomplete draft and grade-record pairs produce blocking findings without inventing a new progress stage.
- [x] Drafts or grade records without a redacted submission produce orphan findings.
- [x] Status inspects the whole grading batch and reports every finding it can establish.
- [x] The status command keeps its current arguments, output meaning, and successful exit behavior.
- [x] Interface-level tests use temporary filesystem trees and do not test private helpers.
- [x] Existing production callers that are not part of this ticket continue to work.

## Answer

Implemented the deep grading-batch status seam while keeping the legacy survey for later caller migrations. Status now separates pending or drafted progress from incomplete, orphaned, and duplicate-submission findings, and the command preserves its existing interface and successful exit behavior.

Verification: 310 tests passed, bytecode compilation passed, the package validator passed, and the final Standards and Spec reviews found no remaining behavioral or architectural issues after corrections.
