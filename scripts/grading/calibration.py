"""Bind a profile to the holdouts that measured it and the instructor who approved it.

The receipt is identity-free: digests, counts, and numbers. The approval binds
the exact profile, rubric, and receipt bytes, so any edit needs approval again.
Neither file authenticates a person. They are workflow evidence, like the
assignment brief.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.grading.holdout import Aggregate, Delta
from scripts.path_safety import (
    has_symlink_component,
    read_bytes_limited,
    require_component,
)
from scripts.redaction.private_files import write_private_text

RECEIPT_SCHEMA = "calibration-receipt/1"
APPROVAL_SCHEMA = "calibration-approval/1"
DEMO_MARKER = "demo.json"
DEMO_AUTHORIZATION = "synthetic-demo"
MIN_SAMPLES = 3
FILE_LIMIT = 1_048_576
BOUND_LIMIT = 5 * 1024 * 1024
_HEX = frozenset("0123456789abcdef")


class CalibrationError(ValueError):
    """The calibration receipt or approval is missing, malformed, or stale."""


def receipt_path(root: Path, course: str) -> Path:
    """Return where a course's holdout receipt lives."""
    require_component(course, "The course")
    return root / "calibration" / f"{course}.receipt.json"


def approval_path(root: Path, course: str) -> Path:
    """Return where a course's profile approval lives."""
    require_component(course, "The course")
    return root / "calibration" / f"{course}.approval.json"


def approval_phrase(course: str) -> str:
    """Return the exact message the instructor must send."""
    return f"Approve calibration profile for {course}"


def _now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def _digest(path: Path, label: str) -> str:
    if has_symlink_component(path):
        raise CalibrationError(f"{label} must not use a symbolic link.")
    try:
        return hashlib.sha256(read_bytes_limited(path, label, BOUND_LIMIT)).hexdigest()
    except (OSError, ValueError) as error:
        raise CalibrationError(str(error)) from error


def _hex(value: object, name: str, *, allow_empty: bool = False) -> str:
    if allow_empty and value == "":
        return ""
    if not isinstance(value, str) or len(value) != 64 or set(value) - _HEX:
        raise CalibrationError(f"{name} must be a lowercase SHA-256 hash.")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise CalibrationError(f"{name} must be a non-negative integer.")
    return value


def _write_json(path: Path, payload: Mapping[str, object], label: str) -> None:
    if has_symlink_component(path):
        raise CalibrationError(f"{label} path must not use a symbolic link.")
    write_private_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _read_json(path: Path, label: str) -> tuple[dict, bytes]:
    if has_symlink_component(path) or not path.is_file():
        raise CalibrationError(f"{label} must be one regular file.")
    try:
        raw = read_bytes_limited(path, label, FILE_LIMIT)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise CalibrationError(f"{label} is unreadable: {error}") from error
    if not isinstance(payload, dict):
        raise CalibrationError(f"{label} must be a JSON object.")
    return payload, raw


@dataclass(frozen=True)
class CalibrationReceipt:
    """The identity-free result of measuring one profile against its holdouts."""

    path: Path
    course: str
    profile_sha256: str
    rubric_sha256: str
    packet_sha256: str
    samples: int
    required_holdouts: int
    holdouts: tuple[dict, ...]
    aggregate: dict
    tolerances: dict
    passed: bool
    created_at: str
    source_sha256: str

    @property
    def findings(self) -> tuple[str, ...]:
        """Every holdout finding, in holdout order."""
        return tuple(
            str(finding)
            for item in self.holdouts
            for finding in item.get("findings", ())
        )


