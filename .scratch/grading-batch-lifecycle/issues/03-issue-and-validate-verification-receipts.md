# 03 — Issue and validate verification receipts

**What to build:** Give a successfully verified grading batch one versioned, identity-free receipt that proves the exact artifacts that passed and makes verified progress derivable and invalidatable.

**Blocked by:** 02 — Make verification authoritative and whole-batch

**Status:** ready-for-agent

- [ ] Persisted verification writes exactly one receipt only after every grade record update commits.
- [ ] No-write verification changes no grade record and writes no receipt.
- [ ] The receipt has a versioned schema and rejects malformed or unknown versions.
- [ ] The receipt binds every redacted submission, draft, grade record, rubric, calibration profile, and pattern reference used.
- [ ] Entries are deterministic and include roles, stable tokens where applicable, normalized relative locators where possible, and SHA-256 hashes.
- [ ] The receipt contains no identity values, identity-bearing filenames, source text, token-map location, or rehydrated text.
- [ ] Added, removed, renamed, missing, or changed participant artifacts invalidate the receipt.
- [ ] Status reports verified progress only while the complete receipt remains current.
- [ ] Legacy grading batches without receipts remain drafted and receive a verification-required finding when publication eligibility is requested.
- [ ] Receipt behavior is covered through the grading-batch interface rather than private serialization helpers.
