"""Create and inspect the fixed synthetic conformance workspace."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from scripts.grading._conformance_model import (
    ConformanceError,
    TRIAL_BATCH,
    TRIAL_BRIEF,
    TRIAL_COURSE,
    TRIAL_DRAFTS,
    TRIAL_MAP,
    TRIAL_MARKER,
    TRIAL_PROFILE,
    TRIAL_PUBLICATION,
    TRIAL_REDACTED,
    TRIAL_REVIEW,
    TRIAL_ROSTER,
    TRIAL_SCHEMA,
    TRIAL_SUBMISSIONS,
)
from scripts.path_safety import is_within, read_bytes_limited, read_text_limited


ARTIFACT_LIMIT = 25 * 1024 * 1024
_REVISION = re.compile(r"^(?:[0-9a-f]{40}(?:\+dirty)?|unknown)$")
_TRIAL_ROSTER_TEXT = (
    "name,student_id,email\nMaya Lin,SYN0001,maya.lin@example.invalid\n"
)
_TRIAL_SUBMISSION_TEXT = """Maya Lin argues that community solar projects need a public queue because opaque interconnection rules reward incumbent developers.

The first mechanism is administrative delay, which raises financing costs while utilities face no matching deadline or disclosure duty.

Public queue data would let regulators compare promised review dates with actual decisions and identify projects that receive unusual treatment.

A utility might answer that disclosure reveals commercially sensitive plans, but project status and elapsed review time do not expose engineering designs.

