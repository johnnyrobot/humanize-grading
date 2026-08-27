# Local-Agent Compatibility Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give local agents executable grading scaffolds, one durable next action, and a reproducible conformance harness while preserving fail-closed verification and identity seams.

**Architecture:** `scripts/grading/drafts.py` owns preparation of a valid but incomplete draft pair. `scripts/grading/workflow.py` derives one `NextAction` from identity-free durable state. `scripts/grading/conformance.py` observes or drives synthetic trials through a JSON-argv subprocess adapter; thin CLIs expose all three modules.

**Tech Stack:** Python 3 standard library, pytest, Markdown agent instructions, JSON/JSONL conformance reports.

**Spec:** `docs/superpowers/specs/2026-08-26-local-agent-compatibility-design.md`

## Global Constraints

- Package version is exactly `3.4.0` in both skills, the first README history entry, and `.claude-plugin/plugin.json`.
- Redaction manifest schema is exactly `redaction-batch/2` and contains
  identity-free `reviewed: bool` plus the final integer `exit_code`.
- Legacy `redaction-batch/1` manifests load as `reviewed=False`, `exit_code=None`.
- Whole-batch verification blocks a nonzero manifest with
  `redaction-incomplete`, or an unreviewed clean manifest with
  `redaction-review-required`, and exit 12.
- A new safe redaction attempt invalidates regular same-batch clean state before
  roster and input processing while preserving identity-free bindings. Links
  and overlapping roots are never rewritten.
- Draft pairs contain feedback Markdown and `grade-record/1` JSON for one token.
- Feedback skeleton marker is exactly `<!-- replace-with-feedback -->` and always blocks unchanged feedback.
- Record preparation reads only an approved brief, current profile/rubric, pattern reference, reviewed manifest, batch inventory, and redacted source.
- Next-action JSON schema is exactly `grading-next-action/1`.
- Conformance report schema is exactly `grading-agent-conformance/1`.
- Optional trace schema is exactly `grading-agent-trace/1`; absent trace is `unverified`.
- External agent commands are parsed JSON argv and run with `shell=False`.
- Nothing under `scripts/grading/` imports or loads the token map or private redaction review.
- Agent output and trace inputs are bounded; symlinks cannot escape a trial workspace.
- Calibration remains local-only and separate from grading.
- Rehydrated drafts remain pending instructor sign-off and are not opened by the model.
- Use Plain Language and keep each `SKILL.md` under 500 lines.
- Update `PACKAGE_FILES.txt` for every tracked file.

---

### Task 1: Persist Redaction Review in Manifest Schema 2

**Files:**
- Modify: `scripts/redaction/manifest.py`
- Modify: `scripts/redaction/pipeline.py`
- Modify: `scripts/grading/batch.py`
- Modify: `tests/redaction/test_pipeline.py`
- Modify: `tests/grading/test_verify.py`
- Modify: `tests/grading/test_publication.py`
- Modify: `tests/grading/test_end_to_end.py`

**Interfaces:**
- Produces: `RedactionManifest(batch, run_id, reviewed, exit_code, outputs)`,
  required `reviewed` and `exit_code` keywords on
  `RedactionManifest.create()`, and verification findings
  `redaction-review-required` / `redaction-incomplete` for Tasks 2–4.
- Preserves: output membership and SHA-256 validation, token-map isolation, receipt binding.

- [ ] **Step 1: Write RED tests for schema 2 and legacy review state**

Add behavior tests equivalent to:

```python
def test_manifest_round_trip_persists_reviewed_state(tmp_path):
    redacted = tmp_path / "redacted"
    redacted.mkdir()
    (redacted / "STUDENT_01.md").write_text("safe", encoding="utf-8")
    RedactionManifest.create(
        "essay-2", "run-identifier-1234", [redacted / "STUDENT_01.md"],
        reviewed=True,
        exit_code=0,
    ).save(redacted)

    loaded = RedactionManifest.load(redacted)

    assert loaded.reviewed is True
    assert loaded.exit_code == 0
    assert json.loads((redacted / "_redaction.json").read_text())["schema"] == "redaction-batch/2"


def test_legacy_manifest_is_treated_as_unreviewed(tmp_path):
    redacted = _write_schema_one_manifest(tmp_path)
    assert RedactionManifest.load(redacted).reviewed is False
```

Add pipeline tests proving review state and the final exit are persisted. Add
verification tests proving unreviewed or nonzero manifests return the matching
token-free finding, check no tokens, write no record or receipt and have
`VerificationFailure.FAILED`.

