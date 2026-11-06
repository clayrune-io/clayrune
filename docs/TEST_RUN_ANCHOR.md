# MC-1148 Phase 14: server-inserted chat anchors

Branch candidate, default dark behind exact-true `test_run_progress_enabled`.
No merge, push, restart or config change is part of this phase.

## Contract

`mc/test_run_anchor.py` owns `post(store, project_id, run_id)`. It reads the
authoritative enrollment, resolves its owning live session, and appends the
ID-only line `[test-run:<32 lowercase hex>]` to that session's `log_lines`,
using `TimestampedLines` when the list is absent. It updates
`last_output_time`, following `mc/browser_agent_read_card.py`. Existing
status/SSE chat readers and Phase 10's live/cold hooks consume that same list;
there is no new output transport, timer or event type.

The caller supplies no session, label, command or marker text. Ownerless,
unavailable, mismatched, incognito and sandboxed sessions emit nothing.
Flag-off returns before storage or session access and changes no behavior.
`post` returns true only when it appended a new line. Exceptions are logged
through `_log` and return false, preserving enrollment receipts, process
launch, job completion and passive detection outcomes.

Existing code has wiring only:

- `TestRunIngest.enroll`: after successful enrollment, covering HTTP wrapper
  runs and manifest/pytest/Node children through the existing shared service.
- `DetectionObserver._apply`: after resolving or creating a passive run;
  repeated observations use the same run identity.
- `agent_jobs.start_job`: after process creation for an explicitly supplied
  `RunCapture`, covering trusted pre-enrolled jobs. Ordinary jobs are unchanged.

`TestRunStore.create` stays a persistence primitive without chat side effects.
Job enrollment does not gain command recognition or reporter injection.

## Idempotency and crash boundary

The run's existing atomic JSON record holds the internal boolean
`chat_anchor_claimed`, outside the enrollment, canonical events and public
snapshot. It is keyed by that record's project/run identity. Reporter
payloads cannot set it, and subsequent store/ingest/finalize writes preserve
it. Shared store `_LOCK` serializes claim and append across threads and store
instances in the installation's existing single-server contract. No new
files appear in the project-records directory and no schema version changes.

The claim is persisted **before** the ephemeral append. A normal eligible
enrollment emits exactly one line; repeated calls, new store instances,
module reload, trimmed logs and server restart never emit a second one.
An already-present exact marker is claimed without another append.

This provides at-most-once emission across crashes, not transactional
exactly-once delivery. A crash or append failure after the claim can lose the
anchor, and a later call deliberately does not emit it again. Design section
6 expressly allows crash-lost/trimmed anchors and identifies the API list as
recovery; the project Test runs panel also exposes those records. A failed
claim write emits no line, logs the error and permits a later retry. No
durable chat rewrite or second history source is introduced.

## Verification

From the repository root, with the project's Python environment:

```text
python -m pytest tests/test_test_run_anchor.py tests/test_test_run_store.py tests/test_test_run_ingest.py tests/test_test_run_capture.py tests/test_test_run_detection.py tests/test_test_run_routes.py tests/test_test_run_finalize.py tests/test_agent_jobs.py -o addopts=-ra -q
pyright mc/test_run_anchor.py
node --test tools/smoke/test-run-card.test.mjs tools/smoke/test-run-data.test.mjs
node tools/smoke/test-run-card.mjs
node tools/smoke/test-run-data.mjs
node tools/smoke/boot-smoke.mjs
git diff --check
```

Foreground invocations used `_scratch/run_anchor_checks.py` to register each
root verification PID immediately and forward output. Smoke dependencies are
the main checkout's `tools/smoke/node_modules`, junctioned into this worktree;
no dependency installation. The junction is removed before committing.

Results (2026-11-06):

- Backend: final combined run 494 passed in 22.65 seconds, rc=0, including
  24 new anchor cases; no skipped tests or warnings.
- New module pyright basic: 0 errors, 0 warnings, 0 informations, rc=0.
- Card/data unit tests: 31 passed, zero failed/skipped, rc=0.
- Card browser smoke: all checks pass, rc=0. It now invokes
  `_test_run_anchor_fixture.py`, which enrolls through real Python ingest,
  persists the claim, retries with a new store instance, and returns the
  actual emitted line plus authoritative snapshot. The real worktree SPA
  renders that line as exactly one correctly titled card. Existing live,
  cold, rebuild, ownership, hostile-text and flag-off cases remain.
- Data browser smoke: all checks pass, rc=0, including hidden/suspended-page
  recovery and no per-run SSE.
- Boot smoke: all seven scenarios and existing guards pass, rc=0.

Tests cover per-ID and concurrent idempotency, reconstructed process state,
trimmed logs, ownerless/unavailable/mismatched/restricted sessions, exact flag
semantics, write failure/retry, partial append failure, persisted metadata
survival through telemetry/finalization, actual ingest/detection/job/HTTP
seams, and launch/receipt/exit isolation on emission failure.

The initial browser invocations failed in the scratch output wrapper's
Windows cp1252 encoding before completing. The wrapper was corrected to
forward UTF-8; the complete successful runs above are the evidence.

## Limits and rollback

Live enabled-server adoption, a real agent-launched command reaching the
deployed card, packaged artifacts, abrupt OS power loss and physical-device
behavior were not exercised. Server restart was modeled by reconstructing
live session state, reloading the module and reopening the durable store.
The browser smoke uses real server enrollment code and real client code with
fixture APIs, rather than modifying the live server or its configuration.

Rollback: revert this phase's commit. Existing claims are ignored by older
store readers and do not alter event replay, snapshots or run verdicts. The
feature flag remains the existing gate; no new control or permission exists.
