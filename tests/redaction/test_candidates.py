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


def test_a_cited_name_in_a_personal_context_is_skipped():
    """Gillespie (2014) is subject matter, even addressed with a title."""
    found = find_candidates("Professor Gillespie (2014) argues that algorithms curate.")
    assert found == []


def test_a_citation_does_not_suppress_a_different_person_elsewhere():
    """A cited Brady must not blind the layer to a different, private Brady."""
    found = find_candidates(
        "Brady (2017) measured outrage. My cousin Brady told me about it."
    )
    assert [c.name for c in found] == ["Brady"]


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


def test_an_interview_subject_is_flagged():
    found = find_candidates("I spoke with Elena about her eviction.")
    assert [c.name for c in found] == ["Elena"]


def test_an_apposition_after_a_relation_word_is_flagged():
    found = find_candidates("My landlord, Elena Vasquez, refused.")
    assert [c.name for c in found] == ["Elena"]


def test_a_possessive_with_first_person_is_flagged():
    found = find_candidates("Elena's testimony changed my view.")
    assert [c.name for c in found] == ["Elena"]


def test_a_quote_attribution_is_flagged():
    found = find_candidates('"The permit never came," Elena said.')
    assert [c.name for c in found] == ["Elena"]


def test_a_scholar_addressing_the_reader_is_not_flagged():
    assert find_candidates("Habermas told us that the public sphere eroded.") == []


def test_two_people_sharing_a_first_name_are_both_flagged():
    found = find_candidates(
        "My sister Rosa filed one complaint. My friend Rosa filed another."
    )
    assert [c.name for c in found] == ["Rosa", "Rosa"]


def test_an_abbreviated_title_is_not_split_across_sentences():
    """"Dr." must not read as a sentence end that strands the name after it."""
    found = find_candidates("Prof. Alvarez disagreed with the ruling.")
    assert [c.name for c in found] == ["Alvarez"]

    found = find_candidates("Mr. Alvarez filed the complaint.")
    assert [c.name for c in found] == ["Alvarez"]


def test_ordinary_sentences_still_split():
    text = (
        "My brother Daniel drove me to the hearing.\n"
        "Professor Alvarez disagreed with the ruling.\n"
        "Priya told me the permit never arrived."
    )
    found = find_candidates(text)
    assert [c.name for c in found] == ["Daniel", "Alvarez", "Priya"]
    assert [c.line for c in found] == [1, 2, 3]


def test_protecting_a_full_name_does_not_suppress_an_unrelated_first_name():
    found = find_candidates(
        "Dr. Vance graded it. My cousin Marcus told me about it.",
        protected=("Marcus Vance",),
    )
    assert [c.name for c in found] == ["Marcus"]
