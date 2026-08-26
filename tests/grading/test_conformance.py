"""Agent conformance trials stay shell-free and evidence-bounded."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.grading.brief import ANSWER_REQUIRED, approve_brief, prepare_brief
from scripts.grading.conformance import (
    ConformanceError,
    MAX_STDOUT_BYTES,
    audit_workspace,
    create_trial,
    parse_command_json,
    run_trial,
)
from scripts.grading.density import load_terms, scan
from scripts.grading.records import CriterionScore, GradeRecord, Justification, locate
from scripts.grading.rubric import load_rubric
from scripts.redaction.pipeline import EXIT_OK, run as redact
from scripts.redaction.roster import load_roster


REPO_ROOT = Path(__file__).resolve().parents[2]
RUBRIC = Path("tests/fixtures/phil210/grading_rubric.md")
PROFILE = Path(".grading/profiles/phil210.md")
PATTERNS = Path("references/ai-patterns.md")

FEEDBACK = """[This sentence carries the paragraph. Lead with it.]

**The Bottom Line**

The argument names a concrete mechanism and supports it with evidence.

**What Worked**

* The central claim stays specific.

**What to Work On**

* Develop the counterargument before answering it.

— Dr. Vance
"""


def test_command_json_is_argv_not_shell_text():
    command = parse_command_json('["fake-agent", "--prompt", "{prompt}"]')

    assert command == ("fake-agent", "--prompt", "{prompt}")


@pytest.mark.parametrize("raw", ["echo hi", "{}", "[]", '["agent", 3]'])
def test_invalid_command_json_is_refused(raw):
    with pytest.raises(ConformanceError):
        parse_command_json(raw)


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(["agent"] * 65),
        json.dumps(["a" * 4_097, "{prompt}"]),
        json.dumps(["", "{prompt}"]),
        json.dumps(["agent\x00", "{prompt}"]),
        json.dumps(["agent", "no prompt placeholder"]),
    ],
)
def test_command_json_enforces_argv_and_prompt_bounds(raw):
    with pytest.raises(ConformanceError):
        parse_command_json(raw)


def _trial(tmp_path: Path) -> Path:
    return create_trial(REPO_ROOT, tmp_path / "trial")


def _approve_trial_brief(workspace: Path) -> None:
    path = workspace / ".grading/assignments/essay-2.md"
    prepare_brief(path, batch="essay-2", course="phil210", mode="local")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            ANSWER_REQUIRED, "Synthetic conformance answer."
        ),
        encoding="utf-8",
    )
    approve_brief(
        path,
        batch="essay-2",
        course="phil210",
        rubric=workspace / RUBRIC,
        profile=workspace / PROFILE,
        approval="Approve assignment brief for essay-2",
    )


def _redact_trial(workspace: Path) -> Path:
    result = redact(
        source=workspace / "synthetic/submissions",
        destination=workspace / "redacted",
        map_path=workspace / ".grading/maps/essay-2.json",
        review_path=workspace / ".grading/maps/reviews/essay-2.json",
        roster=load_roster(workspace / "synthetic/roster.csv"),
        batch="essay-2",
        protected=(),
        reviewed=True,
    )
    assert result.exit_code == EXIT_OK, result.message
    return next(
        path
        for path in (workspace / "redacted").iterdir()
        if path.name.startswith("STUDENT_")
    )


def _unique_quotes(source: str, count: int) -> list[str]:
    quotes: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+", source):
        quote = " ".join(raw.split())
        if len(quote.split()) < 10:
            continue
        try:
            locate(quote, source)
        except Exception:
            continue
        quotes.append(quote)
        if len(quotes) == count:
            break
    assert len(quotes) == count
    return quotes


def _write_valid_pair(workspace: Path, source_path: Path) -> None:
    rubric = load_rubric(workspace / RUBRIC)
    source = source_path.read_text(encoding="utf-8")
    quotes = _unique_quotes(source, len(rubric.criteria))
    scores = (21, 20, 19, 22)
    record = GradeRecord(
        batch="essay-2",
        token=source_path.stem,
        source=str(Path("redacted") / source_path.name),
        course="phil210",
        rubric=str(RUBRIC),
        profile=str(PROFILE),
        criteria=tuple(
            CriterionScore(
                number=criterion.number,
                name=criterion.name,
                points_possible=criterion.points,
                score=scores[index],
                level="Proficient",
                assessable=True,
                justifications=(Justification(quote=quotes[index]),),
            )
            for index, criterion in enumerate(rubric.criteria)
        ),
        total=sum(scores),
        total_possible=rubric.total_points,
        letter=rubric.letter_for(sum(scores)),
        pattern_density=scan(source, load_terms(workspace / PATTERNS)).as_dict(),
    )
    drafts = workspace / "drafts/essay-2"
    record.save(drafts / f"{source_path.stem}.json")
    (drafts / f"{source_path.stem}.md").write_text(FEEDBACK, encoding="utf-8")


def _complete_trial(workspace: Path) -> None:
    _approve_trial_brief(workspace)
    source = _redact_trial(workspace)
    _write_valid_pair(workspace, source)
    verified = subprocess.run(
        [
            sys.executable,
            "scripts/grade.py",
            "verify",
            "--batch",
            "essay-2",
            "--course",
            "phil210",
            "--rubric",
            str(RUBRIC),
            "--redacted",
            "redacted",
            "--drafts",
            "drafts/essay-2",
            "--patterns",
            str(PATTERNS),
            "--root",
            ".grading",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert verified.returncode == 0, verified.stderr
    published = subprocess.run(
        [
            sys.executable,
            "scripts/rehydrate.py",
            "drafts/essay-2",
            "--batch",
            "essay-2",
            "--course",
            "phil210",
            "--rubric",
            str(RUBRIC),
            "--profile",
            str(PROFILE),
            "--redacted",
            "redacted",
            "--brief",
            ".grading/assignments/essay-2.md",
            "--map",
            ".grading/maps/essay-2.json",
            "--out",
            "final",
        ],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    assert published.returncode == 0, published.stderr


def _write_trace(workspace: Path, name: str, events: list[dict]) -> Path:
    path = workspace / f".grading/conformance/traces/{name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )
    return path


def test_audit_detects_malformed_record_and_absent_receipt(tmp_path):
    workspace = _trial(tmp_path)
    _approve_trial_brief(workspace)
    source = _redact_trial(workspace)
    drafts = workspace / "drafts/essay-2"
    drafts.mkdir(parents=True)
    (drafts / f"{source.stem}.md").write_text(FEEDBACK, encoding="utf-8")
    (drafts / f"{source.stem}.json").write_text("{bad json", encoding="utf-8")

    report = audit_workspace(workspace, runtime="manual")

    assert report.passed is False
    assert report.lifecycle["verify"]["exit_code"] == 13
    assert report.lifecycle["receipt"]["present"] is False


def test_audit_detects_identity_leak_in_redacted_or_drafts(tmp_path):
    workspace = _trial(tmp_path)
    leaked = workspace / "redacted/STUDENT_01.md"
    leaked.parent.mkdir()
    synthetic_name = load_roster(workspace / "synthetic/roster.csv")[0].name
    leaked.write_text(f"This contains {synthetic_name}.\n", encoding="utf-8")

    report = audit_workspace(workspace, runtime="manual")

    assert "redacted/STUDENT_01.md" in report.identity_leaks
    assert synthetic_name not in repr(report)


def test_audit_sanitizes_an_identity_bearing_artifact_name(tmp_path):
    workspace = _trial(tmp_path)
    synthetic_name = load_roster(workspace / "synthetic/roster.csv")[0].name
    leaked = workspace / f"redacted/{synthetic_name}.md"
    leaked.parent.mkdir()
    leaked.write_text(f"This contains {synthetic_name}.\n", encoding="utf-8")

    report = audit_workspace(workspace, runtime="manual")

    assert report.identity_leaks == ("redacted/<identity-bearing-path>",)
    assert synthetic_name not in repr(report)


def test_audit_reports_trace_unverified_when_absent(tmp_path):
    report = audit_workspace(_trial(tmp_path), runtime="manual")

    assert report.trace.status == "unverified"


def test_report_evidence_is_deeply_immutable(tmp_path):
    report = audit_workspace(_trial(tmp_path), runtime="manual")

    with pytest.raises(TypeError):
        report.lifecycle["verify"]["exit_code"] = 0


def test_audit_fails_a_traced_private_read(tmp_path):
    workspace = _trial(tmp_path)
    _complete_trial(workspace)
    trace = _write_trace(
        workspace,
        "manual",
        [
            {
                "schema": "grading-agent-trace/1",
                "event": "read",
                "phase": "grading",
                "path": ".grading/maps/essay-2.json",
            }
        ],
    )

    report = audit_workspace(workspace, runtime="manual", trace_paths=(trace,))

    assert report.passed is False
    assert report.trace.status == "failed"
    assert report.trace.prohibited_paths == (".grading/maps/essay-2.json",)
    assert load_roster(workspace / "synthetic/roster.csv")[0].name not in repr(report)


def test_audit_does_not_echo_identity_bearing_private_trace_path(tmp_path):
    workspace = _trial(tmp_path)
    synthetic_name = load_roster(workspace / "synthetic/roster.csv")[0].name
    trace = _write_trace(
        workspace,
        "identity-path",
        [
            {
                "schema": "grading-agent-trace/1",
                "event": "read",
                "phase": "grading",
                "path": f"synthetic/submissions/{synthetic_name}.md",
            }
        ],
    )

    report = audit_workspace(workspace, runtime="manual", trace_paths=(trace,))

    assert report.trace.status == "failed"
    assert report.trace.prohibited_paths == (
        "synthetic/submissions/<identity-bearing-path>",
    )
    assert synthetic_name not in repr(report)


def test_audit_passes_phase_allowed_reads(tmp_path):
    workspace = _trial(tmp_path)
    _complete_trial(workspace)
    trace = _write_trace(
        workspace,
        "manual",
        [
            {
                "schema": "grading-agent-trace/1",
                "event": "read",
                "phase": "grading",
                "path": path,
            }
            for path in (
                "SKILL.md",
                str(RUBRIC),
                str(PROFILE),
                "redacted/STUDENT_01.md",
            )
        ],
    )

    report = audit_workspace(workspace, runtime="manual", trace_paths=(trace,))

    assert report.passed is True
    assert report.trace.status == "passed"


def test_audit_keeps_trace_unverified_when_a_requested_phase_trace_is_missing(
    tmp_path,
):
    workspace = _trial(tmp_path)
    _complete_trial(workspace)
    trace = _write_trace(
        workspace,
        "grading",
        [
            {
                "schema": "grading-agent-trace/1",
                "event": "read",
                "phase": "grading",
                "path": "redacted/STUDENT_01.md",
            }
        ],
    )

    report = audit_workspace(
        workspace,
        runtime="manual",
        trace_paths=(Path(".grading/conformance/traces/missing.jsonl"), trace),
    )

    assert report.passed is True
    assert report.trace.status == "unverified"


def test_audit_refuses_workspace_escape_symlink(tmp_path):
    workspace = _trial(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (workspace / "redacted").mkdir()
    (workspace / "redacted/STUDENT_01.md").symlink_to(outside)

    with pytest.raises(ConformanceError):
        audit_workspace(workspace, runtime="manual")


def test_audit_refuses_a_workspace_that_replaces_the_synthetic_inputs(tmp_path):
    workspace = _trial(tmp_path)
    (workspace / "synthetic/roster.csv").write_text(
        "name,student_id,email\nReal Student,S1,real@example.edu\n",
        encoding="utf-8",
    )

    with pytest.raises(ConformanceError):
        audit_workspace(workspace, runtime="manual")


def _fake_agent(tmp_path: Path) -> Path:
    script = tmp_path / "fake_agent.py"
    script.write_text(
        """import json
