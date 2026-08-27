"""Assignment briefs are explicit, inspectable grading approval state."""

import hashlib
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.grading.brief import (
    ANSWER_REQUIRED,
    BriefError,
    approve_brief,
    brief_path,
    check_brief,
    load_brief,
    prepare_brief,
)


def _answer(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    path.write_text(
        text.replace(ANSWER_REQUIRED, "Instructor answer."), encoding="utf-8"
    )


def _grade(*args: str):
    return subprocess.run(
        [sys.executable, "scripts/grade.py", *args],
        capture_output=True,
        text=True,
    )


def _brief_args(root: Path) -> list[str]:
    return ["--batch", "essay-3", "--course", "phil210", "--root", str(root)]


def _copy_profile(root: Path, profile: Path) -> None:
    (root / "profiles").mkdir(parents=True)
    (root / "profiles" / "phil210.md").write_bytes(profile.read_bytes())


def _assignment_brief_errors(stderr: str) -> list[str]:
    return [line for line in stderr.splitlines() if line.strip()]


def _approved(path: Path, phil210: Path, profile: Path) -> None:
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
        approval="Approve assignment brief for essay-3",
    )


def test_brief_path_uses_one_safe_batch_component(tmp_path):
    assert brief_path(tmp_path, "essay-3") == tmp_path / "assignments" / "essay-3.md"
    with pytest.raises(ValueError, match="grading batch"):
        brief_path(tmp_path, "../essay-3")


def test_prepare_creates_an_owner_only_draft_without_overwriting(tmp_path):
    path = brief_path(tmp_path, "essay-3")
    brief = prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    assert brief.status == "draft"
    assert brief.mode == "local"
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(BriefError, match="already exists"):
        prepare_brief(path, batch="essay-3", course="phil210", mode="local")


def test_load_brief_reads_draft_and_approved_state_without_changing_it(
    tmp_path, phil210, profile
):
    path = brief_path(tmp_path, "essay-3")
    draft = prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    draft_bytes = path.read_bytes()

    assert load_brief(path) == draft
    assert path.read_bytes() == draft_bytes

    _answer(path)
    approved = approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
        approval="Approve assignment brief for essay-3",
    )
    approved_bytes = path.read_bytes()

    assert load_brief(path) == approved
    assert path.read_bytes() == approved_bytes


def test_load_brief_rejects_malformed_metadata(tmp_path):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "status: draft", "status: pending", 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(BriefError, match="status must be draft or approved"):
        load_brief(path)


def test_prepare_rejects_unknown_mode(tmp_path):
    with pytest.raises(BriefError, match="local or cloud"):
        prepare_brief(
            brief_path(tmp_path, "essay-3"),
            batch="essay-3",
            course="phil210",
            mode="hybrid",
        )


def test_approval_requires_the_exact_batch_phrase(tmp_path, phil210, profile):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    with pytest.raises(BriefError, match="Approve assignment brief for essay-3"):
        approve_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
            approval="yes",
        )


def test_approved_brief_binds_body_rubric_and_profile(tmp_path, phil210, profile):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    approved = approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
        approval="Approve assignment brief for essay-3",
    )
    assert approved.status == "approved"
    assert approved.approved_at.endswith("Z")
    assert len(approved.body_sha256) == 64
    assert approved.source_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert check_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    ) == approved


@pytest.mark.parametrize("evidence", ["rubric", "profile"])
def test_approval_and_check_bind_evidence_at_the_five_mebibyte_limit(
    tmp_path, phil210, profile, evidence
):
    path = brief_path(tmp_path, "essay-3")
    large = tmp_path / f"large-{evidence}.md"
    large.write_bytes(b"x" * (5 * 1024 * 1024))
    rubric = large if evidence == "rubric" else phil210 / "grading_rubric.md"
    bound_profile = large if evidence == "profile" else profile
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)

    approved = approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=rubric,
        profile=bound_profile,
        approval="Approve assignment brief for essay-3",
    )

    assert check_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=rubric,
        profile=bound_profile,
    ) == approved


@pytest.mark.parametrize("evidence", ["rubric", "profile"])
def test_approval_refuses_bound_evidence_above_five_mebibytes(
    tmp_path, phil210, profile, evidence
):
    path = brief_path(tmp_path, "essay-3")
    oversized = tmp_path / f"oversized-{evidence}.md"
    oversized.write_bytes(b"x" * ((5 * 1024 * 1024) + 1))
    rubric = oversized if evidence == "rubric" else phil210 / "grading_rubric.md"
    bound_profile = oversized if evidence == "profile" else profile
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)

    with pytest.raises(BriefError, match="5242880 bytes"):
        approve_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=rubric,
            profile=bound_profile,
            approval="Approve assignment brief for essay-3",
        )


