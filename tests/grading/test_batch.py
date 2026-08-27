"""An interrupted batch resumes. It never regrades work that already has a draft."""

import subprocess
import sys

from scripts.grading.batch import BatchPaths, GradingBatch, Progress
from scripts.redaction.manifest import RedactionManifest


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


def test_uppercase_submission_extensions_remain_in_the_grading_batch(tmp_path):
    redacted, drafts = _batch(tmp_path)
    (redacted / "STUDENT_01.DOCX").write_bytes(b"synthetic")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [(item.token, item.progress) for item in report.entries] == [
        ("STUDENT_01", Progress.PENDING)
    ]


def test_symlinked_grading_artifacts_are_blocking_not_authoritative(tmp_path):
    redacted, drafts = _batch(tmp_path)
    outside = tmp_path / "raw.md"
    outside.write_text("private", encoding="utf-8")
    (redacted / "STUDENT_01.md").symlink_to(outside)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [finding.code for finding in report.blocking_findings] == [
        "symlink-artifact"
    ]
    assert report.entries[0].source is None


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


def test_status_report_owns_display_states_and_counts(tmp_path):
    redacted, drafts = _batch(tmp_path)
    for token in ("STUDENT_01", "STUDENT_02", "STUDENT_03"):
        (redacted / f"{token}.md").write_text("essay", encoding="utf-8")
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_02.json").write_text("{}", encoding="utf-8")
    (drafts / "STUDENT_03.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_09.json").write_text("{}", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [report.display_state(entry) for entry in report.entries] == [
        "pending",
        "drafted",
        "incomplete",
        "orphan",
    ]
    assert report.counts() == {
        "pending": 1,
        "drafted": 1,
        "verified": 0,
        "rehydrated": 0,
        "incomplete": 1,
        "orphan": 1,
    }


def test_grading_batch_inventory_is_sorted_and_ignores_reserved_files(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "_review.json").write_text("{}", encoding="utf-8")
    for token in ("UNKNOWN_03", "STUDENT_02", "STUDENT_01"):
        (redacted / f"{token}.md").write_text("essay", encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(
            batch="essay-2",
            redacted=redacted,
            drafts=tmp_path / "absent",
        )
    ).status()

    assert [entry.token for entry in report.entries] == [
        "STUDENT_01",
        "STUDENT_02",
        "UNKNOWN_03",
    ]
    assert all(entry.progress is Progress.PENDING for entry in report.entries)


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


def _run(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def _record_prepare_context(tmp_path, phil210, approved_brief, *, reviewed=True):
    root = tmp_path / ".grading"
    profile = root / "profiles" / "phil210.md"
    profile.parent.mkdir(parents=True)
    profile.write_bytes((phil210 / "profile_phil210.md").read_bytes())
    approved_brief(
        root / "assignments" / "essay-2.md",
        batch="essay-2",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    )
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    source = redacted / "STUDENT_01.md"
    source.write_text("Community solar needs a named causal mechanism.", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [source],
        reviewed=reviewed,
        exit_code=0,
    ).save(redacted)
    return root, redacted, source


def test_record_prepare_creates_the_pending_draft_pair(
    tmp_path, phil210, approved_brief
):
    root, redacted, source = _record_prepare_context(
        tmp_path, phil210, approved_brief
    )

    result = _run(
        "record",
        "prepare",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--source",
        str(source),
        "--redacted",
        str(redacted),
        "--root",
        str(root),
    )

    assert result.returncode == 0
    assert "Fill every criterion" in result.stdout
    assert (root / "drafts" / "essay-2" / "STUDENT_01.json").is_file()
    assert (root / "drafts" / "essay-2" / "STUDENT_01.md").is_file()


def _record_prepare_run(root, redacted, source, phil210):
    return _run(
        "record",
        "prepare",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--source",
        str(source),
        "--redacted",
        str(redacted),
        "--root",
        str(root),
    )


def _assert_plain_record_prepare_failure(result):
    assert result.returncode != 0
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    assert len(result.stderr.strip().splitlines()) == 1


def test_record_prepare_refuses_a_missing_or_unapproved_brief(
    tmp_path, phil210, approved_brief
):
    for condition in ("missing", "unapproved"):
        case = tmp_path / condition
        root, redacted, source = _record_prepare_context(
            case, phil210, approved_brief
        )
        brief = root / "assignments" / "essay-2.md"
        if condition == "missing":
            brief.unlink()
        else:
            brief.write_text(
                brief.read_text(encoding="utf-8").replace(
                    "status: approved", "status: draft", 1
                ),
                encoding="utf-8",
            )

        result = _record_prepare_run(root, redacted, source, phil210)

        _assert_plain_record_prepare_failure(result)
        assert "assignment brief" in result.stderr.lower()


def test_record_prepare_refuses_an_unreviewed_redaction(
    tmp_path, phil210, approved_brief
):
    root, redacted, source = _record_prepare_context(
        tmp_path, phil210, approved_brief, reviewed=False
    )

    result = _record_prepare_run(root, redacted, source, phil210)

    _assert_plain_record_prepare_failure(result)
    assert "review" in result.stderr.lower()


def test_record_prepare_refuses_a_non_pending_token(
    tmp_path, phil210, approved_brief
):
    root, redacted, source = _record_prepare_context(
        tmp_path, phil210, approved_brief
    )
    drafts = root / "drafts" / "essay-2"
    drafts.mkdir(parents=True)
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
    (drafts / "STUDENT_01.json").write_text("{}", encoding="utf-8")

    result = _record_prepare_run(root, redacted, source, phil210)

    _assert_plain_record_prepare_failure(result)
    assert "pending" in result.stderr.lower()


def test_record_prepare_refuses_an_incomplete_or_orphaned_batch(
    tmp_path, phil210, approved_brief
):
    for condition in ("incomplete", "orphaned"):
        case = tmp_path / condition
        root, redacted, source = _record_prepare_context(
            case, phil210, approved_brief
        )
        drafts = root / "drafts" / "essay-2"
        drafts.mkdir(parents=True)
        if condition == "incomplete":
            (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")
        else:
            (drafts / "STUDENT_09.json").write_text("{}", encoding="utf-8")

        result = _record_prepare_run(root, redacted, source, phil210)

        _assert_plain_record_prepare_failure(result)
        assert condition[:-1] in result.stderr.lower()


def test_record_prepare_refuses_a_source_outside_redacted(
    tmp_path, phil210, approved_brief
):
    root, redacted, _ = _record_prepare_context(
        tmp_path, phil210, approved_brief
    )
    outside = tmp_path / "outside" / "STUDENT_01.md"
    outside.parent.mkdir()
    outside.write_text("Not the manifest-bound source.", encoding="utf-8")

    result = _record_prepare_run(root, redacted, outside, phil210)

    _assert_plain_record_prepare_failure(result)
    assert "redacted directory" in result.stderr.lower()


def test_record_prepare_preserves_an_existing_destination(
    tmp_path, phil210, approved_brief
):
    root, redacted, source = _record_prepare_context(
        tmp_path, phil210, approved_brief
    )
    drafts = root / "drafts" / "essay-2"
    drafts.mkdir(parents=True)
    existing = drafts / "STUDENT_01.json"
    existing.write_bytes(b"keep these bytes")

    result = _record_prepare_run(root, redacted, source, phil210)

    _assert_plain_record_prepare_failure(result)
    assert existing.read_bytes() == b"keep these bytes"
    assert not (drafts / "STUDENT_01.md").exists()


def test_record_prepare_refuses_a_profile_for_another_course(
    tmp_path, phil210, approved_brief
):
    root, redacted, source = _record_prepare_context(
        tmp_path, phil210, approved_brief
    )
    profile = root / "profiles" / "phil210.md"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "**Course**: phil210", "**Course**: another-course", 1
        ),
        encoding="utf-8",
    )
    brief = root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(
        brief,
        batch="essay-2",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    )

    result = _record_prepare_run(root, redacted, source, phil210)

    _assert_plain_record_prepare_failure(result)
    assert "another-course" in result.stderr
    assert "phil210" in result.stderr
    assert not (root / "drafts" / "essay-2" / "STUDENT_01.json").exists()
    assert not (root / "drafts" / "essay-2" / "STUDENT_01.md").exists()


def test_record_prepare_collapses_newlines_in_runtime_error_messages(
    tmp_path, phil210
):
    root = tmp_path / "missing\nprofile-root"
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    source = redacted / "STUDENT_01.md"
    source.write_text("A redacted essay.", encoding="utf-8")

    result = _record_prepare_run(root, redacted, source, phil210)

    _assert_plain_record_prepare_failure(result)
    assert "missing profile-root" in result.stderr


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
    redacted = phil210 / "ungraded"
    result = _run(
        "density",
        str(redacted / "paper_7_ungraded_ai_healthcare_slop.md"),
        "--redacted",
        str(redacted),
    )
    assert result.returncode == 0
    assert "per 1000" in result.stdout


def test_density_refuses_a_file_outside_the_redacted_directory(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    raw = tmp_path / "submissions" / "paper.md"
    raw.parent.mkdir()
    raw.write_text("private pivotal text", encoding="utf-8")

    result = _run(
        "density", str(raw), "--redacted", str(redacted), "--json"
    )

    assert result.returncode == 13
    assert "inside the configured redacted directory" in result.stderr
    assert "private" not in result.stdout


def test_density_reports_oversized_redacted_input_without_a_traceback(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    oversized = redacted / "STUDENT_01.md"
    with oversized.open("wb") as handle:
        handle.truncate(26 * 1024 * 1024)

    result = _run(
        "density", str(oversized), "--redacted", str(redacted), "--json"
    )

    assert result.returncode == 13
    assert "too large" in result.stderr
    assert "Traceback" not in result.stderr


def test_the_density_subcommand_can_emit_json(phil210):
    import json

    redacted = phil210 / "ungraded"
    result = _run(
        "density",
        str(redacted / "paper_7_ungraded_ai_healthcare_slop.md"),
        "--redacted",
        str(redacted),
        "--json",
    )
    payload = json.loads(result.stdout)
    assert payload["count"] > 0
    assert "by_pattern" in payload
