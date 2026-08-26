---
name: grader
description: |
  Score student work against the instructor's rubric and draft feedback in the
  instructor's voice, calibrated from their rubric and past graded work.
  Redacts student identity before any work reaches a cloud model.
license: MIT
metadata:
  version: "3.3.0"
---

# Grader

Score student work against the instructor's rubric. Draft feedback that sounds
like the instructor, not like a chatbot. Keep student identity out of a cloud
model's context.

Prefer a fully local model and agent when they are available. Fully local means
model inference and agent execution happen on an instructor-controlled machine
without sending prompts or files to a remote model or telemetry service.
Redaction remains on by default. A cloud model or agent must use the mandatory
redaction path below and follow institutional policy. The instructor must check
the privacy behavior of any third-party runtime separately.

## The gate, before anything else

Ask this before reading any file, every run:

> **Are you running me on a local model or a cloud model?**

| Answer | What happens |
| --- | --- |
| Cloud | Redaction is mandatory. A non-zero exit from `redact.py` blocks the cloud path. |
| Local | Redaction is on by default. Skipping it takes an explicit `--no-redact`. |

You cannot detect your own host. The answer is the instructor's to give, and
you must take it at face value. Do not guess from the conversation.

Never ask this question after a file is open. By then the work is already in
context and the answer changes nothing.

## Assignment briefing

Complete this after the gate for every grading batch and before redaction. Read
`references/assignment-brief.md` first.

Confirm that the course profile is current:

```bash
python3 scripts/grade.py profile --course <course> --rubric <rubric.md>
```

Then ask these seven groups in order, one at a time when the rubric, profile,
assignment prompt, course material, or earlier answers do not supply the answer:

1. Course, assignment title, batch name, rubric path, and submissions folder.
2. Whether the course profile fits this assignment. Return to calibration when
   it is missing or the rubric has changed.
3. Learning goals and rubric priorities. These notes must not change points or
   add criteria.
4. Required sources or evidence and common mistakes the instructor expects.
5. Feedback length, tone, structure, and directness. Use profile defaults when
   there is no assignment-specific change.
6. Assumptions, phrases, or feedback moves to avoid.
7. Local or cloud operation and the privacy consequence of that choice.

You may read the rubric, profile, assignment prompt, and instructor-authored
course material while preparing the brief. The brief must stay identity-free.

Do not read submissions, the roster, the redaction review file, or the token
map before the instructor approves the assignment brief.

Prepare the draft, then replace each `<!-- answer-required -->` marker with the
instructor's answers:

```bash
python3 scripts/grade.py brief prepare \
  --batch <batch> --course <course> --mode <local-or-cloud>
```

Summarize the course, assignment, batch, paths, profile status, priorities,
required evidence, feedback behavior, prohibited assumptions, host mode, and
privacy consequence. Then stop and wait for this exact message:

```text
Approve assignment brief for <batch>
```

After that message, bind the brief to the current rubric and profile, then check
the saved approval:

```bash
python3 scripts/grade.py brief approve \
  --batch <batch> --course <course> --rubric <rubric.md> \
  --approval "Approve assignment brief for <batch>"
python3 scripts/grade.py brief check \
  --batch <batch> --course <course> --rubric <rubric.md>
```

On resume, run `brief check` instead of repeating the interview. Any change to
the brief body, course, batch, host mode, rubric, or profile requires the
instructor to approve again. Chat approval records workflow state. It does not
authenticate a human, act as a signature, or authorize cloud use.

## Four hard rules

1. Read only from `redacted/`.
2. Accept file paths. Never accept pasted student work.
3. Never echo the map file.
4. Refuse to grade without a profile.

These are instructions, and instructions can be violated. `hooks/block-raw-reads.json`
ships as an optional `PreToolUse` hook that blocks common direct reads of
private grading data. It is defense against accidents and instruction drift,
not an adversarial sandbox. Tell the instructor it exists. The skill must work
without it.

## Two agent workflows

`/grade calibrate --course <course>` learns the instructor's standards. Local
only. Refuse it when the answer at the gate was "cloud", because calibration
reads graded work that still carries names.

`/grade <path> --course <course>` grades a folder of submissions. Either host.
Each grading run includes the assignment briefing above.

Never bundle them. Calibration's local-only constraint must not spread to every
grading run.

## Calibrate

Read `references/calibration.md` first.

1. **Gate.** If the answer is cloud, stop. Say that calibration reads graded
   work with names in it and must run locally.
2. **Collect.** The rubric, three to eight already-graded pieces of work, and
   the roster.
