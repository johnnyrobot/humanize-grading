"""No draft reaches an instructor until every claim in it has been checked."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.grading.batch import (
    BatchPaths,
    GradingBatch,
    Progress,
    VerificationFailure,
    VerificationInputs,
)
from scripts.grading.brief import check_brief
from scripts.grading.density import PATTERNS_PATH, load_terms
from scripts.grading.profile import load_profile
from scripts.grading.records import (
    CANNOT_ASSESS,
    CriterionScore,
    GradeRecord,
    Justification,
)
from scripts.grading.rubric import load_rubric
from scripts.grading.verify import errors, verify_draft
from scripts.redaction.manifest import RedactionManifest

SOURCE = """# Essay 2

Community solar cooperatives face an interconnection bottleneck that state
regulators have only begun to address. This paper argues that the bottleneck
is an economic conflict of interest, not a bureaucratic accident.

Under a rate-of-return model, utility profits are tied to capital investment
in the utility's own infrastructure, so distributed generation cuts the
justification for that spending.

New York's VDER framework established standardized hosting capacity maps.
"""

FEEDBACK = """[The framing here is sharp. Say it in the first sentence next time.]

**The Bottom Line**

Your central claim holds. You named the mechanism instead of gesturing at it.

**What Worked**

* Paragraph 2 ties the rate-of-return model to the interconnection queue.

**What to Work On**

* Give the VDER claim a citation.

