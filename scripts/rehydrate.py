#!/usr/bin/env python3
"""Put real names back into draft feedback. Runs locally only.

Usage:
    python3 scripts/rehydrate.py DRAFTS --map .grading/maps/essay-3.json --out final

Requires a current verification receipt and fails before publication when a
token has no mapping or a draft cannot be rendered. The destination changes
only after every draft succeeds.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.batch import RenderResult, VerifiedBatch  # noqa: E402
from scripts.redaction.rehydrate import rehydrate_text  # noqa: E402
from scripts.redaction.tokens import TokenMap  # noqa: E402


class TokenMapRenderer:
    """Write rehydrated drafts while keeping identities outside grading modules."""

    def __init__(self, token_map: TokenMap) -> None:
        self.batch = token_map.batch
        self._token_map = token_map

    @classmethod
    def load(cls, map_path: Path) -> "TokenMapRenderer":
        """Load the identity map inside the local renderer adapter."""
        return cls(TokenMap.load(map_path))

    def render(self, source: Path, destination: Path) -> RenderResult:
        try:
            text = source.read_text(encoding="utf-8")
            rendered, unmapped = rehydrate_text(text, self._token_map)
            destination.write_text(rendered, encoding="utf-8")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        except (OSError, UnicodeError, ValueError) as error:
            return RenderResult(error=str(error))
        return RenderResult(
            sha256=digest,
            unmapped_tokens=tuple(unmapped),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drafts", type=Path, help="Folder of tokenized drafts")
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("final"))
    args = parser.parse_args(argv)

    resumed = VerifiedBatch.resume(args.drafts)
    if resumed.verified_batch is None:
        for finding in resumed.findings:
            print(f"  ERROR {finding.code}: {finding.message}", file=sys.stderr)
        return 5

    try:
        renderer = TokenMapRenderer.load(args.map)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        print(f"  ERROR map-unreadable: {error}", file=sys.stderr)
        return 5
    report = resumed.verified_batch.publish(args.out, renderer)
    if report.findings:
        for finding in report.findings:
            where = f" [{finding.token}]" if finding.token else ""
            print(
                f"  ERROR {finding.code}{where}: {finding.message}",
                file=sys.stderr,
            )
        return 5

    print(
        f"Wrote {args.out}. Every rehydrated feedback file is a DRAFT pending "
        "your sign-off."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