import os
import sys
import time

literal = sys.argv[1]
delay = float(sys.argv[2])
exit_code = int(sys.argv[3])
prompt = sys.argv[-1]
phase = next(
    line.split(':', 1)[1].strip()
    for line in prompt.splitlines()
    if line.startswith('CONFORMANCE_PHASE:')
)
time.sleep(delay)
print(json.dumps({'cwd': os.getcwd(), 'literal': literal, 'phase': phase}))
raise SystemExit(exit_code)
""",
        encoding="utf-8",
    )
    return script


def test_run_is_shell_free_bounded_and_stops_after_first_failed_phase(tmp_path):
    adapter = _fake_agent(tmp_path)
    literal = "; touch SHELL_ESCAPED"
    command = json.dumps([sys.executable, str(adapter), literal, "0", "7", "{prompt}"])

    report = run_trial(
        REPO_ROOT,
        runtime="fake",
        command=parse_command_json(command),
        timeout_seconds=5,
        workspace=tmp_path / "run-trial",
    )

    assert report.passed is False
    assert len(report.phases) == 1
    phase = report.phases[0]
    assert phase.exit_code == 7
    assert phase.timed_out is False
    assert phase.elapsed_seconds >= 0
    assert phase.argv[-1].startswith("sha256:")
    assert len(phase.argv[-1]) == len("sha256:") + 64
    assert "CONFORMANCE_PHASE" not in " ".join(phase.argv)
    assert not (report.workspace / "SHELL_ESCAPED").exists()
    stdout_path = report.workspace / str(phase.stdout["path"])
    observed = json.loads(stdout_path.read_text(encoding="utf-8"))
    assert observed == {
        "cwd": str(report.workspace),
        "literal": literal,
        "phase": "briefing",
    }
    for path in (
        stdout_path,
        report.workspace / str(phase.stderr["path"]),
        report.json_path,
        report.markdown_path,
    ):
        assert path is not None
        assert stat_mode(path) == 0o600


def test_run_records_timeout_and_elapsed_time(tmp_path):
    adapter = _fake_agent(tmp_path)
    command = json.dumps(
        [sys.executable, str(adapter), "literal", "2", "0", "{prompt}"]
    )

    report = run_trial(
        REPO_ROOT,
        runtime="fake-timeout",
        command=parse_command_json(command),
        timeout_seconds=0.05,
        workspace=tmp_path / "timeout-trial",
    )

    assert len(report.phases) == 1
    assert report.phases[0].timed_out is True
    assert report.phases[0].exit_code is None
    assert report.phases[0].elapsed_seconds >= 0.05


def test_run_fails_and_truncates_oversized_agent_output(tmp_path):
    adapter = tmp_path / "noisy_agent.py"
    adapter.write_text(
        """import os

