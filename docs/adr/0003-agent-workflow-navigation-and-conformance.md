---
status: accepted
date: 2026-08-26
---

# Agent workflow navigation uses executable state

Agents navigate grading through executable scaffolding. `grade.py next` derives
one legal action from identity-free durable state. `grade.py record prepare`
creates the valid, deliberately unfinished draft pair for the pending token.
The agent fills that pair instead of reconstructing the record schema from
prompt text.

The redaction manifest is schema `redaction-batch/2` and stores whether the
instructor completed the private review. A schema 1 manifest loads as
unreviewed. Verification blocks an unreviewed manifest because chat history and
an artifact's existence do not prove that review happened.

Conformance adapters are JSON argument arrays. The harness replaces only its
documented placeholders and launches the result without shell interpolation.
Read evidence is separate from artifact and lifecycle evidence. A trial with
no usable read trace is `UNVERIFIED`, even when its artifacts pass.

## Considered options

- **Keep the workflow in prompts only:** Rejected because an interrupted or
  smaller agent can skip a stage or invent a record shape. Executable state can
  be checked after every action and across sessions.
- **Treat artifacts as proof of reads:** Rejected because a correct artifact
  does not show which private files an agent opened. Only an available read
  trace can prove the read boundary.
- **Store redaction review only in chat:** Rejected because chat state is not
  durable or bound to the redaction result. The identity-free manifest carries
  the reviewed state with the exact output set.
- **Accept shell command text for adapters:** Rejected because parsing or
  evaluating shell text adds injection and portability risks. JSON preserves
  the exact argument boundaries.

## Consequences

- Agents run `next --json`, execute only its returned action, and repeat.
- A repair action exits 12 and cannot advance until the state is repaired.
- Prepared feedback markers and unassessed record values remain blocking until
  the agent replaces them with supported grading judgments.
- Legacy redaction batches must be redacted again with reviewed state before
  verification can pass.
- Conformance reports distinguish deterministic success, traced read safety,
  and missing read evidence instead of inferring one from another.
