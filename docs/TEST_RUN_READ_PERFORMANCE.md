# Test-run read performance: review round 3 (2026-11-09)

Continue `clayrune/agent/3ddb2652e060` from `1ea22a71`. Local candidate only:
no merge, push, restart, configuration change, run deletion or pruning. The
round-2 report below is historical; its 4.47s warm list is superseded here.

## List metadata and page materialization

`mc/test_run_listing.py` owns a separate process-local `ListIndex`. Each
absolute path retains only `(mtime_ns, size)`, `created_at`, `run_id` and
`owner_session_id`, independently of the 4096-entry/32 MiB snapshot LRU. There
is no entry/byte cap on list metadata, like the accepted terminal index. Heap
grows with observed paths; a path omitted by a later directory enumeration can
remain in memory until restart. No run file is removed.

`list_runs` enumerates/stats all files, validates missing/stale metadata by
reading and replaying that file, filters/sorts metadata, then calls `_snapshot`
only for the returned page. Off-page metadata refresh neither defensively
copies snapshots nor admits them into the LRU. A cold index therefore still
validates the entire history; page records are read again for their actual
snapshots. Warm unchanged history costs enumeration/stat and metadata sorting,
plus at most the page's snapshot reads/copies, regardless of the LRU cap.
Snapshots too large for the byte budget can still validate on every page read.

Validated direct/Floor snapshot reads also populate list metadata. Successful
fingerprint rechecks are required before either kind of metadata admission.
Create/append invalidate the list index before persistence, including failed
writes and reused fingerprints. Disappearing files are skipped, filling the
page from remaining candidates when a page file vanishes. Stat/read/JSON,
identity and replay errors still fail the list, even outside the requested
page or owner. An owner check at materialization also prevents a concurrent
external owner replacement from escaping the requested owner filter.

Ordering remains descending `(created_at, run_id)`, filtering precedes offset,
and the limit stays 1..100. Responses are defensive copies. As before, reads
are per-file views rather than project-wide transactions; external changes
between metadata capture and materialization can be reflected on a subsequent
poll. Same-size external edits preserving nanosecond mtime remain outside
fingerprint detection. Enumeration/filtering/sorting hold no whole-history
lock; metadata validation and page reads each lock one file at a time.

## Round-3 measurement protocol

Reused the preserved round-2 all-project copy: measured project **5100 files,
123704974 bytes** (1841 real + 3259 synthetic); the other copied project is
preserved. All 5100 source fingerprints are asserted unchanged after each
benchmark. Creates/appends target separate scratch probe projects only.
The largest non-null owner has **90 runs**: the owner-filter measurement keeps
`limit=100` and correctly returns 90; the unfiltered request returns 100.
Offset is zero. No synthetic ownership was added to manufacture a full page.

All final benchmark and pytest commands use the installation's reported launcher with the project
venv interpreter explicitly named as the child. Bare child `python` resolves
to system Python on this host even when the launcher uses the venv; the
preliminary run is retained separately and is not the final evidence. An
initial preparation assertion that every measured owner had 100 rows was
corrected before collecting owner timings. No data or request limit changed.
Cold means all process-local indexes empty, not a flushed OS cache. Each list
variant resets independently; Floor resets independently; warm reads repeat
after the corresponding cold read. Timings use `time.perf_counter` under
ordinary concurrent host load, not a controlled hardware benchmark.

Final results (`final/results.json`), seconds:

| Operation | Cold | Warm | Canonical validations |
| --- | ---: | ---: | --- |
| List, limit 100, no owner | **8.461255** | **0.097032 / 0.080521 / 0.090789** | 5200 cold; 0 each warm |
| List, limit 100, owner with 90 matches | **7.757325** | **0.085576 / 0.079420 / 0.086069** | 5190 cold; 0 each warm |
| Floor storage/projection | **8.503794** | **0.165191 / 0.162149 / 0.158096 / 0.176526 / 0.162866** | 5100 cold; 0 each warm |

The extra 100/90 cold list validations materialize the actual page after the
5100 metadata validations; no off-page snapshots enter the LRU. Warm list
medians are **0.090789s** unfiltered and **0.085576s** owner-filtered, well below
0.5s. Floor's warm median is **0.162866s** with 368 active cached snapshots.
To check the small difference from the historical 0.159355s, a separate
comparison primed the exact round-2 source and candidate independently on the
same corpus, then alternated their poll order for ten warm reads each:

| Paired Floor control | Median | Range | Warm validations |
| --- | ---: | ---: | ---: |
| `1ea22a71` round 2 | **0.170485** | 0.156760..0.179146 | 0 |
| Round-3 candidate | **0.166967** | 0.154997..0.180528 | 0 |

This shows no material Floor regression under the same current host load;
the earlier 0.159355s is a historical individual observation, not a fixed
hardware floor. Both comparisons retain all 5100 corpus fingerprints.

Maximum submit during a **cold list** was **0.403549s** across **230**
create/append samples during an **11.043146s** scan. Its lock wait was
**0.399420s**, overlapping one scan critical section; the submit's atomic
write took **0.003401s**. The probe shares the process-global lock but writes
only its own scratch project, so this is local small-record store latency,
not HTTP latency or a large-history append benchmark.

Cold list indexing remains linear and the unfiltered observation exceeds
the frontend's 8s abort budget. This candidate fixes recurring warm polling;
it does not guarantee a first cold request completes before that timeout.
There is no polling/abort/configuration change. All 5100 corpus fingerprints
remained unchanged.

## Round-3 verification

Final complete selection: **832 passed, 3 skipped in 300.56s**, rc=0, on the
explicit project interpreter. This includes all `test_test_run_*.py` suites,
the **25 new listing checks**, pytest progress, Floor routes/subagents and
atomic JSON. The three existing platform/shell skips remain; no test was
removed or selection changed to avoid a failure. Runtime code was unchanged
after this final run began.

