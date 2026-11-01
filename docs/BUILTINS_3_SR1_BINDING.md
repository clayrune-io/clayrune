# BUILTINS-3 SR-1: steward binding and write boundary

Date: 2026-10-31. Status: branch candidate; no migration or role execution.
Parent: [Steward role specification](BUILTINS_3_STEWARD_ROLE_SPEC.md).

The dispatching owner selected Q1 A and Q2 A: the schedule owns the role,
the project points to it, and only one enabled steward assignment is accepted
per project. Its same-project backlog item is the canonical charter. An
inherited project character is resolved and pinned when saving the binding.
Character definitions do not acquire mandate, cadence or permission fields.

## Contract

`mc/steward_role_model.py` holds validation and read-only views.
`mc/steward_role_writes.py` holds the request authority, locks and persistence
adapters. Existing routes retain the scheduler and project stores; no new
state files, scheduler, fence implementation or dependency are introduced.

The existing schedule POST accepts this third target, exclusive with `task`
and `workflow_id`:

```json
{
  "project_id": "example",
  "character": "global:existing-character",
  "steward_role": {
    "version": 1,
    "charter_item_id": "existing-item",
    "journal_path": "docs/_journal/existing-item-service.md"
  },
  "schedule_type": "interval",
  "interval_minutes": 180,
  "continue_session": true
}
```

The request also needs the existing human proof; it is never stored. This is
an API contract for later controls, not a current user walkthrough. SR-5 owns
the editor, guide update, reviewed migration and activation.

Validation requires version 1 and exactly the three role fields. The owner
must be hired, not removed, and resolve to an actual character in its declared
global or same-project scope. A missing character never becomes a default
persona. The charter must exist once in that project, contain nonempty text
and have a non-closed status. Role rows cannot use legacy `steward` targeting
or `delete_after_run`. Enabled role rows conflict with enabled legacy steward
rows as well as other enabled role rows, even while the project kill switch
is off. Disabled assignments may be retained; they keep charter protection.

Journal paths use forward slashes, remain relative to the registered project
root, end in `.md` and stay under `docs/_journal/` after realpath resolution.
Absolute, traversal, drive/stream and symlink/junction escapes are refused.
Validation creates neither a journal nor runtime sidecars under `data/projects/`.

The project pointer is `steward_assignment: {"schedule_id": "..."}`. A direct
pointer write must name a same-project role and must point to the active one
if there is one. The role's backlog text is exposed by `model.objective()`
and the existing steward status endpoint as the legacy objective compatibility
view. The stored legacy `steward_objective` is not rewritten on reads and is
not an alternative role mandate. SR-2's composer must consume the canonical
model view, not the legacy `steward.core.get_objective()` field reader.

## Authority and recovery

Role create, update, enable, reassignment, detachment and deletion require
caller attribution plus the shared retyped dashboard passcode check. Presence
counts: unchanged values, nulls and mixed payloads do not bypass the gate.
A known managed agent is refused even with Origin and passcode. No passcode
configuration means no human grant. Body `source`, `human` or `client` fields
do not supply proof. Internal callers of the shared schedule creator get the
same check when they submit a role.

The only agent schedule write exception is exactly `{"enabled": false}` from
an OS-attributed session whose server-owned project, schedule trigger type
and trigger ID match the assignment. It may suspend even if its charter or
owner has subsequently disappeared. Human suspension has the same property.
Adding another key loses that exception. There is no agent re-enable path.

Attached charter text, status and deletion are human-only, including for
disabled bindings. Status protection prevents an agent reopening a completed
mandate. Ordinary backlog text/status behavior remains available. Project
pointer, role-related safety fields, routing and owner-roster writes cannot
bypass the boundary. A bound project's deletion also requires human proof.
The existing project route ignores wholesale backlog replacement; the
boundary still refuses agent payloads containing it when a role is attached.

The existing steward enable control enables the selected role and project
mode without seeding a legacy schedule or second objective. Disable sets the
project kill switch off and retains the assignment, charter and shared guards.
It also works with an orphan pointer. The legacy bootstrap refuses projects
with a binding or pointer, including its standalone/internal entry point.
Ordinary legacy projects retain their previous behavior.

Assignment writes serialize with schedule CRUD under a process-wide RLock
and use the existing project write lock. Timer and ordinary Run Now saves
preserve the latest role rows rather than overwrite them with old snapshots,
including deletion/detachment. The existing atomic JSON writers perform
durable replacements. Corrupt schedule stores are logged and refused; they
are never reset to an empty role store by a write adapter.

### Lock order (MC-1139 rework)

Role-owned schedule and role-control writes take `WRITE_LOCK` then the
project write lock, and never take a session-manager lock. Validation
adapters release their locks before calling existing handlers:
`project_write` locks the project only for its validation read;
`charter_write` reads protected schedules under `WRITE_LOCK`; project
deletion and the legacy steward-control fallback also delegate unlocked.
Role-control persistence stays locked because it performs the write itself.

Existing create/recreate handlers retain manager-lock-before-project-lock
order. The provider-preferences serializer and the delegation test's
`__wrapped__` bypass are unchanged. Seven new concurrency cases verify that
validated handlers allow manager-guarded project saves. Validation snapshots
are not a transaction with subsequent legacy writes; no atomic
attach-versus-legacy-write guarantee is claimed. SR-2/SR-3 must revalidate
current authority at execution.

