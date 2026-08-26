# 08 — Ship and verify version 3.2.0

**What to build:** Publish the enforced grading-batch lifecycle as version 3.2.0 with synchronized skill guidance, package metadata, walkthrough evidence, release notes, and complete package checks.

**Blocked by:** 06 — Safely replace a recognized publication; 07 — Contract the legacy batch interface

**Status:** resolved

- [x] The grading skill and README describe verification receipts, verified-batch publication, legacy-batch refusal, atomic staging, and safe force consistently.
- [x] The full walkthrough uses the current commands and shows output reproduced from an actual synthetic grading batch.
- [x] Rehydrated output is consistently described as draft feedback pending instructor sign-off.
- [x] The first README version entry, skill metadata version, plugin metadata version, and package validator all agree on 3.2.0.
- [x] The README includes a short 3.2.0 behavior note.
- [x] Installation and use guidance remains neutral across agents.
- [x] The full test suite passes from the repository root, including the leak and end-to-end tests.
- [x] The package validator passes.
- [x] The skill-list installation check reports the expected skills.
- [x] The plugin validator passes.
- [x] The walkthrough examples, receipt behavior, and publication behavior are checked against live command output.
- [x] The final diff has no whitespace errors and contains no unrelated changes.

## Answer

Version 3.2.0 is synchronized across both skills, the plugin, README history,
and package validation. The Grader and README now state the same receipt,
legacy refusal, staged publication, safe force, and instructor sign-off rules.
The walkthrough output was refreshed from a five-submission synthetic PHIL 210
batch, including a 19-artifact verification receipt and a five-file
publication with its identity-free marker.

Verification: 427 tests passed from the repository root. The leak and
end-to-end tests also passed together, 20 tests total. Bytecode compilation and
the package validator passed. The required default Skills CLI check passed for
the root Grader; the documented `--full-depth` check found both `grader` and
`humanizer`. Claude plugin validation passed without warnings.
`git diff --check` passed.

The public-release audit then reviewed all 95 tracked files and found 17
security and integrity gaps. The release hardening keeps identity-bearing review
data outside model context, removes untrusted inline calibration annotations,
binds the token map to an exact redaction manifest, reruns semantic verification
before rehydration, rejects unsafe paths and collisions, bounds every public
input role, tightens feedback and record validation, and adds an explicit
package inventory. The full synthetic walkthrough was reproduced again with a
19-artifact receipt, five rehydrated drafts, safe force, and rehydrated status.
