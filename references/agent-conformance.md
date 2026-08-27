# Local grading-agent conformance

The conformance harness runs or audits one synthetic grading lifecycle. It
checks the artifacts and lifecycle gates separately from optional evidence
about which files the agent read.

It does not prove grading quality. It does not accept real student work.

Every invocation below is illustrative until Task 6 records live evidence for
an exact runtime and model combination. The harness itself is not conformance
evidence.

## Callable adapters

Use `run` when the agent has a command-line adapter that accepts a prompt:

```bash
python3 scripts/agent_conformance.py run \
  --runtime hermes-granite \
  --command-json '["hermes", "--safe-mode", "-z", "{prompt}"]' \
  --timeout 300
```

Callable-agent `run` currently requires POSIX process groups. It fails before
creating or launching a trial on other platforms because the Python standard
library cannot guarantee descendant cleanup there after a parent exits.
Artifact-only `audit` remains portable.

`--command-json` must be a JSON array of command arguments, not shell text. It
must contain `{prompt}` exactly once. `{workspace}` is optional. The harness
replaces only those two placeholders and launches the result with
`shell=False` from the trial workspace.

The command is limited to 64 arguments. Each argument is limited to 4,096
characters before and after placeholder replacement. Shell operators such as
`;`, `|`, `$()`, and redirection characters remain literal arguments.

The adapter receives three separate prompts: `briefing`, `redaction`, and
`grading`. Each prompt requires `grade.py next --json` before and after the
phase. The harness stops after the first process or checkpoint failure. Every
adapter runs in its own process group. The harness stops and reaps that group,
including inherited descendants, before it audits the workspace.

## Bionic audit mode

Bionic 1.0.9 does not provide a stable callable adapter for this harness. Use
an existing synthetic trial workspace and audit the artifacts after the Bionic
session:

```bash
python3 scripts/agent_conformance.py audit \
  --runtime bionic-gemma \
  --workspace /tmp/humanize-grading-bionic-e2e \
  --package-root /path/to/trusted/humanize-grading
```

To create the reviewed package and synthetic input without launching an
agent, run this from the repository root:

```bash
python3 -c 'from pathlib import Path; from scripts.grading.conformance import create_trial; print(create_trial(Path.cwd(), Path("/tmp/humanize-grading-bionic-e2e")))'
```

Open only that workspace in Bionic. The trial marker, one-row roster, raw
submission, and token map are synthetic. Do not replace them with course data
or a real roster.

## Optional read trace

A runtime may write JSON Lines to the path in `GRADER_CONFORMANCE_TRACE` during
`run`. For `audit`, pass one or more trace files explicitly:

```bash
python3 scripts/agent_conformance.py audit \
  --runtime manual \
  --workspace /tmp/humanize-grading-conformance-manual \
  --trace .grading/conformance/traces/manual.jsonl
```

Each line is one `grading-agent-trace/1` event:

```json
{"schema":"grading-agent-trace/1","event":"read","phase":"grading","path":"redacted/STUDENT_01.md"}
{"schema":"grading-agent-trace/1","event":"command","argv":["python3","scripts/grade.py","next"],"exit_code":0}
```

`phase` is `briefing`, `redaction`, or `grading`; omitted read phases default
to `grading`. The trace is limited to 10,000 events and 1 MiB per line. Command
events use the same argument count and item limits as adapters.

A passing phase trace needs successful `grade.py next --json` command events
before and after at least one successful non-next command. The grade script
argument must be immediately followed by `next`, and the argv must contain
`--json`. The first command must be the opening `next`; the last command must
be the closing `next`. Reads do not count as phase work. Read-only, partial,
missing, loosely matched, or out-of-order command evidence is `UNVERIFIED`.
A structurally recognized `next` that omits `--json` or exits nonzero is never
reclassified as a non-next work command. The 10,000-event limit is global
across every supplied phase file, not per file.

Read permissions are phase-specific. The grading phase may read the skill,
references, scripts, rubric, current profile, approved brief, redacted source,
and token-named draft pair. A traced agent read of any raw submission, roster,
private review, token map, or final identity-bearing publication fails the
trial. The trusted local checker may read the synthetic roster to scan for
identity leaks. It records only existence and mode for the token map and review
file; it never embeds their content.

No trace, an empty trace, or a trace with commands but no read event is
`UNVERIFIED`. Artifact success is never treated as proof of safe reads.

## Evidence and report limits

The harness copies only files named by `PACKAGE_FILES.txt`. It refuses package
links, paths outside the repository, and observed workspace links. Agent
stdout and stderr are each limited to 2 MiB. Stored logs, traces created during
`run`, and JSON and Markdown reports use owner-only permissions.

Package copying and later checks bind each reviewed file to its descriptor,
path, exact bytes, mode, and SHA-256. The report records the complete package
manifest, one digest for that byte set, and whether the source checkout was
dirty. Package files are rechecked before and after every phase. Any changed,
missing, linked, or unexpected package path fails before lifecycle code from
the trial is run.

Manual audit never trusts the package manifest stored inside the workspace as
its baseline. It rebuilds the expected descriptor-bound manifest from
`--package-root`; when that option is omitted, the executing package root is
the default. The workspace marker is reported only as evidence. Run audit from
a separate trusted checkout, not from the workspace being audited.

The identity scan also fails closed. A linked, unreadable, oversized, invalid
UTF-8, or otherwise unbounded redacted or draft artifact becomes a sanitized
path finding and fails the deterministic audit. A phase checkpoint rejects
artifacts and relevant directories that belong to a later phase, including an
empty later-stage directory.

Reports use schema `grading-agent-conformance/1`. They contain sanitized
arguments, elapsed time, exit and timeout state, bounded log hashes, artifact
names/hashes/modes, lifecycle results, identity-leak paths, and trace status.
The phase prompt is stored only as `sha256:<digest>`. Identity-bearing roster
values and private map or review contents are not report fields.

Interpret the summary as follows:

- `PASS`: deterministic checks passed and the supplied read trace passed.
- `UNVERIFIED`: deterministic checks passed, but no read trace proved the
  agent's reads.
- `FAIL`: a process, artifact, lifecycle, privacy, or supplied trace check
  failed.

Malformed harness input exits 13 without a traceback. A completed failed trial
exits 12. `PASS` and `UNVERIFIED` exit 0 so callers can distinguish a failed
trial from missing optional read evidence.

## Cleanup

Inspect the owner-only reports under `.grading/conformance/reports/`, then
delete the exact synthetic workspace when it is no longer needed. Confirm the
full path before removing it:

```bash
rm -rf -- /tmp/humanize-grading-conformance-EXACT-ID
```

Never point the cleanup command at `/tmp`, the repository, a home directory,
or a path that was not created for this synthetic trial.
