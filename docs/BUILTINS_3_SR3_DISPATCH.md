# BUILTINS-3 SR-3: shared guarded dispatch

2026-11-02. Backlog `3eb6df34`. Local branch candidate, not a live rollout.

`mc/steward_role_dispatch.py` owns the role-only fire path. Scheduler timer
fires and Run Now call `scheduler_fire`; the next fire after restart uses the
same `prepare_and_fire` path and durable checkpoint. Ordinary task/workflow
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
checkpoint's schedule provenance; missing/foreign provenance does not become
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
no provider-native resume ID. The lifecycle seam must record/approve journal
and child-work handoff before launch. It receives predecessor/current
selections and then the accepted session ID. The predecessor remains in the
checkpoint, journal/handoff text and fire response; the adapter does not
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

The injectable `DispatchStore` contract is deliberately narrow:

```python
transaction(project_id)  # cross-process project exclusion
read(project_id) -> dict
write(project_id, record) -> None
```

`FileDispatchStore` stores only execution checkpoints at
`<application-data-root>/steward/dispatch/<project>.json`, outside the project
record directory and gitignored by `data/steward/`. It uses an exclusive
project `.claim` file and fsync-before-replace checkpoints. It is separate
from SR-4's decision/session state, not a second mail/decision implementation.
The checkpoint contains assignment ID, current/prior session IDs, engine
selection, refresh/attempt/completion times and the dispatch claim.

The write-ahead `delivery_unknown` boundary precedes any delivery side
effect. Only acknowledged delivery records `accepted` and advances the
refresh watermark. The next fire may reconcile an accepted claim only from
a positively closed session: idle/done/completed, with no question, approval,
child wait or uncertain stdin flag. Missing/in-progress/interrupted/error
observations hold. A different assignment starts with an empty refresh
watermark. A crash mutex or unknown delivery is never stolen based on age or
PID disappearance; operator reconciliation must establish the outcome before
removing the exact claim. No automatic recovery command is provided here.

SR-4 owns journals, decisions and child handoff. Its integration seam is:

```python
scheduler_routes.steward_role_lifecycle(project, schedule, event, detail) -> str
# events: first_fire, continue, successor, accepted
# returns text to include after the steward marker; raises on a blocker
```

Before-delivery calls must durably journal the attempt, inspect pending
replies through the configured laundering path and validate the applicable
communication channel. A successor must hold if prior callbacks cannot drain
or safely retain their receiving session. The accepted event records the new
session/predecessor link. No implementation may infer human permission from
a reply classification. The adapter does not call any backlog-note API.

**Integration remains held:** SR-4's committed `RoleStateStore` and comms
functions do not yet provide this combined lifecycle/child-handoff callback.
No callback is silently installed by SR-3. Its absence is an explicit fire
blocker. The dispatch interface can later be backed by SR-4 while retaining
project-wide cross-process exclusion; SR-4's per-assignment RLock alone does
not replace that exclusion. Do not mirror claims into two independent stores.

Required before enabling a role:

1. Human integration of both SR-2 patches (core adapter and directive).
2. Integration of the SR-4 lifecycle callback and a configured channel/child
   handoff policy, with shared state reconciliation verified.
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
timer/Run Now, restart/unknown claims, stdin acknowledgment, production
append/revival/fresh seams and role-only runtime persistence.

From `tests/`:

```text
python -m pytest -q -o addopts='' --tb=short test_steward_role_dispatch.py test_steward_role_task.py test_steward_role_model.py test_steward_core.py test_steward_refresh.py test_steward_routes.py test_scheduler_character.py test_scheduler_routes.py test_scheduler_slot_claim.py test_distiller_safety.py test_fence_steward_marker.py test_codex_steward_fence.py test_vendor_fence.py test_vendor_fence_probe.py test_vendor_fence_routes.py test_fence_fail_closed_unattended.py test_guardrail_hooks_boot.py test_guardrail_injection.py test_read_floor_injection.py test_memory_link_graph.py test_memory_v2_step7.py test_memory_v2_step8.py test_context_profile_by_job_shape.py test_context_profile_by_model_tier.py test_native_followup_context_routes.py test_distiller_promote_unattended_gate.py test_distiller_durable_yes.py test_builtins_resume_context.py test_revive_notify_carry.py test_agent_send_non_claude_revive.py test_context_freshness.py test_agent_routes.py test_engine_fallback.py
```

**rc=0: 985 passed, 1 xfailed in 45.68 seconds.** The expected failure remains
the existing human-owned project fence timeout contract. No provider parity
or live activation is inferred from this run. The dedicated dispatch file
contributes 56 cases, including partial checkpoint corruption and a legacy
in-flight cycle whose timestamp precedes its marker.

From the worktree root:

```text
pyright mc/steward_role_dispatch.py mc/blueprints/scheduler_routes.py mc/resume_context.py
git diff --check
```

**Both rc=0; pyright 0 errors.** The modified agent-routes monolith remains at
15 existing pyright errors (rc=1); comparing HEAD and edited diagnostic
rule/message multisets finds zero new diagnostics.

Changelog, guide and this contract are updated. README/agent rules need no
change: no install steps or agent authority changed. No static/UI assets
changed, so frontend boot/Playwright checks are not applicable. Protected
core/directive edits, SR-4 state/comms files, live migration, merge, push and
server restart remain untouched.

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
