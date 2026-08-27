# Local-agent compatibility and conformance

**Date:** 2026-08-26
**Status:** Approved through the instructor's delegated recommendation authority

## Summary

Make the Grader workflow easier for small and local agents to execute without
weakening its safety gates. Add three small public interfaces:

1. `grade.py record prepare` creates a structurally valid, deliberately
   incomplete feedback-and-record pair for one pending redacted submission.
2. `grade.py next` inspects durable grading state and returns the next legal
   action in plain text or versioned JSON.
3. `agent_conformance.py` runs or audits synthetic agent trials and records
   commands, elapsed time, artifacts, lifecycle exits, and read-boundary
   evidence when a runtime can provide it.

The redaction manifest becomes the durable source of whether the instructor
completed private review. Verification refuses an unreviewed manifest. After
implementation, repeat the Bionic/Gemma synthetic trial and record whether the
new interfaces improve completion.

The package version becomes 3.4.0. This work does not publish, tag, or push the
release without a separate instruction.

## Evidence from 3.3.0

The 2026-08-26 local-agent trial found:

- Hermes could not produce a first workflow response across the available
  practical Ollama models.
- The Unsloth agent bridges failed or timed out before grading.
- Bionic prepared and approved a brief, redacted correctly, and respected the
  private-review pause.
- Bionic then wrote `{"id": "STUDENT_01", "score": 85, "status": "draft"}`
  and placeholder feedback instead of the required `grade-record/1` pair.
- Verification failed closed with exit 13, but Bionic still attempted
  consistency and rehydration.

The safety layer worked. The agent-facing interface did not give a weak model
enough structure to complete the job.

## Goals

- Let an agent create the exact record and feedback shapes without reconstructing
  schemas from prose.
- Give an agent one authoritative answer to “what may I do next?”
- Persist the instructor's redaction-review completion in identity-free state.
- Prevent verification when the redaction review is incomplete.
- Make verification failures a terminal next-action state until repaired.
- Run reproducible synthetic trials through arbitrary local agent commands
  without shell interpolation.
- Distinguish proven read-boundary compliance, proven violations, and missing
  evidence.
- Preserve the token-map and identity-bearing review-file seam.

## Non-goals

- Choose scores, quotes, feedback, or academic judgment for the model.
- Make malformed or placeholder work pass verification.
- Authenticate the instructor's review confirmation.
- Build runtime-specific integrations into the grading modules.
- Claim that a runtime respected read boundaries when it supplies no trace.
- Submit grades to an LMS.
- Make local inference engines fast or repair third-party agent bridges.

## Domain language

Add these terms to `CONTEXT.md`:

**Draft pair**:
The feedback Markdown and `grade-record/1` JSON for one redacted submission.
Both files must exist before the submission is drafted.
_Avoid_: Output files, grade bundle

**Next action**:
The one legal workflow stage derived from current identity-free grading state.
It may include one command or one ordered command group.
_Avoid_: Suggestion, plan

**Conformance trial**:
A synthetic agent run scored from observable commands, elapsed time, artifacts,
lifecycle exits, and available read traces.
_Avoid_: Benchmark, model grade

## Decisions

| Decision | Choice | Reason |
| --- | --- | --- |
| Module shape | `drafts.py`, `workflow.py`, and `conformance.py` | Each hides a distinct body of behavior behind a small interface. |
| Record command | `grade.py record prepare` | Matches the existing nested `brief` interface and names the action rather than the file format. |
| Generated state | Valid schema, intentionally unassessed | Weak agents start from a loadable record, while verification still blocks unchanged templates. |
| Feedback skeleton | Rubric-derived sections plus explicit markers | The rubric remains the source of truth; markers prevent accidental publication. |
| Workflow command | `grade.py next` | One short leading word is easy for agents to remember and repeat. |
| Workflow output | Plain text by default; `grading-next-action/1` JSON on `--json` | Humans and agents use the same module interface. |
| Redaction persistence | Redaction manifest schema 2 with `reviewed: bool` and integer `exit_code` | Identity-free durable state must prove both a clean run and private review. |
| Legacy manifests | Read schema 1 as `reviewed=False`, `exit_code=None` | Old batches fail safely and must rerun reviewed redaction. |
| Verification | Block nonzero or unreviewed redaction with exit 12 | The state is incomplete, not malformed. |
| Conformance launch | JSON argv with placeholders; no shell command | Avoids injection and works with different CLIs. |
| Read evidence | Optional trace; absent trace is `unverified` | Artifact checks cannot prove which files a model opened. |
| Trial data | Shipped PHIL 210 synthetic fixture | Reproducible and safe to inspect locally. |
| Version | 3.4.0 | New CLI and manifest behavior are a feature-level change. |

