# Orchestration and scheduling review, pass 9 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `3f51b0e0`.
Review only: no source file was changed. Every line cited was opened. A cost or
a wrong result is marked **measured** (a scratch script against the pure
function, or a read-only count) or **read, not measured**. `server` was never
imported, the live server was sent nothing but `GET`, no model call was made,
no mail was sent, and no process that binds a port or launches a provider CLI
was started.

Scope: in `mc/` and `mc/blueprints/`, every module whose name starts with
`hivemind`, `scheduler`, `schedule`, `steward`, `coordination`, `automation`,
`wake`, `question`, `limit`, `allowance`, `usage`, `engine` or `model`, except
`mc/engine_pace_dispatch.py` (read fully by pass 2). 47 files, about 19,400
lines. Earlier passes (`docs/_review/2026-11-16_01` to `_04`) are not repeated;
where a finding here is the other half of one of theirs, the id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology,
**F** does the feature work. Severity, same scale as passes 1 and 2:
**blocker** = wrong result or lost data on a normal path today; **high** =
wrong result or lost data on a reachable path, or a cost paid on every
turn/dispatch/poll; **medium** = narrower trigger, or a latent defect behind a
disabled flag; **low** = hygiene.

Finding ids in this pass start with `OS`.

STATUS: IN PROGRESS. Written incrementally. The ranked summary, feature table
and streamlining section are assembled last from the candidate findings below;
until then read "Candidate findings" and "Progress log".

---

## Ranked summary

(assembled last)

## Feature table (lens 4)

(assembled last)

## Backlog cross-check

(required by an addendum received 2026-11-16 21:56 PST; not yet done. Method:
`GET /api/project/mission_control/backlog`, take every item whose status is
open, in_progress or blocked, pick the ones whose subject is in this region,
skip MC-1179 to MC-1183. Classify each as DONE / PARTLY DONE / NOT DONE /
OBSOLETE or DUPLICATE / NEEDS A HUMAN, with item key, a five-word subject, the
class and the evidence (file:line or commit). Do not PATCH or note any item.
Do it after the last file is read and before the ranked summary. The final
message lists the keys classed DONE or OBSOLETE.)

## Streamlining

(assembled last; candidates are collected under "Streamlining candidates")

---

## Candidate findings (in the order found)

Working notes. Each is rewritten into the detail section at the end; ids are
final.

