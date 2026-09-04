# Guide for agents

This file explains how to change Humanizer without breaking its package or prompt.

## What this repo contains

This repo holds two agent skills, the reference files they share, and the local
scripts that stand behind the grader. The grading skill lives at the repo root
in `SKILL.md`. The Humanizer skill lives at `skills/humanizer/SKILL.md`. Both
skills read `references/ai-patterns.md` for the 35 patterns. `scripts/redaction/`
redacts student identity before any work reaches a model, and `scripts/grading/`
checks every claim a generated grade makes. `scripts/validate-package.py` checks
the package. The repo has no build step.

Keep the skill portable. Do not write instructions that limit it to one or two agent tools.

## Key files

- `SKILL.md` is the grading skill and the package's entry point.
- `skills/humanizer/SKILL.md` is the Humanizer skill. It stays usable on its own.
- `references/ai-patterns.md` holds the 35 patterns. Both skills read it. Edit patterns only here.
- `references/redaction.md`, `references/calibration.md`, and `references/rubric-format.md` explain what redaction covers, how calibration runs, and what a rubric must contain.
- `scripts/redaction/` holds the five redaction layers. Read `docs/superpowers/decisions-redaction-foundation.md` before changing any of them.
- `scripts/grading/` holds the rubric parser, sample parser, density counter, record format, profile loader, verifier, grading-batch lifecycle, and consistency pass. `calibration.py` owns the holdout receipt and profile approval. `review.py` owns the draft review record and the consistency acknowledgement.
- `scripts/redact.py`, `scripts/rehydrate.py`, `scripts/calibrate.py`, and `scripts/grade.py` are the four command-line entry points.
- `hooks/block-raw-reads.json` is the optional `PreToolUse` hook. It must stay opt-in, so it must never be renamed `hooks/hooks.json`.
- `README.md` explains installation, use, patterns, and version history.
- `.claude-plugin/plugin.json` describes the Claude plugin and points its skill loader at both `./` (the grader) and `./skills/humanizer` (Humanizer).
- `.claude-plugin/marketplace.json` lets users add this repo as a Claude marketplace.
- `scripts/validate-package.py` checks package files and shared values.
- `PACKAGE_FILES.txt` is the reviewed public package inventory. Any added or
  removed tracked file requires an explicit inventory update.

## Rules for changes

Keep `SKILL.md` and `README.md` in sync.

- **Patterns:** The 35 numbered patterns live in `references/ai-patterns.md`. If you add, remove, or renumber a pattern, update that file, the README table, the validator, and every pattern reference.
- **Invariants:** The token map is the only artifact that reversibly maps stable tokens to real identities. Nothing under `scripts/grading/` may load it. The model reads only from `redacted/`. Calibration and grading stay separate commands. A real batch never grades without an approved calibration receipt, a reviewed pilot or per-draft approvals, and an acknowledged consistency pass; only the synthetic demo batch may use review cadence `none`.
- **Tests:** Run `python3 -m pytest tests/` from the repo root, so `scripts.` imports resolve. The leak test and the end-to-end test are the two that matter; the rest support them.
- **Version:** Keep the same version in `SKILL.md` under `metadata.version`, the first README version entry, and `.claude-plugin/plugin.json`. Do not add a top-level `version` field to the skill.
- **Compatibility:** Keep install and use instructions neutral across agents. Names such as Claude Code, OpenCode, and Codex are examples, not limits.
- **History:** Add a short README version note for any behavior change or non-obvious fix.
- **Checks:** Before publishing, run `python3 scripts/validate-package.py`, `npx skills add . --list`, and `claude plugin validate .`.

## Writing style

Use Plain Language in code comments, prompts, documentation, descriptions, validation messages, and progress reports.

- Lead with the main point.
- Use common words and active voice.
- Keep sentences and paragraphs short.
- Use one term for the same item.
- Use `must` for requirements.
- Use headings, lists, and tables when they help the reader.
- Remove repeated or unnecessary words.
- Limit acronyms and explain technical terms.
- Avoid double negatives.
- Keep exact identifiers, commands, paths, schema fields, quotations, watched phrases, and behavior-bearing examples.
- Keep the full technical meaning.

## Editing the skill

- Keep the YAML metadata valid.
- Treat the prompt below the metadata as the product.
- Prefer a short, clear instruction over another exception or repeated explanation.

## Agent skills

### Issue tracker

Issues are tracked as local Markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Triage uses the default canonical label vocabulary. See `docs/agents/triage-labels.md`.

### Domain docs

This repo uses a single-context layout. See `docs/agents/domain.md`.
