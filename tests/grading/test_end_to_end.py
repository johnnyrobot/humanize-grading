"""Redact, grade, verify, compare, rehydrate. The whole chain, on the corpus.

The grading itself is done here in plain Python rather than by a model. That
is deliberate: this test proves the plumbing, the checks, and the round trip.
Whether a model's judgement is any good is what the holdout report answers.
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import fields
from pathlib import Path

import pytest

from scripts.grading.batch import (
    BatchPaths,
    GradingBatch,
    Progress,
    RenderResult,
    VerificationInputs,
)
from scripts.grading.density import PATTERNS_PATH, load_terms, scan
from scripts.grading.records import CriterionScore, GradeRecord, Justification, locate
from scripts.grading.rubric import load_rubric
from scripts.redaction.pipeline import EXIT_OK, run
from scripts.redaction.roster import load_roster

FEEDBACK_TEMPLATE = """[{annotation}]

**The Bottom Line**

Your argument holds. You named the mechanism instead of gesturing at it,
and the evidence you chose does real work.

**What Worked**

* Paragraph 2 does the analytical work the thesis promises.

**What to Work On**

* Give the strongest counterargument a full paragraph, not a clause.
* Name the statute or the case rather than describing it.

Come see me in office hours if you want to push this further.

— Dr. Vance
"""


def _unique_sentences(source: str, count: int) -> list[str]:
    """Return sentences long enough to be evidence and unique in the source."""
    picked: list[str] = []
    for raw in re.split(r"(?<=[.!?])\s+", source):
        sentence = " ".join(raw.split())
        if len(sentence.split()) < 12:
            continue
        try:
            locate(sentence, source, None)
        except Exception:
            continue
        picked.append(sentence)
        if len(picked) == count:
            break
    return picked


def _grade(token: str, source_path: Path, rubric, terms) -> tuple[GradeRecord, str]:
    source = source_path.read_text(encoding="utf-8")
    quotes = _unique_sentences(source, len(rubric.criteria))
    assert len(quotes) == len(rubric.criteria), f"{source_path} is too short to grade"

    scores = [21, 20, 19, 22]
    criteria = tuple(
        CriterionScore(
            number=criterion.number,
            name=criterion.name,
            points_possible=criterion.points,
            score=scores[index],
            level="Proficient",
            assessable=True,
            justifications=(Justification(quote=quotes[index]),),
        )
        for index, criterion in enumerate(rubric.criteria)
    )
    total = sum(scores)
    possible = sum(c.points for c in rubric.criteria)
    record = GradeRecord(
        batch="essay-2",
        token=token,
        source=str(source_path),
        course="phil210",
        rubric="tests/fixtures/phil210/grading_rubric.md",
        profile="tests/fixtures/phil210/profile_phil210.md",
        criteria=criteria,
        total=total,
        total_possible=possible,
        letter=rubric.letter_for(100 * total / possible),
        pattern_density=scan(source, terms).as_dict(),
    )
    feedback = FEEDBACK_TEMPLATE.format(
        annotation=f"This sentence carries the paragraph. Lead with it. ({token})"
    )
    return record, feedback


@pytest.fixture
def graded_batch(tmp_path, phil210, instructor):
    # Only five of the ten roster students submitted this batch; the other
    # five appear in graded/ instead. Passing the whole roster makes
    # redaction exit EXIT_ROSTER, which is correct behaviour and is already
    # covered by Plan 1's pipeline tests. This test is about the grading
    # chain, so it hands redaction a roster that matches the batch.
    corpus = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((phil210 / "ungraded").glob("*.md"))
    )
    roster = [
        entry for entry in load_roster(phil210 / "roster.csv")
        if entry.name in corpus
    ]
    assert len(roster) == 5, "the ungraded batch should name five students"

    redacted = tmp_path / "redacted"
    result = run(
        source=phil210 / "ungraded",
        destination=redacted,
        map_path=tmp_path / "maps" / "essay-2.json",
        roster=roster,
        batch="essay-2",
        protected=(instructor,),
        reviewed=True,
    )
    assert result.exit_code == EXIT_OK, result.message
    return tmp_path, redacted


def test_the_chain_runs_from_redaction_to_final_feedback(
    graded_batch, phil210, instructor
):
    tmp_path, redacted = graded_batch
    drafts = tmp_path / "drafts"
    drafts.mkdir()

    rubric = load_rubric(phil210 / "grading_rubric.md")
    terms = load_terms(PATTERNS_PATH)

    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    before = batch.status()
    assert before.entries, "redaction produced no files"
    assert before.counts()["pending"] == len(before.entries)

    for entry in before.entries:
        assert entry.source is not None
        record, feedback = _grade(entry.token, entry.source, rubric, terms)
        record.save(drafts / f"{entry.token}.json")
        (drafts / f"{entry.token}.md").write_text(feedback, encoding="utf-8")

    after = batch.status()
    assert after.counts()["drafted"] == len(after.entries)
    verification = batch.verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=phil210 / "profile_phil210.md",
            patterns=PATTERNS_PATH,
        )
    )
    assert verification.blocking_findings == ()
    assert verification.receipt is not None
    assert all(
        entry.progress is Progress.VERIFIED for entry in batch.status().entries
    )

    consistency = batch.consistency()
    assert consistency.blocking_findings == ()
    assert any(flag.kind == "reused-sentence" for flag in consistency.flags), (
        "identical feedback across the batch must be flagged"
    )

    final = tmp_path / "final"
    rehydrated = subprocess.run(
            [
                sys.executable, "scripts/rehydrate.py", str(drafts),
                "--batch", "essay-2",
                "--course", "phil210",
                "--rubric", str(phil210 / "grading_rubric.md"),
                "--profile", str(phil210 / "profile_phil210.md"),
                "--redacted", str(redacted),
                "--map", str(tmp_path / "maps" / "essay-2.json"),
                "--out", str(final),
            ],
        capture_output=True, text=True,
    )
    assert rehydrated.returncode == 0, rehydrated.stderr
    assert list(final.glob("*.md"))
    assert list(final.glob("*.json")) == [final / "_publication.json"]
    assert (final / "_publication.json").is_file()


def test_no_student_name_reaches_a_draft(graded_batch, phil210):
    tmp_path, redacted = graded_batch
    rubric = load_rubric(phil210 / "grading_rubric.md")
    terms = load_terms(PATTERNS_PATH)
    names = {
        part
        for entry in load_roster(phil210 / "roster.csv")
        for part in entry.name.split()
    }
    batch = GradingBatch.open(
        BatchPaths(
            batch="essay-2",
            redacted=redacted,
            drafts=tmp_path / "absent",
        )
    )
    for entry in batch.status().entries:
        assert entry.source is not None
        record, feedback = _grade(entry.token, entry.source, rubric, terms)
        blob = feedback + "".join(
            j.quote for c in record.criteria for j in c.justifications
        )
        # Whole words only. "Lin" sits inside "The Bottom Line", which the
        # rubric's own protocol requires, and that is not a leak.
        for name in names:
            assert not re.search(rf"\b{re.escape(name)}\b", blob), (
                f"{name} reached a draft for {entry.token}"
            )


def test_the_batch_resumes_instead_of_regrading(graded_batch):
    tmp_path, redacted = graded_batch
    drafts = tmp_path / "drafts2"
    drafts.mkdir()
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    entries = batch.status().entries
    first = entries[0]
    (drafts / f"{first.token}.md").write_text("feedback", encoding="utf-8")
    (drafts / f"{first.token}.json").write_text("{}", encoding="utf-8")
    pending = [
        entry.token
        for entry in batch.status().entries
        if entry.progress is Progress.PENDING
    ]
    assert first.token not in pending
    assert len(pending) == len(entries) - 1


def test_the_grading_scripts_never_open_the_map():
    """Invariant 1, checked by reading the code rather than trusting it."""
    for path in Path("scripts/grading").glob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "TokenMap" not in text, f"{path} must not load the token map"
        assert "maps/" not in text, f"{path} must not reference the map directory"
    grade_cli = Path("scripts/grade.py").read_text(encoding="utf-8")
    assert "TokenMap" not in grade_cli
    assert "text" not in {field.name for field in fields(RenderResult)}
