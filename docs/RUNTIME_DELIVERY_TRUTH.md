# Runtime delivery acknowledgment (MC-1168)

Round 13 (`fix/mc1168-r13`, base `51d0e400`) makes the rollback snapshot
authoritative. `launch_transaction._strict_rows` treats only a missing file
as absent history; exhausted I/O retries, malformed JSON and invalid row
containers refuse before the reservation action changes live or durable state.
It never quarantines history. The general `_load_agent_log` contract remains
unchanged for other callers.

Snapshot, durable unknown-outcome check, reservation action and destination
read share the existing manager -> log leaf -> shared file transaction lock
order. Nested reservation writes reenter those locks; unrelated server leaf
writers and offline tools using `agent_log_file.transaction` cannot interleave
between snapshot and reservation. The locks release before provider spawn.
Launch ownership also uses a strict read under that lock order, so a failed
ownership read cannot authorize a child. Owner-CAS rollback and ambiguous
launch refusal retain the round-12 policy below.

`tests/mc1168_r13/test_snapshot_read.py` and `test_cold_unknown.py` retain
Fenn's review probes byte-for-byte: queue preservation and helper guard
(four cases), plus actual cold scheduled dispatch in Mode A/B with/without
the snapshot I/O fault (four cases). `test_snapshot_authority.py` adds malformed
history, launch-read refusal, and concurrent leaf/shared-file writer controls.
The existing scheduled-delivery and lifecycle fixtures expose their intentional
in-memory stores to the strict lookup too. Lifecycle fixtures use a temporary
log directory and keep the real shared file locks; no behavioral assertion or
reviewer probe is changed.
This remains a local review candidate, without merge, push or restart.

Round-13 final verification: all 18 retained behavioral selections passed
(each exit 0), including 592 runtime tests, 211 isolation tests with 13 skips,
222 writer/history tests and 95 factory tests. The new suite passed 16 cases
(exit 0), comprising the unchanged 4+4 reviewer probes and eight authority/
serialization controls. Counts overlap. All seven narrower typing selections
had zero errors (exit 0); core Pyright retained exactly 15 baseline diagnostics
(exit 1), with no additions or removals by normalized file, severity, rule,
message and multiplicity. The retained baseline was read, never regenerated.
All 27 selections ran through the installation test launcher with frozen
source/evidence hashes and exact retained argv. Framework output and child
exits establish results; launcher transport/enrollment/progress gaps remain
disclosed in the handoff evidence. Reproduce the new suite from the branch:

```text
python <installation>/tools/test-run.py --project mission_control --port 5199 -- python -m pytest tests/mc1168_r13 -q -o addopts=
```

Round 12 introduces `mc/launch_transaction.py` on `fix/mc1168-r12`, continued
from investigation checkpoint `10e13421`. This is a local review candidate;
no merge, push, restart or live provider certification is implied.

The shared transaction snapshots the previous row and turn state under the
manager lock, reserves before launching, and carries that exact delivery token
into publication and metadata settlement. It never adopts a later row's token.
Cold initial reuse reserves whenever history already contains this session,
even without `notify_session`. Fresh standalone/no-row dispatch retains its
existing best-effort postlaunch insert and completion behavior. Incognito and
housekeeping launches do not acquire history destinations.

For a **proven pre-child refusal**, restoration requires both the live and
durable owner still to be the reserved owner. The old complete row is restored,
or a newly inserted reservation is removed. Queue replay puts the undelivered
input back at the front, ahead of later messages. A successor's live state or
durable result cannot be restored over. Allocated delegation numbers are never
reused. Reservation/storage refusal remains strict and starts no process.

The factory preserves exception types and marks all uncertain branches,
including shim-launch and post-child delivery errors, as ambiguous. Production
plain Popen proves refusal only for `FileNotFoundError` at its constructor
boundary (missing executable/cwd); generic OSError may arise after creation
during handle cleanup and remains ambiguous. The transaction's injectable
factory contract permits an unmarked OSError only as a pre-child refusal;
Fenn's unchanged queue probe uses that contract. No exception-type heuristic
can downgrade a marked factory error to a safe refusal.

