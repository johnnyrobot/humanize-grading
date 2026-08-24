"""Put real names back into draft feedback. Runs locally only.

Fails when a token in the drafts has no mapping. A stray STUDENT_12 pasted
into an LMS comment is worth failing the run over.
"""

from __future__ import annotations

import re

from .tokens import KINDS, TokenMap

TOKEN = re.compile(rf"\b(?:{'|'.join(KINDS)})_\d{{2,}}\b")


def rehydrate_text(text: str, tmap: TokenMap) -> tuple[str, list[str]]:
    """Replace tokens with their real values and report any that are unknown."""
    unmapped: list[str] = []

    def swap(match: re.Match[str]) -> str:
        token = match.group(0)
        value = tmap.value_for(token)
        if value is None:
            if token not in unmapped:
                unmapped.append(token)
            return token
        return value

    return TOKEN.sub(swap, text), unmapped