def test_crlf_brief_can_be_approved_and_checked_with_exact_source_binding(
    tmp_path, phil210, profile
):
    path = brief_path(tmp_path, "essay-3")
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    answered = path.read_text(encoding="utf-8").replace(
        ANSWER_REQUIRED, "Instructor answer."
    )
    path.write_bytes(answered.replace("\n", "\r\n").encode("utf-8"))

    approved = approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
        approval="Approve assignment brief for essay-3",
    )
    approved_bytes = path.read_bytes()
    assert approved.source_sha256 == hashlib.sha256(approved_bytes).hexdigest()

    crlf_bytes = approved_bytes.replace(b"\n", b"\r\n")
    path.write_bytes(crlf_bytes)
    checked = check_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    )

    assert checked.body_sha256 == approved.body_sha256
    assert checked.source_sha256 == hashlib.sha256(crlf_bytes).hexdigest()

    path.write_bytes(crlf_bytes + b"Changed after approval.\r\n")
    with pytest.raises(BriefError, match="body changed"):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
        )


@pytest.mark.parametrize("changed", ["body", "rubric", "profile"])
def test_changes_invalidate_approval(tmp_path, phil210, profile, changed):
    path = brief_path(tmp_path, "essay-3")
    rubric = tmp_path / "rubric.md"
    profile_copy = tmp_path / "profile.md"
    rubric.write_bytes((phil210 / "grading_rubric.md").read_bytes())
    profile_copy.write_bytes(profile.read_bytes())
    prepare_brief(path, batch="essay-3", course="phil210", mode="local")
    _answer(path)
    approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=rubric,
        profile=profile_copy,
        approval="Approve assignment brief for essay-3",
    )
    target = {"body": path, "rubric": rubric, "profile": profile_copy}[changed]
    target.write_text(target.read_text(encoding="utf-8") + "\nchanged\n", encoding="utf-8")
    with pytest.raises(BriefError, match="changed|stale"):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=rubric,
            profile=profile_copy,
        )


def test_mode_must_match_the_hash_bound_boundaries_line(tmp_path, phil210, profile):
    path = brief_path(tmp_path, "essay-3")
    _approved(path, phil210, profile)
    original = path.read_text(encoding="utf-8")
    path.write_text(
        original.replace("mode: local", "mode: cloud", 1), encoding="utf-8"
    )
    with pytest.raises(BriefError, match="Host mode"):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
        )

    path.write_text(
        original.replace("mode: local", "mode: cloud", 1).replace(
            "Host mode: local", "Host mode: cloud"
        ),
        encoding="utf-8",
    )
    with pytest.raises(BriefError, match="body changed"):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
        )


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ("unknown", "unknown"),
        ("duplicate", "duplicate"),
        ("missing_heading", "Boundaries"),
        ("remaining_marker", "answer"),
        ("malformed_hash", "SHA-256"),
        ("wrong_course", "course"),
        ("wrong_batch", "batch"),
    ],
)
def test_check_refuses_malformed_or_mismatched_briefs(
    tmp_path, phil210, profile, change, message
):
    path = brief_path(tmp_path, "essay-3")
    _approved(path, phil210, profile)
    text = path.read_text(encoding="utf-8")
    if change == "unknown":
        text = text.replace("schema: grading-assignment-brief/1", "extra: no\nschema: grading-assignment-brief/1")
    elif change == "duplicate":
        text = text.replace("course: phil210", "course: phil210\ncourse: phil210")
    elif change == "missing_heading":
        text = text.replace("## Boundaries", "## Limits")
    elif change == "remaining_marker":
        text = text.replace("Instructor answer.", ANSWER_REQUIRED, 1)
    elif change == "malformed_hash":
        text = text.replace("body_sha256: ", "body_sha256: not-a-hash", 1)
    elif change == "wrong_course":
        text = text.replace("course: phil210", "course: eng101")
    elif change == "wrong_batch":
        text = text.replace("batch: essay-3", "batch: essay-4").replace(
            "Approve assignment brief for essay-3", "Approve assignment brief for essay-4"
        )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BriefError, match=message):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
        )


def test_brief_paths_must_not_use_symbolic_links(tmp_path, phil210, profile):
    target = tmp_path / "target.md"
    target.write_text("not a brief", encoding="utf-8")
    link = tmp_path / "brief.md"
    link.symlink_to(target)
    with pytest.raises((BriefError, ValueError), match="symbolic link"):
        check_brief(
            link,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
        )


def test_reapproval_refuses_an_existing_malformed_approval(tmp_path, phil210, profile):
    path = brief_path(tmp_path, "essay-3")
    _approved(path, phil210, profile)
    text = path.read_text(encoding="utf-8").replace(
        "approval: Approve assignment brief for essay-3", "approval: yes"
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BriefError, match="Approve assignment brief for essay-3"):
        approve_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
            approval="Approve assignment brief for essay-3",
        )


