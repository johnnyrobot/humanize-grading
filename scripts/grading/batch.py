"""Say what a batch still owes, so an interrupted run resumes instead of restarting.

Regrading a submission that already has a draft is not merely wasted work. It
produces a second, different grade for the same student, and nothing in the
output says which one the instructor should send.

A draft with no redacted source is called an orphan and reported. It usually
means a prior run's output was left in the drafts directory, and grading on
top of it would mix two batches.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PENDING = "pending"
DRAFTED = "drafted"
INCOMPLETE = "incomplete"
ORPHAN = "orphan"


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