- [ ] **Step 2: Run focused tests and confirm RED**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/redaction/test_pipeline.py tests/grading/test_verify.py -x -v
```

Expected: manifest constructor/schema/review/exit assertions fail before
production changes.

- [ ] **Step 3: Implement schema 2 with safe schema 1 loading**

Use this public shape:

```python
SCHEMA = "redaction-batch/2"
LEGACY_SCHEMA = "redaction-batch/1"

@dataclass(frozen=True)
class RedactionManifest:
    batch: str
    run_id: str
    reviewed: bool
    exit_code: int | None
    outputs: tuple[tuple[str, str], ...] = ()
```

Require `reviewed` and integer `exit_code` in `create()`, write both in `save()`,
require their JSON types for schema 2, and map schema 1 to `False` / `None`.
Calculate the pipeline's final exit before creating the manifest.

In `GradingBatch.verify()`, after the approved-brief snapshot and before entry
loads, require exit 0 and reviewed state. Add one token-free blocking finding
and return FAILED without reading draft content when either gate is incomplete.

- [ ] **Step 4: Update every explicit test manifest construction**

Search with:

```bash
rg -n 'RedactionManifest\.create\(' scripts tests
```

Every production call passes the real review value. Synthetic verification and
publication fixtures pass `reviewed=True` unless the test exercises the gate.

- [ ] **Step 5: Run focused and full tests**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/redaction/test_pipeline.py tests/grading/test_verify.py tests/grading/test_publication.py tests/grading/test_end_to_end.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

- [ ] **Step 6: Commit Task 1**

```bash
git add scripts/redaction/manifest.py scripts/redaction/pipeline.py scripts/grading/batch.py tests/
git commit -m "feat: persist redaction review state"
```

---

### Task 2: Prepare Valid Draft Pairs

**Files:**
- Create: `scripts/grading/drafts.py`
- Create: `tests/grading/test_drafts.py`
- Modify: `scripts/grading/verify.py`
- Modify: `scripts/grade.py`
- Modify: `tests/grading/test_batch.py`

**Interfaces:**
- Consumes: reviewed `RedactionManifest`, `Rubric`, `Profile`, `GradeRecord`, density scanner and approved brief.
- Produces: `DraftPair`, `DraftPreparationError`, `prepare_draft_pair()`, `TEMPLATE_MARKER`, and `grade.py record prepare` for Tasks 3–6.

- [ ] **Step 1: Write the first RED draft-pair test**

```python
def test_prepare_draft_pair_writes_loadable_rubric_derived_files(
    tmp_path, phil210
):
    source = tmp_path / "redacted" / "STUDENT_01.md"
    source.parent.mkdir()
    source.write_text("Community solar needs a named causal mechanism.", encoding="utf-8")
    drafts = tmp_path / "drafts"

    pair = prepare_draft_pair(
        batch="essay-2",
        course="phil210",
        source=source,
        rubric=load_rubric(phil210 / "grading_rubric.md"),
        profile=phil210 / "profile_phil210.md",
        patterns=PATTERNS_PATH,
        drafts=drafts,
    )

    record = GradeRecord.load(pair.record)
    assert pair.feedback == drafts / "STUDENT_01.md"
    assert [item.number for item in record.criteria] == [1, 2, 3, 4]
    assert all(item.assessable is False and item.score is None for item in record.criteria)
    assert record.pattern_density["words"] == 7
    assert TEMPLATE_MARKER in pair.feedback.read_text(encoding="utf-8")
```

- [ ] **Step 2: Confirm RED**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_drafts.py -v
```

Expected: import failure for `scripts.grading.drafts`.

- [ ] **Step 3: Implement the draft-pair module**

Render a `GradeRecord` with exact rubric criteria, all unassessed, totals 0,
letter `cannot assess`, fresh density and status draft. Render feedback from
`rubric.protocol.sections`, inline-annotation requirement and first sign-off.
When no sections exist, render `**Feedback**` and one marker.

Stage both UTF-8 files in the destination directory. Refuse symlinks and any
existing destination. Install both; if the second install fails, remove the
first installed file and all staging files before raising
`DraftPreparationError`.

- [ ] **Step 4: Add RED/GREEN edge slices**

Add and run one test at a time:

| Test | Exact behavior |
| --- | --- |
| `test_prepare_refuses_to_overwrite_either_member` | Pre-create the JSON destination, call `prepare_draft_pair()`, assert `DraftPreparationError`, the existing bytes unchanged and no Markdown destination. Repeat with only Markdown pre-created. |
| `test_prepare_refuses_a_non_token_source` | Name the source `paper.md`, call the public function and assert the error names the required `STUDENT_NN` or `UNKNOWN_NN` token shape. |
| `test_feedback_uses_exact_protocol_sections_and_signoff` | Parse the PHIL 210 rubric, prepare a pair and assert the Markdown headings equal the protocol order and the last line equals its first allowed sign-off. |
| `test_unchanged_feedback_marker_is_a_blocking_verification_finding` | Verify the prepared record/feedback against its source and assert one error code is `template-marker`. |

`verify.py` imports `TEMPLATE_MARKER` and adds `template-marker` when present.

- [ ] **Step 5: Add the record CLI with RED subprocess tests**

Test this exact successful interface:

```python
result = _run(
    "record", "prepare",
    "--batch", "essay-2",
    "--course", "phil210",
    "--rubric", str(phil210 / "grading_rubric.md"),
    "--source", str(redacted / "STUDENT_01.md"),
    "--redacted", str(redacted),
    "--root", str(root),
)
assert result.returncode == 0
assert "Fill every criterion" in result.stdout
```

Also test missing/unapproved brief, unreviewed manifest, non-pending token,
incomplete/orphaned batch, path outside redacted and overwrite refusal. Each
failure has one plain-language line and no traceback.

- [ ] **Step 6: Implement nested `record prepare` parser and handler**

Use current profile/brief helpers, reviewed manifest and `GradingBatch.status()`.
The handler must verify that the selected source is the source of exactly one
pending entry before calling `prepare_draft_pair()`.

- [ ] **Step 7: Run focused and full tests**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_drafts.py tests/grading/test_batch.py tests/grading/test_verify.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

- [ ] **Step 8: Commit Task 2**

```bash
git add scripts/grading/drafts.py scripts/grading/verify.py scripts/grade.py tests/grading/test_drafts.py tests/grading/test_batch.py tests/grading/test_verify.py
git commit -m "feat: prepare valid grading draft pairs"
```

---

### Task 3: Derive One Durable Next Action

**Files:**
- Create: `scripts/grading/workflow.py`
- Create: `tests/grading/test_workflow.py`
- Modify: `scripts/grading/brief.py`
- Modify: `scripts/grade.py`

**Interfaces:**
- Consumes: Task 1 reviewed manifests, Task 2 draft pairs, existing batch/verification/publication state.
- Produces: `WorkflowContext`, `NextAction`, `next_action()`, `load_brief()` and `grade.py next` for Tasks 4–6.

- [ ] **Step 1: Expose read-only brief loading with RED/GREEN tests**

Add:

```python
def load_brief(path: Path) -> AssignmentBrief:
    """Load a draft or approved assignment brief without changing it."""
```

Test that prepared draft and approved brief both load, while malformed metadata
still raises `BriefError`. Keep approval digest checks in `check_brief()`.

- [ ] **Step 2: Write the first RED next-action test**

```python
def test_next_action_names_the_first_pending_record(tmp_path, ready_batch):
    context = ready_batch.context(tmp_path)
    action = next_action(context)
    assert action.action == "prepare-record"
    assert action.token == "STUDENT_01"
    assert action.commands[0][-4:] == (
        "--source", str(context.redacted / "STUDENT_01.md"),
        "--redacted", str(context.redacted),
    )
```

- [ ] **Step 3: Implement data types and JSON interface**

Use the exact `WorkflowContext` and `NextAction` signatures from the spec.
`NextAction.as_dict()` returns schema, batch, course, stage, action, message,
token, argv-array commands and serialized findings.

- [ ] **Step 4: Add one vertical slice per action precedence**

Create fixture states and literal assertions for every precedence branch:

