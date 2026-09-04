"""Create, approve, and validate identity-free assignment briefs."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.path_safety import (
    has_symlink_component,
    read_bytes_limited,
    require_component,
)
from scripts.redaction.private_files import write_private_text

BRIEF_SCHEMA = "grading-assignment-brief/2"
LEGACY_BRIEF_SCHEMA = "grading-assignment-brief/1"
BRIEF_LIMIT = 1_048_576
EVIDENCE_LIMIT = 5 * 1024 * 1024
ANSWER_REQUIRED = "<!-- answer-required -->"
MODES = {"local", "cloud"}
PURPOSES = {"grading", "holdout"}
REVIEW_CADENCES = {"pilot", "every", "none"}
DEMO_BATCH = "synthetic-demo"
MAX_PILOT = 50
REQUIRED_HEADINGS = (
    "Assignment",
    "Grading priorities",
    "Required evidence",
    "Common mistakes",
    "Feedback",
    "Boundaries",
)

_METADATA = (
    "schema",
    "batch",
    "course",
    "status",
    "mode",
    "purpose",
    "review",
    "pilot",
    "rubric_sha256",
    "profile_sha256",
    "body_sha256",
    "approved_at",
    "approval",
)
_HASH = re.compile(r"^[0-9a-f]{64}$")
_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


class BriefError(ValueError):
    """The assignment brief is missing, malformed, unapproved, or stale."""


@dataclass(frozen=True)
class AssignmentBrief:
    """One parsed assignment brief and the artifacts it binds."""

    path: Path
    batch: str
    course: str
    status: str
    mode: str
    purpose: str
    review: str
    pilot: int
    rubric_sha256: str
    profile_sha256: str
    body_sha256: str
    approved_at: str
    approval: str
    body: str
    source_sha256: str = ""


def brief_path(root: Path, batch: str) -> Path:
    """Return the safe local path for one grading batch's brief."""
    require_component(batch, "The grading batch")
    return root / "assignments" / f"{batch}.md"


def prepare_brief(
    path: Path,
    *,
    batch: str,
    course: str,
    mode: str,
    purpose: str = "grading",
    review: str = "pilot",
    pilot: int = 2,
) -> AssignmentBrief:
    """Create one owner-only draft without replacing an existing brief."""
    _safe_component(batch, "The grading batch")
    _safe_component(course, "The course")
    _validate_mode(mode)
    _validate_review(purpose=purpose, review=review, pilot=pilot, mode=mode, batch=batch)
    _reject_symlink_path(path)
    text = _render(
        batch=batch,
        course=course,
        status="draft",
        mode=mode,
        purpose=purpose,
        review=review,
        pilot=pilot,
        rubric_sha256="",
        profile_sha256="",
        body_sha256="",
        approved_at="",
        approval="",
        body=_draft_body(batch, mode, purpose, review, pilot),
    )
    _create_private_text(path, text)
    return _parse_text(path, text)


def approve_brief(
    path: Path,
    *,
    batch: str,
    course: str,
    rubric: Path,
    profile: Path,
    approval: str,
) -> AssignmentBrief:
    """Bind an answered brief to the current rubric and profile."""
    expected_batch = _safe_component(batch, "The grading batch")
    expected_course = _safe_component(course, "The course")
    brief = _load(path)
    _match_context(brief, batch=expected_batch, course=expected_course)
    _validate_approval(approval, expected_batch)
    if ANSWER_REQUIRED in brief.body:
        raise BriefError("The assignment brief still has answer-required markers.")

    approved = AssignmentBrief(
        path=path,
        batch=brief.batch,
        course=brief.course,
        status="approved",
        mode=brief.mode,
        purpose=brief.purpose,
        review=brief.review,
        pilot=brief.pilot,
        rubric_sha256=_digest_bytes(_read_bound_file(rubric, "The rubric")),
        profile_sha256=_digest_bytes(
            _read_bound_file(profile, "The calibration profile")
        ),
        body_sha256=_digest_text(brief.body),
        approved_at=datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
        approval=approval,
        body=brief.body,
    )
    _reject_symlink_path(path)
    write_private_text(path, _render_brief(approved))
    return _load(path)


