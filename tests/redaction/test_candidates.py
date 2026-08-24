"""Layer 4 must flag people, not cited scholars or platforms."""

from scripts.redaction.candidates import find_candidates


def test_relational_name_is_flagged():
    found = find_candidates("My brother Daniel drove me to the hearing.")
    assert [c.name for c in found] == ["Daniel"]


def test_professor_reference_is_flagged():
    found = find_candidates("Professor Alvarez disagreed with the ruling.")
    assert [c.name for c in found] == ["Alvarez"]


def test_speech_attribution_is_flagged():
    found = find_candidates("Priya told me the permit never arrived.")
    assert [c.name for c in found] == ["Priya"]


def test_a_citation_is_not_flagged():
    """Gillespie (2014) is subject matter."""
    assert find_candidates("As Gillespie (2014) notes, algorithms curate.") == []


def test_a_theorist_in_prose_is_not_flagged():
    assert find_candidates("Habermas envisioned an idealized forum.") == []


def test_a_platform_is_not_flagged():
    assert find_candidates("Recommendation engines on TikTok and YouTube.") == []


def test_a_sentence_opening_word_is_not_flagged():
    assert find_candidates("Contemporary systems do not serve as conduits.") == []


def test_the_candidate_carries_line_and_context():
    text = "First line.\nMy sister Rosa filed the complaint."
    found = find_candidates(text)
    assert found[0].line == 2
    assert "filed the complaint" in found[0].context
