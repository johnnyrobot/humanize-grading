"""Split a graded sample into the student's half and the instructor's half.

Only the instructor's half may reach the profile. That is the whole reason
this module exists: the profile is read during cloud runs, so anything it
holds is something a cloud model sees. The instructor's comments are the
voice; the student's essay is not needed to learn it.

Names get scrubbed on the way out. An instructor writes "Maya, this is
exceptional work", and that first name identifies a past student just as
surely as a header would. The replacement is the literal `[STUDENT]`, which
deliberately does NOT match rehydrate.py's token pattern -- a `STUDENT_01`
leaking out of a profile would be reattached to whichever student holds that
token in the CURRENT batch, which is a misattribution, not a redaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from scripts.redaction.roster import RosterEntry, bare_name_variants, full_name_variants

PLACEHOLDER = "[STUDENT]"

_HEADER_FIELD = re.compile(r"^\*\*(.+?)\*\*\s*:\s*(.+?)\s*$")
_SUBMISSION_HEADING = re.compile(r"^###\s+Student Submission", re.IGNORECASE)
_FEEDBACK_HEADING = re.compile(r"^###\s+Instructor Feedback", re.IGNORECASE)
_GRADE = re.compile(r"^\*\*Grade\*\*\s*:\s*(\d+)\s*/\s*(\d+)\s*\(([^)]+)\)")
_LABEL = re.compile(r"^\*\*(.+?)\*\*\s*:?\s*(.*)$")
_BULLET = re.compile(r"^\*\s+(.+)$")
_SIGN_OFF = re.compile(r"^\s*[—–-]{1,2}\s*(.+?)\s*$")
_ANNOTATION = re.compile(r"\[([^\[\]]{10,})\]")


class SampleError(Exception):
    """The graded sample does not have the shape calibration needs."""


@dataclass(frozen=True)
class Annotation:
    """One inline bracketed comment and where it sits in the essay."""

    text: str
    offset: int


@dataclass
class GradedSample:
    """One piece of work the instructor already graded."""

    path: Path
    header: dict[str, str] = field(default_factory=dict)
    essay: str = ""
    annotations: tuple[Annotation, ...] = ()
    score: int = 0
    possible: int = 100
    letter: str = ""
    bottom_line: str = ""
    what_worked: tuple[str, ...] = ()
    what_to_work_on: tuple[str, ...] = ()
    closing: str = ""
    sign_off: str = ""

    def comments_only(self) -> str:
        """Return everything the instructor wrote, and nothing the student wrote.

        This is the calibration input. Run it through scrub_names before it
        reaches a profile.
        """
        parts: list[str] = []
        for annotation in self.annotations:
            parts.append(f"[{annotation.text}]")
        if self.bottom_line:
            parts.append(self.bottom_line)
        parts.extend(self.what_worked)
        parts.extend(self.what_to_work_on)
        if self.closing:
            parts.append(self.closing)
        if self.sign_off:
            parts.append(self.sign_off)
        return "\n\n".join(parts)


def _split_sections(text: str) -> tuple[str, str, str]:
    """Return the header block, the essay, and the feedback."""
    lines = text.splitlines()
    submission_at: int | None = None
    feedback_at: int | None = None
    for index, line in enumerate(lines):
        if submission_at is None and _SUBMISSION_HEADING.match(line):
            submission_at = index
        elif _FEEDBACK_HEADING.match(line):
            feedback_at = index
            break
    if feedback_at is None:
        raise SampleError(
            "The sample has no instructor feedback section. Add a "
            "'### Instructor Feedback & Grading' heading."
        )
    start = 0 if submission_at is None else submission_at + 1
    header = "\n".join(lines[: submission_at if submission_at is not None else 0])
    essay = "\n".join(lines[start:feedback_at])
    feedback = "\n".join(lines[feedback_at + 1 :])
    return header, essay.strip("\n"), feedback.strip("\n")


def _parse_header(block: str) -> dict[str, str]:
    header: dict[str, str] = {}
    for line in block.splitlines():
        match = _HEADER_FIELD.match(line.strip())
        if match:
            header[match.group(1).strip()] = match.group(2).strip()
    return header


def _parse_feedback(feedback: str) -> dict[str, object]:
    grade_match = None
    for line in feedback.splitlines():
        grade_match = _GRADE.match(line.strip())
        if grade_match:
            break
    if not grade_match:
        raise SampleError(
            "No Grade line found. Write it as '**Grade**: 94/100 (A)'."
        )

    label = ""
    prose: dict[str, list[str]] = {}
    bullets: dict[str, list[str]] = {}
    trailing: list[str] = []
    sign_off = ""
    seen_a_section = False

    for raw in feedback.splitlines():
        line = raw.strip()
        if not line:
            continue
        if _GRADE.match(line):
            continue
        sign = _SIGN_OFF.match(line)
        if sign and not line.startswith("*") and len(line) <= 40:
            sign_off = line
            continue
        labelled = _LABEL.match(line)
        if labelled and not _BULLET.match(line):
            label = labelled.group(1).strip().rstrip(":")
            seen_a_section = True
            prose.setdefault(label, [])
            bullets.setdefault(label, [])
            remainder = labelled.group(2).strip()
            if remainder:
                prose[label].append(remainder)
            continue
        bullet = _BULLET.match(line)
        if bullet and label:
            bullets[label].append(bullet.group(1).strip())
            continue
        if label and not bullets[label]:
            prose[label].append(line)
        elif seen_a_section:
            trailing.append(line)

    def section(name: str) -> str:
        for key, value in prose.items():
            if key.lower().lstrip("the ").startswith(name):
                return " ".join(value).strip()
        return ""

    def listing(name: str) -> tuple[str, ...]:
        for key, value in bullets.items():
            if key.lower().startswith(name):
                return tuple(value)
        return ()

    return {
        "score": int(grade_match.group(1)),
        "possible": int(grade_match.group(2)),
        "letter": grade_match.group(3).strip(),
        "bottom_line": section("bottom line"),
        "what_worked": listing("what worked"),
        "what_to_work_on": listing("what to work on"),
        "closing": " ".join(trailing).strip(),
        "sign_off": sign_off,
    }


def load_sample(path: Path) -> GradedSample:
    """Read one graded sample."""
    text = path.read_text(encoding="utf-8")
    if not _FEEDBACK_HEADING.search(text) and "Instructor Feedback" not in text:
        raise SampleError(
            f"{path.name} has no instructor feedback section. Add a "
            "'### Instructor Feedback & Grading' heading."
        )
    header_block, essay, feedback = _split_sections(text)
    parsed = _parse_feedback(feedback)
    annotations = tuple(
        Annotation(text=match.group(1).strip(), offset=match.start())
        for match in _ANNOTATION.finditer(essay)
    )
    return GradedSample(
        path=path,
        header=_parse_header(header_block),
        essay=essay,
        annotations=annotations,
        score=int(parsed["score"]),
        possible=int(parsed["possible"]),
        letter=str(parsed["letter"]),
        bottom_line=str(parsed["bottom_line"]),
        what_worked=tuple(parsed["what_worked"]),
        what_to_work_on=tuple(parsed["what_to_work_on"]),
        closing=str(parsed["closing"]),
        sign_off=str(parsed["sign_off"]),
    )


def scrub_names(
    text: str,
    roster: list[RosterEntry],
    placeholder: str = PLACEHOLDER,
) -> tuple[str, list[str]]:
    """Replace roster names with a placeholder and report what was found.

    Full names go first so "Maya Lin" never becomes "[STUDENT] Lin". Bare
    first and last names follow. A name that is not on the roster is left
    alone -- a cited scholar is subject matter, not a student.
    """
    found: list[str] = []
    scrubbed = text
    forms: list[str] = []
    for entry in roster:
        forms.extend(full_name_variants(entry.name))
    for entry in roster:
        forms.extend(bare_name_variants(entry.name))
    for form in forms:
        pattern = re.compile(rf"\b{re.escape(form)}\b")
        scrubbed, count = pattern.subn(placeholder, scrubbed)
        if count:
            found.append(form)
    return scrubbed, found
