# API credits for unattended sessions

Branch implementation for backlog e98832d9. Contract: Phase 2 of
`API_CREDITS_BUILD_SPEC.md` at `63b8b1df`. Not deployed or live-verified.

## Billing and auth

`mc/api_credits_session.py` owns eligibility, sticky billing, the start reserve,
result accounting and the one-time subscription replay. The existing
`launch_marker.launch_env(..., session=...)` scopes provider keys first and
then applies the named `env_for_session` exception to that child only.
Initial dispatch prepares that environment before writing its pending record.
Every Claude spawn/respawn/revival in `agent_routes.py` uses this seam.
`agent_runtime.py` and `provider_env_scope.py` are unchanged.

- New scheduled, steward, night-review, workflow, hivemind and agent-dispatched
  Claude sessions qualify. An explicit manual trigger never receives a credits key;
  other engines receive no credits key. Off, untouched and ineligible launches
  preserve the provider-scoped environment, including explicitly configured
  Claude API auth on API-only installations. Only a real credits-to-OAuth
  downgrade removes API auth. Selecting credits replaces scoped API auth with
  the chosen vault key in that child only.
- Mode must be On, `can_spend()` must pass, and headroom below the stop point
  must be at least `session_reserve_usd` (default $3). The reserve is a start
  gate, not a reservation or a hard per-session ceiling.
- The audited vault read uses the selected secret name,
  `consumer='api-credits-session', unattended=True`. Values exist only in the
  returned child env; no global environment or provider settings write.
  The shim launch path also inherits env in memory; its spec persists names
  only. No new secret file or credential-management path was added.
- `billing`, `billing_reason`, `billing_preference`, `billing_oauth` survive
  pending/completion records and revival. Reset recovery persists all four in
  the shared `limit_resume._SNAPSHOT_KEYS` contract used by park, deferred-success
  and completion snapshots, including the OAuth downgrade marker across a
  server restart. Subscription is sticky;
  a new cycle or mode change does not upgrade an existing conversation.
  Credits can downgrade to subscription when stopped or unavailable. The
  downgrade marker retains OAuth across later spawns and cold restoration.
  Cold dispatch reads the most recent matching log row (logs are newest-first).
- A stop causes a live Mode B process to respawn before its next turn,
  independent of the sticky-settings toggle. Ordinary scheduler and role
  append paths check the same gate. A failed teardown retains the respawn
  requirement so a later tick cannot append into the old credits child.

## Results, errors and ledger

Both Claude readers call `record_result` once per result. Existing session-cost
telemetry is still accumulated normally. Billing uses raw `total_cost_usd`
deltas, separately from Clayrune's price corrections: the CLI resume baseline
and per-process counters prevent charging old conversation cost again.
The per-process raw counter also survives shim reader checkpoints. Missing or
invalid actual cost is not replaced with a price estimate.

Ledger entries use `session:<project>:<schedule-id>` for schedules and
`session:<project>:dispatch` otherwise, the event's usage, reported model(s),
and `estimated:false`. Cycle totals retain session spending even when the
entry cap evicts older entries; background spending is total minus sessions.

Only failed result envelopes are classified. Credit-too-low, spend-limit and
401/auth errors reuse the phase-1 matchers and once-per-cycle stop notice.
Only canonical reasons are stored; raw CLI errors and keys are not added to
billing records or billing logs. The failed child is terminated and reaped
before replay through the normal follow-up route, using the original turn
message and OAuth. Internal request metadata preserves the delegation turn and
spawner ownership, skips human-joined marking, and prevents the fragile-resume
heuristic discarding context solely because billing failed before output.
Subscription results cannot arm another billing replay. The failed credits
result does not settle the spawner or publish an intermediate completion.
`mc/api_credits_pending.py` writes each session delta to a separate fsynced,
atomic `data/api_credits_pending.json` journal before attempting the ledger.
A failed ledger write leaves the full delta there, independent of the session
and its raw-cost baseline. The shared `can_spend()` gate and status polling retry
the journal; until it drains, all further credit work is blocked. A live child
uses the existing next-turn respawn path; successful work is never replayed
because accounting failed. Ledger receipts prevent double charging if a crash
lands after ledger commit but before journal clear. Retries retain the original
cycle and timestamp; receipts are removed only after the cleared journal is
durable. Neither journal nor receipts contain auth values. Corrupt/unreadable
journal or ledger state fails the spending gate closed. If the journal itself
cannot be written, the delta stays in a process-level retry queue and spending
remains blocked; a simultaneous process crash and complete storage failure
cannot guarantee persistence of that unwritable delta.

Ledger write failure still allows that session's OAuth fallback; a failed
replay is an observable ordinary session error.

## Scheduler and UI

Schedules validate/persist `billing: auto|subscription` (default auto). The
preference reaches ordinary fires, role dispatches and workflow agent steps;
workflow run persistence freezes it for later steps. Continued sessions keep
their prior sticky choice. Settings reserve reductions require the existing
human passcode gate because they permit more starts.

`static/js/api-credits-session.js` owns the billing select, spend split and
badge markup. The scheduler form restores and saves opt-out; the credits card
edits reserve; chat status polling carries billing to the model header.
The card discloses that credits sessions lose claude.ai-hosted connectors;
local/project MCP remains available. No human-only gate was loosened.

