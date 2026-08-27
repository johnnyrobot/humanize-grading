# Using the grader

A walkthrough of the whole system, from a folder of student essays to feedback
you can paste into your LMS.

Synthetic runs covered `calibrate.py inputs` and `holdout`; `grade.py profile`,
`brief`, `next`, `record prepare`, `status`, `verify` and `consistency`; plus
`redact.py` and `rehydrate.py`. The lifecycle output shown comes from that work
against `tests/fixtures/phil210/`. The conformance `run` and `audit` commands
near the end are illustrative until Task 6 records live agent evidence. You can
follow the deterministic sections with the synthetic corpus before using your
own files.

## What you are working with

| Piece | What it does | Where it runs |
| --- | --- | --- |
| `scripts/redact.py` | Replaces student identity with stable tokens | Your machine only |
| `scripts/calibrate.py` | Builds calibration inputs, reports the holdout delta | Your machine only |
| `scripts/grade.py` | Owns assignment briefs, grading-batch status, verification receipts, pattern counts and consistency flags | Your machine only |
| `scripts/rehydrate.py` | Requires a current receipt, then stages and publishes rehydrated draft feedback | Your machine only |
| `SKILL.md` | The grader. Reads the rubric and the profile, scores the work, drafts the feedback | Local or cloud model |
| `skills/humanizer/SKILL.md` | The finishing pass that keeps feedback from reading like a chatbot | Local or cloud model |

**The model does the judgement. The scripts do the checking.** Nothing in
`scripts/grading/` grades anything. Those modules exist to confirm that a quote
is really in the essay, that the arithmetic adds up, that the letter matches
your scale, and that a density figure was measured rather than invented.

## Requirements

Python 3.13. No runtime dependencies — the standard library only.

For the tests: `pip install -r requirements-dev.txt`, then `python3 -m pytest tests/`.

`python-docx` is optional. Install it if you need `.docx` submissions. PDFs are
not supported; convert them first.

## The safety model, in one page

Read `references/redaction.md` in full before you send anything anywhere. The
short version:

- **Redaction runs on your machine.** Names, student IDs, emails, profile URLs,
  filenames and document metadata become tokens like `STUDENT_03`.
- **The map file is the boundary.** `.grading/maps/<batch>.json` is the only
  artifact that can turn a token back into a name. It is written with owner-only
  permissions. `redact.py` writes it, `rehydrate.py` reads it, and nothing else
  ever loads it.
- **Redaction fails closed.** A non-zero exit means the batch is not safe to
  send. There is no override flag.
- **The map is bound without exposing it.** `redacted/_redaction.json` holds a
  random redaction-run ID, the instructor's durable reviewed state, and no
  identities. The token map holds the same ID. Verification binds the manifest,
  refuses unreviewed state, and rehydration refuses a stale same-label map from
  another redaction result.
- **Verification produces the evidence gate.** `_verification.json` binds the
  exact approved assignment brief, redacted submissions, drafts, grade records,
  rubric, calibration profile, redaction manifest and pattern reference that
  passed. Schema 2 makes the approved brief required evidence. Any later edit
  makes the receipt stale.
- **Publication is staged.** Rehydration changes the destination only after
  every draft renders without an unmapped token. `_publication.json` records
  the receipt digest and output hashes without holding real identities.
- **Pseudonymization is not anonymization.** "My grandmother's escape from
  Saigon in 1975" names nobody and still identifies its author to anyone who
  knows the class. The warning pass flags passages like that. It does not remove
  them, and it does not catch every one.
- **Your institution decides.** FERPA and local policy govern whether student
  work may reach a cloud model. This tool does not make that decision.

Subject matter is kept on purpose. Places, statutes, historical figures and the
dates an essay is about all survive, because removing them would make the work
impossible to grade.

## Choose local or cloud before calibration or grading

The agent asks before it reads any file:

> Are you running me on a local model or a cloud model?

Fully local model inference and agent execution are recommended. They keep
prompts and files on an instructor-controlled machine, and redaction still runs
by default.