def test_reapproval_after_a_deliberate_edit_updates_the_bound_body(
    tmp_path, phil210, profile
):
    path = brief_path(tmp_path, "essay-3")
    _approved(path, phil210, profile)
    before = check_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    )
    text = path.read_text(encoding="utf-8").replace(
        "Instructor answer.", "Revised instructor answer.", 1
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(BriefError, match="changed|stale"):
        check_brief(
            path,
            batch="essay-3",
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=profile,
        )
    reapproved = approve_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
        approval="Approve assignment brief for essay-3",
    )
    assert reapproved.body_sha256 != before.body_sha256
    assert check_brief(
        path,
        batch="essay-3",
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=profile,
    ) == reapproved


def test_brief_cli_prepares_approves_and_checks(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    _copy_profile(root, profile)
    common = _brief_args(root)

    prepared = _grade("brief", "prepare", *common, "--mode", "local")
    assert prepared.returncode == 0, prepared.stderr
    path = root / "assignments" / "essay-3.md"
    _answer(path)

    approved = _grade(
        "brief",
        "approve",
        *common,
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--approval",
        "Approve assignment brief for essay-3",
    )
    assert approved.returncode == 0, approved.stderr
    checked = _grade(
        "brief",
        "check",
        *common,
        "--rubric",
        str(phil210 / "grading_rubric.md"),
    )
    assert checked.returncode == 0, checked.stderr
    assert "approved assignment brief" in checked.stdout.lower()


def test_brief_cli_preserves_missing_profile_exit_10(tmp_path, phil210):
    root = tmp_path / ".grading"
    common = _brief_args(root)
    assert _grade("brief", "prepare", *common, "--mode", "local").returncode == 0
    _answer(root / "assignments" / "essay-3.md")

    result = _grade(
        "brief",
        "approve",
        *common,
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--approval",
        "Approve assignment brief for essay-3",
    )

    assert result.returncode == 10
    errors = _assignment_brief_errors(result.stderr)
    assert len(errors) == 1
    assert errors[0].startswith("ERROR assignment-brief:")


def test_brief_cli_preserves_rubric_drift_exit_11(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    _copy_profile(root, profile)
    common = _brief_args(root)
    assert _grade("brief", "prepare", *common, "--mode", "local").returncode == 0
    _answer(root / "assignments" / "essay-3.md")
    edited = tmp_path / "edited-rubric.md"
    edited.write_text(
        (phil210 / "grading_rubric.md")
        .read_text(encoding="utf-8")
        .replace("Formulates a sharp, defensible", "Formulates a clear"),
        encoding="utf-8",
    )

    result = _grade(
        "brief",
        "approve",
        *common,
        "--rubric",
        str(edited),
        "--approval",
        "Approve assignment brief for essay-3",
    )

    assert result.returncode == 11
    errors = _assignment_brief_errors(result.stderr)
    assert len(errors) == 1
    assert errors[0].startswith("ERROR assignment-brief:")


def test_brief_cli_refuses_a_stale_brief_with_exit_13(tmp_path, phil210, profile):
    root = tmp_path / ".grading"
    _copy_profile(root, profile)
    common = _brief_args(root)
    assert _grade("brief", "prepare", *common, "--mode", "local").returncode == 0
    path = root / "assignments" / "essay-3.md"
    _answer(path)
    assert _grade(
        "brief",
        "approve",
        *common,
        "--rubric",
        str(phil210 / "grading_rubric.md"),
        "--approval",
        "Approve assignment brief for essay-3",
    ).returncode == 0
    path.write_text(path.read_text(encoding="utf-8") + "changed\n", encoding="utf-8")

    result = _grade(
        "brief",
        "check",
        *common,
        "--rubric",
        str(phil210 / "grading_rubric.md"),
    )

    assert result.returncode == 13
    assert "ERROR assignment-brief:" in result.stderr


def test_brief_cli_refuses_duplicate_preparation(tmp_path):
    root = tmp_path / ".grading"
    common = _brief_args(root)
    assert _grade("brief", "prepare", *common, "--mode", "local").returncode == 0

    result = _grade("brief", "prepare", *common, "--mode", "local")

    assert result.returncode == 13
    assert "ERROR assignment-brief:" in result.stderr


def test_brief_cli_help_lists_every_action():
    result = _grade("brief", "--help")

    assert result.returncode == 0
    assert "prepare" in result.stdout
    assert "approve" in result.stdout
    assert "check" in result.stdout


def test_grade_cli_help_describes_navigation_and_repair_exit():
    result = _grade("--help")

    assert result.returncode == 0
    assert "Prepare one pending submission's draft pair." in result.stdout
    assert "Show the next legal grading action." in result.stdout
    assert (
        "12 a grading action is blocked or requested findings are present, including failed "
        "verification"
    ) in " ".join(result.stdout.split())
