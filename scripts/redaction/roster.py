"""Layer 1: replace roster identities with tokens.

Full names, emails, and student IDs are always replaced. A bare first or last
name is replaced only when that student's full name also appears in the same
text, so an essay about the Maya civilization keeps its subject matter.
Protected names, such as the instructor's, are shielded first.

Full names go first, for every student, before any bare name is touched.
Otherwise one student's bare surname can eat another student's full name
still waiting in line (Anna Chen and Samantha Chen collapsing into one
token). Each bare token is then keyed on the bare text itself, not the
full name, so rehydrating a bare hit restores the original word instead of
expanding it back into a full name that was never there.
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


def _context(text: str, match: re.Match) -> str:
    """Return the text around one match, for the instructor to review."""
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
    result = RosterResult(text=text)
    full_name_present: dict[str, bool] = {}

    # Pass 1: full names, emails, and student IDs for every entry, before
    # the protected-name shield or any bare name is touched. A student's
    # full name is PII even when a protected name (e.g. an instructor who
    # shares a surname with a student) is one of its substrings, so it must
    # be tokenized before the shield can hide that substring from view.
    for entry in roster:
        full_forms = full_name_variants(entry.name)
        present = any(
            re.search(rf"\b{re.escape(form)}\b", result.text) for form in full_forms
        )
        full_name_present[entry.name] = present

        if present:
            token = tmap.allocate("STUDENT", entry.name, source_file)
            for form in full_forms:
                result.text, count = _replace(result.text, form, token)
                if count:
                    result.matched.add(entry.name)

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

    # Shield protected names (e.g. the instructor's) from the bare-name
    # pass. This runs after pass 1, not before it, so a student's full name
    # is tokenized first and never corrupted by a protected name that
    # happens to be one of its substrings.
    shields: dict[str, str] = {}
    for index, name in enumerate(protected):
        sentinel = f"\x00SHIELD{index}\x00"
        shields[sentinel] = name
        result.text = re.sub(rf"\b{re.escape(name)}\b", sentinel, result.text)

    # Pass 2: bare first/last names, only for entries whose full name was
    # present. Each bare token is keyed on the literal bare text, so a
    # rehydrated bare hit comes back as the word it replaced, not the
    # student's whole name. Every occurrence gets its own BareHit, with its
    # own context, since the instructor needs to see each one that was
    # substituted, not just the first.
    if bare_names:
        for entry in roster:
            if not full_name_present[entry.name]:
                continue
            for bare in bare_name_variants(entry.name):
                pattern = rf"\b{re.escape(bare)}\b"
                matches = list(re.finditer(pattern, result.text))
                if not matches:
                    continue
                bare_token = tmap.allocate("STUDENT", bare, source_file)
                for match in matches:
                    context = _context(result.text, match)
                    result.bare_hits.append(BareHit(bare, bare_token, context))
                result.text = re.sub(pattern, bare_token, result.text)

    for sentinel, name in shields.items():
        result.text = result.text.replace(sentinel, name)

    return result
