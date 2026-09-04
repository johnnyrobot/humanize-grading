"""One durable action resumes grading from identity-free state."""

import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from scripts.grading.density import PATTERNS_PATH
from scripts.grading.batch import BatchPaths, GradingBatch, VerificationInputs
from scripts.grading.brief import prepare_brief
from scripts.grading.calibration import approval_phrase, approve_calibration
from scripts.grading.review import (
    ack_path,
    ack_phrase,
    approve_review,
    pilot_phrase,
    review_path,
    write_acknowledgement,
)
from scripts.grading.drafts import prepare_draft_pair
from scripts.grading.rubric import load_rubric
from scripts.grading.records import GradeRecord
from scripts.grading.workflow import WorkflowContext, WorkflowError, next_action
from scripts.redaction.manifest import RedactionManifest
from scripts.redaction.pipeline import (
    EXIT_OK as REDACTION_OK,
    EXIT_UNREADABLE as REDACTION_UNREADABLE,
    EXIT_UNSAFE_PATH as REDACTION_UNSAFE,
    run as redact_batch,
)
from scripts.redaction.roster import RosterEntry
from tests.grading.test_calibration import _write as _write_receipt
from tests.grading.test_verify import (
    FEEDBACK,
    SOURCE,
    TEST_MAP_SHA256,
    TEST_REVIEW_SHA256,
    _approve_pilot,
    _refresh_redaction_manifest,
    _write_batch,
)
from tests.grading.test_publication import FakeRenderer, _acknowledge


def _approve_calibration(root: Path, phil210: Path) -> None:
    profile = root / "profiles" / "phil210.md"
    _write_receipt(root, phil210, profile)
    approve_calibration(
        root,
        course="phil210",
        profile=profile,
        rubric=phil210 / "grading_rubric.md",
        approval=approval_phrase("phil210"),
    )


def _inputs_for(context: "WorkflowContext") -> VerificationInputs:
    return VerificationInputs(
        course=context.course,
        rubric=context.rubric,
        profile=context.root / "profiles" / "phil210.md",
        patterns=context.patterns,
        brief=context.root / "assignments" / "essay-2.md",
    )


def _add_draft(context: "WorkflowContext", token: str) -> None:
    """Clone the first drafted pair to a new token, source included."""
    source = context.redacted / f"{token}.md"
    source.write_text(SOURCE, encoding="utf-8")
    (context.drafts / f"{token}.md").write_text(FEEDBACK, encoding="utf-8")
    record = GradeRecord.load(context.drafts / "STUDENT_01.json")
    record.token = token
    record.source = str(source)
    record.save(context.drafts / f"{token}.json")
    _refresh_redaction_manifest(context.redacted)


def _add_source(context: "WorkflowContext", token: str) -> None:
    (context.redacted / f"{token}.md").write_text(SOURCE, encoding="utf-8")
    _refresh_redaction_manifest(context.redacted)


def _pilot_context(tmp_path, phil210, approved_brief, *, review="pilot", pilot=2):
    """One clean drafted submission, no review approval, a pilot-size-two brief."""
    context = _complete_context(
        tmp_path, phil210, approved_brief, pilot_approved=False
    )
    brief = context.root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(
        brief,
        batch=context.batch,
        course=context.course,
        rubric=context.rubric,
        profile=context.root / "profiles" / "phil210.md",
        review=review,
        pilot=pilot,
    )
    return context


@dataclass
class ReadyBatch:
    root: Path
    redacted: Path
    drafts: Path
    rubric: Path

    def context(self, tmp_path: Path) -> WorkflowContext:
        return WorkflowContext(
            batch="essay-2",
            course="phil210",
            rubric=self.rubric,
            redacted=self.redacted,
            drafts=self.drafts,
            root=self.root,
            patterns=PATTERNS_PATH,
            publication=tmp_path / "final",
        )


def _empty_context(tmp_path: Path, phil210: Path) -> WorkflowContext:
    return WorkflowContext(
        batch="essay-2",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        redacted=tmp_path / "redacted",
        drafts=tmp_path / "drafts",
        root=tmp_path / ".grading",
        patterns=PATTERNS_PATH,
        publication=tmp_path / "final",
    )


def _write_profile(
    context: WorkflowContext, phil210: Path, *, approved: bool = True
) -> Path:
    path = context.root / "profiles" / "phil210.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((phil210 / "profile_phil210.md").read_bytes())
    if approved:
        _approve_calibration(context.root, phil210)
    return path


def _answer_brief(path: Path) -> None:
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "<!-- answer-required -->", "Instructor answer."
        ),
        encoding="utf-8",
    )


def _complete_context(
    tmp_path: Path, phil210: Path, approved_brief, **batch_options
) -> WorkflowContext:
    redacted, drafts, root = _write_batch(
        tmp_path, phil210, approved_brief, **batch_options
    )
    _approve_calibration(root, phil210)
    return WorkflowContext(
        batch="essay-2",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        redacted=redacted,
        drafts=drafts,
        root=root,
        patterns=PATTERNS_PATH,
        publication=tmp_path / "final",
    )


