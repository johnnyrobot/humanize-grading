# Supervised calibration and draft review

**Date:** 2026-09-03
**Status:** approved for implementation
**Version:** 3.6.0

## Why

The package says every grade is a draft and the instructor signs off. The
state machine enforces that in only two places: assignment-brief approval and
redaction review. Three gaps remain.

1. The calibration profile is the least-checked input. `grade.py next` accepts
   any Markdown file with eight headings and a matching rubric fingerprint. It
   never checks that a calibration packet was built, how many graded samples
   fed it, whether a holdout ran, or what the holdout gap was.
2. Drafting runs from redaction review to a rehydrated folder with no
   instructor stop. The instructor sees finished feedback for the whole batch
   at once.
3. Consistency and publication return as one command group. The agent
   publishes before the instructor has seen a flag.

This design closes all three. The instructor must supply graded examples,
measure the profile against held-back examples, approve the profile, review a
pilot of drafts before the batch continues, and acknowledge consistency flags
before publication.

## Decisions already made

- Pilot-then-continue is the default review cadence. Per-draft review is an
  option. Neither can be turned off for a real batch.
- The bundled synthetic demo keeps its one-turn, self-approving flow. It is
  the only path that skips these stops, and only for the exact synthetic
  corpus.
- Calibration uses several holdouts and reports an aggregate agreement
  measure.

## Vocabulary

| Term | Meaning |
| --- | --- |
| Calibration packet | The identity-free JSON that `calibrate.py inputs` writes from graded samples |
| Holdout batch | A grading batch whose brief has purpose `holdout`; it grades held-back samples blind and can never publish |
| Calibration receipt | An identity-free record of the holdout agreement measurement for one profile and rubric |
| Profile approval | The instructor's exact-phrase approval bound to the profile, rubric, and receipt digests |
| Review cadence | `pilot`, `every`, or `none`; how drafts reach the instructor before verification |
| Pilot review | The instructor's approval of the first drafts before the batch continues |
| Draft review record | `_review.json` in the drafts folder; holds pilot or per-draft approvals bound to draft digests |
| Consistency acknowledgement | `_consistency.json` in the drafts folder; records that the instructor saw the flags for the exact receipt |

## Part 1: Calibration becomes a gate

### Packet and holdouts

`calibrate.py inputs` accepts `--holdout` more than once. Rules:

- Calibration samples remain at least three and at most eight.
- Holdouts are at least one. When calibration samples plus holdouts number
  six or more, holdouts must be at least two, so calibration never drops
  below four samples to make room.
- The packet's `holdout` object becomes `{"count": N, "labels": [...]}`.
  Labels are positional, never filenames.
- `--holdout-out <dir>` writes each holdout's student half, header and essay
  only, to an owner-only directory under its original filename. That directory
  is the submissions folder for the holdout batch. The instructor feedback
  never enters it, so the model grades blind.

### Holdout batch

The holdout batch is an ordinary grading batch with a brief whose purpose is
`holdout`. `next` treats it specially:

- The calibration approval gate is skipped. A profile under test cannot be
  approved yet.
- Review cadence is `none`. The holdout report is the review.
- After verification the terminal action is `report-holdout`. It has no
  commands. Its message names the `calibrate.py holdout` command shape.
- Consistency and publication never return. `rehydrate.py` refuses a batch
  whose brief purpose is `holdout`.

A holdout brief must use local mode. Calibration reads past students' names.

### Receipt

`calibrate.py holdout` becomes:

```bash
python3 scripts/calibrate.py holdout \
  --course <course> --rubric <rubric.md> --packet <calibration-inputs.json> \
  --record <a.json> --draft <a.md> --sample <a-graded.md> \
  --record <b.json> --draft <b.md> --sample <b-graded.md> \
  [--root .grading] [--json]
```

It requires as many record, draft, and sample triples as the packet's holdout
count. It checks that the packet's rubric digest equals the profile's and the
current rubric's, and that the profile's `Samples` header equals the packet's
sample count. It writes `.grading/calibration/<course>.receipt.json`:

