"""The optional hook blocks common accidental reads of private grading data."""

import json
import re
import subprocess
import sys
from pathlib import Path

HOOK = "hooks/block_raw_reads.py"


def _run(payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, HOOK],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
    )


def _read(path: str) -> dict:
    return {"tool_name": "Read", "tool_input": {"file_path": path}}


def _bash(command: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": command}}


def test_reading_a_raw_submission_is_denied():
    result = _run(_read("/Users/x/course/submissions/essay-3/smith_jane.docx"))
    assert result.returncode == 2
    assert "submissions/" in result.stderr


def test_reading_the_token_map_is_denied():
    result = _run(_read(".grading/maps/essay-3.json"))
    assert result.returncode == 2


def test_reading_the_review_file_is_denied():
    assert _run(_read(".grading/reviews/essay-3.json")).returncode == 2


def test_reading_a_redacted_file_is_allowed():
    assert _run(_read("redacted/STUDENT_01.md")).returncode == 0


def test_reading_a_profile_is_allowed():
    assert _run(_read(".grading/profiles/phil210.md")).returncode == 0


def test_reading_a_draft_is_allowed():
    assert _run(_read(".grading/drafts/essay-3/STUDENT_01.md")).returncode == 0


def test_a_windows_style_path_is_denied_too():
    assert _run(_read(r"C:\course\submissions\essay3\paper.docx")).returncode == 2


def test_a_glob_over_submissions_is_denied():
    payload = {"tool_name": "Glob", "tool_input": {"pattern": "submissions/**/*.docx"}}
    assert _run(payload).returncode == 2


def test_writing_into_submissions_is_denied():
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": "submissions/notes.md", "content": "x"},
    }
    assert _run(payload).returncode == 2


def test_catting_a_submission_is_denied():
    assert _run(_bash("cat submissions/essay-3/paper.md")).returncode == 2


def test_grepping_the_map_is_denied():
    assert _run(_bash("grep STUDENT_01 .grading/maps/essay-3.json")).returncode == 2


def test_catting_the_roster_is_denied():
    assert _run(_bash("cat tests/fixtures/phil210/roster.csv")).returncode == 2


def test_catting_a_bare_roster_filename_is_denied():
    assert _run(_bash("cat roster.csv")).returncode == 2


def test_reading_the_roster_with_a_file_tool_is_denied():
    assert _run(_read("phil210.roster.csv")).returncode == 2


def test_running_redact_with_a_roster_flag_is_allowed():
    command = (
        "python3 scripts/redact.py submissions/essay-3 "
        "--roster roster.csv --batch essay-3"
    )
    assert _run(_bash(command)).returncode == 0


def test_running_rehydrate_with_the_map_flag_is_allowed():
    command = (
        "python3 scripts/rehydrate.py .grading/drafts/essay-3 "
        "--map .grading/maps/essay-3.json --out final/essay-3"
    )
    assert _run(_bash(command)).returncode == 0


def test_a_python_one_liner_reading_the_map_is_denied():
    command = "python3 -c \"print(open('.grading/maps/essay-3.json').read())\""
    assert _run(_bash(command)).returncode == 2


def test_an_unrelated_command_is_allowed():
    assert _run(_bash("python3 -m pytest tests/ -v")).returncode == 0


def test_unreadable_input_allows_rather_than_bricking_the_session():
    result = subprocess.run(
        [sys.executable, HOOK], input="not json", capture_output=True, text=True
    )
    assert result.returncode == 0
    assert result.stderr


def test_the_denial_message_says_what_to_read_instead():
    result = _run(_read("submissions/essay-3/paper.md"))
    assert "redacted/" in result.stderr


def test_the_hook_config_is_valid_and_opt_in():
    config = json.loads(
        Path("hooks/block-raw-reads.json").read_text(encoding="utf-8")
    )
    entries = config["hooks"]["PreToolUse"]
    assert entries
    assert "Bash" in entries[0]["matcher"]
    assert "Read" in entries[0]["matcher"]
    assert not Path("hooks/hooks.json").exists()


def test_the_cloud_workflow_keeps_review_data_out_of_model_context():
    skill = Path("SKILL.md").read_text(encoding="utf-8")

    assert "Read the review file it names" not in skill
    assert re.search(
        r"inspect the review file locally,\s+outside\s+this\s+conversation", skill
    )
    assert "difference between a rule and a guarantee" not in skill
