# Voice-calibrated grading with a redaction gate

**Date:** 2026-08-23
**Status:** Approved design, not yet implemented

## Summary

Build a grading skill that scores student work against the instructor's rubric
and writes feedback in the instructor's own voice. The skill calibrates itself
from the instructor's rubric and past graded work. It redacts personal
information from student work before that work reaches a cloud model.

The existing Humanizer skill becomes a component. Its 35 patterns move to a
shared reference file and run as a finishing pass on the drafted feedback, so
the comments do not read as chatbot output.

## Goals

- Score each rubric criterion, justify each score with a quote from the work,
  and draft narrative feedback that sounds like the instructor.
- Learn the instructor's standards from their rubric and graded samples.
- Keep student identity out of any cloud model's context.
- Keep the existing Humanizer skill working and publishable.

## Non-goals

The following are out of scope and must not be built:

- Detecting AI-written student work. AI detection is unreliable and produces
  false accusations, so this skill does not judge authorship.
- LMS API integration. The instructor pastes the finished feedback.
- A user interface.
- Shared or multi-instructor profiles.
- Automated grade submission.
- Scanned PDF handling.

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Core job | Feedback in the instructor's voice | The 35 patterns serve the instructor's comments, not a verdict on the student |
| Run shape | Single submission is the core; batch drives it | No rewrite later; redaction is built for batch from day one |
| Calibration content | Distilled profile plus the instructor's comments verbatim | The voice lives in the comments, so student essay text can be dropped |
| Redaction trigger | Ask the instructor at the start of every run | The instructor chose this over a config file or always-on |
| Scoring | Draft score per criterion, instructor signs off | Gives an evidence trail; a calibrated model's grade is not defensible on its own |
| Structure | Grader is the product, Humanizer is a component | Keeps both skills small and keeps the existing package intact |

## Architecture

### Repo layout

```
humanize_grading/
├── SKILL.md                        # grader: entry point and mode router
├── references/
│   ├── ai-patterns.md              # the 35 patterns, moved verbatim
│   ├── calibration.md              # the calibration interview protocol
│   ├── redaction.md                # what redaction catches and what it misses
│   └── rubric-format.md            # the normalized rubric shape
├── scripts/
│   ├── redact.py                   # PII to stable tokens, writes local map
│   ├── rehydrate.py                # tokens back to real names, local only
│   └── validate-package.py         # existing script, needs a fix
├── hooks/
│   └── block-raw-reads.json        # optional PreToolUse hook, opt-in
├── skills/humanizer/SKILL.md       # existing skill, points at references/
├── .gitignore                      # must exclude .grading/ and student data
└── .grading/                       # per-user state, never committed
    ├── profiles/<course>.md        # calibration profile
    ├── maps/<batch>.json           # token to real name, mode 0600
    └── drafts/<batch>/             # draft feedback, still tokenized
```

### Components

| Component | Input | Output | Runs where |
| --- | --- | --- | --- |
| Mode router | The instructor's answer at the gate | Local or cloud path | In-skill |
| Redaction gate | Raw submission paths | Redacted files and a map | Local script |
| Calibration | Rubric and 3 to 8 graded samples | `profiles/<course>.md` | Local only |
| Grading engine | Profile and one redacted file | Scores, justifications, feedback | Either |
| Voice pass | Draft feedback | Feedback without AI tells | Either |
| Batch driver | Folder of redacted files | Loops the engine, flags outliers | Either |
| Rehydrate | Drafts and map | Final feedback with real names | Local script |

### Data flow for one batch

```
/grade submissions/essay-3/ --course eng101
  │
  ├─ 1. Gate: "Local model or cloud?"  asked before any file is read
  │        cloud → redaction is mandatory
  │        local → redaction on by default, explicit opt-out
  │
  ├─ 2. redact.py submissions/essay-3/ → redacted/ + maps/essay-3.json
  │        prints a coverage report and self-identification warnings
  │
  ├─ 3. Load profiles/eng101.md; refuse and offer calibrate if missing
  │
  ├─ 4. For each redacted file:
  │        score each rubric criterion and quote the supporting text
  │        draft narrative feedback from the profile
  │        run the feedback through references/ai-patterns.md
  │        write drafts/essay-3/STUDENT_07.md, still tokenized
  │
  ├─ 5. Consistency pass across the batch
  │
  └─ 6. rehydrate.py drafts/essay-3/ → final/essay-3/, local, real names
           every file stamped DRAFT pending the instructor's sign-off
```

### Invariants

Three properties hold across the whole system. Every other rule depends on them.

1. **The map file is the boundary.** `maps/<batch>.json` is the only artifact
   that holds real identities. Local scripts write and read it. No step between
   redaction and rehydration loads it.
