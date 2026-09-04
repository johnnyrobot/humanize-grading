# Supervised Calibration and Draft Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make calibration a bound, instructor-approved gate; put the instructor inside the drafting loop through pilot or per-draft review; and require a consistency acknowledgement before publication.

**Architecture:** Three new identity-free state records mirror the existing assignment-brief pattern: a calibration receipt plus approval under `.grading/calibration/`, a draft review record `_review.json` in the drafts folder, and a consistency acknowledgement `_consistency.json` beside it. `grade.py next` derives new actions from those records. `GradingBatch.verify` and `VerifiedBatch.publish` enforce them, so the gates hold even when an agent runs commands out of order.

**Tech Stack:** Python 3.13 standard library only. pytest for tests. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-03-supervised-calibration-and-review-design.md`

## Global Constraints

- Version becomes `3.6.0` in `SKILL.md` metadata, `skills/humanizer/SKILL.md` metadata, `.claude-plugin/plugin.json`, and the first README release note.
- Nothing under `scripts/grading/` may load the token map. New files hold no student name, no essay text, and no instructor sentence.
- Every new artifact is written with `write_private_text` and refuses symlink path components.
- Exit codes: `calibrate.py` 0 ok, 13 malformed, 14 not approved, 15 holdout gap. `grade.py` adds 14 for an unapproved or stale calibration. Repair actions from `next` exit 12.
- Plain language in every message. Use `must` for requirements.
- Run tests with `python3 -m pytest tests/ -q` from the repo root.
- Commit after each task with a conventional message and the Co-Authored-By trailer.

---

### Task 1: Several holdouts and a blind holdout folder in `calibrate.py inputs`

**Files:**
- Modify: `scripts/grading/samples.py` (add `student_half`)
- Modify: `scripts/grading/holdout.py` (add `required_holdouts`)
- Modify: `scripts/calibrate.py:73-200` (`_inputs`, parser)
- Test: `tests/grading/test_samples.py`, `tests/grading/test_holdout.py`

**Interfaces:**
- Produces: `samples.student_half(text: str) -> str`, `holdout.required_holdouts(total_samples: int) -> int`, packet key `holdout = {"count": int, "labels": [str]}`, CLI `--holdout` repeatable, `--holdout-out DIR`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/grading/test_samples.py`:

```python
from scripts.grading.samples import student_half


def test_student_half_drops_feedback_and_every_bracketed_span(phil210):
    text = (
        phil210 / "graded/paper_5_graded_subscription_software_paradox.md"
    ).read_text(encoding="utf-8")
    half = student_half(text)
    assert "### Student Submission" in half
    assert "Instructor Feedback" not in half
    assert "**Grade**" not in half
    assert "[" not in half and "]" not in half
    assert "Inline Comments" not in half
    assert "subscription economy" in half.lower()
```

Append to `tests/grading/test_holdout.py`:

```python
from scripts.grading.holdout import required_holdouts


def test_required_holdouts_rises_to_two_at_six_samples():
    assert required_holdouts(4) == 1
    assert required_holdouts(5) == 1
    assert required_holdouts(6) == 2
    assert required_holdouts(8) == 2


def test_calibrate_inputs_needs_two_holdouts_for_five_samples(phil210, tmp_path):
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(phil210 / "graded"),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "paper_5_graded_subscription_software_paradox.md",
            "--out", str(tmp_path / "inputs.json"),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 13
    assert "two holdouts" in result.stderr.lower()
    assert not (tmp_path / "inputs.json").exists()


def test_calibrate_inputs_writes_blind_holdout_submissions(phil210, tmp_path):
    out = tmp_path / "inputs.json"
    blind = tmp_path / "blind"
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(phil210 / "graded"),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "paper_5_graded_subscription_software_paradox.md",
            "--holdout", "paper_4_graded_predictive_policing.md",
            "--holdout-out", str(blind),
            "--out", str(out),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert len(payload["samples"]) == 3
    assert payload["holdout"] == {"count": 2, "labels": ["holdout_1", "holdout_2"]}
    written = sorted(path.name for path in blind.iterdir())
    assert written == [
        "paper_4_graded_predictive_policing.md",
        "paper_5_graded_subscription_software_paradox.md",
    ]
    for path in blind.iterdir():
        text = path.read_text(encoding="utf-8")
        assert "Instructor Feedback" not in text
        assert "[" not in text
        assert path.stat().st_mode & 0o777 == 0o600
```

Change the existing assertion in `test_calibrate_inputs_writes_comments_without_essay_text` from `assert payload["holdout"] == {"label": "holdout"}` to `assert payload["holdout"] == {"count": 1, "labels": ["holdout_1"]}`.

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python3 -m pytest tests/grading/test_samples.py tests/grading/test_holdout.py -q`
Expected: FAIL with `ImportError` for `student_half` and `required_holdouts`.

- [ ] **Step 3: Implement**

In `scripts/grading/samples.py`, after `_split_sections`:

```python
_BRACKETED = re.compile(r"\s*\[[^\[\]]*\]")


def student_half(text: str) -> str:
    """Return the header and essay of a graded sample without any feedback.

    Every bracketed span is removed. The instructor's inline comments live in
    brackets, and syntax alone cannot tell them from a student's brackets, so
    the blind copy keeps neither.
    """
    header, essay, _ = _split_sections(text)
    blind_essay = _BRACKETED.sub("", essay)
    parts = []
    if header.strip():
        parts.append(header.strip("\n"))
        parts.append("### Student Submission")
    parts.append(blind_essay.strip("\n"))
    return "\n\n".join(parts) + "\n"
```

In `scripts/grading/holdout.py`, after the constants:

```python
def required_holdouts(total_samples: int) -> int:
    """Say how many holdouts a corpus of graded samples must hold back.

    One holdout cannot show agreement. Six or more graded pieces leave room
    for two.
    """
    return 2 if total_samples >= 6 else 1
