# Durable delegation delivery

Status: offline-tested implementation on the feature branch. No live activation
or end-to-end production delivery guarantee is claimed.

Current settlement candidate: [Definite report delivery states](DELEGATION_SETTLEMENT.md).
That evidence contract supersedes the original scan cadence, report wording and
blanket no-auto-retry description below. The older validation increments remain
as historical evidence, not live-provider guarantees.

## Implemented increment and evidence

`mc/delegation_delivery.py` persists exact per-turn completion sources,
outbox/inbox receipts, turn allocation, fenced claims, explicit blocked and
uncertain states. The existing completion and startup paths now use this store.
Submission is recorded separately from successful task processing; it is not
verification that the parent completed its work.

The selected notification/workflow/spawn regression suite passes 161 tests.
The Codex integration traverses SQLite, drain, Flask inbox and actual dispatch
with a fake runtime, verifies one launch after reopen, and covers both a named
requested model and explicit native-default selection. Claude recovery uses the
real revival helper with a fake process. No live provider is exercised.

Real loopback-HTTP subsystem restart evidence is now available in
`tests/test_delegation_restart.py`: the actual sender and blueprint receiver
reopen temporary SQLite after a subprocess restart, reach delivered/submitted,
and retain exactly one fake parent handoff across another restart. Fault
injection covers lost receipt responses, busy/ambiguous work and project-scoped
status/retry rejection. Registration is opt-in for operator runs; ordinary tests
do not contact a local Clayrune instance. Combined selection: 164 tests passed.
This harness bypasses full server startup and fakes the parent handoff; it does
not prove live native-provider execution or full server lifecycle recovery.

Agent Log now provides a read-only Delivery recovery view for pending, blocked,
uncertain and recovery-required records. Its project-scoped listing omits payloads
and raw exception text, has bounded pagination and deterministic ordering, and
renders read failures with a manual refresh. Submission means parent handoff
accepted, not task verification. No automatic uncertain retry is provided.

Independent registered-browser fixture validation passed pagination, error state,
stale responses and styled desktop/mobile overflow checks; 34 focused Python
tests passed. The fixture loads the real modules/CSS, not the complete dashboard.
The worker separately reported full dashboard smoke passing. The browser test
is retained in the standard smoke suite with opt-in process registration.

Real-server startup wiring is now tested in disposable subprocesses using
`server.boot`, actual dispatch, automatic delivery loop, SQLite and Flask HTTP.
Unrelated startup producers are stubbed and provider execution is fake. Bound
paths are checked before boot; the child permits only its own HTTP destination
and blocks process launches. Three tests pass in the independent registered
run; worker combined selection is 204 passing. Recovery, lost receipt, exact
generation-3 identity and shutdown fencing are covered. Ordinary runs skip the
process integration unless registration is explicitly configured. This is an
isolated startup test, not an unmodified live installation or provider test.

Remaining activation gates: live-provider validation, provider
submission reconciliation beyond retained uncertainty
and task-result acceptance. No Floor badge or automatic recovery is claimed.
Logical deletion is implemented below; physical WAL sanitization is not.
Unsupported native revival fails closed.

Windows verification note: a host runner with an invalid inherited stdin handle
can fail the existing cross-thread subprocess test with WinError 50/6. The same
161-test selection passed with stdin redirected from NUL; no assertion was
removed. This is a test-launch condition, not a delivery-code workaround.

## Ownership and semantics

### Original lost-acknowledgment report confirmation (MC-1106, historical)

`mc/delegation_transcript_reconcile.py` runs before drain/wake in the existing
delivery loop, never from `/agent/status` or Floor polls. A five-minute in-memory
gate applies to scans including misses and read errors. Restart permits a new
scan. Only `uncertain` and `blocked` inbox rows are candidates. No age cutoff
clears a report, and no reconciliation replays a callback.

The evidence is an exact stored callback header as any whole line of a Claude
user text block, with its JSONL timestamp strictly after inbox `created_at`.
Rollover preambles and injected standing-position/memory prefixes are accepted.
Assistant text, tool results, inline/partial-line headers, missing or equal
timestamps do not qualify. Transcripts in the parent's encoded worktree
directory are considered even if the worktree was removed or their ids are
missing from `claude_session_ids`. Shared project transcript directories require
a recorded parent provider id, the parent's injected job/Floor endpoint identity
in a user text block, or a matching `notify_session` recipient in the parent's
own assistant `tool_use.input`. That last signal covers structured arguments,
nested arguments and dispatch JSON embedded in shell commands, including escaped
JSON and `notify_session=<parent>` notation. Parent ids must match exactly.
Assistant prose, user text containing `notify_session`, and tool results cannot
supply the new ownership signal. Ownership applies to the file; an assistant
tool input never counts as the receipt itself. The existing runtime's
path-encoding variants are reused. Missing/unreadable transcripts retain uncertainty.