- **Local:** Calibration and grading are available. Calibration must use this
  path because its source samples and roster still carry student identity.
- **Cloud:** Calibration is not available. Do not gather or open calibration
  samples or the roster. A cloud grading run may use an existing current
  identity-free profile, but it must follow mandatory redaction and
  institutional policy.

If a cloud run has no current profile, stop. Switch to a fully local model and
agent for calibration before returning to the cloud grading path.

## Part 1 — Calibrate, once per course

Calibration learns two things your rubric cannot record: where you actually land
against your own scale, and how you write.

**It is local-only.** Continue only after the instructor chose the local path
above. Calibration reads work the instructor already graded, which still
carries names. Refuse to calibrate after a cloud answer.

### 1.1 Gather your inputs

- Your rubric, as Markdown. See `references/rubric-format.md` for what the
  parser needs — numbered criteria, a point value each, and a grading scale that
  covers 0 to 100.
- Three to eight pieces of work you have already graded, each with the work,
  your comments, and your score.
- Your class roster as CSV with `name,student_id,email` columns.

### 1.2 Build the calibration packet

```bash
python3 scripts/calibrate.py inputs \
  --rubric tests/fixtures/phil210/grading_rubric.md \
  --samples tests/fixtures/phil210/graded \
  --roster tests/fixtures/phil210/roster.csv \
  --holdout paper_5_graded_subscription_software_paradox.md \
  --out .grading/calibration-inputs.json
```

```
Wrote .grading/calibration-inputs.json. 4 calibration samples, 1 held back.
Scrubbed 4 name forms from the comments.
Read references/calibration.md before building the profile.
```

**This is the safety step.** It throws away every student essay and keeps only
your comments, with past students' names replaced by the literal `[STUDENT]`.
It carries no filenames — samples are labelled `sample_1`, `sample_2` and so on
in sorted order, because gradebook exports routinely name files
`Lastname_Firstname_...`.

Build the profile from this packet, never from the sample files directly.

### 1.3 Write the profile

Ask the grader skill to build `.grading/profiles/<course>.md` from the packet.
`references/calibration.md` holds the interview protocol and the eight sections
a profile needs.

Two rules the profile must obey, because a cloud run reads it:

- No student essay text. The voice lives in your comments.
- No student name. Use `[STUDENT]`, never a `STUDENT_01`-shaped token — a token
  leaking out of a profile would be reattached at rehydration to whichever
  student holds it in the current batch.

`tests/fixtures/phil210/profile_phil210.md` is a complete worked example.

### 1.4 Check the profile against the holdout

This is calibration's real deliverable. Grade the held-back sample blind, then:

```bash
python3 scripts/calibrate.py holdout \
  --record .grading/drafts/holdout/STUDENT_01.json \
  --draft  .grading/drafts/holdout/STUDENT_01.md \
  --sample tests/fixtures/phil210/graded/paper_5_graded_subscription_software_paradox.md
```

You get a report comparing your score against the generated one, the letter
band, which of your own sentences carried through, and the length ratio.

**Read it before you grade anything else.** A bad profile should surface in the
first minute, not on the fortieth essay. If the gap is wide or none of your
phrasing came through, edit the profile and run it again. Profiles are plain
Markdown, dated, and yours to hand-edit.

The profile is reusable course guidance. It records your standards and voice.
Before each grading batch, an assignment brief confirms how that profile
applies to the current assignment. It does not replace calibration.

## Part 2 — Grade a batch

### 2.1 Confirm the gate answer for this grading run

The skill asks again before each grading run:

> Are you running me on a local model or a cloud model?

Cloud means redaction is mandatory. Local means redaction is on by default and
opting out takes an explicit flag. The skill cannot detect its own host, so this
answer is yours to give honestly.

### 2.2 Confirm the profile is current

```bash
python3 scripts/grade.py profile --course phil210 --rubric grading_rubric.md
```

```
Profile for phil210 is current, calibrated 2026-08-24 from 4 samples.
```

