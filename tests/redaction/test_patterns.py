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


def test_a_docket_id_reference_is_not_a_student_id():
    """A bare 'id' cue is not evidence of a person.

    "Docket ID 12345678" is ordinary language in a policy essay, not a
    student identifier. Layer 1 (roster.py) already catches every roster
    student's ID by exact match; layer 2 requires an explicit STUDENT cue
    so it does not over-redact subject matter like docket/case/bug IDs.
    """
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Docket ID 12345678 was filed", tmap, "p1.md")
    assert text == "Docket ID 12345678 was filed"
    assert count == 0


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


def test_a_cited_platform_rules_page_is_not_a_profile():
    """Platform rules pages are subject matter, not personal profiles."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("See x.com/rules for the platform's policy", tmap, "p1.md")
    assert text == "See x.com/rules for the platform's policy"
    assert count == 0


def test_a_cited_terms_of_service_page_is_not_a_profile():
    """Terms of service pages are subject matter, not personal profiles."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("Citing twitter.com/tos as the source", tmap, "p1.md")
    assert text == "Citing twitter.com/tos as the source"
    assert count == 0


def test_a_github_repository_is_not_a_profile():
    """GitHub repositories with multiple path segments must not be caught as profiles."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("(see github.com/torvalds/linux for the repo)", tmap, "p1.md")
    assert text == "(see github.com/torvalds/linux for the repo)"
    assert count == 0


def test_a_github_user_profile_is_still_redacted():
    """GitHub single-segment handles must still be caught as profiles."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("github.com/mayalin", tmap, "p1.md")
    assert text == "URL_01"
    assert count == 1


def test_a_custom_single_group_id_pattern_works():
    """A caller-supplied id_pattern with a different group count must not raise.

    redact_structured's substitution used to hardcode reading groups 1 and 2,
    so any custom id_pattern with a single capture group raised
    ``IndexError: no such group``. The substitution must be agnostic to how
    many capture groups the pattern has.
    """
    import re

    custom_pattern = re.compile(r"\bcustomer\s+#(\d{4,8})\b", re.IGNORECASE)
    tmap = TokenMap("essay-1")
    text, count = redact_structured(
        "Customer #4821 called in.", tmap, "p1.md", id_pattern=custom_pattern
    )
    assert text == "Customer #ID_01 called in."
    assert count == 1


def test_a_github_profile_with_a_trailing_slash_is_redacted():
    """A trailing slash (common from browser-bar copy-paste) must not hide a profile."""
    tmap = TokenMap("essay-1")
    text, count = redact_structured("see github.com/mayalin/ for more", tmap, "p1.md")
    assert count == 1
    assert "mayalin" not in text


def test_the_github_lookahead_does_not_backtrack_quadratically():
    """The github alternative must not exhibit catastrophic backtracking.

    An earlier lookahead, `(?![\\w.-]*/)`, re-scanned the tail on every
    backtrack of the greedy handle, making long non-matching runs O(n^2).
    A possessive quantifier closes that hole. This asserts the call
    completes at all -- not a wall-clock threshold, so it is not flaky on
    a loaded machine.
    """
    tmap = TokenMap("essay-1")
    text = "github.com/" + "a" * 20000 + "/"
    redact_structured(text, tmap, "p1.md")
