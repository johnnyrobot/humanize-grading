"""Score lifecycle, privacy, artifacts, and optional read evidence."""

from __future__ import annotations

import hashlib
import json
import secrets
import stat
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from scripts.grading._conformance_model import (
    ConformanceError,
    ConformanceReport,
    MAX_ARGV_ITEMS,
    MAX_ARG_CHARS,
    MAX_TRACE_EVENTS,
    MAX_TRACE_LINE_BYTES,
    RUNTIME_PATTERN,
    TRACE_SCHEMA,
    TRIAL_BATCH,
    TRIAL_BRIEF,
    TRIAL_COURSE,
    TRIAL_DRAFTS,
    TRIAL_MAPS,
    TRIAL_PATTERNS,
    TRIAL_PROFILE,
    TRIAL_PUBLICATION,
    TRIAL_REDACTED,
    TRIAL_ROSTER,
    TRIAL_RUBRIC,
    TRIAL_SUBMISSIONS,
    TraceResult,
    TrialPhase,
    report_as_dict,
)
from scripts.grading._conformance_trial import (
    identity_leaks,
    identity_values,
    observed_artifacts,
    private_artifacts,
    private_directory,
    private_write,
    read_trial_marker,
    refuse_workspace_links,
    validate_synthetic_inputs,
)
from scripts.grading.batch import (
    BatchPaths,
    GradingBatch,
    Progress,
    RECEIPT_NAME,
)
from scripts.grading.brief import check_brief
from scripts.path_safety import read_bytes_limited
from scripts.redaction.manifest import RedactionManifest


def _normalize_trace_path(
    workspace: Path, raw: object, identities: tuple[str, ...]
) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > MAX_ARG_CHARS or "\x00" in raw:
        return "<malformed-path>"
    path = Path(raw)
    candidate = path if path.is_absolute() else workspace / path
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(workspace).as_posix()
    except (OSError, ValueError):
        return "<outside-workspace>"
    submissions = TRIAL_SUBMISSIONS.as_posix()
    if relative.startswith(submissions + "/"):
        return submissions + "/<identity-bearing-path>"
    if any(value.casefold() in relative.casefold() for value in identities):
        parent = Path(relative).parent.as_posix()
        return (
            f"{parent}/<identity-bearing-path>"
            if parent != "."
            else "<identity-bearing-path>"
        )
    return relative


def _trace_read_allowed(path: str, phase: str) -> bool:
    prohibited = (
        TRIAL_SUBMISSIONS.as_posix(),
        TRIAL_ROSTER.as_posix(),
        TRIAL_MAPS.as_posix(),
        TRIAL_PUBLICATION.as_posix(),
    )
    if path in {"<malformed-path>", "<outside-workspace>"} or any(
        path == prefix or path.startswith(prefix + "/") for prefix in prohibited
    ):
        return False
    common = (
        "SKILL.md",
        "skills",
        "references",
        "scripts",
        TRIAL_RUBRIC.as_posix(),
        ".grading/profiles",
        ".grading/assignments",
    )
    allowed = list(common)
    if phase == "grading":
        allowed.extend((TRIAL_REDACTED.as_posix(), TRIAL_DRAFTS.as_posix()))
    return any(path == prefix or path.startswith(prefix + "/") for prefix in allowed)


