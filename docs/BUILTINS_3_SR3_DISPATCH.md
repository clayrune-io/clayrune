# BUILTINS-3 SR-3: shared guarded dispatch

2026-11-02. Backlog `3eb6df34`. Local branch candidate, not a live rollout.

`mc/steward_role_dispatch.py` owns the role-only fire path. Scheduler timer
fires and Run Now call `scheduler_fire`; the next fire after restart uses the
same `prepare_and_fire` path and SR-4 assignment state. Ordinary task/workflow
schedules retain their existing delivery branches. No protected core/fence
file, builtin directive or live assignment was changed.

## Preparation and provenance

Every fire reloads the current project, schedule, pinned hired character and
charter. Disabled mode/schedule, malformed binding, missing/closed charter,
deleted owner and inconsistent project pointer refuse delivery. The final
configuration comparison catches a change during preparation. The private
`Services` boundary allows deterministic tests without a provider process.

Engine precedence comes from `engine_selection.resolve_engine`: nonempty
schedule model/effort override the character; empty values inherit. The
existing opt-in fallback resolver may select an available engine after an
allowance/model refusal. Requested/effective selections and the reason are
carried after the marker and through lifecycle metadata; the effective engine
is checked before launch. No new fallback order or pace policy is enabled.
Unknown efforts and efforts on a runtime without an effort control refuse.

The protected `steward.core.build_cycle_task` entry must expose SR-2's
`cycle_snapshot` keyword and its directive must load. Missing either blocks
delivery. The adapter does not apply the patches or call stale legacy prose.
Tests apply the exact patches only to disposable copies and exercise the
adapter in both integration states.

The task starts with the literal steward marker, checked against both the
Distiller and fence constants. Fresh dispatch records `trigger_type=schedule`
and the schedule ID. Run Now is also unattended. Continuations must match the
assignment's schedule provenance; missing/foreign provenance does not become
a manual run.

`SchedulerServices.refresh` calls the existing persona-aware context builder
with `consumer_unattended=True`, carrying current rules, character body/name,
skills, memory, exploration and ledger context in task text on every delivery
path. The context seam passes the same flag to memory (including link
expansion), exploration and negation-ledger retrieval. The existing switches
and legacy-memory eligibility rules remain in their existing services.
Explicit false cannot override a steward marker. No authority/promotion/
suppression policy changes are made.

## Delivery and guards

Unchanged owner/provider/model/effort and assignment continue the same MC
thread. A guarded idle Claude Mode B process receives the fresh task through
a bounded, acknowledged stdin write. Timeout/write failure leaves delivery
unknown; this adapter never kills an inherited process. Cold revival uses
the existing persona-aware provider revival entry and keeps the MC session
ID. A failed revival is held; it never falls through to fresh dispatch.

The role-only revival keyword keeps Claude auto-fresh disclosures after the
marker and requires stdin acknowledgment. Native revival carries the frozen
prepared context and reuses the MC ID even while its closed row is still in
memory. The shared native context-writer seam prefixes the marker before any
context envelope, reports write/flush/close acknowledgment, and retains the
ordinary context fingerprint/transcript display behavior. Non-role deliveries
do not use this seam. Captured runtime pipes cover fresh/native resume on
Codex/Qwen/Gemini; a captured Claude auto-fresh revival covers handoff order.
These tests launch no CLI or model.

A changed selection or explicit noncontinuation uses a fresh successor with
no provider-native resume ID. SR-3 checks child continuity before launch and durably journals the transition;
the channel lifecycle seam must still approve communications readiness.
It receives the predecessor selection hash/current selection and then the
accepted session ID. Full selection records live in the journal. The
predecessor remains in SR-4 state, journal/handoff text and fire response;
the adapter does not
retarget callbacks or mutate a live process's identity.

Guard readiness is checked on every fire, including append and revival:

- Fencing disabled means roles are blocked, regardless of ordinary launch policy.
- Claude requires a fence-bearing launch settings resource and the shared self-test.
- Codex uses its existing armed self-test; its actual launch repeats the runtime guard.
- Gemini/Qwen use BUILTINS-5's native loader/runner preflight and settings adapter.
- OpenCode/Goose/Aider/Kiro/unknown providers are visibly blocked.
- Live Claude append additionally verifies schedule provenance and the running
  process's fence-bearing `--settings` file. An unverifiable process is held.

