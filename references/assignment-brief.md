# Assignment briefing

The assignment brief records how the rubric and course calibration profile
apply to one grading batch. The agent prepares it with the instructor before it
reads any submission.

## What the agent asks

Ask these seven groups in order. Ask one group at a time when the rubric,
profile, assignment prompt, or earlier answers do not already supply the answer.

1. Course display name, CLI course key, assignment title, batch name, rubric
   path, and submissions folder. The display name may contain spaces, such as
   `PHIL 210`; the course key is the path-safe value used in every command,
   such as `phil210`.
2. Whether the current course profile fits this assignment. Return to
   calibration when the profile is missing or the rubric has changed.
3. Learning goals and rubric priorities that need attention. These notes may
   clarify the rubric but must not change its points or add criteria.
4. Required sources or evidence, plus common mistakes the instructor expects.
5. Feedback length, tone, structure, and directness. Use profile defaults when
   the instructor has no assignment-specific change.
6. Assumptions, phrases, or feedback moves the agent must avoid.
7. Local or cloud operation and the privacy consequence of that choice. Fully
   local model inference and agent execution are recommended. Cloud use still
   requires redaction and institutional permission.

## What the agent may read

Before approval, the agent may read the rubric, calibration profile, assignment
prompt, and instructor-authored course material needed to answer the questions.

Before approval, the agent must not read submissions, the roster, the redaction
review file, or the token map. The assignment brief must stay identity-free. Do
not put student names, IDs, emails, pasted student work, roster content, review
content, or token-map content in it.

## Approval

Prepare the local draft:

```bash
python3 scripts/grade.py brief prepare \
  --batch <batch> --course <course> --mode <local-or-cloud>
```

Use the CLI course key for `<course>`. The prepared file is the only valid
starting shape for an assignment brief. If the command or agent command tool
fails, keep the brief unprepared, report the exact error, and retry with
corrected arguments. Do not hand-build a replacement brief.

Replace every `<!-- answer-required -->` marker with the instructor's answers.
The generated `Host mode:` line already records local or cloud operation. In
the `Boundaries` answer, record the privacy consequence and prohibited moves;
do not repeat or replace that generated line. Then show a short summary with
the course, assignment, batch, file paths,
profile status, grading priorities, required evidence, feedback behavior,
prohibited assumptions, host mode, and privacy consequence.

Draft preparation is complete only after the command exits 0, the expected
regular file exists, and no `<!-- answer-required -->` marker remains. Check
those conditions before claiming the draft was saved.

Wait for this exact message:

```text
Approve assignment brief for <batch>
```

A casual “yes,” approval for another batch, or an agent's inference is not
approval. After the instructor sends the exact message, run:

```bash
python3 scripts/grade.py brief approve \
  --batch <batch> --course <course> --rubric <rubric.md> \
  --approval "Approve assignment brief for <batch>"
```

Confirm the saved approval before redaction or grading:

```bash
python3 scripts/grade.py brief check \
  --batch <batch> --course <course> --rubric <rubric.md>
```

## Resume or revise

An approved brief survives an interrupted agent session or machine restart.
Run `brief check` before resuming.

The instructor must approve again when the brief body, course, batch, host
mode, rubric, or calibration profile changes, or when required metadata or
sections are removed. Verify the whole grading batch again after reapproval.

## File format

The default path is `.grading/assignments/<batch>.md`. The file uses schema
`grading-assignment-brief/1`, strict front matter, and these six body headings:

- `## Assignment`
- `## Grading priorities`
- `## Required evidence`
- `## Common mistakes`
- `## Feedback`
- `## Boundaries`

Preparation creates an owner-only draft. Approval binds the exact brief body,
rubric, and calibration profile with SHA-256 digests and records the exact
approval phrase and UTC time.

## What approval does not prove

Chat approval is an auditable workflow record. It is not a signature, proof of
human identity, or an access-control boundary. An agent running as the local
user can write the same files and run the same commands.

Approval also is not privacy permission. Institutional policy still decides
whether student work may reach a cloud model or agent. Rehydrated feedback
remains a draft until the instructor reviews and signs off on it.