An ambiguous launch keeps its reservation, clears automatic recovery input,
and records `launch_outcome=unknown` on its still-owned durable row where
storage permits. Both live and rebuilt transactions refuse another launch of
that unresolved destination. Failure to save this marker is logged and retains
the live refusal flag and pending owner. There is no retry or fallback dispatch.
Once a child returns, the transaction cannot restore prelaunch history. Failed
metadata settlement retains the original owner and lets its reader complete.

Self-start callback rearm uses the same abstraction with `running=True`.
Because execution already exists, failed rearm persistence rolls back **only
the attempted owner replacement**, retaining the cleared callback latch and
allocated turn. The reader continues and completion can save after recovery.
The first self-start claim's existing idle/live/durable race checks, including
preparing its expected owner before waiting for the manager, remain unchanged.

### Complete route launch inventory

Every `spawn_engine_proc` reference in `agent_routes.py` is passed directly to
the shared transaction, with no direct call or factory alias. The AST guard in
`tests/test_launch_transaction.py` pins all eleven sites and rejects an injected
bypass:

| Path | Sites | Reservation |
| --- | ---: | --- |
| `_auto_recover_failed_resume`, A/B | 2 | Claim the current destination without erasing its history; reject stale recovery owners. |
| `_revive_from_agent_log`, B/A | 2 | Capture/claim before spawn; carry that attempt through publication and delivery errors. |
| `_auto_dispatch_followup_locked` | 1 | Snapshot and reserve the next turn; restore only on proven refusal and retain queued input. |
| `_dispatch_agent_internal`, B/A | 2 | Strict delegated or existing-row reservation; explicit fresh/no-row exception. |
| `agent_followup._do_respawn_b` / `_start_followup` | 2 | Carry the request's turn reservation into asynchronous respawn. |
| `agent_interrupt._do_respawn`, B/A | 2 | Carry the pre-interrupt turn reservation into either replacement mode. |

Four direct `subprocess.Popen` calls are explicitly **out of scope**: the
Windows, macOS and Linux terminal launches in `_launch_terminal_for_binary`
(three), and remote authentication in `agent_auth_login_remote` (one). They
launch auth/terminal tools, not conversation-history turns. Native runtime
dispatch and live stdin submission retain their existing pre-submission
ownership boundaries; this inventory is the eleven route factory sites, not a
claim to replace every runtime's process factory.

### Authorized acceptance correction and retained evidence

Dave selected option 1 on 2026-11-09. `tests/mc1168_r12/reused_initial_original.py`
preserves the original cold-reuse probe bytes. The corrected copy remains
`tests/mc1168_r12/test_reused_initial.py`; its only changed line is:

```diff
-    assert saves == [True]
+    assert saves == [False, True]  # reservation before launch, then metadata enrichment
```

- Original SHA256: `0afecc8f9b05ca94b9ebe63b44c2b6233b1d6395ef476f224eeb571b5745e11f`.
- Corrected SHA256: `8356654aa4d1dd17ded818a17ca90034f408b9bc76477951ac8892a3ca82fb8e`.
- Unchanged queue probe SHA256: `b5ef9e440ce6f668d17959955fc33b80ddc321a094c0516e403e9df7a6525c97`.
- Unchanged extracted parent control SHA256: `c51feb304b49639d4df8222758d7d91d77c5f5cb17e5f8210f3589298273fd96`.

Every other assertion in both probes is unchanged. Literal-path attributes
preserve the evidence across Windows checkouts. The shared older revival test
fixture now uses a real temporary JSON log: its previous reader returned a new
legacy literal on every call, hiding the owner's successful write from launch
revalidation. Lifecycle fixtures explicitly supply either an empty history or
their composing revival row, and the standalone factory outcome test gives
Codex's writer a temporary directory. The older flags inventory now finds the
same wrapped calls and keeps all eleven count/flag assertions. No assertion or
production ownership check was weakened.

