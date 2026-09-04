"""Record the instructor's review of drafts and consistency flags.

Both records are identity-free: tokens, digests, phrases, and times. Neither
authenticates a person. They let the workflow prove that a stop happened and
that nothing changed after it.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.path_safety import (
    has_symlink_component,
    read_bytes_limited,
    require_component,
)
from scripts.redaction.private_files import write_private_text

REVIEW_NAME = "_review.json"
REVIEW_SCHEMA = "grading-draft-review/1"
ACK_NAME = "_consistency.json"
ACK_SCHEMA = "grading-consistency-acknowledgement/1"
SCOPES = {"pilot", "draft"}
FILE_LIMIT = 1_048_576
REVIEW_CODES = frozenset(
    {
        "draft-review-required",
        "draft-review-stale",
        "pilot-size-mismatch",
        "malformed-draft-review",
    }
)
_HEX = frozenset("0123456789abcdef")


class ReviewError(ValueError):
    """The review record or acknowledgement is malformed, mismatched, or unapproved."""


def pilot_phrase(batch: str) -> str:
    """Return the exact message that approves the pilot drafts."""
    return f"Approve pilot drafts for {batch}"


def draft_phrase(token: str, batch: str) -> str:
    """Return the exact message that approves one draft."""
    return f"Approve draft {token} for {batch}"


def ack_phrase(batch: str) -> str:
    """Return the exact message that acknowledges the consistency flags."""
    return f"Acknowledge consistency flags for {batch}"


def feedback_digest(text: str) -> str:
    """Digest feedback text the same way everywhere."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def review_path(drafts: Path) -> Path:
    return drafts / REVIEW_NAME


