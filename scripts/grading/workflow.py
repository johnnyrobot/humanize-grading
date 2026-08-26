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
from scripts.grading.profile import ProfileError, load_profile, profile_path
from scripts.grading.rubric import load_rubric
from scripts.redaction.manifest import RedactionManifest
from scripts.redaction.manifest import NAME as REDACTION_MANIFEST_NAME
from scripts.path_safety import read_bytes_limited, read_text_limited

NEXT_SCHEMA = "grading-next-action/1"


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
            "commands": [list(command) for command in self.commands],
            "findings": [asdict(finding) for finding in self.findings],
        }


def _action(context: WorkflowContext, **values) -> NextAction:
    action = NextAction(**values)
    object.__setattr__(action, "_batch", context.batch)
    object.__setattr__(action, "_course", context.course)
    return action


def _redaction_commands(
    context: WorkflowContext, *, reviewed: bool
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
        command.extend(("--reviewed", "--force"))
    return (tuple(command),)


def _publication_is_current(
    context: WorkflowContext, status, receipt: Path
) -> bool:
    if not context.publication.exists() and not context.publication.is_symlink():
        return False
    if context.publication.is_symlink() or not context.publication.is_dir():
        raise WorkflowError("The publication path must be a regular directory.")
    marker = context.publication / PUBLICATION_NAME
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
    expected = {
        entry.draft.name for entry in status.entries if entry.draft is not None
    }
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        raise WorkflowError("The publication marker has no output list.")
    names: set[str] = set()
    for item in outputs:
        if not isinstance(item, dict):
            raise WorkflowError("The publication marker has a malformed output.")
        name = item.get("path")
        digest = item.get("sha256")
        if (
            not isinstance(name, str)
            or Path(name).name != name
            or name in names
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise WorkflowError("The publication marker has a malformed output.")
        names.add(name)
    if names != expected:
        raise WorkflowError("The publication marker does not name every draft.")
    if any(
        path.is_symlink() or not path.is_file()
        for path in (context.publication / name for name in expected)
    ):
        raise WorkflowError("Every published draft must be one regular file.")
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
    if not current_brief.exists() and not current_brief.is_symlink():
        return _action(
            context,
            stage="briefing",
            action="prepare-brief",
            message="Prepare the assignment brief after choosing local or cloud mode.",
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
    if not manifest.reviewed:
        return _action(
            context,
            stage="redaction",
            action="review-redaction",
            message="Review the private redaction report, then confirm the rerun.",
            commands=_redaction_commands(context, reviewed=True),
        )
    try:
        if manifest.batch != context.batch:
            raise ValueError(
                "The redaction manifest belongs to another grading batch."
            )
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
    status = GradingBatch.open(
        BatchPaths(
            batch=context.batch,
            redacted=context.redacted,
            drafts=context.drafts,
        )
    ).status()
    if status.blocking_findings:
        receipt_codes = {
            "stale-verification-receipt",
            "malformed-verification-receipt",
        }
        if all(
            finding.code in receipt_codes for finding in status.blocking_findings
        ):
            return _action(
                context,
                stage="verification",
                action="repair-verification",
                message="Repair the verification receipt and verify again.",
                findings=status.blocking_findings,
            )
        return _action(
            context,
            stage="drafting",
            action="repair-batch",
            message="Repair the grading batch inventory before continuing.",
            findings=status.blocking_findings,
        )
    pending = next(
        (entry for entry in status.entries if entry.progress is Progress.PENDING),
        None,
    )
    if pending is None:
        inputs = VerificationInputs(
            course=context.course,
            rubric=context.rubric,
            profile=profile.path,
            patterns=context.patterns,
            brief=current_brief,
        )
        receipt = context.drafts / RECEIPT_NAME
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
            consistency = (
                "python3",
                "scripts/grade.py",
                "consistency",
                "--batch",
                context.batch,
                "--redacted",
                str(context.redacted),
                "--drafts",
                str(context.drafts),
                "--root",
                str(context.root),
            )
            publication = (
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
            return _action(
                context,
                stage="publication",
                action="consistency-and-publish",
                message="Review consistency flags, then publish rehydrated drafts.",
                commands=(consistency, publication),
            )
        verification = GradingBatch.open(
            BatchPaths(
                batch=context.batch,
                redacted=context.redacted,
                drafts=context.drafts,
            )
        ).verify(
            inputs,
            persist=False,
        )
        if any(
            finding.code in {"template-marker", "nothing-assessed"}
            for finding in verification.blocking_findings
        ):
            token = next(
                (
                    finding.token
                    for finding in verification.blocking_findings
                    if finding.token
                    and finding.code in {"template-marker", "nothing-assessed"}
                ),
                "",
            )
            return _action(
                context,
                stage="drafting",
                action="complete-draft",
                message=f"Complete the prepared draft pair for {token}.",
                token=token,
                findings=verification.blocking_findings,
            )
        if verification.blocking_findings:
            return _action(
                context,
                stage="verification",
                action="repair-verification",
                message="Repair the verification findings before continuing.",
                findings=verification.blocking_findings,
            )
        command = (
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
        return _action(
            context,
            stage="verification",
            action="verify",
            message="Persist verification for the clean grading batch.",
            commands=(command,),
        )
    if pending.source is None:
        raise ValueError("The pending submission has no redacted source.")
    command = (
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
        str(pending.source),
        "--redacted",
        str(context.redacted),
    )
    return _action(
        context,
        stage="drafting",
        action="prepare-record",
        message=f"Prepare the draft pair for {pending.token}.",
        token=pending.token,
        commands=(command,),
    )
