"""Check every claim a draft makes against something that is not a model.

A calibrated model's grade is not defensible on its own. What makes it
defensible is that each score carries a quote, and that a script confirms the
quote is really in the essay, that the arithmetic adds up, that the letter
comes from the instructor's own scale, that the structure matches the
protocol the rubric states, and that the density figures match a fresh count.

This module also writes the offsets. The model supplies the words of a quote;
`locate` supplies the position. An offset a model guessed is not evidence.

Nothing here loads the token map. Tokens in a draft are checked against the
redacted source, which is the file the model actually read: a token that is
in the draft but not in the source was invented, and rehydration would
otherwise attach a real name to it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from scripts.grading.density import Term, scan
from scripts.grading.profile import Profile
from scripts.grading.records import CANNOT_ASSESS, GradeRecord, RecordError, locate
from scripts.grading.rubric import Rubric, RubricError
from scripts.redaction.rehydrate import TOKEN

ERROR = "error"
WARNING = "warning"

_ANNOTATION = re.compile(r"\[[^\[\]]{10,}\](?!\()")


@dataclass(frozen=True)
class Finding:
    """One thing wrong with a draft, or one thing worth a look."""

    level: str
    code: str
    message: str
    token: str = ""
    criterion: int | None = None


def errors(findings: list[Finding]) -> list[Finding]:
    """Return only the findings that must block the draft."""
    return [finding for finding in findings if finding.level == ERROR]


def _check_criteria(
    record: GradeRecord, rubric: Rubric, source: str, found: list[Finding]
) -> None:
    expected = [c.number for c in rubric.criteria]
    actual = [c.number for c in record.criteria]
    if actual != expected:
        found.append(
            Finding(
                ERROR,
                "criteria-mismatch",
                f"The record scores criteria {actual}, the rubric has {expected}.",
                record.token,
            )
        )
        return

    for scored in record.criteria:
        criterion = rubric.criterion(scored.number)
        if scored.name != criterion.name:
            found.append(
                Finding(
                    WARNING,
                    "criterion-renamed",
                    f"Criterion {scored.number} is named {scored.name!r} in the "
                    f"record and {criterion.name!r} in the rubric.",
                    record.token,
                    scored.number,
                )
            )
        if scored.points_possible != criterion.points:
            found.append(
                Finding(
                    ERROR,
                    "points-possible",
                    f"Criterion {scored.number} is worth {criterion.points} points, "
                    f"not {scored.points_possible}.",
                    record.token,
                    scored.number,
                )
            )
        if not scored.assessable:
            if scored.score is not None:
                found.append(
                    Finding(
                        ERROR,
                        "unassessable-score",
                        f"Criterion {scored.number} is marked unassessable but "
                        f"carries a score of {scored.score}. Never infer a score.",
                        record.token,
                        scored.number,
                    )
                )
            if scored.level != CANNOT_ASSESS:
                found.append(
                    Finding(
                        ERROR,
                        "unassessable-level",
                        f"Criterion {scored.number} is unassessable, so its level "
                        f"must read {CANNOT_ASSESS!r}.",
                        record.token,
                        scored.number,
                    )
                )
            continue

        if scored.score is None:
            found.append(
                Finding(
                    ERROR,
                    "missing-score",
                    f"Criterion {scored.number} is assessable but has no score.",
                    record.token,
                    scored.number,
                )
            )
        elif not 0 <= scored.score <= scored.points_possible:
            found.append(
                Finding(
                    ERROR,
                    "score-range",
                    f"Criterion {scored.number} scored {scored.score} out of "
                    f"{scored.points_possible}.",
                    record.token,
                    scored.number,
                )
            )
        if not scored.justifications:
            found.append(
                Finding(
                    ERROR,
                    "no-quote",
                    f"Criterion {scored.number} has a score and no supporting "
                    "quote. Give no score, and flag it for the instructor.",
                    record.token,
                    scored.number,
                )
            )
        for justification in scored.justifications:
            try:
                offset, line, length = locate(
                    justification.quote, source, justification.occurrence
                )
            except RecordError as error:
                found.append(
                    Finding(ERROR, "quote", str(error), record.token, scored.number)
                )
                continue
            justification.offset = offset
            justification.line = line
            justification.length = length


def _check_totals(
    record: GradeRecord, rubric: Rubric, found: list[Finding]
) -> None:
    scored = [c for c in record.criteria if c.assessable and c.score is not None]
    total = sum(c.score for c in scored)
    possible = sum(c.points_possible for c in scored)

    if record.total != total:
        found.append(
            Finding(
                ERROR,
                "total",
                f"The total is {record.total}; the scored criteria add to {total}.",
                record.token,
            )
        )
    if record.total_possible != possible:
        found.append(
            Finding(
                ERROR,
                "total-possible",
                f"The possible total is {record.total_possible}; the scored "
                f"criteria are worth {possible}.",
                record.token,
            )
        )
    if possible == 0:
        found.append(
            Finding(
                ERROR,
                "nothing-assessed",
                "No criterion could be assessed. There is no grade to give.",
                record.token,
            )
        )
        return
    if possible < rubric.total_points:
        found.append(
            Finding(
                WARNING,
                "partial",
                f"This grade covers {possible} of {rubric.total_points} points. "
                "Tell the instructor which criteria were not assessed.",
                record.token,
            )
        )
    # A record can carry a score above its criterion's maximum, which puts the
    # percentage outside every band the rubric defines. That is a finding, not
    # a crash: this function is the gate that reports malformed records, so it
    # must never raise on one. Without this guard a single out-of-range score
    # takes the whole batch's verification down with it.
    percent = 100 * total / possible
    try:
        expected = rubric.letter_for(percent)
    except RubricError as error:
        found.append(
            Finding(
                ERROR,
                "letter",
                f"{total}/{possible} is {percent:.0f} percent, which the "
                f"rubric's scale does not cover. {error}",
                record.token,
            )
        )
        return
    if record.letter.strip() != expected:
        found.append(
            Finding(
                ERROR,
                "letter",
                f"The rubric's scale gives {expected!r} for {total}/{possible}, "
                f"not {record.letter!r}. The letter is computed, not chosen.",
                record.token,
            )
        )


def _check_density(
    record: GradeRecord, source: str, terms: list[Term], found: list[Finding]
) -> None:
    fresh = scan(source, terms).as_dict()
    reported = record.pattern_density or {}
    if reported.get("count") != fresh["count"] or reported.get(
        "by_pattern"
    ) != fresh["by_pattern"]:
        found.append(
            Finding(
                ERROR,
                "density-mismatch",
                "The pattern-density figures do not match a fresh count of the "
                f"source ({fresh['count']} hits, {fresh['by_pattern']}). Run "
                "`grade.py density` and paste its output.",
                record.token,
            )
        )


def _check_feedback(
    record: GradeRecord,
    markdown: str,
    source: str,
    rubric: Rubric,
    profile: Profile,
    terms: list[Term],
    found: list[Finding],
) -> None:
    lowered = markdown.lower()
    for section in rubric.protocol.sections:
        if section.lower() not in lowered:
            found.append(
                Finding(
                    ERROR,
                    "section-missing",
                    f"The feedback has no {section!r} section. The rubric's "
                    "protocol requires it.",
                    record.token,
                )
            )
    if rubric.protocol.sign_offs:
        tail = [line for line in markdown.strip().splitlines() if line.strip()]
        last = tail[-1].strip() if tail else ""
        if last not in rubric.protocol.sign_offs:
            found.append(
                Finding(
                    ERROR,
                    "sign-off",
                    f"The feedback must end with one of "
                    f"{list(rubric.protocol.sign_offs)}. It ends with {last!r}.",
                    record.token,
                )
            )
    if rubric.protocol.requires_inline_annotations and not _ANNOTATION.search(markdown):
        found.append(
            Finding(
                ERROR,
                "no-annotations",
                "The rubric asks for inline bracketed annotations and the "
                "feedback has none.",
                record.token,
            )
        )

    for phrase in profile.never_use:
        if phrase.lower() in lowered:
            found.append(
                Finding(
                    ERROR,
                    "never-use",
                    f"The feedback says {phrase!r}, which is on the profile's "
                    "never-use list.",
                    record.token,
                )
            )

    for match in TOKEN.finditer(markdown):
        if match.group(0) not in source:
            found.append(
                Finding(
                    ERROR,
                    "stray-token",
                    f"The feedback uses {match.group(0)}, which is not in the "
                    "redacted source. Rehydration would attach a real name to it.",
                    record.token,
                )
            )

    watched = scan(markdown, terms)
    if watched.hits:
        listed = ", ".join(sorted({hit.term for hit in watched.hits})[:6])
        found.append(
            Finding(
                WARNING,
                "watched-term",
                f"The feedback uses {len(watched.hits)} watched AI terms "
                f"({listed}). Run it through references/ai-patterns.md again.",
                record.token,
            )
        )

    if record.status != "draft":
        found.append(
            Finding(
                ERROR,
                "status",
                f"Every generated grade is a draft until the instructor signs "
                f"off. Status reads {record.status!r}.",
                record.token,
            )
        )


def verify_draft(
    record: GradeRecord,
    markdown: str,
    source: str,
    rubric: Rubric,
    profile: Profile,
    terms: list[Term],
) -> list[Finding]:
    """Check one draft and fill in the offsets it is missing."""
    found: list[Finding] = []
    _check_criteria(record, rubric, source, found)
    _check_totals(record, rubric, found)
    _check_density(record, source, terms, found)
    _check_feedback(record, markdown, source, rubric, profile, terms, found)
    return found
