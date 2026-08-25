# Grading-batch lifecycle

Status: ready-for-agent

## Problem Statement

An instructor can currently run status, verification, consistency, and rehydration as separate commands, but the software does not enforce the lifecycle that connects them. Each command reconstructs the grading batch differently. A complete draft and grade-record pair is treated as drafted even when it has never passed verification. Rehydration relies on written instructions telling the user to verify first, and it can write some output before discovering an unmapped token later in the batch.

This creates a safety gap. A stale, mismatched, partly verified, or partly published grading batch can look complete enough to continue. Verification may also trust a source path written inside a model-generated grade record instead of resolving the redacted submission from the configured redacted batch. The instructor needs one enforceable lifecycle that keeps raw identity outside grading modules, reports every blocking finding together, proves exactly what passed verification, and publishes either a complete set of rehydrated drafts or nothing.

## Solution

Deepen the existing grading-batch module so it owns artifact discovery, progress, blocking findings, verification eligibility, verification receipts, consistency inputs, and publication eligibility. Keep command-line programs as thin adapters for arguments, presentation, identity substitution, and existing exit-code behavior.

The lifecycle has four progress stages: pending, drafted, verified, and rehydrated. Problems such as incomplete pairs, orphaned artifacts, malformed records, and metadata mismatches are blocking findings rather than competing progress stages. The configured redacted directory is the only authority for submission text.

Successful persisted verification writes one versioned, identity-free receipt for the whole grading batch. The receipt binds the exact redacted submissions, drafts, grade records, rubric, calibration profile, and pattern reference that passed. Rehydration is available only through a verified-batch capability created by successful verification or receipt revalidation.

Publication prepares every rehydrated draft in staging and reports all unmapped tokens before changing the destination. It refuses legacy batches, stale receipts, unrelated output, and unsafe replacement. A recognized prior publication can be replaced explicitly through a recoverable staged transaction. Every published file remains a draft until the instructor signs off.

## User Stories

