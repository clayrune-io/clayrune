# Runtime delivery acknowledgment (MC-1168)

Round 2, based on `8551e4a1`, follows Dave's Option A decision in the main
checkout journal: keep draining stdout while initial revival delivery is
pending; defer terminal processing rather than blocking the reader.

## Failure contract

- Cold Codex revival preserves `PromptDeliveryError` through
  `_revive_non_claude_from_agent_log`. Both `/agent/send` and `/agent/followup`
  return HTTP 502 with `ok: false`, `delivered: false`, the error, and the
  original MC `session_id`. Neither starts a fresh conversation as a fallback.
- A Claude Mode B revival installs a `TerminalDeliveryGate` tied to the spawned
  process before starting the reader. The real reader keeps consuming stdout.
  Result settlement, completion, result push signals, and EOF completion and
  recovery are queued until the write and flush succeed.
- Acceptance drains queued terminal handlers in order. New terminal events
  arriving during replay remain behind older queued events. A result can be
  released while the persistent reader is still waiting for more stdout.
- Rejection drops queued terminal handlers and rejects later submissions. EOF
  cannot trigger `_auto_recover_failed_resume` for the rejected delivery.
  Already-exited process bookkeeping (`wait` and unregister) still runs.
- `_abort_failed_revive` clears only the attempted process. It restores the
  prior registry entry and membership only if both the registered object and
  its process still belong to that revival. A replacement process or registry
  object retains ownership and membership. Only the attempted process is killed.

`mc/terminal_delivery.py` owns the queue and acknowledgment state; the existing
reader retains its result/EOF policies as local handlers. This repairs the
existing lifecycle path without moving unrelated reader responsibilities.
The queue lock is released before handlers acquire project lifecycle locks.
Terminal-handler failures are logged and do not turn a successfully written
message into a delivery failure; remaining terminal cleanup continues.

## Validation

From the branch worktree, use the installation launcher and project interpreter:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- python -m pytest tests/test_runtime_delivery_truth_r2.py -v -o addopts=
python <installation>/tools/test-run.py --project mission_control --port 5199 -- python -m pytest tests/test_runtime_delivery_truth.py tests/test_builtins_resume_context.py tests/test_delegation_delivery.py -v -o addopts=
```

The round-2 suite passed **15 tests**, exit **0**; Fenn's unchanged selection
passed **86 tests**, exit **0**. The new suite ports the review's cold-Codex
route and EOF-before-write probes, using the real helper, reader and recovery
path with engine process/pipe doubles. It covers write and flush rejection,
result/EOF ordering on exit codes 0 and 1, persistence before EOF, concurrent
arrival during replay, prior-entry restoration, and replacement ownership.
No real engine was launched or killed.

Reader compatibility checks also passed **86 tests**, exit **0**, with this
launcher invocation:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- python -m pytest tests/test_stream_reader_malformed.py tests/test_session_cost_total.py tests/test_session_num_turns.py tests/test_runtime_completion_log.py tests/test_limit_resume_completion.py -q -o addopts=
```

The final combined run of all nine files above passed **187 tests**, exit **0**
(10.69 seconds). It used the same launcher prefix and `python -m pytest`, with
`tests/test_runtime_delivery_truth_r2.py` first, Fenn's three-file selection
next, the five reader-compatibility files last, and `-q -o addopts=`.

Pyright on `mc/blueprints/agent_routes.py` has the same **15 pre-existing
diagnostics** as the base; `mc/terminal_delivery.py` adds **0**. The combined
check exits **1** because of that baseline. Compare diagnostic messages/rules
and multiplicities because extracting the handlers shifts source line numbers.

The test launcher emitted progress-reporting gaps (`transport_unavailable`,
`telemetry_queue_full`, and `unfinalized_runs`, each marked
`progress_incomplete`); pytest stdout and child exit codes were available.

## Limits and rollback

The gate covers terminal handlers, not all startup/parser side effects.
Startup output and metadata may still be observed before acceptance. There is
no new output spool, durable replay store, queue cap, delivery timeout, or
change to normal accepted-resume recovery. The existing role-context delivery
path rejects the gate if its acknowledgment raises, retaining its prior
uncertain-delivery hold behavior.

No live Codex startup timing, live authenticated conversation, deployment, or
restart was tested. The pre-existing 15-second Codex write budget remains
uncertified for live startup availability. Revert the round-2 commit to undo
these changes; the original false-success defects would return. Branch-only
review candidate: no merge, push, or server restart.