def check_brief(
    path: Path,
    *,
    batch: str,
    course: str,
    rubric: Path,
    profile: Path,
) -> AssignmentBrief:
    """Return a current approved brief or explain why it is unsafe to use."""
    expected_batch = _safe_component(batch, "The grading batch")
    expected_course = _safe_component(course, "The course")
    brief = _load(path)
    _match_context(brief, batch=expected_batch, course=expected_course)
    if brief.status != "approved":
        raise BriefError("The assignment brief is not approved.")
    _validate_approval(brief.approval, expected_batch)
    if ANSWER_REQUIRED in brief.body:
        raise BriefError("The approved assignment brief has answer-required markers.")
    if brief.body_sha256 != _digest_text(brief.body):
        raise BriefError("The assignment brief body changed after approval.")
    if brief.rubric_sha256 != _digest_bytes(_read_bound_file(rubric, "The rubric")):
        raise BriefError("The rubric changed after assignment brief approval.")
    if brief.profile_sha256 != _digest_bytes(
        _read_bound_file(profile, "The calibration profile")
    ):
        raise BriefError("The calibration profile changed after assignment brief approval.")
    return brief


def load_brief(path: Path) -> AssignmentBrief:
    """Load a draft or approved assignment brief without changing it."""
    return _load(path)


