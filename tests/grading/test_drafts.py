"""Draft preparation starts each submission from a valid paired template."""

import re
from dataclasses import replace
from pathlib import Path

import pytest

import scripts.grading.drafts as draft_module
from scripts.grading.density import PATTERNS_PATH, load_terms
from scripts.grading.drafts import (
    TEMPLATE_MARKER,
    DraftPreparationError,
    prepare_draft_pair,
)
from scripts.grading.records import GradeRecord
from scripts.grading.profile import load_profile
from scripts.grading.rubric import FeedbackProtocol, load_rubric
from scripts.grading.verify import errors, verify_draft


def test_prepare_draft_pair_writes_loadable_rubric_derived_files(
    tmp_path, phil210
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text(
        "Community solar needs a named causal mechanism.", encoding="utf-8"
    )
    drafts = tmp_path / "drafts"

    pair = prepare_draft_pair(
        batch="essay-2",
        course="phil210",
        source=source,
        rubric=load_rubric(phil210 / "grading_rubric.md"),
        profile=phil210 / "profile_phil210.md",
        patterns=PATTERNS_PATH,
        drafts=drafts,
    )

    record = GradeRecord.load(pair.record)
    assert pair.feedback == drafts / "STUDENT_01.md"
    assert [item.number for item in record.criteria] == [1, 2, 3, 4]
    assert all(item.assessable is False and item.score is None for item in record.criteria)
    assert record.pattern_density["words"] == 7
    assert TEMPLATE_MARKER in pair.feedback.read_text(encoding="utf-8")


@pytest.mark.parametrize("existing_suffix", [".json", ".md"])
def test_prepare_refuses_to_overwrite_either_member(
    tmp_path, phil210, existing_suffix
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    existing = drafts / f"STUDENT_01{existing_suffix}"
    existing.write_bytes(b"keep these exact bytes")

    with pytest.raises(DraftPreparationError):
        prepare_draft_pair(
            batch="essay-2",
            course="phil210",
            source=source,
            rubric=load_rubric(phil210 / "grading_rubric.md"),
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            drafts=drafts,
        )

    assert existing.read_bytes() == b"keep these exact bytes"
    other_suffix = ".md" if existing_suffix == ".json" else ".json"
    assert not (drafts / f"STUDENT_01{other_suffix}").exists()


def test_prepare_refuses_a_non_token_source(tmp_path, phil210):
    source = tmp_path / "redacted" / "paper.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")

    with pytest.raises(
        DraftPreparationError,
        match="STUDENT_NN or UNKNOWN_NN",
    ):
        prepare_draft_pair(
            batch="essay-2",
            course="phil210",
            source=source,
            rubric=load_rubric(phil210 / "grading_rubric.md"),
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            drafts=tmp_path / "drafts",
        )


def test_feedback_uses_exact_protocol_sections_and_signoff(tmp_path, phil210):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")
    rubric = load_rubric(phil210 / "grading_rubric.md")

    pair = prepare_draft_pair(
        batch="essay-2",
        course="phil210",
        source=source,
        rubric=rubric,
        profile=phil210 / "profile_phil210.md",
        patterns=PATTERNS_PATH,
        drafts=tmp_path / "drafts",
    )

    feedback = pair.feedback.read_text(encoding="utf-8")
    headings = re.findall(r"^\*\*(.+?)\*\*$", feedback, re.MULTILINE)
    assert headings == list(rubric.protocol.sections)
    assert feedback.splitlines()[0] == f"[{TEMPLATE_MARKER}]"
    assert feedback.rstrip().splitlines()[-1] == rubric.protocol.sign_offs[0]


def test_unchanged_feedback_marker_is_a_blocking_verification_finding(
    tmp_path, phil210
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source_text = "A safe redacted essay."
    source.write_text(source_text, encoding="utf-8")
    rubric = load_rubric(phil210 / "grading_rubric.md")
    pair = prepare_draft_pair(
        batch="essay-2",
        course="phil210",
        source=source,
        rubric=rubric,
        profile=phil210 / "profile_phil210.md",
        patterns=PATTERNS_PATH,
        drafts=tmp_path / "drafts",
    )

    findings = verify_draft(
        GradeRecord.load(pair.record),
        pair.feedback.read_text(encoding="utf-8"),
        source_text,
        rubric,
        load_profile(phil210 / "profile_phil210.md"),
        load_terms(PATTERNS_PATH),
    )

    assert "template-marker" in {finding.code for finding in errors(findings)}


@pytest.mark.parametrize("linked_path", ["source", "drafts"])
def test_prepare_refuses_source_and_destination_symlinks(
    tmp_path, phil210, linked_path
):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    real_source = redacted / "real.md"
    real_source.write_text("A safe redacted essay.", encoding="utf-8")
    source = redacted / "STUDENT_01.md"
    drafts = tmp_path / "drafts"
    if linked_path == "source":
        source.symlink_to(real_source)
    else:
        source.write_text("A safe redacted essay.", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        drafts.symlink_to(outside, target_is_directory=True)

    with pytest.raises(DraftPreparationError, match="symbolic link"):
        prepare_draft_pair(
            batch="essay-2",
            course="phil210",
            source=source,
            rubric=load_rubric(phil210 / "grading_rubric.md"),
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            drafts=drafts,
        )


def test_prepare_removes_the_first_member_when_the_second_install_fails(
    tmp_path, phil210, monkeypatch
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")
    drafts = tmp_path / "drafts"
    real_install = draft_module._install_without_overwrite
    calls = 0

    def fail_second_install(staged, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("synthetic second install failure")
        real_install(staged, destination)

    monkeypatch.setattr(
        draft_module, "_install_without_overwrite", fail_second_install
    )

    with pytest.raises(DraftPreparationError, match="second install failure"):
        prepare_draft_pair(
            batch="essay-2",
            course="phil210",
            source=source,
            rubric=load_rubric(phil210 / "grading_rubric.md"),
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            drafts=drafts,
        )

    assert list(drafts.iterdir()) == []


def test_second_install_and_rollback_unlink_failures_raise_one_public_error(
    tmp_path, phil210, monkeypatch
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")
    drafts = tmp_path / "drafts"
    record = drafts / "STUDENT_01.json"
    real_install = draft_module._install_without_overwrite
    real_unlink = Path.unlink
    installs = 0

    def fail_second_install(staged, destination):
        nonlocal installs
        installs += 1
        if installs == 2:
            raise OSError("synthetic second install failure")
        real_install(staged, destination)

    def refuse_record_rollback(path, *args, **kwargs):
        if path == record:
            raise OSError("synthetic rollback unlink failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(
        draft_module, "_install_without_overwrite", fail_second_install
    )
    monkeypatch.setattr(Path, "unlink", refuse_record_rollback)

    with pytest.raises(DraftPreparationError) as raised:
        prepare_draft_pair(
            batch="essay-2",
            course="phil210",
            source=source,
            rubric=load_rubric(phil210 / "grading_rubric.md"),
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            drafts=drafts,
        )

    assert "second install failure" in str(raised.value)
    assert "rollback unlink failure" in str(raised.value)
    assert str(record) in str(raised.value)
    assert record.is_file()
    assert not (drafts / "STUDENT_01.md").exists()
    assert not list(drafts.glob(".draft-pair.*"))


def test_staging_cleanup_failure_rolls_back_both_installed_destinations(
    tmp_path, phil210, monkeypatch
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")
    drafts = tmp_path / "drafts"
    real_unlink = Path.unlink
    blocked_stage = None

    def refuse_one_stage(path, *args, **kwargs):
        nonlocal blocked_stage
        if path.parent == drafts and path.name.startswith(".draft-pair."):
            if blocked_stage is None:
                blocked_stage = path
            if path == blocked_stage:
                raise OSError("synthetic staging cleanup failure")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse_one_stage)

    with pytest.raises(DraftPreparationError) as raised:
        prepare_draft_pair(
            batch="essay-2",
            course="phil210",
            source=source,
            rubric=load_rubric(phil210 / "grading_rubric.md"),
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
            drafts=drafts,
        )

    assert blocked_stage is not None
    assert "staging cleanup failure" in str(raised.value)
    assert str(blocked_stage) in str(raised.value)
    assert not (drafts / "STUDENT_01.json").exists()
    assert not (drafts / "STUDENT_01.md").exists()
    assert [path for path in drafts.iterdir()] == [blocked_stage]


def test_feedback_without_protocol_sections_keeps_annotation_scaffolding(
    tmp_path, phil210
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("A safe redacted essay.", encoding="utf-8")
    rubric = replace(
        load_rubric(phil210 / "grading_rubric.md"),
        protocol=FeedbackProtocol(
            sections=(),
            sign_offs=("— Instructor",),
            requires_inline_annotations=True,
        ),
    )

    pair = prepare_draft_pair(
        batch="essay-2",
        course="phil210",
        source=source,
        rubric=rubric,
        profile=phil210 / "profile_phil210.md",
        patterns=PATTERNS_PATH,
        drafts=tmp_path / "drafts",
    )

    feedback = pair.feedback.read_text(encoding="utf-8")
    assert feedback.startswith(f"[{TEMPLATE_MARKER}]\n\n**Feedback**\n")
    assert feedback.count(TEMPLATE_MARKER) == 2
