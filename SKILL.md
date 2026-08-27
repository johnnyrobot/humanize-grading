---
name: grader
description: |
  Score student work against the instructor's rubric and draft feedback in the
  instructor's voice, calibrated from their rubric and past graded work.
  Redacts student identity before any work reaches a cloud model.
license: MIT
metadata:
  version: "3.4.0"
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

Keep the human-readable course name separate from the CLI course key. Use the
path-safe key, such as `phil210`, for every `<course>` argument and profile path.

Then ask these seven groups in order, one at a time when the rubric, profile,
assignment prompt, course material, or earlier answers do not supply the answer:

1. Course display name, CLI course key, assignment title, batch name, rubric
   path, and submissions folder.
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

The command must exit 0 and create the expected regular file before you edit
its answer markers. If the command or agent command tool fails, report the
exact error and retry with corrected arguments. Keep the brief unprepared;
never hand-build a replacement or claim a file exists without checking it.
Drafting is complete only when the file exists and no
`<!-- answer-required -->` marker remains.

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

Complete the gate and assignment briefing first. Do not begin redaction until
`brief check` confirms the exact approved file is current.

After approval, use the executable next action for every stage. The navigation
command is `python3 scripts/grade.py next`; pass the batch, course, rubric,
private path locators, and `--json` so it can return exact JSON argument arrays:

```bash
python3 scripts/grade.py next \
  --batch <batch> --course <course> --rubric <rubric.md> \
  --submissions <submissions/> --roster <roster.csv> \
  --instructor "<Your Name>" --publication final/<batch> --json
```

1. Run `next --json`.
2. Execute only the returned action. When it returns an ordered command group,
   execute the commands in that order.
3. Run `next --json` again. Repeat until it returns `review-publication`.

When the action is `review-redaction`, stop. Ask the instructor to
inspect the review file locally,
outside this conversation and confirm that review is complete. Never read or
quote the review file or token map. Execute the returned reviewed-redaction
command only after that confirmation. Read
`references/redaction.md` before describing what redaction covers.

When the action is `prepare-record`, use the returned command. Its base form is
`python3 scripts/grade.py record prepare`. It creates the feedback Markdown and
`grade-record/1` JSON together. Read only the returned token's file under
`redacted/`, then edit both generated files. Do not create either file from
memory. Read `references/rubric-format.md` for the complete record and feedback
requirements.

After any returned lifecycle command exits non-zero, run a fresh `next --json`
call. Execute its repair action before anything else. Exit 12 means the current
action is blocked and repair is the only legal next step. A verification failure
stays `repair-verification`; a non-zero redaction other than the review-only
exit 3 stays `repair-redaction`. Consistency and publication remain unavailable
until the relevant gate succeeds.

At `review-publication`, tell the instructor where the local rehydrated drafts
were written. Do not open or quote them. They contain real identities and remain
drafts until the instructor reviews the feedback and numeric grades.

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
| `references/rubric-format.md` | Before editing a prepared draft pair, or when a rubric will not parse |
| `references/ai-patterns.md` | During the voice pass, every draft |
| `references/agent-conformance.md` | Before running or auditing a synthetic agent conformance trial |
