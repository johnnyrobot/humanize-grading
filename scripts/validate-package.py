#!/usr/bin/env python3
"""Check Humanizer's package files without external dependencies."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = ROOT / "SKILL.md"
SKILL = SKILL_PATH.read_text(encoding="utf-8")
HUMANIZER_SKILL = (ROOT / "skills" / "humanizer" / "SKILL.md").read_text(
    encoding="utf-8"
)
README = (ROOT / "README.md").read_text(encoding="utf-8")
AGENTS = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
PLUGIN = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))


def package_files() -> set[Path]:
    """Return the files a Git release would ship, or archive contents."""
    if (ROOT / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        )
        tracked = {
            Path(raw.decode("utf-8"))
            for raw in result.stdout.split(b"\0")
            if raw
        }
        if (ROOT / "PACKAGE_FILES.txt").is_file():
            tracked.add(Path("PACKAGE_FILES.txt"))
        return tracked
    generated_directories = {".git", ".pytest_cache", "__pycache__"}
    generated_files = {".DS_Store", ".coverage"}
    return {
        path.relative_to(ROOT)
        for path in ROOT.rglob("*")
        if path.is_file()
        and not (generated_directories & set(path.relative_to(ROOT).parts))
        and path.name not in generated_files
        and path.suffix != ".pyc"
    }


def is_synthetic_fixture(path: Path) -> bool:
    return path.parts[:3] == ("tests", "fixtures", "phil210")


packaged_files = package_files()
manifest_path = ROOT / "PACKAGE_FILES.txt"
if not manifest_path.is_file():
    raise SystemExit("Add PACKAGE_FILES.txt with the reviewed public package inventory")
allowed_files = {
    Path(line)
    for line in manifest_path.read_text(encoding="utf-8").splitlines()
    if line.strip()
}
if packaged_files != allowed_files:
    added = sorted(packaged_files - allowed_files)
    missing = sorted(allowed_files - packaged_files)
    raise SystemExit(
        "Review and update PACKAGE_FILES.txt. "
        f"Unexpected: {added}; missing: {missing}"
    )

for packaged_path in packaged_files:
    sensitive_name = (
        packaged_path.name == "roster.csv"
        or packaged_path.name.endswith(".roster.csv")
        or packaged_path.name.startswith("calibration-inputs")
        or packaged_path.name in {"_verification.json", "_publication.json"}
    )
    sensitive_directory = bool(
        {".grading", "submissions", "redacted", "final"} & set(packaged_path.parts)
    )
    if (sensitive_name or sensitive_directory) and not is_synthetic_fixture(
        packaged_path
    ):
        raise SystemExit(
            f"Remove sensitive local grading artifact from the package: {packaged_path}"
        )


def require_match(match: re.Match[str] | None, message: str) -> re.Match[str]:
    if match is None:
        raise SystemExit(message)
    return match


yaml_metadata = require_match(
    re.match(r"\A---\n(.*?)\n---\n", SKILL, re.DOTALL),
    "SKILL.md must begin with YAML metadata",
).group(1)

for unsupported_field in ("compatibility:", "allowed-tools:"):
    if re.search(rf"(?m)^{re.escape(unsupported_field)}", yaml_metadata):
        raise SystemExit(f"Remove unsupported YAML field: {unsupported_field[:-1]}")

skill_version = require_match(
    re.search(r'(?m)^\s+version:\s*["\']([^"\']+)["\']\s*$', yaml_metadata),
    "Add metadata.version to SKILL.md",
).group(1)
humanizer_yaml_metadata = require_match(
    re.match(r"\A---\n(.*?)\n---\n", HUMANIZER_SKILL, re.DOTALL),
    "skills/humanizer/SKILL.md must begin with YAML metadata",
).group(1)
humanizer_version = require_match(
    re.search(
        r'(?m)^\s+version:\s*["\']([^"\']+)["\']\s*$',
        humanizer_yaml_metadata,
    ),
    "Add metadata.version to skills/humanizer/SKILL.md",
).group(1)
readme_version = require_match(
    re.search(r"(?m)^- \*\*([0-9]+\.[0-9]+\.[0-9]+)\*\*", README),
    "Add a version entry to README.md",
).group(1)

package_versions = {
    skill_version,
    humanizer_version,
    readme_version,
    str(PLUGIN.get("version", "")),
}
if len(package_versions) != 1:
    raise SystemExit(
        f"Use one package version in all files: {sorted(package_versions)}"
    )

skill_files = {path.relative_to(ROOT) for path in ROOT.rglob("SKILL.md")}
expected_skills = {Path("SKILL.md"), Path("skills/humanizer/SKILL.md")}
if skill_files != expected_skills:
    raise SystemExit(f"Expected exactly these skills: {sorted(expected_skills)}")
for skill_file in sorted(expected_skills):
    if (ROOT / skill_file).is_symlink():
        raise SystemExit(f"Keep {skill_file} a regular file")
    skill_text = (ROOT / skill_file).read_text(encoding="utf-8")
    if len(skill_text.splitlines()) > 500:
        raise SystemExit(f"Keep {skill_file} at 500 lines or fewer")
    if f'version: "{skill_version}"' not in skill_text:
        raise SystemExit(f"Use the package version {skill_version} in {skill_file}")
if PLUGIN.get("skills") != ["./", "./skills/humanizer"]:
    raise SystemExit("Point the plugin skill loader at both skills")

plain_language_rules = (
    "## Writing style",
    "Lead with the main point.",
    "Use common words and active voice.",
    "Keep sentences and paragraphs short.",
    "Use `must` for requirements.",
    "Keep the full technical meaning.",
)
missing_plain_language_rules = [
    rule for rule in plain_language_rules if rule not in AGENTS
]
if missing_plain_language_rules:
    raise SystemExit(
        "Add the missing Plain Language rules to AGENTS.md: "
        + ", ".join(missing_plain_language_rules)
    )

PATTERNS = (ROOT / "references" / "ai-patterns.md").read_text(encoding="utf-8")
pattern_numbers = [int(n) for n in re.findall(r"(?m)^### ([0-9]+)\. ", PATTERNS)]
if pattern_numbers != list(range(1, 36)):
    raise SystemExit(f"Number the patterns 1 through 35: {pattern_numbers}")

readme_numbers = {
    int(number) for number in re.findall(r"(?m)^\| ([0-9]+) \|", README)
}
if readme_numbers != set(range(1, 36)):
    raise SystemExit("List patterns 1 through 35 in the README table")

required_references = {
    "ai-patterns.md",
    "assignment-brief.md",
    "calibration.md",
    "redaction.md",
    "rubric-format.md",
}
present_references = {path.name for path in (ROOT / "references").glob("*.md")}
missing_references = sorted(required_references - present_references)
if missing_references:
    raise SystemExit(
        "Add the missing reference files: " + ", ".join(missing_references)
    )

hard_rules = (
    "Read only from `redacted/`.",
    "Accept file paths. Never accept pasted student work.",
    "Never echo the map file.",
    "Refuse to grade without a profile.",
)
missing_rules = [rule for rule in hard_rules if rule not in SKILL]
if missing_rules:
    raise SystemExit(
        "Keep the four hard rules in SKILL.md: " + "; ".join(missing_rules)
    )

assignment_brief_workflow = (
    "Approve assignment brief for <batch>",
    "python3 scripts/grade.py brief prepare",
    "python3 scripts/grade.py brief approve",
)
missing_assignment_brief_workflow = [
    text for text in assignment_brief_workflow if text not in SKILL
]
if missing_assignment_brief_workflow:
    raise SystemExit(
        "Document the assignment brief workflow in SKILL.md: "
        + "; ".join(missing_assignment_brief_workflow)
    )

if (ROOT / "hooks" / "hooks.json").exists():
    raise SystemExit(
        "Do not name the hook hooks/hooks.json. A plugin loads that name "
        "automatically, and this hook must stay opt-in."
    )
hook_path = ROOT / "hooks" / "block-raw-reads.json"
if not hook_path.is_file():
    raise SystemExit("Ship hooks/block-raw-reads.json")
hook = json.loads(hook_path.read_text(encoding="utf-8"))
hook_entries = hook.get("hooks", {}).get("PreToolUse", [])
if not hook_entries or "Bash" not in hook_entries[0].get("matcher", ""):
    raise SystemExit("The hook must match Bash as well as the file tools")
if "block-raw-reads" not in README:
    raise SystemExit("Document the optional hook in README.md")

print(f"Humanizer package v{skill_version} is valid")
