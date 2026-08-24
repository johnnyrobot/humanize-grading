"""The pipeline must produce redacted output and fail closed."""

import json

import pytest

from scripts.redaction.pipeline import EXIT_CANDIDATES, EXIT_OK, EXIT_ROSTER, EXIT_UNREADABLE, run
from scripts.redaction.roster import RosterEntry


def _write(directory, name, body):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / name).write_text(body, encoding="utf-8")


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
    written = list((tmp_path / "out").iterdir())
    assert [p.name for p in written] == ["STUDENT_01.md"]
    assert "Maya" not in written[0].read_text(encoding="utf-8")
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
    assert run(**common, reviewed=True).exit_code == EXIT_OK


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

    written = list((tmp_path / "out").iterdir())
    assert len(written) == 1
    assert "Maya" not in written[0].read_text(encoding="utf-8")
    assert result.files[0].token == "STUDENT_01"
