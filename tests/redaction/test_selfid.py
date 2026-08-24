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
    """Singular so the family marker fires; only the missing first person stops it."""
    text = "The grandmother of the plaintiff testified about the 1975 evacuation."
    assert find_self_identifying(text) == []


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


def test_a_year_alone_does_not_warn():
    """Statute and case years fill policy essays. A year needs a strong marker too."""
    assert find_self_identifying("In this paper I examine the 2019 ruling.") == []
    assert find_self_identifying("I draw on Habermas (1962) throughout.") == []
    assert find_self_identifying("My analysis of the 1996 Act follows Lessig.") == []
    assert find_self_identifying("I will show that the 2021 rule failed.") == []


def test_immigration_status_warns():
    warnings = find_self_identifying("I was undocumented until high school.")
    assert warnings and "status" in warnings[0].reasons

    warnings = find_self_identifying(
        "After my deportation hearing, I changed majors."
    )
    assert warnings and "status" in warnings[0].reasons


def test_first_generation_status_warns():
    text = "I am the first in my family to attend college."
    warnings = find_self_identifying(text)
    assert warnings and "firstgen" in warnings[0].reasons


def test_a_disability_disclosure_warns():
    text = "My disability makes the commute hard."
    warnings = find_self_identifying(text)
    assert warnings and "health" in warnings[0].reasons


def test_military_service_warns():
    text = "I served in Iraq before enrolling."
    warnings = find_self_identifying(text)
    assert warnings and "military" in warnings[0].reasons


def test_a_year_enriches_an_existing_warning():
    text = "My grandmother's escape from Saigon in 1975 shaped my view."
    warnings = find_self_identifying(text)
    assert set(warnings[0].reasons) >= {"family", "year"}
