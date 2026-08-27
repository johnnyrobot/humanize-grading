"""Immutable values and limits shared by conformance internals."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType


REPORT_SCHEMA = "grading-agent-conformance/1"
TRACE_SCHEMA = "grading-agent-trace/1"

MAX_ARGV_ITEMS = 64
MAX_ARG_CHARS = 4_096
MAX_STDOUT_BYTES = 2 * 1024 * 1024
MAX_STDERR_BYTES = 2 * 1024 * 1024
MAX_TRACE_EVENTS = 10_000
MAX_TRACE_LINE_BYTES = 1024 * 1024

TRIAL_SCHEMA = "grading-agent-conformance-trial/1"
TRIAL_MARKER = Path(".grading/conformance-trial.json")
TRIAL_BATCH = "essay-2"
TRIAL_COURSE = "phil210"
TRIAL_RUBRIC = Path("tests/fixtures/phil210/grading_rubric.md")
TRIAL_PROFILE = Path(".grading/profiles/phil210.md")
TRIAL_PATTERNS = Path("references/ai-patterns.md")
TRIAL_BRIEF = Path(".grading/assignments/essay-2.md")
TRIAL_REDACTED = Path("redacted")
TRIAL_DRAFTS = Path("drafts/essay-2")
TRIAL_PUBLICATION = Path("final/agent-e2e")
TRIAL_ROSTER = Path("synthetic/roster.csv")
TRIAL_SUBMISSIONS = Path("synthetic/submissions")
TRIAL_MAPS = Path(".grading") / "maps"
TRIAL_MAP = TRIAL_MAPS / "essay-2.json"
TRIAL_REVIEW = TRIAL_MAPS / "reviews" / "essay-2.json"

RUNTIME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


class ConformanceError(ValueError):
    """A conformance input or observed artifact is unsafe or malformed."""


def _freeze(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class TrialPhase:
    """One bounded agent process and its checkpoint result."""

    name: str
    started_at: str
    ended_at: str
    elapsed_seconds: float
    argv: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    passed: bool
    stdout: Mapping[str, object] = field(default_factory=dict)
    stderr: Mapping[str, object] = field(default_factory=dict)
    artifacts_before: tuple[Mapping[str, object], ...] = ()
    artifacts_after: tuple[Mapping[str, object], ...] = ()
    checks: tuple[str, ...] = ()
    error: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(self, "stdout", _freeze(self.stdout))
        object.__setattr__(self, "stderr", _freeze(self.stderr))
        object.__setattr__(self, "artifacts_before", _freeze(self.artifacts_before))
        object.__setattr__(self, "artifacts_after", _freeze(self.artifacts_after))
        object.__setattr__(self, "checks", tuple(self.checks))


@dataclass(frozen=True)
class TraceResult:
    """Bounded read evidence supplied by an agent runtime."""

    status: str
    events: int = 0
    reads: int = 0
    prohibited_paths: tuple[str, ...] = ()
    findings: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConformanceReport:
    """Identity-free result of one synthetic run or audit."""

    runtime: str
    mode: str
    repository_revision: str
    workspace: Path
    passed: bool
    phases: tuple[TrialPhase, ...] = ()
    lifecycle: Mapping[str, object] = field(default_factory=dict)
    artifacts: tuple[Mapping[str, object], ...] = ()
    artifact_findings: tuple[Mapping[str, object], ...] = ()
    identity_leaks: tuple[str, ...] = ()
    private_artifacts: tuple[Mapping[str, object], ...] = ()
    package: Mapping[str, object] = field(default_factory=dict)
    trace: TraceResult = field(default_factory=lambda: TraceResult("unverified"))
    json_path: Path | None = None
    markdown_path: Path | None = None
    schema: str = REPORT_SCHEMA

    def __post_init__(self) -> None:
        object.__setattr__(self, "phases", tuple(self.phases))
        object.__setattr__(self, "lifecycle", _freeze(self.lifecycle))
        object.__setattr__(self, "artifacts", _freeze(self.artifacts))
        object.__setattr__(self, "artifact_findings", _freeze(self.artifact_findings))
        object.__setattr__(self, "identity_leaks", tuple(self.identity_leaks))
        object.__setattr__(self, "private_artifacts", _freeze(self.private_artifacts))
        object.__setattr__(self, "package", _freeze(self.package))


def utc_now() -> str:
    return (
        datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def parse_command_json(raw: str) -> tuple[str, ...]:
    """Parse one JSON array into argv without accepting shell text."""
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as error:
        raise ConformanceError(
            "The agent command must be a JSON argv array."
        ) from error
    if (
        not isinstance(payload, list)
        or not payload
        or len(payload) > MAX_ARGV_ITEMS
        or any(not isinstance(item, str) for item in payload)
    ):
        raise ConformanceError(
            "The agent command must be a non-empty JSON array of text argv items."
        )
    if (
        not payload[0]
        or any(len(item) > MAX_ARG_CHARS or "\x00" in item for item in payload)
        or sum(item.count("{prompt}") for item in payload) != 1
    ):
        raise ConformanceError(
            "The agent command has an unsafe argv item or needs one {prompt} "
            "placeholder."
        )
    return tuple(payload)


def report_as_dict(report: ConformanceReport) -> dict[str, object]:
    """Return the stable, JSON-ready conformance report document."""
    return {
        "schema": report.schema,
        "runtime": report.runtime,
        "mode": report.mode,
        "repository_revision": report.repository_revision,
        "workspace": str(report.workspace),
        "passed": report.passed,
        "phases": [
            {
                "name": phase.name,
                "started_at": phase.started_at,
                "ended_at": phase.ended_at,
                "elapsed_seconds": phase.elapsed_seconds,
                "argv": list(phase.argv),
                "exit_code": phase.exit_code,
                "timed_out": phase.timed_out,
                "passed": phase.passed,
                "stdout": _thaw(phase.stdout),
                "stderr": _thaw(phase.stderr),
                "artifacts_before": _thaw(phase.artifacts_before),
                "artifacts_after": _thaw(phase.artifacts_after),
                "checks": list(phase.checks),
                "error": phase.error,
            }
            for phase in report.phases
        ],
        "lifecycle": _thaw(report.lifecycle),
        "artifacts": _thaw(report.artifacts),
        "artifact_findings": _thaw(report.artifact_findings),
        "identity_leaks": list(report.identity_leaks),
        "private_artifacts": _thaw(report.private_artifacts),
        "package": _thaw(report.package),
        "trace": {
            "status": report.trace.status,
            "events": report.trace.events,
            "reads": report.trace.reads,
            "prohibited_paths": list(report.trace.prohibited_paths),
            "findings": list(report.trace.findings),
        },
    }
