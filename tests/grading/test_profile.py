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


def test_a_changed_letter_scale_is_reported_as_rubric_drift(
    profile, phil210, tmp_path
):
    text = (phil210 / "grading_rubric.md").read_text(encoding="utf-8")
    edited = tmp_path / "edited.md"
    edited.write_text(text.replace("93–100 (A)", "94–100 (A)"), encoding="utf-8")

    drifted = load_profile(profile).drift(load_rubric(edited))

    assert "grading scale or feedback protocol" in drifted


def test_a_corrupt_whole_rubric_digest_is_reported(profile, phil210, tmp_path):
    broken = tmp_path / "profile.md"
    broken.write_text(
        profile.read_text(encoding="utf-8").replace(
            "**Rubric digest**: 58a64521abb1",
            "**Rubric digest**: 000000000000",
        ),
        encoding="utf-8",
    )

    drifted = load_profile(broken).drift(
        load_rubric(phil210 / "grading_rubric.md")
    )

    assert drifted == ["whole rubric fingerprint"]


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


def test_course_names_cannot_escape_the_profile_directory(tmp_path):
    with pytest.raises(ValueError, match="course"):
        profile_path("../other-course", tmp_path)


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
    text = profile.read_text(encoding="utf-8").replace(
        "## Instructor answers", "## Notes"
    )
    edited = tmp_path / "phil210.md"
    edited.write_text(text, encoding="utf-8")
    with pytest.raises(ProfileError, match="Instructor answers"):
        load_profile(edited)
