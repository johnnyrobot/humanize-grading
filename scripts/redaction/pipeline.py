"""Run every redaction layer over a batch and fail closed on any doubt.

Silent partial success is the failure mode that matters here, so there is no
override flag. A non-zero exit must stop the cloud path.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from .candidates import Candidate, find_candidates
from .documents import extract, output_name
from .patterns import redact_structured
from .roster import BareHit, RosterEntry, redact_roster
from .selfid import SelfIdWarning, find_self_identifying
from .tokens import TokenMap

EXIT_OK = 0
EXIT_ROSTER = 2
EXIT_CANDIDATES = 3
EXIT_UNREADABLE = 4

UNKNOWN_PREFIX = "UNKNOWN"


@dataclass
class FileResult:
    """What happened to one submission."""

    source: Path
    token: str = ""
    output: Path | None = None
    candidates: list[Candidate] = field(default_factory=list)
    warnings: list[SelfIdWarning] = field(default_factory=list)
    bare_hits: list[BareHit] = field(default_factory=list)
    error: str = ""
    skipped_empty: bool = False


@dataclass
class BatchResult:
    """What happened to the batch, and whether the cloud path may proceed."""

    files: list[FileResult] = field(default_factory=list)
    unmatched_roster: list[RosterEntry] = field(default_factory=list)
    exit_code: int = EXIT_OK


def run(
    source: Path,
    destination: Path,
    map_path: Path,
    roster: list[RosterEntry],
    batch: str,
    protected: tuple[str, ...] = (),
    bare_names: bool = True,
    reviewed: bool = False,
    review_path: Path | None = None,
) -> BatchResult:
    """Redact every submission under source and write the map and review file.

    Every file under source is read, every identity every layer can find is
    replaced with a token, and the safe copy is written to destination under
    its token name -- never its original filename. The token map, which is
    the only file that can turn a token back into a real name, is written
    next to map_path, owner-only.

    The review file lists everything a human still needs to check by eye --
    names layer 4 only flagged, passages that may self-identify their
    author, and bare names layer 1 substituted. It is written to
    review_path, NOT into destination: it holds the very names redaction
    just removed from the redacted files, so it must never sit where the
    cloud model would read it.

    A blank submission (the container parsed fine but the body is empty) is
    skipped, not graded, and reported in the review file -- that is a fact
    the instructor must see, not a failure.
    """
    destination.mkdir(parents=True, exist_ok=True)
    if review_path is None:
        review_path = map_path.parent / "reviews" / f"{batch}.json"

    tmap = TokenMap(batch)
    result = BatchResult()
    matched_names: set[str] = set()
    unknown_count = 0

    for path in sorted(p for p in source.rglob("*") if p.is_file()):
        if path.name.startswith("_"):
            continue

        entry = FileResult(source=path)
        extracted = extract(path)
        if extracted.error:
            entry.error = extracted.error
            result.files.append(entry)
            continue

        if extracted.empty_body:
            entry.skipped_empty = True
            result.files.append(entry)
            continue

        # Metadata (docx author, last-editor, title, headers, footers, ...)
        # carries identity outside the visible body. Prepending each string
        # makes it tokenizable by the same passes that scan the body, so
        # none of it is silently dropped.
        text = extracted.text
        for value in extracted.metadata:
            text = f"{value}\n{text}"

        roster_result = redact_roster(
            text, roster, tmap, path.name, protected=protected, bare_names=bare_names
        )
        matched_names |= roster_result.matched
        entry.bare_hits = roster_result.bare_hits

        text, _ = redact_structured(roster_result.text, tmap, path.name)

        entry.candidates = find_candidates(text, protected=protected)
        entry.warnings = find_self_identifying(text)

        tokens = [
            token
            for token, e in tmap.tokens().items()
            if e.kind == "STUDENT" and e.file == path.name
        ]
        if tokens:
            entry.token = tokens[0]
        else:
            unknown_count += 1
            entry.token = f"{UNKNOWN_PREFIX}_{unknown_count:02d}"

        output = destination / output_name(entry.token, path.suffix or ".md")
        output.write_text(text, encoding="utf-8")
        entry.output = output
        result.files.append(entry)

    tmap.save(map_path)

    result.unmatched_roster = [e for e in roster if e.name not in matched_names]
    _write_review(review_path, result)

    if any(f.error for f in result.files):
        result.exit_code = EXIT_UNREADABLE
    elif result.unmatched_roster:
        result.exit_code = EXIT_ROSTER
    elif any(f.candidates for f in result.files) and not reviewed:
        result.exit_code = EXIT_CANDIDATES

    return result


def _write_review(review_path: Path, result: BatchResult) -> None:
    """Write everything a human must look at before the cloud path, owner-only.

    This file is the one place unredacted candidate names are written to
    disk on purpose, so it is written owner-only, like the map, and it must
    never live inside the redacted output directory.
    """
    payload = {
        "candidates": [
            {"file": f.output.name if f.output else f.source.name, "name": c.name,
             "line": c.line, "context": c.context}
            for f in result.files
            for c in f.candidates
        ],
        "self_identifying": [
            {"file": f.output.name if f.output else f.source.name, "line": w.line,
             "sentence": w.sentence, "reasons": list(w.reasons)}
            for f in result.files
            for w in f.warnings
        ],
        "bare_name_substitutions": [
            {"file": f.output.name if f.output else f.source.name, "name": h.name,
             "token": h.token, "context": h.context}
            for f in result.files
            for h in f.bare_hits
        ],
        "skipped_empty": [f.source.name for f in result.files if f.skipped_empty],
    }
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.chmod(review_path, 0o600)
