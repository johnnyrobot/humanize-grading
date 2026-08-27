"""Allocate stable tokens for redacted values and store the mapping.

The saved map is the only artifact that reversibly maps stable tokens to real
identities. Local scripts write and read it. Nothing else must load it.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path

from .private_files import write_private_text
try:
    from scripts.path_safety import read_bytes_limited
except ModuleNotFoundError:  # direct scripts/redact.py execution
    from path_safety import read_bytes_limited

KINDS = ("STUDENT", "PERSON", "EMAIL", "PHONE", "ID", "URL")
MAX_TOKEN_MAP_BYTES = 5 * 1024 * 1024
MAX_TOKEN_MAP_ENTITIES = 10_000
MAX_TOKEN_MAP_VALUE_CHARACTERS = 1_000_000
MAX_TOKEN_MAP_ENTITY_CHARACTERS = 1_000_000
MAX_TOKEN_MAP_ID_CHARACTERS = 128


class TokenMapLimitError(ValueError):
    """The private token map exceeded a public resource limit."""


@dataclass(frozen=True)
class Entity:
    """One redacted value and the token that stands in for it."""

    token: str
    kind: str
    value: str
    file: str


class TokenMap:
    """Hand out stable tokens and remember what each one replaced."""

    def __init__(self, batch: str, *, run_id: str | None = None) -> None:
        self.batch = batch
        self.run_id = run_id or secrets.token_hex(16)
        self._by_key: dict[tuple[str, str], Entity] = {}
        self._by_token: dict[str, Entity] = {}
        self._entity_characters = 0
        self._kind_counts = {kind: 0 for kind in KINDS}

    def allocate(self, kind: str, value: str, file: str) -> str:
        """Return the token for this value, creating it on first sight."""
        if kind not in KINDS:
            raise ValueError(f"Unknown kind: {kind}. Use one of {KINDS}.")
        key = (kind, value.casefold())
        if key in self._by_key:
            return self._by_key[key].token

        count = self._kind_counts[kind]
        token = f"{kind}_{count + 1:02d}"
        entity = Entity(token, kind, value, file)
        entity_characters = len(token) + len(kind) + len(value) + len(file)
        if len(self._by_token) >= MAX_TOKEN_MAP_ENTITIES:
            raise TokenMapLimitError(
                f"The token map may contain at most {MAX_TOKEN_MAP_ENTITIES} entities."
            )
        if kind == "ID" and len(value) > MAX_TOKEN_MAP_ID_CHARACTERS:
            raise TokenMapLimitError(
                "The ID value is too long. The limit is "
                f"{MAX_TOKEN_MAP_ID_CHARACTERS} characters."
            )
        if len(value) > MAX_TOKEN_MAP_VALUE_CHARACTERS:
            raise TokenMapLimitError(
                "One token-map value is too long. The limit is "
                f"{MAX_TOKEN_MAP_VALUE_CHARACTERS} characters."
            )
        if self._entity_characters + entity_characters > MAX_TOKEN_MAP_ENTITY_CHARACTERS:
            raise TokenMapLimitError(
                "The token-map identity text exceeds its character limit."
            )
        self._by_key[key] = entity
        self._by_token[token] = entity
        self._entity_characters += entity_characters
        self._kind_counts[kind] = count + 1
        return token

    def value_for(self, token: str) -> str | None:
        """Return the real value behind a token, or None if unknown."""
        entity = self._by_token.get(token)
        return entity.value if entity else None

    def tokens(self) -> dict[str, Entity]:
        """Return every allocated token."""
        return dict(self._by_token)

    def save(self, path: Path) -> None:
        """Write the map owner-only."""
        write_private_text(path, self.to_bytes().decode("utf-8"))

    def to_bytes(self) -> bytes:
        """Serialize the complete map within its public byte limit."""
        payload = {
            "batch": self.batch,
            "run_id": self.run_id,
            "entities": {
                token: {"kind": e.kind, "value": e.value, "file": e.file}
                for token, e in self._by_token.items()
            },
        }
        data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        if len(data) > MAX_TOKEN_MAP_BYTES:
            raise TokenMapLimitError(
                f"The token map is too large. The limit is {MAX_TOKEN_MAP_BYTES} bytes."
            )
        return data

    @classmethod
    def load(cls, path: Path) -> "TokenMap":
        """Read a saved map."""
        return cls.from_bytes(
            read_bytes_limited(path, "The token map", MAX_TOKEN_MAP_BYTES)
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> "TokenMap":
        """Parse one exact saved-map byte snapshot."""
        if len(data) > MAX_TOKEN_MAP_BYTES:
            raise TokenMapLimitError(
                f"The token map is too large. The limit is {MAX_TOKEN_MAP_BYTES} bytes."
            )
        payload = json.loads(data.decode("utf-8"))
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or len(run_id) < 16:
            raise ValueError("The token map has no valid redaction-run ID.")
        tmap = cls(payload["batch"], run_id=run_id)
        for token, record in payload["entities"].items():
            entity = Entity(token, record["kind"], record["value"], record["file"])
            prefix, separator, raw_number = token.rpartition("_")
            if (
                separator != "_"
                or prefix != entity.kind
                or entity.kind not in KINDS
                or len(raw_number) < 2
                or not raw_number.isdigit()
                or int(raw_number) < 1
            ):
                raise ValueError("The token map has a malformed entity token.")
            number = int(raw_number)
            entity_characters = sum(
                len(value) for value in (token, entity.kind, entity.value, entity.file)
            )
            if len(tmap._by_token) >= MAX_TOKEN_MAP_ENTITIES:
                raise TokenMapLimitError(
                    f"The token map may contain at most {MAX_TOKEN_MAP_ENTITIES} entities."
                )
            if len(entity.value) > MAX_TOKEN_MAP_VALUE_CHARACTERS:
                raise TokenMapLimitError("One token-map value is too long.")
            if (
                entity.kind == "ID"
                and len(entity.value) > MAX_TOKEN_MAP_ID_CHARACTERS
            ):
                raise TokenMapLimitError("The ID value is too long.")
            if (
                tmap._entity_characters + entity_characters
                > MAX_TOKEN_MAP_ENTITY_CHARACTERS
            ):
                raise TokenMapLimitError(
                    "The token-map identity text exceeds its character limit."
                )
            tmap._by_token[token] = entity
            tmap._by_key[(entity.kind, entity.value.casefold())] = entity
            tmap._entity_characters += entity_characters
            tmap._kind_counts[entity.kind] = max(
                tmap._kind_counts[entity.kind], number
            )
        return tmap
