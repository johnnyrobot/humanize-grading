# Instructor-Guided Assignment Briefing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Require an instructor-approved, identity-free assignment brief before an agent reads submissions, and bind that brief to grading-batch verification and publication.

**Architecture:** A focused `scripts/grading/brief.py` module owns the strict Markdown format, safe writes, approval, and digest checks. `scripts/grade.py` exposes prepare/approve/check commands, while `GradingBatch.verify()` treats the approved brief as required batch evidence and version 2 receipts bind it through publication. Agent instructions and public documentation guide the instructor through course calibration, assignment questions, exact approval, grading, and final sign-off.

**Tech Stack:** Python 3 standard library, pytest, Markdown agent instructions, JSON verification receipts.

**Spec:** `docs/superpowers/specs/2026-08-26-assignment-briefing-design.md`

## Global Constraints

- The assignment brief path is `.grading/assignments/<batch>.md` by default.
- The assignment brief schema is exactly `grading-assignment-brief/1`.
- The approval phrase is exactly `Approve assignment brief for <batch>`.
- Approved modes are exactly `local` and `cloud`; fully local is recommended.
- The brief body must contain `## Assignment`, `## Grading priorities`, `## Required evidence`, `## Common mistakes`, `## Feedback`, and `## Boundaries`.
- Approval must refuse while `<!-- answer-required -->` remains in the body.
- An approved brief binds the SHA-256 of its body, rubric, and calibration profile.
- The verification receipt schema is exactly `grading-batch-verification/2` and contains exactly one token-free `assignment-brief` artifact.
- A schema 1 verification receipt cannot publish; the user must approve a brief and verify again.
- `scripts/grading/` must never import or load the token map.
- The brief must not contain student identity, student work, roster content, or token-map content; this remains an agent rule because the parser has no roster.
- Calibration and grading remain separate commands, and calibration remains local-only.
- Rehydrated feedback remains a draft pending instructor sign-off.
- Keep `SKILL.md` and `README.md` in sync and use Plain Language.
- Use package version `3.3.0` in both skills, the first README version entry, and `.claude-plugin/plugin.json`.
- Update `PACKAGE_FILES.txt` for every added tracked file.

---

### Task 1: Assignment Brief Domain Module

**Files:**
- Create: `scripts/grading/brief.py`
- Create: `tests/grading/test_brief.py`

**Interfaces:**
- Consumes: `scripts.path_safety.read_text_limited`, `scripts.path_safety.require_component`, and `scripts.redaction.private_files.write_private_text`.
- Produces: `BRIEF_SCHEMA`, `BriefError`, `AssignmentBrief`, `brief_path()`, `prepare_brief()`, `approve_brief()`, and `check_brief()` for Tasks 2–4.

- [ ] **Step 1: Write failing tests for path safety, preparation, and parsing**

Create `tests/grading/test_brief.py` with focused tests equivalent to:

```python
from pathlib import Path

import pytest

from scripts.grading.brief import (
    BriefError,
    approve_brief,
    brief_path,
    check_brief,
    prepare_brief,
)


def _answer(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace("<!-- answer-required -->", "Instructor answer."),
        encoding="utf-8",
    )


def test_brief_path_uses_one_safe_batch_component(tmp_path):
    assert brief_path(tmp_path, "essay-3") == tmp_path / "assignments" / "essay-3.md"
    with pytest.raises(ValueError, match="grading batch"):
        brief_path(tmp_path, "../essay-3")


def test_prepare_creates_an_owner_only_draft_without_overwriting(tmp_path):
    path = brief_path(tmp_path, "essay-3")
    brief = prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    assert brief.status == "draft"
    assert brief.mode == "local"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(BriefError, match="already exists"):
        prepare_brief(path, batch="essay-3", course="phil210", mode="local")


def test_prepare_rejects_unknown_mode(tmp_path):
    with pytest.raises(BriefError, match="local or cloud"):
        prepare_brief(
            brief_path(tmp_path, "essay-3"),
            batch="essay-3",
            course="phil210",
            mode="hybrid",
        )
```

