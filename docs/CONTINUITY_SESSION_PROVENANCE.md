# Continuity session provenance (MC-1146)

Continuity records carry claims from earlier sessions. Rendering must not turn
those claims into live operational facts. This change is a branch candidate;
the live server has not been restarted or exercised with it.

## Contract

- `mc/continuity_liveness.py` checks explicit 12-hex agent session references,
  parenthesized session references and `clayrune/agent/<id>` branches, including
  `hm_<8 hex>` worker ids. Registry project membership and running/idle statuses
  match the Floor's source. A running session with an exited child is dead;
  idle sessions may legitimately have no running child between turns.
- Explicit branch names are verified as exact local `refs/heads/` refs using
  read-only git, bounded to five seconds per lookup. Legacy `on branch (<id>)`
  uses the established `clayrune/agent/<id>` convention. Branches are checked
  only when a session and a branch are named. No branch is inferred otherwise.
- Dead references render `LOST IN RESTART: <text> (session <id> not running;
  branch absent)` when both facts hold. A surviving branch says `branch exists`.
  A live session with an absent branch renders `STATE MISMATCH`; a failed
  branch/liveness probe renders `UNVERIFIED` and logs the failure. Age remains
  visible separately. Own, shared and other-owner thread views use the same
  checks. Rendering does not edit, delete or reconfirm stored continuity.
- `mc/session_provenance.py` records the actual prompt origin in process-local
  state, keyed by project and MC session id. New user chats, resumed saved
  chats, dispatched sessions and auto-fresh rollovers are distinguished. The
  first rendered conversation for each project since server start/restart is
  labelled explicitly. This is not a claim about why the server started.
- Actual handoff creation marks rollover before prompt rebuilds; fresh dispatch
  transfers that mark to its planned MC id, and cold revival marks before its
  earlier context build. Repeated rebuilds retain the origin; a real later
  rollover replaces it with the immediate predecessor's native session id.
- The origin is one plain line inside continuity, including when there is no
  saved working state. Existing incognito/task-shape/continuity-toggle gates
  still apply. A caller with no MC session id says origin unavailable.
- Recent activity identifies the current or other/predecessor session when
  `session_id` exists. Legacy project events have no owner id, so they say
  `project history; session unknown, may predate this chat`. Ownership is never
  invented from event wording or timestamps. The feed is not rewritten.

The integration in `mc/memory.py` and `mc/blueprints/agent_routes.py` is limited
to rendering and lifecycle hooks; new policy lives in independent modules.
`mc/agent_runtime.py` only recognizes the `Recent activity:` section boundary
when slimming prompts; that boundary is retained. `project_routes.py` uses
activity for a project-summary transform, not session continuity, so it needs
no change. No new dependencies, schema or configuration keys were added.

## Verification

Exact commands, counts and limitations are also recorded in the task journal
`docs/_journal/af7b53e4-continuity-stale-state.md` (gitignored; copy the journal
from the implementation worktree when landing).

```powershell
python -m pytest -o addopts='' -q tests/test_continuity_liveness.py tests/test_continuity.py tests/test_continuity_owner.py tests/test_continuity_regression.py tests/test_context_profile_by_job_shape.py tests/test_context_profile_by_model_tier.py tests/test_context_freshness.py tests/test_auto_fresh_live_process.py tests/test_context_rollover_trigger.py tests/test_context_rollover_live_followup.py tests/test_midturn_rollover.py tests/test_conversation_rollover_lineage.py tests/test_continuation_model.py
pyright mc/continuity_liveness.py mc/session_provenance.py
python -m pytest -o addopts='' -q tests/test_effort_continuity.py tests/test_rollover_threshold.py tests/test_rollover_link_full_buffer.py
```

- Final regression selection: 274 passed in 23.86 seconds, no warnings. Earlier
  runs emitted an intermittent `_Proc` test-double warning (`wait()` missing)
  in a rollover reader thread; it was not reproduced in the final run.
- Additional effort/threshold/full-buffer continuity selection: 67 passed in
  1.87 seconds. Total across the two final selections: 341 passed.
- New modules: pyright basic, zero errors/warnings/informations.
- Real temporary git repository exercises existing and absent branch refs;
  all model/process lifecycle tests use fakes, with no real provider calls.
- No UI/static files changed; browser smoke and user-guide changes do not
  apply. Live restart behavior remains for Dave to verify after integration.
