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

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts.path_safety import (
        has_symlink_component,
        is_within,
        paths_overlap,
        read_bytes_limited,
        require_component,
    )
except ModuleNotFoundError:  # direct scripts/redact.py execution
    from path_safety import (
        has_symlink_component,
        is_within,
        paths_overlap,
        read_bytes_limited,
        require_component,
    )

from .candidates import (
    Candidate,
    CandidateReviewLimitError,
    ExcludedName,
    review_candidates,
)
from .documents import MAX_FILE_BYTES, extract, output_name, read_submission_snapshot
from .patterns import redact_structured
from .private_files import write_private_text
from .manifest import SCHEMA as MANIFEST_SCHEMA
from .manifest import RedactionInvalidationError, RedactionManifest
from .roster import BareHit, RosterEntry, full_name_variants, redact_roster
from .selfid import SelfIdWarning, find_self_identifying
from .tokens import TokenMap, TokenMapLimitError

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
MAX_PRIVATE_ARTIFACT_BYTES = 100 * 1024 * 1024
REVIEW_SCHEMA = "redaction-review/1"
MAX_REVIEW_NAME_ITEMS = 10_000


class PrivateReviewLimitError(ValueError):
    """The private review exceeded its public serialized byte limit."""


def invalidate_existing_redaction(
    *,
    source: Path,
    destination: Path,
    map_path: Path,
    review_path: Path,
    batch: str,
    exit_code: int,
) -> bool:
    """Invalidate prior clean state only when every destination role is safe."""
    try:
        if has_symlink_component(destination) or paths_overlap(source, destination):
            return False
        if map_path == review_path or map_path.resolve() == review_path.resolve():
            return False
        for private_path in (map_path, review_path):
            if has_symlink_component(private_path) or any(
                is_within(private_path, root, follow_symlinks=follow)
                for root in (source, destination)
                for follow in (False, True)
            ):
                return False
    except OSError as error:
        raise RedactionInvalidationError(
            f"Could not validate prior redaction state for invalidation: {error}"
        ) from error
    return RedactionManifest.invalidate_existing(
        destination, batch=batch, exit_code=exit_code
    )


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
    excluded_names: list[ExcludedName] = field(default_factory=list)
    metadata: list[str] = field(default_factory=list)
    source_sha256: str = ""
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
    state_errors: list[str] = field(default_factory=list)
    compact_review: bool = False


def _sha256(path: Path, label: str, max_bytes: int) -> str:
    """Hash one bounded regular artifact without exposing its contents."""
    return hashlib.sha256(read_bytes_limited(path, label, max_bytes)).hexdigest()


def _source_binding(source: Path, source_paths: list[Path]) -> list[dict[str, str | None]]:
    """Read current processed source snapshots for approval comparison."""
    records: list[dict[str, str | None]] = []
    for path in source_paths:
        if path.name.startswith("_"):
            continue
        relative = path.relative_to(source).as_posix()
        digest: str | None = None
        if not path.is_symlink():
            try:
                _, digest = read_submission_snapshot(path)
            except (OSError, ValueError):
                digest = None
        records.append({"path": relative, "sha256": digest})
    return records


def _result_source_binding(
    source: Path, files: list[FileResult]
) -> list[dict[str, str | None]]:
    """Bind the exact byte snapshots already used by extraction."""
    return [
        {
            "path": entry.source.relative_to(source).as_posix(),
            "sha256": entry.source_sha256 or None,
        }
        for entry in files
    ]


def _output_binding(outputs: list[Path]) -> list[dict[str, str]]:
    """Describe the exact model-visible output membership and bytes."""
    return [
        {
            "path": path.name,
            "sha256": _sha256(path, "The redacted submission", MAX_FILE_BYTES),
        }
        for path in sorted(outputs, key=lambda item: item.name)
    ]


