"""The warning pass must catch autobiography without crying wolf."""

from scripts.redaction.selfid import find_self_identifying


def test_family_plus_place_plus_year_warns():
    text = "My grandmother's escape from Saigon in 1975 shaped my view."
    warnings = find_self_identifying(text)
    assert len(warnings) == 1
    assert set(warnings[0].reasons) >= {"family", "year"}


def test_first_person_alone_does_not_warn():
    """Academic essays use the first person. That is not autobiography."""
    assert find_self_identifying("I argue that platforms curate speech.") == []


def test_family_without_first_person_does_not_warn():
    assert find_self_identifying("Grandmothers were surveyed in the study.") == []


def test_employer_disclosure_warns():
    text = "I worked at Kaiser Permanente for six years before this course."
    warnings = find_self_identifying(text)
    assert warnings and "employer" in warnings[0].reasons


def test_the_warning_reports_its_line_and_sentence():
    text = "First line.\nMy father lost his farm in 1987."
    warnings = find_self_identifying(text)
    assert warnings[0].line == 2
    assert warnings[0].sentence.startswith("My father")


def test_multiple_passages_each_warn():
    text = "My mother arrived in 1990. Platforms curate. My uncle farmed in 2001."
    assert len(find_self_identifying(text)) == 2
