"""Parse the instructor's rubric into the shape every later step reads.

The rubric belongs to the instructor. Nothing here rewrites it. The parser
reads three things: the criteria table, the letter-grade scale, and the
feedback protocol.

Reading the protocol matters. The PHIL 210 rubric already states its own
feedback shape -- inline annotations, a Bottom Line, What Worked, What to
Work On, and a sign-off. Taking that from the rubric is better than
inferring a structure the instructor already wrote down.

Each criterion also carries a digest of its own wording. That is how a run
weeks later can say which criteria drifted since calibration, instead of
saying only that something changed.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

_SEPARATOR = re.compile(r"^\|[\s:|-]+\|$")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`([^`]*)`")
_NUMBERED = re.compile(r"^(\d+)\.\s*(.+)$")
_POINTS = re.compile(r"(\d+)\s*pts?\b", re.IGNORECASE)
_LEVEL = re.compile(r"^(.+?)\s*\(([^)]*)\)\s*$")
_BAND = re.compile(
    r"^\*\s*\*\*\s*(?:<\s*(\d+)|(\d+)\s*[–—-]\s*(\d+))\s*\(([^)]+)\)\*\*"
)
_PROTOCOL_ITEM = re.compile(r"^(\d+)\.\s*\*\*(.+?)\*\*")
_PARENTHETICAL = re.compile(r"\s*\([^)]*\)")

# The two protocol items that are not sections of the written feedback. One
# is checked by looking for bracketed annotations in the essay, the other by
# matching the closing line, so both are held apart from `sections`.
_ANNOTATION_WORD = "annotation"
_SIGN_OFF_PREFIX = "sign-off"


class RubricError(Exception):
    """The rubric does not have the shape the grader needs."""


def _norm(text: str) -> str:
    """Collapse whitespace so a reflowed table is not read as an edit."""
    return re.sub(r"\s+", " ", text).strip()


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


@dataclass(frozen=True)
class Level:
    """One performance level of one criterion."""

    name: str
    band: str
    descriptor: str


@dataclass(frozen=True)
class Criterion:
    """One row of the rubric table."""

    number: int
    name: str
    points: int
    levels: tuple[Level, ...]
    digest: str


@dataclass(frozen=True)
class Band:
    """One row of the letter-grade scale. Bounds are inclusive."""

    low: int
    high: int
    label: str


@dataclass(frozen=True)
class FeedbackProtocol:
    """The feedback shape the rubric asks for, when it states one."""

    sections: tuple[str, ...] = ()
    sign_offs: tuple[str, ...] = ()
    requires_inline_annotations: bool = False


@dataclass(frozen=True)
class Rubric:
    """A parsed rubric."""

    path: Path
    title: str
    criteria: tuple[Criterion, ...]
    bands: tuple[Band, ...]
    protocol: FeedbackProtocol

    @property
    def total_points(self) -> int:
        return sum(c.points for c in self.criteria)

    @property
    def digest(self) -> str:
        """Bind every scoring and feedback rule that calibration depends on."""
        payload = {
            "criteria": [
                {
                    "number": item.number,
                    "name": item.name,
                    "points": item.points,
                    "levels": [
                        {
                            "name": level.name,
                            "band": level.band,
                            "descriptor": level.descriptor,
                        }
                        for level in item.levels
                    ],
                }
                for item in self.criteria
            ],
            "bands": [
                {"low": band.low, "high": band.high, "label": band.label}
                for band in self.bands
            ],
            "protocol": {
                "sections": list(self.protocol.sections),
                "sign_offs": list(self.protocol.sign_offs),
                "requires_inline_annotations": self.protocol.requires_inline_annotations,
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    @property
    def context_digest(self) -> str:
        """Bind the scale and feedback protocol outside named criteria."""
        payload = {
            "bands": [
                {"low": band.low, "high": band.high, "label": band.label}
                for band in self.bands
            ],
            "protocol": {
                "sections": list(self.protocol.sections),
                "sign_offs": list(self.protocol.sign_offs),
                "requires_inline_annotations": self.protocol.requires_inline_annotations,
            },
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    def criterion(self, number: int) -> Criterion:
        for item in self.criteria:
            if item.number == number:
                return item
        raise RubricError(f"No criterion numbered {number} in {self.path}")

    def letter_for(self, percent: float) -> str:
        """Return the rubric's own label for a percentage.

        The letter is computed, never chosen. A model that picks its own
        letter can drift from the scale the instructor published.
        """
        value = int(round(percent))
        for band in self.bands:
            if band.low <= value <= band.high:
                return band.label
        raise RubricError(
            f"The grading scale has no band covering {value}. "
            "Cover every score from 0 to 100."
        )


def _digest(name: str, points: int, levels: tuple[Level, ...]) -> str:
    parts = [_norm(name), str(points)]
    parts += [
        f"{_norm(level.name)}[{_norm(level.band)}]={_norm(level.descriptor)}"
        for level in levels
    ]
    payload = "\x1f".join(parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _level_parts(header_cell: str) -> tuple[str, str]:
    match = _LEVEL.match(_norm(header_cell))
    if match:
        return _norm(match.group(1)), _norm(match.group(2))
    return _norm(header_cell), ""


def _parse_criteria(lines: list[str]) -> tuple[Criterion, ...]:
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if header is None:
            if stripped.startswith("|") and "criterion" in stripped.lower():
                header = _cells(stripped)
            continue
        if _SEPARATOR.match(stripped):
            continue
        if not stripped.startswith("|"):
            if rows:
                break
            continue
        rows.append(_cells(stripped))

    if header is None or not rows:
        raise RubricError(
            "No criteria table found. The table's header row must name a "
            "Criterion column, and each following row must be one criterion."
        )

    weight_index = len(header) - 1
    for index, cell in enumerate(header):
        if cell.strip().lower() in {"weight", "points", "weighting"}:
            weight_index = index
            break
    level_headers = [_level_parts(cell) for cell in header[1:weight_index]]

    criteria: list[Criterion] = []
    for row in rows:
        if len(row) != len(header):
            raise RubricError(
                f"A criteria row has {len(row)} cells but the header has "
                f"{len(header)}: {row[0]!r}"
            )
        bold = _BOLD.search(row[0])
        raw_name = _norm(bold.group(1) if bold else row[0])
        numbered = _NUMBERED.match(raw_name)
        if not numbered:
            raise RubricError(
                f"Number each criterion, as in '1. Thesis': {raw_name!r}"
            )
        points_match = _POINTS.search(row[weight_index])
        if not points_match:
            raise RubricError(
                f"Give each criterion a point value, as in '25 pts': "
                f"{row[weight_index]!r}"
            )
        levels = tuple(
            Level(name=name, band=band, descriptor=_norm(row[1 + offset]))
            for offset, (name, band) in enumerate(level_headers)
        )
        points = int(points_match.group(1))
        name = numbered.group(2)
        criteria.append(
            Criterion(
                number=int(numbered.group(1)),
                name=name,
                points=points,
                levels=levels,
                digest=_digest(name, points, levels),
            )
        )
    return tuple(criteria)


def _parse_bands(lines: list[str]) -> tuple[Band, ...]:
    bands: list[Band] = []
    for line in lines:
        match = _BAND.match(line.strip())
        if not match:
            continue
        below, low, high, label = match.groups()
        if below is not None:
            bands.append(Band(0, int(below) - 1, _norm(label)))
        else:
            bands.append(Band(int(low), int(high), _norm(label)))
    if not bands:
        raise RubricError(
            "No grading scale found. List each band as "
            "'* **90-92 (A-)**: ...' so the letter can be computed."
        )
    return tuple(sorted(bands, key=lambda band: band.low))


def _parse_protocol(lines: list[str]) -> FeedbackProtocol:
    start: int | None = None
    for index, line in enumerate(lines):
        if line.startswith("## ") and "feedback protocol" in line.lower():
            start = index
            break
    if start is None:
        return FeedbackProtocol()

    sections: list[str] = []
    sign_offs: tuple[str, ...] = ()
    inline = False
    for line in lines[start + 1 :]:
        if line.startswith("## "):
            break
        match = _PROTOCOL_ITEM.match(line.strip())
        if not match:
            continue
        label = _norm(_CODE.sub("", match.group(2)))
        bare = _norm(_PARENTHETICAL.sub("", label))
        if _ANNOTATION_WORD in bare.lower():
            inline = True
        elif bare.lower().startswith(_SIGN_OFF_PREFIX):
            sign_offs = tuple(
                _norm(found) for found in _CODE.findall(line) if _norm(found)
            )
        else:
            sections.append(bare)
    return FeedbackProtocol(
        sections=tuple(sections),
        sign_offs=sign_offs,
        requires_inline_annotations=inline,
    )


def load_rubric(path: Path) -> Rubric:
    """Read a rubric from Markdown."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = next(
        (_norm(line[2:]) for line in lines if line.startswith("# ")), path.stem
    )
    return Rubric(
        path=path,
        title=title,
        criteria=_parse_criteria(lines),
        bands=_parse_bands(lines),
        protocol=_parse_protocol(lines),
    )
