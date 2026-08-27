"""Run every redaction layer over a batch and fail closed on any doubt.

Silent partial success is the failure mode that matters here. Exit codes:
0 clean, 2 an unmatched roster entry, 3 unreviewed layer-4 candidates, 4 an
unreadable or oversized input, 5 the destination directory is not empty, 6 an
unsafe path, and 7 a participant collision. There is no override for 2, 3, 4,
6, or 7 -- fix the input and run again. Exit 5 exists only
to stop stale files from an earlier redaction from silently mixing into a new one:
without it, a re-run after fixing a warning would leave last time's
STUDENT_02.md sitting next to this time's STUDENT_01.md, indistinguishable
from current output, and an instructor would grade a student who did not
submit this batch. ``force=True`` (the CLI's --force) permits writing into
a non-empty destination, and deletes only the earlier redaction's token-named
outputs first -- never anything else someone put there on purpose.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts.path_safety import (
        has_symlink_component,
        is_within,
        paths_overlap,
        require_component,
    )
except ModuleNotFoundError:  # direct scripts/redact.py execution
    from path_safety import (
        has_symlink_component,
        is_within,
        paths_overlap,
        require_component,
    )

from .candidates import Candidate, find_candidates
from .documents import extract, output_name
from .patterns import redact_structured
from .private_files import write_private_text
from .manifest import RedactionManifest
from .roster import BareHit, RosterEntry, full_name_variants, redact_roster
from .selfid import SelfIdWarning, find_self_identifying
from .tokens import TokenMap

EXIT_OK = 0
EXIT_ROSTER = 2
EXIT_CANDIDATES = 3
EXIT_UNREADABLE = 4
EXIT_DESTINATION_NOT_EMPTY = 5
EXIT_UNSAFE_PATH = 6
EXIT_COLLISION = 7

UNKNOWN_PREFIX = "UNKNOWN"

# The only two prefixes run() ever gives a redacted file's token (see the
# `entry.token` assignment below): a matched roster student, or an
# unmatched submission. Only a file whose name has this shape could be a
# token-named output from an earlier redaction, so only these are ever candidates
# for deletion under --force.
_TOKEN_OUTPUT_PATTERN = re.compile(rf"^(?:STUDENT|{UNKNOWN_PREFIX})_\d+\..+$")
MAX_BATCH_FILES = 500
MAX_BATCH_BYTES = 500 * 1024 * 1024


def _named_students(
    text: str, roster: list[RosterEntry]
) -> list[tuple[int, RosterEntry]]:
    """Return every roster entry named in text, earliest occurrence first.

    A file's author is named first -- typically in a header -- while a
    classmate the author writes about is cited later in the body. Ranking
    by where a name FIRST appears, rather than by roster row order or
    allocation order, is what makes attribution independent of both: the
    same paper attributes to the same author no matter how the roster CSV
    happens to be sorted.
    """
    found = []
    for entry in roster:
        offset = None
        for form in full_name_variants(entry.name):
            match = re.search(rf"\b{re.escape(form)}\b", text, re.IGNORECASE)
            if match and (offset is None or match.start() < offset):
                offset = match.start()
        if offset is not None:
            found.append((offset, entry))
    return sorted(found, key=lambda pair: pair[0])


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
    named_students: list[str] = field(default_factory=list)


@dataclass
class BatchResult:
    """What happened to the batch, and whether the cloud path may proceed."""

    files: list[FileResult] = field(default_factory=list)
    unmatched_roster: list[RosterEntry] = field(default_factory=list)
    exit_code: int = EXIT_OK
    message: str = ""


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
    force: bool = False,
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

    A non-empty destination is refused unless ``force`` is True, so a
    second redaction can never leave earlier files sitting alongside the
    current files -- current and stale output would otherwise be indistinguishable,
    and the model would grade a student who did not submit this batch. With
    ``force``, only files shaped like an earlier redaction's token-named outputs
    (STUDENT_NN.* or UNKNOWN_NN.*) are deleted before writing; anything else
    already in destination is left alone.

    A file's output name is attributed to whichever roster student's full
    name appears EARLIEST in the file, not to whichever roster row happens
    to come first in the CSV: the author is named in a header, a classmate
    the author writes about is cited later in the body. When more than one
    roster student is named in the same file, that is reported under
    ``ambiguous_authorship`` in the review file, listing every student
    named and the token that was chosen -- position is a good heuristic,
    not a guarantee, and it never changes the exit code.
    """
    if review_path is None:
        review_path = map_path.parent / "reviews" / f"{batch}.json"

    try:
        require_component(batch, "The grading batch")
    except ValueError as error:
        return BatchResult(exit_code=EXIT_UNSAFE_PATH, message=str(error))
    if has_symlink_component(source) or has_symlink_component(destination):
        return BatchResult(
            exit_code=EXIT_UNSAFE_PATH,
            message="Submission and redacted-output roots must not be symlinks.",
        )
    if paths_overlap(source, destination):
        return BatchResult(
            exit_code=EXIT_UNSAFE_PATH,
            message="Submission and redacted-output directories must be separate.",
        )
    if map_path == review_path or map_path.resolve() == review_path.resolve():
        return BatchResult(
            exit_code=EXIT_UNSAFE_PATH,
            message="The token map and review file must use separate paths.",
        )
    for label, private_path in (("token map", map_path), ("review file", review_path)):
        if has_symlink_component(private_path) or any(
            is_within(private_path, root, follow_symlinks=follow)
            for root in (source, destination)
            for follow in (False, True)
        ):
            return BatchResult(
                exit_code=EXIT_UNSAFE_PATH,
                message=(
                    f"The {label} must be a regular local file outside the "
                    "submission and redacted-output directories."
                ),
            )

    source_paths = sorted(
        p for p in source.rglob("*") if p.is_file() or p.is_symlink()
    )
    if len(source_paths) > MAX_BATCH_FILES:
        return BatchResult(
            exit_code=EXIT_UNREADABLE,
            message=f"A grading batch may contain at most {MAX_BATCH_FILES} files.",
        )
    try:
        batch_bytes = sum(path.lstat().st_size for path in source_paths)
    except OSError as error:
        return BatchResult(exit_code=EXIT_UNREADABLE, message=str(error))
    if batch_bytes > MAX_BATCH_BYTES:
        return BatchResult(
            exit_code=EXIT_UNREADABLE,
            message="The grading batch is too large. The input limit is 500 MiB.",
        )

    if destination.exists() and any(destination.iterdir()):
        if not force:
            RedactionManifest.create(
                batch,
                TokenMap(batch).run_id,
                [],
                reviewed=reviewed,
                exit_code=EXIT_DESTINATION_NOT_EMPTY,
            ).save(destination)
            return BatchResult(
                exit_code=EXIT_DESTINATION_NOT_EMPTY,
                message=(
                    f"{destination} is not empty. Empty it, choose a "
                    "different destination, or pass --force to overwrite "
                    "the earlier redaction's token-named outputs."
                ),
            )
        for entry in destination.iterdir():
            if entry.is_file() and _TOKEN_OUTPUT_PATTERN.match(entry.name):
                entry.unlink()

    destination.mkdir(parents=True, exist_ok=True)

    tmap = TokenMap(batch)
    result = BatchResult()
    matched_names: set[str] = set()
    unknown_count = 0
    claimed_tokens: dict[str, Path] = {}

    for path in source_paths:
        if path.name.startswith("_"):
            continue

        entry = FileResult(source=path)
        if path.is_symlink():
            entry.error = (
                f"Could not read {path.name}: symbolic links are not accepted "
                "as submissions."
            )
            result.files.append(entry)
            continue
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

        # Attribution must be decided against the RAW text, before any name
        # becomes a token and before roster order can influence anything --
        # see _named_students.
        named = _named_students(text, roster)
        entry.named_students = [e.name for _, e in named]

        roster_result = redact_roster(
            text, roster, tmap, path.name, protected=protected, bare_names=bare_names
        )
        matched_names |= roster_result.matched
        entry.bare_hits = roster_result.bare_hits

        text, _ = redact_structured(roster_result.text, tmap, path.name)

        entry.candidates = find_candidates(text, protected=protected)
        entry.warnings = find_self_identifying(text)

        file_student_tokens = {
            e.value.casefold(): token
            for token, e in tmap.tokens().items()
            if e.kind == "STUDENT" and e.file == path.name
        }
        student_tokens = {
            e.value.casefold(): token
            for token, e in tmap.tokens().items()
            if e.kind == "STUDENT"
        }
        author = named[0][1] if named else None
        if author is not None and author.name.casefold() in student_tokens:
            entry.token = student_tokens[author.name.casefold()]
        elif file_student_tokens:
            # Defensive fallback: a roster student's full name is confirmed
            # present in this file (redact_roster matched it), but the
            # position scan above did not resolve to one of them -- keep
            # the file attributed to *a* matched student rather than
            # falling through to UNKNOWN.
            entry.token = next(iter(file_student_tokens.values()))
        else:
            unknown_count += 1
            entry.token = f"{UNKNOWN_PREFIX}_{unknown_count:02d}"

        output = destination / output_name(entry.token, path.suffix or ".md")
        if entry.token in claimed_tokens:
            entry.error = (
                f"Duplicate output for {entry.token}: both "
                f"{claimed_tokens[entry.token].name} and {path.name} resolve "
                "to the same grading-batch participant."
            )
            result.files.append(entry)
            continue
        claimed_tokens[entry.token] = path
        output.write_text(text, encoding="utf-8")
        entry.output = output
        result.files.append(entry)

    result.unmatched_roster = [e for e in roster if e.name not in matched_names]
    collisions = [f.error for f in result.files if f.error.startswith("Duplicate output")]
    if collisions:
        result.exit_code = EXIT_COLLISION
        result.message = " ".join(collisions)
    elif any(f.error for f in result.files):
        result.exit_code = EXIT_UNREADABLE
    elif result.unmatched_roster:
        result.exit_code = EXIT_ROSTER
    elif any(f.candidates for f in result.files) and not reviewed:
        result.exit_code = EXIT_CANDIDATES

    RedactionManifest.create(
        batch,
        tmap.run_id,
        [file.output for file in result.files if file.output is not None],
        reviewed=reviewed,
        exit_code=result.exit_code,
    ).save(destination)
    tmap.save(map_path)
    _write_review(review_path, result)

    return result


def _write_review(review_path: Path, result: BatchResult) -> None:
    """Write everything a human must look at before the cloud path, owner-only.

    This file is the one place unredacted candidate names are written to
    disk on purpose, so it is written owner-only, like the map, and it must
    never live inside the redacted output directory.
    """
    payload = {
        "errors": [
            {"file": f.source.name, "error": f.error}
            for f in result.files
            if f.error
        ],
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
        # A file naming more than one roster student had to have its
        # authorship decided by position -- a good heuristic, not a
        # guarantee -- so every such case is surfaced for a human to check,
        # without changing the exit code: citing a classmate is legitimate.
        "ambiguous_authorship": [
            {
                "file": f.output.name if f.output else f.source.name,
                "token": f.token,
                "named": f.named_students,
            }
            for f in result.files
            if len(f.named_students) > 1
        ],
    }
    write_private_text(review_path, json.dumps(payload, indent=2))