def write_receipt(
    path: Path,
    *,
    course: str,
    profile_sha256: str,
    rubric_sha256: str,
    packet_sha256: str,
    samples: int,
    deltas: Sequence[Delta],
    summary: Aggregate,
    tolerances: Mapping[str, object],
) -> CalibrationReceipt:
    """Write the receipt for one holdout run. Shared phrases become counts."""
    require_component(course, "The course")
    holdouts = []
    for delta in deltas:
        item = asdict(delta)
        item["shared_phrases"] = len(delta.shared_phrases)
        item["findings"] = list(delta.findings)
        holdouts.append(item)
    aggregate = {
        key: value
        for key, value in asdict(summary).items()
        if key not in {"required", "passed"}
    }
    payload = {
        "schema": RECEIPT_SCHEMA,
        "course": course,
        "hash": "sha256",
        "profile_sha256": _hex(profile_sha256, "profile_sha256"),
        "rubric_sha256": _hex(rubric_sha256, "rubric_sha256"),
        "packet_sha256": _hex(packet_sha256, "packet_sha256"),
        "samples": samples,
        "required_holdouts": summary.required,
        "holdouts": holdouts,
        "aggregate": aggregate,
        "tolerances": dict(tolerances),
        "passed": summary.passed,
        "created_at": _now(),
    }
    _write_json(path, payload, "The calibration receipt")
    return load_receipt(path)


def load_receipt(path: Path) -> CalibrationReceipt:
    """Read a receipt and refuse anything malformed."""
    payload, raw = _read_json(path, "The calibration receipt")
    if payload.get("schema") != RECEIPT_SCHEMA:
        raise CalibrationError(
            f"The calibration receipt must use schema {RECEIPT_SCHEMA}."
        )
    course = payload.get("course")
    if not isinstance(course, str):
        raise CalibrationError("The calibration receipt needs a course.")
    require_component(course, "The course")
    holdouts = payload.get("holdouts")
    aggregate = payload.get("aggregate")
    tolerances = payload.get("tolerances")
    if not isinstance(holdouts, list) or not all(
        isinstance(item, dict) for item in holdouts
    ):
        raise CalibrationError(
            "The calibration receipt holdouts must be a list of objects."
        )
    if not isinstance(aggregate, dict) or not isinstance(tolerances, dict):
        raise CalibrationError(
            "The calibration receipt needs aggregate and tolerances objects."
        )
    if not isinstance(payload.get("passed"), bool):
        raise CalibrationError("The calibration receipt passed value must be a boolean.")
    return CalibrationReceipt(
        path=path,
        course=course,
        profile_sha256=_hex(payload.get("profile_sha256"), "profile_sha256"),
        rubric_sha256=_hex(payload.get("rubric_sha256"), "rubric_sha256"),
        packet_sha256=_hex(payload.get("packet_sha256"), "packet_sha256"),
        samples=_integer(payload.get("samples"), "samples"),
        required_holdouts=_integer(
            payload.get("required_holdouts"), "required_holdouts"
        ),
        holdouts=tuple(holdouts),
        aggregate=aggregate,
        tolerances=tolerances,
        passed=payload["passed"],
        created_at=str(payload.get("created_at", "")),
        source_sha256=hashlib.sha256(raw).hexdigest(),
    )


@dataclass(frozen=True)
class CalibrationApproval:
    """The instructor's exact-phrase approval, bound by digests."""

    path: Path
    course: str
    profile_sha256: str
    rubric_sha256: str
    receipt_sha256: str
    approval: str
    approved_at: str
    gap_accepted: bool
    reason: str
    synthetic: bool


def _write_approval(
    path: Path,
    *,
    course: str,
    profile_sha256: str,
    rubric_sha256: str,
    receipt_sha256: str,
    gap_accepted: bool,
    reason: str,
    synthetic: bool,
) -> CalibrationApproval:
    payload = {
        "schema": APPROVAL_SCHEMA,
        "course": course,
        "hash": "sha256",
        "profile_sha256": profile_sha256,
        "rubric_sha256": rubric_sha256,
        "receipt_sha256": receipt_sha256,
        "approval": approval_phrase(course),
        "approved_at": _now(),
        "gap_accepted": gap_accepted,
        "reason": reason,
        "synthetic": synthetic,
    }
    _write_json(path, payload, "The calibration approval")
    return load_approval(path)