1. As an instructor, I want the software to enforce verification before rehydration, so that I cannot accidentally send unchecked feedback.
2. As an instructor, I want a grading batch to have one clear lifecycle, so that status means the same thing in every command.
3. As an instructor, I want pending work distinguished from drafted work, so that I know what still needs grading.
4. As an instructor, I want drafted work distinguished from verified work, so that file existence is not mistaken for safety.
5. As an instructor, I want verified work distinguished from rehydrated drafts, so that identity restoration is visible as a separate transition.
6. As an instructor, I want incomplete draft and grade-record pairs reported as blocking findings, so that I can repair them without losing the submission's factual progress.
7. As an instructor, I want orphaned drafts and records reported, so that output from another grading batch cannot silently mix with the current one.
8. As an instructor, I want every blocking finding reported in one run, so that I can fix the whole grading batch in one pass.
9. As an instructor, I want consistency flags kept advisory, so that an unusual but legitimate grade does not block the batch.
10. As an instructor, I want the configured redacted submissions to be authoritative, so that verification never follows an unsafe or stale source path from generated data.
11. As an instructor, I want token mismatches reported, so that a draft cannot be paired with another student's grade record.
12. As an instructor, I want batch mismatches reported, so that artifacts from different assignments cannot be combined.
13. As an instructor, I want course mismatches reported, so that one course's profile cannot silently govern another course's work.
14. As an instructor, I want rubric and profile mismatches reported after equivalent paths are normalized, so that genuine context errors block without rejecting harmless path differences.
15. As an instructor, I want malformed grade records reported per submission, so that one broken record does not hide findings in the rest of the batch.
16. As an instructor, I want verification to produce a receipt only when every draft passes, so that the receipt proves a whole-batch result.
17. As an instructor, I want a dry verification run to write nothing, so that checking with the no-write option cannot accidentally authorize rehydration.
18. As an instructor, I want any edit after verification to invalidate the receipt, so that changed feedback must be checked again.
19. As an instructor, I want added, removed, or renamed artifacts to invalidate the receipt, so that batch membership cannot drift after verification.
20. As an instructor, I want old grading batches without receipts refused at rehydration, so that historical command ordering is not treated as proof.
21. As an instructor, I want unknown receipt versions refused clearly, so that newer evidence formats are never guessed at.
22. As an instructor, I want verification receipts to contain no real identities, so that they remain safe beside redacted grading artifacts.
23. As an instructor, I want receipt paths to be portable relative locators where possible, so that rehydration can recheck evidence without new required command arguments.
24. As an instructor, I want rehydration to validate the receipt immediately before publication, so that a stale in-memory view cannot bypass the gate.
25. As an instructor, I want every draft transformed in staging before publication, so that an unmapped token in the last file leaves the destination unchanged.
26. As an instructor, I want all unmapped tokens reported together, so that I can correct them in one pass.
27. As an instructor, I want a non-empty destination refused by default, so that stale final output cannot mix with the new batch.
28. As an instructor, I want an explicit safe replacement option, so that I can intentionally replace a recognized prior publication.
29. As an instructor, I want safe replacement to refuse files I edited or added, so that the tool never deletes unrelated work.
30. As an instructor, I want interrupted replacement to be recoverable, so that a failure does not leave a partly populated new batch.
31. As an instructor, I want published batches marked with their receipt digest and output hashes, so that later replacement can recognize exactly what the tool owns.
32. As an instructor, I want rehydrated feedback still called a draft, so that identity restoration is never confused with instructor approval.
33. As a privacy-conscious instructor, I want the token map loaded only by the local identity-substitution adapter, so that grading modules never gain access to real identities.
34. As a privacy-conscious instructor, I want identity-bearing text written directly into staging by the local adapter, so that it never returns through the grading-batch module.
35. As an agent running the grader, I want the existing commands and required arguments to continue working, so that current instructions remain valid.
36. As an agent running the grader, I want existing exit-code meanings preserved, so that automation can continue interpreting outcomes correctly.
37. As an agent running the grader, I want findings represented consistently across lifecycle operations, so that I do not reconstruct batch rules in each command.
38. As a maintainer, I want one deep grading-batch interface to be the test surface, so that lifecycle changes concentrate in one module.
39. As a maintainer, I want focused parser, verifier, density, profile, rubric, and consistency tests to remain, so that already-deep modules retain direct behavioral coverage.
40. As a maintainer, I want the implementation to use only the standard library at runtime, so that the package keeps its current portability.
41. As a maintainer, I want receipts and publication markers versioned from their first release, so that later schema changes fail explicitly.
42. As a maintainer, I want lifecycle progress recalculated from current artifacts and receipts, so that no separate status store can drift.
43. As a maintainer, I want command-line modules limited to parsing, presentation, identity substitution, and exit mapping, so that lifecycle policy does not leak back into callers.
44. As a maintainer, I want the release documented as version 3.2.0, so that users can identify the stricter lifecycle behavior.

## Implementation Decisions

