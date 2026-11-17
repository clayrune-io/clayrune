# MC-1148 Phase 1: test-run store contract

Status: standalone backend implementation, with no callers wired. This phase
does not deliver live progress or prove capture, launcher, UI, restricted-mode,
or process-restart coverage. Parent design: `TEST_RUN_PROGRESS_DESIGN.md`,
sections 5, 7 and 8. The remaining build phases and gates still apply.

## Storage and ownership

`mc/test_run_store.py` exposes `TestRunStore(data_root)`; pass
`Path(DATA_DIR).parent`, **not** the project-records directory. Records live at
`data_root/test_runs/<sha256-project-id>/<opaque-run-id>.json`. UUID4 run IDs
identify individual launches, including repeated identical commands. The
constructor rejects the project-records directory and its descendants. Project
identities are hashed rather than interpolated into paths; resolved run paths
must remain within the telemetry root.

`create(project_id, observed_at=..., ...)` accepts only named enrollment fields:
owner session/turn, job, same-project parent run, source, runner, unit, cwd,
target ID, already-redacted display label and process creation identity. These
are trusted server metadata, immutable after creation. There are no command,
environment or arbitrary stdout fields. Text identifiers must already be safe
for telemetry; this module cannot identify a secret hidden in a test name.

A JSON envelope contains schema 1, immutable enrollment, canonical accepted
events and the derived snapshot. `create` writes it atomically (`write_json_atomic`
flushes/fsyncs before replacement). While a run is open (MC-1179 SF30) the
envelope is not touched again: each accepted event batch is appended to a
sibling `<run-id>.log`, one canonical JSON event per line (`mc/test_run_log.py`),
with one `write` and one `fsync` of just that batch's bytes. When the run ends
the tail is folded into the envelope in one atomic write and removed, so a
finished run is the single `.json` file it always was. A record written before
this change (events and snapshot in the envelope, no tail) loads exactly as
before and is never rewritten or converted; it just ages out (see Retention).

Crash model: a crash mid-append can leave an unterminated last line. Loading
drops exactly that line (it was never acknowledged) and the next append
truncates it away before writing, so nothing is ever glued to garbage; a complete
line that is not a JSON object is corruption and raises `StoreError`. A crash
between the compacted envelope landing and the tail being removed leaves both:
the envelope is terminal and wins. Stranded temporary files are ignored. A
corrupt envelope or snapshot/replay mismatch raises `StoreError`, without
replacing it or manufacturing an empty/passed run. A failed append drops the
run's in-memory state and the next call rebuilds it from the files, which hold
exactly the acknowledged events.

`get`, `events`, `apply`, `finish` and `list_runs` require project identity.
Cross-project lookups raise `KeyError` just like a missing run. Returned values
are copies. `list_runs` sorts newest first, filters by optional owner session,
and accepts offset plus a limit of 1-100 (default 100). Null owner is a valid
project-level run. Writes take one process-local reentrant lock **per run file**
(`mc/test_run_open.py` `RunLocks`, shared by store instances, dropped when idle),
held only across reducing the batch in memory, one append and, when the batch
ends the run, one compaction. Two runs never wait for each other. A run's
reducer state is kept in a bounded in-memory LRU (`OpenRegistry`, 16 runs) tagged
with the file fingerprint; a write applies only its own batch, and a run that was
evicted, restarted over or edited outside is rebuilt by one replay on its next
write. This is not a multi-server lock.

`all_snapshots(project_id)` returns the same newest-first defensive copies in
one directory pass, with no page cap. The Floor uses `active_snapshots` instead;
`list_runs` retains its public limit of 100 and its owner/offset semantics.
Enumeration and sorting run outside any lock. Validation/cache access
holds one run's lock at a time, allowing submits between records, including
on a cold cache. A list is a per-file observation, not a project-wide atomic
snapshot; a newly created run may appear on the next poll.
Files disappearing after enumeration/stat are skipped, including when they
vanish during a read. Other unreadable/corrupt files still raise `StoreError`.

`mc/test_run_snapshots.py` owns a process-local LRU shared by store instances.
Only validated snapshot copies are retained, keyed by the checked absolute
file path and `(st_mtime_ns, st_size)`. Unchanged files skip JSON reads and
canonical replay. New/changed files still use the full existing `_read`
validation; corruption still raises `StoreError`. Reads check the fingerprint
again after validation and do not cache a record replaced during that read.
Create/append invalidate before writing, including when a write fails. Event
reads and write validation continue to read/replay canonical events.

