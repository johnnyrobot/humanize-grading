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
