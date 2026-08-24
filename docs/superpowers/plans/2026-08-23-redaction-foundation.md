# Redaction Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restructure the package so Humanizer becomes a component, then build and prove the local redaction pipeline that keeps student identity out of a cloud model's context.

**Architecture:** Two Python CLIs, `redact.py` and `rehydrate.py`, over a `scripts/redaction/` package with one module per detection layer. Redaction runs entirely on the instructor's machine. Tokens go out, tokens come back, real names get reattached locally from a map file that nothing else ever loads.

**Tech Stack:** Python 3.13, standard library only at runtime. `pytest` for tests. `python-docx`, `spacy`, and `presidio` are optional and must degrade to a flag, never a silent pass.

**Spec:** `docs/superpowers/specs/2026-08-23-voice-calibrated-grading-design.md`

**Scope note:** This is Plan 1 of 2. It delivers the package restructure and the redaction pipeline, which stand alone and ship working software. Plan 2 covers calibration, the grading engine, the batch driver, and the optional hook, and depends on this plan landing first.

## Global Constraints

- Runtime code must use the standard library only. Optional imports must be wrapped and must degrade to a review flag.
- Redaction must fail closed. A non-zero exit stops the cloud path. There must be no override flag.
- The map file must be written with mode `0600` and must never be committed.
- Redact identity-bearing entities. Keep places, historical figures, and dates the essay is about. Over-redaction destroys gradeability.
- The 35 patterns must stay numbered 1 through 35 after they move to `references/ai-patterns.md`.
- Prose in this repo follows the Plain Language rules in `AGENTS.md`: lead with the main point, active voice, short sentences, `must` for requirements.
- Every task ends with a commit.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `.claude-plugin/plugin.json` | Package metadata. Restored in Task 1. |
| `references/ai-patterns.md` | The 35 patterns, shared by both skills. |
| `skills/humanizer/SKILL.md` | The Humanizer skill, thinned to point at the reference. |
| `scripts/redaction/tokens.py` | Token allocation and map I/O. |
| `scripts/redaction/roster.py` | Layer 1. Roster names, IDs, emails. |
| `scripts/redaction/patterns.py` | Layer 2. Structured PII by regex. |
| `scripts/redaction/documents.py` | Layer 3. Text extraction, doc metadata, output naming. |
| `scripts/redaction/candidates.py` | Layer 4. Flags unmatched personal names for review. |
| `scripts/redaction/selfid.py` | Warns when content may identify its author. |
| `scripts/redaction/pipeline.py` | Orchestration, coverage report, exit codes. |
| `scripts/redact.py` | CLI entry point. |
| `scripts/rehydrate.py` | CLI entry point. Tokens back to names. |
| `tests/fixtures/phil210/` | The synthetic corpus. |

---

### Task 1: Restore the package and fix the validator

`validate-package.py` crashes today because `.claude-plugin/plugin.json` is missing from this copy. Nothing else can be verified until it runs.

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.claude-plugin/marketplace.json`
- Test: run the existing validator

- [ ] **Step 1: Confirm the current failure**

Run: `python3 scripts/validate-package.py`
Expected: `FileNotFoundError` naming `.claude-plugin/plugin.json`

- [ ] **Step 2: Create the plugin manifest**

`.claude-plugin/plugin.json`:

```json
{
  "name": "humanizer",
  "version": "2.11.2",
  "description": "Rewrite AI-sounding text so it reads naturally without changing what it says.",
  "license": "MIT",
  "skills": ["./"]
}
```

- [ ] **Step 3: Create the marketplace manifest**

`.claude-plugin/marketplace.json`:

```json
{
  "name": "humanizer",
  "owner": { "name": "blader" },
  "plugins": [
    {
      "name": "humanizer",
      "source": "./",
      "description": "Rewrite AI-sounding text so it reads naturally without changing what it says."
    }
  ]
}
```

- [ ] **Step 4: Run the validator**

Run: `python3 scripts/validate-package.py`
Expected: `Humanizer package v2.11.2 is valid`

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin
git commit -m "Restore the plugin manifests so the validator runs"
```

---

### Task 2: Extract the patterns and relocate the Humanizer skill

The grader takes the root `SKILL.md`. The 35 patterns move to a shared reference so both skills read one copy. Three existing rules break here and must be updated on purpose.

**Files:**
- Create: `references/ai-patterns.md`
- Create: `skills/humanizer/SKILL.md`
- Modify: `SKILL.md` (becomes a grader placeholder; Plan 2 fills it in)
- Modify: `scripts/validate-package.py`
- Modify: `AGENTS.md`
- Modify: `README.md`, `.claude-plugin/plugin.json` (version to 3.0.0)

**Interfaces:**
- Produces: `references/ai-patterns.md` containing headings `### 1.` through `### 35.`, which Plan 2's voice pass and criterion 4 scoring both read.

- [ ] **Step 1: Move the patterns verbatim**

Copy lines 48 through 430 of the current `SKILL.md` (from `## Content patterns` through the end of `### Human details to keep`) into `references/ai-patterns.md`, unchanged. Add this header above them:

```markdown
# Signs of AI writing: 35 patterns

Shared reference. The Humanizer skill uses these to rewrite text. The grading
skill uses them twice: to score rubric criteria that judge prose, and to keep
the instructor's own feedback from reading like chatbot output.

Source: [Wikipedia: Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)
```

- [ ] **Step 2: Verify nothing was lost**

Run: `grep -c '^### [0-9]\+\. ' references/ai-patterns.md`
Expected: `35`

- [ ] **Step 3: Write the thinned Humanizer skill**

`skills/humanizer/SKILL.md` keeps the current YAML metadata and every section except the patterns, replacing them with a pointer:

```markdown
## The patterns

Read `../../references/ai-patterns.md` before rewriting. It holds all 35
patterns, their watched words, and before-and-after examples.
```

- [ ] **Step 4: Stub the grader entry point**

Replace the root `SKILL.md` with a placeholder Plan 2 will complete:

```markdown
---
name: grader
description: |
  Score student work against the instructor's rubric and draft feedback in the
  instructor's voice, calibrated from their rubric and past graded work.
  Redacts student identity before any work reaches a cloud model.
license: MIT
metadata:
  version: "3.0.0"
---

# Grader

Not yet implemented. See
`docs/superpowers/specs/2026-08-23-voice-calibrated-grading-design.md`.
```

- [ ] **Step 5: Update the validator for the new layout**

In `scripts/validate-package.py`, replace the single-skill check:

```python
skill_files = {path.relative_to(ROOT) for path in ROOT.rglob("SKILL.md")}
expected_skills = {Path("SKILL.md"), Path("skills/humanizer/SKILL.md")}
if skill_files != expected_skills:
    raise SystemExit(f"Expected exactly these skills: {sorted(expected_skills)}")
for skill_file in sorted(expected_skills):
    if (ROOT / skill_file).is_symlink():
        raise SystemExit(f"Keep {skill_file} a regular file")
    if len((ROOT / skill_file).read_text(encoding="utf-8").splitlines()) > 500:
        raise SystemExit(f"Keep {skill_file} at 500 lines or fewer")
if PLUGIN.get("skills") != ["./", "./skills/humanizer"]:
    raise SystemExit("Point the plugin skill loader at both skills")
```

Move the pattern check off `SKILL` and onto the reference:

```python
PATTERNS = (ROOT / "references" / "ai-patterns.md").read_text(encoding="utf-8")
pattern_numbers = [int(n) for n in re.findall(r"(?m)^### ([0-9]+)\. ", PATTERNS)]
if pattern_numbers != list(range(1, 36)):
    raise SystemExit(f"Number the patterns 1 through 35: {pattern_numbers}")
```

