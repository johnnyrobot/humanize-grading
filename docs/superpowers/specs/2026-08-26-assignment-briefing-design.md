# Instructor-guided assignment briefing

**Date:** 2026-08-26
**Status:** Approved through the instructor's delegated recommendation authority

## Summary

Add a required assignment briefing between course calibration and submission
redaction. The agent walks the instructor through a short set of questions,
summarizes the answers, and waits for an exact approval message before it reads
student work.

Approval creates one identity-free Markdown assignment brief under
`.grading/assignments/<batch>.md`. The brief binds the grading batch to the
current rubric and calibration profile. Whole-batch verification checks the
brief and includes it in a version 2 verification receipt. Interrupted work may
resume under the same approval, but an edit to the approved brief, rubric, or
profile requires approval and verification again.

This is a workflow approval, not proof of human identity. An agent running as
the instructor's local user can write the same files and invoke the same
commands. The package must describe that boundary plainly.

## Goals

- Walk an instructor through course calibration and each assignment run in the
  order needed to grade safely.
- Ask assignment-specific questions before reading submissions.
- Require the instructor to approve a concise summary before redaction or
  grading begins.
- Save the approved decisions as local Markdown that an instructor can inspect.
- Make approval reusable for an interrupted run but stale after any relevant
  change.
- Bind the exact approved brief into whole-batch verification and publication.
- Keep local models and agents as the recommended operating mode while retaining
  the mandatory cloud redaction path.

## Non-goals

- Authenticate that a particular human typed the approval message.
- Add a graphical interface or LMS integration.
- Submit grades or mark generated feedback as final.
- Put student names, student work, a roster, or a token map in the assignment
  brief.
- Replace the course calibration profile with assignment-specific prompting.
- Allow assignment notes to override rubric points or institutional policy.

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Approval mechanism | Exact chat phrase, then a local approval command | It is portable across agent tools and makes the pause unambiguous. |
| Approval phrase | `Approve assignment brief for <batch>` | A batch name prevents a casual “yes” from approving the wrong work. |
| Storage | `.grading/assignments/<batch>.md` | The file stays local, inspectable, and beside other per-user grading state. |
| File format | Strict front matter plus plain Markdown sections | The instructor can read it while the CLI can validate it without a YAML dependency. |
| Reuse | Valid across interrupted resumes | Repeating the same interview adds no safety when the inputs did not change. |
| Invalidation | Body, rubric, profile, batch, course, or mode change | These values can change grading intent or privacy handling. |
| Receipt behavior | Require `assignment-brief` in verification receipt schema 2 | Publication must prove it uses the exact approved instructions. |
| Existing receipts | Version 1 receipts require a brief and fresh verification | Silently accepting an old receipt would bypass the new approval gate. |
| Package version | 3.3.0 | This adds a new workflow and CLI while keeping the package's command family stable. |
| Default host | Fully local model and agent | This best limits disclosure; cloud remains supported behind redaction and policy review. |

## Instructor workflow

### 1. Start with the host gate

Before reading any file, the agent asks whether model inference and agent
execution are local or cloud-based. It recommends fully local operation and
does not infer the answer from its environment.

### 2. Establish the course profile

The agent asks for the course, rubric, and existing calibration profile. When
the profile is absent or the rubric has drifted, it guides the instructor
through local calibration before starting an assignment brief:

1. Collect the rubric and three to eight already graded samples.
2. Build the identity-safe calibration packet.
3. Ask only the five calibration questions the samples do not answer.
4. Draft the profile.
5. Run the holdout check and show the instructor the differences.
6. Revise until the holdout supports using the profile.

Calibration remains a separate, local-only command. The assignment briefing
does not read or absorb raw calibration samples.

### 3. Prepare the assignment brief

For every grading batch, the agent confirms these items in order:

1. Course, assignment title, batch name, rubric path, and submissions folder.
2. Whether the current course profile fits this assignment. Missing or drifted
   profiles return to calibration.
3. The assignment's learning goals and any rubric priorities that need special
   attention. These notes cannot change the rubric's points.
4. Required sources or evidence and common mistakes the instructor expects.
5. Feedback length, tone, structure, and directness. Profile defaults apply when
   there is no assignment-specific override.
6. Assumptions, phrases, or feedback moves the agent must avoid.
7. Local or cloud operation and the privacy consequence of that choice.

