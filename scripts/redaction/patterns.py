"""Layer 2: replace structured personal information found by regex.

Each pattern must be specific enough to leave subject matter alone. A cited
year and a statutory section number are not identifiers.
"""

from __future__ import annotations

import re

from .tokens import TokenMap

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"\b(?:\+1[ -]?)?\(?\d{3}\)?[ .-]\d{3}[ .-]\d{4}\b")
DEFAULT_ID = re.compile(r"\b[A-Z]{1,2}\d{6,9}\b")
PROFILE_URL = re.compile(
    r"https?://(?:www\.)?(?:linkedin\.com/in/|github\.com/|x\.com/|twitter\.com/|facebook\.com/)[\w.-]+"
)

_ORDER = (
    ("EMAIL", EMAIL),
    ("URL", PROFILE_URL),
    ("PHONE", PHONE),
)


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
            return tmap.allocate(kind, match.group(0), source_file)

        return pattern.sub(swap, source)

    for kind, pattern in _ORDER:
        text = substitute(kind, pattern, text)
    return substitute("ID", id_pattern, text), total
