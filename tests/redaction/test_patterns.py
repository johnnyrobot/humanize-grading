"""Layer 2 must catch structured PII without touching subject matter."""

from scripts.redaction.patterns import redact_structured
from scripts.redaction.tokens import TokenMap


def test_email_is_replaced():
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Reach me at maya@example.edu.", tmap, "p1.md")
    assert text == "Reach me at EMAIL_01."
    assert count == 1


def test_phone_is_replaced():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("Call 415-555-0142 after five.", tmap, "p1.md")
    assert text == "Call PHONE_01 after five."


def test_student_id_is_replaced():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("Student S1000431 submitted.", tmap, "p1.md")
    assert text == "Student ID_01 submitted."


def test_personal_profile_url_is_replaced():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured(
        "See https://linkedin.com/in/mayalin for details.", tmap, "p1.md"
    )
    assert text == "See URL_01 for details."


def test_a_cited_year_is_not_an_id():
    """Gillespie (2014) must survive. Over-redaction destroys gradeability."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured(
        "As Gillespie (2014) notes, algorithms curate.", tmap, "p1.md"
    )
    assert text == "As Gillespie (2014) notes, algorithms curate."
    assert count == 0


def test_a_statutory_reference_survives():
    """DMCA 1201 is subject matter, not an identifier."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Under DMCA 1201 the rule holds.", tmap, "p1.md")
    assert count == 0


def test_repeated_email_reuses_its_token():
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("a@b.edu and a@b.edu", tmap, "p1.md")
    assert text == "EMAIL_01 and EMAIL_01"
