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

### OS16 · high · F/L — a question mail that fails to send is lost: one attempt, no retry, nothing in the chat, no other alert
`mc/question_channel.py:309-310` (the qid is marked delivered BEFORE any send),
`:389-398` (a `False` from `_send_email` writes one server-log line and returns),
`:217-233`. The grace timer is the only delivery attempt. There is no second
timer, the guardian's stuck-question branch only appends a line to the chat
buffer (`agent_routes.py:18914-18921`), and no push is raised for a question
(searched `agent_routes.py`). A later boundary for the same question returns at
`:280` because the qid is already in `_delivered`.
Reachable on a default install: `question_channel` defaults to `email`
(`server.py:183`), and with no mail credentials the mailer exits 2
(`tools/night-review/send_mail.py:305-310`). So on any install that never set up
mail, every unattended `mc:question` goes nowhere and the run parks for good;
on a configured install the same happens for the length of any SMTP outage.
The design reference says the channel must reach a human; the failure path
reaches a log file.
Smallest fix: on a failed send, remove the qid from `_delivered`, append a chat
line that says the question was NOT sent and why, raise the existing web push,
and re-arm the timer with a backoff. Test: `tests/test_question_channel.py`
covers a successful send and the attended skip; a failed send is not asserted
(to confirm when tests are read).

### OS17 · medium · L — the reply half of the question channel does not survive a restart, and says nothing
`question_channel.py:83-93` (`_outbox`, `_delivered`, `_answered` are process
memory), `:531-533` (an unknown qid returns with no log), `:639-643` (the poller
does not even open the mailbox when `_outbox` is empty). The mail body tells the
reader it may be answered days later and that the agent "stays parked until it
is answered" (`:145-146`, `:198-199`). A server restart between the question
and the reply (normal on an install that restarts to take updates) makes the
reply a no-op: no resume, no log line, no bounce. The agent-mail channel has a
durable owner record for exactly this (`:282-291`); the legacy SMTP channel,
which is the default, has none.
Smallest fix: persist `{qid, project_id, session_id, message_id, to, questions}`
next to the schedules file at send time and load it at start; log an
unmatched `q:` reply.

### OS18 · medium · L — a mailed multi-question form cannot be answered correctly, and a one-word reply can pick an option the reader did not choose
`question_channel.py:161-175` numbers options from 1 inside EACH question;
`:423-435` indexes one flat list across all questions; `_answer` sends a single
label (`:456-459`). **Measured** (scratch script, pure function): for a
two-question form, reply `2` returns question 1's second option and question 2
is never answered; question 2's options are reachable only as `3` and `4`,
which the mail does not show. The loose match at `:442-444` accepts any
substring either way: reply `the`, `e` or `old` to the options "Delete the old
backups" / "Keep them" returns "Delete the old backups". `1.` and `ok` return
nothing (left open, logged only).
Smallest fix: for more than one option-bearing question say in the mail that it
must be answered in the app (the free-text branch at `:190-195` already does
this) and refuse to match; drop the substring pass or require the reply to
equal a label after stripping punctuation; accept a trailing `.` or `)` on a
number.

### OS19 · low · L — an answer that fails to post is consumed
`question_channel.py:553-556` marks the qid answered, `:589` calls `_answer`,
and a non-2xx or an exception (`:462-471`) returns `False` without removing it.
`poll_replies` excludes answered ids (`:640-641`), so the reply is never tried
again and the session stays parked. The two "no match" branches do discard
(`:584-585`, `:599-600`); the failed-post branch is the one that does not.

### OS20 · low · E — the reply poller logs in to the mailbox every two minutes for as long as one mailed question stays unanswered, and the caches are only retired by a loop that is off by default
`question_channel.py:639-662`: while any legacy entry is in `_outbox` and not
answered, each poll opens an IMAP session and downloads up to 30 full messages.
An entry leaves `_outbox` only through `retention.retire`
(`question_channel_retention.py:142-182`), whose one caller is the agent-mail
recovery loop (`mc/agent_mail_recovery.py:375`), started only when
`agent_mail_supervisor_enabled` is true (`server.py:3822-3825`, default off).
On a default install nothing retires: one abandoned question means 720 logins a
day until the next restart, and the three caches grow for the life of the
process (the module docstring at `question_channel_retention.py:5` says they
"used to grow forever"; with the supervisor off they still do).
Fix: call `retention.retire()` from the poller loop.

### OS21 · low · M — `_creds()` swallows a state-file read
`question_channel.py:623-628`: `except Exception: return None`. A credentials
file that exists but does not parse reads as "mail not configured", silently.
Project policy: a JSON state load must log.

### OS22 · low · E — the limit-resume store is re-read and re-validated under an OS file lock on every operation, at least twice per turn of every session, and its rows are never pruned
`mc/limit_resume.py:116-142` (`_transaction` reloads and validates the whole
file), `:421-422` (`new_turn` → `clear`, every non-internal turn of every
session, from `mc/launch_transaction.py:252`), `:371` (`hold_completion` →
`get`, every turn and exit boundary). `limit_resume_dispatch.tick` adds
`2 + 2N` reads every 30 s for N rows (`:53,62,117,119`).
Rows that stay for ever, each with a full copy of the session's `log_lines`
(`:176-177`): an acknowledged continuation (`submitted` → `active`, `:208`)
with no deferred success is matched by none of the prune paths
(`prune_completed` needs `completion_done`, `:295`); `cancel` keeps the whole
snapshot (`:276`); a waiting row whose project was deleted is skipped on every
tick (`limit_resume_dispatch.py:83-84`). Only a later human turn in the same
session clears one (`:418-422`).
**Measured** (read-only, live install): 2 rows, both `cancelled`, 24 KB, six
days old. The cost is small today and grows with every unattended session that
hits a limit.
Fix: drop `active` rows without `completion_done` at the turn boundary that
finds them, trim the snapshot on `cancel` as `close` does (`:286-288`), and
cache the parsed file by `(mtime, size)`.
Related: `new_turn` calls `_store.clear` with no guard (`:421-422`); a lock
wait over 1 s (`:126-139`) or a file damaged after boot raises inside
`start_turn.advance` and fails that send. Read, not measured.

