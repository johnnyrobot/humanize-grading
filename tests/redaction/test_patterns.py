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


def test_a_patent_number_is_not_an_id():
    """Patent numbers (e.g., US9876543) are subject matter, not student IDs."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Patent US9876543 covers this", tmap, "p1.md")
    assert text == "Patent US9876543 covers this"
    assert count == 0


def test_a_case_number_is_not_an_id():
    """Case numbers (e.g., AB1234567) are subject matter, not student IDs."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Case No. AB1234567 was decided", tmap, "p1.md")
    assert text == "Case No. AB1234567 was decided"
    assert count == 0


def test_an_all_digit_id_with_a_cue_is_replaced():
    """Student IDs without letters are caught if preceded by a cue word."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("My student ID is 100443112.", tmap, "p1.md")
    assert text == "My student ID is ID_01."
    assert count == 1


def test_the_id_cue_word_is_not_swallowed():
    """The cue word 'Student' must remain when the ID is replaced."""
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("Student S1000431 submitted.", tmap, "p1.md")
    assert text == "Student ID_01 submitted."


def test_unspaced_and_unformatted_phone_numbers_are_replaced():
    """Phone numbers without spaces or with minimal formatting are caught."""
    tmap = TokenMap("essay-1")
    # Test unspaced after paren
    text1, count1 = redact_structured("Call (415)555-0142 now.", tmap, "p1.md")
    assert text1 == "Call PHONE_01 now."
    assert count1 == 1

    # Test fully unformatted
    tmap2 = TokenMap("essay-2")
    text2, count2 = redact_structured("Call 4155550142 now.", tmap2, "p1.md")
    assert text2 == "Call PHONE_01 now."
    assert count2 == 1


def test_a_parenthesised_phone_number_is_fully_consumed():
    """The leading paren must be inside the match, not orphaned beside the token.

    An earlier pattern anchored on \\b before the optional paren, which cannot
    match between a space and "(", so the paren was left behind as "(PHONE_01".
    """
    tmap = TokenMap("essay-1")
    text, _ = redact_structured("Call (415) 555-0142 now.", tmap, "p1.md")
    assert text == "Call PHONE_01 now."
    assert "(" not in text


def test_a_markdown_bold_id_header_is_caught():
    """Markdown bold student ID headers must be caught, header text preserved."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("**Student ID**: X9998887", tmap, "p1.md")
    assert text == "**Student ID**: ID_01"
    assert count == 1


def test_an_id_hash_colon_header_is_caught():
    """ID headers with hash-colon notation must be caught."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("ID#: X9998887", tmap, "p1.md")
    assert text == "ID#: ID_01"
    assert count == 1


def test_a_version_string_is_not_a_phone_number():
    """Version strings like 'v1.415.555.0142' must not be caught as phone numbers."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("v1.415.555.0142 is the version.", tmap, "p1.md")
    assert text == "v1.415.555.0142 is the version."
    assert count == 0


def test_a_profile_domain_inside_another_url_is_not_redacted():
    """Archived URLs like 'https://archive.org/x.com/y' must not be caught."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("See https://archive.org/x.com/y for details.", tmap, "p1.md")
    assert text == "See https://archive.org/x.com/y for details."
    assert count == 0