- Deepen the existing grading-batch module instead of adding a competing lifecycle module.
- Keep redaction and calibration outside this lifecycle. The lifecycle begins only after redaction completes cleanly.
- Preserve the current command names, required arguments, default locations, output conventions, and exit-code meanings. Add an optional safe-force publication argument without breaking existing invocations.
- Use a named-method interface. A grading batch exposes status, verification, and consistency operations. A separate verified-batch capability exposes publication.
- Give the verified-batch capability no unrestricted public constructor. It comes only from successful persisted verification or from revalidating a known receipt.
- Use operation-specific immutable result values for status, verification, consistency, and publication.
- Model progress as pending, drafted, verified, and rehydrated.
- Keep progress separate from blocking findings and advisory consistency flags.
- Derive progress from current artifacts and validated receipts each time. Do not add a separate status database.
- Start grading-batch membership from token-named files in the configured redacted directory.
- Treat the configured redacted directory as the only authority for submission text. Grade-record source metadata may be normalized and compared, but it must never select a file to open.
- Treat incomplete pairs, orphaned artifacts, unreadable inputs, malformed schemas, duplicate tokens, and genuine metadata mismatches as blocking findings.
- Aggregate every finding that can be established safely across the whole grading batch before returning.
- Preserve missing-profile, rubric-drift, verification-failure, malformed-input, rehydration-failure, and success exit meanings in their current command families.
- Delegate single-draft checking to the existing verifier rather than moving its algorithms into the grading-batch module.
- Delegate consistency analysis to the existing consistency module. Consistency flags never invalidate a verification receipt or block publication inside the lifecycle.
- For persisted verification, prepare updated grade records first, including located evidence offsets. Commit those records before writing the receipt. The receipt is the whole-batch commit marker.
- A successful no-write verification returns findings and success information but writes neither offsets nor a receipt. It cannot create publication authority.
- Store one identity-free verification receipt beside the draft artifacts under a reserved underscore-prefixed name.
- Give the receipt a versioned schema and reject malformed or unknown schemas.
- Bind the receipt to the exact bytes and complete membership of every redacted submission, draft, grade record, rubric, calibration profile, and pattern reference used during verification.
- Use SHA-256 from the standard library for artifact digests.
- Sort receipt entries deterministically and store stable tokens, roles, normalized relative locators where possible, and hashes. Store no source text, original identity-bearing filenames, token-map paths, identity values, or rehydrated text.
- Invalidate the receipt when a bound artifact is changed, missing, added, renamed, or otherwise no longer matches.
- Refuse publication for a legacy grading batch with no receipt. Report that verification must run again.
- Revalidate the receipt immediately before any publication work.
- Keep the token map and identity substitution outside the grading package. The production renderer adapter remains local and is the only participant that loads the map.
- Make the renderer adapter read one tokenized draft and write its identity-bearing result directly to a caller-provided staging destination. Return only identity-free status, unmapped token names, and failure information.
- Require the renderer adapter's batch identity to match the verified grading batch.
- Stage the complete publication in a sibling temporary directory on the same filesystem as the target.
- Collect unmapped tokens and rendering failures for the whole grading batch. Discard staging and leave the target unchanged when any blocking finding occurs.
- Refuse a non-empty target unless safe force was requested.
- Write an identity-free publication marker containing a versioned schema, the grading-batch identity, the verification-receipt digest, the expected output file set, and output hashes.
- Allow safe force only when the target has a valid publication marker for the same grading batch and receipt lineage, every expected file matches, and no unrelated file exists.
- Implement portable safe force by staging the complete replacement, moving the recognized target to a backup, moving staging into place, and restoring or recovering the backup when a later step fails.
- Accept that forced replacement may briefly leave the target path absent. It must never expose a partly populated new grading batch.
- Keep recovery metadata recognizable and identity-free so a later run can resolve an interrupted safe-force transaction.
- Keep the receipt and publication schemas internal to this feature's specification. Do not refactor density evidence or grade-record typing as part of this work.
- Release the behavior as version 3.2.0. Keep the skill metadata, plugin metadata, README version entry, walkthrough, and package validator synchronized.
- Keep runtime code standard-library-only.

## Testing Decisions

