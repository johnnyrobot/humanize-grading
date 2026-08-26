"""Identity-free evidence that binds one redaction result to its token map."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

SCHEMA = "redaction-batch/1"
NAME = "_redaction.json"


@dataclass(frozen=True)
class RedactionManifest:
    batch: str
    run_id: str

    def save(self, directory: Path) -> Path:
        path = directory / NAME
        path.write_text(
            json.dumps(
                {"schema": SCHEMA, "batch": self.batch, "run_id": self.run_id},
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    @classmethod
    def load(cls, directory: Path) -> "RedactionManifest":
        path = directory / NAME
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
            raise ValueError("The redaction manifest has an unknown schema.")
        batch = payload.get("batch")
        run_id = payload.get("run_id")
        if not isinstance(batch, str) or not batch:
            raise ValueError("The redaction manifest needs a grading batch.")
        if not isinstance(run_id, str) or len(run_id) < 16:
            raise ValueError("The redaction manifest needs a redaction-run ID.")
        return cls(batch=batch, run_id=run_id)
