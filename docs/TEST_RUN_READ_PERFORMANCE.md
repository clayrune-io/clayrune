# Test-run read performance: review round 2 (2026-11-09)

Branch candidate on `clayrune/agent/3ddb2652e060`, following `feef8bd7`.
No merge, push, server restart, configuration change, run deletion or pruning.
These are local store/Floor adapter measurements on copied data, not deployed
HTTP endpoint measurements.

## Review findings and changes

The first candidate removed Floor's repeated 100-record paging scans and
released the store lock between records. Its 4096-entry / 32 MiB snapshot LRU
still thrashed when a sequential history scan exceeded either budget.

Bulk history reads now admit misses only into free cache space. A scan cannot
evict the previous scan's resident snapshots; direct `get` reads retain normal
LRU eviction. At 5100 files, repeated warm lists retain 4096 hits and validate
the 1004 overflow files, rather than validating all 5100 again. This preserves
public list/get ordering, filtering, pagination, defensive copies, validation
and corruption semantics; it does not make full-history lists constant-time.

`mc/test_run_terminal.py` separately owns fingerprint-only terminal markers.
The shared store lock protects one absolute path and `(mtime_ns, size)` per
observed terminal file. There is no snapshot/event copy, entry limit or byte
limit in this index. Its memory grows linearly with observed terminal paths;
missing paths are forgotten on direct read, while markers for paths absent
from later directory enumerations may remain until restart. No retention or
run deletion is introduced. Snapshot heap remains bounded by the existing LRU.

Floor calls `active_snapshots` once per project. Unchanged terminal files cost
only enumeration/stat. Terminal snapshots are not admitted into the Floor's
snapshot cache; active snapshots can evict history snapshots. Thus replay,
copying and projection concern active/changed files rather than terminal
history. Directory enumeration/stat still scale with all files. If active
snapshots themselves exceed the LRU budget, active validation can recur.

Terminal immutability was checked before this change: `_append` returns the
existing snapshot before appending events or writing once the state is terminal.
All seven terminal states now have a regression asserting late events and
repeated finish calls leave canonical event count, file bytes and mtime unchanged.
Reporter-only finish events remain active until trusted process finalization.

Changed fingerprints require full canonical validation before markers are
reused. Create/append invalidate both indexes before persistence, including
failed writes or reused fingerprints. Replacements during validation are not
cached under a different fingerprint. Same-size external edits that preserve
nanosecond mtime remain outside metadata detection. Owner/privacy projections
still use current sessions every poll, with no route/project-record cache.

`all_snapshots` now skips files disappearing between glob/stat/read. Permission,
JSON, identity and replay failures still raise `StoreError`; Floor reports the
room unavailable. Public single-run `get` still raises `KeyError` for missing
runs. Enumeration and sorting stay outside the lock; each file's validation
holds it independently.

## Copied-data benchmark

All **1909 real runs across two project directories** were copied into a new
scratch tree at measurement preparation time. Mission Control contributed
**1841 real files**. Added **3259 synthetic canonical terminal runs** to that
copied project, giving **5100 files / 123704974 bytes** for the measured project.
The other project's 68 real records were preserved. Live counts had increased
from review's 1903; no source run was removed or pruned. Only this copied
Mission Control project was projected for the measured Floor storage path.

The copy contains **4732 terminal files and 368 active snapshots**, reducing to
**334 active roots** after parent/privacy projection. No stale active run was
finalized for the benchmark. The Floor snapshot cache after warmup holds only
those 368 active snapshots, **1634333 bytes** of recursively measured objects;
the 4732 fingerprint markers are independent of that LRU.

Final measurements with `time.perf_counter`, project Python and the mandatory
reported launcher, using the same preserved copied corpus:

| Required operation | Seconds | Evidence |
| --- | ---: | --- |
| Floor cold | **7.240150** | 5100 canonical validations; both indexes initially empty |
| Floor warm | **0.159355** | zero canonical validations |
| `list_runs` warm, limit 100 | **4.470684** | separately primed list; 4096 hits / 1004 overflow validations |
| Maximum submit during cold Floor scan | **0.341170** | maximum of 232 create/append samples |

