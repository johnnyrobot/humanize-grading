#!/usr/bin/env python3
"""Local checks that stand behind the grading skill.

The model does the judgement. This does the checking.

Subcommands:
    profile      Confirm a course has a profile and that its rubric has not drifted.
    verify       Check every draft in a batch. Write the quote offsets.
    status       Show which submissions still need a draft.
    density      Count watched AI terms in one file.
    consistency  Flag outliers across a graded batch.
    brief        Prepare, approve, or check an assignment brief.
    record       Prepare one pending submission's draft pair.
    next         Show the next legal grading action.

Exit codes: 0 clean, 10 no profile, 11 the rubric drifted since calibration,
12 a grading action is blocked or requested findings are present, including
failed verification, 13 malformed input, 14 calibration not approved or stale.

Nothing here loads the token map. Quotes and tokens are checked against the
redacted source, which is the file the model actually read.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.density import (  # noqa: E402
    PATTERNS_PATH,
    DensityLimitError,
    load_terms,
)
from scripts.grading.batch import (  # noqa: E402
    BatchPaths,
    GradingBatch,
    Progress,
    VerificationFailure,
    VerificationInputs,
)
from scripts.grading.brief import (  # noqa: E402
    BriefError,
    approve_brief,
    brief_path,
    check_brief,
    load_brief,
    prepare_brief,
)
from scripts.grading.calibration import check_calibration  # noqa: E402
from scripts.grading.density import scan  # noqa: E402
from scripts.grading.drafts import (  # noqa: E402
    DraftPreparationError,
    prepare_draft_pair,
)
from scripts.grading.profile import (  # noqa: E402
    ProfileError,
    load_profile,
    profile_path,
)
from scripts.grading.records import RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402
from scripts.grading.workflow import (  # noqa: E402
    WorkflowContext,
    WorkflowError,
    next_action,
)
from scripts.path_safety import (  # noqa: E402
    is_within,
    read_bytes_limited,
    read_text_limited,
    require_component,
)
from scripts.redaction.manifest import RedactionManifest  # noqa: E402

EXIT_OK = 0
EXIT_NO_PROFILE = 10
EXIT_DRIFT = 11
EXIT_FAILED = 12
EXIT_MALFORMED = 13
EXIT_UNAPPROVED = 14

RECALIBRATE_AFTER_DAYS = 120
RECALIBRATE_AFTER_BRIEFS = 5


def _component(label: str):
    def parse(value: str) -> str:
        return require_component(value, label)

    return parse


class PublicationInspector:
    """Check identity-bearing output hashes without reading a token map."""

    def __init__(self, batch: str, run_id: str) -> None:
        self.batch = batch
        self.run_id = run_id

    def hash_output(self, path: Path) -> str:
        return hashlib.sha256(
            read_bytes_limited(path, "The published feedback", 2 * 1024 * 1024)
        ).hexdigest()


def _drafts_dir(args: argparse.Namespace) -> Path:
    return args.drafts or (args.root / "drafts" / args.batch)


def _profile_or_exit(
    args: argparse.Namespace, *, error_prefix: str | None = None
):
    path = profile_path(args.course, args.root)
    try:
        return load_profile(path)
    except ProfileError as error:
        if error_prefix is not None:
            print(f"{error_prefix} {error}", file=sys.stderr)
            raise SystemExit(EXIT_NO_PROFILE) from error
        print(str(error), file=sys.stderr)
        print(
            f"Run `/grade calibrate --course {args.course}` first. Grading "
            "without a profile would produce feedback in nobody's voice.",
            file=sys.stderr,
        )
        raise SystemExit(EXIT_NO_PROFILE) from error


def _recalibration_warnings(profile, root: Path, profile_sha256: str) -> list[str]:
    """Say when a profile is old or heavily used. Warnings only."""
    warnings: list[str] = []
    try:
        calibrated = datetime.strptime(profile.calibrated, "%Y-%m-%d").date()
    except ValueError:
        calibrated = None
    if calibrated is not None:
        age = (datetime.now(timezone.utc).date() - calibrated).days
        if age > RECALIBRATE_AFTER_DAYS:
            warnings.append(
                f"warning recalibrate: the profile was calibrated {age} days ago. "
                "Add graded work and calibrate again."
            )
    bound = 0
    assignments = root / "assignments"
    if assignments.is_dir():
        for path in sorted(assignments.glob("*.md")):
            try:
                brief = load_brief(path)
            except (BriefError, OSError, UnicodeError, ValueError):
                continue
            if brief.status == "approved" and brief.profile_sha256 == profile_sha256:
                bound += 1
    if bound >= RECALIBRATE_AFTER_BRIEFS:
        warnings.append(
            f"warning recalibrate: {bound} approved assignment briefs use this "
            "profile. Add graded work from them and calibrate again."
        )
    return warnings


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
    status = check_calibration(
        args.root, course=args.course, profile=profile.path, rubric=args.rubric
    )
    if status.state != "current":
        print(f"ERROR calibration {status.state}: {status.message}", file=sys.stderr)
        return EXIT_UNAPPROVED
    print(
        f"Profile for {profile.course} is current, calibrated "
        f"{profile.calibrated} from {profile.samples} samples. Calibration "
        f"approved {status.approval.approved_at}."
    )
    profile_sha256 = hashlib.sha256(
        read_bytes_limited(profile.path, "The calibration profile", 5 * 1024 * 1024)
    ).hexdigest()
    for line in _recalibration_warnings(profile, args.root, profile_sha256):
        print(line)
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
            brief=brief_path(args.root, args.batch),
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
    batch = GradingBatch.open(
        BatchPaths(
            batch=args.batch,
            redacted=args.redacted,
            drafts=_drafts_dir(args),
        )
    )
    inspector = None
    if args.publication is not None:
        try:
            manifest = RedactionManifest.load(args.redacted)
            inspector = PublicationInspector(args.batch, manifest.run_id)
        except (OSError, UnicodeError, ValueError) as error:
            print(f"Cannot inspect publication status: {error}", file=sys.stderr)
            return EXIT_MALFORMED
    report = batch.status(publication=args.publication, renderer=inspector)
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
        f"{counts['verified']} verified, {counts['rehydrated']} rehydrated."
    )
    if counts["orphan"]:
        print(
            "An orphaned artifact has no redacted submission. It is usually "
            "an earlier grading batch's output. Move it out before grading.",
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
    if (
        args.path.is_symlink()
        or not is_within(args.path, args.redacted, follow_symlinks=False)
        or not is_within(args.path, args.redacted, follow_symlinks=True)
        or args.path.resolve().parent != args.redacted.resolve()
    ):
        print(
            "Density accepts only a regular file directly inside the configured "
            "redacted directory.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    try:
        terms = load_terms(args.patterns)
        result = scan(
            read_text_limited(
                args.path, "The redacted submission", 25 * 1024 * 1024
            ),
            terms,
        )
    except DensityLimitError as error:
        print(f"Density stopped safely: {error}", file=sys.stderr)
        return EXIT_MALFORMED
    except (OSError, UnicodeError, ValueError) as error:
        print(f"Cannot read redacted submission: {error}", file=sys.stderr)
        return EXIT_MALFORMED
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


def _brief_file(args: argparse.Namespace) -> Path:
    return args.brief or brief_path(args.root, args.batch)


def _brief_error(error: Exception) -> int:
    print(f"ERROR assignment-brief: {error}", file=sys.stderr)
    return EXIT_MALFORMED


def _brief_profile_and_rubric(args: argparse.Namespace):
    profile = _profile_or_exit(args, error_prefix="ERROR assignment-brief:")
    rubric = load_rubric(args.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        print(
            "ERROR assignment-brief: The rubric changed since calibration: "
            + "; ".join(drifted),
            file=sys.stderr,
        )
        raise SystemExit(EXIT_DRIFT)
    return profile, rubric


def _cmd_brief_prepare(args: argparse.Namespace) -> int:
    try:
        path = _brief_file(args)
        brief = prepare_brief(
            path,
            batch=args.batch,
            course=args.course,
            mode=args.mode,
        )
    except (BriefError, OSError, UnicodeError, ValueError) as error:
        return _brief_error(error)
    print(f"Prepared draft assignment brief: {brief.path}")
    return EXIT_OK


def _cmd_brief_approve(args: argparse.Namespace) -> int:
    try:
        profile, rubric = _brief_profile_and_rubric(args)
        path = _brief_file(args)
        brief = approve_brief(
            path,
            batch=args.batch,
            course=args.course,
            rubric=rubric.path,
            profile=profile.path,
            approval=args.approval,
        )
    except (BriefError, OSError, UnicodeError, ValueError) as error:
        return _brief_error(error)
    print(f"Approved assignment brief: {brief.path}")
    return EXIT_OK


def _cmd_brief_check(args: argparse.Namespace) -> int:
    try:
        profile, rubric = _brief_profile_and_rubric(args)
        path = _brief_file(args)
        brief = check_brief(
            path,
            batch=args.batch,
            course=args.course,
            rubric=rubric.path,
            profile=profile.path,
        )
    except (BriefError, OSError, UnicodeError, ValueError) as error:
        return _brief_error(error)
    print(f"Approved assignment brief is current: {brief.path}")
    return EXIT_OK


def _record_error(message: str, *, exit_code: int = EXIT_MALFORMED) -> int:
    print(f"ERROR record-prepare: {_one_line(message)}", file=sys.stderr)
    return exit_code


def _one_line(message: object) -> str:
    """Collapse runtime error text for one physical CLI line."""
    return " ".join(str(message).split())


def _cmd_record_prepare(args: argparse.Namespace) -> int:
    try:
        profile = load_profile(profile_path(args.course, args.root))
        if profile.course != args.course:
            return _record_error(
                f"The profile belongs to {profile.course!r}, not {args.course!r}."
            )
        rubric = load_rubric(args.rubric)
        drifted = profile.drift(rubric)
        if drifted:
            return _record_error(
                "The rubric changed since calibration: " + "; ".join(drifted),
                exit_code=EXIT_DRIFT,
            )
        try:
            check_brief(
                args.brief or brief_path(args.root, args.batch),
                batch=args.batch,
                course=args.course,
                rubric=rubric.path,
                profile=profile.path,
            )
        except (BriefError, OSError, UnicodeError, ValueError) as error:
            return _record_error(f"The assignment brief is not ready: {error}")
        manifest = RedactionManifest.load(args.redacted)
        if manifest.batch != args.batch:
            return _record_error(
                "The redaction manifest belongs to another grading batch."
            )
        if manifest.exit_code != EXIT_OK:
            outcome = (
                f"exited with code {manifest.exit_code}"
                if manifest.exit_code is not None
                else "has no recorded clean exit"
            )
            return _record_error(
                f"Redaction {outcome}. Run redaction again after fixing its "
                "reported inputs.",
                exit_code=EXIT_FAILED,
            )
        manifest.validate(args.redacted)
        if not manifest.reviewed:
            return _record_error(
                "The instructor must review the redaction result before preparing records.",
                exit_code=EXIT_FAILED,
            )
        if (
            args.source.is_symlink()
            or not is_within(args.source, args.redacted, follow_symlinks=False)
            or not is_within(args.source, args.redacted, follow_symlinks=True)
            or args.source.resolve().parent != args.redacted.resolve()
        ):
            return _record_error(
                "The selected source must be a regular file directly inside the "
                "configured redacted directory."
            )

        drafts = _drafts_dir(args)
        report = GradingBatch.open(
            BatchPaths(batch=args.batch, redacted=args.redacted, drafts=drafts)
        ).status()
        if report.blocking_findings:
            finding = report.blocking_findings[0]
            return _record_error(
                f"The grading batch is {finding.code}: {finding.message}",
                exit_code=EXIT_FAILED,
            )
        selected = [
            entry
            for entry in report.entries
            if entry.source is not None
            and entry.source.resolve() == args.source.resolve()
        ]
        if len(selected) != 1:
            return _record_error(
                "The selected source is not exactly one manifest-bound pending entry.",
                exit_code=EXIT_FAILED,
            )
        entry = selected[0]
        if entry.progress is not Progress.PENDING:
            return _record_error(
                f"{entry.token} is not pending and cannot be prepared again.",
                exit_code=EXIT_FAILED,
            )
        pair = prepare_draft_pair(
            batch=args.batch,
            course=args.course,
            source=args.source,
            rubric=rubric,
            profile=profile.path,
            patterns=args.patterns,
            drafts=drafts,
        )
    except (
        BriefError,
        DraftPreparationError,
        ProfileError,
        RubricError,
        OSError,
        UnicodeError,
        ValueError,
    ) as error:
        return _record_error(str(error))
    print(f"Prepared grade record: {pair.record}")
    print(f"Prepared feedback draft: {pair.feedback}")
    print(
        "Fill every criterion, replace every feedback marker, then run "
        "`python3 scripts/grade.py next`."
    )
    return EXIT_OK


def _cmd_next(args: argparse.Namespace) -> int:
    try:
        action = next_action(
            WorkflowContext(
                batch=args.batch,
                course=args.course,
                rubric=args.rubric,
                redacted=args.redacted,
                drafts=_drafts_dir(args),
                root=args.root,
                patterns=args.patterns,
                publication=args.publication or Path("final") / args.batch,
                submissions=args.submissions,
                roster=args.roster,
                instructor=tuple(args.instructor),
            )
        )
    except (
        WorkflowError,
        BriefError,
        ProfileError,
        RubricError,
        OSError,
        UnicodeError,
        TypeError,
        KeyError,
        ValueError,
    ) as error:
        print(f"ERROR next: {_one_line(error)}", file=sys.stderr)
        return EXIT_MALFORMED
    if args.json:
        print(json.dumps(action.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"Action: {action.action}")
        print(action.message)
        if action.token:
            print(f"Token: {action.token}")
        for command in action.commands:
            print(f"Command: {shlex.join(command)}")
        for finding in action.findings:
            where = f" [{finding.token}]" if finding.token else ""
            print(
                f"Finding: {finding.code}{where}: {_one_line(finding.message)}"
            )
    if action.action in {
        "repair-brief",
        "repair-redaction",
        "repair-batch",
        "repair-verification",
    }:
        return EXIT_FAILED
    return EXIT_OK

def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(".grading"),
        help=(
            "Grading state root (default: .grading); this is not the "
            "repository root"
        ),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    check = subparsers.add_parser("profile", help="Check the profile and rubric drift")
    check.add_argument("--course", required=True, type=_component("The course"))
    check.add_argument("--rubric", type=Path, required=True)
    _add_common(check)
    check.set_defaults(handler=_cmd_profile)

    verify = subparsers.add_parser("verify", help="Check every draft in a batch")
    verify.add_argument("--batch", required=True, type=_component("The grading batch"))
    verify.add_argument("--course", required=True, type=_component("The course"))
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
    status.add_argument("--batch", required=True, type=_component("The grading batch"))
    status.add_argument("--redacted", type=Path, default=Path("redacted"))
    status.add_argument("--drafts", type=Path, default=None)
    status.add_argument(
        "--publication",
        type=Path,
        default=None,
        help="Check a published draft set and report rehydrated progress",
    )
    _add_common(status)
    status.set_defaults(handler=_cmd_status)

    density = subparsers.add_parser("density", help="Count watched AI terms in a file")
    density.add_argument("path", type=Path)
    density.add_argument("--redacted", type=Path, default=Path("redacted"))
    density.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    density.add_argument("--json", action="store_true")
    _add_common(density)
    density.set_defaults(handler=_cmd_density)
    consistency = subparsers.add_parser(
        "consistency", help="Flag outliers across a graded batch"
    )
    consistency.add_argument("--batch", required=True, type=_component("The grading batch"))
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

    brief = subparsers.add_parser("brief", help="Prepare or check an assignment brief")
    brief_actions = brief.add_subparsers(dest="brief_action", required=True)

    prepare = brief_actions.add_parser(
        "prepare", help="Prepare a draft assignment brief"
    )
    prepare.add_argument("--batch", required=True, type=_component("The grading batch"))
    prepare.add_argument("--course", required=True, type=_component("The course"))
    prepare.add_argument("--mode", required=True, choices=("local", "cloud"))
    prepare.add_argument("--brief", type=Path, default=None)
    _add_common(prepare)
    prepare.set_defaults(handler=_cmd_brief_prepare)

    approve = brief_actions.add_parser(
        "approve", help="Approve an answered assignment brief"
    )
    approve.add_argument("--batch", required=True, type=_component("The grading batch"))
    approve.add_argument("--course", required=True, type=_component("The course"))
    approve.add_argument("--rubric", required=True, type=Path)
    approve.add_argument("--approval", required=True)
    approve.add_argument("--brief", type=Path, default=None)
    _add_common(approve)
    approve.set_defaults(handler=_cmd_brief_approve)

    brief_check = brief_actions.add_parser(
        "check", help="Check an approved assignment brief"
    )
    brief_check.add_argument(
        "--batch", required=True, type=_component("The grading batch")
    )
    brief_check.add_argument("--course", required=True, type=_component("The course"))
    brief_check.add_argument("--rubric", required=True, type=Path)
    brief_check.add_argument("--brief", type=Path, default=None)
    _add_common(brief_check)
    brief_check.set_defaults(handler=_cmd_brief_check)

    record = subparsers.add_parser(
        "record", help="Prepare one pending submission's draft pair."
    )
    record_actions = record.add_subparsers(dest="record_action", required=True)
    record_prepare = record_actions.add_parser(
        "prepare", help="Prepare one pending submission's draft pair"
    )
    record_prepare.add_argument(
        "--batch", required=True, type=_component("The grading batch")
    )
    record_prepare.add_argument(
        "--course", required=True, type=_component("The course")
    )
    record_prepare.add_argument("--rubric", required=True, type=Path)
    record_prepare.add_argument("--source", required=True, type=Path)
    record_prepare.add_argument("--redacted", type=Path, default=Path("redacted"))
    record_prepare.add_argument("--drafts", type=Path, default=None)
    record_prepare.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    record_prepare.add_argument("--brief", type=Path, default=None)
    _add_common(record_prepare)
    record_prepare.set_defaults(handler=_cmd_record_prepare)

    next_command = subparsers.add_parser(
        "next", help="Show the next legal grading action."
    )
    next_command.add_argument(
        "--batch", required=True, type=_component("The grading batch")
    )
    next_command.add_argument(
        "--course", required=True, type=_component("The course")
    )
    next_command.add_argument("--rubric", required=True, type=Path)
    next_command.add_argument("--redacted", type=Path, default=Path("redacted"))
    next_command.add_argument("--drafts", type=Path, default=None)
    next_command.add_argument("--patterns", type=Path, default=PATTERNS_PATH)
    next_command.add_argument("--publication", type=Path, default=None)
    next_command.add_argument("--submissions", type=Path, default=None)
    next_command.add_argument("--roster", type=Path, default=None)
    next_command.add_argument("--instructor", action="append", default=[])
    next_command.add_argument("--json", action="store_true")
    _add_common(next_command)
    next_command.set_defaults(handler=_cmd_next)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except SystemExit as exit_request:
        return int(exit_request.code or 0)
    except (
        RubricError,
        RecordError,
        ProfileError,
        FileNotFoundError,
        OSError,
        UnicodeError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED


if __name__ == "__main__":
    raise SystemExit(main())