### OS23 · medium · L — one Gemini or Qwen error whose text contains "429", "quota" or "rate limit" blocks that vendor on every project for 24 hours
`mc/allowance_state.py:52-53` (`_TEXT_HINTS`), `:518-539`, `:550-560`.
A match records the vendor exhausted with no reset time; such a record "never
auto-clears; needs a success" (`:188-189`), and a refused vendor cannot have
one. Gemini and Qwen have no probe (`mc/allowance_probe.py:17-22`), so the only
exits are the 24-hour unverified transition and its single trial run
(`mc/allowance_unverified.py:36`, `:93-99`) or the manual "I topped up" control.
**Measured** (scratch script, pure function): `detect('gemini', …)` and
`detect('qwen', …)` return a hit for the error text "Tool failed: read 1429
bytes then EOF" (`'429'` inside a number), for "see quota docs", and for an
ordinary per-minute throttle ("HTTP 429 Too Many Requests: 15 requests per
minute"), which is a wait of one minute, not an allowance.
Same hazard class as pass 1 X6 and G5 (exit-hint classifiers); this is the
half that writes durable state.
Smallest fix: match `429` on a word boundary and require one of the allowance
phrases next to it; for a hit with no reset time and no structured field, set
a short expiry (minutes) instead of none, so a throttle heals by itself.
Test: `tests/test_allowance_state.py` (to confirm) asserts the hints match;
nothing asserts a non-allowance error is left alone.

### OS24 · high · L/F — a hivemind that was paused or stopped can never be resumed: its workstreams stay `paused` for ever
`mc/blueprints/hivemind_routes.py:813-846` (pause sets every `active`
workstream to `paused`; stop does the same to `active`, `pending` and
`blocked`), `:793-810` (start sets only the manifest back to `active`),
`:497-509` (`_hm_resolve_dependencies` takes `pending` only), `:1784-1849` (the
loop acts on `active`, `pending` and `blocked` only), `:512-517` (terminal needs
every workstream `completed` or `failed`). Nothing turns `paused` back into
anything; the Resume and Restart buttons call `/start` and nothing else
(`static/js/hivemind.js:37-39, 193-195`).
**Measured** (scratch script, the real routes on a temp directory): after
pause then start, the paused workstream is in neither the ready list nor a
terminal state; after stop then start, all three workstreams are `paused`, the
ready list is empty and the manifest says `active`. The run sits there for
good; only a hand-made `POST …/workstreams/<id>/status` moves it.
Also: neither route stops a process. Stop's docstring says "hard stop all
agents" (`:833`); the workers keep running and keep spending, and because the
loop skips a non-active manifest (`:1774`) and the workstream is no longer
`active`, a worker that finishes during a pause is never collected.
Smallest fix: in start, set each `paused` workstream to `active` if its
session is still live, else to `pending`; in stop, call the session stop path
for each live worker. Test: `tests/test_hivemind_routes.py:360-378` pins the
`paused` write; no test resumes afterwards.

### OS25 · high · L/F — a workstream marked `active` whose worker is gone, stopped or idle stays `active`, and that blocks the whole run
`hivemind_routes.py:1784-1816`. The loop collects a worker only when its
session is in `agent_sessions` AND its status is `completed` or `error`
(`:1788-1791`). Three normal cases are neither:
(a) the server restarted: `agent_sessions` starts empty, so `:1788-1789`
`continue`s on every tick. `_hm_reconcile_stale_on_startup` relabels the
MANIFEST `stale` after 24 hours (`:578-580`) and never touches a workstream;
a younger run stays `active` with dead owners. They count against
`max_concurrent_workers` (`:1390-1392`), so with three of them nothing else is
ever spawned;
(b) a person stops the worker's chat: status `stopped`
(`agent_routes.py:15254`), not collected;
(c) a legacy worker on the Claude engine runs in the streaming mode whenever
`use_streaming_agent` is on (`agent_routes.py:12891-12892`; pass 2, R45): at
turn end its status is `idle` (`agent_routes.py:6504`) and the process stays
up, so it becomes `completed` only if the worker itself posted
`status: completed`, or much later when the idle process exits
(`:6549-6550`), at which point `:1804-1807` files the workstream as completed
whether or not the work was done.
**Measured**: scratch script, three `active` workstreams with no session and
one `pending`: after the startup pass and one spawn pass nothing changed and
nothing was spawned. Live install (GET, counts only): 4 of 26 runs are `stale`
and each still holds 1 or 2 `active` workstreams, the oldest 237 days old.
Smallest fix: in the loop, an `active` workstream whose session is absent,
`stopped`, or `idle` past a grace period goes back to `pending` with
`retry_count + 1` (legacy) or to a held state with a reason (roster); run the
same pass once at startup. Test: none
(`tests/test_hivemind_failure_outcomes.py` covers `error`; nothing covers a
missing session).

### OS26 · high · F — what a person sends into a hivemind is not delivered, and a worker's blocker reaches nobody who is not looking at the dashboard
(a) Directive to the orchestrator, the default target (`:1694`,
`hivemind.js:605-609`): appended to the bus and pushed to open dashboards. The
orchestrator is a short-lived session whose prompt carries the goal, the
workstream list, the synthesis and decisions, and no bus message
(`:1272-1279`); `replan`, the one task type that takes extra context, has no
caller (`:1262`, searched). Nothing reads the message.
(b) Answering a worker's open question uses the same call and then marks the
question resolved (`hivemind.js:610-612`). The answer is addressed to
`orchestrator`, so the worker that asked never gets it. **Measured**: of three
bus rows (an orchestrator directive, a broadcast to `all`, a direct message)
`_hm_read_bus_messages(ws_filter='ws_0')` returns only the direct one
(`:376-378`).
(c) Directive to a workstream: it is read once, when the NEXT worker for that
workstream is built (`mc/hivemind_worker.py:428-435`). A running worker is
never told to poll: its capability list (`:448-466`) has no poll call and
`GET …/bus/poll/<ws_id>` has no caller anywhere (searched `mc/`, `static/`,
the builtin skills, the agent reference).
(d) "Synthesize now" posts `{content, to}`; the route wants `message` and
answers 400 (**measured**); the page shows "Synthesis requested"
(`hivemind.js:708-713`). No route starts a synthesis at all (the frontend half
belongs to pass 4's region; it is not in that report).
(e) A worker's escalation (`:1654-1680`, and the `escalation` bus type,
`:1505-1512`) is one line in the bus file and one event to dashboards that are
open at that moment. No push, no mail, no `mc:question`. `requires_response`
and `resolved` are written and never read or changed. Hivemind sessions are
unattended by the file's own account (`:1879-1882`), and the design reference
for this region is that a question must reach a human.
Smallest fix: (e) raise the existing web push on an escalation, and list
unresolved escalations on the run card; (a)/(b) route an answer to the
workstream that asked and include unread `user` messages in the next
orchestrator prompt; (c) put the poll call in the worker's capability list;
(d) add `POST …/synthesize` that calls `_hm_dispatch_orchestrator`. Test:
`tests/test_hivemind_routes.py:724-740` asserts the rows are written, not that
anything reads them.

### OS27 · medium · M (security) — a legacy workstream id is used as a file path without a check
`hivemind_routes.py:681` and `:884` take `id` from the request body;
`:173-187` build `workstreams/<id>.json` from it; `:1482-1483` take the bus
`from` field as a workstream id for `<id>_findings.jsonl` (`:298-300`).
**Measured** (scratch, temp directory): `POST …/workstreams/create` with
`id: "../../../escaped"` answers 200 and writes `escaped.json` three levels
above the run folder; a `finding_report` whose `from` is
`ws_/../../../../leak` creates `leak_findings.jsonl` outside it.
The usual caller is a model (the decomposing orchestrator, `:1239-1243`) whose
prompt contains the user's goal and other agents' findings. One `.json` file
landing in the project-records folder becomes a malformed project and breaks
both restart endpoints (the load-bearing rule in `CLAUDE.md`).
The roster path already refuses this (`mc/hivemind_roster.py:237-240`,
`tests/test_hivemind_roster.py:268, 312`); the legacy path, which is every
run on the live install (26 of 26, measured), never calls it. `hivemind_id`
from the URL has the same shape in `_hm_dir` (`:140-143`).
Smallest fix: one `_safe_id()` (the roster regex) applied inside `_hm_dir` and
the workstream path helpers. Same producer as pass 4 FE3.

### OS28 · medium · L — the closing synthesis is skipped after a decompose in the same server process, and an engine error leaves the orchestrator slot taken for good
Other half of pass 2 R45. `_hm_dispatch_orchestrator` returns `None` while the
id is in `_hivemind_orchestrating` (`:1199-1202`); the id is removed only by
the exit callback (`:1308-1311`) or a dispatch exception (`:1344-1347`). With
the callback lost on the Claude streaming path (R45), a run that was
decomposed by the orchestrator reaches `completed` and the synthesize call at
`:1868` does nothing: the synthesis file keeps "No findings yet".
Separately, `engine_selection.resolve_engine` (`:1282-1288`) is called after
the id is added and outside any `try`. An `EngineSelectionError` (an unknown
provider, or a model that belongs to another provider, both reachable from the
create body, `:650-652`) leaves the id in the set until restart and comes out
of `hivemind_create` as a 500 AFTER the manifest was saved (`:663`, `:712`):
the caller sees a failure, the run exists, is `active`, has no workstreams and
cannot be decomposed.
`max_turns=5` (`:1343`) for a task that asks for "3-8" separate create calls
may also cut a decomposition short. Read, not measured.
Smallest fix: wrap everything after the `add` in `try/except` that discards;
resolve the engine before saving the manifest in create.

### OS29 · medium · L — a spawn that provably launched nothing is filed as `launch_uncertain` and never retried
`hivemind_routes.py:1416-1433`, `:1839`. Any exception other than
`EngineSelectionError` blocks the workstream with `failure_reason:
launch_uncertain`, and the unblock pass skips a blocked row that has a failure
reason. Refusals raised before any process exists land here: the allowance
refusal and the blocked-fallback error from the dispatch choke point
(`agent_routes.py:12727-12752`), the generation check (`:1029-1036`), an
unknown provider (`:1040-1044`), a held roster launch
(`hivemind_worker.py:87-90`). The run then stays `active` with a blocked
workstream and no terminal state; the only signal is one dashboard event.
So a hivemind that meets a usage limit does not wait for the reset and does
not fail: it parks without telling anyone. (It does not switch vendor on its
own, which is the required half.)
Related, read not measured: a legacy pre-session is registered as `running`
with `process_alive: True` before the launch (`hivemind_worker.py:560, 576`;
`hivemind_routes.py:1051`) and `record_launch_failure` returns early for a
legacy session (`hivemind_worker.py:95-96`), so after a failed launch the next
tick's ownership pass (`:1365-1381`) can find that entry "live" and hand the
workstream to it.
Smallest fix: treat an exception raised before the session is registered as
"not launched": allowance refusals go back to `pending` with a next-try time
taken from the vendor's reset, the rest to `failed` with the reason.

### OS30 · medium · L/M — an unreadable workstream file is dropped from the list, and the run can then be declared complete without it
`hivemind_routes.py:196-201` (`except Exception: pass`, no log), `:512-517`,
`:1855-1868`. `_hm_terminal_outcome` runs on whatever the list returned. If the
one unfinished workstream's file fails to read on that tick (damaged, or held
for an instant while a route replaces it), the rest are all `completed`, the
manifest is saved `completed`, synthesis is dispatched and integration starts
with a workstream missing. The same silent `None` at `:163-164` and `:180-181`
makes a damaged manifest a 404 and a damaged workstream "not found". Project
policy: a JSON state load must log. Read, not reproduced.
Smallest fix: log, and have `_hm_list_workstreams` report unreadable files so
the terminal test refuses when there is one.

