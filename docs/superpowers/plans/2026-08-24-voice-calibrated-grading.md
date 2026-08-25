# Voice-Calibrated Grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the grader itself — calibration from the instructor's rubric and graded samples, a scoring engine whose every claim is checkable, a batch driver with a consistency pass, and the optional hook that turns the read rules from instructions into enforcement.

**Architecture:** The model does the judgement. Scripts do the checking. A `scripts/grading/` package parses the rubric, parses graded samples, counts AI-pattern density, locates every justification quote in the source, verifies each draft, and flags outliers across a batch. The grader `SKILL.md` holds the gate, the four hard rules, and the two flows. Nothing in `scripts/grading/` ever loads the token map.

**Tech Stack:** Python 3.13, standard library only at runtime. `pytest` for tests. Markdown for the skill, the profile, and the references.

**Spec:** `docs/superpowers/specs/2026-08-23-voice-calibrated-grading-design.md`

**Scope note:** This is Plan 2 of 2. Plan 1 (`docs/superpowers/plans/2026-08-23-redaction-foundation.md`) shipped the package restructure and the redaction pipeline at commit `14f7fff`. This plan assumes `scripts/redaction/`, `scripts/redact.py`, `scripts/rehydrate.py`, `references/ai-patterns.md`, `references/redaction.md`, and `tests/fixtures/phil210/` all exist and pass. Read `docs/superpowers/decisions-redaction-foundation.md` before touching anything under `scripts/redaction/`.

## Global Constraints

- Runtime code must use the standard library only. No new dependency may become required.
- **The map file is the boundary.** `.grading/maps/<batch>.json` is the only artifact holding real identities. `redact.py` writes it, `rehydrate.py` reads it, and nothing added by this plan may load it. Where a check would need the map, use the redacted source instead.
- **The model reads only from `redacted/`.** No step may open a path under `submissions/`.
- **Calibration and grading are separate commands.** `/grade calibrate` is local-only. `/grade` is not.
- The profile is a cloud-visible artifact. No student name and no student essay text may reach it. Only the instructor's own comments are kept verbatim, with names replaced by the literal `[STUDENT]`.
- `[STUDENT]` must never match `rehydrate.py`'s token pattern, which is `\b(?:STUDENT|PERSON|EMAIL|PHONE|ID|URL)_\d{2,}\b`. Keep the brackets and keep the underscore out.
- The skill must never infer authorship. Pattern density is evidence for rubric criterion 4 and nothing else.
- Every score needs a quote. A criterion that cannot be assessed gets `"cannot assess"` and a null score, never a guess.
- Grading exit codes live in their own range so they can never be confused with redaction's: `10` no profile, `11` rubric drift, `12` verification failed, `13` malformed input. `0` is clean.
- Prose in this repo follows the Plain Language rules in `AGENTS.md`: lead with the main point, active voice, short sentences, `must` for requirements.
- Run tests with `python3 -m pytest` from the repo root, so `scripts.` imports resolve.
- Every task ends with a commit.

---

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/grading/__init__.py` | Package marker. Empty. |
| `scripts/grading/rubric.py` | Parse the rubric: criteria, points, grade bands, feedback protocol, per-criterion digest. |
| `scripts/grading/samples.py` | Parse a graded sample into essay, annotations, score, and the instructor's comments. Scrub names. |
| `scripts/grading/density.py` | Read watched terms from `references/ai-patterns.md`. Count them in any text. |
| `scripts/grading/records.py` | The grade record: dataclasses, JSON I/O, and quote location. |
| `scripts/grading/profile.py` | Load a calibration profile. Refuse when missing. Name drifted criteria. |
| `scripts/grading/holdout.py` | Compare a generated grade against the instructor's own. |
| `scripts/grading/verify.py` | Gate one draft or a whole batch against source, rubric, and profile. |
| `scripts/grading/batch.py` | Survey a batch so an interrupted run can resume. |
| `scripts/grading/consistency.py` | Flag outliers across a graded batch. |
| `scripts/calibrate.py` | CLI. `inputs` and `holdout`. Local only. |
| `scripts/grade.py` | CLI. `profile`, `status`, `density`, `verify`, `consistency`. |
| `references/rubric-format.md` | What a rubric must contain for the parser to read it. |
| `references/calibration.md` | The calibration interview protocol. |
| `SKILL.md` | The grader. Gate, four hard rules, both flows, error table. |
| `hooks/block-raw-reads.json` | Opt-in `PreToolUse` hook config. Not named `hooks.json`, so it stays inert until merged. |
| `hooks/block_raw_reads.py` | The guard the hook runs. |
| `tests/fixtures/phil210/profile_phil210.md` | A hand-written reference profile, so tests need no model. |
| `tests/grading/test_*.py` | One test module per grading module, plus the hook and the end-to-end run. |

### Where the work happens

| Step | Who does it | What proves it |
| --- | --- | --- |
| Read the rubric | `rubric.py` | Task 1 tests |
| Read graded samples | `samples.py` | Task 2 tests |
| Build the profile | The model, from `references/calibration.md` | Task 6's holdout delta |
| Score a submission | The model, from `SKILL.md` | Task 7's verifier |
| Count AI patterns | `density.py` | Task 3 tests, re-checked by Task 7 |
| Write feedback in voice | The model, then `references/ai-patterns.md` | Task 7's never-use and structure checks |
| Loop the batch | The model, resumed by `batch.py` | Task 8 tests |
| Flag outliers | `consistency.py` | Task 9 tests |
| Reattach real names | `rehydrate.py` (Plan 1) | Task 12's end-to-end run |

---

### Task 1: Parse the rubric

The rubric is the instructor's own Markdown. Everything downstream reads it: criteria and point values for scoring, grade bands so the letter is computed and not chosen, and the feedback protocol so the draft's structure comes from what the instructor already wrote down. Each criterion also gets a digest, which is how a later run notices the rubric changed.

**Files:**
- Create: `scripts/grading/__init__.py`
- Create: `scripts/grading/rubric.py`
- Create: `references/rubric-format.md`
- Create: `tests/grading/test_rubric.py`

**Interfaces:**
- Consumes: `tests/fixtures/phil210/grading_rubric.md` from Plan 1.
- Produces:
  - `Level(name: str, band: str, descriptor: str)`
  - `Criterion(number: int, name: str, points: int, levels: tuple[Level, ...], digest: str)`
  - `Band(low: int, high: int, label: str)`
  - `FeedbackProtocol(sections: tuple[str, ...], sign_offs: tuple[str, ...], requires_inline_annotations: bool)`
  - `Rubric(path: Path, title: str, criteria: tuple[Criterion, ...], bands: tuple[Band, ...], protocol: FeedbackProtocol)` with `.total_points -> int`, `.letter_for(percent: float) -> str`, `.criterion(number: int) -> Criterion`
  - `load_rubric(path: Path) -> Rubric`
  - `RubricError(Exception)`

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_rubric.py`:

```python
"""The rubric is the instructor's own document. The parser must read it, not rewrite it."""

from pathlib import Path

import pytest

from scripts.grading.rubric import RubricError, load_rubric


def test_it_reads_every_criterion(phil210):
    rubric = load_rubric(phil210 / "grading_rubric.md")
    assert [c.number for c in rubric.criteria] == [1, 2, 3, 4]
    assert rubric.criteria[0].name == "Thesis & Theoretical Framing"
    assert rubric.criteria[3].name == "Structure, Prose & Academic Voice"


def test_it_reads_the_point_value_of_each_criterion(phil210):
    rubric = load_rubric(phil210 / "grading_rubric.md")
    assert [c.points for c in rubric.criteria] == [25, 25, 25, 25]
    assert rubric.total_points == 100


def test_it_reads_the_four_levels_with_their_bands(phil210):
    rubric = load_rubric(phil210 / "grading_rubric.md")
    levels = rubric.criterion(1).levels
    assert [level.name for level in levels] == [
        "Exemplary",
        "Proficient",
        "Developing",
        "Unacceptable",
    ]
    assert levels[0].band == "A: 90–100%"
    assert "defensible, non-obvious thesis" in levels[0].descriptor


def test_the_letter_comes_from_the_rubric_scale_not_from_a_guess(phil210):
    rubric = load_rubric(phil210 / "grading_rubric.md")
    assert rubric.letter_for(94) == "A"
    assert rubric.letter_for(88) == "B+"
    assert rubric.letter_for(83) == "B"
    assert rubric.letter_for(78) == "C+"
    assert rubric.letter_for(65) == "D"
    assert rubric.letter_for(42) == "F"


def test_every_score_from_0_to_100_lands_in_a_band(phil210):
    rubric = load_rubric(phil210 / "grading_rubric.md")
    for percent in range(0, 101):
        assert rubric.letter_for(percent)


def test_it_takes_the_feedback_structure_from_the_rubric(phil210):
    protocol = load_rubric(phil210 / "grading_rubric.md").protocol
    assert protocol.sections == (
        "The Bottom Line",
        "What Worked",
        "What to Work On",
    )
    assert protocol.requires_inline_annotations is True
    assert protocol.sign_offs == ("— Dr. Vance", "— MV")


def test_a_criterion_digest_changes_when_its_wording_changes(phil210, tmp_path):
    original = load_rubric(phil210 / "grading_rubric.md")
    text = (phil210 / "grading_rubric.md").read_text(encoding="utf-8")
    edited = tmp_path / "edited.md"
    edited.write_text(
        text.replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )
    changed = load_rubric(edited)
    assert changed.criterion(1).digest != original.criterion(1).digest
    assert changed.criterion(2).digest == original.criterion(2).digest


def test_a_digest_ignores_whitespace_only_edits(phil210, tmp_path):
    original = load_rubric(phil210 / "grading_rubric.md")
    text = (phil210 / "grading_rubric.md").read_text(encoding="utf-8")
    reflowed = tmp_path / "reflowed.md"
    reflowed.write_text(text.replace(" | ", "  |  "), encoding="utf-8")
    assert load_rubric(reflowed).criterion(1).digest == original.criterion(1).digest


def test_a_rubric_with_no_criteria_table_is_refused(tmp_path):
    path = tmp_path / "empty.md"
    path.write_text("# A rubric with nothing in it\n", encoding="utf-8")
    with pytest.raises(RubricError, match="criteria table"):
        load_rubric(path)


def test_an_unnumbered_criterion_is_refused(tmp_path):
    path = tmp_path / "unnumbered.md"
    path.write_text(
        "# R\n\n| Evaluation Criterion | Exemplary (A: 90–100%) | Weight |\n"
        "| :--- | :--- | :---: |\n"
        "| **Thesis** | Sharp. | **25 pts** |\n\n"
        "* **90–100 (A)**: Good.\n",
        encoding="utf-8",
    )
    with pytest.raises(RubricError, match="Number each criterion"):
        load_rubric(path)


def test_a_rubric_with_no_feedback_protocol_still_parses(tmp_path):
    path = tmp_path / "plain.md"
    path.write_text(
        "# R\n\n| Evaluation Criterion | Exemplary (A: 90–100%) | Weight |\n"
        "| :--- | :--- | :---: |\n"
        "| **1. Thesis** | Sharp. | **25 pts** |\n\n"
        "* **90–100 (A)**: Good.\n"
        "* **< 90 (F)**: Not good.\n",
        encoding="utf-8",
    )
    rubric = load_rubric(path)
    assert rubric.protocol.sections == ()
    assert rubric.protocol.sign_offs == ()
    assert rubric.protocol.requires_inline_annotations is False
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_rubric.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading'`

- [ ] **Step 3: Create the package marker**

`scripts/grading/__init__.py` is an empty file.

```bash
mkdir -p scripts/grading tests/grading
touch scripts/grading/__init__.py
```

- [ ] **Step 4: Write the parser**

`scripts/grading/rubric.py`:

```python
"""Parse the instructor's rubric into the shape every later step reads.

The rubric belongs to the instructor. Nothing here rewrites it. The parser
reads three things: the criteria table, the letter-grade scale, and the
feedback protocol.

Reading the protocol matters. The PHIL 210 rubric already states its own
feedback shape -- inline annotations, a Bottom Line, What Worked, What to
Work On, and a sign-off. Taking that from the rubric is better than
inferring a structure the instructor already wrote down.

Each criterion also carries a digest of its own wording. That is how a run
weeks later can say which criteria drifted since calibration, instead of
saying only that something changed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]*)`")
_NUMBERED = re.compile(r"^(\d+)\.\s*(.+)$")
_POINTS = re.compile(r"(\d+)\s*pts?\b", re.IGNORECASE)
_LEVEL = re.compile(r"^(.+?)\s*\(([^)]*)\)\s*$")
_BAND = re.compile(
    r"^\*\s*\*\*\s*(?:<\s*(\d+)|(\d+)\s*[–—-]\s*(\d+))\s*\(([^)]+)\)\*\*"
)
_PROTOCOL_ITEM = re.compile(r"^(\d+)\.\s*\*\*(.+?)\*\*")
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")

# The two protocol items that are not sections of the written feedback. One
# is checked by looking for bracketed annotations in the essay, the other by
# matching the closing line, so both are held apart from `sections`.
_ANNOTATION_WORD = "annotation"
_SIGN_OFF_PREFIX = "sign-off"


class RubricError(Exception):
    """The rubric does not have the shape the grader needs."""


def _norm(text: str) -> str:
    """Collapse whitespace so a reflowed table is not read as an edit."""
    return re.sub(r"\s+", " ", text).strip()


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


@dataclass(frozen=True)
class Level:
    """One performance level of one criterion."""

    name: str
    band: str
    descriptor: str


@dataclass(frozen=True)
class Criterion:
    """One row of the rubric table."""

    number: int
    name: str
    points: int
    levels: tuple[Level, ...]
    digest: str


@dataclass(frozen=True)
class Band:
    """One row of the letter-grade scale. Bounds are inclusive."""

    low: int
    high: int
    label: str


@dataclass(frozen=True)
class FeedbackProtocol:
    """The feedback shape the rubric asks for, when it states one."""

    sections: tuple[str, ...] = ()
    sign_offs: tuple[str, ...] = ()
    requires_inline_annotations: bool = False


@dataclass(frozen=True)
class Rubric:
    """A parsed rubric."""

    path: Path
    title: str
    criteria: tuple[Criterion, ...]
    bands: tuple[Band, ...]
    protocol: FeedbackProtocol

    @property
    def total_points(self) -> int:
        return sum(c.points for c in self.criteria)

    def criterion(self, number: int) -> Criterion:
        for item in self.criteria:
            if item.number == number:
                return item
        raise RubricError(f"No criterion numbered {number} in {self.path}")

    def letter_for(self, percent: float) -> str:
        """Return the rubric's own label for a percentage.

        The letter is computed, never chosen. A model that picks its own
        letter can drift from the scale the instructor published.
        """
        value = int(round(percent))
        for band in self.bands:
            if band.low <= value <= band.high:
                return band.label
        raise RubricError(
            f"The grading scale has no band covering {value}. "
            "Cover every score from 0 to 100."
        )


def _digest(name: str, points: int, levels: tuple[Level, ...]) -> str:
    parts = [_norm(name), str(points)]
    parts += [f"{_norm(level.name)}={_norm(level.descriptor)}" for level in levels]
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _level_parts(header_cell: str) -> tuple[str, str]:
    match = _LEVEL.match(_norm(header_cell))
    if match:
        return _norm(match.group(1)), _norm(match.group(2))
    return _norm(header_cell), ""


def _parse_criteria(lines: list[str]) -> tuple[Criterion, ...]:
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if header is None:
            if stripped.startswith("|") and "criterion" in stripped.lower():
                header = _cells(stripped)
            continue
        if _SEPARATOR.match(stripped):
            continue
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        rows.append(_cells(stripped))

    if header is None or not rows:
        raise RubricError(
            "No criteria table found. The table's header row must name a "
            "Criterion column, and each following row must be one criterion."
        )

    weight_index = len(header) - 1
    for index, cell in enumerate(header):
        if cell.strip().lower() in {"weight", "points", "weighting"}:
            weight_index = index
            break
    level_headers = [_level_parts(cell) for cell in header[1:weight_index]]

    criteria: list[Criterion] = []
    for row in rows:
        if len(row) != len(header):
            raise RubricError(
                f"A criteria row has {len(row)} cells but the header has "
                f"{len(header)}: {row[0]!r}"
            )
        bold = _BOLD.search(row[0])
        raw_name = _norm(bold.group(1) if bold else row[0])
        numbered = _NUMBERED.match(raw_name)
        if not numbered:
            raise RubricError(
                f"Number each criterion, as in '1. Thesis': {raw_name!r}"
            )
        points_match = _POINTS.search(row[weight_index])
        if not points_match:
            raise RubricError(
                f"Give each criterion a point value, as in '25 pts': "
                f"{row[weight_index]!r}"
            )
        levels = tuple(
            Level(name=name, band=band, descriptor=_norm(row[1 + offset]))
            for offset, (name, band) in enumerate(level_headers)
        )
        points = int(points_match.group(1))
        name = numbered.group(2)
        criteria.append(
            Criterion(
                number=int(numbered.group(1)),
                name=name,
                points=points,
                levels=levels,
                digest=_digest(name, points, levels),
            )
        )
    return tuple(criteria)


def _parse_bands(lines: list[str]) -> tuple[Band, ...]:
    bands: list[Band] = []
    for line in lines:
        match = _BAND.match(line.strip())
        if not match:
            continue
        below, low, high, label = match.groups()
        if below is not None:
            bands.append(Band(0, int(below) - 1, _norm(label)))
        else:
            bands.append(Band(int(low), int(high), _norm(label)))
    if not bands:
        raise RubricError(
            "No grading scale found. List each band as "
            "'* **90-92 (A-)**: ...' so the letter can be computed."
        )
    return tuple(sorted(bands, key=lambda band: band.low))


def _parse_protocol(lines: list[str]) -> FeedbackProtocol:
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith("## ") and "feedback protocol" in line.lower():
            start = index
            break
    if start is None:
        return FeedbackProtocol()

    sections: list[str] = []
    sign_offs: tuple[str, ...] = ()
    inline = False
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        match = _PROTOCOL_ITEM.match(line.strip())
        if not match:
            continue
        label = _norm(_CODE.sub("", match.group(2)))
        bare = _norm(_PARENTHETICAL.sub("", label))
        if _ANNOTATION_WORD in bare.lower():
            inline = True
        elif bare.lower().startswith(_SIGN_OFF_PREFIX):
            sign_offs = tuple(
                _norm(found) for found in _CODE.findall(line) if _norm(found)
            )
        else:
            sections.append(bare)
    return FeedbackProtocol(
        sections=tuple(sections),
        sign_offs=sign_offs,
        requires_inline_annotations=inline,
    )


def load_rubric(path: Path) -> Rubric:
    """Read a rubric from Markdown."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next(
        (_norm(line[2:]) for line in lines if line.startswith("# ")), path.stem
    )
    return Rubric(
        path=path,
        title=title,
        criteria=_parse_criteria(lines),
        bands=_parse_bands(lines),
        protocol=_parse_protocol(lines),
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_rubric.py -v`
Expected: 11 passed

- [ ] **Step 6: Write the rubric format reference**

`references/rubric-format.md`:

```markdown
# What a rubric must contain

The grader reads your rubric. It does not rewrite it. This page says what the
parser needs so your rubric reads cleanly.

## A criteria table

One Markdown table. Its header row must name a **Criterion** column, one
column per performance level, and a **Weight** column last.

| Evaluation Criterion | Exemplary (A: 90–100%) | Proficient (B: 80–89%) | Weight |
| :--- | :--- | :--- | :---: |
| **1. Thesis** | Sharp and defensible. | Clear, if conventional. | **25 pts** |

Three rules:

- **Number each criterion.** `**1. Thesis**`, not `**Thesis**`. Scores are
  recorded against the number, so a renamed criterion still lines up.
- **Give each criterion a point value.** `**25 pts**` in the Weight column.
- **Name each level in its header.** The text before the parentheses is the
  level name. The text inside is its band.

## A grading scale

One bullet per band. Bounds are inclusive.

```
* **93–100 (A)**: Outstanding original synthesis.
* **90–92 (A-)**: Strong, ambitious essay.
* **< 60 (F)**: Plagiarized, off-topic, or incomplete.
```

The bands must cover every score from 0 to 100. The grader computes the
letter from this scale. It never picks one.

## A feedback protocol, if you want one

State the shape of your feedback and the grader follows it. Skip this section
and calibration infers a shape from your graded samples instead.

```
## Instructor Feedback Protocol

1. **Inline Bracketed Annotations `[...]`**: Marginal comments pinned to
   specific sentences.
2. **The Bottom Line**: A frank assessment of whether the argument holds.
3. **What Worked (Strengths)**: Bullet points citing specific paragraphs.
4. **What to Work On (Areas for Growth)**: Concrete revisions.
5. **Sign-off**: Signature closing: `— Dr. Vance` (or `— MV`).
```

How each item is read:

| Item | Effect |
| --- | --- |
| A label containing "annotation" | Every draft must carry bracketed inline comments |
| A label starting with "Sign-off" | Each backticked string becomes an accepted closing |
| Any other label | Becomes a required section of the written feedback |

A parenthetical in a label is dropped, so `What Worked (Strengths)` becomes
the section `What Worked`.

## Drift

Each criterion carries a digest of its own wording. When you edit a
criterion, `grade.py profile` names that criterion as drifted and asks before
grading. Reflowing the table or changing its spacing is not an edit. Changing
a word is.
```

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -v && python3 scripts/validate-package.py`
Expected: all pass, and `Humanizer package v3.0.0 is valid`

- [ ] **Step 8: Commit**

```bash
git add scripts/grading/__init__.py scripts/grading/rubric.py tests/grading/test_rubric.py references/rubric-format.md
git commit -m "Parse the rubric into criteria, bands, and a feedback protocol"
```

---

### Task 2: Parse graded samples and scrub names

Calibration learns the instructor's voice from work they already graded. This task splits a graded sample into what the student wrote and what the instructor wrote, so only the instructor's half can reach the profile. It also scrubs roster names out of that half, because the profile is read during cloud runs.

**Files:**
- Create: `scripts/grading/samples.py`
- Create: `tests/grading/test_samples.py`

**Interfaces:**
- Consumes: `load_roster`, `RosterEntry`, `bare_name_variants`, `full_name_variants` from `scripts.redaction.roster` (Plan 1).
- Produces:
  - `Annotation(text: str, offset: int)`
  - `GradedSample(path, header, essay, annotations, score, possible, letter, bottom_line, what_worked, what_to_work_on, closing, sign_off)` with `.comments_only() -> str`
  - `load_sample(path: Path) -> GradedSample`
  - `scrub_names(text: str, roster: list[RosterEntry], placeholder: str = "[STUDENT]") -> tuple[str, list[str]]`
  - `SampleError(Exception)`

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_samples.py`:

```python
"""A graded sample holds two voices. Only the instructor's may reach the profile."""

