---
status: accepted
date: 2026-08-24
---

# Verification receipts gate atomic rehydration

A grading batch may be rehydrated only when a versioned, identity-free verification receipt matches the exact redacted submissions, drafts, grade records, rubric, calibration profile, and pattern reference that passed verification. Rehydration validates the whole batch before publishing anything because command order written in instructions cannot enforce this safety rule.

The grading-batch interface exposes publication only through a verified-batch capability created by successful persisted verification or by revalidating a receipt. Identity substitution remains in a renderer adapter outside `scripts/grading/`; the grading-batch module controls eligibility and staging without loading the token map or receiving identity-bearing text.

## Considered options

- **Rely on command order:** Rejected because a user or agent can run rehydration without first running verification.
- **Store status in each grade record:** Rejected because grade records begin as model-written input, and per-record status does not prove that the whole batch passed together.
- **Publish the drafts that pass:** Rejected because a partly published batch can be mistaken for a complete one.

## Consequences

- Any change to a receipt-bound artifact requires verification again.
- A batch created before receipts exist must be verified before rehydration.
- A non-empty publication destination is refused unless an explicit safe replacement is requested.
- The token map remains outside `scripts/grading/`; local rehydration crosses that seam without exposing real identities to grading modules.
- Callers cannot request publication from an ordinary or unverified grading batch.
- Receipt locators are normalized relative paths where possible, so rehydration can validate every bound artifact without trusting grade-record paths or requiring new command arguments.
- Each publication carries an identity-free marker with the receipt digest and exact output hashes. Safe replacement refuses modified, unrelated, or unrecognized output.
- Portable forced replacement stages the complete new publication, moves the recognized old publication to a backup, moves the staged publication into place, and restores or recovers the backup on failure. The target may be briefly absent, but it is never partly populated with a new batch.