### OS31 · low · L — parts of the knowledge base that are written and never read, or read and never written
- Decisions: the only writers are the two review routes (`:1731`, `:1752`),
  which have no caller in `static/` (searched). The orchestrator prompt prints
  `d.get('decision')` (`:1226-1228`) and the worker prompt filters on
  `impacts` / `workstream` and prints `decision` / `rationale`
  (`hivemind_worker.py:438-445`); no record has any of those keys.
- `_hm_write_context` (`:480-483`) has no caller, so "ACCUMULATED CONTEXT"
  (`hivemind_worker.py:414-416`) is always empty.
- Config keys `auto_synthesize`, `synthesize_interval_turns`,
  `require_user_approval_for_decisions` (`:644-646`) are stored and read
  nowhere (searched `mc/`, `static/`, `server.py`); a finding's
  `user_reviewed` (`:1494`) is never set true.
- The worker prompt calls the handoff "REQUIRED before marking complete"
  (`hivemind_worker.py:459`); the status route does not check for one
  (`:967-988`), and a process exit marks completion without one (`:1804`).

### OS32 · low · M/E — route hygiene in the hivemind file
- `PUT /api/hivemind/<id>` and `PUT …/workstreams/<id>` store any `status`
  string and any `dependencies` value (`:783-785`, `:942-945`); a dependency on
  an id that does not exist can never be satisfied, so the workstream waits
  for ever (`:504-505`).