Final round-12 validation used every original Fenn r11 argv through the
installation launcher, plus separate r12/factory selections. All18 behavioral
selections exit0. Core typing retains15 baseline diagnostics (exit1), with
zero additions/removals against the retained r9 baseline; six narrower typing
selections exit0 with zero diagnostics. Counts overlap. Identical final source
hashes were recorded before/after every selection. Telemetry gaps are retained;
framework summaries and actual child exits establish these results.

| Selection | Actual result | Child exit |
| --- | --- | ---: |
| fresh-initial | 6 passed | 0 |
| fresh-reader | 5 passed | 0 |
| r10-final | 5 passed | 0 |
| self-start-final | 50 passed | 0 |
| independent | 9 passed | 0 |
| focused-r5-probes-r4-r5-r6-r7-closed | 50 passed | 0 |
| runtime-permanent17 | 592 passed, 1 skipped | 0 |
| resume-cwd | 54 passed | 0 |
| api-credits | 161 passed | 0 |
| scheduler | 195 passed | 0 |
| mc1165-exact12 | 211 passed, 13 skipped | 0 |
| r8-audit | 222 passed | 0 |
| merge-receiver | 92 passed | 0 |
| pyright | 15 errors | 1 |
| pyright-owned | 0 errors | 0 |
| pyright-r8 | 0 errors | 0 |
| pyright-r9-owned | 0 errors | 0 |
| pyright-r10 | 0 errors | 0 |
| permanent-r11 | 18 passed | 0 |
| pyright-r11 | 0 errors | 0 |
| spawn-refusal | 2 passed | 0 |
| reused-initial | 4 passed | 0 |
| permanent-r12 | 29 passed | 0 |
| pyright-r12 | 0 errors | 0 |
| spawn-factory | 95 passed | 0 |

No live provider, deployment, POSIX or crash-during-storage-failure certification
is implied. The dated journal and local handoff retain exact commands, hashes
and raw evidence. Earlier verification below refers to earlier candidates.

Round 11 carries initial Claude reservations through postlaunch enrichment and
reserves queued replay before spawning. Round 10 extends postlaunch token
rollback through self-start callback rearming.
Round 9 closes the self-start races in round 8's durable ownership guards. `mc/delivery_attempt.py`
mints a UUID at capture, before spawn can start an acknowledgment writer.
It claims the existing durable session row inside the manager -> log-leaf
transaction. Storage failure propagates before spawn. The captured token is
bound to delivery errors; no failure-time live registry or delegation-turn
comparison determines row ownership.

Both Claude revival modes claim and carry a token, including Mode A's argv
delivery, so a successful Mode A successor cannot become an untagged legacy
row on completion. Both Codex first-turn and follow-up capture before spawn.

Every new-delivery pending-row upsert takes the row with a fresh token,
including a non-delegated continuation whose `delegation_turn` remains 1.
Identity-only native-thread backfills require and preserve the token. Completion
compares it against the newest session row before replacing or inserting any
result. A stale completion returns without inserting an older result at the
front, including when the newer attempt has already failed. Completion copies
its token into the replacement durable row; tab closure removes only the registry
object and cannot erase it. UUID claim and pending upsert serialize under the
same manager -> log-leaf lock order as failure recording. No acknowledgment
or process wait was added inside those locks.

Initial delegated Claude dispatch keeps the strict pending save before Popen.
Its temporary reservation dictionary owns the token; both published Mode A
and Mode B sessions receive that exact token after metadata hydration. Their
postlaunch pending write enriches history using the reserved `DeliveryAttempt`
instead of minting another owner. A failed enrichment logs and retains the
saved owner, so reader completion can commit once storage recovers. Merely
rolling back a token on a fresh session would not recover the temporary
dictionary's owner. Standalone initial dispatch retains its existing best-effort
history behavior; when no row exists, completion can insert one with an identity.

Queue replay resolves its isolated cwd, then snapshots/reserves the next
turn through the shared transaction under the existing project/lifecycle locks.
A refused save preserves the prior live owner and flags. A proven spawn refusal
restores only its own reserved row. Both keep input at the queue front and clean
the temporary prompt. A consumed delegation number is not reused. Registration,
running state and reader startup contain no later strict pending save. The
reader clears `_dispatching_followup` in `finally`. Ambiguous launch never
restores a retryable completed row or requeues that attempt.

