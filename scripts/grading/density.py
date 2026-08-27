"""Count watched AI terms in a text, and say where each one is.

The PHIL 210 rubric names "heavy reliance on AI buzzwords (tapestry, delve,
pivotal, testament)" as an Unacceptable marker under criterion 4. Counting
those is assessing prose against a stated criterion. It is in scope.

Concluding who or what wrote an essay is out of scope, and nothing in this
module does it. A count is not an accusation. The report exists so a score on
criterion 4 has evidence attached, the same way every other criterion needs a
quote.

Terms come from references/ai-patterns.md so there is one list, not two.
Counts are grouped by pattern number rather than summed into a single figure,
because the patterns are not equally meaningful in a student essay. Pattern 7
carries the rubric's own buzzwords. Pattern 21 catches chatbot disclaimers.
Reading them apart is the point. When a term is listed under multiple patterns,
it is tagged with the pattern that appears first in the file; per-pattern counts
are therefore a guide rather than an exact partition.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from scripts.path_safety import read_text_limited

PATTERNS_PATH = Path(__file__).resolve().parents[2] / "references" / "ai-patterns.md"

MAX_TERM_WORDS = 6
# Eight whitespace characters cover a normal CRLF wrap plus indentation
# without letting one watched phrase span an arbitrarily large blank region.
MAX_INTERWORD_WHITESPACE = 8
MAX_TERMS = 256
MAX_TERM_CHARACTERS = 256
MAX_TOTAL_TERM_CHARACTERS = 16_384
MAX_MATCHES = 5_000

_HEADING = re.compile(r"^###\s+(\d+)\.")
_WATCH = re.compile(
    r"^\*\*(?:High-frequency AI words|Words to watch|Phrases to watch)"
    r":\*\*\s*(.+)$"
)
_PAREN = re.compile(r"\s*\([^)]*\)")
_BRACKET = re.compile(r"\s*\[[^\]]*\]")
_PLACEHOLDER = re.compile(r"\b[XYZ]\b")
_WORD = re.compile(r"[A-Za-z0-9']+")


class DensityLimitError(ValueError):
    """The density input exceeds a public resource limit."""


@dataclass(frozen=True)
class Term:
    """One watched term and the pattern it belongs to."""

    text: str
    pattern: int


@dataclass(frozen=True)
class Hit:
    """One occurrence of a watched term."""

    pattern: int
    term: str
    offset: int
    line: int
    context: str


@dataclass(frozen=True)
class _PendingHit:
    """One bounded match waiting for document-order line assignment."""

    pattern: int
    term: str
    offset: int
    context: str


@dataclass(frozen=True)
class Density:
    """What a scan found."""

    words: int
    hits: tuple[Hit, ...]
    per_thousand: float
    by_pattern: dict[int, int]

    def as_dict(self) -> dict:
        """Return the JSON shape stored in a grade record."""
        return {
            "words": self.words,
            "count": len(self.hits),
            "per_thousand": self.per_thousand,
            "by_pattern": {str(k): v for k, v in sorted(self.by_pattern.items())},
            "examples": [
                {
                    "pattern": hit.pattern,
                    "term": hit.term,
                    "offset": hit.offset,
                    "line": hit.line,
                    "context": hit.context,
                }
                for hit in self.hits
            ],
        }


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _expand(raw: str) -> list[str]:
    """Turn one comma-separated entry into zero or more literal terms.

    Four kinds of entry are dropped rather than guessed at: a template with
    an ellipsis, a template with an X/Y/Z placeholder, an entry carrying two
    or more slashed words (the alternates multiply and the result stops
    being a phrase anyone writes), and a trailing explanatory clause.
    """
    text = _norm(_BRACKET.sub("", _PAREN.sub("", raw)))
    if not text or "..." in text or _PLACEHOLDER.search(text):
        return []
    words = text.split(" ")
    if len(words) > MAX_TERM_WORDS:
        return []
    slashed = [index for index, word in enumerate(words) if "/" in word]
    if not slashed:
        return [text]
    if len(slashed) > 1:
        return []
    position = slashed[0]
    expanded: list[str] = []
    for alternate in words[position].split("/"):
        if not alternate:
            continue
        expanded.append(" ".join(words[:position] + [alternate] + words[position + 1 :]))
    return expanded


def load_terms(path: Path = PATTERNS_PATH) -> list[Term]:
    """Read every watched term from the shared pattern reference."""
    terms: list[Term] = []
    seen: set[str] = set()
    total_characters = 0
    pattern = 0
    for line in read_text_limited(path, "The pattern reference").splitlines():
        heading = _HEADING.match(line)
        if heading:
            pattern = int(heading.group(1))
            continue
        watch = _WATCH.match(line.strip())
        if not watch or not pattern:
            continue
        for raw in watch.group(1).split(","):
            if len(raw.strip()) > MAX_TERM_CHARACTERS:
                raise DensityLimitError(
                    "Each watched term must use at most "
                    f"{MAX_TERM_CHARACTERS} characters."
                )
            for text in _expand(raw):
                key = text.casefold()
                if key in seen:
                    continue
                if len(terms) >= MAX_TERMS:
                    raise DensityLimitError(
                        "The pattern reference may define at most "
                        f"{MAX_TERMS} unique watched terms."
                    )
                if total_characters + len(text) > MAX_TOTAL_TERM_CHARACTERS:
                    raise DensityLimitError(
                        "Watched terms may use at most "
                        f"{MAX_TOTAL_TERM_CHARACTERS} characters in total."
                    )
                seen.add(key)
                terms.append(Term(text=text, pattern=pattern))
                total_characters += len(text)
    return terms


def _term_pattern(text: str) -> re.Pattern[str]:
    gap = rf"\s{{1,{MAX_INTERWORD_WHITESPACE}}}"
    body = gap.join(re.escape(word) for word in text.split(" "))
    prefix = r"\b" if text[:1].isalnum() else ""
    suffix = r"\b" if text[-1:].isalnum() else ""
    return re.compile(prefix + body + suffix, re.IGNORECASE)


def _validate_terms(terms: list[Term]) -> None:
    if len(terms) > MAX_TERMS:
        raise DensityLimitError(
            f"A density scan accepts at most {MAX_TERMS} unique watched terms."
        )
    seen: set[str] = set()
    total_characters = 0
    for term in terms:
        if len(term.text) > MAX_TERM_CHARACTERS:
            raise DensityLimitError(
                "Each watched term must use at most "
                f"{MAX_TERM_CHARACTERS} characters."
            )
        key = term.text.casefold()
        if key in seen:
            raise DensityLimitError("Watched terms must be unique.")
        seen.add(key)
        total_characters += len(term.text)
        if total_characters > MAX_TOTAL_TERM_CHARACTERS:
            raise DensityLimitError(
                "Watched terms may use at most "
                f"{MAX_TOTAL_TERM_CHARACTERS} characters in total."
            )


def scan(text: str, terms: list[Term]) -> Density:
    """Count every watched term in text and record where each one sits."""
    _validate_terms(terms)
    pending: list[_PendingHit] = []
    for term in terms:
        for match in _term_pattern(term.text).finditer(text):
            if len(pending) >= MAX_MATCHES:
                raise DensityLimitError(
                    "The density scan found more than "
                    f"{MAX_MATCHES} watched-term matches."
                )
            start = max(0, match.start() - 40)
            pending.append(
                _PendingHit(
                    pattern=term.pattern,
                    term=term.text,
                    offset=match.start(),
                    context=_norm(text[start : match.end() + 40]),
                )
            )
    pending.sort(key=lambda hit: (hit.offset, hit.term))

    hits: list[Hit] = []
    line = 1
    scanned_to = 0
    for match in pending:
        line += text.count("\n", scanned_to, match.offset + 1)
        scanned_to = match.offset + 1
        hits.append(
            Hit(
                pattern=match.pattern,
                term=match.term,
                offset=match.offset,
                line=line,
                context=match.context,
            )
        )

    words = sum(1 for _ in _WORD.finditer(text))
    by_pattern: dict[int, int] = {}
    for hit in hits:
        by_pattern[hit.pattern] = by_pattern.get(hit.pattern, 0) + 1
    rate = round(len(hits) * 1000 / words, 2) if words else 0.0
    return Density(
        words=words,
        hits=tuple(hits),
        per_thousand=rate,
        by_pattern=by_pattern,
    )