Delete the old `if len(SKILL.splitlines()) > 500:` check, now covered per skill above.

- [ ] **Step 6: Bump the version in all three places**

Set `3.0.0` in root `SKILL.md` metadata, `skills/humanizer/SKILL.md` metadata, and `.claude-plugin/plugin.json`. Set `plugin.json` `skills` to `["./", "./skills/humanizer"]`. Add to the top of the README version list:

```markdown
- **3.0.0** — Grader becomes the package. Humanizer moves to `skills/humanizer/` and the 35 patterns move to `references/ai-patterns.md`.
```

- [ ] **Step 7: Update AGENTS.md**

Replace the "Key files" bullet claiming `SKILL.md` is the only skill file:

```markdown
- `SKILL.md` is the grading skill and the package's entry point.
- `skills/humanizer/SKILL.md` is the Humanizer skill. It stays usable on its own.
- `references/ai-patterns.md` holds the 35 patterns. Both skills read it. Edit patterns only here.
```

Update the Patterns rule to say the numbering lives in `references/ai-patterns.md`.

- [ ] **Step 8: Run the validator**

Run: `python3 scripts/validate-package.py`
Expected: `Humanizer package v3.0.0 is valid`

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "Make the grader the package and share the 35 patterns"
```

---

### Task 3: Install the fixtures and build the roster

**Files:**
- Create: `tests/fixtures/phil210/` (copied corpus)
- Create: `tests/fixtures/phil210/roster.csv`
- Create: `tests/conftest.py`
- Create: `requirements-dev.txt`

**Interfaces:**
- Produces: a `phil210` pytest fixture returning the corpus root `Path`, used by every later task.

- [ ] **Step 1: Copy the corpus**

```bash
mkdir -p tests/fixtures
cp -R ~/Desktop/grading_mock_papers tests/fixtures/phil210
```

The corpus is synthetic, so it is committed on purpose. `.gitignore` excludes `submissions/`, so confirm the copy is tracked:

Run: `git status --short tests/fixtures/phil210 | head -3`
Expected: files listed as untracked, not ignored

- [ ] **Step 2: Write the roster**

`tests/fixtures/phil210/roster.csv`. Emails and IDs do not appear in the corpus, so they are seeded here to give layers 1 and 2 something real to catch:

```csv
name,student_id,email
Maya Lin,S1000431,mlin@example.edu
Samantha Chen,S1000432,schen@example.edu
Marcus Thorne,S1000433,mthorne@example.edu
David Morales,S1000434,dmorales@example.edu
Chloe Bennett,S1000435,cbennett@example.edu
Jordan Reed,S1000436,jreed@example.edu
Tyler Brooks,S1000437,tbrooks@example.edu
Lauren Higgins,S1000438,lhiggins@example.edu
Evan Ramirez,S1000439,eramirez@example.edu
Brandon Scott,S1000440,bscott@example.edu
```

- [ ] **Step 3: Add the dev requirement**

`requirements-dev.txt`:

```
pytest>=8.0
```

Runtime stays standard-library only. This file is for tests.

- [ ] **Step 4: Write conftest.py**

`tests/conftest.py`:

```python
"""Shared fixtures for the redaction tests."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "phil210"


@pytest.fixture
def phil210() -> Path:
    """Return the synthetic PHIL 210 corpus root."""
    return FIXTURES


@pytest.fixture
def instructor() -> str:
    """Return the instructor name, which must never be redacted."""
    return "Marcus Vance"
```

- [ ] **Step 5: Verify the fixtures load**

Run: `python3 -m pytest tests/ --collect-only -q`
Expected: `no tests ran`, and no collection errors

- [ ] **Step 6: Commit**

```bash
git add tests requirements-dev.txt
git commit -m "Add the PHIL 210 fixtures and roster"
```

---

### Task 4: Token allocation and the map file

**Files:**
- Create: `scripts/redaction/__init__.py` (empty)
- Create: `scripts/redaction/tokens.py`
- Test: `tests/redaction/test_tokens.py`

**Interfaces:**
- Produces: `Entity`, `TokenMap`, `KINDS`. Every later layer calls `TokenMap.allocate(kind, value, file) -> str`. `rehydrate.py` calls `TokenMap.load(path)` and `value_for(token)`.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_tokens.py`:

```python
"""Tokens must be stable, kind-scoped, and stored owner-only."""

import json

import pytest

from scripts.redaction.tokens import TokenMap


def test_same_value_returns_the_same_token():
    tmap = TokenMap("essay-1")
    first = tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    second = tmap.allocate("STUDENT", "Maya Lin", "paper_2.md")
    assert first == second == "STUDENT_01"


def test_matching_ignores_case():
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    assert tmap.allocate("STUDENT", "maya lin", "paper_2.md") == "STUDENT_01"


def test_each_kind_numbers_independently():
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    assert tmap.allocate("EMAIL", "mlin@example.edu", "paper_1.md") == "EMAIL_01"
    assert tmap.allocate("STUDENT", "Tyler Brooks", "paper_2.md") == "STUDENT_02"


def test_unknown_kind_is_rejected():
    tmap = TokenMap("essay-1")
    with pytest.raises(ValueError):
        tmap.allocate("NICKNAME", "Maya", "paper_1.md")


def test_saved_map_is_owner_only(tmp_path):
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_round_trip_preserves_values(tmp_path):
    tmap = TokenMap("essay-1")
    token = tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)

    loaded = TokenMap.load(path)
    assert loaded.batch == "essay-1"
    assert loaded.value_for(token) == "Maya Lin"


def test_saved_shape_is_one_entity_per_token(tmp_path):
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entities"]["STUDENT_01"] == {
        "kind": "STUDENT",
        "value": "Maya Lin",
        "file": "paper_1.md",
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.redaction'`

- [ ] **Step 3: Write the implementation**

Create empty `scripts/__init__.py` and `scripts/redaction/__init__.py`, then `scripts/redaction/tokens.py`:

```python
"""Allocate stable tokens for redacted values and store the mapping.

The saved map is the only artifact that holds real identities. Local scripts
write and read it. Nothing else must load it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

KINDS = ("STUDENT", "PERSON", "EMAIL", "PHONE", "ID", "URL")


@dataclass(frozen=True)
class Entity:
    """One redacted value and the token that stands in for it."""

    token: str
    kind: str
    value: str
    file: str


