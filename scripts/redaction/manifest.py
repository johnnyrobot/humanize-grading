"""Identity-free evidence that binds one redaction result to its token map."""

from __future__ import annotations

import json
import hashlib
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.path_safety import (
        has_symlink_component,
        read_bytes_limited,
    )
except ModuleNotFoundError:  # direct scripts/redact.py execution
    from path_safety import (
        has_symlink_component,
        read_bytes_limited,
    )
from .private_files import write_private_text

SCHEMA = "redaction-batch/3"
SCHEMA_TWO = "redaction-batch/2"
LEGACY_SCHEMA = "redaction-batch/1"
NAME = "_redaction.json"
MAX_MANIFEST_BYTES = 1_048_576


class RedactionInvalidationError(RuntimeError):
    """Eligible prior redaction state could not be made ineligible."""


def _require_digest(value: object, label: str) -> str:
    """Return one canonical SHA-256 digest or raise a plain validation error."""
    if (
        not isinstance(value, str)
        or len(value) != 64
        or value != value.lower()
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"The redaction manifest needs a lowercase {label} SHA-256.")
    return value


def _manifest_identity(state: os.stat_result) -> tuple[int, int, int, int]:
    """Return the file facts that bind an opened manifest to its path."""
    return (
        stat.S_IFMT(state.st_mode),
        state.st_dev,
        state.st_ino,
        state.st_nlink,
    )


def _require_manifest_identity(
    path: Path, expected: tuple[int, int, int, int]
) -> None:
    """Require path to still name the bound regular single-link manifest."""
    try:
        if has_symlink_component(path.parent):
            raise RedactionInvalidationError(
                "The redaction manifest changed during invalidation."
            )
        current = path.lstat()
    except RedactionInvalidationError:
        raise
    except OSError as error:
        raise RedactionInvalidationError(
            "The redaction manifest changed during invalidation."
        ) from error
    if (
        not stat.S_ISREG(current.st_mode)
        or current.st_nlink != 1
        or _manifest_identity(current) != expected
    ):
        raise RedactionInvalidationError(
            "The redaction manifest changed during invalidation."
        )


