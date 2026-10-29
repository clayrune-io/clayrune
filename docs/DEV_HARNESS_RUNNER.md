# Frozen native-command runner (DH-M3)

DH-M4 extends this runner with the internal native recipe seam documented in
`DEV_HARNESS_CHECKS.md`: bounded memory-only stdout validation, source-bound
fresh coverage, released tool pins and declared distribution/config closure.
Ordinary M3 commands still discard both streams. The sections below describe
the original M3 contract; M4 does not add merge/activation authority.

MC-960 / `d813ea78`, spec revision 3. This is a backend foundation for
DH-M4 recipes and DH-M5 integration, not a managed-merge gate or a complete
practice assessment. Existing activation/passcode/review gates are unchanged.

## Ownership

- `mc/dev_harness_diff.py`: full B/H/M/T identities, NUL-safe rename/mode
  records, content/index manifests and disposable candidate/preview checkouts.
- `mc/dev_harness_runner.py`: recipe validation, existing managed-job execution,
  summary projection, internal runner receipts and the report-only CLI function.
- `tools/dev-harness-check.py`: import/exit wrapper only.
- Two narrow existing-owner additions: `agent_worktree.create(isolated=True)`
  skips shared runtime links/MCP rewriting/source ignore edits;
  `agent_jobs.start_job(direct_argv=...)` bypasses shell resolution and directs
  stdout AND stderr to DEVNULL. HTTP job requests cannot select `direct_argv`.
  Normal agent jobs/checkouts retain their existing behavior.

No replacement worktree manager, daemon, shell parser, remote provenance
adapter, scanner, installer or activation route is introduced.

## Frozen inputs

Integration resolves full target B, incoming H, unique merge base M and
conflict-free merged tree T using Git's merge-tree. Branch attribution is M..H;
integration changes are B..T. Conflicts, missing/ambiguous bases, dirty source
or dirty registered incoming checkouts fail. There is no old-base fallback.
Git objects for a synthetic local commit make T checkoutable without moving
target/incoming refs. Existing worktree create/remove owns the disposable
branch lifecycle; no shared runtime is linked. Only the runner's newly created
checkout/branch is removed, including on failed checks.

The existing lifecycle uses `.clayrune/agents/`. That directory must already
be Git-ignored before running commands. Missing exclusion reports Error; the
runner does not edit the source ignore file to manufacture clean input.

Dirty Git previews freeze the index plus all tracked working files and
untracked nonignored files, including staged/unstaged changes, deletions and
renames. Bytes are copied to a disposable HEAD checkout and checked against
the manifest before execution. Staged-only content remains identified in the
index; commands exercise the frozen working bytes. A passing preview never
authorizes a merge. Unborn and non-Git projects expose file manifests only;
command execution there reports Unsupported until a checkoutable input exists.
Non-Git manifests enumerate all files rather than pretending Git ignore rules
exist. A manifest without commands is also Unsupported, never an empty pass.

Paths use NUL-separated Git records and strict UTF-8. Escaping symlinks,
unmerged index entries, submodules and special files are Error. Internal
symlinks retain their link bytes. Binary/undecodable files have explicit binary
identity; changed binary source cannot produce a partial passing command run
without a later approved adapter. Limits: 20,000 files, 16 MiB/file, 256 MiB
total content, 32 MiB Git response, 64 steps and 4 MiB/config or recipe input.
The manifest stores hashes/kinds/modes, not source contents.

The checkout manifest, original checkout, target/incoming refs, executable
bytes and active snapshot are checked after execution. Source mutation, tool
mutation, ref advancement or cancellation produces Error even after exit 0.
This is observation of frozen content, not an OS sandbox or a lock against
hostile unrestricted filesystem changes between observations. M5 must recheck
under integration serialization immediately before landing.

## Native recipe seam and output boundary

An internal recipe is a JSON-compatible object with exactly these fields:

```json
{
  "id": "unit-python",
  "argv": ["python", "-m", "pytest", "tests/unit"],
  "timeout_seconds": 120,
  "fail_codes": [1],
  "configs": ["pyproject.toml"]
}
```

This is an illustrative project command, not an installed/default recipe.
Tools must already exist. Timeout is positive, finite, at most 7,200 seconds;
0 is the only success exit, explicitly declared failure exits produce Fail,
unknown exits/spawn failure/timeouts/cancellation produce Error. Empty or invalid
recipes cannot pass. Commands are argv-only; no command-string parsing occurs.
M4 owns recipe release/report compatibility, positive/finding/crash fixtures,
tool-specific config closure and semantic result validation. Interpreter,
argv, config and checker hashes are recorded; an executable hash alone does
not verify every package/plugin loaded by that executable.

The internal caller supplies process register/unregister callbacks. Each child
is registered immediately on spawn, before the existing job watcher runs;
registration refusal terminates that owned child and prevents a pass. Existing
job timeouts and completion callbacks are reused. Cancellation kills the owned
process tree and waits for completion. Windows uses the existing job-object/
tree-kill helpers; handles close after execution. POSIX starts a new session
and reuses the guarded process-tree kill on timeout/cancellation. POSIX behavior
and the Windows descendant-startup race inherited from the existing job-object
helper have not been independently exercised here.

