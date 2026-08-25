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
    DRAFTED,
    INCOMPLETE,
    ORPHAN,
    PENDING,
    summary,
    survey,
)
from scripts.grading.density import scan  # noqa: E402
from scripts.grading.consistency import review  # noqa: E402
from scripts.grading.profile import (  # noqa: E402
    ProfileError,
    load_profile,
    profile_path,
)
from scripts.grading.records import GradeRecord, RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402
from scripts.grading.verify import ERROR, Finding, errors, verify_draft  # noqa: E402

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


def _print(finding: Finding) -> None:
    mark = "ERROR  " if finding.level == ERROR else "warning"
    where = f" [{finding.token}]" if finding.token else ""
    print(f"  {mark} {finding.code}{where}: {finding.message}")


def _cmd_verify(args: argparse.Namespace) -> int:
    profile = _profile_or_exit(args)
    rubric = load_rubric(args.rubric)
    terms = load_terms(args.patterns)
    drafts = _drafts_dir(args)

    records = sorted(drafts.glob("*.json"))
    if not records:
        print(f"No records under {drafts}. Nothing to verify.", file=sys.stderr)
        return EXIT_MALFORMED

    failed = 0
    for record_path in records:
        record = GradeRecord.load(record_path)
        markdown_path = record_path.with_suffix(".md")
        if not markdown_path.is_file():
            print(f"{record_path.name}: no draft at {markdown_path.name}")
            failed += 1
            continue
        source_path = Path(record.source)
        if not source_path.is_file():
            source_path = args.redacted / f"{record.token}.md"
        if not source_path.is_file():
            print(f"{record_path.name}: no redacted source at {record.source}")
            failed += 1
            continue

        findings = verify_draft(
            record,
            markdown_path.read_text(encoding="utf-8"),
            source_path.read_text(encoding="utf-8"),
            rubric,
            profile,
            terms,
        )
        bad = errors(findings)
        print(f"{record.token}: {len(bad)} errors, {len(findings) - len(bad)} warnings")
        for finding in findings:
            _print(finding)
        if bad:
            failed += 1
        elif not args.no_write:
            record.save(record_path)

    if failed:
        print(
            f"\n{failed} of {len(records)} drafts failed. Fix them before "
            "rehydrating. A failed draft must not reach a student.",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"\nAll {len(records)} drafts verified. Offsets written.")
    return EXIT_OK


def _cmd_status(args: argparse.Namespace) -> int:
    items = survey(args.redacted, _drafts_dir(args))
    if not items:
        print(f"Nothing under {args.redacted}. Run redact.py first.")
        return EXIT_OK
    width = max(len(item.token) for item in items)
    for item in items:
        print(f"  {item.token.ljust(width)}  {item.state}")
    counts = summary(items)
    print(
        f"\n{counts[PENDING]} pending, {counts[DRAFTED]} drafted, "
        f"{counts[INCOMPLETE]} incomplete, {counts[ORPHAN]} orphaned."
    )
    if counts[ORPHAN]:
        print(
            "An orphaned draft has no redacted source. It is usually a prior "
            "run's output. Move it out before grading.",
            file=sys.stderr,
        )
    if counts[INCOMPLETE]:
        print(
            "An incomplete submission has a draft or a record but not both. "
            "Regrade it.",
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
    items = [item for item in survey(args.redacted, drafts_dir) if item.record]
    records = []
    drafts: dict[str, str] = {}
    for item in items:
        record = GradeRecord.load(item.record)
        records.append(record)
        if item.draft:
            drafts[record.token] = item.draft.read_text(encoding="utf-8")

    if not records:
        print(f"No records under {drafts_dir}. Nothing to compare.", file=sys.stderr)
        return EXIT_MALFORMED

    flags = review(records, drafts, threshold=args.threshold)
    print(f"{len(records)} graded submissions, {len(flags)} things to look at.")
    for flag in flags:
        print(f"  {flag.kind}: {flag.message}")
    if not flags:
        print("  Nothing stood out.")
    print(
        "\nThese are flags, not failures. An outlier is often a genuinely "
        "unusual essay. Read the drafts before changing a score."
    )
    if flags and args.fail_on_findings:
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
