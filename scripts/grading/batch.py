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


class Progress(str, Enum):
    """How far one redacted submission has moved through grading."""

    PENDING = "pending"
    DRAFTED = "drafted"


PENDING = Progress.PENDING.value
DRAFTED = Progress.DRAFTED.value
INCOMPLETE = "incomplete"
ORPHAN = "orphan"


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
    """Count how many submissions sit in each state."""
    counts = {PENDING: 0, DRAFTED: 0, INCOMPLETE: 0, ORPHAN: 0}
    for item in items:
        counts[item.state] += 1
    return counts
