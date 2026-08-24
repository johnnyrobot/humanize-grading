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