```json
{
  "schema": "calibration-receipt/1",
  "course": "phil210",
  "hash": "sha256",
  "profile_sha256": "...",
  "rubric_sha256": "...",
  "packet_sha256": "...",
  "samples": 4,
  "required_holdouts": 2,
  "holdouts": [
    {
      "token": "STUDENT_01",
      "generated_total": 86,
      "instructor_total": 83,
      "points": 3,
      "generated_letter": "B",
      "instructor_letter": "B",
      "letter_match": true,
      "shared_phrases": 4,
      "generated_words": 250,
      "instructor_words": 230,
      "length_ratio": 1.09,
      "findings": []
    }
  ],
  "aggregate": {
    "holdouts": 2,
    "mean_abs_points": 3.5,
    "max_abs_points": 5,
    "letter_match_rate": 1.0,
    "phrase_carry_rate": 1.0,
    "length_ratio_low": 0.9,
    "length_ratio_high": 1.3
  },
  "tolerances": {"points": 5, "length_ratio": 2.0},
  "passed": true,
  "created_at": "2026-09-03T18:00:00Z"
}
```

The receipt holds no student name, no essay text, and no instructor sentence.
Shared phrases appear only as counts. The rendered report on stdout still
lists the phrases for the instructor.

`passed` is true when every holdout has no findings and the holdout count
meets `required_holdouts`. Exit codes: 0 passed, 15 gap, 13 malformed. The
receipt is written in both the passed and gap cases.

### Tolerances

The profile header accepts two optional lines:

```markdown
**Point tolerance**: 5
**Length tolerance**: 2.0
```

Defaults are the current constants. An unparseable value is a profile error.

### Profile changes

- `## Instructor answers` is a required section. It holds the calibration
  interview answers in the instructor's words, identity-free. The agent always
  asks the two range questions from `references/calibration.md` and records
  every answer here.
- `**Samples**` is checked against the packet at holdout time.

### Approval

```bash
python3 scripts/calibrate.py approve \
  --course <course> --rubric <rubric.md> \
  --approval "Approve calibration profile for <course>" \
  [--accept-gap --reason "<instructor's words>"] [--root .grading]
python3 scripts/calibrate.py check --course <course> --rubric <rubric.md>
```

`approve` requires a receipt whose profile and rubric digests match the
current files and whose sample count is at least three. When the receipt did
not pass, `--accept-gap` and a non-empty `--reason` are both required, and
both are recorded. It writes `.grading/calibration/<course>.approval.json`:

```json
{
  "schema": "calibration-approval/1",
  "course": "phil210",
  "hash": "sha256",
  "profile_sha256": "...",
  "rubric_sha256": "...",
  "receipt_sha256": "...",
  "approval": "Approve calibration profile for phil210",
  "approved_at": "2026-09-03T18:05:00Z",
  "gap_accepted": false,
  "reason": "",
  "synthetic": false
}
```

`check` and `grade.py profile` exit 14 when the approval is missing, the
digests differ from the current profile, rubric, or receipt, or the receipt
is malformed. Exit 14 is new in both entry points.

`grade.py profile` also prints two warnings that do not change the exit code:
when the profile's calibration date is more than 120 days old, and when five
or more approved briefs under `.grading/assignments/` are bound to the
current profile digest. Both say to add graded work and calibrate again.

### Workflow gate

After the rubric-drift check, `next` loads any existing brief for the batch.
If it loads and its purpose is `holdout`, the gate is skipped. Otherwise:

| State | Action | Commands |
| --- | --- | --- |
| No receipt, or receipt digests differ from the current profile or rubric | `validate-holdout` | none; message names the packet, holdout brief, and `calibrate.py holdout` steps |
| Receipt malformed | `repair-calibration` (exit 12) | none |
| Receipt failed and no gap-accepting approval | `review-holdout` | none; message says to show the report, then either edit the profile and run holdouts again or accept the gap with a reason |
| Receipt passed and approval missing or stale | `approve-profile` | the `calibrate.py approve` command without gap flags |
| Approval current | continue to briefing | |

### Synthetic demo

