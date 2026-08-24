"""Layer 1 must catch roster identities without eating subject matter."""

from scripts.redaction.roster import RosterEntry, load_roster, redact_roster
from scripts.redaction.tokens import TokenMap


def test_full_name_is_replaced():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Maya Lin argues well.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01 argues well."
    assert result.matched == {"Maya Lin"}


def test_possessive_keeps_its_suffix():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Maya Lin's thesis is sharp.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01's thesis is sharp."


def test_last_name_first_form_is_replaced():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Lin, Maya submitted late.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01 submitted late."


def test_email_and_id_are_replaced():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")]
    result = redact_roster(
        "Contact mlin@example.edu or cite S1000431.", roster, tmap, "p1.md"
    )
    assert result.text == "Contact EMAIL_01 or cite ID_01."


def test_the_instructor_is_never_redacted():
    """Marcus Thorne is a student. Dr. Marcus Vance is not."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Marcus Thorne")]
    result = redact_roster(
        "Dr. Marcus Vance graded Marcus Thorne's essay.",
        roster,
        tmap,
        "p6.md",
        protected=("Marcus Vance",),
    )
    assert result.text == "Dr. Marcus Vance graded STUDENT_01's essay."


def test_bare_name_is_skipped_when_the_full_name_is_absent():
    """An essay about the Maya civilization must stay gradeable."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("The Maya civilization collapsed.", roster, tmap, "p2.md")
    assert result.text == "The Maya civilization collapsed."
    assert result.matched == set()


def test_bare_name_is_replaced_and_reported_when_the_full_name_is_present():
    """Identity wins, but the substitution must be visible to the instructor."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster(
        "Maya Lin wrote this. The Maya civilization collapsed.",
        roster,
        tmap,
        "p1.md",
    )
    assert result.text == "STUDENT_01 wrote this. The STUDENT_01 civilization collapsed."
    assert [hit.name for hit in result.bare_hits] == ["Maya"]
    assert "civilization" in result.bare_hits[0].context


def test_bare_names_can_be_turned_off():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster(
        "Maya Lin wrote this. The Maya civilization collapsed.",
        roster,
        tmap,
        "p1.md",
        bare_names=False,
    )
    assert result.text == "STUDENT_01 wrote this. The Maya civilization collapsed."


def test_roster_loads_from_csv(phil210):
    roster = load_roster(phil210 / "roster.csv")
    assert len(roster) == 10
    assert roster[0] == RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")