## Verification

All commands run from the branch worktree. The installation launcher is:
`<installation>\tools\test-run.py`.
`<installation>` means the main checkout; substitute its absolute path.
The journal retains the exact executed commands. The launcher requires the
original command after `--`:

```powershell
<installation>\.venv\Scripts\python.exe <installation>\tools\test-run.py --project mission_control --port 5199 -- <installation>\.venv\Scripts\python.exe -m pytest -o addopts= tests/test_api_credits_session.py tests/test_api_credits_session_integration.py tests/test_api_credits.py tests/test_launch_marker.py tests/test_provider_env_scope.py tests/test_scheduler_routes.py tests/test_scheduler_character.py tests/test_stream_reader_malformed.py tests/test_respawn_stdin_deadlock.py tests/test_guardian_respawn_race.py tests/test_workflows.py tests/test_workflow_runtime_notify.py tests/test_steward_role_dispatch.py -q
pyright mc/api_credits_session.py mc/api_credits_budget.py mc/launch_marker.py mc/api_credits_pending.py
.venv\Scripts\python.exe <installation>\tools\test-run.py --project mission_control --port 5199 -- node tools/smoke/boot-smoke.mjs
.venv\Scripts\python.exe <installation>\tools\test-run.py --project mission_control --port 5199 -- node tools/smoke/api-credits-settings.mjs
.venv\Scripts\python.exe <installation>\tools\test-run.py --project mission_control --port 5199 -- node tools/smoke/api-credits-session.mjs
.venv\Scripts\python.exe <installation>\tools\test-run.py --project mission_control --port 5199 -- node tools/smoke/inline-handler-scope-check.mjs
```

Initial phase-2 validation (261191b0, before round-2 integration): 483 backend tests, pyright 0 errors on the session/budget/launch
modules, dashboard boot smoke, 14 key/settings checks, 18 phase-2 UI checks
at 1440/390, and handler scope across 279 modules. All exit codes 0.

The new integration suite exercises both real readers and both real
follow-up/respawn modes with fake processes, including preserved native resume
ID, exact task, auth removal and delegation-turn ownership. Other tests cover
eligibility, scoping, vault failures, sticky choice/reserve, actual cost and
resume deltas, capped ledger totals, once-only fallback, settings proof,
schedule CRUD and workflow opt-out. Browser tests serve this branch's real
SPA against stub APIs; they do not use the live :5199 frontend. Borrowed main
checkout node_modules is a temporary junction, removed before commit.

Screenshots: `_scratch/api_credits_phase2/{settings,scheduler,chat}-{1440,390}.png`.
No live vault read, paid request, config mutation, schedule creation, merge,
push or server restart was performed. Test reporting can print capture gaps;
the actual framework output and child exit code are the verification evidence.

## Round 2 validation and handoff

On `clayrune/api-credits-phase2-r2`, master `1fdffd96` was integrated as
`d495d1d5`, retaining MC-1165's lifecycle exclusions, resolved isolated cwd and
refusals while passing each stored session to the billing hook.

Fenn's three cold-review probes were copied to scratch, passed, and retained as
`tests/test_api_credits_review_regressions.py`. The new pending suite covers
failed writes, fail-closed gates, restart recovery, journal-clear failures,
original-cycle attribution, corruption, capped totals and auth preservation.
The 13-file backend selection plus both new suites and 18 MC-1165/worktree suites
passed: **759 tests**. Their shared lifecycle fake needed the new `session=`
keyword; no assertions were removed. The final cycle-pruning adjustment was
then validated by **147 accounting/session/probe checks**. Pyright on the four
billing/launch modules: **0 errors**. All four requested browser/static smokes
passed (credits session, credits settings, handler scope and boot).

Actual stdout and child exit codes are the evidence; reporting printed capture
and transport gaps. The main journal retains exact commands and the initial
22-failure fixture-signature run. No live paid acceptance was performed.

Branch candidate only; live paid acceptance and deployment remain outside this
branch. Local handoff state is recorded in the untracked main journal.

## Round 3 reset-recovery validation

Fenn's unchanged restart probe first reproduced the missing `billing_oauth`
marker, then passed after adding it to the shared snapshot contract. Its
function body is retained unchanged in
`tests/test_api_credits_reset_recovery.py`; three additional cases cover park,
deferred-success and completion snapshots, checking billing fields, OAuth-only
child auth and subscription accounting together.

The exact round-2 review selections passed again: **759** combined tests,
**73** cold-review tests, **99** reset-recovery tests, **3** original probes
and **1** restart probe. The new permanent suite passed **4** tests. Pyright
reported **0 errors** on the four billing modules and touched `limit_resume.py`.
Smokes passed **18** session checks, **14** settings checks, **279** module scope
checks and **7** boot scenarios plus **14** feature guards. Reporting emitted
transport/capture gaps; framework stdout and child exits establish these
results. No live paid acceptance was performed.

## Integration handoff

After merge and restart, create one real scheduled Haiku
run with global mode On and Auto billing. Confirm its session record reports
`billing: credits` and that its `session:` ledger entry has a nonzero actual
cost. That live acceptance check remains unverified by this branch build.
Rollback: revert the branch commit. Existing sessions can stay on subscription;
no settings/data migration or human credential change is required.
