"""A bad profile must surface in the first minute, not on the fortieth essay."""

import json
import re
import subprocess
import sys

from scripts.grading.holdout import compare, render, required_holdouts
from scripts.grading.records import CriterionScore, GradeRecord
from scripts.grading.samples import load_sample
from scripts.redaction.roster import load_roster

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


def _copy_graded_samples(phil210, destination):
    destination.mkdir()
    for source in (phil210 / "graded").iterdir():
        (destination / source.name).write_bytes(source.read_bytes())
    return destination


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
    # Whole words only. A plain substring check flags "Lin" inside the
    # rubric's own section name, "The Bottom Line", which the packet
    # legitimately carries. Scanning the full roster is also stronger than
    # spot-checking one student.
    for entry in load_roster(phil210 / "roster.csv"):
        for part in entry.name.split():
            assert not re.search(rf"\b{re.escape(part)}\b", blob), (
                f"{part} reached the calibration packet"
            )
    assert "[STUDENT]" in blob
    assert all("scrubbed_count" in item for item in payload["samples"])
    assert "scrubbed" not in payload["samples"][0]
    assert len(payload["samples"]) == 4
    assert payload["holdout"] == {"count": 1, "labels": ["holdout_1"]}
    assert [item["label"] for item in payload["samples"]] == [
        "sample_1", "sample_2", "sample_3", "sample_4",
    ]
    assert all("path" not in item for item in payload["samples"])


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


def test_calibrate_inputs_refuses_an_incomplete_roster_without_writing_or_naming(
    phil210, tmp_path
):
    roster = tmp_path / "incomplete.csv"
    roster.write_text(
        "name,student_id,email\nUnrelated Person,S9999999,other@example.edu\n",
        encoding="utf-8",
    )
    out = tmp_path / "inputs.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/calibrate.py",
            "inputs",
            "--rubric",
            str(phil210 / "grading_rubric.md"),
            "--samples",
            str(phil210 / "graded"),
            "--roster",
            str(roster),
            "--holdout",
            "paper_5_graded_subscription_software_paradox.md",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 13
    assert not out.exists()
    assert "exactly one roster entry" in result.stderr
    for private_name in ("Maya", "Samantha", "Marcus", "David"):
        assert private_name not in result.stderr


def test_calibrate_inputs_validates_the_holdout_identity_before_writing(
    phil210, tmp_path
):
    samples = _copy_graded_samples(phil210, tmp_path / "graded")
    holdout = samples / "paper_5_graded_subscription_software_paradox.md"
    holdout.write_text(
        holdout.read_text(encoding="utf-8").replace(
            "**Student**: David Morales  \n", "**Student** David Morales  \n"
        ),
        encoding="utf-8",
    )
    out = tmp_path / "inputs.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/calibrate.py",
            "inputs",
            "--rubric",
            str(phil210 / "grading_rubric.md"),
            "--samples",
            str(samples),
            "--roster",
            str(phil210 / "roster.csv"),
            "--holdout",
            holdout.name,
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 13
    assert not out.exists()
    assert "Student header" in result.stderr
    assert "David" not in result.stderr
    assert "Morales" not in result.stderr


def test_calibrate_inputs_refuses_ambiguous_normalized_roster_names(
    phil210, tmp_path
):
    roster = tmp_path / "ambiguous.csv"
    roster.write_text(
        (phil210 / "roster.csv").read_text(encoding="utf-8")
        + "  mAyA   LiN  ,S9999999,duplicate@example.edu\n",
        encoding="utf-8",
    )
    out = tmp_path / "inputs.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/calibrate.py",
            "inputs",
            "--rubric",
            str(phil210 / "grading_rubric.md"),
            "--samples",
            str(phil210 / "graded"),
            "--roster",
            str(roster),
            "--holdout",
            "paper_5_graded_subscription_software_paradox.md",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 13
    assert not out.exists()
    assert "exactly one roster entry" in result.stderr
    assert "Maya" not in result.stderr
    assert "Lin" not in result.stderr


