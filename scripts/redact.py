#!/usr/bin/env python3
"""Redact student submissions before any of them reach a cloud model.

Usage:
    python3 scripts/redact.py SOURCE --roster roster.csv --batch essay-3

Exit codes: 0 clean, 2 unmatched roster entry, 3 unreviewed candidates,
4 unreadable or oversized input, 5 destination not empty, 6 unsafe path,
7 participant collision. A non-zero exit must
stop the cloud path. There is no override for 2, 3, or 4 -- if this prints
anything other than a clean exit for one of those, fix the input and run it
again rather than forcing the cloud path open. Exit 5 exists only to stop a
stale files from an earlier redaction silently mixing into a new one; pass --force
to overwrite the earlier redaction's token-named outputs (nothing else in the
directory is touched).

The review file (candidate names, self-identifying passages, bare-name
substitutions, and any skipped-empty submission) is written next to the
map, not into the redacted output folder: it holds real names, and the
whole point of --out is that nothing in it does.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from redaction.pipeline import (  # noqa: E402
    EXIT_DESTINATION_NOT_EMPTY,
    EXIT_OK,
    EXIT_UNSAFE_PATH,
    run,
)
from redaction.roster import load_roster  # noqa: E402
from path_safety import require_component  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Folder of raw submissions")
    parser.add_argument("--roster", type=Path, required=True, help="Roster CSV")
    parser.add_argument(
        "--batch",
        required=True,
        type=lambda value: require_component(value, "The grading batch"),
        help="Batch name, such as essay-3",
    )
    parser.add_argument("--out", type=Path, default=Path("redacted"))
    parser.add_argument("--map", type=Path, default=None)
    parser.add_argument(
        "--review",
        type=Path,
        default=None,
        help="Where to write the review file. Defaults next to --map, never --out.",
    )
    parser.add_argument("--instructor", action="append", default=[],
                        help="A name that must never be redacted. Repeatable.")
    parser.add_argument("--no-bare-names", action="store_true",
                        help="Skip bare first and last names")
    parser.add_argument("--reviewed", action="store_true",
                        help="Confirm you read the review file")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Allow writing into a non-empty --out, clearing its prior "
        "token-named outputs first",
    )
    args = parser.parse_args(argv)

    map_path = args.map or Path(".grading/maps") / f"{args.batch}.json"
    review_path = args.review or map_path.parent / "reviews" / f"{args.batch}.json"

    result = run(
        source=args.source,
        destination=args.out,
        map_path=map_path,
        roster=load_roster(args.roster),
        batch=args.batch,
        protected=tuple(args.instructor),
        bare_names=not args.no_bare_names,
        reviewed=args.reviewed,
        review_path=review_path,
        force=args.force,
    )

    if result.exit_code in {EXIT_DESTINATION_NOT_EMPTY, EXIT_UNSAFE_PATH}:
        print(result.message, file=sys.stderr)
        print("\nRedaction did not complete cleanly. Do not send this to a "
              "cloud model.", file=sys.stderr)
        return result.exit_code

    print(f"Read {len(result.files)} files. Wrote {args.out}. Map at {map_path}.")
    for index, entry in enumerate(result.files, start=1):
        label = entry.output.name if entry.output else f"submission {index}"
        if entry.error:
            print(f"  ERROR {label}: could not be redacted safely. See {review_path}.")
        elif entry.skipped_empty:
            print(f"  SKIPPED (empty submission) {label}")
    for entry in result.unmatched_roster:
        print(f"  UNMATCHED roster entry never found: {entry.name}")
    flagged = sum(len(f.candidates) for f in result.files)
    warned = sum(len(f.warnings) for f in result.files)
    bare = sum(len(f.bare_hits) for f in result.files)
    print(f"  {flagged} names to review, {warned} self-identifying passages, "
          f"{bare} bare-name substitutions. See {review_path}.")

    if result.exit_code != EXIT_OK:
        print("\nRedaction did not complete cleanly. Do not send this to a "
              "cloud model.", file=sys.stderr)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
