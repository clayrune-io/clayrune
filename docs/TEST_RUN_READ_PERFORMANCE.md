# Test-run snapshot read amplification fix (2026-11-09)

Branch candidate only. No merge, push, server restart, configuration change,
run deletion or pruning. These measurements exercise the store and Floor
storage adapter against a scratch copy, not a deployed server modification.

## Cause and change

The old `list_runs` held the global store lock while globbing, loading and
replaying every record, then sliced its result. Floor's 100-record paging
called that full scan 19 times for 1821 records. Submits queued behind those
scans, exceeding the launcher's two-second telemetry request timeout.

`mc/test_run_snapshots.py` now owns a bounded LRU of validated snapshot copies;
`mc/test_run_store.py` checks each path's nanosecond mtime and size before reuse.
Changed/new records retain full canonical validation and corruption errors.
Create/append invalidate before persistence; failed writes cannot publish
uncommitted state. Event access and write validation still replay full records.
The cache retains no events and is capped at 4096 entries and 32 MiB of
recursively measured snapshot objects. Path/entry metadata is entry-bounded.

`all_snapshots` enumerates each project once and locks one record at a time.
Enumeration, sorting and Floor projections run outside the lock. Floor's
`_all_runs` now calls this once rather than paging. Public `list_runs` keeps
its limit of 1-100, offset, owner filtering and newest-first ordering. All
outputs remain copies. Current sessions and privacy rules are projected anew
on every request; there is no route or project-record cache.

This is a per-file view rather than a project-wide atomic snapshot. A new run
created during enumeration may appear on the next poll. Metadata-preserving
external edits (same nanosecond mtime and size) are outside cache detection;
normal store writes invalidate explicitly. A file replaced during validation
is not cached under the replacement's fingerprint.

## Copied-data timings

The benchmark copied the actual project directory into
`_scratch/test-run-read-amplification/data/test_runs/<project-hash>/` and kept
that copied project unchanged for both versions: **1821 JSON files,
118456875 bytes**. It loaded the unmodified source saved before editing for
the baseline and the working branch modules for the candidate, using the
project virtual environment for both. No store call targeted the live source
directory. Reported launcher telemetry remains the normal live reporting path.

Seconds, measured with `time.perf_counter` on the same copied files:

| Operation | Before | After, empty snapshot cache | After, warm reads |
| --- | ---: | ---: | ---: |
| `list_runs('mission_control')`, limit 100 | 4.728935 | 5.495832 | 0.454322 / 0.448178 |
| `read_projects([project], {})`, complete Floor storage/projection read | 108.246729 | 6.605972 | 0.436427 / 0.424888 |

"Empty cache" means a newly constructed snapshot cache, not a flushed OS file
cache. Times are individual measurements under ordinary concurrent host load,
not a controlled hardware benchmark. The first list read still validates every
record and pays cache-copy/accounting overhead; its measured cold time is
higher than the baseline. Repeated reads avoid replay. Floor's cold read also
benefits immediately from replacing 19 scans with one.

All 1821 validated snapshots fit in **9677127 bytes** of measured snapshot
objects, below both bounds. An additional cold-scan contention probe used the
same copied project and created/appended only to a separate scratch probe
project. Its scan took **5.270103s**; five creates and five appends completed
while it was scanning, with **8.285ms maximum** submit latency. The corresponding
regressions verify directory enumeration never holds the shared lock and that
validation releases it at every file boundary without relying on OS fairness.

Local scratch evidence (gitignored, not a runtime dependency): `benchmark.py`,
`before_store.py`, `before_floor.py`, `before.json`, `after.json`,
`submit_probe.py`, `submit.json`, and `plan.md` under the directory above.
The original source base is `1878fa4f`; the saved modules preserve its exact
store/Floor implementation for the comparison. The global plan path was
refused by the unattended `.claude` write fence; scratch holds the recovery
plan instead. No fence or permission change was attempted.

## Verification

All existing files under `tests/` containing `test_run_store`, `test_run_floor`
or `test_run_routes` were selected, plus the new cache regressions, Floor route
and subagent tests, and atomic JSON tests. The full run returned rc=0:
**722 passed, 3 skipped in 142.34s**. Skips were two POSIX signal/cancellation
tests on Windows and one unavailable-shell case.

Exact selection, run from the worktree through the installation's reported
launcher with its project Python as the child interpreter:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest tests/test_pytest_progress.py tests/test_test_run_anchor.py tests/test_test_run_capture.py tests/test_test_run_detection.py tests/test_test_run_finalize.py tests/test_test_run_floor.py tests/test_test_run_ingest.py tests/test_test_run_launcher.py tests/test_test_run_routes.py tests/test_test_run_store.py tests/test_test_run_snapshot_cache.py tests/test_floor_routes.py tests/test_floor_subagents.py tests/test_atomic_json.py
```

After adding final write-failure, stat-failure and create-invalidation cases,
the same launcher ran `tests/test_test_run_snapshot_cache.py`: **22 passed in
0.54s**, rc=0. No runtime implementation changed after the full suite.
Coverage includes warm reuse across instances, changed mtime alone, changed
size alone, external replacement during validation, malformed JSON and
snapshot mismatch after cache hits, new/deleted files, defensive copies,
project/root isolation, shared write invalidation, failed writes, stat errors,
LRU/byte bounds, single-pass Floor reads, and live owner/privacy reprojection.

Both benchmarks and the contention probe ran through that reported launcher
with the same explicit project interpreter. Earlier preliminary invocations
used bare `python` for the child, which Windows resolved to system Python;
the broader test run then failed collection for missing `psutil`. All final
numbers and suite results above replace those preliminary runs. No dependency
was installed and no test selection was dropped to work around that failure.

The required launcher emitted telemetry transport/enrollment/flush/capture
warnings against the unchanged running server on some invocations; it still
executed the requested children and preserved their exit codes. Thus these are
verified local test/benchmark results, not a claim that live progress reporting
has recovered. Integration and server restart remain the owner's next step.

```text
pyright mc/test_run_snapshots.py mc/test_run_store.py mc/test_run_floor.py
```

Basic type checking: **0 errors, 0 warnings, 0 informations**, rc=0 (only a
non-failing tool-version notice). No new dependencies, UI, build assets or
agent conduct changes: frontend smokes, README, user guide and agent-rule
updates are inapplicable. No retention policy is required by this fix.
