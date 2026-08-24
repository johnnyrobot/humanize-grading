"""Layer 1 must catch roster identities without eating subject matter."""

import re

from scripts.redaction.rehydrate import rehydrate_text
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
    assert result.text == "STUDENT_01 wrote this. The STUDENT_02 civilization collapsed."
    assert [hit.name for hit in result.bare_hits] == ["Maya"]
    assert result.bare_hits[0].token == "STUDENT_02"
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


def test_two_students_sharing_a_surname_get_separate_tokens():
    """A bare surname must not merge two different students into one token."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Anna Chen"), RosterEntry("Samantha Chen")]
    result = redact_roster(
        "Anna Chen and Samantha Chen argued. Chen later withdrew.",
        roster,
        tmap,
        "p1.md",
    )
    assert result.text == "STUDENT_01 and STUDENT_02 argued. STUDENT_03 later withdrew."
    assert "Samantha STUDENT" not in result.text
    assert len({"STUDENT_01", "STUDENT_02", "STUDENT_03"}) == 3


def test_a_students_full_name_is_not_eaten_by_another_students_bare_name():
    """One student's bare last name must not devour the next student's full name."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin"), RosterEntry("Lin Wei")]
    result = redact_roster("Maya Lin and Lin Wei co-wrote it.", roster, tmap, "p1.md")
    assert result.text == "STUDENT_01 and STUDENT_02 co-wrote it."


def test_bare_name_substitution_round_trips_exactly():
    """A bare hit must rehydrate back to the exact word it replaced."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    original = "Maya Lin wrote this. The Maya civilization collapsed."
    result = redact_roster(original, roster, tmap, "p1.md")

    rehydrated = result.text
    for token in tmap.tokens():
        rehydrated = rehydrated.replace(token, tmap.value_for(token))

    assert rehydrated == original


def test_a_protected_name_inside_a_student_name_still_redacts_the_student():
    """An instructor sharing a surname with a student must not shield the student."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Samantha Chen")]
    result = redact_roster(
        "Dr. Chen returned Samantha Chen's paper.",
        roster,
        tmap,
        "p1.md",
        protected=("Chen",),
    )
    assert "Samantha Chen" not in result.text
    assert "Dr. Chen" in result.text
    assert result.matched == {"Samantha Chen"}


def test_every_bare_name_occurrence_is_reported():
    """Every bare-name substitution must get its own hit, not just the first."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster(
        "Maya Lin wrote this. The Maya Empire was vast. Later, Maya art "
        "flourished. Maya's exhibit closed too.",
        roster,
        tmap,
        "p1.md",
    )
    assert len(result.bare_hits) == 3
    assert result.text.count("STUDENT_02") == 3


def test_a_comma_list_of_two_students_does_not_scramble_their_tokens():
    """The comma-list variant must not span a comma between two different names."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Marcus Thorne"), RosterEntry("Marcus Webb")]
    result = redact_roster(
        "Marcus Thorne, Marcus Webb co-authored this.", roster, tmap, "p1.md"
    )
    assert result.text == "STUDENT_01, STUDENT_02 co-authored this."
    assert "Webb" not in result.text


def test_a_comma_after_a_student_name_does_not_defeat_the_instructor_shield():
    """A comma right after a student's name must not eat the next mention of the instructor."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Marcus Thorne")]
    result = redact_roster(
        "Dr. Marcus Vance graded Marcus Thorne, Marcus Vance said.",
        roster,
        tmap,
        "p1.md",
        protected=("Marcus Vance",),
    )
    assert result.text == "Dr. Marcus Vance graded STUDENT_01, Marcus Vance said."


def test_an_ambiguous_initial_is_not_bound_to_one_student():
    """"M. Smith" fits two roster entries; neither may claim it as their own."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Mary Anne Smith"), RosterEntry("Marcus Smith")]
    original = (
        "Mary Anne Smith and Marcus Smith wrote. M. Smith later revised "
        "the section."
    )
    result = redact_roster(original, roster, tmap, "p1.md")

    tokens_used = set(re.findall(r"STUDENT_\d+", result.text))
    assert len(tokens_used) == 3, "the ambiguous mention must not reuse either student's token"

    restored, unmapped = rehydrate_text(result.text, tmap)
    assert unmapped == []
    assert restored == original


def test_an_unambiguous_initial_still_binds_to_its_student():
    """A single matching roster entry must still claim its own initial form."""
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    result = redact_roster("Maya Lin wrote. M. Lin revised.", roster, tmap, "p1.md")

    assert result.text == "STUDENT_01 wrote. STUDENT_01 revised."
    assert tmap.value_for("STUDENT_01") == "Maya Lin"


def test_roster_loads_from_csv(phil210):
    roster = load_roster(phil210 / "roster.csv")
    assert len(roster) == 10
    assert roster[0] == RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")