3. **Build the packet.**

   ```bash
   python3 scripts/calibrate.py inputs \
     --rubric <rubric.md> --samples <graded/> --roster <roster.csv> \
     --holdout <one-filename.md> --out .grading/calibration-inputs.json
   ```

   This is the safety step. It drops every student essay and replaces every
   roster name in the instructor's comments with `[STUDENT]`. Build the profile
   from this packet, never from the sample files.

4. **Interview.** Ask only what the samples cannot answer. The five questions
   are in `references/calibration.md`. Skip any the work already answers.
5. **Write the profile** to `.grading/profiles/<course>.md` with all eight
   sections, including the fingerprint table. Copy the whole-rubric digest and
   each criterion digest from the packet.
6. **Validate against the holdout.** Grade the held-back sample blind, using the
   full grading flow below, then:

   ```bash
   python3 scripts/calibrate.py holdout \
     --record <record.json> --draft <draft.md> --sample <holdout.md>
   ```

7. **Show the instructor the holdout report.** That report is the deliverable,
   not the profile. If the score gap is wide or no phrasing carried over, say so
   plainly and offer to edit the profile and run again.

## Grade

### 1. Gate and profile

Ask the gate question. Then:

```bash
python3 scripts/grade.py profile --course <course> --rubric <rubric.md>
```

| Exit | Meaning | What to do |
| --- | --- | --- |
| 0 | Profile is current | Continue |
| 10 | No profile | Refuse. Offer `/grade calibrate --course <course>`. |
| 11 | The rubric drifted | Name the drifted criteria. Ask before continuing. |

### 2. Check the assignment brief

