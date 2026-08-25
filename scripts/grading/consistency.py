"""Flag what a human should look at twice across a graded batch.

Grading one essay well and grading thirty consistently are different jobs.
This reads the JSON records rather than the prose, which is why the records
exist.

Everything here is a flag, never a failure. An outlier is often a genuinely
unusual essay, and a tool that refused the batch over one would be wrong more
often than right. The instructor decides.

Outliers are found with the median and the median absolute deviation, not
the mean and the standard deviation. One extreme score inflates the standard
deviation enough to hide itself: a batch scoring 20, 21, 20, 19, 4 gives the
4 a z-score of 1.99, just under a threshold of 2, while flagging the 21 as an
outlier instead. The median does not move. A minimum gap in points sits on
top of that, so a one-point difference in a tight batch is never called an
outlier no matter how small the spread.

The density check deserves its own note. A criterion-4 score that runs
against its own density evidence -- a high score on the essay with far more
watched terms -- is worth seeing. It is still not proof: a paper can use
watched words well. The flag names both papers and stops there. Each
submission is compared only against its sharpest counterexample, so a batch
of thirty produces at most thirty flags rather than several hundred pairs.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass

from scripts.grading.records import GradeRecord

MIN_BATCH = 3
Z_THRESHOLD = 2.0
SENTENCE_WORDS = 8
DENSITY_RATIO = 1.5
PROSE_CRITERION = 4

# Below this many points from the median, a score is never an outlier, however
# tight the batch. Without it, a batch of 20, 20, 21, 20, 20 flags the 21.
MIN_POINTS_GAP = 3

# Turns a median absolute deviation into something comparable to a standard
# deviation for normally distributed data.
ROBUST_SCALE = 0.6745

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")
_WORD = re.compile(r"[A-Za-z0-9']+")


@dataclass(frozen=True)
class Outlier:
    """One thing in the batch worth a second look."""

    kind: str
    message: str
    tokens: tuple[str, ...] = ()


def _scores(records: list[GradeRecord], number: int) -> dict[str, int]:
    found: dict[str, int] = {}
    for record in records:
        for criterion in record.criteria:
            if criterion.number == number and criterion.assessable:
                if criterion.score is not None:
                    found[record.token] = criterion.score
    return found


def _criterion_names(records: list[GradeRecord]) -> dict[int, str]:
    names: dict[int, str] = {}
    for record in records:
        for criterion in record.criteria:
            names.setdefault(criterion.number, criterion.name)
    return names


def _score_outliers(
    records: list[GradeRecord], threshold: float, names: dict[int, str]
) -> list[Outlier]:
    flags: list[Outlier] = []
    for number in sorted(names):
        scores = _scores(records, number)
        if len(scores) < MIN_BATCH:
            continue
        values = list(scores.values())
        middle = statistics.median(values)
        # The scale floor of one point keeps a batch of identical scores from
        # dividing by zero, and still lets a genuine gap in that batch show.
        scale = max(statistics.median([abs(v - middle) for v in values]), 1.0)
        for token, score in sorted(scores.items()):
            gap = abs(score - middle)
            if gap < MIN_POINTS_GAP:
                continue
            if ROBUST_SCALE * gap / scale < threshold:
                continue
            flags.append(
                Outlier(
                    "score-outlier",
                    f"{token} scored {score} on {number}. {names[number]} "
                    f"while the batch median was {middle:g}. Check the "
                    "quotes behind it.",
                    (token,),
                )
            )
    return flags


def _density_inversions(records: list[GradeRecord]) -> list[Outlier]:
    prose = _scores(records, PROSE_CRITERION)
    density = {
        record.token: float(record.pattern_density.get("per_thousand", 0.0))
        for record in records
    }
    flags: list[Outlier] = []
    tokens = sorted(prose)
    for high in tokens:
        if density.get(high, 0.0) <= 0:
            continue
        sharpest: tuple[float, str] | None = None
        for low in tokens:
            if low == high or density.get(low, 0.0) <= 0:
                continue
            if density[high] < density[low] * DENSITY_RATIO:
                continue
            if prose[high] <= prose[low]:
                continue
            ratio = density[high] / density[low]
            if sharpest is None or ratio > sharpest[0]:
                sharpest = (ratio, low)
        if sharpest is None:
            continue
        low = sharpest[1]
        flags.append(
            Outlier(
                "density-inversion",
                f"{high} has {density[high]} watched terms per 1000 words and "
                f"scored {prose[high]} on criterion {PROSE_CRITERION}. {low} "
                f"has {density[low]} and scored {prose[low]}. Density is "
                "evidence, not proof, so read both before changing either "
                "score.",
                (high, low),
            )
        )
    return flags


def _reused_sentences(drafts: dict[str, str]) -> list[Outlier]:
    owners: dict[str, set[str]] = defaultdict(set)
    for token, text in drafts.items():
        for raw in _SENTENCE.findall(text):
            words = _WORD.findall(raw.lower())
            if len(words) < SENTENCE_WORDS:
                continue
            owners[" ".join(words)].add(token)
    flags: list[Outlier] = []
    for sentence, tokens in sorted(owners.items()):
        if len(tokens) > 1:
            flags.append(
                Outlier(
                    "reused-sentence",
                    f"{len(tokens)} drafts share this sentence: {sentence!r}. "
                    "Feedback that repeats verbatim reads as a form letter.",
                    tuple(sorted(tokens)),
                )
            )
    return flags


def _unassessed(records: list[GradeRecord], names: dict[int, str]) -> list[Outlier]:
    flags: list[Outlier] = []
    for record in records:
        missing = [
            f"{c.number}. {names.get(c.number, c.name)}"
            for c in record.criteria
            if not c.assessable
        ]
        if missing:
            flags.append(
                Outlier(
                    "unassessed",
                    f"{record.token} has no score on {', '.join(missing)}. "
                    f"The grade covers {record.total_possible} points, not the "
                    "full rubric.",
                    (record.token,),
                )
            )
    return flags


def review(
    records: list[GradeRecord],
    drafts: dict[str, str],
    threshold: float = Z_THRESHOLD,
    min_batch: int = MIN_BATCH,
) -> list[Outlier]:
    """Return everything in this batch worth a second look."""
    if len(records) < min_batch:
        return [
            Outlier(
                "too-small",
                f"This batch has {len(records)} graded submissions. Outlier "
                f"checks need at least {min_batch}, so they were skipped. Read "
                "each draft yourself.",
                tuple(sorted(record.token for record in records)),
            )
        ]
    names = _criterion_names(records)
    return (
        _score_outliers(records, threshold, names)
        + _density_inversions(records)
        + _reused_sentences(drafts)
        + _unassessed(records, names)
    )
