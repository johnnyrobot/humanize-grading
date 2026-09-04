"""The synthetic demo enters the real grading lifecycle without human pauses."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import scripts.grading.demo as demo


REPO_ROOT = Path(__file__).resolve().parents[2]


def _drop_folder(source: Path, destination: Path) -> Path:
    destination.mkdir()
    shutil.copy2(source / "grading_rubric.md", destination / "grading_rubric.md")
    shutil.copytree(source / "graded", destination / "graded")
    shutil.copytree(source / "ungraded", destination / "ungraded")
    return destination


def _prepare(dropped: Path, runs: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/demo.py",
            "prepare",
            "--source",
            str(dropped),
            "--runs-root",
            str(runs),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_prepare_exact_synthetic_corpus_reaches_first_grading_action(
    tmp_path: Path, phil210: Path
):
    dropped = _drop_folder(phil210, tmp_path / "grading_mock_papers")
    runs = tmp_path / "runs"

    result = _prepare(dropped, runs)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "humanizer-grading-demo/1"
    assert payload["synthetic"] is True
    assert payload["status"] == "ready-for-grading"
    assert payload["batch"] == "synthetic-demo"
    assert payload["course"] == "phil210"
    assert payload["next_action"]["action"] == "prepare-record"

    state_root = Path(payload["state_root"])
    redacted = state_root / "redacted/synthetic-demo"
    assert sorted(path.name for path in redacted.glob("STUDENT_*.md")) == [
        "STUDENT_01.md",
        "STUDENT_02.md",
        "STUDENT_03.md",
        "STUDENT_04.md",
        "STUDENT_05.md",
    ]
    assert (state_root / "assignments/synthetic-demo.md").is_file()
    assert (state_root / "profiles/phil210.md").is_file()
    assert (state_root / "demo.json").is_file()


def test_prepare_marks_automatic_authorization_as_synthetic(
    tmp_path: Path, phil210: Path
):
    dropped = _drop_folder(phil210, tmp_path / "grading_mock_papers")

    result = _prepare(dropped, tmp_path / "runs")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    marker = json.loads(
        (Path(payload["state_root"]) / "demo.json").read_text(encoding="utf-8")
    )
    assert marker["authorization"] == "synthetic-demo"


def test_prepare_refuses_changed_corpus_before_creating_a_run(
    tmp_path: Path, phil210: Path
):
    dropped = _drop_folder(phil210, tmp_path / "grading_mock_papers")
    rubric = dropped / "grading_rubric.md"
    rubric.write_text(rubric.read_text(encoding="utf-8") + "\nchanged\n")
    runs = tmp_path / "runs"

    result = _prepare(dropped, runs)

    assert result.returncode == 13
    assert "Use the supervised grading workflow" in result.stderr
    assert not runs.exists()


def test_prepare_refuses_source_changed_between_validation_and_redaction(
    tmp_path: Path, phil210: Path, monkeypatch: pytest.MonkeyPatch
):
    dropped = _drop_folder(phil210, tmp_path / "grading_mock_papers")
    runs = tmp_path / "runs"
    real_redact = demo.redact
    changed = False

    def mutate_then_redact(**arguments):
        nonlocal changed
        if not changed:
            changed = True
            source = arguments["source"]
            paper = source / "paper_10_ungraded_smart_cities_hybrid_slop.md"
            paper.write_text(paper.read_text(encoding="utf-8") + "\nchanged\n")
        return real_redact(**arguments)

    monkeypatch.setattr(demo, "redact", mutate_then_redact)

    with pytest.raises(demo.DemoError, match="exact bundled synthetic corpus"):
        demo.prepare_demo(REPO_ROOT, dropped, runs_root=runs)

    assert not list(runs.rglob("demo.json"))