def _template_context(tmp_path: Path, ready_batch: ReadyBatch) -> WorkflowContext:
    context = ready_batch.context(tmp_path)
    prepare_draft_pair(
        batch=context.batch,
        course=context.course,
        source=context.redacted / "STUDENT_01.md",
        rubric=load_rubric(context.rubric),
        profile=context.root / "profiles" / "phil210.md",
        patterns=context.patterns,
        drafts=context.drafts,
    )
    return context


def _published_context(
    tmp_path: Path, phil210: Path, approved_brief
) -> WorkflowContext:
    context = _complete_context(tmp_path, phil210, approved_brief)
    report = GradingBatch.open(
        BatchPaths(context.batch, context.redacted, context.drafts)
    ).verify(
        VerificationInputs(
            course=context.course,
            rubric=context.rubric,
            profile=context.root / "profiles" / "phil210.md",
            patterns=context.patterns,
            brief=context.root / "assignments" / "essay-2.md",
        )
    )
    assert report.verified_batch is not None
    _acknowledge(context.drafts)
    published = report.verified_batch.publish(context.publication, FakeRenderer())
    assert published.changed is True
    return context


def _mismatched_profile_context(
    tmp_path: Path, ready_batch: ReadyBatch, approved_brief
) -> WorkflowContext:
    context = ready_batch.context(tmp_path)
    profile = context.root / "profiles" / "phil210.md"
    profile.write_text(
        profile.read_text(encoding="utf-8").replace(
            "**Course**: phil210", "**Course**: another-course", 1
        ),
        encoding="utf-8",
    )
    brief = context.root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(
        brief,
        batch=context.batch,
        course=context.course,
        rubric=context.rubric,
        profile=profile,
    )
    return context


def _install_nonregular_receipt(context: WorkflowContext, kind: str) -> Path:
    receipt = context.drafts / "_verification.json"
    if kind == "directory":
        receipt.mkdir()
    else:
        private = context.root / "private-receipt"
        private.mkdir()
        (private / "identity.txt").write_text("must not be read", encoding="utf-8")
        receipt.symlink_to(private, target_is_directory=True)
    return receipt


def _private_sentinels(
    context: WorkflowContext, tmp_path: Path
) -> tuple[WorkflowContext, Path, Path, Path, Path]:
    private = tmp_path / "private-sentinel"
    private.write_text("must not be read", encoding="utf-8")
    private.chmod(0)
    submissions = tmp_path / "submissions-sentinel"
    roster = tmp_path / "roster-sentinel.csv"
    map_path = context.root / "maps" / f"{context.batch}.json"
    review = context.root / "maps" / "reviews" / f"{context.batch}.json"
    review.parent.mkdir(parents=True, exist_ok=True)
    for path in (submissions, roster, map_path, review):
        path.symlink_to(private)
    return (
        replace(context, submissions=submissions, roster=roster),
        submissions,
        roster,
        map_path,
        review,
    )


def _install_clean_reviewed_redaction(
    context: WorkflowContext, tmp_path: Path
) -> RedactionManifest:
    source = tmp_path / "clean-submissions"
    source.mkdir()
    (source / "paper.md").write_text(
        "Maya Lin argues that platforms curate.", encoding="utf-8"
    )
    common = dict(
        source=source,
        destination=context.redacted,
        map_path=context.root / "maps" / f"{context.batch}.json",
        review_path=context.root / "maps" / "reviews" / f"{context.batch}.json",
        roster=[RosterEntry("Maya Lin")],
        batch=context.batch,
    )
    prepared = redact_batch(**common, reviewed=False, force=True)
    assert prepared.exit_code == REDACTION_OK
    assert RedactionManifest.load(context.redacted).reviewed is False
    result = redact_batch(**common, reviewed=True)
    assert result.exit_code == REDACTION_OK
    return RedactionManifest.load(context.redacted)


def _assert_prior_redaction_is_invalidated(
    context: WorkflowContext,
    previous: RedactionManifest,
    exit_code: int,
) -> None:
    current = RedactionManifest.load(context.redacted)
    assert current.batch == previous.batch
    assert current.run_id == previous.run_id
    assert current.outputs == previous.outputs
    assert current.reviewed is False
    assert current.exit_code == exit_code
    assert next_action(context).action == "repair-redaction"


def _next_run(
    context: WorkflowContext, *extra: str, include_publication: bool = True
):
    command = [
        sys.executable,
        "scripts/grade.py",
        "next",
        "--batch",
        context.batch,
        "--course",
        context.course,
        "--rubric",
        str(context.rubric),
        "--redacted",
        str(context.redacted),
        "--drafts",
        str(context.drafts),
        "--root",
        str(context.root),
        "--patterns",
        str(context.patterns),
    ]
    if include_publication:
        command.extend(("--publication", str(context.publication)))
    command.extend(extra)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
    )


