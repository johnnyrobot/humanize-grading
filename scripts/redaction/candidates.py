"""Layer 4: flag capitalized names that appear in personal contexts.

This layer never edits text. It reports names for the instructor to review,
because guessing wrong in either direction is costly: a missed name leaks, and
a wrongly redacted scholar destroys gradeability.

Citation and first-person scope are both judged per sentence, not per
document. A citation in one paragraph must not suppress a different, private
person of the same surname elsewhere in the essay.

An optional model-based pass runs when spacy or presidio is installed. Without
one, the heuristics below still produce flags. A flag must never be dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CONTEXT_WIDTH = 60

NAME = r"([A-Z][a-z]{2,})"

# "my X" allows personal relationships; "our X" excludes business senses
# like "our partner Google" or "our boss", which are not identifying.
REL_MY = (
    r"[Mm]y\s+(?:brother|sister|mother|father|mom|dad|aunt|uncle|cousin|grandmother|"
    r"grandfather|friend|neighbor|roommate|partner|boss|coworker|landlord|teacher|"
    r"classmate|colleague|supervisor|coach|son|daughter)\b[,\s]+"
)
REL_OUR = (
    r"[Oo]ur\s+(?:brother|sister|mother|father|aunt|uncle|cousin|grandmother|"
    r"grandfather|friend|neighbor|roommate|landlord|teacher|classmate|coach)\b[,\s]+"
)
# Senator and Rep. are deliberately excluded: elected officials named in a
# policy essay are subject matter, not identity to protect.
TITLE = r"(?:Professor|Prof\.|Dr\.|Mr\.|Ms\.|Mrs\.|Coach|Principal)\s+"
# "me" only, never "us" -- "X told us" is how an essay cites a scholar
# addressing the reader, not how a student names a person in their life.
SPEECH = r"\s+(?:told|texted|emailed|asked|showed|warned|reminded)\s+me\b"
CONTACT = r"(?:spoke\s+with|talked\s+to|interviewed|met\s+with)\s+"
QUOTE = r"[\"”],?\s+" + NAME + r"\s+(?:said|explained|recalled|testified|told)"
POSSESSIVE = NAME + r"'s\b"

PERSONAL = (
    re.compile(REL_MY + NAME),
    re.compile(REL_OUR + NAME),
    re.compile(TITLE + NAME),
    re.compile(NAME + SPEECH),
    re.compile(CONTACT + NAME),
    re.compile(QUOTE),
)
POSSESSIVE_PATTERN = re.compile(POSSESSIVE)

FIRST_PERSON = re.compile(r"\b(?:I|my|me|we|our)\b", re.IGNORECASE)
CITATION = re.compile(r"([A-Z][a-z]+)\s*(?:et al\.)?\s*\(\d{4}\)")
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


@dataclass(frozen=True)
class Candidate:
    """A name the instructor must review before the cloud path runs."""

    name: str
    line: int
    context: str


def _cited_names(sentence: str) -> set[str]:
    """Return names cited with a year in this sentence, e.g. "Brady (2017)"."""
    return {match.group(1) for match in CITATION.finditer(sentence)}


def _protected_words(protected: tuple[str, ...]) -> set[str]:
    """Split each protected full name into its words.

    "Marcus Vance" protects "Marcus" and "Vance" individually, since a
    pattern may capture only one word of a multi-word name.
    """
    words: set[str] = set()
    for full_name in protected:
        words.update(full_name.split())
    return words


def _context_at(text: str, start: int, end: int) -> str:
    lo = max(0, start - CONTEXT_WIDTH)
    hi = end + CONTEXT_WIDTH
    return text[lo:hi].replace("\n", " ").strip()


def find_candidates(text: str, protected: tuple[str, ...] = ()) -> list[Candidate]:
    """Return names appearing in personal contexts, skipping citations.

    Citations and possessive scope are judged sentence by sentence, so a
    citation in one sentence cannot suppress a same-named person elsewhere.
    A name is deduplicated only within its own sentence: the same first name
    used by two different people in two different sentences is flagged
    twice, on purpose. ``protected`` lists full names, such as the
    instructor's, that must never be flagged even when they match.
    """
    protected_words = _protected_words(protected)
    found: list[Candidate] = []

    for sentence_match in SENTENCE.finditer(text):
        sentence = sentence_match.group(0)
        offset = sentence_match.start()
        cited = _cited_names(sentence)

        matches = []
        for pattern in PERSONAL:
            matches.extend(pattern.finditer(sentence))
        if FIRST_PERSON.search(sentence):
            matches.extend(POSSESSIVE_PATTERN.finditer(sentence))

        seen_in_sentence: set[str] = set()
        for match in sorted(matches, key=lambda m: m.start()):
            name = match.group(1)
            if name in cited or name in protected_words or name in seen_in_sentence:
                continue
            seen_in_sentence.add(name)
            start = offset + match.start()
            end = offset + match.end()
            line = text.count("\n", 0, start) + 1
            found.append(Candidate(name, line, _context_at(text, start, end)))

    return sorted(found, key=lambda c: (c.line, c.name))