def _approval_path_error(
    source: Path, destination: Path, map_path: Path, review_path: Path
) -> str | None:
    """Return why an approval role path is unsafe, or None when roles are separate."""
    if has_symlink_component(source) or has_symlink_component(destination):
        return "Submission and redacted-output roots must not be symlinks."
    if paths_overlap(source, destination):
        return "Submission and redacted-output directories must be separate."
    if map_path == review_path or map_path.resolve() == review_path.resolve():
        return "The token map and review file must use separate paths."
    for label, private_path in (("token map", map_path), ("review file", review_path)):
        if has_symlink_component(private_path) or any(
            is_within(private_path, root, follow_symlinks=follow)
            for root in (source, destination)
            for follow in (False, True)
        ):
            return (
                f"The {label} must be a regular local file outside the "
                "submission and redacted-output directories."
            )
    return None


def _approve_existing_review(
    *,
    source: Path,
    destination: Path,
    map_path: Path,
    review_path: Path,
    batch: str,
) -> BatchResult:
    """Approve only the exact existing run and private report the instructor read."""
    try:
        path_error = _approval_path_error(source, destination, map_path, review_path)
        if path_error:
            return BatchResult(exit_code=EXIT_UNSAFE_PATH, message=path_error)
        manifest = RedactionManifest.load(destination)
        if manifest.schema != MANIFEST_SCHEMA:
            raise ValueError("A fresh current-schema redaction is required before review.")
        if manifest.batch != batch:
            raise ValueError("The redaction manifest belongs to another grading batch.")
        if manifest.reviewed or manifest.exit_code not in {EXIT_OK, EXIT_CANDIDATES}:
            raise ValueError("No eligible unreviewed redaction is waiting for approval.")
        manifest.validate(destination)

        map_bytes = read_bytes_limited(map_path, "The token map", 5 * 1024 * 1024)
        map_digest = hashlib.sha256(map_bytes).hexdigest()
        review_bytes = read_bytes_limited(
            review_path, "The private redaction review", MAX_PRIVATE_ARTIFACT_BYTES
        )
        review_digest = hashlib.sha256(review_bytes).hexdigest()
        if map_digest != manifest.map_sha256 or review_digest != manifest.review_sha256:
            raise ValueError("The private redaction evidence changed after it was written.")

        token_map = TokenMap.from_bytes(map_bytes)
        if token_map.batch != batch or token_map.run_id != manifest.run_id:
            raise ValueError("The token map does not belong to this redaction run.")

        review = json.loads(review_bytes.decode("utf-8"))
        binding = review.get("binding") if isinstance(review, dict) else None
        source_paths = sorted(
            path
            for path in source.rglob("*")
            if path.is_file() or path.is_symlink()
        )
        expected_outputs = [
            {"path": path, "sha256": digest} for path, digest in manifest.outputs
        ]
        if not isinstance(binding, dict) or binding != {
            "schema": REVIEW_SCHEMA,
            "batch": batch,
            "run_id": manifest.run_id,
            "sources": _source_binding(source, source_paths),
            "outputs": expected_outputs,
            "map_sha256": map_digest,
        }:
            raise ValueError("The private review does not match the current redaction run.")

        RedactionManifest(
            batch=manifest.batch,
            run_id=manifest.run_id,
            reviewed=True,
            exit_code=EXIT_OK,
            map_sha256=manifest.map_sha256,
            review_sha256=manifest.review_sha256,
            outputs=manifest.outputs,
        ).save(destination)
        return BatchResult(exit_code=EXIT_OK, message="Approved the existing redaction review.")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        return BatchResult(
            exit_code=EXIT_CANDIDATES,
            message=f"Could not approve the private redaction review: {error}",
        )


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

    ``reviewed=True`` is an approval operation. It does not run redaction
    again. It verifies the existing source, outputs, map, review, and run
    bindings, then marks only that exact manifest reviewed.
    """
    if review_path is None:
        review_path = map_path.parent / "reviews" / f"{batch}.json"

    try:
        require_component(batch, "The grading batch")
    except ValueError as error:
        return BatchResult(exit_code=EXIT_UNSAFE_PATH, message=str(error))

    if reviewed:
        return _approve_existing_review(
            source=source,
            destination=destination,
            map_path=map_path,
            review_path=review_path,
            batch=batch,
        )

    try:
        invalidate_existing_redaction(
            source=source,
            destination=destination,
            map_path=map_path,
            review_path=review_path,
            batch=batch,
            exit_code=EXIT_UNREADABLE,
        )
    except RedactionInvalidationError as error:
        return BatchResult(exit_code=EXIT_UNSAFE_PATH, message=str(error))

    def early_failure(exit_code: int, message: str) -> BatchResult:
        try:
            invalidate_existing_redaction(
                source=source,
                destination=destination,
                map_path=map_path,
                review_path=review_path,
                batch=batch,
                exit_code=exit_code,
            )
        except RedactionInvalidationError as error:
            return BatchResult(exit_code=EXIT_UNSAFE_PATH, message=str(error))
        return BatchResult(exit_code=exit_code, message=message)

    if has_symlink_component(source) or has_symlink_component(destination):
        return early_failure(
            EXIT_UNSAFE_PATH,
            "Submission and redacted-output roots must not be symlinks.",
        )
    if paths_overlap(source, destination):
        return early_failure(
            EXIT_UNSAFE_PATH,
            "Submission and redacted-output directories must be separate.",
        )
    if map_path == review_path or map_path.resolve() == review_path.resolve():
        return early_failure(
            EXIT_UNSAFE_PATH,
            "The token map and review file must use separate paths.",
        )
    for label, private_path in (("token map", map_path), ("review file", review_path)):
        if has_symlink_component(private_path) or any(
            is_within(private_path, root, follow_symlinks=follow)
            for root in (source, destination)
            for follow in (False, True)
        ):
            return early_failure(
                EXIT_UNSAFE_PATH,
                (
                    f"The {label} must be a regular local file outside the "
                    "submission and redacted-output directories."
                ),
            )

    source_paths = sorted(
        p for p in source.rglob("*") if p.is_file() or p.is_symlink()
    )
    if len(source_paths) > MAX_BATCH_FILES:
        return early_failure(
            EXIT_UNREADABLE,
            f"A grading batch may contain at most {MAX_BATCH_FILES} files.",
        )
    try:
        batch_bytes = sum(path.lstat().st_size for path in source_paths)
    except OSError as error:
        return early_failure(EXIT_UNREADABLE, str(error))
    if batch_bytes > MAX_BATCH_BYTES:
        return early_failure(
            EXIT_UNREADABLE,
            "The grading batch is too large. The input limit is 500 MiB.",
        )

    if destination.exists() and any(destination.iterdir()):
        if not force:
            return early_failure(
                EXIT_DESTINATION_NOT_EMPTY,
                (
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
    review_name_items = 0

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
        entry.source_sha256 = extracted.source_sha256
        if extracted.error:
            entry.error = extracted.error
            result.files.append(entry)
            continue

        if extracted.empty_body:
            entry.skipped_empty = True
            result.files.append(entry)
            continue

        # Metadata may identify the author, but it is not submission body and
        # must never enter the model-visible output. Scan it separately for
        # roster attribution and keep the original values only in the private
        # review report.
        text = extracted.text
        entry.metadata = list(extracted.metadata)
        metadata_text = "\n".join(extracted.metadata)
        attribution_text = "\n".join(
            value for value in (metadata_text, text) if value
        )

        # Attribution must be decided against the RAW text, before any name
        # becomes a token and before roster order can influence anything --
        # see _named_students.
        named = _named_students(attribution_text, roster)
        entry.named_students = [e.name for _, e in named]

        try:
            if metadata_text:
                metadata_roster = redact_roster(
                    metadata_text,
                    roster,
                    tmap,
                    path.name,
                    protected=protected,
                    bare_names=False,
                )
                matched_names |= metadata_roster.matched

            roster_result = redact_roster(
                text,
                roster,
                tmap,
                path.name,
                protected=protected,
                bare_names=bare_names,
            )
            matched_names |= roster_result.matched
            entry.bare_hits = roster_result.bare_hits

            text, _ = redact_structured(roster_result.text, tmap, path.name)
        except TokenMapLimitError as error:
            entry.error = f"Could not redact {path.name}: {error}"
            result.state_errors.append(str(error))
            result.files.append(entry)
            continue

        try:
            candidate_review = review_candidates(
                text,
                protected=protected,
                max_items=MAX_REVIEW_NAME_ITEMS - review_name_items,
            )
        except CandidateReviewLimitError as error:
            entry.error = f"Could not review {path.name}: {error}"
            result.state_errors.append(str(error))
            result.compact_review = True
            result.files.append(entry)
            break
        entry.candidates = list(candidate_review.candidates)
        entry.excluded_names = list(candidate_review.excluded)
        review_name_items += len(entry.candidates) + len(entry.excluded_names)
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
        output_bytes = text.encode("utf-8")
        if len(output_bytes) > MAX_FILE_BYTES:
            entry.error = (
                f"Could not write {path.name}: the redacted UTF-8 output exceeds "
                f"{MAX_FILE_BYTES} bytes."
            )
            result.state_errors.append(entry.error)
            result.files.append(entry)
            continue
        claimed_tokens[entry.token] = path
        write_private_text(output, text)
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
    elif any(f.candidates for f in result.files):
        result.exit_code = EXIT_CANDIDATES

    outputs = [file.output for file in result.files if file.output is not None]
    try:
        map_bytes = tmap.to_bytes()
    except TokenMapLimitError as error:
        result.exit_code = EXIT_UNREADABLE
        result.message = str(error)
        result.state_errors.append(str(error))
        tmap = TokenMap(batch, run_id=tmap.run_id)
        map_bytes = tmap.to_bytes()
    write_private_text(map_path, map_bytes.decode("utf-8"))
    map_sha256 = hashlib.sha256(map_bytes).hexdigest()
    binding = {
        "schema": REVIEW_SCHEMA,
        "batch": batch,
        "run_id": tmap.run_id,
        "sources": _result_source_binding(source, result.files),
        "outputs": _output_binding(outputs),
        "map_sha256": map_sha256,
    }
    review_bytes = _write_review(review_path, result, binding=binding)
    review_sha256 = hashlib.sha256(review_bytes).hexdigest()
    RedactionManifest.create(
        batch,
        tmap.run_id,
        outputs,
        reviewed=False,
        exit_code=result.exit_code,
        map_sha256=map_sha256,
        review_sha256=review_sha256,
    ).save(destination)

    return result


def _write_review(
    review_path: Path, result: BatchResult, *, binding: dict[str, object]
) -> bytes:
    """Write everything a human must look at before the cloud path, owner-only.

    This file is the one place unredacted candidate names are written to
    disk on purpose, so it is written owner-only, like the map, and it must
    never live inside the redacted output directory.
    """
    if result.compact_review:
        data = b'{"error":"candidate review item limit exceeded"}'
        write_private_text(review_path, data.decode("utf-8"))
        return data
    payload = {
        "binding": binding,
        "state_errors": result.state_errors,
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
        "excluded_names": [
            {
                "file": f.output.name if f.output else f.source.name,
                "name": item.name,
                "line": item.line,
                "context": item.context,
                "reason": item.reason,
            }
            for f in result.files
            for item in f.excluded_names
        ],
        "document_metadata": [
            {
                "file": f.output.name if f.output else f.source.name,
                "value": value,
            }
            for f in result.files
            for value in f.metadata
        ],
        "unmatched_roster": [
            {
                "name": entry.name,
                "student_id": entry.student_id,
                "email": entry.email,
            }
            for entry in result.unmatched_roster
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
    try:
        data = _bounded_json_bytes(payload, MAX_PRIVATE_ARTIFACT_BYTES)
    except PrivateReviewLimitError:
        message = "The private redaction review exceeded its byte limit."
        result.exit_code = EXIT_UNREADABLE
        result.message = message
        result.state_errors.append(message)
        data = b'{"error":"private review exceeded its byte limit"}'
        if len(data) > MAX_PRIVATE_ARTIFACT_BYTES:
            raise PrivateReviewLimitError(message)
    write_private_text(review_path, data.decode("utf-8"))
    return data


def _bounded_json_bytes(payload: object, max_bytes: int) -> bytes:
    """Encode JSON while retaining no more than max_bytes in memory."""
    encoded = bytearray()
    encoder = json.JSONEncoder(indent=2, sort_keys=True)
    for piece in encoder.iterencode(payload):
        chunk = piece.encode("utf-8")
        if len(encoded) + len(chunk) > max_bytes:
            raise PrivateReviewLimitError(
                "The private redaction review exceeded its byte limit."
            )
        encoded.extend(chunk)
    return bytes(encoded)
