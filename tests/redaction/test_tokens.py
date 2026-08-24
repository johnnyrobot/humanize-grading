"""Tokens must be stable, kind-scoped, and stored owner-only."""

import json

import pytest

from scripts.redaction.tokens import TokenMap


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


def test_round_trip_preserves_values(tmp_path):
    tmap = TokenMap("essay-1")
    token = tmap.allocate("STUDENT", "Maya Lin", "paper_1.md")
    path = tmp_path / "essay-1.json"
    tmap.save(path)

    loaded = TokenMap.load(path)
    assert loaded.batch == "essay-1"
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
