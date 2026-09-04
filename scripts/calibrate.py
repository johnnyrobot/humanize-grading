#!/usr/bin/env python3
"""Build calibration inputs, measure a profile against its holdouts, approve it.

Runs locally only. Calibration reads graded work that still carries names, so
it must not run when the instructor answers "cloud" at the gate.

Usage:
    python3 scripts/calibrate.py inputs --rubric RUBRIC --samples DIR \\
        --roster ROSTER --holdout NAME [--holdout NAME] \\
        [--holdout-out DIR] --out inputs.json

    python3 scripts/calibrate.py holdout --course COURSE --rubric RUBRIC \\
        --packet inputs.json --record R.json --draft R.md --sample SAMPLE.md \\
        [--record ... --draft ... --sample ...]

    python3 scripts/calibrate.py approve --course COURSE --rubric RUBRIC \\
        --approval "Approve calibration profile for COURSE" \\
        [--accept-gap --reason TEXT]

    python3 scripts/calibrate.py check --course COURSE --rubric RUBRIC

`inputs` is the safety step. It parses the rubric and every graded sample,
throws away the student essay text, scrubs roster names out of the
instructor's comments, and writes one JSON packet. Building a profile from
that packet is what keeps student prose and student names out of it.

`holdout` writes an identity-free receipt. `approve` binds the instructor's
exact phrase to the profile, rubric, and receipt. `check` says whether that
approval is still current.

Exit codes: 0 clean, 13 malformed input, 14 calibration not approved or
stale, 15 the holdouts showed a gap.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.calibration import (  # noqa: E402
    CalibrationError,
    approve_calibration,
    check_calibration,
    receipt_path,
    write_receipt,
)
from scripts.grading.holdout import (  # noqa: E402
    aggregate,
    compare,
    render_report,
    required_holdouts,
)
from scripts.grading.profile import ProfileError, load_profile, profile_path  # noqa: E402
from scripts.grading.records import GradeRecord, RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402
from scripts.grading.samples import (  # noqa: E402
    SampleError,
    load_sample,
    scrub_private_data,
    student_half,
)
from scripts.redaction.roster import load_roster  # noqa: E402
from scripts.redaction.private_files import write_private_text  # noqa: E402
from scripts.path_safety import (  # noqa: E402
    paths_overlap,
    read_bytes_limited,
    read_text_limited,
)

EXIT_OK = 0
EXIT_MALFORMED = 13
EXIT_UNAPPROVED = 14
EXIT_GAP = 15

MIN_SAMPLES = 3
MAX_SAMPLES = 8


def _component(label: str):
    def parse(value: str) -> str:
        from scripts.path_safety import require_component

        try:
            return require_component(value, label)
        except ValueError as error:
            raise argparse.ArgumentTypeError(str(error)) from error

    return parse


def _identity_key(value: str) -> str:
    """Normalize harmless name formatting for roster identity checks."""
    return " ".join(value.split()).casefold()


def _require_roster_identity(sample, roster, label: str) -> None:
    """Require one Student header that names one roster entry."""
    identities = [
        value
        for name, value in sample.header.items()
        if name.casefold() == "student"
    ]
    if len(identities) != 1 or not _identity_key(identities[0]):
        raise SampleError(f"{label} must have exactly one Student header.")
    identity = _identity_key(identities[0])
    matches = sum(_identity_key(entry.name) == identity for entry in roster)
    if matches != 1:
        raise SampleError(
            f"{label}'s Student header must match exactly one roster entry."
        )


def _inputs(args: argparse.Namespace) -> int:
    rubric = load_rubric(args.rubric)
    roster = load_roster(args.roster) if args.roster else []
    if not roster:
        print(
            f"{args.roster} was read but contains no entries. Past "
            "students' names cannot be scrubbed from the comment bank "
            "without a roster.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED

    paths = sorted(p for p in args.samples.rglob("*.md") if p.is_file())
    holdout_names = list(dict.fromkeys(args.holdout))
    holdout_paths: list[Path] = []
    for name in holdout_names:
        match = next((p for p in paths if p.name == name), None)
        if match is None:
            print(f"No sample named {name} under {args.samples}.", file=sys.stderr)
            return EXIT_MALFORMED
        holdout_paths.append(match)
    calibration = [p for p in paths if p not in holdout_paths]

    if len(calibration) < MIN_SAMPLES:
        print(
            f"Calibration needs at least three graded samples besides the "
            f"holdout. Found {len(calibration)}.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    if len(calibration) > MAX_SAMPLES:
        print(
            f"Using the first {MAX_SAMPLES} of {len(calibration)} samples. "
            "More adds little and costs a long read."
        )
        calibration = calibration[:MAX_SAMPLES]
    needed = required_holdouts(len(calibration) + len(holdout_paths))
    if len(holdout_paths) < needed:
        print(
            f"{len(calibration) + len(holdout_paths)} graded samples need at "
            f"least {'two holdouts' if needed == 2 else 'one holdout'}. Name "
            f"{needed - len(holdout_paths)} more with --holdout.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    if args.holdout_out is not None:
        if paths_overlap(args.holdout_out, args.samples):
            print(
                "--holdout-out must not sit inside the samples folder.",
                file=sys.stderr,
            )
            return EXIT_MALFORMED
        if args.holdout_out.exists() and (
            not args.holdout_out.is_dir() or any(args.holdout_out.iterdir())
        ):
            print("--holdout-out must be a new or empty folder.", file=sys.stderr)
            return EXIT_MALFORMED

    loaded_calibration = []
    for index, path in enumerate(calibration, start=1):
        sample = load_sample(path)
        _require_roster_identity(sample, roster, f"Calibration sample {index}")
        loaded_calibration.append(sample)
    for index, path in enumerate(holdout_paths, start=1):
        holdout_sample = load_sample(path)
        _require_roster_identity(holdout_sample, roster, f"Holdout sample {index}")

    def packet(index: int, sample) -> dict:
        comments, found = scrub_private_data(sample.comments_only(), roster)
        annotations, _ = scrub_private_data(
            "\n".join(a.text for a in sample.annotations), roster
        )
        # This packet is the artifact that keeps names out of the profile, so
        # it must not itself carry them: record how many name forms were
        # scrubbed, never which ones. The filename is left out for the same
        # reason -- identity scrubbing does not reliably clean it
        # and \b never fires inside an underscore-joined "Lastname_First"
        # name), and gradebook exports routinely embed the student's name
        # in the filename itself. A position label carries no identity.
        return {
            "label": f"sample_{index}",
            "score": sample.score,
            "possible": sample.possible,
            "letter": sample.letter,
            "annotation_count": len(sample.annotations),
            "annotations": annotations.splitlines(),
            "comments": comments,
            "scrubbed_count": len(set(found)),
        }

    payload = {
        "rubric": {
            "path": str(args.rubric),
            "title": rubric.title,
            "digest": rubric.digest,
            "context_digest": rubric.context_digest,
            "total_points": rubric.total_points,
            "criteria": [
                {
                    "number": c.number,
                    "name": c.name,
                    "points": c.points,
                    "digest": c.digest,
                    "levels": [
                        {"name": l.name, "band": l.band, "descriptor": l.descriptor}
                        for l in c.levels
                    ],
                }
                for c in rubric.criteria
            ],
            "bands": [
                {"low": b.low, "high": b.high, "label": b.label} for b in rubric.bands
            ],
            "protocol": {
                "sections": list(rubric.protocol.sections),
                "sign_offs": list(rubric.protocol.sign_offs),
                "requires_inline_annotations": (
                    rubric.protocol.requires_inline_annotations
                ),
            },
        },
        "samples": [
            packet(index, sample)
            for index, sample in enumerate(loaded_calibration, start=1)
        ],
        "holdout": {
            "count": len(holdout_paths),
            "labels": [f"holdout_{index}" for index in range(1, len(holdout_paths) + 1)],
        },
        "note": (
            "This packet holds the instructor's comments only. No student "
            "essay text and no student name is in it. Sample labels are "
            "positions in the sorted sample listing, not filenames -- "
            "filenames are deliberately not carried because they commonly "
            "embed student names. Build the profile from this, never from "
            "the sample files directly."
        ),
    }

    write_private_text(args.out, json.dumps(payload, indent=2, ensure_ascii=False))
    scrubbed = sum(item["scrubbed_count"] for item in payload["samples"])
    print(
        f"Wrote {args.out}. {len(payload['samples'])} calibration samples, "
        f"{len(holdout_paths)} held back."
    )
    print(f"Scrubbed {scrubbed} name forms from the comments.")
    if args.holdout_out is not None:
        for path in holdout_paths:
            text = read_text_limited(path, "The holdout sample", 10 * 1024 * 1024)
            write_private_text(args.holdout_out / path.name, student_half(text))
        print(
            f"Wrote {len(holdout_paths)} blind holdout submissions to "
            f"{args.holdout_out}. Grade them as a holdout batch."
        )
    print("Read references/calibration.md before building the profile.")
    return EXIT_OK


def _holdout(args: argparse.Namespace) -> int:
    if not (len(args.record) == len(args.draft) == len(args.sample)):
        print(
            "Pass one --record, one --draft, and one --sample per holdout.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    profile = load_profile(profile_path(args.course, args.root))
    if profile.course != args.course:
        print(
            f"The profile belongs to {profile.course!r}, not {args.course!r}.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    rubric = load_rubric(args.rubric)
    drifted = profile.drift(rubric)
    if drifted:
        print(
            "The rubric changed since calibration: " + "; ".join(drifted),
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    packet_bytes = read_bytes_limited(
        args.packet, "The calibration packet", 10 * 1024 * 1024
    )
    packet = json.loads(packet_bytes.decode("utf-8"))
    if not isinstance(packet, dict):
        print("The calibration packet must be a JSON object.", file=sys.stderr)
        return EXIT_MALFORMED
    packet_rubric = packet.get("rubric")
    packet_digest = packet_rubric.get("digest") if isinstance(packet_rubric, dict) else None
    if packet_digest != rubric.digest or profile.rubric_digest != rubric.digest:
        print(
            "The packet, profile, and rubric digests must all match. Build the "
            "packet and profile from the current rubric.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    samples = packet.get("samples")
    holdout = packet.get("holdout")
    count = holdout.get("count") if isinstance(holdout, dict) else None
    if not isinstance(samples, list) or not isinstance(count, int):
        print(
            "The calibration packet is malformed. Build it again with "
            "calibrate.py inputs.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    if profile.samples != len(samples):
        print(
            f"The profile says **Samples**: {profile.samples} but the packet "
            f"holds {len(samples)}. Fix the profile header.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    if len(args.record) != count:
        print(
            f"The packet held back {count} holdouts; pass exactly {count} holdouts.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED
    deltas = []
    for record_path, draft_path, sample_path in zip(args.record, args.draft, args.sample):
        record = GradeRecord.load(record_path)
        if record.course != args.course:
            print(f"{record_path} belongs to course {record.course!r}.", file=sys.stderr)
            return EXIT_MALFORMED
        feedback = read_text_limited(draft_path, "The holdout draft", 1_048_576)
        sample = load_sample(sample_path)
        deltas.append(
            compare(
                record,
                feedback,
                sample,
                point_tolerance=profile.point_tolerance,
                length_tolerance=profile.length_tolerance,
            )
        )
    summary = aggregate(deltas, required=required_holdouts(len(samples) + count))
    receipt = write_receipt(
        receipt_path(args.root, args.course),
        course=args.course,
        profile_sha256=hashlib.sha256(
            read_bytes_limited(profile.path, "The calibration profile", 5 * 1024 * 1024)
        ).hexdigest(),
        rubric_sha256=hashlib.sha256(
            read_bytes_limited(rubric.path, "The rubric", 5 * 1024 * 1024)
        ).hexdigest(),
        packet_sha256=hashlib.sha256(packet_bytes).hexdigest(),
        samples=len(samples),
        deltas=deltas,
        summary=summary,
        tolerances={
            "points": profile.point_tolerance,
            "length_ratio": profile.length_tolerance,
        },
    )
    if args.json:
        print(
            json.dumps(
                {
                    "receipt": str(receipt.path),
                    "passed": receipt.passed,
                    "aggregate": receipt.aggregate,
                    "holdouts": [asdict(delta) for delta in deltas],
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(render_report(deltas, summary))
        print(f"{summary.holdouts} holdouts measured. Wrote {receipt.path}.")
    return EXIT_OK if receipt.passed else EXIT_GAP


def _approve(args: argparse.Namespace) -> int:
    profile = load_profile(profile_path(args.course, args.root))
    try:
        approval = approve_calibration(
            args.root,
            course=args.course,
            profile=profile.path,
            rubric=args.rubric,
            approval=args.approval,
            accept_gap=args.accept_gap,
            reason=args.reason or "",
        )
    except CalibrationError as error:
        print(f"ERROR calibration: {error}", file=sys.stderr)
        return EXIT_UNAPPROVED
    print(f"Approved calibration profile: {approval.path}")
    return EXIT_OK


def _check(args: argparse.Namespace) -> int:
    profile = load_profile(profile_path(args.course, args.root))
    status = check_calibration(
        args.root, course=args.course, profile=profile.path, rubric=args.rubric
    )
    if status.state != "current":
        print(f"ERROR calibration {status.state}: {status.message}", file=sys.stderr)
        return EXIT_UNAPPROVED
    print(f"Calibration for {args.course} is approved and current.")
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inputs = subparsers.add_parser("inputs", help="Build the calibration packet")
    inputs.add_argument("--rubric", type=Path, required=True)
    inputs.add_argument("--samples", type=Path, required=True)
    inputs.add_argument("--roster", type=Path, required=True)
    inputs.add_argument(
        "--holdout",
        action="append",
        required=True,
        help="Filename to hold back; repeat for each holdout",
    )
    inputs.add_argument(
        "--holdout-out",
        type=Path,
        default=None,
        help="Write each holdout's student half here for blind grading",
    )
    inputs.add_argument(
        "--out", type=Path, default=Path("calibration-inputs.json")
    )
    inputs.set_defaults(handler=_inputs)

    holdout = subparsers.add_parser(
        "holdout", help="Measure the profile against its holdouts and write a receipt"
    )
    holdout.add_argument("--course", required=True, type=_component("The course"))
    holdout.add_argument("--rubric", type=Path, required=True)
    holdout.add_argument("--packet", type=Path, required=True)
    holdout.add_argument("--record", type=Path, action="append", required=True)
    holdout.add_argument("--draft", type=Path, action="append", required=True)
    holdout.add_argument("--sample", type=Path, action="append", required=True)
    holdout.add_argument("--root", type=Path, default=Path(".grading"))
    holdout.add_argument("--json", action="store_true")
    holdout.set_defaults(handler=_holdout)

    approve = subparsers.add_parser(
        "approve", help="Record the instructor's exact profile approval"
    )
    approve.add_argument("--course", required=True, type=_component("The course"))
    approve.add_argument("--rubric", type=Path, required=True)
    approve.add_argument("--approval", required=True)
    approve.add_argument(
        "--accept-gap",
        action="store_true",
        help="Approve even though the holdouts showed a gap; needs --reason",
    )
    approve.add_argument("--reason", default="")
    approve.add_argument("--root", type=Path, default=Path(".grading"))
    approve.set_defaults(handler=_approve)

    check = subparsers.add_parser(
        "check", help="Say whether the profile approval is still current"
    )
    check.add_argument("--course", required=True, type=_component("The course"))
    check.add_argument("--rubric", type=Path, required=True)
    check.add_argument("--root", type=Path, default=Path(".grading"))
    check.set_defaults(handler=_check)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (
        CalibrationError,
        ProfileError,
        RubricError,
        SampleError,
        RecordError,
        FileNotFoundError,
        OSError,
        UnicodeError,
        ValueError,
    ) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED


if __name__ == "__main__":
    raise SystemExit(main())
