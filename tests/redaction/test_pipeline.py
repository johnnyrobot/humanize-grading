"""The pipeline must produce redacted output and fail closed."""

import json
import os
import tempfile

import pytest

import scripts.redact as redact_cli
from scripts.redaction.pipeline import (
    EXIT_CANDIDATES,
    EXIT_COLLISION,
    EXIT_DESTINATION_NOT_EMPTY,
    EXIT_OK,
    EXIT_ROSTER,
    EXIT_UNSAFE_PATH,
    EXIT_UNREADABLE,
    run,
)
from scripts.redaction.manifest import RedactionManifest
from scripts.redaction.roster import RosterEntry
from scripts.redaction.tokens import TokenMap


def _write(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


def _write_schema_one_manifest(directory):
    output = directory / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2", "run-identifier-1234", [output], reviewed=True, exit_code=0
    ).save(directory)
    path = directory / "_redaction.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["schema"] = "redaction-batch/1"
    payload.pop("reviewed")
    payload.pop("exit_code")
    path.write_text(json.dumps(payload), encoding="utf-8")
    return directory


def test_manifest_round_trip_persists_reviewed_state_and_exit_code(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "STUDENT_01.md").write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2", "run-identifier-1234", [redacted / "STUDENT_01.md"],
        reviewed=True,
        exit_code=2,
    ).save(redacted)

    loaded = RedactionManifest.load(redacted)

    assert loaded.reviewed is True
    assert loaded.exit_code == 2
    assert (
        json.loads((redacted / "_redaction.json").read_text(encoding="utf-8"))["schema"]
        == "redaction-batch/2"
    )


