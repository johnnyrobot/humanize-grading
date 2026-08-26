"""Read a calibration profile, and say when the rubric under it moved.

The profile is plain Markdown on purpose. The instructor owns it, edits it,
and re-runs calibration against it over time, so it must stay readable
without a tool.

It is also the one calibration artifact a cloud run reads. Everything it
holds is something a cloud model sees, which is why the comment bank stores
the instructor's sentences with names replaced by [STUDENT] and stores no
student essay text at all.

The fingerprint table is what makes drift reportable by name. Storing one
digest for the whole rubric would only say that something changed. Storing
one per criterion says which.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.path_safety import read_text_limited, require_component
from scripts.grading.rubric import Rubric

FINGERPRINT_SECTION = "Rubric fingerprint"

REQUIRED_SECTIONS = (
    "Rubric fingerprint",
    "Rubric, normalized",
    "Weighting in practice",
    "Severity",
    "Feedback structure",
    "Voice",
    "Comment bank",
    "Never-use list",
)

_META = re.compile(r"^\*\*(.+?)\*\*\s*:\s*(.+?)\s*$")
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_ROW = re.compile(r"^\|\s*(\d+)\.\s*(.+?)\s*\|\s*([0-9a-f]{6,})\s*\|\s*$")
_BULLET = re.compile(r"^[-*]\s+(.+?)\s*$")


class ProfileError(Exception):
    """The profile is missing, incomplete, or unreadable."""


def profile_path(course: str, root: Path = Path(".grading")) -> Path:
    """Return where a course's profile lives."""
    require_component(course, "The course")
    return root / "profiles" / f"{course}.md"


@dataclass
class Profile:
    """One instructor's calibrated standards for one course."""

    path: Path
    course: str = ""
    calibrated: str = ""
    rubric_path: str = ""
    rubric_digest: str = ""
    rubric_context_digest: str = ""
    samples: int = 0
    holdout: str = ""
    fingerprint: dict[int, str] = field(default_factory=dict)
    criterion_names: dict[int, str] = field(default_factory=dict)
    never_use: tuple[str, ...] = ()
    sections: dict[str, str] = field(default_factory=dict)

    def drift(self, rubric: Rubric) -> list[str]:
        """Return the criteria that changed since calibration, by name.

        A criterion whose wording changed, one that was added, and one that
        was removed all count. Each is reported as "3. Counterargument" so
        the instructor can see exactly what moved.
        """
        drifted: list[str] = []
        current = {c.number: c for c in rubric.criteria}
        for number in sorted(set(current) | set(self.fingerprint)):
            criterion = current.get(number)
            recorded = self.fingerprint.get(number)
            if criterion is None:
                drifted.append(f"{number}. {self.criterion_names.get(number, '')}".strip())
            elif recorded is None or recorded != criterion.digest:
                drifted.append(f"{number}. {criterion.name}")
        if self.rubric_context_digest != rubric.context_digest:
            drifted.append("grading scale or feedback protocol")
        return drifted


def load_profile(path: Path) -> Profile:
    """Read a profile and refuse anything incomplete."""
    if not path.is_file():
        raise ProfileError(
            f"No profile at {path}. Run calibration for this course before grading."
        )
    text = read_text_limited(path, "The calibration profile")
    lines = text.splitlines()

    meta: dict[str, str] = {}
    sections: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        heading = _HEADING.match(line)
        if heading:
            current = heading.group(1).strip()
            sections.setdefault(current, [])
            continue
        if not current:
            found = _META.match(line.strip())
            if found:
                meta[found.group(1).strip()] = found.group(2).strip()
            continue
        sections[current].append(line)

    missing = [name for name in REQUIRED_SECTIONS if name not in sections]
    if missing:
        raise ProfileError(
            f"{path.name} is missing these sections: {', '.join(missing)}"
        )

    fingerprint: dict[int, str] = {}
    names: dict[int, str] = {}
    for line in sections[FINGERPRINT_SECTION]:
        row = _ROW.match(line.strip())
        if row:
            number = int(row.group(1))
            fingerprint[number] = row.group(3)
            names[number] = row.group(2)
    if not fingerprint:
        raise ProfileError(
            f"{path.name} has an empty rubric fingerprint. Each row must read "
            "'| 1. Criterion name | digest |'."
        )

    never_use = tuple(
        match.group(1)
        for match in (_BULLET.match(line.strip()) for line in sections["Never-use list"])
        if match
    )

    course = meta.get("Course", "")
    if not course:
        raise ProfileError(f"{path.name} has no '**Course**:' line.")
    rubric_digest = meta.get("Rubric digest", "")
    if not rubric_digest:
        raise ProfileError(f"{path.name} has no '**Rubric digest**:' line.")
    rubric_context_digest = meta.get("Rubric context digest", "")
    if not rubric_context_digest:
        raise ProfileError(
            f"{path.name} has no '**Rubric context digest**:' line."
        )

    return Profile(
        path=path,
        course=course,
        calibrated=meta.get("Calibrated", ""),
        rubric_path=meta.get("Rubric", ""),
        rubric_digest=rubric_digest,
        rubric_context_digest=rubric_context_digest,
        samples=int(meta.get("Samples", "0") or 0),
        holdout=meta.get("Holdout", ""),
        fingerprint=fingerprint,
        criterion_names=names,
        never_use=never_use,
        sections={name: "\n".join(body).strip() for name, body in sections.items()},
    )
