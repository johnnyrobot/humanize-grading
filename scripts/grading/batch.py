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
class SubmissionStatus:
    """One redacted submission and its current grading progress."""

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

    submissions: tuple[SubmissionStatus, ...]
    blocking_findings: tuple[BlockingFinding, ...]


@dataclass(frozen=True)
class GradingBatch:
    """Own the lifecycle rules for one grading batch."""

    paths: BatchPaths

    @classmethod
    def open(cls, paths: BatchPaths) -> "GradingBatch":
        return cls(paths)

    def status(self) -> StatusReport:
        sources = _tokens(self.paths.redacted, (".md", ".txt", ".docx"))
        drafts = _tokens(self.paths.drafts, (".md",))
        records = _tokens(self.paths.drafts, (".json",))
        submissions: list[SubmissionStatus] = []
        findings: list[BlockingFinding] = []
        for token in sorted(set(sources) | set(drafts) | set(records)):
            source = sources.get(token)
            draft = drafts.get(token)
            record = records.get(token)
            if source is None:
                progress = None
            elif draft is not None and record is not None:
                progress = Progress.DRAFTED
            else:
                progress = Progress.PENDING
            submissions.append(
                SubmissionStatus(
                    token=token,
                    progress=progress,
                    source=source,
                    draft=draft,
                    record=record,
                )
            )
            if source is None:
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
            elif (draft is None) != (record is None):
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
            submissions=tuple(submissions),
            blocking_findings=tuple(findings),
        )


@dataclass(frozen=True)
class Item:
    """One submission and how far the batch has got with it."""

    token: str
    source: Path | None
    draft: Path | None
    record: Path | None
    state: str


def _tokens(directory: Path, suffixes: tuple[str, ...]) -> dict[str, Path]:
    if not directory.is_dir():
        return {}
    return {
        path.stem: path
        for path in sorted(directory.iterdir())
        if path.is_file()
        and path.suffix in suffixes
        and not path.name.startswith("_")
    }


def survey(redacted: Path, drafts: Path) -> list[Item]:
    """Return one item per submission and per orphaned draft, in token order."""
    sources = _tokens(redacted, (".md", ".txt", ".docx"))
    written = _tokens(drafts, (".md",))
    records = _tokens(drafts, (".json",))

    items: list[Item] = []
    for token in sorted(set(sources) | set(written) | set(records)):
        source = sources.get(token)
        draft = written.get(token)
        record = records.get(token)
        if source is None:
            state = ORPHAN
        elif draft is not None and record is not None:
            state = DRAFTED
        elif draft is None and record is None:
            state = PENDING
        else:
            state = INCOMPLETE
        items.append(
            Item(token=token, source=source, draft=draft, record=record, state=state)
        )
    return items


def summary(items: list[Item]) -> dict[str, int]:
    """Count how many submissions sit in each state."""
    counts = {PENDING: 0, DRAFTED: 0, INCOMPLETE: 0, ORPHAN: 0}
    for item in items:
        counts[item.state] += 1
    return counts