Two additional warm Floor reads took **0.163379 / 0.155065s**, both with zero
validations. A second warm list took **4.101292s**, again 1004 validations.
The history-list priming pass took 7.209921s and validated 4732 terminal files;
it is excluded from the warm-list number. Floor intentionally does not retain
terminal snapshots for later list calls.

The contention probe cleared both indexes and scanned the same 5100-file
project in **8.593957s**. It sampled creates/appends throughout that scan in a
separate scratch project sharing the exact same process-global store lock.
Reported submit latency is local store create/append wall time, including
lock wait and atomic write; it is not an HTTP or large-history append benchmark.
One very large record can still hold the lock for its own complete validation.

Cold means empty process-local snapshot/terminal indexes, not a flushed OS
file cache. These are individual observations under normal concurrent host
load. An earlier preparation run measured 26.446062s cold and
0.152168 / 0.161166 / 0.133008s warm on the same corpus. Its first list call
was priming, despite its preliminary label of warm; the final measurements
above explicitly separate priming from warm history reads. An earlier
single-project-only scratch copy was preliminary and is not the final dataset.

Evidence under `_scratch/test-run-read-amplification-r2/` (gitignored):
`benchmark.py` prepares the fresh all-project copy and synthetic terminal data;
`all-projects-data/` preserves that corpus; `measure-final.py` measures it
without recopying; `final-results.json` includes every submit sample. Preliminary
`data/`, `results.json` and `all-projects-results.json` remain intact. No
benchmark operation targets the live run store; the launcher's normal live
reporting remains a separate path.

## Verification

Final complete selection: **807 passed, 3 skipped in 195.05s**, rc=0. The
preceding run before the final active-cache refinement/additional regression
passed 806 with 3 skips; the final full run supersedes that runtime result.
Focused Floor/active-scan/cache checks passed **98 tests in 7.57s**, rc=0.
After correcting the Floor test spies to wrap `active_snapshots` and adding
an explicit assertion against swallowed read failures, both affected Floor
tests passed again: **2 passed in 1.88s**, rc=0. Runtime code was unchanged
after the final complete run started. Three platform/shell skips remain;
no selection was removed to work around a failure.
Selection includes every `test_test_run_*.py` suite, pytest progress, Floor
route/subagent and atomic JSON tests. Exact child selection:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest tests/test_pytest_progress.py tests/test_test_run_anchor.py tests/test_test_run_capture.py tests/test_test_run_detection.py tests/test_test_run_finalize.py tests/test_test_run_floor.py tests/test_test_run_ingest.py tests/test_test_run_launcher.py tests/test_test_run_routes.py tests/test_test_run_store.py tests/test_test_run_snapshot_cache.py tests/test_test_run_active_scan.py tests/test_test_run_transport.py tests/test_test_run_pytest.py tests/test_test_run_node.py tests/test_floor_routes.py tests/test_floor_subagents.py tests/test_atomic_json.py -o addopts= -q
```

New regressions cover repeated scans beyond entry and byte budgets, Floor
history beyond the snapshot cap, disabled snapshot caching, active/terminal
cache separation, late-event immutability, external terminal changes/corruption,
reused-fingerprint create invalidation, disappearance at stat/read and
unreadable-file errors. Existing cache/lock, ordering/pagination, owner/privacy,
launcher, transport and reporter regressions remain selected.

```text
pyright mc/test_run_terminal.py mc/test_run_snapshots.py mc/test_run_store.py mc/test_run_floor.py
```

Basic type checking: **0 errors, 0 warnings, 0 informations**, rc=0.
The installed pyright emits only a non-failing tool-version notice.
The launcher against the unchanged live server emitted HTTP 403 progress,
flush, capture and unfinalized-run warnings. Those are explicitly unverified
live telemetry integration, not a test assertion failure or proof of recovery.
Child exit codes are preserved.

The global plan write was refused by the unattended `.claude` write fence;
`_scratch/test-run-read-amplification-r2/plan.md` retains the recovery plan.
No fence/permission change was made. No new dependency, UI, build asset or
agent convention: frontend smokes, README, user guide and agent-rule edits
are inapplicable. Integration/restart and live HTTP behavior remain unverified.
