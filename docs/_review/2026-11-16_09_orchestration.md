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

## Measurements

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
- Next: schedule_conditions / skip_log / delivery_failure, engine_*, allowance_*, limit_resume*,
  question_*, hivemind_*, usage_breakdown_*, model_upgrade*, wake_lock, automation_*, coordination.
