"""Layer 4 must flag people, not cited scholars or platforms."""

import time
import tracemalloc

from scripts.redaction.candidates import find_candidates, review_candidates


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


def test_a_cited_scholars_possessive_is_not_flagged():
    """The citation and the possessive sit in different sentences."""
    found = find_candidates(
        "Gillespie (2014) frames algorithms as editorial. "
        "I find Gillespie's framing persuasive."
    )
    assert found == []


def test_an_uncited_possessive_is_still_flagged():
    found = find_candidates("Elena's testimony changed my view.")
    assert [c.name for c in found] == ["Elena"]


def test_a_cited_scholar_does_not_suppress_a_strong_signal():
    """A relation word is strong evidence and must survive document-wide
    scholar suppression -- this is the guard against re-introducing the
    Brady bug from an earlier round."""
    found = find_candidates(
        "Brady (2017) measured outrage. "
        "My cousin Brady told me about the eviction."
    )
    assert [c.name for c in found] == ["Brady"]


def test_protected_overlap_collection_is_linear_and_keeps_longest_form():
    text = "\n".join("Marcus Vance reviewed it." for _ in range(5000))

    started = time.perf_counter()
    review = review_candidates(text, protected=("Marcus Vance", "Vance"))
    elapsed = time.perf_counter() - started

    assert len(review.excluded) == 5000
    assert {item.name for item in review.excluded} == {"Marcus Vance"}
    assert review.excluded[0].line == 1
    assert review.excluded[-1].line == 5000
    assert "reviewed it" in review.excluded[0].context
    assert elapsed < 1.0


def test_newline_dense_review_does_not_store_every_newline_offset():
    text = "\n" * 300_000 + "My brother Daniel drove me."

    tracemalloc.start()
    try:
        review = review_candidates(text)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert [item.name for item in review.candidates] == ["Daniel"]
    assert review.candidates[0].line == 300_001
    assert peak < 6 * 1024 * 1024


def test_mixed_blank_lines_keep_protected_candidate_and_citation_lines():
    text = (
        "\n\n"
        "Dr. Marcus Vance reviewed it.\n"
        "\n\n"
        "My brother Daniel drove me.\n"
        "\n"
        "Gillespie (2014) frames algorithms.\n"
        "I find Gillespie's framing useful."
    )

    review = review_candidates(text, protected=("Marcus Vance",))

    assert [(item.name, item.line) for item in review.candidates] == [("Daniel", 6)]
    assert {(item.name, item.reason, item.line) for item in review.excluded} == {
        ("Marcus Vance", "protected", 3),
        ("Gillespie", "citation", 9),
    }
    assert "reviewed it" in next(
        item.context for item in review.excluded if item.name == "Marcus Vance"
    )