import pytest

from scripts.grading.samples import SampleError, load_sample, scrub_names
from scripts.redaction.roster import load_roster

PAPER_1 = "graded/paper_1_graded_algorithmic_gatekeeping.md"
PAPER_3 = "graded/paper_3_graded_gig_economy_ai_slop.md"


def test_it_reads_the_score_and_the_letter(phil210):
    sample = load_sample(phil210 / PAPER_1)
    assert sample.score == 94
    assert sample.possible == 100
    assert sample.letter == "A"


def test_it_reads_a_failing_sample_too(phil210):
    sample = load_sample(phil210 / PAPER_3)
    assert sample.score == 65
    assert sample.letter == "D"


def test_it_reads_the_header_block(phil210):
    sample = load_sample(phil210 / PAPER_1)
    assert sample.header["Student"] == "Maya Lin"
    assert sample.header["Course"] == "PHIL/POLISCI 210"


def test_it_separates_the_essay_from_the_feedback(phil210):
    sample = load_sample(phil210 / PAPER_1)
    assert "Jürgen Habermas envisioned an idealized forum" in sample.essay
    assert "Keep up the rigorous work" not in sample.essay


def test_it_reads_every_inline_annotation(phil210):
    sample = load_sample(phil210 / PAPER_1)
    texts = [annotation.text for annotation in sample.annotations]
    assert len(texts) == 4
    assert texts[0].startswith("Good theoretical anchor")
    assert all(annotation.offset >= 0 for annotation in sample.annotations)


def test_it_reads_the_three_feedback_sections(phil210):
    sample = load_sample(phil210 / PAPER_1)
    assert sample.bottom_line.startswith("Maya, this is an exceptionally")
    assert len(sample.what_worked) == 2
    assert len(sample.what_to_work_on) == 2
    assert sample.what_worked[0].startswith("Your use of Gillespie")


def test_it_reads_the_closing_and_the_sign_off(phil210):
    sample = load_sample(phil210 / PAPER_1)
    assert sample.closing == "Keep up the rigorous work."
    assert sample.sign_off == "— Dr. Vance"


def test_a_sample_with_trailing_prose_after_the_bullets_keeps_it(phil210):
    sample = load_sample(phil210 / PAPER_3)
    assert "office hours" in sample.closing


def test_comments_only_holds_no_student_prose(phil210):
    sample = load_sample(phil210 / PAPER_1)
    comments = sample.comments_only()
    assert "Jürgen Habermas envisioned an idealized forum" not in comments
    assert "Keep up the rigorous work." in comments
    assert "Good theoretical anchor" in comments


def test_scrub_replaces_a_full_roster_name(phil210):
    roster = load_roster(phil210 / "roster.csv")
    scrubbed, found = scrub_names("Maya Lin argued well.", roster)
    assert scrubbed == "[STUDENT] argued well."
    assert found == ["Maya Lin"]


def test_scrub_replaces_a_first_name_on_its_own(phil210):
    roster = load_roster(phil210 / "roster.csv")
    scrubbed, found = scrub_names("Maya, this is exceptional work.", roster)
    assert scrubbed == "[STUDENT], this is exceptional work."
    assert found == ["Maya"]


def test_scrub_leaves_a_cited_scholar_alone(phil210):
    roster = load_roster(phil210 / "roster.csv")
    scrubbed, _ = scrub_names("Your use of Gillespie (2014) was strong.", roster)
    assert scrubbed == "Your use of Gillespie (2014) was strong."


def test_the_placeholder_cannot_be_mistaken_for_a_redaction_token(phil210):
    from scripts.redaction.rehydrate import TOKEN

    roster = load_roster(phil210 / "roster.csv")
    scrubbed, _ = scrub_names("Maya Lin argued well.", roster)
    assert TOKEN.search(scrubbed) is None


def test_a_sample_with_no_grade_line_is_refused(tmp_path):
    path = tmp_path / "ungraded.md"
    path.write_text(
        "**Student**: A\n\n---\n\n### Student Submission\n\nText.\n\n"
        "---\n\n### Instructor Feedback & Grading\n\nNo grade here.\n",
        encoding="utf-8",
    )
    with pytest.raises(SampleError, match="Grade"):
        load_sample(path)


def test_a_sample_with_no_feedback_section_is_refused(tmp_path):
    path = tmp_path / "nofeedback.md"
    path.write_text("### Student Submission\n\nJust an essay.\n", encoding="utf-8")
    with pytest.raises(SampleError, match="feedback"):
        load_sample(path)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_samples.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.samples'`

- [ ] **Step 3: Write the sample parser**

`scripts/grading/samples.py`:

```python
"""Split a graded sample into the student's half and the instructor's half.

Only the instructor's half may reach the profile. That is the whole reason
this module exists: the profile is read during cloud runs, so anything it
holds is something a cloud model sees. The instructor's comments are the
voice; the student's essay is not needed to learn it.

Names get scrubbed on the way out. An instructor writes "Maya, this is
exceptional work", and that first name identifies a past student just as
surely as a header would. The replacement is the literal `[STUDENT]`, which
deliberately does NOT match rehydrate.py's token pattern -- a `STUDENT_01`
leaking out of a profile would be reattached to whichever student holds that
token in the CURRENT batch, which is a misattribution, not a redaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.redaction.roster import RosterEntry, bare_name_variants, full_name_variants

PLACEHOLDER = "[STUDENT]"

_HEADER_FIELD = re.compile(r"^\*\*(.+?)\*\*\s*:\s*(.+?)\s*$")
_SUBMISSION_HEADING = re.compile(r"^###\s+Student Submission", re.IGNORECASE)
_FEEDBACK_HEADING = re.compile(r"^###\s+Instructor Feedback", re.IGNORECASE)
_GRADE = re.compile(r"^\*\*Grade\*\*\s*:\s*(\d+)\s*/\s*(\d+)\s*\(([^)]+)\)")
_LABEL = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$")
_BULLET = re.compile(r"^\*\s+(.+)$")
_SIGN_OFF = re.compile(r"^\s*[—–-]{1,2}\s*(.+?)\s*$")
_ANNOTATION = re.compile(r"\[([^\[\]]{10,})\]")


class SampleError(Exception):
    """The graded sample does not have the shape calibration needs."""


@dataclass(frozen=True)
class Annotation:
    """One inline bracketed comment and where it sits in the essay."""

    text: str
    offset: int


@dataclass
class GradedSample:
    """One piece of work the instructor already graded."""

    path: Path
    header: dict[str, str] = field(default_factory=dict)
    essay: str = ""
    annotations: tuple[Annotation, ...] = ()
    score: int = 0
    possible: int = 100
    letter: str = ""
    bottom_line: str = ""
    what_worked: tuple[str, ...] = ()
    what_to_work_on: tuple[str, ...] = ()
    closing: str = ""
    sign_off: str = ""

    def comments_only(self) -> str:
        """Return everything the instructor wrote, and nothing the student wrote.

        This is the calibration input. Run it through scrub_names before it
        reaches a profile.
        """
        parts: list[str] = []
        for annotation in self.annotations:
            parts.append(f"[{annotation.text}]")
        if self.bottom_line:
            parts.append(self.bottom_line)
        parts.extend(self.what_worked)
        parts.extend(self.what_to_work_on)
        if self.closing:
            parts.append(self.closing)
        if self.sign_off:
            parts.append(self.sign_off)
        return "\n\n".join(parts)


def _split_sections(text: str) -> tuple[str, str, str]:
    """Return the header block, the essay, and the feedback."""
    lines = text.splitlines()
    submission_at: int | None = None
    feedback_at: int | None = None
    for index, line in enumerate(lines):
        if submission_at is None and _SUBMISSION_HEADING.match(line):
            submission_at = index
        elif _FEEDBACK_HEADING.match(line):
            feedback_at = index
            break
    if feedback_at is None:
        raise SampleError(
            "The sample has no instructor feedback section. Add a "
            "'### Instructor Feedback & Grading' heading."
        )
    start = 0 if submission_at is None else submission_at + 1
    header = "\n".join(lines[: submission_at if submission_at is not None else 0])
    essay = "\n".join(lines[start:feedback_at])
    feedback = "\n".join(lines[feedback_at + 1 :])
    return header, essay.strip("\n"), feedback.strip("\n")


def _parse_header(block: str) -> dict[str, str]:
    header: dict[str, str] = {}
    for line in block.splitlines():
        match = _HEADER_FIELD.match(line.strip())
        if match:
            header[match.group(1).strip()] = match.group(2).strip()
    return header


def _parse_feedback(feedback: str) -> dict[str, object]:
    grade_match = None
    for line in feedback.splitlines():
        grade_match = _GRADE.match(line.strip())
        if grade_match:
            break
    if not grade_match:
        raise SampleError(
            "No Grade line found. Write it as '**Grade**: 94/100 (A)'."
        )

    label = ""
    prose: dict[str, list[str]] = {}
    bullets: dict[str, list[str]] = {}
    trailing: list[str] = []
    sign_off = ""
    seen_a_section = False

    for raw in feedback.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _GRADE.match(line):
            continue
        sign = _SIGN_OFF.match(line)
        if sign and not line.startswith("*") and len(line) <= 40:
            sign_off = line
            continue
        labelled = _LABEL.match(line)
        if labelled and not _BULLET.match(line):
            label = labelled.group(1).strip().rstrip(":")
            seen_a_section = True
            prose.setdefault(label, [])
            bullets.setdefault(label, [])
            remainder = labelled.group(2).strip()
            if remainder:
                prose[label].append(remainder)
            continue
        bullet = _BULLET.match(line)
        if bullet and label:
            bullets[label].append(bullet.group(1).strip())
            continue
        if label and not bullets[label]:
            prose[label].append(line)
        elif seen_a_section:
            trailing.append(line)

    def section(name: str) -> str:
        for key, value in prose.items():
            if key.lower().lstrip("the ").startswith(name):
                return " ".join(value).strip()
        return ""

    def listing(name: str) -> tuple[str, ...]:
        for key, value in bullets.items():
            if key.lower().startswith(name):
                return tuple(value)
        return ()

    return {
        "score": int(grade_match.group(1)),
        "possible": int(grade_match.group(2)),
        "letter": grade_match.group(3).strip(),
        "bottom_line": section("bottom line"),
        "what_worked": listing("what worked"),
        "what_to_work_on": listing("what to work on"),
        "closing": " ".join(trailing).strip(),
        "sign_off": sign_off,
    }


def load_sample(path: Path) -> GradedSample:
    """Read one graded sample."""
    text = path.read_text(encoding="utf-8")
    # No pre-check here. _split_sections' guard is already case-insensitive and
    # per-line; a whole-text `_FEEDBACK_HEADING.search` would only match at
    # position 0 (no re.MULTILINE) and silently degrade to a case-SENSITIVE
    # substring test, wrongly refusing a valid lowercase heading.
    header_block, essay, feedback = _split_sections(text)
    parsed = _parse_feedback(feedback)
    annotations = tuple(
        Annotation(text=match.group(1).strip(), offset=match.start())
        for match in _ANNOTATION.finditer(essay)
    )
    return GradedSample(
        path=path,
        header=_parse_header(header_block),
        essay=essay,
        annotations=annotations,
        score=int(parsed["score"]),
        possible=int(parsed["possible"]),
        letter=str(parsed["letter"]),
        bottom_line=str(parsed["bottom_line"]),
        what_worked=tuple(parsed["what_worked"]),
        what_to_work_on=tuple(parsed["what_to_work_on"]),
        closing=str(parsed["closing"]),
        sign_off=str(parsed["sign_off"]),
    )


def scrub_names(
    text: str,
    roster: list[RosterEntry],
    placeholder: str = PLACEHOLDER,
) -> tuple[str, list[str]]:
    """Replace roster names with a placeholder and report what was found.

    Full names go first so "Maya Lin" never becomes "[STUDENT] Lin". Bare
    first and last names follow. A name that is not on the roster is left
    alone -- a cited scholar is subject matter, not a student.
    """
    found: list[str] = []
    scrubbed = text
    forms: list[str] = []
    for entry in roster:
        forms.extend(full_name_variants(entry.name))
    for entry in roster:
        forms.extend(bare_name_variants(entry.name))
    for form in forms:
        pattern = re.compile(rf"\b{re.escape(form)}\b")
        scrubbed, count = pattern.subn(placeholder, scrubbed)
        if count:
            found.append(form)
    return scrubbed, found
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_samples.py -v`
Expected: 15 passed

If `_split_sections` raises on the missing-feedback fixture with the wrong message, fix the message rather than the test — the guard in `load_sample` is what that test targets.

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add scripts/grading/samples.py tests/grading/test_samples.py
git commit -m "Split graded samples into essay and comments, and scrub roster names"
```

---

### Task 3: Count AI-pattern density

Rubric criterion 4 names AI buzzwords as an Unacceptable marker. Counting them is assessing prose against a stated criterion, which is in scope. Concluding who or what wrote the essay is out of scope, and this module must never do it. Counting in a script rather than by model gives the instructor an evidence trail: a number, a per-pattern breakdown, and the exact hits.

**Files:**
- Create: `scripts/grading/density.py`
- Create: `tests/grading/test_density.py`

**Interfaces:**
- Consumes: `references/ai-patterns.md` from Plan 1, whose watched-term lines are `**High-frequency AI words:**`, `**Words to watch:**`, and `**Phrases to watch:**` under `### N.` headings.
- Produces:
  - `Term(text: str, pattern: int)`
  - `Hit(pattern: int, term: str, offset: int, line: int, context: str)`
  - `Density(words: int, hits: tuple[Hit, ...], per_thousand: float, by_pattern: dict[int, int])` with `.as_dict() -> dict`
  - `load_terms(path: Path) -> list[Term]`
  - `scan(text: str, terms: list[Term]) -> Density`
  - `PATTERNS_PATH: Path` — the repo's `references/ai-patterns.md`

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_density.py`:

```python
"""Density is evidence for criterion 4. It is never evidence of authorship."""

import pytest

from scripts.grading.density import PATTERNS_PATH, load_terms, scan


@pytest.fixture(scope="module")
def terms():
    return load_terms(PATTERNS_PATH)


def test_it_reads_terms_from_the_shared_pattern_file(terms):
    texts = {term.text.lower() for term in terms}
    assert "delve" in texts
    assert "tapestry" in texts
    assert "pivotal" in texts
    assert "testament" in texts


def test_it_tags_each_term_with_its_pattern_number(terms):
    delve = [term for term in terms if term.text.lower() == "delve"]
    assert delve and delve[0].pattern == 7


def test_it_strips_a_parenthetical_qualifier(terms):
    texts = {term.text.lower() for term in terms}
    assert "landscape" in texts
    assert "landscape (abstract noun)" not in texts


def test_it_expands_one_slashed_word_into_alternates(terms):
    texts = {term.text.lower() for term in terms}
    assert {"gate", "gated", "gating"} <= texts
    assert {"stands as", "serves as"} <= texts


def test_it_skips_a_template_rather_than_matching_it_literally(terms):
    texts = {term.text.lower() for term in terms}
    assert not any("..." in text for text in texts)
    assert "x is the y of z" not in texts


def test_it_skips_a_long_trailing_clause(terms):
    assert all(len(term.text.split()) <= 6 for term in terms)


def test_the_slop_paper_scores_far_above_the_clean_paper(phil210, terms):
    slop = scan(
        (phil210 / "ungraded/paper_7_ungraded_ai_healthcare_slop.md").read_text(
            encoding="utf-8"
        ),
        terms,
    )
    clean = scan(
        (
            phil210 / "ungraded/paper_6_ungraded_community_solar_interconnection.md"
        ).read_text(encoding="utf-8"),
        terms,
    )
    assert slop.per_thousand > clean.per_thousand * 2


def test_it_reports_where_each_hit_is(terms):
    result = scan("We must delve into the vibrant tapestry.", terms)
    words = {hit.term.lower() for hit in result.hits}
    assert {"delve", "vibrant", "tapestry"} <= words
    for hit in result.hits:
        assert hit.line == 1
        assert hit.offset >= 0


def test_hits_come_back_in_document_order(terms):
    result = scan("The vibrant future. We delve later.", terms)
    offsets = [hit.offset for hit in result.hits]
    assert offsets == sorted(offsets)


def test_it_matches_case_insensitively_on_whole_words(terms):
    assert scan("Delve deeper.", terms).hits
    assert not [hit for hit in scan("Delved deeper.", terms).hits if hit.term == "delve"]


def test_a_multi_word_term_matches_across_a_line_break(terms):
    result = scan("This stands\nas proof.", terms)
    assert any(hit.term.lower() == "stands as" for hit in result.hits)


def test_it_counts_words_and_reports_a_rate(terms):
    result = scan("delve " * 10 + "word " * 990, terms)
    assert result.words == 1000
    assert result.per_thousand == pytest.approx(10.0, abs=0.1)


def test_empty_text_reports_zero_and_does_not_divide_by_zero(terms):
    result = scan("", terms)
    assert result.words == 0
    assert result.per_thousand == 0.0
    assert result.hits == ()


def test_the_breakdown_groups_counts_by_pattern(terms):
    result = scan("A vibrant tapestry we must delve into.", terms)
    assert result.by_pattern[7] >= 2


def test_as_dict_round_trips_through_json(terms):
    import json

    payload = scan("We delve.", terms).as_dict()
    assert json.loads(json.dumps(payload))["per_thousand"] >= 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_density.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.density'`

- [ ] **Step 3: Write the density scanner**

`scripts/grading/density.py`:

```python
"""Count watched AI terms in a text, and say where each one is.

The PHIL 210 rubric names "heavy reliance on AI buzzwords (tapestry, delve,
pivotal, testament)" as an Unacceptable marker under criterion 4. Counting
those is assessing prose against a stated criterion. It is in scope.

Concluding who or what wrote an essay is out of scope, and nothing in this
module does it. A count is not an accusation. The report exists so a score on
criterion 4 has evidence attached, the same way every other criterion needs a
quote.

Terms come from references/ai-patterns.md so there is one list, not two.
Counts are grouped by pattern number rather than summed into a single figure,
because the patterns are not equally meaningful in a student essay. Pattern 7
carries the rubric's own buzzwords. Pattern 21 catches chatbot disclaimers.
Reading them apart is the point.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

PATTERNS_PATH = Path(__file__).resolve().parents[2] / "references" / "ai-patterns.md"

MAX_TERM_WORDS = 6

_HEADING = re.compile(r"^###\s+(\d+)\.")
_WATCH = re.compile(
    r"^\*\*(?:High-frequency AI words|Words to watch|Phrases to watch)"
    r":\*\*\s*(.+)$"
)
_PAREN = re.compile(r"\s*\([^)]*\)")
_BRACKET = re.compile(r"\s*\[[^\]]*\]")
_PLACEHOLDER = re.compile(r"\b[XYZ]\b")
_WORD = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class Term:
    """One watched term and the pattern it belongs to."""

    text: str
    pattern: int


@dataclass(frozen=True)
class Hit:
    """One occurrence of a watched term."""

    pattern: int
    term: str
    offset: int
    line: int
    context: str


@dataclass(frozen=True)
class Density:
    """What a scan found."""

    words: int
    hits: tuple[Hit, ...]
    per_thousand: float
    by_pattern: dict[int, int]

    def as_dict(self) -> dict:
        """Return the JSON shape stored in a grade record."""
        return {
            "words": self.words,
            "count": len(self.hits),
            "per_thousand": self.per_thousand,
            "by_pattern": {str(k): v for k, v in sorted(self.by_pattern.items())},
            "examples": [
                {
                    "pattern": hit.pattern,
                    "term": hit.term,
                    "offset": hit.offset,
                    "line": hit.line,
                    "context": hit.context,
                }
                for hit in self.hits
            ],
        }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _expand(raw: str) -> list[str]:
    """Turn one comma-separated entry into zero or more literal terms.

    Four kinds of entry are dropped rather than guessed at: a template with
    an ellipsis, a template with an X/Y/Z placeholder, an entry carrying two
    or more slashed words (the alternates multiply and the result stops
    being a phrase anyone writes), and a trailing explanatory clause.
    """
    text = _norm(_BRACKET.sub("", _PAREN.sub("", raw)))
    if not text or "..." in text or _PLACEHOLDER.search(text):
        return []
    words = text.split(" ")
    if len(words) > MAX_TERM_WORDS:
        return []
    slashed = [index for index, word in enumerate(words) if "/" in word]
    if not slashed:
        return [text]
    if len(slashed) > 1:
        return []
    position = slashed[0]
    expanded: list[str] = []
    for alternate in words[position].split("/"):
        if not alternate:
            continue
        expanded.append(" ".join(words[:position] + [alternate] + words[position + 1 :]))
    return expanded


def load_terms(path: Path = PATTERNS_PATH) -> list[Term]:
    """Read every watched term from the shared pattern reference."""
    terms: list[Term] = []
    seen: set[str] = set()
    pattern = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = _HEADING.match(line)
        if heading:
            pattern = int(heading.group(1))
            continue
        watch = _WATCH.match(line.strip())
        if not watch or not pattern:
            continue
        for raw in watch.group(1).split(","):
            for text in _expand(raw):
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                terms.append(Term(text=text, pattern=pattern))
    return terms


def _term_pattern(text: str) -> re.Pattern[str]:
    body = r"\s+".join(re.escape(word) for word in text.split(" "))
    prefix = r"\b" if text[:1].isalnum() else ""
    suffix = r"\b" if text[-1:].isalnum() else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


def scan(text: str, terms: list[Term]) -> Density:
    """Count every watched term in text and record where each one sits."""
    hits: list[Hit] = []
    for term in terms:
        for match in _term_pattern(term.text).finditer(text):
            start = max(0, match.start() - 40)
            hits.append(
                Hit(
                    pattern=term.pattern,
                    term=term.text,
                    offset=match.start(),
                    line=text.count("\n", 0, match.start()) + 1,
                    context=_norm(text[start : match.end() + 40]),
                )
            )
    hits.sort(key=lambda hit: (hit.offset, hit.term))

    words = len(_WORD.findall(text))
    by_pattern: dict[int, int] = {}
    for hit in hits:
        by_pattern[hit.pattern] = by_pattern.get(hit.pattern, 0) + 1
    rate = round(len(hits) * 1000 / words, 2) if words else 0.0
    return Density(
        words=words,
        hits=tuple(hits),
        per_thousand=rate,
        by_pattern=by_pattern,
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_density.py -v`
Expected: 15 passed