- Treat the deep grading-batch interface as the primary test surface. Tests should describe lifecycle behavior and survive internal refactors.
- Use real temporary filesystem trees as the local substitute for filesystem dependencies. Do not introduce a filesystem port solely for tests.
- Use a deterministic renderer adapter in tests. It must be able to produce valid output, report configurable unmapped tokens, and simulate a rendering failure without exposing identity data to the grading-batch module.
- Test status through the grading-batch interface for pending, drafted, verified, and rehydrated progress.
- Test incomplete and orphaned conditions as blocking findings separate from factual progress.
- Test full-batch finding aggregation with several different failures in one batch.
- Test that artifact pairing begins from the configured redacted directory.
- Test that a grade record pointing at an existing non-redacted path is never opened and produces a mismatch finding.
- Test filename-token, record-token, batch, course, rubric, and profile mismatch behavior.
- Test path equivalence so harmless relative-versus-absolute differences do not become false mismatches.
- Test that malformed records do not stop inspection of later records.
- Test that a clean persisted verification updates evidence offsets and writes exactly one receipt.
- Test that one failing draft prevents every receipt write.
- Test that no-write verification changes no record and writes no receipt.
- Test receipt determinism, schema version, sorted entries, relative locators, complete artifact membership, and absence of identity-bearing fields.
- Test that edits to each bound artifact role invalidate the receipt independently.
- Test that added, removed, and renamed participant artifacts invalidate the receipt.
- Test that malformed and unknown receipt schemas block publication.
- Test that a legacy grading batch without a receipt blocks publication with a rerun-verification finding.
- Test that the verified-batch capability cannot be obtained from a failing or no-write verification.
- Test that receipt revalidation is repeated immediately before publication.
- Test that the grading package never imports the token-map type, refers to map locations, or receives identity-bearing output.
- Test renderer batch mismatch, unmapped-token aggregation, and rendering failures.
- Test that any renderer failure leaves an absent target absent and an existing target unchanged.
- Test first publication from a complete staging directory.
- Test that a non-empty target is refused without safe force.
- Test that safe force refuses a missing marker, wrong batch, stale receipt digest, modified expected file, missing expected file, and unrelated extra file.
- Test successful safe force replaces a complete recognized publication with a complete new one.
- Test rollback when the replacement rename fails after the old target moves to backup.
- Test recovery of each recognizable interrupted safe-force state.
- Test that publication markers are deterministic, versioned, identity-free, and bind every output hash.
- Keep focused behavioral tests for the existing verifier, rubric parser, profile loader, density scanner, grade-record parser, and consistency reviewer.
- Remove tests that only pin the old shallow survey data shape once no production caller uses that shape.
- Keep thin command-line compatibility tests for arguments, messages that users rely on, and existing exit meanings.
- Keep the end-to-end chain from redaction through verification, consistency, publication, and rehydrated drafts.
- Keep the invariant test that grading modules never load the token map, and strengthen it for the renderer seam.
- Run the full test suite from the repository root.
- Run the package validator, skill-list installation check, and plugin validator before release.
- Require whitespace-clean diffs and verify the generated walkthrough examples against actual command output.

## Out of Scope

- Changing how student identities are detected, tokenized, mapped, or restored inside the redaction modules.
- Changing the rule that the token map is the only artifact holding real identities.
- Changing calibration inputs, profile generation, holdout evaluation, or rubric parsing.
- Implementing the separate persisted-density-evidence candidate.
- Implementing the separate untrusted-record versus verified-grade-facts candidate.
- Changing the mathematical or prose checks performed by the existing verifier.
- Changing consistency algorithms, thresholds, or the advisory meaning of consistency flags.
- Adding a database, remote dependency, third-party runtime package, or external publication system.
- Treating rehydrated drafts as instructor-approved or automatically sending them to students or an LMS.
- Supporting publication of only the subset of drafts that passed.
- Preserving publication authority for legacy batches that have not produced a receipt.
- Allowing safe force to delete or overwrite modified, unrelated, or unrecognized files.
- Providing a plug-in system for arbitrary lifecycle operations or renderers.
- Refactoring the calibration packet, rubric parser, verifier body, or consistency reviewer merely because they are large.

## Further Notes

- The glossary defines redacted submission, grading batch, verified draft, verification receipt, rehydrated draft, blocking finding, and consistency flag. Use those terms consistently in tickets, tests, code comments, documentation, and messages.
- The accepted architecture decision records why verification receipts gate atomic rehydration and why the identity seam remains outside grading modules.
- Publication is transactional at the lifecycle level. Portable standard-library forced replacement cannot guarantee one kernel-level exchange of two non-empty directories; it instead guarantees complete staged content, no partially populated new target, rollback, and recoverability.
- Exact byte hashes intentionally make harmless edits require verification again. Safety and evidence reproducibility take priority over avoiding repeated verification.
- The verified-batch capability is a structural guard, not proof against malicious code in the same process. The current threat is accidental sequencing and identity leakage through module design, not hostile local execution.
- The implementation should proceed through test-driven tracer bullets after this spec is split into tickets. Each ticket must preserve a runnable vertical slice and declare its blocking edges.
