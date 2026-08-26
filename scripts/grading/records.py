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
MAX_RECORD_BYTES = 1_048_576
MAX_CRITERIA = 50
MAX_JUSTIFICATIONS_PER_CRITERION = 16
MAX_QUOTE_CHARACTERS = 2_000

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


def _text(value: object, name: str) -> str:
    if not isinstance(value, str):
        raise RecordError(f"{name} must be JSON text.")
    return value


def _integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RecordError(f"{name} must be a JSON integer.")
    return value


def _optional_integer(value: object, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


# A quote may span a line WRAP -- a model reflows text, and it is still the
# same passage. It may never span a blank line: the tail of one paragraph
# stitched to the head of the next is two passages, and certifying it as one
# would let a fabricated quote pass as evidence.
_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")


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
    # Anchor only where the boundary character is alphanumeric, so a quote
    # ending in "." still matches while "one cat" no longer matches inside
    # "one category".
    body = r"\s+".join(re.escape(word) for word in words)
    prefix = r"\b" if words[0][:1].isalnum() else ""
    suffix = r"\b" if words[-1][-1:].isalnum() else ""
    pattern = re.compile(prefix + body + suffix)
    if occurrence is not None and occurrence < 1:
        raise RecordError(
            f"Occurrence must be 1 or greater, not {occurrence}: {quote!r}"
        )
    count = 0
    selected = None
    for match in pattern.finditer(source):
        if _PARAGRAPH_BREAK.search(match.group(0)):
            continue
        count += 1
        if occurrence is None and count == 2:
            raise RecordError(
                f"Quote appears 2 times or more. Lengthen it, or set "
                f'"occurrence": {quote!r}'
            )
        if count == (occurrence or 1):
            selected = match
            if occurrence is not None:
                break
    if selected is None and count == 0:
        raise RecordError(f"Quote is not in the source: {quote!r}")
    if selected is None:
        raise RecordError(
            f"Quote appears {count} times, so occurrence "
            f"{occurrence} does not exist: {quote!r}"
        )
    match = selected
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
        if not isinstance(payload, dict):
            raise RecordError("Every justification must be a JSON object.")
        if "quote" not in payload:
            raise RecordError("Every justification needs a quote.")
        allowed = {"quote", "note", "occurrence", "offset", "line", "length"}
        if set(payload) - allowed:
            raise RecordError("A justification has unknown fields.")
        if not isinstance(payload["quote"], str):
            raise RecordError("A justification quote must be text.")
        if len(payload["quote"]) > MAX_QUOTE_CHARACTERS:
            raise RecordError("A justification quote is too large.")
        occurrence = payload.get("occurrence")
        if occurrence is not None and (
            not isinstance(occurrence, int)
            or isinstance(occurrence, bool)
            or not 1 <= occurrence <= 100
        ):
            raise RecordError("A justification occurrence must be from 1 to 100.")
        note = payload.get("note", "")
        if not isinstance(note, str):
            raise RecordError("A justification note must be text.")
        return cls(
            quote=payload["quote"],
            note=note,
            occurrence=occurrence,
            offset=_optional_integer(payload.get("offset"), "Justification offset"),
            line=_optional_integer(payload.get("line"), "Justification line"),
            length=_optional_integer(payload.get("length"), "Justification length"),
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
        if not isinstance(payload, dict):
            raise RecordError("Every criterion must be a JSON object.")
        for name in ("number", "name", "points_possible", "level", "assessable"):
            if name not in payload:
                raise RecordError(f"A criterion is missing {name!r}.")
        allowed = {
            "number",
            "name",
            "points_possible",
            "score",
            "level",
            "assessable",
            "justifications",
        }
        if set(payload) - allowed:
            raise RecordError("A criterion has unknown fields.")
        number = _integer(payload["number"], "Criterion number")
        points_possible = _integer(
            payload["points_possible"], "Criterion points_possible"
        )
        score = payload.get("score")
        if score is not None:
            score = _integer(score, "Criterion score")
        if not isinstance(payload["assessable"], bool):
            raise RecordError("Criterion assessable must be a JSON boolean.")
        justifications = payload.get("justifications", [])
        if not isinstance(justifications, list):
            raise RecordError("Criterion justifications must be a list.")
        if len(justifications) > MAX_JUSTIFICATIONS_PER_CRITERION:
            raise RecordError("A criterion has too many justifications.")
        return cls(
            number=number,
            name=_text(payload["name"], "Criterion name"),
            points_possible=points_possible,
            score=score,
            level=_text(payload["level"], "Criterion level"),
            assessable=payload["assessable"],
            justifications=tuple(
                Justification.from_dict(item)
                for item in justifications
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
        if not isinstance(payload, dict):
            raise RecordError("The grade record must be a JSON object.")
        if payload.get("schema") != SCHEMA:
            raise RecordError(
                f"Unknown record schema {payload.get('schema')!r}. "
                f"This grader writes {SCHEMA!r}."
            )
        missing = [name for name in _REQUIRED if name not in payload]
        if missing:
            raise RecordError(f"The record is missing: {', '.join(missing)}")
        if set(payload) - set(_REQUIRED):
            raise RecordError("The grade record has unknown fields.")
        total = _integer(payload["total"], "total")
        total_possible = _integer(payload["total_possible"], "total_possible")
        criteria = payload["criteria"]
        if not isinstance(criteria, list):
            raise RecordError("The grade record criteria must be a list.")
        if len(criteria) > MAX_CRITERIA:
            raise RecordError("The grade record has too many criteria.")
        pattern_density = payload["pattern_density"]
        if not isinstance(pattern_density, dict):
            raise RecordError("pattern_density must be a JSON object.")
        status = _text(payload["status"], "status")
        if status != "draft":
            raise RecordError("status must be 'draft'.")
        return cls(
            schema=_text(payload["schema"], "schema"),
            batch=_text(payload["batch"], "batch"),
            token=_text(payload["token"], "token"),
            source=_text(payload["source"], "source"),
            course=_text(payload["course"], "course"),
            rubric=_text(payload["rubric"], "rubric"),
            profile=_text(payload["profile"], "profile"),
            criteria=tuple(CriterionScore.from_dict(item) for item in criteria),
            total=total,
            total_possible=total_possible,
            letter=_text(payload["letter"], "letter"),
            pattern_density=dict(pattern_density),
            status=status,
        )

    @classmethod
    def load(cls, path: Path) -> "GradeRecord":
        try:
            if path.stat().st_size > MAX_RECORD_BYTES:
                raise RecordError(
                    f"{path.name} is too large. Grade records are limited to 1 MiB."
                )
        except OSError as error:
            raise RecordError(f"Cannot read {path}: {error}") from error
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise RecordError(f"{path.name} is not valid JSON: {error}") from error
        return cls.from_dict(payload)
