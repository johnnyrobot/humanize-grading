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
    MAX_PACKAGE_BYTES,
    MAX_PACKAGE_FILE_BYTES,
    MAX_PACKAGE_FILES,
    MAX_WORKSPACE_BYTES,
    MAX_WORKSPACE_DEPTH,
    MAX_WORKSPACE_ENTRIES,
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
from scripts.grading._conformance_process import (
    popen_group_options,
    terminate_process_group,
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


def _is_python_bytecode_path(path: Path) -> bool:
    return "__pycache__" in path.parts or path.suffix == ".pyc"


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
    def git_output(arguments: list[str]) -> tuple[int, str]:
        process = subprocess.Popen(
            arguments,
            cwd=repo_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            **popen_group_options(),
        )
        try:
            stdout, _ = process.communicate(timeout=5)
            if len(stdout) > 1024 * 1024:
                return 1, ""
            return int(process.returncode or 0), stdout
        finally:
            terminate_process_group(process)

    try:
        revision_code, revision_output = git_output(["git", "rev-parse", "HEAD"])
        dirty_code, dirty_output = git_output(["git", "status", "--porcelain"])
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if revision_code != 0:
        return "unknown"
    value = revision_output.strip()
    if dirty_code == 0 and dirty_output:
        value += "+dirty"
    return value


def _read_fd_limited(descriptor: int, label: str, limit: int) -> bytes:
    state = os.fstat(descriptor)
    if not stat.S_ISREG(state.st_mode) or state.st_size > limit:
        raise ConformanceError(f"{label} is not one bounded regular file.")
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(descriptor, min(64 * 1024, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise ConformanceError(f"{label} is too large.")
    return data


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )


def _open_directory_chain(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
    current = os.dup(root_fd)
    try:
        for part in parts:
            if create:
                try:
                    os.mkdir(part, 0o755, dir_fd=current)
                except FileExistsError:
                    pass
            child = os.open(part, _directory_flags(), dir_fd=current)
            os.close(current)
            current = child
        return current
    except Exception:
        os.close(current)
        raise


def _open_regular_at(parent_fd: int, name: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, dir_fd=parent_fd)
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise ConformanceError("A reviewed package source is not a regular file.")
    return descriptor


def _package_paths(repo_root: Path) -> tuple[Path, ...]:
    root_fd = os.open(repo_root, _directory_flags())
    try:
        manifest_fd = _open_regular_at(root_fd, "PACKAGE_FILES.txt")
        try:
            source = _read_fd_limited(
                manifest_fd, "The reviewed package inventory", 1024 * 1024
            )
        finally:
            os.close(manifest_fd)
    except (OSError, UnicodeError, ValueError, ConformanceError) as error:
        raise ConformanceError("Cannot read the reviewed package inventory.") from error
    finally:
        os.close(root_fd)
    try:
        lines = source.decode("utf-8").splitlines()
    except UnicodeError as error:
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
        if _is_python_bytecode_path(relative):
            raise ConformanceError("PACKAGE_FILES.txt must not include bytecode.")
        paths.append(relative)
        if len(paths) > MAX_PACKAGE_FILES:
            raise ConformanceError("The reviewed package exceeds the file limit.")
    return tuple(paths)


def _copy_package_file(
    source_root_fd: int, destination_root_fd: int, relative: Path
) -> dict[str, object]:
    source_parent = _open_directory_chain(
        source_root_fd, relative.parts[:-1], create=False
    )
    destination_parent = _open_directory_chain(
        destination_root_fd, relative.parts[:-1], create=True
    )
    source_fd = -1
    destination_fd = -1
    try:
        source_fd = _open_regular_at(source_parent, relative.name)
        before = os.fstat(source_fd)
        if before.st_size > MAX_PACKAGE_FILE_BYTES:
            raise ConformanceError("A reviewed package file exceeds the byte limit.")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        destination_fd = os.open(
            relative.name,
            flags,
            stat.S_IMODE(before.st_mode),
            dir_fd=destination_parent,
        )
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(source_fd, 64 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                view = view[written:]
        os.fchmod(destination_fd, stat.S_IMODE(before.st_mode))
        after = os.fstat(source_fd)
        path_after = os.stat(relative.name, dir_fd=source_parent, follow_symlinks=False)
        verified_parent = _open_directory_chain(
            source_root_fd, relative.parts[:-1], create=False
        )
        try:
            parent_after = os.fstat(verified_parent)
        finally:
            os.close(verified_parent)
        identity = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
        if (
            identity
            != (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
            )
            or (path_after.st_dev, path_after.st_ino)
            != (
                before.st_dev,
                before.st_ino,
            )
            or (parent_after.st_dev, parent_after.st_ino)
            != (
                os.fstat(source_parent).st_dev,
                os.fstat(source_parent).st_ino,
            )
        ):
            raise ConformanceError("A reviewed package source changed during copy.")
        return {
            "path": relative.as_posix(),
            "bytes": total,
            "sha256": digest.hexdigest(),
            "mode": f"{stat.S_IMODE(before.st_mode):04o}",
        }
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        if source_fd >= 0:
            os.close(source_fd)
        os.close(destination_parent)
        os.close(source_parent)


def _package_document(
    files: tuple[dict[str, object], ...], revision: str
) -> dict[str, object]:
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return {
        "schema": "grading-agent-package/1",
        "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "repository_revision": revision,
        "dirty": revision.endswith("+dirty"),
        "files": files,
    }


def _lock_package_directories(destination: Path, paths: tuple[Path, ...]) -> None:
    directories = {
        parent
        for relative in paths
        for parent in relative.parents
        if parent != Path(".")
    }
    for relative in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        (destination / relative).chmod(0o555)


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
        source_root_fd = os.open(source_root, _directory_flags())
        destination_root_fd = os.open(destination, _directory_flags())
        try:
            copied: list[dict[str, object]] = []
            package_bytes = 0
            reviewed_paths = _package_paths(source_root)
            for relative in reviewed_paths:
                item = _copy_package_file(
                    source_root_fd, destination_root_fd, relative
                )
                package_bytes += int(item["bytes"])
                if package_bytes > MAX_PACKAGE_BYTES:
                    raise ConformanceError(
                        "The reviewed package exceeds the aggregate byte limit."
                    )
                copied.append(item)
            package_files = tuple(copied)
        finally:
            os.close(destination_root_fd)
            os.close(source_root_fd)
        _lock_package_directories(destination, reviewed_paths)

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
        revision = _repository_revision(source_root)
        package = _package_document(package_files, revision)
        private_write(
            destination / TRIAL_MARKER,
            (
                json.dumps(
                    {
                        "schema": TRIAL_SCHEMA,
                        "synthetic": True,
                        "batch": TRIAL_BATCH,
                        "course": TRIAL_COURSE,
                        "repository_revision": revision,
                        "package": package,
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            ).encode("utf-8"),
        )
        validate_workspace_budget(destination)
    except OSError as error:
        shutil.rmtree(destination, ignore_errors=True)
        raise ConformanceError("Cannot copy the reviewed package safely.") from error
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
            read_text_limited(marker, "The conformance trial marker", 1024 * 1024)
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
        or not isinstance(payload.get("package"), dict)
    ):
        raise ConformanceError("The conformance trial marker is malformed.")
    package = payload["package"]
    if (
        package.get("schema") != "grading-agent-package/1"
        or not isinstance(package.get("digest"), str)
        or len(package["digest"]) != 64
        or not isinstance(package.get("repository_revision"), str)
        or not _REVISION.fullmatch(str(package.get("repository_revision")))
        or package.get("repository_revision") != payload.get("repository_revision")
        or not isinstance(package.get("dirty"), bool)
        or not isinstance(package.get("files"), list)
    ):
        raise ConformanceError("The conformance package attestation is malformed.")
    return payload


def _current_package_file(
    root_fd: int, relative: Path, *, expected_bytes: int | None = None
) -> dict[str, object]:
    parent = _open_directory_chain(root_fd, relative.parts[:-1], create=False)
    descriptor = -1
    try:
        descriptor = _open_regular_at(parent, relative.name)
        state = os.fstat(descriptor)
        if expected_bytes is not None and state.st_size != expected_bytes:
            return {
                "path": relative.as_posix(),
                "bytes": state.st_size,
                "sha256": "",
                "mode": f"{stat.S_IMODE(state.st_mode):04o}",
            }
        if state.st_size > MAX_PACKAGE_FILE_BYTES:
            raise ConformanceError("A reviewed package file exceeds the byte limit.")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 64 * 1024)
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
        return {
            "path": relative.as_posix(),
            "bytes": total,
            "sha256": digest.hexdigest(),
            "mode": f"{stat.S_IMODE(state.st_mode):04o}",
        }
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def _unexpected_package_paths(
    workspace: Path, expected_paths: set[str]
) -> tuple[str, ...]:
    expected_top = {Path(path).parts[0] for path in expected_paths}
    runtime_top = {".grading", "redacted", "drafts", "final", "synthetic"}
    unexpected: set[str] = set()
    for entry in workspace.iterdir():
        if entry.name not in expected_top | runtime_top:
            unexpected.add(entry.name)
    for top in sorted(expected_top):
        root = workspace / top
        if not root.is_dir() or root.is_symlink():
            continue
        for current, directories, files in os.walk(root, followlinks=False):
            base = Path(current)
            for name in directories + files:
                path = base / name
                relative = path.relative_to(workspace).as_posix()
                if relative not in expected_paths and (
                    path.is_symlink()
                    or path.is_file()
                    or name == "__pycache__"
                    or path.suffix == ".pyc"
                ):
                    unexpected.add(relative)
    return tuple(sorted(unexpected))


def attest_package(
    workspace: Path, expected: dict[str, object] | None = None
) -> dict[str, object]:
    """Revalidate every reviewed package byte without trusting package code."""
    if expected is None:
        expected = dict(read_trial_marker(workspace)["package"])
    validate_workspace_budget(workspace)
    raw_files = expected.get("files")
    if (
        not isinstance(raw_files, (list, tuple))
        or len(raw_files) > MAX_PACKAGE_FILES
    ):
        raise ConformanceError("The conformance package attestation is malformed.")
    files: list[dict[str, object]] = []
    expected_paths: set[str] = set()
    expected_bytes = 0
    for item in raw_files:
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("path"), str)
            or Path(item["path"]).is_absolute()
            or ".." in Path(item["path"]).parts
            or _is_python_bytecode_path(Path(item["path"]))
            or not isinstance(item.get("bytes"), int)
            or isinstance(item.get("bytes"), bool)
            or not 0 <= item["bytes"] <= MAX_PACKAGE_FILE_BYTES
            or not isinstance(item.get("sha256"), str)
            or len(item["sha256"]) != 64
            or not isinstance(item.get("mode"), str)
        ):
            raise ConformanceError("The conformance package attestation is malformed.")
        if item["path"] in expected_paths:
            raise ConformanceError(
                "The conformance package attestation has duplicates."
            )
        expected_paths.add(item["path"])
        files.append(dict(item))
        expected_bytes += item["bytes"]
        if expected_bytes > MAX_PACKAGE_BYTES:
            raise ConformanceError("The conformance package exceeds the byte limit.")
    files.sort(key=lambda item: item["path"])
    canonical = json.dumps(tuple(files), sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    if (
        expected.get("digest") != digest
        or not isinstance(expected.get("repository_revision"), str)
        or not _REVISION.fullmatch(str(expected.get("repository_revision")))
        or not isinstance(expected.get("dirty"), bool)
    ):
        raise ConformanceError("The conformance package attestation is malformed.")

    root_fd = os.open(workspace, _directory_flags())
    findings: set[str] = set()
    actual_files: list[dict[str, object]] = []
    try:
        for item in files:
            relative = Path(item["path"])
            try:
                actual = _current_package_file(
                    root_fd, relative, expected_bytes=int(item["bytes"])
                )
            except (OSError, ConformanceError):
                findings.add(f"package-missing:{relative.as_posix()}")
                continue
            actual_files.append(actual)
            if actual != item:
                findings.add(f"package-changed:{relative.as_posix()}")
    finally:
        os.close(root_fd)
    for relative in _unexpected_package_paths(workspace, expected_paths):
        findings.add(f"package-unexpected:{relative}")
    actual_canonical = json.dumps(
        tuple(sorted(actual_files, key=lambda item: item["path"])),
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        "schema": "grading-agent-package/1",
        "status": "failed" if findings else "passed",
        "digest": digest,
        "actual_digest": hashlib.sha256(actual_canonical.encode("utf-8")).hexdigest(),
        "repository_revision": str(expected["repository_revision"]),
        "dirty": bool(expected["dirty"]),
        "files": tuple(files),
        "findings": tuple(sorted(findings)),
    }


def trusted_package_attestation(package_root: Path) -> dict[str, object]:
    """Build an expected package byte set from a trusted external root."""
    candidate = package_root.expanduser()
    if candidate.is_symlink():
        raise ConformanceError("The trusted package root must not be a link.")
    root = candidate.resolve()
    if not root.is_dir():
        raise ConformanceError("The trusted package root must be one directory.")
    root_fd = os.open(root, _directory_flags())
    try:
        collected: list[dict[str, object]] = []
        package_bytes = 0
        for relative in _package_paths(root):
            item = _current_package_file(root_fd, relative)
            package_bytes += int(item["bytes"])
            if package_bytes > MAX_PACKAGE_BYTES:
                raise ConformanceError(
                    "The reviewed package exceeds the aggregate byte limit."
                )
            collected.append(item)
        files = tuple(collected)
    except (OSError, ConformanceError) as error:
        raise ConformanceError("Cannot attest the trusted package root.") from error
    finally:
        os.close(root_fd)
    package = _package_document(files, _repository_revision(root))
    package["trusted_root"] = str(root)
    return package


def refuse_workspace_links(
    workspace: Path,
    *,
    allow_identity_artifacts: bool = False,
    allowed_package_paths: set[str] | None = None,
) -> None:
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
            relative = path.relative_to(workspace).as_posix()
            if allow_identity_artifacts and any(
                path == workspace / root or workspace / root in path.parents
                for root in (TRIAL_REDACTED, TRIAL_DRAFTS)
            ):
                continue
            if allowed_package_paths:
                package_roots = {
                    Path(package_path).parts[0]
                    for package_path in allowed_package_paths
                }
                if Path(relative).parts[0] in package_roots or any(
                    relative == package_path or package_path.startswith(relative + "/")
                    for package_path in allowed_package_paths
                ):
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


def validate_workspace_budget(workspace: Path) -> None:
    """Reject a trial tree before an audit performs repeated deep scans."""
    entries = 0
    total_bytes = 0
    pending = [(workspace, 0)]
    try:
        while pending:
            current, depth = pending.pop()
            if depth > MAX_WORKSPACE_DEPTH:
                raise ConformanceError("The workspace exceeds the depth limit.")
            with os.scandir(current) as members:
                for member in members:
                    entries += 1
                    if entries > MAX_WORKSPACE_ENTRIES:
                        raise ConformanceError(
                            "The workspace exceeds the entry limit."
                        )
                    state = member.stat(follow_symlinks=False)
                    if stat.S_ISREG(state.st_mode):
                        total_bytes += state.st_size
                        if total_bytes > MAX_WORKSPACE_BYTES:
                            raise ConformanceError(
                                "The workspace exceeds the byte limit."
                            )
                    elif stat.S_ISDIR(state.st_mode):
                        pending.append((Path(member.path), depth + 1))
                    elif not stat.S_ISLNK(state.st_mode):
                        raise ConformanceError(
                            "The workspace contains a special file."
                        )
    except ConformanceError:
        raise
    except OSError as error:
        raise ConformanceError("Cannot inspect the workspace limits.") from error


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


def open_workspace_directory(workspace: Path, relative: Path) -> int:
    root_fd = os.open(workspace, _directory_flags())
    try:
        return _open_directory_chain(root_fd, relative.parts, create=False)
    finally:
        os.close(root_fd)


def workspace_directory_matches(
    workspace: Path, relative: Path, expected: os.stat_result
) -> bool:
    descriptor = -1
    try:
        descriptor = open_workspace_directory(workspace, relative)
        current = os.fstat(descriptor)
        return (current.st_dev, current.st_ino) == (expected.st_dev, expected.st_ino)
    except OSError:
        return False
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _report_path(relative: str, identities: tuple[str, ...]) -> str:
    if _contains_identity(relative, identities):
        parent = Path(relative).parent.as_posix()
        return (
            f"{parent}/<identity-bearing-path>"
            if parent != "."
            else "<identity-bearing-path>"
        )
    return relative


def _contains_identity(text: str, identities: tuple[str, ...]) -> bool:
    folded = text.casefold()
    return any(
        re.search(
            rf"(?<![\w]){re.escape(identity.casefold())}(?![\w])",
            folded,
        )
        is not None
        for identity in identities
    )


def _artifact_entry(
    workspace: Path,
    path: Path,
    *,
    content: bool = True,
    identities: tuple[str, ...] = (),
) -> dict[str, object]:
    relative = _report_path(path.relative_to(workspace).as_posix(), identities)
    info = path.lstat()
    kind = (
        "file"
        if stat.S_ISREG(info.st_mode)
        else "directory"
        if stat.S_ISDIR(info.st_mode)
        else "symlink"
        if stat.S_ISLNK(info.st_mode)
        else "other"
    )
    result: dict[str, object] = {
        "path": relative,
        "bytes": info.st_size,
        "mode": f"{stat.S_IMODE(info.st_mode):04o}",
        "regular": stat.S_ISREG(info.st_mode),
        "kind": kind,
    }
    if content and result["regular"] and info.st_size <= ARTIFACT_LIMIT:
        try:
            result["sha256"] = hashlib.sha256(
                read_bytes_limited(path, "The observed artifact", ARTIFACT_LIMIT)
            ).hexdigest()
            result["bounded"] = True
        except (OSError, UnicodeError, ValueError):
            result["sha256"] = ""
            result["bounded"] = False
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
    paths: dict[Path, bool] = {}
    for relative in roots:
        root = workspace / relative
        try:
            root_state = root.lstat()
        except OSError:
            continue
        if stat.S_ISDIR(root_state.st_mode):
            paths[root] = False
            for current, directories, files in os.walk(root, followlinks=False):
                base = Path(current)
                for name in directories + files:
                    path = base / name
                    if Path(name).suffix.lower() in {".md", ".json"}:
                        paths[path] = True
        else:
            paths[root] = True
    trusted = identities if identities is not None else identity_values(workspace)
    return tuple(
        _artifact_entry(
            workspace,
            path,
            content=paths[path],
            identities=trusted,
        )
        for path in sorted(paths)
    )


def identity_values(workspace: Path) -> tuple[str, ...]:
    try:
        text = read_text_limited(
            workspace / TRIAL_ROSTER, "The synthetic roster", 1024 * 1024
        )
        rows = tuple(csv.DictReader(text.splitlines()))
        values = {
            value.strip()
            for row in rows
            for value in row.values()
            if isinstance(value, str) and len(value.strip()) >= 3
        }
        for row in rows:
            name = row.get("name")
            if not isinstance(name, str):
                continue
            parts = tuple(part for part in name.split() if len(part) >= 2)
            values.update(parts)
            if len(parts) >= 2:
                values.add(f"{parts[0][0]}. {parts[-1]}")
                values.add(f"{parts[0][0]} {parts[-1]}")
    except (OSError, UnicodeError, ValueError, csv.Error) as error:
        raise ConformanceError("Cannot read the trusted synthetic roster.") from error
    if not values:
        raise ConformanceError("The trusted synthetic roster is empty.")
    return tuple(sorted(values, key=str.casefold))


def _identity_artifact_paths(
    workspace: Path,
) -> tuple[tuple[Path, ...], tuple[Path, ...]]:
    paths: set[Path] = set()
    unreadable: set[Path] = set()

    def record_unreadable(error: OSError) -> None:
        if error.filename:
            unreadable.add(Path(error.filename))

    for relative in (TRIAL_REDACTED, TRIAL_DRAFTS):
        root = workspace / relative
        if root.is_symlink():
            paths.add(root)
            continue
        if not root.is_dir():
            continue
        for current, directories, files in os.walk(
            root, followlinks=False, onerror=record_unreadable
        ):
            base = Path(current)
            for name in directories + files:
                if Path(name).suffix.lower() in {".md", ".json"}:
                    paths.add(base / name)
    return tuple(sorted(paths)), tuple(sorted(unreadable))


def identity_scan(
    workspace: Path, identities: tuple[str, ...] | None = None
) -> tuple[tuple[str, ...], tuple[dict[str, str], ...]]:
    trusted = identities if identities is not None else identity_values(workspace)
    leaks: set[str] = set()
    findings: dict[str, str] = {}
    root_fd = os.open(workspace, _directory_flags())
    try:
        artifact_paths, unreadable_paths = _identity_artifact_paths(workspace)
        for path in unreadable_paths:
            relative = _report_path(path.relative_to(workspace).as_posix(), trusted)
            findings[relative] = "artifact-unreadable"
        for path in artifact_paths:
            relative_path = path.relative_to(workspace)
            relative = _report_path(relative_path.as_posix(), trusted)
            try:
                state = path.lstat()
            except OSError:
                findings[relative] = "artifact-unreadable"
                continue
            if stat.S_ISLNK(state.st_mode):
                findings[relative] = "artifact-linked"
                continue
            if not stat.S_ISREG(state.st_mode):
                findings[relative] = "artifact-nonregular"
                continue
            if state.st_size > ARTIFACT_LIMIT:
                findings[relative] = "artifact-too-large"
                continue
            descriptor = -1
            parent = -1
            try:
                parent = _open_directory_chain(
                    root_fd, relative_path.parts[:-1], create=False
                )
                descriptor = _open_regular_at(parent, relative_path.name)
                opened = os.fstat(descriptor)
                if (opened.st_dev, opened.st_ino) != (state.st_dev, state.st_ino):
                    findings[relative] = "artifact-unreadable"
                    continue
                source = _read_fd_limited(
                    descriptor, "The identity-free artifact", ARTIFACT_LIMIT
                )
            except (OSError, ConformanceError):
                findings[relative] = "artifact-unreadable"
                continue
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
                if parent >= 0:
                    os.close(parent)
            try:
                text = source.decode("utf-8")
            except UnicodeError:
                findings[relative] = "artifact-invalid-utf8"
                continue
            if _contains_identity(text, trusted):
                leaks.add(relative)
    finally:
        os.close(root_fd)
    return (
        tuple(sorted(leaks)),
        tuple({"path": path, "code": code} for path, code in sorted(findings.items())),
    )
