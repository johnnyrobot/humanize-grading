"""The instructor sees pilot drafts and consistency flags before anything moves on."""

import json

import pytest

from scripts.grading.records import CriterionScore, GradeRecord, Justification
from scripts.grading.review import (
    DraftArtifacts,
    ReviewError,
    ack_path,
    ack_phrase,
    approve_review,
    draft_phrase,
    feedback_digest,
    load_acknowledgement,
    load_review,
    pilot_phrase,
    review_findings,
    review_path,
    write_acknowledgement,
)


def _record(token="STUDENT_01", offset=None, score=20):
    justification = Justification(
        quote="a quote",
        offset=offset,
        line=3 if offset is not None else None,
        length=7 if offset is not None else None,
    )
    return GradeRecord(
        batch="essay-2",
        token=token,
        source=f"redacted/{token}.md",
        course="phil210",
        rubric="r.md",
        profile="p.md",
        criteria=(
            CriterionScore(1, "Thesis", 25, score, "Proficient", True, (justification,)),
        ),
        total=score,
        total_possible=25,
        letter="B",
        pattern_density={},
    )


def _artifacts(token="STUDENT_01", feedback="Good.", offset=None, score=20):
    return DraftArtifacts(
        token, feedback_digest(feedback), _record(token, offset, score).review_digest()
    )


def test_review_digest_ignores_offsets_but_not_scores():
    assert _record().review_digest() == _record(offset=12).review_digest()
    assert _record(score=19).review_digest() != _record().review_digest()


def test_pilot_approval_round_trips(tmp_path):
    path = review_path(tmp_path)
    artifacts = [_artifacts("STUDENT_01"), _artifacts("STUDENT_02", "Fine.")]
    review = approve_review(
        path, batch="essay-2", course="phil210", cadence="pilot", pilot=2,
        scope="pilot", artifacts=artifacts, approval=pilot_phrase("essay-2"),
    )
    assert review.pilot_approval().tokens == ("STUDENT_01", "STUDENT_02")
    loaded = load_review(path, batch="essay-2", course="phil210")
    assert loaded.pilot_approval().artifacts == tuple(artifacts)
    assert path.stat().st_mode & 0o777 == 0o600


def test_approval_needs_the_exact_phrase_and_matching_scope(tmp_path):
    path = review_path(tmp_path)
    with pytest.raises(ReviewError, match="exactly"):
        approve_review(
            path, batch="essay-2", course="phil210", cadence="pilot", pilot=2,
            scope="pilot", artifacts=[_artifacts()], approval="ok",
        )
    with pytest.raises(ReviewError, match="cadence"):
        approve_review(
            path, batch="essay-2", course="phil210", cadence="every", pilot=2,
            scope="pilot", artifacts=[_artifacts()], approval=pilot_phrase("essay-2"),
        )
    with pytest.raises(ReviewError, match="one token"):
        approve_review(
            path, batch="essay-2", course="phil210", cadence="every", pilot=2,
            scope="draft", artifacts=[_artifacts(), _artifacts("STUDENT_02")],
            approval=draft_phrase("STUDENT_01", "essay-2"),
        )
    with pytest.raises(ReviewError, match="none"):
        approve_review(
            path, batch="essay-2", course="phil210", cadence="none", pilot=2,
            scope="pilot", artifacts=[_artifacts()], approval=pilot_phrase("essay-2"),
        )
    assert not path.exists()


def test_findings_require_then_accept_then_stale(tmp_path):
    drafted = {
        "STUDENT_01": _artifacts("STUDENT_01"),
        "STUDENT_02": _artifacts("STUDENT_02"),
    }
    findings = review_findings(
        cadence="pilot", pilot=2, total=5, drafted=drafted, review=None, batch="essay-2"
    )
    assert [f.code for f in findings] == ["draft-review-required"]
    assert "Approve pilot drafts for essay-2" in findings[0].message
    review = approve_review(
        review_path(tmp_path), batch="essay-2", course="phil210", cadence="pilot",
        pilot=2, scope="pilot", artifacts=list(drafted.values()),
        approval=pilot_phrase("essay-2"),
    )
    assert review_findings(
        cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2"
    ) == ()
    drafted["STUDENT_02"] = _artifacts("STUDENT_02", "Edited after approval.")
    findings = review_findings(
        cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2"
    )
    assert [(f.code, f.token) for f in findings] == [("draft-review-stale", "STUDENT_02")]
    drafted["STUDENT_02"] = _artifacts("STUDENT_02", "Fine.", score=18)
    findings = review_findings(
        cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2"
    )
    assert [(f.code, f.token) for f in findings] == [("draft-review-stale", "STUDENT_02")]