If you have edited the rubric since calibrating, this names the drifted criteria
and exits 11, because grading against a changed criterion applies standards
calibrated for different words. Recalibrate before preparing the assignment
brief.

### 2.3 Prepare and approve the assignment brief

Read `references/assignment-brief.md` before starting. For this batch, the agent
asks about the assignment and paths, profile fit, learning goals and rubric
priorities, required evidence and common mistakes, feedback behavior, things to
avoid, and the local or cloud privacy consequence.

Record both the human-readable course name (`PHIL 210`) and its path-safe CLI
key (`phil210`). Every `--course` argument and profile path uses the key.

Before approval, the agent may read the rubric, profile, assignment prompt and
instructor-authored course material. It must not read submissions, the roster,
the redaction review file or the token map.

Prepare the owner-only local draft:

```bash
python3 scripts/grade.py brief prepare \
  --batch essay-2 \
  --course phil210 \
  --mode cloud
```

The default path is `.grading/assignments/essay-2.md`. The agent fills every
`<!-- answer-required -->` marker, then shows you one short identity-free
summary. The agent may claim the draft is saved only after the command exits 0,
the regular file exists, and no answer marker remains. A command failure keeps
the brief unprepared; the agent reports the exact error and retries instead of
constructing another file shape. If the summary matches your instructions,
respond with exactly:

```text
Approve assignment brief for essay-2
```

The agent then records and checks that approval:

```bash
python3 scripts/grade.py brief approve \
  --batch essay-2 \
  --course phil210 \
  --rubric grading_rubric.md \
  --approval "Approve assignment brief for essay-2"

python3 scripts/grade.py brief check \
  --batch essay-2 \
  --course phil210 \
  --rubric grading_rubric.md
```

On resume, run `brief check`; do not repeat the interview when the approved
inputs have not changed. A change to the brief body, course, batch, host mode,
rubric or profile requires approval again. Chat approval records workflow state.
It is not a signature, human authentication or privacy permission.

### 2.4 Run the next action, then redact

After approval, run the `next --json` command shown in the next section. At
this stage it returns the redaction action. Execute that returned command:

```bash
python3 scripts/redact.py submissions/essay-2 \
  --roster roster.csv \
  --batch essay-2 \
  --out redacted \
  --instructor "Marcus Vance"
```

```
Read 5 files. Wrote redacted. Map at .grading/maps/essay-2.json.
  0 names to review, 0 self-identifying passages, 0 bare-name substitutions. See .grading/maps/reviews/essay-2.json.
```

Pass `--instructor` with your own name so you are never redacted. Repeat the
flag for more than one name.

**Inspect the review file locally, outside any model conversation.** It lives
beside the map, not in `redacted/`, because it holds the very names redaction
just removed. It lists candidate names for your judgement, passages that may
identify their author, bare surnames that were substituted, and any submission
that was empty. Confirm the local review, then re-run with `--reviewed`. Never
paste or ask an agent to read this file.

Output files are named by token, so a filename can never carry a name.
The identity-free `_redaction.json` manifest beside them binds this exact
redaction result to its private token map. Current manifests use
`redaction-batch/2` and store the review decision plus the final integer
`exit_code`. Exit 3 or a clean unreviewed result requires private review. Codes
2, 4, 5, 6 and 7 require `repair-redaction`; `reviewed: true` never overrides a
failed run. A legacy schema 1 manifest still loads with no recorded exit and is
treated as unreviewed. Inspect the private review file, then rerun the same
redaction command with `--reviewed --force` to create current durable state.
Verification exits 12 until redaction records both exit 0 and reviewed state.
For a safe existing destination, a new attempt clears prior reviewed state
before roster and input checks. Early failures preserve the prior run ID and
output digests but make `next` return `repair-redaction` instead of drafting.
Atomic manifest replacement is preferred. Immediately before replacing, the
tool requires the path to still identify the already-opened regular single-link
file; a late replacement or hard link returns exit 6 without overwriting it.
When atomic I/O fails and that identity is unchanged, the tool rewrites the
manifest through its bound file descriptor. If neither write can complete,
redaction returns exit 6 before loading the roster; a partial write is malformed
and therefore still blocks grading.

