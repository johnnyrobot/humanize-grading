"""Rehydration must restore names locally and fail on an unmapped token."""

import pytest

from scripts.redaction.rehydrate import rehydrate_text
from scripts.redaction.roster import RosterEntry, redact_roster
from scripts.redaction.tokens import TokenMap


def test_tokens_become_names_again():
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "p1.md")
    text, unmapped = rehydrate_text("STUDENT_01 argues well.", tmap)
    assert text == "Maya Lin argues well."
    assert unmapped == []


def test_an_unmapped_token_is_reported():
    """A stray token pasted into an LMS comment is worth failing over."""
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "p1.md")
    _, unmapped = rehydrate_text("STUDENT_01 and STUDENT_12 differ.", tmap)
    assert unmapped == ["STUDENT_12"]


def test_round_trip_restores_the_original():
    original = "Maya Lin's thesis, at mlin@example.edu, cites Gillespie (2014)."
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin", "S1000431", "mlin@example.edu")]
    redacted = redact_roster(original, roster, tmap, "p1.md").text
    restored, unmapped = rehydrate_text(redacted, tmap)
    assert restored == original
    assert unmapped == []


def test_possessives_survive_the_round_trip():
    tmap = TokenMap("essay-1")
    roster = [RosterEntry("Maya Lin")]
    redacted = redact_roster("Maya Lin's essay.", roster, tmap, "p1.md").text
    assert rehydrate_text(redacted, tmap)[0] == "Maya Lin's essay."