Fenn's r10 probes are retained unchanged in `tests/mc1168_r11/`:

- `test_initial_pending.py`: SHA256 `c6e61494378af3a343aa0e2a3b2cd087b8775b890584753fb80d0163a15b72ab`.
- `test_reader_pending.py`: SHA256 `a1533204f1f61d457a637628bb1654630a104cfaf8d774b95fd321d0a068b254`.

Git treats those two files as exact bytes. Additional refusal and single-mint
controls live separately in `tests/test_runtime_delivery_truth_r11.py`.

### Round-11 pending-writer audit

The complete direct `_log_agent_dispatch_pending` call inventory and its
indirect turn-helper paths were inspected at the candidate. These are code
ordering verdicts, supported by the reviewer probes and regression selections;
they do not certify live provider behavior.

| Call site | Ownership boundary and verdict |
| --- | --- |
| `_note_claude_sid` | Postlaunch, `identity_only=True`; compares/preserves the owner, no mint. Unchanged injected-save-failure control passes. |
| `_note_self_started_turn` initial pending write | Already executing; idle/live/durable revalidation and failed-save token rollback under manager lock; reader continues. r9 probes cover it. |
| `_note_self_started_turn` callback rearm | Already executing; `postlaunch=True` reaches strict pending write through both turn helpers; rollback occurs before the existing catch. r10 probe covers both save boundaries. |
| `_auto_dispatch_followup_locked` | Fixed in r11: staged strict reservation before spawn; refusal retains queued message and prior live state. No postspawn pending write remains. |
| `_dispatch_via_runtime` | Strict delegated pending reservation before `runtime.dispatch`; INIT uses `_runtime_note_init` with owner-checked metadata only. No postspawn mint. |
| `_dispatch_agent_internal` temporary pending dictionary | Strict delegated save before either Claude spawn. r11 preserves that dictionary's owner in the real session. |
| `_dispatch_agent_internal` Mode B enrichment | Fixed in r11: postlaunch write passes the prelaunch attempt; no second mint. Storage failure keeps that owner and reader setup proceeds. |
| `_dispatch_agent_internal` Mode A enrichment | Same r11 fix as Mode B; unchanged probe exercises actual Mode A completion. |
| `agent_followup._start_new_turn` | Native runtime call, Claude dead-child/model-switch spawn, Mode A spawn, and live Mode B stdin delivery all follow strict turn reservation. A live process exists in the last case, but the new input has not been submitted. Not postsubmission bookkeeping. |
| `agent_interrupt._start_new_turn` | Native write/respawn and Claude replacement spawn occur later; strict prelaunch reservation. Claude sets `_interrupting` before reservation and restores it on refusal. |
| `scheduler_routes._scheduled_continue` | Captures/publishes pending with the same explicit attempt before stdin delivery. A live child alone does not make this a postsubmission save. |

Other direct `pending_fields` users were also checked: completion mints only
for a new row with no destination; model-pin metadata mints only when creating
a previously absent row; Hivemind lineage/reviewer/integration records reserve
before worker dispatch. None replaces a successful prelaunch reservation with
a second postlaunch owner. No change to callback gates, provider timeouts or
startup retry policy is included. Fenn's later r11 review established that
spawn refusal after a successful queue reservation strands an overwritten
pending row; that regression was reproduced at the investigation checkpoint and is addressed by the round-12 transaction above.
`agent_log_file.claim_rows` assigns identities only to untagged legacy rows;
its merge path mints only for a new synthesized transcript without a managed
session. `resume_cwd.refusal` mints only for a new refusal row and preserves
the identity of an existing row. These are maintenance/refusal writers rather
than postlaunch delivery replacements.