Schedule and project records are two stores, not a transaction. The schedule
is saved first. For a new binding without an existing pointer, a failed pointer
save leaves it blocked and visible as a missing-pointer error; a second active
binding is refused. An edit to an already-pointed row may persist its authorized
schedule change even if the project save fails: re-read both records before
retrying. A human can repair the pointer or remove the schedule. An interrupted deletion
can leave an orphan pointer; it cannot trigger a legacy bootstrap, and the
human kill switch retains guards. No multi-process assignment lock or live
restart-overlap guarantee is claimed here.

## Integration limits and validation

SR-1 deliberately does not launch roles. The timer skips every row containing
`steward_role`, including malformed rows. Run Now returns 409. Schedule and
steward reads expose `execution_error` explaining that the SR-2/SR-3 guarded
runner is unavailable. Status returns concrete binding errors and never
asserts `fenced: true`. Existing task/workflow execution remains in place.
SR-3 must replace these explicit stops with its shared guarded preparation
path, checking pointer, project kill switch, schedule enablement and current
charter/owner on every fire and continuation.

The new tests use temporary stores, a Flask blueprint app, actual character
files and the shared human-proof helper with an isolated verifier. They do not
import `server` or start providers, browser/login flows, mail or a server.
They cover schema/merged validation, path and symlink escape, concurrent
activation, spoofed attribution, own suspension, charter and project writes,
orphan/partial saves, legacy conflict, stale runtime saves and blocked firing.
Existing scheduler, steward, project, learning-safety and proof suites supply
regression coverage. All-engine live operation, UI and migration are deferred
to their tickets, and remain unverified here.

Changed paths: `mc/steward_role_model.py`, `mc/steward_role_writes.py`,
`mc/project_safety_settings.py`, `mc/blueprints/scheduler_routes.py`,
`mc/blueprints/steward_routes.py`, `mc/blueprints/project_routes.py`,
`tests/test_steward_role_model.py`, `tests/test_project_safety_settings.py`,
`docs/BUILTINS_3_SR1_BINDING.md`, `docs/BUILTINS_3_STEWARD_ROLE_SPEC.md`,
and `CHANGELOG.md`. The new concern is carved into its own modules; existing
large routes receive hooks and shared schedule-field builder extraction only.
There are no static changes or frontend smoke requirements. The user guide
is deferred with the role controls to SR-5; no UI or migration ships here.

Initial selected validation: the command below passed 557 checks (including 78 new
role checks), exit code 0. Both new modules passed pyright basic with 0 errors,
exit code 0. `git diff --check` passed, exit code 0. No live role/provider cycle,
human UI, migration, multi-process restart overlap or deployment was exercised.

```powershell
python -m pytest tests/test_steward_role_model.py tests/test_project_safety_settings.py tests/test_steward_routes.py tests/test_scheduler_routes.py tests/test_scheduler_character.py tests/test_scheduler_slot_claim.py tests/test_steward_core.py tests/test_steward_refresh.py tests/test_project_routes.py tests/test_project_routing_guard.py tests/test_backlog_links.py tests/test_distiller_safety.py tests/test_fence_steward_marker.py tests/test_codex_steward_fence.py tests/test_human_proof_guard.py -q
pyright mc/steward_role_model.py mc/steward_role_writes.py
git diff --check
```

## MC-1139 rework verification

The unchanged concurrent-create regression now passes. Its original failure
was reproduced on `cf68f3de` before the fix (`BrokenBarrierError`, HTTP 500).
The focused regression plus role suite passed 86 checks; the full run also
passed that regression and all 85 role checks (including seven new cases).
`pyright mc/steward_role_model.py mc/steward_role_writes.py` reported 0 errors,
exit code 0. No delegation-privacy test or provider serializer was changed.

The required full command completed with **18,207 passed, 51 skipped,
1 expected xfail and 1 failed**, exit code **1**, in 2,221.70 seconds:

```powershell
python -m pytest -q -p no:cacheprovider tests
```

JUnit independently records 18,260 cases and no errors. The remaining failure
is `tests/test_scribe_log_fallback.py::test_codex_transcript_path_lookup_failure_falls_back_cleanly`:
the mocked `get_runtime` raises from `background_model_policy.automatic_model`
after transcript-lookup failure was caught. A read-only Fable review reproduced
it in isolation and verified that its relevant files are unchanged against
baseline `5f6ec8a8`; it also found the failure recorded in the earlier continuity
repair report. It is not attributed to the SR-1 rework. The full-green gate
remains **unmet**; Dave owns the separate Scribe fallback decision. No Scribe
source or tests were changed here, and no passing subset substitutes for the
full result.

An earlier quiet full attempt terminated at 60% without a traceback, Windows
exit code 4294967295 (-1); its cause remains unconfirmed. The completed retry
ran under the Clayrune job runner with output-only `PYTEST_ADDOPTS=-vvv` and
JUnit reporting. No merge, push, restart, migration or live role execution was
performed. Skips cover platform/tool availability and explicitly gated live
checks; the expected xfail requires a separate human-owned fence patch.
