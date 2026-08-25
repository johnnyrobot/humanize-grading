# Using the grader

A walkthrough of the whole system, from a folder of student essays to feedback
you can paste into your LMS.

Every command here was run against the synthetic PHIL 210 corpus in
`tests/fixtures/phil210/`, and the output shown is real. You can follow along
with that corpus before you point this at anything of your own.

## What you are working with

| Piece | What it does | Where it runs |
| --- | --- | --- |
| `scripts/redact.py` | Replaces student identity with stable tokens | Your machine only |
| `scripts/calibrate.py` | Builds calibration inputs, reports the holdout delta | Your machine only |
| `scripts/grade.py` | Checks the profile, surveys a batch, counts patterns, verifies drafts, flags outliers | Your machine only |
| `scripts/rehydrate.py` | Puts real names back into finished feedback | Your machine only |
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
- **Pseudonymization is not anonymization.** "My grandmother's escape from
  Saigon in 1975" names nobody and still identifies its author to anyone who
  knows the class. The warning pass flags passages like that. It does not remove
  them, and it does not catch every one.
- **Your institution decides.** FERPA and local policy govern whether student
  work may reach a cloud model. This tool does not make that decision.

Subject matter is kept on purpose. Places, statutes, historical figures and the
dates an essay is about all survive, because removing them would make the work
impossible to grade.

## Part 1 — Calibrate, once per course

Calibration learns two things your rubric cannot record: where you actually land
against your own scale, and how you write.

**It is local-only.** It reads work you have already graded, which still carries
names. Do not run it against a cloud model.

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

## Part 2 — Grade a batch

### 2.1 Answer the gate

The skill asks before it reads anything:

> Are you running me on a local model or a cloud model?

Cloud means redaction is mandatory. Local means redaction is on by default and
opting out takes an explicit flag. The skill cannot detect its own host, so this
answer is yours to give honestly.

### 2.2 Redact

```bash
python3 scripts/redact.py submissions/essay-2 \
  --roster roster.csv \
  --batch essay-2 \
  --out redacted \
  --instructor "Marcus Vance"
```

```
Read 5 files. Wrote redacted. Map at .grading/maps/essay-2.json.
  0 names to review, 0 self-identifying passages, 0 bare-name substitutions.
  See .grading/maps/reviews/essay-2.json.
```

Pass `--instructor` with your own name so you are never redacted. Repeat the
flag for more than one name.

**Read the review file.** It lives beside the map, not in `redacted/`, because
it holds the very names redaction just removed. It lists candidate names for
your judgement, passages that may identify their author, bare surnames that were
substituted, and any submission that was empty. Then re-run with `--reviewed`.

Output files are named by token, so a filename can never carry a name.

**A non-zero exit stops the run.** The codes:

| Exit | Meaning |
| --- | --- |
| 2 | A roster entry never appeared in any file — usually someone did not submit |
| 3 | Layer-4 candidates need review; re-run with `--reviewed` |
| 4 | A file could not be read |
| 5 | The destination is not empty; use `--force` to clear the prior run's outputs |

Exit 2 is common and benign — it means your roster is bigger than the batch.
Trim the roster to who actually submitted, or accept that those students are
missing.

### 2.3 Confirm the profile is current

```bash
python3 scripts/grade.py profile --course phil210 --rubric grading_rubric.md
```

```
Profile for phil210 is current, calibrated 2026-08-24 from 4 samples.
```

If you have edited the rubric since calibrating, this names the drifted criteria
and exits 11, because grading against a changed criterion applies standards
calibrated for different words.

### 2.4 See what the batch owes

```bash
python3 scripts/grade.py status --batch essay-2 --redacted redacted
```

```
  STUDENT_01  pending
  STUDENT_02  pending
  STUDENT_03  pending
  STUDENT_04  pending
  STUDENT_05  pending

5 pending, 0 drafted, 0 incomplete, 0 orphaned.
```

Grade only what is `pending`. A submission that already has a draft has already
been graded, and regrading produces a second, different grade with nothing to
say which one to send. This is also how an interrupted batch resumes.

`orphan` means a draft with no matching source, usually a prior run's output.
`incomplete` means a draft or a record but not both.

### 2.5 Grade each pending submission

The skill reads one file at a time from `redacted/` and writes two files to
`.grading/drafts/<batch>/`:

- `STUDENT_03.md` — the feedback you will read
- `STUDENT_03.json` — the record, holding per-criterion scores, the quotes
  behind them, the total, and the pattern-density evidence

For criterion-4 evidence it runs:

```bash
python3 scripts/grade.py density redacted/STUDENT_02.md --json
```

```
STUDENT_02.md: 383 words, 0 watched terms
  0.0 per 1000 words

This is evidence for the prose criterion. It says nothing about who wrote
the essay, and must never be used to claim it does.
```