class TokenMap:
    """Hand out stable tokens and remember what each one replaced."""

    def __init__(self, batch: str) -> None:
        self.batch = batch
        self._by_key: dict[tuple[str, str], Entity] = {}
        self._by_token: dict[str, Entity] = {}

    def allocate(self, kind: str, value: str, file: str) -> str:
        """Return the token for this value, creating it on first sight."""
        if kind not in KINDS:
            raise ValueError(f"Unknown kind: {kind}. Use one of {KINDS}.")
        key = (kind, value.casefold())
        if key in self._by_key:
            return self._by_key[key].token

        count = sum(1 for e in self._by_token.values() if e.kind == kind)
        token = f"{kind}_{count + 1:02d}"
        entity = Entity(token, kind, value, file)
        self._by_key[key] = entity
        self._by_token[token] = entity
        return token

    def value_for(self, token: str) -> str | None:
        """Return the real value behind a token, or None if unknown."""
        entity = self._by_token.get(token)
        return entity.value if entity else None

    def tokens(self) -> dict[str, Entity]:
        """Return every allocated token."""
        return dict(self._by_token)

    def save(self, path: Path) -> None:
        """Write the map owner-only."""
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "batch": self.batch,
            "entities": {
                token: {"kind": e.kind, "value": e.value, "file": e.file}
                for token, e in self._by_token.items()
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(path, 0o600)

    @classmethod
    def load(cls, path: Path) -> "TokenMap":
        """Read a saved map."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        tmap = cls(payload["batch"])
        for token, record in payload["entities"].items():
            entity = Entity(token, record["kind"], record["value"], record["file"])
            tmap._by_token[token] = entity
            tmap._by_key[(entity.kind, entity.value.casefold())] = entity
        return tmap
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_tokens.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/__init__.py scripts/redaction tests/redaction/test_tokens.py
git commit -m "Add stable token allocation and the owner-only map file"
```

---

### Task 5: Layer 1, roster matching

The highest-value layer. The instructor has a roster, so author identity is an exact-match problem rather than a guess.

Two rules make this safe. The instructor's name must never be redacted, or `Dr. Marcus Vance` gets eaten by the student `Marcus Thorne`. And a bare first or last name is replaced only when that student's full name also appears in the same text, so an unrelated essay keeps its subject matter.

**Files:**
- Create: `scripts/redaction/roster.py`
- Test: `tests/redaction/test_roster.py`

**Interfaces:**
- Consumes: `TokenMap.allocate` from Task 4.
- Produces: `RosterEntry`, `load_roster(path) -> list[RosterEntry]`, and `redact_roster(text, roster, tmap, source_file, protected=()) -> RosterResult` where `RosterResult` has `.text: str`, `.matched: set[str]`, and `.bare_hits: list[BareHit]`.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_roster.py`:

```python
"""Layer 1 must catch roster identities without eating subject matter."""

from scripts.redaction.roster import RosterEntry, load_roster, redact_roster
from scripts.redaction.tokens import TokenMap


def test_full_name_is_replaced():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Maya Lin argues well.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01 argues well."
    assert result.matched == {"Maya Lin"}


def test_possessive_keeps_its_suffix():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Maya Lin's thesis is sharp.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01's thesis is sharp."


def test_last_name_first_form_is_replaced():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Lin, Maya submitted late.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01 submitted late."


def test_email_and_id_are_replaced():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")]
    result = redact_roster(
        "Contact mlin@example.edu or cite S1000431.", roster, tmap, "p1.md"
    )
    assert result.text == "Contact EMAIL_01 or cite ID_01."


def test_the_instructor_is_never_redacted():
    """Marcus Thorne is a student. Dr. Marcus Vance is not."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Marcus Thorne")]
    result = redact_roster(
        "Dr. Marcus Vance graded Marcus Thorne's essay.",
        roster,
        tmap,
        "p6.md",
        protected=("Marcus Vance",),
    )
    assert result.text == "Dr. Marcus Vance graded STUDENT_01's essay."


def test_bare_name_is_skipped_when_the_full_name_is_absent():
    """An essay about the Maya civilization must stay gradeable."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("The Maya civilization collapsed.", roster, tmap, "p2.md")
    assert result.text == "The Maya civilization collapsed."
    assert result.matched == set()


def test_bare_name_is_replaced_and_reported_when_the_full_name_is_present():
    """Identity wins, but the substitution must be visible to the instructor."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster(
        "Maya Lin wrote this. The Maya civilization collapsed.",
        roster,
        tmap,
        "p1.md",
    )
    assert result.text == "STUDENT_01 wrote this. The STUDENT_01 civilization collapsed."
    assert [hit.name for hit in result.bare_hits] == ["Maya"]
    assert "civilization" in result.bare_hits[0].context


def test_bare_names_can_be_turned_off():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster(
        "Maya Lin wrote this. The Maya civilization collapsed.",
        roster,
        tmap,
        "p1.md",
        bare_names=False,
    )
    assert result.text == "STUDENT_01 wrote this. The Maya civilization collapsed."


def test_roster_loads_from_csv(phil210):
    roster = load_roster(phil210 / "roster.csv")
    assert len(roster) == 10
    assert roster[0] == RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_roster.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.redaction.roster'`

- [ ] **Step 3: Write the implementation**

`scripts/redaction/roster.py`:

```python
"""Layer 1: replace roster identities with tokens.

Full names, emails, and student IDs are always replaced. A bare first or last
name is replaced only when that student's full name also appears in the same
text, so an essay about the Maya civilization keeps its subject matter.
Protected names, such as the instructor's, are shielded first.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .tokens import TokenMap

CONTEXT_WIDTH = 40


@dataclass(frozen=True)
class RosterEntry:
    """One student from the course roster."""

    name: str
    student_id: str = ""
    email: str = ""


@dataclass(frozen=True)
class BareHit:
    """A bare first or last name that was replaced, with its context."""

    name: str
    token: str
    context: str


@dataclass
class RosterResult:
    """What layer 1 changed and what the instructor should review."""

    text: str
    matched: set[str] = field(default_factory=set)
    bare_hits: list[BareHit] = field(default_factory=list)


def load_roster(path: Path) -> list[RosterEntry]:
    """Read a roster CSV with name, student_id, and email columns."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            RosterEntry(
                row["name"].strip(),
                (row.get("student_id") or "").strip(),
                (row.get("email") or "").strip(),
            )
            for row in csv.DictReader(handle)
            if (row.get("name") or "").strip()
        ]


def full_name_variants(name: str) -> list[str]:
    """Return the written forms of a full name, longest first."""
    parts = name.split()
    variants = {name}
    if len(parts) >= 2:
        variants.add(f"{parts[-1]}, {' '.join(parts[:-1])}")
        variants.add(f"{parts[0][0]}. {parts[-1]}")
    return sorted(variants, key=len, reverse=True)


def bare_name_variants(name: str) -> list[str]:
    """Return the first and last name on their own, longest first."""
    parts = name.split()
    if len(parts) < 2:
        return []
    return sorted({parts[0], parts[-1]}, key=len, reverse=True)


def _replace(text: str, needle: str, token: str) -> tuple[str, int]:
    return re.subn(rf"\b{re.escape(needle)}\b", token, text)


def _context(text: str, needle: str) -> str:
    match = re.search(rf"\b{re.escape(needle)}\b", text)
    if not match:
        return ""
    start = max(0, match.start() - CONTEXT_WIDTH)
    return text[start : match.end() + CONTEXT_WIDTH].replace("\n", " ").strip()


def redact_roster(
    text: str,
    roster: list[RosterEntry],
    tmap: TokenMap,
    source_file: str,
    protected: tuple[str, ...] = (),
    bare_names: bool = True,
) -> RosterResult:
    """Replace roster identities in text and report what changed."""
    shields: dict[str, str] = {}
    for index, name in enumerate(protected):
        sentinel = f"\x00SHIELD{index}\x00"
        shields[sentinel] = name
        text = re.sub(rf"\b{re.escape(name)}\b", sentinel, text)

    result = RosterResult(text=text)

    for entry in roster:
        full_forms = full_name_variants(entry.name)
        present = any(
            re.search(rf"\b{re.escape(form)}\b", result.text) for form in full_forms
        )

        if present:
            token = tmap.allocate("STUDENT", entry.name, source_file)
            for form in full_forms:
                result.text, count = _replace(result.text, form, token)
                if count:
                    result.matched.add(entry.name)
            if bare_names:
                for bare in bare_name_variants(entry.name):
                    context = _context(result.text, bare)
                    result.text, count = _replace(result.text, bare, token)
                    if count:
                        result.bare_hits.append(BareHit(bare, token, context))

        if entry.email and entry.email in result.text:
            email_token = tmap.allocate("EMAIL", entry.email, source_file)
            result.text = result.text.replace(entry.email, email_token)
            result.matched.add(entry.name)

        if entry.student_id:
            id_token = tmap.allocate("ID", entry.student_id, source_file)
            result.text, count = _replace(result.text, entry.student_id, id_token)
            if count:
                result.matched.add(entry.name)

    for sentinel, name in shields.items():
        result.text = result.text.replace(sentinel, name)

    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_roster.py -v`