The cache holds at most 4096 entries and 32 MiB of recursively measured snapshot
objects (entry/path metadata is separately entry-bounded). Oversized snapshots
are returned after validation but not cached. Events are never cached; entries
and returns are defensive copies. Every request still enumerates/stat-checks
files and rebuilds owner/privacy projections. As with any metadata cache,
external edits preserving both nanosecond mtime and size cannot be detected;
normal store writes invalidate explicitly. This is neither a route cache nor
a project-record cache, and adds no on-disk index or retention policy.

Bulk history scans admit snapshots only into free cache space; a miss cannot
evict the previous scan's residents. Direct reads retain ordinary LRU eviction.
Thus a directory larger than either budget keeps its resident hits rather
than thrashing to zero hits every scan. Overflow still validates on each list;
the 100-result public page limit does not bound the underlying history scan.

`active_snapshots(project_id)` uses `mc/test_run_terminal.py`'s separate
process-local `TerminalIndex`. Each validated terminal path retains only its
`(mtime_ns, size)`, with no snapshot/event copy and no LRU/byte cap. Unchanged
terminal files cost enumeration/stat, with no validation, snapshot copying or
projection. Active files use the snapshot LRU and can evict history snapshots;
Floor does not admit terminal snapshots into that cache. Changed terminal
fingerprints invalidate markers and require canonical validation again.
Creates/appends invalidate both structures before persistence, even if a
fingerprint is reused. A small module-level guard protects both indexes.

Terminal markers grow linearly with observed terminal paths over the process
lifetime (one absolute path and two integers per path); this is an intentional
metadata budget independent of the bounded snapshot heap. Missing paths are forgotten on direct read;
paths absent from subsequent enumerations may retain a small marker until
restart. The in-memory caches are rebuilt after restart, but a terminal file
validated once is trusted across restarts by `mc/test_run_ledger.py`: a
`_validated.jsonl` per project folder records `(path, mtime_ns, size)` of each
validated terminal file, so the first Floor read after boot stats the files
instead of replaying them. It is a cache only: missing, truncated or stale, it
costs a re-validation and nothing else. Warm Floor validation/copying
and projection scale with active/changed files, but enumeration and stat still
scale with all files. If active snapshots themselves exceed the LRU bounds,
active validation may recur; terminal history still requires no replay.

## Events and reduction

`apply(project_id, run_id, event)` accepts this strict envelope:

```json
{
  "schema": 1,
  "run_id": "32-lowercase-hex-run-id",
  "producer_id": "reporter-instance-generation",
  "producer_seq": 1,
  "observed_at": 100.5,
  "type": "unit_report",
  "payload": {
    "unit_id": "tests/test_demo.py::test_case",
    "attempt": 1,
    "phase": "call",
    "outcome": "pass",
    "final": true,
    "revision": 1
  }
}
```

Timestamps are finite, nonnegative Unix seconds supplied by the observer;
replay never reads a clock. Producer sequence and attempt/revision numbers are
positive integers. Unknown fields/types, invalid values and events larger than
16 KiB of canonical UTF-8 JSON raise `ValueError` before persistence.

| Type | Payload |
| --- | --- |
| `run_started` | Optional `reporter_version`. Cannot reopen a terminal run. |
| `collection` | Required `selected_total` (nonnegative integer or null), `revision`; optional absolute `deselected`, `collection_errors`. |
| `unit_started` | Required `unit_id`, `attempt`. Higher attempt supersedes prior results for this logical unit. |
| `unit_report` | Required `unit_id`, `attempt`, `phase`, `outcome`, `final`, `revision`. |
| `diagnostic` | Required lowercase identifier `code`; optional `capture=complete/partial/lost`, default partial. No free-form error/stdout text. |
| `heartbeat` | Optional boolean `output` advances last-output time independently of last-completion time. |
| `run_finished` | Required terminal `state`; optional `exit_code`, `not_run`. Reporters supply information only. |

Identity is `(run_id, producer_id, producer_seq)`. Exact replays do nothing,
including after reopening the store. Conflicting duplicates raise `ValueError`.
Unseen lower producer sequences remain eligible: delayed workers must not lose
earlier distinct units. A producer restart/source generation must use a fresh
producer ID. The store assigns its own increasing snapshot `seq` to every
accepted distinct event, including semantically stale reports.

