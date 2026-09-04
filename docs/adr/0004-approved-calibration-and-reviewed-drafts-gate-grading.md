---
status: accepted
date: 2026-09-03
---

# Approved calibration and reviewed drafts gate grading

A course may grade only with a calibration profile that holdouts measured and
the instructor approved. A grading batch may verify only after the instructor
approved the pilot drafts, or every draft under per-draft review. It may
publish only after the instructor acknowledged the consistency flags for the
exact verification receipt. The state machine derives each stop from
identity-free files, so an agent that skips a stop is refused by verification
or publication rather than by instructions alone.

The bundled synthetic demo is the one path with no stops. Its brief carries
review cadence `none`, which the brief validator accepts only for the demo
batch name, and its calibration approval is marked synthetic and is valid only
beside the demo marker in the same state root. The synthetic conformance trial
uses the same synthetic authorization.

## Considered options

- **Keep the holdout as a report:** Rejected because a report nobody reads
  gates nothing. The receipt records the measurement; the approval binds it.
- **Review only at publication:** Rejected because a forty-essay batch would
  be graded before the instructor saw one draft. The pilot stop surfaces a bad
  profile or brief on the second essay.
- **Bind review state into the verification receipt:** Rejected because
  publication already re-runs verification, which re-checks the review record.
  The receipt schema stays at 2.
- **A `none` cadence for any batch:** Rejected because it would make
  unattended grading one flag away. Only the demo batch may use it, and only
  with the demo marker present.
- **One holdout with a hard tolerance:** Rejected because one paper cannot
  show agreement. Six or more graded pieces require two holdouts, and the
  receipt reports the aggregate.

## Consequences

- Grading a course for the first time takes one holdout batch and one
  approval. Editing the profile or rubric repeats both.
- Each grading batch stops at least three times: the brief, the pilot, and the
  consistency flags. Redaction review remains a fourth stop.
- A hand-written profile never grades. A batch drafted past the pilot without
  approval cannot verify until the extra drafts are removed.
- Assignment brief schema 1 files must be prepared and approved again.
- Every phrase remains workflow evidence, not authentication, as ADR-0002
  states.