- [ ] **Step 5: Check the count on a real paper by hand**

Run:

```bash
python3 -c "
from scripts.grading.density import PATTERNS_PATH, load_terms, scan
from pathlib import Path
terms = load_terms(PATTERNS_PATH)
for name in ['paper_6_ungraded_community_solar_interconnection.md',
             'paper_7_ungraded_ai_healthcare_slop.md']:
    text = Path('tests/fixtures/phil210/ungraded') .joinpath(name).read_text(encoding='utf-8')
    result = scan(text, terms)
    print(name, result.words, len(result.hits), result.per_thousand, result.by_pattern)
"
```

Expected: paper 7's rate is at least double paper 6's, and pattern 7 is its largest group. If a term is producing obvious noise, note it — do not tune the list here. The list belongs to `references/ai-patterns.md` and both skills read it.

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scripts/grading/density.py tests/grading/test_density.py
git commit -m "Count AI-pattern density as evidence for criterion 4"
```

---
### Task 4: The grade record and quote location

The engine writes a machine-readable record beside each Markdown draft. Markdown is for the instructor; JSON is what makes the consistency pass and any later comparison possible without reparsing prose.

The record's most important field is the justification quote. A quote a model invented is not evidence, so the script locates every quote in the redacted source and writes the offset itself. The model supplies the words; the script supplies the position. An offset a model guessed proves nothing.

**Files:**
- Create: `scripts/grading/records.py`
- Create: `tests/grading/test_records.py`

**Interfaces:**
- Consumes: nothing from earlier tasks. `verify.py` (Task 7) is the caller.
- Produces:
  - `MIN_QUOTE_WORDS: int` (4)
  - `RecordError(Exception)`
  - `locate(quote: str, source: str, occurrence: int | None = None) -> tuple[int, int, int]` returning `(offset, line, length)`
  - `Justification(quote: str, note: str = "", occurrence: int | None = None, offset: int | None = None, line: int | None = None, length: int | None = None)`
  - `CriterionScore(number: int, name: str, points_possible: int, score: int | None, level: str, assessable: bool, justifications: tuple[Justification, ...])`
  - `GradeRecord(schema, batch, token, source, course, rubric, profile, criteria, total, total_possible, letter, pattern_density, status)` with `.as_dict()`, `.save(path)`, and classmethods `.from_dict(payload)`, `.load(path)`
  - `SCHEMA: str` (`"grade-record/1"`)
  - `CANNOT_ASSESS: str` (`"cannot assess"`)

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_records.py`:

```python
"""A score without a locatable quote is not evidence."""

import json

import pytest

from scripts.grading.records import (
    CANNOT_ASSESS,
    SCHEMA,
    CriterionScore,
    GradeRecord,
    Justification,
    RecordError,
    locate,
)

SOURCE = (
    "The transition to renewable energy is often discussed in macro terms.\n"
    "Interconnection queues are the formal regulatory process for connecting.\n"
    "The transition to renewable energy is often discussed in macro terms.\n"
)


def test_it_finds_a_quote_and_reports_its_position():
    offset, line, length = locate(
        "Interconnection queues are the formal regulatory process", SOURCE, None
    )
    assert SOURCE[offset : offset + length].startswith("Interconnection queues")
    assert line == 2


def test_a_quote_that_is_not_in_the_source_is_refused():
    with pytest.raises(RecordError, match="not in the source"):
        locate("Solar panels are made entirely of hope", SOURCE, None)


def test_a_quote_too_short_to_be_evidence_is_refused():
    with pytest.raises(RecordError, match="too short"):
        locate("is often", SOURCE, None)


def test_an_ambiguous_quote_is_refused_until_it_is_pinned():
    with pytest.raises(RecordError, match="appears 2 times"):
        locate("The transition to renewable energy is often", SOURCE, None)


def test_an_ambiguous_quote_resolves_when_an_occurrence_is_given():
    first = locate("The transition to renewable energy is often", SOURCE, 1)
    second = locate("The transition to renewable energy is often", SOURCE, 2)
    assert first[1] == 1
    assert second[1] == 3


def test_an_occurrence_past_the_end_is_refused():
    with pytest.raises(RecordError, match="occurrence 5"):
        locate("The transition to renewable energy is often", SOURCE, 5)


def test_a_quote_reflowed_across_a_line_break_still_matches():
    source = "The interconnection queue is\na five year wait in most markets.\n"
    offset, line, length = locate("queue is a five year wait", source, None)
    assert offset >= 0
    assert length > len("queue is a five year wait") - 1


def _record() -> GradeRecord:
    return GradeRecord(
        batch="essay-2",
        token="STUDENT_03",
        source="redacted/STUDENT_03.md",
        course="phil210",
        rubric="tests/fixtures/phil210/grading_rubric.md",
        profile=".grading/profiles/phil210.md",
        criteria=(
            CriterionScore(
                number=1,
                name="Thesis & Theoretical Framing",
                points_possible=25,
                score=21,
                level="Proficient",
                assessable=True,
                justifications=(
                    Justification(quote="a sharp defensible thesis here", note="clear"),
                ),
            ),
            CriterionScore(
                number=2,
                name="Evidence & Empirical Grounding",
                points_possible=25,
                score=None,
                level=CANNOT_ASSESS,
                assessable=False,
                justifications=(),
            ),
        ),
        total=21,
        total_possible=25,
        letter="B-",
        pattern_density={"words": 600, "count": 3, "per_thousand": 5.0},
    )


def test_a_record_round_trips_through_json(tmp_path):
    path = tmp_path / "STUDENT_03.json"
    _record().save(path)
    reloaded = GradeRecord.load(path)
    assert reloaded.token == "STUDENT_03"
    assert reloaded.criteria[0].justifications[0].quote.startswith("a sharp")
    assert reloaded.criteria[1].score is None
    assert reloaded.criteria[1].level == CANNOT_ASSESS


def test_the_saved_file_names_its_schema(tmp_path):
    path = tmp_path / "STUDENT_03.json"
    _record().save(path)
    assert json.loads(path.read_text(encoding="utf-8"))["schema"] == SCHEMA


def test_a_record_from_an_unknown_schema_is_refused(tmp_path):
    path = tmp_path / "old.json"
    path.write_text(json.dumps({"schema": "grade-record/0"}), encoding="utf-8")
    with pytest.raises(RecordError, match="schema"):
        GradeRecord.load(path)


def test_a_record_missing_a_required_field_is_refused(tmp_path):
    payload = _record().as_dict()
    del payload["total"]
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(RecordError, match="total"):
        GradeRecord.load(path)


def test_a_record_that_is_not_json_is_refused(tmp_path):
    path = tmp_path / "junk.json"
    path.write_text("not json at all", encoding="utf-8")
    with pytest.raises(RecordError, match="not valid JSON"):
        GradeRecord.load(path)


def test_every_record_starts_as_a_draft():
    assert _record().status == "draft"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_records.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.records'`

- [ ] **Step 3: Write the record module**

`scripts/grading/records.py`:

```python
"""The grade record: what the engine writes beside each Markdown draft.

Markdown is for the instructor. JSON is what makes the batch consistency
pass, and any later comparison, possible without reparsing prose.

The record's load-bearing field is the justification quote. `locate` is the
reason it can be trusted: the model supplies the words, and this module
supplies the position by finding those words in the redacted source. A quote
that is not there fails. A quote too short to identify a passage fails. A
quote that appears twice fails until the model pins which one it means.

Nothing here loads the token map. Verification works against the redacted
source, which is the file the model actually read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "grade-record/1"
CANNOT_ASSESS = "cannot assess"
MIN_QUOTE_WORDS = 4

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")

_REQUIRED = (
    "schema",
    "batch",
    "token",
    "source",
    "course",
    "rubric",
    "profile",
    "criteria",
    "total",
    "total_possible",
    "letter",
    "pattern_density",
    "status",
)


class RecordError(Exception):
    """The record cannot be read, or a quote in it cannot be trusted."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def locate(
    quote: str, source: str, occurrence: int | None = None
) -> tuple[int, int, int]:
    """Find a quote in the source and return (offset, line, length).

    Whitespace is flexible, so a quote the model reflowed across a line
    break still matches the essay it came from. Everything else is strict.
    """
    needle = _norm(quote)
    words = needle.split(" ")
    if len(words) < MIN_QUOTE_WORDS:
        raise RecordError(
            f"Quote is too short to be evidence. Use at least "
            f"{MIN_QUOTE_WORDS} words: {quote!r}"
        )
    # Anchor only where the boundary character is alphanumeric, so a quote
    # ending in "." still matches while "one cat" no longer matches inside
    # "one category".
    body = r"\s+".join(re.escape(word) for word in words)
    prefix = r"\b" if words[0][:1].isalnum() else ""
    suffix = r"\b" if words[-1][-1:].isalnum() else ""
    pattern = re.compile(prefix + body + suffix)
    # A quote may span a line WRAP -- a model reflows text and it is the same
    # passage. It may never span a blank line: the tail of one paragraph
    # stitched to the head of the next is two passages, and certifying it as
    # one would let a fabricated quote pass as evidence.
    matches = [
        match
        for match in pattern.finditer(source)
        if not _PARAGRAPH_BREAK.search(match.group(0))
    ]
    if not matches:
        raise RecordError(f"Quote is not in the source: {quote!r}")
    if len(matches) > 1 and occurrence is None:
        raise RecordError(
            f"Quote appears {len(matches)} times. Lengthen it, or set "
            f'"occurrence": {quote!r}'
        )
    if occurrence is not None and occurrence < 1:
        raise RecordError(
            f"Occurrence must be 1 or greater, not {occurrence}: {quote!r}"
        )
    index = (occurrence or 1) - 1
    if index >= len(matches):
        raise RecordError(
            f"Quote appears {len(matches)} times, so occurrence "
            f"{occurrence} does not exist: {quote!r}"
        )
    match = matches[index]
    line = source.count("\n", 0, match.start()) + 1
    return match.start(), line, match.end() - match.start()


@dataclass
class Justification:
    """One quote that supports one score.

    `offset`, `line`, and `length` are written by verification, not by the
    model. `occurrence` is the one positional field the model may set, and
    only when the same words appear more than once.
    """

    quote: str
    note: str = ""
    occurrence: int | None = None
    offset: int | None = None
    line: int | None = None
    length: int | None = None

    def as_dict(self) -> dict:
        payload: dict = {"quote": self.quote, "note": self.note}
        for name in ("occurrence", "offset", "line", "length"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "Justification":
        if "quote" not in payload:
            raise RecordError("Every justification needs a quote.")
        return cls(
            quote=str(payload["quote"]),
            note=str(payload.get("note", "")),
            occurrence=payload.get("occurrence"),
            offset=payload.get("offset"),
            line=payload.get("line"),
            length=payload.get("length"),
        )


@dataclass
class CriterionScore:
    """One criterion's score, or an honest refusal to give one."""

    number: int
    name: str
    points_possible: int
    score: int | None
    level: str
    assessable: bool
    justifications: tuple[Justification, ...] = ()

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "points_possible": self.points_possible,
            "score": self.score,
            "level": self.level,
            "assessable": self.assessable,
            "justifications": [j.as_dict() for j in self.justifications],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CriterionScore":
        for name in ("number", "name", "points_possible", "level", "assessable"):
            if name not in payload:
                raise RecordError(f"A criterion is missing {name!r}.")
        return cls(
            number=int(payload["number"]),
            name=str(payload["name"]),
            points_possible=int(payload["points_possible"]),
            score=payload.get("score"),
            level=str(payload["level"]),
            assessable=bool(payload["assessable"]),
            justifications=tuple(
                Justification.from_dict(item)
                for item in payload.get("justifications", [])
            ),
        )


@dataclass
class GradeRecord:
    """One submission's scores, evidence, and pattern-density report."""

    batch: str
    token: str
    source: str
    course: str
    rubric: str
    profile: str
    criteria: tuple[CriterionScore, ...]
    total: int
    total_possible: int
    letter: str
    pattern_density: dict = field(default_factory=dict)
    status: str = "draft"
    schema: str = SCHEMA

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "batch": self.batch,
            "token": self.token,
            "source": self.source,
            "course": self.course,
            "rubric": self.rubric,
            "profile": self.profile,
            "criteria": [c.as_dict() for c in self.criteria],
            "total": self.total,
            "total_possible": self.total_possible,
            "letter": self.letter,
            "pattern_density": self.pattern_density,
            "status": self.status,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, payload: dict) -> "GradeRecord":
        if payload.get("schema") != SCHEMA:
            raise RecordError(
                f"Unknown record schema {payload.get('schema')!r}. "
                f"This grader writes {SCHEMA!r}."
            )
        missing = [name for name in _REQUIRED if name not in payload]
        if missing:
            raise RecordError(f"The record is missing: {', '.join(missing)}")
        return cls(
            schema=str(payload["schema"]),
            batch=str(payload["batch"]),
            token=str(payload["token"]),
            source=str(payload["source"]),
            course=str(payload["course"]),
            rubric=str(payload["rubric"]),
            profile=str(payload["profile"]),
            criteria=tuple(
                CriterionScore.from_dict(item) for item in payload["criteria"]
            ),
            total=int(payload["total"]),
            total_possible=int(payload["total_possible"]),
            letter=str(payload["letter"]),
            pattern_density=dict(payload["pattern_density"]),
            status=str(payload["status"]),
        )

    @classmethod
    def load(cls, path: Path) -> "GradeRecord":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RecordError(f"{path.name} is not valid JSON: {error}") from error
        return cls.from_dict(payload)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_records.py -v`
Expected: 15 passed

- [ ] **Step 5: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 6: Commit**

```bash
git add scripts/grading/records.py tests/grading/test_records.py
git commit -m "Add the grade record and locate every justification quote in its source"
```

---

### Task 5: The calibration profile

The profile is what makes feedback sound like a specific person. It is plain Markdown so the instructor can edit it by hand, and it carries a fingerprint of the rubric it was built against so a later run can name which criteria drifted.

The profile is also the one calibration artifact that a cloud run reads. So it must hold no student name and no student essay text.

**Files:**
- Create: `scripts/grading/profile.py`
- Create: `references/calibration.md`
- Create: `tests/fixtures/phil210/profile_phil210.md`
- Create: `tests/grading/test_profile.py`
- Modify: `tests/conftest.py`

**Interfaces:**
- Consumes: `Rubric`, `load_rubric`, `RubricError` from Task 1; `load_roster` from Plan 1 (tests only).
- Produces:
  - `REQUIRED_SECTIONS: tuple[str, ...]`
  - `Profile(path, course, calibrated, rubric_path, samples, holdout, fingerprint: dict[int, str], criterion_names: dict[int, str], never_use: tuple[str, ...], sections: dict[str, str])` with `.drift(rubric: Rubric) -> list[str]`
  - `load_profile(path: Path) -> Profile`
  - `profile_path(course: str, root: Path = Path(".grading")) -> Path`
  - `ProfileError(Exception)`
- Adds the `profile` pytest fixture to `tests/conftest.py`.

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_profile.py`:

```python
"""The profile is cloud-visible. It must carry the instructor's voice and nobody's name."""

import re

import pytest

from scripts.grading.profile import ProfileError, load_profile, profile_path
from scripts.grading.rubric import load_rubric
from scripts.redaction.rehydrate import TOKEN
from scripts.redaction.roster import load_roster


def test_it_reads_the_profile_metadata(profile):
    loaded = load_profile(profile)
    assert loaded.course == "phil210"
    assert loaded.calibrated == "2026-08-24"
    assert loaded.samples == 4


def test_it_reads_every_required_section(profile):
    loaded = load_profile(profile)
    assert "Weighting in practice" in loaded.sections
    assert "Comment bank" in loaded.sections
    assert loaded.sections["Voice"].strip()


def test_it_reads_the_never_use_list(profile):
    loaded = load_profile(profile)
    assert "Great job!" in loaded.never_use
    assert len(loaded.never_use) >= 3


def test_a_matching_rubric_shows_no_drift(profile, phil210):
    loaded = load_profile(profile)
    assert loaded.drift(load_rubric(phil210 / "grading_rubric.md")) == []


def test_an_edited_criterion_is_named_as_drifted(profile, phil210, tmp_path):
    text = (phil210 / "grading_rubric.md").read_text(encoding="utf-8")
    edited = tmp_path / "edited.md"
    edited.write_text(
        text.replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )
    drifted = load_profile(profile).drift(load_rubric(edited))
    assert drifted == ["1. Thesis & Theoretical Framing"]


def test_a_removed_criterion_is_named_as_drifted(profile, phil210, tmp_path):
    lines = (phil210 / "grading_rubric.md").read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.startswith("| **3.")]
    edited = tmp_path / "short.md"
    edited.write_text("\n".join(kept), encoding="utf-8")
    drifted = load_profile(profile).drift(load_rubric(edited))
    assert any("3." in name for name in drifted)


def test_the_profile_holds_no_roster_name(profile, phil210):
    # Whole words only. A plain substring check flags "Lin" inside the rubric's
    # own section name, "The Bottom Line", which is not a leak.
    text = profile.read_text(encoding="utf-8")
    for entry in load_roster(phil210 / "roster.csv"):
        for part in entry.name.split():
            assert not re.search(rf"\b{re.escape(part)}\b", text), (
                f"{part} must not appear in a profile"
            )


def test_the_profile_holds_no_redaction_token(profile):
    assert TOKEN.search(profile.read_text(encoding="utf-8")) is None


def test_the_profile_holds_no_student_essay_text(profile, phil210):
    text = profile.read_text(encoding="utf-8")
    essay = (
        phil210 / "graded/paper_1_graded_algorithmic_gatekeeping.md"
    ).read_text(encoding="utf-8")
    sentence = "In modern digital ecosystems, however, this forum is no longer"
    assert sentence in essay
    assert sentence not in text


def test_a_missing_profile_is_refused(tmp_path):
    with pytest.raises(ProfileError, match="No profile"):
        load_profile(tmp_path / "absent.md")


def test_a_profile_missing_a_required_section_is_refused(profile, tmp_path):
    text = profile.read_text(encoding="utf-8")
    broken = tmp_path / "broken.md"
    broken.write_text(text.replace("## Comment bank", "## Notes"), encoding="utf-8")
    with pytest.raises(ProfileError, match="Comment bank"):
        load_profile(broken)


def test_a_profile_without_a_fingerprint_is_refused(profile, tmp_path):
    lines = profile.read_text(encoding="utf-8").splitlines()
    kept = [line for line in lines if not line.startswith("| 1.") and not line.startswith("| 2.")
            and not line.startswith("| 3.") and not line.startswith("| 4.")]
    broken = tmp_path / "nofingerprint.md"
    broken.write_text("\n".join(kept), encoding="utf-8")
    with pytest.raises(ProfileError, match="fingerprint"):
        load_profile(broken)


def test_the_default_path_is_under_the_grading_directory(tmp_path):
    assert profile_path("eng101", tmp_path).name == "eng101.md"
    assert profile_path("eng101", tmp_path).parent.name == "profiles"
```

- [ ] **Step 2: Add the `profile` fixture**

Append to `tests/conftest.py`:

```python
@pytest.fixture
def profile() -> Path:
    """Return the hand-written reference profile for PHIL 210."""
    return FIXTURES / "profile_phil210.md"
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_profile.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.profile'`

- [ ] **Step 4: Write the profile module**

`scripts/grading/profile.py`:

