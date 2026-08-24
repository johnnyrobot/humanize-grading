"""Layer 2: replace structured personal information found by regex.

Each pattern must be specific enough to leave subject matter alone. A cited
year and a statutory section number are not identifiers.
"""

from __future__ import annotations

import re

from .tokens import TokenMap

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<!\d)(?:\+1[ -]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)")
DEFAULT_ID = re.compile(
    r"(?:\b(?:student\s+)?id(?:\s+(?:is|no\.?|number))?\s*[:#]?\s*|\bstudent\s+)"
    r"([A-Za-z]{0,2}-?\d{6,9})\b",
    re.IGNORECASE,
)
PROFILE_URL = re.compile(
    r"(?:https?://)?(?:www\.)?"
    r"(?:linkedin\.com/in/|github\.com/|x\.com/|twitter\.com/|facebook\.com/|instagram\.com/)[\w.-]+"
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

    def substitute(kind: str, pattern: re.Pattern[str], source: str, group: int = 0) -> str:
        nonlocal total

        def swap(match: re.Match[str]) -> str:
            nonlocal total
            total += 1
            value = match.group(group)
            token = tmap.allocate(kind, value, source_file)

            if group == 0:
                return token
            else:
                # For group 1, preserve the prefix/cue and replace only the captured group.
                prefix = match.group(0)[:match.start(group) - match.start(0)]
                suffix = match.group(0)[match.end(group) - match.start(0):]
                return prefix + token + suffix

        return pattern.sub(swap, source)

    for kind, pattern in _ORDER:
        text = substitute(kind, pattern, text)
    return substitute("ID", id_pattern, text, group=1), total