def test_calibrate_inputs_matches_student_headers_with_case_and_space_changes(
    phil210, tmp_path
):
    samples = _copy_graded_samples(phil210, tmp_path / "graded")
    sample = samples / "paper_1_graded_algorithmic_gatekeeping.md"
    sample.write_text(
        sample.read_text(encoding="utf-8").replace(
            "**Student**: Maya Lin  \n", "**Student**:   mAyA   LiN  \n"
        ),
        encoding="utf-8",
    )
    out = tmp_path / "inputs.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/calibrate.py",
            "inputs",
            "--rubric",
            str(phil210 / "grading_rubric.md"),
            "--samples",
            str(samples),
            "--roster",
            str(phil210 / "roster.csv"),
            "--holdout",
            "paper_5_graded_subscription_software_paradox.md",
            "--out",
            str(out),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Maya" not in out.read_text(encoding="utf-8")


def test_a_name_bearing_filename_never_reaches_the_packet(phil210, tmp_path):
    # Canvas-style exports name files "Lastname_Firstname_...". scrub_names
    # cannot clean a filename -- it is case-sensitive, and its \b word
    # boundaries never fire inside an underscore-joined name -- so the fix
    # is to never carry filenames at all, not to scrub them.
    canvas_export = tmp_path / "canvas_export"
    canvas_export.mkdir()
    mapping = {
        "Lin_Maya_essay1.md": "paper_1_graded_algorithmic_gatekeeping.md",
        "Brooks_Tyler_essay1.md": "paper_3_graded_gig_economy_ai_slop.md",
        "essay_c.md": "paper_2_graded_carbon_pricing_developing_nations.md",
        "essay_d.md": "paper_4_graded_predictive_policing.md",
    }
    for dest_name, source_name in mapping.items():
        (canvas_export / dest_name).write_text(
            (phil210 / "graded" / source_name).read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    out = tmp_path / "inputs.json"
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(canvas_export),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "Lin_Maya_essay1.md",
            "--out", str(out),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    blob = out.read_text(encoding="utf-8")

    assert "Lin_Maya" not in blob
    assert "Lin_Maya" not in result.stdout
    assert "Brooks_Tyler" not in blob
    assert "Brooks_Tyler" not in result.stdout
    for entry in load_roster(phil210 / "roster.csv"):
        for part in entry.name.split():
            pattern = rf"\b{re.escape(part)}\b"
            assert not re.search(pattern, blob), f"{part} reached the packet"
            assert not re.search(pattern, result.stdout), f"{part} reached stdout"


def test_required_holdouts_rises_to_two_at_six_samples():
    assert required_holdouts(4) == 1
    assert required_holdouts(5) == 1
    assert required_holdouts(6) == 2
    assert required_holdouts(8) == 2


def test_calibrate_inputs_needs_two_holdouts_for_six_samples(phil210, tmp_path):
    six = tmp_path / "six"
    six.mkdir()
    for path in (phil210 / "graded").iterdir():
        (six / path.name).write_bytes(path.read_bytes())
    (six / "paper_1_second_copy.md").write_bytes(
        (phil210 / "graded" / "paper_1_graded_algorithmic_gatekeeping.md").read_bytes()
    )
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(six),
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


def test_calibrate_inputs_refuses_a_holdout_folder_inside_the_samples(phil210, tmp_path):
    result = subprocess.run(
        [
            sys.executable, "scripts/calibrate.py", "inputs",
            "--rubric", str(phil210 / "grading_rubric.md"),
            "--samples", str(phil210 / "graded"),
            "--roster", str(phil210 / "roster.csv"),
            "--holdout", "paper_5_graded_subscription_software_paradox.md",
            "--holdout", "paper_4_graded_predictive_policing.md",
            "--holdout-out", str(phil210 / "graded" / "blind"),
            "--out", str(tmp_path / "inputs.json"),
        ],
        capture_output=True, text=True,
    )
    assert result.returncode == 13
    assert "samples folder" in result.stderr
    assert not (phil210 / "graded" / "blind").exists()


def test_compare_uses_the_supplied_point_tolerance(phil210):
    sample = load_sample(phil210 / PAPER_5)
    loose = compare(_record(87, "B"), "Solid.", sample, point_tolerance=5)
    tight = compare(_record(87, "B"), "Solid.", sample, point_tolerance=2)
    assert not any("points" in finding for finding in loose.findings)
    assert any("points" in finding for finding in tight.findings)