```python
"""Read a calibration profile, and say when the rubric under it moved.

The profile is plain Markdown on purpose. The instructor owns it, edits it,
and re-runs calibration against it over time, so it must stay readable
without a tool.

It is also the one calibration artifact a cloud run reads. Everything it
holds is something a cloud model sees, which is why the comment bank stores
the instructor's sentences with names replaced by [STUDENT] and stores no
student essay text at all.

The fingerprint table is what makes drift reportable by name. Storing one
digest for the whole rubric would only say that something changed. Storing
one per criterion says which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.grading.rubric import Rubric, RubricError

FINGERPRINT_SECTION = "Rubric fingerprint"

REQUIRED_SECTIONS = (
    "Rubric fingerprint",
    "Rubric, normalized",
    "Weighting in practice",
    "Severity",
    "Feedback structure",
    "Voice",
    "Comment bank",
    "Never-use list",
)

_META = re.compile(r"^\*\*(.+?)\*\*\s*:\s*(.+?)\s*$")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_ROW = re.compile(r"^\|\s*(\d+)\.\s*(.+?)\s*\|\s*([0-9a-f]{6,})\s*\|\s*$")
_BULLET = re.compile(r"^[-*]\s+(.+?)\s*$")


class ProfileError(Exception):
    """The profile is missing, incomplete, or unreadable."""


def profile_path(course: str, root: Path = Path(".grading")) -> Path:
    """Return where a course's profile lives."""
    return root / "profiles" / f"{course}.md"


@dataclass
class Profile:
    """One instructor's calibrated standards for one course."""

    path: Path
    course: str = ""
    calibrated: str = ""
    rubric_path: str = ""
    samples: int = 0
    holdout: str = ""
    fingerprint: dict[int, str] = field(default_factory=dict)
    criterion_names: dict[int, str] = field(default_factory=dict)
    never_use: tuple[str, ...] = ()
    sections: dict[str, str] = field(default_factory=dict)

    def drift(self, rubric: Rubric) -> list[str]:
        """Return the criteria that changed since calibration, by name.

        A criterion whose wording changed, one that was added, and one that
        was removed all count. Each is reported as "3. Counterargument" so
        the instructor can see exactly what moved.
        """
        drifted: list[str] = []
        current = {c.number: c for c in rubric.criteria}
        for number in sorted(set(current) | set(self.fingerprint)):
            criterion = current.get(number)
            recorded = self.fingerprint.get(number)
            if criterion is None:
                drifted.append(f"{number}. {self.criterion_names.get(number, '')}".strip())
            elif recorded is None or recorded != criterion.digest:
                drifted.append(f"{number}. {criterion.name}")
        return drifted


def load_profile(path: Path) -> Profile:
    """Read a profile and refuse anything incomplete."""
    if not path.is_file():
        raise ProfileError(
            f"No profile at {path}. Run calibration for this course before grading."
        )
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()

    meta: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        if not current:
            found = _META.match(line.strip())
            if found:
                meta[found.group(1).strip()] = found.group(2).strip()
            continue
        sections[current].append(line)

    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise ProfileError(
            f"{path.name} is missing these sections: {', '.join(missing)}"
        )

    fingerprint: dict[int, str] = {}
    names: dict[int, str] = {}
    for line in sections[FINGERPRINT_SECTION]:
        row = _ROW.match(line.strip())
        if row:
            number = int(row.group(1))
            fingerprint[number] = row.group(3)
            names[number] = row.group(2)
    if not fingerprint:
        raise ProfileError(
            f"{path.name} has an empty rubric fingerprint. Each row must read "
            "'| 1. Criterion name | digest |'."
        )

    never_use = tuple(
        match.group(1)
        for match in (_BULLET.match(line.strip()) for line in sections["Never-use list"])
        if match
    )

    course = meta.get("Course", "")
    if not course:
        raise ProfileError(f"{path.name} has no '**Course**:' line.")

    return Profile(
        path=path,
        course=course,
        calibrated=meta.get("Calibrated", ""),
        rubric_path=meta.get("Rubric", ""),
        samples=int(meta.get("Samples", "0") or 0),
        holdout=meta.get("Holdout", ""),
        fingerprint=fingerprint,
        criterion_names=names,
        never_use=never_use,
        sections={name: "\n".join(body).strip() for name, body in sections.items()},
    )
```

- [ ] **Step 5: Write the reference profile fixture**

The fingerprint digests must match the fixture rubric exactly, so generate the file rather than typing hashes by hand. Run this from the repo root:

```bash
python3 - <<'PY'
from pathlib import Path

from scripts.grading.rubric import load_rubric

rubric = load_rubric(Path("tests/fixtures/phil210/grading_rubric.md"))
rows = "\n".join(f"| {c.number}. {c.name} | {c.digest} |" for c in rubric.criteria)

body = """# Grading profile: PHIL/POLISCI 210

**Course**: phil210
**Calibrated**: 2026-08-24
**Rubric**: tests/fixtures/phil210/grading_rubric.md
**Samples**: 4
**Holdout**: paper_5

Synthetic reference profile, built from the four calibration samples in this
corpus. Every comment below is the instructor's own. No student name and no
student essay text appears anywhere in this file.

## Rubric fingerprint

| Criterion | Digest |
| :--- | :--- |
__ROWS__

## Rubric, normalized

Four criteria at 25 points each, 100 total. Four levels: Exemplary,
Proficient, Developing, Unacceptable. The letter comes from the rubric's own
scale, which runs from 93 and up for an A down to under 60 for an F.

## Weighting in practice

The rubric weights all four criteria equally. The samples do not.

- A weak or purely descriptive thesis costs the most. It pulls criterion 1
  down and drags criteria 2 and 3 with it, because there is nothing for the
  evidence to support.
- Missing citations cost heavily and specifically. Naming no statute, case,
  or dataset is the single most reliable predictor of a C or below.
- Prose problems alone do not sink an essay. A well-argued paper with filler
  phrases loses two or three points on criterion 4 and keeps its A.
- Bulleted lists in place of continuous prose are treated as a structural
  failure, not a formatting preference.

## Severity

Scores in the samples run 65 to 94, a 29-point spread across four papers.

- A begins at 93 and requires a named theorist used as an analytical tool,
  not as decoration.
- The B range is the working middle. It means the argument holds and the
  evidence is real, with one clear gap.
- A C means the essay drifted into opinion.
- A D is reserved for work that does not meet the level of the course. It is
  given, not softened.

## Feedback structure

Taken from the rubric, which states it.

1. Inline bracketed annotations, pinned to specific sentences. Four to six
   per essay in the samples, spread across the paper rather than clustered.
2. The Bottom Line: two to three sentences, opening with the student's name,
   saying plainly whether the argument holds.
3. What Worked: one or two bullets, each naming a specific paragraph.
4. What to Work On: two or three bullets, each an action for the next draft.
5. A closing line, then the sign-off.

Feedback runs 180 to 320 words. Praise comes first and is specific. Critique
is direct and never hedged.

## Voice

- Second person throughout. "You did not just repeat standard talking
  points." Never "the author" or "this paper's writer".
- Opens the Bottom Line with the student's first name and a comma.
- Short declarative sentences. Semicolons are rare. Questions are rare.
- Names the exact offending phrase in quotation marks or italics, then says
  what to do instead.
- Uses imperatives for revisions: "Cut the fluff." "Tighten this sentence."
  "Trust your argument and state the claim directly."
- Cites paragraph numbers rather than page numbers.
- Uses italics for the words being criticised, bold for section labels.
- Will name a concrete source the student should have read, including
  statutes and court cases, rather than saying "add citations".

## Comment bank

The instructor's own sentences, verbatim, with names replaced.

- "[STUDENT], this is an exceptionally well-argued, tightly structured essay."
- "You did not just repeat standard talking points."
- "Good theoretical anchor to open with."
- "Strong synthesis. You clearly understand how technical incentives warp
  editorial outcomes."
- "Sharp distinction between cultural discovery and political discourse."
- "Tighten this sentence: drop 'It is important to emphasize that' and just
  state your claim."
- "Watch out for throat-clearing academic filler phrases. Trust your argument
  and state the claim directly."
- "Keep up the rigorous work."
- "[STUDENT], this submission is unacceptable for a 200-level university
  course."
- "There is virtually no critical analysis, no historical context, and zero
  primary or secondary citations."
- "Classic AI buzzword salad. Cut the fluff."
- "This reads like an investor pitch deck. Where is the discussion of
  algorithmic management, wage volatility, or misclassification lawsuits?"
- "Why is this formatted as a bulleted list with bold subheaders? This is a
  formal analytical essay, not a PowerPoint slide."
- "Zero substance in this conclusion."
- "Get your hands dirty with real cases."
- "Academic writing requires evidence, not high-altitude platitudes."
- "Come see me in office hours before the next paper deadline."

## Never-use list

Phrases this instructor does not say. A draft containing one fails
verification.

- Great job!
- Overall, this is a solid effort
- I hope this helps
- Keep up the good work
- You might want to consider possibly
- As an AI
- It is important to note that
- delve
- tapestry
"""

Path("tests/fixtures/phil210/profile_phil210.md").write_text(
    body.replace("__ROWS__", rows), encoding="utf-8"
)
print("wrote tests/fixtures/phil210/profile_phil210.md")
PY
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_profile.py -v`
Expected: 13 passed

The name test is the one to watch. It fails if any roster first or last name survived into the fixture. Fix the fixture, not the test.

- [ ] **Step 7: Write the calibration reference**

`references/calibration.md`:

```markdown
# How calibration works

Calibration learns two things a rubric cannot record: where this instructor
actually lands against their own scale, and how they write.

It runs locally. It must refuse when the instructor answers "cloud" at the
gate, because calibration reads graded work that still carries names.

## What it needs

- The rubric.
- Three to eight pieces of work the instructor already graded, each holding
  the work, their comments, and their score.
- The class roster, so past students' names can be scrubbed out.

Fewer than three samples cannot show a pattern. More than eight adds little
and costs a long read.

## What it produces

The profile at `.grading/profiles/<course>.md`, and a holdout report.

**The report is the deliverable, not the profile.** A bad profile must
surface in the first minute rather than on the fortieth essay.

## The seven sections

| Section | Holds |
| --- | --- |
| Rubric fingerprint | One digest per criterion, so drift can be named |
| Rubric, normalized | Criteria, levels, weights, point values |
| Weighting in practice | The gap between the rubric and the instructor's behaviour |
| Severity | Where the instructor lands against the rubric's language; score spread |
| Feedback structure | Opening, praise-to-critique ratio, closing, typical length |
| Voice | Sentence length, person, questions or directives, habitual hedges |
| Comment bank | The instructor's own sentences, verbatim |
| Never-use list | Phrases the instructor does not say |

The weighting section carries most of the value. A rubric may weight citation
errors and thesis errors equally while the samples show the instructor
forgives citations and punishes a weak thesis. No rubric records that, and it
is most of what makes feedback sound like a specific person.

## Two rules about what the profile may hold

**No student essay text.** The voice lives in the comments. The essays are
not needed to learn it, and the profile is read during cloud runs.

**No student name.** Instructors write "Maya, this is exceptional work". Run
every comment through name scrubbing first, so the bank reads "[STUDENT],
this is exceptional work". Use the literal `[STUDENT]`, never a
`STUDENT_01`-shaped token: a token leaking out of a profile would be
reattached at rehydration to whichever student holds it in the current batch.

## Take structure from the rubric

Where the rubric states its feedback protocol, use it. The PHIL 210 rubric
names inline annotations, a Bottom Line, What Worked, What to Work On, and a
sign-off. Read that instead of inferring a structure the instructor already
wrote down. Infer only what the rubric leaves unsaid.

## The interview

Samples show habits. They do not show range. Ask only what the samples cannot
answer:

1. Every sample is B-range. Where does an A begin, and what does one do that
   a B does not?
2. Which criterion do you forgive, and which do you never forgive?
3. When work is failing, do you say so plainly or soften it?
4. Is there anything you would never write to a student?
5. How long should feedback be?

Skip any question the samples already answer. An interview that re-asks what
is visible in the work wastes the instructor's time and teaches nothing.

## Holdout validation

Hold one graded sample back. Grade it blind against the finished profile.
Report the delta:

- The instructor's score against the generated score.
- The letter band, matched or not.
- Which of the instructor's own sentences the generated feedback reused.
- The length of the generated feedback against the instructor's.

`python3 scripts/calibrate.py holdout` writes that report.

## Recalibration

Profiles are plain Markdown, dated, and editable by hand. Add graded work
over time and run calibration again. When the rubric changes, `grade.py
profile` names the drifted criteria and asks before grading.
```

- [ ] **Step 8: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 9: Commit**

```bash
git add scripts/grading/profile.py references/calibration.md tests/fixtures/phil210/profile_phil210.md tests/grading/test_profile.py tests/conftest.py
git commit -m "Add the calibration profile, its drift check, and the interview protocol"
```

---

### Task 6: Holdout validation and the calibrate CLI

Calibration's deliverable is the holdout report. It grades one held-back sample blind against the finished profile and shows the instructor how far off it was, in the first minute rather than on the fortieth essay.

The samples carry a whole-paper grade, not per-criterion scores, so the delta compares totals, letters, voice reuse, and length. It must not invent per-criterion instructor scores that the instructor never wrote.

**Files:**
- Create: `scripts/grading/holdout.py`
- Create: `scripts/calibrate.py`
- Create: `tests/grading/test_holdout.py`

**Interfaces:**
- Consumes: `GradedSample`, `load_sample`, `scrub_names` (Task 2); `GradeRecord` (Task 4); `Rubric`, `load_rubric` (Task 1); `load_roster` from Plan 1.
- Produces:
  - `Delta(token, generated_total, instructor_total, points, generated_letter, instructor_letter, letter_match, shared_phrases, generated_words, instructor_words, length_ratio, findings)`
  - `compare(record: GradeRecord, feedback: str, sample: GradedSample) -> Delta`
  - `render(delta: Delta) -> str`
  - `PHRASE_WORDS: int` (5)
  - CLI `scripts/calibrate.py` with subcommands `inputs` and `holdout`

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_holdout.py`:

```python
"""A bad profile must surface in the first minute, not on the fortieth essay."""

import json
import subprocess
import sys

from scripts.grading.holdout import compare, render
from scripts.grading.records import CriterionScore, GradeRecord
from scripts.grading.samples import load_sample

PAPER_5 = "graded/paper_5_graded_subscription_software_paradox.md"


def _record(total: int, letter: str) -> GradeRecord:
    return GradeRecord(
        batch="holdout",
        token="STUDENT_01",
        source="redacted/STUDENT_01.md",
        course="phil210",
        rubric="tests/fixtures/phil210/grading_rubric.md",
        profile="tests/fixtures/phil210/profile_phil210.md",
        criteria=(
            CriterionScore(1, "Thesis", 25, total // 4, "Proficient", True, ()),
        ),
        total=total,
        total_possible=100,
        letter=letter,
        pattern_density={},
    )


def test_it_reports_the_score_gap(phil210):
    sample = load_sample(phil210 / PAPER_5)
    delta = compare(_record(86, "B"), "Solid work.", sample)
    assert delta.instructor_total == 83
    assert delta.generated_total == 86
    assert delta.points == 3


def test_a_matching_letter_is_reported_as_a_match(phil210):
    sample = load_sample(phil210 / PAPER_5)
    assert compare(_record(83, "B"), "Solid.", sample).letter_match is True


def test_a_different_letter_is_reported_as_a_miss(phil210):
    sample = load_sample(phil210 / PAPER_5)
    delta = compare(_record(94, "A"), "Solid.", sample)
    assert delta.letter_match is False
    assert any("letter" in finding.lower() for finding in delta.findings)


def test_a_large_score_gap_becomes_a_finding(phil210):
    sample = load_sample(phil210 / PAPER_5)
    delta = compare(_record(96, "A"), "Solid.", sample)
    assert any("points" in finding.lower() for finding in delta.findings)


def test_a_small_score_gap_raises_no_finding_about_points(phil210):
    sample = load_sample(phil210 / PAPER_5)
    delta = compare(_record(85, "B"), "Solid.", sample)
    assert not any("points above" in finding for finding in delta.findings)


def test_it_reports_the_instructor_phrases_the_draft_reused(phil210):
    sample = load_sample(phil210 / PAPER_5)
    borrowed = sample.bottom_line
    delta = compare(_record(83, "B"), borrowed, sample)
    assert delta.shared_phrases


def test_feedback_that_shares_nothing_reports_no_phrases(phil210):
    sample = load_sample(phil210 / PAPER_5)
    delta = compare(_record(83, "B"), "Zebra quantum marmalade orbit.", sample)
    assert delta.shared_phrases == ()
    assert any("no phrasing" in finding.lower() for finding in delta.findings)


def test_feedback_far_longer_than_the_instructors_becomes_a_finding(phil210):
    sample = load_sample(phil210 / PAPER_5)
    delta = compare(_record(83, "B"), "word " * 2000, sample)
    assert delta.length_ratio > 3
    assert any("length" in finding.lower() for finding in delta.findings)


def test_the_report_names_both_scores(phil210):
    sample = load_sample(phil210 / PAPER_5)
    report = render(compare(_record(86, "B"), "Solid.", sample))
    assert "83" in report and "86" in report


def test_calibrate_inputs_writes_comments_without_essay_text(phil210, tmp_path):
    out = tmp_path / "inputs.json"
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(phil210 / "graded"),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "paper_5_graded_subscription_software_paradox.md",
            "--out", str(out),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    blob = json.dumps(payload)
    assert "Jürgen Habermas envisioned an idealized forum" not in blob
    assert "Maya" not in blob
    assert "Lin" not in blob
    assert "[STUDENT]" in blob
    assert len(payload["samples"]) == 4
    assert payload["holdout"]["path"].endswith(
        "paper_5_graded_subscription_software_paradox.md"
    )


def test_calibrate_inputs_carries_the_rubric_fingerprint(phil210, tmp_path):
    out = tmp_path / "inputs.json"
    subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(phil210 / "graded"),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "paper_5_graded_subscription_software_paradox.md",
            "--out", str(out),
        ],
        capture_output=True, text=True, check=True,
    )
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["rubric"]["criteria"]) == 4
    assert all(item["digest"] for item in payload["rubric"]["criteria"])


