"""An interrupted batch resumes. It never regrades work that already has a draft."""

import subprocess
import sys

from scripts.grading.batch import (
    DRAFTED,
    INCOMPLETE,
    ORPHAN,
    PENDING,
    BatchPaths,
    GradingBatch,
    Progress,
    summary,
    survey,
)


def _batch(tmp_path):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    return redacted, drafts


def test_grading_batch_reports_a_redacted_submission_as_pending(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [(item.token, item.progress) for item in report.entries] == [
        ("STUDENT_01", Progress.PENDING)
    ]
    assert report.blocking_findings == ()


def test_grading_batch_reports_a_complete_pair_as_drafted(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_01.json").write_text("{}", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert report.entries[0].progress is Progress.DRAFTED
    assert report.blocking_findings == ()


def test_grading_batch_reports_each_incomplete_pair_without_changing_progress(
    tmp_path,
):
    redacted, drafts = _batch(tmp_path)
    for token in ("STUDENT_01", "STUDENT_02"):
        (redacted / f"{token}.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [item.progress for item in report.entries] == [
        Progress.PENDING,
        Progress.PENDING,
    ]
    assert [(finding.token, finding.code) for finding in report.blocking_findings] == [
        ("STUDENT_01", "incomplete-pair"),
        ("STUDENT_02", "incomplete-pair"),
    ]


def test_grading_batch_reports_each_artifact_without_a_submission_as_orphaned(
    tmp_path,
):
    _, drafts = _batch(tmp_path)
    (drafts / "STUDENT_08.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_09.json").write_text("{}", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(
            batch="essay-2",
            redacted=tmp_path / "redacted",
            drafts=drafts,
        )
    ).status()

    assert [(item.token, item.progress) for item in report.entries] == [
        ("STUDENT_08", None),
        ("STUDENT_09", None),
    ]
    assert [(finding.token, finding.code) for finding in report.blocking_findings] == [
        ("STUDENT_08", "orphaned-artifact"),
        ("STUDENT_09", "orphaned-artifact"),
    ]


def test_grading_batch_blocks_duplicate_redacted_submissions_for_one_token(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay one", encoding="utf-8")
    (redacted / "STUDENT_01.txt").write_text("essay two", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [(entry.token, entry.progress, entry.source) for entry in report.entries] == [
        ("STUDENT_01", Progress.PENDING, None)
    ]
    assert [(finding.token, finding.code) for finding in report.blocking_findings] == [
        ("STUDENT_01", "duplicate-redacted-submission")
    ]


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


def test_the_status_subcommand_reports_progress_and_every_blocking_condition(tmp_path):
    redacted, drafts = _batch(tmp_path)
    for token in ("STUDENT_01", "STUDENT_02", "STUDENT_03"):
        (redacted / f"{token}.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")
    (drafts / "STUDENT_03.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_09.json").write_text("{}", encoding="utf-8")

    result = _run(
        "status",
        "--batch",
        "essay-2",
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
    )

    assert result.returncode == 0
    assert "STUDENT_01  pending" in result.stdout
    assert "STUDENT_02  drafted" in result.stdout
    assert "STUDENT_03  incomplete" in result.stdout
    assert "STUDENT_09  orphan" in result.stdout
    assert "1 pending, 1 drafted, 1 incomplete, 1 orphaned" in result.stdout
    assert "An incomplete submission" in result.stderr
    assert "An orphaned artifact" in result.stderr


def test_the_status_subcommand_prints_duplicate_submission_findings(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.md").write_text("essay one", encoding="utf-8")
    (redacted / "STUDENT_01.txt").write_text("essay two", encoding="utf-8")

    result = _run(
        "status",
        "--batch",
        "essay-2",
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
    )

    assert result.returncode == 0
    assert "duplicate-redacted-submission [STUDENT_01]" in result.stderr


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
