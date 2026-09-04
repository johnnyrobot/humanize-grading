"""grade.py review and consistency --acknowledge record instructor stops."""

import hashlib
import json
import subprocess
import sys

from scripts.grading.batch import BatchPaths, GradingBatch
from scripts.grading.density import PATTERNS_PATH
from scripts.grading.review import ack_path, load_acknowledgement, load_review, review_path
from tests.grading.test_verify import _inputs, _write_batch


def _grade(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def _common(phil210, redacted, drafts, root):
    return [
        "--batch", "essay-2",
        "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted),
        "--drafts", str(drafts),
        "--patterns", str(PATTERNS_PATH),
        "--root", str(root),
    ]


def _consistency(redacted, drafts, root, *extra):
    return _grade(
        "consistency",
        "--batch", "essay-2",
        "--course", "phil210",
        "--redacted", str(redacted),
        "--drafts", str(drafts),
        "--root", str(root),
        *extra,
    )


def test_review_approve_records_the_pilot_and_check_reports_it(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, approved_brief, pilot_approved=False
    )
    common = _common(phil210, redacted, drafts, root)
    check = _grade("review", "check", *common)
    assert check.returncode == 12
    assert "draft-review-required" in check.stdout
    result = _grade(
        "review", "approve", *common,
        "--scope", "pilot", "--approval", "Approve pilot drafts for essay-2",
    )
    assert result.returncode == 0, result.stderr
    review = load_review(review_path(drafts), batch="essay-2", course="phil210")
    assert review.pilot_approval().tokens == ("STUDENT_01",)
    check = _grade("review", "check", *common)
    assert check.returncode == 0, check.stderr


def test_review_approve_refuses_the_wrong_phrase_and_scope(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, approved_brief, pilot_approved=False
    )
    common = _common(phil210, redacted, drafts, root)
    result = _grade("review", "approve", *common, "--scope", "pilot", "--approval", "yes")
    assert result.returncode == 13
    assert "exactly" in result.stderr
    result = _grade(
        "review", "approve", *common,
        "--scope", "draft", "--token", "STUDENT_01",
        "--approval", "Approve draft STUDENT_01 for essay-2",
    )
    assert result.returncode == 13
    assert "cadence" in result.stderr
    assert not review_path(drafts).exists()


def test_review_approve_refuses_a_draft_with_verification_errors(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, approved_brief, pilot_approved=False
    )
    record = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    record["total"] = 99
    (drafts / "STUDENT_01.json").write_text(json.dumps(record), encoding="utf-8")
    result = _grade(
        "review", "approve", *_common(phil210, redacted, drafts, root),
        "--scope", "pilot", "--approval", "Approve pilot drafts for essay-2",
    )
    assert result.returncode == 12
    assert "total" in result.stdout + result.stderr
    assert not review_path(drafts).exists()


def test_review_approve_refuses_more_drafts_than_the_pilot(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, approved_brief, pilot_approved=False
    )
    from tests.grading.test_publication import _add_second_draft

    _add_second_draft(redacted, drafts)
    result = _grade(
        "review", "approve", *_common(phil210, redacted, drafts, root),
        "--scope", "pilot", "--approval", "Approve pilot drafts for essay-2",
    )
    assert result.returncode == 12
    assert "exactly 1" in result.stderr
    assert not review_path(drafts).exists()


def test_consistency_acknowledge_binds_the_current_receipt(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    result = _consistency(
        redacted, drafts, root, "--acknowledge", "Acknowledge consistency flags for essay-2"
    )
    assert result.returncode == 12
    assert "verification" in (result.stdout + result.stderr).lower()
    assert not ack_path(drafts).exists()
    assert GradingBatch.open(BatchPaths("essay-2", redacted, drafts)).verify(
        _inputs(phil210, root)
    ).persisted
    result = _consistency(redacted, drafts, root, "--acknowledge", "nope")
    assert result.returncode == 13
    assert not ack_path(drafts).exists()
    result = _consistency(
        redacted, drafts, root, "--acknowledge", "Acknowledge consistency flags for essay-2"
    )
    assert result.returncode == 0, result.stderr
    ack = load_acknowledgement(ack_path(drafts), batch="essay-2", course="phil210")
    assert ack.receipt_sha256 == hashlib.sha256(
        (drafts / "_verification.json").read_bytes()
    ).hexdigest()
    assert "Acknowledged" in result.stdout


def test_consistency_acknowledge_needs_a_course(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    result = _grade(
        "consistency", "--batch", "essay-2", "--redacted", str(redacted),
        "--drafts", str(drafts), "--root", str(root),
        "--acknowledge", "Acknowledge consistency flags for essay-2",
    )
    assert result.returncode == 13
    assert "--course" in result.stderr
