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

## Drift

Each criterion carries a digest of its own wording. When you edit a
criterion, `grade.py profile` names that criterion as drifted and asks before
grading. Reflowing the table or changing its spacing is not an edit. Changing
a word is.
