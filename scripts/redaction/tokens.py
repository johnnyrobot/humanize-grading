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

    def __init__(self, batch: str, *, run_id: str | None = None) -> None:
        self.batch = batch
        self.run_id = run_id or secrets.token_hex(16)
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
            "run_id": self.run_id,
            "entities": {
                token: {"kind": e.kind, "value": e.value, "file": e.file}
                for token, e in self._by_token.items()
            },
        }
        write_private_text(path, json.dumps(payload, indent=2, sort_keys=True))

    @classmethod
    def load(cls, path: Path) -> "TokenMap":
        """Read a saved map."""
        payload = json.loads(path.read_text(encoding="utf-8"))
        run_id = payload.get("run_id")
        if not isinstance(run_id, str) or len(run_id) < 16:
            raise ValueError("The token map has no valid redaction-run ID.")
        tmap = cls(payload["batch"], run_id=run_id)
        for token, record in payload["entities"].items():
            entity = Entity(token, record["kind"], record["value"], record["file"])
            tmap._by_token[token] = entity
            tmap._by_key[(entity.kind, entity.value.casefold())] = entity
        return tmap