- `int(request.args.get('limit'))` raises on a non-number (`:1543`, `:1635`).
- `_hm_resolve_question` rewrites the file in place, not atomically, and one
  bad line raises (`:435-455`).
- `_hm_append_finding` re-saves the whole workstream record to bump a counter
  (`:301-305`), unlocked, against the loop's own saves: a finding posted while
  the loop marks the workstream completed can write the older status back.
- The bus and findings files are read whole on every call (`:358-382`,
  `:308-348`) and never trimmed. Small today (26 runs on the live install).
- Restarting a `completed` run re-dispatches synthesis and mints a second
  topic node on the next tick (`:799`, `:1856-1898`).

### OS33 · high · L/F — reviewed integration (roster runs) holds for good on timing that is normal, and a hold has no release anywhere in the product
`mc/hivemind_integration_cycle.py`. `held` is terminal (`:31`): `advance`
returns it untouched (`:286-287`), `start` refuses to drive it (`:454-455`),
and the orchestrator loop no longer visits the run because its manifest is
`completed` (`hivemind_routes.py:1774`). Every exception inside a tick becomes
a hold (`:429-431`). Four of those exceptions are ordinary timing:
(a) **The last worker is still in its turn.** A worker is told to mark its own
workstream complete with a curl (`hivemind_worker.py:463-465`; route
`hivemind_routes.py:967-988`), so the row is `completed` while the session is
still `running`. The loop ticks every 10 s (`:1760-1763`), sees every row
complete, sets the manifest and starts integration (`:1855-1872`). The first
tick calls `_native_idle` (`:317`, body `:144-167`), which refuses unless every
contributing session is `completed` or `idle` with no live process. The worker
has only to spend 10 more seconds writing its closing message.
(b) **The landing lock is busy.** `landing()` waits 5 s and raises
`LandingBusy` (`landing_serialization.py:105-112`, `:127-132`). The handler at
`:425-428` files that as `native standards held` and marks the worker branch
`awaiting_standards`: a busy lock is recorded as a standards failure.
(c) **The server restarts during a review.** `agent_sessions` is empty after a
restart, so `hivemind_reviewer.py:282-283` turns a pending review into `held`.
A review may run 45 minutes (`:30`).
(d) **The synthesis lands while a candidate is being smoked.** Synthesis is
dispatched at the same instant as integration (`hivemind_routes.py:1868`) and
its notify route rewrites `manifest['updated_at']` (`:1608-1609`). `advance`
hands `launch_reviewer` the manifest it read at `:331`, before the merge and
the smokes (the re-check at `:366` discards its return value);
`hivemind_reviewer.py:371-373` compares whole manifests and refuses with
`stale reviewer launch inputs`.
No release: the only writer of the state file is `_hm_write_integration`
(`hivemind_routes.py:241`), reached from `_save` and the legacy thread; there
is no route, and the file carries a digest (`:59-60`), so an edit by hand that
does not recompute it reads as corrupt, which is again `held`
(`hivemind_routes.py:236-238`). `docs/HIVEMIND_INTEGRATION_REVIEW.md` says
holds are sticky by design and that a coordinating agent "must arrange scoped
reconciliation"; no tool for that exists.
**Read, not measured** end to end (it needs a live run).
`tests/test_hivemind_integration_review.py:213-231` (`native`) and `:445` pin
that a session which is not finished produces the hold; nothing covers the
timing. Smallest fix: stay `running` and re-tick while a contributor is still
`running` (bounded by a grace period) and on `LandingBusy`; compare the
manifest's identity fields, not the whole dict; add one human-only route that
archives a held attempt (state file, branch, worktree) so the run can be
integrated again.

### OS34 · medium · L — legacy integration starts while the last worker may still be committing, runs once, and can never be run again
`hivemind_routes.py:279-293`, `mc/hivemind_integration.py:100-187`. The legacy
path has no check on worker sessions at all. Same trigger as OS33(a): the row
is `completed` before the worker's turn ends. Then either the worker tree is
dirty and the whole integration stops (`hivemind_integration.py:229-232`), or
it is clean at that instant, the tip is merged, and whatever the worker commits
afterwards is not on the integration branch while the state says `completed`.
One attempt only: `_hm_start_integration` returns for ANY existing state
(`:285-286`), including `error`, `stopped` and the `interrupted` a restart
writes (`:561-565`); and `integrate` refuses when the branch exists
(`hivemind_integration.py:147-149`), which it does after the first attempt
because only the worktree is removed (`:177-179`). A run that is restarted and
completes again is never integrated. **Read, not measured.** Smallest fix:
wait until the contributing sessions have left `running`; allow a new attempt
when the previous one did not succeed (a numbered branch, or reuse when the
branch holds no merge). Tests cover a single attempt only.

### OS35 · low · E — a pending review re-checks the whole run every 5 seconds
`hivemind_integration_cycle.py:436-442` drives `advance` every 5 s; while a
review is pending each tick runs `_check` (`:170-190`): `_hm_list_workstreams`,
three git processes on the integration tree, and a `status` plus a `rev-parse`
per queued workstream, then relinks the smoke dependencies (`:324-325`). With
5 workstreams that is 13 git processes per tick, about 7,000 over a 45-minute
review. **Read, not measured.** Smallest fix: while `awaiting_review` poll only
`reviewer_status`; `_check` already runs again when the verdict arrives
(`:403`).

### OS36 · low · L — a recorded pass is discarded if the reviewer's session then ends badly
`hivemind_reviewer.py:275-285` tests the session's state before the recorded
result: a reviewer that posted `pass` inside its deadline and then ends
`error`, `blocked` or `stopped` before the next 5 s poll is `held` (`:276-277`),
and the hold is permanent (OS33). Smallest fix: test `result` first. No test.

### OS37 · low · M — an operator's persona name is in strings the API returns
`mc/hivemind_routing.py:250` (`hold_reason`) and
`hivemind_integration_cycle.py:335` (the hold reason shown on the run) name a
specific agent persona that exists only on the authors' install; eight more
mentions are in docstrings of the same files. Project rule: nothing
operator-specific in the repo. Smallest fix: "the coordinating agent".

