"""Only a current verified grading batch can publish rehydrated drafts."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.grading.batch import (
    BatchPaths,
    GradingBatch,
    Progress,
    RenderResult,
    VerifiedBatch,
    VerificationInputs,
)
from scripts.grading.density import PATTERNS_PATH
from scripts.grading.records import GradeRecord
from scripts.redaction.tokens import TokenMap
from tests.grading.test_verify import _write_batch


class FakeRenderer:
    def __init__(
        self,
        batch: str = "essay-2",
        *,
        unmapped: dict[str, tuple[str, ...]] | None = None,
        errors: dict[str, str] | None = None,
    ) -> None:
        self.batch = batch
        self.rendered: list[str] = []
        self.unmapped = unmapped or {}
        self.errors = errors or {}

    def render(self, source: Path, destination: Path) -> RenderResult:
        self.rendered.append(source.name)
        if source.name in self.errors:
            return RenderResult(error=self.errors[source.name])
        text = "Maya Lin\n\n" + source.read_text(encoding="utf-8")
        destination.write_text(text, encoding="utf-8")
        return RenderResult(
            sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
            unmapped_tokens=self.unmapped.get(source.name, ()),
        )


def _add_second_draft(redacted: Path, drafts: Path) -> None:
    second_source = redacted / "STUDENT_02.md"
    second_source.write_bytes((redacted / "STUDENT_01.md").read_bytes())
    (drafts / "STUDENT_02.md").write_bytes(
        (drafts / "STUDENT_01.md").read_bytes()
    )
    record = GradeRecord.load(drafts / "STUDENT_01.json")
    record.token = "STUDENT_02"
    record.source = str(second_source)
    record.save(drafts / "STUDENT_02.json")


def test_only_persisted_verification_returns_verified_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    batch = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    )
    inputs = VerificationInputs(
        course="phil210",
        rubric=phil210 / "grading_rubric.md",
        profile=root / "profiles" / "phil210.md",
        patterns=PATTERNS_PATH,
    )

    dry_run = batch.verify(inputs, persist=False)
    persisted = batch.verify(inputs)

    assert dry_run.verified_batch is None
    assert isinstance(persisted.verified_batch, VerifiedBatch)
    assert not hasattr(batch, "publish")
    with pytest.raises(TypeError, match="persisted verification"):
        VerifiedBatch(batch.paths, "not-trusted", object())


def test_verified_batch_resume_requires_a_current_receipt(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )

    current = VerifiedBatch.resume(drafts)
    (drafts / "_verification.json").unlink()
    missing = VerifiedBatch.resume(drafts)

    assert isinstance(current.verified_batch, VerifiedBatch)
    assert current.findings == ()
    assert missing.verified_batch is None
    assert [finding.code for finding in missing.findings] == [
        "verification-required"
    ]


@pytest.mark.parametrize(
    ("change", "expected_code"),
    [
        ("stale", "stale-verification-receipt"),
        ("malformed", "malformed-verification-receipt"),
        ("unknown", "malformed-verification-receipt"),
    ],
)
def test_verified_batch_resume_rejects_untrusted_receipts(
    tmp_path, phil210, change, expected_code
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )
    receipt = drafts / "_verification.json"
    if change == "stale":
        (drafts / "STUDENT_01.md").write_text("changed", encoding="utf-8")
    elif change == "malformed":
        receipt.write_text("{not json", encoding="utf-8")
    else:
        payload = json.loads(receipt.read_text(encoding="utf-8"))
        payload["schema"] = "grading-batch-verification/99"
        receipt.write_text(json.dumps(payload), encoding="utf-8")

    resumed = VerifiedBatch.resume(drafts)

    assert resumed.verified_batch is None
    assert [finding.code for finding in resumed.findings] == [expected_code]


def test_verified_batch_publishes_complete_first_destination(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    verified = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    ).verified_batch
    assert verified is not None
    renderer = FakeRenderer()
    target = tmp_path / "final"

    report = verified.publish(target, renderer)

    assert report.findings == ()
    assert report.progress is Progress.REHYDRATED
    assert [path.name for path in report.published] == ["STUDENT_01.md"]
    assert renderer.rendered == ["STUDENT_01.md"]
    assert "Maya Lin" in (target / "STUDENT_01.md").read_text(encoding="utf-8")
    assert "STUDENT_01" not in (target / "STUDENT_01.md").read_text(encoding="utf-8")
    marker = json.loads((target / "_publication.json").read_text(encoding="utf-8"))
    assert marker["schema"] == "grading-batch-publication/1"
    assert marker["batch"] == "essay-2"
    assert len(marker["receipt_sha256"]) == 64
    assert marker["outputs"] == [
        {
            "path": "STUDENT_01.md",
            "sha256": hashlib.sha256(
                (target / "STUDENT_01.md").read_bytes()
            ).hexdigest(),
        }
    ]
    assert "Maya Lin" not in (target / "_publication.json").read_text(
        encoding="utf-8"
    )


def test_publish_revalidates_receipt_before_staging(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    verified = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    ).verified_batch
    assert verified is not None
    (drafts / "STUDENT_01.md").write_text("changed", encoding="utf-8")
    renderer = FakeRenderer()
    target = tmp_path / "final"

    report = verified.publish(target, renderer)

    assert "stale-verification-receipt" in {
        finding.code for finding in report.findings
    }
    assert all("verification" in finding.message.lower() for finding in report.findings)
    assert renderer.rendered == []
    assert not target.exists()


def test_publish_rejects_renderer_for_another_grading_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    verified = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    ).verified_batch
    assert verified is not None
    renderer = FakeRenderer(batch="essay-9")

    report = verified.publish(tmp_path / "final", renderer)

    assert [finding.code for finding in report.findings] == [
        "renderer-batch-mismatch"
    ]
    assert renderer.rendered == []


def test_first_publication_refuses_nonempty_destination(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    verified = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    ).verified_batch
    assert verified is not None
    renderer = FakeRenderer()
    target = tmp_path / "final"
    target.mkdir()
    existing = target / "keep.txt"
    existing.write_text("keep me", encoding="utf-8")

    report = verified.publish(target, renderer)

    assert [finding.code for finding in report.findings] == [
        "destination-not-empty"
    ]
    assert renderer.rendered == []
    assert existing.read_text(encoding="utf-8") == "keep me"


def test_publish_aggregates_renderer_failures_and_keeps_destination_untouched(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    _add_second_draft(redacted, drafts)
    verified = GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    ).verified_batch
    assert verified is not None
    renderer = FakeRenderer(
        unmapped={"STUDENT_01.md": ("STUDENT_99",)},
        errors={"STUDENT_02.md": "Could not render this draft."},
    )
    target = tmp_path / "final"
    target.mkdir()

    report = verified.publish(target, renderer)

    assert [finding.code for finding in report.findings] == [
        "unmapped-token",
        "render-failed",
    ]
    assert renderer.rendered == ["STUDENT_01.md", "STUDENT_02.md"]
    assert list(target.iterdir()) == []
    assert list(tmp_path.glob(".final-*")) == []


def test_rehydrate_cli_publishes_through_verified_batch(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    source = redacted / "STUDENT_01.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\nSTUDENT_01\n",
        encoding="utf-8",
    )
    draft = drafts / "STUDENT_01.md"
    draft.write_text(
        draft.read_text(encoding="utf-8").replace(
            "Your central claim holds.", "STUDENT_01, your central claim holds."
        ),
        encoding="utf-8",
    )
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )
    token_map = TokenMap("essay-2")
    token_map.allocate("STUDENT", "Maya Lin", "paper.md")
    map_path = tmp_path / "maps" / "essay-2.json"
    token_map.save(map_path)
    target = tmp_path / "final"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rehydrate.py",
            str(drafts),
            "--map",
            str(map_path),
            "--out",
            str(target),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Every rehydrated feedback file is a DRAFT" in result.stdout
    assert "Maya Lin" in (target / "STUDENT_01.md").read_text(encoding="utf-8")
    assert not (target / "STUDENT_01.json").exists()
    assert (target / "_publication.json").is_file()


def test_rehydrate_cli_requires_receipt_before_loading_map(tmp_path):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    (drafts / "STUDENT_01.md").write_text("feedback", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rehydrate.py",
            str(drafts),
            "--map",
            str(tmp_path / "missing-map.json"),
            "--out",
            str(tmp_path / "final"),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 5
    assert "verification-required" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "final").exists()


def test_rehydrate_cli_reports_unreadable_map_as_failure(tmp_path, phil210):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )
    target = tmp_path / "final"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rehydrate.py",
            str(drafts),
            "--map",
            str(tmp_path / "missing-map.json"),
            "--out",
            str(target),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 5
    assert "map-unreadable" in result.stderr
    assert "Traceback" not in result.stderr
    assert not target.exists()


def test_rehydrate_cli_leaves_destination_absent_for_unmapped_token(
    tmp_path, phil210
):
    redacted, drafts, root = _write_batch(tmp_path, phil210)
    source = redacted / "STUDENT_01.md"
    source.write_text(
        source.read_text(encoding="utf-8") + "\nSTUDENT_99\n",
        encoding="utf-8",
    )
    draft = drafts / "STUDENT_01.md"
    draft.write_text(
        draft.read_text(encoding="utf-8").replace(
            "Your central claim holds.", "STUDENT_99, your central claim holds."
        ),
        encoding="utf-8",
    )
    GradingBatch.open(
        BatchPaths(batch="essay-2", redacted=redacted, drafts=drafts)
    ).verify(
        VerificationInputs(
            course="phil210",
            rubric=phil210 / "grading_rubric.md",
            profile=root / "profiles" / "phil210.md",
            patterns=PATTERNS_PATH,
        )
    )
    token_map = TokenMap("essay-2")
    map_path = tmp_path / "maps" / "essay-2.json"
    token_map.save(map_path)
    target = tmp_path / "final"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/rehydrate.py",
            str(drafts),
            "--map",
            str(map_path),
            "--out",
            str(target),
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 5
    assert "unmapped-token" in result.stderr
    assert "STUDENT_99" in result.stderr
    assert not target.exists()