**A non-zero exit stops redaction.** The codes:

| Exit | Meaning |
| --- | --- |
| 2 | A roster entry never appeared in any file — usually someone did not submit |
| 3 | Layer-4 candidates need review; re-run with `--reviewed` |
| 4 | A file could not be read or exceeded an input limit |
| 5 | The destination is not empty; use `--force` to clear the prior redaction outputs |
| 6 | A batch name or private/output path is unsafe or overlaps another role |
| 7 | Two source files resolve to the same grading-batch participant |

Exit 2 is common when the roster is bigger than the batch, but it still blocks
grading. Trim the roster to who actually submitted, then rerun redaction.

### 2.5 Ask for the next legal action

After every returned action, the agent runs this again before the next stage:

```bash
python3 scripts/grade.py next \
  --batch essay-2 \
  --course phil210 \
  --rubric grading_rubric.md \
  --submissions submissions/essay-2 \
  --roster roster.csv \
  --instructor "Marcus Vance" \
  --publication final/essay-2 \
  --json
```

If `--publication` is omitted, `next` resolves it to `final/essay-2` for this
batch.

The versioned result contains argument arrays rather than shell strings:

```json
{
  "schema": "grading-next-action/1",
  "batch": "essay-2",
  "course": "phil210",
  "stage": "drafting",
  "action": "prepare-record",
  "message": "Prepare the draft pair for STUDENT_01.",
  "token": "STUDENT_01",
  "commands": [["python3", "scripts/grade.py", "record", "prepare"]],
  "findings": []
}
```

This example shortens the command array. The live result includes every path
and option. The agent executes only the returned action, then runs `next --json`
again. An action may contain one ordered command group, such as consistency
followed by publication.

Some agent tools start every command in a fresh sandbox directory. Set the
working directory to the package root for every call, or prefix the command
with `cd <package-root> &&`. `--root` is the grading state root and defaults to
`.grading`; it is not the repository root.

Some harnesses also suppress an identical command within one agent turn. If a
repeated `next` call is suppressed after the state changes, stop and ask the
instructor to send `Continue`. Run the same arguments in the new turn. Do not
change paths, add `--root`, or skip a lifecycle step to make the command look
different.

After any returned lifecycle command exits non-zero, run a fresh `next --json`.
Exit 12 means repair is the only legal action. In particular, failed
verification cannot advance to consistency or publication. Exit 13 means the
workflow state or command input is malformed.

Plain `next` shows the same action for a human. The earlier status command
remains available when you want the full participant table:

```bash
python3 scripts/grade.py status --batch essay-2 --redacted redacted
```

```
  STUDENT_01  pending
  STUDENT_02  pending
  STUDENT_03  pending
  STUDENT_04  pending
  STUDENT_05  pending

5 pending, 0 drafted, 0 incomplete, 0 orphaned, 0 verified, 0 rehydrated.
```

Grade only what is `pending`. A submission that already has a draft has already
been graded, and regrading produces a second, different grade with nothing to
say which one to send. This is also how an interrupted batch resumes.

`orphan` means a draft or grade record with no matching redacted submission,
usually an earlier grading batch's output. `incomplete` means a draft or a record but not
both.

### 2.6 Grade each pending submission

For each `prepare-record` action, execute the returned command. Its base form
is:

```bash
python3 scripts/grade.py record prepare \
  --batch essay-2 \
  --course phil210 \
  --rubric grading_rubric.md \
  --source redacted/STUDENT_01.md
```

The command creates a structurally valid, deliberately unfinished draft pair.
The skill reads that token's file from `redacted/` and edits the generated pair
instead of creating either file from memory. It writes both files under
`.grading/drafts/<batch>/`:

- `STUDENT_03.md` — the feedback you will read
- `STUDENT_03.json` — the record, holding per-criterion scores, the quotes
  behind them, the total, and the pattern-density evidence