2. **The model reads only from `redacted/`.** The grading engine must never open
   a path under `submissions/`.
3. **Calibration and grading are separate commands.** `/grade calibrate` is
   local-only. `/grade` is not. Bundling them would force calibration's
   constraint onto every grading run.

## The redaction gate

### Four detection layers

| Layer | Catches | Reliability | Dependency |
| --- | --- | --- | --- |
| 1. Roster match | The author and classmates, by name, ID, or email | Near-total | None |
| 2. Structured patterns | Emails, phones, student IDs, profile URLs | High | None |
| 3. Filename and metadata | `smith_jane_essay3.docx`, the docx author field | High | None |
| 4. NER fallback | People named inside the essay | Mediocre | Optional |

Layer 1 makes the gate trustworthy. Name detection by model is unreliable, but
the instructor already has a class roster, which turns author identity into an
exact-match problem. `redact.py` takes a roster CSV once per course and
tokenizes every occurrence, including possessives and first-name-only mentions.

Layer 4 must stay optional and dependency-free by default, matching the existing
script. It runs if `presidio` or `spacy` is installed. If neither is present,
unmatched capitalized candidates must be flagged for review, never passed
silently. An unreviewed flag blocks the cloud path.

### Redact identity, not subject matter

Over-redaction destroys gradeability. An essay with every place name and date
removed cannot be assessed.

So identity-bearing entities must be tokenized and content entities must be left
alone:

- **Tokenize:** author name, classmates, emails, phone numbers, student IDs,
  filenames, document metadata.
- **Keep:** places, historical figures, and dates that the essay is about.

The skill must state this choice to the instructor. It must not imply that
everything is scrubbed.

### What redaction cannot catch

Autobiographical content identifies its author to anyone who knows the class. No
tokenizer will catch "my grandmother's escape from Saigon in 1975."

`redact.py` must run a heuristic warning pass. When first-person language
clusters with family, place, and date markers, it flags the file as "author may
be identifiable from content," quotes the specific sentences, and leaves the
decision to the instructor. This is a warning, not a block.

Pseudonymization is not anonymization. `references/redaction.md` must say so in
plain terms.

### Fail closed

`redact.py` must exit non-zero when any of the following happens:

- A roster entry never appears in any file.
- A layer 4 candidate is flagged and unreviewed.
- Text extraction fails.
- A file cannot be read.

A non-zero exit must stop the cloud path. There is no override flag. Silent
partial success is the failure mode that matters, so the script must not have
one.

### The map file

- Path: `.grading/maps/<batch>.json`
- Mode: `0600`
- Shape: `{"STUDENT_07": {"name": "...", "id": "...", "file": "..."}}`
- Written by `redact.py`, read by `rehydrate.py`, loaded by nothing else.

`rehydrate.py` must fail when a token in the drafts has no mapping. A stray
`STUDENT_12` pasted into an LMS comment is worth failing the run over.

### Rules in the skill, and their limit

`SKILL.md` must state four hard rules:

1. Read only from `redacted/`.
2. Accept file paths. Never accept pasted student work.
3. Never echo the map file.
4. Refuse to grade without a profile.

These are instructions, and a model can violate an instruction. Markdown cannot
close that gap. So `hooks/block-raw-reads.json` ships as an optional
`PreToolUse` hook that denies reads of `submissions/` and `maps/`. It is Claude
Code specific and opt-in. The skill must work without it, so portability holds.
The README must describe the hook as the difference between a rule and a
guarantee.

### File formats

- `.txt` and `.md`: handled natively.
- `.docx`: handled through `python-docx` when installed. Extraction must drop
  the document author field. That field is the most commonly missed leak.
- `.pdf`: the instructor must convert first. The skill must not pretend to
  handle scanned submissions.

## Calibration

Command: `/grade calibrate --course eng101`. It must refuse when the instructor
answers "cloud" at the gate.

Inputs: the rubric, plus 3 to 8 pieces of work the instructor already graded,
including the work, their comments, and their scores. A short interview then
fills gaps the samples cannot show, such as where an A begins when every sample
is B-range.

### Profile contents

| Section | Holds |
| --- | --- |
| Rubric, normalized | Criteria, levels, weights, point values |
| Weighting in practice | The gap between the rubric and the instructor's behavior |
| Severity | Where the instructor lands against the rubric's language; score spread |
| Feedback structure | Opening, praise-to-critique ratio, closing, typical length |
| Voice | Sentence length, person, questions or directives, habitual hedges |
| Comment bank | The instructor's own sentences, verbatim |
| Never-use list | Phrases the instructor does not say |

The weighting row carries most of the value. A rubric may weight citation errors
and thesis errors equally while the samples show the instructor forgives
citations and punishes a weak thesis. No rubric records that, and it is most of
what makes feedback sound like a specific person.

