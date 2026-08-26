"""Small path checks shared by the local grading commands."""

from __future__ import annotations

import os
from pathlib import Path


def require_component(value: str, label: str) -> str:
    """Return one safe path component or raise a plain validation error."""
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
        or Path(value).name != value
    ):
        raise ValueError(f"{label} must be one name, without slashes or '..'.")
    return value


def normalized(path: Path, *, follow_symlinks: bool) -> Path:
    """Return an absolute path, optionally resolving existing symlinks."""
    return path.expanduser().resolve() if follow_symlinks else Path(os.path.abspath(path))


def is_within(path: Path, directory: Path, *, follow_symlinks: bool) -> bool:
    """Say whether path is the directory or one of its descendants."""
    candidate = normalized(path, follow_symlinks=follow_symlinks)
    root = normalized(directory, follow_symlinks=follow_symlinks)
    return candidate == root or root in candidate.parents


def paths_overlap(left: Path, right: Path) -> bool:
    """Reject both lexical and symlink-resolved ancestor relationships."""
    for follow in (False, True):
        if is_within(left, right, follow_symlinks=follow) or is_within(
            right, left, follow_symlinks=follow
        ):
            return True
    return False


def has_symlink_component(path: Path) -> bool:
    """Say whether an existing path component is a symbolic link."""
    current = normalized(path, follow_symlinks=False)
    while True:
        if current.is_symlink():
            return True
        if current.parent == current:
            return False
        current = current.parent


def read_bytes_limited(path: Path, label: str, max_bytes: int) -> bytes:
    """Read one regular file after a public byte bound and link check."""
    if has_symlink_component(path):
        raise ValueError(f"{label} must not use a symbolic link.")
    size = path.stat().st_size
    if size > max_bytes:
        raise ValueError(f"{label} is too large. The limit is {max_bytes} bytes.")
    return path.read_bytes()


def read_text_limited(
    path: Path, label: str, max_bytes: int = 5 * 1024 * 1024
) -> str:
    return read_bytes_limited(path, label, max_bytes).decode("utf-8")