Each directory is scanned once across all pending parent groups. Its files whose
mtime predates that directory's oldest pending `created_at` are skipped; skipped
files never count as receipt evidence. Binary prefiltering avoids decoding tool
records without candidate markers; user/text/whole-line/timestamp/ownership
validation remains authoritative. Project records and logs are loaded once per
project per scan. The delivery loop keeps the five-minute gate.

A read-only measurement entry point uses the same scanner and ownership rules:

```powershell
python -m mc.delegation_transcript_reconcile --dry-run --database /path/to/data/delegation_delivery.sqlite3 --data-dir /path/to/data/projects
```

It opens SQLite with `mode=ro` and `query_only=ON`, closes the connection before
scanning, never constructs a `DeliveryStore` or calls resolution, and reads saved
project/agent-log records plus real transcript directories. Live in-memory
session ids are not supplied in this offline mode. `--projects-home` can select a
test transcript root. Output includes per-project matched/parked/eligible counts,
event ids, files scanned/skipped, and elapsed scan/total dry-run tick seconds.
Unmatched reports remain unresolved; an unmatched count is not evidence of loss.
Install-specific measurement snapshots belong in the ticket journal.

Matches become `confirmed_in_transcript`; this is receipt evidence, not task
acceptance. `DeliveryStore.mark_handled(event_id, project_id)` and
`POST /api/project/<project_id>/agent/delegation/mark-handled` with
`{"event_id":"..."}` end a parked report as `handled` without delivery. Both
operations atomically settle its inbox/outbox copies, preserve immutable
payloads, clear recovery flags, refuse active claims and remain terminal across
restart/source recovery. The route returns 400 for invalid input, 404 outside
the project or for a missing event, and 409 if a handoff is no longer parked.
No new schema or physical deletion is involved.

The chat notice is one line for multiple reports until expanded. Recovery rows
offer Retry (existing reviewed-resolution route, with duplicate-delivery
confirmation) and Mark handled. Expanded/collapsed state survives status polls
and modal rebuilds, retaining the MC-1063 cache restoration path. Expanded lists
scroll within 30vh. Live activation requires integration and a server restart;
branch tests alone do not clear existing production rows.

MC-1106 branch validation (2026-10-16; isolated databases and fake providers):

```powershell
python -m pytest tests/test_delegation_delivery.py tests/test_delegation_lifecycle.py tests/test_delegation_privacy.py tests/test_delegation_restart.py tests/test_delegation_startup_harness.py tests/test_delegation_status.py tests/test_delegation_storage_usage.py tests/test_delegation_uncertain_rows.py tests/test_delegation_waiting.py tests/test_delegation_wake.py tests/test_delegation_transcript_reconcile.py tests/test_spawner_callback_rearm.py tests/test_callback_shape.py tests/test_question_callback.py tests/test_agent_spawn_notify.py -q -o addopts=''
node --test tools/smoke/report-waiting.test.mjs
node tools/smoke/report-waiting-marker.mjs
node tools/smoke/boot-smoke.mjs
node tools/smoke/delivery-status-visibility.mjs
node tools/smoke/agent-stream-dedupe.mjs
pyright mc/delegation_transcript_reconcile.py mc/blueprints/delegation_report_routes.py mc/delegation_delivery.py mc/delegation_waiting.py
```

Results after ownership review correction: 204 Python passes (56 reconciliation
checks), 1 skip (the existing opt-in full-startup subprocess
harness requires explicit process-registration configuration); 4 JS unit passes;
46 report-notice smoke checks across 1440px and 390px; boot (7 scenarios plus
interaction guards), recovery visibility and stream dedupe passed; four modules
type-check with zero errors. The smoke dependency junction was borrowed from
the main checkout and removed before commit. No live provider or production
database mutation was exercised.

UI check after integration/approved restart:
1. Open a parent chat with multiple unresolved reports; confirm one summary line,
   expand it, and reopen the chat to check the view survives.
2. Mark a report handled; confirm it disappears without starting a parent turn.
3. Wait up to five minutes: reports already present as newer parent Claude user
   turns clear; an unmatched report remains available for review/Retry.

### Delivery-loop lifecycle

Start, stop admission and dependency rewiring serialize under a lifecycle lock.
The loop retains its thread and stop event; waits are interruptible and joins
are bounded outside the lock. A timed-out join retains ownership and does not
authorize a duplicate loop. Stop closes parent handoff admission after the
manager-lock wait; work already admitted remains an in-flight outcome, not a
canceled action. The durable store remains available for final completion writes.

The shutdown hook runs before persistent-agent cleanup and logs a join timeout.
Worker validation: 18 lifecycle tests, 201 combined regressions. Independent
parent validation: 33 lifecycle/delivery tests. Type checking remains at the
reported pre-existing baseline of 9 errors and 1 warning, not globally clean.
This proves the isolated lifecycle contract, not full-server startup isolation.

### Delivery-record deletion boundary

