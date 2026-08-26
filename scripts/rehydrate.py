#!/usr/bin/env python3
"""Put real names back into draft feedback. Runs locally only.

Usage:
    python3 scripts/rehydrate.py DRAFTS --batch essay-3 --course phil210 \
      --rubric rubric.md --profile .grading/profiles/phil210.md \
      --redacted redacted --map .grading/maps/essay-3.json --out final

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

from scripts.grading.batch import (  # noqa: E402
    BatchPaths,
    RenderResult,
    VerifiedBatch,
    VerificationInputs,
)
from scripts.grading.density import PATTERNS_PATH  # noqa: E402
from scripts.path_safety import read_bytes_limited, require_component  # noqa: E402
from scripts.redaction.rehydrate import rehydrate_text  # noqa: E402
from scripts.redaction.tokens import TokenMap  # noqa: E402


class TokenMapRenderer:
    """Write rehydrated drafts while keeping identities outside grading modules."""

    def __init__(self, token_map: TokenMap) -> None:
        self.batch = token_map.batch
        self.run_id = token_map.run_id
        self._token_map = token_map

    @classmethod
    def load(cls, map_path: Path) -> "TokenMapRenderer":
        """Load the identity map inside the local renderer adapter."""
        return cls(TokenMap.load(map_path))

    def render(
        self, source: Path, destination: Path, expected_sha256: str
    ) -> RenderResult:
        try:
            source_bytes = read_bytes_limited(
                source, "The feedback draft", 1_048_576
            )
            if hashlib.sha256(source_bytes).hexdigest() != expected_sha256:
                return RenderResult(
                    error=(
                        f"{source.name} changed after publication validation. "
                        "Run verification again."
                    )
                )
            text = source_bytes.decode("utf-8")
            rendered, unmapped = rehydrate_text(text, self._token_map)
            destination.write_text(rendered, encoding="utf-8")
            digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        except (OSError, UnicodeError, ValueError) as error:
            return RenderResult(error=str(error))
        return RenderResult(
            sha256=digest,
            unmapped_tokens=tuple(unmapped),
        )

    def hash_output(self, path: Path) -> str:
        """Hash rehydrated output without returning its identity-bearing text."""
        return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drafts", type=Path, help="Folder of tokenized drafts")
    parser.add_argument(
        "--batch",
        type=lambda value: require_component(value, "The grading batch"),
    )
    parser.add_argument(
        "--course",
        type=lambda value: require_component(value, "The course"),
    )
    parser.add_argument("--rubric", type=Path)
    parser.add_argument("--profile", type=Path)
    parser.add_argument("--redacted", type=Path)
    parser.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    parser.add_argument("--map", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=Path("final"))
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace only an unchanged publication from this grading batch",
    )
    args = parser.parse_args(argv)

    supplied = (args.batch, args.course, args.rubric, args.profile, args.redacted)
    if any(value is not None for value in supplied) and not all(
        value is not None for value in supplied
    ):
        print(
            "  ERROR context-incomplete: pass batch, course, rubric, profile and "
            "redacted together, or omit all five for the legacy receipt context.",
            file=sys.stderr,
        )
        return 5
    try:
        if all(value is not None for value in supplied):
            paths = BatchPaths(
                batch=args.batch,
                redacted=args.redacted,
                drafts=args.drafts,
            )
            inputs = VerificationInputs(
                course=args.course,
                rubric=args.rubric,
                profile=args.profile,
                patterns=args.patterns,
            )
        else:
            paths, inputs = VerifiedBatch.context_from_receipt(args.drafts)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        print(f"  ERROR verification-context: {error}", file=sys.stderr)
        return 5
    resumed = VerifiedBatch.resume(paths, inputs)
    if resumed.verified_batch is None:
        for finding in resumed.findings:
            print(f"  ERROR {finding.code}: {finding.message}", file=sys.stderr)
        return 5

    try:
        renderer = TokenMapRenderer.load(args.map)
    except (OSError, UnicodeError, ValueError, TypeError, KeyError) as error:
        print(f"  ERROR map-unreadable: {error}", file=sys.stderr)
        return 5
    try:
        report = resumed.verified_batch.publish(
            args.out, renderer, force=args.force
        )
    except (OSError, UnicodeError, ValueError, TypeError) as error:
        print(f"  ERROR publication-failed: {error}", file=sys.stderr)
        return 5
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
