"""Tokens must be stable, kind-scoped, and stored owner-only."""

import json

import pytest

from scripts.redaction.tokens import TokenMap, TokenMapLimitError


def test_same_value_returns_the_same_token():
    tmap = TokenMap("essay-1")
    first = tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    second = tmap.allocate("STUDENT", "Maya Lin", "paper_2.md")
    assert first == second == "STUDENT_01"


def test_matching_ignores_case():
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    assert tmap.allocate("STUDENT", "maya lin", "paper_2.md") == "STUDENT_01"


def test_each_kind_numbers_independently():
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    assert tmap.allocate("EMAIL", "mlin@example.edu", "paper_1.md") == "EMAIL_01"
    assert tmap.allocate("STUDENT", "Tyler Brooks", "paper_2.md") == "STUDENT_02"


def test_unknown_kind_is_rejected():
    tmap = TokenMap("essay-1")
    with pytest.raises(ValueError):
        tmap.allocate("NICKNAME", "Maya", "paper_1.md")


def test_saved_map_is_owner_only(tmp_path):
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)
    assert path.stat().st_mode & 0o777 == 0o600


def test_saving_a_map_replaces_a_symlink_without_touching_its_target(tmp_path):
    target = tmp_path / "unrelated.json"
    target.write_text("leave me alone", encoding="utf-8")
    path = tmp_path / "essay-1.json"
    path.symlink_to(target)
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")

    tmap.save(path)

    assert not path.is_symlink()
    assert target.read_text(encoding="utf-8") == "leave me alone"
    assert path.stat().st_mode & 0o777 == 0o600


def test_round_trip_preserves_values(tmp_path):
    tmap = TokenMap("essay-1")
    token = tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)

    loaded = TokenMap.load(path)
    assert loaded.batch == "essay-1"
    assert loaded.run_id == tmap.run_id
    assert loaded.value_for(token) == "Maya Lin"


def test_from_bytes_parses_the_supplied_snapshot(tmp_path):
    tmap = TokenMap("essay-1", run_id="test-redaction-run")
    token = tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)

    loaded = TokenMap.from_bytes(path.read_bytes())

    assert loaded.batch == "essay-1"
    assert loaded.run_id == "test-redaction-run"
    assert loaded.value_for(token) == "Maya Lin"


def test_saved_shape_is_one_entity_per_token(tmp_path):
    tmap = TokenMap("essay-1")
    tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entities"]["STUDENT_01"] == {
        "kind": "STUDENT",
        "value": "Maya Lin",
        "file": "paper_1.md",
    }


def test_tokens_past_ninety_nine_stay_unique_and_well_formed():
    """:02d pads to a minimum of two digits, so the 100th token is STUDENT_100.

    That is intended. The downstream token regex is \\d{2,}, so three-digit
    tokens match, and no token can match inside another at a word boundary.
    """
    tmap = TokenMap("essay-1")
    tokens = [tmap.allocate("STUDENT", f"Person {i}", "p.md") for i in range(101)]

    assert tokens[98] == "STUDENT_99"
    assert tokens[99] == "STUDENT_100"
    assert len(set(tokens)) == 101


def test_loaded_token_counters_continue_after_the_highest_persisted_number():
    payload = {
        "batch": "essay-1",
        "run_id": "run-identifier-1234",
        "entities": {
            "EMAIL_05": {
                "kind": "EMAIL",
                "value": "first@example.edu",
                "file": "paper.md",
            }
        },
    }

    loaded = TokenMap.from_bytes(json.dumps(payload).encode("utf-8"))

    assert loaded.allocate("EMAIL", "second@example.edu", "paper.md") == "EMAIL_06"


def test_only_id_values_have_a_short_fixed_candidate_limit():
    tmap = TokenMap("essay-1")

    with pytest.raises(TokenMapLimitError, match="ID value is too long"):
        tmap.allocate("ID", "A" * 129, "paper.md")

    assert tmap.allocate("URL", "a" * 20_000, "paper.md") == "URL_01"