— Dr. Vance
"""


@pytest.fixture(scope="module")
def terms():
    return load_terms(PATTERNS_PATH)


@pytest.fixture
def rubric(phil210):
    return load_rubric(phil210 / "grading_rubric.md")


@pytest.fixture
def loaded_profile(profile):
    return load_profile(profile)


def _record(**overrides) -> GradeRecord:
    criteria = [
        CriterionScore(
            number=1,
            name="Thesis & Theoretical Framing",
            points_possible=25,
            score=21,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="the bottleneck is an economic conflict of interest"),
            ),
        ),
        CriterionScore(
            number=2,
            name="Evidence & Empirical Grounding",
            points_possible=25,
            score=20,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="New York's VDER framework established standardized"),
            ),
        ),
        CriterionScore(
            number=3,
            name="Counterargument & Critical Analysis",
            points_possible=25,
            score=19,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="utility profits are tied to capital investment"),
            ),
        ),
        CriterionScore(
            number=4,
            name="Structure, Prose & Academic Voice",
            points_possible=25,
            score=22,
            level="Proficient",
            assessable=True,
            justifications=(
                Justification(quote="Community solar cooperatives face an interconnection"),
            ),
        ),
    ]
    from scripts.grading.density import scan

    density = scan(SOURCE, load_terms(PATTERNS_PATH)).as_dict()
    values = {
        "batch": "essay-2",
        "token": "STUDENT_01",
        "source": "redacted/STUDENT_01.md",
        "course": "phil210",
        "rubric": "tests/fixtures/phil210/grading_rubric.md",
        "profile": "tests/fixtures/phil210/profile_phil210.md",
        "criteria": tuple(criteria),
        "total": 82,
        "total_possible": 100,
        # 82 of 100 lands in the rubric's 80-82 band, which is "B-", not "B".
        # The verifier recomputes this, so a wrong letter here fails the test.
        "letter": "B-",
        "pattern_density": density,
    }
    values.update(overrides)
    return GradeRecord(**values)


def test_a_clean_draft_produces_no_errors(rubric, loaded_profile, terms):
    findings = verify_draft(_record(), FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert errors(findings) == []


def test_verification_writes_the_offsets_the_model_did_not(rubric, loaded_profile, terms):
    record = _record()
    verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    justification = record.criteria[0].justifications[0]
    assert justification.offset is not None
    # The quote spans a line break in the source, so the located text is
    # "the bottleneck\nis an economic ...". That is the whole point of
    # matching whitespace flexibly.
    assert SOURCE[justification.offset :].startswith("the bottleneck")
    assert justification.length > len("the bottleneck is an economic conflict")
    assert justification.line == 4


def test_a_fabricated_quote_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].justifications[0].quote = "the author cites forty seven studies"
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "quote" for finding in errors(findings))


def test_a_scored_criterion_with_no_quote_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].justifications = ()
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "no-quote" for finding in errors(findings))


def test_a_cannot_assess_criterion_needs_no_quote(rubric, loaded_profile, terms):
    # Dropping criterion 2 leaves 21 + 19 + 22 = 62 of 75 possible, which is
    # 82.7 percent and lands in the "B" band.
    record = _record(total=62, total_possible=75, letter="B")
    criteria = list(record.criteria)
    criteria[1] = CriterionScore(
        number=2,
        name="Evidence & Empirical Grounding",
        points_possible=25,
        score=None,
        level=CANNOT_ASSESS,
        assessable=False,
        justifications=(),
    )
    record.criteria = tuple(criteria)
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert errors(findings) == []
    assert any(finding.code == "partial" for finding in findings)


def test_a_cannot_assess_criterion_carrying_a_score_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[1].assessable = False
    record.criteria[1].level = CANNOT_ASSESS
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "unassessable-score" for finding in errors(findings))


def test_a_total_that_does_not_add_up_fails(rubric, loaded_profile, terms):
    findings = verify_draft(_record(total=95), FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "total" for finding in errors(findings))


def test_a_score_above_the_criterion_maximum_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].score = 40
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "score-range" for finding in errors(findings))


def test_a_letter_that_does_not_match_the_scale_fails(rubric, loaded_profile, terms):
    findings = verify_draft(_record(letter="A"), FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "letter" for finding in errors(findings))


def test_a_missing_criterion_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria = record.criteria[:3]
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "criteria-mismatch" for finding in errors(findings))


def test_invented_density_figures_fail(rubric, loaded_profile, terms):
    record = _record()
    record.pattern_density = {"words": 10, "count": 99, "per_thousand": 9900.0,
                             "by_pattern": {"7": 99}, "examples": []}
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "density-mismatch" for finding in errors(findings))


def test_every_density_field_must_match_the_fresh_scan(rubric, loaded_profile, terms):
    record = _record()
    record.pattern_density["per_thousand"] = 999.0
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "density-mismatch" for finding in errors(findings))


def test_a_missing_feedback_section_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("**What Worked**", "**Nice Things**")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "section-missing" for finding in errors(findings))


def test_section_names_in_prose_do_not_satisfy_the_protocol(
    rubric, loaded_profile, terms
):
    broken = FEEDBACK.replace(
        "**What Worked**",
        "This paragraph says What Worked, but it is not a section heading.",
    )
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "section-missing" for finding in errors(findings))


def test_a_required_feedback_section_must_have_content(
    rubric, loaded_profile, terms
):
    broken = FEEDBACK.replace(
        "* Paragraph 2 ties the rate-of-return model to the interconnection queue.\n",
        "",
    )
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "section-empty" for finding in errors(findings))


def test_a_missing_sign_off_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("— Dr. Vance", "Regards,")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "sign-off" for finding in errors(findings))


def test_missing_inline_annotations_fail(rubric, loaded_profile, terms):
    broken = "\n".join(FEEDBACK.splitlines()[2:])
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "no-annotations" for finding in errors(findings))


def test_bracketed_filler_inside_prose_is_not_an_inline_annotation(
    rubric, loaded_profile, terms
):
    broken = "\n".join(FEEDBACK.splitlines()[2:]).replace(
        "Your central claim holds.", "Your central claim holds [generic filler text]."
    )
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "no-annotations" for finding in errors(findings))


def test_a_never_use_phrase_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("Your central claim holds.", "Great job! Your claim holds.")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "never-use" for finding in errors(findings))


def test_a_token_the_source_never_held_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("Your central claim", "STUDENT_09, your central claim")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "stray-token" for finding in errors(findings))


def test_a_foreign_source_token_cannot_be_copied_into_feedback(
    rubric, loaded_profile, terms
):
    source = SOURCE + "\nA classmate appears as STUDENT_02.\n"
    broken = FEEDBACK.replace("Your central claim", "STUDENT_02, your central claim")
    findings = verify_draft(_record(), broken, source, rubric, loaded_profile, terms)
    assert any(finding.code == "foreign-token" for finding in errors(findings))


def test_an_unknown_rubric_level_fails(rubric, loaded_profile, terms):
    record = _record()
    record.criteria[0].level = "Invented Level"
    findings = verify_draft(record, FEEDBACK, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "level" for finding in errors(findings))


def test_watched_terms_in_the_feedback_are_a_warning_not_an_error(
    rubric, loaded_profile, terms
):
    wordy = FEEDBACK.replace(
        "Your central claim holds.",
        "Your central claim showcases a pivotal interplay.",
    )
    findings = verify_draft(_record(), wordy, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "watched-term" for finding in findings)
    assert not any(finding.code == "watched-term" for finding in errors(findings))


def _write_batch(tmp_path, phil210, approved_brief, feedback=FEEDBACK):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    (redacted / "STUDENT_01.md").write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(redacted)
    (drafts / "STUDENT_01.md").write_text(feedback, encoding="utf-8")
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    profile_path = root / "profiles" / "phil210.md"
    profile_path.write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    approved_brief(
        root / "assignments" / "essay-2.md",
        batch="essay-2",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile_path,
    )
    record = _record(
        source=str(redacted / "STUDENT_01.md"),
        rubric=str(phil210 / "grading_rubric.md"),
        profile=str(profile_path),
    )
    record.save(drafts / "STUDENT_01.json")
    return redacted, drafts, root


def _replace_approved_brief(
    root, phil210, approved_brief, *, rubric=None, profile=None
):
    brief = root / "assignments" / "essay-2.md"
    brief.unlink()
    return approved_brief(
        brief,
        batch="essay-2",
        course="phil210",
        rubric=rubric or phil210 / "grading_rubric.md",
        profile=profile or root / "profiles" / "phil210.md",
    )


def _refresh_redaction_manifest(redacted, batch="essay-2"):
    try:
        run_id = RedactionManifest.load(redacted).run_id
    except (OSError, ValueError, json.JSONDecodeError):
        run_id = "test-redaction-run"
    RedactionManifest.create(
        batch,
        run_id,
        [path for path in redacted.iterdir() if not path.name.startswith("_")],
    ).save(redacted)


def _run(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def test_verification_inputs_requires_an_assignment_brief(phil210):
    with pytest.raises(TypeError, match="brief"):
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
        )


@pytest.mark.parametrize("state", ["missing", "stale"])
def test_assignment_brief_failure_stops_verification_without_writes(
    tmp_path, phil210, approved_brief, state
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    brief = root / "assignments" / "essay-2.md"
    if state == "missing":
        brief.unlink()
    if state == "stale":
        brief.write_text(
            brief.read_text(encoding="utf-8") + "changed\n", encoding="utf-8"
        )
    record = drafts / "STUDENT_01.json"
    before = record.read_bytes()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=brief,
        )
    )

    assert report.status.entries == ()
    assert report.status.blocking_findings == ()
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-assignment-brief"
    ]
    assert report.checked_tokens == ()
    assert record.read_bytes() == before
    assert report.receipt is None
    assert report.persisted is False
    assert report.failure is VerificationFailure.MALFORMED
    assert not (drafts / "_verification.json").exists()


def test_assignment_brief_gate_runs_before_batch_status(
    tmp_path, phil210, approved_brief, monkeypatch
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    (root / "assignments" / "essay-2.md").unlink()

    def fail_status(*args, **kwargs):
        raise AssertionError("batch artifacts were read")

    monkeypatch.setattr(GradingBatch, "_status", fail_status)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-assignment-brief"
    ]


@pytest.mark.parametrize("changed", ["brief", "rubric", "profile"])
def test_approval_snapshot_change_before_initial_digest_blocks_receipt(
    tmp_path, phil210, approved_brief, monkeypatch, changed
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    rubric = tmp_path / "rubric.md"
    rubric.write_bytes((phil210 / "grading_rubric.md").read_bytes())
    record = drafts / "STUDENT_01.json"
    grade_record = GradeRecord.load(record)
    grade_record.rubric = str(rubric)
    grade_record.save(record)
    _replace_approved_brief(root, phil210, approved_brief, rubric=rubric)
    inputs = VerificationInputs(
        course="phil210",
        rubric=rubric,
        profile=root / "profiles" / "phil210.md",
        patterns=PATTERNS_PATH,
        brief=root / "assignments" / "essay-2.md",
    )
    changed_path = {
        "brief": inputs.brief,
        "rubric": inputs.rubric,
        "profile": inputs.profile,
    }[changed]
    before = record.read_bytes()

    def mutate_after_check(*args, **kwargs):
        checked = check_brief(*args, **kwargs)
        changed_path.write_bytes(changed_path.read_bytes() + b"\n")
        return checked

    monkeypatch.setattr("scripts.grading.batch.check_brief", mutate_after_check)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(inputs)

    assert [finding.code for finding in report.blocking_findings] == [
        "batch-changed"
    ]
    assert report.persisted is False
    assert report.receipt is None
    assert record.read_bytes() == before
    assert not (drafts / "_verification.json").exists()


def test_grading_batch_verifies_and_persists_a_clean_batch(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.blocking_findings == ()
    assert report.checked_tokens == ("STUDENT_01",)
    assert report.persisted is True
    payload = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    assert payload["criteria"][0]["justifications"][0]["offset"] >= 0


def test_predictable_verification_temp_symlinks_are_never_followed(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    sentinel = tmp_path / "unrelated.txt"
    sentinel.write_text("leave me alone", encoding="utf-8")
    (drafts / ".STUDENT_01.json.verified.tmp").symlink_to(sentinel)
    (drafts / "_verification.tmp").symlink_to(sentinel)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.blocking_findings == ()
    assert report.persisted is True
    assert sentinel.read_text(encoding="utf-8") == "leave me alone"


def test_verification_refuses_a_manifest_from_another_redaction_result(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    source = redacted / "STUDENT_01.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\nchanged", encoding="utf-8"
    )

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert any(
        finding.code == "malformed-redaction-manifest"
        for finding in report.blocking_findings
    )
    assert not (drafts / "_verification.json").exists()


def test_persisted_verification_writes_one_versioned_receipt(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    receipt = drafts / "_verification.json"
    assert report.receipt == receipt
    assert [path.name for path in drafts.glob("_verification*.json")] == [
        "_verification.json"
    ]
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assert payload["schema"] == "grading-batch-verification/2"
    assert payload["batch"] == "essay-2"


def test_no_write_verification_writes_neither_offsets_nor_a_receipt(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    record_path = drafts / "STUDENT_01.json"
    before = record_path.read_bytes()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        ),
        persist=False,
    )

    assert report.blocking_findings == ()
    assert report.persisted is False
    assert report.receipt is None
    assert record_path.read_bytes() == before
    assert not (drafts / "_verification.json").exists()


def test_receipt_binds_every_identity_free_artifact_with_sha256(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    receipt_path = drafts / "_verification.json"
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert payload["course"] == "phil210"
    assert payload["hash"] == "sha256"
    assert [artifact["role"] for artifact in payload["artifacts"]] == [
        "assignment-brief",
        "calibration-profile",
        "draft",
        "grade-record",
        "pattern-reference",
        "redacted-submission",
        "redaction-manifest",
        "rubric",
    ]
    participant_roles = {"draft", "grade-record", "redacted-submission"}
    for artifact in payload["artifacts"]:
        assert Path(artifact["path"]).is_absolute() is False
        assert len(artifact["sha256"]) == 64
        if artifact["role"] in participant_roles:
            assert artifact["token"] == "STUDENT_01"
        else:
            assert "token" not in artifact

    record_artifact = next(
        item for item in payload["artifacts"] if item["role"] == "grade-record"
    )
    assert record_artifact["sha256"] == hashlib.sha256(
        (drafts / "STUDENT_01.json").read_bytes()
    ).hexdigest()
    blob = receipt_path.read_text(encoding="utf-8")
    assert "Community solar cooperatives" not in blob
    assert "Maya Lin" not in blob
    assert "TokenMap" not in blob


def test_repeated_verification_writes_the_same_receipt_bytes(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    inputs = VerificationInputs(
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=root / "profiles" / "phil210.md",
        patterns=PATTERNS_PATH,
        brief=root / "assignments" / "essay-2.md",
    )

    batch.verify(inputs)
    first = (drafts / "_verification.json").read_bytes()
    batch.verify(inputs)

    assert (drafts / "_verification.json").read_bytes() == first


def test_current_receipt_makes_grading_batch_progress_verified(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    batch.verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    status = batch.status()

    assert [entry.progress for entry in status.entries] == [Progress.VERIFIED]
    assert status.blocking_findings == ()


def test_legacy_batch_requires_verification_only_when_eligibility_is_requested(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, _ = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )

    ordinary = batch.status()
    required = batch.status(require_verified=True)

    assert [entry.progress for entry in ordinary.entries] == [Progress.DRAFTED]
    assert ordinary.blocking_findings == ()
    assert [entry.progress for entry in required.entries] == [Progress.DRAFTED]
    assert [finding.code for finding in required.blocking_findings] == [
        "verification-required"
    ]


@pytest.mark.parametrize(
    "receipt_text",
    [
        "{not json",
        json.dumps({"schema": "grading-batch-verification/99"}),
    ],
)
def test_malformed_or_unknown_receipt_never_grants_verified_progress(
    tmp_path, phil210, approved_brief, receipt_text
):
    redacted, drafts, _ = _write_batch(tmp_path, phil210, approved_brief)
    (drafts / "_verification.json").write_text(receipt_text, encoding="utf-8")

    status = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).status()

    assert [entry.progress for entry in status.entries] == [Progress.DRAFTED]
    assert [finding.code for finding in status.blocking_findings] == [
        "malformed-verification-receipt"
    ]


def test_receipt_with_malformed_artifact_evidence_is_rejected(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    batch.verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )
    receipt = drafts / "_verification.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    payload["artifacts"][0]["sha256"] = "z" * 64
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    status = batch.status()

    assert [entry.progress for entry in status.entries] == [Progress.DRAFTED]
    assert [finding.code for finding in status.blocking_findings] == [
        "malformed-verification-receipt"
    ]


@pytest.mark.parametrize("change", ["missing", "duplicate", "token", "suffix"])
def test_receipt_requires_one_token_free_markdown_assignment_brief(
    tmp_path, phil210, approved_brief, change
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    batch.verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )
    receipt = drafts / "_verification.json"
    payload = json.loads(receipt.read_text(encoding="utf-8"))
    assignment = next(
        item for item in payload["artifacts"] if item["role"] == "assignment-brief"
    )
    if change == "missing":
        payload["artifacts"].remove(assignment)
    elif change == "duplicate":
        payload["artifacts"].append(dict(assignment))
    elif change == "token":
        assignment["token"] = "STUDENT_01"
    else:
        assignment["path"] = "../assignments/essay-2.txt"
    receipt.write_text(json.dumps(payload), encoding="utf-8")

    status = batch.status()

    assert [finding.code for finding in status.blocking_findings] == [
        "malformed-verification-receipt"
    ]


@pytest.mark.parametrize(
    "change",
    ["changed", "added", "removed", "renamed", "missing-course-evidence"],
)
def test_any_receipt_bound_artifact_change_invalidates_verified_progress(
    tmp_path, phil210, approved_brief, change
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    inputs = VerificationInputs(
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=root / "profiles" / "phil210.md",
        patterns=PATTERNS_PATH,
        brief=root / "assignments" / "essay-2.md",
    )
    batch.verify(inputs)

    if change == "changed":
        (drafts / "STUDENT_01.md").write_text("changed feedback", encoding="utf-8")
    elif change == "added":
        (redacted / "STUDENT_02.md").write_text(SOURCE, encoding="utf-8")
    elif change == "removed":
        (drafts / "STUDENT_01.md").unlink()
    elif change == "renamed":
        (drafts / "STUDENT_01.json").rename(drafts / "STUDENT_02.json")
    else:
        inputs.profile.unlink()

    status = batch.status()

    assert all(entry.progress is not Progress.VERIFIED for entry in status.entries)
    assert "stale-verification-receipt" in {
        finding.code for finding in status.blocking_findings
    }


@pytest.mark.parametrize(
    "role",
    [
        "assignment-brief",
        "redacted-submission",
        "draft",
        "grade-record",
        "rubric",
        "calibration-profile",
        "pattern-reference",
        "redaction-manifest",
    ],
)
def test_edit_to_each_bound_artifact_role_invalidates_receipt(
    tmp_path, phil210, approved_brief, role
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    rubric = tmp_path / "rubric.md"
    rubric.write_bytes((phil210 / "grading_rubric.md").read_bytes())
    patterns = tmp_path / "patterns.md"
    patterns.write_bytes(PATTERNS_PATH.read_bytes())
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.rubric = str(rubric)
    record.save(record_path)
    inputs = VerificationInputs(
        course="phil210",
        rubric=rubric,
        profile=root / "profiles" / "phil210.md",
        patterns=patterns,
        brief=root / "assignments" / "essay-2.md",
    )
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    batch.verify(inputs)
    artifacts = {
        "assignment-brief": inputs.brief,
        "redacted-submission": redacted / "STUDENT_01.md",
        "draft": drafts / "STUDENT_01.md",
        "grade-record": record_path,
        "rubric": rubric,
        "calibration-profile": inputs.profile,
        "pattern-reference": patterns,
        "redaction-manifest": redacted / "_redaction.json",
    }

    changed = artifacts[role]
    changed.write_bytes(changed.read_bytes() + b"\n")

    assert "stale-verification-receipt" in {
        finding.code for finding in batch.status().blocking_findings
    }


def test_verification_receipt_preserves_crlf_brief_byte_binding(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    brief = root / "assignments" / "essay-2.md"
    brief.write_bytes(brief.read_bytes().replace(b"\n", b"\r\n"))
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    report = batch.verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=brief,
        )
    )
    assert report.verified_batch is not None

    exact_bytes = brief.read_bytes()
    brief.write_bytes(exact_bytes.replace(b"\r\n", b"\n", 1))

    assert "stale-verification-receipt" in {
        finding.code for finding in batch.status().blocking_findings
    }


def test_reverification_replaces_a_stale_receipt(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    inputs = VerificationInputs(
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=root / "profiles" / "phil210.md",
        patterns=PATTERNS_PATH,
        brief=root / "assignments" / "essay-2.md",
    )
    batch.verify(inputs)
    (drafts / "STUDENT_01.md").write_text(FEEDBACK + "\n", encoding="utf-8")
    assert "stale-verification-receipt" in {
        finding.code for finding in batch.status().blocking_findings
    }

    report = batch.verify(inputs)

    assert report.persisted is True
    assert [entry.progress for entry in batch.status().entries] == [Progress.VERIFIED]


def test_status_cli_reports_verified_progress_from_a_current_receipt(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

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
    assert "STUDENT_01  verified" in result.stdout
    assert "1 verified" in result.stdout


def test_grading_batch_never_trusts_the_grade_record_source_path(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    outside = tmp_path / "submissions" / "STUDENT_01.md"
    outside.parent.mkdir()
    outside.write_bytes(b"\xff")
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.source = str(outside)
    record.save(record_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == ["source-mismatch"]
    assert report.checked_tokens == ("STUDENT_01",)
    assert report.persisted is False
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert "offset" not in payload["criteria"][0]["justifications"][0]


def test_grading_batch_reports_every_grading_context_mismatch(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.token = "STUDENT_99"
    record.batch = "essay-9"
    record.course = "eng999"
    record.rubric = str(tmp_path / "another-rubric.md")
    record.profile = str(tmp_path / "another-profile.md")
    record.save(record_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "token-mismatch",
        "batch-mismatch",
        "course-mismatch",
        "rubric-mismatch",
        "profile-mismatch",
    ]
    assert all(finding.token == "STUDENT_01" for finding in report.blocking_findings)
    assert report.persisted is False


def test_loaded_profile_must_name_the_requested_course(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    profile_path = root / "profiles" / "phil210.md"
    profile_path.write_text(
        profile_path.read_text(encoding="utf-8").replace(
            "**Course**: phil210", "**Course**: eng999"
        ),
        encoding="utf-8",
    )
    _replace_approved_brief(root, phil210, approved_brief, profile=profile_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile_path,
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert any(
        finding.code == "profile-course-mismatch"
        for finding in report.blocking_findings
    )


def test_missing_bound_profile_stops_before_other_course_evidence_reads(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    profile_path = root / "profiles" / "phil210.md"
    profile_path.unlink()
    rubric_path = tmp_path / "broken-rubric.md"
    rubric_path.write_text("not a rubric", encoding="utf-8")
    patterns_path = tmp_path / "broken-patterns.md"
    patterns_path.write_bytes(b"\xff")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=rubric_path,
            profile=profile_path,
            patterns=patterns_path,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-assignment-brief"
    ]
    assert report.checked_tokens == ()


def test_grading_batch_accepts_equivalent_normalized_paths(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.source = str(redacted / ".." / "redacted" / "STUDENT_01.md")
    record.rubric = str(phil210 / ".." / "phil210" / "grading_rubric.md")
    record.profile = str(root / "profiles" / ".." / "profiles" / "phil210.md")
    record.save(record_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.blocking_findings == ()
    assert report.persisted is True


def test_grading_batch_aggregates_failures_and_leaves_every_record_unchanged(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(
        tmp_path,
        phil210,
        approved_brief,
        feedback=FEEDBACK.replace("— Dr. Vance", "Regards,"),
    )
    (redacted / "STUDENT_02.md").write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(redacted)
    (drafts / "STUDENT_02.md").write_text(FEEDBACK, encoding="utf-8")
    malformed = drafts / "STUDENT_02.json"
    malformed.write_text("[]", encoding="utf-8")
    first_record = drafts / "STUDENT_01.json"
    before_first = first_record.read_bytes()
    before_second = malformed.read_bytes()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert {finding.code for finding in report.blocking_findings} == {
        "sign-off",
        "malformed-record",
    }
    assert report.checked_tokens == ("STUDENT_01",)
    assert report.persisted is False
    assert report.failure is VerificationFailure.MALFORMED
    assert first_record.read_bytes() == before_first
    assert malformed.read_bytes() == before_second
    assert not (drafts / "_verification.json").exists()


def test_grading_batch_reports_unreadable_artifacts_and_checks_later_work(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    (drafts / "STUDENT_01.md").write_bytes(b"\xff")
    second_source = redacted / "STUDENT_02.md"
    second_source.write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(redacted)
    (drafts / "STUDENT_02.md").write_text(FEEDBACK, encoding="utf-8")
    second_record = drafts / "STUDENT_02.json"
    _record(
        token="STUDENT_02",
        source=str(second_source),
        rubric=str(phil210 / "grading_rubric.md"),
        profile=str(root / "profiles" / "phil210.md"),
    ).save(second_record)
    before_second = second_record.read_bytes()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "unreadable-draft"
    ]
    assert report.checked_tokens == ("STUDENT_02",)
    assert report.persisted is False
    assert second_record.read_bytes() == before_second


def test_semantically_malformed_record_does_not_stop_later_verification(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    first_path = drafts / "STUDENT_01.json"
    first = GradeRecord.load(first_path)
    first.criteria[0].score = "twenty"
    first.save(first_path)
    second_source = redacted / "STUDENT_02.md"
    second_source.write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(redacted)
    (drafts / "STUDENT_02.md").write_text(FEEDBACK, encoding="utf-8")
    _record(
        token="STUDENT_02",
        source=str(second_source),
        rubric=str(phil210 / "grading_rubric.md"),
        profile=str(root / "profiles" / "phil210.md"),
    ).save(drafts / "STUDENT_02.json")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-record"
    ]
    assert report.checked_tokens == ("STUDENT_02",)
    assert report.failure is VerificationFailure.MALFORMED


def test_missing_profile_invalidates_assignment_approval_before_inventory(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    (drafts / "STUDENT_02.md").write_text("feedback", encoding="utf-8")
    profile_path = root / "profiles" / "phil210.md"
    profile_path.unlink()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile_path,
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-assignment-brief",
    ]
    assert report.status.entries == ()
    assert report.checked_tokens == ()
    assert report.persisted is False


def test_grading_batch_reports_an_unreadable_profile_as_malformed(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    profile_path = root / "profiles" / "phil210.md"
    profile_path.write_bytes(b"\xff")
    _replace_approved_brief(root, phil210, approved_brief, profile=profile_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile_path,
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-profile"
    ]


def test_missing_profile_stops_before_a_malformed_grade_record_is_read(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    (drafts / "STUDENT_01.json").write_text("{not json", encoding="utf-8")
    profile_path = root / "profiles" / "phil210.md"
    profile_path.unlink()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile_path,
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-assignment-brief",
    ]
    assert report.checked_tokens == ()


def test_missing_profile_stops_before_record_metadata_and_draft_reads(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    outside = tmp_path / "submissions" / "STUDENT_01.md"
    outside.parent.mkdir()
    outside.write_text("outside", encoding="utf-8")
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.token = "STUDENT_99"
    record.source = str(outside)
    record.save(record_path)
    (drafts / "STUDENT_01.md").write_bytes(b"\xff")
    profile_path = root / "profiles" / "phil210.md"
    profile_path.unlink()

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile_path,
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-assignment-brief",
    ]
    assert report.checked_tokens == ()


def test_grading_batch_names_rubric_drift_before_checking_drafts(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    edited = tmp_path / "edited-rubric.md"
    edited.write_text(
        (phil210 / "grading_rubric.md")
        .read_text(encoding="utf-8")
        .replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.rubric = str(edited)
    record.save(record_path)
    _replace_approved_brief(root, phil210, approved_brief, rubric=edited)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=edited,
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.failure is VerificationFailure.RUBRIC_DRIFT
    assert [finding.code for finding in report.blocking_findings] == ["rubric-drift"]
    assert "Thesis" in report.blocking_findings[0].message
    assert report.checked_tokens == ()
    assert report.persisted is False


def test_incomplete_pairs_still_report_available_record_metadata(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    (drafts / "STUDENT_01.md").unlink()
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.token = "STUDENT_99"
    record.batch = "essay-9"
    record.course = "eng999"
    record.rubric = str(tmp_path / "another-rubric.md")
    record.profile = str(tmp_path / "another-profile.md")
    record.save(record_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "incomplete-pair",
        "token-mismatch",
        "batch-mismatch",
        "course-mismatch",
        "rubric-mismatch",
        "profile-mismatch",
    ]


def test_grading_batch_refuses_verification_when_there_are_no_grade_records(
    tmp_path, phil210, approved_brief
):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    (redacted / "STUDENT_01.md").write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(redacted)
    root = tmp_path / ".grading"
    brief = approved_brief(
        root / "assignments" / "essay-2.md",
        batch="essay-2",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=phil210 / "profile_phil210.md",
    )

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            brief=brief,
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == ["no-records"]
    assert report.persisted is False


def test_grading_batch_reports_a_missing_pattern_reference_as_malformed(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=tmp_path / "missing-patterns.md",
            brief=root / "assignments" / "essay-2.md",
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-patterns"
    ]
    assert report.checked_tokens == ()


def test_the_cli_passes_a_clean_batch(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_cli_writes_offsets_back_into_the_record(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )
    payload = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    assert payload["criteria"][0]["justifications"][0]["offset"] >= 0


def test_the_cli_no_write_check_writes_neither_offsets_nor_receipt(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    record_path = drafts / "STUDENT_01.json"
    before = record_path.read_bytes()

    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
        "--no-write",
    )

    assert result.returncode == 0
    assert "Nothing written" in result.stdout
    assert record_path.read_bytes() == before
    assert not (drafts / "_verification.json").exists()


def test_the_cli_fails_a_batch_with_a_bad_draft(tmp_path, phil210, approved_brief):
    redacted, drafts, root = _write_batch(
        tmp_path,
        phil210,
        approved_brief,
        feedback=FEEDBACK.replace("— Dr. Vance", "Regards,"),
    )
    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )
    assert result.returncode == 12
    assert "sign-off" in result.stdout + result.stderr


def test_the_cli_never_persists_part_of_a_failing_batch(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    first_record = drafts / "STUDENT_01.json"
    before_first = first_record.read_bytes()
    second_source = redacted / "STUDENT_02.md"
    second_source.write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(redacted)
    (drafts / "STUDENT_02.md").write_text(
        FEEDBACK.replace("— Dr. Vance", "Regards,"), encoding="utf-8"
    )
    _record(
        token="STUDENT_02",
        source=str(second_source),
        rubric=str(phil210 / "grading_rubric.md"),
        profile=str(root / "profiles" / "phil210.md"),
    ).save(drafts / "STUDENT_02.json")

    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )

    assert result.returncode == 12
    assert "sign-off" in result.stdout + result.stderr
    assert first_record.read_bytes() == before_first


def test_the_cli_never_opens_a_non_redacted_record_source(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    outside = tmp_path / "submissions" / "STUDENT_01.md"
    outside.parent.mkdir()
    outside.write_bytes(b"\xff")
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.source = str(outside)
    record.save(record_path)

    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )

    assert result.returncode == 12
    assert "source-mismatch" in result.stdout + result.stderr


def test_the_verify_cli_reports_missing_bound_profile_as_malformed_brief(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    (root / "profiles" / "phil210.md").unlink()

    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )

    assert result.returncode == 13
    assert "malformed-assignment-brief" in result.stdout + result.stderr


def test_the_verify_cli_reports_an_unreadable_profile_as_malformed(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    profile_path = root / "profiles" / "phil210.md"
    profile_path.write_bytes(b"\xff")
    _replace_approved_brief(root, phil210, approved_brief, profile=profile_path)

    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )

    assert result.returncode == 13
    assert "malformed-profile" in result.stdout + result.stderr


def test_the_verify_cli_preserves_rubric_drift_exit_11(
    tmp_path, phil210, approved_brief
):
    redacted, drafts, root = _write_batch(tmp_path, phil210, approved_brief)
    edited = tmp_path / "edited-rubric.md"
    edited.write_text(
        (phil210 / "grading_rubric.md")
        .read_text(encoding="utf-8")
        .replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.rubric = str(edited)
    record.save(record_path)
    _replace_approved_brief(root, phil210, approved_brief, rubric=edited)

    result = _run(
        "verify",
        "--batch",
        "essay-2",
        "--course",
        "phil210",
        "--rubric",
        str(edited),
        "--redacted",
        str(redacted),
        "--drafts",
        str(drafts),
        "--root",
        str(root),
    )

    assert result.returncode == 11
    assert "Thesis" in result.stdout + result.stderr


def test_the_cli_refuses_to_grade_without_a_profile(tmp_path, phil210):
    result = _run(
        "profile",
        "--course",
        "eng999",
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--root",
        str(tmp_path),
    )
    assert result.returncode == 10
    assert "calibrate" in (result.stdout + result.stderr).lower()


def test_the_cli_names_drifted_criteria(tmp_path, phil210):
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    edited = tmp_path / "edited.md"
    edited.write_text(
        (phil210 / "grading_rubric.md")
        .read_text(encoding="utf-8")
        .replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )
    result = _run(
        "profile", "--course", "phil210", "--rubric", str(edited), "--root", str(root)
    )
    assert result.returncode == 11
    assert "Thesis" in result.stdout + result.stderr


def test_the_cli_accepts_a_matching_rubric(tmp_path, phil210):
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    result = _run("profile", "--course", "phil210",
                  "--rubric", str(phil210 / "grading_rubric.md"), "--root", str(root))
    assert result.returncode == 0
