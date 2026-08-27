# What a rubric must contain

The grader reads your rubric. It does not rewrite it. This page says what the
parser needs so your rubric reads cleanly.

## A criteria table

One Markdown table. Its header row must name a **Criterion** column, one
column per performance level, and a **Weight** column last.

| Evaluation Criterion | Exemplary (A: 90–100%) | Proficient (B: 80–89%) | Weight |
| :--- | :--- | :--- | :---: |
| **1. Thesis** | Sharp and defensible. | Clear, if conventional. | **25 pts** |

Three rules:

- **Number each criterion.** `**1. Thesis**`, not `**Thesis**`. Scores are
  recorded against the number, so a renamed criterion still lines up.
- **Give each criterion a point value.** `**25 pts**` in the Weight column.
- **Name each level in its header.** The text before the parentheses is the
  level name. The text inside is its band.

## A grading scale

One bullet per band. Bounds are inclusive.

```
* **93–100 (A)**: Outstanding original synthesis.
* **90–92 (A-)**: Strong, ambitious essay.
* **< 60 (F)**: Plagiarized, off-topic, or incomplete.
```

The bands must cover every score from 0 to 100. The grader computes the
letter from this scale. It never picks one.

## A feedback protocol, if you want one

State the shape of your feedback and the grader follows it. Skip this section
and calibration infers a shape from your graded samples instead.

```
## Instructor Feedback Protocol

1. **Inline Bracketed Annotations `[...]`**: Marginal comments pinned to
   specific sentences.
2. **The Bottom Line**: A frank assessment of whether the argument holds.
3. **What Worked (Strengths)**: Bullet points citing specific paragraphs.
4. **What to Work On (Areas for Growth)**: Concrete revisions.
5. **Sign-off**: Signature closing: `— Dr. Vance` (or `— MV`).
```

How each item is read:

| Item | Effect |
| --- | --- |
| A label containing "annotation" | Every draft must carry bracketed inline comments |
| A label starting with "Sign-off" | Each backticked string becomes an accepted closing |
| Any other label | Becomes a required section of the written feedback |

A parenthetical in a label is dropped, so `What Worked (Strengths)` becomes
the section `What Worked`.

## The prepared draft pair

When `grade.py next --json` returns `prepare-record`, execute its command. The
base command is:

```bash
python3 scripts/grade.py record prepare \
  --batch <batch> --course <course> --rubric <rubric.md> \
  --source redacted/<TOKEN>.md
```

The command creates `.grading/drafts/<batch>/<TOKEN>.md` and
`.grading/drafts/<batch>/<TOKEN>.json` together. It refuses to replace either
file. The Markdown already has the rubric's required sections, annotation
shape, and first allowed sign-off. Replace every
`<!-- replace-with-feedback -->` marker. An unchanged marker blocks
verification.

The JSON is a loadable but deliberately unassessed `grade-record/1`. Keep its
generated `schema`, `batch`, `token`, `source`, `course`, `rubric`, `profile`,
`pattern_density`, and `status` fields. The full record shape is:

```json
{
  "schema": "grade-record/1",
  "batch": "essay-2",
  "token": "STUDENT_01",
  "source": "redacted/STUDENT_01.md",
  "course": "phil210",
  "rubric": "grading_rubric.md",
  "profile": ".grading/profiles/phil210.md",
  "criteria": [
    {
      "number": 1,
      "name": "Thesis",
      "points_possible": 25,
      "score": 21,
      "level": "Proficient",
      "assessable": true,
      "justifications": [
        {
          "quote": "At least four exact words from the redacted source.",
          "note": "How this passage supports the score."
        }
      ]
    }
  ],
  "total": 21,
  "total_possible": 25,
  "letter": "B",
  "pattern_density": {
    "words": 600,
    "count": 0,
    "per_thousand": 0.0,
    "by_pattern": {},
    "examples": []
  },
  "status": "draft"
}
```

Keep every rubric criterion in its original order with its exact number, name,
and point value. For each assessable criterion:

- set `assessable` to `true`;
- give an integer `score` within the criterion's point value;
- use the rubric's exact performance-level name; and
- add at least one exact quote of four or more words from the redacted source.

Use `occurrence` only when the same quote appears more than once. Verification
writes `offset`, `line`, and `length`; the agent does not. For a criterion the
work cannot support, keep `assessable: false`, `score: null`, level
`cannot assess`, and an empty `justifications` list. Never infer a score.

Set `total` to the sum of assessed scores and `total_possible` to the sum of
their possible points. Compute `letter` from the rubric's grading scale. Leave
the generated `pattern_density` object unchanged; verification recounts it.
Density is rubric evidence about prose, not evidence of authorship. Never use it
to claim that a person or model wrote the submission.

Write feedback from the current profile and approved assignment brief. Keep the
rubric's section order and allowed sign-off. Use the profile's voice, structure,
comment-bank phrasing, and never-use list. Check the finished feedback against
`references/ai-patterns.md` before running `next --json` again.

## Drift

Each criterion carries a digest of its own wording. When you edit a
criterion, `grade.py profile` names that criterion as drifted and asks before
grading. Reflowing the table or changing its spacing is not an edit. Changing
a word is.