def test_a_pilot_smaller_than_the_batch_uses_the_pilot_size(tmp_path):
    drafted = {"STUDENT_01": _artifacts("STUDENT_01")}
    review = approve_review(
        review_path(tmp_path), batch="essay-2", course="phil210", cadence="pilot",
        pilot=2, scope="pilot", artifacts=list(drafted.values()),
        approval=pilot_phrase("essay-2"),
    )
    assert review_findings(
        cadence="pilot", pilot=2, total=1, drafted=drafted, review=review, batch="essay-2"
    ) == ()
    findings = review_findings(
        cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2"
    )
    assert [f.code for f in findings] == ["pilot-size-mismatch"]


def test_a_review_record_for_another_cadence_is_malformed(tmp_path):
    drafted = {"STUDENT_01": _artifacts("STUDENT_01")}
    review = approve_review(
        review_path(tmp_path), batch="essay-2", course="phil210", cadence="pilot",
        pilot=1, scope="pilot", artifacts=list(drafted.values()),
        approval=pilot_phrase("essay-2"),
    )
    findings = review_findings(
        cadence="pilot", pilot=2, total=5, drafted=drafted, review=review, batch="essay-2"
    )
    assert [f.code for f in findings] == ["malformed-draft-review"]


def test_every_cadence_needs_each_drafted_token(tmp_path):
    drafted = {
        "STUDENT_01": _artifacts("STUDENT_01"),
        "STUDENT_02": _artifacts("STUDENT_02"),
    }
    review = approve_review(
        review_path(tmp_path), batch="essay-2", course="phil210", cadence="every",
        pilot=2, scope="draft", artifacts=[drafted["STUDENT_01"]],
        approval=draft_phrase("STUDENT_01", "essay-2"),
    )
    findings = review_findings(
        cadence="every", pilot=2, total=2, drafted=drafted, review=review, batch="essay-2"
    )
    assert [(f.code, f.token) for f in findings] == [("draft-review-required", "STUDENT_02")]
    review = approve_review(
        review_path(tmp_path), batch="essay-2", course="phil210", cadence="every",
        pilot=2, scope="draft", artifacts=[drafted["STUDENT_02"]],
        approval=draft_phrase("STUDENT_02", "essay-2"),
    )
    assert review_findings(
        cadence="every", pilot=2, total=2, drafted=drafted, review=review, batch="essay-2"
    ) == ()


def test_cadence_none_needs_nothing():
    assert review_findings(
        cadence="none", pilot=2, total=3, drafted={}, review=None, batch="essay-2"
    ) == ()


def test_malformed_review_is_refused(tmp_path):
    path = review_path(tmp_path)
    path.write_text(
        json.dumps({"schema": "grading-draft-review/1", "batch": "other"}),
        encoding="utf-8",
    )
    with pytest.raises(ReviewError):
        load_review(path, batch="essay-2", course="phil210")
    assert load_review(tmp_path / "missing" / "_review.json", batch="essay-2", course="phil210") is None
    path.write_text(
        json.dumps({
            "schema": "grading-draft-review/1", "batch": "essay-2", "course": "phil210",
            "cadence": "pilot", "pilot": 2,
            "approvals": [{
                "scope": "pilot", "tokens": ["STUDENT_01"],
                "artifacts": [{"token": "STUDENT_01", "feedback_sha256": "a" * 64, "grade_sha256": "b" * 64}],
                "approval": "yes", "approved_at": "",
            }],
        }),
        encoding="utf-8",
    )
    with pytest.raises(ReviewError, match="exactly"):
        load_review(path, batch="essay-2", course="phil210")


def test_acknowledgement_binds_the_receipt(tmp_path):
    path = ack_path(tmp_path)
    write_acknowledgement(
        path, batch="essay-2", course="phil210", receipt_sha256="a" * 64,
        flags=[("outlier", "STUDENT_03 sits far from the batch median.")],
        approval=ack_phrase("essay-2"),
    )
    loaded = load_acknowledgement(path, batch="essay-2", course="phil210")
    assert loaded.receipt_sha256 == "a" * 64
    assert loaded.flag_count == 1
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ReviewError, match="exactly"):
        write_acknowledgement(
            path, batch="essay-2", course="phil210", receipt_sha256="a" * 64,
            flags=[], approval="fine",
        )
    assert load_acknowledgement(tmp_path / "none" / "_consistency.json", batch="essay-2", course="phil210") is None
    with pytest.raises(ReviewError, match="another"):
        load_acknowledgement(path, batch="essay-3", course="phil210")
