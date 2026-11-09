# Runtime delivery acknowledgment (MC-1168)

Round 3 integrates master `1fdffd96` with the prior delivery repair at
`c046c4da`. Dave selected Option A: keep draining stdout while initial delivery
is pending, and separate guarded launch/publication from acknowledgment. The
outermost guarded caller awaits only after its project/lifecycle locks exit.

Round 5 merges master `f9ebacd6` into round 4 `23fa5f2f`, retaining MC-1167
resume provenance and API credits phase 2. `_revive_non_claude_from_agent_log`
rethrows `ResumeCwdMissing` first, then `PromptDeliveryError`, before the broad
logged fallback. They are separate exception classes: cwd refusal reaches the
route's HTTP 409 `resume_cwd_missing` response, while failed delivery reaches
HTTP 502. Send, follow-up and dispatch retain their existing missing-cwd
handlers; neither typed failure authorizes a fresh conversation.

API-credit result accounting/halting and exit replay stay inside Mode B's
deferred result/finish handlers. They run after accepted delivery, in result
then EOF order, and are discarded on rejection. A billing halt terminates the
original child and suppresses later result settlement; exit replay retains
master's completion/recovery suppression. Claude revival restores the billing
snapshot while also retaining round 4's prior-session rollback ownership.

`tests/test_runtime_delivery_truth_r5.py` drives the same cold non-Claude HTTP
request through a cwd refusal and then a prompt failure for send and follow-up.
It also exercises the real gated Mode B reader with accepted/rejected billing
result and replay hooks. No new policy or startup timeout is introduced.

The MC-1167 fake-process fixture now runs the named revival stdin writer as
well as Git's pipe readers. Its provider readers/guardians remain inert;
suppressing the writer would leave the new acknowledgment waiting forever.

Round-5 combined-tree checks, all through the installation test launcher:

| Selection | Passed | Failed | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: |
| Exact requested 18-file delivery selection | 593 | 1 | 1 | 1 |
| All five `test_resume_cwd_*.py` files | 54 | 0 | 0 | 0 |
| All six `test_api_credits*.py` files | 161 | 0 | 0 | 0 |
| Exact 12-file MC-1165 isolation selection | 211 | 0 | 13 | 0 |
| Six scheduler-related files | 195 | 0 | 0 | 0 |
| Round 5 + round 4 + API-credit session integration | 26 | 0 | 0 | 0 |

These selections overlap. The 18-file run retains the unchanged original
scratch retry probe: it first requires a replacement, then later requires no
replacement. Its stale first assertion is the sole failure; the corrected
permanent retry regressions pass. This is not a fully green selection.
Pyright on 19 merged production modules exits 1 with the same 15 existing
route errors and no new diagnostics. Explicit checks on both edited tests
also add no diagnostics. Exact argv, outputs, durations and baseline
comparison are recorded in the main-checkout item journal. No live provider,
paid billing run, deployment, master merge, push or restart was exercised.

## Failure contract

- Scheduled cold revival propagates `ReviveDeliveryError` and
  `PromptDeliveryError` to cadence and Run Now. Neither automatically dispatches
  or retries that task. Run Now returns HTTP 502 with `ok: false`,
  `delivered: false`, the addressed `session_id` and the delivery error. Cadence
  consumes the failed slot, retaining the regular next cadence; a failed
  one-shot stays disabled rather than retrying or deleting itself.
- Both paths retain `last_error` / `last_error_at` and mark the schedule's
  conversation row `error` with the delivery error in its summary. Prior
  conversation identity and usage remain intact. If no row exists yet, the
  failure is inserted into the same serialized agent-log store, so Runs can
  show it. MC-1165 isolation refusals retain their existing handling. Only
  explicit clean misses permit fallback; unexpected revival errors propagate.
