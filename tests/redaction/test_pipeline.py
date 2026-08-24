"""The pipeline must produce redacted output and fail closed."""

import json

import pytest

from scripts.redaction.pipeline import (
    EXIT_CANDIDATES,
    EXIT_DESTINATION_NOT_EMPTY,
    EXIT_OK,
    EXIT_ROSTER,
    EXIT_UNREADABLE,
    run,
)
from scripts.redaction.roster import RosterEntry
from scripts.redaction.tokens import TokenMap


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
    assert sorted(p.name for p in destination.iterdir()) == [
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

    written = sorted(p.name for p in destination.iterdir())
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
    written = [p.name for p in (tmp_path / "out").iterdir()]
    assert written == ["STUDENT_01.md"]
    review = json.loads(
        (tmp_path / "reviews" / "essay-1.json").read_text(encoding="utf-8")
    )
    assert review["skipped_empty"] == ["blank.md"]