These are adapter tests, not certification of live provider parity. In
particular, the installed Gemini ownership refusal documented in BUILTINS-5
is retained. No model, login, browser, tunnel or mail process was launched.

## Dispatch persistence and SR-4 seam

Local master `a34961dc` (SR-4 `2a602387`) is merged into this branch.
`RoleStateStore(application_data_root)` is the only assignment state store,
keyed by `(project_id, schedule_id)`. SR-3's `DispatchStore`, `FileDispatchStore`
and checkpoint schema are removed. No changes were made to SR-4 modules.

The adapter calls `acquire_claim`, `record_attempt`, `set_claim_state`,
`record_session`, `accept_delivery` and `release_claim`. Selection, context,
character and task hashes use SR-4's `snapshot_hashes`; full selections are
journal records, not a parallel JSON store. `accept_delivery` is the only
watermark mover. Session recording precedes acceptance: failure in either
leaves `delivery_unknown` and the previous watermark intact. A failed
preparation after acquisition leaves `preparing`; it is also held for review.

`project_exclusion` supplements SR-4's assignment RLock with an exclusive
`<application-data-root>/steward/dispatch-locks/<project>.claim` file. This is
only a mutex, not an assignment state copy. A crash mutex, preparing claim
or unknown delivery is never stolen by age/PID disappearance. Legacy SR-3
`steward/dispatch/<project>.json` or `.claim` files block rather than being
silently ignored or automatically migrated. An unresolved claim on a different
assignment also blocks a rebind. Runtime files stay outside project records.

An accepted claim is reconciled only after observing a positively closed
session: idle/done/completed, with no question, approval, child wait or
uncertain stdin flag. Missing/in-progress/interrupted/error observations hold.
The completion notifier must persist successfully before releasing that exact
claim. A new assignment starts with its own empty watermark. No automatic
operator reconciliation command is included.

SR-4's `record_lifecycle(..., store=)` handles `started`, `completed` and
scheduler failure/skip notifications. Its returned `ok` is checked. Failure
notifications preserve the original refusal and add a visible `lifecycle_error`
when persistence also fails. They run after releasing the schedule write lock
to avoid reversing preparation's assignment-lock/write-lock ordering.

SR-4's `append_journal` handles delivery events `first_fire`, `continue`,
`successor`, `accepted`; those are deliberately not passed to the notifier's
fixed event enum. Pre-delivery journal failure blocks launch. At cycle start,
`check_pending_replies` uses the existing laundering runner; reader errors
block before handoff. Only status/decision enums (always
`grants_authority=False`) enter handoff metadata/journals, never reply bodies.
No backlog notes, raw mailbox reads or email sends are added here.

## Child-work handoff follow-up

SR-4's callback confirmed that child continuity belongs to SR-3. New
`mc/steward_role_children.py` now provides a strict read-only projection of
current/durable children and callback receipts. It uses the already configured
agent-delivery database in SQLite `mode=ro` with `query_only=ON`; it neither
creates a missing database nor runs the UI projection's best-effort empty
fallback. Database/read/schema failures hold the transition.

It scopes sessions, log entries and events to the project and predecessor
receiving chain, including known transitive children. It reads identifiers
and states, never completion payloads or task/summary text. A source/outbox
record without a terminal inbox receipt remains pending. Only `processed`,
`confirmed_in_transcript` and `handled` are settled receipts; `submitted`,
unknown states and older-turn receipts do not prove current-turn delivery.
Running/waiting children or missing turn/receipt evidence remain unfinished.
A child row with no session ID also holds; it is not silently dropped.

`SchedulerServices.lifecycle` adds this operational state to continuation
handoff text and to durable journal transition metadata. An unchanged thread
can collect outstanding work without a new
cycle being held just because a non-waiting child exists. A successor must
have a drained snapshot; there is no callback retargeting or duplicated
child launch. `Prepared` carries predecessor/successor identity, and
`SchedulerServices.deliver` repeats the drain and predecessor-idle checks
under the existing project manager lock immediately before selecting its
fresh launch path. New child work arriving during context preparation holds
the successor with its uncertain claim intact.

