#!/usr/bin/env python3
"""Local checks that stand behind the grading skill.

The model does the judgement. This does the checking.

Subcommands:
    profile      Confirm a course has a profile and that its rubric has not drifted.
    verify       Check every draft in a batch. Write the quote offsets.
    status       Show which submissions still need a draft.
    density      Count watched AI terms in one file.
    consistency  Flag outliers across a graded batch.

Exit codes: 0 clean, 10 no profile, 11 the rubric drifted since calibration,
12 a draft failed verification, 13 malformed input.

Nothing here loads the token map. Quotes and tokens are checked against the
redacted source, which is the file the model actually read.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.density import PATTERNS_PATH, load_terms  # noqa: E402
from scripts.grading.batch import (  # noqa: E402
    BatchPaths,
    GradingBatch,
    VerificationFailure,
    VerificationInputs,
)
from scripts.grading.density import scan  # noqa: E402
from scripts.grading.profile import (  # noqa: E402
    ProfileError,
    load_profile,
    profile_path,
)
from scripts.grading.records import RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402

EXIT_OK = 0
EXIT_NO_PROFILE = 10
EXIT_DRIFT = 11
EXIT_FAILED = 12
EXIT_MALFORMED = 13


def _drafts_dir(args: argparse.Namespace) -> Path:
    return args.drafts or (args.root / "drafts" / args.batch)


def _profile_or_exit(args: argparse.Namespace):
    path = profile_path(args.course, args.root)
    try:
        return load_profile(path)
    except ProfileError as error:
        print(str(error), file=sys.stderr)
        print(
            f"Run `/grade calibrate --course {args.course}` first. Grading "
            "without a profile would produce feedback in nobody's voice.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_NO_PROFILE) from error


def _cmd_profile(args: argparse.Namespace) -> int:
    profile = _profile_or_exit(args)
    rubric = load_rubric(args.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        print(f"The rubric changed since {profile.calibrated}. Drifted criteria:")
        for name in drifted:
            print(f"  {name}")
        print(
            "\nGrading against a drifted criterion applies standards that were "
            "calibrated for different words. Recalibrate, or confirm you want "
            "to proceed anyway.",
            file=sys.stderr,
        )
        return EXIT_DRIFT
    print(
        f"Profile for {profile.course} is current, calibrated "
        f"{profile.calibrated} from {profile.samples} samples."
    )
    return EXIT_OK


def _print_batch_finding(finding, *, blocking: bool) -> None:
    mark = "ERROR  " if blocking else "warning"
    where = f" [{finding.token}]" if finding.token else ""
    print(f"  {mark} {finding.code}{where}: {finding.message}")


def _cmd_verify(args: argparse.Namespace) -> int:
    drafts = _drafts_dir(args)
    report = GradingBatch.open(
        BatchPaths(batch=args.batch, redacted=args.redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course=args.course,
            rubric=args.rubric,
            profile=profile_path(args.course, args.root),
            patterns=args.patterns,
        ),
        persist=not args.no_write,
    )

    for token in report.checked_tokens:
        bad = sum(
            1 for finding in report.blocking_findings if finding.token == token
        )
        warned = sum(1 for finding in report.warnings if finding.token == token)
        print(f"{token}: {bad} errors, {warned} warnings")
    for finding in report.blocking_findings:
        _print_batch_finding(finding, blocking=True)
    for finding in report.warnings:
        _print_batch_finding(finding, blocking=False)

    if report.failure is VerificationFailure.NO_PROFILE:
        print(
            f"Run `/grade calibrate --course {args.course}` first. Grading "
            "without a profile would produce feedback in nobody's voice.",
            file=sys.stderr,
        )
        return EXIT_NO_PROFILE
    if report.failure is VerificationFailure.RUBRIC_DRIFT:
        print(
            "Grading against a drifted criterion applies standards calibrated "
            "for different words. Recalibrate first.",
            file=sys.stderr,
        )
        return EXIT_DRIFT
    if report.failure is VerificationFailure.MALFORMED:
        return EXIT_MALFORMED
    if report.failure is VerificationFailure.FAILED:
        failed = len(
            {finding.token for finding in report.blocking_findings if finding.token}
        )
        print(
            f"\n{failed} of {len(report.status.entries)} drafts failed. Fix them before "
            "rehydrating. A failed draft must not reach a student.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    if args.no_write:
        print(f"\nAll {len(report.checked_tokens)} drafts verified. Nothing written.")
    else:
        print(
            f"\nAll {len(report.checked_tokens)} drafts verified. "
            "Offsets and verification receipt written."
        )
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    report = GradingBatch.open(
        BatchPaths(
            batch=args.batch,
            redacted=args.redacted,
            drafts=_drafts_dir(args),
        )
    ).status()
    if not report.entries:
        print(f"Nothing under {args.redacted}. Run redact.py first.")
        return EXIT_OK

    states = {item.token: report.display_state(item) for item in report.entries}

    width = max(len(item.token) for item in report.entries)
    for item in report.entries:
        print(f"  {item.token.ljust(width)}  {states[item.token]}")
    counts = report.counts()
    print(
        f"\n{counts['pending']} pending, {counts['drafted']} drafted, "
        f"{counts['incomplete']} incomplete, {counts['orphan']} orphaned, "
        f"{counts['verified']} verified."
    )
    if counts["orphan"]:
        print(
            "An orphaned artifact has no redacted submission. It is usually a "
            "prior run's output. Move it out before grading.",
            file=sys.stderr,
        )
    if counts["incomplete"]:
        print(
            "An incomplete submission has a draft or a record but not both. "
            "Regrade it.",
            file=sys.stderr,
        )
    for finding in report.blocking_findings:
        if finding.code not in {"incomplete-pair", "orphaned-artifact"}:
            print(
                f"  ERROR {finding.code} [{finding.token}]: {finding.message}",
                file=sys.stderr,
            )
    return EXIT_OK


def _cmd_density(args: argparse.Namespace) -> int:
    terms = load_terms(args.patterns)
    result = scan(args.path.read_text(encoding="utf-8"), terms)
    if args.json:
        import json

        print(json.dumps(result.as_dict(), indent=2, ensure_ascii=False))
        return EXIT_OK
    print(f"{args.path.name}: {result.words} words, {len(result.hits)} watched terms")
    print(f"  {result.per_thousand} per 1000 words")
    for pattern, count in sorted(result.by_pattern.items()):
        print(f"  pattern {pattern}: {count}")
    print(
        "\nThis is evidence for the prose criterion. It says nothing about who "
        "wrote the essay, and must never be used to claim it does."
    )
    return EXIT_OK

def _cmd_consistency(args: argparse.Namespace) -> int:
    drafts_dir = _drafts_dir(args)
    report = GradingBatch.open(
        BatchPaths(batch=args.batch, redacted=args.redacted, drafts=drafts_dir)
    ).consistency(threshold=args.threshold)
    for finding in report.blocking_findings:
        _print_batch_finding(finding, blocking=True)
    print(
        f"{report.records_count} graded submissions, "
        f"{len(report.flags)} things to look at."
    )
    for flag in report.flags:
        print(f"  {flag.kind}: {flag.message}")
    if not report.flags:
        print("  Nothing stood out.")
    print(
        "\nThese are flags, not failures. An outlier is often a genuinely "
        "unusual essay. Read the drafts before changing a score."
    )
    if report.blocking_findings:
        return EXIT_MALFORMED
    if report.flags and args.fail_on_findings:
        return EXIT_FAILED
    return EXIT_OK

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--root", type=Path, default=Path(".grading"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("profile", help="Check the profile and rubric drift")
    check.add_argument("--course", required=True)
    check.add_argument("--rubric", type=Path, required=True)
    _add_common(check)
    check.set_defaults(handler=_cmd_profile)

    verify = subparsers.add_parser("verify", help="Check every draft in a batch")
    verify.add_argument("--batch", required=True)
    verify.add_argument("--course", required=True)
    verify.add_argument("--rubric", type=Path, required=True)
    verify.add_argument("--redacted", type=Path, default=Path("redacted"))
    verify.add_argument("--drafts", type=Path, default=None)
    verify.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    verify.add_argument(
        "--no-write",
        action="store_true",
        help="Do not write located offsets back into the records",
    )
    _add_common(verify)
    verify.set_defaults(handler=_cmd_verify)

    status = subparsers.add_parser("status", help="Show what a batch still owes")
    status.add_argument("--batch", required=True)
    status.add_argument("--redacted", type=Path, default=Path("redacted"))
    status.add_argument("--drafts", type=Path, default=None)
    _add_common(status)
    status.set_defaults(handler=_cmd_status)

    density = subparsers.add_parser("density", help="Count watched AI terms in a file")
    density.add_argument("path", type=Path)
    density.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    density.add_argument("--json", action="store_true")
    _add_common(density)
    density.set_defaults(handler=_cmd_density)
    consistency = subparsers.add_parser(
        "consistency", help="Flag outliers across a graded batch"
    )
    consistency.add_argument("--batch", required=True)
    consistency.add_argument("--redacted", type=Path, default=Path("redacted"))
    consistency.add_argument("--drafts", type=Path, default=None)
    consistency.add_argument("--threshold", type=float, default=2.0)
    consistency.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="Exit non-zero when anything is flagged",
    )
    _add_common(consistency)
    consistency.set_defaults(handler=_cmd_consistency)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)
    except (RubricError, RecordError, ProfileError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED


if __name__ == "__main__":
    raise SystemExit(main())
