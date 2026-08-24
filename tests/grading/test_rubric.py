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
