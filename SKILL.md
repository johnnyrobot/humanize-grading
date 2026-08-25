---
name: grader
description: |
  Score student work against the instructor's rubric and draft feedback in the
  instructor's voice, calibrated from their rubric and past graded work.
  Redacts student identity before any work reaches a cloud model.
license: MIT
metadata:
  version: "3.1.0"
---

# Grader

Score student work against the instructor's rubric. Draft feedback that sounds
like the instructor, not like a chatbot. Keep student identity out of a cloud
model's context.

## The gate, before anything else

Ask this before reading any file, every run:

> **Are you running me on a local model or a cloud model?**

| Answer | What happens |
| --- | --- |
| Cloud | Redaction is mandatory. A non-zero exit from `redact.py` stops the run. |
| Local | Redaction is on by default. Skipping it takes an explicit `--no-redact`. |

You cannot detect your own host. The answer is the instructor's to give, and
you must take it at face value. Do not guess from the conversation.

Never ask this question after a file is open. By then the work is already in
context and the answer changes nothing.

## Four hard rules

1. Read only from `redacted/`.
2. Accept file paths. Never accept pasted student work.
3. Never echo the map file.
4. Refuse to grade without a profile.

These are instructions, and instructions can be violated. `hooks/block-raw-reads.json`
ships as an optional `PreToolUse` hook that denies reads of `submissions/` and
`.grading/maps/`. It is the difference between a rule and a guarantee. Tell the
instructor it exists. The skill must work without it.

## Two commands

`/grade calibrate --course <course>` learns the instructor's standards. Local
only. Refuse it when the answer at the gate was "cloud", because calibration
reads graded work that still carries names.

`/grade <path> --course <course>` grades a folder of submissions. Either host.

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
   sections, including the fingerprint table. Copy each criterion's digest from
   the packet.
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

### 2. Redact

```bash
python3 scripts/redact.py <submissions/> --roster <roster.csv> \
  --batch <batch> --out redacted/ --instructor "<Your Name>"
```

Read the review file it names. It lists candidate names, passages that may
identify their author, and every bare surname substituted. Then re-run with
`--reviewed`.

A non-zero exit stops the cloud path. There is no override. Report the exit
code and what caused it.

Tell the instructor what redaction does and does not catch, from
`references/redaction.md`. Do not imply that everything is scrubbed.
Pseudonymization is not anonymization.

### 3. Survey

```bash
python3 scripts/grade.py status --batch <batch>
```

Grade only what is `pending`. A submission that already has a draft has already
been graded; regrading it produces a second, different grade for the same
student. Report anything `orphan` or `incomplete` rather than working around it.

### 4. Grade each pending submission

For each one, read only its file under `redacted/`.

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

**Draft the feedback** from the profile, in the structure the rubric states.
Follow the profile's Voice and Feedback structure sections. Use the comment bank
for phrasing, adapted to this essay. Never use a phrase from the never-use list.

**Run the voice pass.** Check the draft against `references/ai-patterns.md`, the
same 35 patterns the Humanizer skill uses. Feedback that reads like a chatbot
defeats the point of calibrating.

**Write both files** to `.grading/drafts/<batch>/`:

- `<TOKEN>.md` — the feedback the instructor reads.
- `<TOKEN>.json` — the record. Schema `grade-record/1`, status `draft`.

### 5. Verify

```bash
python3 scripts/grade.py verify --batch <batch> --course <course> \
  --rubric <rubric.md>
```

This checks every quote against the source, the arithmetic against itself, the
letter against the rubric's scale, the structure against the protocol, the
phrasing against the never-use list, and the density figures against a fresh
count. It writes the located offsets back into each record.

After every draft passes, verification also writes the versioned, identity-free
`_verification.json` receipt beside the drafts. The receipt binds the exact
redacted submissions, drafts, grade records, rubric, calibration profile, and
pattern reference that passed. Any later edit makes the receipt stale and
requires verification again. The `--no-write` check writes neither offsets nor
a receipt.

Exit 12 means at least one draft failed. Fix the draft and run again. Do not
rehydrate a batch that failed verification.

### 6. Consistency

```bash
python3 scripts/grade.py consistency --batch <batch>
```

Show the instructor everything it flags. These are flags, not failures. An
outlier is often a genuinely unusual essay.

### 7. Rehydrate

```bash
python3 scripts/rehydrate.py .grading/drafts/<batch>/ \
  --map .grading/maps/<batch>.json --out final/<batch>/
```

Local only. Rehydration first requires and revalidates the verification receipt,
then restores identities through the local token map. It prepares every draft in
staging before publishing anything. A missing, stale, or malformed receipt, a
token with no mapping, or a rendering failure leaves the destination unchanged.
Successful publication includes the identity-free `_publication.json` marker.
Pass `--force` only to replace an unchanged publication recognized by that marker.
Safe force stages the complete replacement, moves the prior publication to a
backup, installs the replacement, and restores or recovers the backup on failure.
The destination may be briefly absent during that portable directory swap, but
it is never exposed as a partly populated new grading batch.

Every rehydrated feedback file is a draft pending the instructor's sign-off. Say
so. The identity-free publication marker is not feedback.

## Errors

Every condition refuses rather than guesses.

| Condition | Behavior |
| --- | --- |
| No profile for the course | Refuse. Offer `calibrate`. |
| Rubric changed since calibration | Warn, name the drifted criteria, ask before proceeding |
| Student text pasted instead of a path | Refuse. Say the text is already in context and what that means. |
| `redact.py` exits non-zero | The cloud path refuses. No override. |
| No roster supplied | Layer 1 is off. Warn. The cloud path requires explicit acknowledgment. |
| Criterion not assessable from the work | Score it "cannot assess". Never infer a score. |
| No supporting quote for a score | Give no score. Flag it for the instructor. |
| Batch interrupted | Resume. Skip files that already have drafts. |
| Unmapped token at rehydrate | Fail the run. |
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
| `references/calibration.md` | Before running `calibrate` |
| `references/rubric-format.md` | When a rubric will not parse |
| `references/ai-patterns.md` | During the voice pass, every draft |