Student essay text must not be stored in the profile. Only the instructor's
comments are kept verbatim.

### Holdout validation

Calibration must hold one graded sample back, grade it blind against the
finished profile, and report the delta: the instructor's score against the
generated score, and the instructor's comments against the generated comments.

That report is the deliverable of `calibrate`, not the profile file. A bad
profile must surface in the first minute rather than on the fortieth essay.

### Recalibration

Profiles are plain Markdown, dated, and editable by hand. The instructor can add
graded work over time and rerun calibration.

## Error handling

Every condition below refuses rather than guesses.

| Condition | Behavior |
| --- | --- |
| No profile for the course | Refuse. Offer `calibrate`. |
| Rubric changed since calibration | Warn, name the drifted criteria, ask before proceeding |
| Student text pasted instead of a path | Refuse. State that the text is already in context and what that means. |
| `redact.py` exits non-zero | The cloud path refuses. No override. |
| No roster supplied | Layer 1 is off. Warn. The cloud path requires explicit acknowledgment. |
| Criterion not assessable from the work | Score it "cannot assess". Never infer a score. |
| No supporting quote for a score | Give no score. Flag it for the instructor. |
| Batch interrupted | Resume. Skip files that already have drafts. |
| Unmapped token at rehydrate | Fail the run. |
| Empty or corrupt submission | Skip it, report it, continue the batch. |

## Testing

The leak test is the one that matters. Everything else supports it.

- **Leak test:** a fixture submission seeded with about 20 known PII instances
  across all four layers, plus a roster. Assert that zero survive into
  `redacted/`.
- **Round trip:** `redact` then `rehydrate` reproduces the original text exactly.
- **Metadata test:** a `.docx` fixture with a populated author field. Assert the
  field is stripped.
- **Filename test:** a fixture named `smith_jane_essay3.md`. Assert the name does
  not survive.
- **Fail-closed tests:** one per non-zero exit condition.
- **Package validation:** extend `validate-package.py` to check that the 35
  patterns stay numbered 1 through 35 in `references/ai-patterns.md`, that
  versions stay in sync, and that each `SKILL.md` stays within its line cap.

`validate-package.py` currently crashes because `.claude-plugin/plugin.json` is
missing from this copy. Fix that before extending it.

### Package rules that must change

The new layout breaks three existing rules. Each must be updated deliberately,
not worked around.

| Rule today | Where | Change |
| --- | --- | --- |
| Exactly one `SKILL.md`, at the repo root | `validate-package.py` | Allow the root grader plus `skills/humanizer/SKILL.md`. Assert both exist. |
| Pattern numbers 1 through 35 live in `SKILL.md` | `validate-package.py` | Move the check to `references/ai-patterns.md`. Do not duplicate it. |
| "`SKILL.md` is the repo's only skill file" | `AGENTS.md` | Rewrite to describe the grader, the Humanizer component, and the shared reference. |

`AGENTS.md` also requires `SKILL.md` and `README.md` to stay in sync. That rule
still holds and now covers both skills.

## Build order

Each step must pass its own tests before the next begins.

1. Fix `validate-package.py` and restore `.claude-plugin/plugin.json`.
2. Extract the 35 patterns to `references/ai-patterns.md`. Move the Humanizer
   skill to `skills/humanizer/SKILL.md`. Update the validator and `AGENTS.md`.
   No behavior change.
3. Build `redact.py` and `rehydrate.py` with the full test suite, including the
   leak test. These stand alone and need no skill.
4. Write `references/redaction.md` and the grader `SKILL.md` gate and rules.
5. Build calibration, including holdout validation.
6. Build the single-submission grading engine and the voice pass.
7. Build the batch driver and the consistency pass.
8. Ship `hooks/block-raw-reads.json` and document it.

Steps 1 through 3 carry no student data and can be verified with fixtures alone.

## Open risks

- **Pseudonymization is not anonymization.** Content can identify an author even
  after every name is tokenized. The warning pass reduces this risk; it does not
  remove it.
- **Institutional policy governs.** FERPA and local policy decide whether student
  work may reach a cloud model. This design does not make that decision and must
  not imply it has been made.
- **Instructions are not enforcement.** Without the optional hook, the read rules
  depend on model compliance.
- **The gate trusts the answer.** A run on a cloud model where the instructor
  answers "local" skips redaction. The skill cannot detect its own host, so this
  risk is inherent to asking each run. Redaction stays on by default in local
  mode to narrow it, and opting out must require an explicit flag rather than a
  default.
- **Calibration samples are small.** A profile built from 3 to 8 samples captures
  habits, not the full range of the instructor's judgment. Draft status and
  sign-off exist because of this.