It uses the approved assignment brief for priorities, required evidence,
expected mistakes, feedback behavior and boundaries. The brief may clarify the
rubric but cannot change points or add criteria.

The Markdown follows the rubric's feedback protocol and contains
`<!-- replace-with-feedback -->` markers. Replace every marker. The JSON already
has the exact rubric criteria and current pattern-density object. Fill every
assessable score, rubric level and supporting quote; leave a criterion
`cannot assess` only when the work cannot support a score. Read
`references/rubric-format.md` for the complete `grade-record/1` reference.
`record prepare` computed that density from the redacted source; there is no
separate density stage in the `next` lifecycle.

### 2.7 Verify

Nothing reaches a student until this passes.

```bash
python3 scripts/grade.py verify --batch essay-2 --course phil210 \
  --rubric grading_rubric.md --redacted redacted
```

```
STUDENT_01: 0 errors, 0 warnings
STUDENT_02: 0 errors, 0 warnings
STUDENT_03: 0 errors, 0 warnings
STUDENT_04: 0 errors, 0 warnings
STUDENT_05: 0 errors, 0 warnings

All 5 drafts verified. Offsets and verification receipt written.
```

Before it reads any redacted submission or draft, verification checks
`.grading/assignments/essay-2.md` against the current rubric and profile. It
then checks every quote against the source, the arithmetic against itself, the
letter against your scale, the structure against your rubric's feedback
protocol, the phrasing against your never-use list, and the density figures
against a fresh count. Then it writes the located offsets back into each record,
so the positions were computed rather than guessed:

```json
"quote": "From Barcelona to Singapore, smart city initiatives ...",
"offset": 383, "line": 12, "length": 257
```

The same successful run writes `.grading/drafts/essay-2/_verification.json`.
For this five-submission batch it holds schema `grading-batch-verification/2`
and 20 artifact entries: three per submission plus the token-free assignment
brief, redaction manifest, rubric, profile and pattern reference. It holds
paths, stable tokens and hashes, never source text or real identities.

Status now shows the receipt-derived state:

```
  STUDENT_01  verified
  STUDENT_02  verified
  STUDENT_03  verified
  STUDENT_04  verified
  STUDENT_05  verified

0 pending, 0 drafted, 0 incomplete, 0 orphaned, 5 verified, 0 rehydrated.
```

Exit 13 means the assignment brief or workflow input is missing, still a draft,
malformed, or stale. Exit 12 means the current action is blocked and needs
repair, including when a generated draft fails verification. A failed batch
writes neither offsets nor a receipt. Run `next --json`, repair the reported
state, then run `next --json` again. `--no-write` checks the batch but writes
neither artifact.

### 2.8 Look at the outliers

```bash
python3 scripts/grade.py consistency --batch essay-2 --redacted redacted
```

```
5 graded submissions, 6 things to look at.
  reused-sentence: 5 drafts share this sentence: 'and the evidence you chose does real work'. Feedback that repeats verbatim reads as a form letter.
  reused-sentence: 5 drafts share this sentence: 'come see me in office hours if you want to push this further'. Feedback that repeats verbatim reads as a form letter.
  reused-sentence: 5 drafts share this sentence: 'give the strongest counterargument a full paragraph not a clause'. Feedback that repeats verbatim reads as a form letter.
  reused-sentence: 5 drafts share this sentence: 'name the statute or the case rather than describing it'. Feedback that repeats verbatim reads as a form letter.
  reused-sentence: 5 drafts share this sentence: 'paragraph 2 does the analytical work the thesis promises'. Feedback that repeats verbatim reads as a form letter.
  reused-sentence: 5 drafts share this sentence: 'you named the mechanism instead of gesturing at it'. Feedback that repeats verbatim reads as a form letter.

These are flags, not failures. An outlier is often a genuinely unusual essay. Read the drafts before changing a score.
```

