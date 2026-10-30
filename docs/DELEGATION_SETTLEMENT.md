# Definite report delivery states

Branch candidate, 2026-10-29. No production activation or live-provider delivery
guarantee is claimed. This supersedes the timing/UI/retry descriptions in the
historical MC-1106 section of `DELEGATION_DELIVERY_CONTRACT.md`.

## Evidence contract

`mc/delegation_settlement.py` owns settlement. `agent_routes.py` only wires the
handoff, Claude result and native-runtime completion hooks and supplies the
existing project manager lock. No idle/age/two-unchanged-reads heuristic proves
absence.

Before an actual parent handoff, `DeliveryStore.record_handoff` records the
claim fence token, time, provider/native identity, actual cwd and a complete
owned native transcript's path, file identity, size and SHA256 prefix digest.
This write precedes the provider call, under the parent manager lock. It applies
to dispatched-child reports, not generic background-job notifications. A failed
baseline read is retained as a named check failure, not invented evidence.

An actual Claude non-error `result` records a completion for that attempt. Other
native runtimes require an actual TURN_END event followed by process-exit
handling after MC question parsing. `waiting_for_question`/`pending_questions`
prevent completion recording. No process exit or `idle` status alone qualifies.
Completion metadata survives a server crash; a crash before it is persisted
cannot manufacture completion during recovery.

The return path also durably records whether the original submission settled:
Mode B requires the real `written` stdin acknowledgment, the same parent id and
no in-memory queuing. Without that record, even a later provider completion
cannot prove that a late writer will not deliver the original after restart.
Native Mode A records its same-parent, nonqueued return and requires its actual
provider turn-end/process-exit path. This is additional evidence, never an
assumption inferred from an idle parent.

Receipt requires the exact callback header as a whole line in newer user text;
assistant quotes and nested tool results cannot confirm it. MC-1106's historical
Claude directory/rollover/dispatch ownership matcher remains available for old
rows lacking a boundary. New attempts use their recorded native transcript.

Absence additionally requires all of:

- The matching provider completion occurred strictly after the handoff boundary.
- The transcript can be completely parsed as JSONL, including its final newline,
  and did not change during the read. Its own metadata verifies native id/cwd.
- The baseline file identity and entire prefix still agree; replacement,
  truncation and rewritten history do not qualify.
- A persisted provider terminal record is newer than the boundary and no newer
  than the recorded completion: Claude assistant `end_turn`/`stop_sequence`, or
  Codex `event_msg.task_complete`.
- No callback receipt exists, no running/question-waiting parent is involved,
  no identity change occurred, and no unresolved writer or in-memory queued
  handoff could still deliver the original report.

A whole-line matching user report with an unusable or non-newer timestamp is
an explicit check failure, not absence. Malformed native message fields also
make the log incomplete even when its lines happen to parse as JSON.

Claude and Codex formats are supported. Other providers show a named unsupported
check. Historical rows without an attempt boundary can be positively confirmed,
but never automatically resent using invented historical evidence.

## One durable automatic resend

Settlement and retry run under the same parent manager lock used by ordinary
handoffs. `DeliveryStore.retry(..., absence_proof=...)` uses its existing
`BEGIN IMMEDIATE` transaction to re-read the row, verify the exact boundary,
completion and claim fence, honor revocation, re-read/re-evaluate the artifact,
and consume `auto_resends` from zero to one atomically with transition to
`pending`. Neither the UI nor the check route can supply this proof.

The inbox columns are additive: `handoff_boundary`, `turn_completion`,
`auto_resends`, `check_status`, `check_requested`. They contain evidence metadata,
not additional report bodies. Existing deletion/revocation removes them with
the inbox row. No sidecar is placed under the project-record directory.

The allowance is never reset by opening the store, claiming/finishing a resend,
or a human Retry. A crash after consuming it cannot grant a second automatic
resend. A second completed, proven absence becomes `recovery_required` with
`check_status=not_delivered`. A failed/partial check after the resend remains a
check failure; it is never mislabeled proven non-delivery.

## Checks and presentation

The delivery loop checks new/changed parked rows and boundary-bearing submitted
reports on its next tick (normally five seconds). Changes to completion,
parent state, explicit recheck revision or transcript metadata bypass the
five-minute full sweep gate. Metadata triggers a read only; it is not absence
evidence. Status/Floor polls remain read-only.

