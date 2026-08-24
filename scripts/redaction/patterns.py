"""Layer 2: replace structured personal information found by regex.

Each pattern must be specific enough to leave subject matter alone. A cited
year and a statutory section number are not identifiers.
"""

from __future__ import annotations

import re

from .tokens import TokenMap

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PHONE = re.compile(r"(?<![\w.])(?:\+1[ -]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\w)")
DEFAULT_ID = re.compile(
    r"(?:\b(?:student\s+)?(?:sid|id|student\s+number)(?:\s+(?:is|no\.?|number))?)"
    r"[\s*_#:\-]{0,6}"
    r"([A-Za-z]{0,2}-?\d{6,9})\b"
    r"|\bstudent\s+([A-Za-z]{1,2}-?\d{6,9})\b",
    re.IGNORECASE,
)
PROFILE_URL = re.compile(
    r"(?<![\w/.])(?:https?://)?(?:www\.)?"
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

    def substitute(kind: str, pattern: re.Pattern[str], source: str, group: int = 0, groups: list[int] | None = None) -> str:
        nonlocal total

        def swap(match: re.Match[str]) -> str:
            nonlocal total
            total += 1
            # For multi-group patterns, pick the non-None group
            if groups:
                value = match.group(groups[0]) or match.group(groups[1])
                group_idx = groups[0] if match.group(groups[0]) else groups[1]
            else:
                value = match.group(group)
                group_idx = group

            token = tmap.allocate(kind, value, source_file)

            if group_idx == 0:
                return token
            else:
                # For group 1+, preserve the prefix/cue and replace only the captured group.
                prefix = match.group(0)[:match.start(group_idx) - match.start(0)]
                suffix = match.group(0)[match.end(group_idx) - match.start(0):]
                return prefix + token + suffix

        return pattern.sub(swap, source)

    for kind, pattern in _ORDER:
        text = substitute(kind, pattern, text)
    return substitute("ID", id_pattern, text, groups=[1, 2]), total