| Test | Setup | Expected action |
| --- | --- | --- |
| `test_missing_profile_returns_calibrate` | No profile path | `calibrate` |
| `test_rubric_drift_precedes_brief_state` | Drifted profile plus no brief | `recalibrate` |
| `test_missing_brief_returns_prepare_brief` | Current profile only | `prepare-brief` |
| `test_unanswered_brief_returns_complete_brief` | Prepared brief with markers | `complete-brief` |
| `test_complete_draft_brief_returns_approve_brief` | Markers replaced, status draft | `approve-brief` |
| `test_stale_approved_brief_returns_repair_brief` | Approved brief then edit its body | `repair-brief` |
| `test_missing_manifest_returns_redact` | Current approved brief, no manifest | `redact` |
| `test_unreviewed_manifest_returns_review_redaction` | Exit 3 or clean schema 2 reviewed false | `review-redaction` |
| `test_incomplete_redaction_returns_exact_repair_action_and_cli_exit` | Schema 2 exit 2, 4, 5, 6 or 7 | `repair-redaction` |
| `test_inventory_finding_returns_repair_batch` | Reviewed manifest plus orphan JSON | `repair-batch` |
| `test_template_pair_returns_complete_draft` | Pair from Task 2 unchanged | `complete-draft` |
| `test_invalid_record_returns_repair_verification` | Structurally loadable pair with wrong total | `repair-verification` |
| `test_clean_dry_run_returns_verify` | Complete valid pair, no receipt | `verify` |
| `test_current_receipt_returns_consistency_and_publish` | Persisted verification receipt | `consistency-and-publish` |
| `test_publication_returns_terminal_review_publication` | Valid publication marker and named output files | `review-publication` |

Every test observes only `next_action()` results. Use the real filesystem and
real grading modules; do not mock internal functions.

- [ ] **Step 5: Prove no private reads**

Put unreadable/symlink sentinels at submissions, roster, review and map paths.
Pass their path names through `WorkflowContext`. Assert `next_action()` returns
the expected redaction or publication argv without opening the sentinels.

- [ ] **Step 6: Add `grade.py next` CLI tests and implementation**

CLI args: required batch/course/rubric; optional redacted/drafts/root/patterns,
publication/submissions/roster/instructor; `--json`. Omitted publication resolves
to `final/<batch>`.

JSON test:

```python
payload = json.loads(result.stdout)
assert payload["schema"] == "grading-next-action/1"
assert payload["action"] == "prepare-record"
assert isinstance(payload["commands"][0], list)
```

Plain output prints action, message, token, each command with shell-safe quoting
for display only, and findings. Exit 0 for progress actions, 12 for repair
actions, 13 for malformed workflow state.

- [ ] **Step 7: Run focused and full tests**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_workflow.py tests/grading/test_brief.py tests/grading/test_batch.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

- [ ] **Step 8: Commit Task 3**

```bash
git add scripts/grading/workflow.py scripts/grading/brief.py scripts/grade.py tests/grading/test_workflow.py tests/grading/test_brief.py
git commit -m "feat: expose the next legal grading action"
```

---

### Task 4: Add the Agent Conformance Harness

**Files:**
- Create: `scripts/grading/conformance.py`
- Create: `scripts/agent_conformance.py`
- Create: `tests/grading/test_conformance.py`
- Create: `references/agent-conformance.md`

**Interfaces:**
- Consumes: `grade.py next`, synthetic package fixture, optional trace JSONL.
- Produces: `grading-agent-conformance/1` reports through `run` and `audit`.

- [ ] **Step 1: Write RED tests for safe command parsing**

```python
def test_command_json_is_argv_not_shell_text():
    command = parse_command_json('["fake-agent", "--prompt", "{prompt}"]')
    assert command == ("fake-agent", "--prompt", "{prompt}")


@pytest.mark.parametrize("raw", ["echo hi", "{}", "[]", '["agent", 3]'])
def test_invalid_command_json_is_refused(raw):
    with pytest.raises(ConformanceError):
        parse_command_json(raw)
```

- [ ] **Step 2: Implement bounded report data types**

Create immutable `TrialPhase`, `TraceResult`, `ConformanceReport` and
`ConformanceError`. Set exact bounds: 64 argv items, 4,096 characters per item,
2 MiB stdout, 2 MiB stderr, 10,000 trace events and 1 MiB per trace line.

- [ ] **Step 3: Write RED/GREEN audit tests**

Use real synthetic workspaces for these exact cases:

| Test | Observable assertion |
| --- | --- |
| `test_audit_detects_malformed_record_and_absent_receipt` | Report has `passed=False`, lifecycle verify exit 13 and receipt absent. |
| `test_audit_detects_identity_leak_in_redacted_or_drafts` | Put the synthetic roster name in a redacted file; report contains the relative path but not the name value. |
| `test_audit_reports_trace_unverified_when_absent` | No trace file yields `trace.status == "unverified"`. |
| `test_audit_fails_a_traced_private_read` | Trace a read of `.grading/maps/essay-2.json`; report yields failed and names only the prohibited relative path. |
| `test_audit_passes_phase_allowed_reads` | Trace skill/rubric/profile and redacted source in their allowed phases; report yields passed. |
| `test_audit_refuses_workspace_escape_symlink` | Symlink one observed artifact outside the trial; audit raises `ConformanceError` before reading it. |