## Architecture

### Draft-pair module

Create `scripts/grading/drafts.py` with this public interface:

```python
TEMPLATE_MARKER = "<!-- replace-with-feedback -->"

@dataclass(frozen=True)
class DraftPair:
    record: Path
    feedback: Path

class DraftPreparationError(ValueError):
    pass

def prepare_draft_pair(
    *,
    batch: str,
    course: str,
    source: Path,
    rubric: Rubric,
    profile: Path,
    patterns: Path,
    drafts: Path,
) -> DraftPair: ...
```

The implementation:

- requires a regular token-named source inside the configured redacted root;
- computes pattern density from the redacted source;
- creates every rubric criterion with exact number, name and points;
- initializes criteria as `assessable: false`, `score: null`, level
  `cannot assess`, and no justifications;
- sets total and possible total to 0 and letter to `cannot assess`;
- renders the rubric's exact feedback sections and first allowed sign-off;
- includes `TEMPLATE_MARKER` in every unfinished feedback region;
- refuses if either destination exists;
- stages and installs both files as one operation, leaving neither on failure;
- never reads submissions, roster, review file, or token map.

`verify.py` adds a blocking `template-marker` finding whenever the feedback
still contains `TEMPLATE_MARKER`. An untouched record loads successfully and
fails semantically with `nothing-assessed`, not `malformed-record`.

### Record CLI

Add:

```bash
python3 scripts/grade.py record prepare \
  --batch essay-2 --course phil210 \
  --rubric tests/fixtures/phil210/grading_rubric.md \
  --source redacted/STUDENT_01.md
```

Defaults:

- profile: `.grading/profiles/<course>.md`
- brief: `.grading/assignments/<batch>.md`
- drafts: `.grading/drafts/<batch>/`
- patterns: `references/ai-patterns.md`
- redacted root: `redacted/`

The command checks the current profile, rubric, approved brief, reviewed
redaction manifest and grading-batch inventory. It prepares only a pending
token. It refuses overwrite, duplicates, incomplete pairs and orphaned state.

Success prints both paths and the next instruction: fill every criterion,
replace every feedback marker, then run `grade.py next`.

## Durable redaction review

Change the current manifest to:

```json
{
  "schema": "redaction-batch/2",
  "batch": "essay-2",
  "run_id": "...",
  "reviewed": true,
  "exit_code": 0,
  "outputs": [{"path": "STUDENT_01.md", "sha256": "..."}]
}
```

`RedactionManifest.create()` requires `reviewed` and the final integer
`exit_code`. The pipeline calculates the final result before saving the
manifest. Schema 1 loads as `reviewed=False`, `exit_code=None`, preserving its
output bindings but requiring the instructor to rerun:

```bash
python3 scripts/redact.py <submissions> ... --reviewed --force
```

Before roster loading or any pipeline input check, a new attempt invalidates a
regular same-batch manifest at a safe destination. It preserves the run ID and
output digests, writes `reviewed: false`, and records the nonzero attempt code.
It does not follow links or write when roots overlap. First-ever failures with
no manifest remain retryable and cannot advance to drafting.

Open an eligible manifest with `O_NOFOLLOW` where available and bind it by
regular-file type, device, inode and single-link count. Stage an atomic owner-only
replacement, then recheck those facts immediately before replacing the path. A
mismatch stops without replacement or in-place fallback. If atomic I/O fails
while identity is unchanged, rewrite the complete reviewed-false nonzero payload
through the bound descriptor, truncate and sync it, and set mode 0600. A partial
write is malformed and blocks grading. If neither write completes, raise a
dedicated plain invalidation error; the CLI and pipeline return exit 6 before
roster or input processing.

Whole-batch verification adds `redaction-incomplete` before loading draft
content when the manifest does not record exit 0. A clean unreviewed result adds
`redaction-review-required`. The token map and private review file remain
unchanged and outside `scripts/grading/`.

## Next-action module

Create `scripts/grading/workflow.py`:

```python
NEXT_SCHEMA = "grading-next-action/1"

@dataclass(frozen=True)
class WorkflowContext:
    batch: str
    course: str
    rubric: Path
    redacted: Path
    drafts: Path
    root: Path
    patterns: Path
    publication: Path
    submissions: Path | None = None
    roster: Path | None = None
    instructor: tuple[str, ...] = ()

@dataclass(frozen=True)
class NextAction:
    stage: str
    action: str
    message: str
    commands: tuple[tuple[str, ...], ...] = ()
    token: str = ""
    findings: tuple[BlockingFinding, ...] = ()

    def as_dict(self) -> dict: ...

def next_action(context: WorkflowContext) -> NextAction: ...
```

Action codes, in precedence order:

1. `calibrate` — profile missing.
2. `recalibrate` — rubric drift.
3. `prepare-brief` — assignment brief missing.
4. `complete-brief` — draft brief still contains answer markers.
5. `approve-brief` — complete brief is still draft.
6. `repair-brief` — approved brief is stale or malformed.
7. `redact` — no manifest exists.
8. `review-redaction` — exit 3 or clean manifest with `reviewed: false`.
9. `repair-redaction` — manifest records nonzero exit 2, 4, 5, 6 or 7.
10. `repair-batch` — inventory or manifest is malformed, duplicated,
   incomplete or orphaned.
11. `prepare-record` — first sorted participant is pending.
12. `complete-draft` — a prepared pair is still semantically incomplete.
13. `repair-verification` — dry whole-batch verification has blocking findings.
14. `verify` — every pair passes a dry run but no current receipt exists.
15. `consistency-and-publish` — receipt is current; commands are consistency
    then explicit rehydration.
16. `review-publication` — a publication exists and the instructor must review
    its identity-bearing drafts outside model context. This is the terminal
    action; instructor sign-off is not stored or inferred.

`next_action()` may read only identity-free state. It never reads submissions,
roster content, review content, token-map content, or rehydrated text. Optional
private paths are used only to render exact redaction and rehydration commands.

Add CLI:

```bash
python3 scripts/grade.py next --batch essay-2 --course phil210 \
  --rubric tests/fixtures/phil210/grading_rubric.md \
  --publication final/essay-2 --json
```

The JSON shape is stable and uses argv arrays, not shell strings:

```json
{
  "schema": "grading-next-action/1",
  "batch": "essay-2",
  "course": "phil210",
  "stage": "drafting",
  "action": "prepare-record",
  "message": "Prepare the draft pair for STUDENT_01.",
  "token": "STUDENT_01",
  "commands": [["python3", "scripts/grade.py", "record", "prepare"]],
  "findings": []
}
```

The command exits 0 whenever it can name a legal action. It exits 12 when the
only legal action is repair and 13 when workflow state itself is malformed.
Direct verification and publication gates remain authoritative.

## Conformance module and CLI

Create `scripts/grading/conformance.py` for trial state and scoring, plus the
thin entry point `scripts/agent_conformance.py`.

The following invocation shapes are illustrative until Task 6 records live
evidence for an exact runtime and model combination.

Public CLI:

```bash
python3 scripts/agent_conformance.py run \
  --runtime hermes-granite \
  --command-json '["hermes", "--safe-mode", "-z", "{prompt}"]' \
  --timeout 300

python3 scripts/agent_conformance.py audit \
  --runtime bionic-gemma \
  --workspace /tmp/humanize-grading-bionic-e2e
```

`run`:

1. Copies only reviewed package files and the one-submission synthetic fixture
   into a new temporary workspace.
2. Writes a one-row synthetic roster and current synthetic profile.
3. Runs three self-contained phases: briefing, approval/redaction and
   reviewed-grading/publication.
   The trial publication path is exactly `final/agent-e2e` in phase prompts,
   next-action argv and artifact audit.
4. Replaces only `{prompt}` and `{workspace}` in the parsed JSON argv.
5. Uses `subprocess.run(..., shell=False)` with a per-phase timeout.
6. Captures stdout/stderr to owner-only files and records SHA-256 digests in the
   report rather than embedding unlimited logs.
7. Stops after the first failed phase.
8. Runs artifact, lifecycle and privacy checks after every phase.

`audit` evaluates an existing synthetic workspace without launching an agent.

Trial report schema: `grading-agent-conformance/1`. It records:

- runtime label and exact repository version;
- phase start/end, elapsed seconds, process exit and timeout;
- sanitized argv with prompt replaced by its SHA-256;
- artifact names, hashes and modes before and after;
- brief, manifest, status, dry verification, receipt and publication results;
- identity-leak scan over redacted work and draft pairs;
- trace result: `passed`, `failed` or `unverified`.

Optional trace schema: `grading-agent-trace/1`, one JSON object per line:

