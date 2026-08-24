"""Nothing personal may survive into the redacted output.

This is the test the redaction pipeline exists to pass. It seeds known values
across all four detection layers and asserts that zero reach disk. The tests
after it pin defects found while building the pipeline; each one failed before
its fix.
"""

from __future__ import annotations

import pytest

from scripts.redaction.pipeline import EXIT_DESTINATION_NOT_EMPTY, run
from scripts.redaction.rehydrate import rehydrate_text
from scripts.redaction.roster import RosterEntry, load_roster
from scripts.redaction.tokens import TokenMap

SEEDED_CONTACTS = [
    "mthorne@example.edu",
    "schen@example.edu",
    "415-555-0142",
    "(212) 555-8891",
    "https://linkedin.com/in/marcusthorne",
]

SEEDED_IDS = ["S1000433", "S1000432"]

ROSTER_NAMES = [
    "Maya Lin",
    "Samantha Chen",
    "Marcus Thorne",
    "David Morales",
    "Chloe Bennett",
    "Jordan Reed",
    "Tyler Brooks",
    "Lauren Higgins",
    "Evan Ramirez",
    "Brandon Scott",
]

# Verified present in papers 6-10. The plan's original list named Habermas,
# Gillespie, TikTok and 2014, none of which appear in those papers, so
# asserting on them would have passed without testing anything.
MUST_SURVIVE = ["DMCA", "1201", "John Deere", "RealPage", "Sherman", "interconnection"]


def _seeded_source(phil210, tmp_path):
    """Copy the ungraded papers and inject contact details into the first two."""
    source = tmp_path / "seeded"
    source.mkdir()
    papers = sorted((phil210 / "ungraded").glob("*.md"))
    for index, paper in enumerate(papers):
        body = paper.read_text(encoding="utf-8")
        if index == 0:
            body += (
                "\n\nReach me at mthorne@example.edu or 415-555-0142. "
                "Student ID S1000433. Portfolio: "
                "https://linkedin.com/in/marcusthorne\n"
            )
        if index == 1:
            body += (
                "\n\nContact schen@example.edu or (212) 555-8891. "
                "Student ID S1000432.\n"
            )
        (source / paper.name).write_text(body, encoding="utf-8")
    return source


def _redact(source, phil210, tmp_path, name, instructor):
    destination = tmp_path / name
    map_path = tmp_path / name / "map.json"
    result = run(
        source=source,
        destination=destination,
        map_path=tmp_path / f"{name}-state" / "map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch=name,
        protected=(instructor,),
        reviewed=True,
    )
    return result, destination


def _corpus_text(destination):
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(destination.glob("*.md"))
    )


def test_no_seeded_contact_or_id_survives(phil210, tmp_path, instructor):
    source = _seeded_source(phil210, tmp_path)
    _, destination = _redact(source, phil210, tmp_path, "contacts", instructor)

    corpus = _corpus_text(destination)
    leaked = [v for v in SEEDED_CONTACTS + SEEDED_IDS if v in corpus]
    assert leaked == [], f"These values reached the redacted output: {leaked}"


def test_no_roster_name_survives(phil210, tmp_path, instructor):
    source = _seeded_source(phil210, tmp_path)
    _, destination = _redact(source, phil210, tmp_path, "names", instructor)

    corpus = _corpus_text(destination)
    leaked = [name for name in ROSTER_NAMES if name in corpus]
    assert leaked == [], f"These names reached the redacted output: {leaked}"


def test_no_roster_surname_survives(phil210, tmp_path, instructor):
    """A bare surname identifies a student as surely as a full name."""
    source = _seeded_source(phil210, tmp_path)
    _, destination = _redact(source, phil210, tmp_path, "surnames", instructor)

    corpus = _corpus_text(destination)
    leaked = [name.split()[-1] for name in ROSTER_NAMES if name.split()[-1] in corpus]
    assert leaked == [], f"These surnames reached the redacted output: {leaked}"


def test_no_filename_carries_a_name(phil210, tmp_path, instructor):
    source = _seeded_source(phil210, tmp_path)
    _, destination = _redact(source, phil210, tmp_path, "filenames", instructor)

    names = " ".join(path.name for path in destination.glob("*.md"))
    leaked = [n for n in ROSTER_NAMES if n.split()[-1] in names]
    assert leaked == [], f"These names reached a filename: {leaked}"


def test_subject_matter_survives(phil210, tmp_path, instructor):
    """Over-redaction destroys gradeability, so the essay's substance must remain."""
    source = _seeded_source(phil210, tmp_path)
    _, destination = _redact(source, phil210, tmp_path, "subject", instructor)

    corpus = _corpus_text(destination)
    missing = [term for term in MUST_SURVIVE if term not in corpus]
    assert missing == [], f"Redaction destroyed subject matter: {missing}"


