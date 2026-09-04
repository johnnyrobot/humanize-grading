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
from scripts.grading.holdout import aggregate, compare
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
        criteria=(
            CriterionScore(1, "Thesis", 25, total // 4, "Proficient", True, ()),
        ),
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
    return [
        compare(
            _record(totals[0], sample5.letter, "STUDENT_01"),
            sample5.comments_only(),
            sample5,
        ),
        compare(
            _record(totals[1], sample4.letter, "STUDENT_02"),
            sample4.comments_only(),
            sample4,
        ),
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


def test_receipt_round_trips_and_holds_no_instructor_sentence(
    tmp_path, phil210, profile
):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    _write(root, phil210, installed)
    loaded = load_receipt(receipt_path(root, "phil210"))
    assert loaded.passed is True
    assert loaded.samples == 3
    assert loaded.required_holdouts == 2
    assert len(loaded.holdouts) == 2
    blob = receipt_path(root, "phil210").read_text(encoding="utf-8")
    sample = load_sample(phil210 / PAPER_5)
    assert sample.bottom_line[:30] not in blob
    assert "David" not in blob and "Morales" not in blob
    assert receipt_path(root, "phil210").stat().st_mode & 0o777 == 0o600


def test_check_reports_no_receipt_then_unapproved_then_current(
    tmp_path, phil210, profile
):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "no-receipt"
    )
    _write(root, phil210, installed)
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "unapproved"
    )
    approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=rubric,
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
            root,
            course="phil210",
            profile=installed,
            rubric=phil210 / "grading_rubric.md",
            approval="yes",
        )


def test_a_gap_needs_an_accepted_reason(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    _write(root, phil210, installed, totals=(96, None))
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "gap"
    )
    with pytest.raises(CalibrationError, match="accept-gap"):
        approve_calibration(
            root,
            course="phil210",
            profile=installed,
            rubric=rubric,
            approval=approval_phrase("phil210"),
        )
    with pytest.raises(CalibrationError, match="reason"):
        approve_calibration(
            root,
            course="phil210",
            profile=installed,
            rubric=rubric,
            approval=approval_phrase("phil210"),
            accept_gap=True,
            reason="  ",
        )
    approval = approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=rubric,
        approval=approval_phrase("phil210"),
        accept_gap=True,
        reason="The holdout was an unusual paper.",
    )
    assert approval.gap_accepted is True
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "current"
    )


def test_editing_the_profile_makes_the_receipt_and_approval_stale(
    tmp_path, phil210, profile
):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    _write(root, phil210, installed)
    approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=rubric,
        approval=approval_phrase("phil210"),
    )
    installed.write_text(
        installed.read_text(encoding="utf-8") + "\n- extra\n", encoding="utf-8"
    )
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "stale-receipt"
    )


def test_a_changed_receipt_makes_the_approval_stale(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    _write(root, phil210, installed)
    approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=rubric,
        approval=approval_phrase("phil210"),
    )
    _write(root, phil210, installed, totals=(84, None))
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "stale-approval"
    )


def test_a_malformed_receipt_is_reported(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    path = receipt_path(root, "phil210")
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    assert (
        check_calibration(
            root, course="phil210", profile=installed, rubric=phil210 / "grading_rubric.md"
        ).state
        == "malformed-receipt"
    )


def test_synthetic_approval_needs_the_demo_marker(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = phil210 / "grading_rubric.md"
    approve_synthetic(root, course="phil210", profile=installed, rubric=rubric)
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "unapproved"
    )
    (root / "demo.json").write_text(
        json.dumps({"authorization": "synthetic-demo"}), encoding="utf-8"
    )
    assert (
        check_calibration(root, course="phil210", profile=installed, rubric=rubric).state
        == "current"
    )


def _calibrate(*args):
    return subprocess.run(
        [sys.executable, "scripts/calibrate.py", *args],
        capture_output=True,
        text=True,
    )


def _packet(tmp_path, phil210):
    out = tmp_path / "inputs.json"
    result = _calibrate(
        "inputs",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--samples", str(phil210 / "graded"),
        "--roster", str(phil210 / "roster.csv"),
        "--holdout", "paper_5_graded_subscription_software_paradox.md",
        "--holdout", "paper_4_graded_predictive_policing.md",
        "--out", str(out),
    )
    assert result.returncode == 0, result.stderr
    return out


def _holdout_pair(drafts: Path, token: str, total: int, letter: str, feedback: str):
    _record(total, letter, token).save(drafts / f"{token}.json")
    (drafts / f"{token}.md").write_text(feedback, encoding="utf-8")


def test_holdout_cli_writes_a_receipt_and_exits_by_outcome(
    tmp_path, phil210, profile
):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    text = installed.read_text(encoding="utf-8").replace(
        "**Samples**: 4", "**Samples**: 3"
    )
    installed.write_text(text, encoding="utf-8")
    packet = _packet(tmp_path, phil210)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    sample5 = load_sample(phil210 / PAPER_5)
    sample4 = load_sample(phil210 / PAPER_4)
    _holdout_pair(
        drafts, "STUDENT_01", sample5.score + 1, sample5.letter, sample5.comments_only()
    )
    _holdout_pair(
        drafts, "STUDENT_02", sample4.score, sample4.letter, sample4.comments_only()
    )
    common = [
        "holdout",
        "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--packet", str(packet),
        "--root", str(root),
        "--record", str(drafts / "STUDENT_01.json"),
        "--draft", str(drafts / "STUDENT_01.md"),
        "--sample", str(phil210 / PAPER_5),
        "--record", str(drafts / "STUDENT_02.json"),
        "--draft", str(drafts / "STUDENT_02.md"),
        "--sample", str(phil210 / PAPER_4),
    ]
    result = _calibrate(*common)
    assert result.returncode == 0, result.stderr
    assert "2 holdouts" in result.stdout
    assert load_receipt(receipt_path(root, "phil210")).passed is True

    _holdout_pair(drafts, "STUDENT_01", 99, "A", sample5.comments_only())
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
        "holdout",
        "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--packet", str(packet),
        "--root", str(root),
        "--record", str(drafts / "STUDENT_01.json"),
        "--draft", str(drafts / "STUDENT_01.md"),
        "--sample", str(phil210 / PAPER_5),
        "--record", str(drafts / "STUDENT_01.json"),
        "--draft", str(drafts / "STUDENT_01.md"),
        "--sample", str(phil210 / PAPER_4),
    )
    assert result.returncode == 13
    assert "Samples" in result.stderr
    assert not receipt_path(root, "phil210").exists()


def test_approve_and_check_cli(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = str(phil210 / "grading_rubric.md")
    check = _calibrate("check", "--course", "phil210", "--rubric", rubric, "--root", str(root))
    assert check.returncode == 14
    assert "no-receipt" in check.stderr
    _write(root, phil210, installed)
    approve = _calibrate(
        "approve", "--course", "phil210", "--rubric", rubric, "--root", str(root),
        "--approval", "Approve calibration profile for phil210",
    )
    assert approve.returncode == 0, approve.stderr
    check = _calibrate("check", "--course", "phil210", "--rubric", rubric, "--root", str(root))
    assert check.returncode == 0, check.stderr


def test_approve_cli_refuses_a_gap_without_a_reason(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    rubric = str(phil210 / "grading_rubric.md")
    _write(root, phil210, installed, totals=(97, None))
    approve = _calibrate(
        "approve", "--course", "phil210", "--rubric", rubric, "--root", str(root),
        "--approval", "Approve calibration profile for phil210",
    )
    assert approve.returncode == 14
    assert "accept-gap" in approve.stderr
    assert not approval_path(root, "phil210").exists()
