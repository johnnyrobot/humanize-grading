"""Prepare one valid, deliberately unfinished grading draft pair."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from scripts.grading.density import load_terms, scan
from scripts.grading.records import CANNOT_ASSESS, CriterionScore, GradeRecord
from scripts.grading.rubric import Rubric
from scripts.path_safety import has_symlink_component, read_text_limited, require_component

TEMPLATE_MARKER = "<!-- replace-with-feedback -->"
_TOKEN_NAME = re.compile(r"^(?:STUDENT|UNKNOWN)_\d{2,}$")

@dataclass(frozen=True)
class DraftPair:
    """The two identity-free files an agent fills for one submission."""

    record: Path
    feedback: Path


class DraftPreparationError(ValueError):
    """A safe draft pair could not be prepared."""


def _cleanup_paths(paths: list[Path], action: str) -> list[str]:
    """Try every unlink and return path-specific errors without raising."""
    failures: list[str] = []
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError as error:
            failures.append(f"{action} could not remove {path}: {error}")
    return failures


def _stage_text(directory: Path, *, suffix: str, text: str) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=".draft-pair.", suffix=suffix, dir=directory
    )
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception as error:
        cleanup_failures = _cleanup_paths([path], "Staging cleanup")
        if cleanup_failures:
            raise DraftPreparationError(
                f"Could not stage {path}: {error}; " + "; ".join(cleanup_failures)
            ) from error
        raise
    return path


def _install_without_overwrite(staged: Path, destination: Path) -> None:
    """Install a same-filesystem stage without replacing an existing path."""
    os.link(staged, destination)


def _feedback_template(rubric: Rubric) -> str:
    if not rubric.protocol.sections:
        lines = ["**Feedback**", "", TEMPLATE_MARKER]
        if rubric.protocol.sign_offs:
            lines.extend(("", rubric.protocol.sign_offs[0]))
        return "\n".join(lines) + "\n"
    lines: list[str] = []
    if rubric.protocol.requires_inline_annotations:
        lines.extend((f"[{TEMPLATE_MARKER}]", ""))
    for section in rubric.protocol.sections:
        lines.extend((f"**{section}**", "", TEMPLATE_MARKER, ""))
    if rubric.protocol.sign_offs:
        lines.append(rubric.protocol.sign_offs[0])
    return "\n".join(lines).rstrip() + "\n"


def prepare_draft_pair(
    *,
    batch: str,
    course: str,
    source: Path,
    rubric: Rubric,
    profile: Path,
    patterns: Path,
    drafts: Path,
) -> DraftPair:
    """Create a loadable record and rubric-derived feedback template."""
    try:
        require_component(batch, "The grading batch")
        require_component(course, "The course")
    except ValueError as error:
        raise DraftPreparationError(str(error)) from error
    if has_symlink_component(source) or not source.is_file():
        raise DraftPreparationError(
            "The redacted source must be one regular file without a symbolic link."
        )
    if has_symlink_component(drafts):
        raise DraftPreparationError(
            "The drafts directory must not use a symbolic link."
        )
    token = source.stem
    if not _TOKEN_NAME.fullmatch(token):
        raise DraftPreparationError(
            "The redacted source name must use the STUDENT_NN or UNKNOWN_NN token shape."
        )
    feedback = drafts / f"{token}.md"
    record_path = drafts / f"{token}.json"
    if (
        feedback.exists()
        or feedback.is_symlink()
        or record_path.exists()
        or record_path.is_symlink()
    ):
        raise DraftPreparationError(
            f"A draft pair member already exists for {token}. Nothing was overwritten."
        )
    try:
        source_text = read_text_limited(
            source, "The redacted submission", 25 * 1024 * 1024
        )
        density = scan(source_text, load_terms(patterns)).as_dict()
    except (OSError, UnicodeError, ValueError) as error:
        raise DraftPreparationError(
            f"Could not read the identity-free draft inputs: {error}"
        ) from error
    record = GradeRecord(
        batch=batch,
        token=token,
        source=str(source),
        course=course,
        rubric=str(rubric.path),
        profile=str(profile),
        criteria=tuple(
            CriterionScore(
                number=criterion.number,
                name=criterion.name,
                points_possible=criterion.points,
                score=None,
                level=CANNOT_ASSESS,
                assessable=False,
                justifications=(),
            )
            for criterion in rubric.criteria
        ),
        total=0,
        total_possible=0,
        letter=CANNOT_ASSESS,
        pattern_density=density,
        status="draft",
    )
    try:
        drafts.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise DraftPreparationError(
            f"Could not create the drafts directory: {error}"
        ) from error
    if has_symlink_component(drafts):
        raise DraftPreparationError(
            "The drafts directory must not use a symbolic link."
        )
    staged: list[Path] = []
    installed: list[Path] = []
    try:
        staged_record = _stage_text(
            drafts,
            suffix=".json.tmp",
            text=json.dumps(record.as_dict(), indent=2, ensure_ascii=False) + "\n",
        )
        staged.append(staged_record)
        staged_feedback = _stage_text(
            drafts, suffix=".md.tmp", text=_feedback_template(rubric)
        )
        staged.append(staged_feedback)

        _install_without_overwrite(staged_record, record_path)
        installed.append(record_path)
        _install_without_overwrite(staged_feedback, feedback)
        installed.append(feedback)
    except Exception as error:
        cleanup_failures = _cleanup_paths(
            list(reversed(installed)), "Rollback"
        )
        cleanup_failures.extend(_cleanup_paths(staged, "Staging cleanup"))
        detail = f"Could not prepare the draft pair for {token}: {error}"
        if cleanup_failures:
            detail += "; " + "; ".join(cleanup_failures)
        raise DraftPreparationError(
            detail
        ) from error
    staging_failures = _cleanup_paths(staged, "Staging cleanup")
    if staging_failures:
        rollback_failures = _cleanup_paths(
            list(reversed(installed)), "Rollback"
        )
        raise DraftPreparationError(
            f"Could not finish the draft pair for {token}: "
            + "; ".join(staging_failures + rollback_failures)
        )
    return DraftPair(record=record_path, feedback=feedback)
