# Usage-limit continuation (MC-1163)

An unattended run that ends on an observed provider usage limit with a known
reset timestamp waits for the reset plus two minutes, then continues the same
session through the existing follow-up handler. It makes at most two automatic
continuations per task chain. A third exhaustion ends through normal completion.
This waits for a provider's scheduled reset; it never redeems an account reset.

`limit_resume_enabled` defaults to `true` and is accepted by the existing config
settings allowlist. Turning it off prevents new waits and automatic submissions;
already-persisted waits remain paused until enabled or cancelled. The scheduler
master pause also blocks submissions. The scheduler checks every 30 seconds, so
submission may occur up to one tick after the reset-plus-two-minute deadline.

## Eligibility and lifecycle

- A session must have `source=agent|schedule` or an unattended schedule/steward/
  dispatch trigger. Incognito, human-owned turns, stopped/cancelled sessions,
  questions and approval waits are excluded. Workflow children use `source=agent`.
- Only an exhaustion observed on that session can arm a wait. A global provider
  exhaustion record alone cannot. Invalid, missing or timezone-less reset times
  preserve existing terminal behavior. Allowed/advisory rate events do not arm it.
- Claude's two readers capture the normalized rejected/exceeded event. Native
  readers capture `ALLOWANCE_EXHAUSTED`; Codex's exit-error fallback does too.
- A parked session has `status=waiting_for_reset`. Completion logging, worktree
  merge-back and parent/workflow completion notifications are held. Guardian
  recovery and stale-session cleanup leave it parked. Workflow boot adoption and
  reconciliation treat the durable wait as in flight.
- A human message cancels the previous wait even if the new send is refused for
  allowance. Normal new turns clear retry bookkeeping; an internal reset
  continuation keeps it and keeps the current turn owner. Trigger/fence identity
  is unchanged. Stop, closing the chat tab and the explicit cancel action cancel
  the pending wait durably.

## State and recovery

`mc/limit_resume.py` owns policy and the atomic store at `data/limit_resume.json`,
outside `data/projects/` and gitignored. Writes use the existing fsync/replace
helper and publish in-memory state only after the disk write succeeds. Unreadable
state disables this feature for that process and is logged without overwriting
the file. A failed initial park falls through to existing terminal handling.
An OS lock in the sibling `limit_resume.lock` serializes transactions across the
brief old/new server overlap at restart. Each transaction reloads state while
holding that lock, preventing stale attempt claims and cancellation overwrites.
The OS releases the lock if the process crashes; no stale-file deletion is needed.

Each record contains `session_id`, `project_id`, `resets_at`, `resume_at`,
`attempts`, `phase` and an allowlisted session snapshot. The snapshot preserves
the provider conversation, model/effort, persona, dispatch callbacks, worktree,
roster prompt references, task and visible log. It excludes process/lock objects
and cached system prompts. Recovery restores a paused session without launching
a provider. The existing follow-up code recreates its runtime and process
registration when due. Deleted projects are never recreated.

The store claims `phase=submitting` and increments the attempt count *before*
provider submission. Accepted submissions become `active`; another exhaustion
re-parks with the same count. This is a conservative at-most-once submission
boundary: a crash after claim and before a definite acknowledgment leaves
submission uncertain. On restart it is shown as **Reset continuation needs
review**, never automatically replayed. The user can continue manually or cancel.
This avoids duplicate paid work in the crash window. Attempt bookkeeping remains
until a new normal turn, so an intermediate successful turn or background-task
wake cannot replenish the task chain's two-continuation budget.

`mc/limit_resume_dispatch.py` owns the scheduler adapter and cancel blueprint.
Its private request-environment marker preserves ownership and retry count when
calling the existing follow-up handler; an external JSON field cannot select it.
The attempt cap survives a server restart and is not a global provider counter.

## API and Floor

Session status responses and Floor figures include `limit_resume` (or null):
`{resume_at, resets_at, attempts, phase}`. Floor keeps parked figures visible,
formats the resume deadline in the viewing browser's local timezone, and offers
**Cancel resume**. No additional poller or SSE connection is opened.

`POST /api/project/<project_id>/agent/<session_id>/limit-resume/cancel` cancels a
matching pending wait and completes it as stopped. Unknown/mismatched waits
return 404; unavailable state returns 503. An already-running continuation returns
409 and can be ended with normal Stop. Cancelling a paused session does not
terminate another process.

## Validation

Branch validation on 2026-11-07: the selected backend suite reported
`373 passed in 15.31s` (exit 0); pyright on both new modules reported
`0 errors, 0 warnings, 0 informations` (exit 0). The reset UI smoke, inline-handler
scope check (278 modules) and seven-scenario boot smoke with its UI guards passed.
The launcher printed telemetry queue/transport/enrollment incompleteness notices;
these do not change child exit results, and complete dashboard progress capture
is not claimed.

Use the project interpreter and reported-test launcher. From the repository root:

```powershell
python tools/test-run.py --project mission_control --port 5199 -- python -m pytest tests/test_limit_resume.py tests/test_workflow_runtime_notify.py tests/test_runtime_completion_log.py tests/test_allowance_state.py tests/test_codex_run_error.py tests/test_floor_routes.py tests/test_human_joined_scheduled_chat.py tests/test_scheduler_routes.py tests/test_stream_reader_malformed.py tests/test_revive_notify_carry.py tests/test_hivemind_roster_worker.py -o addopts=-ra -q
python tools/test-run.py --project mission_control --port 5199 -- pyright mc/limit_resume.py mc/limit_resume_dispatch.py
```

From `tools/smoke/`, with the main checkout's existing dependencies linked into
the worktree (do not install another copy):

```powershell
python ../test-run.py --project mission_control --port 5199 -- node limit-resume.mjs
python ../test-run.py --project mission_control --port 5199 -- node boot-smoke.mjs
python ../test-run.py --project mission_control --port 5199 -- node inline-handler-scope-check.mjs
```

The dedicated UI smoke loads this worktree's real SPA with fixture endpoints and
checks the local deadline, cancellation request, refresh and click propagation.
Backend tests exercise both real Claude readers with synthetic rejected-limit
streams and the real native follow-up handler with a stubbed provider. A real
vendor limit/reset, production restart recovery and live continuation are not
exercised: this branch is not merged, pushed, restarted or deployed.