### Hivemind roster, routing, review modules: sound
`hivemind_roster.py`, `hivemind_fallback.py`, `hivemind_routing.py`,
`hivemind_lineage.py`, `hivemind_review.py` and
`blueprints/hivemind_review_routes.py` were read fully and no defect was
found. Reviewer identity is taken from the OS-attributed caller, never the
body (`hivemind_review.py:241-252`, `hivemind_review_routes.py:246-249`);
records are digest-checked and a corrupt one is a hold, never a pass
(`hivemind_review.py:161-202`); an unusable engine is a refusal, never another
vendor (`hivemind_roster.py:165-173`, `hivemind_fallback.py:1-5`); every
swallowed I/O error logs. Streamlining: `hivemind_roster.lookup_character`
(`:93-109`) has no caller but its own test (17 lines).
Of the 341 smoke suites, 32 name the live port; an integration smoke that is
one of those tests the running server, not the merged tree
(`hivemind_integration.py:69-91` runs it in the integration worktree). Count
measured, effect read.

### OS38 · medium · L — every agent with a live session is listed as its own sibling, at the top
`mc/blueprints/coordination_routes.py:347-362` excludes the caller by
`my_session_id`. The only caller passes `project.get('_coord_session_id') or ''`
(`agent_routes.py:5673-5675`), and nothing in `mc/` or `server.py` ever sets
that key (searched; one hit, the read). So the id is always empty, the
exclusion at `:361` never fires, and a session whose context is rebuilt while
it is `running` or `idle` (a resume, a rollover, a hand-off) gets its own task
back as an "open intention". Its overlap with itself is the maximum, so it
takes the first of the three slots under a header that says not to duplicate
that agent's work. **Measured**: scratch script, one live session, the same
task: with `''` the session is returned (score 4); with its id passed it is
excluded. **Observed** in this review session's own system prompt: its first
SIBLING ACTIVITY line was this pass's own dispatch. Smallest fix: pass the
`session_id` argument `_build_agent_context` already has. Tests: none sets or
reads the key.

### OS39 · medium · M (security) — `/coord/publish` puts unbounded, unvalidated text into other agents' system prompts, and one malformed event blinds the feed for everyone
`coordination_routes.py:612-638`. The route stores `agent` (`:628`) and
`targets` (`:631`) as sent: no length limit, no type check, no check that
coordination is enabled. `sibling_activity` keeps a completion even at zero
overlap (`:401-402`), cuts only `summary` to 200 characters (`:391`), and
`render_readfloor` prints `agent` and the first six `targets` whole
(`:434-437`) under a trusted-looking header inside the system prompt of every
other agent on the project (`agent_routes.py:5672-5678`). Any local caller,
including an agent that has just read a hostile page, can therefore write
kilobytes of its own text into its siblings' system prompts, and with two
words copied from a likely task it is promoted to the `OVERLAPS your task`
line (`:438-440`). **Measured**: one planted event produced an 8,270-character
block with the conflict marker.
Second half: `targets` that is not a list of strings raises inside the single
`try` that wraps the whole function (`:355`, `:385-387`, `:405-407`), so the
feed returns `[]` for every agent until that event leaves the last 100 lines
or turns six hours old. **Measured**: `targets: 5` → `can only join an
iterable` → empty feed. Smallest fix: in the route require
`targets` to be a list of at most 20 strings of at most 200 characters, cap
`agent` and `summary`, refuse when disabled; in the reader, skip a bad event
instead of failing the call. Tests: `tests/test_coordination*.py` cover the
happy path only.

### OS40 · low · L/E — coordination: what is written and never read, and what is never trimmed
- An explicit `intention` is stored and pushed (`:621-637`) but the read-floor
  takes intentions only from live sessions (`:359-375`) and skips every stored
  event that is not a `completion` (`:380`): publishing an intention changes
  no agent's prompt.
- `mc/blueprints/topics_routes.py:309-323` appends `TOPICS_REFRESHED` rows
  (no `type` key) to the same log; nothing in coordination reads them. On the
  live install they are 1,184 of 4,766 lines (**measured**, counts only), and
  they share the 100-line window `_read_events` gives the read-floor.
- `/coord/stream`, `_push_sse` and `_sse_queues` (`:49-51`, `:191-197`,
  `:672-699`) have no consumer: `static/` never names the path and the MCP
  tools use `roster`, `events` and `publish` only (`mc/clayrune_mcp/tools.py`).
- `events.jsonl` is append-only with no trim (`:112-116`): 1.9 MB on the live
  install; every context build reads all of it (`:119-138`), 3.2 ms
  (**measured**, read-only timing), plus two git processes (`:278`, `:235`)
  under the project lock.
- Completions come from `HEAD` of the project path (`:229-234`, `:274`). With
  worktree isolation on (it is on the live install), an agent's commits are
  invisible to its siblings until they are merged into the main checkout.
- `_read_events` (`:130-131`) and `_load_state` (`:177-178`) swallow a
  state-file read without a log line; an unreadable `state.json` then reads as
  "never reconciled", republishes the last five commits and drops the
  interrupt dedup list (`:286-313`).

### OS41 · medium · L — an unreadable suggestion store erases every latched "no" on the next list request

`mc/automation_suggestions.py:258-265` treats a store that will not parse as
empty, and the comment there says this is safe because "the write path below
only ever merges, so a later write cannot silently erase decisions it could not
parse". The write paths do not merge with the file, they merge with what
`_read_store` returned: `refresh_project` (`:319-331`), `prune_project`
(`:343-351`) and `record_decision` (`:300-305`) each read (empty), add to it
and write the whole object back. `GET /api/automation/suggestions` calls
`refresh_project` for every project (`mc/blueprints/automation_routes.py:86-96`),
so the first time the Scheduler dialog opens after the file is damaged, the
decisions are gone and every dismissed habit is offered again.

**Measured** (`_scratch`, temp store): three suggestions dismissed, file
replaced with a truncated object, one `refresh_project` call: decisions 3 → 0,
and the three dismissed ids are back in `suggestions`. This is the "no must be
durable" rail (CLAUDE.md, learning-system safety rails, rule 3) failing in the
one module that says it mirrors it.

Smallest fix: `_read_store` returns a distinct "unreadable" result and the
three writers return without writing when they get it (or move the bad file
aside to `*.corrupt` first and log it). No existing test covers it:
`tests/test_automation_suggestions.py` has restart and content-key tests
(`:264`, `:293`), none with a damaged store.

### OS42 · medium · L — an accepted suggestion is scheduled at a different hour from the habit, and from the hour the card shows