def load_approval(path: Path) -> CalibrationApproval:
    """Read an approval and refuse anything malformed."""
    payload, _ = _read_json(path, "The calibration approval")
    if payload.get("schema") != APPROVAL_SCHEMA:
        raise CalibrationError(
            f"The calibration approval must use schema {APPROVAL_SCHEMA}."
        )
    course = payload.get("course")
    if not isinstance(course, str):
        raise CalibrationError("The calibration approval needs a course.")
    require_component(course, "The course")
    synthetic = payload.get("synthetic")
    gap_accepted = payload.get("gap_accepted")
    if not isinstance(synthetic, bool) or not isinstance(gap_accepted, bool):
        raise CalibrationError("The calibration approval flags must be booleans.")
    if payload.get("approval") != approval_phrase(course):
        raise CalibrationError(
            f"The calibration approval must read exactly: {approval_phrase(course)}"
        )
    reason = payload.get("reason", "")
    if not isinstance(reason, str):
        raise CalibrationError("The calibration approval reason must be text.")
    return CalibrationApproval(
        path=path,
        course=course,
        profile_sha256=_hex(payload.get("profile_sha256"), "profile_sha256"),
        rubric_sha256=_hex(payload.get("rubric_sha256"), "rubric_sha256"),
        receipt_sha256=_hex(
            payload.get("receipt_sha256"), "receipt_sha256", allow_empty=synthetic
        ),
        approval=payload["approval"],
        approved_at=str(payload.get("approved_at", "")),
        gap_accepted=gap_accepted,
        reason=reason,
        synthetic=synthetic,
    )


def approve_calibration(
    root: Path,
    *,
    course: str,
    profile: Path,
    rubric: Path,
    approval: str,
    accept_gap: bool = False,
    reason: str = "",
) -> CalibrationApproval:
    """Bind the instructor's exact phrase to the current profile, rubric, and receipt."""
    require_component(course, "The course")
    if approval != approval_phrase(course):
        raise CalibrationError(f"Approval must read exactly: {approval_phrase(course)}")
    profile_sha256 = _digest(profile, "The calibration profile")
    rubric_sha256 = _digest(rubric, "The rubric")
    receipt = load_receipt(receipt_path(root, course))
    if receipt.course != course:
        raise CalibrationError("The calibration receipt belongs to another course.")
    if (
        receipt.profile_sha256 != profile_sha256
        or receipt.rubric_sha256 != rubric_sha256
    ):
        raise CalibrationError(
            "The calibration receipt does not match the current profile and "
            "rubric. Run the holdouts again."
        )
    if receipt.samples < MIN_SAMPLES:
        raise CalibrationError(
            f"The calibration receipt records {receipt.samples} samples. "
            f"Calibration needs at least {MIN_SAMPLES}."
        )
    if len(receipt.holdouts) < receipt.required_holdouts:
        raise CalibrationError(
            f"The calibration receipt records {len(receipt.holdouts)} holdouts "
            f"of {receipt.required_holdouts} required."
        )
    if not receipt.passed:
        if not accept_gap:
            raise CalibrationError(
                "The holdouts did not pass. Edit the profile and run them again, "
                "or pass --accept-gap with a reason."
            )
        if not reason.strip():
            raise CalibrationError("Accepting a holdout gap needs a non-empty reason.")
    return _write_approval(
        approval_path(root, course),
        course=course,
        profile_sha256=profile_sha256,
        rubric_sha256=rubric_sha256,
        receipt_sha256=receipt.source_sha256,
        gap_accepted=not receipt.passed,
        reason=reason.strip() if not receipt.passed else "",
        synthetic=False,
    )


def approve_synthetic(
    root: Path, *, course: str, profile: Path, rubric: Path
) -> CalibrationApproval:
    """Record the bundled demo's synthetic approval. Only demo preparation calls this."""
    require_component(course, "The course")
    return _write_approval(
        approval_path(root, course),
        course=course,
        profile_sha256=_digest(profile, "The calibration profile"),
        rubric_sha256=_digest(rubric, "The rubric"),
        receipt_sha256="",
        gap_accepted=False,
        reason="",
        synthetic=True,
    )