def test_legacy_manifest_is_treated_as_unreviewed(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    loaded = RedactionManifest.load(_write_schema_one_manifest(redacted))

    assert loaded.reviewed is False
    assert loaded.exit_code is None


@pytest.mark.parametrize("exit_code", [True, "2", None])
def test_schema_two_manifest_requires_an_integer_exit_code(tmp_path, exit_code):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    output = redacted / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")

    with pytest.raises(ValueError, match="integer exit code"):
        RedactionManifest.create(
            "essay-2",
            "run-identifier-1234",
            [output],
            reviewed=True,
            exit_code=exit_code,
        )


def test_schema_two_save_refuses_a_legacy_manifest_without_an_exit_code(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    legacy = RedactionManifest.load(_write_schema_one_manifest(redacted))

    with pytest.raises(ValueError, match="integer exit code"):
        legacy.save(redacted)


@pytest.mark.parametrize("linked", ["destination", "manifest"])
def test_invalidation_never_follows_a_symbolic_link(tmp_path, linked):
    real = tmp_path / "real-redacted"
    real.mkdir()
    output = real / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(real)
    external_manifest = real / "_redaction.json"
    before = external_manifest.read_bytes()

    if linked == "destination":
        destination = tmp_path / "linked-redacted"
        destination.symlink_to(real, target_is_directory=True)
    else:
        destination = tmp_path / "linked-manifest-redacted"
        destination.mkdir()
        (destination / "_redaction.json").symlink_to(external_manifest)

    changed = RedactionManifest.invalidate_existing(
        destination,
        batch="essay-2",
        exit_code=EXIT_UNREADABLE,
    )

    assert changed is False
    assert external_manifest.read_bytes() == before


def test_invalidation_refuses_a_hard_linked_regular_manifest(tmp_path):
    real = tmp_path / "real-redacted"
    real.mkdir()
    output = real / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(real)
    external_manifest = real / "_redaction.json"
    before = external_manifest.read_bytes()
    destination = tmp_path / "hard-linked-redacted"
    destination.mkdir()
    os.link(external_manifest, destination / "_redaction.json")

    with pytest.raises(RuntimeError, match="single link"):
        RedactionManifest.invalidate_existing(
            destination,
            batch="essay-2",
            exit_code=EXIT_UNREADABLE,
        )

    assert external_manifest.read_bytes() == before


def test_read_only_destination_uses_descriptor_bound_invalidation(tmp_path):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    destination.chmod(0o555)
    try:
        changed = RedactionManifest.invalidate_existing(
            destination,
            batch="essay-2",
            exit_code=EXIT_UNREADABLE,
        )
        current = RedactionManifest.load(destination)
    finally:
        destination.chmod(0o755)

    assert changed is True
    assert current.reviewed is False
    assert current.exit_code == EXIT_UNREADABLE
    assert (destination / "_redaction.json").stat().st_mode & 0o777 == 0o600


def test_invalidation_refuses_a_manifest_replaced_after_open(
    tmp_path, monkeypatch
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    manifest = destination / "_redaction.json"
    replacement_bytes = b"replacement must stay untouched"
    replacement = destination / "replacement"
    replacement.write_bytes(replacement_bytes)
    original_replace = os.replace

    def replace_then_fail(source, target):
        original_replace(replacement, target)
        raise OSError("synthetic atomic replacement failure")

    monkeypatch.setattr(os, "replace", replace_then_fail)

    with pytest.raises(RuntimeError, match="manifest changed during invalidation"):
        RedactionManifest.invalidate_existing(
            destination,
            batch="essay-2",
            exit_code=EXIT_UNREADABLE,
        )

    assert manifest.read_bytes() == replacement_bytes


def test_cli_stops_when_manifest_is_replaced_before_atomic_write(
    tmp_path, monkeypatch, capsys
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    manifest = destination / "_redaction.json"
    replacement_bytes = b"replacement must stay untouched"
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(replacement_bytes)
    original_mkstemp = tempfile.mkstemp
    injected = False
    roster_loaded = False

    def replace_then_stage(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            os.replace(replacement, manifest)
        return original_mkstemp(*args, **kwargs)

    def load_roster(path):
        nonlocal roster_loaded
        roster_loaded = True
        return []

    monkeypatch.setattr(tempfile, "mkstemp", replace_then_stage)
    monkeypatch.setattr(redact_cli, "load_roster", load_roster)

    result = redact_cli.main(
        [
            str(tmp_path / "submissions"),
            "--roster",
            str(tmp_path / "roster.csv"),
            "--batch",
            "essay-2",
            "--out",
            str(destination),
            "--map",
            str(tmp_path / "map.json"),
        ]
    )
    captured = capsys.readouterr()

    assert result == EXIT_UNSAFE_PATH
    assert roster_loaded is False
    assert "manifest changed during invalidation" in captured.err
    assert manifest.read_bytes() == replacement_bytes
    assert list(destination.glob("._redaction.json.*.tmp")) == []


def test_pipeline_stops_when_manifest_gains_a_hard_link_before_atomic_write(
    tmp_path, monkeypatch
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    manifest = destination / "_redaction.json"
    before = manifest.read_bytes()
    hard_link = tmp_path / "hard-linked-manifest.json"
    original_mkstemp = tempfile.mkstemp
    injected = False

    def link_then_stage(*args, **kwargs):
        nonlocal injected
        if not injected:
            injected = True
            os.link(manifest, hard_link)
        return original_mkstemp(*args, **kwargs)

    def fail_fallback(*args, **kwargs):
        raise AssertionError("an identity mismatch must not use the fallback")

    monkeypatch.setattr(tempfile, "mkstemp", link_then_stage)
    monkeypatch.setattr(RedactionManifest, "_invalidate_in_place", fail_fallback)
    source = tmp_path / "too-many"
    source.mkdir()
    for index in range(501):
        (source / f"paper-{index:03d}.md").write_text("safe", encoding="utf-8")

    result = run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=[],
        batch="essay-2",
        force=True,
    )

    assert result.exit_code == EXIT_UNSAFE_PATH
    assert "manifest changed during invalidation" in result.message
    assert manifest.read_bytes() == before
    assert hard_link.read_bytes() == before
    assert list(destination.glob("._redaction.json.*.tmp")) == []


def test_invalidation_atomically_replaces_an_unchanged_manifest(tmp_path):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    manifest = destination / "_redaction.json"
    original_inode = manifest.stat().st_ino

    changed = RedactionManifest.invalidate_existing(
        destination,
        batch="essay-2",
        exit_code=EXIT_UNREADABLE,
    )
    current = RedactionManifest.load(destination)

    assert changed is True
    assert current.reviewed is False
    assert current.exit_code == EXIT_UNREADABLE
    assert manifest.stat().st_ino != original_inode
    assert list(destination.glob("._redaction.json.*.tmp")) == []


def test_invalidation_uses_bound_descriptor_after_atomic_replace_fails(
    tmp_path, monkeypatch
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    manifest = destination / "_redaction.json"
    original_inode = manifest.stat().st_ino

    def fail_replace(source, target):
        raise OSError("synthetic atomic replace failure")

    monkeypatch.setattr(os, "replace", fail_replace)

    changed = RedactionManifest.invalidate_existing(
        destination,
        batch="essay-2",
        exit_code=EXIT_UNREADABLE,
    )
    current = RedactionManifest.load(destination)

    assert changed is True
    assert current.reviewed is False
    assert current.exit_code == EXIT_UNREADABLE
    assert manifest.stat().st_ino == original_inode
    assert list(destination.glob("._redaction.json.*.tmp")) == []


def test_cli_stops_before_roster_when_both_invalidation_writes_fail(
    tmp_path, monkeypatch, capsys
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)

    def fail_atomic(source, target):
        raise OSError("synthetic atomic failure")

    def fail_in_place(descriptor, data):
        raise OSError("synthetic in-place failure")

    def fail_roster(path):
        raise AssertionError("roster loading must not run")

    monkeypatch.setattr(os, "replace", fail_atomic)
    monkeypatch.setattr(os, "write", fail_in_place)
    monkeypatch.setattr(redact_cli, "load_roster", fail_roster)

    result = redact_cli.main(
        [
            str(tmp_path / "submissions"),
            "--roster",
            str(tmp_path / "roster.csv"),
            "--batch",
            "essay-2",
            "--out",
            str(destination),
            "--map",
            str(tmp_path / "map.json"),
        ]
    )
    captured = capsys.readouterr()

    assert result == EXIT_UNSAFE_PATH
    assert "Could not invalidate prior redaction state" in captured.err
    assert "Traceback" not in captured.err
    with pytest.raises((ValueError, json.JSONDecodeError)):
        RedactionManifest.load(destination)


def test_pipeline_returns_unsafe_when_both_invalidation_writes_fail(
    tmp_path, monkeypatch
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    source = tmp_path / "too-many"
    source.mkdir()
    for index in range(501):
        (source / f"paper-{index:03d}.md").write_text("safe", encoding="utf-8")

    monkeypatch.setattr(
        os,
        "replace",
        lambda source, target: (_ for _ in ()).throw(
            OSError("synthetic atomic failure")
        ),
    )
    monkeypatch.setattr(
        os,
        "write",
        lambda descriptor, data: (_ for _ in ()).throw(
            OSError("synthetic in-place failure")
        ),
    )

    result = run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=[],
        batch="essay-2",
        force=True,
    )

    assert result.exit_code == EXIT_UNSAFE_PATH
    assert "Could not invalidate prior redaction state" in result.message


def test_invalidation_without_an_existing_manifest_is_a_noop(tmp_path):
    destination = tmp_path / "redacted"
    destination.mkdir()

    assert (
        RedactionManifest.invalidate_existing(
            destination,
            batch="essay-2",
            exit_code=EXIT_UNREADABLE,
        )
        is False
    )


@pytest.mark.parametrize("reviewed", [False, True])
def test_pipeline_persists_the_instructor_review_decision(tmp_path, reviewed):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")

    run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
        reviewed=reviewed,
    )

    assert RedactionManifest.load(tmp_path / "out").reviewed is reviewed


def test_a_clean_batch_exits_zero(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_OK


def test_output_is_named_by_token_and_holds_no_name(tmp_path):
    source = tmp_path / "in"
    _write(source, "lin_maya_essay.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    written = [p for p in (tmp_path / "out").iterdir() if not p.name.startswith("_")]
    assert [p.name for p in written] == ["STUDENT_01.md"]
    assert "Maya" not in written[0].read_text(encoding="utf-8")
    assert result.exit_code == EXIT_OK


@pytest.mark.parametrize("written_name", ["maya lin", "MAYA LIN", "Maya lin"])
def test_case_variants_of_roster_names_never_survive_a_clean_batch(
    tmp_path, written_name
):
    source = tmp_path / "in"
    _write(
        source,
        "paper.md",
        f"Student: {written_name}\nEmail: mlin@example.edu\nThe argument holds.",
    )

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")],
        batch="essay-1",
    )

    output = (tmp_path / "out" / "STUDENT_01.md").read_text(encoding="utf-8")
    assert written_name not in output
    assert result.files[0].token == "STUDENT_01"
    assert result.exit_code == EXIT_OK


def test_a_missing_roster_student_fails_closed(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin"), RosterEntry("Tyler Brooks")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_ROSTER
    assert [e.name for e in result.unmatched_roster] == ["Tyler Brooks"]


def test_reviewed_roster_failure_persists_the_final_exit_code(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin"), RosterEntry("Tyler Brooks")],
        batch="essay-1",
        reviewed=True,
    )

    manifest = RedactionManifest.load(tmp_path / "out")
    assert result.exit_code == EXIT_ROSTER
    assert manifest.reviewed is True
    assert manifest.exit_code == EXIT_ROSTER


def test_unreviewed_candidates_fail_closed(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin wrote this. My brother Daniel drove me.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_CANDIDATES
    # The review file holds unredacted candidate names, so it must never sit
    # inside the redacted output directory the model reads from.
    review = json.loads(
        (tmp_path / "reviews" / "essay-1.json").read_text(encoding="utf-8")
    )
    assert review["candidates"][0]["name"] == "Daniel"
    assert not (tmp_path / "out" / "_review.json").exists()


def test_reviewed_candidates_pass(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin wrote this. My brother Daniel drove me.")
    common = {
        "source": source,
        "destination": tmp_path / "out",
        "map_path": tmp_path / "map.json",
        "roster": [RosterEntry("Maya Lin")],
        "batch": "essay-1",
    }
    run(**common)
    # A second run into the same destination now needs force=True -- see
    # test_a_non_empty_destination_is_refused -- this test is about
    # `reviewed`, not staleness, so it opts back in explicitly.
    assert run(**common, reviewed=True, force=True).exit_code == EXIT_OK


def test_an_unreadable_file_fails_closed(tmp_path):
    source = tmp_path / "in"
    source.mkdir(parents=True)
    (source / "paper.pdf").write_bytes(b"%PDF-1.4")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_UNREADABLE


def test_submission_symlinks_are_refused(tmp_path):
    outside = tmp_path / "private.md"
    outside.write_text("Maya Lin secret", encoding="utf-8")
    source = tmp_path / "in"
    source.mkdir()
    (source / "paper.md").symlink_to(outside)

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNREADABLE
    assert not list((tmp_path / "out").glob("*.md"))


def test_a_batch_with_more_than_five_hundred_files_is_refused_before_reading(
    tmp_path,
):
    source = tmp_path / "in"
    source.mkdir()
    for index in range(501):
        (source / f"paper-{index:03d}.md").touch()

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNREADABLE
    assert "500 files" in result.message
    assert not (tmp_path / "out").exists()


def test_the_map_is_written_owner_only(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    map_path = tmp_path / "map.json"
    run(
        source=source,
        destination=tmp_path / "out",
        map_path=map_path,
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert map_path.stat().st_mode & 0o777 == 0o600


def test_the_docx_author_is_tokenized_not_dropped(tmp_path):
    """The author field is the most commonly missed leak."""
    docx = pytest.importorskip("docx")
    source = tmp_path / "in"
    source.mkdir(parents=True)
    document = docx.Document()
    document.add_paragraph("Platforms curate what audiences see.")
    document.core_properties.author = "Maya Lin"
    document.save(str(source / "paper.docx"))

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    written = [p for p in (tmp_path / "out").iterdir() if not p.name.startswith("_")]
    assert len(written) == 1
    assert "Maya" not in written[0].read_text(encoding="utf-8")
    assert result.files[0].token == "STUDENT_01"


def test_a_non_empty_destination_is_refused(tmp_path):
    """A second run must never leave stale output mixed with current output."""
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    common = {
        "source": source,
        "destination": tmp_path / "out",
        "map_path": tmp_path / "map.json",
        "roster": [RosterEntry("Maya Lin")],
        "batch": "essay-1",
    }
    run(**common)
    before = sorted(p.name for p in (tmp_path / "out").iterdir())

    result = run(**common)

    assert result.exit_code == EXIT_DESTINATION_NOT_EMPTY
    assert RedactionManifest.load(tmp_path / "out").exit_code == (
        EXIT_DESTINATION_NOT_EMPTY
    )
    after = sorted(p.name for p in (tmp_path / "out").iterdir())
    assert after == before


def test_force_clears_stale_token_outputs(tmp_path):
    """Three submissions, then one, with force: no stale file may survive."""
    source = tmp_path / "in"
    _write(source, "lin.md", "Maya Lin argues the network effect matters most.")
    _write(source, "brooks.md", "Tyler Brooks argues regulation is the only lever.")
    _write(source, "reed.md", "Jordan Reed argues decentralization solves nothing.")
    destination = tmp_path / "out"
    run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=[
            RosterEntry("Maya Lin"),
            RosterEntry("Tyler Brooks"),
            RosterEntry("Jordan Reed"),
        ],
        batch="essay-1",
    )
    assert sorted(p.name for p in destination.iterdir() if not p.name.startswith("_")) == [
        "STUDENT_01.md",
        "STUDENT_02.md",
        "STUDENT_03.md",
    ]

    source2 = tmp_path / "in2"
    _write(source2, "bennett.md", "Chloe Bennett argues infrastructure debt is underrated.")
    result = run(
        source=source2,
        destination=destination,
        map_path=tmp_path / "map2.json",
        roster=[RosterEntry("Chloe Bennett")],
        batch="essay-2",
        force=True,
    )

    written = sorted(p.name for p in destination.iterdir() if not p.name.startswith("_"))
    assert written == ["STUDENT_01.md"]
    content = (destination / "STUDENT_01.md").read_text(encoding="utf-8")
    assert "infrastructure debt" in content
    assert "network effect" not in content
    assert result.exit_code == EXIT_OK


def test_force_leaves_unrelated_files_alone(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    destination = tmp_path / "out"
    destination.mkdir(parents=True)
    (destination / "notes.txt").write_text("instructor notes", encoding="utf-8")

    run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
        force=True,
    )

    assert (destination / "notes.txt").read_text(encoding="utf-8") == "instructor notes"


def test_an_empty_destination_needs_no_force(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    destination = tmp_path / "out"
    destination.mkdir(parents=True)  # exists, but empty

    result = run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_OK


@pytest.mark.parametrize("private_name", ["map.json", "review.json"])
def test_private_state_cannot_be_written_inside_redacted_output(
    tmp_path, private_name
):
    source = tmp_path / "in"
    destination = tmp_path / "out"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")
    private_path = destination / private_name

    result = run(
        source=source,
        destination=destination,
        map_path=private_path if private_name == "map.json" else tmp_path / "map.json",
        review_path=(
            private_path if private_name == "review.json" else tmp_path / "review.json"
        ),
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNSAFE_PATH
    assert not private_path.exists()


def test_two_sources_cannot_overwrite_one_students_redacted_submission(tmp_path):
    source = tmp_path / "in"
    _write(source, "first.md", "Maya Lin argues the first position.")
    _write(source, "second.md", "Maya Lin argues the second position.")

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_COLLISION
    assert "duplicate output" in result.message.lower()


def test_one_student_cannot_split_across_extensions(tmp_path):
    source = tmp_path / "in"
    _write(source, "first.md", "Maya Lin argues the first position.")
    _write(source, "second.txt", "Maya Lin argues the second position.")

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_COLLISION


def test_authorship_does_not_depend_on_roster_order(tmp_path):
    """The author is whoever is named first in the file, not CSV row order."""
    source = tmp_path / "in"
    _write(source, "paper.md", "Student: Zoe Chen. My classmate Amy Adams disagreed.")

    for i, roster in enumerate(
        [
            [RosterEntry("Amy Adams"), RosterEntry("Zoe Chen")],
            [RosterEntry("Zoe Chen"), RosterEntry("Amy Adams")],
        ]
    ):
        map_path = tmp_path / f"map-{i}.json"
        result = run(
            source=source,
            destination=tmp_path / f"out-{i}",
            map_path=map_path,
            roster=roster,
            batch="essay-1",
        )
        tmap = TokenMap.load(map_path)
        zoe_token = next(
            token
            for token, e in tmap.tokens().items()
            if e.kind == "STUDENT" and e.value == "Zoe Chen"
        )
        assert result.files[0].token == zoe_token, f"roster order {i} attributed wrong"


def test_a_file_naming_two_students_is_reported_as_ambiguous(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Student: Zoe Chen. My classmate Amy Adams disagreed.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Amy Adams"), RosterEntry("Zoe Chen")],
        batch="essay-1",
    )
    review = json.loads(
        (tmp_path / "reviews" / "essay-1.json").read_text(encoding="utf-8")
    )
    [ambiguous] = review["ambiguous_authorship"]
    assert set(ambiguous["named"]) == {"Amy Adams", "Zoe Chen"}
    assert ambiguous["token"] == result.files[0].token
    assert result.exit_code == EXIT_OK


def test_skipped_empty_is_reported(tmp_path):
    """A blank submission is skipped and reported, never silently graded."""
    source = tmp_path / "in"
    _write(source, "blank.md", "   \n\n  ")
    _write(source, "real.md", "Maya Lin argues that platforms curate.")
    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )
    assert result.exit_code == EXIT_OK
    written = [
        p.name for p in (tmp_path / "out").iterdir() if not p.name.startswith("_")
    ]
    assert written == ["STUDENT_01.md"]
    review = json.loads(
        (tmp_path / "reviews" / "essay-1.json").read_text(encoding="utf-8")
    )
    assert review["skipped_empty"] == ["blank.md"]