The miner converts each run's timestamp to UTC and takes the most common UTC
hour (`mc/automation_suggestions.py:188-190`, `:204`), then writes it as the
spec's `time` (`:227`). The card labels it "Daily at HH:00 UTC"
(`static/js/automation-suggestions.js:37-39`). Accept hands the spec to the
ordinary create path (`mc/blueprints/automation_routes.py:139`), and the
scheduler reads a daily `time` as the host's local wall clock
(`mc/blueprints/scheduler_routes.py:364-377`, `:419`). On a host eight hours
behind UTC, a job run by hand at 09:00 local is proposed as "17:00 UTC" and
then fires at 17:00 local: eight hours after the habit and eight hours after
what the card promised. Correct only on a host whose zone is UTC. Read, not
measured (the two conversions are a line each).

Smallest fix: bucket by local hour in the miner (`dt.astimezone()` with no
argument) and drop "UTC" from the card. `test_accept_creates_exactly_one_real_schedule`
(`tests/test_automation_suggestions.py:182`) checks that a row is created, not
when it fires.

### OS43 · low · L — the three-per-project cap is applied before the "already decided" filter, and "accepted" is latched like "dismissed"

- `mine_recurring_manual_tasks` cuts to the top three by count
  (`mc/automation_suggestions.py:236-237`) and `refresh_project` then drops the
  decided ones (`:325`). Once a project's three most frequent habits are
  decided, no fourth is ever offered. **Measured**: five qualifying habits,
  top three dismissed, next refresh adds 0 and the pending list is empty.
  Fix: pass the decided set into the miner, or cut after the filter.
- The module docstring says an accepted suggestion becomes proposable again
  when its schedule is deleted (`:32-35`). `refresh_project` skips any
  fingerprint in `decisions` whatever the decision was (`:325`), so it never
  does. Either the docstring or the check is wrong; the test
  (`tests/test_automation_suggestions.py:236`) only covers the case where the
  schedule still exists.
- Two accepts of one suggestion arriving together both pass `get_pending`
  (`mc/blueprints/automation_routes.py:124`) before either records the
  decision (`:145`), and two schedules are created. Narrow; the fix is to
  record the decision under the store lock before creating, and undo it on a
  failed create.

### OS44 · low · E — the list request mines every project's whole agent log twice, and is a GET that writes

`_refresh` mines once to learn the live ids (`mc/blueprints/automation_routes.py:74`)
and `refresh_project` mines the same entries again (`mc/automation_suggestions.py:317`);
`_load_schedules()` is read once per project (`automation_routes.py:58`);
`load_projects()` twice per request (`:91`, `:103`); the store file is read and
parsed under the lock twice per project (`prune_project`, `refresh_project`)
and once more for the list. All of it runs on each open of the Scheduler
dialog (`static/js/scheduler.js:82`) and again after each accept or dismiss
(`static/js/automation-suggestions.js:80`, `:91`). Bounded by dialog opens, so
low. `is_decided` (`automation_suggestions.py:282`) has no caller in `mc/`,
`server.py` or `static/` (searched). Read, not measured.

### OS45 · high · L/M — an auto-upgrade of a character's model rewrites the whole file and drops every frontmatter key the character module does not model

`mc/model_upgrade.py:228-238` applies an approved upgrade by reading the
character and calling `characters.write_character(..., overwrite=True,
engine=engine)`. That function rebuilds the frontmatter from a fixed list
(name, description, agent name, avatar, skills, emojis, engine keys:
`mc/characters.py:451-510`) and writes the file whole (`:517-519`). Any other
key in the file is gone afterwards.

**Measured** (`_scratch`, temp agents directory): a file with `tools: Read,
Grep` and `color: blue` plus a provider and model pin; after
`_character_apply('global', name, None)('new-model')` the file holds only
name, description, provider and model. Global characters live in the
directory the Claude CLI reads its own subagent definitions from, where
`tools:` is the list that limits what that subagent may call. Removing the
line widens it to every tool. The job that does this is unattended by design
(`POST /api/model-upgrades/run` from a daily scheduled run,
`tools/cli-version-check.py:563-574`), on by default
(`mc/blueprints/model_upgrade_routes.py:124`), and its audit line records the
model change only (`model_upgrade.py:504-509`).

Second effect, same write: a built-in character is tracked by a hash marker
beside its file, and a file whose hash differs from the marker is treated as
user-edited and never updated again (`mc/characters.py:594-597`). One
auto-upgrade therefore takes a built-in out of built-in updates for good.
Read, not measured.

The drop is `write_character`'s (outside this region; the human editor has
it too, where a person at least chose to save). What is in this region is
that an unattended job triggers it across every pinned character at once.
Smallest fix here: change the one `model:` line in place (read the text,
replace the frontmatter value, write) instead of round-tripping through
`write_character`, and rewrite the built-in marker when the file was in sync
before the change. No test covers it: the model-upgrade tests build
characters with modelled keys only.

### OS46 · medium · M (security) — the price gate is decided by numbers any caller can post, with no bound, and "run" has no caller check

The rule is "upgrade only when the newer model costs the same or less"
(`mc/model_upgrade.py:5-6`, `:178`). Both prices come from
`POST /api/model-upgrades/prices`, which is open to agents on purpose
(`mc/blueprints/model_upgrade_routes.py:14-20`, `:81-99`) and validates only
"not negative" (`model_upgrade.py:94-95`). `POST /api/model-upgrades/run`
(`:120-137`) has no human or caller check either. So one session that posts a
large price for the old model, or zero for the head, and then posts `run`,
moves every pinned character, project, global default, schedule and workflow
step to the family head whatever it really costs. The module's statement that
the write "is never attacker-controlled" (`:18-20`) is true of the value
written and not of the decision to write it.

**Measured**: `set_price` accepts `inf` and `nan`; with the old model at `inf`
the comparison at `:178` approves any head price.

Related, read: `verified_at` is stored (`:100`) and never read, so a price
recorded once approves upgrades for ever; and the docstring's "fails closed on
... no discovery source" (`:7`) is not what `evaluate_pin` does: it takes the
head from `model_choices()` (`:159`, `:168`), which falls back to the static
list in source (`mc/agent_runtime.py:1021-1033`, `:1039-1041`), and never
calls `discover_models()`.

Smallest fix: refuse non-finite prices and require a source URL; make `run`
with `apply` true refuse an agent caller the way the character routes do (the
scheduled run can still post prices and read `/report`), or cap what one run
may change and mail the list. `tests/` has gate tests with honest prices only.