The agent writes a draft brief. It may read the rubric, profile, assignment
prompt, and instructor-authored course material while preparing the brief. It
must not read submissions, the roster, the redaction review file, or the token
map.

### 4. Summarize and approve

The agent presents one short summary containing:

- the exact course and batch;
- assignment title and file paths;
- profile status;
- grading priorities and required evidence;
- feedback behavior and prohibited assumptions;
- selected local or cloud path; and
- the exact approval phrase.

The agent pauses until the instructor sends:

```text
Approve assignment brief for <batch>
```

After that message, the agent runs the approval command. A casual “yes,” an
earlier approval for another batch, or agent inference is not approval.

### 5. Redact, grade, verify, and publish

Only after approval may the agent open submissions or start redaction. The
existing grading-batch lifecycle then applies:

1. Redact and complete the instructor's local redaction review.
2. Survey the grading batch and resume only pending submissions.
3. Draft scores, evidence, and feedback from the rubric, profile, and brief.
4. Verify the whole batch. Verification checks and binds the approved brief.
5. Show advisory consistency flags.
6. Rehydrate locally through a current schema 2 receipt.
7. Leave all rehydrated feedback as drafts for instructor sign-off.

## Assignment brief format

The file begins with strict front matter. Keys are unique and unknown keys are
rejected so a misspelling cannot silently remove a safety field.

```markdown
---
schema: grading-assignment-brief/1
batch: essay-3
course: phil210
status: approved
mode: local
rubric_sha256: <64 lowercase hexadecimal characters>
profile_sha256: <64 lowercase hexadecimal characters>
body_sha256: <64 lowercase hexadecimal characters>
approved_at: 2026-08-26T18:30:00Z
approval: Approve assignment brief for essay-3
---

# Assignment brief: Essay 3

## Assignment

Course, assignment title, batch, rubric, and submissions folder.

## Grading priorities

Learning goals and assignment-specific emphasis that does not change points.

## Required evidence

Required sources, evidence, or “None beyond the rubric.”

## Common mistakes

Expected misconceptions or “None identified.”

## Feedback

Length, tone, structure, directness, and profile defaults.

## Boundaries

Prohibited assumptions, phrases, policy limits, and the selected host mode.
```

The body must contain all six headings. A prepared draft uses the literal
marker `<!-- answer-required -->` under unanswered headings. Approval refuses
while any marker remains.

The approval command computes the rubric, profile, and body SHA-256 values,
sets the UTC timestamp, and records the exact approval phrase. It writes the
file atomically with local-only permissions. The check command recomputes every
digest and refuses a draft, a stale approval phrase, a mismatched batch or
course, an invalid mode, malformed metadata, missing sections, or changed
content.

## Interfaces

### Brief module

Create `scripts/grading/brief.py` as the only parser and writer for assignment
briefs. It exposes focused interfaces:

```python
@dataclass(frozen=True)
class AssignmentBrief:
    batch: str
    course: str
    status: str
    mode: str
    rubric_sha256: str
    profile_sha256: str
    body_sha256: str
    approved_at: str
    approval: str
    body: str

class BriefError(ValueError):
    pass

def brief_path(root: Path, batch: str) -> Path: ...
def prepare_brief(path: Path, *, batch: str, course: str, mode: str) -> AssignmentBrief: ...
def approve_brief(path: Path, *, batch: str, course: str, rubric: Path,
                  profile: Path, approval: str) -> AssignmentBrief: ...
def check_brief(path: Path, *, batch: str, course: str, rubric: Path,
                profile: Path) -> AssignmentBrief: ...
```

The module reads only the brief, rubric, and calibration profile. It never
imports or loads the token map.

### CLI

Add a nested `brief` command to `scripts/grade.py`:

```bash
python3 scripts/grade.py brief prepare --batch essay-3 --course phil210 \
  --mode local

python3 scripts/grade.py brief approve --batch essay-3 --course phil210 \
  --rubric grading_rubric.md \
  --approval "Approve assignment brief for essay-3"

python3 scripts/grade.py brief check --batch essay-3 --course phil210 \
  --rubric grading_rubric.md
```