The role-specific mutex prevents competing role fires; this change does not
reserve or disable ordinary chat APIs or replace the delivery system's own
receiver guards. A live rollout must still exercise callback arrival around
successor selection. No live provider/child-retention claim is made here.

**Remaining interface decision for Dave: channel readiness.** SR-4 provides
state, journaling, decision routing and default mail/digest runners. The
existing `question_channel.channel_for(project)` selects `email` or `off`,
but there is no no-send readiness/status API. The existing `_creds` and
mailer `_load_config` return credential values; those are not acceptable
agent-facing readiness contracts. SR-4's default runners are fixed to the
application tool paths, not a project-scoped tool registration. No credential
file/value was read or script invoked to work around this missing contract.

The current production gate remains:

```python
scheduler_routes.steward_role_lifecycle(project, schedule, event, detail) -> str
# first_fire / continue / successor / accepted
# channel readiness approval and optional handoff text; raises on a blocker
```

SR-3 now owns the child check/text and SR-4 journal calls; this missing callback
only needs channel readiness. `fire` still refuses its absence explicitly.
No dummy always-ready implementation is installed.

Two choices, not selected by the builder:

- **A (recommended):** add a no-send, metadata-only readiness probe to the
  existing pilot mail tooling; keep unconfigured other projects visibly held.
  Cost: a probe contract and command tests, without new project settings.
- **B:** add project-scoped approved sender/digest tool registration and
  readiness metadata. Cost: configuration/schema and registration tests in
  addition to the runtime adapter; wider than the current SR-3 scope.

Both preserve existing routing, mail pause/send verification, reply laundering
and human gates. Dave selects the interface before the missing callback is
implemented; no product option or credential-access rule was inferred here.

Required before enabling a role:

1. Human integration of both SR-2 patches (core adapter and directive).
2. Dave-selected channel readiness interface and its production callback.
   Child drain checks are implemented; controlled callback-race verification
   remains part of live acceptance.
3. A controlled cycle on each enabled installed guard-capable provider;
   adapter mocks alone are not rollout evidence.

Run failures persist a concrete `last_error`/timestamp and a Runs skip entry.
Timer runtime updates reload the role row under the existing write lock, so
they retain concurrent human binding edits; Run Now leaves cadence untouched.
The existing project steward status compatibility surface is still SR-1's
generic execution block; SR-5/integration must replace it alongside the UI.

## Validation

All pytest commands are run from the worktree's `tests/` directory so its
live-tunnel and operator-home isolation fixtures load. The dedicated suite
covers dependency absence/presence, current charter/voice, filter forwarding,
engine precedence/fallback, four successor triggers, held children, concurrent
timer/Run Now, SR-4 persistence failures, pending replies, journal/notifier
failures, legacy/rebound claims, lock ordering, stdin acknowledgment, production
append/revival/fresh seams and role-only runtime persistence.

From `tests/`:

```text
python ../_scratch/run_sr3_regression.py -q -o addopts='' --tb=short test_steward_role_children.py test_steward_role_dispatch.py test_steward_role_state.py test_steward_role_comms.py test_delegation_delivery.py test_delegation_waiting.py test_delegation_settlement.py test_delegation_privacy.py test_delegation_lifecycle.py test_steward_role_task.py test_steward_role_model.py test_steward_core.py test_steward_refresh.py test_steward_routes.py test_scheduler_character.py test_scheduler_routes.py test_scheduler_slot_claim.py test_distiller_safety.py test_fence_steward_marker.py test_codex_steward_fence.py test_vendor_fence.py test_vendor_fence_probe.py test_vendor_fence_routes.py test_fence_fail_closed_unattended.py test_guardrail_hooks_boot.py test_guardrail_injection.py test_read_floor_injection.py test_memory_link_graph.py test_memory_v2_step7.py test_memory_v2_step8.py test_context_profile_by_job_shape.py test_context_profile_by_model_tier.py test_native_followup_context_routes.py test_distiller_promote_unattended_gate.py test_distiller_durable_yes.py test_builtins_resume_context.py test_revive_notify_carry.py test_agent_send_non_claude_revive.py test_context_freshness.py test_agent_routes.py test_engine_fallback.py
```

