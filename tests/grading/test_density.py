"""Density is evidence for criterion 4. It is never evidence of authorship."""

import tracemalloc

import pytest

from scripts.grading.density import (
    PATTERNS_PATH,
    DensityLimitError,
    Term,
    load_terms,
    scan,
)


def _write_pattern_reference(path, watched_terms):
    path.write_text(
        "### 7. Watched prose\n"
        "**Words to watch:** " + ", ".join(watched_terms) + "\n",
        encoding="utf-8",
    )


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
    # Real values today: slop 55.97 per 1000 across 15 hits, clean 0.0. These
    # floors leave headroom while still failing if hit detection regresses.
    # A bare ratio does not work here: the clean paper scores 0.0, so
    # `slop > clean * 2` reduces to `slop > 0`.
    assert slop.per_thousand >= 20
    assert len(slop.hits) >= 10
    assert clean.per_thousand < 5


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


@pytest.mark.parametrize("gap", [" ", "\t", "\n", "\n    "])
def test_a_multi_word_term_matches_across_ordinary_whitespace(gap, terms):
    result = scan(f"This stands{gap}as proof.", terms)
    assert any(hit.term.lower() == "stands as" for hit in result.hits)


def test_a_multi_word_term_does_not_span_unbounded_line_breaks(terms):
    result = scan("This stands" + "\n" * 1_000 + "as proof.", terms)

    assert not any(hit.term.lower() == "stands as" for hit in result.hits)


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


def test_load_terms_rejects_more_than_256_unique_terms(tmp_path):
    patterns = tmp_path / "patterns.md"
    _write_pattern_reference(
        patterns,
        [f"watched{i:03d}" for i in range(257)],
    )

    with pytest.raises(DensityLimitError, match="at most 256 unique watched terms"):
        load_terms(patterns)


def test_load_terms_rejects_a_term_longer_than_256_characters(tmp_path):
    patterns = tmp_path / "patterns.md"
    _write_pattern_reference(patterns, ["x" * 257])

    with pytest.raises(DensityLimitError, match="at most 256 characters"):
        load_terms(patterns)


def test_load_terms_rejects_more_than_16384_aggregate_characters(tmp_path):
    patterns = tmp_path / "patterns.md"
    _write_pattern_reference(
        patterns,
        [f"{index:03d}" + "x" * 253 for index in range(65)],
    )

    with pytest.raises(
        DensityLimitError, match="at most 16384 characters in total"
    ):
        load_terms(patterns)


def test_scan_rejects_more_than_256_term_entries():
    terms = [Term(f"watched{index:03d}", 7) for index in range(257)]

    with pytest.raises(DensityLimitError, match="at most 256 unique watched terms"):
        scan("", terms)


def test_scan_rejects_duplicate_custom_terms_instead_of_double_counting():
    terms = [Term("delve", 7), Term("DELVE", 8)]

    with pytest.raises(DensityLimitError, match="must be unique"):
        scan("We delve.", terms)


def test_scan_rejects_a_custom_term_longer_than_256_characters():
    terms = [Term("x" * 257, 7)]

    with pytest.raises(DensityLimitError, match="at most 256 characters"):
        scan("", terms)


def test_scan_rejects_more_than_16384_custom_term_characters():
    terms = [
        Term(f"{index:03d}" + "x" * 253, 7)
        for index in range(65)
    ]

    with pytest.raises(
        DensityLimitError, match="at most 16384 characters in total"
    ):
        scan("", terms)


def test_scan_rejects_more_than_5000_matches_instead_of_truncating():
    with pytest.raises(
        DensityLimitError, match="more than 5000 watched-term matches"
    ):
        scan("delve " * 5001, [Term("delve", 7)])


def _scan_with_peak_memory(text):
    tracemalloc.start()
    try:
        result = scan(text, [])
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, peak


def test_newline_dense_text_does_not_allocate_one_offset_per_line_break():
    result, peak = _scan_with_peak_memory("\n" * 100_000)

    assert result.words == 0
    assert result.hits == ()
    assert peak < 1_000_000


def test_word_dense_text_counts_with_bounded_auxiliary_memory():
    result, peak = _scan_with_peak_memory("word " * 100_000)

    assert result.words == 100_000
    assert result.hits == ()
    assert peak < 1_000_000
