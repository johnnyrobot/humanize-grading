"""Derive one legal grading action from durable identity-free state."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts.grading.batch import (
    BatchPaths,
    BlockingFinding,
    GradingBatch,
    Progress,
    PUBLICATION_NAME,
    PUBLICATION_SCHEMA,
    RECEIPT_NAME,
    VerifiedBatch,
    VerificationInputs,
)
from scripts.grading.brief import (
    ANSWER_REQUIRED,
    BriefError,
    brief_path,
    check_brief,
    load_brief,
)
from scripts.grading.calibration import (
    approval_phrase,
    check_calibration,
    demo_authorized,
)
from scripts.grading.profile import ProfileError, load_profile, profile_path
from scripts.grading.review import (
    REVIEW_CODES,
    ReviewError,
    ack_path,
    ack_phrase,
    draft_phrase,
    load_acknowledgement,
    load_review,
    pilot_phrase,
    review_path,
)
from scripts.grading.rubric import load_rubric
from scripts.redaction.manifest import RedactionManifest
from scripts.redaction.manifest import NAME as REDACTION_MANIFEST_NAME
from scripts.redaction.manifest import SCHEMA as REDACTION_MANIFEST_SCHEMA
from scripts.path_safety import read_bytes_limited, read_text_limited

NEXT_SCHEMA = "grading-next-action/2"
_TEMPLATE_INCOMPLETE_CODES = {"template-marker", "nothing-assessed"}


class WorkflowError(ValueError):
    """Durable grading state is malformed or contradictory."""


@dataclass(frozen=True)
class WorkflowContext:
    batch: str
    course: str
    rubric: Path
    redacted: Path
    drafts: Path
    root: Path
    patterns: Path
    publication: Path
    submissions: Path | None = None
    roster: Path | None = None
    instructor: tuple[str, ...] = ()


@dataclass(frozen=True)
class NextAction:
    stage: str
    action: str
    message: str
    commands: tuple[tuple[str, ...], ...] = ()
    token: str = ""
    findings: tuple[BlockingFinding, ...] = ()
    tokens: tuple[str, ...] = ()
    _batch: str = field(default="", init=False, repr=False, compare=False)
    _course: str = field(default="", init=False, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Return the stable JSON-ready next-action document."""
        return {
            "schema": NEXT_SCHEMA,
            "batch": self._batch,
            "course": self._course,
            "stage": self.stage,
            "action": self.action,
            "message": self.message,
            "token": self.token,
            "tokens": list(self.tokens),
            "commands": [list(command) for command in self.commands],
            "findings": [asdict(finding) for finding in self.findings],
        }


def _action(context: WorkflowContext, **values) -> NextAction:
    action = NextAction(**values)
    object.__setattr__(action, "_batch", context.batch)
    object.__setattr__(action, "_course", context.course)
    return action


def _redaction_commands(
    context: WorkflowContext, *, reviewed: bool, force: bool = False
) -> tuple[tuple[str, ...], ...]:
    if context.submissions is None or context.roster is None:
        return ()
    command = [
        "python3",
        "scripts/redact.py",
        str(context.submissions),
        "--roster",
        str(context.roster),
        "--batch",
        context.batch,
        "--out",
        str(context.redacted),
        "--map",
        str(context.root / "maps" / f"{context.batch}.json"),
        "--review",
        str(context.root / "maps" / "reviews" / f"{context.batch}.json"),
    ]
    for instructor in context.instructor:
        command.extend(("--instructor", instructor))
    if reviewed:
        command.append("--reviewed")
    if force:
        command.append("--force")
    return (tuple(command),)


