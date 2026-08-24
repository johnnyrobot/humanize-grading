"""Layer 4: flag capitalized names that appear in personal contexts.

This layer never edits text. It reports names for the instructor to review,
because guessing wrong in either direction is costly: a missed name leaks, and
a wrongly redacted scholar destroys gradeability.

An optional model-based pass runs when spacy or presidio is installed. Without
one, the heuristics below still produce flags. A flag must never be dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONTEXT_WIDTH = 60

NAME = r"([A-Z][a-z]{2,})"

# The pronoun alternation is case-tolerant ([Mm]y, [Oo]ur) because a sentence
# opening with the relation ("My brother Daniel...") capitalizes "My". The
# NAME group above stays case-sensitive: it is what tells a name apart from
# an ordinary capitalized word, so it cannot take re.IGNORECASE either.
RELATION = (
    r"(?:[Mm]y|[Oo]ur)\s+(?:brother|sister|mother|father|mom|dad|aunt|uncle|cousin|"
    r"grandmother|grandfather|friend|neighbor|roommate|partner|boss|coworker)\s+"
)
TITLE = r"(?:Professor|Prof\.|Dr\.|Mr\.|Ms\.|Mrs\.|Coach|Principal|Senator|Rep\.)\s+"
SPEECH = r"\s+(?:told|texted|emailed|asked|showed|warned|reminded)\s+(?:me|us)\b"

PERSONAL = (
    re.compile(RELATION + NAME),
    re.compile(TITLE + NAME),
    re.compile(NAME + SPEECH),
)

CITATION = re.compile(r"[A-Z][a-z]+\s*(?:et al\.)?\s*\(\d{4}\)")


@dataclass(frozen=True)
class Candidate:
    """A name the instructor must review before the cloud path runs."""

    name: str
    line: int
    context: str


def _is_cited(text: str, name: str) -> bool:
    return any(name in match.group(0) for match in CITATION.finditer(text))


def find_candidates(text: str) -> list[Candidate]:
    """Return names appearing in personal contexts, skipping citations."""
    found: list[Candidate] = []
    seen: set[str] = set()

    for pattern in PERSONAL:
        for match in pattern.finditer(text):
            name = match.group(1)
            if name in seen or _is_cited(text, name):
                continue
            seen.add(name)
            line = text.count("\n", 0, match.start()) + 1
            start = max(0, match.start() - CONTEXT_WIDTH)
            context = text[start : match.end() + CONTEXT_WIDTH]
            found.append(Candidate(name, line, context.replace("\n", " ").strip()))

    return sorted(found, key=lambda c: (c.line, c.name))
