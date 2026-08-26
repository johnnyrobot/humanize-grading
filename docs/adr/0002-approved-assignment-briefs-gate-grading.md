---
status: accepted
date: 2026-08-26
---

# Approved assignment briefs gate grading

Every grading batch must have an identity-free, instructor-approved assignment
brief before an agent reads submissions or verification reads batch artifacts.
The brief records how the current rubric and calibration profile apply to one
assignment. Approval binds the exact brief body, rubric and profile with
digests.

Whole-batch verification writes schema 2 receipts. Each receipt includes one
token-free `assignment-brief` artifact and binds the exact approved file used
for verification. Schema 1 receipts predate this evidence and must be verified
again after the instructor approves a brief.

The exact chat phrase and local approval command create auditable workflow
state. They do not authenticate the person who sent the message, provide a
signature, control access or authorize cloud processing. Institutional policy
still decides whether student work may reach a cloud model or agent.

## Considered options

- **Keep approval only in chat:** Rejected because chat state cannot be checked
  after an interruption or bound to verification and publication.
- **Require the instructor to run the approval command:** Rejected as the main
  workflow because an agent sharing the same local account can run it too. The
  command records the instructor's exact prior message; it does not authenticate
  a human.
- **Repeat the interview after every interruption:** Rejected because unchanged,
  digest-bound approval can be checked directly. Repetition adds work without
  stronger evidence.
- **Retain verification receipt schema 1:** Rejected because making assignment
  briefing mandatory under the old schema would hide the compatibility break.

## Consequences

- The agent may read the rubric, profile, assignment prompt and
  instructor-authored course material before approval, but not submissions, the
  roster, the redaction review file or the token map.
- A changed brief body, course, batch, host mode, rubric or profile requires
  approval and whole-batch verification again.
- Interrupted work may resume after `brief check` confirms that the approval is
  current.
- Schema 1 receipts cannot publish. The instructor must approve a brief and the
  agent must verify the whole batch again.
- Rehydrated feedback remains a draft until the instructor signs off.