An earlier full run had 828 passes, 3 skips and one new test failure: it
incorrectly required one canonical read when a changed record moved onto the
page. Metadata validation and page materialization can each read that record.
The corrected assertion verifies that only the changed record is reread;
the focused selection then passed 65 checks, before adding three malformed
enrollment cases. The final complete run above supersedes those results.

Exact child selection from the worktree:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- <project-python> -m pytest tests/test_pytest_progress.py tests/test_test_run_anchor.py tests/test_test_run_capture.py tests/test_test_run_detection.py tests/test_test_run_finalize.py tests/test_test_run_floor.py tests/test_test_run_ingest.py tests/test_test_run_launcher.py tests/test_test_run_routes.py tests/test_test_run_store.py tests/test_test_run_snapshot_cache.py tests/test_test_run_active_scan.py tests/test_test_run_listing.py tests/test_test_run_transport.py tests/test_test_run_pytest.py tests/test_test_run_node.py tests/test_floor_routes.py tests/test_floor_subagents.py tests/test_atomic_json.py -o addopts= -q
```

```text
pyright mc/test_run_listing.py mc/test_run_store.py
```

Both touched modules pass basic type checking: **0 errors, 0 warnings,
0 informations**, rc=0 (only a non-failing tool-version notice).
The unchanged live server's launcher reporting emitted transport-unavailable,
queue-full, event-flush, capture-diagnostic and unfinalized-run warnings.
These are capture gaps: local pytest child exit codes/counts are verified,
but live telemetry finalization/recovery is not.

## Why round 2 saw 0.34s submits after round 1 reported 8ms

The cause reproduced by instrumenting the **exact `1ea22a71` store source** is
one large canonical replay holding the shared store lock. The file contains
**9586702 bytes / 24820 events** and is present in both preserved corpora.
In three instrumented round-2 reruns on the project interpreter, maximum
submits were **0.433621 / 0.462925 / 0.455286s**. Their lock waits were
**0.431642 / 0.452473 / 0.448386s**, each overlapping **one** scan critical
section reading that same file. Its canonical read/replay took
**0.491472 / 0.503462 / 0.437587s**; corresponding submit atomic writes took
**0.001343 / 0.009884 / 0.001473s**. This isolates lock contention behind that
record, rather than attributing the outlier to write latency or LRU overflow.

Round 1's `submit_probe.py` did only five immediate create/append pairs at the
start, then waited for the 5.27s scan. The ten saved operation latencies sum to
approximately **0.052593s**. Round 2 sampled through the entire scan (232
operations, 50ms between pairs). Thus round 1's 8.285ms maximum was a sparse
early sample, not a bound on all cold validation. The historical 0.341170s
sample itself lacks a per-record trace, so its exact blocking record cannot be
retroactively proved; the same code/data reproduce this measured mechanism.

The sampling gap was also reproduced with the **exact `feef8bd7` round-1
store source and its original 1821-file copy**, without replacing either
dataset or implementation. Five immediate pairs ended at **0.151047s**; the
large record's validation did not start until **3.692795s** (ending at
4.145947s). Their maximum was 0.030622s and none overlapped that record.
Sampling throughout a second scan produced **196 operations**, including a
**0.485315s** submit overlapping the large read, with **0.458872s** spent
waiting for the store lock. This directly demonstrates what the early-only
probe misses. That second run's overall maximum was **0.661017s**, outside
the large-read interval and with only **0.001018s** lock wait; that separate
outside-lock outlier is not attributed to canonical replay. Raw traces retain
all operations, including that outlier. These controls ran under ordinary
host load, concurrently with suite work; their absolute durations are not
presented as isolated performance comparisons.

The list optimization leaves cold one-file lock duration unchanged. Moving
canonical replay outside the shared lock is a separate consistency change;
this candidate does not claim cold-submit latency recovery.

## Round-3 evidence and scope

Gitignored evidence under `_scratch/test-run-read-amplification-r3/`:
`measure.py`, `final/results.json`, `final/list-trace.json`,
`project-python/results.json`, `project-python/*-trace.json`,
`compare-floor.py`, `paired-floor-results.json`,
`round1-sampling.py`, `round1-sampling-results.json` and its traces. The initial
system-Python measurements/traces remain at the scratch directory root.
The global plan write was refused by the unattended `.claude` fence;
`plan.md` there is the recovery record, without a fence/permission change.
The fence also refused a server-job submission whose PowerShell command was
stored in a variable. The full selection was instead rerun as the same literal
foreground launcher command (the preceding run took 288.55s); no job, permission
change or alternative test selection was substituted.

The new listing regressions compare cold and warm responses to the old full
read on a mixed active/terminal fixture with equal timestamps, multiple
owners, another project, empty filters, offsets and page boundaries. They
also assert page-only `_snapshot` calls above the cache cap and with caching
disabled, page-only cold LRU admission, missing/stale metadata rereads,
off-page corruption/read/stat errors, vanished files at metadata/page reads,
defensive copies, data-root separation, failed-write/reused-fingerprint
invalidation, replacement-during-validation and concurrent owner replacement.
Malformed enrollment sort/owner fields also raise `StoreError`; they cannot
be mistaken for vanished files by the listing's `KeyError` handler.

No UI, route response, configuration, build artifact, dependency or agent
convention changed; frontend smokes, README, user guide and agent-rule edits
are inapplicable. HTTP latency, live telemetry recovery and deployment remain
unverified. The accepted Floor algorithm is unchanged.

---

# Historical report: review round 2 (2026-11-09)

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
