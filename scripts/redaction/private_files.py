"""Write identity-bearing local state without a permissive creation window."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

try:
    from scripts.path_safety import has_symlink_component
except ModuleNotFoundError:  # direct scripts/redact.py execution
    from path_safety import has_symlink_component


def write_private_text(path: Path, text: str) -> None:
    """Atomically replace path with an owner-only regular UTF-8 file."""
    if has_symlink_component(path.parent):
        raise ValueError("Private grading-state directories must not be symlinks.")
    path.parent.mkdir(parents=True, exist_ok=True)
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
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
