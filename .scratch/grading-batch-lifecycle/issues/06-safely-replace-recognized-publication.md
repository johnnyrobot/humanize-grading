# 06 — Safely replace a recognized publication

**What to build:** Let an instructor explicitly replace an unchanged publication from the same grading batch while refusing modified or unrelated output and recovering safely from an interrupted replacement.

**Blocked by:** 05 — Publish through the verified-batch capability

**Status:** ready-for-agent

- [ ] A non-empty destination is refused unless safe force is explicitly requested.
- [ ] The publication marker binds the grading-batch identity, verification-receipt digest, exact output file set, and output hashes.
- [ ] Safe force refuses a missing or unknown marker, another grading batch, stale receipt lineage, modified output, missing expected output, and unrelated extra files.
- [ ] Safe force prepares the complete replacement beside the destination before changing current output.
- [ ] Replacement moves the recognized destination to a backup before moving the staged publication into place.
- [ ] Failure after the backup move restores the prior complete publication when possible.
- [ ] A later run recognizes and recovers every supported interrupted-transaction state.
- [ ] The destination is never exposed as a partly populated new grading batch.
- [ ] The documented behavior acknowledges that portable forced replacement may briefly leave the target path absent.
- [ ] Safe-force tests exercise successful replacement, refusal, rollback, and recovery through the publication interface.