Round-11 verification through the installation test launcher: unchanged
initial6 and reader5, permanent18 (the original11 plus seven new controls),
r10-rearm5, self-start50, independent9, focused50, runtime592/skip1,
resume54, API-credits161, scheduler195, MC-1165 isolation211/skip13,
writer-audit222 and receiver92 all exit 0. Counts overlap. All four recorded
owned/r8/r9/r10 Pyright selections and the new r11 controls have zero errors,
exit 0. Core retains 15 errors, exit 1: normalized additions0/removals0 against
the retained Fenn r9 baseline, not a regenerated baseline. No live-provider
or deployment verification is claimed.

`record_failure` finds the session row and compares its `delivery_attempt_id`
with the captured UUID inside that same transaction. A different token logs
a skip, even after registry cleanup or a changed trigger. An absent or empty
destination token refuses the update: missing identity is not authorization.
Legacy revival claims the row before executing. An absent row
is inserted as before. Ordinary rollback records its own failure because the
durable claim survives restoration/removal of the failed live session. A
failure lacking a captured attempt still logs a skip. Run Now stays 502 with
`ok=false`/`delivered=false`; cadence retains `last_error` and consumes the slot
without fallback or retry.

`tests/test_closed_successor.py` is Fenn's round-6 probe, copied byte for byte
from the main review folder. `tests/test_runtime_delivery_truth_r7.py` adds
actual JSON-log reload after real completion, repeated capture with turn 1,
identity-backfill preservation, unowned/own-row and restored/absent-registry
controls, takeover inside the leaf transaction, changed-trigger protection,
and storage failure before Claude/Codex spawn. Round 8 restores untagged rows
in the round-6 durable-preservation and reused-predecessor controls, and inverts
round 7's unsafe legacy-overwrite expectation. The
round-4 lock probe distinguishes the new short prelaunch claim transaction
from deferred failure cleanup, which must still run outside publication locks.
Processes/pipes remain doubles; no live provider or deployment is certified.
The shared MC-1165 revival fixture pairs its existing in-memory log reader
with a matching writer for the new durable claim; its isolation assertions
and production persistence requirements remain intact.

Live scheduled Mode B appends capture a new `DeliveryAttempt`, then publish the
pending task using that same token before starting stdin delivery. Its deferred
write failure keeps the captured token and original process. No manager/log
lock is acquired while the stdin lock is held. Self-started Mode B turns capture
the previous attempt before observing idle. Pending-entry preparation stays
outside the manager lock; at claim time, the status must still be idle, no
question may be waiting, and the captured live token must still match. The
durable row must also belong to that token. Only then can the writer mint a
fresh pending identity and mark the session running, in the same manager-lock
critical section. A delayed wake loses without changing a newer delivery's
row, task, status or callback state, even if that delivery already returned to
idle. No UUID is minted for a refused claim.

This wake is already executing: a pending-history save failure is logged and
does not escape the live reader. The live token is restored to its prior saved
owner while still holding the manager lock, the session is marked running,
and stdout continues draining. Strict prelaunch persistence still refuses a
launch on failure. Incognito and housekeeping wakes update liveness without
minting tokens or writing history. Fenn's two unchanged real-reader probes live
in `tests/mc1168_r9/test_self_start.py`; round-9 controls additionally cover
token rollback, ownership before the first idle read, completed intervening
deliveries, question/running transitions, manager-lock atomicity, unowned rows,
prelaunch refusal and history exclusions. Provider processes/pipes are doubles.

Callback rearming also writes pending history after allocating the next
delegation turn. The self-start caller explicitly passes `postlaunch=True`
through `_rearm_notify_for_new_turn` and `_advance_delegation_turn` to the
pending writer. If this second save fails, its newly minted token is restored
to the immediately preceding live/saved owner under the manager lock before
the exception reaches the existing reader-side rearm catch. This works when
only the rearm save fails and when both self-start saves fail: once storage
recovers, completion still owns the durable row and records the new answer.
The cleared callback latch and allocated turn keep their existing rearm behavior; the shared round-12 running policy restores only the failed token.
Ordinary API continuations use the default prelaunch policy and still raise
on a strict persistence failure before execution begins.