Expected: 9 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/redaction/roster.py tests/redaction/test_roster.py
git commit -m "Add layer 1 roster matching with instructor shielding"
```

---

### Task 6: Layer 2, structured patterns

**Files:**
- Create: `scripts/redaction/patterns.py`
- Test: `tests/redaction/test_patterns.py`

**Interfaces:**
- Consumes: `TokenMap.allocate`.
- Produces: `redact_structured(text, tmap, source_file, id_pattern=DEFAULT_ID) -> tuple[str, int]` returning the text and the replacement count.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_patterns.py`:

```python
"""Layer 2 must catch structured PII without touching subject matter."""

from scripts.redaction.patterns import redact_structured
from scripts.redaction.tokens import TokenMap


def test_email_is_replaced():
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Reach me at maya@example.edu.", tmap, "p1.md")
    assert text == "Reach me at EMAIL_01."
    assert count == 1


def test_phone_is_replaced():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("Call 415-555-0142 after five.", tmap, "p1.md")
    assert text == "Call PHONE_01 after five."


def test_student_id_is_replaced():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("Student S1000431 submitted.", tmap, "p1.md")
    assert text == "Student ID_01 submitted."


def test_personal_profile_url_is_replaced():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured(
        "See https://linkedin.com/in/mayalin for details.", tmap, "p1.md"
    )
    assert text == "See URL_01 for details."


def test_a_cited_year_is_not_an_id():
    """Gillespie (2014) must survive. Over-redaction destroys gradeability."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured(
        "As Gillespie (2014) notes, algorithms curate.", tmap, "p1.md"
    )
    assert text == "As Gillespie (2014) notes, algorithms curate."
    assert count == 0


def test_a_statutory_reference_survives():
    """DMCA 1201 is subject matter, not an identifier."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Under DMCA 1201 the rule holds.", tmap, "p1.md")
    assert count == 0


def test_repeated_email_reuses_its_token():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("a@b.edu and a@b.edu", tmap, "p1.md")
    assert text == "EMAIL_01 and EMAIL_01"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_patterns.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`scripts/redaction/patterns.py`:

```python
"""Layer 2: replace structured personal information found by regex.

Each pattern must be specific enough to leave subject matter alone. A cited
year and a statutory section number are not identifiers.
"""

from __future__ import annotations

import re

from .tokens import TokenMap

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"\b(?:\+1[ -]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")
DEFAULT_ID = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")
PROFILE_URL = re.compile(
    r"https?://(?:www\.)?(?:linkedin\.com/in/|github\.com/|x\.com/|twitter\.com/|facebook\.com/)[\w.-]+"
)

_ORDER = (
    ("EMAIL", EMAIL),
    ("URL", PROFILE_URL),
    ("PHONE", PHONE),
)


def redact_structured(
    text: str,
    tmap: TokenMap,
    source_file: str,
    id_pattern: re.Pattern[str] = DEFAULT_ID,
) -> tuple[str, int]:
    """Replace emails, profile URLs, phone numbers, and student IDs."""
    total = 0

    def substitute(kind: str, pattern: re.Pattern[str], source: str) -> str:
        nonlocal total

        def swap(match: re.Match[str]) -> str:
            nonlocal total
            total += 1
            return tmap.allocate(kind, match.group(0), source_file)

        return pattern.sub(swap, source)

    for kind, pattern in _ORDER:
        text = substitute(kind, pattern, text)
    return substitute("ID", id_pattern, text), total
```

`DEFAULT_ID` requires a leading letter, so a bare four-digit year cannot match.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_patterns.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/redaction/patterns.py tests/redaction/test_patterns.py
git commit -m "Add layer 2 structured pattern redaction"
```

---

### Task 7: Layer 3, extraction, metadata, and output naming

The document `author` field is the most commonly missed leak. Output files are named by token, which removes filename leaks entirely.

**Files:**
- Create: `scripts/redaction/documents.py`
- Test: `tests/redaction/test_documents.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `extract(path) -> Extracted` where `Extracted` has `.text: str`, `.author: str`, and `.error: str`. Also `output_name(token, suffix=".md") -> str`.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_documents.py`:

```python
"""Layer 3 must read text, surface doc metadata, and never leak a filename."""

from scripts.redaction.documents import extract, output_name


def test_markdown_is_read(tmp_path):
    path = tmp_path / "paper.md"
    path.write_text("Maya Lin wrote this.", encoding="utf-8")
    result = extract(path)
    assert result.text == "Maya Lin wrote this."
    assert result.author == ""
    assert result.error == ""


def test_plain_text_is_read(tmp_path):
    path = tmp_path / "paper.txt"
    path.write_text("Body text.", encoding="utf-8")
    assert extract(path).text == "Body text."


def test_pdf_is_refused(tmp_path):
    path = tmp_path / "paper.pdf"
    path.write_bytes(b"%PDF-1.4")
    result = extract(path)
    assert result.text == ""
    assert "convert" in result.error.lower()


def test_unknown_suffix_is_refused(tmp_path):
    path = tmp_path / "paper.pages"
    path.write_bytes(b"x")
    assert extract(path).error != ""


def test_unreadable_file_reports_an_error(tmp_path):
    path = tmp_path / "paper.md"
    path.write_bytes(b"\xff\xfe\x00bad")
    result = extract(path)
    assert result.error != ""


def test_output_is_named_by_token():
    """A filename must never carry a real name."""
    assert output_name("STUDENT_07") == "STUDENT_07.md"
    assert output_name("STUDENT_07", ".txt") == "STUDENT_07.txt"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_documents.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`scripts/redaction/documents.py`:

```python
"""Layer 3: read submission text, surface document metadata, name outputs.

Output files are named by token. A real name must never reach a filename.
The docx author field must be returned so the caller can tokenize it, never
silently dropped and never silently kept.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {".md", ".txt"}


@dataclass(frozen=True)
class Extracted:
    """Text pulled from a submission, plus anything the container revealed."""

    text: str = ""
    author: str = ""
    error: str = ""


def _extract_docx(path: Path) -> Extracted:
    try:
        import docx  # type: ignore
    except ImportError:
        return Extracted(
            error="Reading .docx needs python-docx. Install it or convert to Markdown."
        )
    try:
        document = docx.Document(str(path))
    except Exception as exc:  # noqa: BLE001 - report any container failure
        return Extracted(error=f"Could not read {path.name}: {exc}")
    text = "\n\n".join(paragraph.text for paragraph in document.paragraphs)
    author = document.core_properties.author or ""
    return Extracted(text=text, author=author)


def extract(path: Path) -> Extracted:
    """Return the text of a submission, or an error explaining why not."""
    suffix = path.suffix.lower()

    if suffix in TEXT_SUFFIXES:
        try:
            return Extracted(text=path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError) as exc:
            return Extracted(error=f"Could not read {path.name}: {exc}")

    if suffix == ".docx":
        return _extract_docx(path)

    if suffix == ".pdf":
        return Extracted(
            error=f"{path.name} is a PDF. Convert it to Markdown first; "
            "scanned submissions are out of scope."
        )

    return Extracted(error=f"{path.name} has an unsupported type: {suffix}")


def output_name(token: str, suffix: str = ".md") -> str:
    """Name a redacted file by its token so no real name reaches disk."""
    return f"{token}{suffix}"
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_documents.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/redaction/documents.py tests/redaction/test_documents.py
git commit -m "Add layer 3 extraction, metadata capture, and token-named output"
```

---

### Task 8: Layer 4, flag unmatched personal names

The corpus cites Habermas, Gillespie, Brady, TikTok, and Bluesky. Flagging every capitalized word would make this layer unusable, so it flags names in personal contexts and skips citation contexts.

**Files:**
- Create: `scripts/redaction/candidates.py`
- Test: `tests/redaction/test_candidates.py`

**Interfaces:**
- Produces: `Candidate` with `.name: str`, `.line: int`, `.context: str`; and `find_candidates(text) -> list[Candidate]`.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_candidates.py`:

```python
"""Layer 4 must flag people, not cited scholars or platforms."""

from scripts.redaction.candidates import find_candidates


def test_relational_name_is_flagged():
    found = find_candidates("My brother Daniel drove me to the hearing.")
    assert [c.name for c in found] == ["Daniel"]


def test_professor_reference_is_flagged():
    found = find_candidates("Professor Alvarez disagreed with the ruling.")
    assert [c.name for c in found] == ["Alvarez"]


def test_speech_attribution_is_flagged():
    found = find_candidates("Priya told me the permit never arrived.")
    assert [c.name for c in found] == ["Priya"]


def test_a_citation_is_not_flagged():
    """Gillespie (2014) is subject matter."""
    assert find_candidates("As Gillespie (2014) notes, algorithms curate.") == []


def test_a_theorist_in_prose_is_not_flagged():
    assert find_candidates("Habermas envisioned an idealized forum.") == []


def test_a_platform_is_not_flagged():
    assert find_candidates("Recommendation engines on TikTok and YouTube.") == []


def test_a_sentence_opening_word_is_not_flagged():
    assert find_candidates("Contemporary systems do not serve as conduits.") == []


def test_the_candidate_carries_line_and_context():
    text = "First line.\nMy sister Rosa filed the complaint."
    found = find_candidates(text)
    assert found[0].line == 2
    assert "filed the complaint" in found[0].context
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_candidates.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`scripts/redaction/candidates.py`:

```python
"""Layer 4: flag capitalized names that appear in personal contexts.

