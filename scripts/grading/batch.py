"""Say what a batch still owes, so an interrupted run resumes instead of restarting.

Regrading a submission that already has a draft is not merely wasted work. It
produces a second, different grade for the same student, and nothing in the
output says which one the instructor should send.

A draft or grade record with no redacted submission is orphaned and reported.
It usually means a prior run's output was left in the drafts directory, and
grading on top of it would mix two batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from scripts.grading.density import load_terms
from scripts.grading.profile import ProfileError, load_profile
from scripts.grading.records import GradeRecord, RecordError
from scripts.grading.rubric import RubricError, load_rubric
from scripts.grading.verify import ERROR, verify_draft


class Progress(str, Enum):
    """How far one redacted submission has moved through grading."""

    PENDING = "pending"
    DRAFTED = "drafted"


PENDING = Progress.PENDING.value
DRAFTED = Progress.DRAFTED.value
INCOMPLETE = "incomplete"
ORPHAN = "orphan"

_MALFORMED_CODES = {
    "malformed-record",
    "unreadable-source",
    "unreadable-draft",
    "no-records",
    "malformed-rubric",
    "malformed-patterns",
}


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

    def legacy_state(self, entry: BatchEntry) -> str:
        """Return the mixed state expected by callers that have not migrated."""
        codes = {
            finding.code
            for finding in self.blocking_findings
            if finding.token == entry.token
        }
        if "orphaned-artifact" in codes:
            return ORPHAN
        if "incomplete-pair" in codes:
            return INCOMPLETE
        if entry.progress is not None:
            return entry.progress.value
        return ORPHAN


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


@dataclass(frozen=True)
class GradingBatch:
    """Own the lifecycle rules for one grading batch."""

    paths: BatchPaths

    @classmethod
    def open(cls, paths: BatchPaths) -> "GradingBatch":
        return cls(paths)

    def status(self) -> StatusReport:
        sources = _artifact_paths(self.paths.redacted, (".md", ".txt", ".docx"))
        drafts = _artifact_paths(self.paths.drafts, (".md",))
        records = _artifact_paths(self.paths.drafts, (".json",))
        entries: list[BatchEntry] = []
        findings: list[BlockingFinding] = []
        for token in sorted(set(sources) | set(drafts) | set(records)):
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
        return StatusReport(
            entries=tuple(entries),
            blocking_findings=tuple(findings),
        )

    def verify(
        self, inputs: VerificationInputs, *, persist: bool = True
    ) -> VerificationReport:
        status = self.status()
        blocking = list(status.blocking_findings)
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
        loaded_records: dict[str, GradeRecord] = {}
        for entry in status.entries:
            if entry.record is None:
                continue
            try:
                loaded_records[entry.token] = GradeRecord.load(entry.record)
            except (RecordError, OSError, UnicodeError) as error:
                blocking.append(
                    BlockingFinding(
                        code="malformed-record",
                        message=str(error),
                        token=entry.token,
                    )
                )
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
            return VerificationReport(
                status=status,
                blocking_findings=tuple(blocking),
                warnings=(),
                checked_tokens=(),
                persisted=False,
                failure=VerificationFailure.NO_PROFILE,
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
            return VerificationReport(
                status=status,
                blocking_findings=tuple(blocking),
                warnings=(),
                checked_tokens=(),
                persisted=False,
                failure=VerificationFailure.MALFORMED,
            )
        drifted = profile.drift(rubric)
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
            return VerificationReport(
                status=status,
                blocking_findings=tuple(blocking),
                warnings=(),
                checked_tokens=(),
                persisted=False,
                failure=VerificationFailure.RUBRIC_DRIFT,
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
            return VerificationReport(
                status=status,
                blocking_findings=tuple(blocking),
                warnings=(),
                checked_tokens=(),
                persisted=False,
                failure=VerificationFailure.MALFORMED,
            )
        warnings: list[AdvisoryFinding] = []
        checked: list[str] = []
        records: list[tuple[Path, GradeRecord]] = []

        for entry in status.entries:
            if entry.source is None or entry.draft is None or entry.record is None:
                continue
            record = loaded_records.get(entry.token)
            if record is None:
                continue
            blocking.extend(_metadata_findings(entry, record, self.paths, inputs))
            try:
                source_text = entry.source.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                blocking.append(
                    BlockingFinding(
                        code="unreadable-source",
                        message=f"Cannot read {entry.source.name}: {error}",
                        token=entry.token,
                    )
                )
                continue
            try:
                draft_text = entry.draft.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as error:
                blocking.append(
                    BlockingFinding(
                        code="unreadable-draft",
                        message=f"Cannot read {entry.draft.name}: {error}",
                        token=entry.token,
                    )
                )
                continue
            findings = verify_draft(
                record,
                draft_text,
                source_text,
                rubric,
                profile,
                terms,
            )
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

        if persist and not blocking:
            for path, record in records:
                record.save(path)
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
        )


@dataclass(frozen=True)
class Item:
    """One token's artifacts in the legacy survey shape."""

    token: str
    source: Path | None
    draft: Path | None
    record: Path | None
    state: str


def _artifact_paths(
    directory: Path, suffixes: tuple[str, ...]
) -> dict[str, tuple[Path, ...]]:
    if not directory.is_dir():
        return {}
    found: dict[str, list[Path]] = {}
    for path in sorted(directory.iterdir()):
        if (
            path.is_file()
            and path.suffix in suffixes
            and not path.name.startswith("_")
        ):
            found.setdefault(path.stem, []).append(path)
    return {token: tuple(paths) for token, paths in found.items()}


def _same_path(left: str | Path, right: str | Path) -> bool:
    return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()


def _metadata_findings(
    entry: BatchEntry,
    record: GradeRecord,
    paths: BatchPaths,
    inputs: VerificationInputs,
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
        (
            record.course != inputs.course,
            "course-mismatch",
            "The grade record belongs to a different course.",
        ),
        (
            not _same_path(record.source, entry.source),
            "source-mismatch",
            (
                "The grade record names a different source. Verification uses the "
                "redacted submission from this grading batch."
            ),
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
    )
    return [
        BlockingFinding(code=code, message=message, token=entry.token)
        for mismatched, code, message in checks
        if mismatched
    ]


def survey(redacted: Path, drafts: Path) -> list[Item]:
    """Return the legacy mixed state for each token's artifacts, in token order."""
    report = GradingBatch.open(
        BatchPaths(batch="", redacted=redacted, drafts=drafts)
    ).status()

    items = []
    for entry in report.entries:
        items.append(
            Item(
                token=entry.token,
                source=entry.source,
                draft=entry.draft,
                record=entry.record,
                state=report.legacy_state(entry),
            )
        )
    return items


def summary(items: list[Item]) -> dict[str, int]:
    """Count how many legacy survey items sit in each state."""
    counts = {PENDING: 0, DRAFTED: 0, INCOMPLETE: 0, ORPHAN: 0}
    for item in items:
        counts[item.state] += 1
    return counts
