# 06 — Safely replace a recognized publication

**What to build:** Let an instructor explicitly replace an unchanged publication from the same grading batch while refusing modified or unrelated output and recovering safely from an interrupted replacement.

**Blocked by:** 05 — Publish through the verified-batch capability

**Status:** resolved

- [x] A non-empty destination is refused unless safe force is explicitly requested.
- [x] The publication marker binds the grading-batch identity, verification-receipt digest, exact output file set, and output hashes.
- [x] Safe force refuses a missing or unknown marker, another grading batch, stale receipt lineage, modified output, missing expected output, and unrelated extra files.
- [x] Safe force prepares the complete replacement beside the destination before changing current output.
- [x] Replacement moves the recognized destination to a backup before moving the staged publication into place.
- [x] Failure after the backup move restores the prior complete publication when possible.
- [x] A later run recognizes and recovers every supported interrupted-transaction state.
- [x] The destination is never exposed as a partly populated new grading batch.
- [x] The documented behavior acknowledges that portable forced replacement may briefly leave the target path absent.
- [x] Safe-force tests exercise successful replacement, refusal, rollback, and recovery through the publication interface.

## Answer

Safe force now replaces only an unchanged publication whose marker, grading batch, receipt lineage, exact file set, and output hashes all match. It stages the complete replacement, moves the prior publication to a recognized backup, installs the new publication, and restores or recovers supported interrupted states. Versioned transaction markers prevent deletion of unrelated staging directories, and recovery filesystem errors return blocking findings instead of tracebacks.

Verification: 383 tests passed, bytecode compilation passed, the package validator passed, and the final Standards and Spec reviews reported no findings.
