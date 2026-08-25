# How calibration works

Calibration learns two things a rubric cannot record: where this instructor
actually lands against their own scale, and how they write.

It runs locally. It must refuse when the instructor answers "cloud" at the
gate, because calibration reads graded work that still carries names.

## What it needs

- The rubric.
- Three to eight pieces of work the instructor already graded, each holding
  the work, their comments, and their score.
- The class roster, so past students' names can be scrubbed out.

Fewer than three samples cannot show a pattern. More than eight adds little
and costs a long read.

## What it produces

The profile at `.grading/profiles/<course>.md`, and a holdout report.

**The report is the deliverable, not the profile.** A bad profile must
surface in the first minute rather than on the fortieth essay.

## The seven sections

| Section | Holds |
| --- | --- |
| Rubric fingerprint | One digest per criterion, so drift can be named |
| Rubric, normalized | Criteria, levels, weights, point values |
| Weighting in practice | The gap between the rubric and the instructor's behaviour |
| Severity | Where the instructor lands against the rubric's language; score spread |
| Feedback structure | Opening, praise-to-critique ratio, closing, typical length |
| Voice | Sentence length, person, questions or directives, habitual hedges |
| Comment bank | The instructor's own sentences, verbatim |
| Never-use list | Phrases the instructor does not say |

The weighting section carries most of the value. A rubric may weight citation
errors and thesis errors equally while the samples show the instructor
forgives citations and punishes a weak thesis. No rubric records that, and it
is most of what makes feedback sound like a specific person.

## Two rules about what the profile may hold

**No student essay text.** The voice lives in the comments. The essays are
not needed to learn it, and the profile is read during cloud runs.

**No student name.** Instructors write "Maya, this is exceptional work". Run
every comment through name scrubbing first, so the bank reads "[STUDENT],
this is exceptional work". Use the literal `[STUDENT]`, never a
`STUDENT_01`-shaped token: a token leaking out of a profile would be
reattached at rehydration to whichever student holds it in the current batch.

## Take structure from the rubric

Where the rubric states its feedback protocol, use it. The PHIL 210 rubric
names inline annotations, a Bottom Line, What Worked, What to Work On, and a
sign-off. Read that instead of inferring a structure the instructor already
wrote down. Infer only what the rubric leaves unsaid.

## The interview

Samples show habits. They do not show range. Ask only what the samples cannot
answer:

1. Every sample is B-range. Where does an A begin, and what does one do that
   a B does not?
2. Which criterion do you forgive, and which do you never forgive?
3. When work is failing, do you say so plainly or soften it?
4. Is there anything you would never write to a student?
5. How long should feedback be?

Skip any question the samples already answer. An interview that re-asks what
is visible in the work wastes the instructor's time and teaches nothing.

## Holdout validation

Hold one graded sample back. Grade it blind against the finished profile.
Report the delta:

- The instructor's score against the generated score.
- The letter band, matched or not.
- Which of the instructor's own sentences the generated feedback reused.
- The length of the generated feedback against the instructor's.

`python3 scripts/calibrate.py holdout` writes that report.

## Recalibration

Profiles are plain Markdown, dated, and editable by hand. Add graded work
over time and run calibration again. When the rubric changes, `grade.py
profile` names the drifted criteria and asks before grading.
