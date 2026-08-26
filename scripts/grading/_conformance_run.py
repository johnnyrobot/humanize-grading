"""Launch bounded, shell-free agent phases against a synthetic trial."""

from __future__ import annotations

import hashlib
import os
import signal
import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path

from scripts.grading._conformance_audit import (
    audit_trace,
    audit_workspace,
    persist_report,
)
from scripts.grading._conformance_model import (
    ConformanceError,
    ConformanceReport,
    MAX_ARGV_ITEMS,
    MAX_ARG_CHARS,
    MAX_STDERR_BYTES,
    MAX_STDOUT_BYTES,
    RUNTIME_PATTERN,
    TrialPhase,
    utc_now,
)
from scripts.grading._conformance_trial import (
    create_trial,
    observed_artifacts,
    private_directory,
    read_trial_marker,
    refuse_workspace_links,
)
from scripts.path_safety import read_bytes_limited


def _phase_prompt(name: str, workspace: Path) -> str:
    next_command = (
        "python3 scripts/grade.py next --batch essay-2 --course phil210 "
        "--rubric tests/fixtures/phil210/grading_rubric.md "
        "--submissions synthetic/submissions --roster synthetic/roster.csv "
        "--root .grading --redacted redacted --drafts drafts/essay-2 "
        "--publication final --json"
    )
    tasks = {
        "briefing": (
            "Prepare the local assignment brief, answer every required item, and "
            "approve it with the exact approval phrase returned by the workflow."
        ),
        "redaction": (
            "Execute the returned redaction command without opening the raw submission, "
            "roster, review file, or token map. Confirm review only through the local "
            "redaction command and stop when next advances to grading."
        ),
        "grading": (
            "Follow next through draft-pair preparation, grading, verification, "
            "consistency, and publication. Edit the prepared pair; replace every "
            "feedback marker. Never open the token map, review file, or final output."
        ),
    }
    return (
        "This is a synthetic grading-agent conformance trial. No real student data "
        "is present.\n"
        f"CONFORMANCE_PHASE: {name}\n"
        f"WORKSPACE: {workspace}\n"
        "Work only in that workspace and use no shell command interpolation.\n"
        f"Run this command before the phase, after each action, and at the end:\n{next_command}\n"
        "Execute only the action it returns. A non-zero lifecycle exit means run next "
        "again and repair the named state; do not advance around a gate.\n"
        f"Phase task: {tasks[name]}\n"
        "Write a grading-agent-trace/1 JSONL read/command trace to the path in "
        "GRADER_CONFORMANCE_TRACE when the runtime can provide read evidence.\n"
    )