def test_next_help_distinguishes_state_root_from_repository_root():
    result = subprocess.run(
        [sys.executable, "scripts/grade.py", "next", "--help"],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    help_text = " ".join(result.stdout.split())
    assert "Grading state root" in help_text
    assert "not the repository root" in help_text


@pytest.fixture
def ready_batch(tmp_path, phil210, approved_brief) -> ReadyBatch:
    root = tmp_path / ".grading"
    profile = root / "profiles" / "phil210.md"
    profile.parent.mkdir(parents=True)
    profile.write_bytes((phil210 / "profile_phil210.md").read_bytes())
    _approve_calibration(root, phil210)
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
    source.write_text("A redacted submission ready to grade.", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "test-redaction-run",
        [source],
        reviewed=True,
        exit_code=0,
        map_sha256=TEST_MAP_SHA256,
        review_sha256=TEST_REVIEW_SHA256,
    ).save(redacted)
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    return ReadyBatch(
        root=root,
        redacted=redacted,
        drafts=drafts,
        rubric=phil210 / "grading_rubric.md",
    )


def test_next_action_names_the_first_pending_record(tmp_path, ready_batch):
    context = ready_batch.context(tmp_path)

    action = next_action(context)

    assert action.action == "prepare-record"
    assert action.token == "STUDENT_01"
    assert action.commands[0][-4:] == (
        "--source",
        str(context.redacted / "STUDENT_01.md"),
        "--redacted",
        str(context.redacted),
    )


def test_next_action_serializes_context_commands_and_findings(tmp_path, ready_batch):
    action = next_action(ready_batch.context(tmp_path))

    assert action.as_dict() == {
        "schema": "grading-next-action/2",
        "batch": "essay-2",
        "course": "phil210",
        "stage": "drafting",
        "action": "prepare-record",
        "message": "Prepare the draft pair for STUDENT_01.",
        "token": "STUDENT_01",
        "tokens": [],
        "commands": [list(action.commands[0])],
        "findings": [],
    }


def test_missing_profile_returns_calibrate(tmp_path, phil210):
    action = next_action(_empty_context(tmp_path, phil210))

    assert action.action == "calibrate"


def test_rubric_drift_precedes_brief_state(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)
    drifted = tmp_path / "drifted-rubric.md"
    drifted.write_text(
        context.rubric.read_text(encoding="utf-8").replace(
            "Thesis & Theoretical Framing", "Thesis and Framing", 1
        ),
        encoding="utf-8",
    )
    context = WorkflowContext(**{**context.__dict__, "rubric": drifted})

    action = next_action(context)

    assert action.action == "recalibrate"


def test_profile_for_another_course_is_malformed_workflow_state(
    tmp_path, ready_batch, approved_brief
):
    context = _mismatched_profile_context(tmp_path, ready_batch, approved_brief)

    with pytest.raises(WorkflowError, match="another-course.*phil210"):
        next_action(context)


def test_next_cli_profile_course_mismatch_exits_thirteen(
    tmp_path, ready_batch, approved_brief
):
    context = _mismatched_profile_context(tmp_path, ready_batch, approved_brief)

    result = _next_run(context, "--json")

    assert result.returncode == 13
    assert result.stdout == ""
    assert "another-course" in result.stderr
    assert "phil210" in result.stderr
    assert "prepare-record" not in result.stdout + result.stderr


def test_missing_brief_returns_prepare_brief(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)

    action = next_action(context)

    assert action.action == "prepare-brief"


def test_unanswered_brief_returns_complete_brief(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)
    prepare_brief(
        context.root / "assignments" / "essay-2.md",
        batch=context.batch,
        course=context.course,
        mode="local",
    )

    action = next_action(context)

    assert action.action == "complete-brief"


def test_draft_brief_from_wrong_batch_returns_repair_brief(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)
    prepare_brief(
        context.root / "assignments" / "essay-2.md",
        batch="essay-9",
        course=context.course,
        mode="local",
    )

    action = next_action(context)

    assert action.action == "repair-brief"


def test_complete_draft_brief_returns_approve_brief(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210)
    path = context.root / "assignments" / "essay-2.md"
    prepare_brief(path, batch=context.batch, course=context.course, mode="local")
    _answer_brief(path)

    action = next_action(context)

    assert action.action == "approve-brief"


def test_stale_approved_brief_returns_repair_brief(tmp_path, ready_batch):
    context = ready_batch.context(tmp_path)
    path = context.root / "assignments" / "essay-2.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "Changed after approval.\n",
        encoding="utf-8",
    )

    action = next_action(context)

    assert action.action == "repair-brief"


def test_missing_manifest_returns_redact(tmp_path, ready_batch):
    context = ready_batch.context(tmp_path)
    (context.redacted / "_redaction.json").unlink()

    action = next_action(context)

    assert action.action == "redact"


