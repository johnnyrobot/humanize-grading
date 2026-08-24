"""Warn when a passage may identify its author regardless of tokens.

Pseudonymization is not anonymization. This pass finds first-person writing
clustered with family, employment, or dated personal events, quotes the
sentence, and leaves the decision to the instructor. It never blocks and never
edits.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|my|me|mine)\b", re.IGNORECASE)

MARKERS = {
    "family": re.compile(
        r"\b(?:mother|father|mom|dad|grandmother|grandfather|brother|sister|"
        r"aunt|uncle|cousin|son|daughter|wife|husband|partner)\b",
        re.IGNORECASE,
    ),
    "employer": re.compile(
        r"\b(?:worked|work|interned|employed|job)\s+(?:at|for)\s+[A-Z][\w&.\- ]{2,}",
    ),
    "year": re.compile(r"\b(?:19|20)\d{2}\b"),
    "hometown": re.compile(
        r"\b(?:grew up in|hometown|moved to|born in|raised in)\b", re.IGNORECASE
    ),
}

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

    Sentence splitting runs on a copy with abbreviation periods masked out,
    so a title like "Dr." never fools SENTENCE into ending a sentence early.
    The line number is computed by counting newlines in that same masked
    copy, up to the masked offset where its sentence starts -- masking
    changes string length but not which newlines precede a given point.
    """
    masked = _mask_abbreviations(text)
    warnings: list[SelfIdWarning] = []

    for match in SENTENCE.finditer(masked):
        sentence = match.group(0).replace(_DOT, ".").strip()
        if not sentence or not FIRST_PERSON.search(sentence):
            continue

        reasons = tuple(
            name for name, pattern in MARKERS.items() if pattern.search(sentence)
        )
        if not reasons:
            continue

        line = masked.count("\n", 0, match.start()) + 1
        warnings.append(SelfIdWarning(line, sentence, reasons))

    return warnings