All commands accept `--root`, and prepare/approve/check default to
`.grading/assignments/<batch>.md`. `prepare` refuses to overwrite an existing
file. `approve` and `check` use `.grading/profiles/<course>.md`. Failure prints
one plain-language reason and exits with the existing malformed-input exit code
13. Missing profiles and rubric drift retain exit codes 10 and 11.

### Grading-batch verification

`VerificationInputs` gains a required `brief: Path`. `GradingBatch.verify()`
checks the brief before it reads redacted submissions or drafts. Invalid or
stale approval returns a blocking finding and does not write records or a
receipt.

The receipt schema becomes `grading-batch-verification/2`. Its evidence roles
include exactly one `assignment-brief` with no student token. Receipt validation
checks its `.md` suffix, one-megabyte size limit, and SHA-256 digest. Both
explicit and receipt-recovered rehydration contexts carry the brief path.

The rehydration CLI adds `--brief` to the complete explicit context. Omitting
all context arguments still recovers paths from the receipt. A schema 1 receipt
is rejected with instructions to create and approve a brief, then verify again.

## State and invalidation

An approved brief remains valid when an agent session ends, the machine
restarts, or grading resumes later. Before resuming, the agent runs `brief
check`. Approval becomes invalid when:

- the brief body changes;
- its course, batch, or host mode changes;
- the rubric bytes change;
- the calibration profile bytes change;
- required metadata or sections are removed; or
- the approval phrase no longer names the exact batch.

If any bound artifact changes after verification, receipt validation is stale
even if the brief itself still passes. The instructor approves the revised
brief, then the agent verifies the whole batch again.

## Safety boundaries

- The brief must be identity-free. It must not contain student names, IDs,
  emails, pasted student work, roster content, or token-map content.
- The brief approval is auditable workflow state, not a signature or an access
  control boundary.
- The model-read boundary remains `redacted/`; the brief does not weaken it.
- Calibration samples remain local and separate from assignment briefing.
- Assignment priorities may clarify the rubric but may not change its point
  values or invent criteria.
- Cloud operation still requires successful redaction and institutional
  approval. An approved brief is not privacy authorization.
- Rehydrated feedback remains a draft until the instructor signs off.

## Documentation and domain language

Add **Assignment brief** to `CONTEXT.md` and avoid calling it a prompt, config,
or approval receipt. The verification receipt proves that the exact brief was
used; the brief records the instructor's assignment decisions.

Add an ADR for the new gate because it changes the publication evidence model.
Update `SKILL.md`, `README.md`, `docs/using-the-grader.md`, and a new
`references/assignment-brief.md` together. The README begins with the guided
workflow and puts the grading example before the existing writing example.

The package version becomes 3.3.0 in both skills, the first README history
entry, and `.claude-plugin/plugin.json`. `PACKAGE_FILES.txt` includes every new
tracked file.

## Test strategy

Use test-driven development at three levels:

1. **Brief unit tests:** strict parsing, safe preparation, exact approval,
   required sections, placeholder refusal, atomic write behavior, digest
   invalidation, and mode validation. Identity-free authoring remains an agent
   rule because the brief parser has no roster and cannot prove that prose is
   anonymous.
2. **Lifecycle tests:** verification refuses before reading batch artifacts when
   a brief is absent or stale; successful receipts contain one assignment brief;
   changed briefs make receipts stale; schema 1 receipts cannot publish.
3. **CLI and end-to-end tests:** prepare/approve/check exit behavior, explicit
   rehydration context, legacy receipt context, and the full synthetic grading
   chain.

Final checks are:

```bash
python3 -m pytest tests/
python3 scripts/validate-package.py
npx skills add . --list
claude plugin validate .
```

The release is not tagged or pushed as part of this change unless the instructor
separately asks to publish version 3.3.0.

## Alternatives considered

### Chat approval without a durable brief

Rejected. It guides the conversation but cannot be checked on resume or bound
to verification and publication.

### Instructor-run approval command only

Rejected as the primary workflow. It adds tool-specific ceremony and still does
not authenticate a human when the agent shares the same local account. The
agent-operated command follows an explicit instructor message instead.

### Re-ask every question after interruption

Rejected. It adds friction without changing evidence. Digest-bound approval is
safer and easier to audit.

### Keep verification receipt schema 1

Rejected. Making a previously optional role mandatory under the old schema
would obscure the compatibility boundary. Schema 2 states that approval is now
part of the verified batch.
