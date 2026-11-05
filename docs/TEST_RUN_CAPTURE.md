# MC-1148 Phase 2: enrolled job capture

Status: dark internal backend; no route enrolls a run or passes capture today.
This does not deliver live test counts, a launcher, a reporter, UI, or restart
survival. Parent: `TEST_RUN_PROGRESS_DESIGN.md` sections 5/7; store contract:
`TEST_RUN_STORE.md`. Phase 1's format and implementation are unchanged.

## Internal integration

Trusted server code first enrolls a run using `TestRunStore.create`, constructs
`RunCapture(store, project_id, run_id, on_batch=optional_consumer)`, and passes it
as `capture=` to `agent_jobs.start_job`. Each instance belongs to one launch.
The worker verifies the record exists, so it cannot manufacture enrollment.
Owner binding, producer receipts and authorized source attachment remain phase
3; this internal API is not an authentication boundary. The caller retains
job/process registration and must preserve existing execution refusals.

Without `capture`, the hooks return immediately: no observer thread, file reads,
store accesses, or altered callback payload. There is no automatic command
detection, configuration switch, or HTTP request field. The worker starts after
the existing subprocess is recorded. No pipe, replacement process, environment
change, or stdout tee is introduced; the child's stdout/stderr destinations and
the job log remain unchanged.

## Bounded observation and batching

A single daemon thread opens the existing binary log for incremental reads of
at most 64 KiB, polling at 50 ms when idle. An incremental UTF-8 decoder handles
characters split across reads. Newline-delimited lines preserve a final line
without a newline, normalize CRLF, and replace invalid UTF-8. Incomplete lines
retain at most 16 Ki characters. A longer line delivers its retained prefix,
discards its remaining characters until the newline, and records an explicit
`line_truncated` partial-capture diagnostic once. The original job log is intact.

`on_batch` receives a tuple of completed lines for one bounded read, or the final
partial line. It runs only on the capture thread. It receives untrusted raw text
and must bound its own retained data. It is an internal adapter seam, not a
reporter implementation. No counts, failure names, or percentages are inferred
from console words or process status.

The log itself supplies the output backlog. The only in-memory queue has one
slot for process-exit evidence; output never accumulates there. A slow consumer
or store leaves unread bytes on disk, without dropping output or blocking the
child's stdout. This intentionally avoids a second reader/consumer queue and
its overflow policy. Retaining/managing the original log is still the existing
job subsystem's responsibility.

Output health is coalesced into one heartbeat per 256 KiB or 0.5 seconds while
new bytes arrive, plus a final flush. Diagnostics are deduplicated by code per
capture. Raw output, arbitrary exception text, commands and environment are
never written to telemetry. A 3,000-line fixture requires only start, heartbeat,
and process-finish events. Every heartbeat remains an existing store append;
this reduces write frequency, not the store's asymptotic whole-history cost.
There is no batch persistence API for thousands of structured test reports in
this phase; high-volume reporter ingestion must address that in its own phase.

File identity, size and a 32-byte anchor preceding the offset detect rotation,
truncation and many truncate-and-regrow cases. A detected change resets byte,
decoder and line state and records `source_reset` with partial capture; prior
results in the store are retained. This is not a filesystem audit guarantee:
rewrites preserving identity, size and the anchor are indistinguishable. A
missing/unreadable source is a capture failure, never an empty successful read.
Offsets/generations are process-local; they do not establish restart adoption.

## Completion boundary selected by Dave

The watcher closes its log and computes its existing job result as before. It
passes the real observed exit code, timeout flag and process-finish timestamp
to the worker through the one-slot queue. It waits for worker acknowledgment
for at most `FINAL_DRAIN_WAIT_SECONDS = 2.0`, then invokes the existing
`on_complete` callback exactly once. The wait does not change job duration,
exit status, result keys, timeout policy, kill function or process registration.

Normally the worker drains to EOF, flushes the decoder/last line and coalesced
events, and calls `store.finish` before acknowledging, so the callback can read
a terminal snapshot. Exit zero maps to passed, nonzero to failed, timeout to
timeout, and unavailable exit evidence to lost. The store still downgrades
success when known failures exist. These are process verdicts, not a claim that
all tests ran or passed; totals remain unknown without a reporter.

If the window expires, `_log` records the run ID and elapsed wait. The worker
continues draining and persists the original exit evidence when it can. There
is no timeout-triggered partial-capture event, forced terminal state, discarded
final output, or second job callback. A worker that never resumes leaves the
record non-terminal. Restart reconciliation is outside this phase. The bound
applies to the acknowledgment wait, not existing job-tail I/O, scheduler delays,
or the logging subsystem.

Capture exceptions are caught and logged with run ID and exception class;
arbitrary exception messages can contain output and are withheld. The worker
attempts a `capture_failed` partial-capture diagnostic, logs diagnostic-write
failure too, and exits without inventing a terminal verdict. Its completion
signal also releases a waiting watcher on failure. Start/completion hook
exceptions cannot replace the original job result or callback.

`direct_argv` still directs both streams to DEVNULL. An enrolled suppressed job
records lifecycle and `output_suppressed` with partial capture, never reads the
log or calls the text consumer, and records its real process exit. Output
suppression is intentional and is not undone to obtain progress.

## Validation

Focused tests use fake subprocesses to isolate capture timing and exactly-once
callbacks. They cover normal finish-before-callback, stalled storage (including
startup), a hung consumer, late finalization, disk backlog, UTF-8 splits and
invalid final bytes, truncation/regrowth/rotation, long-line limits, interval and
byte-count batching, suppressed direct argv, hook/store/consumer exceptions,
missing enrollment/exit evidence, and timeout evidence. Existing job, endpoint,
store and harness-runner tests run unchanged, including real subprocess and
process-registration regressions.

Commands from the candidate checkout:

```text
python -m pytest tests/test_test_run_capture.py tests/test_agent_jobs.py tests/test_test_run_store.py tests/test_background_job_endpoint.py tests/test_dev_harness_runner.py
pyright mc/test_run_capture.py
git diff --check
```

No production enrollment, live UI, cross-engine reporting, deployment or
server-restart capture has been tested or claimed. No dependencies were added.

Candidate results (2026-11-05): selected combined suite **165 passed in 53.92s**;
after strengthening the late-drain no-partial-fallback assertion, focused capture
suite **24 passed in 7.31s**. Pyright on the new module: **0 errors, 0 warnings,
0 informations**. `git diff --check`: exit 0.