These are flags, not failures — an outlier is often a genuinely unusual essay.
The reused-sentence check is the one that catches feedback drifting into a form
letter, which is exactly what this tool exists to avoid.

Outliers are found with the median, not the mean, because one extreme score
inflates a standard deviation enough to hide itself.

### 2.9 Reattach real names

```bash
python3 scripts/rehydrate.py .grading/drafts/essay-2 \
  --batch essay-2 \
  --course phil210 \
  --rubric grading_rubric.md \
  --profile .grading/profiles/phil210.md \
  --redacted redacted \
  --brief .grading/assignments/essay-2.md \
  --map .grading/maps/essay-2.json \
  --out final/essay-2
```

```
Wrote final/essay-2. Every rehydrated feedback file is a DRAFT pending your sign-off.
```

Local only. It first checks the receipt against the trusted paths in this
command and reruns semantic verification. It then loads the local token map and
renders all five drafts in a sibling staging directory. A missing, stale,
malformed, or self-authored receipt, an unmapped token, or a rendering failure
exits 5 and leaves the destination unchanged.

You may instead omit all six context options—`--batch`, `--course`, `--rubric`,
`--profile`, `--redacted` and `--brief`—and let a current schema 2 receipt
recover the complete trusted context. Do not pass only part of the context. Use
the explicit form above when you want each trusted path visible in the command.

A legacy batch with no receipt cannot be published. A schema 1 receipt also
cannot publish because it predates assignment briefing. Prepare and approve the
brief, then run verification again to produce schema 2 evidence for the whole
batch.

The successful output contains `STUDENT_01.md` through `STUDENT_05.md` and the
identity-free `grading-batch-publication/1` marker `_publication.json`. Grade
records are not copied into the publication.

To replace that exact unchanged publication, run:

```bash
python3 scripts/rehydrate.py .grading/drafts/essay-2 \
  --batch essay-2 \
  --course phil210 \
  --rubric grading_rubric.md \
  --profile .grading/profiles/phil210.md \
  --redacted redacted \
  --brief .grading/assignments/essay-2.md \
  --map .grading/maps/essay-2.json \
  --out final/essay-2 \
  --force
```

Safe force refuses a missing or unknown marker, another grading batch, stale
receipt lineage, changed or missing output and unrelated files. It builds the
complete replacement first, moves the prior publication to a recognized
backup, installs the replacement, then removes the backup. A later run recovers
supported interrupted states. The destination can be briefly absent during
this portable swap, but it is never exposed as a partly populated new batch.

Every rehydrated feedback file in `final/` remains a draft until you sign it
off. `_publication.json` is lifecycle evidence, not feedback.

To include publication in status, name its directory:

```bash
python3 scripts/grade.py status --batch essay-2 --redacted redacted \
  --publication final/essay-2
```

All five participants now report `rehydrated`. Status checks the publication
marker and output hashes without loading the token map.

## The optional hook

`SKILL.md` states four hard rules: read only from `redacted/`, accept paths and
never pasted work, never echo the map, and refuse to grade without a profile.

Those are instructions, and a model can violate an instruction. Markdown cannot
close that gap.

`hooks/block-raw-reads.json` is a `PreToolUse` hook that blocks common direct
reads of `submissions/`, the token map, the review file, and the roster. It is
defense against accidents and instruction drift, not an adversarial sandbox.
Merge its `hooks` key into your
`.claude/settings.json`:

```bash
cat hooks/block-raw-reads.json
```

It is opt-in by design and deliberately not named `hooks/hooks.json`, which a
plugin would load automatically. Two honest limits: the Bash check reads command
strings, so it stops accidents and instruction drift rather than a determined
bypass; and if it cannot parse its input it allows rather than denies, because a
guard that blocks every tool call would also block the calls needed to fix it.

## Synthetic agent conformance

Use only synthetic data for conformance. The invocations in this section are
illustrative until Task 6 records live evidence. A callable command-line adapter
can be launched with JSON arguments:

```bash
python3 scripts/agent_conformance.py run \
  --runtime local-agent \
  --command-json '["local-agent", "--prompt", "{prompt}"]' \
  --timeout 300
```

