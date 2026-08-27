"""Identity-free evidence that binds one redaction result to its token map."""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.path_safety import (
        has_symlink_component,
        read_bytes_limited,
        read_text_limited,
    )
except ModuleNotFoundError:  # direct scripts/redact.py execution
    from path_safety import (
        has_symlink_component,
        read_bytes_limited,
        read_text_limited,
    )
from .private_files import write_private_text

SCHEMA = "redaction-batch/2"
LEGACY_SCHEMA = "redaction-batch/1"
NAME = "_redaction.json"


@dataclass(frozen=True)
class RedactionManifest:
    batch: str
    run_id: str
    reviewed: bool
    exit_code: int | None
    outputs: tuple[tuple[str, str], ...] = ()

    @classmethod
    def create(
        cls,
        batch: str,
        run_id: str,
        outputs: list[Path],
        *,
        reviewed: bool,
        exit_code: int,
    ) -> "RedactionManifest":
        if not isinstance(reviewed, bool):
            raise ValueError("The redaction manifest needs a reviewed decision.")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise ValueError("The redaction manifest needs an integer exit code.")
        bound = tuple(
            sorted(
                (
                    path.name,
                    hashlib.sha256(
                        read_bytes_limited(
                            path, "The redacted submission", 25 * 1024 * 1024
                        )
                    ).hexdigest(),
                )
                for path in outputs
            )
        )
        return cls(
            batch=batch,
            run_id=run_id,
            reviewed=reviewed,
            exit_code=exit_code,
            outputs=bound,
        )

    def save(self, directory: Path) -> Path:
        if not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool):
            raise ValueError("The redaction manifest needs an integer exit code.")
        path = directory / NAME
        write_private_text(
            path,
            json.dumps(
                {
                    "schema": SCHEMA,
                    "batch": self.batch,
                    "run_id": self.run_id,
                    "reviewed": self.reviewed,
                    "exit_code": self.exit_code,
                    "outputs": [
                        {"path": path, "sha256": digest}
                        for path, digest in self.outputs
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
        )
        return path

    @classmethod
    def invalidate_existing(
        cls, directory: Path, *, batch: str, exit_code: int
    ) -> bool:
        """Mark same-batch regular manifest state as an incomplete new attempt."""
        if (
            not isinstance(exit_code, int)
            or isinstance(exit_code, bool)
            or exit_code == 0
        ):
            raise ValueError("Manifest invalidation needs a non-zero integer exit code.")
        path = directory / NAME
        if has_symlink_component(directory) or has_symlink_component(path):
            return False
        try:
            if not directory.is_dir() or not path.is_file():
                return False
            current = cls.load(directory)
            if current.batch != batch:
                return False
            cls(
                batch=current.batch,
                run_id=current.run_id,
                reviewed=False,
                exit_code=exit_code,
                outputs=current.outputs,
            ).save(directory)
        except (OSError, UnicodeError, ValueError):
            return False
        return True

    @classmethod
    def load(cls, directory: Path) -> "RedactionManifest":
        path = directory / NAME
        payload = json.loads(read_text_limited(path, "The redaction manifest", 1_048_576))
        if not isinstance(payload, dict):
            raise ValueError("The redaction manifest has an unknown schema.")
        schema = payload.get("schema")
        if schema == SCHEMA:
            reviewed = payload.get("reviewed")
            if not isinstance(reviewed, bool):
                raise ValueError("The redaction manifest needs a reviewed decision.")
            exit_code = payload.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                raise ValueError("The redaction manifest needs an integer exit code.")
        elif schema == LEGACY_SCHEMA:
            reviewed = False
            exit_code = None
        else:
            raise ValueError("The redaction manifest has an unknown schema.")
        batch = payload.get("batch")
        run_id = payload.get("run_id")
        if not isinstance(batch, str) or not batch:
            raise ValueError("The redaction manifest needs a grading batch.")
        if not isinstance(run_id, str) or len(run_id) < 16:
            raise ValueError("The redaction manifest needs a redaction-run ID.")
        raw_outputs = payload.get("outputs")
        if not isinstance(raw_outputs, list) or (
            not raw_outputs and (schema == LEGACY_SCHEMA or exit_code == 0)
        ):
            raise ValueError("The redaction manifest needs its output list.")
        outputs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for item in raw_outputs:
            if not isinstance(item, dict):
                raise ValueError("The redaction manifest has a malformed output.")
            name = item.get("path")
            digest = item.get("sha256")
            if (
                not isinstance(name, str)
                or Path(name).name != name
                or name.startswith("_")
                or name in seen
                or not isinstance(digest, str)
                or len(digest) != 64
            ):
                raise ValueError("The redaction manifest has a malformed output.")
            seen.add(name)
            outputs.append((name, digest))
        return cls(
            batch=batch,
            run_id=run_id,
            reviewed=reviewed,
            exit_code=exit_code,
            outputs=tuple(sorted(outputs)),
        )

    def validate(self, directory: Path) -> None:
        """Require the exact token-named file set and bytes from redaction."""
        actual = {
            path.name
            for path in directory.iterdir()
            if path.is_file() and not path.name.startswith("_")
        }
        expected = {name for name, _ in self.outputs}
        if actual != expected:
            raise ValueError("The redaction manifest output membership changed.")
        for name, digest in self.outputs:
            path = directory / name
            actual_digest = hashlib.sha256(
                read_bytes_limited(
                    path, "The redacted submission", 25 * 1024 * 1024
                )
            ).hexdigest()
            if actual_digest != digest:
                raise ValueError(f"The redaction manifest does not match {name}.")
