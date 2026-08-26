"""Warn when a passage may identify its author regardless of tokens.

Pseudonymization is not anonymization. This pass finds first-person writing
clustered with family, employment, or dated personal events, quotes the
sentence, and leaves the decision to the instructor. It never blocks and never
edits.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me|mine)\b", re.IGNORECASE)

# Each of these alone is specific enough to raise a warning: a policy essay
# does not casually mention a parent, an employer, a deportation hearing, or
# a disability. YEAR below is different -- see its comment -- and is kept
# out of this dict on purpose.
STRONG_MARKERS = {
    "family": re.compile(
        r"\b(?:mother|father|mom|dad|grandmother|grandfather|brother|sister|"
        r"aunt|uncle|cousin|son|daughter|wife|husband|partner)\b",
        re.IGNORECASE,
    ),
    "employer": re.compile(
        r"\b(?:worked|work|interned|employed|job)\s+(?:at|for)\s+[A-Z][\w&.\- ]{2,}",
    ),
    "hometown": re.compile(
        r"\b(?:grew up in|hometown|moved to|born in|raised in)\b", re.IGNORECASE
    ),
    "status": re.compile(
        r"\b(?:undocumented|asylum|deportation|deported|green card|visa|"
        r"naturaliz\w+|refugee|immigrated)\b",
        re.IGNORECASE,
    ),
    "health": re.compile(
        r"\b(?:disability|disabled|diagnosed|chronic illness|my treatment|"
        r"wheelchair)\b",
        re.IGNORECASE,
    ),
    "military": re.compile(
        r"\b(?:served in|deployed to|my unit|veteran|enlisted)\b", re.IGNORECASE
    ),
    "firstgen": re.compile(r"\bfirst\s+in\s+my\s+family\b", re.IGNORECASE),
}

# A year is common in ordinary academic prose -- a statute, a court case, a
# study -- so it must never raise a warning by itself. It only enriches a
# warning a strong marker already raised, e.g. "family" + "year" together.
YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

# An abbreviated title's period looks like a sentence end to SENTENCE, which
# would cut "Dr." away from whatever follows it. Masking that period before
# splitting, then restoring it in each yielded sentence, keeps "Dr. Vance"
# together the same way candidates.py protects titled names -- see that
# module for the shared rationale.
ABBREVIATION = re.compile(r"\b(Prof|Dr|Mr|Mrs|Ms|Sr|Jr|St)\.")
_DOT = "\x00DOT\x00"


def _mask_abbreviations(text: str) -> str:
    """Hide an abbreviated title's period so SENTENCE does not split on it."""
    return ABBREVIATION.sub(lambda m: m.group(1) + _DOT, text)


@dataclass(frozen=True)
class SelfIdWarning:
    """A sentence that may identify its author from content alone."""

    line: int
    sentence: str
    reasons: tuple[str, ...]


def find_self_identifying(text: str) -> list[SelfIdWarning]:
    """Return sentences where first-person writing meets personal markers.

    A sentence needs at least one strong marker to warn at all -- a bare
    year never triggers a warning by itself, only enriches one a strong
    marker already raised, because policy essays cite statute and case
    years constantly and would otherwise flag nearly every first-person
    paragraph.

    Sentence splitting runs on a copy with abbreviation periods masked out,
    so a title like "Dr." never fools SENTENCE into ending a sentence early.
    The line number is computed by counting newlines in that same masked
    copy, up to the masked offset where its sentence starts -- masking
    changes string length but not which newlines precede a given point.
    """
    masked = _mask_abbreviations(text)
    newlines = [index for index, character in enumerate(masked) if character == "\n"]
    warnings: list[SelfIdWarning] = []

    for match in SENTENCE.finditer(masked):
        sentence = match.group(0).replace(_DOT, ".").strip()
        if not sentence or not FIRST_PERSON.search(sentence):
            continue

        reasons = tuple(
            name for name, pattern in STRONG_MARKERS.items() if pattern.search(sentence)
        )
        if not reasons:
            continue
        if YEAR.search(sentence):
            reasons = reasons + ("year",)

        line = bisect_right(newlines, match.start()) + 1
        warnings.append(SelfIdWarning(line, sentence, reasons))

    return warnings
