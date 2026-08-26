"""Say what a grading batch still owes, so interrupted work resumes safely.

Regrading a submission that already has a draft is not merely wasted work. It
produces a second, different grade for the same student, and nothing in the
output says which one the instructor should send.

A draft or grade record with no redacted submission is orphaned and reported.
It usually means an earlier grading batch's output was left in the drafts directory, and
grading on top of it would mix two batches.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Protocol

from scripts.grading.consistency import Outlier, review
from scripts.grading.density import PATTERNS_PATH, load_terms
from scripts.grading.profile import ProfileError, load_profile
from scripts.grading.records import GradeRecord, RecordError
from scripts.grading.rubric import RubricError, load_rubric
from scripts.grading.verify import ERROR, verify_draft
from scripts.redaction.manifest import NAME as REDACTION_MANIFEST_NAME
from scripts.redaction.manifest import RedactionManifest
from scripts.path_safety import (
    has_symlink_component,
    read_bytes_limited,
    read_text_limited,
    require_component,
)


class Progress(str, Enum):
    """How far one redacted submission has moved through grading."""

    PENDING = "pending"
    DRAFTED = "drafted"
    VERIFIED = "verified"
    REHYDRATED = "rehydrated"


RECEIPT_NAME = "_verification.json"
RECEIPT_SCHEMA = "grading-batch-verification/1"
PUBLICATION_NAME = "_publication.json"
PUBLICATION_SCHEMA = "grading-batch-publication/1"
TRANSACTION_NAME = "_transaction.json"
TRANSACTION_SCHEMA = "grading-batch-publication-transaction/1"
_PARTICIPANT_ROLES = {"draft", "grade-record", "redacted-submission"}
_COURSE_EVIDENCE_ROLES = {"calibration-profile", "pattern-reference", "rubric"}
_BATCH_EVIDENCE_ROLES = {"redaction-manifest"}
_RECEIPT_ROLES = (
    _PARTICIPANT_ROLES | _COURSE_EVIDENCE_ROLES | _BATCH_EVIDENCE_ROLES
)

_MALFORMED_CODES = {
    "malformed-record",
    "unreadable-source",
    "unreadable-draft",
    "no-records",
    "malformed-profile",
    "malformed-rubric",
    "malformed-patterns",
    "malformed-redaction-manifest",
}
_RECORD_LOAD_ERRORS = (
    RecordError,
    OSError,
    UnicodeError,
    AttributeError,
    TypeError,
    KeyError,
    ValueError,
)
_VERIFIED_BATCH_KEY = object()


@dataclass(frozen=True)
class BatchPaths:
    """The paths that identify one grading batch."""

    batch: str
    redacted: Path
    drafts: Path


@dataclass(frozen=True)
class BatchEntry:
    """One token's artifacts and current grading progress."""

    token: str
    progress: Progress | None
    source: Path | None
    draft: Path | None
    record: Path | None


@dataclass(frozen=True)
class BlockingFinding:
    """A failed requirement that stops a grading batch from advancing."""

    code: str
    message: str
    token: str


@dataclass(frozen=True)
class StatusReport:
    """Current progress and blocking findings for a grading batch."""

    entries: tuple[BatchEntry, ...]
    blocking_findings: tuple[BlockingFinding, ...]

    def display_state(self, entry: BatchEntry) -> str:
        """Return one concise state for status output."""
        codes = {
            finding.code
            for finding in self.blocking_findings
            if finding.token == entry.token
        }
        if "orphaned-artifact" in codes:
            return "orphan"
        if "incomplete-pair" in codes:
            return "incomplete"
        if entry.progress is not None:
            return entry.progress.value
        return "orphan"

    def counts(self) -> dict[str, int]:
        """Count entries by their status display state."""
        counts = {
            "pending": 0,
            "drafted": 0,
            "verified": 0,
            "rehydrated": 0,
            "incomplete": 0,
            "orphan": 0,
        }
        for entry in self.entries:
            counts[self.display_state(entry)] += 1
        return counts


@dataclass(frozen=True)
class VerificationInputs:
    """The current course evidence used to verify a grading batch."""

    course: str
    rubric: Path
    profile: Path
    patterns: Path


class VerificationFailure(str, Enum):
    """The existing command failure meanings for whole-batch verification."""

    NO_PROFILE = "no-profile"
    RUBRIC_DRIFT = "rubric-drift"
    FAILED = "failed"
    MALFORMED = "malformed"


@dataclass(frozen=True)
class VerifiedBatchResume:
    """Result of revalidating a receipt for publication."""

    verified_batch: "VerifiedBatch | None"
    findings: tuple[BlockingFinding, ...]


@dataclass(frozen=True)
class RenderResult:
    """Identity-free result from rendering one rehydrated draft."""

    sha256: str = ""
    unmapped_tokens: tuple[str, ...] = ()
    error: str = ""


class PublicationInspector(Protocol):
    """Check a publication without loading or returning identity-bearing text."""

    batch: str
    run_id: str

    def hash_output(self, path: Path) -> str: ...


class RehydrationRenderer(PublicationInspector, Protocol):
    """Write identity-bearing output without returning its text."""

    def render(
        self, source: Path, destination: Path, expected_sha256: str
    ) -> RenderResult: ...

@dataclass(frozen=True)
class PublicationReport:
    """Result of attempting to publish one verified grading batch."""

    destination: Path
    progress: Progress
    findings: tuple[BlockingFinding, ...]
    published: tuple[Path, ...] = ()
    changed: bool = False


