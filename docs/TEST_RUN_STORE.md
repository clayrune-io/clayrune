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

An atomic JSON envelope contains schema 1, immutable enrollment, canonical
accepted events and the derived snapshot. `write_json_atomic` flushes/fsyncs
before replacement. Events and snapshot commit together; a failed replacement
preserves the prior record and allows the same event to be retried. There is no
separate append log whose torn tail could disagree with the snapshot. Stranded
temporary files are ignored. A corrupt envelope or snapshot/replay mismatch
raises `StoreError`, without replacing it or manufacturing an empty/passed run.

`get`, `events`, `apply`, `finish` and `list_runs` require project identity.
Cross-project lookups raise `KeyError` just like a missing run. Returned values
are copies. `list_runs` sorts newest first, filters by optional owner session,
and accepts offset plus a limit of 1-100 (default 100). Null owner is a valid
project-level run. A shared process-local reentrant lock prevents lost updates
across threads and store instances; this is not a multi-server lock.

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

Snapshots retain the latest 100 failure-phase details and 100 diagnostics with
explicit totals/omission counts. Canonical events retain full accepted history;
there is no automatic pruning policy or deletion path. Failure totals count
failing phases, whereas outcome counters count completed logical units.
`events()` is an internal full-history accessor, not a bounded public route.

Every access verifies replay; every accepted update rewrites one envelope.
Replay is linear in retained events plus phase details. Whole-record I/O grows
with history; batching/compaction and high-volume measurements remain an
integration consideration before enabling capture for large suites. No
throughput claim is established by the focused correctness tests.

Authorization, producer receipts, timestamp provenance, owner attribution,
reporter adaptation and process evidence verification belong to later phases.
This module trusts its server caller; it is not an ingestion security boundary.
It never launches, adopts, signals or kills a process. It does not define any
sandbox/incognito policy or offer an ephemeral store; Q4 remains deferred.

Validation:

```text
python -m pytest tests/test_test_run_store.py tests/test_atomic_json.py
pyright mc/test_run_store.py
```

The focused tests exercise torn replacement/corruption, duplicate/reordered
events, retries and phase accounting, all terminal states, changed totals,
project/session boundaries, concurrent store instances and bounded details.
Existing atomic-writer regressions cover its shared fsync/replace behavior.
No user-visible surface changes, so no user guide or user-facing changelog
entry is appropriate at this dark-backend phase.