def _load(path: Path) -> AssignmentBrief:
    _reject_symlink_path(path)
    try:
        source = read_bytes_limited(path, "The assignment brief", BRIEF_LIMIT)
        text = source.decode("utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        raise BriefError(str(error)) from error
    return _parse_text(path, text, source_sha256=_digest_bytes(source))


def _parse_text(
    path: Path, text: str, *, source_sha256: str = ""
) -> AssignmentBrief:
    metadata, body = _split_front_matter(_normalize_line_endings(text))
    pilot = _validate_metadata(metadata)
    _validate_body(
        body,
        mode=metadata["mode"],
        purpose=metadata["purpose"],
        review=metadata["review"],
        pilot=pilot,
    )
    return AssignmentBrief(
        path=path,
        batch=metadata["batch"],
        course=metadata["course"],
        status=metadata["status"],
        mode=metadata["mode"],
        purpose=metadata["purpose"],
        review=metadata["review"],
        pilot=pilot,
        rubric_sha256=metadata["rubric_sha256"],
        profile_sha256=metadata["profile_sha256"],
        body_sha256=metadata["body_sha256"],
        approved_at=metadata["approved_at"],
        approval=metadata["approval"],
        body=body,
        source_sha256=source_sha256 or _digest_bytes(text.encode("utf-8")),
    )


def _normalize_line_endings(value: str) -> str:
    """Use one newline form for structure and body hashes."""
    return value.replace("\r\n", "\n").replace("\r", "\n")


def _split_front_matter(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        raise BriefError("The assignment brief must begin with front matter.")
    closing = next(
        (index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"),
        None,
    )
    if closing is None:
        raise BriefError("The assignment brief front matter has no closing delimiter.")

    metadata: dict[str, str] = {}
    for raw_line in lines[1:closing]:
        line = raw_line.rstrip("\r\n")
        key, separator, value = line.partition(":")
        if not separator or not key or key.strip() != key:
            raise BriefError("The assignment brief has malformed front matter.")
        if key in metadata:
            raise BriefError(f"The assignment brief has duplicate metadata key '{key}'.")
        if key not in _METADATA:
            raise BriefError(f"The assignment brief has unknown metadata key '{key}'.")
        metadata[key] = value.lstrip(" ")

    missing = [key for key in _METADATA if key not in metadata]
    if missing:
        raise BriefError(
            "The assignment brief is missing metadata: " + ", ".join(missing) + "."
        )
    return metadata, "".join(lines[closing + 1 :])


def _validate_metadata(metadata: dict[str, str]) -> int:
    """Validate the front matter and return the parsed pilot size."""
    if metadata["schema"] == LEGACY_BRIEF_SCHEMA:
        raise BriefError(
            "The assignment brief uses schema 1. Prepare and approve a new brief "
            "with brief prepare."
        )
    if metadata["schema"] != BRIEF_SCHEMA:
        raise BriefError(f"The assignment brief must use schema {BRIEF_SCHEMA}.")
    _safe_component(metadata["batch"], "The grading batch")
    _safe_component(metadata["course"], "The course")
    if metadata["status"] not in {"draft", "approved"}:
        raise BriefError("The assignment brief status must be draft or approved.")
    _validate_mode(metadata["mode"])
    try:
        pilot = int(metadata["pilot"])
    except ValueError as error:
        raise BriefError("The assignment brief pilot size must be an integer.") from error
    _validate_review(
        purpose=metadata["purpose"],
        review=metadata["review"],
        pilot=pilot,
        mode=metadata["mode"],
        batch=metadata["batch"],
    )
    if metadata["status"] == "approved":
        for field in ("rubric_sha256", "profile_sha256", "body_sha256"):
            if not _HASH.fullmatch(metadata[field]):
                raise BriefError(f"The approved {field} must be a lowercase SHA-256 hash.")
        _validate_timestamp(metadata["approved_at"])
        _validate_approval(metadata["approval"], metadata["batch"])
    else:
        for field in ("rubric_sha256", "profile_sha256", "body_sha256"):
            if metadata[field] and not _HASH.fullmatch(metadata[field]):
                raise BriefError(f"The draft {field} must be empty or a lowercase SHA-256 hash.")
        if metadata["approved_at"]:
            _validate_timestamp(metadata["approved_at"])
    return pilot


def _validate_review(
    *, purpose: str, review: str, pilot: int, mode: str, batch: str
) -> None:
    if purpose not in PURPOSES:
        raise BriefError("The assignment brief purpose must be grading or holdout.")
    if review not in REVIEW_CADENCES:
        raise BriefError(
            "The assignment brief review cadence must be pilot, every, or none."
        )
    if not isinstance(pilot, int) or isinstance(pilot, bool) or not 1 <= pilot <= MAX_PILOT:
        raise BriefError(
            f"The assignment brief pilot size must be from 1 to {MAX_PILOT}."
        )
    if purpose == "holdout":
        if mode != "local":
            raise BriefError(
                "A holdout brief must use local mode. Calibration reads past "
                "students' names."
            )
        if review != "none":
            raise BriefError(
                "A holdout brief uses review cadence none. The holdout report is "
                "the review."
            )
    elif review == "none" and batch != DEMO_BATCH:
        raise BriefError(
            "Review cadence none is reserved for the synthetic demo batch. Use "
            "pilot or every."
        )


_BOUNDARY_LINES = (
    ("Host mode", "mode", r"(local|cloud)"),
    ("Purpose", "purpose", r"(grading|holdout)"),
    ("Review cadence", "review", r"(pilot|every|none)"),
    ("Pilot size", "pilot", r"(\d+)"),
)


def _validate_body(
    body: str, *, mode: str, purpose: str, review: str, pilot: int
) -> None:
    headings = set(_HEADING.findall(body))
    missing = [heading for heading in REQUIRED_HEADINGS if heading not in headings]
    if missing:
        raise BriefError(
            "The assignment brief is missing headings: " + ", ".join(missing) + "."
        )
    boundaries = _section_body(body, "Boundaries")
    expected = {
        "mode": mode,
        "purpose": purpose,
        "review": review,
        "pilot": str(pilot),
    }
    for label, field, pattern in _BOUNDARY_LINES:
        found = re.findall(rf"^{label}: {pattern}$", boundaries, re.MULTILINE)
        if found != [expected[field]]:
            raise BriefError(
                f"The Boundaries section must contain one {label} line that "
                f"matches {field}."
            )


def _section_body(body: str, heading: str) -> str:
    match = re.search(
        rf"^## {re.escape(heading)}\s*$\n(.*?)(?=^##\s|\Z)",
        body,
        re.MULTILINE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _validate_mode(mode: str) -> None:
    if mode not in MODES:
        raise BriefError("The assignment brief mode must be local or cloud.")


def _validate_timestamp(value: str) -> None:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise BriefError("The approved_at value must be an ISO-8601 UTC timestamp.") from error
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise BriefError("The approved_at value must be an ISO-8601 UTC timestamp.")


def _validate_approval(value: str, batch: str) -> None:
    expected = f"Approve assignment brief for {batch}"
    if value != expected:
        raise BriefError(f"Approval must read exactly: {expected}")


def _match_context(brief: AssignmentBrief, *, batch: str, course: str) -> None:
    if brief.batch != batch:
        raise BriefError("The assignment brief batch does not match the grading batch.")
    if brief.course != course:
        raise BriefError("The assignment brief course does not match the course.")


def _safe_component(value: str, label: str) -> str:
    try:
        return require_component(value, label)
    except ValueError as error:
        raise BriefError(str(error)) from error


def _reject_symlink_path(path: Path) -> None:
    if has_symlink_component(path):
        raise BriefError("The assignment brief path must not use a symbolic link.")


def _read_bound_file(path: Path, label: str) -> bytes:
    try:
        return read_bytes_limited(path, label, EVIDENCE_LIMIT)
    except (OSError, ValueError) as error:
        raise BriefError(str(error)) from error


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8"))


def _digest_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _draft_body(batch: str, mode: str, purpose: str, review: str, pilot: int) -> str:
    sections = []
    for heading in REQUIRED_HEADINGS:
        content = ANSWER_REQUIRED
        if heading == "Boundaries":
            content = (
                f"Host mode: {mode}\nPurpose: {purpose}\nReview cadence: {review}\n"
                f"Pilot size: {pilot}\n\n{ANSWER_REQUIRED}"
            )
        sections.append(f"## {heading}\n\n{content}")
    rendered = "\n\n".join(sections)
    return f"# Assignment brief: {batch}\n\n{rendered}\n"


def _render_brief(brief: AssignmentBrief) -> str:
    return _render(
        batch=brief.batch,
        course=brief.course,
        status=brief.status,
        mode=brief.mode,
        purpose=brief.purpose,
        review=brief.review,
        pilot=brief.pilot,
        rubric_sha256=brief.rubric_sha256,
        profile_sha256=brief.profile_sha256,
        body_sha256=brief.body_sha256,
        approved_at=brief.approved_at,
        approval=brief.approval,
        body=brief.body,
    )


def _render(
    *,
    batch: str,
    course: str,
    status: str,
    mode: str,
    purpose: str,
    review: str,
    pilot: int,
    rubric_sha256: str,
    profile_sha256: str,
    body_sha256: str,
    approved_at: str,
    approval: str,
    body: str,
) -> str:
    values = {
        "schema": BRIEF_SCHEMA,
        "batch": batch,
        "course": course,
        "status": status,
        "mode": mode,
        "purpose": purpose,
        "review": review,
        "pilot": str(pilot),
        "rubric_sha256": rubric_sha256,
        "profile_sha256": profile_sha256,
        "body_sha256": body_sha256,
        "approved_at": approved_at,
        "approval": approval,
    }
    front_matter = "\n".join(f"{key}: {values[key]}" for key in _METADATA)
    return f"---\n{front_matter}\n---\n{body}"


def _create_private_text(path: Path, text: str) -> None:
    if path.exists() or path.is_symlink():
        raise BriefError(f"The assignment brief already exists at {path}.")
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_path(path)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        try:
            os.link(temporary, path)
        except FileExistsError as error:
            raise BriefError(f"The assignment brief already exists at {path}.") from error
    finally:
        if temporary.exists():
            temporary.unlink()