`tests/mc1168_r10/test_rearm_claim.py` retains Fenn's five-case round-9 review
probe byte-for-byte: successful rearm, rearm-only failure, both saves failing,
competing self-start claims and a manager-lock waiter overtaken by a completed
newer delivery. Its SHA256 is
`dedfa0bdf18fe70169658de806792a037855ad61a4979310c9be8d90dc1e91d6`;
the scoped `.gitattributes` entry preserves these bytes on checkout. Both
failure cases failed before the fix; the unchanged five cases pass afterward.
No pre-existing test assertions were changed. The monolith edits extend the
existing ownership policy across its existing rearm helpers; no new feature
body, module or dependency is added.

Round-10 validation used the installation's `tools/test-run.py`, the project
interpreter, the task worktree cwd and unchanged r9 file order/flags:

| Selection | Passed | Skipped | Exit |
| --- | ---: | ---: | ---: |
| Unchanged round-9 rearm probe (permanent copy) | 5 | 0 | 0 |
| Round-9 self-start/wake selection | 50 | 0 | 0 |
| Independent prior-review probes | 9 | 0 | 0 |
| Focused round-4 through round-7 and prior-review probes | 50 | 0 | 0 |
| Permanent runtime17 | 592 | 1 | 0 |
| Resume-cwd / API credits / scheduler | 54 / 161 / 195 | 0 | 0 each |
| Exact twelve MC-1165 isolation files | 211 | 13 | 0 |
| Round-8 writer audit | 222 | 0 | 0 |
| Integrated receiver | 92 | 0 | 0 |

Counts overlap. Core pyright retains the same 15 pre-existing route errors
as Fenn's r9 artifact (exit 1), with zero added or removed diagnostic
file/rule/message/severity multiplicities. Earlier owned checks and the new
immutable r10 regression each report zero errors (exit 0). Reporting emitted
transport/enrollment/finalization gaps; actual framework output and child exits
establish these results. No live provider, outbound callback, authenticated
conversation, paid billing, POSIX locking, restart or deployment was tested.
This is a local branch candidate, with no merge or push.

Hivemind lineage/reviewer pending writers
claim fresh IDs; the integration-review task update requires the existing ID.

`mc/agent_log_maintenance.py` owns startup log concurrency. It claims untagged
legacy records under the existing transaction, then returns a frozen snapshot.
Transcript/Scribe work happens outside the locks. Commit requires the snapshot
token and unchanged row contents, so neither a new claim nor a same-attempt
completion can be undone by a delayed maintenance pass. Transcript synthesis
inserts a newly tagged, sessionless row only if its native ID is still absent.
Provider migration, transcript backfill, Scribe reconciliation, telemetry
backfill and pending interruption all use this path rather than saving an old
whole-log copy. Human-joined flags use the same snapshot guard; model selection
checks its captured row ID. Existing-row resume-refusal edits only append
request metadata; newly created refusal rows receive a fresh ID.

Round-8 regressions cover untagged completion, stale native-init/pending-init/
start-failure callbacks, callback-token capture before leaf acquisition, same-dictionary completion takeover, startup takeover,
same-attempt completion, removed rows, legacy maintenance followed by revival,
duplicate transcript imports, live scheduler pipe failure and superseded review
task updates. Fenn's original scratch probes remain unchanged and run separately.

### Run-row writer inventory (round 8)

Line numbers are for the round-8 candidate. A claim intentionally changes the
owner before delivery; every subsequent runtime write must match that ID.