### OS1 · high · L — a failed fire of an `interval` schedule is retried every 30 s, for as long as the cause lasts
`mc/blueprints/scheduler_routes.py:851-857, 877` with `:428-444`.
On a failed dispatch the loop leaves `last_run` alone (`:855-857`, correct) and
then calls `_compute_next_run` (`:877`). For an `interval` row that function
computes `last_run + interval`, finds it in the past, and returns `now + 5 s`
(`:438-441`; with no `last_run` at all, `:444`). The next tick, 30 s later,
finds the row due again, the slot string is new so `_claim_slot` passes, and
the dispatch is attempted again. Each attempt: `_steward_cycle_task` for a
steward row (memory search, exploration floor, a project save, `:734`),
`_log_agent_activity` (a load and save of the project record, `:834`), a new
claim file (`:205`), and for a quota block a push notification (`:843-848`).
The two sibling paths in the same loop know the hazard and roll an interval
row explicitly: the paused path (`:660-663`) and the condition-skip path
(`:596-598`, whose docstring says "an interval row computes its next fire from
`last_run`, hence the explicit roll"). The steward-role path does too
(`mc/steward_role_dispatch.py:570-572`). The ordinary failure path is the one
that does not.
Persistent causes that reach it: `EngineFallbackBlocked` (provider allowance
exhausted and no fallback configured, hours), `RevivalRefused` (an isolated
conversation whose worktree is gone, permanent), a deleted project, a missing
CLI, a workflow start that raises (`:715`).
Failure: an hourly legacy steward (it is an `interval` row,
`steward_routes.py:66`) whose engine is out of allowance for five hours makes
about 600 dispatch attempts, 600 activity lines and 600 "Scheduled run blocked"
notifications instead of 5.
Smallest fix: on `not dispatch_ok` for an interval row set
`next_run = now + interval`, as the three sibling paths do. Test: none; the
scheduler tests assert `last_run` is not stamped on failure but not the next
`next_run` (to confirm when tests are read). Measured: see "Measurements".

### OS2 · medium · L — the scheduler tick and Run Now write back a whole-file snapshot taken before the dispatch; a schedule created, edited or deleted in between is undone
`scheduler_routes.py:627` (load), `:809` (dispatch), `:882` (save);
`:1685` / `:1780` / `:1806` for Run Now; `mc/steward_role_writes.py:60-67`.
`save_runtime_schedules` re-reads the file only to protect role rows; every
ordinary row comes from the caller's stale list. The module comment at
`:158-160` says the unlocked read-modify-write "is safe with one scheduler";
it is not safe with one scheduler and an HTTP writer. `WRITE_LOCK` serialises
the write call, not the read-to-write span.
Failure: `POST /api/schedules` lands while a tick is inside
`_dispatch_agent_internal` (seconds: context build plus process spawn). The
route appends the row and answers 201; the tick then saves its older list and
the row is gone. Same for a PUT (reverted) and a DELETE (row comes back).
An agent that schedules its own follow-up while Run Now is dispatching is the
same race from a route.
Smallest fix: make the tick's save a per-row merge under `WRITE_LOCK`: re-read,
copy only the runtime fields (`last_run`, `next_run`, `last_error`,
`last_error_at`, `enabled` for a fired one-shot) onto rows that still exist,
drop `to_delete`, write. `_save_next_wake` (`steward_role_dispatch.py:655-662`)
already does this for role rows. Test: none. Measured: see "Measurements".

### OS3 · medium · L/M — `_load_schedules` reads any failure as "no schedules", silently, and the legacy steward enable then saves that
`scheduler_routes.py:144-150` (`except Exception: pass`, no retry, no log);
`mc/blueprints/steward_routes.py:160-170`.
The second reader of the same file, `steward_role_writes._schedules`
(`:46-57`), retries, fails closed and says why; `append_ordinary`
(`:127-137`) exists because "ordinary creates must not reset a corrupt store".
`_do_enable` still uses the swallowing reader and then writes the list back
with its own row appended.
Failure: the schedules file is unreadable for one call (crash damage, a
scanner holding it). `POST /api/project/<id>/steward/enable` reads `[]`,
appends the steward row, saves: every other schedule is gone and the response
is 200. Lesser effects of the same reader: `GET /api/schedules` answers `[]`,
Run Now answers 404, and the scheduler tick fires nothing, all without a log
line (project policy: a JSON state load must log).
Smallest fix: one reader. Have `_load_schedules` call `_schedules()` and let
callers that can tolerate a miss catch and log. Test: none for the enable path.

### OS4 · medium · F — steward roles cannot fire on `master`: two human-applied patches are not applied, and nothing in the role editor's "runnable" verdict covers the third gate
`mc/steward_role_dispatch.py:181-184`; the steward package's core module
`:137-138`; `mc/steward_role_task.py:9-10, 36-44`;
`mc/steward_role_channel.py:41-43`;
`mc/blueprints/steward_role_routes.py:39-68, 250`.
(a) `prepare_and_fire` refuses unless the steward package's
`build_cycle_task` takes a `cycle_snapshot` parameter. On `master` it does not,
and the builtin `mc-steward` skill folder has no `CYCLE_DIRECTIVE.md`. Both are
in `docs/patches/` waiting for a person (that package is human-owned). The
editor reports both honestly (`execution_blockers`), and the live install
returns exactly those two blockers and zero assignments (GET, measured). So
the whole SR-1 to SR-5 stack (2,415 lines in `mc/steward_role_*.py`, 253 in
the routes file, plus the editor script) is shipped and cannot run.
(b) Once the patches are applied there is a third gate the editor never
reports. `require_ready` compares `<project_path>/tools/night-review/send_mail.py`
with the server's own copy (`steward_role_channel.py:41-43`) and refuses when
they differ. Only a project whose folder IS the Clayrune checkout passes.
`execution_blockers()` does not run this check, so `preview` answers
`runnable_now: true` (`:250`) and the editor prints "this install could run
it" for a project where every wake will be refused as `blocked_channel`.
Smallest fix for (b): run `require_ready` in `preview` as one more check.
Test: `tests/test_steward_role_routes.py` covers the two patch blockers; the
channel gate in preview is not covered (to confirm).

### OS5 · medium (latent behind OS4) · L — a role claim left in `preparing` or `delivery_unknown` has no release path anywhere in the product
`mc/steward_role_dispatch.py:227-247, 44-57`; `mc/steward_role_state.py:289-297`.
After `set_claim_state(..., 'delivery_unknown')` (`:235`) any exception from
`services.deliver` (`:239`) leaves the claim in that state: a refusal raised by
`_dispatch_agent_internal` before any byte reached a child, a missing CLI, the
10 s acknowledgement timeout (`:496-498`, `:538-543`), or a server restart
during the dispatch. Every later fire then raises "prior role cycle is active
or delivery is unknown; review required" (`:153-155`). `release_claim` has one
caller, `:225`, reachable only when the claim is already `accepted`. The
dispatch mutex file (`dispatch-locks/<project>.claim`, `:44`) has the same
property after a crash. `steward_role_routes.py` is read-only by its own
docstring, and no route, tool or UI clears either (searched). The spec says
"review required"; the product has no review action, so the remedy is
hand-editing a JSON file under the data folder.
Related: a prior cycle that ended as `error`, `interrupted` or `stopped` is
never `_closed` (`:114-120`), so the next fire is refused until a person sends
that chat one more message that completes.
Smallest fix: one human-only route (passcode, same gate as the other role
writes) that releases a named claim and removes the mutex, journalled.
Test: the state tests pin "never stolen"; none covers recovery.

### OS6 · low (latent behind OS4) · L — role fire compares the whole project record before and after preparation
`steward_role_dispatch.py:220-222`. `current_project != project` is a deep
compare of the full record (backlog, activity log, `last_updated`).
Preparation in between runs a guard self-test and a context build. Any write
to the record by anything else in that window (an activity line from another
dispatch, a backlog edit) refuses the fire with "role configuration changed
during preparation". Compare the bound fields only: `steward_mode`,
`steward_assignment`, `roster`, `project_path`, the charter item.

### OS7 · low (latent behind OS4) · L — the role kill switch leaves the schedule row enabled, so the timer keeps firing into a refusal
`steward_role_writes.py:377-383` sets `steward_mode='off'` and keeps the row
enabled; `steward_role_dispatch.py:619-652` (`timer_tick`) checks only
`row.enabled`; the fire raises "steward role is disabled" (`:135-136`) and
`scheduler_fire` records `last_error`, a Runs-panel skip and a journal entry
(`:562-566`, `:596`) on every cadence, with no end.
Fix: return early in `timer_tick` when the project's `steward_mode` is not on.

### OS8 · low · L — `steward_role_writes.EXECUTION_ERROR` is a constant from SR-1 that three responses still return
`steward_role_writes.py:25, 285, 287, 383`. It says "execution unavailable:
SR-2/SR-3 guarded runner required" whatever the real state;
`steward_role_dispatch.execution_error()` (`:605-616`) computes the real one
and `GET /api/schedules` uses it (`scheduler_routes.py:1303`). No script in
`static/` reads `execution_error` (searched), so today it is payload nobody
reads; delete the constant and the three fields, or return the computed value.

### OS9 · low · L — the legacy steward's "respawn deferred" is dropped, not deferred
`scheduler_routes.py:734, 753-759` with `:566-571`. `_steward_cycle_task`
stamps `steward_last_cycle_at` before the caller decides whether to fire. When
a new skill is pending and `_steward_cycle_running` says busy, the tick is
skipped with "respawn deferred"; the stamp has already moved, so the next
cycle sees no new skill, takes the stdin-append path, and the live process
never loads it. `_steward_cycle_running` (`:470-484`) is also true for ANY
running session of the project, not only the steward's, so on a busy project
the skip is the normal case. Fix: stamp after a successful dispatch.

### OS10 · low · L — one-shot schedules: a skipped fire is filed as a completed one
`scheduler_routes.py:772-774` (busy), `:707-714` (workflow missing, already
live, disabled), then `:858-875`. All four leave `dispatch_ok = True`, so a
`once` row is disabled, or deleted outright with `delete_after_run`, though
nothing ran. The comment at `:866-873` reasons about failures only.

### OS11 · low · L — create and update accept rows that can never fire
`scheduler_routes.py:1525-1561`, `:1652-1661`. Type is validated; the fields
the type needs are not. `schedule_type: "cron"` with an unparseable
`cron_expr`, or `"once"` with no `run_at`, returns 201 with `enabled: true`
and `next_run: null`; the loop recomputes `None` on every tick (`:638-644`)
and never says why. This is the failure the file's own comments at
`:1488-1492` call out for an unknown type. `time` is not validated either: an
unparseable value silently becomes 09:00 (`:406-409`), and `"25:00"` raises at
`:419` (a 500). Fix: refuse a row whose `_compute_next_run` is `None` at
create/enable time unless it is a past one-shot being parked.

### OS12 · low · E/M — six agent-log scans per continued fire, three of them the same loop
`scheduler_routes.py:1049-1170`. A cold-resumed fire calls
`_latest_session_id_for_schedule`, `_latest_claude_sid_for_schedule`,
`_schedule_resume_provider`, `_session_owning_resume`,
`_newest_run_session_id_for_schedule` and `_row_flags_isolated`; each loads and
walks the whole agent log (pass 2 measured about 6 ms per parse on a 2 MB
log). `_latest_resume_for_schedule`, `_schedule_resume_provider` and
`_session_owning_resume` are one loop written three times. This is the
scheduler's share of 2026-09-10 E3. One pass returning
`(resume_id, provider, owner_session_id, newest_session_id, isolated)` removes
five parses and about 60 lines.

### OS13 · low · E — the scheduler thread does unrelated network and disk work every 30 s
`scheduler_routes.py:920-961`: two `load_projects()` calls per tick (every
project record parsed twice, to test two booleans), then GitHub sync and git
fetch inline. A sync that hangs stalls schedule dispatch, limit resume and
workflow waits, which share the thread. `_prune_claims()` globs and stats the
claims folder every tick (`:1038`). Not measured.

### OS14 · low · L — `steward_status` answers `fenced: true` as a literal
`steward_routes.py:100`. `_do_enable` can finish with the fence not installed
(`:150-153`, logged as "running unfenced"); the status route still says
fenced.

### OS15 · low · L — daily/weekly/cron next-run uses a fixed UTC offset
`scheduler_routes.py:377, 419`. `datetime.now().astimezone()` carries a fixed
offset, so the first fire after a daylight-saving change is one hour off
(computed the day before with the old offset). It self-corrects on the next
compute.

## Streamlining candidates

## Sound (notes)

## Progress log

- Read fully: `mc/blueprints/scheduler_routes.py` (1871), `mc/blueprints/steward_routes.py` (328),
  `mc/blueprints/steward_role_routes.py` (253), `mc/steward_role_writes.py` (383),
  `mc/steward_role_model.py` (155), `mc/steward_role_dispatch.py` (662),
  `mc/steward_role_comms.py` (576), `mc/steward_role_state.py` (328),
  `mc/steward_role_task.py` (123), `mc/steward_role_children.py` (120),
  `mc/steward_role_channel.py` (68).
- Dead-code note for streamlining: `steward_role_comms.raise_decision`, `resolve_unknown`,
  `default_mailer`, `compose_body`, `choose_route`, `_upsert`, `_finish` have no caller outside
  the module and its tests (searched `mc/`, the steward package, `tools/`, `server.py`,
  `static/`): about 300 lines (`:155-204`, `:319-576`). `check_pending_replies` therefore
  always walks an empty decision list.
- Tooling note: the unattended fence refuses a shell heredoc whose text names a protected
  path, so the report is written with the Edit tool only.
- Next: schedule_conditions / skip_log / delivery_failure, engine_*, allowance_*, limit_resume*,
  question_*, hivemind_*, usage_breakdown_*, model_upgrade*, wake_lock, automation_*, coordination.