### OS47 · medium · L — the upgrade writers go round the write discipline of the stores they change

- Global default: `_global_apply` (`mc/model_upgrade.py:311-317`) opens
  `config.json` with mode `w` and `json.dump`s the live config dict into it.
  The settings route writes the same file atomically
  (`mc/blueprints/settings_routes.py:495`, `:548`). A crash or a
  non-serializable value mid-dump leaves the install's whole configuration
  truncated.
- Schedules: `_schedule_apply` (`:334-343`) is a bare load, edit, save of the
  whole schedules file. Every other schedule mutation runs under
  `serialized_write`, and a steward-role row may only be changed by a human
  (`mc/steward_role_writes.py:140-159`); this path has neither, so it can
  change a role row's model without the human gate, and it is one more writer
  in the lost-update race of OS2.
- Projects: `_project_apply` (`:276-284`) loads the whole project record, sets
  one key and saves it, without the project write lock that
  `steward_role_writes` takes for the same record (`:169`). A backlog edit
  between the load and the save is undone.

Read, not measured. Smallest fix: `write_json_atomic` for the config; route
the schedule write through `serialized_write` and skip role rows; take
`_project_write_lock` around the project write.

### OS48 · high · E/F — the Usage Breakdown panel cannot load for Claude: one request computes for about 100 s, the browser gives up at 25 s, and the cache that was meant to absorb it is invalidated every minute
`mc/usage_breakdown_aggregate.py:903-953` (the pair loop), `:883-902`,
`:1287-1295` (`_memoized`), `:1324-1331` (the cache key);
caller `mc/blueprints/system_routes.py:1470-1471`; client
`static/js/system-status.js:906-916`.
`_eligible_intervals` walks every consecutive pair of allowance samples in the
90-day history (`:903`) and, for each pair, every session's whole segment list
(`:922-934`): three list scans per session per pair, one of them
`t not in contained`, which compares dicts. The comment at `:885-887` sized
this for "~2000 pairs". The store on this install holds 29,046 samples for the
Claude 7-day window, 1,412 sessions and 24,115 segments, so the loop is about
29,000 x 24,000 segment tests.
**Measured** (scratch copy of the store taken with the sqlite backup API,
source opened read-only, `_scratch/os_ub_profile.py`): fetch 0.30 s, digests
0.21 s, evidence for all sessions 0.85 s, `filter_facts_in_range` 0.83 s,
`compute_turn_sizes` 0.05 s, **`_eligible_intervals` 95.76 s** for 28,987
intervals. The same request against the running server
(`GET /api/system/usage/breakdown?provider=claude&window_kind=7d`) answered in
**103.1 s**. The Codex window (4,776 samples) answered in 5.8 s.
Three things turn a slow function into a feature that does not work:
1. The client aborts at 25 s and retries once (`system-status.js:906-916`), on
   the stated assumption that the retry "lands on a warm cache". Both attempts
   abort; the panel shows its error state.
2. The cache key includes a digest of the calibration samples (`:1326`). The
   sampler adds a sample every 60 s while any session runs and every 300 s
   idle (`usage_breakdown_sampler.py:356-359`), so the key changes more often
   than one compute takes. A warm hit needs a second request inside the same
   sampling minute as the end of a 100 s compute.
3. `_memoized` holds `_memo_lock` across the compute (`:1288-1292`) and the
   lock is one lock for every key. An aborted request keeps computing, and
   every later breakdown request, for any provider or window, waits behind it.
   Each one then starts its own compute with the newer digest. The work is
   pure Python, so for those 100 s every other request on the server shares
   the interpreter with it. Pass 4 reported that hovering a provider bar
   starts two of these requests; that hover is the trigger for this cost.
Growth: samples and segments both grow with days of history up to the 90-day
prune, so the cost grows with the square of the history until it levels off.
This install is at 73 days.
Smallest fix: sort the segments once by start time and advance a pointer with
the sample pairs (both lists are time-ordered), so each pair looks only at
segments that can overlap it; that is linear in pairs plus segments. Then key
the calibration cache on something that does not change every minute (the
history up to the previous hour) and compute outside the lock, with a
per-key in-flight marker.
Test: none would catch it. `tests/test_usage_breakdown_speed.py` counts sqlite
connections and cache hits on small fixtures and never runs the pair loop at
size; its `test_new_allowance_sample_invalidates` (`:160`) pins the
once-a-minute invalidation as the intended behaviour, so the cache fix must
change that test.
Pass 4 `SS4` (two computes per bar hover) is the client half of this.

### OS49 · low · L — engine fallback: a second swap can follow the first, and the availability probe swallows its failure
`mc/blueprints/agent_routes.py:12753-12758, 12795-12811, 4005-4016`;
`mc/engine_fallback.py:59-87`.
Read, not measured (the module is sound; these are in its one caller, cited
only as far as needed).
(a) After an allowance swap from vendor A to B (`:12753`), the fallback entry's
own model goes through `_model_quota_blocked` (`:12795`). If that model is
blocked too, `_apply_engine_fallback` runs again with B as the blocked vendor
(`:12806`). The record kept on the session is then "B to C" and the chat line
carries two prefixes; the first hop is in the activity log and a push only.
When the first trigger was a model block and not an exhausted allowance, the
second walk may return A, the vendor the dispatch started on, with a
different model, because `resolve_fallback` skips only the vendor passed in
and vendors with an exhaustion record (`engine_fallback.py:75-78`). Every hop
is logged and pushed, so nothing here is silent; the defect is that the kept
record names the wrong starting vendor. Smallest fix: allow one swap per
dispatch (`if _engine_fallback_record: raise EngineFallbackBlocked`).
(b) `_provider_available_for_fallback` catches every exception from
`health_check()` and returns False without logging (`:4014-4015`), so a
fallback entry that is skipped because its probe crashed looks the same as one
that is signed out. `resolve_fallback` has a logging handler for exactly this
(`engine_fallback.py:82-85`) that can never fire, because the probe it wraps
never raises. Project policy: a swallowed subprocess failure must log.
Test: `tests/test_engine_fallback.py` covers the single swap; not the chain.