| Writer | Ownership evidence inside the log transaction |
| --- | --- |
| `mc/delivery_attempt.py:50` prelaunch claim | Explicit new attempt claims the current session row before launch; returned immutable ID travels with rejection. |
| `mc/blueprints/agent_routes.py:8941` pending upsert | New delivery mints/stamps a fresh ID; an explicit captured attempt must match the destination; identity-only updates require `owns_row`. |
| `mc/blueprints/agent_routes.py:10395` completion | Newest session row must carry this nonempty attempt ID before any replacement or front insertion; new standalone rows receive an ID. |
| `mc/blueprints/agent_routes.py:10509` Scribe marker | Session ID, completion timestamp and attempt ID must all match. |
| `mc/blueprints/agent_routes.py:10626` native INIT | Captured nonempty attempt ID and session ID must match; values are captured before waiting for the leaf. |
| `mc/blueprints/agent_routes.py:10641` start failure | Captured attempt ID, session ID and pending status must match; deferred errors keep their captured attempt. |
| `mc/schedule_delivery_failure.py:28` delivery failure | Captured `DeliveryAttempt.owns_row`; missing/empty destination IDs refuse; an absent destination is created with the captured ID. |
| `mc/hivemind_lineage.py:68` worker pending/recovery | Explicit prelaunch claim mints/stamps a fresh ID before insertion or recovery update. |
| `mc/hivemind_reviewer.py:197` reviewer pending | Existing session ID is refused; new row and session share a freshly claimed ID. |
| `mc/hivemind_integration_cycle.py:222` frozen review task | Captured `DeliveryAttempt.owns_row(row)` before replacing task or launching. |
| `mc/agent_log_file.py:61,68` migration/maintenance | Claims untagged rows before work; commit requires snapshot ID plus complete unchanged row; new synthesized native ID must still be absent. |
| `server.py:1391` provider migration | Maintenance snapshot/commit; no whole-log replacement. |
| `server.py:1500` transcript synthesis | Maintenance snapshot/commit; absent native ID and fresh insertion ID. |
| `server.py:1579,1656` Scribe baseline/reconciliation | Maintenance snapshot/commit. |
| `server.py:1706` telemetry backfill | Maintenance snapshot/commit. |
| `server.py:1839` startup pending interruption | Maintenance snapshot/commit; adopted sessions remain excluded. |
| `mc/blueprints/agent_routes.py:7487,7494` human-joined flag | Maintenance snapshot ID and unchanged-row check. |
| `mc/blueprints/agent_routes.py:13550` model selection | Captured current row ID must match; absent-row creation claims a fresh ID before writing task/status. |
| `mc/blueprints/agent_routes.py:15942,15954` plan-reference cleanup | Maintenance snapshot/commit; no stale whole-log replacement. |
| `tools/backfill-human-joined.py:122,180` offline human-joined repair | Shared file transaction; snapshot ID and unchanged row required. Dry run writes nothing. |
| `tools/repair-agent-log-personas.py:60,95` offline persona repair | Shared file transaction; snapshot ID and unchanged row required. Dry run writes nothing. |
| `mc/resume_cwd.py:152` cwd refusal | New error-row creation claims an ID; existing-row path only appends refusal metadata, preserving all run fields and ownership. |

The disabled `attend_session` body at `agent_routes.py:7090` is unreachable
(unconditional HTTP 410), modifies only trigger metadata, and remains disabled.
`_update_agent_log`/`_save_agent_log` are transaction/storage primitives; all
production semantic callers are enumerated above. Grep covered these symbols,
direct `_agent_log.json` writes, and status/summary/task/token mutations across
`server.py`, `mc/`, and standalone repair tools. Live scheduler append and self-started-turn entry points
route through the pending writer; Claude revival and both Codex launch paths
retain their prelaunch capture. No human-only gate changes.

`mc/agent_log_file.py:20` also serializes the server transaction and both offline
repair tools across processes. It reuses the existing OS byte-lock pattern
(Windows `msvcrt`, POSIX `fcntl`), with thread reentrancy and a bounded ten-second
lock-acquisition budget. The per-log `*_agent_log.lock` sidecar is ignored and
is not JSON, so project discovery cannot ingest it. No acknowledgment, provider
wait, transcript read or Scribe call holds the file lock. Storage errors propagate
through existing failure handling. There is no new dependency. A separate
Windows two-process probe verifies contention followed by refusal of the stale
snapshot, in addition to the permanent server/offline-tool takeover tests.

Final round-7 stable-source checks, through the installation test launcher:

| Selection | Passed | Skipped | Exit |
| --- | ---: | ---: | ---: |
| Unchanged r5/closed probes + r4/r5/r6/r7 | 50 | 0 | 0 |
| Fenn's permanent runtime17 | 592 | 1 | 0 |
| Five resume-cwd files | 54 | 0 | 0 |
| Six API-credits files | 161 | 0 | 0 |
| Six scheduler files | 195 | 0 | 0 |
| Exact twelve MC-1165 isolation files | 211 | 13 | 0 |