This layer never edits text. It reports names for the instructor to review,
because guessing wrong in either direction is costly: a missed name leaks, and
a wrongly redacted scholar destroys gradeability.

An optional model-based pass runs when spacy or presidio is installed. Without
one, the heuristics below still produce flags. A flag must never be dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONTEXT_WIDTH = 60

NAME = r"([A-Z][a-z]{2,})"

RELATION = (
    r"(?:my|our)\s+(?:brother|sister|mother|father|mom|dad|aunt|uncle|cousin|"
    r"grandmother|grandfather|friend|neighbor|roommate|partner|boss|coworker)\s+"
)
TITLE = r"(?:Professor|Prof\.|Dr\.|Mr\.|Ms\.|Mrs\.|Coach|Principal|Senator|Rep\.)\s+"
SPEECH = r"\s+(?:told|texted|emailed|asked|showed|warned|reminded)\s+(?:me|us)\b"

PERSONAL = (
    re.compile(RELATION + NAME),
    re.compile(TITLE + NAME),
    re.compile(NAME + SPEECH),
)

CITATION = re.compile(r"[A-Z][a-z]+\s*(?:et al\.)?\s*\(\d{4}\)")


@dataclass(frozen=True)
class Candidate:
    """A name the instructor must review before the cloud path runs."""

    name: str
    line: int
    context: str


def _is_cited(text: str, name: str) -> bool:
    return any(name in match.group(0) for match in CITATION.finditer(text))


def find_candidates(text: str) -> list[Candidate]:
    """Return names appearing in personal contexts, skipping citations."""
    found: list[Candidate] = []
    seen: set[str] = set()

    for pattern in PERSONAL:
        for match in pattern.finditer(text):
            name = match.group(1)
            if name in seen or _is_cited(text, name):
                continue
            seen.add(name)
            line = text.count("\n", 0, match.start()) + 1
            start = max(0, match.start() - CONTEXT_WIDTH)
            context = text[start : match.end() + CONTEXT_WIDTH]
            found.append(Candidate(name, line, context.replace("\n", " ").strip()))

    return sorted(found, key=lambda c: (c.line, c.name))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_candidates.py -v`
Expected: 8 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/redaction/candidates.py tests/redaction/test_candidates.py
git commit -m "Add layer 4 personal-name flagging that skips citations"
```

---

### Task 9: Warn when content identifies its author

No tokenizer catches "my grandmother's escape from Saigon in 1975". This pass warns and leaves the decision to the instructor.

**Files:**
- Create: `scripts/redaction/selfid.py`
- Test: `tests/redaction/test_selfid.py`

**Interfaces:**
- Produces: `SelfIdWarning` with `.line: int`, `.sentence: str`, `.reasons: tuple[str, ...]`; and `find_self_identifying(text) -> list[SelfIdWarning]`.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_selfid.py`:

```python
"""The warning pass must catch autobiography without crying wolf."""

from scripts.redaction.selfid import find_self_identifying


def test_family_plus_place_plus_year_warns():
    text = "My grandmother's escape from Saigon in 1975 shaped my view."
    warnings = find_self_identifying(text)
    assert len(warnings) == 1
    assert set(warnings[0].reasons) >= {"family", "year"}


def test_first_person_alone_does_not_warn():
    """Academic essays use the first person. That is not autobiography."""
    assert find_self_identifying("I argue that platforms curate speech.") == []


def test_family_without_first_person_does_not_warn():
    assert find_self_identifying("Grandmothers were surveyed in the study.") == []


def test_employer_disclosure_warns():
    text = "I worked at Kaiser Permanente for six years before this course."
    warnings = find_self_identifying(text)
    assert warnings and "employer" in warnings[0].reasons


def test_the_warning_reports_its_line_and_sentence():
    text = "First line.\nMy father lost his farm in 1987."
    warnings = find_self_identifying(text)
    assert warnings[0].line == 2
    assert warnings[0].sentence.startswith("My father")


def test_multiple_passages_each_warn():
    text = "My mother arrived in 1990. Platforms curate. My uncle farmed in 2001."
    assert len(find_self_identifying(text)) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_selfid.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`scripts/redaction/selfid.py`:

