#!/usr/bin/env python3
"""Build calibration inputs and check a profile against a held-back sample.

Runs locally only. Calibration reads graded work that still carries names, so
it must not run when the instructor answers "cloud" at the gate.

Usage:
    python3 scripts/calibrate.py inputs --rubric RUBRIC --samples DIR \\
        --roster ROSTER --holdout NAME --out inputs.json

    python3 scripts/calibrate.py holdout --record R.json --draft R.md \\
        --sample SAMPLE.md

`inputs` is the safety step. It parses the rubric and every graded sample,
throws away the student essay text, scrubs roster names out of the
instructor's comments, and writes one JSON packet. Building a profile from
that packet is what keeps student prose and student names out of it.

Exit codes: 0 clean, 13 malformed input.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.grading.holdout import compare, render  # noqa: E402
from scripts.grading.records import GradeRecord, RecordError  # noqa: E402
from scripts.grading.rubric import RubricError, load_rubric  # noqa: E402
from scripts.grading.samples import SampleError, load_sample, scrub_names  # noqa: E402
from scripts.redaction.roster import load_roster  # noqa: E402

EXIT_OK = 0
EXIT_MALFORMED = 13

MIN_SAMPLES = 3
MAX_SAMPLES = 8


def _inputs(args: argparse.Namespace) -> int:
    rubric = load_rubric(args.rubric)
    roster = load_roster(args.roster) if args.roster else []
    if not roster:
        print(
            "No roster given. Past students' names cannot be scrubbed from "
            "the comment bank. Supply --roster.",
            file=sys.stderr,
        )
        return EXIT_MALFORMED

    paths = sorted(p for p in args.samples.rglob("*.md") if p.is_file())
    holdout_path = next((p for p in paths if p.name == args.holdout), None)
    if holdout_path is None:
        print(f"No sample named {args.holdout} under {args.samples}.", file=sys.stderr)
        return EXIT_MALFORMED
    calibration = [p for p in paths if p != holdout_path]

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

    def packet(path: Path) -> dict:
        sample = load_sample(path)
        comments, found = scrub_names(sample.comments_only(), roster)
        annotations, _ = scrub_names(
            "\n".join(a.text for a in sample.annotations), roster
        )
        # This packet is the artifact that keeps names out of the profile, so
        # it must not itself carry them: record how many name forms were
        # scrubbed, never which ones.
        return {
            "path": str(path),
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
        "samples": [packet(path) for path in calibration],
        "holdout": {"path": str(holdout_path)},
        "note": (
            "This packet holds the instructor's comments only. No student "
            "essay text and no student name is in it. Build the profile from "
            "this, never from the sample files directly."
        ),
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    scrubbed = sum(item["scrubbed_count"] for item in payload["samples"])
    print(
        f"Wrote {args.out}. {len(payload['samples'])} calibration samples, "
        f"holdout {holdout_path.name}."
    )
    print(f"Scrubbed {scrubbed} name forms from the comments.")
    print("Read references/calibration.md before building the profile.")
    return EXIT_OK


def _holdout(args: argparse.Namespace) -> int:
    record = GradeRecord.load(args.record)
    feedback = args.draft.read_text(encoding="utf-8")
    sample = load_sample(args.sample)
    delta = compare(record, feedback, sample)
    if args.json:
        print(json.dumps(asdict(delta), indent=2, ensure_ascii=False))
    else:
        print(render(delta))
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    inputs = subparsers.add_parser("inputs", help="Build the calibration packet")
    inputs.add_argument("--rubric", type=Path, required=True)
    inputs.add_argument("--samples", type=Path, required=True)
    inputs.add_argument("--roster", type=Path, required=True)
    inputs.add_argument("--holdout", required=True, help="Filename to hold back")
    inputs.add_argument("--out", type=Path, default=Path("calibration-inputs.json"))
    inputs.set_defaults(handler=_inputs)

    holdout = subparsers.add_parser("holdout", help="Report the holdout delta")
    holdout.add_argument("--record", type=Path, required=True)
    holdout.add_argument("--draft", type=Path, required=True)
    holdout.add_argument("--sample", type=Path, required=True)
    holdout.add_argument("--json", action="store_true")
    holdout.set_defaults(handler=_holdout)

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (RubricError, SampleError, RecordError, FileNotFoundError) as error:
        print(str(error), file=sys.stderr)
        return EXIT_MALFORMED


if __name__ == "__main__":
    raise SystemExit(main())
