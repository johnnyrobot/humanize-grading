# 03 — Issue and validate verification receipts

**What to build:** Give a successfully verified grading batch one versioned, identity-free receipt that proves the exact artifacts that passed and makes verified progress derivable and invalidatable.

**Blocked by:** 02 — Make verification authoritative and whole-batch

**Status:** resolved

- [x] Persisted verification writes exactly one receipt only after every grade record update commits.
- [x] No-write verification changes no grade record and writes no receipt.
- [x] The receipt has a versioned schema and rejects malformed or unknown versions.
- [x] The receipt binds every redacted submission, draft, grade record, rubric, calibration profile, and pattern reference used.
- [x] Entries are deterministic and include roles, stable tokens where applicable, normalized relative locators where possible, and SHA-256 hashes.
- [x] The receipt contains no identity values, identity-bearing filenames, source text, token-map location, or rehydrated text.
- [x] Added, removed, renamed, missing, or changed participant artifacts invalidate the receipt.
- [x] Status reports verified progress only while the complete receipt remains current.
- [x] Legacy grading batches without receipts remain drafted and receive a verification-required finding when publication eligibility is requested.
- [x] Receipt behavior is covered through the grading-batch interface rather than private serialization helpers.

## Answer

Successful persisted verification now writes one deterministic, versioned, identity-free receipt after every updated grade record is saved. The receipt binds all six artifact roles with normalized locators and SHA-256 hashes. Status derives verified progress only while the complete receipt is current, reports stale or malformed receipts, and adds verification-required only when eligibility is requested for a legacy batch. No-write verification changes nothing.

Verification: 359 tests passed, bytecode compilation passed, the package validator passed, and the final Standards and Spec reviews reported no findings.
