#!/usr/bin/env python3
"""Run or audit one synthetic local grading-agent conformance trial."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.conformance import (  # noqa: E402
    ConformanceError,
    audit_workspace,
    parse_command_json,
    report_summary,
    run_trial,
)

EXIT_OK = 0
EXIT_FAILED = 12
EXIT_MALFORMED = 13


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ConformanceError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)

    run = actions.add_parser("run", help="Create and drive a synthetic trial")
    run.add_argument("--runtime", required=True)
    run.add_argument("--command-json", required=True)
    run.add_argument("--timeout", required=True, type=float)
    run.add_argument("--workspace", type=Path, default=None)

    audit = actions.add_parser("audit", help="Audit an existing synthetic trial")
    audit.add_argument("--runtime", required=True)
    audit.add_argument("--workspace", required=True, type=Path)
    audit.add_argument("--trace", action="append", type=Path, default=[])
    audit.add_argument(
        "--package-root",
        type=Path,
        default=Path(__file__).resolve().parent.parent,
        help="Trusted package root used as the immutable audit baseline",
    )
    return parser


def _print_result(report) -> None:
    print(
        f"{report_summary(report)}  JSON: {report.json_path}  "
        f"Markdown: {report.markdown_path}"
    )


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if args.action == "run":
            report = run_trial(
                Path(__file__).resolve().parent.parent,
                runtime=args.runtime,
                command=parse_command_json(args.command_json),
                timeout_seconds=args.timeout,
                workspace=args.workspace,
            )
        else:
            report = audit_workspace(
                args.workspace,
                runtime=args.runtime,
                trace_paths=tuple(args.trace),
                package_root=args.package_root,
            )
    except (ConformanceError, OSError, UnicodeError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED
    _print_result(report)
    return EXIT_OK if report.passed else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
