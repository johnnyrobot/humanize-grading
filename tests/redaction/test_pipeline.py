"""The pipeline must produce redacted output and fail closed."""

import json
import os
import tempfile
from pathlib import Path

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

TEST_MAP_SHA256 = "62d325824da560ffec8bc44d147e265e4ae1b77656cfde632b72c7a633f711e7"
TEST_REVIEW_SHA256 = "31f937c105ebe8e03a0dcd1396a5b3cd3675fdc257cdd5bc5fd8598ab130de7b"


def _create_manifest(*args, **kwargs):
    kwargs.setdefault("map_sha256", TEST_MAP_SHA256)
    kwargs.setdefault("review_sha256", TEST_REVIEW_SHA256)
    return RedactionManifest.create(*args, **kwargs)


def _write(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


def _write_schema_one_manifest(directory):
    output = directory / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    _create_manifest(
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
    _create_manifest(
        "essay-2", "run-identifier-1234", [redacted / "STUDENT_01.md"],
        reviewed=True,
        exit_code=2,
    ).save(redacted)

    loaded = RedactionManifest.load(redacted)

    assert loaded.reviewed is True
    assert loaded.exit_code == 2
    assert (
        json.loads((redacted / "_redaction.json").read_text(encoding="utf-8"))["schema"]
        == "redaction-batch/3"
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
        _create_manifest(
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

    with pytest.raises(ValueError, match="current redaction manifest schema"):
        legacy.save(redacted)


@pytest.mark.parametrize("linked", ["destination", "manifest"])
def test_invalidation_never_follows_a_symbolic_link(tmp_path, linked):
    real = tmp_path / "real-redacted"
    real.mkdir()
    output = real / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    _create_manifest(
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
    _create_manifest(
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
    _create_manifest(
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
    _create_manifest(
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
    _create_manifest(
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
    _create_manifest(
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
    _create_manifest(
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


def test_direct_pipeline_invalidates_clean_state_before_source_traversal(
    tmp_path, monkeypatch
):
    source = tmp_path / "submissions"
    source.mkdir()
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    _create_manifest(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
    ).save(destination)
    original_rglob = Path.rglob

    def interrupt_during_traversal(path, pattern):
        if path == source:
            current = RedactionManifest.load(destination)
            assert current.reviewed is False
            assert current.exit_code == EXIT_UNREADABLE
            raise RuntimeError("synthetic interruption during traversal")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", interrupt_during_traversal)

    with pytest.raises(RuntimeError, match="synthetic interruption"):
        run(
            source=source,
            destination=destination,
            map_path=tmp_path / "map.json",
            roster=[],
            batch="essay-2",
            force=True,
        )

    current = RedactionManifest.load(destination)
    assert current.reviewed is False
    assert current.exit_code == EXIT_UNREADABLE


def test_invalidation_uses_bound_descriptor_after_atomic_replace_fails(
    tmp_path, monkeypatch
):
    destination = tmp_path / "redacted"
    destination.mkdir()
    output = destination / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    _create_manifest(
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
    _create_manifest(
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
    _create_manifest(
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


def test_pipeline_stays_unreviewed_until_exact_approval(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")

    run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin")],
        batch="essay-1",
    )

    assert RedactionManifest.load(tmp_path / "out").reviewed is False


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


def test_roster_failure_cannot_be_approved(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Maya Lin argues that platforms curate.")

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin"), RosterEntry("Tyler Brooks")],
        batch="essay-1",
    )

    manifest = RedactionManifest.load(tmp_path / "out")
    assert result.exit_code == EXIT_ROSTER
    assert manifest.reviewed is False
    assert manifest.exit_code == EXIT_ROSTER

    approval = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        roster=[RosterEntry("Maya Lin"), RosterEntry("Tyler Brooks")],
        batch="essay-1",
        reviewed=True,
    )
    assert approval.exit_code == EXIT_CANDIDATES
    assert RedactionManifest.load(tmp_path / "out").reviewed is False


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


def test_non_roster_docx_metadata_stays_out_of_model_visible_output(tmp_path):
    docx = pytest.importorskip("docx")
    source = tmp_path / "in"
    source.mkdir()
    document = docx.Document()
    document.add_paragraph("Platforms curate public attention.")
    document.core_properties.author = "Jane Smith"
    document.save(str(source / "paper.docx"))
    review_path = tmp_path / "review.json"

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        review_path=review_path,
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_OK
    [output] = [p for p in (tmp_path / "out").iterdir() if not p.name.startswith("_")]
    assert "Jane Smith" not in output.read_text(encoding="utf-8")
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["document_metadata"] == [
        {"file": "UNKNOWN_01.docx", "value": "Jane Smith"}
    ]


def test_protected_and_citation_suppressed_names_reach_private_review(tmp_path):
    source = tmp_path / "in"
    _write(
        source,
        "paper.md",
        "Dr. Marcus Vance graded it. Gillespie (2014) frames algorithms. "
        "I find Gillespie's framing useful.",
    )
    review_path = tmp_path / "review.json"

    run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        review_path=review_path,
        roster=[],
        batch="essay-1",
        protected=("Marcus Vance",),
    )

    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert {(item["name"], item["reason"]) for item in review["excluded_names"]} == {
        ("Marcus Vance", "protected"),
        ("Gillespie", "citation"),
    }


def test_redacted_outputs_are_owner_only_even_when_review_is_required(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "My brother Daniel drove me to the hearing.")
    old_umask = os.umask(0o022)
    try:
        result = run(
            source=source,
            destination=tmp_path / "out",
            map_path=tmp_path / "map.json",
            roster=[],
            batch="essay-1",
        )
    finally:
        os.umask(old_umask)

    assert result.exit_code == EXIT_CANDIDATES
    [output] = [p for p in (tmp_path / "out").iterdir() if not p.name.startswith("_")]
    assert output.stat().st_mode & 0o777 == 0o600


def test_cli_reports_only_the_unmatched_roster_count(tmp_path, capsys):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    roster = tmp_path / "roster.csv"
    roster.write_text(
        "name,student_id,email\nMaya Lin,S1000431,mlin@example.edu\n",
        encoding="utf-8",
    )

    exit_code = redact_cli.main(
        [
            str(source),
            "--roster",
            str(roster),
            "--batch",
            "essay-1",
            "--out",
            str(tmp_path / "out"),
            "--map",
            str(tmp_path / "map.json"),
            "--review",
            str(tmp_path / "review.json"),
        ]
    )
    captured = capsys.readouterr()

    assert exit_code == EXIT_ROSTER
    assert "Maya Lin" not in captured.out
    assert "1 unmatched roster entry" in captured.out
    review = json.loads((tmp_path / "review.json").read_text(encoding="utf-8"))
    assert review["unmatched_roster"] == [
        {
            "name": "Maya Lin",
            "student_id": "S1000431",
            "email": "mlin@example.edu",
        }
    ]


def test_direct_reviewed_run_without_an_existing_exact_review_fails_closed(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        review_path=tmp_path / "review.json",
        roster=[],
        batch="essay-1",
        reviewed=True,
    )

    assert result.exit_code == EXIT_CANDIDATES
    assert not (tmp_path / "out" / "_redaction.json").exists()


def test_review_approval_preserves_and_binds_the_exact_existing_run(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    destination = tmp_path / "out"
    map_path = tmp_path / "map.json"
    review_path = tmp_path / "review.json"
    common = {
        "source": source,
        "destination": destination,
        "map_path": map_path,
        "review_path": review_path,
        "roster": [],
        "batch": "essay-1",
    }
    assert run(**common).exit_code == EXIT_OK
    before = RedactionManifest.load(destination)
    map_bytes = map_path.read_bytes()
    review_bytes = review_path.read_bytes()

    approved = run(**common, reviewed=True)
    after = RedactionManifest.load(destination)

    assert approved.exit_code == EXIT_OK
    assert after.reviewed is True
    assert after.run_id == before.run_id
    assert after.outputs == before.outputs
    assert map_path.read_bytes() == map_bytes
    assert review_path.read_bytes() == review_bytes
    assert after.map_sha256 == __import__("hashlib").sha256(map_bytes).hexdigest()
    assert after.review_sha256 == __import__("hashlib").sha256(review_bytes).hexdigest()


def test_review_approval_rejects_a_changed_source(tmp_path):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    destination = tmp_path / "out"
    common = {
        "source": source,
        "destination": destination,
        "map_path": tmp_path / "map.json",
        "review_path": tmp_path / "review.json",
        "roster": [],
        "batch": "essay-1",
    }
    run(**common)
    (source / "paper.md").write_text(
        "My brother Daniel drove me to the hearing.", encoding="utf-8"
    )

    result = run(**common, reviewed=True)

    assert result.exit_code == EXIT_CANDIDATES
    assert RedactionManifest.load(destination).reviewed is False


@pytest.mark.parametrize("artifact", ["map", "review", "output"])
def test_review_approval_rejects_changed_bound_artifacts(tmp_path, artifact):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    destination = tmp_path / "out"
    map_path = tmp_path / "map.json"
    review_path = tmp_path / "review.json"
    common = {
        "source": source,
        "destination": destination,
        "map_path": map_path,
        "review_path": review_path,
        "roster": [],
        "batch": "essay-1",
    }
    run(**common)
    targets = {
        "map": map_path,
        "review": review_path,
        "output": next(
            path for path in destination.iterdir() if not path.name.startswith("_")
        ),
    }
    targets[artifact].write_text(
        targets[artifact].read_text(encoding="utf-8") + "changed",
        encoding="utf-8",
    )

    result = run(**common, reviewed=True)

    assert result.exit_code == EXIT_CANDIDATES
    assert RedactionManifest.load(destination).reviewed is False


def test_map_and_review_are_written_before_the_final_manifest(tmp_path, monkeypatch):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    destination = tmp_path / "out"
    map_path = tmp_path / "map.json"
    review_path = tmp_path / "review.json"

    def fail_review(*args, **kwargs):
        raise OSError("synthetic review write failure")

    monkeypatch.setattr("scripts.redaction.pipeline._write_review", fail_review)

    with pytest.raises(OSError, match="synthetic review write failure"):
        run(
            source=source,
            destination=destination,
            map_path=map_path,
            review_path=review_path,
            roster=[],
            batch="essay-1",
        )

    assert map_path.exists()
    assert not review_path.exists()
    assert not (destination / "_redaction.json").exists()


def test_cross_batch_force_invalidates_old_review_before_output_mutation(
    tmp_path, monkeypatch
):
    first_source = tmp_path / "first"
    _write(first_source, "paper.md", "Alice Smith wrote the same body.")
    destination = tmp_path / "out"
    first = {
        "source": first_source,
        "destination": destination,
        "map_path": tmp_path / "alice-map.json",
        "review_path": tmp_path / "alice-review.json",
        "roster": [RosterEntry("Alice Smith")],
        "batch": "alice-batch",
    }
    run(**first)
    assert run(**first, reviewed=True).exit_code == EXIT_OK
    assert RedactionManifest.load(destination).reviewed is True

    second_source = tmp_path / "second"
    _write(second_source, "paper.md", "Bob Jones wrote the same body.")

    original_write_private_text = __import__(
        "scripts.redaction.pipeline", fromlist=["write_private_text"]
    ).write_private_text

    def fail_map_write(path, text):
        if path == tmp_path / "bob-map.json":
            raise OSError("synthetic map write failure")
        return original_write_private_text(path, text)

    monkeypatch.setattr("scripts.redaction.pipeline.write_private_text", fail_map_write)
    with pytest.raises(OSError, match="synthetic map write failure"):
        run(
            source=second_source,
            destination=destination,
            map_path=tmp_path / "bob-map.json",
            review_path=tmp_path / "bob-review.json",
            roster=[RosterEntry("Bob Jones")],
            batch="bob-batch",
            force=True,
        )

    assert RedactionManifest.load(destination).reviewed is False


@pytest.mark.parametrize("unexpected", ["_raw.md", "nested/raw.md"])
def test_review_approval_rejects_every_unbound_destination_entry(
    tmp_path, unexpected
):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    destination = tmp_path / "out"
    unbound = destination / unexpected
    unbound.parent.mkdir(parents=True, exist_ok=True)
    unbound.write_text("identity-bearing material", encoding="utf-8")
    common = {
        "source": source,
        "destination": destination,
        "map_path": tmp_path / "map.json",
        "review_path": tmp_path / "review.json",
        "roster": [],
        "batch": "essay-1",
    }
    run(**common, force=True)

    approval = run(**common, reviewed=True)

    assert approval.exit_code == EXIT_CANDIDATES
    assert RedactionManifest.load(destination).reviewed is False


def test_review_source_binding_reuses_the_extracted_snapshot(tmp_path, monkeypatch):
    source = tmp_path / "in"
    _write(source, "paper.md", "Platforms curate public attention.")
    original_sha256 = __import__("scripts.redaction.pipeline", fromlist=["_sha256"])._sha256

    def reject_submission_reread(path, label, max_bytes):
        if label == "The submission":
            raise AssertionError("source binding reread the submission")
        return original_sha256(path, label, max_bytes)

    monkeypatch.setattr("scripts.redaction.pipeline._sha256", reject_submission_reread)

    result = run(
        source=source,
        destination=tmp_path / "out",
        map_path=tmp_path / "map.json",
        review_path=tmp_path / "review.json",
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_OK


@pytest.mark.parametrize(
    ("limit_name", "limit"),
    [("MAX_TOKEN_MAP_ENTITIES", 1), ("MAX_TOKEN_MAP_BYTES", 300)],
)
def test_token_map_limits_fail_closed_with_durable_private_state(
    tmp_path, monkeypatch, limit_name, limit
):
    source = tmp_path / "in"
    _write(
        source,
        "paper.md",
        "Contact first.person@example.edu and second.person@example.edu.",
    )
    monkeypatch.setattr(f"scripts.redaction.tokens.{limit_name}", limit, raising=False)
    destination = tmp_path / "out"
    map_path = tmp_path / "map.json"
    review_path = tmp_path / "review.json"

    result = run(
        source=source,
        destination=destination,
        map_path=map_path,
        review_path=review_path,
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNREADABLE
    assert map_path.stat().st_mode & 0o777 == 0o600
    assert review_path.stat().st_mode & 0o777 == 0o600
    manifest = RedactionManifest.load(destination)
    assert manifest.reviewed is False
    assert manifest.exit_code == EXIT_UNREADABLE
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["state_errors"]


def test_manifest_can_parse_the_exact_bytes_that_were_hashed(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    output = redacted / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    manifest = _create_manifest(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=False,
        exit_code=0,
    )
    manifest.save(redacted)
    data = (redacted / "_redaction.json").read_bytes()

    loaded = RedactionManifest.from_bytes(data)

    assert loaded == manifest


def test_oversized_private_review_falls_back_before_any_oversized_write(
    tmp_path, monkeypatch
):
    source = tmp_path / "in"
    _write(source, "paper.md", "My brother Daniel drove me to the hearing.")
    destination = tmp_path / "out"
    map_path = tmp_path / "map.json"
    review_path = tmp_path / "review.json"
    monkeypatch.setattr("scripts.redaction.pipeline.MAX_PRIVATE_ARTIFACT_BYTES", 500)

    result = run(
        source=source,
        destination=destination,
        map_path=map_path,
        review_path=review_path,
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNREADABLE
    assert review_path.stat().st_size <= 500
    assert review_path.stat().st_mode & 0o777 == 0o600
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["error"] == "private review exceeded its byte limit"
    manifest = RedactionManifest.load(destination)
    assert manifest.reviewed is False
    assert manifest.exit_code == EXIT_UNREADABLE


def test_candidate_review_item_budget_fails_closed_with_compact_private_state(
    tmp_path, monkeypatch
):
    source = tmp_path / "in"
    _write(
        source,
        "paper.md",
        "My brother Daniel drove me. My sister Rosa called me. "
        "My cousin Priya warned me.",
    )
    monkeypatch.setattr(
        "scripts.redaction.pipeline.MAX_REVIEW_NAME_ITEMS", 2, raising=False
    )
    destination = tmp_path / "out"
    review_path = tmp_path / "review.json"

    result = run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        review_path=review_path,
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNREADABLE
    review = json.loads(review_path.read_text(encoding="utf-8"))
    assert review["error"] == "candidate review item limit exceeded"
    assert "candidates" not in review
    manifest = RedactionManifest.load(destination)
    assert manifest.reviewed is False
    assert manifest.exit_code == EXIT_UNREADABLE


def test_final_redacted_utf8_size_is_checked_before_output_write(
    tmp_path, monkeypatch
):
    source = tmp_path / "in"
    _write(source, "paper.md", "a@b.co c@d.co")
    monkeypatch.setattr("scripts.redaction.pipeline.MAX_FILE_BYTES", 16)
    destination = tmp_path / "out"

    result = run(
        source=source,
        destination=destination,
        map_path=tmp_path / "map.json",
        review_path=tmp_path / "review.json",
        roster=[],
        batch="essay-1",
    )

    assert result.exit_code == EXIT_UNREADABLE
    assert [path for path in destination.iterdir() if not path.name.startswith("_")] == []
    manifest = RedactionManifest.load(destination)
    assert manifest.reviewed is False
    assert manifest.exit_code == EXIT_UNREADABLE


def test_schema_two_manifest_loads_as_unreviewed_and_ineligible(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    output = redacted / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    payload = {
        "schema": "redaction-batch/2",
        "batch": "essay-2",
        "run_id": "run-identifier-1234",
        "reviewed": True,
        "exit_code": 0,
        "outputs": [
            {
                "path": output.name,
                "sha256": __import__("hashlib").sha256(output.read_bytes()).hexdigest(),
            }
        ],
    }
    (redacted / "_redaction.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = RedactionManifest.load(redacted)

    assert loaded.reviewed is False
    assert loaded.map_sha256 == ""
    assert loaded.review_sha256 == ""


def test_schema_three_manifest_requires_map_and_review_digests(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    output = redacted / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    map_digest = "62d325824da560ffec8bc44d147e265e4ae1b77656cfde632b72c7a633f711e7"
    review_digest = "31f937c105ebe8e03a0dcd1396a5b3cd3675fdc257cdd5bc5fd8598ab130de7b"

    manifest = _create_manifest(
        "essay-2",
        "run-identifier-1234",
        [output],
        reviewed=True,
        exit_code=0,
        map_sha256=map_digest,
        review_sha256=review_digest,
    )
    manifest.save(redacted)
    loaded = RedactionManifest.load(redacted)

    assert loaded.map_sha256 == map_digest
    assert loaded.review_sha256 == review_digest
    payload = json.loads((redacted / "_redaction.json").read_text(encoding="utf-8"))
    assert payload["schema"] == "redaction-batch/3"


@pytest.mark.parametrize("field", ["map_sha256", "review_sha256"])
@pytest.mark.parametrize("digest", ["", "A" * 64, "g" * 64, "0" * 63])
def test_schema_three_manifest_rejects_noncanonical_private_digests(
    tmp_path, field, digest
):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    output = redacted / "STUDENT_01.md"
    output.write_text("safe", encoding="utf-8")
    kwargs = {
        "map_sha256": TEST_MAP_SHA256,
        "review_sha256": TEST_REVIEW_SHA256,
    }
    kwargs[field] = digest

    with pytest.raises(ValueError, match="lowercase"):
        RedactionManifest.create(
            "essay-2",
            "run-identifier-1234",
            [output],
            reviewed=False,
            exit_code=0,
            **kwargs,
        )