- `delivery_failure_handler` captures runtime launch cleanup with each deferred
  acknowledgment. A rejected Codex write reaches `_persist_runtime_start_failure`
  after the outer publication locks exit, marking its pending row `error` with
  the failure summary. Cleanup checks session and process ownership, leaving
  a newer owner and its pending row untouched.
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
- Both Codex launch paths install the same process-scoped gate before their
  real Mode A reader starts. Turn-end usage/completion work and EOF capture,
  model-refusal retry, status and callbacks wait for write plus stdin close.
  Rejected delivery drops them. An accepted model refusal can still retry once;
  a rejected original cannot spawn a replacement and then report failure.
- Codex write failure changes liveness/status only while the attempted process
  still owns the session. A replacement remains live. The bounded Mode B
  writer likewise preserves a replacement's state on old write failure/timeout.

`mc/terminal_delivery.py` owns the queue and acknowledgment state; the existing
reader retains its result/EOF policies as local handlers. This repairs the
existing lifecycle path without moving unrelated reader responsibilities.
The queue lock is released before handlers acquire project lifecycle locks.
Terminal-handler failures are logged and do not turn a successfully written
message into a delivery failure; remaining terminal cleanup continues.

## Publication and acknowledgment boundary

`mc/pending_delivery.py` owns a scoped list of pending completions. Nested
launch helpers join the active boundary, rather than awaiting while an outer
caller still owns the reentrant project lock. The owner resets the scope and
settles every launched completion after its body exits, including exceptional
returns. Resetting before settlement gives accepted terminal retries their own
acknowledgment rather than silently appending them to an already-finished call.

Claude revival's existing window stack closes the MC-1165 `resuming` window
before settlement. Runtime dispatch and core dispatch have equivalent nested
boundaries. `_process_inbox` owns the outer boundary around its guarded helper:
receipt revalidation, shutdown admission, identity/quota checks and process
publication remain under `manager.lock`. Its cold-parent Claude and Codex paths
defer acknowledgment beyond that outer lock, including non-isolated revival.
The live Mode B inbox path carries a mutable pending acknowledgment out too;
handoff response evidence is recorded only after it resolves. A write failure
after submission remains uncertain, so the receipt is not implicitly replayed.

`mc/pending_delivery_http.py` supplies the outer boundary for send, follow-up
and interrupt, preserving HTTP 502 with the addressed session identity for
deferred delivery failures. The internal interrupt form retains its plain
dict/status response. Isolation failures still return HTTP 409. No private
RLock release/restore operation, new reservation or admission bypass is used.

`tests/test_session_mcp_launch.py` now provides `StringIO` stdin and executes
writer threads while suppressing only reader threads. Production acknowledgment
and its time budget are not weakened to accommodate that fixture. The older
MC-1165 hung-writer regression now expects the truthful acknowledgment wait
and proves another thread can acquire the project lock before releasing stdin.

## Validation

Round-4 results and exact commands are recorded in the main-checkout journal
`docs/_journal/da5ee79f-runtime-delivery-truth.md`. All three round-3 finding
probes were copied unchanged and pass. The permanent regressions run real
scheduler/revival/dispatch paths with process doubles; cadence and Run Now
each cover write failure and flush failure after an accepted write. They also
check Run history, native-provider delivery errors, deferred durable cleanup
outside locks, and preservation of replacement process/registry ownership.

The historical round-3 594-pass run below used a corrected scratch retry
probe. Fenn's original retained probe has contradictory assertions requiring
two spawned processes before requiring only one. Round 4 retains that original
file unchanged in the requested 18-file selection; its failure is disclosed
separately from the permanent tests and the three finding probes.

| Round-4 selection | Passed | Failed | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: |
| Three unchanged round-3 finding probes | 3 | 0 | 0 | 0 |
| New permanent regressions + scheduler route suite | 84 | 0 | 0 | 0 |
| Fenn's exact 18-file runtime selection, unchanged scratch probes | 593 | 1 | 1 | 1 |
| Fenn's exact 12-file MC-1165 selection | 209 | 0 | 13 | 0 |

