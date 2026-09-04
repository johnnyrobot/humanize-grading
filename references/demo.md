# Narrated synthetic demo

Use this workflow only when the instructor asks to run the demo with the
bundled `grading_mock_papers` corpus. The first user message authorizes the
synthetic demonstration. It does not approve grading for any other folder.

## Prepare the run

Do not open the dropped files. Run the package command from the package root:

```bash
python3 scripts/demo.py prepare --source <grading_mock_papers/> --json
```

The command checks the complete file inventory and every file digest before it
creates state. It then creates a unique owner-only run directory, installs the
synthetic PHIL 210 profile with a synthetic calibration approval, installs the
batch roster, binds the fixed local assignment brief with review cadence
`none`, redacts the five submissions, completes the bound synthetic review, and
returns the first legal grading action. The synthetic approval and the `none`
cadence are valid only inside this prepared run. A real course measures
holdouts, approves the profile, reviews a pilot, and acknowledges consistency
flags; say so when you narrate.

Continue without further approvals only when the JSON says all of the
following:

- `schema` is `humanizer-grading-demo/1`;
- `synthetic` is `true`;
- `status` is `ready-for-grading`;
- `next_action.action` is `prepare-record`.

If preparation exits non-zero or any value differs, stop demo mode. Explain
that the folder was not recognized and offer the ordinary supervised grading
workflow. Never work around the corpus check.

## Narrate and grade

Lead with a short explanation that the profile came from four synthetic graded
samples, paper 5 is the holdout, papers 6–10 form the live batch, and grading
uses four 25-point criteria. Explain that redaction protects identity while the
verification scripts check evidence and arithmetic; the model still makes the
grading judgement.

Use concise commentary before each stage:

1. **Profile and rubric:** explain the calibrated standards and feedback voice.
2. **Redaction:** explain that only tokenized submissions enter model context.
3. **Grading:** name the current token and the four rubric criteria. Explain
   conclusions and cited evidence without exposing hidden chain-of-thought.
4. **Verification:** explain that quotes, totals, grade bands, required feedback
   sections, watched language, and file bindings are checked locally.
5. **Consistency and publication:** explain that consistency flags are advisory
   and that rehydrated outputs remain drafts for instructor review.

Run the exact `next_command` from the preparation JSON. Execute only the
argument arrays returned by that command, in order. After every action, run the
same `next_command` again.

For `prepare-record`, execute the returned command, read only the returned
token's file under the run's redacted directory, and edit both generated draft
files. Read `references/rubric-format.md` before the first draft. Use the
rubric, profile, assignment brief, and redacted submission as evidence.

For a repair action, fix only the named draft or verification finding and run
`next_command` again. Allow at most two repair attempts for the same token or
whole-batch verification failure. Stop and report the exact blocking finding
after the second failed repair; never invent a successful result.

Demo preparation has already handled the assignment-brief and redaction-review
gates for the exact synthetic corpus. If `next_command` unexpectedly returns
`approve-brief` or `review-redaction`, stop and report that the prepared demo
state is inconsistent.

When the action is `review-publication`, the demo is complete. Report the
`publication` path from the preparation JSON and summarize scores using the
tokenized grade records. Do not open the rehydrated files.

## Hard boundary

The demo remains local and synthetic. Keep the raw folder, roster, review file,
token map, and rehydrated drafts outside model context. Do not send feedback to
an LMS. Never use automatic demo authorization for a changed or unknown corpus.
