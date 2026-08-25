#!/usr/bin/env python3
"""Deny reads of raw submissions, the token map, and the roster.

SKILL.md states four hard rules. They are instructions, and a model can
violate an instruction. This closes that gap for the tools it covers.

Reads one PreToolUse payload on stdin. Exit 2 denies the call and shows the
reason. Exit 0 allows it.

Two deliberate limits, both worth stating plainly:

Bash is checked by reading the command string. A denial fires only when a
reading utility and a guarded path appear together, because `redact.py
--roster roster.csv` is a legitimate call and `cat roster.csv` is not. A
determined bypass -- encoding the path, or building it at runtime -- defeats
this. The hook stops accidents and instruction drift, not an adversary.

Unreadable input allows rather than denies. A guard that cannot parse its
payload and denies everything would block every tool call in the session,
including the ones that would fix it. It warns instead.
"""

from __future__ import annotations

import json
import re
import sys

# Never readable. `submissions/` holds raw student work, `maps/` is the only
# artifact that turns a token back into a name, and `reviews/` lists the very
# names redaction just removed.
GUARDED = (
    "submissions/",
    ".grading/maps/",
    ".grading/reviews/",
)

# The prefix class, rather than a plain (^|/), is what catches a bare
# `cat roster.csv` as well as `cat course/roster.csv`.
_ROSTER = re.compile(r"""(?:^|[\s"'=:/])[\w.-]*roster\.csv\b""", re.IGNORECASE)

# Fields that carry a path across the file tools.
_PATH_FIELDS = ("file_path", "path", "notebook_path", "pattern", "glob")

# Utilities that put a file's contents into the conversation. A command has to
# use one of these AND name a guarded path before it is denied.
_READERS = re.compile(
    r"(^|[\s;|&(])"
    r"(cat|bat|head|tail|less|more|nl|strings|xxd|od|open|"
    r"grep|rg|ag|ack|awk|sed|cut|jq|python3?\s+-c|node\s+-e|perl\s+-e)"
    r"([\s;|&)]|$)"
)

REASON = (
    "The grading skill reads only from redacted/. "
    "Run scripts/redact.py first, then read the redacted copy. "
    "The map and review files never enter a model's context."
)


def offending(text: str) -> str | None:
    """Return the guarded fragment a path contains, or None."""
    haystack = text.replace("\\", "/")
    for guarded in GUARDED:
        if guarded in haystack:
            return guarded
    if _ROSTER.search(haystack):
        return "roster.csv"
    return None


def decide(payload: dict) -> str | None:
    """Return the reason to deny this tool call, or None to allow it."""
    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return None

    if tool == "Bash":
        command = str(tool_input.get("command", ""))
        if not _READERS.search(command):
            return None
        found = offending(command)
        return f"{tool} would read {found}" if found else None

    for field in _PATH_FIELDS:
        value = tool_input.get(field)
        if not isinstance(value, str):
            continue
        found = offending(value)
        if found:
            return f"{tool} would touch {found}"
    return None


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(
            "block_raw_reads: could not read the hook payload, so the call was "
            "allowed. Check the hook configuration.",
            file=sys.stderr,
        )
        return 0
    if not isinstance(payload, dict):
        print("block_raw_reads: unexpected payload shape; allowed.", file=sys.stderr)
        return 0

    reason = decide(payload)
    if reason is None:
        return 0
    print(f"Blocked: {reason}. {REASON}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
