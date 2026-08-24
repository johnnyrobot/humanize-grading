"""Layer 1: replace roster identities with tokens.

Full names, emails, and student IDs are always replaced. A bare first or last
name is replaced only when that student's full name also appears in the same
text, so an essay about the Maya civilization keeps its subject matter.
Protected names, such as the instructor's, are shielded first.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .tokens import TokenMap

CONTEXT_WIDTH = 40


@dataclass(frozen=True)
class RosterEntry:
    """One student from the course roster."""

    name: str
    student_id: str = ""
    email: str = ""


@dataclass(frozen=True)
class BareHit:
    """A bare first or last name that was replaced, with its context."""

    name: str
    token: str
    context: str


@dataclass
class RosterResult:
    """What layer 1 changed and what the instructor should review."""

    text: str
    matched: set[str] = field(default_factory=set)
    bare_hits: list[BareHit] = field(default_factory=list)


def load_roster(path: Path) -> list[RosterEntry]:
    """Read a roster CSV with name, student_id, and email columns."""
    with path.open(newline="", encoding="utf-8") as handle:
        return [
            RosterEntry(
                row["name"].strip(),
                (row.get("student_id") or "").strip(),
                (row.get("email") or "").strip(),
            )
            for row in csv.DictReader(handle)
            if (row.get("name") or "").strip()
        ]


def full_name_variants(name: str) -> list[str]:
    """Return the written forms of a full name, longest first."""
    parts = name.split()
    variants = {name}
    if len(parts) >= 2:
        variants.add(f"{parts[-1]}, {' '.join(parts[:-1])}")
        variants.add(f"{parts[0][0]}. {parts[-1]}")
    return sorted(variants, key=lambda s: (-len(s), s))


def bare_name_variants(name: str) -> list[str]:
    """Return the first and last name on their own, longest first."""
    parts = name.split()
    if len(parts) < 2:
        return []
    return sorted({parts[0], parts[-1]}, key=lambda s: (-len(s), s))


def _replace(text: str, needle: str, token: str) -> tuple[str, int]:
    return re.subn(rf"\b{re.escape(needle)}\b", token, text)


def _context(text: str, needle: str) -> str:
    match = re.search(rf"\b{re.escape(needle)}\b", text)
    if not match:
        return ""
    start = max(0, match.start() - CONTEXT_WIDTH)
    return text[start : match.end() + CONTEXT_WIDTH].replace("\n", " ").strip()


def redact_roster(
    text: str,
    roster: list[RosterEntry],
    tmap: TokenMap,
    source_file: str,
    protected: tuple[str, ...] = (),
    bare_names: bool = True,
) -> RosterResult:
    """Replace roster identities in text and report what changed."""
    shields: dict[str, str] = {}
    for index, name in enumerate(protected):
        sentinel = f"\x00SHIELD{index}\x00"
        shields[sentinel] = name
        text = re.sub(rf"\b{re.escape(name)}\b", sentinel, text)

    result = RosterResult(text=text)

    for entry in roster:
        full_forms = full_name_variants(entry.name)
        present = any(
            re.search(rf"\b{re.escape(form)}\b", result.text) for form in full_forms
        )

        if present:
            token = tmap.allocate("STUDENT", entry.name, source_file)
            for form in full_forms:
                result.text, count = _replace(result.text, form, token)
                if count:
                    result.matched.add(entry.name)
            if bare_names:
                for bare in bare_name_variants(entry.name):
                    context = _context(result.text, bare)
                    result.text, count = _replace(result.text, bare, token)
                    if count:
                        result.bare_hits.append(BareHit(bare, token, context))

        if entry.email and entry.email in result.text:
            email_token = tmap.allocate("EMAIL", entry.email, source_file)
            result.text = result.text.replace(entry.email, email_token)
            result.matched.add(entry.name)

        if entry.student_id and re.search(
            rf"\b{re.escape(entry.student_id)}\b", result.text
        ):
            id_token = tmap.allocate("ID", entry.student_id, source_file)
            result.text, count = _replace(result.text, entry.student_id, id_token)
            if count:
                result.matched.add(entry.name)

    for sentinel, name in shields.items():
        result.text = result.text.replace(sentinel, name)

    return result