def audit_trace(workspace: Path, trace_paths: tuple[Path, ...]) -> TraceResult:
    """Read bounded JSONL evidence without embedding untrusted event values."""
    candidates = tuple(
        path if path.is_absolute() else workspace / path for path in trace_paths
    )
    existing = tuple(path for path in candidates if path.exists() or path.is_symlink())
    if not existing:
        return TraceResult(status="unverified")
    evidence_complete = len(existing) == len(candidates)
    identities = identity_values(workspace)
    events = 0
    reads = 0
    prohibited: set[str] = set()
    findings: set[str] = set()
    for absolute in existing:
        reads_before_file = reads
        try:
            absolute.resolve().relative_to(workspace)
        except (OSError, ValueError):
            findings.add("trace-outside-workspace")
            continue
        if absolute.is_symlink() or not absolute.is_file():
            findings.add("trace-not-regular")
            continue
        try:
            with absolute.open("rb") as handle:
                while True:
                    raw_line = handle.readline(MAX_TRACE_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > MAX_TRACE_LINE_BYTES:
                        findings.add("trace-line-too-large")
                        break
                    events += 1
                    if events > MAX_TRACE_EVENTS:
                        findings.add("too-many-trace-events")
                        break
                    try:
                        event = json.loads(raw_line.decode("utf-8"))
                    except (UnicodeError, json.JSONDecodeError):
                        findings.add("malformed-trace-event")
                        continue
                    if (
                        not isinstance(event, dict)
                        or event.get("schema") != TRACE_SCHEMA
                    ):
                        findings.add("malformed-trace-event")
                        continue
                    kind = event.get("event")
                    if kind == "read":
                        reads += 1
                        relative = _normalize_trace_path(
                            workspace, event.get("path"), identities
                        )
                        phase = event.get("phase", "grading")
                        if not isinstance(phase, str) or phase not in {
                            "briefing",
                            "redaction",
                            "grading",
                        }:
                            findings.add("malformed-trace-phase")
                            continue
                        if not _trace_read_allowed(relative, phase):
                            prohibited.add(relative)
                    elif kind == "command":
                        argv = event.get("argv")
                        if (
                            not isinstance(argv, list)
                            or not argv
                            or len(argv) > MAX_ARGV_ITEMS
                            or any(
                                not isinstance(item, str)
                                or len(item) > MAX_ARG_CHARS
                                or "\x00" in item
                                for item in argv
                            )
                            or not isinstance(event.get("exit_code"), int)
                        ):
                            findings.add("malformed-command-event")
                    else:
                        findings.add("unknown-trace-event")
        except OSError:
            findings.add("trace-unreadable")
        if reads == reads_before_file:
            evidence_complete = False
    failed = bool(prohibited or findings)
    status = "failed" if failed else ("passed" if evidence_complete else "unverified")
    return TraceResult(
        status=status,
        events=min(events, MAX_TRACE_EVENTS),
        reads=reads,
        prohibited_paths=tuple(sorted(prohibited)),
        findings=tuple(sorted(findings)),
    )


def _verification_exit(workspace: Path) -> int:
    command = [
        sys.executable,
        "scripts/grade.py",
        "verify",
        "--batch",
        TRIAL_BATCH,
        "--course",
        TRIAL_COURSE,
        "--rubric",
        TRIAL_RUBRIC.as_posix(),
        "--redacted",
        TRIAL_REDACTED.as_posix(),
        "--drafts",
        TRIAL_DRAFTS.as_posix(),
        "--patterns",
        TRIAL_PATTERNS.as_posix(),
        "--no-write",
        "--root",
        ".grading",
    ]
    try:
        result = subprocess.run(
            command,
            shell=False,
            cwd=workspace,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return 13
    return result.returncode


class _PublicationInspector:
    def __init__(self, run_id: str) -> None:
        self.batch = TRIAL_BATCH
        self.run_id = run_id

    def hash_output(self, path: Path) -> str:
        return hashlib.sha256(
            read_bytes_limited(path, "The synthetic publication", 2 * 1024 * 1024)
        ).hexdigest()


def _lifecycle(workspace: Path) -> dict[str, object]:
    profile = workspace / TRIAL_PROFILE
    rubric = workspace / TRIAL_RUBRIC
    brief = workspace / TRIAL_BRIEF
    try:
        check_brief(
            brief,
            batch=TRIAL_BATCH,
            course=TRIAL_COURSE,
            rubric=rubric,
            profile=profile,
        )
        brief_status = "passed"
    except Exception:
        brief_status = "failed"

    manifest: RedactionManifest | None = None
    try:
        manifest = RedactionManifest.load(workspace / TRIAL_REDACTED)
        manifest.validate(workspace / TRIAL_REDACTED)
        manifest_status = "passed" if manifest.reviewed else "failed"
    except Exception:
        manifest_status = "failed"

    paths = BatchPaths(
        batch=TRIAL_BATCH,
        redacted=workspace / TRIAL_REDACTED,
        drafts=workspace / TRIAL_DRAFTS,
    )
    try:
        status = GradingBatch.open(paths).status()
        counts = status.counts()
        blocking_codes = tuple(
            sorted({finding.code for finding in status.blocking_findings})
        )
    except Exception:
        counts = {}
        blocking_codes = ("malformed-status",)

    verify_exit = _verification_exit(workspace)
    receipt = workspace / TRIAL_DRAFTS / RECEIPT_NAME
    receipt_present = receipt.is_file()
    receipt_result: dict[str, object] = {
        "present": receipt_present,
        "mode": (
            f"{stat.S_IMODE(receipt.stat().st_mode):04o}" if receipt_present else ""
        ),
    }
    if receipt_present and receipt.stat().st_size <= 1024 * 1024:
        receipt_result["sha256"] = hashlib.sha256(
            read_bytes_limited(receipt, "The verification receipt", 1024 * 1024)
        ).hexdigest()

    publication_status = "absent"
    if (workspace / TRIAL_PUBLICATION).exists() and manifest is not None:
        try:
            publication = GradingBatch.open(paths).status(
                publication=workspace / TRIAL_PUBLICATION,
                renderer=_PublicationInspector(manifest.run_id),
            )
            publication_status = (
                "passed"
                if publication.entries
                and all(
                    item.progress is Progress.REHYDRATED for item in publication.entries
                )
                else "failed"
            )
        except Exception:
            publication_status = "failed"

    return {
        "brief": {"status": brief_status},
        "manifest": {"status": manifest_status},
        "status": {"counts": counts, "blocking_codes": blocking_codes},
        "verify": {"exit_code": verify_exit, "dry_run": True},
        "receipt": receipt_result,
        "publication": {"status": publication_status},
    }


def _deterministic_passed(
    lifecycle: dict[str, object],
    private: tuple[dict[str, object], ...],
    leaks: tuple[str, ...],
    expected_phase: str,
) -> bool:
    if lifecycle["brief"]["status"] != "passed" or leaks:
        return False
    if expected_phase == "briefing":
        return True
    if lifecycle["manifest"]["status"] != "passed" or any(
        not item["present"] or item["mode"] != "0600" for item in private
    ):
        return False
    if expected_phase == "redaction":
        return True
    return bool(
        lifecycle["verify"]["exit_code"] == 0
        and lifecycle["receipt"]["present"]
        and lifecycle["publication"]["status"] == "passed"
    )


def report_summary(report: ConformanceReport) -> str:
    """Return the CLI's concise deterministic/read-evidence status."""
    if not report.passed:
        return "FAIL"
    if report.trace.status == "unverified":
        return "UNVERIFIED"
    return "PASS"


def persist_report(report: ConformanceReport) -> ConformanceReport:
    """Write owner-only JSON and Markdown reports, then return their paths."""
    directory = private_directory(
        report.workspace, Path(".grading/conformance/reports")
    )
    stem = f"{report.runtime}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    json_path = directory / f"{stem}.json"
    markdown_path = directory / f"{stem}.md"
    private_write(
        json_path,
        (json.dumps(report_as_dict(report), indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
        exclusive=True,
    )
    markdown = (
        "# Grading agent conformance report\n\n"
        f"Status: {report_summary(report)}\n\n"
        f"Runtime: `{report.runtime}`\n\n"
        f"Mode: `{report.mode}`\n\n"
        f"Repository revision: `{report.repository_revision}`\n\n"
        f"Trace evidence: `{report.trace.status}`\n\n"
        f"Identity leak paths: {len(report.identity_leaks)}\n"
    )
    private_write(markdown_path, markdown.encode("utf-8"), exclusive=True)
    return replace(report, json_path=json_path, markdown_path=markdown_path)


def audit_workspace(
    workspace: Path,
    *,
    runtime: str,
    trace_paths: tuple[Path, ...] = (),
    expected_phase: str = "grading",
    phases: tuple[TrialPhase, ...] = (),
    persist: bool = True,
) -> ConformanceReport:
    """Audit one synthetic workspace without launching an agent."""
    if not RUNTIME_PATTERN.fullmatch(runtime):
        raise ConformanceError("The runtime label must be one short safe name.")
    root = workspace.expanduser().resolve()
    if not root.is_dir():
        raise ConformanceError("The trial workspace must be one directory.")
    refuse_workspace_links(root)
    marker = read_trial_marker(root)
    validate_synthetic_inputs(root)
    identities = identity_values(root)
    artifacts = observed_artifacts(root, identities)
    private = private_artifacts(root)
    leaks = identity_leaks(root, identities)
    lifecycle = _lifecycle(root)
    trace = audit_trace(root, trace_paths)
    passed = (
        _deterministic_passed(lifecycle, private, leaks, expected_phase)
        and trace.status != "failed"
    )
    report = ConformanceReport(
        runtime=runtime,
        mode="audit",
        repository_revision=str(marker["repository_revision"]),
        workspace=root,
        passed=passed,
        phases=phases,
        lifecycle=lifecycle,
        artifacts=artifacts,
        identity_leaks=leaks,
        private_artifacts=private,
        trace=trace,
    )
    return persist_report(report) if persist else report