Counts overlap. Full ten-module pyright exits 1 with the same 15 existing
route diagnostics as Fenn's exact `edc02876` artifact, zero new/removed by
normalized file/rule/message/severity multiplicities; baseline not rerun.
Owned-module and edited runtime-test pyright exits 0 with zero errors.
Initial isolation fixture failures, its recovery, the final Mode A hook
regression, exact argv and all final results are retained in the main journal
and scratch handoff. Launcher progress telemetry had HTTP/enrollment gaps;
framework stdout and child exits establish counts. No live timing,
authenticated conversation, paid billing or deployment verification.

The following round-6 design and validation describe the superseded snapshot
predicate and are retained as historical evidence.

Round 6 integrates exact master `2ffe19ad` and repairs stale scheduled-failure
persistence. `mc/delivery_attempt.py` captures the attempted session object,
process and delegation turn before acknowledgment can wait, plus the predecessor
that Claude rollback may restore. Claude revival and both Codex launch paths
bind this immutable snapshot to their delivery exception. Neither scheduler
caller infers ownership from the replacement session at failure time.

`record_failure` takes the project lock, checks the captured owner (including
unchanged predecessor restoration), then uses the serialized log transaction.
The transaction refuses to update a row with a later delegation turn. Manager
then log remains the lock order; no acknowledgment or process wait occurs in
this compare-and-set. Replaced process, replaced registry object, reused
predecessor or newer durable turn causes a logged skip. An exception without
captured attempt identity also logs a skip rather than guessing. The original
Run Now still returns HTTP 502; cadence still records the error and consumes
the slot. Dave's pick (c), no automatic fallback or retry, remains intact.

`tests/test_runtime_delivery_truth_r6.py` ports Fenn's two replacement probes
and covers cadence and Run Now, same-turn replacements, takeover between
rollback and recording, newer durable rows after owner cleanup, absent attempt
identity and predecessor reuse. A real Codex acknowledgment regression checks
that capture uses its actual spawned process even if the session field already
points at a successor. The two synthetic failures in the round-4
fixtures now supply the same ownership snapshot as real delivery exceptions;
the real failure-history and no-replay assertions are retained. No scheduler
route policy changed. Round 6 validation is recorded below.

Round-6 stable-source checks after exact master integration, all through the
installation test launcher:

| Selection | Passed | Failed | Skipped | Exit |
| --- | ---: | ---: | ---: | ---: |
| Fenn's unchanged replacement probes | 2 | 0 | 0 | 0 |
| Round 4 + round 5 + round 6 regressions | 31 | 0 | 0 | 0 |
| Exact requested 18-file runtime selection | 593 | 1 | 1 | 1 |
| All five resume-cwd files | 54 | 0 | 0 | 0 |
| All six API-credits files | 161 | 0 | 0 | 0 |
| Six scheduler files | 195 | 0 | 0 | 0 |
| Exact 12-file MC-1165 selection | 211 | 0 | 13 | 0 |

Counts overlap. Round 6 contributes 15 permanent tests. The exact runtime
selection retains the original contradictory scratch retry assertion at line
109, unchanged: it requires two spawns before requiring one spawn at line 115.
The sole failure remains that assertion; the command is red, with no landing
gate waiver. Pyright on ten production modules (including the new module)
exits 1 with 15 existing agent-route diagnostics. Comparison by normalized
file, rule, message, severity and multiplicity against Fenn's exact round-5
artifact gives zero new and zero removed diagnostics; that baseline was not
rerun. The other nine modules are clean. Telemetry gaps were printed; counts
and exits derive from child output. Exact commands, outputs and timing are
retained in the main-checkout journal and scratch handoff report. No live
provider, authenticated conversation, paid billing, deployment, master merge,
push or restart was exercised.

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
  conversation row `error` with the delivery error in its summary if it is
  still owned by the failed attempt. A superseding owner or newer durable turn
  preserves its row and produces a logged skip. Prior
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
