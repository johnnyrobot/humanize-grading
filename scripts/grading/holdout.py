"""Compare a generated grade against the one the instructor actually gave.

This is calibration's deliverable. The profile is only the means.

The samples carry one grade per paper, not one per criterion, so the delta
compares what the instructor actually wrote: the total, the letter band, how
much of their own phrasing came through, and how long the feedback ran.
Inventing per-criterion instructor scores to compare against would be making
up the instructor's judgement.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.grading.records import GradeRecord
from scripts.grading.samples import GradedSample

PHRASE_WORDS = 5
POINT_TOLERANCE = 5
LENGTH_TOLERANCE = 2.0

_WORD = re.compile(r"[A-Za-z0-9']+")


def required_holdouts(total_samples: int) -> int:
    """Say how many holdouts a corpus of graded samples must hold back.

    One holdout cannot show agreement. Six or more graded pieces leave room
    for two without pushing calibration below four samples.
    """
    return 2 if total_samples >= 6 else 1


@dataclass(frozen=True)
class Delta:
    """How far the generated grade sits from the instructor's."""

    token: str
    generated_total: int
    instructor_total: int
    points: int
    generated_letter: str
    instructor_letter: str
    letter_match: bool
    shared_phrases: tuple[str, ...]
    generated_words: int
    instructor_words: int
    length_ratio: float
    findings: tuple[str, ...]


def _words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def _phrases(words: list[str]) -> set[str]:
    return {
        " ".join(words[index : index + PHRASE_WORDS])
        for index in range(len(words) - PHRASE_WORDS + 1)
    }


def compare(
    record: GradeRecord,
    feedback: str,
    sample: GradedSample,
    *,
    point_tolerance: int = POINT_TOLERANCE,
    length_tolerance: float = LENGTH_TOLERANCE,
) -> Delta:
    """Compare one generated grade and its feedback against a graded sample."""
    generated_words = _words(feedback)
    instructor_words = _words(sample.comments_only())
    shared = sorted(_phrases(generated_words) & _phrases(instructor_words))

    points = record.total - sample.score
    letter_match = record.letter.strip() == sample.letter.strip()
    ratio = (
        round(len(generated_words) / len(instructor_words), 2)
        if instructor_words
        else 0.0
    )

    findings: list[str] = []
    if abs(points) > point_tolerance:
        direction = "above" if points > 0 else "below"
        findings.append(
            f"The generated total is {abs(points)} points {direction} the "
            f"instructor's. Check the severity section of the profile."
        )
    if not letter_match:
        findings.append(
            f"The letter band differs: generated {record.letter!r}, "
            f"instructor {sample.letter!r}."
        )
    if not shared:
        findings.append(
            "The feedback reuses no phrasing from the instructor's own "
            "comments. Check the comment bank and the voice section."
        )
    if ratio and (ratio > length_tolerance or ratio < 1 / length_tolerance):
        findings.append(
            f"The feedback length is {ratio}x the instructor's. Check the "
            "feedback structure section."
        )

    return Delta(
        token=record.token,
        generated_total=record.total,
        instructor_total=sample.score,
        points=points,
        generated_letter=record.letter,
        instructor_letter=sample.letter,
        letter_match=letter_match,
        shared_phrases=tuple(shared),
        generated_words=len(generated_words),
        instructor_words=len(instructor_words),
        length_ratio=ratio,
        findings=tuple(findings),
    )


def render(delta: Delta) -> str:
    """Render the delta as the report the instructor reads."""
    lines = [
        "# Holdout report",
        "",
        f"Held-back sample graded blind as `{delta.token}`.",
        "",
        "| Measure | Instructor | Generated |",
        "| --- | --- | --- |",
        f"| Total | {delta.instructor_total} | {delta.generated_total} |",
        f"| Letter | {delta.instructor_letter} | {delta.generated_letter} |",
        f"| Feedback words | {delta.instructor_words} | {delta.generated_words} |",
        "",
        f"Score gap: {delta.points:+d} points. "
        f"Letter: {'match' if delta.letter_match else 'differs'}.",
        "",
        "## Phrasing carried over",
        "",
    ]
    if delta.shared_phrases:
        lines += [f"- {phrase}" for phrase in delta.shared_phrases[:10]]
        if len(delta.shared_phrases) > 10:
            lines.append(f"- ... and {len(delta.shared_phrases) - 10} more")
    else:
        lines.append("None. The voice did not carry.")
    lines += ["", "## What to look at", ""]
    if delta.findings:
        lines += [f"- {finding}" for finding in delta.findings]
    else:
        lines.append("Nothing. The profile reproduced this sample closely.")
    lines += [
        "",
        "This report judges the profile, not the student. A close match means "
        "the profile is worth grading with. A wide one means edit the profile "
        "and run calibration again.",
        "",
    ]
    return "\n".join(lines)
