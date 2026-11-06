# Test-run data layer (MC-1148 Phase 9)

`static/js/test-run-data.js` is the one shared client reader for the test-run
read API (`GET /api/project/<id>/test-runs`, `mc/blueprints/test_run_routes.py`).
It renders nothing; phase 10's card, phase 12's Floor line and phase 13's panel
subscribe to it. It is inert until something subscribes, and the server flag
`test_run_progress_enabled` (default off) still gates the whole feature.

## Public surface (`window.TestRunData`)

| Call | Meaning |
| --- | --- |
| `subscribe(projectId, sessionId\|null, cb)` | `cb(view)` fires async with the cache, then on every change. Returns `unsubscribe()`. `view = {projectId, sessionId, runs:[frozen snapshots, newest first], status:{state, lastOkAt, failures, error}}`. |
| `refresh(projectId)` | Read server truth now; resolves `{outcome, changed}` after a fetch that **started after** the call. |
| `hint(projectId, {run_id, seq})` | Optional SSE accelerator. Returns `true` only if the hint is news. |
| `list(projectId, sessionId?)`, `get(projectId, runId)` | Synchronous cache reads. |
| `status(projectId)`, `isDisabled()`, `stats()` | Diagnostics. |
| `configure(opts)` | Tuning/tests (cadences, timeout). |

`status.state` is one of `loading`, `ok`, `stale` (2+ consecutive failures; last
good data is kept), `error` (failing, never had data), `disabled`, `restricted`,
`missing`. Cards should show `stale`, not hide the run.

## Delivery rules

- **Polling, no per-run streams.** One timer for all projects, one request per
  project per tick, however many cards/sessions are mounted. The module opens no
  `EventSource`. Chat SSE closes at turn end and background tests outlive turns,
  so a run keeps updating after the agent goes idle.
- **Cadence.** 1 s while a displayed run is non-terminal; otherwise 2 s doubling
  to 10 s while nothing changes (any change, hint, wake or refresh resets it);
  failures back off 2 s doubling to 30 s; flag-off re-probes every 120 s.
- **Visibility and suspension.** Nothing is requested while the page is hidden.
  `visibilitychange` (visible), `focus`, `pageshow`, `resume` and `online` each
  refetch every subscribed project at once. A timer that fires more than 5 s
  late is treated as a resume too (mobile browsers freeze timers).
- **Requests** carry an 8 s abort timeout. Page size is the server maximum
  (100); if a page is full, each subscribed session is also read directly so an
  older run is not mistaken for absent.
- **Reconciliation by `seq`.** A row is kept only if it is new or has a higher
  snapshot `seq` (equal `seq` only upgrades to a terminal state). Late,
  out-of-order or hint-timed responses cannot move a run backwards. Rows with a
  bad shape are dropped and counted. Snapshots are deep-frozen. Cache is
  per project (the same run id in two projects never collides), capped at 300
  runs, evicting the oldest terminal ones.
- **Flag off.** The server answers a bare (non-JSON) 404 before any work; that
  reads as `disabled`. A JSON 404 is a real missing project/session (stops
  polling it); 403 is a restricted (incognito/sandboxed/unattributed) scope
  (stops polling it). A new `subscribe` retries both.
- **SSE hint.** `resume-preview.js` forwards an `agent/stream` event
  `{type:"test_run", run_id, seq}` to `TestRunData.hint`. A hint carries no run
  data: it only brings the next poll forward (and forces a follow-up fetch if
  one is in flight). Hints for runs already cached at that `seq` are ignored.

## Not in this phase

- The server does **not** emit `test_run` stream events yet; the bridge is
  ready and harmless without them. Emitting them touches `agent_routes.py`'s
  stream loop and needs its own decision (see the phase 9 handoff).
- No UI, no `[test-run:<id>]` anchor handling, no Floor use (phases 10-13).
- Only the first page (100 newest) per project/session is read; there is no
  "load older runs" yet.
- Real mobile-device suspension is emulated (debugger pause), not measured on a
  phone.

## Verification

```text
node --test tools/smoke/test-run-data.test.mjs     # 19 tests, fake clock/fetch, no browser
node tools/smoke/test-run-data.mjs                 # real Chromium, stubbed API
node tools/smoke/boot-smoke.mjs                    # SPA still boots with the module loaded
```
