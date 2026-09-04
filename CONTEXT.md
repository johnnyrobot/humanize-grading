# Humanizer grading

Humanizer grading prepares student work for identity-safe grading, checks generated grades, and returns draft feedback to the instructor for review.

## Language

**Redacted submission**:
Student work with identifying content replaced by stable tokens. It is the only form of a submission used during grading and verification.
_Avoid_: Anonymized submission, raw submission

**Grading batch**:
A set of redacted submissions for one assignment, together with their draft feedback and grade records.
_Avoid_: Job, run, folder

**Assignment brief**:
An identity-free, instructor-approved record of how the rubric and calibration
profile apply to one grading batch. Verification binds the exact approved file.
_Avoid_: Prompt, config, approval receipt

**Draft pair**:
The feedback Markdown and `grade-record/1` JSON for one redacted submission.
Both files must exist before the submission is drafted.
_Avoid_: Output files, grade bundle

**Next action**:
The one legal workflow stage derived from current identity-free grading state.
It may include one command or one ordered command group.
_Avoid_: Suggestion, plan

**Conformance trial**:
A synthetic agent run scored from observable commands, elapsed time, artifacts,
lifecycle exits, and available read traces.
_Avoid_: Benchmark, model grade

**Verified draft**:
Draft feedback and its grade record after every required check has passed
against the approved assignment brief, redacted submission, rubric, and
calibration profile.
_Avoid_: Approved feedback, final grade

**Verification receipt**:
A schema 2 identity-free record that a grading batch passed every required
check for the exact approved assignment brief, redacted submissions, drafts,
grade records, rubric, profile, redaction manifest, and pattern reference used.
_Avoid_: Status flag, instructor approval

**Rehydrated draft**:
Draft feedback whose tokens have been replaced with real identities on the instructor's machine. It remains a draft until the instructor signs off.
_Avoid_: Final feedback, completed grade

**Blocking finding**:
A failed requirement that prevents a grading batch from advancing to verification or rehydration.
_Avoid_: Consistency flag, warning

**Consistency flag**:
An advisory signal that asks the instructor to compare work across a grading batch. It never blocks the batch.
_Avoid_: Verification failure, blocking finding

**Calibration packet**:
The identity-free JSON that `calibrate.py inputs` writes from graded samples: the instructor's scrubbed comments, scores, and the rubric fingerprint. It carries no essay text and no name.
_Avoid_: Training set, sample dump

**Holdout batch**:
A grading batch whose brief has purpose `holdout`. It grades blind copies of held-back graded samples so the profile can be measured, and it can never publish.
_Avoid_: Test batch, calibration run

**Calibration receipt**:
An identity-free record that holdouts measured one exact profile against one exact rubric: digests, counts, the per-holdout gaps, the aggregate, and whether it passed.
_Avoid_: Holdout report, score card

**Profile approval**:
The instructor's exact-phrase approval of a calibration profile, bound to the profile, rubric, and receipt digests. A gap may be accepted with a recorded reason.
_Avoid_: Calibration sign-off, profile lock

**Review cadence**:
How drafts reach the instructor before verification: `pilot` stops after the first drafts, `every` stops after each draft, and `none` is reserved for the synthetic demo.
_Avoid_: Review mode, approval level

**Pilot review**:
The instructor's exact-phrase approval of the first drafts of a batch, bound to those drafts' digests, before the batch continues.
_Avoid_: Spot check, sample review

**Draft review record**:
`_review.json` in the drafts folder. It holds pilot or per-draft approvals bound to feedback and grade digests. Verification refuses a changed or unapproved draft.
_Avoid_: Approval log, sign-off file

**Consistency acknowledgement**:
`_consistency.json` in the drafts folder. It records that the instructor saw the consistency flags for the exact verification receipt. Publication refuses without it.
_Avoid_: Consistency approval, flag dismissal