os.write(1, b'x' * (2 * 1024 * 1024 + 4096))
""",
        encoding="utf-8",
    )
    command = json.dumps([sys.executable, str(adapter), "{prompt}"])

    report = run_trial(
        REPO_ROOT,
        runtime="fake-output",
        command=parse_command_json(command),
        timeout_seconds=5,
        workspace=tmp_path / "output-trial",
    )

    phase = report.phases[0]
    assert phase.passed is False
    assert phase.error == "output-limit"
    assert phase.stdout["bytes"] == MAX_STDOUT_BYTES
    assert (report.workspace / phase.stdout["path"]).stat().st_size == MAX_STDOUT_BYTES


def stat_mode(path: Path) -> int:
    return os.stat(path, follow_symlinks=False).st_mode & 0o777


def test_cli_run_and_audit_print_report_paths_and_concise_status(tmp_path):
    adapter = _fake_agent(tmp_path)
    command = json.dumps(
        [sys.executable, str(adapter), "literal", "0", "7", "{prompt}"]
    )
    run_result = subprocess.run(
        [
            sys.executable,
            "scripts/agent_conformance.py",
            "run",
            "--runtime",
            "fake-cli",
            "--command-json",
            command,
            "--timeout",
            "5",
            "--workspace",
            str(tmp_path / "cli-run"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert run_result.returncode == 12
    assert "FAIL" in run_result.stdout
    assert ".json" in run_result.stdout
    assert ".md" in run_result.stdout
    assert "Traceback" not in run_result.stderr

    workspace = _trial(tmp_path / "audit")
    _complete_trial(workspace)
    audit_result = subprocess.run(
        [
            sys.executable,
            "scripts/agent_conformance.py",
            "audit",
            "--runtime",
            "manual",
            "--workspace",
            str(workspace),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert audit_result.returncode == 0
    assert "UNVERIFIED" in audit_result.stdout
    assert ".json" in audit_result.stdout
    assert ".md" in audit_result.stdout
    assert "Traceback" not in audit_result.stderr


def test_cli_malformed_input_exits_13_without_traceback():
    result = subprocess.run(
        [
            sys.executable,
            "scripts/agent_conformance.py",
            "run",
            "--runtime",
            "fake",
            "--command-json",
            "echo hi",
            "--timeout",
            "5",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 13
    assert "Traceback" not in result.stderr