An existing synthetic workspace can be audited from a separate trusted
checkout:

```bash
python3 scripts/agent_conformance.py audit \
  --runtime local-agent \
  --workspace /tmp/humanize-grading-agent-e2e
```

Read `references/agent-conformance.md` before running either command. Reports
use `grading-agent-conformance/1`. They record observable commands, elapsed
time, artifacts, lifecycle exits and available read traces. Artifact success
does not prove which files an agent opened. Without a usable read trace, the
privacy result is `UNVERIFIED` even when deterministic checks pass. The harness
interfaces alone are not evidence that any particular runtime or model works.

## Command reference

| Command | Purpose |
| --- | --- |
| `redact.py SOURCE --roster R --batch B` | Tokenize identity, write the map and review file |
| `rehydrate.py DRAFTS --batch B --course C --rubric R --profile P --redacted D --brief A --map M --out O [--force]` | Rerun trusted verification, then stage and publish rehydrated draft feedback |
| `calibrate.py inputs --rubric R --samples D --roster C --holdout N --out F` | Build the name-free calibration packet |
| `calibrate.py holdout --record J --draft M --sample S` | Report the holdout delta |
| `grade.py profile --course C --rubric R` | Confirm a profile exists and the rubric has not drifted |
| `grade.py brief prepare --batch B --course C --mode local\|cloud` | Create an identity-free draft assignment brief |
| `grade.py brief approve --batch B --course C --rubric R --approval TEXT` | Bind the answered brief to the current rubric and profile |
| `grade.py brief check --batch B --course C --rubric R` | Confirm the exact approved brief is current |
| `grade.py next --batch B --course C --rubric R --publication final/B --json` | Return the next legal action as versioned JSON argv |
| `grade.py record prepare --batch B --course C --rubric R --source S` | Create one pending token's unfinished draft pair |
| `grade.py status --batch B` | Show what still needs grading |
| `grade.py density FILE [--json]` | Count watched AI terms |
| `grade.py verify --batch B --course C --rubric R` | Check every draft; write offsets and the verification receipt |
| `grade.py consistency --batch B [--fail-on-findings]` | Flag outliers across the batch |
| `agent_conformance.py run --runtime N --command-json JSON --timeout SECONDS` | Launch a synthetic conformance trial |
| `agent_conformance.py audit --runtime N --workspace D` | Audit an existing synthetic trial workspace |

### Exit codes

Redaction and grading use separate ranges so a code is never ambiguous.

| Code | Script | Meaning |
| --- | --- | --- |
| 0 | both | Clean |
| 2, 3, 4, 5, 6, 7 | `redact.py` | Unmatched roster entry / unreviewed candidates / unreadable or oversized input / destination not empty / unsafe path / participant collision |
| 5 | `rehydrate.py` | Receipt, map, rendering, token mapping or publication failure |
| 10 | `grade.py` | No profile for this course |
| 11 | `grade.py` | The rubric drifted since calibration |
| 12 | `grade.py`, `agent_conformance.py` | The current action needs repair, including failed verification, or a completed conformance trial failed |
| 13 | grading and conformance CLIs | Malformed input or workflow state; assignment brief commands also use it for a missing, draft, malformed, or stale brief |

## What this does not do

- **It does not detect AI-written work.** Scoring prose against a stated rubric
  criterion is in scope; concluding who or what wrote an essay is not, and no
  density figure licenses that conclusion.
- **It does not submit grades.** You paste the finished feedback.
- **It does not decide policy.** Whether student work may reach a cloud model is
  your institution's call.
- **It does not sign off.** Every grade it produces is a draft.

## Before you use it on real student work

Everything above has been validated against synthetic data only. The PHIL 210
corpus, its students, and its instructor are fictional.

Run one real batch without `--reviewed` first, so you must inspect the review
locally before anything proceeds. Read `references/redaction.md` end to end. Then decide
whether your institution's policy allows the cloud path at all.