```

In `scripts/calibrate.py`, import `required_holdouts` and `student_half`, plus `paths_overlap` from `scripts.path_safety`. Replace the holdout selection in `_inputs`:

```python
    paths = sorted(p for p in args.samples.rglob("*.md") if p.is_file())
    holdout_names = list(dict.fromkeys(args.holdout))
    holdout_paths: list[Path] = []
    for name in holdout_names:
        match = next((p for p in paths if p.name == name), None)
        if match is None:
            print(f"No sample named {name} under {args.samples}.", file=sys.stderr)
            return EXIT_MALFORMED
        holdout_paths.append(match)
    calibration = [p for p in paths if p not in holdout_paths]

    if len(calibration) < MIN_SAMPLES:
        ... (unchanged message)
    if len(calibration) > MAX_SAMPLES:
        ... (unchanged)
    needed = required_holdouts(len(calibration) + len(holdout_paths))
    if len(holdout_paths) < needed:
        print(
            f"{len(calibration) + len(holdout_paths)} graded samples need at "
            f"least {'two holdouts' if needed == 2 else 'one holdout'}. Name "
            f"{needed - len(holdout_paths)} more with --holdout.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    if args.holdout_out is not None:
        if paths_overlap(args.holdout_out, args.samples):
            print("--holdout-out must not sit inside the samples folder.", file=sys.stderr)
            return EXIT_MALFORMED
        if args.holdout_out.exists() and (
            not args.holdout_out.is_dir() or any(args.holdout_out.iterdir())
        ):
            print("--holdout-out must be a new or empty folder.", file=sys.stderr)
            return EXIT_MALFORMED
```

Load every holdout sample with `_require_roster_identity`, keep them in `holdout_samples`, and write the packet's holdout object as `{"count": len(holdout_paths), "labels": [f"holdout_{i}" for i in range(1, len(holdout_paths) + 1)]}`. After writing the packet, when `--holdout-out` is set:

```python
    if args.holdout_out is not None:
        for path in holdout_paths:
            text = read_text_limited(path, "The holdout sample", 10 * 1024 * 1024)
            write_private_text(args.holdout_out / path.name, student_half(text))
        print(
            f"Wrote {len(holdout_paths)} blind holdout submissions to "
            f"{args.holdout_out}. Grade them as a holdout batch."
        )
```

Parser: `inputs.add_argument("--holdout", action="append", required=True, help="Filename to hold back; repeat for each holdout")` and `inputs.add_argument("--holdout-out", type=Path, default=None, help="Write each holdout's student half here for blind grading")`. Update the print line to say `f"{len(holdout_paths)} held back."`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_samples.py tests/grading/test_holdout.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/grading/samples.py scripts/grading/holdout.py scripts/calibrate.py tests/grading/test_samples.py tests/grading/test_holdout.py
git commit -m "feat: hold back several samples and write blind holdout copies"
```

---

### Task 2: Profile tolerances and the instructor-answers section

**Files:**
- Modify: `scripts/grading/profile.py`
- Modify: `scripts/grading/holdout.py` (`compare` takes tolerances)
- Modify: `tests/fixtures/phil210/profile_phil210.md`, `.grading/profiles/phil210.md`
- Test: `tests/grading/test_profile.py`, `tests/grading/test_holdout.py`

**Interfaces:**
- Produces: `Profile.point_tolerance: int`, `Profile.length_tolerance: float`, required section `Instructor answers`, `compare(record, feedback, sample, *, point_tolerance=5, length_tolerance=2.0)`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/grading/test_profile.py`:

```python
def test_it_reads_default_tolerances(profile):
    loaded = load_profile(profile)
    assert loaded.point_tolerance == 5
    assert loaded.length_tolerance == 2.0


def test_it_reads_explicit_tolerances(profile, tmp_path):
    text = profile.read_text(encoding="utf-8").replace(
        "**Samples**: 4",
        "**Samples**: 4\n**Point tolerance**: 3\n**Length tolerance**: 1.5",
    )
    edited = tmp_path / "phil210.md"
    edited.write_text(text, encoding="utf-8")
    loaded = load_profile(edited)
    assert loaded.point_tolerance == 3
    assert loaded.length_tolerance == 1.5


def test_a_bad_tolerance_is_refused(profile, tmp_path):
    text = profile.read_text(encoding="utf-8").replace(
        "**Samples**: 4", "**Samples**: 4\n**Length tolerance**: 0.5"
    )
    edited = tmp_path / "phil210.md"
    edited.write_text(text, encoding="utf-8")
    with pytest.raises(ProfileError, match="Length tolerance"):
        load_profile(edited)


def test_instructor_answers_is_required(profile, tmp_path):
    text = profile.read_text(encoding="utf-8").replace("## Instructor answers", "## Notes")
    edited = tmp_path / "phil210.md"
    edited.write_text(text, encoding="utf-8")
    with pytest.raises(ProfileError, match="Instructor answers"):
        load_profile(edited)
```

Append to `tests/grading/test_holdout.py`:

```python
def test_compare_uses_the_supplied_point_tolerance(phil210):
    sample = load_sample(phil210 / PAPER_5)
    loose = compare(_record(87, "B"), "Solid.", sample, point_tolerance=5)
    tight = compare(_record(87, "B"), "Solid.", sample, point_tolerance=2)
    assert not any("points" in f for f in loose.findings)
    assert any("points" in f for f in tight.findings)
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python3 -m pytest tests/grading/test_profile.py tests/grading/test_holdout.py -q`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement**

`scripts/grading/profile.py`: add `"Instructor answers"` to `REQUIRED_SECTIONS`; add fields `point_tolerance: int = 5` and `length_tolerance: float = 2.0` to `Profile`; add:

```python
DEFAULT_POINT_TOLERANCE = 5
DEFAULT_LENGTH_TOLERANCE = 2.0


def _tolerance(meta: dict[str, str], name: str, kind, default, low, high, path: Path):
    raw = meta.get(name)
    if raw is None:
        return default
    try:
        value = kind(raw)
    except ValueError as error:
        raise ProfileError(f"{path.name} has an unreadable '**{name}**:' value.") from error
    if not low <= value <= high:
        raise ProfileError(
            f"{path.name} '**{name}**:' must be between {low} and {high}."
        )
    return value
```

In `load_profile`, before `return Profile(...)`:

```python
    point_tolerance = _tolerance(meta, "Point tolerance", int, DEFAULT_POINT_TOLERANCE, 0, 100, path)
    length_tolerance = _tolerance(meta, "Length tolerance", float, DEFAULT_LENGTH_TOLERANCE, 1.0, 10.0, path)
```

and pass both into `Profile`. `1.0` fails the `0.5` test because `0.5 < 1.0`; make the lower bound exclusive by checking `low < value` for floats: implement as `if value < low or value > high or (kind is float and value == low)`.

`scripts/grading/holdout.py`: change the signature to `compare(record, feedback, sample, *, point_tolerance: int = POINT_TOLERANCE, length_tolerance: float = LENGTH_TOLERANCE)` and use the parameters in place of the constants.

Add this section to the end of `tests/fixtures/phil210/profile_phil210.md` and copy the file to `.grading/profiles/phil210.md`:

```markdown
## Instructor answers

The calibration interview, in the instructor's words. No student is named.

- **Where does an A begin, and what does one do that a B does not?** An A
  begins at 93. It uses a named theorist as an analytical tool. A B uses the
  theorist as decoration.
- **Which criterion do you forgive, and which do you never forgive?** I
  forgive prose slips when the argument holds. I never forgive a missing
  citation.
- **When work is failing, do you say so plainly or soften it?** Plainly. A D
  is given, not softened.
- **Is there anything you would never write to a student?** Nothing that
  sounds like a chatbot, and never an empty compliment.
- **How long should feedback be?** 180 to 320 words.
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_profile.py tests/grading/test_holdout.py tests/grading/test_verify.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/grading/profile.py scripts/grading/holdout.py tests/fixtures/phil210/profile_phil210.md tests/grading/test_profile.py tests/grading/test_holdout.py
git commit -m "feat: read profile tolerances and require instructor answers"
```

---

### Task 3: Calibration receipt, aggregate report, and profile approval

**Files:**
- Create: `scripts/grading/calibration.py`
- Modify: `scripts/grading/holdout.py` (aggregate and report rendering)
- Modify: `scripts/calibrate.py` (`holdout`, `approve`, `check`)
- Test: `tests/grading/test_calibration.py` (new), `tests/grading/test_holdout.py`

**Interfaces:**
- Produces:
  - `holdout.Aggregate` dataclass, `holdout.aggregate(deltas, *, required) -> Aggregate`, `holdout.render_report(deltas, summary) -> str`.
  - `calibration.RECEIPT_SCHEMA = "calibration-receipt/1"`, `APPROVAL_SCHEMA = "calibration-approval/1"`, `DEMO_AUTHORIZATION = "synthetic-demo"`, `DEMO_MARKER = "demo.json"`.
  - `calibration.receipt_path(root, course)`, `approval_path(root, course)`, `approval_phrase(course)`.
  - `calibration.write_receipt(path, *, course, profile_sha256, rubric_sha256, packet_sha256, samples, deltas, summary, tolerances) -> CalibrationReceipt`, `load_receipt(path)`.
  - `calibration.approve_calibration(root, *, course, profile, rubric, approval, accept_gap=False, reason="") -> CalibrationApproval`, `approve_synthetic(root, *, course, profile, rubric)`, `load_approval(path)`.
  - `calibration.check_calibration(root, *, course, profile, rubric) -> CalibrationStatus` with `state` in `current`, `no-receipt`, `stale-receipt`, `malformed-receipt`, `gap`, `unapproved`, `stale-approval`, `malformed-approval`.
  - `calibration.demo_authorized(root) -> bool`.
  - `calibration.CalibrationError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/grading/test_calibration.py`:

```python
"""A profile grades nothing until holdouts measured it and the instructor approved it."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.grading.calibration import (
    CalibrationError,
    approval_path,
    approval_phrase,
    approve_calibration,
    approve_synthetic,
    check_calibration,
    load_receipt,
    receipt_path,
    write_receipt,
)
from scripts.grading.holdout import Delta, aggregate, compare
from scripts.grading.records import CriterionScore, GradeRecord
from scripts.grading.samples import load_sample

PAPER_4 = "graded/paper_4_graded_predictive_policing.md"
PAPER_5 = "graded/paper_5_graded_subscription_software_paradox.md"


def _record(total: int, letter: str, token: str = "STUDENT_01") -> GradeRecord:
    return GradeRecord(
        batch="holdout-phil210",
        token=token,
        source=f"redacted/{token}.md",
        course="phil210",
        rubric="tests/fixtures/phil210/grading_rubric.md",
        profile=".grading/profiles/phil210.md",
        criteria=(CriterionScore(1, "Thesis", 25, total // 4, "Proficient", True, ()),),
        total=total,
        total_possible=100,
        letter=letter,
        pattern_density={},
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _install_profile(root: Path, profile: Path) -> Path:
    target = root / "profiles" / "phil210.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(profile.read_bytes())
    return target


def _deltas(phil210, totals):
    sample5 = load_sample(phil210 / PAPER_5)
    sample4 = load_sample(phil210 / PAPER_4)
    feedback5 = sample5.bottom_line
    feedback4 = sample4.bottom_line
    return [
        compare(_record(totals[0], "B", "STUDENT_01"), feedback5, sample5),
        compare(_record(totals[1], sample4.letter, "STUDENT_02"), feedback4, sample4),
    ]


def _write(root, phil210, profile_file, *, totals=(83, None)):
    sample4 = load_sample(phil210 / PAPER_4)
    totals = (totals[0], sample4.score if totals[1] is None else totals[1])
    deltas = _deltas(phil210, totals)
    summary = aggregate(deltas, required=2)
    return write_receipt(
        receipt_path(root, "phil210"),
        course="phil210",
        profile_sha256=_sha(profile_file),
        rubric_sha256=_sha(phil210 / "grading_rubric.md"),
        packet_sha256="a" * 64,
        samples=3,
        deltas=deltas,
        summary=summary,
        tolerances={"points": 5, "length_ratio": 2.0},
    )


def test_aggregate_summarizes_every_holdout(phil210):
    sample4 = load_sample(phil210 / PAPER_4)
    deltas = _deltas(phil210, (86, sample4.score + 2))
    summary = aggregate(deltas, required=2)
    assert summary.holdouts == 2
    assert summary.mean_abs_points == 2.5
    assert summary.max_abs_points == 3
    assert summary.letter_match_rate == 1.0
    assert summary.phrase_carry_rate == 1.0
    assert summary.passed is True


def test_aggregate_fails_when_a_holdout_has_a_finding(phil210):
    sample4 = load_sample(phil210 / PAPER_4)
    deltas = _deltas(phil210, (95, sample4.score))
    assert aggregate(deltas, required=2).passed is False


def test_aggregate_fails_when_too_few_holdouts(phil210):
    deltas = _deltas(phil210, (83, load_sample(phil210 / PAPER_4).score))[:1]
    assert aggregate(deltas, required=2).passed is False


def test_receipt_round_trips_and_holds_no_instructor_sentence(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    receipt = _write(root, phil210, installed)
    loaded = load_receipt(receipt_path(root, "phil210"))
    assert loaded.passed is True
    assert loaded.samples == 3
    assert loaded.required_holdouts == 2
    assert len(loaded.holdouts) == 2
    blob = (receipt_path(root, "phil210")).read_text(encoding="utf-8")
    sample = load_sample(phil210 / PAPER_5)
    assert sample.bottom_line[:30] not in blob
    assert "David" not in blob and "Morales" not in blob
    assert receipt_path(root, "phil210").stat().st_mode & 0o777 == 0o600


def test_check_reports_no_receipt_then_unapproved_then_current(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "no-receipt"
    _write(root, phil210, installed)
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "unapproved"
    approve_calibration(
        root, course="phil210", profile=installed, rubric=rubric,
        approval=approval_phrase("phil210"),
    )
    status = check_calibration(root, course="phil210", profile=installed, rubric=rubric)
    assert status.state == "current"
    assert approval_path(root, "phil210").stat().st_mode & 0o777 == 0o600


def test_approval_requires_the_exact_phrase(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    _write(root, phil210, installed)
    with pytest.raises(CalibrationError, match="exactly"):
        approve_calibration(
            root, course="phil210", profile=installed,
            rubric=phil210 / "grading_rubric.md", approval="yes",
        )


def test_a_gap_needs_an_accepted_reason(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    _write(root, phil210, installed, totals=(96, None))
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "gap"
    with pytest.raises(CalibrationError, match="accept-gap"):
        approve_calibration(
            root, course="phil210", profile=installed, rubric=rubric,
            approval=approval_phrase("phil210"),
        )
    with pytest.raises(CalibrationError, match="reason"):
        approve_calibration(
            root, course="phil210", profile=installed, rubric=rubric,
            approval=approval_phrase("phil210"), accept_gap=True, reason="  ",
        )
    approval = approve_calibration(
        root, course="phil210", profile=installed, rubric=rubric,
        approval=approval_phrase("phil210"), accept_gap=True,
        reason="The holdout was an unusual paper.",
    )
    assert approval.gap_accepted is True
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "current"


def test_editing_the_profile_makes_the_receipt_and_approval_stale(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    _write(root, phil210, installed)
    approve_calibration(root, course="phil210", profile=installed, rubric=rubric, approval=approval_phrase("phil210"))
    installed.write_text(installed.read_text(encoding="utf-8") + "\n- extra\n", encoding="utf-8")
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "stale-receipt"


def test_a_malformed_receipt_is_reported(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    path = receipt_path(root, "phil210")
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    assert check_calibration(root, course="phil210", profile=installed, rubric=phil210 / "grading_rubric.md").state == "malformed-receipt"


def test_synthetic_approval_needs_the_demo_marker(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    approve_synthetic(root, course="phil210", profile=installed, rubric=rubric)
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "unapproved"
    (root / "demo.json").write_text(json.dumps({"authorization": "synthetic-demo"}), encoding="utf-8")
    assert check_calibration(root, course="phil210", profile=installed, rubric=rubric).state == "current"


def _calibrate(*args):
    return subprocess.run([sys.executable, "scripts/calibrate.py", *args], capture_output=True, text=True)


def _packet(tmp_path, phil210):
    out = tmp_path / "inputs.json"
    result = _calibrate(
        "inputs", "--rubric", str(phil210 / "grading_rubric.md"),
        "--samples", str(phil210 / "graded"), "--roster", str(phil210 / "roster.csv"),
        "--holdout", "paper_5_graded_subscription_software_paradox.md",
        "--holdout", "paper_4_graded_predictive_policing.md", "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    return out


def _holdout_pair(drafts: Path, token: str, total: int, letter: str, feedback: str):
    _record(total, letter, token).save(drafts / f"{token}.json")
    (drafts / f"{token}.md").write_text(feedback, encoding="utf-8")


def test_holdout_cli_writes_a_receipt_and_exits_by_outcome(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    text = installed.read_text(encoding="utf-8").replace("**Samples**: 4", "**Samples**: 3")
    installed.write_text(text, encoding="utf-8")
    packet = _packet(tmp_path, phil210)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    sample5 = load_sample(phil210 / PAPER_5)
    sample4 = load_sample(phil210 / PAPER_4)
    _holdout_pair(drafts, "STUDENT_01", sample5.score + 1, sample5.letter, sample5.bottom_line)
    _holdout_pair(drafts, "STUDENT_02", sample4.score, sample4.letter, sample4.bottom_line)
    common = [
        "holdout", "--course", "phil210", "--rubric", str(phil210 / "grading_rubric.md"),
        "--packet", str(packet), "--root", str(root),
        "--record", str(drafts / "STUDENT_01.json"), "--draft", str(drafts / "STUDENT_01.md"),
        "--sample", str(phil210 / PAPER_5),
        "--record", str(drafts / "STUDENT_02.json"), "--draft", str(drafts / "STUDENT_02.md"),
        "--sample", str(phil210 / PAPER_4),
    ]
    result = _calibrate(*common)
    assert result.returncode == 0, result.stderr
    assert "2 holdouts" in result.stdout
    assert load_receipt(receipt_path(root, "phil210")).passed is True

    _holdout_pair(drafts, "STUDENT_01", 99, "A", sample5.bottom_line)
    result = _calibrate(*common)
    assert result.returncode == 15
    assert load_receipt(receipt_path(root, "phil210")).passed is False

    result = _calibrate(*common[:-6])
    assert result.returncode == 13
    assert "2 holdouts" in result.stderr


def test_holdout_cli_refuses_a_sample_count_mismatch(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    _install_profile(root, profile)
    packet = _packet(tmp_path, phil210)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    sample5 = load_sample(phil210 / PAPER_5)
    _holdout_pair(drafts, "STUDENT_01", sample5.score, sample5.letter, "x")
    result = _calibrate(
        "holdout", "--course", "phil210", "--rubric", str(phil210 / "grading_rubric.md"),
        "--packet", str(packet), "--root", str(root),
        "--record", str(drafts / "STUDENT_01.json"), "--draft", str(drafts / "STUDENT_01.md"),
        "--sample", str(phil210 / PAPER_5),
        "--record", str(drafts / "STUDENT_01.json"), "--draft", str(drafts / "STUDENT_01.md"),
        "--sample", str(phil210 / PAPER_4),
    )
    assert result.returncode == 13
    assert "Samples" in result.stderr


def test_approve_and_check_cli(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = str(phil210 / "grading_rubric.md")
    check = _calibrate("check", "--course", "phil210", "--rubric", rubric, "--root", str(root))
    assert check.returncode == 14
    _write(root, phil210, installed)
    approve = _calibrate(
        "approve", "--course", "phil210", "--rubric", rubric, "--root", str(root),
        "--approval", "Approve calibration profile for phil210",
    )
    assert approve.returncode == 0, approve.stderr
    check = _calibrate("check", "--course", "phil210", "--rubric", rubric, "--root", str(root))
    assert check.returncode == 0, check.stderr
```

- [ ] **Step 2: Run the tests to confirm they fail**

Run: `python3 -m pytest tests/grading/test_calibration.py -q`
Expected: FAIL with `ModuleNotFoundError: scripts.grading.calibration`.

- [ ] **Step 3: Implement `holdout.py` aggregate and report**

Append to `scripts/grading/holdout.py`:

```python
@dataclass(frozen=True)
class Aggregate:
    """How the profile did across every holdout together."""

    holdouts: int
    required: int
    mean_abs_points: float
    max_abs_points: int
    letter_match_rate: float
    phrase_carry_rate: float
    length_ratio_low: float
    length_ratio_high: float
    passed: bool


def aggregate(deltas: Sequence[Delta], *, required: int) -> Aggregate:
    """Summarize holdout deltas. Passing means no holdout had a finding."""
    count = len(deltas)
    if count == 0:
        return Aggregate(0, required, 0.0, 0, 0.0, 0.0, 0.0, 0.0, False)
    abs_points = [abs(delta.points) for delta in deltas]
    ratios = [delta.length_ratio for delta in deltas]
    return Aggregate(
        holdouts=count,
        required=required,
        mean_abs_points=round(sum(abs_points) / count, 2),
        max_abs_points=max(abs_points),
        letter_match_rate=round(sum(delta.letter_match for delta in deltas) / count, 2),
        phrase_carry_rate=round(
            sum(bool(delta.shared_phrases) for delta in deltas) / count, 2
        ),
        length_ratio_low=min(ratios),
        length_ratio_high=max(ratios),
        passed=count >= required and all(not delta.findings for delta in deltas),
    )


def render_report(deltas: Sequence[Delta], summary: Aggregate) -> str:
    """Render every holdout and the aggregate as the report the instructor reads."""
    lines = ["# Holdout report", ""]
    for delta in deltas:
        lines += render(delta).splitlines()[2:-4]
        lines.append("")
    lines += [
        "## Agreement across holdouts",
        "",
        f"| Measure | Value |",
        f"| --- | --- |",
        f"| Holdouts | {summary.holdouts} of {summary.required} required |",
        f"| Mean score gap | {summary.mean_abs_points} points |",
        f"| Largest score gap | {summary.max_abs_points} points |",
        f"| Letter matches | {int(summary.letter_match_rate * 100)}% |",
        f"| Holdouts with the instructor's phrasing | {int(summary.phrase_carry_rate * 100)}% |",
        f"| Length ratio range | {summary.length_ratio_low} to {summary.length_ratio_high} |",
        "",
        "Result: " + ("passed. The profile is worth grading with." if summary.passed else "gap. Edit the profile and run the holdouts again, or accept the gap with a reason."),
        "",
        "This report judges the profile, not the students.",
        "",
    ]
    return "\n".join(lines)
```

Add `from collections.abc import Sequence` to the imports.

- [ ] **Step 4: Implement `scripts/grading/calibration.py`**

```python
"""Bind a calibration profile to the holdouts that measured it and the instructor who approved it.

The receipt is identity-free: digests, counts, and numbers. The approval binds
the exact profile, rubric, and receipt bytes, so any edit needs approval again.
Neither file authenticates a person. They are workflow evidence, like the
assignment brief.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.grading.holdout import Aggregate, Delta
from scripts.path_safety import has_symlink_component, read_bytes_limited, require_component
from scripts.redaction.private_files import write_private_text

RECEIPT_SCHEMA = "calibration-receipt/1"
APPROVAL_SCHEMA = "calibration-approval/1"
DEMO_MARKER = "demo.json"
DEMO_AUTHORIZATION = "synthetic-demo"
MIN_SAMPLES = 3
FILE_LIMIT = 1_048_576
BOUND_LIMIT = 5 * 1024 * 1024


class CalibrationError(ValueError):
    """The calibration receipt or approval is missing, malformed, or stale."""


def receipt_path(root: Path, course: str) -> Path:
    require_component(course, "The course")
    return root / "calibration" / f"{course}.receipt.json"


def approval_path(root: Path, course: str) -> Path:
    require_component(course, "The course")
    return root / "calibration" / f"{course}.approval.json"


def approval_phrase(course: str) -> str:
    return f"Approve calibration profile for {course}"


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(path: Path, label: str) -> str:
    if has_symlink_component(path):
        raise CalibrationError(f"{label} must not use a symbolic link.")
    try:
        return hashlib.sha256(read_bytes_limited(path, label, BOUND_LIMIT)).hexdigest()
    except (OSError, ValueError) as error:
        raise CalibrationError(str(error)) from error


def _hex(value: object, name: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise CalibrationError(f"{name} must be a lowercase SHA-256 hash.")
    return value


@dataclass(frozen=True)
class CalibrationReceipt:
    path: Path
    course: str
    profile_sha256: str
    rubric_sha256: str
    packet_sha256: str
    samples: int
    required_holdouts: int
    holdouts: tuple[dict, ...]
    aggregate: dict
    tolerances: dict
    passed: bool
    created_at: str
    source_sha256: str

    @property
    def findings(self) -> tuple[str, ...]:
        return tuple(f for item in self.holdouts for f in item.get("findings", ()))


def write_receipt(path, *, course, profile_sha256, rubric_sha256, packet_sha256, samples, deltas, summary, tolerances) -> CalibrationReceipt:
    holdouts = []
    for delta in deltas:
        item = asdict(delta)
        item["shared_phrases"] = len(delta.shared_phrases)
        holdouts.append(item)
    payload = {
        "schema": RECEIPT_SCHEMA,
        "course": course,
        "hash": "sha256",
        "profile_sha256": profile_sha256,
        "rubric_sha256": rubric_sha256,
        "packet_sha256": packet_sha256,
        "samples": samples,
        "required_holdouts": summary.required,
        "holdouts": holdouts,
        "aggregate": {k: v for k, v in asdict(summary).items() if k not in {"required", "passed"}},
        "tolerances": dict(tolerances),
        "passed": summary.passed,
        "created_at": _now(),
    }
    if has_symlink_component(path):
        raise CalibrationError("The calibration receipt path must not use a symbolic link.")
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return load_receipt(path)


def load_receipt(path: Path) -> CalibrationReceipt:
    if has_symlink_component(path) or not path.is_file():
        raise CalibrationError("The calibration receipt must be one regular file.")
    try:
        raw = read_bytes_limited(path, "The calibration receipt", FILE_LIMIT)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise CalibrationError(f"The calibration receipt is unreadable: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != RECEIPT_SCHEMA:
        raise CalibrationError(f"The calibration receipt must use schema {RECEIPT_SCHEMA}.")
    course = payload.get("course")
    if not isinstance(course, str):
        raise CalibrationError("The calibration receipt needs a course.")
    require_component(course, "The course")
    holdouts = payload.get("holdouts")
    aggregate = payload.get("aggregate")
    tolerances = payload.get("tolerances")
    if not isinstance(holdouts, list) or not all(isinstance(i, dict) for i in holdouts):
        raise CalibrationError("The calibration receipt holdouts must be a list of objects.")
    if not isinstance(aggregate, dict) or not isinstance(tolerances, dict):
        raise CalibrationError("The calibration receipt needs aggregate and tolerances objects.")
    for name in ("samples", "required_holdouts"):
        if not isinstance(payload.get(name), int) or isinstance(payload.get(name), bool):
            raise CalibrationError(f"The calibration receipt {name} must be an integer.")
    if not isinstance(payload.get("passed"), bool):
        raise CalibrationError("The calibration receipt passed value must be a boolean.")
    return CalibrationReceipt(
        path=path,
        course=course,
        profile_sha256=_hex(payload.get("profile_sha256"), "profile_sha256"),
        rubric_sha256=_hex(payload.get("rubric_sha256"), "rubric_sha256"),
        packet_sha256=_hex(payload.get("packet_sha256"), "packet_sha256"),
        samples=payload["samples"],
        required_holdouts=payload["required_holdouts"],
        holdouts=tuple(holdouts),
        aggregate=aggregate,
        tolerances=tolerances,
        passed=payload["passed"],
        created_at=str(payload.get("created_at", "")),
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True)
class CalibrationApproval:
    path: Path
    course: str
    profile_sha256: str
    rubric_sha256: str
    receipt_sha256: str
    approval: str
    approved_at: str
    gap_accepted: bool
    reason: str
    synthetic: bool


def _write_approval(path, *, course, profile_sha256, rubric_sha256, receipt_sha256, approval, gap_accepted, reason, synthetic) -> CalibrationApproval:
    payload = {
        "schema": APPROVAL_SCHEMA,
        "course": course,
        "hash": "sha256",
        "profile_sha256": profile_sha256,
        "rubric_sha256": rubric_sha256,
        "receipt_sha256": receipt_sha256,
        "approval": approval,
        "approved_at": _now(),
        "gap_accepted": gap_accepted,
        "reason": reason,
        "synthetic": synthetic,
    }
    if has_symlink_component(path):
        raise CalibrationError("The calibration approval path must not use a symbolic link.")
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return load_approval(path)


def load_approval(path: Path) -> CalibrationApproval:
    if has_symlink_component(path) or not path.is_file():
        raise CalibrationError("The calibration approval must be one regular file.")
    try:
        payload = json.loads(read_bytes_limited(path, "The calibration approval", FILE_LIMIT).decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise CalibrationError(f"The calibration approval is unreadable: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != APPROVAL_SCHEMA:
        raise CalibrationError(f"The calibration approval must use schema {APPROVAL_SCHEMA}.")
    course = payload.get("course")
    if not isinstance(course, str):
        raise CalibrationError("The calibration approval needs a course.")
    require_component(course, "The course")
    synthetic = payload.get("synthetic")
    gap_accepted = payload.get("gap_accepted")
    if not isinstance(synthetic, bool) or not isinstance(gap_accepted, bool):
        raise CalibrationError("The calibration approval flags must be booleans.")
    approval = payload.get("approval")
    if approval != approval_phrase(course):
        raise CalibrationError(f"The calibration approval must read exactly: {approval_phrase(course)}")
    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        raise CalibrationError("The calibration approval reason must be text.")
    return CalibrationApproval(
        path=path,
        course=course,
        profile_sha256=_hex(payload.get("profile_sha256"), "profile_sha256"),
        rubric_sha256=_hex(payload.get("rubric_sha256"), "rubric_sha256"),
        receipt_sha256=_hex(payload.get("receipt_sha256"), "receipt_sha256", allow_empty=synthetic),
        approval=approval,
        approved_at=str(payload.get("approved_at", "")),
        gap_accepted=gap_accepted,
        reason=reason,
        synthetic=synthetic,
    )


def approve_calibration(root, *, course, profile, rubric, approval, accept_gap=False, reason="") -> CalibrationApproval:
    require_component(course, "The course")
    if approval != approval_phrase(course):
        raise CalibrationError(f"Approval must read exactly: {approval_phrase(course)}")
    profile_sha256 = _digest(profile, "The calibration profile")
    rubric_sha256 = _digest(rubric, "The rubric")
    receipt = load_receipt(receipt_path(root, course))
    if receipt.course != course:
        raise CalibrationError("The calibration receipt belongs to another course.")
    if receipt.profile_sha256 != profile_sha256 or receipt.rubric_sha256 != rubric_sha256:
        raise CalibrationError("The calibration receipt does not match the current profile and rubric. Run the holdouts again.")
    if receipt.samples < MIN_SAMPLES:
        raise CalibrationError(f"The calibration receipt records {receipt.samples} samples. Calibration needs at least {MIN_SAMPLES}.")
    if len(receipt.holdouts) < receipt.required_holdouts:
        raise CalibrationError(f"The calibration receipt records {len(receipt.holdouts)} holdouts of {receipt.required_holdouts} required.")
    if not receipt.passed:
        if not accept_gap:
            raise CalibrationError("The holdouts did not pass. Edit the profile and run them again, or pass --accept-gap with a reason.")
        if not reason.strip():
            raise CalibrationError("Accepting a holdout gap needs a non-empty reason.")
    return _write_approval(
        approval_path(root, course),
        course=course, profile_sha256=profile_sha256, rubric_sha256=rubric_sha256,
        receipt_sha256=receipt.source_sha256, approval=approval,
        gap_accepted=not receipt.passed, reason=reason.strip() if not receipt.passed else "",
        synthetic=False,
    )


def approve_synthetic(root, *, course, profile, rubric) -> CalibrationApproval:
    """Record the bundled demo's synthetic approval. Only demo preparation calls this."""
    require_component(course, "The course")
    return _write_approval(
        approval_path(root, course),
        course=course,
        profile_sha256=_digest(profile, "The calibration profile"),
        rubric_sha256=_digest(rubric, "The rubric"),
        receipt_sha256="", approval=approval_phrase(course),
        gap_accepted=False, reason="", synthetic=True,
    )


def demo_authorized(root: Path) -> bool:
    marker = root / DEMO_MARKER
    if has_symlink_component(marker) or not marker.is_file():
        return False
    try:
        payload = json.loads(read_bytes_limited(marker, "The demo marker", FILE_LIMIT).decode("utf-8"))
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("authorization") == DEMO_AUTHORIZATION


@dataclass(frozen=True)
class CalibrationStatus:
    state: str
    message: str
    receipt: CalibrationReceipt | None = None
    approval: CalibrationApproval | None = None


def check_calibration(root, *, course, profile, rubric) -> CalibrationStatus:
    require_component(course, "The course")
    profile_sha256 = _digest(profile, "The calibration profile")
    rubric_sha256 = _digest(rubric, "The rubric")
    approval = None
    approval_file = approval_path(root, course)
    if approval_file.exists() or approval_file.is_symlink():
        try:
            approval = load_approval(approval_file)
        except CalibrationError as error:
            return CalibrationStatus("malformed-approval", str(error))
        if approval.course != course:
            return CalibrationStatus("malformed-approval", "The calibration approval belongs to another course.")
    if approval is not None and approval.synthetic:
        if not demo_authorized(root):
            return CalibrationStatus("unapproved", "A synthetic calibration approval is valid only inside a prepared demo run.", approval=approval)
        if approval.profile_sha256 != profile_sha256 or approval.rubric_sha256 != rubric_sha256:
            return CalibrationStatus("stale-approval", "The synthetic profile or rubric changed after demo preparation.", approval=approval)
        return CalibrationStatus("current", "Synthetic demo calibration is current.", approval=approval)
    receipt_file = receipt_path(root, course)
    if not receipt_file.exists() and not receipt_file.is_symlink():
        return CalibrationStatus("no-receipt", "No holdout has measured this profile. Grade the holdout batch and run calibrate.py holdout.")
    try:
        receipt = load_receipt(receipt_file)
    except CalibrationError as error:
        return CalibrationStatus("malformed-receipt", str(error))
    if receipt.course != course:
        return CalibrationStatus("malformed-receipt", "The calibration receipt belongs to another course.")
    if receipt.profile_sha256 != profile_sha256 or receipt.rubric_sha256 != rubric_sha256:
        return CalibrationStatus("stale-receipt", "The profile or rubric changed after the holdouts ran. Run the holdouts again.", receipt=receipt)
    def approval_matches() -> bool:
        return (
            approval is not None
            and approval.profile_sha256 == profile_sha256
            and approval.rubric_sha256 == rubric_sha256
            and approval.receipt_sha256 == receipt.source_sha256
        )
    if not receipt.passed:
        if approval_matches() and approval.gap_accepted:
            return CalibrationStatus("current", "The instructor accepted the holdout gap.", receipt, approval)
        return CalibrationStatus("gap", "The holdouts did not pass. Show the instructor the report.", receipt, approval)
    if approval is None:
        return CalibrationStatus("unapproved", "The holdouts passed. The instructor must approve the profile.", receipt)
    if not approval_matches():
        return CalibrationStatus("stale-approval", "The profile, rubric, or receipt changed after approval. Approve again.", receipt, approval)
    return CalibrationStatus("current", "Calibration is approved and current.", receipt, approval)
```

- [ ] **Step 5: Implement the `calibrate.py` commands**

Replace `_holdout` and add `_approve`, `_check`:

```python
EXIT_UNAPPROVED = 14
EXIT_GAP = 15


def _holdout(args: argparse.Namespace) -> int:
    if not (len(args.record) == len(args.draft) == len(args.sample)):
        print("Pass one --record, one --draft, and one --sample per holdout.", file=sys.stderr)
        return EXIT_MALFORMED
    profile = load_profile(profile_path(args.course, args.root))
    if profile.course != args.course:
        print(f"The profile belongs to {profile.course!r}, not {args.course!r}.", file=sys.stderr)
        return EXIT_MALFORMED
    rubric = load_rubric(args.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        print("The rubric changed since calibration: " + "; ".join(drifted), file=sys.stderr)
        return EXIT_MALFORMED
    packet_bytes = read_bytes_limited(args.packet, "The calibration packet", 10 * 1024 * 1024)
    packet = json.loads(packet_bytes.decode("utf-8"))
    packet_rubric = packet.get("rubric", {}).get("digest")
    if packet_rubric != rubric.digest or profile.rubric_digest != rubric.digest:
        print("The packet, profile, and rubric digests must all match. Build the packet and profile from the current rubric.", file=sys.stderr)
        return EXIT_MALFORMED
    samples = packet.get("samples")
    holdout = packet.get("holdout", {})
    if not isinstance(samples, list) or not isinstance(holdout.get("count"), int):
        print("The calibration packet is malformed. Build it again with calibrate.py inputs.", file=sys.stderr)
        return EXIT_MALFORMED
    if profile.samples != len(samples):
        print(f"The profile says **Samples**: {profile.samples} but the packet holds {len(samples)}. Fix the profile header.", file=sys.stderr)
        return EXIT_MALFORMED
    if len(args.record) != holdout["count"]:
        print(f"The packet held back {holdout['count']} holdouts; pass exactly {holdout['count']} holdouts.", file=sys.stderr)
        return EXIT_MALFORMED
    deltas = []
    for record_path, draft_path, sample_path in zip(args.record, args.draft, args.sample):
        record = GradeRecord.load(record_path)
        if record.course != args.course:
            print(f"{record_path} belongs to course {record.course!r}.", file=sys.stderr)
            return EXIT_MALFORMED
        feedback = read_text_limited(draft_path, "The holdout draft", 1_048_576)
        sample = load_sample(sample_path)
        deltas.append(compare(record, feedback, sample, point_tolerance=profile.point_tolerance, length_tolerance=profile.length_tolerance))
    summary = aggregate(deltas, required=required_holdouts(len(samples) + holdout["count"]))
    receipt = write_receipt(
        receipt_path(args.root, args.course),
        course=args.course,
        profile_sha256=hashlib.sha256(read_bytes_limited(profile.path, "The calibration profile", 5 * 1024 * 1024)).hexdigest(),
        rubric_sha256=hashlib.sha256(read_bytes_limited(rubric.path, "The rubric", 5 * 1024 * 1024)).hexdigest(),
        packet_sha256=hashlib.sha256(packet_bytes).hexdigest(),
        samples=len(samples), deltas=deltas, summary=summary,
        tolerances={"points": profile.point_tolerance, "length_ratio": profile.length_tolerance},
    )
    if args.json:
        print(json.dumps({"receipt": str(receipt.path), "passed": receipt.passed, "aggregate": receipt.aggregate, "holdouts": [asdict(d) for d in deltas]}, indent=2, ensure_ascii=False))
    else:
        print(render_report(deltas, summary))
        print(f"Wrote {receipt.path}.")
    return EXIT_OK if receipt.passed else EXIT_GAP
```

Note that `rubric.path` exists on the Rubric object (used by `grade.py`). The "2 holdouts" string the test expects comes from the report table line `| Holdouts | 2 of 2 required |`; make the plain print also say `f"{summary.holdouts} holdouts measured."` before `Wrote`.

```python
def _approve(args):
    profile = load_profile(profile_path(args.course, args.root))
    try:
        approval = approve_calibration(args.root, course=args.course, profile=profile.path, rubric=args.rubric, approval=args.approval, accept_gap=args.accept_gap, reason=args.reason or "")
    except CalibrationError as error:
        print(f"ERROR calibration: {error}", file=sys.stderr)
        return EXIT_UNAPPROVED
    print(f"Approved calibration profile: {approval.path}")
    return EXIT_OK


def _check(args):
    profile = load_profile(profile_path(args.course, args.root))
    status = check_calibration(args.root, course=args.course, profile=profile.path, rubric=args.rubric)
    if status.state != "current":
        print(f"ERROR calibration {status.state}: {status.message}", file=sys.stderr)
        return EXIT_UNAPPROVED
    print(f"Calibration for {args.course} is approved and current.")
    return EXIT_OK
```

Parser: `holdout` gains `--course` (required, component-checked), `--rubric` (required, Path), `--packet` (required, Path), `--root` (Path, default `.grading`), `--record/--draft/--sample` with `action="append", required=True`. `approve` takes `--course --rubric --approval --accept-gap (store_true) --reason --root`. `check` takes `--course --rubric --root`. Add `CalibrationError` and `ProfileError` to the `main` except tuple. Update the module docstring usage and exit codes.

- [ ] **Step 6: Run the tests**

Run: `python3 -m pytest tests/grading/test_calibration.py tests/grading/test_holdout.py -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add scripts/grading/calibration.py scripts/grading/holdout.py scripts/calibrate.py tests/grading/test_calibration.py
git commit -m "feat: write calibration receipts and approve profiles"
```

---

### Task 4: `grade.py profile` reports calibration approval and recalibration nudges

**Files:**
- Modify: `scripts/grade.py:129-149`
- Test: `tests/grading/test_profile_command.py` (new)

**Interfaces:**
- Produces: exit 14 (`EXIT_UNAPPROVED`) from `grade.py profile`; warnings text `warning recalibrate:`.

- [ ] **Step 1: Write the failing tests**

```python
"""grade.py profile says whether a course may grade today."""

import json
import subprocess
import sys
from pathlib import Path

from scripts.grading.calibration import approval_phrase, approve_calibration
from tests.grading.test_calibration import _install_profile, _write


def _profile_cmd(root, phil210):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", "profile", "--course", "phil210",
         "--rubric", str(phil210 / "grading_rubric.md"), "--root", str(root)],
        capture_output=True, text=True,
    )


def test_profile_exits_fourteen_until_calibration_is_approved(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    result = _profile_cmd(root, phil210)
    assert result.returncode == 14
    assert "no-receipt" in result.stderr
    _write(root, phil210, installed)
    result = _profile_cmd(root, phil210)
    assert result.returncode == 14
    assert "unapproved" in result.stderr
    approve_calibration(root, course="phil210", profile=installed, rubric=phil210 / "grading_rubric.md", approval=approval_phrase("phil210"))
    result = _profile_cmd(root, phil210)
    assert result.returncode == 0, result.stderr
    assert "approved" in result.stdout


def test_profile_warns_when_calibration_is_old(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    installed.write_text(installed.read_text(encoding="utf-8").replace("**Calibrated**: 2026-08-24", "**Calibrated**: 2025-01-01"), encoding="utf-8")
    _write(root, phil210, installed)
    approve_calibration(root, course="phil210", profile=installed, rubric=phil210 / "grading_rubric.md", approval=approval_phrase("phil210"))
    result = _profile_cmd(root, phil210)
    assert result.returncode == 0
    assert "warning recalibrate" in result.stdout
    assert "days" in result.stdout
```

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_profile_command.py -q`
Expected: FAIL (exit 0 today, no warning text).

- [ ] **Step 3: Implement**

In `scripts/grade.py` add `EXIT_UNAPPROVED = 14`, import `check_calibration` and `load_brief`, and replace `_cmd_profile`:

```python
RECALIBRATE_AFTER_DAYS = 120
RECALIBRATE_AFTER_BRIEFS = 5


def _recalibration_warnings(profile, root: Path, profile_sha256: str) -> list[str]:
    warnings = []
    try:
        calibrated = datetime.strptime(profile.calibrated, "%Y-%m-%d").date()
    except ValueError:
        calibrated = None
    if calibrated is not None:
        age = (datetime.now(timezone.utc).date() - calibrated).days
        if age > RECALIBRATE_AFTER_DAYS:
            warnings.append(f"warning recalibrate: the profile was calibrated {age} days ago. Add graded work and calibrate again.")
    bound = 0
    assignments = root / "assignments"
    if assignments.is_dir():
        for path in sorted(assignments.glob("*.md")):
            try:
                brief = load_brief(path)
            except (BriefError, OSError, UnicodeError, ValueError):
                continue
            if brief.status == "approved" and brief.profile_sha256 == profile_sha256:
                bound += 1
    if bound >= RECALIBRATE_AFTER_BRIEFS:
        warnings.append(f"warning recalibrate: {bound} approved assignment briefs use this profile. Add graded work from them and calibrate again.")
    return warnings


def _cmd_profile(args):
    profile = _profile_or_exit(args)
    rubric = load_rubric(args.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        ... (unchanged, returns EXIT_DRIFT)
    status = check_calibration(args.root, course=args.course, profile=profile.path, rubric=args.rubric)
    if status.state != "current":
        print(f"ERROR calibration {status.state}: {status.message}", file=sys.stderr)
        return EXIT_UNAPPROVED
    print(f"Profile for {profile.course} is current, calibrated {profile.calibrated} from {profile.samples} samples. Calibration approved {status.approval.approved_at}.")
    profile_sha256 = hashlib.sha256(read_bytes_limited(profile.path, "The calibration profile", 5 * 1024 * 1024)).hexdigest()
    for line in _recalibration_warnings(profile, args.root, profile_sha256):
        print(line)
    return EXIT_OK
```

Import `from datetime import datetime, timezone`. Update the module docstring exit codes to include `14 calibration not approved or stale`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_profile_command.py tests/grading/test_brief.py -q`
Expected: PASS for the new file. `test_brief.py` CLI tests that run `brief approve` still pass because approval is gated in Task 5, not here.

- [ ] **Step 5: Commit**

```bash
git add scripts/grade.py tests/grading/test_profile_command.py
git commit -m "feat: report calibration approval and recalibration nudges"
```

---

### Task 5: Assignment brief schema 2 with purpose, review cadence, and pilot size

**Files:**
- Modify: `scripts/grading/brief.py`
- Modify: `scripts/grade.py` (`brief prepare` flags, `brief approve` calibration gate)
- Modify: `tests/conftest.py` (`approved_brief` accepts `purpose`, `review`, `pilot`)
- Test: `tests/grading/test_brief.py`

**Interfaces:**
- Produces: `BRIEF_SCHEMA = "grading-assignment-brief/2"`, `AssignmentBrief.purpose`, `.review`, `.pilot`, `prepare_brief(path, *, batch, course, mode, purpose="grading", review="pilot", pilot=2)`, `DEMO_BATCH = "synthetic-demo"`, `PURPOSES`, `REVIEW_CADENCES`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/grading/test_brief.py`:

```python
def test_prepare_records_purpose_cadence_and_pilot_size(tmp_path):
    path = brief_path(tmp_path, "essay-3")
    brief = prepare_brief(path, batch="essay-3", course="phil210", mode="local", review="every", pilot=3)
    assert brief.purpose == "grading"
    assert brief.review == "every"
    assert brief.pilot == 3
    text = path.read_text(encoding="utf-8")
    assert "schema: grading-assignment-brief/2" in text
    assert "Purpose: grading" in text
    assert "Review cadence: every" in text
    assert "Pilot size: 3" in text


def test_prepare_defaults_to_a_two_draft_pilot(tmp_path):
    brief = prepare_brief(brief_path(tmp_path, "essay-3"), batch="essay-3", course="phil210", mode="cloud")
    assert (brief.review, brief.pilot) == ("pilot", 2)


def test_a_holdout_brief_must_be_local_with_no_cadence(tmp_path):
    with pytest.raises(BriefError, match="local"):
        prepare_brief(brief_path(tmp_path, "h1"), batch="h1", course="phil210", mode="cloud", purpose="holdout")
    brief = prepare_brief(brief_path(tmp_path, "h2"), batch="h2", course="phil210", mode="local", purpose="holdout")
    assert brief.review == "none"


def test_review_none_is_reserved_for_the_demo_batch(tmp_path):
    with pytest.raises(BriefError, match="synthetic"):
        prepare_brief(brief_path(tmp_path, "essay-3"), batch="essay-3", course="phil210", mode="local", review="none")
    brief = prepare_brief(brief_path(tmp_path, "synthetic-demo"), batch="synthetic-demo", course="phil210", mode="local", review="none")
    assert brief.review == "none"


def test_schema_one_briefs_are_refused_with_guidance(tmp_path):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    text = path.read_text(encoding="utf-8").replace("grading-assignment-brief/2", "grading-assignment-brief/1")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BriefError, match="schema 1"):
        load_brief(path)


def test_cadence_lines_must_match_metadata(tmp_path):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    text = path.read_text(encoding="utf-8").replace("Pilot size: 2", "Pilot size: 4")
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BriefError, match="Pilot size"):
        load_brief(path)


def test_brief_cli_accepts_review_flags_and_holdout_purpose(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    _copy_profile(root, profile)
    result = _grade("brief", "prepare", *_brief_args(root), "--mode", "local", "--review", "every", "--pilot-size", "1")
    assert result.returncode == 0, result.stderr
    assert "Review cadence: every" in (root / "assignments" / "essay-3.md").read_text(encoding="utf-8")
    result = _grade("brief", "prepare", "--batch", "holdout-1", "--course", "phil210", "--root", str(root), "--mode", "local", "--purpose", "holdout")
    assert result.returncode == 0, result.stderr
    assert "Purpose: holdout" in (root / "assignments" / "holdout-1.md").read_text(encoding="utf-8")


def test_brief_cli_approve_requires_approved_calibration_for_grading(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    _copy_profile(root, profile)
    _grade("brief", "prepare", *_brief_args(root), "--mode", "local")
    _answer(root / "assignments" / "essay-3.md")
    result = _grade("brief", "approve", *_brief_args(root), "--rubric", str(phil210 / "grading_rubric.md"), "--approval", "Approve assignment brief for essay-3")
    assert result.returncode == 14
    assert "calibration" in result.stderr
```

Update `test_check_refuses_malformed_or_mismatched_briefs` so its replace target reads `schema: grading-assignment-brief/2`.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_brief.py -q`
Expected: the new tests FAIL with `TypeError: unexpected keyword` and schema mismatches.

- [ ] **Step 3: Implement `brief.py`**

Constants:

```python
BRIEF_SCHEMA = "grading-assignment-brief/2"
LEGACY_BRIEF_SCHEMA = "grading-assignment-brief/1"
PURPOSES = {"grading", "holdout"}
REVIEW_CADENCES = {"pilot", "every", "none"}
DEMO_BATCH = "synthetic-demo"
MAX_PILOT = 50
_METADATA = ("schema", "batch", "course", "status", "mode", "purpose", "review", "pilot", "rubric_sha256", "profile_sha256", "body_sha256", "approved_at", "approval")
```

Add `purpose: str`, `review: str`, `pilot: int` to `AssignmentBrief` after `mode`. Add:

```python
def _validate_review(*, purpose: str, review: str, pilot: int, mode: str, batch: str) -> None:
    if purpose not in PURPOSES:
        raise BriefError("The assignment brief purpose must be grading or holdout.")
    if review not in REVIEW_CADENCES:
        raise BriefError("The assignment brief review cadence must be pilot, every, or none.")
    if not isinstance(pilot, int) or isinstance(pilot, bool) or not 1 <= pilot <= MAX_PILOT:
        raise BriefError(f"The assignment brief pilot size must be from 1 to {MAX_PILOT}.")
    if purpose == "holdout":
        if mode != "local":
            raise BriefError("A holdout brief must use local mode. Calibration reads past students' names.")
        if review != "none":
            raise BriefError("A holdout brief uses review cadence none. The holdout report is the review.")
    elif review == "none" and batch != DEMO_BATCH:
        raise BriefError("Review cadence none is reserved for the synthetic demo batch. Use pilot or every.")
```

`prepare_brief` gains `purpose="grading", review="pilot", pilot=2`, calls `_validate_review`, and passes the values to `_render` and `_draft_body`. `_draft_body(batch, mode, purpose, review, pilot)` writes the Boundaries section as `f"Host mode: {mode}\nPurpose: {purpose}\nReview cadence: {review}\nPilot size: {pilot}\n\n{ANSWER_REQUIRED}"`.

`_validate_metadata`: replace the schema check with

```python
    if metadata["schema"] == LEGACY_BRIEF_SCHEMA:
        raise BriefError("The assignment brief uses schema 1. Prepare and approve a new brief with brief prepare.")
    if metadata["schema"] != BRIEF_SCHEMA:
        raise BriefError(f"The assignment brief must use schema {BRIEF_SCHEMA}.")
```

and after `_validate_mode`, parse `pilot`:

```python
    try:
        pilot = int(metadata["pilot"])
    except ValueError as error:
        raise BriefError("The assignment brief pilot size must be an integer.") from error
    _validate_review(purpose=metadata["purpose"], review=metadata["review"], pilot=pilot, mode=metadata["mode"], batch=metadata["batch"])
```

`_validate_body(body, mode, purpose, review, pilot)` checks four lines with the same regex pattern as host mode: `Purpose: (grading|holdout)`, `Review cadence: (pilot|every|none)`, `Pilot size: (\d+)`, each exactly once and equal to the metadata value; message names the line, e.g. `"The Boundaries section must contain one Pilot size line that matches pilot."`. `_parse_text` passes the metadata values. `_render` and `_render_brief` carry the three fields. `approve_brief` and `check_brief` copy them through.

`scripts/grade.py`: `brief prepare` adds `--purpose` (`choices=("grading", "holdout")`, default `grading`), `--review` (`choices=("pilot", "every")`, default `pilot`), `--pilot-size` (`type=int`, default 2). In `_cmd_brief_prepare`, pass `purpose=args.purpose, review="none" if args.purpose == "holdout" else args.review, pilot=args.pilot_size`. In `_cmd_brief_approve`, after `_brief_profile_and_rubric` and before `approve_brief`, load the brief with `load_brief(path)`; when `brief.purpose == "grading"`, run `check_calibration(args.root, course=args.course, profile=profile.path, rubric=rubric.path)` and on any state other than `current` print `ERROR calibration <state>: <message>` to stderr and return `EXIT_UNAPPROVED`.

`tests/conftest.py`: `approved_brief` accepts `purpose="grading", review="pilot", pilot=2` and forwards them to `prepare_brief`.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_brief.py tests/grading/test_workflow.py tests/grading/test_verify.py tests/grading/test_demo.py -q`
Expected: `test_brief.py` PASS. Workflow, verify, and demo tests may fail on the calibration gate that Task 8 and Task 9 add; note the failures and continue.

- [ ] **Step 5: Commit**

```bash
git add scripts/grading/brief.py scripts/grade.py tests/conftest.py tests/grading/test_brief.py
git commit -m "feat: record review cadence and purpose in assignment briefs"
```

---

### Task 6: Draft review record and consistency acknowledgement

**Files:**
- Create: `scripts/grading/review.py`
- Modify: `scripts/grading/records.py` (`GradeRecord.review_digest`)
- Test: `tests/grading/test_review.py` (new)

**Interfaces:**
- Produces:
  - `records.GradeRecord.review_digest() -> str` (offsets, lines, lengths removed).
  - `review.REVIEW_NAME = "_review.json"`, `REVIEW_SCHEMA`, `ACK_NAME = "_consistency.json"`, `ACK_SCHEMA`, `REVIEW_CODES = frozenset({...})`.
  - `review.pilot_phrase(batch)`, `draft_phrase(token, batch)`, `ack_phrase(batch)`, `feedback_digest(text)`.
  - `review.DraftArtifacts(token, feedback_sha256, grade_sha256)`, `ReviewApproval(scope, tokens, artifacts, approval, approved_at)`, `DraftReview(path, batch, course, cadence, pilot, approvals)` with `pilot_approval()` and `draft_approval(token)`.
  - `review.review_path(drafts)`, `load_review(path, *, batch, course) -> DraftReview | None`, `approve_review(path, *, batch, course, cadence, pilot, scope, artifacts: Sequence[DraftArtifacts], approval) -> DraftReview`.
  - `review.ReviewFinding(code, message, token)`, `review_findings(*, cadence, pilot, total, drafted: Mapping[str, DraftArtifacts], review, batch) -> tuple[ReviewFinding, ...]`.
  - `review.Acknowledgement(path, batch, course, receipt_sha256, flag_count, flags, approval, acknowledged_at)`, `ack_path(drafts)`, `write_acknowledgement(path, *, batch, course, receipt_sha256, flags: Sequence[tuple[str, str]], approval)`, `load_acknowledgement(path, *, batch, course) -> Acknowledgement | None`.
  - `review.ReviewError(ValueError)`.

- [ ] **Step 1: Write the failing tests**

Create `tests/grading/test_review.py`:

```python
"""The instructor sees pilot drafts and consistency flags before anything moves on."""

import json

import pytest

from scripts.grading.records import CriterionScore, GradeRecord, Justification
from scripts.grading.review import (
    DraftArtifacts,
    ReviewError,
    ack_path,
    ack_phrase,
    approve_review,
    draft_phrase,
    feedback_digest,
    load_acknowledgement,
    load_review,
    pilot_phrase,
    review_findings,
    review_path,
    write_acknowledgement,
)


def _record(token="STUDENT_01", offset=None):
    return GradeRecord(
        batch="essay-2", token=token, source=f"redacted/{token}.md", course="phil210",
        rubric="r.md", profile="p.md",
        criteria=(CriterionScore(1, "Thesis", 25, 20, "Proficient", True, (Justification(quote="a quote", offset=offset, line=3 if offset else None, length=7 if offset else None),)),),
        total=20, total_possible=25, letter="B", pattern_density={},
    )


def _artifacts(token="STUDENT_01", feedback="Good.", offset=None):
    return DraftArtifacts(token, feedback_digest(feedback), _record(token, offset).review_digest())


def test_review_digest_ignores_offsets_but_not_scores():
    assert _record().review_digest() == _record(offset=12).review_digest()
    changed = _record()
    changed.criteria[0].score = 19
    assert changed.review_digest() != _record().review_digest()


def test_pilot_approval_round_trips(tmp_path):
    path = review_path(tmp_path)
    artifacts = [_artifacts("STUDENT_01"), _artifacts("STUDENT_02", "Fine.")]
    review = approve_review(path, batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=artifacts, approval=pilot_phrase("essay-2"))
    assert review.pilot_approval().tokens == ("STUDENT_01", "STUDENT_02")
    loaded = load_review(path, batch="essay-2", course="phil210")
    assert loaded.pilot_approval().artifacts == tuple(artifacts)
    assert path.stat().st_mode & 0o777 == 0o600


def test_approval_needs_the_exact_phrase_and_matching_scope(tmp_path):
    path = review_path(tmp_path)
    with pytest.raises(ReviewError, match="exactly"):
        approve_review(path, batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=[_artifacts()], approval="ok")
    with pytest.raises(ReviewError, match="cadence"):
        approve_review(path, batch="essay-2", course="phil210", cadence="every", pilot=2, scope="pilot", artifacts=[_artifacts()], approval=pilot_phrase("essay-2"))
    with pytest.raises(ReviewError, match="one token"):
        approve_review(path, batch="essay-2", course="phil210", cadence="every", pilot=2, scope="draft", artifacts=[_artifacts(), _artifacts("STUDENT_02")], approval=draft_phrase("STUDENT_01", "essay-2"))


def test_findings_require_then_accept_then_stale(tmp_path):
    drafted = {"STUDENT_01": _artifacts("STUDENT_01"), "STUDENT_02": _artifacts("STUDENT_02")}
    findings = review_findings(cadence="pilot", pilot=2, total=5, drafted=drafted, review=None, batch="essay-2")
    assert [f.code for f in findings] == ["draft-review-required"]
    review = approve_review(review_path(tmp_path), batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=list(drafted.values()), approval=pilot_phrase("essay-2"))
    assert review_findings(cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2") == ()
    drafted["STUDENT_02"] = _artifacts("STUDENT_02", "Edited after approval.")
    findings = review_findings(cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2")
    assert [(f.code, f.token) for f in findings] == [("draft-review-stale", "STUDENT_02")]


def test_pilot_size_mismatch_is_a_finding(tmp_path):
    drafted = {"STUDENT_01": _artifacts("STUDENT_01")}
    review = approve_review(review_path(tmp_path), batch="essay-2", course="phil210", cadence="pilot", pilot=1, scope="pilot", artifacts=list(drafted.values()), approval=pilot_phrase("essay-2"))
    findings = review_findings(cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2")
    assert any(f.code in {"pilot-size-mismatch", "malformed-draft-review"} for f in findings)


def test_every_cadence_needs_each_drafted_token(tmp_path):
    drafted = {"STUDENT_01": _artifacts("STUDENT_01"), "STUDENT_02": _artifacts("STUDENT_02")}
    review = approve_review(review_path(tmp_path), batch="essay-2", course="phil210", cadence="every", pilot=2, scope="draft", artifacts=[drafted["STUDENT_01"]], approval=draft_phrase("STUDENT_01", "essay-2"))
    findings = review_findings(cadence="every", pilot=2, total=2, drafted=drafted, review=review, batch="essay-2")
    assert [(f.code, f.token) for f in findings] == [("draft-review-required", "STUDENT_02")]


def test_cadence_none_needs_nothing():
    assert review_findings(cadence="none", pilot=2, total=3, drafted={}, review=None, batch="essay-2") == ()


def test_malformed_review_is_refused(tmp_path):
    path = review_path(tmp_path)
    path.write_text(json.dumps({"schema": "grading-draft-review/1", "batch": "other"}), encoding="utf-8")
    with pytest.raises(ReviewError):
        load_review(path, batch="essay-2", course="phil210")
    assert load_review(tmp_path / "missing" / "_review.json", batch="essay-2", course="phil210") is None


def test_acknowledgement_binds_the_receipt(tmp_path):
    path = ack_path(tmp_path)
    write_acknowledgement(path, batch="essay-2", course="phil210", receipt_sha256="a" * 64, flags=[("outlier", "STUDENT_03 sits far from the batch median.")], approval=ack_phrase("essay-2"))
    loaded = load_acknowledgement(path, batch="essay-2", course="phil210")
    assert loaded.receipt_sha256 == "a" * 64
    assert loaded.flag_count == 1
    with pytest.raises(ReviewError, match="exactly"):
        write_acknowledgement(path, batch="essay-2", course="phil210", receipt_sha256="a" * 64, flags=[], approval="fine")
```

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_review.py -q`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement `records.review_digest`**

In `scripts/grading/records.py`, import `hashlib` and add to `GradeRecord`:

```python
    def review_digest(self) -> str:
        """Digest the grading judgement without the offsets verification writes."""
        payload = self.as_dict()
        for criterion in payload["criteria"]:
            for justification in criterion["justifications"]:
                for key in ("offset", "line", "length"):
                    justification.pop(key, None)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
```

- [ ] **Step 4: Implement `scripts/grading/review.py`**

```python
"""Record the instructor's review of drafts and consistency flags.

Both records are identity-free: tokens, digests, phrases, and times. Neither
authenticates a person. They let the workflow prove that a stop happened and
that nothing changed after it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.path_safety import has_symlink_component, read_bytes_limited, require_component
from scripts.redaction.private_files import write_private_text

REVIEW_NAME = "_review.json"
REVIEW_SCHEMA = "grading-draft-review/1"
ACK_NAME = "_consistency.json"
ACK_SCHEMA = "grading-consistency-acknowledgement/1"
SCOPES = {"pilot", "draft"}
FILE_LIMIT = 1_048_576
REVIEW_CODES = frozenset({"draft-review-required", "draft-review-stale", "pilot-size-mismatch", "malformed-draft-review"})


class ReviewError(ValueError):
    """The review record or acknowledgement is malformed, mismatched, or unapproved."""


def pilot_phrase(batch: str) -> str:
    return f"Approve pilot drafts for {batch}"


def draft_phrase(token: str, batch: str) -> str:
    return f"Approve draft {token} for {batch}"


def ack_phrase(batch: str) -> str:
    return f"Acknowledge consistency flags for {batch}"


def feedback_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def review_path(drafts: Path) -> Path:
    return drafts / REVIEW_NAME


def ack_path(drafts: Path) -> Path:
    return drafts / ACK_NAME


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hex(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ReviewError(f"{name} must be a lowercase SHA-256 hash.")
    return value


def _token(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewError("Every reviewed token must be text.")
    require_component(value, "The token")
    return value


@dataclass(frozen=True)
class DraftArtifacts:
    token: str
    feedback_sha256: str
    grade_sha256: str


@dataclass(frozen=True)
class ReviewApproval:
    scope: str
    tokens: tuple[str, ...]
    artifacts: tuple[DraftArtifacts, ...]
    approval: str
    approved_at: str


@dataclass(frozen=True)
class DraftReview:
    path: Path
    batch: str
    course: str
    cadence: str
    pilot: int
    approvals: tuple[ReviewApproval, ...]

    def pilot_approval(self) -> ReviewApproval | None:
        return next((a for a in reversed(self.approvals) if a.scope == "pilot"), None)

    def draft_approval(self, token: str) -> ReviewApproval | None:
        return next((a for a in reversed(self.approvals) if a.scope == "draft" and token in a.tokens), None)


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    message: str
    token: str


def _read_json(path: Path, label: str) -> dict | None:
    if path.is_symlink() or has_symlink_component(path):
        raise ReviewError(f"{label} must not use a symbolic link.")
    if not path.exists():
        return None
    if not path.is_file():
        raise ReviewError(f"{label} must be one regular file.")
    try:
        payload = json.loads(read_bytes_limited(path, label, FILE_LIMIT).decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ReviewError(f"{label} is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise ReviewError(f"{label} must be a JSON object.")
    return payload


def _parse_approval(item: object) -> ReviewApproval:
    if not isinstance(item, dict):
        raise ReviewError("Every review approval must be a JSON object.")
    scope = item.get("scope")
    if scope not in SCOPES:
        raise ReviewError("A review approval scope must be pilot or draft.")
    tokens = item.get("tokens")
    artifacts = item.get("artifacts")
    if not isinstance(tokens, list) or not isinstance(artifacts, list) or not tokens:
        raise ReviewError("A review approval needs tokens and artifacts lists.")
    parsed = tuple(
        DraftArtifacts(_token(a.get("token")), _hex(a.get("feedback_sha256"), "feedback_sha256"), _hex(a.get("grade_sha256"), "grade_sha256"))
        for a in artifacts if isinstance(a, dict)
    )
    if len(parsed) != len(artifacts) or tuple(_token(t) for t in tokens) != tuple(a.token for a in parsed):
        raise ReviewError("A review approval's tokens and artifacts disagree.")
    approval = item.get("approval")
    if not isinstance(approval, str):
        raise ReviewError("A review approval needs its phrase.")
    return ReviewApproval(scope, tuple(a.token for a in parsed), parsed, approval, str(item.get("approved_at", "")))


def load_review(path: Path, *, batch: str, course: str) -> DraftReview | None:
    payload = _read_json(path, "The draft review record")
    if payload is None:
        return None
    if payload.get("schema") != REVIEW_SCHEMA:
        raise ReviewError(f"The draft review record must use schema {REVIEW_SCHEMA}.")
    if payload.get("batch") != batch or payload.get("course") != course:
        raise ReviewError("The draft review record belongs to another grading batch or course.")
    cadence = payload.get("cadence")
    pilot = payload.get("pilot")
    if cadence not in {"pilot", "every"} or not isinstance(pilot, int) or isinstance(pilot, bool):
        raise ReviewError("The draft review record needs a cadence and an integer pilot size.")
    approvals = payload.get("approvals")
    if not isinstance(approvals, list):
        raise ReviewError("The draft review record approvals must be a list.")
    review = DraftReview(path, batch, course, cadence, pilot, tuple(_parse_approval(a) for a in approvals))
    for approval in review.approvals:
        expected = pilot_phrase(batch) if approval.scope == "pilot" else draft_phrase(approval.tokens[0], batch)
        if approval.approval != expected:
            raise ReviewError(f"A review approval must read exactly: {expected}")
    return review


def _write_review(review: DraftReview) -> DraftReview:
    payload = {
        "schema": REVIEW_SCHEMA, "batch": review.batch, "course": review.course,
        "cadence": review.cadence, "pilot": review.pilot,
        "approvals": [
            {"scope": a.scope, "tokens": list(a.tokens),
             "artifacts": [{"token": x.token, "feedback_sha256": x.feedback_sha256, "grade_sha256": x.grade_sha256} for x in a.artifacts],
             "approval": a.approval, "approved_at": a.approved_at}
            for a in review.approvals
        ],
    }
    if has_symlink_component(review.path):
        raise ReviewError("The draft review record path must not use a symbolic link.")
    write_private_text(review.path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    loaded = load_review(review.path, batch=review.batch, course=review.course)
    assert loaded is not None
    return loaded


def approve_review(path, *, batch, course, cadence, pilot, scope, artifacts: Sequence[DraftArtifacts], approval) -> DraftReview:
    require_component(batch, "The grading batch")
    require_component(course, "The course")
    if scope not in SCOPES:
        raise ReviewError("The review scope must be pilot or draft.")
    if (scope == "pilot") != (cadence == "pilot"):
        raise ReviewError(f"The brief's review cadence is {cadence}; a {scope} approval does not fit it.")
    if cadence == "none":
        raise ReviewError("Review cadence none has no draft approvals.")
    if not artifacts:
        raise ReviewError("A review approval needs at least one drafted token.")
    if scope == "draft" and len(artifacts) != 1:
        raise ReviewError("A draft approval covers exactly one token.")
    tokens = tuple(a.token for a in artifacts)
    expected = pilot_phrase(batch) if scope == "pilot" else draft_phrase(tokens[0], batch)
    if approval != expected:
        raise ReviewError(f"Approval must read exactly: {expected}")
    existing = load_review(path, batch=batch, course=course)
    if existing is not None and (existing.cadence != cadence or existing.pilot != pilot):
        raise ReviewError("The draft review record was created for a different cadence or pilot size.")
    approvals = existing.approvals if existing is not None else ()
    entry = ReviewApproval(scope, tokens, tuple(artifacts), approval, _now())
    return _write_review(DraftReview(path, batch, course, cadence, pilot, approvals + (entry,)))


def review_findings(*, cadence, pilot, total, drafted: Mapping[str, DraftArtifacts], review: DraftReview | None, batch) -> tuple[ReviewFinding, ...]:
    if cadence == "none":
        return ()
    if review is not None and (review.cadence != cadence or review.pilot != pilot):
        return (ReviewFinding("malformed-draft-review", "The draft review record does not match the brief's cadence or pilot size.", ""),)
    findings: list[ReviewFinding] = []
    if cadence == "pilot":
        approval = review.pilot_approval() if review is not None else None
        if approval is None:
            return (ReviewFinding("draft-review-required", f"The instructor must approve the pilot drafts. Wait for: {pilot_phrase(batch)}", ""),)
        expected = min(pilot, total)
        if len(approval.tokens) != expected:
            findings.append(ReviewFinding("pilot-size-mismatch", f"The pilot approval covers {len(approval.tokens)} drafts; the brief asks for {expected}.", ""))
        for artifact in approval.artifacts:
            current = drafted.get(artifact.token)
            if current != artifact:
                findings.append(ReviewFinding("draft-review-stale", "This pilot draft changed after approval. Show it again and wait for the phrase.", artifact.token))
        return tuple(findings)
    for token in sorted(drafted):
        approval = review.draft_approval(token) if review is not None else None
        if approval is None:
            findings.append(ReviewFinding("draft-review-required", f"The instructor must approve this draft. Wait for: {draft_phrase(token, batch)}", token))
            continue
        bound = next(a for a in approval.artifacts if a.token == token)
        if drafted[token] != bound:
            findings.append(ReviewFinding("draft-review-stale", "This draft changed after approval. Show it again and wait for the phrase.", token))
    return tuple(findings)


@dataclass(frozen=True)
class Acknowledgement:
    path: Path
    batch: str
    course: str
    receipt_sha256: str
    flag_count: int
    flags: tuple[dict, ...]
    approval: str
    acknowledged_at: str


def write_acknowledgement(path, *, batch, course, receipt_sha256, flags: Sequence[tuple[str, str]], approval) -> Acknowledgement:
    require_component(batch, "The grading batch")
    require_component(course, "The course")
    if approval != ack_phrase(batch):
        raise ReviewError(f"Acknowledgement must read exactly: {ack_phrase(batch)}")
    payload = {
        "schema": ACK_SCHEMA, "batch": batch, "course": course,
        "receipt_sha256": _hex(receipt_sha256, "receipt_sha256"),
        "flag_count": len(flags), "flags": [{"kind": k, "message": m} for k, m in flags],
        "approval": approval, "acknowledged_at": _now(),
    }
    if has_symlink_component(path):
        raise ReviewError("The acknowledgement path must not use a symbolic link.")
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")
    loaded = load_acknowledgement(path, batch=batch, course=course)
    assert loaded is not None
    return loaded


def load_acknowledgement(path, *, batch, course) -> Acknowledgement | None:
    payload = _read_json(path, "The consistency acknowledgement")
    if payload is None:
        return None
    if payload.get("schema") != ACK_SCHEMA:
        raise ReviewError(f"The consistency acknowledgement must use schema {ACK_SCHEMA}.")
    if payload.get("batch") != batch or payload.get("course") != course:
        raise ReviewError("The consistency acknowledgement belongs to another grading batch or course.")
    if payload.get("approval") != ack_phrase(batch):
        raise ReviewError(f"The consistency acknowledgement must read exactly: {ack_phrase(batch)}")
    flags = payload.get("flags")
    if not isinstance(flags, list) or not all(isinstance(f, dict) for f in flags):
        raise ReviewError("The consistency acknowledgement flags must be a list of objects.")
    count = payload.get("flag_count")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(flags):
        raise ReviewError("The consistency acknowledgement flag count is wrong.")
    return Acknowledgement(path, batch, course, _hex(payload.get("receipt_sha256"), "receipt_sha256"), count, tuple(flags), payload["approval"], str(payload.get("acknowledged_at", "")))
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest tests/grading/test_review.py tests/grading/test_records.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/grading/review.py scripts/grading/records.py tests/grading/test_review.py
git commit -m "feat: add draft review and consistency acknowledgement records"
```

---

### Task 7: Verification and publication enforce review state

**Files:**
- Modify: `scripts/grading/batch.py` (`verify`, `VerifiedBatch._create`, `publish`, new `draft_artifacts`)
- Modify: `scripts/grade.py` (`consistency --acknowledge`, `review approve`, `review check`)
- Test: `tests/grading/test_verify.py`, `tests/grading/test_publication.py`, `tests/grading/test_review_command.py` (new)

**Interfaces:**
- Produces: `GradingBatch.draft_artifacts() -> dict[str, DraftArtifacts]`; `VerifiedBatch` slots `_review_cadence`, `_purpose`; publish findings `consistency-acknowledgement-required`, `holdout-cannot-publish`; CLI `grade.py consistency --course C --acknowledge PHRASE`, `grade.py review approve|check`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/grading/test_verify.py` (uses its existing `_write_batch`, `SOURCE`, `FEEDBACK`, `_record`):

```python
from scripts.grading.review import approve_review, pilot_phrase, review_path


def _inputs(phil210, root):
    return VerificationInputs(
        course="phil210", rubric=phil210 / "grading_rubric.md",
        profile=root / "profiles" / "phil210.md", patterns=PATTERNS_PATH,
        brief=root / "assignments" / "essay-2.md",
    )


def test_verification_blocks_until_the_pilot_is_approved(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(BatchPaths("essay-2", redacted, drafts))
    report = batch.verify(_inputs(phil210, root), persist=False)
    assert [f.code for f in report.blocking_findings] == ["draft-review-required"]
    artifacts = batch.draft_artifacts()
    approve_review(review_path(drafts), batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=list(artifacts.values()), approval=pilot_phrase("essay-2"))
    report = batch.verify(_inputs(phil210, root))
    assert report.blocking_findings == ()
    assert report.persisted is True


def test_a_draft_edited_after_pilot_approval_blocks_verification(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(BatchPaths("essay-2", redacted, drafts))
    approve_review(review_path(drafts), batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=list(batch.draft_artifacts().values()), approval=pilot_phrase("essay-2"))
    (drafts / "STUDENT_01.md").write_text(FEEDBACK.replace("holds", "stands"), encoding="utf-8")
    report = batch.verify(_inputs(phil210, root), persist=False)
    assert [(f.code, f.token) for f in report.blocking_findings] == [("draft-review-stale", "STUDENT_01")]


def test_verification_offsets_do_not_stale_the_pilot_approval(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(BatchPaths("essay-2", redacted, drafts))
    approve_review(review_path(drafts), batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=list(batch.draft_artifacts().values()), approval=pilot_phrase("essay-2"))
    assert batch.verify(_inputs(phil210, root)).persisted is True
    assert batch.verify(_inputs(phil210, root), persist=False).blocking_findings == ()


def test_cadence_none_demo_brief_needs_no_review(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    brief = root / "assignments" / "essay-2.md"
    brief.unlink()
    (root / "assignments" / "synthetic-demo.md")
    approved_brief(brief.with_name("synthetic-demo.md"), batch="synthetic-demo", course="phil210", rubric=phil210 / "grading_rubric.md", profile=root / "profiles" / "phil210.md", review="none")
```

Stop: the batch name is bound in records and the redaction manifest, so a `synthetic-demo` brief cannot verify an `essay-2` batch. Replace that last test with one that checks the holdout purpose instead:

```python
def test_a_holdout_brief_verifies_without_review(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    brief = root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(brief, batch="essay-2", course="phil210", rubric=phil210 / "grading_rubric.md", profile=root / "profiles" / "phil210.md", purpose="holdout")
    batch = GradingBatch.open(BatchPaths("essay-2", redacted, drafts))
    assert batch.verify(_inputs(phil210, root), persist=False).blocking_findings == ()
```

Append to `tests/grading/test_publication.py` (it has `_verified_batch`-style helpers; adapt to its existing setup that produces a `VerifiedBatch` from `_write_batch`):

```python
from scripts.grading.review import ack_path, ack_phrase, approve_review, pilot_phrase, review_path, write_acknowledgement


def _approve_pilot(drafts, batch):
    approve_review(review_path(drafts), batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=list(batch.draft_artifacts().values()), approval=pilot_phrase("essay-2"))


def test_publish_refuses_without_a_current_acknowledgement(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(BatchPaths("essay-2", redacted, drafts))
    _approve_pilot(drafts, batch)
    report = batch.verify(_inputs(phil210, root))
    verified = report.verified_batch
    result = verified.publish(tmp_path / "final", FakeRenderer())
    assert [f.code for f in result.findings] == ["consistency-acknowledgement-required"]
    assert not (tmp_path / "final").exists()
    write_acknowledgement(ack_path(drafts), batch="essay-2", course="phil210", receipt_sha256="b" * 64, flags=[], approval=ack_phrase("essay-2"))
    result = verified.publish(tmp_path / "final", FakeRenderer())
    assert [f.code for f in result.findings] == ["consistency-acknowledgement-required"]
    receipt_digest = hashlib.sha256((drafts / "_verification.json").read_bytes()).hexdigest()
    write_acknowledgement(ack_path(drafts), batch="essay-2", course="phil210", receipt_sha256=receipt_digest, flags=[], approval=ack_phrase("essay-2"))
    result = verified.publish(tmp_path / "final", FakeRenderer())
    assert result.findings == ()
    assert result.changed is True


def test_a_holdout_batch_never_publishes(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    brief = root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(brief, batch="essay-2", course="phil210", rubric=phil210 / "grading_rubric.md", profile=root / "profiles" / "phil210.md", purpose="holdout")
    batch = GradingBatch.open(BatchPaths("essay-2", redacted, drafts))
    verified = batch.verify(_inputs(phil210, root)).verified_batch
    result = verified.publish(tmp_path / "final", FakeRenderer())
    assert [f.code for f in result.findings] == ["holdout-cannot-publish"]
```

Every existing publication and workflow test that reaches `publish` needs the pilot approval and acknowledgement. Add a helper `_review_and_acknowledge(drafts, batch, report)` in `tests/grading/test_publication.py` that approves the pilot before `verify` and writes an acknowledgement bound to the written receipt after it, then use it wherever those tests call `publish`. In `tests/grading/test_workflow.py`, `_published_context` and `test_current_receipt_returns_consistency_and_publish` need the same treatment (Task 8 rewrites those).

Create `tests/grading/test_review_command.py`:

```python
"""grade.py review and consistency --acknowledge record instructor stops."""

import hashlib
import json
import subprocess
import sys

from scripts.grading.batch import BatchPaths, GradingBatch
from scripts.grading.density import PATTERNS_PATH
from scripts.grading.review import load_acknowledgement, load_review, review_path, ack_path
from tests.grading.test_verify import _inputs, _write_batch


def _grade(*args):
    return subprocess.run([sys.executable, "scripts/grade.py", *args], capture_output=True, text=True)


def _common(phil210, redacted, drafts, root):
    return ["--batch", "essay-2", "--course", "phil210", "--rubric", str(phil210 / "grading_rubric.md"),
            "--redacted", str(redacted), "--drafts", str(drafts), "--patterns", str(PATTERNS_PATH), "--root", str(root)]


def test_review_approve_records_the_pilot_and_check_reports_it(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    common = _common(phil210, redacted, drafts, root)
    check = _grade("review", "check", *common)
    assert check.returncode == 12
    result = _grade("review", "approve", *common, "--scope", "pilot", "--approval", "Approve pilot drafts for essay-2")
    assert result.returncode == 0, result.stderr
    assert load_review(review_path(drafts), batch="essay-2", course="phil210").pilot_approval().tokens == ("STUDENT_01",)
    check = _grade("review", "check", *common)
    assert check.returncode == 0, check.stderr


def test_review_approve_refuses_the_wrong_phrase_and_scope(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    common = _common(phil210, redacted, drafts, root)
    result = _grade("review", "approve", *common, "--scope", "pilot", "--approval", "yes")
    assert result.returncode == 13
    result = _grade("review", "approve", *common, "--scope", "draft", "--token", "STUDENT_01", "--approval", "Approve draft STUDENT_01 for essay-2")
    assert result.returncode == 13
    assert "cadence" in result.stderr


def test_review_approve_refuses_a_draft_with_verification_errors(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    record = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    record["total"] = 99
    (drafts / "STUDENT_01.json").write_text(json.dumps(record), encoding="utf-8")
    result = _grade("review", "approve", *_common(phil210, redacted, drafts, root), "--scope", "pilot", "--approval", "Approve pilot drafts for essay-2")
    assert result.returncode == 12
    assert not review_path(drafts).exists()


def test_consistency_acknowledge_binds_the_current_receipt(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    common = _common(phil210, redacted, drafts, root)
    assert _grade("review", "approve", *common, "--scope", "pilot", "--approval", "Approve pilot drafts for essay-2").returncode == 0
    result = _grade("consistency", "--batch", "essay-2", "--course", "phil210", "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root), "--acknowledge", "Acknowledge consistency flags for essay-2")
    assert result.returncode == 12
    assert "verification" in result.stderr.lower()
    assert GradingBatch.open(BatchPaths("essay-2", redacted, drafts)).verify(_inputs(phil210, root)).persisted
    result = _grade("consistency", "--batch", "essay-2", "--course", "phil210", "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root), "--acknowledge", "Acknowledge consistency flags for essay-2")
    assert result.returncode == 0, result.stderr
    ack = load_acknowledgement(ack_path(drafts), batch="essay-2", course="phil210")
    assert ack.receipt_sha256 == hashlib.sha256((drafts / "_verification.json").read_bytes()).hexdigest()
```

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_verify.py tests/grading/test_publication.py tests/grading/test_review_command.py -q`
Expected: new tests FAIL.

- [ ] **Step 3: Implement in `batch.py`**

Imports: `from scripts.grading.review import DraftArtifacts, ReviewError, ack_path, feedback_digest, load_acknowledgement, load_review, review_findings, review_path`, and `from scripts.grading.brief import load_brief` (already imports `check_brief`, `BriefError`).

Add to `GradingBatch`:

```python
    def draft_artifacts(self) -> dict[str, DraftArtifacts]:
        """Digest every readable drafted pair without judging it."""
        artifacts: dict[str, DraftArtifacts] = {}
        for entry in self._status(check_receipt=False).entries:
            if entry.progress is not Progress.DRAFTED:
                continue
            loaded = _load_entry(entry, self.paths)
            if loaded.record is None or loaded.draft_text is None or any(
                finding.code in _MALFORMED_CODES for finding in loaded.findings
            ):
                continue
            artifacts[entry.token] = DraftArtifacts(
                entry.token, feedback_digest(loaded.draft_text), loaded.record.review_digest()
            )
        return artifacts
```

In `verify`, after the per-draft loop and before `receipt = None`:

```python
        drafted = {
            loaded.entry.token: DraftArtifacts(
                loaded.entry.token,
                feedback_digest(loaded.draft_text),
                loaded.record.review_digest(),
            )
            for loaded in loaded_entries
            if loaded.entry.progress is Progress.DRAFTED
            and loaded.record is not None
            and loaded.draft_text is not None
        }
        total = sum(1 for entry in status.entries if entry.progress is not None)
        try:
            review = load_review(
                review_path(self.paths.drafts), batch=self.paths.batch, course=inputs.course
            )
        except ReviewError as error:
            blocking.append(BlockingFinding("malformed-draft-review", str(error), ""))
        else:
            for finding in review_findings(
                cadence=approved_brief.review, pilot=approved_brief.pilot, total=total,
                drafted=drafted, review=review, batch=self.paths.batch,
            ):
                blocking.append(BlockingFinding(finding.code, finding.message, finding.token))
```

`VerifiedBatch`: add slots `_review_cadence` and `_purpose`, constructor keyword arguments `review_cadence: str = "pilot"`, `purpose: str = "grading"`. In `_create`, after the manifest check:

```python
        brief_artifact = next(item for item in artifacts if item["role"] == "assignment-brief")
        brief_locator = Path(brief_artifact["path"])
        brief_file = brief_locator if brief_locator.is_absolute() else receipt.parent / brief_locator
        brief = load_brief(brief_file)
```

and pass `review_cadence=brief.review, purpose=brief.purpose`. In `publish`, after the renderer checks and before `if findings: return`:

```python
        if self._purpose != "grading":
            findings.append(BlockingFinding("holdout-cannot-publish", "A holdout batch measures the profile. It never publishes feedback.", ""))
        elif self._review_cadence != "none":
            try:
                acknowledgement = load_acknowledgement(
                    ack_path(self._paths.drafts), batch=self._paths.batch, course=""
                )
            except ReviewError as error:
                findings.append(BlockingFinding("consistency-acknowledgement-required", str(error), ""))
            else:
                if acknowledgement is None or acknowledgement.receipt_sha256 != self._receipt_digest:
                    findings.append(BlockingFinding("consistency-acknowledgement-required", "The instructor must see the consistency flags for this exact receipt before publication. Run consistency, then acknowledge it.", ""))
```

`load_acknowledgement` compares course; `VerifiedBatch` does not hold the course, so store `_course` too, read from the decoded receipt (`decoded.course`) in `_create`, and pass it.

- [ ] **Step 4: Implement the CLI in `grade.py`**

`consistency` parser: add `--course` (component-checked, default `None`) and `--acknowledge` (default `None`). In `_cmd_consistency`, after printing flags and before the return logic:

```python
    if args.acknowledge is not None:
        if args.course is None:
            print("ERROR consistency: --acknowledge needs --course.", file=sys.stderr)
            return EXIT_MALFORMED
        if report.blocking_findings:
            return EXIT_MALFORMED
        verified = GradingBatch.open(BatchPaths(batch=args.batch, redacted=args.redacted, drafts=drafts_dir)).status(require_verified=True)
        if verified.blocking_findings:
            for finding in verified.blocking_findings:
                _print_batch_finding(finding, blocking=True)
            print("ERROR consistency: verification must pass before the flags can be acknowledged.", file=sys.stderr)
            return EXIT_FAILED
        receipt_digest = hashlib.sha256(read_bytes_limited(drafts_dir / "_verification.json", "The verification receipt", 10 * 1024 * 1024)).hexdigest()
        try:
            write_acknowledgement(ack_path(drafts_dir), batch=args.batch, course=args.course, receipt_sha256=receipt_digest, flags=[(flag.kind, flag.message) for flag in report.flags], approval=args.acknowledge)
        except ReviewError as error:
            print(f"ERROR consistency: {error}", file=sys.stderr)
            return EXIT_MALFORMED
        print(f"Acknowledged {len(report.flags)} consistency flags for {args.batch}.")
```

`review` parser with `approve` and `check` actions. Both take `--batch --course --rubric --redacted --drafts --patterns --brief --root`; `approve` adds `--scope {pilot,draft}`, `--token`, `--approval`.

```python
def _review_context(args):
    profile = load_profile(profile_path(args.course, args.root))
    rubric = load_rubric(args.rubric)
    if profile.drift(rubric):
        raise BriefError("The rubric changed since calibration.")
    brief = check_brief(args.brief or brief_path(args.root, args.batch), batch=args.batch, course=args.course, rubric=rubric.path, profile=profile.path)
    drafts = _drafts_dir(args)
    batch = GradingBatch.open(BatchPaths(batch=args.batch, redacted=args.redacted, drafts=drafts))
    inputs = VerificationInputs(course=args.course, rubric=args.rubric, profile=profile.path, patterns=args.patterns, brief=brief.path)
    return brief, batch, drafts, inputs


def _cmd_review_approve(args):
    try:
        brief, batch, drafts, inputs = _review_context(args)
    except (BriefError, ProfileError, RubricError, OSError, UnicodeError, ValueError) as error:
        print(f"ERROR review: {_one_line(error)}", file=sys.stderr)
        return EXIT_MALFORMED
    if brief.purpose != "grading" or brief.review == "none":
        print("ERROR review: this brief has no draft review stops.", file=sys.stderr)
        return EXIT_MALFORMED
    if (args.scope == "pilot") != (brief.review == "pilot"):
        print(f"ERROR review: the brief's review cadence is {brief.review}; use the matching scope.", file=sys.stderr)
        return EXIT_MALFORMED
    if args.scope == "draft" and not args.token:
        print("ERROR review: --scope draft needs --token.", file=sys.stderr)
        return EXIT_MALFORMED
    artifacts = batch.draft_artifacts()
    status = batch.status()
    total = sum(1 for entry in status.entries if entry.progress is not None)
    existing = load_review(review_path(drafts), batch=args.batch, course=args.course)
    if args.scope == "pilot":
        prior = existing.pilot_approval() if existing else None
        tokens = list(prior.tokens) if prior else sorted(artifacts)
        expected = min(brief.pilot, total)
        if prior is None and len(tokens) != expected:
            print(f"ERROR review: the pilot needs exactly {expected} drafted submissions; found {len(tokens)}.", file=sys.stderr)
            return EXIT_FAILED
    else:
        tokens = [args.token]
    missing = [token for token in tokens if token not in artifacts]
    if missing:
        print("ERROR review: not drafted or unreadable: " + ", ".join(missing), file=sys.stderr)
        return EXIT_FAILED
    report = batch.verify(inputs, persist=False)
    dirty = [f for f in report.blocking_findings if f.code not in REVIEW_CODES and (f.token in tokens or not f.token)]
    if dirty:
        for finding in dirty:
            _print_batch_finding(finding, blocking=True)
        print("ERROR review: fix these findings before asking the instructor to approve.", file=sys.stderr)
        return EXIT_FAILED
    try:
        approve_review(review_path(drafts), batch=args.batch, course=args.course, cadence=brief.review, pilot=brief.pilot, scope=args.scope, artifacts=[artifacts[t] for t in tokens], approval=args.approval)
    except ReviewError as error:
        print(f"ERROR review: {error}", file=sys.stderr)
        return EXIT_MALFORMED
    print(f"Approved {args.scope} drafts: " + ", ".join(tokens))
    return EXIT_OK


def _cmd_review_check(args):
    try:
        brief, batch, drafts, inputs = _review_context(args)
    except (BriefError, ProfileError, RubricError, OSError, UnicodeError, ValueError) as error:
        print(f"ERROR review: {_one_line(error)}", file=sys.stderr)
        return EXIT_MALFORMED
    report = batch.verify(inputs, persist=False)
    findings = [f for f in report.blocking_findings if f.code in REVIEW_CODES]
    for finding in findings:
        _print_batch_finding(finding, blocking=True)
    if findings:
        return EXIT_FAILED
    print(f"Draft review for {args.batch} is current ({brief.review}).")
    return EXIT_OK
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m pytest tests/grading/test_verify.py tests/grading/test_publication.py tests/grading/test_review_command.py tests/grading/test_end_to_end.py -q`
Expected: PASS after updating existing publish-path tests with the helper.

- [ ] **Step 6: Commit**

```bash
git add scripts/grading/batch.py scripts/grade.py tests/grading/test_verify.py tests/grading/test_publication.py tests/grading/test_review_command.py tests/grading/test_end_to_end.py
git commit -m "feat: enforce pilot review and consistency acknowledgement"
```

---

### Task 8: `next` derives the calibration, review, and consistency stops

**Files:**
- Modify: `scripts/grading/workflow.py`
- Modify: `scripts/grade.py:_cmd_next` (repair exit set)
- Test: `tests/grading/test_workflow.py`

**Interfaces:**
- Produces: `NEXT_SCHEMA = "grading-next-action/2"`, `NextAction.tokens: tuple[str, ...]`, actions `validate-holdout`, `review-holdout`, `approve-profile`, `repair-calibration`, `review-pilot`, `review-draft`, `repair-review`, `review-consistency`, `publish`, `report-holdout`.

- [ ] **Step 1: Write the failing tests**

In `tests/grading/test_workflow.py`, add a fixture-level helper that approves calibration for a context and use it in `_write_profile`, `ReadyBatch`, `_complete_context`, and `_template_context` so existing tests keep passing:

```python
from scripts.grading.calibration import approval_phrase, approve_calibration
from scripts.grading.review import ack_path, ack_phrase, approve_review, pilot_phrase, review_path, write_acknowledgement
from tests.grading.test_calibration import _write as _write_receipt


def _approve_calibration(root: Path, phil210: Path) -> None:
    profile = root / "profiles" / "phil210.md"
    _write_receipt(root, phil210, profile)
    approve_calibration(root, course="phil210", profile=profile, rubric=phil210 / "grading_rubric.md", approval=approval_phrase("phil210"))
```

Call `_approve_calibration(context.root, phil210)` right after every place a profile is installed. Then add:

```python
def test_missing_receipt_returns_validate_holdout(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)
    action = next_action(context)
    assert action.action == "validate-holdout"
    assert "holdout" in action.message


def test_passed_receipt_returns_approve_profile_with_the_command(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    profile = _write_profile(context, phil210)
    _write_receipt(context.root, phil210, profile)
    action = next_action(context)
    assert action.action == "approve-profile"
    assert action.commands[0][:3] == ("python3", "scripts/calibrate.py", "approve")
    assert "Approve calibration profile for phil210" in action.commands[0]


def test_failed_receipt_returns_review_holdout_without_commands(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    profile = _write_profile(context, phil210)
    _write_receipt(context.root, phil210, profile, totals=(97, None))
    action = next_action(context)
    assert action.action == "review-holdout"
    assert action.commands == ()
    assert action.findings


def test_a_holdout_brief_skips_the_calibration_gate(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)
    path = context.root / "assignments" / "essay-2.md"
    prepare_brief(path, batch="essay-2", course="phil210", mode="local", purpose="holdout")
    assert next_action(context).action == "complete-brief"


def test_review_none_grading_brief_needs_the_demo_marker(tmp_path, phil210, approved_brief):
    context = _empty_context(tmp_path, phil210)
    context = replace(context, batch="synthetic-demo")
    _write_profile(context, phil210)
    _approve_calibration(context.root, phil210)
    approved_brief(context.root / "assignments" / "synthetic-demo.md", batch="synthetic-demo", course="phil210", rubric=context.rubric, profile=context.root / "profiles" / "phil210.md", review="none")
    action = next_action(context)
    assert action.action == "repair-brief"
    assert action.findings[0].code == "review-cadence-reserved"


def test_pilot_stops_after_the_pilot_size(tmp_path, phil210, approved_brief):
    context = _complete_context(tmp_path, phil210, approved_brief)   # one drafted, clean
    (context.redacted / "STUDENT_02.md").write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(context.redacted)
    action = next_action(context)
    assert action.action == "prepare-record" and action.token == "STUDENT_02"
    _draft_clean(context, "STUDENT_02")
    action = next_action(context)
    assert action.action == "review-pilot"
    assert action.tokens == ("STUDENT_01", "STUDENT_02")
    assert action.commands[0][:4] == ("python3", "scripts/grade.py", "review", "approve")
    (context.redacted / "STUDENT_03.md").write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(context.redacted)
    assert next_action(context).action == "review-pilot"
    approve_review(review_path(context.drafts), batch="essay-2", course="phil210", cadence="pilot", pilot=2, scope="pilot", artifacts=list(GradingBatch.open(BatchPaths("essay-2", context.redacted, context.drafts)).draft_artifacts().values()), approval=pilot_phrase("essay-2"))
    action = next_action(context)
    assert action.action == "prepare-record" and action.token == "STUDENT_03"


def test_drafting_past_the_pilot_without_approval_is_a_repair(tmp_path, phil210, approved_brief):
    context = _complete_context(tmp_path, phil210, approved_brief)
    for token in ("STUDENT_02", "STUDENT_03"):
        (context.redacted / f"{token}.md").write_text(SOURCE, encoding="utf-8")
        _draft_clean(context, token)
    _refresh_redaction_manifest(context.redacted)
    action = next_action(context)
    assert action.action == "repair-review"
    assert action.findings[0].code == "pilot-overrun"
    assert "STUDENT_03" in action.findings[0].message


def test_every_cadence_stops_after_each_draft(tmp_path, phil210, approved_brief):
    context = _complete_context(tmp_path, phil210, approved_brief)
    brief = context.root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(brief, batch="essay-2", course="phil210", rubric=context.rubric, profile=context.root / "profiles" / "phil210.md", review="every")
    action = next_action(context)
    assert action.action == "review-draft" and action.token == "STUDENT_01"
    assert "Approve draft STUDENT_01 for essay-2" in action.commands[0]


def test_verified_batch_stops_for_consistency_then_publishes(tmp_path, phil210, approved_brief):
    context = _complete_context(tmp_path, phil210, approved_brief)
    _approve_pilot(context)
    GradingBatch.open(BatchPaths("essay-2", context.redacted, context.drafts)).verify(_inputs_for(context))
    action = next_action(context)
    assert action.action == "review-consistency"
    assert action.commands[0][:3] == ("python3", "scripts/grade.py", "consistency")
    assert "--course" in action.commands[0]
    receipt = hashlib.sha256((context.drafts / "_verification.json").read_bytes()).hexdigest()
    write_acknowledgement(ack_path(context.drafts), batch="essay-2", course="phil210", receipt_sha256=receipt, flags=[], approval=ack_phrase("essay-2"))
    action = next_action(context)
    assert action.action == "publish"
    assert action.commands[0][:2] == ("python3", "scripts/rehydrate.py")


def test_holdout_batch_ends_at_report_holdout(tmp_path, phil210, approved_brief):
    context = _complete_context(tmp_path, phil210, approved_brief)
    brief = context.root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(brief, batch="essay-2", course="phil210", rubric=context.rubric, profile=context.root / "profiles" / "phil210.md", purpose="holdout")
    assert next_action(context).action == "verify"
    GradingBatch.open(BatchPaths("essay-2", context.redacted, context.drafts)).verify(_inputs_for(context))
    action = next_action(context)
    assert action.action == "report-holdout"
    assert action.commands == ()
    assert "calibrate.py holdout" in action.message
```

Helpers `_draft_clean(context, token)` writes a clean pair for the token using `test_verify._record` and `FEEDBACK`; `_approve_pilot(context)` approves the pilot from `draft_artifacts()`; `_inputs_for(context)` builds `VerificationInputs`. Update `test_current_receipt_returns_consistency_and_publish` to expect `review-consistency`, and `_published_context` to approve the pilot and write the acknowledgement before publishing. Update the two `grading-next-action/1` assertions to `/2` and add `"tokens": []` to the serialized dictionary assertion.

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_workflow.py -q`
Expected: new tests FAIL; several existing tests FAIL on the calibration gate until the helper is wired.

- [ ] **Step 3: Implement `workflow.py`**

Imports: `check_calibration`, `approval_phrase`, `demo_authorized` from `scripts.grading.calibration`; `REVIEW_CODES`, `ReviewError`, `ack_path`, `ack_phrase`, `draft_phrase`, `load_acknowledgement`, `load_review`, `pilot_phrase`, `review_path` from `scripts.grading.review`; `Profile` from profile.

`NEXT_SCHEMA = "grading-next-action/2"`. Add `tokens: tuple[str, ...] = ()` to `NextAction` and `"tokens": list(self.tokens)` to `as_dict`.

Add the calibration gate:

```python
def _calibration_gate(context: WorkflowContext, profile) -> NextAction | None:
    status = check_calibration(context.root, course=context.course, profile=profile.path, rubric=context.rubric)
    if status.state == "current":
        return None
    if status.state in {"malformed-receipt", "malformed-approval"}:
        return _action(context, stage="calibration", action="repair-calibration",
                       message="Repair the calibration receipt or approval before grading.",
                       findings=(BlockingFinding(status.state, status.message, ""),))
    if status.state in {"no-receipt", "stale-receipt"}:
        return _action(context, stage="calibration", action="validate-holdout",
                       message=("Measure the profile against its holdouts before grading. Build the packet with "
                                "calibrate.py inputs and --holdout-out, prepare a holdout brief with "
                                "brief prepare --purpose holdout, grade that batch, then run calibrate.py holdout."),
                       findings=(BlockingFinding(status.state, status.message, ""),) if status.state == "stale-receipt" else ())
    if status.state == "gap":
        findings = tuple(BlockingFinding("holdout-gap", finding, "") for finding in status.receipt.findings) or (BlockingFinding("holdout-gap", status.message, ""),)
        return _action(context, stage="calibration", action="review-holdout",
                       message=("Show the instructor the holdout report. They may edit the profile and run the holdouts "
                                "again, or accept the gap with calibrate.py approve --accept-gap --reason in their words."),
                       findings=findings)
    command = ("python3", "scripts/calibrate.py", "approve", "--course", context.course, "--rubric", str(context.rubric),
               "--approval", approval_phrase(context.course), "--root", str(context.root))
    return _action(context, stage="calibration", action="approve-profile",
                   message="The holdouts passed. Wait for the exact approval phrase, then record it.", commands=(command,))
```

In `next_action`, after the drift check:

```python
    current_brief = brief_path(context.root, context.batch)
    peeked = _peek_brief(current_brief)
    if peeked is None or peeked.purpose != "holdout":
        gate = _calibration_gate(context, profile)
        if gate is not None:
            return gate
```

with `_peek_brief` returning `load_brief(path)` or `None` on missing or `BriefError`. After `check_brief` succeeds, add:

```python
    if loaded_brief.review == "none" and loaded_brief.purpose == "grading" and not demo_authorized(context.root):
        return _action(context, stage="briefing", action="repair-brief",
                       message="Review cadence none is reserved for the prepared synthetic demo. Prepare a brief with pilot or every.",
                       findings=(BlockingFinding("review-cadence-reserved", "This grading brief has review cadence none without a demo marker.", ""),))
```

Replace the drafting and publication section (from `pending = next(...)` to the end) with:

```python
    entries = [entry for entry in status.entries if entry.progress is not None]
    drafted = [entry for entry in entries if entry.progress is Progress.DRAFTED]
    pending = [entry for entry in entries if entry.progress is Progress.PENDING]
    inputs = VerificationInputs(course=context.course, rubric=context.rubric, profile=profile.path, patterns=context.patterns, brief=current_brief)
    batch = GradingBatch.open(BatchPaths(context.batch, context.redacted, context.drafts))
    if drafted or not pending:
        verification = batch.verify(inputs, persist=False)
        template = [f for f in verification.blocking_findings if f.code in _TEMPLATE_INCOMPLETE_CODES]
        other = [f for f in verification.blocking_findings if f.code not in _TEMPLATE_INCOMPLETE_CODES and f.code not in REVIEW_CODES]
        if other:
            return _action(context, stage="verification", action="repair-verification",
                           message="Repair the verification findings before continuing.", findings=tuple(other))
        if template:
            token = next((f.token for f in template if f.token), "")
            return _action(context, stage="drafting", action="complete-draft",
                           message=f"Complete the prepared draft pair for {token}.", token=token, findings=tuple(template))
        review_stop = _review_gate(context, loaded_brief, batch, drafted, len(entries))
        if review_stop is not None:
            return review_stop
    if pending:
        first = pending[0]
        if first.source is None:
            raise ValueError("The pending submission has no redacted source.")
        return _action(context, stage="drafting", action="prepare-record",
                       message=f"Prepare the draft pair for {first.token}.", token=first.token,
                       commands=(_record_prepare_command(context, first.source),))
    if receipt.is_file():
        resumed = VerifiedBatch.resume(BatchPaths(context.batch, context.redacted, context.drafts), inputs)
        if resumed.verified_batch is None:
            return _action(context, stage="verification", action="repair-verification",
                           message="Repair the stale verification receipt and verify again.", findings=resumed.findings)
        if loaded_brief.purpose == "holdout":
            return _action(context, stage="calibration", action="report-holdout",
                           message=("The holdout batch is verified. Run calibrate.py holdout with --course, --rubric, --packet, "
                                    "and one --record, --draft, and --sample per holdout, then show the report."))
        if _publication_is_current(context, status, receipt):
            return _action(context, stage="publication", action="review-publication",
                           message="Review every identity-bearing published draft before sending feedback.")
        consistency = _consistency_command(context)
        publication = _publication_command(context, profile, current_brief)
        if loaded_brief.review == "none":
            return _action(context, stage="publication", action="consistency-and-publish",
                           message="Review consistency flags, then publish rehydrated drafts.", commands=(consistency, publication))
        acknowledgement = _current_acknowledgement(context, receipt)
        if acknowledgement is None:
            return _action(context, stage="publication", action="review-consistency",
                           message=(f"Run consistency and show the instructor every flag. Wait for: {ack_phrase(context.batch)} "
                                    "Then run the same command again with --acknowledge and that phrase."),
                           commands=(consistency,))
        return _action(context, stage="publication", action="publish",
                       message="Publish the rehydrated drafts locally. The instructor reviews them before sending anything.",
                       commands=(publication,))
    return _action(context, stage="verification", action="verify",
                   message="Persist verification for the clean grading batch.", commands=(_verify_command(context),))
```

with helpers `_record_prepare_command`, `_consistency_command` (adds `--course`), `_publication_command`, `_verify_command` extracted from the current inline tuples, and:

```python
def _current_acknowledgement(context, receipt: Path):
    try:
        acknowledgement = load_acknowledgement(ack_path(context.drafts), batch=context.batch, course=context.course)
    except ReviewError:
        return None
    if acknowledgement is None:
        return None
    digest = hashlib.sha256(read_bytes_limited(receipt, "The verification receipt", 10 * 1024 * 1024)).hexdigest()
    return acknowledgement if acknowledgement.receipt_sha256 == digest else None


def _review_gate(context, brief, batch, drafted, total) -> NextAction | None:
    if brief.review == "none":
        return None
    try:
        review = load_review(review_path(context.drafts), batch=context.batch, course=context.course)
    except ReviewError as error:
        return _action(context, stage="drafting", action="repair-review", message="Repair the malformed draft review record.",
                       findings=(BlockingFinding("malformed-draft-review", str(error), ""),))
    artifacts = batch.draft_artifacts()
    base = ("python3", "scripts/grade.py", "review", "approve", "--batch", context.batch, "--course", context.course,
            "--rubric", str(context.rubric), "--redacted", str(context.redacted), "--drafts", str(context.drafts),
            "--patterns", str(context.patterns), "--root", str(context.root))
    drafted_tokens = tuple(entry.token for entry in drafted)
    if brief.review == "pilot":
        expected = min(brief.pilot, total)
        approval = review.pilot_approval() if review is not None else None
        command = base + ("--scope", "pilot", "--approval", pilot_phrase(context.batch))
        if approval is None:
            if len(drafted_tokens) < expected:
                return None
            if len(drafted_tokens) == expected:
                return _action(context, stage="review", action="review-pilot",
                               message=(f"Show the instructor these {expected} tokenized pilot drafts and wait for: "
                                        f"{pilot_phrase(context.batch)}"), tokens=drafted_tokens, commands=(command,))
            extra = ", ".join(drafted_tokens[expected:])
            return _action(context, stage="review", action="repair-review",
                           message="Remove the drafts made past the pilot size, or ask the instructor how to proceed.",
                           findings=(BlockingFinding("pilot-overrun", f"Drafts exist beyond the pilot without approval: {extra}", ""),))
        if len(approval.tokens) != expected:
            return _action(context, stage="review", action="repair-review", message="The pilot approval does not match the brief's pilot size.",
                           findings=(BlockingFinding("pilot-size-mismatch", f"Approved {len(approval.tokens)} drafts; the brief asks for {expected}.", ""),))
        stale = tuple(a.token for a in approval.artifacts if artifacts.get(a.token) != a)
        if stale:
            return _action(context, stage="review", action="review-pilot",
                           message=f"Pilot drafts changed after approval: {', '.join(stale)}. Show them again and wait for: {pilot_phrase(context.batch)}",
                           tokens=approval.tokens, commands=(command,), findings=tuple(BlockingFinding("draft-review-stale", "Changed after approval.", t) for t in stale))
        return None
    unapproved = [t for t in drafted_tokens if (review is None or review.draft_approval(t) is None or next(a for a in review.draft_approval(t).artifacts if a.token == t) != artifacts.get(t))]
    if not unapproved:
        return None
    if len(unapproved) == 1:
        token = unapproved[0]
        return _action(context, stage="review", action="review-draft",
                       message=f"Show the instructor the tokenized draft for {token} and wait for: {draft_phrase(token, context.batch)}",
                       token=token, tokens=(token,), commands=(base + ("--scope", "draft", "--token", token, "--approval", draft_phrase(token, context.batch)),))
    return _action(context, stage="review", action="repair-review", message="Only one draft may wait for approval at a time under per-draft review.",
                   findings=(BlockingFinding("review-overrun", "Unapproved drafts: " + ", ".join(unapproved), ""),))
```

`grade.py _cmd_next`: add `"repair-calibration"` and `"repair-review"` to the exit-12 set.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_workflow.py tests/grading/test_end_to_end.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/grading/workflow.py scripts/grade.py tests/grading/test_workflow.py
git commit -m "feat: derive calibration, pilot, and consistency stops from state"
```

---

### Task 9: The synthetic demo prepares a synthetic calibration approval and a cadence-none brief

**Files:**
- Modify: `scripts/grading/demo.py`
- Test: `tests/grading/test_demo.py`

- [ ] **Step 1: Write the failing test**

```python
def test_prepare_records_a_synthetic_calibration_approval_and_no_review_stops(tmp_path, phil210):
    dropped = _drop_folder(phil210, tmp_path / "grading_mock_papers")
    result = _prepare(dropped, tmp_path / "runs")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    state_root = Path(payload["state_root"])
    approval = json.loads((state_root / "calibration/phil210.approval.json").read_text(encoding="utf-8"))
    assert approval["synthetic"] is True
    brief = (state_root / "assignments/synthetic-demo.md").read_text(encoding="utf-8")
    assert "Review cadence: none" in brief
    assert payload["next_action"]["action"] == "prepare-record"
```

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_demo.py -q`
Expected: FAIL. The existing first test also fails because `next_action` returns `validate-holdout`.

- [ ] **Step 3: Implement**

In `prepare_demo`: after writing the profile, call `approve_synthetic(state_root, course=course, profile=profile, rubric=rubric)`. Change `prepare_brief(brief, batch=batch, course=course, mode="local")` to add `review="none"`. Move the `marker` dictionary and its `write_private_text` call to just before `action = next_action(context)`. Update the third narration line to say the synthetic profile carries a synthetic calibration approval and the demo brief skips the pilot and consistency stops that real batches require.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_demo.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/grading/demo.py tests/grading/test_demo.py
git commit -m "feat: give the synthetic demo a synthetic calibration approval"
```

---

### Task 10: Conformance trial prompts name the new stops

**Files:**
- Modify: `scripts/grading/_conformance_run.py:47-80`
- Test: `tests/grading/test_conformance.py`

- [ ] **Step 1: Write the failing test**

```python
def test_grading_phase_prompt_names_pilot_and_consistency_phrases(tmp_path):
    prompt = conformance_run._phase_prompt("grading", tmp_path)
    assert "Approve pilot drafts for essay-2" in prompt
    assert "Acknowledge consistency flags for essay-2" in prompt
```

- [ ] **Step 2: Run to confirm failure**

Run: `python3 -m pytest tests/grading/test_conformance.py -k prompt -q`

- [ ] **Step 3: Implement**

Change the `grading` task text to:

```python
        "grading": (
            "Follow next through draft-pair preparation, grading, pilot review, "
            "verification, consistency review, and publication. Edit the prepared "
            "pair; replace every feedback marker. In this synthetic trial you also "
            "act as the instructor: when next returns review-pilot, run its returned "
            "command with the phrase 'Approve pilot drafts for essay-2'; when it "
            "returns review-consistency, run the consistency command, then run it "
            "again with --acknowledge 'Acknowledge consistency flags for essay-2'. "
            "Never open the token map, review file, or final output."
        ),
```

Also add to the briefing task: "Calibration for this synthetic course is already approved."; and in `create_trial` (`_conformance_trial.py`), after installing the synthetic profile, write a synthetic receipt and approval by calling `approve_synthetic` and writing a `demo.json` marker with `{"authorization": "synthetic-demo", "synthetic": true}` into the trial's `.grading` root, so the trial agent passes the calibration gate. Read `create_trial` for where the profile is installed and add the calls beside it.

- [ ] **Step 4: Run the tests**

Run: `python3 -m pytest tests/grading/test_conformance.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/grading/_conformance_run.py scripts/grading/_conformance_trial.py tests/grading/test_conformance.py
git commit -m "feat: teach the conformance trial the review stops"
```

---

### Task 11: Documentation, ADR, glossary, version, and package inventory

**Files:**
- Modify: `SKILL.md`, `README.md`, `docs/using-the-grader.md`, `references/calibration.md`, `references/assignment-brief.md`, `references/demo.md`, `CONTEXT.md`, `AGENTS.md`, `PACKAGE_FILES.txt`, `.claude-plugin/plugin.json`, `skills/humanizer/SKILL.md`
- Create: `docs/adr/0004-approved-calibration-and-reviewed-drafts-gate-grading.md`
- Test: `python3 scripts/validate-package.py`, `python3 -m pytest tests/ -q`

- [ ] **Step 1: Write the ADR**

```markdown
---
status: accepted
date: 2026-09-03
---

# Approved calibration and reviewed drafts gate grading

A course may grade only with a calibration profile that holdouts measured
and the instructor approved. A grading batch may verify only after the
instructor approved the pilot drafts, or every draft under per-draft review.
It may publish only after the instructor acknowledged the consistency flags
for the exact verification receipt. The state machine derives each stop from
identity-free files, so an agent that skips a stop is refused by verification
or publication rather than by instructions alone.

The bundled synthetic demo is the one path with no stops. Its brief carries
review cadence `none`, which the brief validator accepts only for the demo
batch name, and its calibration approval is marked synthetic and valid only
beside the demo marker.

## Considered options

- **Keep the holdout as a report:** Rejected because a report nobody reads
  gates nothing. The receipt records the measurement; approval binds it.
- **Review only at publication:** Rejected because a forty-essay batch would
  be graded before the instructor saw one draft. The pilot stop surfaces a
  bad profile or brief on the second essay.
- **Bind review state into the verification receipt:** Rejected because
  publication already re-runs verification, which re-checks the review
  record. The receipt schema stays at 2.
- **A `none` cadence for any batch:** Rejected because it would make
  unattended grading a flag away. Only the demo batch may use it, and only
  with the demo marker present.

## Consequences

- Grading a course for the first time takes one holdout batch and one
  approval. Editing the profile or rubric repeats both.
- Each grading batch stops at least three times: the brief, the pilot, and
  the consistency flags. Redaction review remains a fourth stop.
- A hand-written profile never grades. A batch drafted past the pilot
  without approval cannot verify until the extra drafts are removed.
- Assignment brief schema 1 files must be prepared and approved again.
- Every phrase remains workflow evidence, not authentication, as ADR-0002
  states.
```

- [ ] **Step 2: Update `SKILL.md`**

Bump `metadata.version` to `3.6.0`. Rewrite the Calibrate section:

```markdown
## Calibrate

Read `references/calibration.md` first.

1. **Gate.** If the answer is cloud, stop. Say that calibration reads graded
   work with names in it and must run locally.
2. **Collect.** The rubric, four to ten already-graded pieces of work, and
   the roster. Three to eight become calibration samples. One or two are
   held back: one when there are fewer than six pieces in total, two
   otherwise.
3. **Build the packet and the blind holdout copies.**

   ```bash
   python3 scripts/calibrate.py inputs \
     --rubric <rubric.md> --samples <graded/> --roster <roster.csv> \
     --holdout <one-filename.md> --holdout <another-filename.md> \
     --holdout-out .grading/holdout-submissions/<course> \
     --out .grading/calibration-inputs.json
   ```

   This is the safety step. It drops every student essay, replaces every
   roster name in the instructor's comments with `[STUDENT]`, and writes each
   holdout's essay without its feedback or bracketed comments. Build the
   profile from the packet, never from the sample files.

4. **Interview.** Always ask the two range questions in
   `references/calibration.md`. Ask the other three only when the samples
   cannot answer them. Record every answer, in the instructor's words, under
   `## Instructor answers`.
5. **Write the profile** to `.grading/profiles/<course>.md` with all nine
   sections, including the fingerprint table. Copy the whole-rubric digest and
   each criterion digest from the packet. Set `**Samples**` to the packet's
   sample count.
6. **Grade the holdouts blind.** Prepare a holdout brief and run the grading
   loop below on the blind copies:

   ```bash
   python3 scripts/grade.py brief prepare \
     --batch holdout-<course> --course <course> --mode local --purpose holdout
   ```

   A holdout batch skips the profile-approval gate, has no pilot stop, and
   ends at `report-holdout`. It can never publish.
7. **Measure.** One `--record`, `--draft`, and `--sample` per holdout:

   ```bash
   python3 scripts/calibrate.py holdout \
     --course <course> --rubric <rubric.md> \
     --packet .grading/calibration-inputs.json \
     --record <drafts>/STUDENT_01.json --draft <drafts>/STUDENT_01.md \
     --sample <graded>/<holdout-one.md> \
     --record <drafts>/STUDENT_02.json --draft <drafts>/STUDENT_02.md \
     --sample <graded>/<holdout-two.md>
   ```

   The instructor reads the token map locally to pair each token with its
   sample. The command writes an identity-free calibration receipt. Exit 0
   means every holdout stayed inside the profile's tolerances. Exit 15 means
   a gap.
8. **Show the instructor the holdout report.** That report is the
   deliverable, not the profile. On a gap, offer to edit the profile and run
   the holdouts again. The instructor may instead accept the gap; record their
   reason with `--accept-gap --reason`.
9. **Approve.** Wait for this exact message:

   ```text
   Approve calibration profile for <course>
   ```

   Then run:

   ```bash
   python3 scripts/calibrate.py approve \
     --course <course> --rubric <rubric.md> \
     --approval "Approve calibration profile for <course>"
   python3 scripts/calibrate.py check --course <course> --rubric <rubric.md>
   ```

   Any edit to the profile or rubric makes the receipt and approval stale.
   Grading refuses until the holdouts run and the instructor approves again.
```

In Assignment briefing, add an eighth question group: "8. Review cadence: pilot of N drafts (default two), or every draft. Record it with `--review` and `--pilot-size` on `brief prepare`." In Grade, after the `review-redaction` paragraph, add:

```markdown
When the action is `review-pilot` or `review-draft`, stop. Show the instructor
the tokenized drafts it names, from `.grading/drafts/<batch>/`, or tell them
where to open them. They are identity-free. Wait for the exact phrase in the
action's message, then run the returned command. The batch does not continue
until that command succeeds. A draft edited after approval must be shown and
approved again.

When the action is `review-consistency`, run the returned consistency command
and show every flag. Wait for the exact acknowledgement phrase, then run the
same command again with `--acknowledge "<phrase>"`. Only then does `next`
return `publish`.

When the action is `validate-holdout`, `review-holdout`, or
`approve-profile`, the course is not ready to grade. Return to Calibrate.
```

Add to the errors table: `| Calibration receipt missing, stale, or failed | Refuse. Return to calibrate. |`, `| Profile not approved | Refuse. Wait for the exact approval phrase. |`, `| Drafts exist past the pilot without approval | Repair. Remove the extra drafts or ask the instructor. |`, `| Consistency flags not acknowledged | Refuse publication. |`. Add to the does-not-do list: `- **It does not grade unattended.** Every real batch stops for the brief, the pilot, and the consistency flags. The bundled synthetic demo is the one exception, and only for that corpus.` Update the four hard rules' fourth rule to "Refuse to grade without an approved profile."

- [ ] **Step 3: Update `README.md`**

Add an "Instructor onboarding" section after the demo section listing the steps: gather three to ten graded papers; build the packet with holdouts; answer the interview; grade the holdouts blind; read the report; approve the profile; then for each assignment brief, approve, redact, review the pilot, verify, acknowledge consistency, publish, review. Update the who-does-what table with rows for "Runs holdouts and shows the report" / "Approves the calibration profile", "Stops at the pilot" / "Reads and approves the pilot drafts", "Runs consistency" / "Acknowledges the flags". Add after the table: "It will not grade a batch without an approved calibration profile, a reviewed pilot, and an acknowledged consistency pass. The synthetic demo is the only exception." Replace "unattended, narrated demo" with "self-contained, narrated demo". Add the 3.6.0 release note:

```markdown
- **3.6.0** — Calibration is now a gate. `calibrate.py inputs` holds back one or two samples and writes blind copies; `calibrate.py holdout` measures every holdout, reports aggregate agreement, and writes an identity-free receipt; `calibrate.py approve` binds the instructor's exact phrase to the profile, rubric, and receipt. Profiles gain an `Instructor answers` section and optional tolerances. Assignment briefs move to schema 2 with purpose, review cadence, and pilot size. Drafting stops at the pilot (default two drafts) or at every draft; verification refuses unreviewed or changed drafts. Consistency is separate from publication and needs an acknowledgement bound to the receipt. `grade.py profile` exits 14 until calibration is approved and warns when a profile is old or heavily used. The synthetic demo keeps its one-turn flow through a synthetic calibration approval and a demo-only review cadence.
```

Update the numbered walkthrough and the example JSON schema string to `grading-next-action/2`.

- [ ] **Step 4: Update the remaining docs**

- `docs/using-the-grader.md`: delete the "until Task 6" sentence; rewrite Part 1 to the nine-step calibrate flow above with the phil210 example (holdouts paper 4 and paper 5); add Part 2 steps for pilot review and consistency acknowledgement; update the command table with `calibrate.py holdout`, `approve`, `check`, `grade.py review approve|check`, `grade.py consistency --acknowledge`; update the schema string.
- `references/calibration.md`: add "Holdouts" (rule of one or two, blind copies, receipt, tolerances), change "seven sections" to "nine" with the `Instructor answers` row and "Rubric fingerprint" already present (the table lists eight; add the ninth), state that the two range questions are always asked, and add the approval section.
- `references/assignment-brief.md`: add the eighth question, `--purpose`, `--review`, `--pilot-size`, schema 2, the four generated Boundaries lines, and the rule that `none` is demo-only.
- `references/demo.md`: add one sentence that the demo brief uses review cadence `none` and a synthetic calibration approval, both valid only inside the prepared run.
- `CONTEXT.md`: add Calibration receipt, Profile approval, Holdout batch, Review cadence, Pilot review, Draft review record, Consistency acknowledgement, each with an `_Avoid_` line.
- `AGENTS.md`: add `scripts/grading/calibration.py` and `scripts/grading/review.py` to the key files sentence; change "Refuse to grade without a profile" invariant language to "an approved profile".
- `PACKAGE_FILES.txt`: add `docs/adr/0004-approved-calibration-and-reviewed-drafts-gate-grading.md`, `scripts/grading/calibration.py`, `scripts/grading/review.py`, `tests/grading/test_calibration.py`, `tests/grading/test_profile_command.py`, `tests/grading/test_review.py`, `tests/grading/test_review_command.py`, and the two new `docs/superpowers/` files, sorted.
- Version `3.6.0` in `skills/humanizer/SKILL.md` and `.claude-plugin/plugin.json`.

- [ ] **Step 5: Validate**

Run:

```bash
python3 scripts/validate-package.py
python3 -m pytest tests/ -q
grep -rn "grading-next-action/1\|grading-assignment-brief/1" README.md SKILL.md docs/using-the-grader.md references CONTEXT.md
```

Expected: validator exits 0, every test passes, the grep finds only the historical spec and plan files.

- [ ] **Step 6: Commit**

```bash
git add -A docs references README.md SKILL.md CONTEXT.md AGENTS.md PACKAGE_FILES.txt .claude-plugin/plugin.json skills/humanizer/SKILL.md
git commit -m "docs: describe supervised calibration and review for v3.6.0"
```

---

## Self-review

**Spec coverage.** Packet and holdouts: Task 1. Tolerances and instructor answers: Task 2. Receipt, aggregate, approval, check, synthetic approval, demo marker: Task 3. `grade.py profile` exit 14 and nudges: Task 4. Brief schema 2, CLI flags, `brief approve` calibration gate: Task 5. Review record, digests, phrases, acknowledgement: Task 6. Verify and publish enforcement, `review` and `consistency --acknowledge` commands: Task 7. Every new `next` action including holdout purpose and cadence none: Task 8. Demo: Task 9. Conformance: Task 10. Docs, ADR, glossary, version, package: Task 11.

**Type consistency.** `check_calibration(root, *, course, profile, rubric)` is used the same way in Tasks 3, 4, 5, and 8. `approve_review(path, *, batch, course, cadence, pilot, scope, artifacts, approval)` matches across Tasks 6, 7, and 8. `DraftArtifacts` equality drives staleness in both `review_findings` and `_review_gate`. `NextAction.tokens` is added in Task 8 and asserted there.

**Known caution.** `VerifiedBatch` gains three slots; `__slots__` must list `_review_cadence`, `_purpose`, and `_course` or construction fails at runtime, which the publication tests will catch.