def demo_authorized(root: Path) -> bool:
    """Say whether this state root was prepared by the synthetic demo."""
    marker = root / DEMO_MARKER
    if has_symlink_component(marker) or not marker.is_file():
        return False
    try:
        payload = json.loads(
            read_bytes_limited(marker, "The demo marker", FILE_LIMIT).decode("utf-8")
        )
    except (OSError, UnicodeError, ValueError):
        return False
    return isinstance(payload, dict) and payload.get("authorization") == DEMO_AUTHORIZATION


@dataclass(frozen=True)
class CalibrationStatus:
    """Whether a course may grade, and why not when it may not."""

    state: str
    message: str
    receipt: CalibrationReceipt | None = None
    approval: CalibrationApproval | None = None


def check_calibration(
    root: Path, *, course: str, profile: Path, rubric: Path
) -> CalibrationStatus:
    """Compare the receipt and approval against the current profile and rubric."""
    require_component(course, "The course")
    profile_sha256 = _digest(profile, "The calibration profile")
    rubric_sha256 = _digest(rubric, "The rubric")

    approval = None
    approval_file = approval_path(root, course)
    if approval_file.exists() or approval_file.is_symlink():
        try:
            approval = load_approval(approval_file)
        except CalibrationError as error:
            return CalibrationStatus("malformed-approval", str(error))
        if approval.course != course:
            return CalibrationStatus(
                "malformed-approval",
                "The calibration approval belongs to another course.",
            )
    if approval is not None and approval.synthetic:
        if not demo_authorized(root):
            return CalibrationStatus(
                "unapproved",
                "A synthetic calibration approval is valid only inside a prepared "
                "demo run.",
                approval=approval,
            )
        if (
            approval.profile_sha256 != profile_sha256
            or approval.rubric_sha256 != rubric_sha256
        ):
            return CalibrationStatus(
                "stale-approval",
                "The synthetic profile or rubric changed after demo preparation.",
                approval=approval,
            )
        return CalibrationStatus(
            "current", "Synthetic demo calibration is current.", approval=approval
        )

    receipt_file = receipt_path(root, course)
    if not receipt_file.exists() and not receipt_file.is_symlink():
        return CalibrationStatus(
            "no-receipt",
            "No holdout has measured this profile. Grade the holdout batch and "
            "run calibrate.py holdout.",
        )
    try:
        receipt = load_receipt(receipt_file)
    except CalibrationError as error:
        return CalibrationStatus("malformed-receipt", str(error))
    if receipt.course != course:
        return CalibrationStatus(
            "malformed-receipt", "The calibration receipt belongs to another course."
        )
    if (
        receipt.profile_sha256 != profile_sha256
        or receipt.rubric_sha256 != rubric_sha256
    ):
        return CalibrationStatus(
            "stale-receipt",
            "The profile or rubric changed after the holdouts ran. Run the "
            "holdouts again.",
            receipt=receipt,
        )
    approval_matches = (
        approval is not None
        and approval.profile_sha256 == profile_sha256
        and approval.rubric_sha256 == rubric_sha256
        and approval.receipt_sha256 == receipt.source_sha256
    )
    if not receipt.passed:
        if approval_matches and approval.gap_accepted:
            return CalibrationStatus(
                "current", "The instructor accepted the holdout gap.", receipt, approval
            )
        return CalibrationStatus(
            "gap",
            "The holdouts did not pass. Show the instructor the report.",
            receipt,
            approval,
        )
    if approval is None:
        return CalibrationStatus(
            "unapproved",
            "The holdouts passed. The instructor must approve the profile.",
            receipt,
        )
    if not approval_matches:
        return CalibrationStatus(
            "stale-approval",
            "The profile, rubric, or receipt changed after approval. Approve again.",
            receipt,
            approval,
        )
    return CalibrationStatus(
        "current", "Calibration is approved and current.", receipt, approval
    )