**Current full regression: 1204 passed, 1 existing xfailed in 58.60s,
rc=0.** The expected failure is the existing human-owned fence timeout
contract. Historical `7ffbc80d` validation (its original test list): post-merge
119 passed; focused 134 passed; full 1063 passed, 1 existing xfail, all rc=0.
The registered
foreground runner invokes `pytest.main` with the exact arguments above from
`tests/`, guarded by `if __name__ == "__main__"` for Windows multiprocessing.
To run without process registration, replace the runner prefix with
`python -m pytest`, retaining every argument and test file. The helper is a
gitignored local artifact. All model/mail/IMAP dependencies are mocked.

From the worktree root:

```text
pyright mc/steward_role_children.py mc/steward_role_dispatch.py mc/blueprints/scheduler_routes.py mc/resume_context.py
git diff --check
```

**Both rc=0; pyright 0 errors (including the new child module).** The modified agent-routes monolith remains at
15 existing pyright errors (rc=1); comparing HEAD and edited diagnostic
rule/message multisets finds zero new diagnostics.

Changelog and this contract are updated. The original candidate updated the
guide; this follow-up changes internal persistence, and its existing guide
text remains accurate. README/agent rules need no change: no install steps
or agent authority changed. No static/UI assets
changed, so frontend boot/Playwright checks are not applicable. Protected
core/directive edits, SR-4 state/comms files, live migration, merge to master,
push and server restart remain untouched. Local master was merged INTO this
branch as requested.

Changed paths:

```text
.gitignore
CHANGELOG.md
docs/BUILTINS_3_SR3_DISPATCH.md
docs/BUILTINS_3_STEWARD_ROLE_SPEC.md
docs/USER_GUIDE.md
mc/steward_role_dispatch.py
mc/blueprints/scheduler_routes.py
mc/blueprints/agent_routes.py
mc/resume_context.py
tests/test_steward_role_dispatch.py
tests/test_steward_role_model.py
```

## SR-4 follow-up scope

Changed in this follow-up: `.gitignore` (deduplicate runtime exclusion),
`CHANGELOG.md`, `docs/BUILTINS_3_SR3_DISPATCH.md`,
`docs/BUILTINS_3_STEWARD_ROLE_SPEC.md`, `mc/steward_role_dispatch.py`,
`tests/test_steward_role_dispatch.py`. The changed-path list above includes
the original SR-3 commit as well. No new product choice was made.

The unattended fence refused the required home-directory plan write; the
plan and progress are preserved in the gitignored item journal instead.

The initial full-suite attempt was interrupted at the multiprocessing slot
race: the temporary process-registration wrapper lacked a Windows entry
guard. That run has no validation verdict. The corrected guarded wrapper ran
the full, unchanged test argument list to the passing result above. The
interrupted runner and its observed worker PIDs were gone before the retry.

## Child follow-up validation

Focused command (from `tests/`):

```text
python -m pytest -q -o addopts='' --tb=short test_steward_role_children.py test_steward_role_dispatch.py test_steward_role_state.py test_steward_role_comms.py
```

**159 passed in 8.06s, rc=0** (25 child-continuity cases). New child module
and dispatch/scheduler/resume helpers: **pyright 0 errors, rc=0**.
**Expanded full suite: 1204 passed, 1 existing xfailed in 58.60s, rc=0.**
The historical 1063-case result covers 7ffbc80d, before this follow-up.

Follow-up changed paths: `mc/steward_role_children.py`,
`tests/test_steward_role_children.py`, `mc/steward_role_dispatch.py`,
`docs/BUILTINS_3_SR3_DISPATCH.md`, `docs/BUILTINS_3_STEWARD_ROLE_SPEC.md`,
`docs/USER_GUIDE.md`, `CHANGELOG.md`. SR-4 state/comms remain unchanged.
