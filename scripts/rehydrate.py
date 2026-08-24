#!/usr/bin/env python3
"""Put real names back into draft feedback. Runs locally only.

Usage:
    python3 scripts/rehydrate.py DRAFTS --map .grading/maps/essay-3.json --out final

Fails when a token in the drafts has no mapping. A stray STUDENT_12 pasted
into an LMS comment is worth failing the run over.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from redaction.rehydrate import rehydrate_text  # noqa: E402
from redaction.tokens import TokenMap  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drafts", type=Path, help="Folder of tokenized drafts")
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("final"))
    args = parser.parse_args(argv)

    tmap = TokenMap.load(args.map)
    args.out.mkdir(parents=True, exist_ok=True)

    problems: list[str] = []
    for path in sorted(p for p in args.drafts.rglob("*") if p.is_file()):
        if path.name.startswith("_"):
            continue
        text, unmapped = rehydrate_text(path.read_text(encoding="utf-8"), tmap)
        if unmapped:
            problems.append(f"{path.name}: {', '.join(unmapped)}")
        (args.out / path.name).write_text(text, encoding="utf-8")

    if problems:
        print("Unmapped tokens found:", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 5

    print(f"Wrote {args.out}. Every file is a DRAFT pending your sign-off.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