The audit may read the synthetic roster as a trusted local checker. It checks
only existence/mode for map and review and never embeds their content.

- [ ] **Step 4: Write a fake executable adapter and RED run test**

The test adapter reads the phase prompt from its final argv item, writes only
the phase artifacts requested by the test, and exits with a configured literal
code. Assert the harness:

- uses `shell=False` behavior (metacharacters stay literal);
- sets cwd to the trial workspace;
- stops after first failed phase;
- records timeout and elapsed time;
- stores logs mode 0600 and report JSON/Markdown;
- replaces prompt in stored argv with `sha256:<digest>`.

- [ ] **Step 5: Implement trial creation and phased runner**

Copy files named by `PACKAGE_FILES.txt` while refusing links and paths outside
the repo. Build one synthetic submission, one-row roster and current profile.
Generate three self-contained prompts that require `grade.py next` before and
after each stage. Run parsed argv with
`subprocess.run(argv, shell=False, cwd=trial, timeout=timeout_seconds)` and
`GRADER_CONFORMANCE_TRACE` set to the phase trace path.
Use `final/agent-e2e` for the trial publication in prompts, next argv and audit.

- [ ] **Step 6: Add CLI subprocess tests and thin entry point**

Test:

These commands prove the deterministic harness interface. They are illustrative
agent invocations, not live runtime-support evidence.

```bash
python3 scripts/agent_conformance.py run --runtime fake \
  --command-json '["python3", "fake_agent.py", "{prompt}"]' --timeout 5
python3 scripts/agent_conformance.py audit --runtime manual \
  --workspace /tmp/humanize-grading-conformance-manual
```

Both commands print report paths and a concise PASS/FAIL/UNVERIFIED summary.
Malformed input exits 13 without traceback; a failed trial exits 12.

- [ ] **Step 7: Write the agent conformance reference**

Document callable JSON-argv adapters, Bionic audit mode, trace schema, read
evidence limits, synthetic-only requirement, report interpretation and cleanup.

- [ ] **Step 8: Run focused and full tests**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/grading/test_conformance.py -v
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
```

- [ ] **Step 9: Commit Task 4**

```bash
git add scripts/grading/conformance.py scripts/agent_conformance.py tests/grading/test_conformance.py references/agent-conformance.md
git commit -m "feat: add local agent conformance trials"
```

---

### Task 5: Update Agent Instructions, Domain Docs, and Version

**Files:**
- Create: `docs/adr/0003-agent-workflow-navigation-and-conformance.md`
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `docs/using-the-grader.md`
- Modify: `references/rubric-format.md`
- Modify: `CONTEXT.md`
- Modify: `scripts/validate-package.py`
- Modify: `skills/humanizer/SKILL.md`
- Modify: `.claude-plugin/plugin.json`
- Modify: `PACKAGE_FILES.txt`

**Interfaces:**
- Consumes: all runtime interfaces from Tasks 1–4.
- Produces: portable agent sequence and package v3.4.0 for live Task 6.

- [ ] **Step 1: Add RED package requirements**

Require `references/agent-conformance.md` and exact skill text:

```text
python3 scripts/grade.py next
python3 scripts/grade.py record prepare
```

Run validator and observe failure before docs/version updates.

- [ ] **Step 2: Update domain and ADR**

Add Draft pair, Next action and Conformance trial exactly as specified. ADR
0003 records executable scaffolding, durable reviewed state, JSON argv and
unverified missing traces, with rejected prompt-only and artifact-equals-read
proof alternatives.

- [ ] **Step 3: Rewrite the agent sequence around `next`**

Keep gate and briefing first. After approval, instruct the agent to run
`grade.py next --json`, execute only the returned action, and repeat. For
`prepare-record`, require editing the generated pair. Pair every non-zero exit
with a fresh `next` call; verification failure remains repair-only.

Keep full record reference in `references/rubric-format.md`; keep conformance
details in its new reference. `SKILL.md` holds only context pointers and steps.

- [ ] **Step 4: Update README and using guide**

Add commands, JSON example, manifest migration, conformance examples and a
plain statement that conformance without read trace is unverified. Do not claim
Hermes, Unsloth or Bionic support before live retest.

- [ ] **Step 5: Set version and package inventory**

Set 3.4.0 in both skills, plugin and first README history entry. Add all new
tracked files to sorted `PACKAGE_FILES.txt`.

- [ ] **Step 6: Run package and full checks**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python scripts/validate-package.py
wc -l SKILL.md skills/humanizer/SKILL.md
git diff --check
npx skills add . --list
npx skills add . --full-depth --list
claude plugin validate .
```

