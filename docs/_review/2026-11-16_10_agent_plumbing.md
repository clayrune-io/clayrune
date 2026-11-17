# Agent plumbing outside the runtime core, pass 10 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `90b9786f`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, or a read-only
count) or **read, not measured**. `server` was never imported, the live server
was sent nothing but `GET`, no model call was made, and no process that binds a
port or launches a provider CLI was started. Nothing was written under `data/`
or to a real memory directory.

STATUS: IN PROGRESS. This file is written incrementally. Sections below the
"Working notes" heading are candidate findings in the order found; the ranked
summary, feature table, backlog table and streamlining section are written
last. If you are resuming after a rollover: read "Progress log" first.

## Progress log

- [ ] process_guard, process_ledger, process_sweep, windows_process_job
- [ ] bg_job_liveness, background_tasks, background_model_policy, agent_jobs
- [ ] agent_shim, agent_shim_adopt, agent_shim_client, agent_shim_completion(+retry), agent_shim_tramp
- [ ] agent_spawn, agent_spawn_interactive, agent_uncertain_launch, agent_worktree_followup, agent_log_file, agent_log_maintenance
- [ ] conversation_contract, conversation_store, conversation_views
- [ ] execution_lifecycle, execution_policy, launch_marker, launch_transaction, isolated_launch
- [ ] midturn_rollover, mid_turn_send, turn_clock
- [ ] guardrail_hooks, fence_fail_closed, vendor_fence(+hook,+probe), vendor_context_sync
- [ ] cli_install, cli_update, cli_update_retry, mac_update, claude_*, codex_*, aider_*
- [ ] attend_once_*, caller_attribution, resume_*, state, transform_*, pty_backend
- [ ] blueprints: agent_router_stats_routes, claude_import_routes, cli_update_routes
- [ ] backlog cross-check
- [ ] prior audits still open
- [ ] final tables

## Scope note

`mc/agent_runtime.py` was pass 1, `mc/blueprints/agent_routes.py` and
`mc/agent_worktree.py` were pass 2. The `mc/agent_mail_*.py` modules and the
`mc/blueprints/agent_mail_*_routes.py` modules are taken to be pass 05b's
(its report is not in this checkout); they are not read here.

## Working notes (candidate findings, in the order found)