**No verifier stdout/stderr is captured or written to disk.** Both streams go
to DEVNULL before execution. Job log files are empty; public job records and
callbacks contain only the fixed command label and process/exit/time metadata.
Receipts contain validated step IDs, argv/tool/config digests, duration, exit,
timeout/cancel flags and native-command-only coverage. Exceptions become a
closed error vocabulary, not raw strings/tracebacks. M3 accepts no scanner
report body and copies no scanner artifact. M4 must use source-redacted output
and approved report validation before persisting any tool reports; arbitrary
native commands are not sandboxed against writing their own files.

## Report-only CLI

```powershell
python tools/dev-harness-check.py --project <repo> --project-id <id>
python tools/dev-harness-check.py --project <repo> --steps <recipe-array.json>
python tools/dev-harness-check.py --project <repo> --target master --incoming <branch> --steps <recipe-array.json>
```

Every result has `kind=headless_report`, `trusted=false` and
`authorizes_merge=false`. JSON string fields escape control characters, angle
brackets, brackets and backticks so filenames cannot become chat protocol markers.
The CLI has no data-root, receipt-write, activation, trusted or managed flag.
It uses temporary empty managed-job logs and prints only the structured report.
It can run inside the existing registered session job for completion delivery;
running it in optional external CI does not authenticate CI provenance.

Exit 0 = completed named-command pass; 1 = findings/review required;
2 = unable to evaluate, Unsupported, Pending or any other incomplete status.
A pass does not prove whole-practice coverage, independent review, activation
eligibility, release readiness or permission to merge.

## Internal runner receipts

`run_managed(store, project, steps, target, incoming, register, unregister, cancel)`
is an internal coordinator seam for M5, not an HTTP/CLI body-submission path.
It reads the active snapshot itself, evaluates inputs, rechecks the snapshot,
then atomically writes a `runner_receipt` under
`data/dev_harness/<project>/runs/<run_id>.json`. Nothing is stored under
`data/projects/`. `read_receipt` checks identity, content integrity and current
active policy/catalogue/digest/revision bindings; missing, malformed, corrupt
or stale records raise Error. Store-write failure propagates rather than
returning a persisted-pass claim.

These receipts prove local execution provenance only. They explicitly retain
`authorizes_merge=false`; they are not `measured_preview` activation evidence
and never write M1's `evidence/` directory. No catalogue capability is upgraded.
M4 must validate supported native reports and fixtures; M5 must compose the
actual standards/review hold and invalidate receipts for changed inputs.
The unrestricted-local-filesystem trust boundary is the same as M1, not a
signature or protection against a local writer fabricating records.

## Verification

Focused tests use temporary repositories and synthetic commands only:

```powershell
python -m pytest tests/test_dev_harness_diff.py tests/test_dev_harness_runner.py tests/test_agent_jobs.py tests/test_agent_worktree.py
python -m pytest tests -k dev_harness
pyright mc/dev_harness_diff.py mc/dev_harness_runner.py mc/agent_jobs.py
git diff --check
```

No static assets or UI changed; frontend smokes and USER_GUIDE changes are not
applicable to this backend ticket. Real native scanners/tool report adapters,
live managed integration/activation, packaged builds and POSIX execution remain
unverified. No merge, push, server restart or tool/guidance installation.

Final Windows validation:

- `python -m pytest tests/test_dev_harness_diff.py tests/test_dev_harness_runner.py`:
  **60 passed, 1 skipped**, rc=0 (78.25s).
- `python -m pytest tests -k dev_harness`: **383 passed, 1 skipped,
  15980 deselected**, rc=0 (116.27s).
- `pyright mc/dev_harness_diff.py mc/dev_harness_runner.py mc/agent_jobs.py`:
  **0 errors, 0 warnings**, rc=0.
- `git diff --check` and `git diff --cached --check`: rc=0.
- Additional focused run including existing `test_agent_jobs.py` and
  `test_agent_worktree.py`: **105 passed, 1 skipped**, rc=0, before the final
  missing-source/Ctrl+C fixtures (covered by both final suites above).

The skip is the POSIX newline-filename fixture: Windows rejects that filename.
Synthetic argv fixtures exercised actual subprocess execution, dirty-preview
copying, timeout/Event cancellation/Ctrl+C, metadata/output suppression,
candidate/source/tool mutation, target advancement and receipt integrity.
Ctrl+C terminates/reaps the in-flight verifier and returns structured
`cancelled` Error rather than leaving its detached child running.

## DH-M5 A2a approval boundary

[Native approval](DEV_HARNESS_NATIVE_APPROVAL.md) adds nonexecuting immutable
plan proposals and a separate trusted activation-evidence composer. M3's
ordinary runner and report-only CLI are unchanged; neither grants activation
or merge authority. A2a composition tests use synthetic stored receipts only.
A2b must add approved Preview receipt production and M5 plan/checker metadata
before live native activation measurement is available. No landing gate or
native execution route ships in A2a.