### OS50 · low · L — pace routing treats a weekly reading above 100 as no reading
`mc/engine_pace.py:34-38, 48-51`.
`_percent` rejects any value outside 0 to 100, so a vendor bar that reports
more than 100 makes `weekly_window` return None and `decide` keeps the
original character. **Measured** (real `decide`, alternate configured, reset
three days out): 86 reroutes, 100 reroutes, 100.5 and 104 stay as asked. The
reroute stops at the moment the vendor is most over. Whether a vendor ever
reports above 100 was not determined; the allowance refusal still applies in
that state, so the effect is a refusal where a reroute was configured.
Smallest fix: clamp to 100 instead of rejecting. Test: none for this input.

### OS51 · low · L — keep-awake on Linux reports "engaged" for an inhibitor that exited at once, and repeats that every 20 s
`mc/wake_lock.py:89-105, 131-138, 161-178`.
Read, not measured (no Linux host; starting the inhibitor is outside this
review's rules).
`_engage_subprocess` returns True as soon as `Popen` succeeds. If
`systemd-inhibit` starts and exits straight away (the session may not take an
inhibitor lock, or the service is not running), `set_active` logs "engaged".
On the next tick `_inhibitor_dead()` is true, so it logs "inhibitor process
died" and engages again: two log lines every 20 s for as long as the setting
is on, and the machine is free to sleep the whole time. When the binary is
missing the "not found" line repeats at the same rate (`:99-102`). Nothing in
the product shows the state; the log is the only place.
Also: `keep_awake_poll_s` is read once at start (`server.py:3816-3819`), so
only the on/off flag is live, and a non-numeric value there stops the
reconciler from starting at all.
Smallest fix: after `Popen`, wait a short moment and treat an exited child as
a failed engage; log a repeated failure once until the state changes.
Windows path **measured**: `SetThreadExecutionState` returned a non-zero
previous state on engage and on release from a fresh thread, so the two
"returned 0" checks (`:66, :79`) do not misfire on the first call.

### Engine selection, fallback, pace, run-role projection, wake lock, usage store and sampler: sound
- `mc/engine_selection.py`: explicit selections never change silently; an
  unknown provider raises with "No fallback was attempted" (`:137-140`); an
  inherited foreign model is dropped to the native default and an explicit one
  raises (`:197-205`).
- `mc/engine_fallback.py`: off unless the user wrote an order
  (`:45-47`); a resume never crosses vendors (`agent_routes.py:12750-12752`);
  the swap is recorded in the activity log, a push, the chat line and the
  session record from one place (`agent_routes.py:4046-4061`). The constraint
  "usage-limit handling must never silently fall back to another vendor"
  holds on every path read.
- `mc/engine_pace.py`: pure, no I/O, reroutes only to an alternate the user
  named, never recursively (`:81-84`).
- `mc/hivemind_run_roles.py`: read-only and fail-closed. `accepted` needs a
  completed integration, an exact passing review per merged workstream and no
  workstream moved since it was pinned (`:264-268, 291-301, 318-325`); a
  projection error returns `unverified`, never green (`:351-360`).
- `mc/usage_breakdown_store.py`, `mc/usage_breakdown_sampler.py`: writes go
  through one connection helper in WAL mode with a 30 s busy timeout
  (`usage_breakdown_store.py:418-423`), the 90-day prune runs in batches
  (`:848-859`), and the store read for one breakdown request is 0.30 s
  (measured, in OS48). No finding beyond OS48, which is in the aggregate.
- `mc/hivemind_native_workflow.py` has no production importer (see
  Streamlining); it was read at structure level only.

## Measurements

- OS48: `_scratch/os_ub_profile.py` and `_scratch/os_ub_counts.py`; two timed
  `GET`s of the breakdown route (Claude 7-day, Codex 7-day).
- OS50: real `engine_pace.decide`. OS51: one `SetThreadExecutionState`
  engage/release pair on a scratch thread (per-thread state, cleared on exit).
- OS45: `_scratch` script, temp agents directory, real `_character_apply`.
- OS46: `_scratch` script, `prices_path` pointed at a temp file.
- OS41, OS43: `_scratch` script against `mc.automation_suggestions` with a temp
  store path; results quoted in the findings.
- OS38, OS39: `_scratch` script importing the coordination module with a temp
  `COORD_DIR` and a stub session manager; results quoted in the findings.
- OS24, OS25, OS26, OS27: `_scratch` script driving the real blueprint through a
  Flask test client on a temp directory; results quoted in the findings.
- `GET /api/hivemind/list` on the live install: 26 runs, 144 KB, 0.10-0.16 s
  warm (1.19 s on the first call). Not a finding.
- OS1: `_compute_next_run` for an `interval` row of 60 minutes whose `last_run`
  is 3 hours old returns now + 5.0 s; with no `last_run`, now + 5.0 s
  (scratch script against the real function). The loop ticks every 30 s, so a
  failed interval row is due again on every tick.
- OS11: `cron` with an unparseable expression → `None`; `once` with no
  `run_at` → `None`; `daily` with `time: "bogus"` → 09:00 local;
  `daily` with `time: "25:00"` raises `ValueError` (same script).
- OS18, OS23: see the findings.

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
- Also read fully (second batch): `mc/schedule_conditions.py`, `mc/schedule_skip_log.py`,
  `mc/schedule_delivery_failure.py`, `mc/question_channel.py`, `mc/question_channel_retention.py`,
  `mc/question_callback.py`, `mc/limit_resume.py`, `mc/limit_resume_completion.py`,
  `mc/limit_resume_dispatch.py`, `mc/allowance_state.py`, `mc/allowance_probe.py`,
  `mc/allowance_unverified.py`.
- Rollover 2 (2026-11-16): `engine_selection`, `engine_fallback`, `engine_pace` and
  `hivemind_routes` were read before the rollover but their notes were not yet in this file;
  they are being re-read, findings written as found.
- Rollover 3 (2026-11-16): read fully and written up: `hivemind_routes`, `hivemind_worker`,
  `hivemind_roster`, `hivemind_fallback`, `hivemind_routing`, `hivemind_lineage`,
  `hivemind_integration`, `hivemind_integration_cycle`, `hivemind_review`,
  `hivemind_review_routes`, `hivemind_reviewer` (OS24-OS37).
- Rollover 4 (2026-11-16): `coordination_routes` written up (OS38-OS40). `automation_routes`
  and `automation_suggestions` were being read when the context rolled; re-reading them.
- Next: `hivemind_run_roles`, `hivemind_native_workflow`, engine_* (re-read: notes lost),
  coordination_routes, automation_*, model_upgrade*, usage_breakdown_*, wake_lock, then the
  backlog cross-check, then the assembled sections.
