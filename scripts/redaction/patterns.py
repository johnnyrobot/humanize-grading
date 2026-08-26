"""Layer 2: replace structured personal information found by regex.

Each pattern must be specific enough to leave subject matter alone. A cited
year and a statutory section number are not identifiers.
"""

from __future__ import annotations

import re

from .tokens import TokenMap

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\w.])(?:\+1[ -]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)")

# A bare "id" cue (e.g. "Docket ID 12345678", "Case ID 9876543") is not
# evidence of a person: it is ordinary language in a policy essay about
# platform governance and identifiers of all kinds. Layer 1 (roster.py)
# already catches every roster student's ID by exact match; layer 2 is only
# a backstop for a non-roster person, which is rare. So this pattern
# requires an explicit STUDENT cue (or "sid") rather than a bare "id".
DEFAULT_ID = re.compile(
    r"(?:\bstudent(?:'s|’s)?(?:\s+(?:id|number|no\.?))?(?:\s+(?:is|no\.?|number))?|\bsid)"
    r"[\s*_#:\-]{0,6}"
    r"([A-Za-z]{0,2}-?\d{6,9})\b",
    re.IGNORECASE,
)

RESERVED_PATH = (
    r"(?:rules|tos|terms|privacy|policies|about|help|home|search|explore|settings|legal"
    r"|support|security|blog|careers|press|developers?|docs|i|intent|share|hashtag"
    r"|notifications|messages|login|signup|safety|community|guidelines|en)"
)

PROFILE_URL = re.compile(
    r"(?<![\w/.])(?:https?://)?(?:www\.)?(?:"
    r"linkedin\.com/in/[\w.-]+"
    r"|github\.com/(?!" + RESERVED_PATH + r"\b)[\w.-]++(?!/[\w.-])"
    r"|(?:x|twitter|facebook|instagram)\.com/(?!" + RESERVED_PATH + r"\b)[\w.-]+"
    r")"
)

_ORDER = (
    ("EMAIL", EMAIL),
    ("URL", PROFILE_URL),
    ("PHONE", PHONE),
)


def _replacement_span(match: re.Match[str]) -> tuple[int, int]:
    """Return the (start, end) offsets of the text a match should tokenize.

    If the pattern has capture groups, use the first non-None group's span
    so surrounding cue text (e.g. "Student") survives untouched. Otherwise
    the whole match is the identifier. This works for any capture-group
    count -- zero, one, or more -- so a caller-supplied ``id_pattern`` with a
    different group count from DEFAULT_ID cannot raise an IndexError here.
    """
    for idx in range(1, (match.re.groups or 0) + 1):
        if match.group(idx) is not None:
            return match.start(idx), match.end(idx)
    return match.start(0), match.end(0)


def _sweep_known_ids(text: str, tmap: TokenMap) -> tuple[str, int]:
    """Replace later bare repeats of an ID already established by a cued match.

    A cued match is what proves a value is a student ID. Once proven, every
    other occurrence of that exact value in the same document is the same ID,
    so it needs no cue of its own. Values never cued are never swept, which is
    why "Docket ID 12345678" stays untouched.
    """
    total = 0
    for token, entity in list(tmap.tokens().items()):
        if entity.kind != "ID":
            continue
        text, count = re.subn(rf"\b{re.escape(entity.value)}\b", token, text)
        total += count
    return text, total


def redact_structured(
    text: str,
    tmap: TokenMap,
    source_file: str,
    id_pattern: re.Pattern[str] = DEFAULT_ID,
) -> tuple[str, int]:
    """Replace emails, profile URLs, phone numbers, and student IDs."""
    total = 0

    def substitute(kind: str, pattern: re.Pattern[str], source: str) -> str:
        nonlocal total

        def swap(match: re.Match[str]) -> str:
            nonlocal total
            total += 1
            start, end = _replacement_span(match)
            value = match.string[start:end]
            token = tmap.allocate(kind, value, source_file)
            whole = match.group(0)
            base = match.start(0)
            prefix = whole[: start - base]
            suffix = whole[end - base :]
            return prefix + token + suffix

        return pattern.sub(swap, source)

    for kind, pattern in _ORDER:
        text = substitute(kind, pattern, text)
    text = substitute("ID", id_pattern, text)
    text, swept = _sweep_known_ids(text, tmap)
    total += swept
    return text, total


def scrub_structured(
    text: str, placeholder: str = "[REDACTED]"
) -> tuple[str, list[str]]:
    """Replace structured identity without creating a reversible token map."""
    found: list[str] = []
    id_values: list[str] = []

    def substitute(pattern: re.Pattern[str], source: str, *, is_id: bool = False) -> str:
        def swap(match: re.Match[str]) -> str:
            start, end = _replacement_span(match)
            value = match.string[start:end]
            found.append(value)
            if is_id:
                id_values.append(value)
            whole = match.group(0)
            base = match.start(0)
            return whole[: start - base] + placeholder + whole[end - base :]

        return pattern.sub(swap, source)

    scrubbed = text
    for _, pattern in _ORDER:
        scrubbed = substitute(pattern, scrubbed)
    scrubbed = substitute(DEFAULT_ID, scrubbed, is_id=True)
    for value in id_values:
        scrubbed = re.sub(
            rf"\b{re.escape(value)}\b", placeholder, scrubbed, flags=re.IGNORECASE
        )
    return scrubbed, found