```python
"""Warn when a passage may identify its author regardless of tokens.

Pseudonymization is not anonymization. This pass finds first-person writing
clustered with family, employment, or dated personal events, quotes the
sentence, and leaves the decision to the instructor. It never blocks and never
edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me|mine)\b", re.IGNORECASE)

MARKERS = {
    "family": re.compile(
        r"\b(?:mother|father|mom|dad|grandmother|grandfather|brother|sister|"
        r"aunt|uncle|cousin|son|daughter|wife|husband|partner)\b",
        re.IGNORECASE,
    ),
    "employer": re.compile(
        r"\b(?:worked|work|interned|employed|job)\s+(?:at|for)\s+[A-Z][\w&.\- ]{2,}",
    ),
    "year": re.compile(r"\b(?:19|20)\d{2}\b"),
    "hometown": re.compile(
        r"\b(?:grew up in|hometown|moved to|born in|raised in)\b", re.IGNORECASE
    ),
}

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True)
class SelfIdWarning:
    """A sentence that may identify its author from content alone."""

    line: int
    sentence: str
    reasons: tuple[str, ...]


def find_self_identifying(text: str) -> list[SelfIdWarning]:
    """Return sentences where first-person writing meets personal markers."""
    warnings: list[SelfIdWarning] = []

    for match in SENTENCE.finditer(text):
        sentence = match.group(0).strip()
        if not sentence or not FIRST_PERSON.search(sentence):
            continue

        reasons = tuple(
            name for name, pattern in MARKERS.items() if pattern.search(sentence)
        )
        if not reasons:
            continue

        line = text.count("\n", 0, match.start()) + 1
        warnings.append(SelfIdWarning(line, sentence, reasons))

    return warnings
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_selfid.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/redaction/selfid.py tests/redaction/test_selfid.py
git commit -m "Warn when content may identify its author"
```

---

### Task 10: The pipeline, the coverage report, and fail-closed exits

**Files:**
- Create: `scripts/redaction/pipeline.py`
- Create: `scripts/redact.py`
- Test: `tests/redaction/test_pipeline.py`

**Interfaces:**
- Consumes: `load_roster`, `redact_roster`, `redact_structured`, `extract`, `output_name`, `find_candidates`, `find_self_identifying`, `TokenMap`.
- Produces: `FileResult`, `BatchResult`, `run(...) -> BatchResult`, and the exit codes below.

| Code | Meaning |
| --- | --- |
| 0 | Clean |
| 2 | A roster entry never appeared in any file |
| 3 | Layer 4 flagged candidates that are unreviewed |
| 4 | A file could not be read or extracted |

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_pipeline.py`:

```python
"""The pipeline must produce redacted output and fail closed."""

import json

from scripts.redaction.pipeline import EXIT_CANDIDATES, EXIT_OK, EXIT_ROSTER, EXIT_UNREADABLE, run
from scripts.redaction.roster import RosterEntry


