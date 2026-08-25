"""An interrupted batch resumes. It never regrades work that already has a draft."""

import subprocess
import sys

from scripts.grading.batch import DRAFTED, INCOMPLETE, ORPHAN, PENDING, summary, survey


def _batch(tmp_path):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    return redacted, drafts


def test_a_file_with_no_draft_is_pending(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    items = survey(redacted, drafts)
    assert [(item.token, item.state) for item in items] == [("STUDENT_01", PENDING)]


def test_a_file_with_both_a_draft_and_a_record_is_done(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_01.json").write_text("{}", encoding="utf-8")
    assert survey(redacted, drafts)[0].state == DRAFTED


def test_a_draft_without_its_record_is_incomplete(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    assert survey(redacted, drafts)[0].state == INCOMPLETE


def test_a_record_without_its_draft_is_incomplete(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.json").write_text("{}", encoding="utf-8")
    assert survey(redacted, drafts)[0].state == INCOMPLETE


def test_a_draft_with_no_redacted_source_is_an_orphan(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (drafts / "STUDENT_09.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_09.json").write_text("{}", encoding="utf-8")
    items = survey(redacted, drafts)
    assert items[0].state == ORPHAN
    assert items[0].source is None


def test_an_unknown_token_prefix_is_still_surveyed(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "UNKNOWN_01.md").write_text("essay", encoding="utf-8")
    assert survey(redacted, drafts)[0].token == "UNKNOWN_01"


def test_the_review_file_is_never_treated_as_a_submission(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "_review.json").write_text("{}", encoding="utf-8")
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    assert [item.token for item in survey(redacted, drafts)] == ["STUDENT_01"]


def test_items_come_back_in_token_order(tmp_path):
    redacted, drafts = _batch(tmp_path)
    for token in ["STUDENT_03", "STUDENT_01", "STUDENT_02"]:
        (redacted / f"{token}.md").write_text("essay", encoding="utf-8")
    assert [item.token for item in survey(redacted, drafts)] == [
        "STUDENT_01",
        "STUDENT_02",
        "STUDENT_03",
    ]


def test_the_summary_counts_each_state(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (redacted / "STUDENT_02.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")
    counts = summary(survey(redacted, drafts))
    assert counts[PENDING] == 1
    assert counts[DRAFTED] == 1


def test_a_missing_drafts_directory_reports_everything_as_pending(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    assert survey(redacted, tmp_path / "absent")[0].state == PENDING


def _run(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def test_the_status_subcommand_lists_what_is_left(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (redacted / "STUDENT_02.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")
    result = _run(
        "status", "--batch", "essay-2",
        "--redacted", str(redacted), "--drafts", str(drafts),
    )
    assert result.returncode == 0
    assert "STUDENT_01" in result.stdout
    assert "pending" in result.stdout


def test_the_density_subcommand_reports_a_count(phil210):
    result = _run(
        "density",
        str(phil210 / "ungraded/paper_7_ungraded_ai_healthcare_slop.md"),
    )
    assert result.returncode == 0
    assert "per 1000" in result.stdout


def test_the_density_subcommand_can_emit_json(phil210):
    import json

    result = _run(
        "density",
        str(phil210 / "ungraded/paper_7_ungraded_ai_healthcare_slop.md"),
        "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["count"] > 0
    assert "by_pattern" in payload