The stronger policy combines a public queue with an appeal process, published service standards, and penalties when unexplained delay exceeds those standards.
"""


def private_write(path: Path, data: bytes, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if exclusive:
        flags |= os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        raise
    path.chmod(0o600)


def _repository_revision(repo_root: Path) -> str:
    try:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if revision.returncode != 0:
        return "unknown"
    value = revision.stdout.strip()
    if dirty.returncode == 0 and dirty.stdout:
        value += "+dirty"
    return value


def _package_paths(repo_root: Path) -> tuple[Path, ...]:
    manifest = repo_root / "PACKAGE_FILES.txt"
    if manifest.is_symlink() or not manifest.is_file():
        raise ConformanceError("PACKAGE_FILES.txt must be one regular file.")
    try:
        lines = read_text_limited(
            manifest, "The reviewed package inventory", 1024 * 1024
        ).splitlines()
    except (OSError, UnicodeError, ValueError) as error:
        raise ConformanceError("Cannot read the reviewed package inventory.") from error
    paths: list[Path] = []
    for raw in lines:
        if not raw:
            continue
        relative = Path(raw)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.as_posix() != raw
        ):
            raise ConformanceError("PACKAGE_FILES.txt contains an unsafe path.")
        source = repo_root / relative
        if source.is_symlink() or not source.is_file():
            raise ConformanceError(
                f"Reviewed package path is not one regular file: {relative.as_posix()}"
            )
        try:
            source.resolve().relative_to(repo_root)
        except ValueError as error:
            raise ConformanceError(
                "A reviewed package path escapes the repository."
            ) from error
        paths.append(relative)
    return tuple(paths)


def create_trial(repo_root: Path, workspace: Path | None = None) -> Path:
    """Copy the reviewed package and create one synthetic grading input."""
    source_root = repo_root.expanduser().resolve()
    if workspace is None:
        import tempfile

        destination = Path(
            tempfile.mkdtemp(prefix="humanize-grading-conformance-")
        ).resolve()
    else:
        destination = Path(os.path.abspath(workspace))
        if destination.exists() or destination.is_symlink():
            raise ConformanceError("The trial workspace must not already exist.")
        destination.mkdir(parents=True, mode=0o700)
        destination = destination.resolve()
    destination.chmod(0o700)

    try:
        for relative in _package_paths(source_root):
            source = source_root / relative
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            target.chmod(stat.S_IMODE(source.stat(follow_symlinks=False).st_mode))

        profile_source = destination / "tests/fixtures/phil210/profile_phil210.md"
        private_write(
            destination / TRIAL_PROFILE,
            read_bytes_limited(profile_source, "The synthetic profile", 1024 * 1024),
        )
        private_write(destination / TRIAL_ROSTER, _TRIAL_ROSTER_TEXT.encode("utf-8"))
        private_write(
            destination / TRIAL_SUBMISSIONS / "Maya Lin.md",
            _TRIAL_SUBMISSION_TEXT.encode("utf-8"),
        )
        private_write(
            destination / TRIAL_MARKER,
            (
                json.dumps(
                    {
                        "schema": TRIAL_SCHEMA,
                        "synthetic": True,
                        "batch": TRIAL_BATCH,
                        "course": TRIAL_COURSE,
                        "repository_revision": _repository_revision(source_root),
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    return destination


def read_trial_marker(workspace: Path) -> dict[str, object]:
    marker = workspace / TRIAL_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ConformanceError(
            "Audit accepts only a workspace created for a synthetic conformance trial."
        )
    try:
        payload = json.loads(
            read_text_limited(marker, "The conformance trial marker", 64 * 1024)
        )
    except (OSError, UnicodeError, ValueError) as error:
        raise ConformanceError("The conformance trial marker is malformed.") from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != TRIAL_SCHEMA
        or payload.get("synthetic") is not True
        or payload.get("batch") != TRIAL_BATCH
        or payload.get("course") != TRIAL_COURSE
        or not isinstance(payload.get("repository_revision"), str)
        or not _REVISION.fullmatch(str(payload.get("repository_revision")))
    ):
        raise ConformanceError("The conformance trial marker is malformed.")
    return payload


def refuse_workspace_links(workspace: Path) -> None:
    def refuse_unreadable(_error: OSError) -> None:
        raise ConformanceError("The trial contains an unreadable directory.")

    for current, directories, files in os.walk(
        workspace, followlinks=False, onerror=refuse_unreadable
    ):
        base = Path(current)
        for name in directories + files:
            path = base / name
            if not path.is_symlink():
                continue
            try:
                target = path.resolve(strict=False)
            except OSError as error:
                raise ConformanceError(
                    "The trial contains an unreadable link."
                ) from error
            if not is_within(target, workspace, follow_symlinks=False):
                raise ConformanceError("A trial link escapes the workspace.")
            raise ConformanceError("Observed trial artifacts must not be links.")


def validate_synthetic_inputs(workspace: Path) -> None:
    roster = read_bytes_limited(
        workspace / TRIAL_ROSTER, "The synthetic roster", 1024 * 1024
    )
    submissions = workspace / TRIAL_SUBMISSIONS
    if roster != _TRIAL_ROSTER_TEXT.encode("utf-8") or not submissions.is_dir():
        raise ConformanceError("The synthetic conformance inputs changed.")
    try:
        members = tuple(sorted(path.name for path in submissions.iterdir()))
    except OSError as error:
        raise ConformanceError("Cannot inspect the synthetic inputs.") from error
    source = submissions / "Maya Lin.md"
    if members != ("Maya Lin.md",) or read_bytes_limited(
        source, "The synthetic submission", 1024 * 1024
    ) != _TRIAL_SUBMISSION_TEXT.encode("utf-8"):
        raise ConformanceError("The synthetic conformance inputs changed.")


def private_directory(workspace: Path, relative: Path) -> Path:
    current = workspace
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ConformanceError("A private conformance directory is a link.")
        if current.exists() and not current.is_dir():
            raise ConformanceError("A private conformance path is not a directory.")
        current.mkdir(mode=0o700, exist_ok=True)
        current.chmod(0o700)
    return current


def _report_path(relative: str, identities: tuple[str, ...]) -> str:
    if any(value.casefold() in relative.casefold() for value in identities):
        parent = Path(relative).parent.as_posix()
        return (
            f"{parent}/<identity-bearing-path>"
            if parent != "."
            else "<identity-bearing-path>"
        )
    return relative


def _artifact_entry(
    workspace: Path,
    path: Path,
    *,
    content: bool = True,
    identities: tuple[str, ...] = (),
) -> dict[str, object]:
    relative = _report_path(path.relative_to(workspace).as_posix(), identities)
    info = path.stat(follow_symlinks=False)
    result: dict[str, object] = {
        "path": relative,
        "bytes": info.st_size,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "regular": stat.S_ISREG(info.st_mode),
    }
    if content and result["regular"] and info.st_size <= ARTIFACT_LIMIT:
        result["sha256"] = hashlib.sha256(
            read_bytes_limited(path, "The observed artifact", ARTIFACT_LIMIT)
        ).hexdigest()
        result["bounded"] = True
    elif content:
        result["sha256"] = ""
        result["bounded"] = False
    return result


def private_artifacts(workspace: Path) -> tuple[dict[str, object], ...]:
    results: list[dict[str, object]] = []
    for relative in (TRIAL_MAP, TRIAL_REVIEW):
        path = workspace / relative
        if not path.exists():
            results.append({"path": relative.as_posix(), "present": False, "mode": ""})
            continue
        item = _artifact_entry(workspace, path, content=False)
        results.append(
            {
                "path": item["path"],
                "present": bool(item["regular"]),
                "mode": item["mode"],
            }
        )
    return tuple(results)


def observed_artifacts(
    workspace: Path, identities: tuple[str, ...] | None = None
) -> tuple[dict[str, object], ...]:
    roots = (TRIAL_BRIEF, TRIAL_REDACTED, TRIAL_DRAFTS, TRIAL_PUBLICATION)
    paths: set[Path] = set()
    for relative in roots:
        root = workspace / relative
        if root.is_file():
            paths.add(root)
        elif root.is_dir():
            paths.update(path for path in root.rglob("*") if path.is_file())
    trusted = identities if identities is not None else identity_values(workspace)
    return tuple(
        _artifact_entry(workspace, path, identities=trusted) for path in sorted(paths)
    )


def identity_values(workspace: Path) -> tuple[str, ...]:
    try:
        text = read_text_limited(
            workspace / TRIAL_ROSTER, "The synthetic roster", 1024 * 1024
        )
        rows = csv.DictReader(text.splitlines())
        values = {
            value.strip()
            for row in rows
            for value in row.values()
            if isinstance(value, str) and len(value.strip()) >= 3
        }
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        raise ConformanceError("Cannot read the trusted synthetic roster.") from error
    if not values:
        raise ConformanceError("The trusted synthetic roster is empty.")
    return tuple(sorted(values, key=str.casefold))


def identity_leaks(
    workspace: Path, identities: tuple[str, ...] | None = None
) -> tuple[str, ...]:
    trusted = identities if identities is not None else identity_values(workspace)
    folded_identities = tuple(value.casefold() for value in trusted)
    leaks: set[str] = set()
    for relative in (TRIAL_REDACTED, TRIAL_DRAFTS):
        directory = workspace / relative
        if not directory.is_dir():
            continue
        for path in sorted(item for item in directory.rglob("*") if item.is_file()):
            try:
                text = read_text_limited(
                    path, "The identity-free artifact", ARTIFACT_LIMIT
                )
            except (OSError, UnicodeError, ValueError):
                continue
            folded = text.casefold()
            if any(value in folded for value in folded_identities):
                leaks.add(_report_path(path.relative_to(workspace).as_posix(), trusted))
    return tuple(sorted(leaks))