The 18-file failure is solely the contradictory scratch retry assertion at
line 109; its 592 permanent tests pass. The first run also exposed an old
permanent scheduler test expecting `None`; that test now requires propagation
and passes in the final exact selection. Counts overlap. Pyright on the seven
reviewed runtime modules plus scheduler routes and the new failure module
reports 15 errors, all pre-existing in agent routes. Comparing diagnostic
file/rule/message/severity multiplicities with Fenn's exact `156d8a56` report
gives zero new and zero removed diagnostics; the other eight modules are clean.

Round-3 validation through the installation's `tools/test-run.py` launcher,
from the task worktree with the project's interpreter (`-q -o addopts=`):

| Selection | Passed | Skipped | Exit |
| --- | ---: | ---: | ---: |
| Fenn selection 1 (nine unchanged files) | 187 | 0 | 0 |
| Fenn selection 3 (provider runtimes + MCP boundary) | 296 | 1 | 0 |
| Final delivery/route/delegation compatibility run (18 files, including both probes) | 594 | 1 | 0 |
| MC-1165 isolation, lifecycle and review-gate suites (12 files) | 209 | 13 | 0 |
| Focused post-change lock/lifecycle suites (4 files) | 48 | 0 | 0 |
| Mid-turn send, negation interrupt, stdin deadlock | 57 | 0 | 0 |
| Mid-turn rollover (separate process) | 67 | 0 | 0 |
| Steward role dispatch | 71 | 0 | 0 |

Counts overlap; they are not a total of unique tests. An optional combined
interaction run recorded 718 passed, 1 skipped and 1 teardown error when a
question timer outlived its fixture and attempted a mail subprocess; the test
guard refused that subprocess, so no mail was sent. The interaction selections
above passed separately. An earlier isolation run overlapped source edits and
invalidated an `inspect.getsource` line lookup; the stable rerun above passed.

Pyright on `mc/agent_runtime.py`, `mc/blueprints/agent_routes.py`,
`mc/steward_role_dispatch.py`, `mc/terminal_delivery.py`,
`mc/resume_context.py`, `mc/pending_delivery.py` and
`mc/pending_delivery_http.py` exits 1 with the
same 15 existing route diagnostics as master. There are no new diagnostics by
message/rule/multiplicity comparison, and no diagnostics in the other six
modules. The feature diff against integrated master passes `git diff --check`.
Launcher transport/enrollment/telemetry
gaps were printed; counts and exits above come from actual pytest output.

`tests/test_runtime_delivery_truth_r3.py` ports both reviewer race scenarios,
extends them to both Codex launch paths, and checks accepted retry, turn-end/EOF
ordering, replacement ownership, HTTP failure truth, stale receipts and outer
lock availability for Claude/Codex parents with and without isolation. It also
checks live Mode B delivery and exceptional boundary settlement. Processes and
pipes are doubles; provider launch and kill are not exercised.

The retained review probe originally contained observation assertions that
demanded a replacement before its final assertion demanded no replacement.
The port removes that contradiction and retains the desired no-retry and
no-terminal-callback contracts. The scratch copy uses the same corrected
assertions; it is not represented as an unchanged copy of the reviewer file.

The following validation records describe round 2, before master integration:

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

For round 3, revert its task-branch integration commit to restore the prior
branch state; preserve the separate MC-1165 work on master. No production
deployment or master merge is part of this review candidate.

Round 4 is a separate fix commit on that branch state. Reverting it removes
the scheduler failure history and deferred runtime cleanup repairs without
changing MC-1165 or the round-3 acknowledgment boundaries.

Round 3 preserves Claude revival's existing unbounded acknowledgment wait,
now outside the project/lifecycle locks; it does not add a startup deadline.
Codex keeps its existing 15-second budget and live Mode B delegation its
existing 10-second acknowledgment budget. Those policies were not retuned.