`prepare_demo` writes a synthetic approval with `synthetic: true`, an empty
receipt digest, and the phrase `Approve calibration profile for phil210`.
`check_calibration` accepts a synthetic approval only when `demo.json` in the
same state root records `authorization: synthetic-demo`. The demo writes that
marker before it derives the first action.

## Part 2: Instructor review inside drafting

### Brief schema 2

`grading-assignment-brief/2` adds three metadata keys and three generated
Boundaries lines that must match them, the same way `Host mode` binds `mode`:

| Key | Values | Default | Boundaries line |
| --- | --- | --- | --- |
| `purpose` | `grading`, `holdout` | `grading` | `Purpose: grading` |
| `review` | `pilot`, `every`, `none` | `pilot` | `Review cadence: pilot` |
| `pilot` | integer 1 to 50 | `2` | `Pilot size: 2` |

Rules:

- `purpose: holdout` requires `mode: local` and `review: none`.
- `review: none` with `purpose: grading` is valid only when the batch is the
  demo batch name. The CLI never offers `none`; `brief prepare --purpose
  holdout` sets it.
- `next` additionally requires the `demo.json` synthetic marker for a
  `review: none` grading brief. Without it the action is `repair-brief`.

CLI: `brief prepare --purpose {grading,holdout} --review {pilot,every}
--pilot-size N`. Schema 1 briefs fail to load with a message to prepare and
approve a new brief. Existing receipts bound to schema 1 briefs therefore
fail `check_brief` and need verification again.

The briefing interview gains an eighth question group: review cadence and
pilot size. The agent records the answer through the prepare flags.

### Draft review record

`scripts/grading/review.py` owns `_review.json` in the drafts folder:

```json
{
  "schema": "grading-draft-review/1",
  "batch": "essay-2",
  "course": "phil210",
  "cadence": "pilot",
  "pilot": 2,
  "approvals": [
    {
      "scope": "pilot",
      "tokens": ["STUDENT_01", "STUDENT_02"],
      "artifacts": [
        {"token": "STUDENT_01", "feedback_sha256": "...", "grade_sha256": "..."},
        {"token": "STUDENT_02", "feedback_sha256": "...", "grade_sha256": "..."}
      ],
      "approval": "Approve pilot drafts for essay-2",
      "approved_at": "2026-09-03T18:30:00Z"
    }
  ]
}
```

`feedback_sha256` is the digest of the feedback Markdown text.
`grade_sha256` is `GradeRecord.review_digest()`: the digest of the record's
JSON with every justification offset removed, so verification writing offsets
does not make an approval stale. Any other change to either file does.

Phrases:

- Pilot: `Approve pilot drafts for <batch>`
- Per draft: `Approve draft <TOKEN> for <batch>`

Command:

```bash
python3 scripts/grade.py review approve \
  --batch <batch> --course <course> --rubric <rubric.md> \
  --redacted <redacted/> --drafts <drafts/> --patterns <patterns.md> \
  --scope pilot --approval "Approve pilot drafts for <batch>" [--root]
python3 scripts/grade.py review approve ... --scope draft --token STUDENT_03 \
  --approval "Approve draft STUDENT_03 for <batch>"
python3 scripts/grade.py review check --batch <batch> --course <course> ...
```

`review approve` refuses unless the brief cadence matches the scope, the
tokens are drafted, and a dry verification reports no blocking finding for
them. For pilot scope with no prior pilot approval, the drafted count must
equal `min(pilot, submissions)`. Re-approval after a change keeps the recorded
token set. A pilot approval covers the batch: drafts after the pilot are
reviewed at publication, as today.

### Workflow during drafting

For a grading brief, after redaction checks:

1. If any submission is drafted, run a dry verification. Whole-batch findings
   and non-template token findings return `repair-verification`, as today.
   Template findings return `complete-draft` for the first such token.
2. Cadence `pilot` with no current pilot approval: fewer drafted than the
   pilot size continues to `prepare-record`; exactly the pilot size returns
   `review-pilot` with the tokens and the approve command; more returns
   `repair-review` (exit 12) naming the extra tokens. A stale pilot approval
   returns `review-pilot` for the recorded tokens.
