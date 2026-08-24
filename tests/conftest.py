"""Shared fixtures for the redaction tests."""

from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "phil210"


@pytest.fixture
def phil210() -> Path:
    """Return the synthetic PHIL 210 corpus root."""
    return FIXTURES


@pytest.fixture
def instructor() -> str:
    """Return the instructor name, which must never be redacted."""
    return "Marcus Vance"