def _write_invalidation_atomically(
    path: Path,
    text: str,
    expected: tuple[int, int, int, int],
) -> None:
    """Stage owner-only text and replace only the still-bound manifest."""
    _require_manifest_identity(path, expected)
    descriptor = -1
    temporary: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(temporary_name)
        os.fchmod(descriptor, 0o600)
        handle = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _require_manifest_identity(path, expected)
        os.replace(temporary, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                # Keep the original mismatch or I/O error. A cleanup error must
                # not turn a refused pathname change into the in-place fallback.
                pass


@dataclass(frozen=True)
class RedactionManifest:
    batch: str
    run_id: str
    reviewed: bool
    exit_code: int | None
    map_sha256: str
    review_sha256: str
    outputs: tuple[tuple[str, str], ...] = ()
    schema: str = SCHEMA

    @classmethod
    def create(
        cls,
        batch: str,
        run_id: str,
        outputs: list[Path],
        *,
        reviewed: bool,
        exit_code: int,
        map_sha256: str,
        review_sha256: str,
    ) -> "RedactionManifest":
        if not isinstance(reviewed, bool):
            raise ValueError("The redaction manifest needs a reviewed decision.")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise ValueError("The redaction manifest needs an integer exit code.")
        map_sha256 = _require_digest(map_sha256, "token-map")
        review_sha256 = _require_digest(review_sha256, "review")
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
            map_sha256=map_sha256,
            review_sha256=review_sha256,
            outputs=bound,
        )

    def save(self, directory: Path) -> Path:
        if self.schema != SCHEMA:
            raise ValueError("Only the current redaction manifest schema can be saved.")
        if not isinstance(self.exit_code, int) or isinstance(self.exit_code, bool):
            raise ValueError("The redaction manifest needs an integer exit code.")
        _require_digest(self.map_sha256, "token-map")
        _require_digest(self.review_sha256, "review")
        path = directory / NAME
        write_private_text(path, self._text())
        return path

    def _text(self) -> str:
        return (
            json.dumps(
                {
                    "schema": SCHEMA,
                    "batch": self.batch,
                    "run_id": self.run_id,
                    "reviewed": self.reviewed,
                    "exit_code": self.exit_code,
                    "map_sha256": self.map_sha256,
                    "review_sha256": self.review_sha256,
                    "outputs": [
                        {"path": path, "sha256": digest}
                        for path, digest in self.outputs
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    @classmethod
    def _read_descriptor(cls, descriptor: int) -> "RedactionManifest":
        state = os.fstat(descriptor)
        if state.st_size > MAX_MANIFEST_BYTES:
            raise ValueError("The redaction manifest is too large.")
        os.lseek(descriptor, 0, os.SEEK_SET)
        data = bytearray()
        while True:
            chunk = os.read(descriptor, min(64 * 1024, MAX_MANIFEST_BYTES + 1))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > MAX_MANIFEST_BYTES:
                raise ValueError("The redaction manifest is too large.")
        return cls.from_bytes(bytes(data))

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
        descriptor = -1
        eligible = False
        try:
            if not directory.is_dir():
                return False
            path_state = path.lstat()
            if not stat.S_ISREG(path_state.st_mode):
                return False
            if path_state.st_nlink != 1:
                raise RedactionInvalidationError(
                    "The redaction manifest must be one regular file with a single link."
                )
            eligible = True
            flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            opened_state = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened_state.st_mode)
                or opened_state.st_nlink != 1
                or (
                    opened_state.st_dev,
                    opened_state.st_ino,
                    opened_state.st_nlink,
                )
                != (path_state.st_dev, path_state.st_ino, path_state.st_nlink)
            ):
                raise RedactionInvalidationError(
                    "The redaction manifest changed during invalidation."
                )
            current = cls._read_descriptor(descriptor)
            if current.schema != SCHEMA:
                return False
            invalidated = cls(
                batch=current.batch,
                run_id=current.run_id,
                reviewed=False,
                exit_code=exit_code,
                map_sha256=current.map_sha256,
                review_sha256=current.review_sha256,
                outputs=current.outputs,
            )
            text = invalidated._text()
            expected = _manifest_identity(opened_state)
            try:
                _write_invalidation_atomically(path, text, expected)
                return True
            except (OSError, ValueError) as atomic_error:
                cls._invalidate_in_place(
                    path,
                    descriptor,
                    opened_state,
                    text.encode("utf-8"),
                    atomic_error,
                )
                return True
        except FileNotFoundError as error:
            if not eligible:
                return False
            raise RedactionInvalidationError(
                "The redaction manifest changed during invalidation."
            ) from error
        except RedactionInvalidationError:
            raise
        except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
            if not eligible:
                return False
            raise RedactionInvalidationError(
                f"Could not invalidate prior redaction state: {error}"
            ) from error
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    @staticmethod
    def _invalidate_in_place(
        path: Path,
        descriptor: int,
        opened_state,
        data: bytes,
        atomic_error: Exception,
    ) -> None:
        try:
            current_state = path.lstat()
            descriptor_state = os.fstat(descriptor)
            expected = (
                opened_state.st_dev,
                opened_state.st_ino,
                opened_state.st_nlink,
            )
            if (
                not stat.S_ISREG(current_state.st_mode)
                or not stat.S_ISREG(descriptor_state.st_mode)
                or current_state.st_nlink != 1
                or descriptor_state.st_nlink != 1
                or (
                    current_state.st_dev,
                    current_state.st_ino,
                    current_state.st_nlink,
                )
                != expected
                or (
                    descriptor_state.st_dev,
                    descriptor_state.st_ino,
                    descriptor_state.st_nlink,
                )
                != expected
            ):
                raise RedactionInvalidationError(
                    "The redaction manifest changed during invalidation."
                )
            os.lseek(descriptor, 0, os.SEEK_SET)
            os.ftruncate(descriptor, 0)
            offset = 0
            while offset < len(data):
                written = os.write(descriptor, data[offset:])
                if written <= 0:
                    raise OSError("The in-place manifest write made no progress.")
                offset += written
            os.ftruncate(descriptor, len(data))
            os.fsync(descriptor)
            os.fchmod(descriptor, 0o600)
            final_state = path.lstat()
            if (
                final_state.st_dev,
                final_state.st_ino,
                final_state.st_nlink,
            ) != expected:
                raise RedactionInvalidationError(
                    "The redaction manifest changed during invalidation."
                )
        except RedactionInvalidationError:
            raise
        except OSError as inplace_error:
            raise RedactionInvalidationError(
                "Could not invalidate prior redaction state: atomic replacement "
                f"failed ({atomic_error}); in-place write failed ({inplace_error})."
            ) from inplace_error

    @classmethod
    def load(cls, directory: Path) -> "RedactionManifest":
        path = directory / NAME
        return cls.from_bytes(
            read_bytes_limited(path, "The redaction manifest", MAX_MANIFEST_BYTES)
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "RedactionManifest":
        """Parse one exact saved-manifest byte snapshot."""
        if len(data) > MAX_MANIFEST_BYTES:
            raise ValueError("The redaction manifest is too large.")
        return cls._from_payload(json.loads(data.decode("utf-8")))

    @classmethod
    def _from_payload(cls, payload: object) -> "RedactionManifest":
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
            map_sha256 = _require_digest(payload.get("map_sha256"), "token-map")
            review_sha256 = _require_digest(payload.get("review_sha256"), "review")
        elif schema == SCHEMA_TWO:
            reviewed = False
            exit_code = payload.get("exit_code")
            if not isinstance(exit_code, int) or isinstance(exit_code, bool):
                raise ValueError("The redaction manifest needs an integer exit code.")
            map_sha256 = ""
            review_sha256 = ""
        elif schema == LEGACY_SCHEMA:
            reviewed = False
            exit_code = None
            map_sha256 = ""
            review_sha256 = ""
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
            map_sha256=map_sha256,
            review_sha256=review_sha256,
            outputs=tuple(sorted(outputs)),
            schema=schema,
        )

    def validate(self, directory: Path) -> None:
        """Require the exact token-named file set and bytes from redaction."""
        actual = {path.name for path in directory.iterdir() if path.name != NAME}
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