- [ ] **Step 2: Run the new test file and confirm RED**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_brief.py -v
```

Expected: collection fails because `scripts.grading.brief` does not exist.

- [ ] **Step 3: Implement the strict draft format and exclusive safe creation**

In `scripts/grading/brief.py`, define these constants and public types:

```python
BRIEF_SCHEMA = "grading-assignment-brief/1"
BRIEF_LIMIT = 1_048_576
ANSWER_REQUIRED = "<!-- answer-required -->"
MODES = {"local", "cloud"}
REQUIRED_HEADINGS = (
    "Assignment",
    "Grading priorities",
    "Required evidence",
    "Common mistakes",
    "Feedback",
    "Boundaries",
)


class BriefError(ValueError):
    """The assignment brief is missing, malformed, unapproved, or stale."""


@dataclass(frozen=True)
class AssignmentBrief:
    path: Path
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
```

Implement `brief_path(root, batch)` with `require_component()`. Implement a
strict front-matter parser that splits only the first `---` block, rejects
duplicate and unknown keys, requires all ten metadata keys, validates safe
course/batch components, validates `draft|approved`, validates `local|cloud`,
and requires every exact `##` heading. Draft digest and approval fields may be
empty; approved values must be valid.

Implement exclusive preparation with a same-directory `mkstemp()`, `fsync()`,
`chmod(0o600)`, and `os.link(temporary, path)` so an existing brief is never
replaced. The prepared body must use the six exact headings and one
`ANSWER_REQUIRED` marker under each.

- [ ] **Step 4: Run preparation tests and confirm GREEN**

Run the Task 1 test command. Expected: the three preparation tests pass.

- [ ] **Step 5: Write failing approval and invalidation tests**

Add tests that:

```python
def test_approval_requires_the_exact_batch_phrase(tmp_path, phil210, profile):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    with pytest.raises(BriefError, match="Approve assignment brief for essay-3"):
        approve_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
            approval="yes",
        )


def test_approved_brief_binds_body_rubric_and_profile(tmp_path, phil210, profile):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    approved = approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
        approval="Approve assignment brief for essay-3",
    )
    assert approved.status == "approved"
    assert approved.approved_at.endswith("Z")
    assert len(approved.body_sha256) == 64
    assert check_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    ) == approved


@pytest.mark.parametrize("changed", ["body", "rubric", "profile"])
def test_changes_invalidate_approval(tmp_path, phil210, profile, changed):
    path = brief_path(tmp_path, "essay-3")
    rubric = tmp_path / "rubric.md"
    profile_copy = tmp_path / "profile.md"
    rubric.write_bytes((phil210 / "grading_rubric.md").read_bytes())
    profile_copy.write_bytes(profile.read_bytes())
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=rubric,
        profile=profile_copy,
        approval="Approve assignment brief for essay-3",
    )
    target = {"body": path, "rubric": rubric, "profile": profile_copy}[changed]
    target.write_text(target.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    with pytest.raises(BriefError, match="changed|stale"):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=rubric,
            profile=profile_copy,
        )
```

Also test unknown keys, duplicate keys, missing headings, remaining answer
markers, malformed hashes, wrong course, wrong batch, symlink paths, and
reapproval after a deliberate edit.

- [ ] **Step 6: Confirm the new approval tests fail**

Run the Task 1 test command. Expected: failures name the unimplemented approval
and check behavior.

- [ ] **Step 7: Implement approval and checking**

Implement SHA-256 over UTF-8 body text and raw rubric/profile bytes read with
the public size bounds. `approve_brief()` must:

1. load and validate the draft;
2. reject `ANSWER_REQUIRED`;
3. require the exact approval phrase for the safe batch component;
4. compute all three hashes;
5. set an ISO-8601 UTC timestamp without microseconds;
6. render metadata in one stable order; and
7. atomically replace the brief through `write_private_text()`.

`check_brief()` must require `status: approved`, the exact approval phrase,
valid lowercase SHA-256 strings, an internally current body digest, and current
rubric/profile digests. Return the fully parsed `AssignmentBrief` on success.

- [ ] **Step 8: Run focused and full tests**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_brief.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

Expected: all brief tests and the 428-test baseline plus new tests pass with no
warnings.

- [ ] **Step 9: Commit Task 1**

```bash
git add scripts/grading/brief.py tests/grading/test_brief.py
git commit -m "feat: add approved assignment briefs"
```

---

### Task 2: Assignment Brief CLI

**Files:**
- Modify: `scripts/grade.py`
- Modify: `tests/grading/test_brief.py`

**Interfaces:**
- Consumes: Task 1 `brief_path()`, `prepare_brief()`, `approve_brief()`, and `check_brief()`.
- Produces: `grade.py brief prepare|approve|check`, with exit codes 0, 10, 11, and 13 for later documentation.

