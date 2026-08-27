"""Layer 4: flag capitalized names that appear in personal contexts.

This layer never edits text. It reports names for the instructor to review,
because guessing wrong in either direction is costly: a missed name leaks, and
a wrongly redacted scholar destroys gradeability.

A strong signal -- a relation word, a title, "told me" -- stays scoped to its
own sentence: a citation in one paragraph must not suppress a different,
private person of the same surname elsewhere in the essay. A weak signal -- a
bare possessive, a quote verb -- is common academic prose about a cited
scholar too, so it alone is suppressed document-wide for anyone also cited
with a year anywhere in the text.

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

# STRONG patterns name a specific relationship, title, or act of speaking
# directly to the writer -- evidence unlikely to describe a cited scholar.
# WEAK patterns (a possessive, a quote verb) are common in ordinary academic
# prose about a scholar too ("I find Gillespie's framing persuasive"), so
# they alone are suppressed document-wide for anyone also cited with a year
# anywhere in the text. A strong pattern is never suppressed that way: it
# stays scoped to its own sentence, so a citation elsewhere in the document
# can never blind it to a different, private person of the same name (see
# find_candidates).
STRONG_PATTERNS = (
    re.compile(REL_MY + NAME),
    re.compile(REL_OUR + NAME),
    re.compile(TITLE + NAME),
    re.compile(NAME + SPEECH),
    re.compile(CONTACT + NAME),
)
WEAK_PATTERNS = (re.compile(QUOTE),)
POSSESSIVE_PATTERN = re.compile(POSSESSIVE)

FIRST_PERSON = re.compile(r"\b(?:I|my|me|we|our)\b", re.IGNORECASE)
CITATION = re.compile(r"([A-Z][a-z]+)\s*(?:et al\.)?\s*\(\d{4}\)")
SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

# An abbreviated title's period looks like a sentence end to SENTENCE, which
# would cut "Dr." away from the name right after it and silently defeat
# TITLE. Masking that period before splitting, then restoring it in each
# yielded sentence, keeps "Dr. Alvarez" together without a name list of
# every title in existence.
ABBREVIATION = re.compile(r"\b(Prof|Dr|Mr|Mrs|Ms|Sr|Jr|St)\.")
_DOT = "\x00DOT\x00"


def _mask_abbreviations(text: str) -> str:
    """Hide an abbreviated title's period so SENTENCE does not split on it."""
    return ABBREVIATION.sub(lambda m: m.group(1) + _DOT, text)


def _protected_forms(protected: tuple[str, ...]) -> list[str]:
    """List each protected full name plus its surname, longest first.

    Longest first means "Marcus Vance" is masked whole before its surname
    form is tried, so masking "Vance" out of "Dr. Vance" can never also eat
    the "Marcus" half of an unrelated "my cousin Marcus" -- only "Marcus
    Vance" together, or "Vance" alone, is ever a protected form.
    """
    forms = []
    for name in protected:
        forms.append(name)
        parts = name.split()
        if len(parts) > 1:
            forms.append(parts[-1])
    return sorted(set(forms), key=lambda s: (-len(s), s))


def _mask(text: str, protected: tuple[str, ...]) -> str:
    """Hide protected names, then abbreviation periods, before any pattern runs.

    Masking a protected name here, rather than filtering a matched candidate
    by name afterward, means a pattern can never capture even one word of
    it -- the same shield roster.py uses for the instructor. Unlike
    roster.py, nothing here restores the protected name afterward: this
    layer never returns text, only Candidate records built from whatever is
    left unmasked.
    """
    for index, form in enumerate(_protected_forms(protected)):
        text = re.sub(rf"\b{re.escape(form)}\b", f"\x00SHIELD{index}\x00", text)
    return _mask_abbreviations(text)


@dataclass(frozen=True)
class Candidate:
    """A name the instructor must review before the cloud path runs."""

    name: str
    line: int
    context: str


@dataclass(frozen=True)
class ExcludedName:
    """A visible name excluded from blocking detection, for private review."""

    name: str
    line: int
    context: str
    reason: str


@dataclass(frozen=True)
class CandidateReview:
    """Blocking candidates plus names intentionally excluded from them."""

    candidates: tuple[Candidate, ...]
    excluded: tuple[ExcludedName, ...]


class CandidateReviewLimitError(ValueError):
    """The private name-review item budget was exhausted."""


def _cited_names(sentence: str) -> set[str]:
    """Return names cited with a year in this sentence, e.g. "Brady (2017)"."""
    return {match.group(1) for match in CITATION.finditer(sentence)}