Queued and confirming states are neutral. Check errors name missing, unreadable,
partial, changed/unowned/unsupported logs, missing boundary, identity changes,
unsettled writers/queues, incomplete parent turns or unpersisted terminal turns.
Their Retry calls `POST /api/project/<id>/agent/delegation/check` with `event_id`;
it only increments the check revision and requests confirmation, without
changing handoff state, evidence or resend budget. The route is project-scoped
and validates its input. The existing explicit Retry route remains reserved for
actual resend; Mark handled remains a terminal no-resend action.

Chat and Floor summaries count real stages separately: `not delivered`,
`could not check`, `delivering`, `queued`. Pending rows never inflate the failure
count. Collapse/reopen behavior and scoped report actions remain intact.

## Validation and integration

`tests/test_delegation_settlement.py` covers receipt, proven absence, one resend,
question pauses, unreadable/missing/partial logs, old/missing/future terminal
evidence, identity/prefix changes, pending writers/queues, concurrent retry,
revocation, restart mid-resend, prompt checks, check-only routing and native
Claude/Codex hooks. Existing MC-1106 receipt/ownership tests are retained. The
opt-in startup fixture serializes seeding against source recovery and waits for
the actual submitted state before asserting it. Its process fence stays
subclassable for Windows asyncio imports while still refusing construction;
its substantive assertions are unchanged. Old-schema upgrade coverage preserves
the report and supplies empty evidence rather than inventing a historical
handoff.

Run the affected delegation/runtime/route pytest files, JS report-waiting unit
tests, `report-waiting-marker.mjs`, `boot-smoke.mjs`, and pyright on the changed
delivery modules. Borrow the main smoke dependency junction in a worktree and
remove it before committing. The full-startup harness can be enabled with
explicit process-registration URL/project environment variables; it runs an
isolated test server with fake providers, not the production server.

Verified in the branch (2026-10-29):

| Check | Result | rc |
| --- | --- | --- |
| Combined affected pytest selection below, registered full-startup harness enabled | 448 passed, no skips | 0 |
| JavaScript report notice unit tests | 6 passed | 0 |
| Report notice browser smoke | 54 checks at 1440px and 390px | 0 |
| Dashboard boot smoke | 7 scenarios and all interaction guards passed | 0 |
| Pyright, five changed delivery modules | 0 errors | 0 |
| Final projection checks after the transport-only detail clarification | 15 passed | 0 |

The executed child commands (long-running commands were registered before
waiting) were:

```powershell
# Enable isolated startup subprocess coverage; use an existing registration project.
$env:MC_TEST_PROCESS_REGISTER_URL = 'http://localhost:5199/api/processes/register'
$env:MC_TEST_PROCESS_PROJECT = '<existing-project-id>'
python -m pytest tests/test_delegation_settlement.py tests/test_delegation_transcript_reconcile.py tests/test_delegation_waiting.py tests/test_delegation_delivery.py tests/test_delegation_lifecycle.py tests/test_delegation_privacy.py tests/test_delegation_restart.py tests/test_delegation_status.py tests/test_delegation_uncertain_rows.py tests/test_delegation_wake.py tests/test_delegation_storage_usage.py tests/test_delegation_startup_harness.py tests/test_agent_routes.py tests/test_runtime_completion_log.py tests/test_stream_reader_malformed.py tests/test_respawn_stdin_deadlock.py tests/test_provider_neutral_orchestration.py tests/test_spawner_callback_rearm.py tests/test_callback_shape.py tests/test_question_callback.py tests/test_agent_spawn_notify.py -o addopts= -q --tb=short
node --test tools/smoke/report-waiting.test.mjs
node tools/smoke/report-waiting-marker.mjs
node tools/smoke/boot-smoke.mjs
pyright mc/delegation_settlement.py mc/delegation_transcript_reconcile.py mc/blueprints/delegation_report_routes.py mc/delegation_delivery.py mc/delegation_waiting.py
python -m pytest tests/test_delegation_waiting.py -o addopts= -q --tb=short
```

The temporary dependency junction was removed after browser validation. No live
provider was invoked and no production delivery row was changed.

The integration owner performs merge/restart and live-provider validation. Rollback is a code revert;
the additive columns may remain unused. Do not reset automatic resend counts or
manually replay retained uncertain rows as part of rollback.