- [ ] **Step 1: Write failing CLI tests**

Add subprocess tests that copy the synthetic profile into
`<root>/profiles/phil210.md` and assert:

```python
def _grade(*args: str):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args],
        capture_output=True,
        text=True,
    )


def test_brief_cli_prepares_approves_and_checks(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_bytes(profile.read_bytes())
    common = ["--batch", "essay-3", "--course", "phil210", "--root", str(root)]

    prepared = _grade("brief", "prepare", *common, "--mode", "local")
    assert prepared.returncode == 0, prepared.stderr
    path = root / "assignments" / "essay-3.md"
    _answer(path)

    approved = _grade(
        "brief", "approve", *common,
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--approval", "Approve assignment brief for essay-3",
    )
    assert approved.returncode == 0, approved.stderr
    checked = _grade(
        "brief", "check", *common,
        "--rubric", str(phil210 / "grading_rubric.md"),
    )
    assert checked.returncode == 0, checked.stderr
    assert "approved assignment brief" in checked.stdout.lower()
```

Add tests for missing profile (10), rubric drift (11), malformed/stale brief
(13), duplicate prepare refusal, and help output showing all three actions.

- [ ] **Step 2: Run CLI tests and confirm RED**

Run the Task 1 focused command. Expected: argparse rejects `brief`.

- [ ] **Step 3: Add nested CLI parsers and handlers**

Add `brief` to the module docstring and a nested parser with shared exact args:

```python
brief = subparsers.add_parser("brief", help="Prepare or check an assignment brief")
brief_actions = brief.add_subparsers(dest="brief_action", required=True)

prepare = brief_actions.add_parser("prepare", help="Prepare a draft assignment brief")
prepare.add_argument("--batch", required=True, type=_component("The grading batch"))
prepare.add_argument("--course", required=True, type=_component("The course"))
prepare.add_argument("--mode", required=True, choices=("local", "cloud"))
prepare.add_argument("--brief", type=Path, default=None)
_add_common(prepare)
prepare.set_defaults(handler=_cmd_brief_prepare)
```

`approve` and `check` use the same batch/course/root/brief args and require
`--rubric`; `approve` additionally requires `--approval`. Resolve an omitted
brief with `brief_path(args.root, args.batch)`.

Before approval or check, use `_profile_or_exit()`, `load_rubric()`, and
`profile.drift()` so missing profiles retain exit 10 and rubric drift retains
exit 11. Catch `BriefError`, `OSError`, `UnicodeError`, and `ValueError`, print
one `ERROR assignment-brief:` line to stderr, and return 13. Successful output
must state the exact file and whether it is draft or approved.

- [ ] **Step 4: Run focused and full tests**

Run the two Task 1 verification commands. Expected: all tests pass with no
warnings.

- [ ] **Step 5: Commit Task 2**

```bash
git add scripts/grade.py tests/grading/test_brief.py
git commit -m "feat: guide assignment brief approval"
```

---

### Task 3: Verification Receipt Schema 2

**Files:**
- Modify: `scripts/grading/batch.py`
- Modify: `tests/conftest.py`
- Modify: `tests/grading/test_verify.py`
- Modify: `tests/grading/test_batch.py`
- Modify: `tests/grading/test_publication.py`

**Interfaces:**
- Consumes: Task 1 `BriefError`, `check_brief()`, and `brief_path()`.
- Produces: `VerificationInputs.brief`, early approval gating, receipt schema 2, and the `assignment-brief` evidence role for Task 4.

- [ ] **Step 1: Add an approved-brief fixture and failing lifecycle tests**

In `tests/conftest.py`, add a callable fixture that prepares a brief, replaces
all answer markers with identity-free synthetic answers, approves it against
the supplied rubric/profile, and returns its path:

```python
@pytest.fixture
def approved_brief():
    def make(path, *, batch, course, rubric, profile, mode="local"):
        from scripts.grading.brief import approve_brief, prepare_brief

        prepare_brief(path, batch=batch, course=course, mode=mode)
        text = path.read_text(encoding="utf-8").replace(
            "<!-- answer-required -->", "Use the rubric and calibrated profile."
        )
        path.write_text(text, encoding="utf-8")
        approve_brief(
            path,
            batch=batch,
            course=course,
            rubric=rubric,
            profile=profile,
            approval=f"Approve assignment brief for {batch}",
        )
        return path

    return make
```

