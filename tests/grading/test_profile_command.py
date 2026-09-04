"""grade.py profile says whether a course may grade today."""

import subprocess
import sys

from scripts.grading.calibration import approval_phrase, approve_calibration
from tests.grading.test_calibration import _install_profile, _write


def _profile_cmd(root, phil210):
    return subprocess.run(
        [
            sys.executable, "scripts/grade.py", "profile",
            "--course", "phil210",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--root", str(root),
        ],
        capture_output=True,
        text=True,
    )


def test_profile_exits_fourteen_until_calibration_is_approved(
    tmp_path, phil210, profile
):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    result = _profile_cmd(root, phil210)
    assert result.returncode == 14
    assert "no-receipt" in result.stderr
    _write(root, phil210, installed)
    result = _profile_cmd(root, phil210)
    assert result.returncode == 14
    assert "unapproved" in result.stderr
    approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=phil210 / "grading_rubric.md",
        approval=approval_phrase("phil210"),
    )
    result = _profile_cmd(root, phil210)
    assert result.returncode == 0, result.stderr
    assert "approved" in result.stdout
    assert "warning" not in result.stdout


def test_profile_warns_when_calibration_is_old(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    installed.write_text(
        installed.read_text(encoding="utf-8").replace(
            "**Calibrated**: 2026-08-24", "**Calibrated**: 2025-01-01"
        ),
        encoding="utf-8",
    )
    _write(root, phil210, installed)
    approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=phil210 / "grading_rubric.md",
        approval=approval_phrase("phil210"),
    )
    result = _profile_cmd(root, phil210)
    assert result.returncode == 0, result.stderr
    assert "warning recalibrate" in result.stdout
    assert "days" in result.stdout


def test_profile_warns_after_many_briefs_use_it(tmp_path, phil210, profile, approved_brief):
    root = tmp_path / ".grading"
    installed = _install_profile(root, profile)
    _write(root, phil210, installed)
    approve_calibration(
        root,
        course="phil210",
        profile=installed,
        rubric=phil210 / "grading_rubric.md",
        approval=approval_phrase("phil210"),
    )
    for index in range(5):
        approved_brief(
            root / "assignments" / f"essay-{index}.md",
            batch=f"essay-{index}",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=installed,
        )
    result = _profile_cmd(root, phil210)
    assert result.returncode == 0, result.stderr
    assert "5 approved assignment briefs" in result.stdout