- [ ] **Step 7: Commit Task 5**

```bash
git add SKILL.md README.md docs/ references/ CONTEXT.md scripts/validate-package.py skills/humanizer/SKILL.md .claude-plugin/plugin.json PACKAGE_FILES.txt
git commit -m "docs: guide local agents through grading"
```

---

### Task 6: Run Bionic/Gemma Live Conformance Trial

**Files:**
- Create ignored local report: `.grading/evaluations/2026-08-26-bionic-gemma-3.4.md`
- Modify tracked compatibility wording only if live evidence contradicts Task 5.

**Interfaces:**
- Consumes: Bionic 1.0.9, local Gemma 4 E4B, `grade.py next`, draft-pair preparation and conformance audit.
- Produces: live evidence; it does not change production code unless a reproducible repository defect is found.

- [ ] **Step 1: Create a new disposable Bionic project**

Use a fresh package copy and synthetic one-submission fixture. Do not reuse the
3.3.0 session or workspace.

- [ ] **Step 2: Run the gated workflow**

Require Bionic to run `next --json` before every action. Supply the instructor
approval phrase only after inspecting the draft brief. Confirm synthetic private
review without exposing the review or map. Allow one repair turn for each
verification failure.

- [ ] **Step 3: Audit with the conformance CLI**

```bash
python3 scripts/agent_conformance.py audit \
  --runtime bionic-gemma-4-e4b \
  --workspace /tmp/humanize-grading-bionic-3.4-e2e
```

Record trace status as unverified unless a machine-readable Bionic trace was
captured. Inspect only redacted and draft content for leaks; do not open map,
review or final output.

- [ ] **Step 4: Apply evidence wording**

If the full lifecycle passes, document the exact tested Bionic/LM Studio and
model versions without generalizing to other models. If it fails, document the
stage and confirm whether lifecycle gates prevented publication. Never write
“fully works” without a complete receipt-gated publication.

- [ ] **Step 5: Commit evidence wording only when tracked docs changed**

```bash
git add README.md docs/using-the-grader.md
git commit -m "docs: record local agent conformance result"
```

No empty commit is created when only the ignored report changes.

---

### Task 7: Whole-Branch Audit and Release Readiness

**Files:**
- Modify only files required by audit or review findings.

**Interfaces:**
- Consumes: complete branch and live conformance evidence.
- Produces: fresh evidence for independent whole-branch review.

- [ ] **Step 1: Audit every construction and schema relationship**

```bash
rg -n 'redaction-batch/2|reviewed|grading-next-action/1|grading-agent-conformance/1|replace-with-feedback' scripts tests SKILL.md README.md docs references CONTEXT.md
rg -n 'RedactionManifest\.create\(' scripts tests
rg -n 'version: "3.4.0"|\*\*3.4.0\*\*|"version": "3.4.0"' SKILL.md skills/humanizer/SKILL.md README.md .claude-plugin/plugin.json
rg -n 'TokenMap|\.grading/maps|reviews/' scripts/grading
```

Confirm every manifest creation passes reviewed state and no grading module
loads private identity data.

- [ ] **Step 2: Run a deterministic CLI smoke trial**

In a disposable synthetic workspace, run brief prepare/approve/check, redaction
before and after review, `next`, record prepare, deterministic record/draft
completion, verify, next, consistency and rehydration. Confirm every next action
changes in the specified order and final output remains unopened.

- [ ] **Step 3: Run all fresh release checks**

```bash
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python -m pytest tests/
/Users/laccd/code/humanize_grading/.worktrees/.venv-assignment-briefing/bin/python scripts/validate-package.py
npx skills add . --list
npx skills add . --full-depth --list
claude plugin validate .
git diff --check
git status --short --branch
```

- [ ] **Step 4: Commit only a real audit fix**

```bash
git add -u
git commit -m "fix: close local agent compatibility audit gaps"
```

Create no empty commit when the audit changes nothing.
