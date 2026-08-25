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
