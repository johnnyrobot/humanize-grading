# How calibration works

Calibration learns two things a rubric cannot record: where this instructor
actually lands against their own scale, and how they write.

It runs locally. It must refuse when the instructor answers "cloud" at the
gate, because calibration reads graded work that still carries names.

## What it needs

- The rubric.
- Four to ten pieces of work the instructor already graded, each holding the
  work, their comments, and their score. Three to eight become calibration
  samples. The rest are held back.
- The class roster, so past students' names can be scrubbed out.

Fewer than three calibration samples cannot show a pattern. More than eight
adds little and costs a long read. Hold back one piece when there are fewer
than six in total, and two otherwise. One holdout cannot show agreement.

## What it produces

The profile at `.grading/profiles/<course>.md`, a calibration receipt at
`.grading/calibration/<course>.receipt.json`, and an approval beside it.

**The holdout report is the deliverable, not the profile.** A bad profile
must surface in the first minute rather than on the fortieth essay. Grading
refuses until the receipt exists, matches the current profile and rubric, and
the instructor has approved the profile with the exact phrase.

## Handoff to assignment briefing

Calibration sets reusable course standards: rubric interpretation, scoring
habits, feedback structure, voice, and phrases to avoid. It does not decide how
those standards apply to every future assignment.

Before each grading batch, the assignment brief confirms how the current
profile applies to that assignment. It records assignment-specific priorities,
required evidence, expected mistakes, feedback behavior, boundaries, and host
mode. These notes may clarify the rubric but must not change its points or add
criteria. Read `assignment-brief.md` before preparing one.

When the rubric or profile changes, approve the assignment brief again before
grading and verify the whole batch again.

## The nine sections

| Section | Holds |
| --- | --- |
| Rubric fingerprint | The whole-rubric digest plus one digest per criterion, so scale/protocol drift and named criterion drift are both reported |
| Rubric, normalized | Criteria, levels, weights, point values |
| Weighting in practice | The gap between the rubric and the instructor's behaviour |
| Severity | Where the instructor lands against the rubric's language; score spread |
| Feedback structure | Opening, praise-to-critique ratio, closing, typical length |
| Voice | Sentence length, person, questions or directives, habitual hedges |
| Comment bank | The instructor's own sentences, verbatim |
| Never-use list | Phrases the instructor does not say |
| Instructor answers | The interview answers, in the instructor's words, so they can read and correct what the agent believed |

The header may also set `**Point tolerance**` (default 5) and
`**Length tolerance**` (default 2.0), the limits the holdouts are measured
against. `**Samples**` must equal the packet's sample count.

The weighting section carries most of the value. A rubric may weight citation
errors and thesis errors equally while the samples show the instructor
forgives citations and punishes a weak thesis. No rubric records that, and it
is most of what makes feedback sound like a specific person.

## Two rules about what the profile may hold

**No student essay text.** The voice lives in the comments. The essays are
not needed to learn it, and the profile is read during cloud runs.

**No student name.** Instructors write "Maya, this is exceptional work". Run
every comment through name and structured-identity scrubbing first, so the bank reads "[STUDENT],
this is exceptional work". Use the literal `[STUDENT]`, never a
`STUDENT_01`-shaped token: a token leaking out of a profile would be
reattached at rehydration to whichever student holds it in the current batch.

**No inline brackets from the submission.** Bracket syntax inside student prose
does not prove that an instructor wrote it. Calibration uses only the single
instructor feedback section. More than one feedback heading is ambiguous and
must be resolved locally before the sample can be used.

## Take structure from the rubric

Where the rubric states its feedback protocol, use it. The PHIL 210 rubric
names inline annotations, a Bottom Line, What Worked, What to Work On, and a
sign-off. Read that instead of inferring a structure the instructor already
wrote down. Infer only what the rubric leaves unsaid.

## The interview

The five questions:

1. Every sample is B-range. Where does an A begin, and what does one do that
   a B does not?
2. Which criterion do you forgive, and which do you never forgive?
3. When work is failing, do you say so plainly or soften it?
4. Is there anything you would never write to a student?
5. How long should feedback be?

Always ask the first two. Samples show habits, not range, so no sample can
answer where an A begins or which criterion is never forgiven. Ask the other
three only when the samples cannot answer them. Record every answer under
`## Instructor answers`, in the instructor's words and without any name.

## Holdout validation

Every held-back sample is graded blind. `calibrate.py inputs --holdout-out`
writes each holdout's header and essay to an owner-only folder with no
feedback and no bracketed comment, because the instructor's inline comments
live in brackets and cannot be told from a student's by syntax. Grade that
folder as a holdout batch:

```bash
python3 scripts/grade.py brief prepare \
  --batch holdout-<course> --course <course> --mode local --purpose holdout
```

A holdout brief must be local. Its batch skips the profile-approval gate, has
no pilot stop, ends at `report-holdout`, and can never publish.

Then measure, with one `--record`, `--draft`, and `--sample` per holdout:

```bash
python3 scripts/calibrate.py holdout \
  --course <course> --rubric <rubric.md> \
  --packet .grading/calibration-inputs.json \
  --record <drafts>/STUDENT_01.json --draft <drafts>/STUDENT_01.md \
  --sample <graded>/<first-holdout.md> \
  --record <drafts>/STUDENT_02.json --draft <drafts>/STUDENT_02.md \
  --sample <graded>/<second-holdout.md>
```

The instructor pairs each token with its sample by reading the token map
locally. For each holdout the report shows:

- The instructor's score against the generated score.
- The letter band, matched or not.
- Which of the instructor's own sentences the generated feedback reused. A
  phrase both sides quoted from the student's essay does not count.
- The length of the generated feedback against the instructor's.

Then an aggregate: mean and largest score gap, letter match rate, how many
holdouts carried the instructor's phrasing, and the length ratio range. The
command writes an identity-free receipt with counts and digests only. Exit 0
means every holdout stayed inside the profile's tolerances. Exit 15 means a
gap; the receipt records it.

## Approval

After the report, wait for this exact message:

```text
Approve calibration profile for <course>
```

Then run:

```bash
python3 scripts/calibrate.py approve \
  --course <course> --rubric <rubric.md> \
  --approval "Approve calibration profile for <course>"
python3 scripts/calibrate.py check --course <course> --rubric <rubric.md>
```

On a gap, the instructor may edit the profile and run the holdouts again, or
accept the gap. Accepting needs `--accept-gap --reason "<their words>"`, and
the reason is recorded. Approval binds the profile, rubric, and receipt by
digest. It is workflow evidence, not a signature.

## Recalibration

Profiles are plain Markdown, dated, and editable by hand. Any edit makes the
receipt and approval stale, so grading refuses until the holdouts run and the
instructor approves again. Add graded work over time and run calibration
again. `grade.py profile` warns when a profile is more than 120 days old or
has five or more approved briefs behind it. When the rubric changes, it names
the drifted criteria and refuses before grading.