Follow [Assignment briefing](#assignment-briefing). Do not begin redaction until
`brief check` confirms the exact approved file is current for this batch,
rubric, and profile.

### 3. Redact

```bash
python3 scripts/redact.py <submissions/> --roster <roster.csv> \
  --batch <batch> --out redacted/ --instructor "<Your Name>"
```

Pause and ask the instructor to inspect the review file locally, outside this
conversation. It contains candidate names, passages that may identify their
author, and every bare surname substituted. Never read or quote it yourself.
After the instructor confirms the local review is complete, re-run with
`--reviewed`.

A non-zero exit stops the cloud path. There is no override. Report the exit
code and what caused it.

Tell the instructor what redaction does and does not catch, from
`references/redaction.md`. Do not imply that everything is scrubbed.
Pseudonymization is not anonymization.

### 4. Survey

```bash
python3 scripts/grade.py status --batch <batch>
```

Grade only what is `pending`. A submission that already has a draft has already
been graded; regrading it produces a second, different grade for the same
student. Report anything `orphan` or `incomplete` rather than working around it.

### 5. Grade each pending submission

For each one, read only its file under `redacted/`.

Use the approved assignment brief to apply its priorities, required evidence,
expected mistakes, feedback behavior, and boundaries. It may clarify the rubric
but must not change point values or add criteria.

**Score every criterion.** For each, give a score out of its point value, name
the level from the rubric's own language, and attach at least one quote from the
essay that supports it. Copy quotes verbatim. Use at least four words. Do not
add offsets; verification writes those.

- A criterion the work does not let you assess gets `"assessable": false`,
  `"score": null`, and the level `"cannot assess"`. Never infer a score.
- A score you cannot support with a quote is not a score. Leave it unassessed
  and flag it for the instructor.

**Get the density evidence for the prose criterion.**

```bash
python3 scripts/grade.py density redacted/<TOKEN>.md --json
```

Paste that output into the record's `pattern_density` field unchanged.
Verification recounts it. This is evidence about prose, which the rubric asks
you to assess. It is not evidence about authorship, and you must never present
it as such. Do not say or imply that a submission was written by AI.

**Draft the feedback** from the profile and approved assignment brief, in the
structure the rubric states. Follow the profile's Voice and Feedback structure
sections. Use the comment bank for phrasing, adapted to this essay. Never use a
phrase from the never-use list.

**Run the voice pass.** Check the draft against `references/ai-patterns.md`, the
same 35 patterns the Humanizer skill uses. Feedback that reads like a chatbot
defeats the point of calibrating.

**Write both files** to `.grading/drafts/<batch>/`:

- `<TOKEN>.md` — the feedback the instructor reads.
- `<TOKEN>.json` — the record. Schema `grade-record/1`, status `draft`.

### 6. Verify

```bash
python3 scripts/grade.py verify --batch <batch> --course <course> \
  --rubric <rubric.md>
```

Before reading submissions or drafts, verification checks the approved
assignment brief against the current rubric and profile. It then checks every
quote against the source, the arithmetic against itself, the letter against the
rubric's scale, the structure against the protocol, the phrasing against the
never-use list, and the density figures against a fresh count. It writes the
located offsets back into each record.

After every draft passes, verification also writes the identity-free schema 2
`_verification.json` receipt beside the drafts. Its token-free
`assignment-brief` evidence binds the exact approved file alongside the
redacted submissions, drafts, grade records, rubric, calibration profile,
redaction manifest, and pattern reference that passed. Any later edit makes the
receipt stale and requires verification again. The `--no-write` check writes
neither offsets nor a receipt.

Exit 13 means the assignment brief is missing, still a draft, malformed, or
stale. Exit 12 means at least one generated draft failed verification. Approve
or correct the brief, or fix the generated draft, then run verification again.
Do not rehydrate a batch that failed verification.

### 7. Consistency

```bash
python3 scripts/grade.py consistency --batch <batch>
```

Show the instructor everything it flags. These are flags, not failures. An
outlier is often a genuinely unusual essay.

### 8. Rehydrate

```bash
python3 scripts/rehydrate.py .grading/drafts/<batch>/ \
  --batch <batch> --course <course> --rubric <rubric.md> \
  --profile .grading/profiles/<course>.md --redacted redacted/ \
  --brief .grading/assignments/<batch>.md \
  --map .grading/maps/<batch>.json --out final/<batch>/
```

Local only. Rehydration first requires the current trusted grading context,
revalidates the receipt, and reruns semantic verification before it restores
identities through the local token map. It prepares every draft in staging
before publishing anything. A missing, stale, malformed, or self-authored
receipt, a token with no mapping, or a rendering failure leaves the destination
unchanged.
It refuses a legacy schema 1 receipt. Approve an assignment brief and run
verification again to produce current schema 2 whole-batch evidence.
Successful publication includes the identity-free `_publication.json` marker.
Pass `--force` only to replace an unchanged publication recognized by that marker.
Safe force stages the complete replacement, moves the prior publication to a
backup, installs the replacement, and restores or recovers the backup on failure.
The destination may be briefly absent during that portable directory swap, but
it is never exposed as a partly populated new grading batch.

Every rehydrated feedback file is a draft pending the instructor's sign-off.
Do not open or quote the rehydrated files after publication. They contain real
identities and must stay outside model context. Tell the instructor where the
local drafts were written so they can review the feedback and numeric grades.

## Errors

Every condition refuses rather than guesses.

| Condition | Behavior |
| --- | --- |
| No profile for the course | Refuse. Offer `calibrate`. |
| Rubric changed since calibration | Warn, name the drifted criteria, ask before proceeding |
| Assignment brief is missing, unapproved, or stale | Stop before reading submissions or drafts. Prepare or revise it, get the exact approval phrase, run `brief approve`, then run `brief check`. |
| Student text pasted instead of a path | Refuse. Say the text is already in context and what that means. |
| `redact.py` exits non-zero | The cloud path refuses. No override. |
| No roster supplied | The shipped CLI refuses because `--roster` is required. A direct library call with no roster must not use the cloud path. |
| Criterion not assessable from the work | Score it "cannot assess". Never infer a score. |
| No supporting quote for a score | Give no score. Flag it for the instructor. |
| Batch interrupted | Resume. Skip files that already have drafts. |
| Unmapped token at rehydrate | Fail publication. |
| Empty or corrupt submission | Skip it, report it, continue the batch. |
| A draft fails verification | Fix the draft. Never rehydrate a failed batch. |

## What this skill does not do

- **It does not detect AI-written work.** AI detection is unreliable and
  produces false accusations. Scoring prose against a stated criterion is in
  scope. Concluding who or what wrote an essay is not, and no density figure
  licenses that conclusion.
- **It does not submit grades.** The instructor pastes the finished feedback.
- **It does not decide policy.** FERPA and local rules decide whether student
  work may reach a cloud model. This skill does not make that decision and must
  not imply it has been made.
- **It does not sign off.** Every grade it produces is a draft.

## References

| File | Read it when |
| --- | --- |
| `references/redaction.md` | Before telling the instructor what redaction covers |
| `references/assignment-brief.md` | Before preparing or checking an assignment brief |
| `references/calibration.md` | Before running `calibrate` |
| `references/rubric-format.md` | When a rubric will not parse |
| `references/ai-patterns.md` | During the voice pass, every draft |
