# Humanizer grading

Humanizer grading prepares student work for identity-safe grading, checks generated grades, and returns draft feedback to the instructor for review.

## Language

**Redacted submission**:
Student work with identifying content replaced by stable tokens. It is the only form of a submission used during grading and verification.
_Avoid_: Anonymized submission, raw submission

**Grading batch**:
A set of redacted submissions for one assignment run, together with their draft feedback and grade records.
_Avoid_: Job, run, folder

**Verified draft**:
Draft feedback and its grade record after every required check has passed against the redacted submission, rubric, and calibration profile.
_Avoid_: Approved feedback, final grade

**Verification receipt**:
An identity-free record that a grading batch passed every required check for the exact submissions, drafts, grade records, rubric, profile, and pattern reference used.
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