Logical unit identity is `unit_id`, independent of producer/worker; attempts are
explicit. Starting a higher attempt removes the previous attempt's contribution
until the new attempt completes. Delayed lower attempts cannot replace it.
Within an attempt, revisions are per phase; lower revisions are ignored and
conflicting reports at the same revision are rejected. Collection revisions
likewise reject contradictory equal revisions and ignore older revisions.

A report with `final=false` can expose a failure immediately but does not count
a completed logical unit. A final report closes that unit once. Failure/error
phases survive a passing call or final phase. Outcome precedence is error,
fail, xpass, xfail, skip, pass; adapters must map setup/teardown failures to
`error` and strict xpass to the runner's actual failing outcome. Collection
errors, deselected and explicitly not-run units are separate from completed
counts. Full phase/retry history remains in canonical events.

Selected totals may increase, decrease or become unknown. Each revision is
retained, and `total_recalculated` indicates a changed denominator after the
first declaration. Percentage is completed/selected total, even when it moves
backward; zero/unknown/inconsistent totals yield null. ETA remains null in
Phase 1; no timing estimator is claimed. Last-output, last-completion and
latest-observation timestamps are separate and never move backward.

## Process evidence and terminal invariants

Reporter `run_finished` only updates `reporter_state`. Even 100% completed and
a reporter's exit zero leave the run running until the trusted capture layer
calls `finish(project_id, run_id, state=..., observed_at=..., exit_code=...)`.
The reserved process producer cannot be submitted through `apply`.

Terminal states are `passed`, `failed`, `error`, `cancelled`, `timeout`,
`no_tests`, and `lost`. Passed/failed/no-tests require observed exit evidence;
passed requires exit zero. Known failures (including unfinished failing units
or collection errors) downgrade a requested pass to failed. Exit zero with
partial capture remains expressible; capture health and verdict are separate.
Lost also marks capture lost. `not_run` must be explicit rather than guessed.

Once terminal, the entire snapshot is frozen. Later starts/reports/totals are
ignored; repeated `finish` calls return the existing verdict, including when
their observation timestamps differ. A run retry requires a new run ID.
`replay(enrollment, events)` uses the same validation/reducer and deterministically
reconstructs snapshots without launching processes or validating PID adoption.

## Retention, trust and remaining work

`mc/test_run_retention.py` deletes a run once it reached a terminal state more
than `test_run_retention_days` ago (default 14; `0` keeps everything; no size
bound). An active run is never deleted by age: the orphan sweep ends a dead one
and it then ages out like any other. The sweep runs once at startup off the boot
path, then at most once a day, and logs one line with the runs and bytes removed.
Files modified inside the window are not opened. For an older candidate the store
rechecks under that run's lock that it is terminal, old and unchanged, then
removes the tail and the record. A file that cannot be read or removed is logged
and the sweep continues. Nothing else is deleted, and no file is rewritten.

Snapshots retain the latest 100 failure-phase details and 100 diagnostics with
explicit totals/omission counts. Canonical events retain full accepted history
for the life of the run. Failure totals count failing phases, whereas outcome
counters count completed logical units. `events()` is an internal full-history
accessor, not a bounded public route.

Cache misses and event reads verify replay. An accepted update on an open run
costs the batch's own bytes, not the history; replay happens only when a run is
loaded from disk. Phase 2's `TEST_RUN_CAPTURE.md` bounds log reads and coalesces
output heartbeats before using this API. Structured reporter ingestion/batch
persistence is still a later concern.

Authorization, producer receipts, timestamp provenance, owner attribution,
reporter adaptation and process evidence verification belong to later phases.
This module trusts its server caller; it is not an ingestion security boundary.
It never launches, adopts, signals or kills a process. It does not define any
sandbox/incognito policy or offer an ephemeral store; Q4 remains deferred.

Validation:

```text
python -m pytest tests/test_test_run_store.py tests/test_test_run_append_log.py tests/test_test_run_retention.py tests/test_atomic_json.py
pyright mc/test_run_store.py mc/test_run_log.py mc/test_run_open.py mc/test_run_ledger.py mc/test_run_retention.py
```

The focused tests exercise torn replacement/corruption, duplicate/reordered
events, retries and phase accounting, all terminal states, changed totals,
project/session boundaries, concurrent store instances and bounded details.
Existing atomic-writer regressions cover its shared fsync/replace behavior.
The only user-visible surface is the `test_run_retention_days` setting
(Settings > Working defaults); see the CHANGELOG entry for MC-1179 / SF30.