```json
{"schema":"grading-agent-trace/1","event":"read","path":"redacted/STUDENT_01.md"}
{"schema":"grading-agent-trace/1","event":"command","argv":["python3","scripts/grade.py","next"],"exit_code":0}
```

Allowed reads are phase-specific. Any traced read of submissions, roster,
private review, token map or final publication by the agent fails the trial.
When a runtime supplies no trace, the report says `unverified`; artifact and
lifecycle results remain independently scored.

## Agent instructions

Keep the primary skill sequence short:

1. Run `grade.py next --json` before each stage.
2. Execute only the returned action.
3. For `prepare-record`, edit the generated pair rather than creating files
   from memory.
4. After any non-zero lifecycle exit, run `next` and repair; the legal action
   cannot advance while verification is blocked.

Move record-field reference into `references/rubric-format.md` and conformance
instructions into a new `references/agent-conformance.md`. Use strong context
pointers from `SKILL.md`; do not duplicate full schemas in the skill.

## Error handling

- Template preparation is exclusive and pair-atomic.
- A stale profile or brief blocks preparation and `next` advancement.
- Schema 1, exit 3, or clean unreviewed schema 2 manifests return
  `review-redaction`. Other nonzero exits return `repair-redaction`. All block
  verification.
- Invalid command JSON, missing placeholders, oversized output and process
  timeouts fail the conformance phase without a traceback.
- Agent output is untrusted and bounded before storage.
- Conformance never follows symlinks outside its workspace.
- Absence of read trace is reported, never inferred.

## Test seams

TDD tests use only these public seams:

1. `prepare_draft_pair()` and `grade.py record prepare` — exact files,
   overwrite refusal, atomic cleanup, rubric-derived shape and template-marker
   verification.
2. `RedactionManifest.create/load/validate` and verification — schema 2,
   legacy schema 1, reviewed state and blocking behavior.
3. `next_action()` and `grade.py next --json` — one known fixture per action
   code, precedence and no private reads.
4. `agent_conformance.py run/audit` — fake agent adapters, timeout, bounded
   logs, trace pass/fail/unverified, leak detection and stop-on-failure.
5. Existing end-to-end test — use record preparation before deterministic
   grading and preserve successful receipt/publication behavior.

No test mocks internal grading modules. Fake external agents are executable
adapters at the real subprocess seam.

## Documentation and release

- Add ADR 0003 for agent-driven workflow navigation and conformance evidence.
- Update `SKILL.md`, `README.md`, `docs/using-the-grader.md`,
  `references/rubric-format.md`, `CONTEXT.md` and version history together.
- Add new package files to `PACKAGE_FILES.txt`.
- Set version 3.4.0 in both skills and plugin metadata.
- Keep each skill under 500 lines.

Release checks remain:

```bash
python3 -m pytest tests/
python3 scripts/validate-package.py
npx skills add . --list
npx skills add . --full-depth --list
claude plugin validate .
```

## Retest acceptance

Run Bionic 1.0.9 with local Gemma 4 E4B against a new synthetic workspace.
The trial passes only if:

- Bionic uses `next` between stages;
- it prepares rather than invents the draft pair;
- no prohibited read appears in available tool evidence;
- the record loads as `grade-record/1`;
- unchanged markers cannot verify;
- the repaired draft passes whole-batch verification;
- consistency runs only after verification;
- publication occurs only with a current receipt;
- final identity-bearing output is not opened by the model; and
- the conformance report records elapsed time and evidence limits honestly.

If Bionic still fails, the release may still ship when the deterministic
interfaces and harness pass, but documentation must state the observed agent
limit. “Fully works” is not claimed until at least one non-Codex local agent
completes the synthetic lifecycle.

## Alternatives considered

### Add more examples to `SKILL.md`

Rejected. Bionic already read the skill and still invented an invalid record.
More prose increases context load without making the file shape executable.

### Put all logic in `scripts/grade.py`

Rejected. CLI handlers would duplicate profile, brief, manifest, inventory and
verification ordering. The complexity would reappear in tests and future
adapters.

### Treat artifact success as proof of safe reads

Rejected. A model can read a prohibited file and still produce safe artifacts.
Read compliance is proven only by trace evidence or reported as unverified.

### Build one adapter per local app now

Rejected. Hermes, Unsloth, OpenCode and Bionic expose different session and UI
models. The JSON-argv seam supports callable agents now; Bionic uses audit mode
until it exposes a stable CLI adapter.
