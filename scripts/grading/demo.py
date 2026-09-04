"""Prepare the exact bundled synthetic corpus for a narrated grading demo."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from scripts.grading.brief import ANSWER_REQUIRED, approve_brief, prepare_brief
from scripts.grading.calibration import approve_synthetic
from scripts.grading.workflow import WorkflowContext, next_action
from scripts.path_safety import has_symlink_component, paths_overlap, read_bytes_limited
from scripts.redaction.pipeline import EXIT_OK, run as redact
from scripts.redaction.private_files import write_private_text
from scripts.redaction.roster import load_roster

DEMO_SCHEMA = "humanizer-grading-demo/1"
CORPUS_SCHEMA = "humanizer-grading-demo-corpus/1"
MAX_MANIFEST_BYTES = 1_048_576
MAX_DEMO_FILE_BYTES = 10 * 1_048_576


class DemoError(ValueError):
    """The requested folder is not eligible for unattended demo mode."""


@dataclass(frozen=True)
class DemoPreparation:
    """Identity-free paths and the first legal grading action."""

    run_id: str
    batch: str
    course: str
    source: Path
    state_root: Path
    publication: Path
    next_command: tuple[str, ...]
    next_action: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": DEMO_SCHEMA,
            "synthetic": True,
            "status": "ready-for-grading",
            "run_id": self.run_id,
            "batch": self.batch,
            "course": self.course,
            "source": str(self.source),
            "state_root": str(self.state_root),
            "publication": str(self.publication),
            "next_command": list(self.next_command),
            "next_action": self.next_action,
            "narration": [
                "The dropped folder matches the bundled synthetic corpus exactly.",
                "The demo uses the identity-free PHIL 210 profile built from four calibration samples, with paper 5 reserved as its holdout. It carries a synthetic calibration approval that is valid only inside this prepared run; a real course needs measured holdouts and the instructor's exact approval.",
                "The fixed synthetic assignment brief is authorized for demo use and bound to the current rubric and profile. Its review cadence is none, so the pilot and consistency stops that every real batch requires are skipped here.",
                "Five submissions were redacted locally and the bound synthetic review was completed; grading reads only tokenized files.",
            ],
        }


def _read_manifest(repo_root: Path) -> dict[str, object]:
    path = repo_root / "references/demo-corpus.json"
    try:
        raw = read_bytes_limited(path, "The demo corpus manifest", MAX_MANIFEST_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise DemoError(f"The bundled demo manifest is unavailable: {error}") from error
    if not isinstance(payload, dict) or payload.get("schema") != CORPUS_SCHEMA:
        raise DemoError("The bundled demo manifest has an unknown schema.")
    return payload


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(path))


def _validate_corpus(source: Path, manifest: dict[str, object]) -> None:
    if has_symlink_component(source) or not source.is_dir():
        raise DemoError("Demo mode requires one regular folder without symbolic links.")
    expected = manifest.get("files")
    if not isinstance(expected, dict) or not expected:
        raise DemoError("The bundled demo manifest has no file inventory.")

    actual: dict[str, Path] = {}
    try:
        for path in source.rglob("*"):
            if path.is_symlink():
                raise DemoError("The demo folder must not contain symbolic links.")
            if path.is_dir():
                continue
            if not path.is_file() or path.stat().st_nlink != 1:
                raise DemoError("The demo folder must contain only regular files.")
            actual[path.relative_to(source).as_posix()] = path
    except OSError as error:
        raise DemoError(f"The demo folder could not be inspected safely: {error}") from error

    if set(actual) != set(expected):
        raise DemoError("The folder does not match the exact bundled synthetic corpus.")
    for relative, expected_digest in expected.items():
        if not isinstance(relative, str) or not isinstance(expected_digest, str):
            raise DemoError("The bundled demo manifest has a malformed file inventory.")
        digest = hashlib.sha256(
            read_bytes_limited(actual[relative], "A synthetic demo file", MAX_DEMO_FILE_BYTES)
        ).hexdigest()
        if digest != expected_digest:
            raise DemoError("The folder does not match the exact bundled synthetic corpus.")


def _run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def _write_batch_roster(
    destination: Path, source: Path, allowed_ids: object
) -> None:
    if not isinstance(allowed_ids, list) or not all(
        isinstance(item, str) for item in allowed_ids
    ):
        raise DemoError("The bundled demo manifest has a malformed roster selection.")
    allowed = set(allowed_ids)
    rows = []
    with source.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            if row.get("student_id") in allowed:
                rows.append(row)
    if {row["student_id"] for row in rows} != allowed:
        raise DemoError("The bundled synthetic roster is incomplete.")
    output = io.StringIO()
    writer = csv.DictWriter(
        output, fieldnames=("name", "student_id", "email"), lineterminator="\n"
    )
    writer.writeheader()
    writer.writerows(rows)
    write_private_text(destination, output.getvalue())


def _answer_demo_brief(path: Path) -> None:
    answers = (
        "Course display name: PHIL/POLISCI 210\nCourse key: phil210\nAssignment: Essay 2 — Technology, Policy, and Modern Society (synthetic mixed-topic demo batch)\nThe five submissions use the standard essay rubric across energy, health care, right to repair, housing, and smart-city topics.",
        "Apply all four 25-point criteria. Prioritize a defensible thesis, specific evidence, serious counterargument analysis, and clear continuous academic prose without changing the rubric.",
        "Require named theorists, cases, statutes, datasets, or other specific credible sources when a claim needs evidence. Tie feedback to concrete paragraphs and sentences.",
        "Watch for broad generalizations, unsupported claims, missing citations, thin counterarguments, descriptive conclusions, slide-like lists, and AI buzzwords used in place of analysis.",
        "Write 180–320 words in the instructor's direct second-person voice. Include four to six inline annotations, The Bottom Line, What Worked, What to Work On, and the sign-off — Dr. Vance.",
        "This is a synthetic demonstration. Keep raw files, the roster, review file, token map, and rehydrated drafts outside model context. Read only redacted submissions and never send grades to an LMS.",
    )
    text = path.read_text(encoding="utf-8")
    for answer in answers:
        text = text.replace(ANSWER_REQUIRED, answer, 1)
    if ANSWER_REQUIRED in text:
        raise DemoError("The synthetic assignment brief template is incomplete.")
    write_private_text(path, text)


def _next_command(context: WorkflowContext) -> tuple[str, ...]:
    command = [
        "python3",
        "scripts/grade.py",
        "next",
        "--batch",
        context.batch,
        "--course",
        context.course,
        "--rubric",
        str(context.rubric),
        "--redacted",
        str(context.redacted),
        "--drafts",
        str(context.drafts),
        "--patterns",
        str(context.patterns),
        "--publication",
        str(context.publication),
        "--submissions",
        str(context.submissions),
        "--roster",
        str(context.roster),
    ]
    for instructor in context.instructor:
        command.extend(("--instructor", instructor))
    command.extend(("--root", str(context.root), "--json"))
    return tuple(command)


def prepare_demo(
    repo_root: Path, source: Path, *, runs_root: Path | None = None
) -> DemoPreparation:
    """Create a unique, synthetic-only run ready for the first draft pair."""
    repo_root = _absolute(repo_root)
    source = _absolute(source)
    manifest = _read_manifest(repo_root)
    _validate_corpus(source, manifest)

    runs_root = _absolute(runs_root or repo_root / ".grading/demo-runs")
    if has_symlink_component(runs_root) or paths_overlap(source, runs_root):
        raise DemoError("The demo run directory must be separate and free of symbolic links.")
    run_id = _run_id()
    state_root = runs_root / run_id
    state_root.mkdir(parents=True, exist_ok=False)
    state_root.chmod(0o700)

    batch = str(manifest["batch"])
    course = str(manifest["course"])
    rubric = source / "grading_rubric.md"
    profile = state_root / "profiles" / f"{course}.md"
    profile_source = repo_root / str(manifest["profile"])
    write_private_text(
        profile,
        read_bytes_limited(
            profile_source, "The synthetic grading profile", MAX_DEMO_FILE_BYTES
        ).decode("utf-8"),
    )

    approve_synthetic(state_root, course=course, profile=profile, rubric=rubric)

    roster = state_root / "private" / "essay-2.roster.csv"
    _write_batch_roster(
        roster,
        repo_root / str(manifest["roster"]),
        manifest.get("batch_student_ids"),
    )

    brief = state_root / "assignments" / f"{batch}.md"
    prepare_brief(brief, batch=batch, course=course, mode="local", review="none")
    _answer_demo_brief(brief)
    approve_brief(
        brief,
        batch=batch,
        course=course,
        rubric=rubric,
        profile=profile,
        approval=f"Approve assignment brief for {batch}",
    )

    redacted = state_root / "redacted" / batch
    map_path = state_root / "maps" / f"{batch}.json"
    review_path = state_root / "maps" / "reviews" / f"{batch}.json"
    redaction_args = {
        "source": source / "ungraded",
        "destination": redacted,
        "map_path": map_path,
        "review_path": review_path,
        "batch": batch,
        "protected": ("Marcus Vance",),
    }
    first = redact(roster=load_roster(roster), **redaction_args)
    if first.exit_code != EXIT_OK:
        raise DemoError("The exact synthetic corpus did not redact cleanly.")
    _validate_corpus(source, manifest)
    reviewed = redact(roster=[], reviewed=True, **redaction_args)
    if reviewed.exit_code != EXIT_OK:
        raise DemoError("The synthetic redaction review could not be bound.")

    context = WorkflowContext(
        batch=batch,
        course=course,
        rubric=rubric,
        redacted=redacted,
        drafts=state_root / "drafts" / batch,
        root=state_root,
        patterns=repo_root / "references/ai-patterns.md",
        publication=state_root / "final",
        submissions=source / "ungraded",
        roster=roster,
        instructor=("Marcus Vance",),
    )
    marker = {
        "schema": DEMO_SCHEMA,
        "synthetic": True,
        "authorization": "synthetic-demo",
        "status": "ready-for-grading",
        "run_id": run_id,
        "batch": batch,
        "course": course,
        "source": str(source),
    }
    write_private_text(state_root / "demo.json", json.dumps(marker, indent=2) + "\n")
    action = next_action(context)
    if action.action != "prepare-record":
        raise DemoError(
            "The synthetic demo did not reach its first grading action: "
            f"{action.action}."
        )
    return DemoPreparation(
        run_id=run_id,
        batch=batch,
        course=course,
        source=source,
        state_root=state_root,
        publication=context.publication,
        next_command=_next_command(context),
        next_action=action.as_dict(),
    )