class VerifiedBatch:
    """A grading batch whose current verification receipt has been trusted."""

    __slots__ = (
        "_paths",
        "_receipt_digest",
        "_redaction_run_id",
        "_draft_digests",
    )

    def __init__(
        self,
        paths: BatchPaths,
        receipt_digest: str,
        key: object,
        redaction_run_id: str = "",
        draft_digests: dict[str, str] | None = None,
    ) -> None:
        if key is not _VERIFIED_BATCH_KEY:
            raise TypeError(
                "VerifiedBatch comes only from persisted verification or receipt "
                "revalidation."
            )
        self._paths = paths
        self._receipt_digest = receipt_digest
        self._redaction_run_id = redaction_run_id
        self._draft_digests = dict(draft_digests or {})

    @classmethod
    def _create(cls, paths: BatchPaths, receipt: Path) -> "VerifiedBatch":
        digest = _receipt_digest(receipt)
        manifest = RedactionManifest.load(paths.redacted)
        decoded = _decode_receipt(receipt)
        draft_digests = {
            Path(item["path"]).name: item["sha256"]
            for item in decoded.artifacts
            if isinstance(item, dict)
            and item.get("role") == "draft"
            and isinstance(item.get("path"), str)
            and isinstance(item.get("sha256"), str)
        }
        return cls(
            paths,
            digest,
            _VERIFIED_BATCH_KEY,
            redaction_run_id=manifest.run_id,
            draft_digests=draft_digests,
        )

    @classmethod
    def resume(
        cls, paths: BatchPaths, inputs: VerificationInputs
    ) -> VerifiedBatchResume:
        receipt = paths.drafts / RECEIPT_NAME
        status = GradingBatch.open(paths).status(require_verified=True)
        if status.blocking_findings:
            return VerifiedBatchResume(
                verified_batch=None,
                findings=tuple(
                    replace(
                        finding,
                        message=(
                            finding.message
                            + " Run verification again before publication."
                        ),
                    )
                    for finding in status.blocking_findings
                ),
            )
        semantic = GradingBatch.open(paths).verify(inputs, persist=False)
        if semantic.blocking_findings:
            return VerifiedBatchResume(
                verified_batch=None,
                findings=tuple(
                    replace(
                        finding,
                        message=_with_verification_instruction(finding.message),
                    )
                    for finding in semantic.blocking_findings
                ),
            )
        return VerifiedBatchResume(
            verified_batch=cls._create(paths, receipt),
            findings=(),
        )

    @classmethod
    def context_from_receipt(
        cls, drafts: Path
    ) -> tuple[BatchPaths, VerificationInputs]:
        """Recover the legacy rehydration context, then rerun verification."""
        return _context_from_receipt(drafts)

    def publish(
        self,
        target: Path,
        renderer: RehydrationRenderer,
        *,
        force: bool = False,
    ) -> PublicationReport:
        receipt = self._paths.drafts / RECEIPT_NAME
        status = GradingBatch.open(self._paths).status(require_verified=True)
        current_digest = (
            _receipt_digest(receipt)
            if receipt.is_file()
            else ""
        )
        findings = [
            replace(
                finding,
                message=_with_verification_instruction(finding.message),
            )
            for finding in status.blocking_findings
        ]
        if current_digest != self._receipt_digest:
            findings.append(
                BlockingFinding(
                    code="stale-verified-batch",
                    message=(
                        "The verification receipt changed after this verified batch "
                        "was created. Run verification again before publication."
                    ),
                    token="",
                )
            )
        if renderer.batch != self._paths.batch:
            findings.append(
                BlockingFinding(
                    code="renderer-batch-mismatch",
                    message="The renderer belongs to a different grading batch.",
                    token="",
                )
            )
        if renderer.run_id != self._redaction_run_id:
            findings.append(
                BlockingFinding(
                    code="renderer-redaction-mismatch",
                    message=(
                        "The token map belongs to a different redaction result. "
                        "Use the map created with this grading batch."
                    ),
                    token="",
                )
            )
        if findings:
            return PublicationReport(
                destination=target,
                progress=Progress.VERIFIED,
                findings=tuple(findings),
            )
        if force:
            recovery_findings = _recover_publication_transaction(
                target,
                renderer,
                self._paths.batch,
                current_digest,
                status,
            )
            if recovery_findings:
                return PublicationReport(
                    destination=target,
                    progress=Progress.VERIFIED,
                    findings=recovery_findings,
                )
        target_has_content = target.exists() and (
            not target.is_dir() or any(target.iterdir())
        )
        if target_has_content:
            if force:
                findings.extend(
                    _validate_existing_publication(
                        target,
                        renderer,
                        self._paths.batch,
                        current_digest,
                        status,
                    )
                )
            else:
                findings.append(
                    BlockingFinding(
                        code="destination-not-empty",
                        message=(
                            "The publication destination is not empty. Pass --force "
                            "only for an unchanged publication from this grading batch."
                        ),
                        token="",
                    )
                )
        if findings:
            return PublicationReport(
                destination=target,
                progress=Progress.VERIFIED,
                findings=tuple(findings),
            )

        staging: Path | None = None
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            staging = Path(
                tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent)
            )
            (staging / TRANSACTION_NAME).write_text(
                json.dumps(
                    {
                        "schema": TRANSACTION_SCHEMA,
                        "batch": self._paths.batch,
                        "receipt_sha256": current_digest,
                        "target": target.name,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
        except OSError as error:
            if staging is not None and staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            return PublicationReport(
                destination=target,
                progress=Progress.VERIFIED,
                findings=(
                    _publication_finding(
                        "publication-failed",
                        f"Could not prepare publication staging: {error}",
                    ),
                ),
            )
        outputs: list[dict[str, str]] = []
        try:
            for entry in status.entries:
                if entry.draft is None:
                    continue
                destination = staging / entry.draft.name
                expected_digest = self._draft_digests.get(entry.draft.name, "")
                result = renderer.render(
                    entry.draft, destination, expected_digest
                )
                if result.error:
                    findings.append(
                        BlockingFinding(
                            code="render-failed",
                            message=result.error,
                            token=entry.token,
                        )
                    )
                for token in result.unmapped_tokens:
                    findings.append(
                        BlockingFinding(
                            code="unmapped-token",
                            message=f"No identity mapping exists for {token}.",
                            token=entry.token,
                        )
                    )
                if not result.error and (
                    not destination.is_file() or len(result.sha256) != 64
                ):
                    findings.append(
                        BlockingFinding(
                            code="render-failed",
                            message="The renderer did not write a verifiable draft.",
                            token=entry.token,
                        )
                    )
                elif not result.error:
                    outputs.append(
                        {"path": destination.name, "sha256": result.sha256}
                    )
            if findings:
                return PublicationReport(
                    destination=target,
                    progress=Progress.VERIFIED,
                    findings=tuple(findings),
                )
            outputs.sort(key=lambda item: item["path"])
            (staging / PUBLICATION_NAME).write_text(
                json.dumps(
                    {
                        "schema": PUBLICATION_SCHEMA,
                        "batch": self._paths.batch,
                        "receipt_sha256": current_digest,
                        "outputs": outputs,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if target_has_content:
                backup = _publication_backup(target)
                try:
                    target.replace(backup)
                except OSError as error:
                    return PublicationReport(
                        destination=target,
                        progress=Progress.VERIFIED,
                        findings=(
                            _publication_finding(
                                "publication-failed",
                                f"Could not back up the publication: {error}",
                            ),
                        ),
                    )
                try:
                    staging.replace(target)
                except OSError as error:
                    try:
                        backup.replace(target)
                    except OSError as restore_error:
                        return PublicationReport(
                            destination=target,
                            progress=Progress.VERIFIED,
                            findings=(
                                _publication_finding(
                                    "publication-failed",
                                    "Could not install or restore the publication: "
                                    f"{error}; {restore_error}",
                                ),
                            ),
                        )
                    return PublicationReport(
                        destination=target,
                        progress=Progress.VERIFIED,
                        findings=(
                            BlockingFinding(
                                code="publication-failed",
                                message=f"Could not replace the publication: {error}",
                                token="",
                            ),
                        ),
                        )
                try:
                    (target / TRANSACTION_NAME).unlink()
                except OSError:
                    pass
                try:
                    shutil.rmtree(backup)
                except OSError:
                    pass
            else:
                if target.is_dir():
                    target.rmdir()
                staging.replace(target)
                try:
                    (target / TRANSACTION_NAME).unlink()
                except OSError:
                    pass
            return PublicationReport(
                destination=target,
                progress=Progress.REHYDRATED,
                findings=(),
                published=tuple(target / item["path"] for item in outputs),
                changed=True,
            )
        finally:
            if staging is not None and staging.exists():
                shutil.rmtree(staging)


@dataclass(frozen=True)
class AdvisoryFinding:
    """A verification finding that asks for review but does not block."""

    code: str
    message: str
    token: str
    criterion: int | None = None


@dataclass(frozen=True)
class VerificationReport:
    """Whole-batch verification findings and persistence result."""

    status: StatusReport
    blocking_findings: tuple[BlockingFinding, ...]
    warnings: tuple[AdvisoryFinding, ...]
    checked_tokens: tuple[str, ...]
    persisted: bool
    failure: VerificationFailure | None
    receipt: Path | None = None
    verified_batch: VerifiedBatch | None = None


@dataclass(frozen=True)
class ConsistencyReport:
    """Structural findings and advisory flags for a grading batch."""

    status: StatusReport
    blocking_findings: tuple[BlockingFinding, ...]
    flags: tuple[Outlier, ...]
    records_count: int


@dataclass(frozen=True)
class _LoadedEntry:
    entry: BatchEntry
    record: GradeRecord | None
    source_text: str | None
    draft_text: str | None
    findings: tuple[BlockingFinding, ...]


@dataclass(frozen=True)
class _ReceiptValidation:
    present: bool
    valid: bool
    findings: tuple[BlockingFinding, ...]


@dataclass(frozen=True)
class _DecodedReceipt:
    batch: str
    course: str
    artifacts: tuple[object, ...]


@dataclass(frozen=True)
class GradingBatch:
    """Own the lifecycle rules for one grading batch."""

    paths: BatchPaths

    @classmethod
    def open(cls, paths: BatchPaths) -> "GradingBatch":
        return cls(paths)

    def status(
        self,
        *,
        require_verified: bool = False,
        publication: Path | None = None,
        renderer: PublicationInspector | None = None,
    ) -> StatusReport:
        report = self._status(
            check_receipt=True,
            require_verified=require_verified or publication is not None,
        )
        if publication is None or report.blocking_findings:
            return report
        if renderer is None:
            return StatusReport(
                entries=report.entries,
                blocking_findings=(
                    BlockingFinding(
                        code="publication-renderer-required",
                        message=(
                            "Rehydrated status needs the local renderer to check "
                            "identity-bearing output hashes."
                        ),
                        token="",
                    ),
                ),
            )
        receipt = self.paths.drafts / RECEIPT_NAME
        digest = _receipt_digest(receipt)
        findings = _validate_existing_publication(
            publication,
            renderer,
            self.paths.batch,
            digest,
            report,
        )
        if findings:
            return StatusReport(
                entries=report.entries,
                blocking_findings=findings,
            )
        return StatusReport(
            entries=tuple(
                replace(entry, progress=Progress.REHYDRATED)
                for entry in report.entries
            ),
            blocking_findings=(),
        )

    def _status(
        self,
        *,
        check_receipt: bool,
        require_verified: bool = False,
        require_manifest: bool = False,
    ) -> StatusReport:
        sources, source_links = _scan_artifacts(
            self.paths.redacted, (".md", ".txt", ".docx")
        )
        drafts, draft_links = _scan_artifacts(self.paths.drafts, (".md",))
        records, record_links = _scan_artifacts(self.paths.drafts, (".json",))
        entries: list[BatchEntry] = []
        findings: list[BlockingFinding] = []
        if require_manifest:
            try:
                manifest = RedactionManifest.load(self.paths.redacted)
                if manifest.batch != self.paths.batch:
                    raise ValueError(
                        "The redaction manifest belongs to another grading batch."
                    )
                manifest.validate(self.paths.redacted)
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
                findings.append(
                    BlockingFinding(
                        code="malformed-redaction-manifest",
                        message=str(error),
                        token="",
                    )
                )
        for token in sorted(
            set(sources)
            | set(drafts)
            | set(records)
            | set(source_links)
            | set(draft_links)
            | set(record_links)
        ):
            source_paths = sources.get(token, ())
            draft_paths = drafts.get(token, ())
            record_paths = records.get(token, ())
            source = source_paths[0] if len(source_paths) == 1 else None
            draft = draft_paths[0] if len(draft_paths) == 1 else None
            record = record_paths[0] if len(record_paths) == 1 else None
            if not source_paths:
                progress = None
            elif draft is not None and record is not None:
                progress = Progress.DRAFTED
            else:
                progress = Progress.PENDING
            entries.append(
                BatchEntry(
                    token=token,
                    progress=progress,
                    source=source,
                    draft=draft,
                    record=record,
                )
            )
            linked = (
                source_links.get(token, ())
                + draft_links.get(token, ())
                + record_links.get(token, ())
            )
            if linked:
                findings.append(
                    BlockingFinding(
                        code="symlink-artifact",
                        message=(
                            "Symbolic links are not grading-batch artifacts: "
                            + ", ".join(path.name for path in linked)
                        ),
                        token=token,
                    )
                )
                continue
            if not source_paths:
                findings.append(
                    BlockingFinding(
                        code="orphaned-artifact",
                        message=(
                            "Draft feedback or a grade record has no redacted "
                            "submission in this grading batch."
                        ),
                        token=token,
                    )
                )
            else:
                if len(source_paths) > 1:
                    findings.append(
                        BlockingFinding(
                            code="duplicate-redacted-submission",
                            message=(
                                "More than one redacted submission uses this token. "
                                "Keep exactly one before grading."
                            ),
                            token=token,
                        )
                    )
                if (draft is None) != (record is None):
                    findings.append(
                        BlockingFinding(
                            code="incomplete-pair",
                            message=(
                                "The submission needs both draft feedback and a grade "
                                "record before it is drafted."
                            ),
                            token=token,
                        )
                    )
        report = StatusReport(
            entries=tuple(entries),
            blocking_findings=tuple(findings),
        )
        if not check_receipt:
            return report
        validation = _validate_verification_receipt(self.paths, report)
        findings.extend(validation.findings)
        if require_verified and not validation.present:
            findings.append(
                BlockingFinding(
                    code="verification-required",
                    message=(
                        "This grading batch has no verification receipt. Run "
                        "verification before publication."
                    ),
                    token="",
                )
            )
        if validation.valid:
            entries = [replace(entry, progress=Progress.VERIFIED) for entry in entries]
        return StatusReport(
            entries=tuple(entries),
            blocking_findings=tuple(findings),
        )

    def verify(
        self, inputs: VerificationInputs, *, persist: bool = True
    ) -> VerificationReport:
        status = self._status(check_receipt=False, require_manifest=True)
        blocking = list(status.blocking_findings)
        try:
            initial_digests = _verification_digests(self.paths, inputs, status)
        except OSError:
            initial_digests = {}
        if not any(entry.record is not None for entry in status.entries):
            blocking.append(
                BlockingFinding(
                    code="no-records",
                    message=(
                        "The grading batch has no grade records. Nothing can be "
                        "verified."
                    ),
                    token="",
                )
            )
        loaded_entries = tuple(
            _load_entry(entry, self.paths, inputs) for entry in status.entries
        )
        for loaded in loaded_entries:
            blocking.extend(loaded.findings)
        profile = None
        rubric = None
        terms = None
        try:
            profile = load_profile(inputs.profile)
        except ProfileError as error:
            blocking.append(
                BlockingFinding(
                    code="no-profile",
                    message=str(error),
                    token="",
                )
            )
        except (OSError, UnicodeError, ValueError, TypeError) as error:
            blocking.append(
                BlockingFinding(
                    code="malformed-profile",
                    message=str(error),
                    token="",
                )
            )
        try:
            rubric = load_rubric(inputs.rubric)
        except (RubricError, OSError, UnicodeError) as error:
            blocking.append(
                BlockingFinding(
                    code="malformed-rubric",
                    message=str(error),
                    token="",
                )
            )
        if profile is not None and profile.course != inputs.course:
            blocking.append(
                BlockingFinding(
                    code="profile-course-mismatch",
                    message=(
                        f"The profile belongs to {profile.course!r}, not "
                        f"{inputs.course!r}."
                    ),
                    token="",
                )
            )
        drifted = (
            profile.drift(rubric)
            if profile is not None and rubric is not None
            else []
        )
        if drifted:
            blocking.append(
                BlockingFinding(
                    code="rubric-drift",
                    message=(
                        "The rubric changed since calibration: " + ", ".join(drifted)
                    ),
                    token="",
                )
            )
        try:
            terms = load_terms(inputs.patterns)
        except (OSError, UnicodeError, ValueError) as error:
            blocking.append(
                BlockingFinding(
                    code="malformed-patterns",
                    message=str(error),
                    token="",
                )
            )
        if profile is None or rubric is None or terms is None or drifted:
            codes = {finding.code for finding in blocking}
            failure = (
                VerificationFailure.NO_PROFILE
                if "no-profile" in codes
                else (
                    VerificationFailure.RUBRIC_DRIFT
                    if "rubric-drift" in codes
                    else VerificationFailure.MALFORMED
                )
            )
            return VerificationReport(
                status=status,
                blocking_findings=tuple(blocking),
                warnings=(),
                checked_tokens=(),
                persisted=False,
                failure=failure,
            )
        warnings: list[AdvisoryFinding] = []
        checked: list[str] = []
        records: list[tuple[Path, GradeRecord]] = []

        for loaded in loaded_entries:
            entry = loaded.entry
            record = loaded.record
            if (
                entry.source is None
                or entry.draft is None
                or entry.record is None
                or record is None
                or loaded.source_text is None
                or loaded.draft_text is None
                or any(
                    finding.code in _MALFORMED_CODES
                    for finding in loaded.findings
                )
            ):
                continue
            try:
                findings = verify_draft(
                    record,
                    loaded.draft_text,
                    loaded.source_text,
                    rubric,
                    profile,
                    terms,
                )
            except (AttributeError, TypeError, KeyError, ValueError) as error:
                blocking.append(
                    BlockingFinding(
                        code="malformed-record",
                        message=f"Cannot verify {entry.record.name}: {error}",
                        token=entry.token,
                    )
                )
                continue
            checked.append(entry.token)
            records.append((entry.record, record))
            for finding in findings:
                if finding.level == ERROR:
                    blocking.append(
                        BlockingFinding(
                            code=finding.code,
                            message=finding.message,
                            token=finding.token,
                        )
                    )
                else:
                    warnings.append(
                        AdvisoryFinding(
                            code=finding.code,
                            message=finding.message,
                            token=finding.token,
                            criterion=finding.criterion,
                        )
                    )

        receipt = None
        if persist and not blocking:
            try:
                current = self._status(check_receipt=False)
                current_digests = _verification_digests(
                    self.paths, inputs, current
                )
            except OSError as error:
                blocking.append(
                    BlockingFinding(
                        code="batch-changed",
                        message=f"The grading batch changed during verification: {error}",
                        token="",
                    )
                )
                current_digests = {}
            if not blocking and current_digests != initial_digests:
                blocking.append(
                    BlockingFinding(
                        code="batch-changed",
                        message=(
                            "The grading batch changed during verification. Run "
                            "verification again on the current files."
                        ),
                        token="",
                    )
                )
            staged: list[tuple[Path, Path, Path]] = []
            preserved_backups: set[Path] = set()
            try:
                if not blocking:
                    for path, record in records:
                        temporary = _stage_record(record, path.parent)
                        backup = _backup_file(path)
                        staged.append((path, temporary, backup))
                    latest = self._status(check_receipt=False)
                    latest_digests = _verification_digests(
                        self.paths, inputs, latest
                    )
                    if latest_digests != initial_digests:
                        blocking.append(
                            BlockingFinding(
                                code="batch-changed",
                                message=(
                                    "The grading batch changed while verified records "
                                    "were staged. Nothing was committed."
                                ),
                                token="",
                            )
                        )
                if not blocking:
                    receipt_digests = dict(initial_digests)
                    for path, temporary, _ in staged:
                        receipt_digests[path.resolve()] = hashlib.sha256(
                            read_bytes_limited(
                                temporary, "The staged grade record", 1_048_576
                            )
                        ).hexdigest()
                    installed: list[tuple[Path, Path]] = []
                    try:
                        for path, temporary, backup in staged:
                            temporary.replace(path)
                            installed.append((path, backup))
                        receipt = _write_verification_receipt(
                            self.paths, inputs, status, receipt_digests
                        )
                    except OSError as error:
                        restore_errors: list[str] = []
                        for path, backup in reversed(installed):
                            try:
                                backup.replace(path)
                            except OSError as restore_error:
                                preserved_backups.add(backup)
                                restore_errors.append(
                                    f"{path.name} remains at {backup.name}: "
                                    f"{restore_error}"
                                )
                        detail = f"Could not commit verified records: {error}"
                        if restore_errors:
                            detail += "; restore failed: " + "; ".join(restore_errors)
                        blocking.append(
                            BlockingFinding(
                                code="record-commit-failed",
                                message=detail,
                                token="",
                            )
                        )
                        receipt = None
            except OSError as error:
                blocking.append(
                    BlockingFinding(
                        code="record-commit-failed",
                        message=f"Could not stage verified records: {error}",
                        token="",
                    )
                )
                receipt = None
            finally:
                for _, temporary, backup in staged:
                    for transient in (temporary, backup):
                        if transient.exists() and transient not in preserved_backups:
                            transient.unlink()
        failure = None
        if blocking:
            failure = (
                VerificationFailure.MALFORMED
                if any(finding.code in _MALFORMED_CODES for finding in blocking)
                else VerificationFailure.FAILED
            )
        return VerificationReport(
            status=status,
            blocking_findings=tuple(blocking),
            warnings=tuple(warnings),
            checked_tokens=tuple(checked),
            persisted=persist and not blocking,
            failure=failure,
            receipt=receipt,
            verified_batch=(
                VerifiedBatch._create(self.paths, receipt)
                if receipt is not None
                else None
            ),
        )

    def consistency(self, *, threshold: float = 2.0) -> ConsistencyReport:
        status = self._status(check_receipt=False)
        blocking = list(status.blocking_findings)
        records: list[GradeRecord] = []
        drafts: dict[str, str] = {}
        for entry in status.entries:
            loaded = _load_entry(entry, self.paths)
            blocking.extend(loaded.findings)
            if (
                entry.source is None
                or entry.draft is None
                or entry.record is None
                or loaded.record is None
                or loaded.draft_text is None
                or loaded.findings
            ):
                continue
            records.append(loaded.record)
            drafts[entry.token] = loaded.draft_text
        if not records:
            blocking.append(
                BlockingFinding(
                    code="no-records",
                    message=(
                        "The grading batch has no readable grade records. Nothing "
                        "can be compared."
                    ),
                    token="",
                )
            )
        return ConsistencyReport(
            status=status,
            blocking_findings=tuple(blocking),
            flags=(
                tuple(review(records, drafts, threshold=threshold)) if records else ()
            ),
            records_count=len(records),
        )


def _scan_artifacts(
    directory: Path, suffixes: tuple[str, ...]
) -> tuple[dict[str, tuple[Path, ...]], dict[str, tuple[Path, ...]]]:
    if not directory.is_dir():
        return {}, {}
    regular: dict[str, list[Path]] = {}
    links: dict[str, list[Path]] = {}
    for path in sorted(directory.iterdir()):
        if (
            path.suffix.lower() in suffixes
            and not path.name.startswith("_")
        ):
            if path.is_symlink():
                links.setdefault(path.stem, []).append(path)
            elif path.is_file():
                regular.setdefault(path.stem, []).append(path)
    return (
        {token: tuple(paths) for token, paths in regular.items()},
        {token: tuple(paths) for token, paths in links.items()},
    )


def _verification_digests(
    paths: BatchPaths,
    inputs: VerificationInputs,
    status: StatusReport,
) -> dict[Path, str]:
    """Snapshot every byte sequence that verification and its receipt bind."""
    artifacts: list[tuple[Path, str, int]] = []
    for entry in status.entries:
        for role, path in _participant_paths(entry):
            if path is None:
                continue
            limit = 25 * 1024 * 1024 if role == "redacted-submission" else 1_048_576
            artifacts.append((path, role, limit))
    artifacts.extend(
        [
            (paths.redacted / REDACTION_MANIFEST_NAME, "redaction manifest", 1_048_576),
            (inputs.rubric, "rubric", 5 * 1024 * 1024),
            (inputs.profile, "calibration profile", 5 * 1024 * 1024),
            (inputs.patterns, "pattern reference", 5 * 1024 * 1024),
        ]
    )
    return {
        path.resolve(): hashlib.sha256(
            read_bytes_limited(path, f"The {label}", limit)
        ).hexdigest()
        for path, label, limit in artifacts
    }


def _write_verification_receipt(
    paths: BatchPaths,
    inputs: VerificationInputs,
    status: StatusReport,
    digests: dict[Path, str],
) -> Path:
    receipt = paths.drafts / RECEIPT_NAME
    base = receipt.parent.resolve()
    artifacts: list[dict[str, str]] = []
    for entry in status.entries:
        for role, path in _participant_paths(entry):
            if path is not None:
                artifacts.append(
                    _receipt_artifact(
                        role, path, base, digests[path.resolve()], entry.token
                    )
                )
    for role, path in (
        ("redaction-manifest", paths.redacted / REDACTION_MANIFEST_NAME),
        ("rubric", inputs.rubric),
        ("calibration-profile", inputs.profile),
        ("pattern-reference", inputs.patterns),
    ):
        artifacts.append(
            _receipt_artifact(role, path, base, digests[path.resolve()])
        )
    artifacts.sort(key=lambda item: (item["role"], item.get("token", ""), item["path"]))
    payload = {
        "schema": RECEIPT_SCHEMA,
        "batch": paths.batch,
        "course": inputs.course,
        "hash": "sha256",
        "artifacts": artifacts,
    }
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{receipt.name}.", suffix=".tmp", dir=receipt.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(receipt)
    finally:
        if temporary.exists():
            temporary.unlink()
    return receipt


def _stage_record(record: GradeRecord, directory: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".verified-record.", suffix=".tmp", dir=directory
    )
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(record.as_dict(), indent=2, ensure_ascii=False))
        handle.flush()
        os.fsync(handle.fileno())
    return Path(name)


def _backup_file(path: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".record-backup.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(read_bytes_limited(path, "The grade record", 1_048_576))
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _receipt_artifact(
    role: str,
    path: Path,
    base: Path,
    digest: str,
    token: str | None = None,
) -> dict[str, str]:
    resolved = path.expanduser().resolve()
    artifact = {
        "role": role,
        "path": _receipt_locator(resolved, base),
        "sha256": digest,
    }
    if token is not None:
        artifact["token"] = token
    return artifact


def _receipt_locator(path: Path, base: Path) -> str:
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path.as_posix()


def _receipt_digest(receipt: Path) -> str:
    return hashlib.sha256(
        read_bytes_limited(
            receipt, "The verification receipt", 10 * 1024 * 1024
        )
    ).hexdigest()


def _context_from_receipt(
    drafts: Path,
) -> tuple[BatchPaths, VerificationInputs]:
    receipt = drafts / RECEIPT_NAME
    decoded = _decode_receipt(receipt)
    require_component(decoded.batch, "The grading batch")
    require_component(decoded.course, "The course")
    redacted = Path("redacted").resolve()
    profile = (Path(".grading") / "profiles" / f"{decoded.course}.md").resolve()
    rubric = Path("grading_rubric.md").resolve()
    return (
        BatchPaths(
            batch=decoded.batch,
            redacted=redacted,
            drafts=drafts.resolve(),
        ),
        VerificationInputs(
            course=decoded.course,
            rubric=rubric,
            profile=profile,
            patterns=PATTERNS_PATH,
        ),
    )


def _decode_receipt(receipt: Path) -> _DecodedReceipt:
    payload = json.loads(
        read_text_limited(receipt, "The verification receipt", 10 * 1024 * 1024)
    )
    if not isinstance(payload, dict):
        raise ValueError("The verification receipt must be a JSON object.")
    if payload.get("schema") != RECEIPT_SCHEMA:
        raise ValueError(
            f"Unknown verification receipt schema {payload.get('schema')!r}."
        )
    batch = payload.get("batch")
    course = payload.get("course")
    artifacts = payload.get("artifacts")
    if not isinstance(batch, str) or not batch:
        raise ValueError("The verification receipt needs a grading batch.")
    if not isinstance(course, str) or not course:
        raise ValueError("The verification receipt needs a course.")
    if payload.get("hash") != "sha256":
        raise ValueError("The verification receipt must use SHA-256.")
    if not isinstance(artifacts, list):
        raise ValueError("The verification receipt artifacts must be a list.")
    return _DecodedReceipt(batch=batch, course=course, artifacts=tuple(artifacts))


def _publication_backup(target: Path) -> Path:
    return target.with_name(f".{target.name}.backup")


def _recover_publication_transaction(
    target: Path,
    renderer: RehydrationRenderer,
    batch: str,
    receipt_digest: str,
    status: StatusReport,
) -> tuple[BlockingFinding, ...]:
    backup = _publication_backup(target)
    target_transaction = target / TRANSACTION_NAME
    if target_transaction.is_file():
        if not _transaction_matches(
            target_transaction, target, batch, receipt_digest
        ):
            return (
                _publication_finding(
                    "publication-recovery-required",
                    "The installed publication has an unrecognized transaction marker.",
                ),
            )
        target_findings = _validate_existing_publication(
            target,
            renderer,
            batch,
            receipt_digest,
            status,
            allow_transaction=True,
        )
        if target_findings:
            return (
                _publication_finding(
                    "publication-recovery-required",
                    "The installed publication cannot be recovered automatically.",
                ),
            )
        try:
            target_transaction.unlink()
        except OSError as error:
            return (
                _publication_finding(
                    "publication-recovery-required",
                    f"Could not finish the publication transaction: {error}",
                ),
            )
    if backup.exists():
        if not backup.is_dir():
            return (
                _publication_finding(
                    "publication-recovery-required",
                    "The publication backup is not a directory. Move it manually.",
                ),
            )
        backup_findings = _validate_existing_publication(
            backup, renderer, batch, receipt_digest, status
        )
        if backup_findings:
            return (
                _publication_finding(
                    "publication-recovery-required",
                    "The publication backup is not recognized. Move it manually.",
                ),
            )
        if target.exists():
            target_findings = _validate_existing_publication(
                target, renderer, batch, receipt_digest, status
            )
            if target_findings:
                return (
                    _publication_finding(
                        "publication-recovery-required",
                        "Both publication and backup exist. Move one manually.",
                    ),
                )
            try:
                shutil.rmtree(backup)
            except OSError as error:
                return (
                    _publication_finding(
                        "publication-recovery-required",
                        f"Could not remove the recovered backup: {error}",
                    ),
                )
        else:
            try:
                backup.replace(target)
            except OSError as error:
                return (
                    _publication_finding(
                        "publication-recovery-required",
                        f"Could not restore the publication backup: {error}",
                    ),
                )

    expected_names = {
        entry.draft.name for entry in status.entries if entry.draft is not None
    } | {PUBLICATION_NAME, TRANSACTION_NAME}
    for staging in sorted(target.parent.glob(f".{target.name}-*")):
        if not staging.is_dir():
            return (
                _publication_finding(
                    "publication-recovery-required",
                    f"Unrecognized publication staging path: {staging.name}",
                ),
            )
        transaction = staging / TRANSACTION_NAME
        if not _transaction_matches(transaction, target, batch, receipt_digest):
            return (
                _publication_finding(
                    "publication-recovery-required",
                    f"Unrecognized publication staging path: {staging.name}",
                ),
            )
        entries = list(staging.iterdir())
        if any(not path.is_file() for path in entries) or {
            path.name for path in entries
        } - expected_names:
            return (
                _publication_finding(
                    "publication-recovery-required",
                    f"Unrecognized files remain in publication staging: {staging.name}",
                ),
            )
        try:
            shutil.rmtree(staging)
        except OSError as error:
            return (
                _publication_finding(
                    "publication-recovery-required",
                    f"Could not remove publication staging: {error}",
                ),
            )
    return ()


def _validate_existing_publication(
    target: Path,
    renderer: PublicationInspector,
    batch: str,
    receipt_digest: str,
    status: StatusReport,
    *,
    allow_transaction: bool = False,
) -> tuple[BlockingFinding, ...]:
    marker = target / PUBLICATION_NAME
    try:
        payload = json.loads(read_text_limited(marker, "The publication marker", 1_048_576))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        return (_publication_finding("unrecognized-publication", str(error)),)
    if not isinstance(payload, dict) or payload.get("schema") != PUBLICATION_SCHEMA:
        return (
            _publication_finding(
                "unrecognized-publication",
                "The destination has no recognized publication marker.",
            ),
        )
    if payload.get("batch") != batch:
        return (
            _publication_finding(
                "publication-batch-mismatch",
                "The destination belongs to a different grading batch.",
            ),
        )
    if payload.get("receipt_sha256") != receipt_digest:
        return (
            _publication_finding(
                "stale-publication",
                "The destination was published from a different verification receipt.",
            ),
        )
    outputs = payload.get("outputs")
    if not isinstance(outputs, list):
        return (
            _publication_finding(
                "unrecognized-publication",
                "The publication marker has no output list.",
            ),
        )
    expected_names = {
        entry.draft.name for entry in status.entries if entry.draft is not None
    }
    marker_outputs: dict[str, str] = {}
    for output in outputs:
        if not isinstance(output, dict):
            return (
                _publication_finding(
                    "unrecognized-publication",
                    "The publication marker has a malformed output entry.",
                ),
            )
        name = output.get("path")
        digest = output.get("sha256")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or not isinstance(digest, str)
            or len(digest) != 64
            or name in marker_outputs
        ):
            return (
                _publication_finding(
                    "unrecognized-publication",
                    "The publication marker has a malformed output entry.",
                ),
            )
        marker_outputs[name] = digest
    if set(marker_outputs) != expected_names:
        return (
            _publication_finding(
                "publication-files-changed",
                "The publication marker does not list the expected feedback files.",
            ),
        )
    actual_names = {path.name for path in target.iterdir()}
    allowed_names = expected_names | {PUBLICATION_NAME}
    if allow_transaction:
        allowed_names.add(TRANSACTION_NAME)
    if actual_names != allowed_names:
        return (
            _publication_finding(
                "publication-files-changed",
                "The destination has missing, renamed, or unrelated files.",
            ),
        )
    try:
        for name, digest in marker_outputs.items():
            if renderer.hash_output(target / name) != digest:
                return (
                    _publication_finding(
                        "publication-files-changed",
                        f"The published feedback changed: {name}",
                    ),
                )
    except (OSError, UnicodeError, ValueError) as error:
        return (
            _publication_finding("publication-files-changed", str(error)),
        )
    return ()


def _transaction_matches(
    marker: Path, target: Path, batch: str, receipt_digest: str
) -> bool:
    try:
        payload = json.loads(read_text_limited(marker, "The transaction marker", 1_048_576))
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return False
    return (
        isinstance(payload, dict)
        and payload.get("schema") == TRANSACTION_SCHEMA
        and payload.get("batch") == batch
        and payload.get("receipt_sha256") == receipt_digest
        and payload.get("target") == target.name
    )


def _publication_finding(code: str, message: str) -> BlockingFinding:
    return BlockingFinding(code=code, message=message, token="")


def _with_verification_instruction(message: str) -> str:
    lowered = message.lower()
    if "run verification" in lowered and "publication" in lowered:
        return message
    return message + " Run verification again before publication."


def _validate_verification_receipt(
    paths: BatchPaths, status: StatusReport
) -> _ReceiptValidation:
    receipt = paths.drafts / RECEIPT_NAME
    if not receipt.is_file():
        return _ReceiptValidation(present=False, valid=False, findings=())
    try:
        decoded = _decode_receipt(receipt)
        artifacts = _validated_receipt_artifacts(decoded, paths)
        expected = _expected_participant_artifacts(status, receipt.parent)
        actual = {
            (item["role"], item["token"], item["path"])
            for item in artifacts
            if item["role"] in _PARTICIPANT_ROLES
        }
        if actual != expected:
            raise _StaleReceipt("The grading batch membership changed after verification.")
        for item in artifacts:
            locator = Path(item["path"])
            artifact_path = locator if locator.is_absolute() else receipt.parent / locator
            if has_symlink_component(artifact_path):
                raise ValueError(
                    f"A verification receipt artifact uses a symlink: {item['path']}"
                )
            if not artifact_path.is_file():
                raise _StaleReceipt(f"A verified artifact is missing: {item['path']}")
            limits = {
                "redacted-submission": 25 * 1024 * 1024,
                "draft": 1_048_576,
                "grade-record": 1_048_576,
                "redaction-manifest": 1_048_576,
                "rubric": 5 * 1024 * 1024,
                "calibration-profile": 5 * 1024 * 1024,
                "pattern-reference": 5 * 1024 * 1024,
            }
            digest = hashlib.sha256(
                read_bytes_limited(
                    artifact_path,
                    f"The {item['role']} receipt artifact",
                    limits[item["role"]],
                )
            ).hexdigest()
            if digest != item["sha256"]:
                raise _StaleReceipt(f"A verified artifact changed: {item['path']}")
    except _StaleReceipt as error:
        return _ReceiptValidation(
            present=True,
            valid=False,
            findings=(
                BlockingFinding(
                    code="stale-verification-receipt",
                    message=str(error),
                    token="",
                ),
            ),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, KeyError, ValueError) as error:
        return _ReceiptValidation(
            present=True,
            valid=False,
            findings=(
                BlockingFinding(
                    code="malformed-verification-receipt",
                    message=str(error),
                    token="",
                ),
            ),
        )
    return _ReceiptValidation(present=True, valid=True, findings=())


class _StaleReceipt(Exception):
    """A well-formed receipt no longer matches its artifacts."""


def _validated_receipt_artifacts(
    receipt: _DecodedReceipt, paths: BatchPaths
) -> list[dict[str, str]]:
    if receipt.batch != paths.batch:
        raise _StaleReceipt("The verification receipt belongs to another grading batch.")
    validated: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for artifact in receipt.artifacts:
        if not isinstance(artifact, dict):
            raise ValueError("Each verification receipt artifact must be an object.")
        role = artifact.get("role")
        locator = artifact.get("path")
        digest = artifact.get("sha256")
        token = artifact.get("token", "")
        if role not in _RECEIPT_ROLES or not isinstance(locator, str) or not locator:
            raise ValueError("A verification receipt artifact has an invalid role or path.")
        suffixes = {
            "rubric": ".md",
            "calibration-profile": ".md",
            "pattern-reference": ".md",
        }
        if role in suffixes and Path(locator).suffix.lower() != suffixes[role]:
            raise ValueError(
                f"The {role} receipt artifact must be a Markdown file."
            )
        if role == "redaction-manifest" and Path(locator).name != REDACTION_MANIFEST_NAME:
            raise ValueError("The receipt names an invalid redaction manifest.")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError("A verification receipt artifact has an invalid SHA-256 hash.")
        try:
            if len(bytes.fromhex(digest)) != 32:
                raise ValueError
        except ValueError as error:
            raise ValueError(
                "A verification receipt artifact has an invalid SHA-256 hash."
            ) from error
        if role in (_COURSE_EVIDENCE_ROLES | _BATCH_EVIDENCE_ROLES) and "token" in artifact:
            raise ValueError("Course evidence in a receipt must not carry a token.")
        if role in _PARTICIPANT_ROLES and (not isinstance(token, str) or not token):
            raise ValueError(
                "A redacted submission, draft, or grade record in a receipt needs "
                "a token."
            )
        key = (role, token, locator)
        if key in seen:
            raise ValueError("The verification receipt repeats an artifact.")
        seen.add(key)
        item = {"role": role, "path": locator, "sha256": digest}
        if token:
            item["token"] = token
        validated.append(item)
    if {
        item["role"]
        for item in validated
        if item["role"] in _COURSE_EVIDENCE_ROLES
    } != _COURSE_EVIDENCE_ROLES:
        raise ValueError("The verification receipt is missing course evidence.")
    for role in _COURSE_EVIDENCE_ROLES:
        if sum(1 for item in validated if item["role"] == role) != 1:
            raise ValueError("The verification receipt repeats course evidence.")
    if sum(1 for item in validated if item["role"] == "redaction-manifest") != 1:
        raise ValueError("The verification receipt needs one redaction manifest.")
    return validated


def _expected_participant_artifacts(
    status: StatusReport, base: Path
) -> set[tuple[str, str, str]]:
    expected: set[tuple[str, str, str]] = set()
    for entry in status.entries:
        for role, path in _participant_paths(entry):
            if path is not None:
                expected.add(
                    (role, entry.token, _receipt_locator(path.resolve(), base.resolve()))
                )
    return expected


def _participant_paths(entry: BatchEntry) -> tuple[tuple[str, Path | None], ...]:
    return (
        ("redacted-submission", entry.source),
        ("draft", entry.draft),
        ("grade-record", entry.record),
    )


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def _load_entry(
    entry: BatchEntry,
    paths: BatchPaths,
    inputs: VerificationInputs | None = None,
) -> _LoadedEntry:
    findings: list[BlockingFinding] = []
    record = None
    if entry.record is not None:
        try:
            record = GradeRecord.load(entry.record)
        except _RECORD_LOAD_ERRORS as error:
            findings.append(
                BlockingFinding(
                    code="malformed-record",
                    message=str(error),
                    token=entry.token,
                )
            )
    if record is not None:
        if inputs is None:
            findings.extend(_batch_identity_findings(entry, record, paths))
        else:
            findings.extend(_metadata_findings(entry, record, paths, inputs))
        semantic_error = _record_semantic_error(record)
        if semantic_error:
            findings.append(
                BlockingFinding(
                    code="malformed-record",
                    message=semantic_error,
                    token=entry.token,
                )
            )

    source_text = None
    if entry.source is not None:
        try:
            source_text = read_text_limited(
                entry.source, "The redacted submission", 25 * 1024 * 1024
            )
        except (OSError, UnicodeError, ValueError) as error:
            findings.append(
                BlockingFinding(
                    code="unreadable-source",
                    message=f"Cannot read {entry.source.name}: {error}",
                    token=entry.token,
                )
            )

    draft_text = None
    if entry.draft is not None:
        try:
            draft_text = read_text_limited(
                entry.draft, "The feedback draft", 1_048_576
            )
        except (OSError, UnicodeError, ValueError) as error:
            findings.append(
                BlockingFinding(
                    code="unreadable-draft",
                    message=f"Cannot read {entry.draft.name}: {error}",
                    token=entry.token,
                )
            )
    return _LoadedEntry(
        entry=entry,
        record=record,
        source_text=source_text,
        draft_text=draft_text,
        findings=tuple(findings),
    )


def _record_semantic_error(record: GradeRecord) -> str:
    for criterion in record.criteria:
        if criterion.score is not None and (
            not isinstance(criterion.score, int)
            or isinstance(criterion.score, bool)
        ):
            return (
                f"Criterion {criterion.number} score must be an integer or null, "
                f"not {criterion.score!r}."
            )
        for justification in criterion.justifications:
            if justification.occurrence is not None and (
                not isinstance(justification.occurrence, int)
                or isinstance(justification.occurrence, bool)
            ):
                return (
                    "A justification occurrence must be an integer or null, not "
                    f"{justification.occurrence!r}."
                )

    density = record.pattern_density
    numeric_fields = ("words", "count", "per_thousand")
    for name in numeric_fields:
        value = density.get(name)
        if value is not None and (
            not isinstance(value, (int, float)) or isinstance(value, bool)
        ):
            return f"Pattern density {name} must be numeric, not {value!r}."
    if "by_pattern" in density and not isinstance(density["by_pattern"], dict):
        return "Pattern density by_pattern must be an object."
    if "examples" in density and not isinstance(density["examples"], list):
        return "Pattern density examples must be a list."
    return ""


def _metadata_findings(
    entry: BatchEntry,
    record: GradeRecord,
    paths: BatchPaths,
    inputs: VerificationInputs,
) -> list[BlockingFinding]:
    findings = _batch_identity_findings(entry, record, paths)
    checks = [
        (
            record.course != inputs.course,
            "course-mismatch",
            "The grade record belongs to a different course.",
        ),
        (
            not _same_path(record.rubric, inputs.rubric),
            "rubric-mismatch",
            "The grade record names a different rubric.",
        ),
        (
            not _same_path(record.profile, inputs.profile),
            "profile-mismatch",
            "The grade record names a different calibration profile.",
        ),
    ]
    if entry.source is not None:
        checks.insert(
            1,
            (
                not _same_path(record.source, entry.source),
                "source-mismatch",
                (
                    "The grade record names a different source. Verification uses "
                    "the redacted submission from this grading batch."
                ),
            ),
        )
    findings.extend(
        BlockingFinding(code=code, message=message, token=entry.token)
        for mismatched, code, message in checks
        if mismatched
    )
    return findings


def _batch_identity_findings(
    entry: BatchEntry, record: GradeRecord, paths: BatchPaths
) -> list[BlockingFinding]:
    checks = (
        (
            record.token != entry.token,
            "token-mismatch",
            "The grade record token does not match its filename token.",
        ),
        (
            record.batch != paths.batch,
            "batch-mismatch",
            "The grade record belongs to a different grading batch.",
        ),
    )
    return [
        BlockingFinding(code=code, message=message, token=entry.token)
        for mismatched, code, message in checks
        if mismatched
    ]