def _publication_is_current(
    context: WorkflowContext, status, receipt: Path
) -> bool:
    if not context.publication.exists() and not context.publication.is_symlink():
        return False
    if context.publication.is_symlink() or not context.publication.is_dir():
        raise WorkflowError("The publication path must be a regular directory.")
    expected = {
        entry.draft.name for entry in status.entries if entry.draft is not None
    }
    marker = context.publication / PUBLICATION_NAME
    try:
        actual = {path.name for path in context.publication.iterdir()}
    except OSError as error:
        raise WorkflowError(f"Cannot inspect publication membership: {error}") from error
    if actual != expected | {PUBLICATION_NAME}:
        raise WorkflowError(
            "The publication has missing, renamed, or unexpected entries."
        )
    if marker.is_symlink() or not marker.is_file():
        raise WorkflowError("The publication marker must be one regular file.")
    try:
        payload = json.loads(
            read_text_limited(marker, "The publication marker", 1_048_576)
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise WorkflowError(f"The publication marker is malformed: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != PUBLICATION_SCHEMA:
        raise WorkflowError("The publication marker has an unknown schema.")
    if payload.get("batch") != context.batch:
        raise WorkflowError("The publication belongs to another grading batch.")
    receipt_digest = hashlib.sha256(
        read_bytes_limited(receipt, "The verification receipt", 1_048_576)
    ).hexdigest()
    if payload.get("receipt_sha256") != receipt_digest:
        raise WorkflowError("The publication was created from a stale receipt.")
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        raise WorkflowError("The publication marker has no output list.")
    marker_outputs: dict[str, str] = {}
    for item in outputs:
        if not isinstance(item, dict):
            raise WorkflowError("The publication marker has a malformed output.")
        name = item.get("path")
        digest = item.get("sha256")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in marker_outputs
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise WorkflowError("The publication marker has a malformed output.")
        marker_outputs[name] = digest
    if set(marker_outputs) != expected:
        raise WorkflowError("The publication marker does not name every draft.")
    if any(
        path.is_symlink() or not path.is_file()
        for path in (context.publication / name for name in expected)
    ):
        raise WorkflowError("Every published draft must be one regular file.")
    for name, expected_digest in marker_outputs.items():
        actual_digest = hashlib.sha256(
            read_bytes_limited(
                context.publication / name,
                "The published feedback",
                2 * 1024 * 1024,
            )
        ).hexdigest()
        if actual_digest != expected_digest:
            raise WorkflowError(f"The published feedback changed: {name}")
    return True


def next_action(context: WorkflowContext) -> NextAction:
    """Return the first legal action for the current grading state."""
    current_profile = profile_path(context.course, context.root)
    try:
        profile = load_profile(current_profile)
    except ProfileError:
        if current_profile.exists() or current_profile.is_symlink():
            raise
        return _action(
            context,
            stage="calibration",
            action="calibrate",
            message=f"Calibrate a profile for {context.course} before grading.",
        )
    if profile.course != context.course:
        raise WorkflowError(
            f"The calibration profile belongs to {profile.course!r}, "
            f"not {context.course!r}."
        )
    rubric = load_rubric(context.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        return _action(
            context,
            stage="calibration",
            action="recalibrate",
            message="Recalibrate the profile for the current rubric: "
            + "; ".join(drifted),
        )
    current_brief = brief_path(context.root, context.batch)
    peeked = _peek_brief(current_brief)
    if peeked is None or peeked.purpose != "holdout":
        gate = _calibration_gate(context, profile)
        if gate is not None:
            return gate
    if not current_brief.exists() and not current_brief.is_symlink():
        return _action(
            context,
            stage="briefing",
            action="prepare-brief",
            message=(
                "Prepare the assignment brief after choosing local or cloud mode "
                "and a review cadence."
            ),
        )
    try:
        loaded_brief = load_brief(current_brief)
    except BriefError as error:
        finding = BlockingFinding("assignment-brief", str(error), "")
        return _action(
            context,
            stage="briefing",
            action="repair-brief",
            message="Repair or recreate the malformed assignment brief.",
            findings=(finding,),
        )
    if loaded_brief.batch != context.batch or loaded_brief.course != context.course:
        finding = BlockingFinding(
            "assignment-brief-context",
            "The assignment brief batch or course does not match this workflow.",
            "",
        )
        return _action(
            context,
            stage="briefing",
            action="repair-brief",
            message="Repair the assignment brief context before continuing.",
            findings=(finding,),
        )
    if loaded_brief.status == "draft" and ANSWER_REQUIRED in loaded_brief.body:
        return _action(
            context,
            stage="briefing",
            action="complete-brief",
            message="Answer every required item in the draft assignment brief.",
        )
    if loaded_brief.status == "draft":
        command = (
            "python3",
            "scripts/grade.py",
            "brief",
            "approve",
            "--batch",
            context.batch,
            "--course",
            context.course,
            "--rubric",
            str(context.rubric),
            "--approval",
            f"Approve assignment brief for {context.batch}",
            "--root",
            str(context.root),
        )
        return _action(
            context,
            stage="briefing",
            action="approve-brief",
            message="Approve the completed assignment brief with the exact phrase.",
            commands=(command,),
        )
    try:
        check_brief(
            current_brief,
            batch=context.batch,
            course=context.course,
            rubric=context.rubric,
            profile=profile.path,
        )
    except BriefError as error:
        finding = BlockingFinding("assignment-brief", str(error), "")
        return _action(
            context,
            stage="briefing",
            action="repair-brief",
            message="Repair and reapprove the stale assignment brief.",
            findings=(finding,),
        )
    if (
        loaded_brief.review == "none"
        and loaded_brief.purpose == "grading"
        and not demo_authorized(context.root)
    ):
        finding = BlockingFinding(
            "review-cadence-reserved",
            "This grading brief has review cadence none without a demo marker.",
            "",
        )
        return _action(
            context,
            stage="briefing",
            action="repair-brief",
            message=(
                "Review cadence none is reserved for the prepared synthetic demo. "
                "Prepare a brief with pilot or every."
            ),
            findings=(finding,),
        )
    manifest_path = context.redacted / REDACTION_MANIFEST_NAME
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return _action(
            context,
            stage="redaction",
            action="redact",
            message="Redact the submissions before reading student work.",
            commands=_redaction_commands(context, reviewed=False),
        )
    if manifest_path.is_symlink():
        finding = BlockingFinding(
            "symlink-redaction-manifest",
            "The redaction manifest must not use a symbolic link.",
            "",
        )
        return _action(
            context,
            stage="redaction",
            action="repair-batch",
            message="Replace the linked redaction manifest with a regular file.",
            findings=(finding,),
        )
    try:
        manifest = RedactionManifest.load(context.redacted)
    except (OSError, UnicodeError, ValueError) as error:
        finding = BlockingFinding("malformed-redaction-manifest", str(error), "")
        return _action(
            context,
            stage="redaction",
            action="repair-batch",
            message="Repair the malformed redaction manifest.",
            findings=(finding,),
        )
    if manifest.batch != context.batch:
        finding = BlockingFinding(
            "malformed-redaction-manifest",
            "The redaction manifest belongs to another grading batch.",
            "",
        )
        return _action(
            context,
            stage="redaction",
            action="repair-batch",
            message="Repair the redaction result before grading.",
            findings=(finding,),
        )
    if manifest.schema != REDACTION_MANIFEST_SCHEMA:
        finding = BlockingFinding(
            "legacy-redaction-manifest",
            "The redaction manifest uses an older schema and must be replaced.",
            "",
        )
        return _action(
            context,
            stage="redaction",
            action="repair-redaction",
            message="Run a fresh unreviewed redaction for the current schema.",
            commands=_redaction_commands(context, reviewed=False, force=True),
            findings=(finding,),
        )
    if manifest.exit_code == 3 or (
        manifest.exit_code in {None, 0} and not manifest.reviewed
    ):
        return _action(
            context,
            stage="redaction",
            action="review-redaction",
            message="Review the private redaction report, then confirm the rerun.",
            commands=_redaction_commands(context, reviewed=True),
        )
    if manifest.exit_code != 0:
        finding = BlockingFinding(
            "redaction-incomplete",
            (
                f"Redaction exited with code {manifest.exit_code}. Fix its reported "
                "inputs or paths, then run redaction again."
            ),
            "",
        )
        return _action(
            context,
            stage="redaction",
            action="repair-redaction",
            message=(
                f"Repair the redaction inputs after exit {manifest.exit_code}, "
                "then run redaction again."
            ),
            findings=(finding,),
        )
    try:
        manifest.validate(context.redacted)
    except (OSError, UnicodeError, ValueError) as error:
        finding = BlockingFinding("malformed-redaction-manifest", str(error), "")
        return _action(
            context,
            stage="redaction",
            action="repair-batch",
            message="Repair the redaction result before grading.",
            findings=(finding,),
        )
    receipt = context.drafts / RECEIPT_NAME
    batch = GradingBatch.open(
        BatchPaths(
            batch=context.batch,
            redacted=context.redacted,
            drafts=context.drafts,
        )
    )
    status = batch.status()
    if status.blocking_findings:
        receipt_codes = {
            "stale-verification-receipt",
            "malformed-verification-receipt",
        }
        if all(
            finding.code in receipt_codes for finding in status.blocking_findings
        ):
            stale_only = all(
                finding.code == "stale-verification-receipt"
                for finding in status.blocking_findings
            )
            return _action(
                context,
                stage="verification",
                action="repair-verification",
                message=(
                    "A verified artifact changed. Run verification again."
                    if stale_only
                    else "Repair the verification receipt and verify again."
                ),
                commands=(_verify_command(context),) if stale_only else (),
                findings=status.blocking_findings,
            )
        return _action(
            context,
            stage="drafting",
            action="repair-batch",
            message="Repair the grading batch inventory before continuing.",
            findings=status.blocking_findings,
        )
    if receipt.is_symlink() or (receipt.exists() and not receipt.is_file()):
        finding = BlockingFinding(
            "malformed-verification-receipt",
            (
                "The verification receipt must be one regular file without a "
                "symbolic link. Remove it, then run verification again."
            ),
            "",
        )
        return _action(
            context,
            stage="verification",
            action="repair-verification",
            message="Remove the unsafe verification receipt and verify again.",
            findings=(finding,),
        )
    entries = [entry for entry in status.entries if entry.progress is not None]
    drafted = [entry for entry in entries if entry.progress is Progress.DRAFTED]
    pending = [entry for entry in entries if entry.progress is Progress.PENDING]
    inputs = VerificationInputs(
        course=context.course,
        rubric=context.rubric,
        profile=profile.path,
        patterns=context.patterns,
        brief=current_brief,
    )
    if drafted or not pending:
        verification = batch.verify(inputs, persist=False)
        template = [
            finding
            for finding in verification.blocking_findings
            if finding.code in _TEMPLATE_INCOMPLETE_CODES
        ]
        other = [
            finding
            for finding in verification.blocking_findings
            if finding.code not in _TEMPLATE_INCOMPLETE_CODES
            and finding.code not in REVIEW_CODES
        ]
        if other:
            return _action(
                context,
                stage="verification",
                action="repair-verification",
                message="Repair the verification findings before continuing.",
                findings=tuple(
                    finding
                    for finding in verification.blocking_findings
                    if finding.code not in REVIEW_CODES
                ),
            )
        if template:
            token = next((finding.token for finding in template if finding.token), "")
            return _action(
                context,
                stage="drafting",
                action="complete-draft",
                message=f"Complete the prepared draft pair for {token}.",
                token=token,
                findings=tuple(template),
            )
        review_stop = _review_gate(context, loaded_brief, batch, drafted, len(entries))
        if review_stop is not None:
            return review_stop
    if pending:
        first = pending[0]
        if first.source is None:
            raise ValueError("The pending submission has no redacted source.")
        return _action(
            context,
            stage="drafting",
            action="prepare-record",
            message=f"Prepare the draft pair for {first.token}.",
            token=first.token,
            commands=(_record_prepare_command(context, first.source),),
        )
    if receipt.is_file():
        resumed = VerifiedBatch.resume(
            BatchPaths(context.batch, context.redacted, context.drafts), inputs
        )
        if resumed.verified_batch is None:
            return _action(
                context,
                stage="verification",
                action="repair-verification",
                message="Repair the stale verification receipt and verify again.",
                findings=resumed.findings,
            )
        if loaded_brief.purpose == "holdout":
            return _action(
                context,
                stage="calibration",
                action="report-holdout",
                message=(
                    "The holdout batch is verified. Run calibrate.py holdout with "
                    "--course, --rubric, --packet, and one --record, --draft, and "
                    "--sample per holdout, then show the instructor the report."
                ),
            )
        if _publication_is_current(context, status, receipt):
            return _action(
                context,
                stage="publication",
                action="review-publication",
                message=(
                    "Review every identity-bearing published draft before "
                    "sending feedback."
                ),
            )
        consistency = _consistency_command(context)
        publication = _publication_command(context, profile, current_brief)
        if loaded_brief.review == "none":
            return _action(
                context,
                stage="publication",
                action="consistency-and-publish",
                message="Review consistency flags, then publish rehydrated drafts.",
                commands=(consistency, publication),
            )
        if _current_acknowledgement(context, receipt) is None:
            return _action(
                context,
                stage="publication",
                action="review-consistency",
                message=(
                    "Run consistency and show the instructor every flag. Wait for: "
                    f"{ack_phrase(context.batch)} Then run the same command again "
                    "with --acknowledge and that exact phrase."
                ),
                commands=(consistency,),
            )
        return _action(
            context,
            stage="publication",
            action="publish",
            message=(
                "Publish the rehydrated drafts locally. The instructor reviews "
                "them before sending anything."
            ),
            commands=(publication,),
        )
    return _action(
        context,
        stage="verification",
        action="verify",
        message="Persist verification for the clean grading batch.",
        commands=(_verify_command(context),),
    )


def _peek_brief(path: Path):
    """Return the brief when it exists and loads, without judging its state."""
    if not path.exists() or path.is_symlink():
        return None
    try:
        return load_brief(path)
    except (BriefError, OSError, UnicodeError, ValueError):
        return None


def _calibration_gate(context: WorkflowContext, profile) -> NextAction | None:
    """Refuse to grade with a profile no holdout measured or no instructor approved."""
    status = check_calibration(
        context.root,
        course=context.course,
        profile=profile.path,
        rubric=context.rubric,
    )
    if status.state == "current":
        return None
    if status.state in {"malformed-receipt", "malformed-approval"}:
        return _action(
            context,
            stage="calibration",
            action="repair-calibration",
            message="Repair the calibration receipt or approval before grading.",
            findings=(BlockingFinding(status.state, status.message, ""),),
        )
    if status.state in {"no-receipt", "stale-receipt"}:
        findings = (
            (BlockingFinding(status.state, status.message, ""),)
            if status.state == "stale-receipt"
            else ()
        )
        return _action(
            context,
            stage="calibration",
            action="validate-holdout",
            message=(
                "Measure the profile against its holdouts before grading. Build "
                "the packet with calibrate.py inputs and --holdout-out, prepare a "
                "holdout brief with brief prepare --purpose holdout, grade that "
                "batch through next, then run calibrate.py holdout."
            ),
            findings=findings,
        )
    if status.state == "gap":
        findings = tuple(
            BlockingFinding("holdout-gap", finding, "")
            for finding in (status.receipt.findings if status.receipt else ())
        ) or (BlockingFinding("holdout-gap", status.message, ""),)
        return _action(
            context,
            stage="calibration",
            action="review-holdout",
            message=(
                "Show the instructor the holdout report. They may edit the profile "
                "and run the holdouts again, or accept the gap by running "
                "calibrate.py approve with --accept-gap and --reason in their words."
            ),
            findings=findings,
        )
    command = (
        "python3",
        "scripts/calibrate.py",
        "approve",
        "--course",
        context.course,
        "--rubric",
        str(context.rubric),
        "--approval",
        approval_phrase(context.course),
        "--root",
        str(context.root),
    )
    return _action(
        context,
        stage="calibration",
        action="approve-profile",
        message=(
            "The holdouts passed. Wait for the exact approval phrase, then run "
            "the returned command."
        ),
        commands=(command,),
    )


def _review_gate(
    context: WorkflowContext, brief, batch: GradingBatch, drafted, total: int
) -> NextAction | None:
    """Stop for the instructor's pilot or per-draft review."""
    if brief.review == "none":
        return None
    try:
        review = load_review(
            review_path(context.drafts), batch=context.batch, course=context.course
        )
    except ReviewError as error:
        return _action(
            context,
            stage="review",
            action="repair-review",
            message="Repair the malformed draft review record.",
            findings=(BlockingFinding("malformed-draft-review", str(error), ""),),
        )
    artifacts = batch.draft_artifacts()
    base = (
        "python3",
        "scripts/grade.py",
        "review",
        "approve",
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
        "--patterns",
        str(context.patterns),
        "--root",
        str(context.root),
    )
    drafted_tokens = tuple(entry.token for entry in drafted)
    if review is not None and (review.cadence != brief.review or review.pilot != brief.pilot):
        return _action(
            context,
            stage="review",
            action="repair-review",
            message="The draft review record was made for another cadence or pilot size.",
            findings=(
                BlockingFinding(
                    "malformed-draft-review",
                    "Remove the draft review record and ask the instructor again.",
                    "",
                ),
            ),
        )
    if brief.review == "pilot":
        expected = min(brief.pilot, total)
        approval = review.pilot_approval() if review is not None else None
        command = base + ("--scope", "pilot", "--approval", pilot_phrase(context.batch))
        if approval is None:
            if len(drafted_tokens) < expected:
                return None
            if len(drafted_tokens) == expected:
                return _action(
                    context,
                    stage="review",
                    action="review-pilot",
                    message=(
                        f"Show the instructor these {expected} tokenized pilot "
                        "drafts, then wait for: "
                        f"{pilot_phrase(context.batch)}"
                    ),
                    tokens=drafted_tokens,
                    commands=(command,),
                )
            extra = ", ".join(drafted_tokens[expected:])
            return _action(
                context,
                stage="review",
                action="repair-review",
                message=(
                    "Remove the drafts made past the pilot size, or ask the "
                    "instructor how to proceed."
                ),
                findings=(
                    BlockingFinding(
                        "pilot-overrun",
                        f"Drafts exist beyond the pilot without approval: {extra}",
                        "",
                    ),
                ),
            )
        if len(approval.tokens) != expected:
            return _action(
                context,
                stage="review",
                action="repair-review",
                message="The pilot approval does not match the brief's pilot size.",
                findings=(
                    BlockingFinding(
                        "pilot-size-mismatch",
                        (
                            f"Approved {len(approval.tokens)} drafts; the brief asks "
                            f"for {expected}. Remove the review record and ask again."
                        ),
                        "",
                    ),
                ),
            )
        stale = tuple(
            item.token
            for item in approval.artifacts
            if artifacts.get(item.token) != item
        )
        if stale:
            return _action(
                context,
                stage="review",
                action="review-pilot",
                message=(
                    f"Pilot drafts changed after approval: {', '.join(stale)}. Show "
                    f"them again, then wait for: {pilot_phrase(context.batch)}"
                ),
                tokens=approval.tokens,
                commands=(command,),
                findings=tuple(
                    BlockingFinding(
                        "draft-review-stale", "Changed after approval.", token
                    )
                    for token in stale
                ),
            )
        return None
    unapproved = []
    for token in drafted_tokens:
        approval = review.draft_approval(token) if review is not None else None
        if approval is None:
            unapproved.append(token)
            continue
        bound = next(item for item in approval.artifacts if item.token == token)
        if artifacts.get(token) != bound:
            unapproved.append(token)
    if not unapproved:
        return None
    if len(unapproved) == 1:
        token = unapproved[0]
        return _action(
            context,
            stage="review",
            action="review-draft",
            message=(
                f"Show the instructor the tokenized draft for {token}, then wait "
                f"for: {draft_phrase(token, context.batch)}"
            ),
            token=token,
            tokens=(token,),
            commands=(
                base
                + (
                    "--scope",
                    "draft",
                    "--token",
                    token,
                    "--approval",
                    draft_phrase(token, context.batch),
                ),
            ),
        )
    return _action(
        context,
        stage="review",
        action="repair-review",
        message="Only one draft may wait for approval at a time under per-draft review.",
        findings=(
            BlockingFinding(
                "review-overrun",
                "Unapproved drafts: " + ", ".join(unapproved),
                "",
            ),
        ),
    )


def _current_acknowledgement(context: WorkflowContext, receipt: Path):
    """Return the acknowledgement bound to this exact receipt, or None."""
    try:
        acknowledgement = load_acknowledgement(
            ack_path(context.drafts), batch=context.batch, course=context.course
        )
    except ReviewError:
        return None
    if acknowledgement is None:
        return None
    digest = hashlib.sha256(
        read_bytes_limited(receipt, "The verification receipt", 10 * 1024 * 1024)
    ).hexdigest()
    return acknowledgement if acknowledgement.receipt_sha256 == digest else None


def _record_prepare_command(context: WorkflowContext, source: Path) -> tuple[str, ...]:
    return (
        "python3",
        "scripts/grade.py",
        "record",
        "prepare",
        "--batch",
        context.batch,
        "--course",
        context.course,
        "--rubric",
        str(context.rubric),
        "--drafts",
        str(context.drafts),
        "--patterns",
        str(context.patterns),
        "--root",
        str(context.root),
        "--source",
        str(source),
        "--redacted",
        str(context.redacted),
    )


def _verify_command(context: WorkflowContext) -> tuple[str, ...]:
    return (
        "python3",
        "scripts/grade.py",
        "verify",
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
        "--patterns",
        str(context.patterns),
        "--root",
        str(context.root),
    )


def _consistency_command(context: WorkflowContext) -> tuple[str, ...]:
    return (
        "python3",
        "scripts/grade.py",
        "consistency",
        "--batch",
        context.batch,
        "--course",
        context.course,
        "--redacted",
        str(context.redacted),
        "--drafts",
        str(context.drafts),
        "--root",
        str(context.root),
    )


def _publication_command(
    context: WorkflowContext, profile, current_brief: Path
) -> tuple[str, ...]:
    return (
        "python3",
        "scripts/rehydrate.py",
        str(context.drafts),
        "--batch",
        context.batch,
        "--course",
        context.course,
        "--rubric",
        str(context.rubric),
        "--profile",
        str(profile.path),
        "--redacted",
        str(context.redacted),
        "--brief",
        str(current_brief),
        "--patterns",
        str(context.patterns),
        "--map",
        str(context.root / "maps" / f"{context.batch}.json"),
        "--out",
        str(context.publication),
    )
