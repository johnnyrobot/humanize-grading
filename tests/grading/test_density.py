"""Density is evidence for criterion 4. It is never evidence of authorship."""

import pytest

from scripts.grading.density import PATTERNS_PATH, load_terms, scan


@pytest.fixture(scope="module")
def terms():
    return load_terms(PATTERNS_PATH)


def test_it_reads_terms_from_the_shared_pattern_file(terms):
    texts = {term.text.lower() for term in terms}
    assert "delve" in texts
    assert "tapestry" in texts
    assert "pivotal" in texts
    assert "testament" in texts


def test_it_tags_each_term_with_its_pattern_number(terms):
    delve = [term for term in terms if term.text.lower() == "delve"]
    assert delve and delve[0].pattern == 7


def test_it_strips_a_parenthetical_qualifier(terms):
    texts = {term.text.lower() for term in terms}
    assert "landscape" in texts
    assert "landscape (abstract noun)" not in texts


def test_it_expands_one_slashed_word_into_alternates(terms):
    texts = {term.text.lower() for term in terms}
    assert {"gate", "gated", "gating"} <= texts
    assert {"stands as", "serves as"} <= texts


def test_it_skips_a_template_rather_than_matching_it_literally(terms):
    texts = {term.text.lower() for term in terms}
    assert not any("..." in text for text in texts)
    assert "x is the y of z" not in texts


def test_it_skips_a_long_trailing_clause(terms):
    assert all(len(term.text.split()) <= 6 for term in terms)


def test_the_slop_paper_scores_far_above_the_clean_paper(phil210, terms):
    slop = scan(
        (phil210 / "ungraded/paper_7_ungraded_ai_healthcare_slop.md").read_text(
            encoding="utf-8"
        ),
        terms,
    )
    clean = scan(
        (
            phil210 / "ungraded/paper_6_ungraded_community_solar_interconnection.md"
        ).read_text(encoding="utf-8"),
        terms,
    )
    assert slop.per_thousand > clean.per_thousand * 2


def test_it_reports_where_each_hit_is(terms):
    result = scan("We must delve into the vibrant tapestry.", terms)
    words = {hit.term.lower() for hit in result.hits}
    assert {"delve", "vibrant", "tapestry"} <= words
    for hit in result.hits:
        assert hit.line == 1
        assert hit.offset >= 0


def test_hits_come_back_in_document_order(terms):
    result = scan("The vibrant future. We delve later.", terms)
    offsets = [hit.offset for hit in result.hits]
    assert offsets == sorted(offsets)


def test_it_matches_case_insensitively_on_whole_words(terms):
    assert scan("Delve deeper.", terms).hits
    assert not [hit for hit in scan("Delved deeper.", terms).hits if hit.term == "delve"]


def test_a_multi_word_term_matches_across_a_line_break(terms):
    result = scan("This stands\nas proof.", terms)
    assert any(hit.term.lower() == "stands as" for hit in result.hits)


def test_it_counts_words_and_reports_a_rate(terms):
    result = scan("delve " * 10 + "word " * 990, terms)
    assert result.words == 1000
    assert result.per_thousand == pytest.approx(10.0, abs=0.1)


def test_empty_text_reports_zero_and_does_not_divide_by_zero(terms):
    result = scan("", terms)
    assert result.words == 0
    assert result.per_thousand == 0.0
    assert result.hits == ()


def test_the_breakdown_groups_counts_by_pattern(terms):
    result = scan("A vibrant tapestry we must delve into.", terms)
    assert result.by_pattern[7] >= 2


def test_as_dict_round_trips_through_json(terms):
    import json

    payload = scan("We delve.", terms).as_dict()
    assert json.loads(json.dumps(payload))["per_thousand"] >= 0