### 2.6 Verify

Nothing reaches a student until this passes.

```bash
python3 scripts/grade.py verify --batch essay-2 --course phil210 \
  --rubric grading_rubric.md --redacted redacted
```

```
STUDENT_01: 0 errors, 0 warnings
...
All 5 drafts verified. Offsets written.
```

It checks every quote against the source, the arithmetic against itself, the
letter against your scale, the structure against your rubric's feedback
protocol, the phrasing against your never-use list, and the density figures
against a fresh count. Then it writes the located offsets back into each record,
so the positions were computed rather than guessed:

```json
"quote": "From Barcelona to Singapore, smart city initiatives ...",
"offset": 383, "line": 12, "length": 257
```

Exit 12 means at least one draft failed. Fix it and run again. Do not rehydrate
a batch that failed verification.

### 2.7 Look at the outliers

```bash
python3 scripts/grade.py consistency --batch essay-2 --redacted redacted
```

```
5 graded submissions, 11 things to look at.
  score-outlier: STUDENT_03 scored 15 on 1. Thesis & Theoretical Framing while
    the batch median was 21. Check the quotes behind it.
  density-inversion: STUDENT_01 has 31.5 watched terms per 1000 words and scored
    23 on criterion 4. STUDENT_05 has 2.93 and scored 21. Density is evidence,
    not proof, so read both before changing either score.
  reused-sentence: 5 drafts share this sentence: 'come see me in office hours if
    you want to push this further'. Feedback that repeats verbatim reads as a
    form letter.
```

These are flags, not failures — an outlier is often a genuinely unusual essay.
The reused-sentence check is the one that catches feedback drifting into a form
letter, which is exactly what this tool exists to avoid.

Outliers are found with the median, not the mean, because one extreme score
inflates a standard deviation enough to hide itself.

### 2.8 Reattach real names

```bash
python3 scripts/rehydrate.py .grading/drafts/essay-2 \
  --map .grading/maps/essay-2.json \
  --out final/essay-2
```

```
Wrote final/essay-2. Every file is a DRAFT pending your sign-off.
```

Local only. It fails when a token has no mapping, which is correct: a stray
`STUDENT_12` pasted into an LMS comment is worth failing the run over.

Everything in `final/` is a draft until you sign it off.

## The optional hook

`SKILL.md` states four hard rules: read only from `redacted/`, accept paths and
never pasted work, never echo the map, and refuse to grade without a profile.

Those are instructions, and a model can violate an instruction. Markdown cannot
close that gap.

`hooks/block-raw-reads.json` is a `PreToolUse` hook that denies reads of
`submissions/`, the token map, the review file, and the roster. It is the
difference between a rule and a guarantee. Merge its `hooks` key into your
`.claude/settings.json`:

```bash
cat hooks/block-raw-reads.json
```

It is opt-in by design and deliberately not named `hooks/hooks.json`, which a
plugin would load automatically. Two honest limits: the Bash check reads command
strings, so it stops accidents and instruction drift rather than a determined
bypass; and if it cannot parse its input it allows rather than denies, because a
guard that blocks every tool call would also block the calls needed to fix it.

## Command reference

| Command | Purpose |
| --- | --- |
| `redact.py SOURCE --roster R --batch B` | Tokenize identity, write the map and review file |
| `rehydrate.py DRAFTS --map M --out O` | Put real names back |
| `calibrate.py inputs --rubric R --samples D --roster C --holdout N --out F` | Build the name-free calibration packet |
| `calibrate.py holdout --record J --draft M --sample S` | Report the holdout delta |
| `grade.py profile --course C --rubric R` | Confirm a profile exists and the rubric has not drifted |
| `grade.py status --batch B` | Show what still needs grading |
| `grade.py density FILE [--json]` | Count watched AI terms |
| `grade.py verify --batch B --course C --rubric R` | Check every draft; write quote offsets |
| `grade.py consistency --batch B [--fail-on-findings]` | Flag outliers across the batch |

### Exit codes

Redaction and grading use separate ranges so a code is never ambiguous.

| Code | Script | Meaning |
| --- | --- | --- |
| 0 | both | Clean |
| 2, 3, 4, 5 | `redact.py` | Unmatched roster entry / unreviewed candidates / unreadable file / destination not empty |
| 10 | `grade.py` | No profile for this course |
| 11 | `grade.py` | The rubric drifted since calibration |
| 12 | `grade.py` | A draft failed verification |
| 13 | both CLIs | Malformed input |

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

Run one real batch without `--reviewed` first, so the review file has to be read
before anything proceeds. Read `references/redaction.md` end to end. Then decide
whether your institution's policy allows the cloud path at all.
