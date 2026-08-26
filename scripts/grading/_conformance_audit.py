"""Score lifecycle, privacy, artifacts, and optional read evidence."""

from __future__ import annotations

import hashlib
import json
import os
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
from scripts.grading._conformance_process import (
    popen_group_options,
    terminate_process_group,
)
from scripts.grading._conformance_trial import (
    attest_package,
    identity_scan,
    identity_values,
    observed_artifacts,
    private_artifacts,
    private_directory,
    private_write,
    read_trial_marker,
    refuse_workspace_links,
    trusted_package_attestation,
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


DEFAULT_PACKAGE_ROOT = Path(__file__).resolve().parents[2]


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


def _default_trace_phase(path: Path) -> str:
    for phase in ("briefing", "redaction", "grading"):
        if phase in path.name:
            return phase
    return "grading"


def _is_successful_next(argv: list[str], exit_code: int) -> bool:
    if exit_code != 0 or "--json" not in argv:
        return False
    return any(
        item.endswith("scripts/grade.py") and argv[index + 1] == "next"
        for index, item in enumerate(argv[:-1])
    )


def audit_trace(
    workspace: Path,
    trace_paths: tuple[Path, ...],
    *,
    required_phases: tuple[str, ...] = ("briefing", "redaction", "grading"),
) -> TraceResult:
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
    phase_commands: dict[str, list[tuple[bool, int]]] = {
        phase: [] for phase in ("briefing", "redaction", "grading")
    }
    phase_work: dict[str, list[int]] = {
        phase: [] for phase in ("briefing", "redaction", "grading")
    }
    stop_all = False
    for file_index, absolute in enumerate(existing):
        if events >= MAX_TRACE_EVENTS:
            for remaining in existing[file_index:]:
                try:
                    state = remaining.lstat()
                except OSError:
                    findings.add("trace-unreadable")
                    continue
                if stat.S_ISREG(state.st_mode) and state.st_size:
                    findings.add("too-many-trace-events")
                    break
                if not stat.S_ISREG(state.st_mode):
                    findings.add("trace-not-regular")
            break
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
                    if events >= MAX_TRACE_EVENTS:
                        if handle.tell() < os.fstat(handle.fileno()).st_size:
                            findings.add("too-many-trace-events")
                            stop_all = True
                        break
                    raw_line = handle.readline(MAX_TRACE_LINE_BYTES + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > MAX_TRACE_LINE_BYTES:
                        findings.add("trace-line-too-large")
                        break
                    events += 1
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
                    event_phase = event.get("phase", _default_trace_phase(absolute))
                    if not isinstance(event_phase, str) or event_phase not in {
                        "briefing",
                        "redaction",
                        "grading",
                    }:
                        findings.add("malformed-trace-phase")
                        continue
                    if kind == "read":
                        reads += 1
                        relative = _normalize_trace_path(
                            workspace, event.get("path"), identities
                        )
                        if not _trace_read_allowed(relative, event_phase):
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
                            is_next = _is_successful_next(argv, int(event["exit_code"]))
                            phase_commands[event_phase].append((is_next, events))
                            if not is_next and int(event["exit_code"]) == 0:
                                phase_work[event_phase].append(events)
                    else:
                        findings.add("unknown-trace-event")
        except OSError:
            findings.add("trace-unreadable")
        if reads == reads_before_file:
            evidence_complete = False
        if stop_all:
            break
    for phase in required_phases:
        commands = phase_commands[phase]
        successful_next = [index for is_next, index in commands if is_next]
        work = phase_work[phase]
        if (
            len(successful_next) < 2
            or not work
            or commands[0][0] is not True
            or commands[-1][0] is not True
            or not successful_next[0] < min(work)
            or not max(work) < successful_next[-1]
        ):
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
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        process = subprocess.Popen(
            command,
            shell=False,
            cwd=workspace,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            **popen_group_options(),
        )
    except OSError:
        return 13
    try:
        process.wait(timeout=30)
        return_code = int(process.returncode or 0)
    except subprocess.TimeoutExpired:
        return_code = 13
    finally:
        terminate_process_group(process)
    return return_code


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
    artifact_findings: tuple[dict[str, str], ...],
    package: dict[str, object],
    expected_phase: str,
) -> bool:
    if (
        lifecycle["brief"]["status"] != "passed"
        or leaks
        or artifact_findings
        or package["status"] != "passed"
        or lifecycle["phase"]["status"] != "passed"
    ):
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


def _phase_findings(
    artifacts: tuple[dict[str, object], ...],
    private: tuple[dict[str, object], ...],
    expected_phase: str,
) -> tuple[str, ...]:
    if expected_phase not in {"briefing", "redaction", "grading"}:
        raise ConformanceError("The expected conformance phase is unknown.")
    later_roots = {
        "briefing": ("redacted", "drafts/essay-2", "final"),
        "redaction": ("drafts/essay-2", "final"),
        "grading": (),
    }[expected_phase]
    later = [
        item
        for item in artifacts
        if any(
            str(item["path"]) == root or str(item["path"]).startswith(root + "/")
            for root in later_roots
        )
    ]
    findings = {
        f"later-stage-artifact:{item['path']}"
        for item in later
        if not (
            item.get("kind") == "directory"
            and any(
                str(other["path"]).startswith(str(item["path"]) + "/")
                for other in later
            )
        )
    }
    if expected_phase == "briefing":
        findings.update(
            f"later-stage-artifact:{item['path']}"
            for item in private
            if item["present"]
        )
    return tuple(sorted(findings))


def _failed_lifecycle(expected_phase: str, phase_findings: tuple[str, ...]) -> dict:
    return {
        "brief": {"status": "unverified"},
        "manifest": {"status": "unverified"},
        "status": {"counts": {}, "blocking_codes": ("package-changed",)},
        "verify": {"exit_code": 13, "dry_run": True},
        "receipt": {"present": False, "mode": ""},
        "publication": {"status": "unverified"},
        "phase": {
            "expected": expected_phase,
            "status": "failed" if phase_findings else "unverified",
            "findings": phase_findings,
        },
    }


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
        f"Package: `{report.package.get('status', 'unverified')}` "
        f"(`{report.package.get('digest', '')}`)\n\n"
        f"Trace evidence: `{report.trace.status}`\n\n"
        f"Identity leak paths: {len(report.identity_leaks)}\n"
        f"Artifact findings: {len(report.artifact_findings)}\n"
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
    expected_package: dict[str, object] | None = None,
    package_root: Path | None = None,
) -> ConformanceReport:
    """Audit one synthetic workspace without launching an agent."""
    if not RUNTIME_PATTERN.fullmatch(runtime):
        raise ConformanceError("The runtime label must be one short safe name.")
    candidate = workspace.expanduser()
    if candidate.is_symlink():
        raise ConformanceError("The trial workspace itself must not be a link.")
    root = candidate.resolve()
    if not root.is_dir():
        raise ConformanceError("The trial workspace must be one directory.")
    marker = read_trial_marker(root)
    if expected_package is not None:
        package_baseline = expected_package
        trusted_root = "run-baseline"
    else:
        package_baseline = trusted_package_attestation(
            package_root if package_root is not None else DEFAULT_PACKAGE_ROOT
        )
        trusted_root = str(package_baseline["trusted_root"])
    package_paths = {
        str(item["path"])
        for item in package_baseline.get("files", ())
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    refuse_workspace_links(
        root,
        allow_identity_artifacts=True,
        allowed_package_paths=package_paths,
    )
    validate_synthetic_inputs(root)
    identities = identity_values(root)
    artifacts = observed_artifacts(root, identities)
    private = private_artifacts(root)
    leaks, artifact_findings = identity_scan(root, identities)
    bounded_findings = {
        str(item["path"]): "artifact-unbounded"
        for item in artifacts
        if (
            str(item["path"]).startswith("redacted/")
            or str(item["path"]).startswith("drafts/")
        )
        and item.get("bounded") is False
    }
    for finding in artifact_findings:
        bounded_findings[str(finding["path"])] = str(finding["code"])
    artifact_findings = tuple(
        {"path": path, "code": code} for path, code in sorted(bounded_findings.items())
    )
    unbounded_paths = {str(item["path"]) for item in artifact_findings}
    artifacts = tuple(
        {**item, "bounded": False} if str(item["path"]) in unbounded_paths else item
        for item in artifacts
    )
    phase_findings = _phase_findings(artifacts, private, expected_phase)
    package = attest_package(
        root,
        package_baseline,
    )
    package["trusted_root"] = trusted_root
    package["marker_digest"] = str(marker["package"].get("digest", ""))
    package["marker_matches"] = package["marker_digest"] == package_baseline.get(
        "digest"
    )
    if package["status"] == "passed":
        lifecycle = _lifecycle(root)
        lifecycle["phase"] = {
            "expected": expected_phase,
            "status": "failed" if phase_findings else "passed",
            "findings": phase_findings,
        }
    else:
        lifecycle = _failed_lifecycle(expected_phase, phase_findings)
    required = {
        "briefing": ("briefing",),
        "redaction": ("briefing", "redaction"),
        "grading": ("briefing", "redaction", "grading"),
    }[expected_phase]
    trace = audit_trace(root, trace_paths, required_phases=required)
    passed = (
        _deterministic_passed(
            lifecycle,
            private,
            leaks,
            artifact_findings,
            package,
            expected_phase,
        )
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
        artifact_findings=artifact_findings,
        identity_leaks=leaks,
        private_artifacts=private,
        package=package,
        trace=trace,
    )
    return persist_report(report) if persist else report
