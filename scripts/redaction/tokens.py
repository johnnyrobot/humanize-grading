"""Allocate stable tokens for redacted values and store the mapping.

The saved map is the only artifact that holds real identities. Local scripts
write and read it. Nothing else must load it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

KINDS = ("STUDENT", "PERSON", "EMAIL", "PHONE", "ID", "URL")


@dataclass(frozen=True)
class Entity:
    """One redacted value and the token that stands in for it."""

    token: str
    kind: str
    value: str
    file: str


class TokenMap:
    """Hand out stable tokens and remember what each one replaced."""

    def __init__(self, batch: str) -> None:
        self.batch = batch
        self._by_key: dict[tuple[str, str], Entity] = {}
        self._by_token: dict[str, Entity] = {}

    def allocate(self, kind: str, value: str, file: str) -> str:
        """Return the token for this value, creating it on first sight."""
        if kind not in KINDS:
            raise ValueError(f"Unknown kind: {kind}. Use one of {KINDS}.")
        key = (kind, value.casefold())
        if key in self._by_key:
            return self._by_key[key].token

        count = sum(1 for e in self._by_token.values() if e.kind == kind)
        token = f"{kind}_{count + 1:02d}"
        entity = Entity(token, kind, value, file)
        self._by_key[key] = entity
        self._by_token[token] = entity
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
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "batch": self.batch,
            "entities": {
                token: {"kind": e.kind, "value": e.value, "file": e.file}
                for token, e in self._by_token.items()
            },
        }
        path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.chmod(path, 0o600)

    @classmethod
    def load(cls, path: Path) -> "TokenMap":
        """Read a saved map."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        tmap = cls(payload["batch"])
        for token, record in payload["entities"].items():
            entity = Entity(token, record["kind"], record["value"], record["file"])
            tmap._by_token[token] = entity
            tmap._by_key[(entity.kind, entity.value.casefold())] = entity
        return tmap