def ack_path(drafts: Path) -> Path:
    return drafts / ACK_NAME


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _hex(value: object, name: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise ReviewError(f"{name} must be a lowercase SHA-256 hash.")
    return value


def _token(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise ReviewError("Every reviewed token must be text.")
    require_component(value, "The token")
    return value


@dataclass(frozen=True)
class DraftArtifacts:
    """What one drafted pair looked like when the instructor approved it."""

    token: str
    feedback_sha256: str
    grade_sha256: str


@dataclass(frozen=True)
class ReviewApproval:
    scope: str
    tokens: tuple[str, ...]
    artifacts: tuple[DraftArtifacts, ...]
    approval: str
    approved_at: str


@dataclass(frozen=True)
class DraftReview:
    path: Path
    batch: str
    course: str
    cadence: str
    pilot: int
    approvals: tuple[ReviewApproval, ...]

    def pilot_approval(self) -> ReviewApproval | None:
        """Return the latest pilot approval, if any."""
        return next(
            (item for item in reversed(self.approvals) if item.scope == "pilot"), None
        )

    def draft_approval(self, token: str) -> ReviewApproval | None:
        """Return the latest per-draft approval covering this token, if any."""
        return next(
            (
                item
                for item in reversed(self.approvals)
                if item.scope == "draft" and token in item.tokens
            ),
            None,
        )


@dataclass(frozen=True)
class ReviewFinding:
    code: str
    message: str
    token: str


def _read_json(path: Path, label: str) -> dict | None:
    if path.is_symlink() or has_symlink_component(path):
        raise ReviewError(f"{label} must not use a symbolic link.")
    if not path.exists():
        return None
    if not path.is_file():
        raise ReviewError(f"{label} must be one regular file.")
    try:
        payload = json.loads(read_bytes_limited(path, label, FILE_LIMIT).decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise ReviewError(f"{label} is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise ReviewError(f"{label} must be a JSON object.")
    return payload


def _write_json(path: Path, payload: Mapping[str, object], label: str) -> None:
    if has_symlink_component(path):
        raise ReviewError(f"{label} path must not use a symbolic link.")
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _parse_approval(item: object) -> ReviewApproval:
    if not isinstance(item, dict):
        raise ReviewError("Every review approval must be a JSON object.")
    scope = item.get("scope")
    if scope not in SCOPES:
        raise ReviewError("A review approval scope must be pilot or draft.")
    tokens = item.get("tokens")
    artifacts = item.get("artifacts")
    if not isinstance(tokens, list) or not isinstance(artifacts, list) or not tokens:
        raise ReviewError("A review approval needs tokens and artifacts lists.")
    parsed = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise ReviewError("Every review artifact must be a JSON object.")
        parsed.append(
            DraftArtifacts(
                _token(artifact.get("token")),
                _hex(artifact.get("feedback_sha256"), "feedback_sha256"),
                _hex(artifact.get("grade_sha256"), "grade_sha256"),
            )
        )
    if tuple(_token(token) for token in tokens) != tuple(a.token for a in parsed):
        raise ReviewError("A review approval's tokens and artifacts disagree.")
    approval = item.get("approval")
    if not isinstance(approval, str):
        raise ReviewError("A review approval needs its phrase.")
    return ReviewApproval(
        scope,
        tuple(a.token for a in parsed),
        tuple(parsed),
        approval,
        str(item.get("approved_at", "")),
    )


def load_review(path: Path, *, batch: str, course: str) -> DraftReview | None:
    """Read the review record, or return None when there is none yet."""
    payload = _read_json(path, "The draft review record")
    if payload is None:
        return None
    if payload.get("schema") != REVIEW_SCHEMA:
        raise ReviewError(f"The draft review record must use schema {REVIEW_SCHEMA}.")
    if payload.get("batch") != batch or payload.get("course") != course:
        raise ReviewError(
            "The draft review record belongs to another grading batch or course."
        )
    cadence = payload.get("cadence")
    pilot = payload.get("pilot")
    if cadence not in {"pilot", "every"} or not isinstance(pilot, int) or isinstance(pilot, bool):
        raise ReviewError(
            "The draft review record needs a cadence and an integer pilot size."
        )
    approvals = payload.get("approvals")
    if not isinstance(approvals, list):
        raise ReviewError("The draft review record approvals must be a list.")
    review = DraftReview(
        path, batch, course, cadence, pilot, tuple(_parse_approval(a) for a in approvals)
    )
    for approval in review.approvals:
        expected = (
            pilot_phrase(batch)
            if approval.scope == "pilot"
            else draft_phrase(approval.tokens[0], batch)
        )
        if approval.approval != expected:
            raise ReviewError(f"A review approval must read exactly: {expected}")
    return review


def _write_review(review: DraftReview) -> DraftReview:
    payload = {
        "schema": REVIEW_SCHEMA,
        "batch": review.batch,
        "course": review.course,
        "cadence": review.cadence,
        "pilot": review.pilot,
        "approvals": [
            {
                "scope": item.scope,
                "tokens": list(item.tokens),
                "artifacts": [
                    {
                        "token": artifact.token,
                        "feedback_sha256": artifact.feedback_sha256,
                        "grade_sha256": artifact.grade_sha256,
                    }
                    for artifact in item.artifacts
                ],
                "approval": item.approval,
                "approved_at": item.approved_at,
            }
            for item in review.approvals
        ],
    }
    _write_json(review.path, payload, "The draft review record")
    loaded = load_review(review.path, batch=review.batch, course=review.course)
    if loaded is None:
        raise ReviewError("The draft review record vanished after it was written.")
    return loaded


def approve_review(
    path: Path,
    *,
    batch: str,
    course: str,
    cadence: str,
    pilot: int,
    scope: str,
    artifacts: Sequence[DraftArtifacts],
    approval: str,
) -> DraftReview:
    """Append one approval bound to the drafts as they are right now."""
    require_component(batch, "The grading batch")
    require_component(course, "The course")
    if scope not in SCOPES:
        raise ReviewError("The review scope must be pilot or draft.")
    if cadence == "none":
        raise ReviewError("Review cadence none has no draft approvals.")
    if (scope == "pilot") != (cadence == "pilot"):
        raise ReviewError(
            f"The brief's review cadence is {cadence}; a {scope} approval does "
            "not fit it."
        )
    if not artifacts:
        raise ReviewError("A review approval needs at least one drafted token.")
    if scope == "draft" and len(artifacts) != 1:
        raise ReviewError("A draft approval covers exactly one token.")
    tokens = tuple(artifact.token for artifact in artifacts)
    expected = pilot_phrase(batch) if scope == "pilot" else draft_phrase(tokens[0], batch)
    if approval != expected:
        raise ReviewError(f"Approval must read exactly: {expected}")
    existing = load_review(path, batch=batch, course=course)
    if existing is not None and (existing.cadence != cadence or existing.pilot != pilot):
        raise ReviewError(
            "The draft review record was created for a different cadence or "
            "pilot size."
        )
    approvals = existing.approvals if existing is not None else ()
    entry = ReviewApproval(scope, tokens, tuple(artifacts), approval, _now())
    return _write_review(DraftReview(path, batch, course, cadence, pilot, approvals + (entry,)))


def review_findings(
    *,
    cadence: str,
    pilot: int,
    total: int,
    drafted: Mapping[str, DraftArtifacts],
    review: DraftReview | None,
    batch: str,
) -> tuple[ReviewFinding, ...]:
    """Say what still stands between these drafts and verification."""
    if cadence == "none":
        return ()
    if review is not None and (review.cadence != cadence or review.pilot != pilot):
        return (
            ReviewFinding(
                "malformed-draft-review",
                "The draft review record does not match the brief's cadence or "
                "pilot size.",
                "",
            ),
        )
    findings: list[ReviewFinding] = []
    if cadence == "pilot":
        approval = review.pilot_approval() if review is not None else None
        if approval is None:
            return (
                ReviewFinding(
                    "draft-review-required",
                    "The instructor must approve the pilot drafts. Wait for: "
                    + pilot_phrase(batch),
                    "",
                ),
            )
        expected = min(pilot, total)
        if len(approval.tokens) != expected:
            findings.append(
                ReviewFinding(
                    "pilot-size-mismatch",
                    f"The pilot approval covers {len(approval.tokens)} drafts; the "
                    f"brief asks for {expected}.",
                    "",
                )
            )
        for artifact in approval.artifacts:
            if drafted.get(artifact.token) != artifact:
                findings.append(
                    ReviewFinding(
                        "draft-review-stale",
                        "This pilot draft changed after approval. Show it again and "
                        "wait for the phrase.",
                        artifact.token,
                    )
                )
        return tuple(findings)
    for token in sorted(drafted):
        approval = review.draft_approval(token) if review is not None else None
        if approval is None:
            findings.append(
                ReviewFinding(
                    "draft-review-required",
                    "The instructor must approve this draft. Wait for: "
                    + draft_phrase(token, batch),
                    token,
                )
            )
            continue
        bound = next(a for a in approval.artifacts if a.token == token)
        if drafted[token] != bound:
            findings.append(
                ReviewFinding(
                    "draft-review-stale",
                    "This draft changed after approval. Show it again and wait for "
                    "the phrase.",
                    token,
                )
            )
    return tuple(findings)


@dataclass(frozen=True)
class Acknowledgement:
    path: Path
    batch: str
    course: str
    receipt_sha256: str
    flag_count: int
    flags: tuple[dict, ...]
    approval: str
    acknowledged_at: str


def write_acknowledgement(
    path: Path,
    *,
    batch: str,
    course: str,
    receipt_sha256: str,
    flags: Sequence[tuple[str, str]],
    approval: str,
) -> Acknowledgement:
    """Record that the instructor saw these flags for this exact receipt."""
    require_component(batch, "The grading batch")
    require_component(course, "The course")
    if approval != ack_phrase(batch):
        raise ReviewError(f"Acknowledgement must read exactly: {ack_phrase(batch)}")
    payload = {
        "schema": ACK_SCHEMA,
        "batch": batch,
        "course": course,
        "receipt_sha256": _hex(receipt_sha256, "receipt_sha256"),
        "flag_count": len(flags),
        "flags": [{"kind": kind, "message": message} for kind, message in flags],
        "approval": approval,
        "acknowledged_at": _now(),
    }
    _write_json(path, payload, "The consistency acknowledgement")
    loaded = load_acknowledgement(path, batch=batch, course=course)
    if loaded is None:
        raise ReviewError("The consistency acknowledgement vanished after it was written.")
    return loaded


def load_acknowledgement(
    path: Path, *, batch: str, course: str
) -> Acknowledgement | None:
    """Read the acknowledgement, or return None when there is none yet."""
    payload = _read_json(path, "The consistency acknowledgement")
    if payload is None:
        return None
    if payload.get("schema") != ACK_SCHEMA:
        raise ReviewError(f"The consistency acknowledgement must use schema {ACK_SCHEMA}.")
    if payload.get("batch") != batch or payload.get("course") != course:
        raise ReviewError(
            "The consistency acknowledgement belongs to another grading batch or course."
        )
    if payload.get("approval") != ack_phrase(batch):
        raise ReviewError(
            f"The consistency acknowledgement must read exactly: {ack_phrase(batch)}"
        )
    flags = payload.get("flags")
    if not isinstance(flags, list) or not all(isinstance(flag, dict) for flag in flags):
        raise ReviewError("The consistency acknowledgement flags must be a list of objects.")
    count = payload.get("flag_count")
    if not isinstance(count, int) or isinstance(count, bool) or count != len(flags):
        raise ReviewError("The consistency acknowledgement flag count is wrong.")
    return Acknowledgement(
        path,
        batch,
        course,
        _hex(payload.get("receipt_sha256"), "receipt_sha256"),
        count,
        tuple(flags),
        payload["approval"],
        str(payload.get("acknowledged_at", "")),
    )
