"""No draft reaches an instructor until every claim in it has been checked."""

import json
import subprocess
import sys

import pytest

from scripts.grading.batch import (
    BatchPaths,
    GradingBatch,
    VerificationFailure,
    VerificationInputs,
)
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


def test_a_missing_feedback_section_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("**What Worked**", "**Nice Things**")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "section-missing" for finding in errors(findings))


def test_a_missing_sign_off_fails(rubric, loaded_profile, terms):
    broken = FEEDBACK.replace("— Dr. Vance", "Regards,")
    findings = verify_draft(_record(), broken, SOURCE, rubric, loaded_profile, terms)
    assert any(finding.code == "sign-off" for finding in errors(findings))


def test_missing_inline_annotations_fail(rubric, loaded_profile, terms):
    broken = "\n".join(FEEDBACK.splitlines()[2:])
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


def _write_batch(tmp_path, phil210, feedback=FEEDBACK):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    (redacted / "STUDENT_01.md").write_text(SOURCE, encoding="utf-8")
    (drafts / "STUDENT_01.md").write_text(feedback, encoding="utf-8")
    root = tmp_path / ".grading"
    (root / "profiles").mkdir(parents=True)
    profile_path = root / "profiles" / "phil210.md"
    profile_path.write_text(
        (phil210 / "profile_phil210.md").read_text(encoding="utf-8"), encoding="utf-8"
    )
    record = _record(
        source=str(redacted / "STUDENT_01.md"),
        rubric=str(phil210 / "grading_rubric.md"),
        profile=str(profile_path),
    )
    record.save(drafts / "STUDENT_01.json")
    return redacted, drafts, root


def _run(*args):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args], capture_output=True, text=True
    )


def test_grading_batch_verifies_and_persists_a_clean_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )

    assert report.blocking_findings == ()
    assert report.checked_tokens == ("STUDENT_01",)
    assert report.persisted is True
    payload = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    assert payload["criteria"][0]["justifications"][0]["offset"] >= 0


def test_grading_batch_never_trusts_the_grade_record_source_path(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "source-mismatch"
    ]
    assert report.checked_tokens == ("STUDENT_01",)
    assert report.persisted is False
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    assert "offset" not in payload["criteria"][0]["justifications"][0]


def test_grading_batch_reports_every_grading_context_mismatch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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


def test_grading_batch_accepts_equivalent_normalized_paths(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    record_path = drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.source = str(redacted / ".." / "redacted" / "STUDENT_01.md")
    record.rubric = str(phil210 / ".." / "phil210" / "grading_rubric.md")
    record.profile = str(
        root / "profiles" / ".." / "profiles" / "phil210.md"
    )
    record.save(record_path)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )

    assert report.blocking_findings == ()
    assert report.persisted is True


def test_grading_batch_aggregates_failures_and_leaves_every_record_unchanged(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(
        tmp_path,
        phil210,
        feedback=FEEDBACK.replace("— Dr. Vance", "Regards,"),
    )
    (redacted / "STUDENT_02.md").write_text(SOURCE, encoding="utf-8")
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


def test_grading_batch_reports_unreadable_artifacts_and_checks_later_work(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    (drafts / "STUDENT_01.md").write_bytes(b"\xff")
    second_source = redacted / "STUDENT_02.md"
    second_source.write_text(SOURCE, encoding="utf-8")
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
        )
    )

    assert [finding.code for finding in report.blocking_findings] == [
        "unreadable-draft"
    ]
    assert report.checked_tokens == ("STUDENT_02",)
    assert report.persisted is False
    assert second_record.read_bytes() == before_second


def test_grading_batch_reports_a_missing_profile_without_skipping_inventory(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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
        )
    )

    assert report.failure is VerificationFailure.NO_PROFILE
    assert [finding.code for finding in report.blocking_findings] == [
        "orphaned-artifact",
        "no-profile",
    ]
    assert report.checked_tokens == ()
    assert report.persisted is False


def test_missing_profile_does_not_hide_a_malformed_grade_record(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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
        )
    )

    assert report.failure is VerificationFailure.NO_PROFILE
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-record",
        "no-profile",
    ]


def test_missing_profile_does_not_hide_safe_metadata_and_read_findings(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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
        )
    )

    assert report.failure is VerificationFailure.NO_PROFILE
    assert [finding.code for finding in report.blocking_findings] == [
        "token-mismatch",
        "source-mismatch",
        "unreadable-draft",
        "no-profile",
    ]


def test_grading_batch_names_rubric_drift_before_checking_drafts(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=edited,
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )

    assert report.failure is VerificationFailure.RUBRIC_DRIFT
    assert [finding.code for finding in report.blocking_findings] == [
        "rubric-drift"
    ]
    assert "Thesis" in report.blocking_findings[0].message
    assert report.checked_tokens == ()
    assert report.persisted is False


def test_incomplete_pairs_still_report_available_record_metadata(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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
    tmp_path, phil210
):
    redacted = tmp_path / "redacted"
    drafts = tmp_path / "drafts"
    redacted.mkdir()
    drafts.mkdir()
    (redacted / "STUDENT_01.md").write_text(SOURCE, encoding="utf-8")

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == ["no-records"]
    assert report.persisted is False


def test_grading_batch_reports_a_missing_pattern_reference_as_malformed(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)

    report = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=tmp_path / "missing-patterns.md",
        )
    )

    assert report.failure is VerificationFailure.MALFORMED
    assert [finding.code for finding in report.blocking_findings] == [
        "malformed-patterns"
    ]
    assert report.checked_tokens == ()


def test_the_cli_passes_a_clean_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    result = _run(
        "verify", "--batch", "essay-2", "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root),
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_cli_writes_offsets_back_into_the_record(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    _run(
        "verify", "--batch", "essay-2", "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root),
    )
    payload = json.loads((drafts / "STUDENT_01.json").read_text(encoding="utf-8"))
    assert payload["criteria"][0]["justifications"][0]["offset"] >= 0


def test_the_cli_fails_a_batch_with_a_bad_draft(tmp_path, phil210):
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, feedback=FEEDBACK.replace("— Dr. Vance", "Regards,")
    )
    result = _run(
        "verify", "--batch", "essay-2", "--course", "phil210",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--redacted", str(redacted), "--drafts", str(drafts), "--root", str(root),
    )
    assert result.returncode == 12
    assert "sign-off" in result.stdout + result.stderr


def test_the_cli_never_persists_part_of_a_failing_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    first_record = drafts / "STUDENT_01.json"
    before_first = first_record.read_bytes()
    second_source = redacted / "STUDENT_02.md"
    second_source.write_text(SOURCE, encoding="utf-8")
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


def test_the_cli_never_opens_a_non_redacted_record_source(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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


def test_the_verify_cli_preserves_missing_profile_exit_10(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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

    assert result.returncode == 10
    assert "calibrate" in (result.stdout + result.stderr).lower()


def test_the_verify_cli_preserves_rubric_drift_exit_11(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
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
        "profile", "--course", "eng999",
        "--rubric", str(phil210 / "grading_rubric.md"),
        "--root", str(tmp_path),
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
    result = _run("profile", "--course", "phil210", "--rubric", str(edited),
                  "--root", str(root))
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