def test_roster_load_failure_invalidates_prior_clean_redaction(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    previous = _install_clean_reviewed_redaction(context, tmp_path)
    source = tmp_path / "new-submissions"
    source.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/redact.py",
            str(source),
            "--roster",
            str(tmp_path / "missing-roster.csv"),
            "--batch",
            context.batch,
            "--out",
            str(context.redacted),
            "--map",
            str(context.root / "maps" / f"{context.batch}.json"),
                "--review",
                str(context.root / "maps" / "reviews" / f"{context.batch}.json"),
                "--force",
            ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == REDACTION_UNREADABLE
    _assert_prior_redaction_is_invalidated(
        context, previous, REDACTION_UNREADABLE
    )


def test_oversized_file_count_invalidates_prior_clean_redaction(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    previous = _install_clean_reviewed_redaction(context, tmp_path)
    source = tmp_path / "too-many-submissions"
    source.mkdir()
    for index in range(501):
        (source / f"paper-{index:03d}.md").write_text("safe", encoding="utf-8")

    result = redact_batch(
        source=source,
        destination=context.redacted,
        map_path=context.root / "maps" / f"{context.batch}.json",
        roster=[],
        batch=context.batch,
        reviewed=False,
        force=True,
    )

    assert result.exit_code == REDACTION_UNREADABLE
    _assert_prior_redaction_is_invalidated(
        context, previous, REDACTION_UNREADABLE
    )


def test_unsafe_source_invalidates_safe_prior_redaction(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    previous = _install_clean_reviewed_redaction(context, tmp_path)
    real_source = tmp_path / "real-new-submissions"
    real_source.mkdir()
    unsafe_source = tmp_path / "linked-new-submissions"
    unsafe_source.symlink_to(real_source, target_is_directory=True)

    result = redact_batch(
        source=unsafe_source,
        destination=context.redacted,
        map_path=context.root / "maps" / f"{context.batch}.json",
        roster=[],
        batch=context.batch,
        reviewed=False,
        force=True,
    )

    assert result.exit_code == REDACTION_UNSAFE
    _assert_prior_redaction_is_invalidated(context, previous, REDACTION_UNSAFE)


def test_first_failure_without_manifest_cannot_advance_to_drafting(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    (context.redacted / "_redaction.json").unlink()
    source = tmp_path / "first-too-many-submissions"
    source.mkdir()
    for index in range(501):
        (source / f"paper-{index:03d}.md").write_text("safe", encoding="utf-8")

    result = redact_batch(
        source=source,
        destination=context.redacted,
        map_path=context.root / "maps" / f"{context.batch}.json",
        roster=[],
        batch=context.batch,
        reviewed=False,
        force=True,
    )

    assert result.exit_code == REDACTION_UNREADABLE
    assert next_action(context).action == "redact"


def test_symlinked_manifest_returns_repair_batch_without_following_it(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    manifest = context.redacted / "_redaction.json"
    private = tmp_path / "private-manifest.json"
    private.write_bytes(manifest.read_bytes())
    manifest.unlink()
    manifest.symlink_to(private)

    action = next_action(context)

    assert action.action == "repair-batch"
    assert action.findings[0].code == "symlink-redaction-manifest"


@pytest.mark.parametrize("exit_code", [0, 3])
def test_unreviewed_manifest_returns_review_redaction(
    tmp_path, ready_batch, exit_code
):
    context = ready_batch.context(tmp_path)
    context = replace(
        context,
        submissions=tmp_path / "submissions",
        roster=tmp_path / "roster.csv",
    )
    source = context.redacted / "STUDENT_01.md"
    RedactionManifest.create(
        context.batch,
        "test-redaction-run",
        [source],
        reviewed=False,
        exit_code=exit_code,
        map_sha256=TEST_MAP_SHA256,
        review_sha256=TEST_REVIEW_SHA256,
    ).save(context.redacted)

    action = next_action(context)
    result = _next_run(
        context,
        "--json",
        "--submissions",
        str(context.submissions),
        "--roster",
        str(context.roster),
    )

    assert action.action == "review-redaction"
    assert "--reviewed" in action.commands[0]
    assert "--force" not in action.commands[0]
    assert result.returncode == 0
    assert json.loads(result.stdout) == action.as_dict()


@pytest.mark.parametrize("schema", ["redaction-batch/1", "redaction-batch/2"])
def test_legacy_manifest_requires_a_fresh_unreviewed_redaction(
    tmp_path, ready_batch, schema
):
    context = replace(
        ready_batch.context(tmp_path),
        submissions=tmp_path / "submissions",
        roster=tmp_path / "roster.csv",
    )
    output = context.redacted / "STUDENT_01.md"
    payload = {
        "schema": schema,
        "batch": context.batch,
        "run_id": "test-redaction-run",
        "outputs": [
            {
                "path": output.name,
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
        ],
    }
    if schema == "redaction-batch/2":
        payload.update({"reviewed": True, "exit_code": 0})
    (context.redacted / "_redaction.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )

    action = next_action(context)

    assert action.action == "repair-redaction"
    assert [finding.code for finding in action.findings] == [
        "legacy-redaction-manifest"
    ]
    assert "--reviewed" not in action.commands[0]
    assert "--force" in action.commands[0]


@pytest.mark.parametrize("exit_code", [2, 4, 5, 6, 7])
def test_incomplete_redaction_returns_exact_repair_action_and_cli_exit(
    tmp_path, ready_batch, exit_code
):
    context = ready_batch.context(tmp_path)
    source = context.redacted / "STUDENT_01.md"
    RedactionManifest.create(
        context.batch,
        "test-redaction-run",
        [source],
        reviewed=True,
        exit_code=exit_code,
        map_sha256=TEST_MAP_SHA256,
        review_sha256=TEST_REVIEW_SHA256,
    ).save(context.redacted)

    action = next_action(context)
    result = _next_run(context, "--json")

    assert action.action == "repair-redaction"
    assert action.commands == ()
    assert len(action.findings) == 1
    assert action.findings[0].code == "redaction-incomplete"
    assert action.findings[0].token == ""
    assert action.as_dict()["message"] == (
        f"Repair the redaction inputs after exit {exit_code}, then run redaction again."
    )
    assert result.returncode == 12
    assert json.loads(result.stdout) == action.as_dict()


def test_reviewed_rerun_with_roster_failure_precedes_inventory_and_does_not_advance(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    source = context.redacted / "STUDENT_01.md"
    RedactionManifest.create(
        context.batch,
        "test-redaction-run",
        [source],
        reviewed=True,
        exit_code=2,
        map_sha256=TEST_MAP_SHA256,
        review_sha256=TEST_REVIEW_SHA256,
    ).save(context.redacted)
    (context.drafts / "STUDENT_99.json").write_text("{}", encoding="utf-8")

    assert next_action(context).action == "repair-redaction"


def test_record_prepare_cli_blocks_a_reviewed_incomplete_redaction(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    source = context.redacted / "STUDENT_01.md"
    RedactionManifest.create(
        context.batch,
        "test-redaction-run",
        [source],
        reviewed=True,
        exit_code=2,
        map_sha256=TEST_MAP_SHA256,
        review_sha256=TEST_REVIEW_SHA256,
    ).save(context.redacted)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/grade.py",
            "record",
            "prepare",
            "--batch",
            context.batch,
            "--course",
            context.course,
            "--rubric",
            str(context.rubric),
            "--source",
            str(source),
            "--redacted",
            str(context.redacted),
            "--drafts",
            str(context.drafts),
            "--root",
            str(context.root),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 12
    assert "Redaction exited with code 2" in result.stderr
    assert list(context.drafts.iterdir()) == []


def test_inventory_finding_returns_repair_batch(tmp_path, ready_batch):
    context = ready_batch.context(tmp_path)
    (context.drafts / "STUDENT_99.json").write_text("{}", encoding="utf-8")

    action = next_action(context)

    assert action.action == "repair-batch"
    assert action.findings[0].code == "orphaned-artifact"


def test_template_pair_returns_complete_draft(tmp_path, ready_batch):
    context = _template_context(tmp_path, ready_batch)

    action = next_action(context)

    assert action.action == "complete-draft"
    assert action.token == "STUDENT_01"


def test_template_pair_with_total_error_returns_repair_verification(
    tmp_path, ready_batch
):
    context = _template_context(tmp_path, ready_batch)
    record_path = context.drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.total = 1
    record.save(record_path)

    action = next_action(context)

    assert action.action == "repair-verification"
    assert {finding.code for finding in action.findings} >= {
        "template-marker",
        "nothing-assessed",
        "total",
    }


def test_next_cli_mixed_template_findings_exit_twelve(tmp_path, ready_batch):
    context = _template_context(tmp_path, ready_batch)
    record_path = context.drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.total = 1
    record.save(record_path)

    result = _next_run(context, "--json")

    assert result.returncode == 12
    payload = json.loads(result.stdout)
    assert payload["action"] == "repair-verification"
    assert {finding["code"] for finding in payload["findings"]} >= {
        "template-marker",
        "nothing-assessed",
        "total",
    }


def test_invalid_record_returns_repair_verification(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    record_path = context.drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.total = 95
    record.save(record_path)

    action = next_action(context)

    assert action.action == "repair-verification"
    assert "total" in {finding.code for finding in action.findings}
    assert {
        "code": "total",
        "message": "The total is 95; the scored criteria add to 82.",
        "token": "STUDENT_01",
    } in action.as_dict()["findings"]


def test_clean_dry_run_returns_verify(tmp_path, phil210, approved_brief):
    context = _complete_context(tmp_path, phil210, approved_brief)

    action = next_action(context)

    assert action.action == "verify"
    assert action.commands[0][:3] == ("python3", "scripts/grade.py", "verify")


def test_current_receipt_returns_consistency_and_publish(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    report = GradingBatch.open(
        BatchPaths(context.batch, context.redacted, context.drafts)
    ).verify(
        VerificationInputs(
            course=context.course,
            rubric=context.rubric,
            profile=context.root / "profiles" / "phil210.md",
            patterns=context.patterns,
            brief=context.root / "assignments" / "essay-2.md",
        )
    )
    assert report.persisted is True

    action = next_action(context)

    assert action.action == "review-consistency"
    assert [command[2] for command in action.commands] == ["consistency"]
    assert "--course" in action.commands[0]
    assert ack_phrase("essay-2") in action.message

    _acknowledge(context.drafts)
    action = next_action(context)

    assert action.action == "publish"
    assert [command[2] for command in action.commands] == [str(context.drafts)]


def test_stale_receipt_returns_repair_verification(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    report = GradingBatch.open(
        BatchPaths(context.batch, context.redacted, context.drafts)
    ).verify(
        VerificationInputs(
            course=context.course,
            rubric=context.rubric,
            profile=context.root / "profiles" / "phil210.md",
            patterns=context.patterns,
            brief=context.root / "assignments" / "essay-2.md",
        )
    )
    assert report.persisted is True
    (context.drafts / "_verification.json").write_text("{}", encoding="utf-8")

    action = next_action(context)

    assert action.action == "repair-verification"
    assert action.findings[0].code == "malformed-verification-receipt"


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_nonregular_receipt_precedes_pending_batch_action(
    tmp_path, ready_batch, kind
):
    context = ready_batch.context(tmp_path)
    _install_nonregular_receipt(context, kind)

    action = next_action(context)

    assert action.action == "repair-verification"
    assert action.findings[0].code == "malformed-verification-receipt"
    assert "regular file" in action.findings[0].message


@pytest.mark.parametrize("kind", ["directory", "symlink"])
def test_next_cli_nonregular_receipt_exits_twelve(
    tmp_path, ready_batch, kind
):
    context = ready_batch.context(tmp_path)
    _install_nonregular_receipt(context, kind)

    result = _next_run(context, "--json")

    assert result.returncode == 12
    payload = json.loads(result.stdout)
    assert payload["action"] == "repair-verification"
    assert payload["findings"][0]["code"] == "malformed-verification-receipt"


def test_orphan_inventory_precedes_directory_receipt_repair(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    (context.drafts / "STUDENT_99.json").write_text("{}", encoding="utf-8")
    _install_nonregular_receipt(context, "directory")

    action = next_action(context)

    assert action.action == "repair-batch"
    assert "orphaned-artifact" in {finding.code for finding in action.findings}


def test_incomplete_inventory_precedes_symlink_receipt_repair(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    (context.drafts / "STUDENT_01.md").write_text(
        "unfinished feedback", encoding="utf-8"
    )
    _install_nonregular_receipt(context, "symlink")

    action = next_action(context)

    assert action.action == "repair-batch"
    assert "incomplete-pair" in {finding.code for finding in action.findings}


def test_publication_returns_terminal_review_publication(
    tmp_path, phil210, approved_brief
):
    context = _published_context(tmp_path, phil210, approved_brief)

    action = next_action(context)

    assert action.action == "review-publication"
    assert action.commands == ()


def test_modified_published_feedback_is_not_terminal(
    tmp_path, phil210, approved_brief
):
    context = _published_context(tmp_path, phil210, approved_brief)
    (context.publication / "STUDENT_01.md").write_text(
        "modified identity-bearing feedback", encoding="utf-8"
    )

    with pytest.raises(WorkflowError, match="published feedback changed"):
        next_action(context)


def test_publication_output_digest_must_be_lowercase_hex(
    tmp_path, phil210, approved_brief
):
    context = _published_context(tmp_path, phil210, approved_brief)
    marker_path = context.publication / "_publication.json"
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    marker["outputs"][0]["sha256"] = "A" * 64
    marker_path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(WorkflowError, match="malformed output"):
        next_action(context)


def test_publication_with_stale_extra_file_is_not_terminal(
    tmp_path, phil210, approved_brief
):
    context = _published_context(tmp_path, phil210, approved_brief)
    (context.publication / "STALE_STUDENT.md").write_text(
        "identity-bearing stale output", encoding="utf-8"
    )

    with pytest.raises(WorkflowError, match="unexpected entries"):
        next_action(context)


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_publication_with_extra_nonregular_entry_is_not_terminal(
    tmp_path, phil210, approved_brief, kind
):
    context = _published_context(tmp_path, phil210, approved_brief)
    extra = context.publication / "STALE_ENTRY"
    if kind == "symlink":
        private = tmp_path / "private-extra-publication"
        private.write_text("must not be read", encoding="utf-8")
        private.chmod(0)
        extra.symlink_to(private)
    else:
        extra.mkdir()

    with pytest.raises(WorkflowError, match="unexpected entries"):
        next_action(context)


@pytest.mark.parametrize("kind", ["symlink", "directory"])
def test_publication_expected_member_must_be_a_regular_unlinked_file(
    tmp_path, phil210, approved_brief, kind
):
    context = _published_context(tmp_path, phil210, approved_brief)
    output = context.publication / "STUDENT_01.md"
    output.unlink()
    if kind == "symlink":
        private = tmp_path / "private-publication"
        private.write_text("must not be read", encoding="utf-8")
        private.chmod(0)
        output.symlink_to(private)
    else:
        output.mkdir()

    with pytest.raises(WorkflowError, match="regular file"):
        next_action(context)


def test_redaction_action_renders_private_paths_without_reading_them(
    tmp_path, ready_batch
):
    context = ready_batch.context(tmp_path)
    (context.redacted / "_redaction.json").unlink()
    context, submissions, roster, map_path, review = _private_sentinels(
        context, tmp_path
    )

    action = next_action(context)

    assert action.action == "redact"
    assert action.commands == (
        (
            "python3",
            "scripts/redact.py",
            str(submissions),
            "--roster",
            str(roster),
            "--batch",
            context.batch,
            "--out",
            str(context.redacted),
            "--map",
            str(map_path),
            "--review",
            str(review),
        ),
    )


def test_publication_action_renders_map_path_without_reading_private_sentinels(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    report = GradingBatch.open(
        BatchPaths(context.batch, context.redacted, context.drafts)
    ).verify(
        VerificationInputs(
            course=context.course,
            rubric=context.rubric,
            profile=context.root / "profiles" / "phil210.md",
            patterns=context.patterns,
            brief=context.root / "assignments" / "essay-2.md",
        )
    )
    assert report.persisted is True
    _acknowledge(context.drafts)
    context, _, _, map_path, _ = _private_sentinels(context, tmp_path)

    action = next_action(context)

    assert action.action == "publish"
    assert action.commands[0][-4:] == (
        "--map",
        str(map_path),
        "--out",
        str(context.publication),
    )


def test_next_cli_emits_stable_json_argv(tmp_path, ready_batch):
    result = _next_run(ready_batch.context(tmp_path), "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema"] == "grading-next-action/2"
    assert payload["action"] == "prepare-record"
    assert isinstance(payload["commands"][0], list)


def test_next_cli_defaults_publication_to_batch_directory(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    report = GradingBatch.open(
        BatchPaths(context.batch, context.redacted, context.drafts)
    ).verify(
        VerificationInputs(
            course=context.course,
            rubric=context.rubric,
            profile=context.root / "profiles" / "phil210.md",
            patterns=context.patterns,
            brief=context.root / "assignments" / "essay-2.md",
        )
    )
    assert report.persisted is True

    _acknowledge(context.drafts)
    result = _next_run(context, "--json", include_publication=False)

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["action"] == "publish"
    publication = payload["commands"][0]
    assert publication[-2:] == ["--out", "final/essay-2"]


def test_next_cli_prints_plain_repair_action_and_exits_twelve(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    record_path = context.drafts / "STUDENT_01.json"
    record = GradeRecord.load(record_path)
    record.total = 95
    record.save(record_path)

    result = _next_run(context)

    assert result.returncode == 12
    assert "repair-verification" in result.stdout
    assert "total" in result.stdout
    assert "Traceback" not in result.stderr


def test_next_cli_exits_thirteen_for_malformed_workflow_state(
    tmp_path, ready_batch
):
    context = replace(
        ready_batch.context(tmp_path), rubric=tmp_path / "missing-rubric.md"
    )

    result = _next_run(context, "--json")

    assert result.returncode == 13
    assert result.stdout == ""
    assert "Traceback" not in result.stderr


def test_missing_receipt_returns_validate_holdout(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210, approved=False)

    action = next_action(context)

    assert action.action == "validate-holdout"
    assert action.stage == "calibration"
    assert "holdout" in action.message
    assert action.commands == ()


def test_passed_receipt_returns_approve_profile_with_the_command(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    profile = _write_profile(context, phil210, approved=False)
    _write_receipt(context.root, phil210, profile)

    action = next_action(context)

    assert action.action == "approve-profile"
    assert action.commands[0][:3] == ("python3", "scripts/calibrate.py", "approve")
    assert "Approve calibration profile for phil210" in action.commands[0]
    assert "--accept-gap" not in action.commands[0]


def test_failed_receipt_returns_review_holdout_without_commands(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    profile = _write_profile(context, phil210, approved=False)
    _write_receipt(context.root, phil210, profile, totals=(97, None))

    action = next_action(context)

    assert action.action == "review-holdout"
    assert action.commands == ()
    assert action.findings
    assert all(finding.code == "holdout-gap" for finding in action.findings)
    assert "accept-gap" in action.message


def test_edited_profile_after_approval_returns_validate_holdout(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    profile = _write_profile(context, phil210)
    profile.write_text(profile.read_text(encoding="utf-8") + "\n- extra\n", encoding="utf-8")

    action = next_action(context)

    assert action.action == "validate-holdout"
    assert action.findings[0].code == "stale-receipt"


def test_malformed_receipt_returns_repair_calibration_and_exits_twelve(
    tmp_path, phil210
):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210, approved=False)
    receipt = context.root / "calibration" / "phil210.receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text("{}", encoding="utf-8")

    assert next_action(context).action == "repair-calibration"
    result = _next_run(context)
    assert result.returncode == 12


def test_a_holdout_brief_skips_the_calibration_gate(tmp_path, phil210):
    context = _empty_context(tmp_path, phil210)
    _write_profile(context, phil210, approved=False)
    path = context.root / "assignments" / "essay-2.md"
    prepare_brief(
        path, batch="essay-2", course="phil210", mode="local",
        purpose="holdout", review="none",
    )

    assert next_action(context).action == "complete-brief"


def test_review_none_grading_brief_needs_the_demo_marker(
    tmp_path, phil210, approved_brief
):
    context = replace(_empty_context(tmp_path, phil210), batch="synthetic-demo")
    profile = _write_profile(context, phil210)
    approved_brief(
        context.root / "assignments" / "synthetic-demo.md",
        batch="synthetic-demo",
        course="phil210",
        rubric=context.rubric,
        profile=profile,
        review="none",
    )

    action = next_action(context)

    assert action.action == "repair-brief"
    assert action.findings[0].code == "review-cadence-reserved"
    (context.root / "demo.json").write_text(
        json.dumps({"authorization": "synthetic-demo"}), encoding="utf-8"
    )
    assert next_action(context).action == "redact"


def test_pilot_stops_after_the_pilot_size(tmp_path, phil210, approved_brief):
    context = _pilot_context(tmp_path, phil210, approved_brief)
    _add_source(context, "STUDENT_02")

    action = next_action(context)
    assert action.action == "prepare-record"
    assert action.token == "STUDENT_02"

    _add_draft(context, "STUDENT_02")
    action = next_action(context)
    assert action.action == "review-pilot"
    assert action.stage == "review"
    assert action.tokens == ("STUDENT_01", "STUDENT_02")
    assert action.commands[0][:4] == ("python3", "scripts/grade.py", "review", "approve")
    assert pilot_phrase("essay-2") in action.commands[0]
    assert pilot_phrase("essay-2") in action.message

    _add_source(context, "STUDENT_03")
    assert next_action(context).action == "review-pilot"

    _approve_pilot(context.redacted, context.drafts, tokens=("STUDENT_01", "STUDENT_02"))
    review = review_path(context.drafts)
    review.write_text(
        review.read_text(encoding="utf-8").replace('"pilot": 1', '"pilot": 2'),
        encoding="utf-8",
    )
    action = next_action(context)
    assert action.action == "prepare-record"
    assert action.token == "STUDENT_03"


def test_a_pilot_draft_edited_after_approval_stops_again(
    tmp_path, phil210, approved_brief
):
    context = _pilot_context(tmp_path, phil210, approved_brief, pilot=1)
    _approve_pilot(context.redacted, context.drafts)
    _add_source(context, "STUDENT_02")
    assert next_action(context).action == "prepare-record"

    (context.drafts / "STUDENT_01.md").write_text(
        FEEDBACK.replace("holds", "stands"), encoding="utf-8"
    )
    action = next_action(context)

    assert action.action == "review-pilot"
    assert action.tokens == ("STUDENT_01",)
    assert [finding.code for finding in action.findings] == ["draft-review-stale"]


def test_drafting_past_the_pilot_without_approval_is_a_repair(
    tmp_path, phil210, approved_brief
):
    context = _pilot_context(tmp_path, phil210, approved_brief)
    for token in ("STUDENT_02", "STUDENT_03"):
        _add_draft(context, token)

    action = next_action(context)

    assert action.action == "repair-review"
    assert action.findings[0].code == "pilot-overrun"
    assert "STUDENT_03" in action.findings[0].message
    assert _next_run(context).returncode == 12


def test_every_cadence_stops_after_each_draft(tmp_path, phil210, approved_brief):
    context = _pilot_context(tmp_path, phil210, approved_brief, review="every")
    _add_source(context, "STUDENT_02")

    action = next_action(context)

    assert action.action == "review-draft"
    assert action.token == "STUDENT_01"
    assert "Approve draft STUDENT_01 for essay-2" in action.commands[0]
    assert ("--scope", "draft", "--token", "STUDENT_01") == action.commands[0][-6:-2]

    artifacts = GradingBatch.open(
        BatchPaths("essay-2", context.redacted, context.drafts)
    ).draft_artifacts()
    approve_review(
        review_path(context.drafts), batch="essay-2", course="phil210",
        cadence="every", pilot=2, scope="draft",
        artifacts=[artifacts["STUDENT_01"]],
        approval="Approve draft STUDENT_01 for essay-2",
    )
    action = next_action(context)
    assert action.action == "prepare-record"
    assert action.token == "STUDENT_02"


def test_verified_batch_stops_for_consistency_then_publishes(
    tmp_path, phil210, approved_brief
):
    context = _complete_context(tmp_path, phil210, approved_brief)
    GradingBatch.open(
        BatchPaths("essay-2", context.redacted, context.drafts)
    ).verify(_inputs_for(context))

    action = next_action(context)
    assert action.action == "review-consistency"
    assert action.commands[0][:3] == ("python3", "scripts/grade.py", "consistency")

    write_acknowledgement(
        ack_path(context.drafts), batch="essay-2", course="phil210",
        receipt_sha256="b" * 64, flags=[], approval=ack_phrase("essay-2"),
    )
    assert next_action(context).action == "review-consistency"

    _acknowledge(context.drafts)
    action = next_action(context)
    assert action.action == "publish"
    assert action.commands[0][:2] == ("python3", "scripts/rehydrate.py")


def test_holdout_batch_ends_at_report_holdout(tmp_path, phil210, approved_brief):
    context = _complete_context(
        tmp_path, phil210, approved_brief, pilot_approved=False
    )
    brief = context.root / "assignments" / "essay-2.md"
    brief.unlink()
    approved_brief(
        brief, batch="essay-2", course="phil210", rubric=context.rubric,
        profile=context.root / "profiles" / "phil210.md", purpose="holdout",
    )
    for path in (context.root / "calibration").iterdir():
        path.unlink()

    assert next_action(context).action == "verify"
    GradingBatch.open(
        BatchPaths("essay-2", context.redacted, context.drafts)
    ).verify(_inputs_for(context))
    action = next_action(context)

    assert action.action == "report-holdout"
    assert action.stage == "calibration"
    assert action.commands == ()
    assert "calibrate.py holdout" in action.message