Project and conversation deletion revoke matching delivery identities and purge
outbox, inbox and completion-source payload rows. Native conversation aliases
are mapped to MC session identities; closing a tab does not revoke a conversation.
Rejected conversation deletion leaves delivery state unchanged; a transcript
rename failure after revocation returns an explicit partial outcome.

Project generations are bound before delegated execution and carried through
completion/source recovery. Recreating a deleted project cannot accept an unseen
completion from its old generation. Creation validates and saves before reopening
delivery under the manager guard, with a concurrent-creation recheck.

This is logical SQLite deletion, not physical WAL/disk sanitization. Existing
transcript, agent-log and memory retention semantics are unchanged. Retention
budgets, broader lifecycle cutover and operator recovery UI remain separate gates.

Privacy increment evidence: worker combined selection **180 passed**; independent
parent privacy/delivery selection **31 passed**. The failure tests use the actual
derived database path and temporary workspaces. Fake runtime invocation asserts
the recreated generation before launch; reopen/source recovery retains it.

A delegated task is a visible Clayrune session with immutable parent/project
identity and explicit requested provider/model. Completion of a child turn is
not acceptance of its work. Preserve three distinct facts:

1. The child emitted a terminal outcome and result (or failure).
2. The parent's durable inbox accepted the completion event.
3. The parent processed it and verified the task's acceptance criteria.

An HTTP response alone proves none of these without the corresponding durable
record. A completed worker with failing tests must not advance the task graph.

## Delivery protocol

Persist an immutable outbox event keyed by child session and durable turn ID.
Retain the full result or a durable artifact reference; a short chat preview is
not the sole copy. Enqueue before recording that notification was handled.
Transport retries use the same event identity. The receiver persists an inbox
receipt and pending work atomically before acknowledging acceptance.

Concurrent senders use leases or equivalent transactional claiming. Retry with
bounded backoff; failures retain the event and observable reason. Restart
reconciliation recovers both undelivered outbox events and accepted-but-pending
inbox work. No daemon-thread-only state is authoritative.

Parent processing is not exactly-once merely because inbox insertion is
idempotent. Persist dispatch intent and attempt ownership before launching or
writing to a provider. If a crash makes submission uncertain, expose that state
and reconcile evidence; never blindly repeat an actionful model turn. Do not
mark an event processed when it merely entered an in-memory queue.

## Safety boundaries

- A running parent receives pending work without destructive interruption by a
  retrying transport. Existing user-message interrupt behavior is separate.
- Missing/deleted parent, wrong project, quota/auth failure and provider
  unavailability are explicit blocked or pending states, not a fresh dispatch
  using project defaults. No implicit provider/model substitution.
- Completion notifications carry result data, not newly elevated authority.
  Existing unattended-origin and privacy protections remain in force.
- Incognito runs must not leak task/result content into the durable outbox.
- Failed or stale worker liveness never authorizes duplicate child execution.
- Durable bookkeeping is outside the directory whose JSON files are loaded as
  project records. Reuse established database/path/locking conventions.
- Existing workflow callbacks retain their own completion semantics; do not
  accidentally acknowledge them through a spawner-chat receipt.

## Acceptance evidence

Offline tests must exercise integrated production seams, not only a store:

- Failure before/after enqueue, transport timeout, receiver persistence failure,
  lost response after acceptance, concurrent delivery and exact duplicate IDs.
- Reopen after sender/receiver restart, accepted pending work, and uncertain
  provider submission without an automatic duplicate turn.
- Consecutive child turns, duplicated finalization, and delayed prior-turn
  callbacks. A new turn must not reuse a prior completion identity.
- Busy parent, missing parent, quota block, wrong project and incognito child.
- Parent provider/model/effort preservation and no fresh-default fallback.
- Full result retention, explicit delivery/processing/verification states and
  compatibility with existing notification/workflow regressions.

Activation additionally needs a controlled end-to-end test through the running
server. Offline green does not imply that an un-restarted server is fixed.

## Logical payload usage advisory (WARN ONLY)

The recovery status endpoint also reports project-scoped logical delivery
payload usage for `completion_sources`, `outbox`, and `inbox`. Counts use the
exact UTF-8 bytes of each SQLite payload via aggregate SQL and identify each
table separately. Agent logs, native transcripts, SQLite file size, and WAL
amplification are excluded; this is not total disk usage.

`delegation_payload_warning_bytes` is an adjustable Settings value, defaulting
to 1 GiB per project. A value of `0` disables the warning only. Equality with
the threshold warns. The advisory never gates dispatch or completion, never
truncates/deletes/auto-rejects results, and does not change retention or
reservation behavior. A read failure is returned as explicit unknown state,
never as zero.

The selected policy is warn-only, not admission backpressure. Logical retained
usage remains uncapped; actual disk-full and I/O failure are not prevented by
an advisory. Worker combined evidence: 208 tests, no skips. Independent checks:
43 storage/status/Settings tests and registered real-module browser validation
for warning, unknown and disabled states. No production activation is implied.
