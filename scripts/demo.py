#!/usr/bin/env python3
"""Prepare the exact synthetic corpus for a one-turn narrated grading demo."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.demo import DemoError, prepare_demo  # noqa: E402

EXIT_MALFORMED = 13


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare = subparsers.add_parser("prepare", help="Validate and prepare a demo run")
    prepare.add_argument("--source", type=Path, required=True)
    prepare.add_argument("--runs-root", type=Path)
    prepare.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        result = prepare_demo(
            Path(__file__).resolve().parent.parent,
            args.source,
            runs_root=args.runs_root,
        )
    except DemoError as error:
        print(f"Demo preparation stopped: {error}", file=sys.stderr)
        print(
            "Use the supervised grading workflow for this folder.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Demo preparation stopped: {error}", file=sys.stderr)
        return EXIT_MALFORMED

    payload = result.as_dict()
    if args.json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(f"Prepared synthetic demo run {result.run_id}.")
        print(f"State: {result.state_root}")
        print(f"Next action: {result.next_action['action']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
