"""The grade record: what the engine writes beside each Markdown draft.

Markdown is for the instructor. JSON is what makes the batch consistency
pass, and any later comparison, possible without reparsing prose.

The record's load-bearing field is the justification quote. `locate` is the
reason it can be trusted: the model supplies the words, and this module
supplies the position by finding those words in the redacted source. A quote
that is not there fails. A quote too short to identify a passage fails. A
quote that appears twice fails until the model pins which one it means.

Nothing here loads the token map. Verification works against the redacted
source, which is the file the model actually read.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = "grade-record/1"
CANNOT_ASSESS = "cannot assess"
MIN_QUOTE_WORDS = 4

_REQUIRED = (
    "schema",
    "batch",
    "token",
    "source",
    "course",
    "rubric",
    "profile",
    "criteria",
    "total",
    "total_possible",
    "letter",
    "pattern_density",
    "status",
)


class RecordError(Exception):
    """The record cannot be read, or a quote in it cannot be trusted."""


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def locate(
    quote: str, source: str, occurrence: int | None = None
) -> tuple[int, int, int]:
    """Find a quote in the source and return (offset, line, length).

    Whitespace is flexible, so a quote the model reflowed across a line
    break still matches the essay it came from. Everything else is strict.
    """
    needle = _norm(quote)
    words = needle.split(" ")
    if len(words) < MIN_QUOTE_WORDS:
        raise RecordError(
            f"Quote is too short to be evidence. Use at least "
            f"{MIN_QUOTE_WORDS} words: {quote!r}"
        )
    pattern = re.compile(r"\s+".join(re.escape(word) for word in words))
    matches = list(pattern.finditer(source))
    if not matches:
        raise RecordError(f"Quote is not in the source: {quote!r}")
    if len(matches) > 1 and occurrence is None:
        raise RecordError(
            f"Quote appears {len(matches)} times. Lengthen it, or set "
            f'"occurrence": {quote!r}'
        )
    index = (occurrence or 1) - 1
    if index < 0 or index >= len(matches):
        raise RecordError(
            f"Quote appears {len(matches)} times, so occurrence "
            f"{occurrence} does not exist: {quote!r}"
        )
    match = matches[index]
    line = source.count("\n", 0, match.start()) + 1
    return match.start(), line, match.end() - match.start()


@dataclass
class Justification:
    """One quote that supports one score.

    `offset`, `line`, and `length` are written by verification, not by the
    model. `occurrence` is the one positional field the model may set, and
    only when the same words appear more than once.
    """

    quote: str
    note: str = ""
    occurrence: int | None = None
    offset: int | None = None
    line: int | None = None
    length: int | None = None

    def as_dict(self) -> dict:
        payload: dict = {"quote": self.quote, "note": self.note}
        for name in ("occurrence", "offset", "line", "length"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "Justification":
        if "quote" not in payload:
            raise RecordError("Every justification needs a quote.")
        return cls(
            quote=str(payload["quote"]),
            note=str(payload.get("note", "")),
            occurrence=payload.get("occurrence"),
            offset=payload.get("offset"),
            line=payload.get("line"),
            length=payload.get("length"),
        )


@dataclass
class CriterionScore:
    """One criterion's score, or an honest refusal to give one."""

    number: int
    name: str
    points_possible: int
    score: int | None
    level: str
    assessable: bool
    justifications: tuple[Justification, ...] = ()

    def as_dict(self) -> dict:
        return {
            "number": self.number,
            "name": self.name,
            "points_possible": self.points_possible,
            "score": self.score,
            "level": self.level,
            "assessable": self.assessable,
            "justifications": [j.as_dict() for j in self.justifications],
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "CriterionScore":
        for name in ("number", "name", "points_possible", "level", "assessable"):
            if name not in payload:
                raise RecordError(f"A criterion is missing {name!r}.")
        return cls(
            number=int(payload["number"]),
            name=str(payload["name"]),
            points_possible=int(payload["points_possible"]),
            score=payload.get("score"),
            level=str(payload["level"]),
            assessable=bool(payload["assessable"]),
            justifications=tuple(
                Justification.from_dict(item)
                for item in payload.get("justifications", [])
            ),
        )


@dataclass
class GradeRecord:
    """One submission's scores, evidence, and pattern-density report."""

    batch: str
    token: str
    source: str
    course: str
    rubric: str
    profile: str
    criteria: tuple[CriterionScore, ...]
    total: int
    total_possible: int
    letter: str
    pattern_density: dict = field(default_factory=dict)
    status: str = "draft"
    schema: str = SCHEMA

    def as_dict(self) -> dict:
        return {
            "schema": self.schema,
            "batch": self.batch,
            "token": self.token,
            "source": self.source,
            "course": self.course,
            "rubric": self.rubric,
            "profile": self.profile,
            "criteria": [c.as_dict() for c in self.criteria],
            "total": self.total,
            "total_possible": self.total_possible,
            "letter": self.letter,
            "pattern_density": self.pattern_density,
            "status": self.status,
        }

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.as_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @classmethod
    def from_dict(cls, payload: dict) -> "GradeRecord":
        if payload.get("schema") != SCHEMA:
            raise RecordError(
                f"Unknown record schema {payload.get('schema')!r}. "
                f"This grader writes {SCHEMA!r}."
            )
        missing = [name for name in _REQUIRED if name not in payload]
        if missing:
            raise RecordError(f"The record is missing: {', '.join(missing)}")
        return cls(
            schema=str(payload["schema"]),
            batch=str(payload["batch"]),
            token=str(payload["token"]),
            source=str(payload["source"]),
            course=str(payload["course"]),
            rubric=str(payload["rubric"]),
            profile=str(payload["profile"]),
            criteria=tuple(
                CriterionScore.from_dict(item) for item in payload["criteria"]
            ),
            total=int(payload["total"]),
            total_possible=int(payload["total_possible"]),
            letter=str(payload["letter"]),
            pattern_density=dict(payload["pattern_density"]),
            status=str(payload["status"]),
        )

    @classmethod
    def load(cls, path: Path) -> "GradeRecord":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RecordError(f"{path.name} is not valid JSON: {error}") from error
        return cls.from_dict(payload)