def test_the_instructor_survives(phil210, tmp_path, instructor):
    """Dr. Marcus Vance grades the work; he is not a student."""
    destination = tmp_path / "graded"
    run(
        source=phil210 / "graded",
        destination=destination,
        map_path=tmp_path / "graded-state" / "map.json",
        roster=load_roster(phil210 / "roster.csv"),
        batch="graded",
        protected=(instructor,),
        reviewed=True,
    )
    assert "Vance" in _corpus_text(destination)


def test_a_non_empty_destination_is_refused(phil210, tmp_path, instructor):
    """A re-run must not leave stale output that looks like this run's work."""
    source = _seeded_source(phil210, tmp_path)
    destination = tmp_path / "reused"
    common = {
        "source": source,
        "destination": destination,
        "map_path": tmp_path / "reused-state" / "map.json",
        "roster": load_roster(phil210 / "roster.csv"),
        "batch": "reused",
        "protected": (instructor,),
        "reviewed": True,
    }
    run(**common)
    before = sorted(p.name for p in destination.iterdir())

    second = run(**common)

    assert second.exit_code == EXIT_DESTINATION_NOT_EMPTY
    assert sorted(p.name for p in destination.iterdir()) == before


def test_two_students_sharing_a_surname_are_not_confused(tmp_path):
    """One student's feedback must never come back addressed to another.

    A bare surname used to be replaced while still inside another student's
    full name, collapsing two people into one token.
    """
    source = tmp_path / "chens"
    source.mkdir()
    (source / "a.md").write_text(
        "Anna Chen wrote this essay. Samantha Chen wrote another. Chen withdrew.",
        encoding="utf-8",
    )
    destination = tmp_path / "chens-out"
    map_path = tmp_path / "chens-state" / "map.json"
    run(
        source=source,
        destination=destination,
        map_path=map_path,
        roster=[RosterEntry("Anna Chen"), RosterEntry("Samantha Chen")],
        batch="chens",
        reviewed=True,
    )

    redacted = _corpus_text(destination)
    assert "Chen" not in redacted

    tmap = TokenMap.load(map_path)
    students = {e.value for e in tmap.tokens().values() if e.kind == "STUDENT"}
    assert "Anna Chen" in students
    assert "Samantha Chen" in students

    restored, unmapped = rehydrate_text(redacted, tmap)
    assert unmapped == []
    assert "Anna Chen wrote this essay." in restored
    assert "Samantha Chen wrote another." in restored


def test_a_cited_platform_page_survives(tmp_path):
    """A cited rules page or repository is subject matter, not a profile."""
    source = tmp_path / "cites"
    source.mkdir()
    (source / "a.md").write_text(
        "See x.com/rules for the platform's policy, and "
        "github.com/torvalds/linux for the repository.",
        encoding="utf-8",
    )
    destination = tmp_path / "cites-out"
    run(
        source=source,
        destination=destination,
        map_path=tmp_path / "cites-state" / "map.json",
        roster=[],
        batch="cites",
        reviewed=True,
    )

    corpus = _corpus_text(destination)
    assert "x.com/rules" in corpus
    assert "github.com/torvalds/linux" in corpus


def test_a_patent_number_survives(tmp_path):
    """A patent number is the argument of a right-to-repair essay, not an ID."""
    source = tmp_path / "patent"
    source.mkdir()
    (source / "a.md").write_text(
        "Patent US9876543 covers the diagnostic lockout.", encoding="utf-8"
    )
    destination = tmp_path / "patent-out"
    run(
        source=source,
        destination=destination,
        map_path=tmp_path / "patent-state" / "map.json",
        roster=[],
        batch="patent",
        reviewed=True,
    )

    assert "US9876543" in _corpus_text(destination)


def test_the_round_trip_is_lossless_on_the_real_corpus(phil210, tmp_path, instructor):
    """Redacting then rehydrating must return the submission unchanged."""
    destination = tmp_path / "trip"
    map_path = tmp_path / "trip-state" / "map.json"
    run(
        source=phil210 / "ungraded",
        destination=destination,
        map_path=map_path,
        roster=load_roster(phil210 / "roster.csv"),
        batch="trip",
        protected=(instructor,),
        reviewed=True,
    )

    tmap = TokenMap.load(map_path)
    originals = {
        path.read_text(encoding="utf-8")
        for path in (phil210 / "ungraded").glob("*.md")
    }

    restored_all = []
    for path in sorted(destination.glob("*.md")):
        restored, unmapped = rehydrate_text(path.read_text(encoding="utf-8"), tmap)
        assert unmapped == [], f"{path.name} holds unmapped tokens: {unmapped}"
        restored_all.append(restored)

    assert len(restored_all) == 5
    for restored in restored_all:
        assert restored in originals, "a rehydrated file did not match any original"


@pytest.mark.parametrize("value", SEEDED_CONTACTS)
def test_each_seeded_contact_is_individually_absent(
    phil210, tmp_path, instructor, value
):
    """Name each seeded value so a failure says which one leaked."""
    source = _seeded_source(phil210, tmp_path)
    _, destination = _redact(
        source, phil210, tmp_path, f"one-{abs(hash(value))}", instructor
    )
    assert value not in _corpus_text(destination)
