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


@pytest.fixture
def profile() -> Path:
    """Return the hand-written reference profile for PHIL 210."""
    return FIXTURES / "profile_phil210.md"


@pytest.fixture
def approved_brief():
    """Create one answered brief bound to the supplied grading context."""

    def make(
        path,
        *,
        batch,
        course,
        rubric,
        profile,
        mode="local",
        purpose="grading",
        review="pilot",
        pilot=2,
    ):
        from scripts.grading.brief import approve_brief, prepare_brief

        if purpose == "holdout":
            review = "none"
        prepare_brief(
            path,
            batch=batch,
            course=course,
            mode=mode,
            purpose=purpose,
            review=review,
            pilot=pilot,
        )
        text = path.read_text(encoding="utf-8").replace(
            "<!-- answer-required -->", "Use the rubric and calibrated profile."
        )
        path.write_text(text, encoding="utf-8")
        approve_brief(
            path,
            batch=batch,
            course=course,
            rubric=rubric,
            profile=profile,
            approval=f"Approve assignment brief for {batch}",
        )
        return path

    return make