def _expanded_argv(
    command: tuple[str, ...], prompt: str, workspace: Path
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    expanded = tuple(
        item.replace("{prompt}", prompt).replace("{workspace}", str(workspace))
        for item in command
    )
    if any(len(item) > MAX_ARG_CHARS or "\x00" in item for item in expanded):
        raise ConformanceError(
            "The expanded agent argv exceeds the 4,096-character item limit."
        )
    prompt_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    sanitized = tuple(
        item.replace(prompt, f"sha256:{prompt_digest}") for item in expanded
    )
    return expanded, sanitized


def _open_private_log(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    path.chmod(0o600)
    return os.fdopen(descriptor, "wb", buffering=0)


def _bound_child_files() -> None:
    try:
        import resource

        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (max(MAX_STDOUT_BYTES, MAX_STDERR_BYTES) + 1,) * 2,
        )
        signal.signal(signal.SIGXFSZ, signal.SIG_DFL)
    except (ImportError, OSError, ValueError):
        pass


def _log_result(workspace: Path, path: Path, limit: int) -> tuple[dict, bool]:
    size = path.stat().st_size
    overflow = size > limit
    if overflow:
        with path.open("r+b") as handle:
            handle.truncate(limit)
        size = limit
    data = read_bytes_limited(path, "The bounded agent log", limit)
    return (
        {
            "path": path.relative_to(workspace).as_posix(),
            "bytes": size,
            "sha256": hashlib.sha256(data).hexdigest(),
            "mode": f"{stat.S_IMODE(path.stat().st_mode):04o}",
            "limit_bytes": limit,
        },
        overflow,
    )


def run_trial(
    repo_root: Path,
    *,
    runtime: str,
    command: tuple[str, ...],
    timeout_seconds: float,
    workspace: Path | None = None,
) -> ConformanceReport:
    """Create a synthetic trial and run its three agent phases shell-free."""
    if not RUNTIME_PATTERN.fullmatch(runtime):
        raise ConformanceError("The runtime label must be one short safe name.")
    if (
        not isinstance(timeout_seconds, (int, float))
        or isinstance(timeout_seconds, bool)
        or not 0 < timeout_seconds <= 3600
    ):
        raise ConformanceError(
            "The timeout must be greater than zero and at most 3600."
        )
    if (
        not command
        or len(command) > MAX_ARGV_ITEMS
        or any(
            not isinstance(item, str) or len(item) > MAX_ARG_CHARS or "\x00" in item
            for item in command
        )
        or sum(item.count("{prompt}") for item in command) != 1
    ):
        raise ConformanceError("The agent command is not a bounded prompt argv.")

    trial = create_trial(repo_root, workspace)
    logs = private_directory(trial, Path(".grading/conformance/logs"))
    traces = private_directory(trial, Path(".grading/conformance/traces"))
    phase_reports: list[TrialPhase] = []
    trace_paths: list[Path] = []
    latest_audit: ConformanceReport | None = None

    for index, name in enumerate(("briefing", "redaction", "grading"), start=1):
        prompt = _phase_prompt(name, trial)
        argv, sanitized = _expanded_argv(command, prompt, trial)
        stdout_path = logs / f"{index}-{name}.stdout.log"
        stderr_path = logs / f"{index}-{name}.stderr.log"
        trace_path = traces / f"{index}-{name}.jsonl"
        trace_paths.append(trace_path)
        before = observed_artifacts(trial)
        started_at = utc_now()
        started = time.monotonic()
        exit_code: int | None = None
        timed_out = False
        error = ""
        env = os.environ.copy()
        env["GRADER_CONFORMANCE_TRACE"] = str(trace_path)
        try:
            with (
                _open_private_log(stdout_path) as stdout,
                _open_private_log(stderr_path) as stderr,
            ):
                completed = subprocess.run(
                    argv,
                    shell=False,
                    cwd=trial,
                    env=env,
                    stdout=stdout,
                    stderr=stderr,
                    timeout=timeout_seconds,
                    check=False,
                    preexec_fn=_bound_child_files if os.name == "posix" else None,
                )
                exit_code = completed.returncode
        except subprocess.TimeoutExpired:
            timed_out = True
            error = "timeout"
        except OSError:
            error = "launch-failed"
        elapsed = time.monotonic() - started
        ended_at = utc_now()
        refuse_workspace_links(trial)
        stdout_result, stdout_overflow = _log_result(
            trial, stdout_path, MAX_STDOUT_BYTES
        )
        stderr_result, stderr_overflow = _log_result(
            trial, stderr_path, MAX_STDERR_BYTES
        )
        if exit_code == -getattr(signal, "SIGXFSZ", 25) or (
            exit_code not in (None, 0)
            and (
                stdout_result["bytes"] == MAX_STDOUT_BYTES
                or stderr_result["bytes"] == MAX_STDERR_BYTES
            )
        ):
            stdout_overflow = stdout_overflow or (
                stdout_result["bytes"] == MAX_STDOUT_BYTES
            )
            stderr_overflow = stderr_overflow or (
                stderr_result["bytes"] == MAX_STDERR_BYTES
            )
        if stdout_overflow or stderr_overflow:
            error = "output-limit"
        if trace_path.exists() and not trace_path.is_symlink():
            trace_path.chmod(0o600)

        process_passed = exit_code == 0 and not timed_out and not error
        try:
            after = observed_artifacts(trial)
            latest_audit = audit_workspace(
                trial,
                runtime=runtime,
                trace_paths=tuple(trace_paths),
                expected_phase=name,
                persist=False,
            )
            checkpoint_passed = latest_audit.passed
            checks = (
                f"checkpoint:{'passed' if checkpoint_passed else 'failed'}",
                f"trace:{latest_audit.trace.status}",
            )
        except ConformanceError:
            after = ()
            checkpoint_passed = False
            checks = ("checkpoint:unsafe-artifact",)
            error = error or "unsafe-artifact"
        phase_passed = process_passed and checkpoint_passed
        phase_reports.append(
            TrialPhase(
                name=name,
                started_at=started_at,
                ended_at=ended_at,
                elapsed_seconds=round(elapsed, 6),
                argv=sanitized,
                exit_code=exit_code,
                timed_out=timed_out,
                passed=phase_passed,
                stdout=stdout_result,
                stderr=stderr_result,
                artifacts_before=before,
                artifacts_after=after,
                checks=checks,
                error=error,
            )
        )
        if not phase_passed:
            break

    if latest_audit is None:
        marker = read_trial_marker(trial)
        base = ConformanceReport(
            runtime=runtime,
            mode="run",
            repository_revision=str(marker["repository_revision"]),
            workspace=trial,
            passed=False,
            phases=tuple(phase_reports),
            trace=audit_trace(trial, tuple(trace_paths)),
        )
    else:
        base = replace(
            latest_audit,
            mode="run",
            passed=(
                len(phase_reports) == 3
                and all(phase.passed for phase in phase_reports)
                and latest_audit.passed
            ),
            phases=tuple(phase_reports),
            json_path=None,
            markdown_path=None,
        )
    return persist_report(base)
