"""Write identity-bearing local state without a permissive creation window."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_private_text(path: Path, text: str) -> None:
    """Atomically replace path with an owner-only regular UTF-8 file."""
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
