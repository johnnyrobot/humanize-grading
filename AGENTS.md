# Guide for agents

This file explains how to change Humanizer without breaking its package or prompt.

## What this repo contains

This repo holds two agent skills and the reference they share, all written in Markdown. The grading skill lives at the repo root in `SKILL.md`. The Humanizer skill lives at `skills/humanizer/SKILL.md`. Both skills read `references/ai-patterns.md` for the 35 patterns. `scripts/validate-package.py` checks all three. The repo has no build step.

Keep the skill portable. Do not write instructions that limit it to one or two agent tools.

## Key files

- `SKILL.md` is the grading skill and the package's entry point.
- `skills/humanizer/SKILL.md` is the Humanizer skill. It stays usable on its own.
- `references/ai-patterns.md` holds the 35 patterns. Both skills read it. Edit patterns only here.
- `README.md` explains installation, use, patterns, and version history.
- `.claude-plugin/plugin.json` describes the Claude plugin and points its skill loader at both `./` (the grader) and `./skills/humanizer` (Humanizer).
- `.claude-plugin/marketplace.json` lets users add this repo as a Claude marketplace.
- `scripts/validate-package.py` checks package files and shared values.

## Rules for changes

Keep `SKILL.md` and `README.md` in sync.

- **Patterns:** The 35 numbered patterns live in `references/ai-patterns.md`. If you add, remove, or renumber a pattern, update that file, the README table, the validator, and every pattern reference.
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
