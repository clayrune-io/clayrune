# MC-1166 Unit 6: continuation acknowledgment policy

Status: implementation paused for Dave's decision, per the dispatched ticket.
Base: `9f73231fdf538fd543735ca042a71a5f870e9155`.
Branch: `clayrune/mc1166-u6-wake`.
This checkpoint adds a decision handoff and two store-contract cases only.
Unit 6 is not implemented or enabled.

## Decision needed

What acknowledgment policy should **mail-origin** Claude revival use when the
existing follow-up route waits for a slow or stuck stdin writer?

**A (recommended): bounded mail-origin acknowledgment.** Reuse the existing
durable-follow-up 10-second acknowledgment window. If delivery may have started
but acknowledgment is missing, retain the mail event as visible `uncertain`;
never replay automatically, consume a question, or kill/restart a process just
because the mail wait elapsed. Persist the event-to-delivery-attempt association
before submission. A later positive acknowledgment may reconcile that exact
attempt; a matching terminal callback alone is not proof of prompt delivery.
Ordinary interactive follow-up/revival keeps its current wait policy.

Cost: a mail-scoped acknowledgment seam in the existing revive path, durable
late-receipt reconciliation, and tests for timeout/late acceptance/revocation.
Startup/context construction time needs to be distinguished from the pipe-write
acknowledgment window; a 10-second acknowledgment window is not a total follow-up
deadline. It must not be reported as one.

**B: retain indefinite revival acknowledgment and renew the mail lease while
waiting.** Require a tracked lease owner/heartbeat and stop renewing on loss of
consent or ownership. A possible write is still uncertain after a crash. No
automatic replay is allowed.

Cost: a renewable waiting lifecycle, cleanup/ownership tests and a status surface
for an indefinitely pending writer. This preserves slow startup behavior but
can leave the mail delivery worker waiting without a completion bound.

These alternatives change the recovery and waiting behavior of a real provider
submission. The builder has not selected either. The existing mail lease must
not simply be increased to conceal the mismatch, and the ordinary route must
not be changed globally as a side effect of this unit.

## Evidence on the exact base

- `mc/blueprints/agent_routes.py:8180`: `_acknowledge_revival()` calls
  `delivery.done.wait()` without a timeout. It is deferred through the existing
  publication boundary, so returning from the inner handler is not acceptance.
- `mc/blueprints/agent_routes.py:14398`: live Mode B durable follow-up already
  uses `wait_for_ack=10.0`. This is the precedent for option A, not authorization
  to change the distinct revival policy.
- `mc/agent_mail_store.py:140`: the default submission lease is 30 seconds.
- `mc/agent_mail_store.py:695`: reconciliation of an expired `submitting` lease
  makes it `uncertain` and removes its fence; no automatic retry occurs.
- `mc/agent_mail_store.py:861`: `mark_submitted()` requires `submitting` plus
  its matching fence. After the transition above, late success needs the
  dedicated `reconcile_uncertain()` path and positive evidence.
- `mc/agent_runtime.py:5530`: non-Claude prompt acknowledgment has its own
  existing bound. Its timeout behavior cannot be inferred from Claude revival.

The synthetic store probe exercises the actual SQLite store and fake time:
claim a ready event, cross the lease boundary, reconcile, and attempt a late
positive submission. Expected: `uncertain`, no reclaim, late `mark_submitted`
refused, explicit positive-evidence reconciliation succeeds, and only the
matching turn can complete. It does not call a provider, create a process,
send mail, or establish a live hang or provider failure.

## Verification

From the assigned worktree, using the MAIN project interpreter and installation
launcher:

```powershell
C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe C:/Users/levir/Documents/_claude/mission-control/tools/test-run.py --project mission_control --port 5199 -- C:/Users/levir/Documents/_claude/mission-control/.venv/Scripts/python.exe -m pytest tests/test_agent_mail_wake_contract.py tests/test_agent_mail_store.py -q
```

Exit 0: both new cases (ordinary/startup reconciliation) and the 59 existing
store cases pass. The runner's quiet output is a complete 61-dot progress line
and `[100%]`; it does not print a framework summary/count line in this setup.
The initial launcher call used the relative `.venv/Scripts/python.exe` as child
argv and failed before starting the child (`child_start_failed_FileNotFoundError`,
`pytest_reporter_finish_missing_progress_incomplete`). The corrected call uses
absolute interpreter paths, the same worktree cwd and the same test selection;
no test assertions or permissions changed.

No new production modules remain, so new-module pyright is not applicable.
No static files changed, so frontend smoke is not applicable. No runtime change
is claimed; route/provider continuation, resumed text consumption, callback/gate
behavior, real mail, restart and live integration are not verified. This is a
store-contract test and decision checkpoint, not a Unit 6 acceptance result.
README, USER_GUIDE, CHANGELOG, AGENTS and rules need no changes for this
unimplemented internal integration; the dedicated handoff and journal carry it.

## Remaining implementation plan

1. Add immutable local snapshots of project generation, provider/native
   conversation identity, persona/worktree/continuation context, and the full
   question plus revision. Units 1-5 currently bind only project/session and
   owner generation. Missing pre-send snapshots must hold; do not backfill them
   from current state or a message-supplied session id.
2. Claim only an accepted laundered ready event. Enter the **existing** follow-up
   route using an internal Python-authenticated envelope and exact-conversation
   constraint. Check identity/current consent/holds before any provider action;
   refuse cold, auto-fresh and unproven successor paths before submission.
3. Keep the existing callback ownership semantics. Mail-origin calls must not
   re-arm the spawner/workflow notification. Serialize busy replies in the
   durable inbox rather than an in-memory route queue.
4. Implement the selected acknowledgment policy and persist the real
   `delivery_attempt_id` before starting the provider action. An HTTP success,
   background-thread enqueue, process start or completion of an unrelated
   attempt is insufficient. Reconcile matching positive receipts after crashes;
   otherwise retain `uncertain`, with no blind retry.
5. Preserve plan gates. Consume only a matching stored question/revision after
   proven delivery; preserve multi-question boundaries and multi-select meaning.
   Keep free text intact. Notes, invalid choices and late replies cannot consume
   another pending question or relabel text as an approval.
6. Test live/ended routes with fake transports for Claude and each affected
   provider, exact history/identity, busy/replay/revocation races, late receipts,
   held events and callbacks. Demonstrate the resumed turn reads the delivered
   text. Use the installation test launcher and pyright on new modules.
7. Update the internal contract/changelog and commit explicit session paths.
   Keep disabled. No merge, push, restart, deployment, real mail or credentials.

## Session notes

Memory/API skills were read and the matching memory topics inspected. The first
main-checkout journal read was truncated and was followed by targeted reads of
the recent decisions and exact source; a complete journal read is not claimed.
The plan-file write under the human-owned `.claude/plans` directory was rejected
by the unattended fence and was not retried. The plan is preserved above and in
the journal. No permission changes were made.

Two exploratory new modules and the associated schema-version edit were removed
before this checkpoint; the production-source diff from the base is empty. No
partial continuation API or schema migration is being handed off as working code.