3. Cadence `every`: one drafted token without a current approval returns
   `review-draft` for it; more than one returns `repair-review`.
4. Then `prepare-record` for the first pending token, or `verify` when none
   remain.

`review-pilot` and `review-draft` exit 0. The agent shows the tokenized drafts
in the conversation or names their local paths, waits for the exact phrase,
runs the returned command, and calls `next` again.

### Verification

`GradingBatch.verify` adds review findings after the per-draft checks:

| Code | When |
| --- | --- |
| `malformed-draft-review` | `_review.json` unreadable, wrong schema, batch, or course |
| `draft-review-required` | cadence `pilot` with no pilot approval, or cadence `every` with an unapproved drafted token |
| `draft-review-stale` | an approved token's feedback or grade digest changed |
| `pilot-size-mismatch` | the pilot approval's token count is not `min(pilot, submissions)` |

Cadence `none` adds nothing. Because `VerifiedBatch.resume` re-runs
verification before publication, these findings block publication too, and
the receipt schema stays at 2.

## Part 3: Consistency before publication

`grade.py consistency` gains `--acknowledge "<phrase>"`. With the flag it
computes and prints the flags as before, requires a valid current receipt,
then writes `_consistency.json`:

```json
{
  "schema": "grading-consistency-acknowledgement/1",
  "batch": "essay-2",
  "course": "phil210",
  "receipt_sha256": "...",
  "flag_count": 1,
  "flags": [{"kind": "outlier", "message": "..."}],
  "approval": "Acknowledge consistency flags for essay-2",
  "acknowledged_at": "2026-09-03T19:00:00Z"
}
```

`next` after a current receipt, for cadence `pilot` or `every`:

| State | Action | Commands |
| --- | --- | --- |
| No acknowledgement, or its receipt digest differs | `review-consistency` | the consistency run command |
| Acknowledgement current | `publish` | the rehydrate command |

Cadence `none` keeps `consistency-and-publish` unchanged. `VerifiedBatch`
refuses `publish` when the brief cadence is not `none` and no current
acknowledgement exists, and always when the brief purpose is `holdout`.

## Part 4: Documentation and packaging

- `SKILL.md`: rewrite Calibrate for packets with several holdouts, the
  holdout batch, the receipt, the approval phrase, and the accept-gap path.
  Add the review cadence question to briefing. Add the pilot, per-draft, and
  consistency stops to Grade. Add error rows. Add "It does not grade
  unattended" to the does-not-do list, with the synthetic demo named as the
  one exception.
- `README.md`: add an instructor onboarding section, update the who-does-what
  table, add the will-not line, add the 3.6.0 note.
- `docs/using-the-grader.md`: rewrite Part 1 and extend Part 2; remove the
  stale "until Task 6" note; update the command table.
- `references/calibration.md`, `references/assignment-brief.md`: update.
- `references/demo.md`: note that the demo's brief uses cadence `none` and a
  synthetic profile approval.
- `CONTEXT.md`: add the vocabulary above.
- `docs/adr/0004-approved-calibration-and-reviewed-drafts-gate-grading.md`.
- `AGENTS.md`: list the new modules.
- Version 3.6.0 in `SKILL.md`, `skills/humanizer/SKILL.md`,
  `.claude-plugin/plugin.json`, and the README release notes.
- `PACKAGE_FILES.txt`: add new files.
- Conformance trial prompts: the synthetic agent approves the pilot and
  acknowledges consistency with the exact phrases, as it already approves the
  brief.

## What this design does not do

- It does not authenticate the instructor. Every phrase and command is
  workflow evidence, as ADR-0002 states.
- It does not change the redaction pipeline, the token map boundary, or the
  receipt schema.
- It does not add per-criterion instructor scores to graded samples. Samples
  still carry one total. Per-criterion agreement is future work.

## Testing

Every new gate gets a test that shows the gate closed, a test that shows it
open after the exact approval, and a test that shows it closed again after
the bound artifact changes. Security-relevant tests must fail when the guard
is removed; the review lessons in this repo's memory note two tests that
passed while proving nothing.
