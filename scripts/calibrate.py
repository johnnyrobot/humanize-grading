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
from scripts.grading.samples import (  # noqa: E402
    SampleError,
    load_sample,
    scrub_private_data,
)
from scripts.redaction.roster import load_roster  # noqa: E402
from scripts.redaction.private_files import write_private_text  # noqa: E402
from scripts.path_safety import read_text_limited  # noqa: E402

EXIT_OK = 0
EXIT_MALFORMED = 13

MIN_SAMPLES = 3
MAX_SAMPLES = 8


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

    loaded_calibration = []
    for index, path in enumerate(calibration, start=1):
        sample = load_sample(path)
        _require_roster_identity(sample, roster, f"Calibration sample {index}")
        loaded_calibration.append(sample)
    holdout_sample = load_sample(holdout_path)
    _require_roster_identity(holdout_sample, roster, "The holdout sample")

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
        "holdout": {"label": "holdout"},
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
        "1 held back."
    )
    print(f"Scrubbed {scrubbed} name forms from the comments.")
    print("Read references/calibration.md before building the profile.")
    return EXIT_OK


def _holdout(args: argparse.Namespace) -> int:
    record = GradeRecord.load(args.record)
    feedback = read_text_limited(args.draft, "The holdout draft", 1_048_576)
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
    inputs.add_argument(
        "--out", type=Path, default=Path("calibration-inputs.json")
    )
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
    except (
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