Add tests proving:

- `VerificationInputs` requires `brief`;
- a missing or stale brief yields `malformed-assignment-brief`, no checked
  tokens, no record writes, and no receipt;
- brief failure occurs before `_status()` or batch artifact reads;
- a clean receipt uses schema `grading-batch-verification/2`;
- roles include one token-free `assignment-brief` in sorted order;
- editing the brief after verification makes the receipt stale; and
- a manually downgraded schema 1 receipt cannot resume publication.

The early-read test may monkeypatch the opened batch's `_status` method to
raise `AssertionError("batch artifacts were read")`; verification with an
absent brief must return the brief finding without triggering that assertion.

- [ ] **Step 2: Run the focused lifecycle tests and confirm RED**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_verify.py tests/grading/test_publication.py -x -v
```

Expected: constructions and receipt expectations fail because the brief is not
yet a verification input.

- [ ] **Step 3: Add the approval gate before any batch read**

Change the public input type to:

```python
@dataclass(frozen=True)
class VerificationInputs:
    """The current identity-free evidence used to verify a grading batch."""

    course: str
    rubric: Path
    profile: Path
    patterns: Path
    brief: Path
```

At the first line of `GradingBatch.verify()`, call `check_brief()` with the
batch, course, rubric, and profile. On `BriefError`, `OSError`, `UnicodeError`,
or `ValueError`, return a `VerificationReport` with an empty `StatusReport`, one
`malformed-assignment-brief` blocking finding, no checked tokens, no receipt,
`persisted=False`, and `VerificationFailure.MALFORMED`. Add that code to
`_MALFORMED_CODES`.

Only after this gate succeeds may verification call `_status()`, compute batch
digests, or load entries.

- [ ] **Step 4: Bind the brief into receipt schema 2**

Make these exact structural changes:

```python
RECEIPT_SCHEMA = "grading-batch-verification/2"
_BATCH_EVIDENCE_ROLES = {"assignment-brief", "redaction-manifest"}
```

Add `(inputs.brief, "assignment brief", 1_048_576)` to
`_verification_digests()` and `("assignment-brief", inputs.brief)` to
`_write_verification_receipt()`. Receipt validation must require exactly one of
each batch evidence role, require `.md` for `assignment-brief`, forbid tokens on
all course and batch evidence, and use a one-megabyte read limit.

Special-case `grading-batch-verification/1` in `_decode_receipt()` with a plain
message explaining that it predates assignment briefing and must be verified
again after approval. Keep every other unknown schema error unchanged.

In `_context_from_receipt()`, add
`brief=(Path(".grading") / "assignments" / f"{decoded.batch}.md").resolve()` to
the recovered `VerificationInputs`.

- [ ] **Step 5: Update every lifecycle test input deliberately**

For each `VerificationInputs(...)` in the listed test files, create the brief
through the fixture using the exact batch/course/rubric/profile used by that
test and pass `brief=brief`. Do not add a permissive default to production code
and do not hand-write fake approved metadata.

Update role lists and schema assertions to include `assignment-brief` and
schema 2. Preserve tests that deliberately mutate or forge receipt artifacts;
their expected failure class remains stale or malformed as appropriate.

- [ ] **Step 6: Run focused and full tests**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_verify.py tests/grading/test_batch.py tests/grading/test_publication.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

Expected: all lifecycle tests and the full suite pass with no warnings.

- [ ] **Step 7: Commit Task 3**

```bash
git add scripts/grading/batch.py tests/conftest.py tests/grading/test_verify.py tests/grading/test_batch.py tests/grading/test_publication.py
git commit -m "feat: bind assignment briefs to verification"
```

---

### Task 4: Rehydration and End-to-End Context

**Files:**
- Modify: `scripts/rehydrate.py`
- Modify: `scripts/grade.py`
- Modify: `tests/grading/test_end_to_end.py`
- Modify: `tests/grading/test_publication.py`
- Modify: any remaining test file that constructs `VerificationInputs`

**Interfaces:**
- Consumes: Task 3 `VerificationInputs.brief` and schema 2 receipt behavior.
- Produces: complete explicit rehydration context with `--brief`, receipt-recovered resume, and an end-to-end approved grading chain.

- [ ] **Step 1: Write failing explicit and recovered-context tests**

Update the publication command helper and add assertions that:

- explicit rehydration requires batch, course, rubric, profile, redacted, and
  brief together;
- omitting only `--brief` returns context-incomplete before loading the map;
- an explicit current brief publishes;
- omitting all six context values recovers the schema 2 receipt context and
  publishes;
- a schema 1 receipt returns the migration message and leaves the destination
  absent; and
- changing the brief after verification blocks publication.

Update the end-to-end fixture to create and approve
`tmp_path/.grading/assignments/essay-2.md`, pass it to verification, and include
`--brief` in the explicit rehydration subprocess.

- [ ] **Step 2: Run focused tests and confirm RED**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_end_to_end.py tests/grading/test_publication.py -x -v
```