def _write(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


def test_a_clean_batch_exits_zero(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_OK


def test_output_is_named_by_token_and_holds_no_name(tmp_path):
    source = tmp_path / "in"
    _write(source, "lin_maya_essay.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    written = list((tmp_path / "out").iterdir())
    assert [p.name for p in written] == ["STUDENT_01.md"]
    assert "Maya" not in written[0].read_text(encoding="utf-8")
    assert result.exit_code == EXIT_OK


def test_a_missing_roster_student_fails_closed(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin"), RosterEntry("Tyler Brooks")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_ROSTER
    assert [e.name for e in result.unmatched_roster] == ["Tyler Brooks"]


def test_unreviewed_candidates_fail_closed(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin wrote this. My brother Daniel drove me.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_CANDIDATES
    review = json.loads((tmp_path / "out" / "_review.json").read_text(encoding="utf-8"))
    assert review["candidates"][0]["name"] == "Daniel"


def test_reviewed_candidates_pass(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin wrote this. My brother Daniel drove me.")
    common = {
        "source": source,
        "destination": tmp_path / "out",
        "map_path": tmp_path / "map.json",
        "roster": [RosterEntry("Maya Lin")],
        "batch": "essay-1",
    }
    run(**common)
    assert run(**common, reviewed=True).exit_code == EXIT_OK


def test_an_unreadable_file_fails_closed(tmp_path):
    source = tmp_path / "in"
    source.mkdir(parents=True)
    (source / "paper.pdf").write_bytes(b"%PDF-1.4")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_UNREADABLE


def test_the_map_is_written_owner_only(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    map_path = tmp_path / "map.json"
    run(
        source=source,
        destination=tmp_path / "out",
        map_path=map_path,
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert map_path.stat().st_mode & 0o777 == 0o600


def test_the_docx_author_is_tokenized_not_dropped(tmp_path):
    """The author field is the most commonly missed leak."""
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert result.files[0].token == "STUDENT_01"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the pipeline**

`scripts/redaction/pipeline.py`:

```python
"""Run every redaction layer over a batch and fail closed on any doubt.

Silent partial success is the failure mode that matters here, so there is no
override flag. A non-zero exit must stop the cloud path.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .candidates import Candidate, find_candidates
from .documents import extract, output_name
from .patterns import redact_structured
from .roster import BareHit, RosterEntry, redact_roster
from .selfid import SelfIdWarning, find_self_identifying
from .tokens import TokenMap

EXIT_OK = 0
EXIT_ROSTER = 2
EXIT_CANDIDATES = 3
EXIT_UNREADABLE = 4

UNKNOWN_PREFIX = "UNKNOWN"


@dataclass
class FileResult:
    """What happened to one submission."""

    source: Path
    token: str = ""
    output: Path | None = None
    candidates: list[Candidate] = field(default_factory=list)
    warnings: list[SelfIdWarning] = field(default_factory=list)
    bare_hits: list[BareHit] = field(default_factory=list)
    error: str = ""


@dataclass
class BatchResult:
    """What happened to the batch, and whether the cloud path may proceed."""

    files: list[FileResult] = field(default_factory=list)
    unmatched_roster: list[RosterEntry] = field(default_factory=list)
    exit_code: int = EXIT_OK


def run(
    source: Path,
    destination: Path,
    map_path: Path,
    roster: list[RosterEntry],
    batch: str,
    protected: tuple[str, ...] = (),
    bare_names: bool = True,
    reviewed: bool = False,
) -> BatchResult:
    """Redact every submission under source and write the map."""
    destination.mkdir(parents=True, exist_ok=True)
    tmap = TokenMap(batch)
    result = BatchResult()
    matched_names: set[str] = set()
    unknown_count = 0

    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        if path.name.startswith("_"):
            continue

        entry = FileResult(source=path)
        extracted = extract(path)
        if extracted.error:
            entry.error = extracted.error
            result.files.append(entry)
            continue

        text = extracted.text
        if extracted.author:
            text = f"{extracted.author}\n{text}"

        roster_result = redact_roster(
            text, roster, tmap, path.name, protected=protected, bare_names=bare_names
        )
        matched_names |= roster_result.matched
        entry.bare_hits = roster_result.bare_hits

        text, _ = redact_structured(roster_result.text, tmap, path.name)

        entry.candidates = find_candidates(text)
        entry.warnings = find_self_identifying(text)

        tokens = [
            token
            for token, e in tmap.tokens().items()
            if e.kind == "STUDENT" and e.file == path.name
        ]
        if tokens:
            entry.token = tokens[0]
        else:
            unknown_count += 1
            entry.token = f"{UNKNOWN_PREFIX}_{unknown_count:02d}"

        output = destination / output_name(entry.token, path.suffix or ".md")
        output.write_text(text, encoding="utf-8")
        entry.output = output
        result.files.append(entry)

    tmap.save(map_path)

    result.unmatched_roster = [e for e in roster if e.name not in matched_names]
    _write_review(destination, result)

    if any(f.error for f in result.files):
        result.exit_code = EXIT_UNREADABLE
    elif result.unmatched_roster:
        result.exit_code = EXIT_ROSTER
    elif any(f.candidates for f in result.files) and not reviewed:
        result.exit_code = EXIT_CANDIDATES

    return result


def _write_review(destination: Path, result: BatchResult) -> None:
    """Write everything the instructor must look at before the cloud path."""
    payload = {
        "candidates": [
            {"file": f.output.name if f.output else f.source.name, "name": c.name,
             "line": c.line, "context": c.context}
            for f in result.files
            for c in f.candidates
        ],
        "self_identifying": [
            {"file": f.output.name if f.output else f.source.name, "line": w.line,
             "sentence": w.sentence, "reasons": list(w.reasons)}
            for f in result.files
            for w in f.warnings
        ],
        "bare_name_substitutions": [
            {"file": f.output.name if f.output else f.source.name, "name": h.name,
             "token": h.token, "context": h.context}
            for f in result.files
            for h in f.bare_hits
        ],
    }
    (destination / "_review.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_pipeline.py -v`
Expected: 8 passed

- [ ] **Step 5: Write the CLI**

`scripts/redact.py`:

```python
#!/usr/bin/env python3
"""Redact student submissions before any of them reach a cloud model.

Usage:
    python3 scripts/redact.py SOURCE --roster roster.csv --batch essay-3

Exit codes: 0 clean, 2 unmatched roster entry, 3 unreviewed candidates,
4 unreadable file. A non-zero exit must stop the cloud path.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from redaction.pipeline import EXIT_OK, run
from redaction.roster import load_roster


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Folder of raw submissions")
    parser.add_argument("--roster", type=Path, required=True, help="Roster CSV")
    parser.add_argument("--batch", required=True, help="Batch name, such as essay-3")
    parser.add_argument("--out", type=Path, default=Path("redacted"))
    parser.add_argument("--map", type=Path, default=None)
    parser.add_argument("--instructor", action="append", default=[],
                        help="A name that must never be redacted. Repeatable.")
    parser.add_argument("--no-bare-names", action="store_true",
                        help="Skip bare first and last names")
    parser.add_argument("--reviewed", action="store_true",
                        help="Confirm you read _review.json")
    args = parser.parse_args(argv)

    map_path = args.map or Path(".grading/maps") / f"{args.batch}.json"

    result = run(
        source=args.source,
        destination=args.out,
        map_path=map_path,
        roster=load_roster(args.roster),
        batch=args.batch,
        protected=tuple(args.instructor),
        bare_names=not args.no_bare_names,
        reviewed=args.reviewed,
    )

    print(f"Read {len(result.files)} files. Wrote {args.out}. Map at {map_path}.")
    for entry in result.files:
        if entry.error:
            print(f"  ERROR {entry.source.name}: {entry.error}")
    for entry in result.unmatched_roster:
        print(f"  UNMATCHED roster entry never found: {entry.name}")
    flagged = sum(len(f.candidates) for f in result.files)
    warned = sum(len(f.warnings) for f in result.files)
    bare = sum(len(f.bare_hits) for f in result.files)
    print(f"  {flagged} names to review, {warned} self-identifying passages, "
          f"{bare} bare-name substitutions. See {args.out}/_review.json.")

    if result.exit_code != EXIT_OK:
        print("\nRedaction did not complete cleanly. Do not send this to a "
              "cloud model.", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
```

- [ ] **Step 6: Run the CLI against the fixtures**

```bash
python3 scripts/redact.py tests/fixtures/phil210/ungraded \
  --roster tests/fixtures/phil210/roster.csv \
  --batch essay-2 --out /tmp/redacted --map /tmp/essay-2.json \
  --instructor "Marcus Vance"
```

Expected: a non-zero exit, because only 5 of the 10 roster students appear in the ungraded set. Confirm `/tmp/redacted/STUDENT_*.md` exist and hold no real names:

```bash
grep -rE 'Marcus Thorne|Samantha Chen|Jordan Reed' /tmp/redacted && echo LEAK || echo clean
```

Expected: `clean`

- [ ] **Step 7: Commit**

```bash
git add scripts/redaction/pipeline.py scripts/redact.py tests/redaction/test_pipeline.py
git commit -m "Add the redaction pipeline, coverage report, and fail-closed exits"
```

---

### Task 11: Rehydrate

**Files:**
- Create: `scripts/rehydrate.py`
- Test: `tests/redaction/test_rehydrate.py`

**Interfaces:**
- Consumes: `TokenMap.load`, `TokenMap.value_for`.
- Produces: `rehydrate_text(text, tmap) -> tuple[str, list[str]]` returning the text and any unmapped tokens; `main(argv) -> int`.

- [ ] **Step 1: Write the failing tests**

`tests/redaction/test_rehydrate.py`:

```python
"""Rehydration must restore names locally and fail on an unmapped token."""

import pytest

from scripts.rehydrate import rehydrate_text
from scripts.redaction.roster import RosterEntry, redact_roster
from scripts.redaction.tokens import TokenMap


def test_tokens_become_names_again():
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "p1.md")
    text, unmapped = rehydrate_text("STUDENT_01 argues well.", tmap)
    assert text == "Maya Lin argues well."
    assert unmapped == []


def test_an_unmapped_token_is_reported():
    """A stray token pasted into an LMS comment is worth failing over."""
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "p1.md")
    _, unmapped = rehydrate_text("STUDENT_01 and STUDENT_12 differ.", tmap)
    assert unmapped == ["STUDENT_12"]


def test_round_trip_restores_the_original():
    original = "Maya Lin's thesis, at mlin@example.edu, cites Gillespie (2014)."
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")]
    redacted = redact_roster(original, roster, tmap, "p1.md").text
    restored, unmapped = rehydrate_text(redacted, tmap)
    assert restored == original
    assert unmapped == []


def test_possessives_survive_the_round_trip():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    redacted = redact_roster("Maya Lin's essay.", roster, tmap, "p1.md").text
    assert rehydrate_text(redacted, tmap)[0] == "Maya Lin's essay."
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/redaction/test_rehydrate.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.rehydrate'`

- [ ] **Step 3: Write the implementation**

`scripts/rehydrate.py`:

```python
#!/usr/bin/env python3
"""Put real names back into draft feedback. Runs locally only.

Usage:
    python3 scripts/rehydrate.py DRAFTS --map .grading/maps/essay-3.json --out final

Fails when a token in the drafts has no mapping. A stray STUDENT_12 pasted into
an LMS comment is worth failing the run over.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from redaction.tokens import KINDS, TokenMap

TOKEN = re.compile(rf"\b(?:{'|'.join(KINDS)})_\d{{2,}}\b")


def rehydrate_text(text: str, tmap: TokenMap) -> tuple[str, list[str]]:
    """Replace tokens with their real values and report any that are unknown."""
    unmapped: list[str] = []

    def swap(match: re.Match[str]) -> str:
        token = match.group(0)
        value = tmap.value_for(token)
        if value is None:
            if token not in unmapped:
                unmapped.append(token)
            return token
        return value

    return TOKEN.sub(swap, text), unmapped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drafts", type=Path, help="Folder of tokenized drafts")
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("final"))
    args = parser.parse_args(argv)

    tmap = TokenMap.load(args.map)
    args.out.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    for path in sorted(p for p in args.drafts.rglob("*") if p.is_file()):
        if path.name.startswith("_"):
            continue
        text, unmapped = rehydrate_text(path.read_text(encoding="utf-8"), tmap)
        if unmapped:
            problems.append(f"{path.name}: {', '.join(unmapped)}")
        (args.out / path.name).write_text(text, encoding="utf-8")

    if problems:
        print("Unmapped tokens found:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 5

    print(f"Wrote {args.out}. Every file is a DRAFT pending your sign-off.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/redaction/test_rehydrate.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add scripts/rehydrate.py tests/redaction/test_rehydrate.py
git commit -m "Add rehydration that fails on an unmapped token"
```

---

### Task 12: The leak test

The test the whole plan exists to pass. It seeds known PII across all four layers into the real corpus and asserts that none survives.

**Files:**
- Create: `tests/redaction/test_leak.py`
- Create: `references/redaction.md`

- [ ] **Step 1: Write the failing test**

`tests/redaction/test_leak.py`:

```python
"""Nothing personal may survive into the redacted output.

This is the test the redaction pipeline exists to pass. It seeds twenty known
values across all four layers and asserts that zero reach disk.
"""

import pytest

from scripts.redaction.pipeline import run
from scripts.redaction.roster import load_roster

SEEDED = {
    "emails": ["mthorne@example.edu", "schen@example.edu"],
    "ids": ["S1000433", "S1000432"],
    "phones": ["415-555-0142", "(212) 555-8891"],
    "urls": ["https://linkedin.com/in/marcusthorne"],
    "names": [
        "Marcus Thorne", "Samantha Chen", "David Morales", "Chloe Bennett",
        "Jordan Reed", "Tyler Brooks", "Lauren Higgins", "Evan Ramirez",
        "Brandon Scott", "Maya Lin",
    ],
}

MUST_SURVIVE = [
    "Habermas", "Gillespie", "DMCA", "Sherman Act", "TikTok", "2014",
]


@pytest.fixture
def seeded_batch(phil210, tmp_path):
    """Copy the corpus and inject contact details into the first two papers."""
    source = tmp_path / "in"
    source.mkdir()
    papers = sorted((phil210 / "ungraded").glob("*.md"))
    for index, paper in enumerate(papers):
        body = paper.read_text(encoding="utf-8")
        if index == 0:
            body += (
                "\n\nReach me at mthorne@example.edu or 415-555-0142. "
                "Student S1000433. Portfolio: https://linkedin.com/in/marcusthorne\n"
            )
        if index == 1:
            body += "\n\nContact schen@example.edu or (212) 555-8891. ID S1000432.\n"
        (source / paper.name).write_text(body, encoding="utf-8")
    return source


def test_no_seeded_value_survives(seeded_batch, phil210, tmp_path, instructor):
    destination = tmp_path / "out"
    run(
        source=seeded_batch,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch="leak",
        protected=(instructor,),
        reviewed=True,
    )

    corpus = "\n".join(
        path.read_text(encoding="utf-8")
        for path in destination.glob("*.md")
    )
    leaked = [
        value
        for group in SEEDED.values()
        for value in group
        if value in corpus
    ]
    assert leaked == [], f"These values reached the redacted output: {leaked}"


def test_no_filename_carries_a_name(seeded_batch, phil210, tmp_path, instructor):
    destination = tmp_path / "out"
    run(
        source=seeded_batch,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch="leak",
        protected=(instructor,),
        reviewed=True,
    )
    names = " ".join(path.name for path in destination.glob("*.md"))
    assert not any(n.split()[-1] in names for n in SEEDED["names"])


def test_subject_matter_survives(seeded_batch, phil210, tmp_path, instructor):
    """Over-redaction destroys gradeability, so the content must remain."""
    destination = tmp_path / "out"
    run(
        source=seeded_batch,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch="leak",
        protected=(instructor,),
        reviewed=True,
    )
    corpus = "\n".join(
        path.read_text(encoding="utf-8") for path in destination.glob("*.md")
    )
    present = [term for term in MUST_SURVIVE if term in corpus]
    assert present, "The redacted corpus lost all of its subject matter"


def test_the_instructor_survives(seeded_batch, phil210, tmp_path, instructor):
    """Dr. Marcus Vance is the grader, not a student."""
    destination = tmp_path / "out"
    run(
        source=seeded_batch,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch="leak",
        protected=(instructor,),
        reviewed=True,
    )
    graded = tmp_path / "graded_out"
    run(
        source=phil210 / "graded",
        destination=graded,
        map_path=tmp_path / "graded_map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch="leak-graded",
        protected=(instructor,),
        reviewed=True,
    )
    corpus = "\n".join(
        path.read_text(encoding="utf-8") for path in graded.glob("*.md")
    )
    assert "Vance" in corpus
```

- [ ] **Step 2: Run the test to verify it fails or passes for the right reason**

Run: `python3 -m pytest tests/redaction/test_leak.py -v`
Expected: any failure here is a real leak. Fix the layer that let the value through, not the test.

- [ ] **Step 3: Write the redaction reference**

`references/redaction.md`. This is the document the grading skill reads and quotes to the instructor:

```markdown
# What redaction catches, and what it misses

Redaction runs locally before any submission reaches a model. It replaces
identity with stable tokens and keeps the mapping on this machine.

## What it catches

| Layer | Catches |
| --- | --- |
| Roster match | The author and classmates, by name, ID, or email |
| Structured patterns | Emails, phone numbers, student IDs, profile URLs |
| Filename and metadata | Names in filenames and the document author field |
| Review flags | People named in personal contexts inside the essay |

## What it misses

Pseudonymization is not anonymization.

Redaction cannot catch content that identifies its author. "My grandmother's
escape from Saigon in 1975" names no one and still identifies the writer to
anyone who knows the class. The warning pass flags passages like this and
quotes them. It does not remove them, and it does not catch every one.

Places, historical figures, and dates the essay is about are left alone on
purpose. Removing them would make the work impossible to grade.

## What this means for you

Read `_review.json` before you send anything to a cloud model. Your
institution's policy decides whether student work may leave this machine.
This tool does not make that decision.
```

- [ ] **Step 4: Run the whole suite**

Run: `python3 -m pytest tests/ -v && python3 scripts/validate-package.py`
Expected: all tests pass, and `Humanizer package v3.0.0 is valid`

- [ ] **Step 5: Commit**

```bash
git add tests/redaction/test_leak.py references/redaction.md
git commit -m "Add the leak test and the redaction reference"
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: package restructure to Tasks 1 and 2, the reference corpus to Task 3, the four detection layers to Tasks 5 through 8, identity-versus-subject-matter to Tasks 5, 6, and 12, the self-identification pass to Task 9, fail-closed and the map file to Tasks 4 and 10, `references/redaction.md` to Task 12, and the leak, round-trip, filename, and fail-closed tests to Tasks 10 through 12.

**Deferred to Plan 2, by design:** the grader `SKILL.md` and its four hard rules, `references/calibration.md`, `references/rubric-format.md`, calibration with holdout validation, the grading engine, the JSON structured output, the voice pass, the batch consistency pass, and `hooks/block-raw-reads.json`. Task 2 leaves the grader `SKILL.md` a stub for exactly this reason.

**Known gap.** The docx author path in Task 7 is implemented and unit-tested for the no-`python-docx` case, but Task 10's `test_the_docx_author_is_tokenized_not_dropped` uses a Markdown file, so the author-prepend branch is not covered end to end. Add a real `.docx` fixture in Plan 2 if `python-docx` becomes a supported path, or drop docx support and require conversion.

**Type consistency.** `TokenMap.allocate(kind, value, file)` is called identically in `roster.py`, `patterns.py`, and `pipeline.py`. `redact_roster` returns `RosterResult` everywhere. `extract` returns `Extracted` in both its caller and its tests. Exit code names `EXIT_OK`, `EXIT_ROSTER`, `EXIT_CANDIDATES`, `EXIT_UNREADABLE` are defined in `pipeline.py` and imported by name in the tests and the CLI.