def _context_at(sentence: str, start: int, end: int) -> str:
    """Return the text around one match, clamped to its own sentence.

    A "sentence" (per SENTENCE below) never contains a newline, so no
    newline cleanup is needed here.
    """
    lo = max(0, start - CONTEXT_WIDTH)
    hi = end + CONTEXT_WIDTH
    return sentence[lo:hi].strip()


def review_candidates(
    text: str,
    protected: tuple[str, ...] = (),
    *,
    max_items: int | None = None,
) -> CandidateReview:
    """Return names appearing in personal contexts, skipping citations.

    Citations and possessive scope are judged sentence by sentence, so a
    citation in one sentence cannot suppress a same-named person elsewhere
    -- for a STRONG pattern (a relation word, a title, "told me", an
    interview-contact verb). A WEAK pattern (a possessive, a quote verb) is
    also dropped when its name is cited with a year ANYWHERE in the
    document, because "I find Gillespie's framing persuasive" is ordinary
    academic prose, not evidence of a private relationship, the moment
    "Gillespie (2014)" appears anywhere else in the same essay. A name is
    deduplicated only within its own sentence: the same first name used by
    two different people in two different sentences is flagged twice, on
    purpose. ``protected`` lists full names, such as the instructor's, that
    are masked out before any pattern runs, so neither the full name nor
    its surname alone can ever be flagged -- but an unrelated first name
    that merely matches part of a protected name, like "my cousin Marcus"
    against protected name "Marcus Vance", still is.

    Sentence splitting runs on a copy with protected names and abbreviation
    periods both masked out, so a title's period never fools SENTENCE into
    cutting "Dr." away from the name after it. The line number for each
    candidate is computed by counting newlines in that same masked copy, up
    to the masked offset where its sentence starts -- masking changes
    string length but not which newlines precede a given point, so the
    count still lands on the right line even though the raw offset itself
    no longer maps directly back into the original text.
    """
    scholars = _cited_names(text)
    excluded: list[ExcludedName] = []
    found: list[Candidate] = []

    def append_item(target: list, item: Candidate | ExcludedName) -> None:
        if max_items is not None and len(found) + len(excluded) >= max_items:
            raise CandidateReviewLimitError(
                "The candidate review item limit was exceeded."
            )
        target.append(item)

    protected_bytes = bytearray(len(text))
    for name in _protected_forms(protected):
        protected_line = 1
        scanned_until = 0
        for match in re.finditer(rf"\b{re.escape(name)}\b", text):
            start, end = match.span()
            protected_line += text.count("\n", scanned_until, start)
            scanned_until = start
            if any(protected_bytes[start:end]):
                continue
            protected_bytes[start:end] = b"\x01" * (end - start)
            append_item(
                excluded,
                ExcludedName(
                    name=name,
                    line=protected_line,
                    context=_context_at(text, start, end),
                    reason="protected",
                ),
            )
    masked = _mask(text, protected)
    sentence_line = 1
    scanned_until = 0

    for sentence_match in SENTENCE.finditer(masked):
        if not sentence_match.group(0).strip():
            continue
        sentence_line += masked.count("\n", scanned_until, sentence_match.start())
        scanned_until = sentence_match.start()
        line = sentence_line
        sentence = sentence_match.group(0).replace(_DOT, ".")
        cited = _cited_names(sentence)

        matches = []
        for pattern in STRONG_PATTERNS:
            matches.extend((m, False) for m in pattern.finditer(sentence))
        for pattern in WEAK_PATTERNS:
            matches.extend((m, True) for m in pattern.finditer(sentence))
        if FIRST_PERSON.search(sentence):
            matches.extend((m, True) for m in POSSESSIVE_PATTERN.finditer(sentence))

        seen_in_sentence: set[str] = set()
        excluded_in_sentence: set[str] = set()
        for match, weak in sorted(matches, key=lambda pair: pair[0].start()):
            name = match.group(1)
            if name in seen_in_sentence:
                continue
            if name in cited or (weak and name in scholars):
                if name not in excluded_in_sentence:
                    append_item(
                        excluded,
                        ExcludedName(
                            name=name,
                            line=line,
                            context=_context_at(sentence, match.start(), match.end()),
                            reason="citation",
                        ),
                    )
                    excluded_in_sentence.add(name)
                continue
            seen_in_sentence.add(name)
            context = _context_at(sentence, match.start(), match.end())
            append_item(found, Candidate(name, line, context))

    return CandidateReview(
        candidates=tuple(sorted(found, key=lambda c: (c.line, c.name))),
        excluded=tuple(sorted(excluded, key=lambda c: (c.line, c.name, c.reason))),
    )


def find_candidates(text: str, protected: tuple[str, ...] = ()) -> list[Candidate]:
    """Return only names that must block the model path."""
    return list(review_candidates(text, protected).candidates)