Expected: argparse or context validation fails because `--brief` is not wired.

- [ ] **Step 3: Add the explicit brief argument and trusted context rule**

In `scripts/rehydrate.py`, add `parser.add_argument("--brief", type=Path)` and
include it in the all-or-none supplied tuple. Change the error text to name all
six values. In the explicit branch, pass `brief=args.brief` to
`VerificationInputs`. The receipt-recovered branch continues through
`VerifiedBatch.context_from_receipt()`.

In `scripts/grade.py`, pass the default `brief_path(args.root, args.batch)` to
the verify command's `VerificationInputs`.

Search the entire test tree for `VerificationInputs(` and update every remaining
construction with an approved brief matched to its exact batch, rubric, and
profile.

- [ ] **Step 4: Run focused and full tests**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_end_to_end.py tests/grading/test_publication.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

Expected: the full synthetic chain publishes through an approved brief and all
tests pass without warnings.

- [ ] **Step 5: Commit Task 4**

```bash
git add scripts/rehydrate.py scripts/grade.py tests/
git commit -m "feat: require approved brief for publication"
```

---

### Task 5: Agent Workflow, Domain Docs, Package, and Version

**Files:**
- Create: `references/assignment-brief.md`
- Create: `docs/adr/0002-approved-assignment-briefs-gate-grading.md`
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `docs/using-the-grader.md`
- Modify: `references/calibration.md`
- Modify: `CONTEXT.md`
- Modify: `scripts/validate-package.py`
- Modify: `skills/humanizer/SKILL.md`
- Modify: `.claude-plugin/plugin.json`
- Modify: `PACKAGE_FILES.txt`

**Interfaces:**
- Consumes: all completed CLI and lifecycle behavior from Tasks 1–4.
- Produces: the public instructor walkthrough, portable agent instructions, domain decision record, package inventory, and version 3.3.0.

- [ ] **Step 1: Add failing package assertions before documentation**

Extend `scripts/validate-package.py` so `required_references` includes
`assignment-brief.md`. Add exact checks that `SKILL.md` contains:

```text
Approve assignment brief for <batch>
python3 scripts/grade.py brief prepare
python3 scripts/grade.py brief approve
```

Add an assertion that the first README version entry, both skill metadata
versions, and plugin version remain equal; the existing validator already
checks this relationship.

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python scripts/validate-package.py
```

Expected: failure names the absent reference or required workflow text.

- [ ] **Step 2: Write the assignment briefing reference**

Create `references/assignment-brief.md` with these sections in Plain Language:

- `# Assignment briefing`
- `## What the agent asks`
- `## What the agent may read`
- `## Approval`
- `## Resume or revise`
- `## File format`
- `## What approval does not prove`

List the seven question groups from the design. State that the agent may read
the rubric, profile, assignment prompt, and instructor-authored course material
before approval, but must not read submissions, roster, review file, or map.
Include the exact approval phrase and prepare/approve/check commands. Explain
that chat approval is a workflow record, not a signature or privacy approval.

- [ ] **Step 3: Put the guided workflow into the grading skill**

Keep the local/cloud gate first. After the gate and before redaction, add an
`## Assignment briefing` section that tells the agent to read
`references/assignment-brief.md`, check the profile, ask the seven groups one
at a time when answers are missing, prepare the brief, summarize it, and wait
for the exact phrase. State explicitly:

```text
Do not read submissions, the roster, the redaction review file, or the token
map before the instructor approves the assignment brief.
```

Update the grading sequence and command examples so brief prepare/approve/check
comes before redaction, verification names the brief evidence, and rehydration
passes `--brief .grading/assignments/<batch>.md`. Update the error table for a
missing, unapproved, or stale brief. Keep `SKILL.md` at 500 lines or fewer.