def test_calibrate_inputs_refuses_fewer_than_three_samples(phil210, tmp_path):
    thin = tmp_path / "thin"
    thin.mkdir()
    for name in [
        "paper_1_graded_algorithmic_gatekeeping.md",
        "paper_5_graded_subscription_software_paradox.md",
    ]:
        (thin / name).write_text(
            (phil210 / "graded" / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(thin),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "paper_5_graded_subscription_software_paradox.md",
            "--out", str(tmp_path / "out.json"),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 13
    assert "three" in result.stderr.lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_holdout.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.holdout'`

- [ ] **Step 3: Write the holdout comparison**

`scripts/grading/holdout.py`:

```python
"""Compare a generated grade against the one the instructor actually gave.

This is calibration's deliverable. The profile is only the means.

The samples carry one grade per paper, not one per criterion, so the delta
compares what the instructor actually wrote: the total, the letter band, how
much of their own phrasing came through, and how long the feedback ran.
Inventing per-criterion instructor scores to compare against would be making
up the instructor's judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.grading.records import GradeRecord
from scripts.grading.samples import GradedSample

PHRASE_WORDS = 5
POINT_TOLERANCE = 5
LENGTH_TOLERANCE = 2.0

_WORD = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class Delta:
    """How far the generated grade sits from the instructor's."""

    token: str
    generated_total: int
    instructor_total: int
    points: int
    generated_letter: str
    instructor_letter: str
    letter_match: bool
    shared_phrases: tuple[str, ...]
    generated_words: int
    instructor_words: int
    length_ratio: float
    findings: tuple[str, ...]


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def _phrases(words: list[str]) -> set[str]:
    return {
        " ".join(words[index : index + PHRASE_WORDS])
        for index in range(len(words) - PHRASE_WORDS + 1)
    }


def compare(record: GradeRecord, feedback: str, sample: GradedSample) -> Delta:
    """Compare one generated grade and its feedback against a graded sample."""
    generated_words = _words(feedback)
    instructor_words = _words(sample.comments_only())
    shared = sorted(_phrases(generated_words) & _phrases(instructor_words))

    points = record.total - sample.score
    letter_match = record.letter.strip() == sample.letter.strip()
    ratio = (
        round(len(generated_words) / len(instructor_words), 2)
        if instructor_words
        else 0.0
    )

    findings: list[str] = []
    if abs(points) > POINT_TOLERANCE:
        direction = "above" if points > 0 else "below"
        findings.append(
            f"The generated total is {abs(points)} points {direction} the "
            f"instructor's. Check the severity section of the profile."
        )
    if not letter_match:
        findings.append(
            f"The letter band differs: generated {record.letter!r}, "
            f"instructor {sample.letter!r}."
        )
    if not shared:
        findings.append(
            "The feedback reuses no phrasing from the instructor's own "
            "comments. Check the comment bank and the voice section."
        )
    if ratio and (ratio > LENGTH_TOLERANCE or ratio < 1 / LENGTH_TOLERANCE):
        findings.append(
            f"The feedback length is {ratio}x the instructor's. Check the "
            "feedback structure section."
        )

    return Delta(
        token=record.token,
        generated_total=record.total,
        instructor_total=sample.score,
        points=points,
        generated_letter=record.letter,
        instructor_letter=sample.letter,
        letter_match=letter_match,
        shared_phrases=tuple(shared),
        generated_words=len(generated_words),
        instructor_words=len(instructor_words),
        length_ratio=ratio,
        findings=tuple(findings),
    )


def render(delta: Delta) -> str:
    """Render the delta as the report the instructor reads."""
    lines = [
        "# Holdout report",
        "",
        f"Held-back sample graded blind as `{delta.token}`.",
        "",
        "| Measure | Instructor | Generated |",
        "| --- | --- | --- |",
        f"| Total | {delta.instructor_total} | {delta.generated_total} |",
        f"| Letter | {delta.instructor_letter} | {delta.generated_letter} |",
        f"| Feedback words | {delta.instructor_words} | {delta.generated_words} |",
        "",
        f"Score gap: {delta.points:+d} points. "
        f"Letter: {'match' if delta.letter_match else 'differs'}.",
        "",
        "## Phrasing carried over",
        "",
    ]
    if delta.shared_phrases:
        lines += [f"- {phrase}" for phrase in delta.shared_phrases[:10]]
        if len(delta.shared_phrases) > 10:
            lines.append(f"- ... and {len(delta.shared_phrases) - 10} more")
    else:
        lines.append("None. The voice did not carry.")
    lines += ["", "## What to look at", ""]
    if delta.findings:
        lines += [f"- {finding}" for finding in delta.findings]
    else:
        lines.append("Nothing. The profile reproduced this sample closely.")
    lines += [
        "",
        "This report judges the profile, not the student. A close match means "
        "the profile is worth grading with. A wide one means edit the profile "
        "and run calibration again.",
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Write the calibrate CLI**

`scripts/calibrate.py`:

```python
#!/usr/bin/env python3
"""Build calibration inputs and check a profile against a held-back sample.

Runs locally only. Calibration reads graded work that still carries names, so
it must not run when the instructor answers "cloud" at the gate.

Usage:
    python3 scripts/calibrate.py inputs --rubric RUBRIC --samples DIR \\
        --roster ROSTER --holdout NAME --out inputs.json

    python3 scripts/calibrate.py holdout --record R.json --draft R.md \\
        --sample SAMPLE.md

`inputs` is the safety step. It parses the rubric and every graded sample,
throws away the student essay text, scrubs roster names out of the
instructor's comments, and writes one JSON packet. Building a profile from
that packet is what keeps student prose and student names out of it.

Exit codes: 0 clean, 13 malformed input.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.holdout import compare, render  # noqa: E402
from scripts.grading.records import GradeRecord, RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402
from scripts.grading.samples import SampleError, load_sample, scrub_names  # noqa: E402
from scripts.redaction.roster import load_roster  # noqa: E402

EXIT_OK = 0
EXIT_MALFORMED = 13

MIN_SAMPLES = 3
MAX_SAMPLES = 8


def _inputs(args: argparse.Namespace) -> int:
    rubric = load_rubric(args.rubric)
    roster = load_roster(args.roster) if args.roster else []
    if not roster:
        print(
            "No roster given. Past students' names cannot be scrubbed from "
            "the comment bank. Supply --roster.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED

    paths = sorted(p for p in args.samples.rglob("*.md") if p.is_file())
    holdout_path = next((p for p in paths if p.name == args.holdout), None)
    if holdout_path is None:
        print(f"No sample named {args.holdout} under {args.samples}.", file=sys.stderr)
        return EXIT_MALFORMED
    calibration = [p for p in paths if p != holdout_path]

    if len(calibration) < MIN_SAMPLES:
        print(
            f"Calibration needs at least three graded samples besides the "
            f"holdout. Found {len(calibration)}.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    if len(calibration) > MAX_SAMPLES:
        print(
            f"Using the first {MAX_SAMPLES} of {len(calibration)} samples. "
            "More adds little and costs a long read."
        )
        calibration = calibration[:MAX_SAMPLES]

    def packet(path: Path) -> dict:
        sample = load_sample(path)
        comments, found = scrub_names(sample.comments_only(), roster)
        annotations, _ = scrub_names(
            "\n".join(a.text for a in sample.annotations), roster
        )
        return {
            "path": str(path),
            "score": sample.score,
            "possible": sample.possible,
            "letter": sample.letter,
            "annotation_count": len(sample.annotations),
            "annotations": annotations.splitlines(),
            "comments": comments,
            # A COUNT, never the names. `found` holds the literal names just
            # replaced -- writing them here would put them back into the one
            # artifact whose purpose is to be name-free.
            "scrubbed_count": len(set(found)),
        }

    payload = {
        "rubric": {
            "path": str(args.rubric),
            "title": rubric.title,
            "total_points": rubric.total_points,
            "criteria": [
                {
                    "number": c.number,
                    "name": c.name,
                    "points": c.points,
                    "digest": c.digest,
                    "levels": [
                        {"name": l.name, "band": l.band, "descriptor": l.descriptor}
                        for l in c.levels
                    ],
                }
                for c in rubric.criteria
            ],
            "bands": [
                {"low": b.low, "high": b.high, "label": b.label} for b in rubric.bands
            ],
            "protocol": {
                "sections": list(rubric.protocol.sections),
                "sign_offs": list(rubric.protocol.sign_offs),
                "requires_inline_annotations": (
                    rubric.protocol.requires_inline_annotations
                ),
            },
        },
        "samples": [packet(path) for path in calibration],
        "holdout": {"path": str(holdout_path)},
        "note": (
            "This packet holds the instructor's comments only. No student "
            "essay text and no student name is in it. Build the profile from "
            "this, never from the sample files directly."
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    scrubbed = sorted({name for item in payload["samples"] for name in item["scrubbed"]})
    print(
        f"Wrote {args.out}. {len(payload['samples'])} calibration samples, "
        f"holdout {holdout_path.name}."
    )
    print(f"Scrubbed {len(scrubbed)} name forms from the comments.")
    print("Read references/calibration.md before building the profile.")
    return EXIT_OK


def _holdout(args: argparse.Namespace) -> int:
    record = GradeRecord.load(args.record)
    feedback = args.draft.read_text(encoding="utf-8")
    sample = load_sample(args.sample)
    delta = compare(record, feedback, sample)
    if args.json:
        print(json.dumps(asdict(delta), indent=2, ensure_ascii=False))
    else:
        print(render(delta))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inputs = subparsers.add_parser("inputs", help="Build the calibration packet")
    inputs.add_argument("--rubric", type=Path, required=True)
    inputs.add_argument("--samples", type=Path, required=True)
    inputs.add_argument("--roster", type=Path, required=True)
    inputs.add_argument("--holdout", required=True, help="Filename to hold back")
    inputs.add_argument("--out", type=Path, default=Path("calibration-inputs.json"))
    inputs.set_defaults(handler=_inputs)

    holdout = subparsers.add_parser("holdout", help="Report the holdout delta")
    holdout.add_argument("--record", type=Path, required=True)
    holdout.add_argument("--draft", type=Path, required=True)
    holdout.add_argument("--sample", type=Path, required=True)
    holdout.add_argument("--json", action="store_true")
    holdout.set_defaults(handler=_holdout)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (RubricError, SampleError, RecordError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_holdout.py -v`
Expected: 13 passed

- [ ] **Step 6: Build the real calibration packet once by hand**

Run:

```bash
python3 scripts/calibrate.py inputs \
  --rubric tests/fixtures/phil210/grading_rubric.md \
  --samples tests/fixtures/phil210/graded \
  --roster tests/fixtures/phil210/roster.csv \
  --holdout paper_5_graded_subscription_software_paradox.md \
  --out /tmp/calibration-inputs.json
grep -c STUDENT /tmp/calibration-inputs.json
grep -ci "habermas envisioned" /tmp/calibration-inputs.json || true
```

Expected: the command reports 4 samples, the `[STUDENT]` count is above zero, and the essay-sentence grep finds nothing.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add scripts/grading/holdout.py scripts/calibrate.py tests/grading/test_holdout.py
git commit -m "Add holdout validation and the calibrate CLI"
```

---
### Task 7: Verify a draft before anyone sees it

This is the task the grading engine exists to pass, the way the leak test is the one redaction exists to pass. Every claim a draft makes gets checked against something that is not a model: quotes against the redacted source, arithmetic against itself, the letter against the rubric's scale, the structure against the rubric's protocol, the phrasing against the profile's never-use list, and the density figures against a fresh count.

The verifier is also where offsets come from. The model writes quotes; this writes positions.

**Files:**
- Create: `scripts/grading/verify.py`
- Create: `scripts/grade.py`
- Create: `tests/grading/test_verify.py`

**Interfaces:**
- Consumes: `Rubric` (Task 1), `Term`/`load_terms`/`scan` (Task 3), `GradeRecord`/`CriterionScore`/`Justification`/`locate`/`RecordError`/`CANNOT_ASSESS` (Task 4), `Profile`/`load_profile`/`profile_path`/`ProfileError` (Task 5), `TOKEN` from `scripts.redaction.rehydrate` (Plan 1).
- Produces:
  - `Finding(level: str, code: str, message: str, token: str = "", criterion: int | None = None)`
  - `verify_draft(record, markdown, source, rubric, profile, terms) -> list[Finding]` — writes `offset`, `line`, and `length` onto each justification it can locate
  - `errors(findings) -> list[Finding]`
  - `EXIT_NO_PROFILE = 10`, `EXIT_DRIFT = 11`, `EXIT_FAILED = 12`, `EXIT_MALFORMED = 13` in `scripts/grade.py`
  - CLI `scripts/grade.py` with subcommands `profile` and `verify`. Tasks 8 and 9 add `status`, `density`, and `consistency`.

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_verify.py`:

```python
"""No draft reaches an instructor until every claim in it has been checked."""

import json
import subprocess
import sys

import pytest

from scripts.grading.density import PATTERNS_PATH, load_terms
from scripts.grading.profile import load_profile
from scripts.grading.records import (
    CANNOT_ASSESS,
    CriterionScore,
    GradeRecord,
    Justification,
)
from scripts.grading.rubric import load_rubric
from scripts.grading.verify import errors, verify_draft

SOURCE = """# Essay 2

Community solar cooperatives face an interconnection bottleneck that state
regulators have only begun to address. This paper argues that the bottleneck
is an economic conflict of interest, not a bureaucratic accident.

Under a rate-of-return model, utility profits are tied to capital investment
in the utility's own infrastructure, so distributed generation cuts the
justification for that spending.

New York's VDER framework established standardized hosting capacity maps.
"""

FEEDBACK = """[The framing here is sharp. Say it in the first sentence next time.]

**The Bottom Line**

Your central claim holds. You named the mechanism instead of gesturing at it.

**What Worked**

* Paragraph 2 ties the rate-of-return model to the interconnection queue.

**What to Work On**

* Give the VDER claim a citation.

— Dr. Vance
"""


@pytest.fixture(scope="module")
def terms():
    return load_terms(PATTERNS_PATH)


@pytest.fixture
def rubric(phil210):
    return load_rubric(phil210 / "grading_rubric.md")


@pytest.fixture
def loaded_profile(profile):
    return load_profile(profile)


def _record(**overrides) -> GradeRecord:
    criteria = [
        CriterionScore(
            number=1,
            name="Thesis & Theoretical Framing",
            points_possible=25,
            score=21,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="the bottleneck is an economic conflict of interest"),
            ),
        ),
        CriterionScore(
            number=2,
            name="Evidence & Empirical Grounding",
            points_possible=25,
            score=20,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="New York's VDER framework established standardized"),
            ),
        ),
        CriterionScore(
            number=3,
            name="Counterargument & Critical Analysis",
            points_possible=25,
            score=19,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="utility profits are tied to capital investment"),
            ),
        ),
        CriterionScore(
            number=4,
            name="Structure, Prose & Academic Voice",
            points_possible=25,
            score=22,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="Community solar cooperatives face an interconnection"),
            ),
        ),
    ]
    from scripts.grading.density import scan

    density = scan(SOURCE, load_terms(PATTERNS_PATH)).as_dict()
    values = {
        "batch": "essay-2",
        "token": "STUDENT_01",
        "source": "redacted/STUDENT_01.md",
        "course": "phil210",
        "rubric": "tests/fixtures/phil210/grading_rubric.md",
        "profile": "tests/fixtures/phil210/profile_phil210.md",
        "criteria": tuple(criteria),
        "total": 82,
        "total_possible": 100,
        # 82 of 100 lands in the rubric's 80-82 band, which is "B-", not "B".
        # The verifier recomputes this, so a wrong letter here fails the test.
        "letter": "B-",
        "pattern_density": density,
    }
    values.update(overrides)
    return GradeRecord(**values)


def test_a_clean_draft_produces_no_errors(rubric, loaded_profile, terms):
    findings = verify_draft(_record(), FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert errors(findings) == []


def test_verification_writes_the_offsets_the_model_did_not(rubric, loaded_profile, terms):
    record = _record()
    verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    justification = record.criteria[0].justifications[0]
    assert justification.offset is not None
    # The quote spans a line break in the source, so the located text is
    # "the bottleneck\nis an economic ...". That is the whole point of
    # matching whitespace flexibly.
    assert SOURCE[justification.offset :].startswith("the bottleneck")
    assert justification.length > len("the bottleneck is an economic conflict")
    assert justification.line == 4


def test_a_fabricated_quote_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].justifications[0].quote = "the author cites forty seven studies"
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "quote" for finding in errors(findings))


def test_a_scored_criterion_with_no_quote_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].justifications = ()
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "no-quote" for finding in errors(findings))


def test_a_cannot_assess_criterion_needs_no_quote(rubric, loaded_profile, terms):
    # Dropping criterion 2 leaves 21 + 19 + 22 = 62 of 75 possible, which is
    # 82.7 percent and lands in the "B" band.
    record = _record(total=62, total_possible=75, letter="B")
    criteria = list(record.criteria)
    criteria[1] = CriterionScore(
        number=2,
        name="Evidence & Empirical Grounding",
        points_possible=25,
        score=None,
        level=CANNOT_ASSESS,
        assessable=False,
        justifications=(),
    )
    record.criteria = tuple(criteria)
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert errors(findings) == []
    assert any(finding.code == "partial" for finding in findings)


def test_a_cannot_assess_criterion_carrying_a_score_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[1].assessable = False
    record.criteria[1].level = CANNOT_ASSESS
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "unassessable-score" for finding in errors(findings))


def test_a_total_that_does_not_add_up_fails(rubric, loaded_profile, terms):
    findings = verify_draft(_record(total=95), FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "total" for finding in errors(findings))


def test_a_score_above_the_criterion_maximum_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].score = 40
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "score-range" for finding in errors(findings))


def test_a_letter_that_does_not_match_the_scale_fails(rubric, loaded_profile, terms):
    findings = verify_draft(_record(letter="A"), FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "letter" for finding in errors(findings))


def test_a_missing_criterion_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria = record.criteria[:3]
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "criteria-mismatch" for finding in errors(findings))


def test_invented_density_figures_fail(rubric, loaded_profile, terms):
    record = _record()
    record.pattern_density = {"words": 10, "count": 99, "per_thousand": 9900.0,
                             "by_pattern": {"7": 99}, "examples": []}
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "density-mismatch" for finding in errors(findings))


def test_a_missing_feedback_section_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("**What Worked**", "**Nice Things**")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "section-missing" for finding in errors(findings))


def test_a_missing_sign_off_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("— Dr. Vance", "Regards,")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "sign-off" for finding in errors(findings))


def test_missing_inline_annotations_fail(rubric, loaded_profile, terms):
    broken = "\n".join(FEEDBACK.splitlines()[2:])
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "no-annotations" for finding in errors(findings))


def test_a_never_use_phrase_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("Your central claim holds.", "Great job! Your claim holds.")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "never-use" for finding in errors(findings))


def test_a_token_the_source_never_held_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("Your central claim", "STUDENT_09, your central claim")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "stray-token" for finding in errors(findings))


def test_watched_terms_in_the_feedback_are_a_warning_not_an_error(
    rubric, loaded_profile, terms
):
    wordy = FEEDBACK.replace(
        "Your central claim holds.",
        "Your central claim showcases a pivotal interplay.",
    )
    findings = verify_draft(_record(), wordy, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "watched-term" for finding in findings)
    assert not any(finding.code == "watched-term" for finding in errors(findings))


def _write_batch(tmp_path, phil210, feedback=FEEDBACK):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    (redacted / "STUDENT_01.md").write_text(SOURCE, encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text(feedback, encoding="utf-8")
    record = _record(source=str(redacted / "STUDENT_01.md"))
    record.save(drafts / "STUDENT_01.json")
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return redacted, drafts, root


def _run(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def test_the_cli_passes_a_clean_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    result = _run(
        "verify", "--batch", "essay-2", "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_cli_writes_offsets_back_into_the_record(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    _run(
        "verify", "--batch", "essay-2", "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root),
    )
    payload = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    assert payload["criteria"][0]["justifications"][0]["offset"] >= 0


def test_the_cli_fails_a_batch_with_a_bad_draft(tmp_path, phil210):
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, feedback=FEEDBACK.replace("— Dr. Vance", "Regards,")
    )
    result = _run(
        "verify", "--batch", "essay-2", "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root),
    )
    assert result.returncode == 12
    assert "sign-off" in result.stdout + result.stderr


def test_the_cli_refuses_to_grade_without_a_profile(tmp_path, phil210):
    result = _run(
        "profile", "--course", "eng999",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--root", str(tmp_path),
    )
    assert result.returncode == 10
    assert "calibrate" in (result.stdout + result.stderr).lower()


def test_the_cli_names_drifted_criteria(tmp_path, phil210):
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    edited = tmp_path / "edited.md"
    edited.write_text(
        (phil210 / "grading_rubric.md")
        .read_text(encoding="utf-8")
        .replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )
    result = _run("profile", "--course", "phil210", "--rubric", str(edited),
                  "--root", str(root))
    assert result.returncode == 11
    assert "Thesis" in result.stdout + result.stderr


def test_the_cli_accepts_a_matching_rubric(tmp_path, phil210):
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = _run("profile", "--course", "phil210",
                  "--rubric", str(phil210 / "grading_rubric.md"), "--root", str(root))
    assert result.returncode == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.verify'`

- [ ] **Step 3: Write the verifier**

`scripts/grading/verify.py`:

```python
"""Check every claim a draft makes against something that is not a model.

A calibrated model's grade is not defensible on its own. What makes it
defensible is that each score carries a quote, and that a script confirms the
quote is really in the essay, that the arithmetic adds up, that the letter
comes from the instructor's own scale, that the structure matches the
protocol the rubric states, and that the density figures match a fresh count.

This module also writes the offsets. The model supplies the words of a quote;
`locate` supplies the position. An offset a model guessed is not evidence.

Nothing here loads the token map. Tokens in a draft are checked against the
redacted source, which is the file the model actually read: a token that is
in the draft but not in the source was invented, and rehydration would
otherwise attach a real name to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.grading.density import Term, scan
from scripts.grading.profile import Profile
from scripts.grading.records import CANNOT_ASSESS, GradeRecord, RecordError, locate
from scripts.grading.rubric import Rubric, RubricError
from scripts.redaction.rehydrate import TOKEN

ERROR = "error"
WARNING = "warning"

_ANNOTATION = re.compile(r"\[[^\[\]]{10,}\](?!\()")


@dataclass(frozen=True)
class Finding:
    """One thing wrong with a draft, or one thing worth a look."""

    level: str
    code: str
    message: str
    token: str = ""
    criterion: int | None = None


def errors(findings: list[Finding]) -> list[Finding]:
    """Return only the findings that must block the draft."""
    return [finding for finding in findings if finding.level == ERROR]


def _check_criteria(
    record: GradeRecord, rubric: Rubric, source: str, found: list[Finding]
) -> None:
    expected = [c.number for c in rubric.criteria]
    actual = [c.number for c in record.criteria]
    if actual != expected:
        found.append(
            Finding(
                ERROR,
                "criteria-mismatch",
                f"The record scores criteria {actual}, the rubric has {expected}.",
                record.token,
            )
        )
        return

    for scored in record.criteria:
        criterion = rubric.criterion(scored.number)
        if scored.name != criterion.name:
            found.append(
                Finding(
                    WARNING,
                    "criterion-renamed",
                    f"Criterion {scored.number} is named {scored.name!r} in the "
                    f"record and {criterion.name!r} in the rubric.",
                    record.token,
                    scored.number,
                )
            )
        if scored.points_possible != criterion.points:
            found.append(
                Finding(
                    ERROR,
                    "points-possible",
                    f"Criterion {scored.number} is worth {criterion.points} points, "
                    f"not {scored.points_possible}.",
                    record.token,
                    scored.number,
                )
            )
        if not scored.assessable:
            if scored.score is not None:
                found.append(
                    Finding(
                        ERROR,
                        "unassessable-score",
                        f"Criterion {scored.number} is marked unassessable but "
                        f"carries a score of {scored.score}. Never infer a score.",
                        record.token,
                        scored.number,
                    )
                )
            if scored.level != CANNOT_ASSESS:
                found.append(
                    Finding(
                        ERROR,
                        "unassessable-level",
                        f"Criterion {scored.number} is unassessable, so its level "
                        f"must read {CANNOT_ASSESS!r}.",
                        record.token,
                        scored.number,
                    )
                )
            continue

        if scored.score is None:
            found.append(
                Finding(
                    ERROR,
                    "missing-score",
                    f"Criterion {scored.number} is assessable but has no score.",
                    record.token,
                    scored.number,
                )
            )
        elif not 0 <= scored.score <= scored.points_possible:
            found.append(
                Finding(
                    ERROR,
                    "score-range",
                    f"Criterion {scored.number} scored {scored.score} out of "
                    f"{scored.points_possible}.",
                    record.token,
                    scored.number,
                )
            )
        if not scored.justifications:
            found.append(
                Finding(
                    ERROR,
                    "no-quote",
                    f"Criterion {scored.number} has a score and no supporting "
                    "quote. Give no score, and flag it for the instructor.",
                    record.token,
                    scored.number,
                )
            )
        for justification in scored.justifications:
            try:
                offset, line, length = locate(
                    justification.quote, source, justification.occurrence
                )
            except RecordError as error:
                found.append(
                    Finding(ERROR, "quote", str(error), record.token, scored.number)
                )
                continue
            justification.offset = offset
            justification.line = line
            justification.length = length


def _check_totals(
    record: GradeRecord, rubric: Rubric, found: list[Finding]
) -> None:
    scored = [c for c in record.criteria if c.assessable and c.score is not None]
    total = sum(c.score for c in scored)
    possible = sum(c.points_possible for c in scored)

    if record.total != total:
        found.append(
            Finding(
                ERROR,
                "total",
                f"The total is {record.total}; the scored criteria add to {total}.",
                record.token,
            )
        )
    if record.total_possible != possible:
        found.append(
            Finding(
                ERROR,
                "total-possible",
                f"The possible total is {record.total_possible}; the scored "
                f"criteria are worth {possible}.",
                record.token,
            )
        )
    if possible == 0:
        found.append(
            Finding(
                ERROR,
                "nothing-assessed",
                "No criterion could be assessed. There is no grade to give.",
                record.token,
            )
        )
        return
    if possible < rubric.total_points:
        found.append(
            Finding(
                WARNING,
                "partial",
                f"This grade covers {possible} of {rubric.total_points} points. "
                "Tell the instructor which criteria were not assessed.",
                record.token,
            )
        )
    # A score above its criterion maximum puts the percentage outside every
    # band. That is a finding, not a crash: this is the gate that reports
    # malformed records, so it must never raise on one.
    percent = 100 * total / possible
    try:
        expected = rubric.letter_for(percent)
    except RubricError as error:
        found.append(
            Finding(
                ERROR,
                "letter",
                f"{total}/{possible} is {percent:.0f} percent, which the "
                f"rubric's scale does not cover. {error}",
                record.token,
            )
        )
        return
    if record.letter.strip() != expected:
        found.append(
            Finding(
                ERROR,
                "letter",
                f"The rubric's scale gives {expected!r} for {total}/{possible}, "
                f"not {record.letter!r}. The letter is computed, not chosen.",
                record.token,
            )
        )


def _check_density(
    record: GradeRecord, source: str, terms: list[Term], found: list[Finding]
) -> None:
    fresh = scan(source, terms).as_dict()
    reported = record.pattern_density or {}
    if reported.get("count") != fresh["count"] or reported.get(
        "by_pattern"
    ) != fresh["by_pattern"]:
        found.append(
            Finding(
                ERROR,
                "density-mismatch",
                "The pattern-density figures do not match a fresh count of the "
                f"source ({fresh['count']} hits, {fresh['by_pattern']}). Run "
                "`grade.py density` and paste its output.",
                record.token,
            )
        )


def _check_feedback(
    record: GradeRecord,
    markdown: str,
    source: str,
    rubric: Rubric,
    profile: Profile,
    terms: list[Term],
    found: list[Finding],
) -> None:
    lowered = markdown.lower()
    for section in rubric.protocol.sections:
        if section.lower() not in lowered:
            found.append(
                Finding(
                    ERROR,
                    "section-missing",
                    f"The feedback has no {section!r} section. The rubric's "
                    "protocol requires it.",
                    record.token,
                )
            )
    if rubric.protocol.sign_offs:
        tail = [line for line in markdown.strip().splitlines() if line.strip()]
        last = tail[-1].strip() if tail else ""
        if last not in rubric.protocol.sign_offs:
            found.append(
                Finding(
                    ERROR,
                    "sign-off",
                    f"The feedback must end with one of "
                    f"{list(rubric.protocol.sign_offs)}. It ends with {last!r}.",
                    record.token,
                )
            )
    if rubric.protocol.requires_inline_annotations and not _ANNOTATION.search(markdown):
        found.append(
            Finding(
                ERROR,
                "no-annotations",
                "The rubric asks for inline bracketed annotations and the "
                "feedback has none.",
                record.token,
            )
        )

    for phrase in profile.never_use:
        if phrase.lower() in lowered:
            found.append(
                Finding(
                    ERROR,
                    "never-use",
                    f"The feedback says {phrase!r}, which is on the profile's "
                    "never-use list.",
                    record.token,
                )
            )

    for match in TOKEN.finditer(markdown):
        if match.group(0) not in source:
            found.append(
                Finding(
                    ERROR,
                    "stray-token",
                    f"The feedback uses {match.group(0)}, which is not in the "
                    "redacted source. Rehydration would attach a real name to it.",
                    record.token,
                )
            )

    watched = scan(markdown, terms)
    if watched.hits:
        listed = ", ".join(sorted({hit.term for hit in watched.hits})[:6])
        found.append(
            Finding(
                WARNING,
                "watched-term",
                f"The feedback uses {len(watched.hits)} watched AI terms "
                f"({listed}). Run it through references/ai-patterns.md again.",
                record.token,
            )
        )

    if record.status != "draft":
        found.append(
            Finding(
                ERROR,
                "status",
                f"Every generated grade is a draft until the instructor signs "
                f"off. Status reads {record.status!r}.",
                record.token,
            )
        )


def verify_draft(
    record: GradeRecord,
    markdown: str,
    source: str,
    rubric: Rubric,
    profile: Profile,
    terms: list[Term],
) -> list[Finding]:
    """Check one draft and fill in the offsets it is missing."""
    found: list[Finding] = []
    _check_criteria(record, rubric, source, found)
    _check_totals(record, rubric, found)
    _check_density(record, source, terms, found)
    _check_feedback(record, markdown, source, rubric, profile, terms, found)
    return found
```

- [ ] **Step 4: Write the grade CLI with its first two subcommands**

`scripts/grade.py`:

```python
#!/usr/bin/env python3
"""Local checks that stand behind the grading skill.

The model does the judgement. This does the checking.

Subcommands:
    profile      Confirm a course has a profile and that its rubric has not drifted.
    verify       Check every draft in a batch. Write the quote offsets.

Exit codes: 0 clean, 10 no profile, 11 the rubric drifted since calibration,
12 a draft failed verification, 13 malformed input.

Nothing here loads the token map. Quotes and tokens are checked against the
redacted source, which is the file the model actually read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.density import PATTERNS_PATH, load_terms  # noqa: E402
from scripts.grading.profile import (  # noqa: E402
    ProfileError,
    load_profile,
    profile_path,
)
from scripts.grading.records import GradeRecord, RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402
from scripts.grading.verify import ERROR, Finding, errors, verify_draft  # noqa: E402

EXIT_OK = 0
EXIT_NO_PROFILE = 10
EXIT_DRIFT = 11
EXIT_FAILED = 12
EXIT_MALFORMED = 13


def _drafts_dir(args: argparse.Namespace) -> Path:
    return args.drafts or (args.root / "drafts" / args.batch)


def _profile_or_exit(args: argparse.Namespace):
    path = profile_path(args.course, args.root)
    try:
        return load_profile(path)
    except ProfileError as error:
        print(str(error), file=sys.stderr)
        print(
            f"Run `/grade calibrate --course {args.course}` first. Grading "
            "without a profile would produce feedback in nobody's voice.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_NO_PROFILE) from error


def _cmd_profile(args: argparse.Namespace) -> int:
    profile = _profile_or_exit(args)
    rubric = load_rubric(args.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        print(f"The rubric changed since {profile.calibrated}. Drifted criteria:")
        for name in drifted:
            print(f"  {name}")
        print(
            "\nGrading against a drifted criterion applies standards that were "
            "calibrated for different words. Recalibrate, or confirm you want "
            "to proceed anyway.",
            file=sys.stderr,
        )
        return EXIT_DRIFT
    print(
        f"Profile for {profile.course} is current, calibrated "
        f"{profile.calibrated} from {profile.samples} samples."
    )
    return EXIT_OK


def _print(finding: Finding) -> None:
    mark = "ERROR  " if finding.level == ERROR else "warning"
    where = f" [{finding.token}]" if finding.token else ""
    print(f"  {mark} {finding.code}{where}: {finding.message}")


def _cmd_verify(args: argparse.Namespace) -> int:
    profile = _profile_or_exit(args)
    rubric = load_rubric(args.rubric)
    terms = load_terms(args.patterns)
    drafts = _drafts_dir(args)

    records = sorted(drafts.glob("*.json"))
    if not records:
        print(f"No records under {drafts}. Nothing to verify.", file=sys.stderr)
        return EXIT_MALFORMED

    failed = 0
    for record_path in records:
        record = GradeRecord.load(record_path)
        markdown_path = record_path.with_suffix(".md")
        if not markdown_path.is_file():
            print(f"{record_path.name}: no draft at {markdown_path.name}")
            failed += 1
            continue
        source_path = Path(record.source)
        if not source_path.is_file():
            source_path = args.redacted / f"{record.token}.md"
        if not source_path.is_file():
            print(f"{record_path.name}: no redacted source at {record.source}")
            failed += 1
            continue

        findings = verify_draft(
            record,
            markdown_path.read_text(encoding="utf-8"),
            source_path.read_text(encoding="utf-8"),
            rubric,
            profile,
            terms,
        )
        bad = errors(findings)
        print(f"{record.token}: {len(bad)} errors, {len(findings) - len(bad)} warnings")
        for finding in findings:
            _print(finding)
        if bad:
            failed += 1
        elif not args.no_write:
            record.save(record_path)

    if failed:
        print(
            f"\n{failed} of {len(records)} drafts failed. Fix them before "
            "rehydrating. A failed draft must not reach a student.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"\nAll {len(records)} drafts verified. Offsets written.")
    return EXIT_OK


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path(".grading"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("profile", help="Check the profile and rubric drift")
    check.add_argument("--course", required=True)
    check.add_argument("--rubric", type=Path, required=True)
    _add_common(check)
    check.set_defaults(handler=_cmd_profile)

    verify = subparsers.add_parser("verify", help="Check every draft in a batch")
    verify.add_argument("--batch", required=True)
    verify.add_argument("--course", required=True)
    verify.add_argument("--rubric", type=Path, required=True)
    verify.add_argument("--redacted", type=Path, default=Path("redacted"))
    verify.add_argument("--drafts", type=Path, default=None)
    verify.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    verify.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write located offsets back into the records",
    )
    _add_common(verify)
    verify.set_defaults(handler=_cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)
    except (RubricError, RecordError, ProfileError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_verify.py -v`
Expected: 23 passed

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scripts/grading/verify.py scripts/grade.py tests/grading/test_verify.py
git commit -m "Verify every draft against source, rubric, and profile"
```

---

### Task 8: Batch survey, resume, and the density subcommand

A batch that stops halfway must resume, not restart. Regrading a submission that already has a draft wastes the run and can produce a second, different grade for the same student.

**Files:**
- Create: `scripts/grading/batch.py`
- Create: `tests/grading/test_batch.py`
- Modify: `scripts/grade.py`

**Interfaces:**
- Consumes: nothing from earlier tasks except `density` for the new subcommand.
- Produces:
  - `PENDING`, `DRAFTED`, `INCOMPLETE`, `ORPHAN` state constants
  - `Item(token: str, source: Path | None, draft: Path | None, record: Path | None, state: str)`
  - `survey(redacted: Path, drafts: Path) -> list[Item]`
  - `summary(items: list[Item]) -> dict[str, int]`
  - `grade.py status` and `grade.py density` subcommands

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_batch.py`:

```python
"""An interrupted batch resumes. It never regrades work that already has a draft."""

import subprocess
import sys

from scripts.grading.batch import DRAFTED, INCOMPLETE, ORPHAN, PENDING, summary, survey


def _batch(tmp_path):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    return redacted, drafts


def test_a_file_with_no_draft_is_pending(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    items = survey(redacted, drafts)
    assert [(item.token, item.state) for item in items] == [("STUDENT_01", PENDING)]


def test_a_file_with_both_a_draft_and_a_record_is_done(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_01.json").write_text("{}", encoding="utf-8")
    assert survey(redacted, drafts)[0].state == DRAFTED


def test_a_draft_without_its_record_is_incomplete(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    assert survey(redacted, drafts)[0].state == INCOMPLETE


def test_a_record_without_its_draft_is_incomplete(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.json").write_text("{}", encoding="utf-8")
    assert survey(redacted, drafts)[0].state == INCOMPLETE


def test_a_draft_with_no_redacted_source_is_an_orphan(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (drafts / "STUDENT_09.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_09.json").write_text("{}", encoding="utf-8")
    items = survey(redacted, drafts)
    assert items[0].state == ORPHAN
    assert items[0].source is None


def test_an_unknown_token_prefix_is_still_surveyed(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "UNKNOWN_01.md").write_text("essay", encoding="utf-8")
    assert survey(redacted, drafts)[0].token == "UNKNOWN_01"


def test_the_review_file_is_never_treated_as_a_submission(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "_review.json").write_text("{}", encoding="utf-8")
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    assert [item.token for item in survey(redacted, drafts)] == ["STUDENT_01"]


def test_items_come_back_in_token_order(tmp_path):
    redacted, drafts = _batch(tmp_path)
    for token in ["STUDENT_03", "STUDENT_01", "STUDENT_02"]:
        (redacted / f"{token}.md").write_text("essay", encoding="utf-8")
    assert [item.token for item in survey(redacted, drafts)] == [
        "STUDENT_01",
        "STUDENT_02",
        "STUDENT_03",
    ]


def test_the_summary_counts_each_state(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (redacted / "STUDENT_02.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")
    counts = summary(survey(redacted, drafts))
    assert counts[PENDING] == 1
    assert counts[DRAFTED] == 1


def test_a_missing_drafts_directory_reports_everything_as_pending(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    assert survey(redacted, tmp_path / "absent")[0].state == PENDING


def _run(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def test_the_status_subcommand_lists_what_is_left(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (redacted / "STUDENT_02.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")
    result = _run(
        "status", "--batch", "essay-2",
        "--redacted", str(redacted), "--drafts", str(drafts),
    )
    assert result.returncode == 0
    assert "STUDENT_01" in result.stdout
    assert "pending" in result.stdout


def test_the_density_subcommand_reports_a_count(phil210):
    result = _run(
        "density",
        str(phil210 / "ungraded/paper_7_ungraded_ai_healthcare_slop.md"),
    )
    assert result.returncode == 0
    assert "per 1000" in result.stdout


def test_the_density_subcommand_can_emit_json(phil210):
    import json

    result = _run(
        "density",
        str(phil210 / "ungraded/paper_7_ungraded_ai_healthcare_slop.md"),
        "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["count"] > 0
    assert "by_pattern" in payload
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_batch.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.batch'`

- [ ] **Step 3: Write the batch survey**

`scripts/grading/batch.py`:

```python
"""Say what a batch still owes, so an interrupted run resumes instead of restarting.

Regrading a submission that already has a draft is not merely wasted work. It
produces a second, different grade for the same student, and nothing in the
output says which one the instructor should send.

A draft with no redacted source is called an orphan and reported. It usually
means a prior run's output was left in the drafts directory, and grading on
top of it would mix two batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PENDING = "pending"
DRAFTED = "drafted"
INCOMPLETE = "incomplete"
ORPHAN = "orphan"


@dataclass(frozen=True)
class Item:
    """One submission and how far the batch has got with it."""

    token: str
    source: Path | None
    draft: Path | None
    record: Path | None
    state: str


def _tokens(directory: Path, suffixes: tuple[str, ...]) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        path.stem: path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and path.suffix in suffixes
        and not path.name.startswith("_")
    }


def survey(redacted: Path, drafts: Path) -> list[Item]:
    """Return one item per submission and per orphaned draft, in token order."""
    sources = _tokens(redacted, (".md", ".txt", ".docx"))
    written = _tokens(drafts, (".md",))
    records = _tokens(drafts, (".json",))

    items: list[Item] = []
    for token in sorted(set(sources) | set(written) | set(records)):
        source = sources.get(token)
        draft = written.get(token)
        record = records.get(token)
        if source is None:
            state = ORPHAN
        elif draft is not None and record is not None:
            state = DRAFTED
        elif draft is None and record is None:
            state = PENDING
        else:
            state = INCOMPLETE
        items.append(
            Item(token=token, source=source, draft=draft, record=record, state=state)
        )
    return items


def summary(items: list[Item]) -> dict[str, int]:
    """Count how many submissions sit in each state."""
    counts = {PENDING: 0, DRAFTED: 0, INCOMPLETE: 0, ORPHAN: 0}
    for item in items:
        counts[item.state] += 1
    return counts
```

- [ ] **Step 4: Add the status and density subcommands**

In `scripts/grade.py`, add these imports beside the existing ones:

```python
from scripts.grading.batch import (  # noqa: E402
    DRAFTED,
    INCOMPLETE,
    ORPHAN,
    PENDING,
    summary,
    survey,
)
from scripts.grading.density import scan  # noqa: E402
```

Add these two handlers above `_add_common`:

```python
def _cmd_status(args: argparse.Namespace) -> int:
    items = survey(args.redacted, _drafts_dir(args))
    if not items:
        print(f"Nothing under {args.redacted}. Run redact.py first.")
        return EXIT_OK
    width = max(len(item.token) for item in items)
    for item in items:
        print(f"  {item.token.ljust(width)}  {item.state}")
    counts = summary(items)
    print(
        f"\n{counts[PENDING]} pending, {counts[DRAFTED]} drafted, "
        f"{counts[INCOMPLETE]} incomplete, {counts[ORPHAN]} orphaned."
    )
    if counts[ORPHAN]:
        print(
            "An orphaned draft has no redacted source. It is usually a prior "
            "run's output. Move it out before grading.",
            file=sys.stderr,
        )
    if counts[INCOMPLETE]:
        print(
            "An incomplete submission has a draft or a record but not both. "
            "Regrade it.",
            file=sys.stderr,
        )
    return EXIT_OK


def _cmd_density(args: argparse.Namespace) -> int:
    terms = load_terms(args.patterns)
    result = scan(args.path.read_text(encoding="utf-8"), terms)
    if args.json:
        import json

        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK
    print(f"{args.path.name}: {result.words} words, {len(result.hits)} watched terms")
    print(f"  {result.per_thousand} per 1000 words")
    for pattern, count in sorted(result.by_pattern.items()):
        print(f"  pattern {pattern}: {count}")
    print(
        "\nThis is evidence for the prose criterion. It says nothing about who "
        "wrote the essay, and must never be used to claim it does."
    )
    return EXIT_OK
```

Register both in `build_parser`, before the `return parser` line:

```python
    status = subparsers.add_parser("status", help="Show what a batch still owes")
    status.add_argument("--batch", required=True)
    status.add_argument("--redacted", type=Path, default=Path("redacted"))
    status.add_argument("--drafts", type=Path, default=None)
    _add_common(status)
    status.set_defaults(handler=_cmd_status)

    density = subparsers.add_parser("density", help="Count watched AI terms in a file")
    density.add_argument("path", type=Path)
    density.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    density.add_argument("--json", action="store_true")
    _add_common(density)
    density.set_defaults(handler=_cmd_density)
```

Update the module docstring's subcommand list to include:

```
    status       Show which submissions still need a draft.
    density      Count watched AI terms in one file.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_batch.py -v`
Expected: 13 passed

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scripts/grading/batch.py scripts/grade.py tests/grading/test_batch.py
git commit -m "Survey a batch so an interrupted run resumes, and report density from the CLI"
```

---

### Task 9: The batch consistency pass

Grading one essay well is not the same as grading thirty consistently. The consistency pass reads the JSON records the engine wrote and flags what a human should look at twice: a score far from the batch, a criterion-4 score that runs against its own density evidence, and feedback sentences copied between students.

These are flags, not failures. An outlier is often a genuinely unusual essay. The pass reports and lets the instructor decide.

**Files:**
- Create: `scripts/grading/consistency.py`
- Create: `tests/grading/test_consistency.py`
- Modify: `scripts/grade.py`

**Interfaces:**
- Consumes: `GradeRecord`, `CriterionScore` (Task 4); `survey` (Task 8).
- Produces:
  - `Outlier(kind: str, message: str, tokens: tuple[str, ...])`
  - `review(records: list[GradeRecord], drafts: dict[str, str], threshold: float = 2.0, min_batch: int = 3) -> list[Outlier]`
  - `MIN_BATCH: int` (3), `Z_THRESHOLD: float` (2.0), `SENTENCE_WORDS: int` (8)
  - `grade.py consistency` subcommand, with `--fail-on-findings`

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_consistency.py`:

```python
"""Grading thirty essays consistently is a different job from grading one well."""

import subprocess
import sys

from scripts.grading.consistency import review
from scripts.grading.records import CANNOT_ASSESS, CriterionScore, GradeRecord

NAMES = {
    1: "Thesis & Theoretical Framing",
    2: "Evidence & Empirical Grounding",
    3: "Counterargument & Critical Analysis",
    4: "Structure, Prose & Academic Voice",
}


def _record(token, scores, density=5.0, unassessable=()):
    criteria = []
    for number, score in scores.items():
        assessable = number not in unassessable
        criteria.append(
            CriterionScore(
                number=number,
                name=NAMES[number],
                points_possible=25,
                score=score if assessable else None,
                level="Proficient" if assessable else CANNOT_ASSESS,
                assessable=assessable,
                justifications=(),
            )
        )
    scored = [c for c in criteria if c.assessable and c.score is not None]
    return GradeRecord(
        batch="essay-2",
        token=token,
        source=f"redacted/{token}.md",
        course="phil210",
        rubric="r.md",
        profile="p.md",
        criteria=tuple(criteria),
        total=sum(c.score for c in scored),
        total_possible=sum(c.points_possible for c in scored),
        letter="B",
        pattern_density={"per_thousand": density, "count": int(density), "words": 1000},
    )


def _even_batch():
    return [
        _record("STUDENT_01", {1: 20, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_02", {1: 21, 2: 20, 3: 19, 4: 20}),
        _record("STUDENT_03", {1: 20, 2: 21, 3: 20, 4: 19}),
        _record("STUDENT_04", {1: 19, 2: 20, 3: 21, 4: 20}),
    ]


def test_an_even_batch_raises_nothing():
    assert review(_even_batch(), {}) == []


def test_a_score_far_from_the_batch_is_flagged():
    records = _even_batch()
    records.append(_record("STUDENT_05", {1: 4, 2: 20, 3: 20, 4: 20}))
    flags = review(records, {})
    assert any(flag.kind == "score-outlier" for flag in flags)
    assert any("STUDENT_05" in flag.tokens for flag in flags)


def test_the_flag_names_the_criterion_that_moved():
    records = _even_batch()
    records.append(_record("STUDENT_05", {1: 4, 2: 20, 3: 20, 4: 20}))
    flags = [flag for flag in review(records, {}) if flag.kind == "score-outlier"]
    assert len(flags) == 1
    assert "Thesis" in flags[0].message
    assert flags[0].tokens == ("STUDENT_05",)


def test_a_one_point_difference_in_a_tight_batch_is_not_an_outlier():
    records = [
        _record("STUDENT_01", {1: 20, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_02", {1: 20, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_03", {1: 21, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_04", {1: 20, 2: 20, 3: 20, 4: 20}),
    ]
    assert not [
        flag for flag in review(records, {}) if flag.kind == "score-outlier"
    ]


def test_one_extreme_score_cannot_hide_behind_the_spread_it_creates():
    """A mean-and-stdev check misses this. The 4 gives itself a z of 1.99."""
    records = [
        _record("STUDENT_01", {1: 20, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_02", {1: 21, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_03", {1: 20, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_04", {1: 19, 2: 20, 3: 20, 4: 20}),
        _record("STUDENT_05", {1: 4, 2: 20, 3: 20, 4: 20}),
    ]
    flags = [flag for flag in review(records, {}) if flag.kind == "score-outlier"]
    assert [flag.tokens for flag in flags] == [("STUDENT_05",)]


def test_a_batch_too_small_for_statistics_says_so():
    flags = review([_record("STUDENT_01", {1: 20, 2: 20, 3: 20, 4: 20})], {})
    assert any(flag.kind == "too-small" for flag in flags)


def test_a_criterion_4_score_running_against_its_density_is_flagged():
    records = [
        _record("STUDENT_01", {1: 20, 2: 20, 3: 20, 4: 24}, density=40.0),
        _record("STUDENT_02", {1: 20, 2: 20, 3: 20, 4: 12}, density=2.0),
        _record("STUDENT_03", {1: 20, 2: 20, 3: 20, 4: 18}, density=10.0),
    ]
    flags = review(records, {})
    inversions = [flag for flag in flags if flag.kind == "density-inversion"]
    assert inversions
    assert inversions[0].tokens == ("STUDENT_01", "STUDENT_02")
    # Each submission is compared only against its sharpest counterexample,
    # so this never grows to every pair in the batch.
    assert len(inversions) <= len(records)


def test_density_close_together_is_not_an_inversion():
    records = [
        _record("STUDENT_01", {1: 20, 2: 20, 3: 20, 4: 21}, density=10.0),
        _record("STUDENT_02", {1: 20, 2: 20, 3: 20, 4: 20}, density=9.5),
        _record("STUDENT_03", {1: 20, 2: 20, 3: 20, 4: 20}, density=10.2),
    ]
    assert not [
        flag for flag in review(records, {}) if flag.kind == "density-inversion"
    ]


def test_a_sentence_reused_across_two_students_is_flagged():
    drafts = {
        "STUDENT_01": "Your thesis needs a sharper claim in the opening paragraph here.",
        "STUDENT_02": "Your thesis needs a sharper claim in the opening paragraph here.",
        "STUDENT_03": "A different comment entirely about counterarguments and evidence.",
        "STUDENT_04": "Another different comment about the structure of your argument.",
    }
    flags = review(_even_batch(), drafts)
    reused = [flag for flag in flags if flag.kind == "reused-sentence"]
    assert reused
    assert set(reused[0].tokens) == {"STUDENT_01", "STUDENT_02"}


def test_a_short_shared_phrase_is_not_flagged_as_reuse():
    drafts = {
        "STUDENT_01": "Good work here.",
        "STUDENT_02": "Good work here.",
        "STUDENT_03": "Something else.",
        "STUDENT_04": "Another thing.",
    }
    assert not [
        flag for flag in review(_even_batch(), drafts) if flag.kind == "reused-sentence"
    ]


def test_the_sign_off_is_not_counted_as_reuse():
    drafts = {token: "— Dr. Vance" for token in
              ["STUDENT_01", "STUDENT_02", "STUDENT_03", "STUDENT_04"]}
    assert not [
        flag for flag in review(_even_batch(), drafts) if flag.kind == "reused-sentence"
    ]


def test_an_unassessed_criterion_is_reported():
    records = _even_batch()
    records.append(_record("STUDENT_05", {1: 20, 2: 20, 3: 20, 4: 20}, unassessable=(2,)))
    flags = review(records, {})
    assert any(flag.kind == "unassessed" for flag in flags)


def test_the_consistency_subcommand_runs_over_a_directory(tmp_path):
    drafts = tmp_path / "drafts"
    redacted = tmp_path / "redacted"
    drafts.mkdir()
    redacted.mkdir()
    records = _even_batch()
    records.append(_record("STUDENT_05", {1: 4, 2: 20, 3: 20, 4: 20}))
    for record in records:
        (redacted / f"{record.token}.md").write_text("essay", encoding="utf-8")
        (drafts / f"{record.token}.md").write_text("feedback here", encoding="utf-8")
        record.save(drafts / f"{record.token}.json")
    result = subprocess.run(
        [
            sys.executable, "scripts/grade.py", "consistency",
            "--batch", "essay-2", "--redacted", str(redacted), "--drafts", str(drafts),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "score-outlier" in result.stdout


def test_the_consistency_subcommand_can_fail_on_findings(tmp_path):
    drafts = tmp_path / "drafts"
    redacted = tmp_path / "redacted"
    drafts.mkdir()
    redacted.mkdir()
    records = _even_batch()
    records.append(_record("STUDENT_05", {1: 4, 2: 20, 3: 20, 4: 20}))
    for record in records:
        (redacted / f"{record.token}.md").write_text("essay", encoding="utf-8")
        (drafts / f"{record.token}.md").write_text("feedback here", encoding="utf-8")
        record.save(drafts / f"{record.token}.json")
    result = subprocess.run(
        [
            sys.executable, "scripts/grade.py", "consistency",
            "--batch", "essay-2", "--redacted", str(redacted),
            "--drafts", str(drafts), "--fail-on-findings",
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 12
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_consistency.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.grading.consistency'`

- [ ] **Step 3: Write the consistency pass**

`scripts/grading/consistency.py`:

```python
"""Flag what a human should look at twice across a graded batch.

Grading one essay well and grading thirty consistently are different jobs.
This reads the JSON records rather than the prose, which is why the records
exist.

Everything here is a flag, never a failure. An outlier is often a genuinely
unusual essay, and a tool that refused the batch over one would be wrong more
often than right. The instructor decides.

Outliers are found with the median and the median absolute deviation, not
the mean and the standard deviation. One extreme score inflates the standard
deviation enough to hide itself: a batch scoring 20, 21, 20, 19, 4 gives the
4 a z-score of 1.99, just under a threshold of 2, while flagging the 21 as an
outlier instead. The median does not move. A minimum gap in points sits on
top of that, so a one-point difference in a tight batch is never called an
outlier no matter how small the spread.

The density check deserves its own note. A criterion-4 score that runs
against its own density evidence -- a high score on the essay with far more
watched terms -- is worth seeing. It is still not proof: a paper can use
watched words well. The flag names both papers and stops there. Each
submission is compared only against its sharpest counterexample, so a batch
of thirty produces at most thirty flags rather than several hundred pairs.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

from scripts.grading.records import GradeRecord

MIN_BATCH = 3
Z_THRESHOLD = 2.0
SENTENCE_WORDS = 8
DENSITY_RATIO = 1.5
PROSE_CRITERION = 4

# Below this many points from the median, a score is never an outlier, however
# tight the batch. Without it, a batch of 20, 20, 21, 20, 20 flags the 21.
MIN_POINTS_GAP = 3

# Turns a median absolute deviation into something comparable to a standard
# deviation for normally distributed data.
ROBUST_SCALE = 0.6745

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_WORD = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class Outlier:
    """One thing in the batch worth a second look."""

    kind: str
    message: str
    tokens: tuple[str, ...] = ()


def _scores(records: list[GradeRecord], number: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for record in records:
        for criterion in record.criteria:
            if criterion.number == number and criterion.assessable:
                if criterion.score is not None:
                    found[record.token] = criterion.score
    return found


def _criterion_names(records: list[GradeRecord]) -> dict[int, str]:
    names: dict[int, str] = {}
    for record in records:
        for criterion in record.criteria:
            names.setdefault(criterion.number, criterion.name)
    return names


def _score_outliers(
    records: list[GradeRecord], threshold: float, names: dict[int, str]
) -> list[Outlier]:
    flags: list[Outlier] = []
    for number in sorted(names):
        scores = _scores(records, number)
        if len(scores) < MIN_BATCH:
            continue
        values = list(scores.values())
        middle = statistics.median(values)
        # The scale floor of one point keeps a batch of identical scores from
        # dividing by zero, and still lets a genuine gap in that batch show.
        scale = max(statistics.median([abs(v - middle) for v in values]), 1.0)
        for token, score in sorted(scores.items()):
            gap = abs(score - middle)
            if gap < MIN_POINTS_GAP:
                continue
            if ROBUST_SCALE * gap / scale < threshold:
                continue
            flags.append(
                Outlier(
                    "score-outlier",
                    f"{token} scored {score} on {number}. {names[number]} "
                    f"while the batch median was {middle:g}. Check the "
                    "quotes behind it.",
                    (token,),
                )
            )
    return flags


def _density_inversions(records: list[GradeRecord]) -> list[Outlier]:
    prose = _scores(records, PROSE_CRITERION)
    density = {
        record.token: float(record.pattern_density.get("per_thousand", 0.0))
        for record in records
    }
    flags: list[Outlier] = []
    tokens = sorted(prose)
    for high in tokens:
        if density.get(high, 0.0) <= 0:
            continue
        sharpest: tuple[float, str] | None = None
        for low in tokens:
            if low == high or density.get(low, 0.0) <= 0:
                continue
            if density[high] < density[low] * DENSITY_RATIO:
                continue
            if prose[high] <= prose[low]:
                continue
            ratio = density[high] / density[low]
            if sharpest is None or ratio > sharpest[0]:
                sharpest = (ratio, low)
        if sharpest is None:
            continue
        low = sharpest[1]
        flags.append(
            Outlier(
                "density-inversion",
                f"{high} has {density[high]} watched terms per 1000 words and "
                f"scored {prose[high]} on criterion {PROSE_CRITERION}. {low} "
                f"has {density[low]} and scored {prose[low]}. Density is "
                "evidence, not proof, so read both before changing either "
                "score.",
                (high, low),
            )
        )
    return flags


def _reused_sentences(drafts: dict[str, str]) -> list[Outlier]:
    owners: dict[str, set[str]] = defaultdict(set)
    for token, text in drafts.items():
        for raw in _SENTENCE.findall(text):
            words = _WORD.findall(raw.lower())
            if len(words) < SENTENCE_WORDS:
                continue
            owners[" ".join(words)].add(token)
    flags: list[Outlier] = []
    for sentence, tokens in sorted(owners.items()):
        if len(tokens) > 1:
            flags.append(
                Outlier(
                    "reused-sentence",
                    f"{len(tokens)} drafts share this sentence: {sentence!r}. "
                    "Feedback that repeats verbatim reads as a form letter.",
                    tuple(sorted(tokens)),
                )
            )
    return flags


def _unassessed(records: list[GradeRecord], names: dict[int, str]) -> list[Outlier]:
    flags: list[Outlier] = []
    for record in records:
        missing = [
            f"{c.number}. {names.get(c.number, c.name)}"
            for c in record.criteria
            if not c.assessable
        ]
        if missing:
            flags.append(
                Outlier(
                    "unassessed",
                    f"{record.token} has no score on {', '.join(missing)}. "
                    f"The grade covers {record.total_possible} points, not the "
                    "full rubric.",
                    (record.token,),
                )
            )
    return flags


def review(
    records: list[GradeRecord],
    drafts: dict[str, str],
    threshold: float = Z_THRESHOLD,
    min_batch: int = MIN_BATCH,
) -> list[Outlier]:
    """Return everything in this batch worth a second look."""
    if len(records) < min_batch:
        return [
            Outlier(
                "too-small",
                f"This batch has {len(records)} graded submissions. Outlier "
                f"checks need at least {min_batch}, so they were skipped. Read "
                "each draft yourself.",
                tuple(sorted(record.token for record in records)),
            )
        ]
    names = _criterion_names(records)
    return (
        _score_outliers(records, threshold, names)
        + _density_inversions(records)
        + _reused_sentences(drafts)
        + _unassessed(records, names)
    )
```

- [ ] **Step 4: Add the consistency subcommand**

In `scripts/grade.py`, add the import:

```python
from scripts.grading.consistency import review  # noqa: E402
```

Add the handler above `_add_common`:

```python
def _cmd_consistency(args: argparse.Namespace) -> int:
    drafts_dir = _drafts_dir(args)
    items = [item for item in survey(args.redacted, drafts_dir) if item.record]
    records = []
    drafts: dict[str, str] = {}
    for item in items:
        record = GradeRecord.load(item.record)
        records.append(record)
        if item.draft:
            drafts[record.token] = item.draft.read_text(encoding="utf-8")

    if not records:
        print(f"No records under {drafts_dir}. Nothing to compare.", file=sys.stderr)
        return EXIT_MALFORMED

    flags = review(records, drafts, threshold=args.threshold)
    print(f"{len(records)} graded submissions, {len(flags)} things to look at.")
    for flag in flags:
        print(f"  {flag.kind}: {flag.message}")
    if not flags:
        print("  Nothing stood out.")
    print(
        "\nThese are flags, not failures. An outlier is often a genuinely "
        "unusual essay. Read the drafts before changing a score."
    )
    if flags and args.fail_on_findings:
        return EXIT_FAILED
    return EXIT_OK
```

Register it in `build_parser`:

```python
    consistency = subparsers.add_parser(
        "consistency", help="Flag outliers across a graded batch"
    )
    consistency.add_argument("--batch", required=True)
    consistency.add_argument("--redacted", type=Path, default=Path("redacted"))
    consistency.add_argument("--drafts", type=Path, default=None)
    consistency.add_argument("--threshold", type=float, default=2.0)
    consistency.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit non-zero when anything is flagged",
    )
    _add_common(consistency)
    consistency.set_defaults(handler=_cmd_consistency)
```

Add to the module docstring's subcommand list:

```
    consistency  Flag outliers across a graded batch.
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_consistency.py -v`
Expected: 12 passed

- [ ] **Step 6: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 7: Commit**

```bash
git add scripts/grading/consistency.py scripts/grade.py tests/grading/test_consistency.py
git commit -m "Flag score outliers, density inversions, and reused feedback across a batch"
```

---
### Task 10: The grader skill

The scripts are the checking. This is the judgement. `SKILL.md` holds the gate, the four hard rules, both flows, and the error table. It is the product; everything else supports it.

Keep the version at `3.0.0` here. Task 12 bumps `SKILL.md`, `README.md`, and `plugin.json` together, because `validate-package.py` requires all three to match.

**Files:**
- Modify: `SKILL.md` (replacing the Plan 1 stub)

**Interfaces:**
- Consumes: everything built in Tasks 1 through 9, plus `scripts/redact.py` and `scripts/rehydrate.py` from Plan 1.
- Produces: the four hard rules, quoted verbatim, which `validate-package.py` checks for in Task 12.

- [ ] **Step 1: Confirm the stub is what you are replacing**

Run: `cat SKILL.md`
Expected: the Plan 1 placeholder that says "Not yet implemented".

- [ ] **Step 2: Write the grader skill**

Replace all of `SKILL.md` with:

````markdown
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

Local only. It fails when a token has no mapping, which is correct: a stray
`STUDENT_12` pasted into an LMS comment is worth failing the run over.

Every file is a draft pending the instructor's sign-off. Say so.

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
````

- [ ] **Step 3: Check the line count**

Run: `wc -l SKILL.md`
Expected: under 500. The validator enforces this in Task 12.

- [ ] **Step 4: Run the validator and the suite**

Run: `python3 scripts/validate-package.py && python3 -m pytest tests/ -v`
Expected: `Humanizer package v3.0.0 is valid`, and all tests pass.

- [ ] **Step 5: Commit**

```bash
git add SKILL.md
git commit -m "Write the grader skill: the gate, four hard rules, and both flows"
```

---

### Task 11: The optional hook

`SKILL.md`'s read rules are instructions, and a model can violate an instruction. Markdown cannot close that gap. This hook can, for the tools it covers.

Name matters here. A file at `hooks/hooks.json` is loaded automatically by a Claude plugin. This one must stay opt-in, so it is named `hooks/block-raw-reads.json` and the instructor merges it into their own settings.

**Files:**
- Create: `hooks/block-raw-reads.json`
- Create: `hooks/block_raw_reads.py`
- Create: `tests/grading/test_hook.py`

**Interfaces:**
- Consumes: nothing. The guard is standalone by design, so it works in a session where nothing else is loaded.
- Produces:
  - `GUARDED: tuple[str, ...]` — path fragments that are never readable
  - `offending(text: str) -> str | None`
  - `decide(payload: dict) -> str | None` — the reason to deny, or `None` to allow
  - exit code 2 to deny, 0 to allow

- [ ] **Step 1: Write the failing tests**

`tests/grading/test_hook.py`:

```python
"""Instructions can be violated. This is the part that cannot be."""

import json
import subprocess
import sys

HOOK = "hooks/block_raw_reads.py"


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _read(path: str) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_reading_a_raw_submission_is_denied():
    result = _run(_read("/Users/x/course/submissions/essay-3/smith_jane.docx"))
    assert result.returncode == 2
    assert "submissions/" in result.stderr


def test_reading_the_token_map_is_denied():
    result = _run(_read(".grading/maps/essay-3.json"))
    assert result.returncode == 2


def test_reading_the_review_file_is_denied():
    assert _run(_read(".grading/reviews/essay-3.json")).returncode == 2


def test_reading_a_redacted_file_is_allowed():
    assert _run(_read("redacted/STUDENT_01.md")).returncode == 0


def test_reading_a_profile_is_allowed():
    assert _run(_read(".grading/profiles/phil210.md")).returncode == 0


def test_reading_a_draft_is_allowed():
    assert _run(_read(".grading/drafts/essay-3/STUDENT_01.md")).returncode == 0


def test_a_windows_style_path_is_denied_too():
    assert _run(_read(r"C:\course\submissions\essay3\paper.docx")).returncode == 2


def test_a_glob_over_submissions_is_denied():
    payload = {"tool_name": "Glob", "tool_input": {"pattern": "submissions/**/*.docx"}}
    assert _run(payload).returncode == 2


def test_writing_into_submissions_is_denied():
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "submissions/notes.md", "content": "x"},
    }
    assert _run(payload).returncode == 2


def test_catting_a_submission_is_denied():
    assert _run(_bash("cat submissions/essay-3/paper.md")).returncode == 2


def test_grepping_the_map_is_denied():
    assert _run(_bash("grep STUDENT_01 .grading/maps/essay-3.json")).returncode == 2


def test_catting_the_roster_is_denied():
    assert _run(_bash("cat tests/fixtures/phil210/roster.csv")).returncode == 2


def test_catting_a_bare_roster_filename_is_denied():
    assert _run(_bash("cat roster.csv")).returncode == 2


def test_reading_the_roster_with_a_file_tool_is_denied():
    assert _run(_read("phil210.roster.csv")).returncode == 2


def test_running_redact_with_a_roster_flag_is_allowed():
    command = (
        "python3 scripts/redact.py submissions/essay-3 "
        "--roster roster.csv --batch essay-3"
    )
    assert _run(_bash(command)).returncode == 0


def test_running_rehydrate_with_the_map_flag_is_allowed():
    command = (
        "python3 scripts/rehydrate.py .grading/drafts/essay-3 "
        "--map .grading/maps/essay-3.json --out final/essay-3"
    )
    assert _run(_bash(command)).returncode == 0


def test_a_python_one_liner_reading_the_map_is_denied():
    command = "python3 -c \"print(open('.grading/maps/essay-3.json').read())\""
    assert _run(_bash(command)).returncode == 2


def test_an_unrelated_command_is_allowed():
    assert _run(_bash("python3 -m pytest tests/ -v")).returncode == 0


def test_unreadable_input_allows_rather_than_bricking_the_session():
    result = subprocess.run(
        [sys.executable, HOOK], input="not json", capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stderr


def test_the_denial_message_says_what_to_read_instead():
    result = _run(_read("submissions/essay-3/paper.md"))
    assert "redacted/" in result.stderr


def test_the_hook_config_is_valid_and_opt_in():
    from pathlib import Path

    config = json.loads(
        Path("hooks/block-raw-reads.json").read_text(encoding="utf-8")
    )
    entries = config["hooks"]["PreToolUse"]
    assert entries
    assert "Bash" in entries[0]["matcher"]
    assert "Read" in entries[0]["matcher"]
    assert not Path("hooks/hooks.json").exists()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m pytest tests/grading/test_hook.py -v`
Expected: FAIL — `hooks/block_raw_reads.py` does not exist, so every subprocess exits 2 with "can't open file".

- [ ] **Step 3: Write the guard**

`hooks/block_raw_reads.py`:

```python
#!/usr/bin/env python3
"""Deny reads of raw submissions, the token map, and the roster.

SKILL.md states four hard rules. They are instructions, and a model can
violate an instruction. This closes that gap for the tools it covers.

Reads one PreToolUse payload on stdin. Exit 2 denies the call and shows the
reason. Exit 0 allows it.

Two deliberate limits, both worth stating plainly:

Bash is checked by reading the command string. A denial fires only when a
reading utility and a guarded path appear together, because `redact.py
--roster roster.csv` is a legitimate call and `cat roster.csv` is not. A
determined bypass -- encoding the path, or building it at runtime -- defeats
this. The hook stops accidents and instruction drift, not an adversary.

Unreadable input allows rather than denies. A guard that cannot parse its
payload and denies everything would block every tool call in the session,
including the ones that would fix it. It warns instead.
"""

from __future__ import annotations

import json
import re
import sys

# Never readable. `submissions/` holds raw student work, `maps/` is the only
# artifact that turns a token back into a name, and `reviews/` lists the very
# names redaction just removed.
GUARDED = (
    "submissions/",
    ".grading/maps/",
    ".grading/reviews/",
)

# The prefix class, rather than a plain (^|/), is what catches a bare
# `cat roster.csv` as well as `cat course/roster.csv`.
_ROSTER = re.compile(r"""(?:^|[\s"'=:/])[\w.-]*roster\.csv\b""", re.IGNORECASE)

# Fields that carry a path across the file tools.
_PATH_FIELDS = ("file_path", "path", "notebook_path", "pattern", "glob")

# Utilities that put a file's contents into the conversation. A command has to
# use one of these AND name a guarded path before it is denied.
_READERS = re.compile(
    r"(^|[\s;|&(])"
    r"(cat|bat|head|tail|less|more|nl|strings|xxd|od|open|"
    r"grep|rg|ag|ack|awk|sed|cut|jq|python3?\s+-c|node\s+-e|perl\s+-e)"
    r"([\s;|&)]|$)"
)

REASON = (
    "The grading skill reads only from redacted/. "
    "Run scripts/redact.py first, then read the redacted copy. "
    "The map and review files never enter a model's context."
)


def offending(text: str) -> str | None:
    """Return the guarded fragment a path contains, or None."""
    haystack = text.replace("\\", "/")
    for guarded in GUARDED:
        if guarded in haystack:
            return guarded
    if _ROSTER.search(haystack):
        return "roster.csv"
    return None


def decide(payload: dict) -> str | None:
    """Return the reason to deny this tool call, or None to allow it."""
    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if not _READERS.search(command):
            return None
        found = offending(command)
        return f"{tool} would read {found}" if found else None

    for field in _PATH_FIELDS:
        value = tool_input.get(field)
        if not isinstance(value, str):
            continue
        found = offending(value)
        if found:
            return f"{tool} would touch {found}"
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(
            "block_raw_reads: could not read the hook payload, so the call was "
            "allowed. Check the hook configuration.",
            file=sys.stderr,
        )
        return 0
    if not isinstance(payload, dict):
        print("block_raw_reads: unexpected payload shape; allowed.", file=sys.stderr)
        return 0

    reason = decide(payload)
    if reason is None:
        return 0
    print(f"Blocked: {reason}. {REASON}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write the hook config**

`hooks/block-raw-reads.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Read|Glob|Grep|Edit|Write|NotebookEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python3 \"$CLAUDE_PROJECT_DIR/hooks/block_raw_reads.py\""
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m pytest tests/grading/test_hook.py -v`
Expected: 19 passed

- [ ] **Step 6: Try the guard by hand**

Run:

```bash
echo '{"tool_name":"Read","tool_input":{"file_path":"submissions/x.md"}}' \
  | python3 hooks/block_raw_reads.py; echo "exit $?"
echo '{"tool_name":"Read","tool_input":{"file_path":"redacted/STUDENT_01.md"}}' \
  | python3 hooks/block_raw_reads.py; echo "exit $?"
```

Expected: the first prints a blocked message and `exit 2`. The second prints
nothing and `exit 0`.

- [ ] **Step 7: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all pass

- [ ] **Step 8: Commit**

```bash
git add hooks/ tests/grading/test_hook.py
git commit -m "Ship the opt-in hook that denies reads of submissions and the map"
```

---

### Task 12: End to end, then the package

The last task proves the whole chain on the corpus and brings the package back into a shippable state. Three package rules change, and the version moves to 3.1.0 in the three places `validate-package.py` compares.

**Files:**
- Create: `tests/grading/test_end_to_end.py`
- Modify: `scripts/validate-package.py`
- Modify: `AGENTS.md`
- Modify: `README.md`
- Modify: `SKILL.md`, `skills/humanizer/SKILL.md`, `.claude-plugin/plugin.json` (version)

**Interfaces:**
- Consumes: everything.
- Produces: a passing package and a proven chain.

- [ ] **Step 1: Write the end-to-end test**

`tests/grading/test_end_to_end.py`:

```python
"""Redact, grade, verify, compare, rehydrate. The whole chain, on the corpus.

The grading itself is done here in plain Python rather than by a model. That
is deliberate: this test proves the plumbing, the checks, and the round trip.
Whether a model's judgement is any good is what the holdout report answers.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.grading.batch import DRAFTED, PENDING, summary, survey
from scripts.grading.consistency import review
from scripts.grading.density import PATTERNS_PATH, load_terms, scan
from scripts.grading.profile import load_profile
from scripts.grading.records import CriterionScore, GradeRecord, Justification, locate
from scripts.grading.rubric import load_rubric
from scripts.grading.verify import errors, verify_draft
from scripts.redaction.pipeline import EXIT_OK, run
from scripts.redaction.roster import load_roster

FEEDBACK_TEMPLATE = """[{annotation}]

**The Bottom Line**

Your argument holds. You named the mechanism instead of gesturing at it,
and the evidence you chose does real work.

**What Worked**

* Paragraph 2 does the analytical work the thesis promises.

**What to Work On**

* Give the strongest counterargument a full paragraph, not a clause.
* Name the statute or the case rather than describing it.

Come see me in office hours if you want to push this further.

— Dr. Vance
"""


def _unique_sentences(source: str, count: int) -> list[str]:
    """Return sentences long enough to be evidence and unique in the source."""
    picked: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+", source):
        sentence = " ".join(raw.split())
        if len(sentence.split()) < 12:
            continue
        try:
            locate(sentence, source, None)
        except Exception:
            continue
        picked.append(sentence)
        if len(picked) == count:
            break
    return picked


def _grade(token: str, source_path: Path, rubric, terms) -> tuple[GradeRecord, str]:
    source = source_path.read_text(encoding="utf-8")
    quotes = _unique_sentences(source, len(rubric.criteria))
    assert len(quotes) == len(rubric.criteria), f"{source_path} is too short to grade"

    scores = [21, 20, 19, 22]
    criteria = tuple(
        CriterionScore(
            number=criterion.number,
            name=criterion.name,
            points_possible=criterion.points,
            score=scores[index],
            level="Proficient",
            assessable=True,
            justifications=(Justification(quote=quotes[index]),),
        )
        for index, criterion in enumerate(rubric.criteria)
    )
    total = sum(scores)
    possible = sum(c.points for c in rubric.criteria)
    record = GradeRecord(
        batch="essay-2",
        token=token,
        source=str(source_path),
        course="phil210",
        rubric="tests/fixtures/phil210/grading_rubric.md",
        profile="tests/fixtures/phil210/profile_phil210.md",
        criteria=criteria,
        total=total,
        total_possible=possible,
        letter=rubric.letter_for(100 * total / possible),
        pattern_density=scan(source, terms).as_dict(),
    )
    feedback = FEEDBACK_TEMPLATE.format(
        annotation=f"This sentence carries the paragraph. Lead with it. ({token})"
    )
    return record, feedback


@pytest.fixture
def graded_batch(tmp_path, phil210, instructor):
    # Only five of the ten roster students submitted this batch; the other
    # five appear in graded/ instead. Passing the whole roster makes
    # redaction exit EXIT_ROSTER, which is correct behaviour and is already
    # covered by Plan 1's pipeline tests. This test is about the grading
    # chain, so it hands redaction a roster that matches the batch.
    corpus = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((phil210 / "ungraded").glob("*.md"))
    )
    roster = [
        entry for entry in load_roster(phil210 / "roster.csv")
        if entry.name in corpus
    ]
    assert len(roster) == 5, "the ungraded batch should name five students"

    redacted = tmp_path / "redacted"
    result = run(
        source=phil210 / "ungraded",
        destination=redacted,
        map_path=tmp_path / "maps" / "essay-2.json",
        roster=roster,
        batch="essay-2",
        protected=(instructor,),
        reviewed=True,
    )
    assert result.exit_code == EXIT_OK, result.message
    return tmp_path, redacted


def test_the_chain_runs_from_redaction_to_final_feedback(
    graded_batch, phil210, instructor
):
    tmp_path, redacted = graded_batch
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    rubric = load_rubric(phil210 / "grading_rubric.md")
    profile = load_profile(phil210 / "profile_phil210.md")
    terms = load_terms(PATTERNS_PATH)

    before = survey(redacted, drafts)
    assert before, "redaction produced no files"
    assert summary(before)[PENDING] == len(before)

    records = []
    for item in before:
        record, feedback = _grade(item.token, item.source, rubric, terms)
        findings = verify_draft(record, feedback, item.source.read_text("utf-8"),
                                rubric, profile, terms)
        assert errors(findings) == [], (item.token, errors(findings))
        record.save(drafts / f"{item.token}.json")
        (drafts / f"{item.token}.md").write_text(feedback, encoding="utf-8")
        records.append(record)

    after = survey(redacted, drafts)
    assert summary(after)[DRAFTED] == len(after)

    flags = review(records, {r.token: FEEDBACK_TEMPLATE for r in records})
    assert any(flag.kind == "reused-sentence" for flag in flags), (
        "identical feedback across the batch must be flagged"
    )

    final = tmp_path / "final"
    rehydrated = subprocess.run(
        [
            sys.executable, "scripts/rehydrate.py", str(drafts),
            "--map", str(tmp_path / "maps" / "essay-2.json"), "--out", str(final),
        ],
        capture_output=True, text=True,
    )
    assert rehydrated.returncode == 0, rehydrated.stderr
    assert list(final.glob("*.md"))


def test_no_student_name_reaches_a_draft(graded_batch, phil210):
    tmp_path, redacted = graded_batch
    rubric = load_rubric(phil210 / "grading_rubric.md")
    terms = load_terms(PATTERNS_PATH)
    names = {
        part
        for entry in load_roster(phil210 / "roster.csv")
        for part in entry.name.split()
    }
    for item in survey(redacted, tmp_path / "absent"):
        record, feedback = _grade(item.token, item.source, rubric, terms)
        blob = feedback + "".join(
            j.quote for c in record.criteria for j in c.justifications
        )
        # Whole words only. "Lin" sits inside "The Bottom Line", which the
        # rubric's own protocol requires, and that is not a leak.
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", blob), (
                f"{name} reached a draft for {item.token}"
            )


def test_the_batch_resumes_instead_of_regrading(graded_batch):
    tmp_path, redacted = graded_batch
    drafts = tmp_path / "drafts2"
    drafts.mkdir()
    items = survey(redacted, drafts)
    first = items[0]
    (drafts / f"{first.token}.md").write_text("feedback", encoding="utf-8")
    (drafts / f"{first.token}.json").write_text("{}", encoding="utf-8")
    pending = [item.token for item in survey(redacted, drafts) if item.state == PENDING]
    assert first.token not in pending
    assert len(pending) == len(items) - 1


def test_the_grading_scripts_never_open_the_map():
    """Invariant 1, checked by reading the code rather than trusting it."""
    for path in Path("scripts/grading").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "TokenMap" not in text, f"{path} must not load the token map"
        assert "maps/" not in text, f"{path} must not reference the map directory"
    grade_cli = Path("scripts/grade.py").read_text(encoding="utf-8")
    assert "TokenMap" not in grade_cli
```

- [ ] **Step 2: Run it**

Run: `python3 -m pytest tests/grading/test_end_to_end.py -v`
Expected: 4 passed.

If `_unique_sentences` cannot find four usable sentences in one of the shorter
ungraded fixtures, the assertion names the file. Lower the 12-word floor to 10
rather than shortening the quotes — a shorter quote risks ambiguity, which
`locate` refuses on purpose.

- [ ] **Step 3: Extend the validator**

In `scripts/validate-package.py`, replace the existing skill-file loop with one
that also pins the version in both skills:

```python
for skill_file in sorted(expected_skills):
    if (ROOT / skill_file).is_symlink():
        raise SystemExit(f"Keep {skill_file} a regular file")
    skill_text = (ROOT / skill_file).read_text(encoding="utf-8")
    if len(skill_text.splitlines()) > 500:
        raise SystemExit(f"Keep {skill_file} at 500 lines or fewer")
    if f'version: "{skill_version}"' not in skill_text:
        raise SystemExit(f"Use the package version {skill_version} in {skill_file}")
```

Then add these checks just above the final `print`:

```python
required_references = {
    "ai-patterns.md",
    "calibration.md",
    "redaction.md",
    "rubric-format.md",
}
present_references = {path.name for path in (ROOT / "references").glob("*.md")}
missing_references = sorted(required_references - present_references)
if missing_references:
    raise SystemExit(
        "Add the missing reference files: " + ", ".join(missing_references)
    )

hard_rules = (
    "Read only from `redacted/`.",
    "Accept file paths. Never accept pasted student work.",
    "Never echo the map file.",
    "Refuse to grade without a profile.",
)
missing_rules = [rule for rule in hard_rules if rule not in SKILL]
if missing_rules:
    raise SystemExit(
        "Keep the four hard rules in SKILL.md: " + "; ".join(missing_rules)
    )

if (ROOT / "hooks" / "hooks.json").exists():
    raise SystemExit(
        "Do not name the hook hooks/hooks.json. A plugin loads that name "
        "automatically, and this hook must stay opt-in."
    )
hook_path = ROOT / "hooks" / "block-raw-reads.json"
if not hook_path.is_file():
    raise SystemExit("Ship hooks/block-raw-reads.json")
hook = json.loads(hook_path.read_text(encoding="utf-8"))
hook_entries = hook.get("hooks", {}).get("PreToolUse", [])
if not hook_entries or "Bash" not in hook_entries[0].get("matcher", ""):
    raise SystemExit("The hook must match Bash as well as the file tools")
if "block-raw-reads" not in README:
    raise SystemExit("Document the optional hook in README.md")
```

- [ ] **Step 4: Run the validator and watch it fail for the right reasons**

Run: `python3 scripts/validate-package.py`
Expected: it fails, naming the README hook documentation. That is Step 6's job.

- [ ] **Step 5: Update AGENTS.md**

Replace the "What this repo contains" paragraph with:

```markdown
This repo holds two agent skills, the reference files they share, and the local
scripts that stand behind the grader. The grading skill lives at the repo root
in `SKILL.md`. The Humanizer skill lives at `skills/humanizer/SKILL.md`. Both
skills read `references/ai-patterns.md` for the 35 patterns. `scripts/redaction/`
redacts student identity before any work reaches a model, and `scripts/grading/`
checks every claim a generated grade makes. `scripts/validate-package.py` checks
the package. The repo has no build step.
```

Add these bullets to "Key files", after the `references/ai-patterns.md` line:

```markdown
- `references/redaction.md`, `references/calibration.md`, and `references/rubric-format.md` explain what redaction covers, how calibration runs, and what a rubric must contain.
- `scripts/redaction/` holds the five redaction layers. Read `docs/superpowers/decisions-redaction-foundation.md` before changing any of them.
- `scripts/grading/` holds the rubric parser, sample parser, density counter, record format, profile loader, verifier, batch survey, and consistency pass.
- `scripts/redact.py`, `scripts/rehydrate.py`, `scripts/calibrate.py`, and `scripts/grade.py` are the four command-line entry points.
- `hooks/block-raw-reads.json` is the optional `PreToolUse` hook. It must stay opt-in, so it must never be renamed `hooks/hooks.json`.
```

Add these rules to "Rules for changes", after the **Patterns** bullet:

```markdown
- **Invariants:** The token map is the only artifact holding real identities. Nothing under `scripts/grading/` may load it. The model reads only from `redacted/`. Calibration and grading stay separate commands.
- **Tests:** Run `python3 -m pytest tests/` from the repo root, so `scripts.` imports resolve. The leak test and the end-to-end test are the two that matter; the rest support them.
```

- [ ] **Step 6: Update README.md**

Add this section immediately after the `# Humanizer` intro paragraph, before
`## How it works`:

````markdown
## Two skills in one package

This repo ships two skills. **Humanizer** rewrites AI-sounding text. **Grader**
scores student work against an instructor's rubric and drafts feedback in that
instructor's voice, using Humanizer's 35 patterns as its finishing pass.

Grader keeps student identity out of a cloud model's context. It redacts names,
IDs, emails, filenames, and document metadata to stable tokens on your machine,
grades the redacted copy, and reattaches real names locally at the end.

```
/grade calibrate --course phil210      # learn the instructor's standards
/grade submissions/essay-3 --course phil210
```

Read `references/redaction.md` before sending anything to a cloud model.
Pseudonymization is not anonymization, and your institution's policy decides
whether student work may leave your machine.

### The optional hook

Grader's read rules are instructions in `SKILL.md`, and a model can violate an
instruction. `hooks/block-raw-reads.json` is a `PreToolUse` hook that denies
reads of `submissions/`, the token map, and the roster. It is the difference
between a rule and a guarantee.

It is opt-in. Merge it into `.claude/settings.json` yourself:

```bash
cat hooks/block-raw-reads.json   # then merge the "hooks" key into your settings
```

It is Claude Code specific, and Grader works without it. Its Bash check matches
command strings, so it stops accidents and instruction drift, not a determined
bypass.
````

Add this line at the top of the version history list:

```markdown
- **3.1.0** — Grader is implemented: calibration with holdout validation, a scoring engine whose every quote is checked against its source, batch resume, a consistency pass, and the optional `block-raw-reads` hook.
```

- [ ] **Step 7: Bump the version in all three places**

- `SKILL.md`: `version: "3.1.0"`
- `skills/humanizer/SKILL.md`: `version: "3.1.0"`
- `.claude-plugin/plugin.json`: `"version": "3.1.0"`

- [ ] **Step 8: Run the validator**

Run: `python3 scripts/validate-package.py`
Expected: `Humanizer package v3.1.0 is valid`

- [ ] **Step 9: Run everything**

Run:

```bash
python3 -m pytest tests/ -v
python3 scripts/validate-package.py
python3 scripts/grade.py density tests/fixtures/phil210/ungraded/paper_7_ungraded_ai_healthcare_slop.md
```

Expected: every test passes, the package is valid, and the density report prints
a rate with a per-pattern breakdown.

- [ ] **Step 10: Commit**

```bash
git add tests/grading/test_end_to_end.py scripts/validate-package.py AGENTS.md README.md SKILL.md skills/humanizer/SKILL.md .claude-plugin/plugin.json
git commit -m "Prove the chain end to end and ship the package at 3.1.0"
```

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task.

| Spec section | Task |
| --- | --- |
| Mode router, the gate, four hard rules | 10 |
| Calibration inputs, profile contents | 5, 6 |
| Holdout validation | 6 |
| Recalibration and rubric drift | 1 (digest), 5 (drift), 7 (`grade.py profile`) |
| Grading engine, scoring, quotes | 4, 7, 10 |
| Voice pass | 10 (the pass), 7 (never-use and structure checks) |
| Structured output, JSON beside Markdown | 4 |
| Pattern density for criterion 4 | 3, 8 |
| Batch driver, resume | 8 |
| Consistency pass | 9 |
| `references/rubric-format.md` | 1 |
| `references/calibration.md` | 5 |
| `hooks/block-raw-reads.json` | 11 |
| Error-handling table | 7, 8, 10 |
| Package rules that must change | 12 |
| Reference corpus | Plan 1; used throughout |

Already delivered by Plan 1 and not repeated here: the redaction gate and its
four detection layers, the self-identification warning pass, fail-closed exits,
the map file, `references/redaction.md`, and the leak, round-trip, filename, and
metadata tests.

**Decisions this plan makes that the spec left open.**

1. *The profile must be name-free.* The spec says calibration is local-only and
   that the instructor's comments are kept verbatim, but those comments contain
   past students' first names, and the profile is read during cloud runs. Task 2
   scrubs names to the literal `[STUDENT]`, and Task 5 tests that no roster name
   survives into the profile. Cost if wrong: a comment bank that reads slightly
   less naturally than the instructor's original.
2. *Offsets are written by the verifier, not the model.* The spec asks for
   "justification quotes with offsets". A model counting characters would be
   wrong constantly, and a wrong offset is worse than none. Task 4's `locate`
   makes the model responsible for the words and the script responsible for the
   position. Cost if wrong: a quote that appears twice fails until the model
   lengthens it, which is one extra round on a small number of drafts.
3. *The letter is computed from the rubric's scale.* The spec does not say who
   picks it. Computing it means the verifier can check it. Cost if wrong: a
   rubric whose scale does not cover 0 to 100 is refused at parse time.
4. *Consistency flags, and exits 0.* The spec says "flags outliers" and gives no
   exit code. An outlier is often a genuinely unusual essay, so failing the batch
   would be wrong more often than right. `--fail-on-findings` exists for anyone
   who disagrees. Cost if wrong: an instructor who skims past the report.
5. *Grading exit codes use 10 to 13.* Redaction owns 2 to 5. Overlapping ranges
   across two CLIs would make a script's exit code ambiguous to read.
6. *The hook's Bash check requires a reading utility and a guarded path
   together.* Denying every command that mentions `roster.csv` would block
   `redact.py --roster`, which is the tool's own required call. Cost if wrong: a
   reading utility not on the list gets through; the list is one line to extend.
7. *The hook allows on unparseable input.* Denying would block every tool call
   in the session, including the ones needed to fix the hook. This is the one
   place in the package that does not fail closed, and Task 11's docstring says
   so.

**Placeholder scan.** No task contains TBD, "implement later", "handle edge
cases", "similar to Task N", or a test step without test code. Two steps
generate a file with a command rather than pasting literal content: Task 5's
profile fixture, because its digests must match the parser's output exactly, and
Task 12's version bump, which edits three known lines. Both give the exact
command.

**Type consistency.** `Rubric.criterion(number)` is called in `verify.py` and
`grade.py`. `Criterion.points` is the field name everywhere; `CriterionScore`
uses `points_possible` to keep the rubric's value and the record's copy visibly
distinct, and Task 7 compares them. `load_terms(path)` defaults to
`PATTERNS_PATH` and is called with and without an argument. `scan(text, terms)`
takes the same two arguments in `density.py`, `verify.py`, and `grade.py`.
`GradeRecord.load` / `.save` are used in Tasks 6, 7, 9, and 12. `Profile.drift`
takes a `Rubric` and returns a list of strings in both Task 5 and Task 7.
`survey(redacted, drafts)` returns `list[Item]` in Tasks 8, 9, and 12. `Finding`
carries `level`, `code`, `message`, `token`, `criterion` and is filtered by
`errors()` in both `verify.py` and `grade.py`. `TOKEN` is imported from
`scripts.redaction.rehydrate` in Tasks 2, 5, and 7 and is the same compiled
pattern in each.

**Defects found during execution, and fixed in both code and this plan.**
Every one of these passed its own tests before it was caught.

1. `locate` certified a quote spliced across a paragraph break — the tail of
   one paragraph stitched to the head of the next passed as evidence. Fixed by
   discarding any match spanning a blank line, while a line wrap still matches.
2. The calibration packet wrote `"scrubbed"`, the list of names it had just
   replaced, into the one artifact whose purpose is to be name-free. Fixed to a
   count. The CLI's stdout carried the same leak, and sample filenames a third.
3. `_check_totals` raised instead of reporting when a score exceeded its
   criterion maximum — a crash in the gate that exists to report bad records.
4. `load_sample`'s guard silently degraded to a case-sensitive check and
   refused validly-headed samples.
5. Outlier detection used mean and standard deviation, so one extreme score
   inflated the spread enough to hide itself. Now median and MAD.
6. Two tests asserted nothing: a density ratio that collapsed to `> 0`, and a
   stdout check that was already true before the fix it guarded.

**Known gaps, carried forward.**

- Plan 1's self-review noted that the docx author path is unit-tested but not
  covered end to end, because no real `.docx` fixture exists. This plan does not
  close that. `batch.survey` accepts `.docx` sources so the gap does not widen.
- Nothing here validates a model's judgement. The holdout report is the only
  measure of whether a profile grades like its instructor, and it measures one
  sample. The spec's own risk list says the same thing.
- The comparison webpage the project wants next needs two runs committed: one
  calibrated, one plain-LLM baseline. Task 4's JSON record is what makes that
  possible without reparsing prose. Generating those runs is not in this plan.

## Execution Handoff

Plan complete. Two execution options:

**1. Subagent-Driven (recommended)** — a fresh subagent per task, review between
tasks, fast iteration.

**2. Inline Execution** — execute tasks in this session with checkpoints.

Which approach?