Update `references/calibration.md` to explain the handoff: course calibration
sets reusable voice and standards; each assignment brief confirms how that
profile applies to one batch.

- [ ] **Step 4: Rewrite the public workflow from an instructor's viewpoint**

At the top of `README.md`, retain the recommendation for fully local models and
agents, then add a numbered `How the agent walks an instructor through grading`
section before the first grading example. It must cover:

1. choose local or cloud;
2. calibrate once or when the rubric changes;
3. answer assignment-specific questions;
4. approve the exact summary;
5. let the agent redact, grade, and verify;
6. review rehydrated drafts and submit them yourself.

Show one short dialogue ending in `Approve assignment brief for essay-3` and
state what causes reapproval. Keep the grading example before the existing
before/after writing example. Update installation/use commands and the receipt
description to schema 2.

Update `docs/using-the-grader.md` with the same operational order and exact CLI
commands, without copying large README paragraphs verbatim.

- [ ] **Step 5: Record the domain term and architecture decision**

Add this concept to `CONTEXT.md`:

```markdown
**Assignment brief**:
An identity-free, instructor-approved record of how the rubric and calibration
profile apply to one grading batch. Verification binds the exact approved file.
_Avoid_: Prompt, config, approval receipt
```

Create ADR 0002 with accepted status and date 2026-08-26. Record that approved
assignment briefs gate grading, schema 2 receipts bind them, old receipts must
be reverified, and the approval is not human authentication. Include considered
alternatives: chat-only state, instructor-run command only, repeated interviews,
and retaining receipt schema 1.

- [ ] **Step 6: Bump the package and inventory every file**

Set version `3.3.0` in:

- `SKILL.md` metadata;
- `skills/humanizer/SKILL.md` metadata;
- `.claude-plugin/plugin.json`; and
- the first README version entry.

The README 3.3.0 note must say that grading now requires an approved,
digest-bound assignment brief and schema 2 receipts. Do not tag or push.

Add every new tracked file to `PACKAGE_FILES.txt`, including the design spec,
implementation plan, brief module/test, reference, and ADR. Keep the inventory
sorted exactly like `git ls-files` output.

- [ ] **Step 7: Run documentation and package checks**

Run:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python scripts/validate-package.py
wc -l SKILL.md skills/humanizer/SKILL.md
git diff --check
```

Expected: the validator exits 0, both skills are at most 500 lines, and the diff
check is empty.

- [ ] **Step 8: Run every required release check**

Run fresh from the worktree root:

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python scripts/validate-package.py
npx skills add . --list
claude plugin validate .
```

Expected: all tests pass, package validation exits 0, both packaged skills are
listed, and Claude reports a valid plugin.

- [ ] **Step 9: Commit Task 5**

```bash
git add SKILL.md README.md docs/ references/ CONTEXT.md scripts/validate-package.py skills/humanizer/SKILL.md .claude-plugin/plugin.json PACKAGE_FILES.txt
git commit -m "docs: guide instructor-approved grading"
```

---

### Task 6: Whole-Branch Verification and Review Readiness

**Files:**
- Modify only files required by review findings.

**Interfaces:**
- Consumes: the complete feature branch and Task 5 release-check commands.
- Produces: fresh evidence that the branch is ready for an independent whole-branch review.

- [ ] **Step 1: Audit the implementation against every global constraint**

Read the spec, then use searches that prove the cross-file relationships:

```bash
rg -n 'grading-assignment-brief/1|grading-batch-verification/2|assignment-brief' scripts tests SKILL.md README.md docs references CONTEXT.md
rg -n 'version: "3.3.0"|\*\*3.3.0\*\*|"version": "3.3.0"' SKILL.md skills/humanizer/SKILL.md README.md .claude-plugin/plugin.json
rg -n 'VerificationInputs\(' scripts tests
```

Confirm every `VerificationInputs` construction has a matched approved brief,
every explicit rehydration command has `--brief`, and no token-map import exists
under `scripts/grading/`.

- [ ] **Step 2: Run the complete fresh verification set**

Run all four required release checks from Task 5 again after any final fixes.
Read the complete output and record exact pass counts and exit status.

- [ ] **Step 3: Commit only if the audit required a fix**

If this task changes files, run the focused regression plus the full suite, then
commit with:

```bash
git add -u
git commit -m "fix: close assignment briefing audit gaps"
```

If the audit changes nothing, create no empty commit.
