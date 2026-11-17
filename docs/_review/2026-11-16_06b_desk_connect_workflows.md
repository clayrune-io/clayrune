# MC-1179 pass 06b: Desk Connect and Workflows

Date: 2026-11-16. Review only; no source file changed.

Scope: `mc/desk_connect/` (108 Python files, 16,784 lines, plus 17 JSON
data files), `mc/workflows.py`, `mc/workflow_mail.py`,
`mc/blueprints/workflow_routes.py`.

Finding-id prefix: `DCW`.

STATUS: IN PROGRESS. Sections below are filled as files are read. The
ranked summary, feature table and streamlining section are written last
and sit above the detail once complete.

## Working notes (candidate findings, in reading order)

### Workflows: `mc/workflows.py` (2,366 lines, read fully), `mc/blueprints/workflow_routes.py` (253, read fully), `mc/workflow_mail.py` (15, read fully)

Callers followed: `mc/blueprints/scheduler_routes.py` (fire path 690-720, tick
895-917, create/update 1440-1640, run-now 1678-1710, runs page 1838-1855),
`mc/blueprints/agent_routes.py` (`_maybe_notify_spawner` 9204-9289,
`_notify_workflow_step` 9597-9640, question pause 6291-6307),
`mc/blueprints/secrets_routes.py:132-179` (`_require_human_passcode`),
`mc/model_upgrade.py:372-387`.

Reproductions are a scratch pytest file (not committed, `_scratch/` is
ignored) that builds its own Flask app from `workflow_routes.bp`, points the
stores at a temp directory and replaces dispatch with a recorder. It does not
import `server`.

#### DCW-W1 · high · L — the 30-second reconciler completes a step that is paused on a question (measured)

`mc/workflows.py:2246-2266` (`_step_session_verdict`), `:2087`
(`_STEP_END_STATUSES`), `:2343-2348` (`reconcile_running_steps`).

When a workflow's agent asks a question, `agent_routes` sets
`waiting_for_question = True` and forces the session status to `idle`
(`agent_routes.py:6297-6301`). `_maybe_notify_spawner` knows this is a pause
and returns before the workflow callback (`agent_routes.py:9256-9261`: "A
pause is not a finish"). The reconciler does not know. `idle` is in
`_STEP_END_STATUSES`, so `_step_session_verdict` falls past the "still
working" test at `:2247`, finds either an agent-log row tagged `idle`
(written for a question pause on the exit path) or a live session with no row,
and returns `('ended', 'idle', <last reply text>)`. `reconcile_running_steps`
replays that through `on_agent_step_complete`, where `idle` is a success
status (`:117`, `:1697`).

Result, measured both ways (row still `in_progress`, and row `idle`): on the
next scheduler tick the step is `completed`, its `output` is the question
text, and the next step is dispatched with that text substituted into its
prompt (`'use Which account should I use? A or B'`). When the human later
answers and the agent really finishes, `on_agent_step_complete` returns
`'ignored'` because the step is no longer `running`, and
`_notify_workflow_sent` is permanent, so the real answer never reaches the
run. An action step downstream (`notify_operator`, `backlog_create`) runs on
the non-answer.

This is the unattended case the question channel exists for: a scheduled
workflow whose step emails a question is advanced within 30 seconds, before
anyone can answer.

Smallest fix: in `_step_session_verdict`, return `'in_flight'` when the live
session has `waiting_for_question` (or non-empty `pending_questions`), before
the agent-log lookup. One test in `tests/test_workflow_step_reconcile.py`.

Existing tests: none would catch it. `test_workflow_step_reconcile.py`,
`test_workflows.py` and `test_workflow_runtime_notify.py` contain no case
with a question (grep, zero hits);
`test_a_live_idle_session_without_a_row_advances_with_the_hook_summary` pins
the behaviour that causes it.

Other half of pass 01 **A1** (`_collect_trailing_reply_text` drops lines
starting with `[`): A1 makes a real completion's output wrong; this one makes
a non-completion count as one. Both end with the next step running on text
that is not the step's answer.

#### DCW-W2 · high · L, M (authority) — running and scheduling a workflow are open to any caller, and the definitions file is trusted as-is (measured)

- `mc/blueprints/workflow_routes.py:180-194`: `POST /api/workflows/<id>/run`
  calls `start_run` with no `_refuse_if_agent_caller`. The docstring says
  this is deliberate ("running an already-authored, human-approved workflow
  does not expand any agent's capability set").
- `mc/blueprints/scheduler_routes.py:1576-1581` (`POST /api/schedules`) and
  `:1584` (`PUT`): a body with `workflow_id` creates an enabled recurring
  schedule for that workflow. `_build_schedule_from_spec` (`:1471-1561`)
  checks only that the workflow exists (`:1485`). No Origin check, no
  passcode. `grep -n "Origin|passcode|_refuse"` over the file: no hits.
- `:1678-1707` (`POST /api/schedule/<id>/run-now`): same, ungated.
- `mc/workflows.py:242-253` (`_read_definitions`) reads the definitions file
  on every call with no record of who wrote a definition. `:1305` treats a
  record with no `enabled` key as enabled; `:282` makes `enabled` default to
  `True` on create.

The standing rule is that only a human may enable, schedule or run a workflow
unattended. What holds: definition create/update/delete, draft, decision and
cancel are behind Origin plus the retyped passcode (`:69-86`), and I found no
HTTP path by which an agent can save a definition. What does not hold:

1. **Schedule.** Any session with `curl` can put an existing workflow on any
   cadence. The agent pointer card documents `POST /api/schedules` for agent
   use; adding `workflow_id` is one field.
2. **Run.** Any session can start any enabled workflow, and re-start it as
   soon as the one-live-run guard clears.
3. **Author, then run.** The gate is on the route, not on the store. Measured:
   with `POST /api/workflows` refusing a no-Origin caller (403), a record
   written straight into the definitions file (no `enabled` key) was started
   by a no-Origin, no-passcode `POST /api/workflows/<id>/run` (200, one
   dispatch). Agents here run with file tools; nothing in `start_run`
   distinguishes that record from one a human saved.

Rated high rather than blocker because path 3 needs a direct file write (an
agent that can do that has other options), and paths 1 and 2 act on
definitions a human authored. It is still the rule as written, broken on two
of its three verbs.

Smallest fix: (a) apply the `_refuse_if_agent_caller` check to schedule
create/update when the merged row has `workflow_id`, and to run-now for a
workflow row; decide explicitly whether manual `run` stays open, and if it
does, say so in the standing rule; (b) stamp definitions the human route
saved (for example an HMAC over the record with a key kept outside the data
directory, checked in `start_run`), or at minimum treat a missing `enabled`
as disabled.

Existing tests: `tests/test_workflows.py` asserts the run route is open (by
design). Nothing tests a schedule create with `workflow_id` from a no-Origin
caller.

Related, not re-reported: `mc/model_upgrade.py:372-387` rewrites a step's
`model` through `update_workflow` from `POST /api/model-upgrades/run`, a
second definition writer outside the human gate (one field, value chosen by
server logic). Platform pass territory.

#### DCW-W3 · medium · M (untrusted input) — `journal_append` and the self-HTTP actions use rendered slot values as path parts with no check (measured)

`mc/workflows.py:1884-1906` (`_journal_append`), `:1961-2002`
(`_execute_action`), `:1956-1957` (rendering).

Action `config` values are templates. A value such as
`{{steps.triage.result.item}}` is filled from an agent's reply, which is
untrusted text. `item_id` then goes into `journal_dir.glob(f'{item_id}-*.md')`
and `journal_dir / f'{item_id}-{slug}.md'` unchecked. Measured in a temp
directory:

| `item_id` | effect |
|---|---|
| `*` | appended to the first existing journal of another item |
| `MC-[0-9]` | same (glob character class) |
| `../victim` | appended to `docs/victim-notes.md`, outside the journal directory |
| `../../planted` | created `planted-x.md` two levels above the journal directory |

The file name must contain a hyphen and end `.md`, and the parent directory
must exist, so this is "append to, or create, a hyphenated Markdown file
anywhere the process can write", not arbitrary write. Rules, memory and skill
files are Markdown.

The same rendered values are placed unescaped into URLs:
`/api/project/{pid}/backlog` (`:1963`), `/backlog/{item_id}` (`:1973`),
`/api/backup/restore-point/{pid}` (`:2001`). A `?` or `#` in the value
changes which resource the POST or PATCH hits (read, not measured).

Smallest fix: one id pattern (`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`, no `..`)
checked on `project_id` and `item_id` after rendering, failing the step; use
`glob.escape` for the journal lookup.

Existing tests: `tests/test_workflows.py` covers `journal_append` with clean
ids only.

#### DCW-W4 · medium · L — `notify_operator` and the approval mail pass the body on the command line; a long step output fails the send on Windows (measured)

`mc/workflows.py:1927-1930`. `[sys.executable, mailer, '--subject', subject,
'--body', body]`. The canonical use is mailing a step's output
(`{{steps.X.output}}`, the incident in the comment at `:120-124`). Measured
on this platform: a 30,000-character argument runs; 33,000 raises
`FileNotFoundError [WinError 206]`. The exception is caught at `:1935`, the
action returns failed, and the run ends `failed` at the notify step, which is
usually the step whose job was to tell someone. A body that starts with `-`
and has no space is also rejected by `argparse` (read, not measured).

Smallest fix: write the body to a temp file and pass `--body-file`, which the
mailer already accepts.

Existing tests: sends are suppressed under pytest (`:1918`), so none.

#### DCW-W5 · medium · E, L — one global lock is held across dispatch, self-HTTP and the mail subprocess (read, not measured)

`mc/workflows.py:99` (`_runs_lock`), `:1439-1526` (`_advance_run`).

Inside the lock: `_dispatch_step` (context build and process launch,
`:1572`), `_execute_action` (HTTP to this server, 30 s timeout per call,
`:1944`; `desk_harvest` is a network scan), and `_notify_approval_waiting` /
`notify_operator` (subprocess, 60 s timeout, `:1930`). The lock is not per
run. While it is held:

- every other run's completion callback waits (`:1651`);
- `cancel_run`, `resolve_decision` and `start_run` for any workflow wait;
- `resume_due_waits` (`:1810`) waits, and it is called inline from the
  scheduler loop (`scheduler_routes.py:916`), so the tick that fires every
  other schedule on the install is stalled for the same time.

A chain of three action steps is three sequential 30-second windows under one
acquisition, since the loop at `:1457` does not release between steps.

Smallest fix: a lock per run id for the read-modify-write, and do the side
effect outside it (mark the step `running`, write, release, execute, re-take,
record). That also closes DCW-W7.

#### DCW-W6 · low · E — run files are never pruned, and each scheduler tick parses all of them four times (measured)

`mc/workflows.py:1206, 1807, 2054, 2110, 2133, 2155, 2333`: seven
`glob('*.json')` loops, each reading and parsing every run file. There is no
delete anywhere in the module (the spec left retention "Open"). Per 30 s
tick: `running_run_session_steps` and `running_run_updated`
(`agent_shim_completion_retry.py:76, 98`), `reconcile_running_steps`
(`scheduler_routes.py:911`), `resume_due_waits` (`:916`, under the lock, one
acquisition per file). `list_runs` is also a full parse on every `start_run`
(`_has_live_run`, `:1225`) and on every Runs page, which loads up to 1,000
whole run records to return 50 (`scheduler_routes.py:1843-1845`).

Measured on this install: 48 run files, 560 KB, 5 ms per warm scan, so about
20 ms per tick today. Each run file carries the full definition snapshot and
every step's output, and the cost is linear in runs ever made. A workflow on
a 15-minute schedule adds about 35,000 files a year.

Smallest fix: one scan per tick shared by the three consumers, with terminal
runs moved to a `done/` subdirectory (or an index of live run ids), plus
retention (spec recommendation: last 50 per workflow or 30 days).

#### DCW-W7 · low · L — an action's side effect happens before its result is saved (read, not measured)

`mc/workflows.py:1513-1523`. After `_execute_action` the step is marked
`completed` in memory and the loop continues; the run is written only when
the loop next blocks or ends. A crash or an uncaught exception in between
leaves the step `pending` on disk, and `_adopt_runs_on_startup` (`:2063-2066`)
calls `_advance_run`, which executes the action again: a second backlog item,
a second operator mail. Fix: `_write_run(run)` before `continue`.

#### DCW-W8 · low · M — `validate_workflow` says "Never raises" and raises on four malformed shapes (measured)

`mc/workflows.py:403-404`. Measured: `outcomes: [{…}]` → `TypeError` at
`:457`; a `wait` node whose `config` is a list → `AttributeError` at `:472`;
an edge whose `from` is an object → `TypeError` at `:498`; an action whose
`config` is a list → `AttributeError` at `:538`. Callers catch only
`ValueError` (`workflow_routes.py:104, 117, 173`), so create, update and the
ungated review route return 500. `outcomes: [1, 2]` passes validation and
then raises `TypeError` in `', '.join(outcomes)` at `:1570`, inside
`_advance_run`, leaving the run `running` with nothing in flight until
someone cancels it. Human-only routes, so low. Fix: type checks at the top of
each node branch.

#### DCW-W9 · low · L — smaller items in the workflow engine

- **Slots are half-validated.** `:543-557` checks `steps.*` and `prev.*`
  only. `{{trigger.typo}}`, `{{run.x}}`, `{{anything}}` and
  `{{steps.a.result.key}}` for an undeclared key save cleanly and fail the
  run at that step, after earlier steps have had their side effects. There is
  no way to write a literal `{{name}}` in a prompt.
- **A failed or interrupted run tells no one.** `_fail_run` (`:1405-1409`)
  and the two `interrupted` paths (`:1708`, `:2298`) log only. Spec R3-6
  (`on_failure` edges, runner notification on by default) is not built;
  `docs/WORKFLOW_BLOCK_VOCABULARY_REVIEW.md:95` already says so. A scheduled
  workflow that fails is silent unless someone opens the Runs panel.
- **The approval mail gives a command that cannot work.** `:1868-1869` tells
  the reader to `POST /api/workflow-runs/<id>/decision`; that route refuses
  any caller without an Origin header and the passcode
  (`workflow_routes.py:221`). Replying to the mail does not resolve the gate
  either: `mail_context_args` routes the reply into the last completed step's
  chat, and that agent cannot decide.
- **Spec against code.** R3-4 rules a wait node out; `NODE_TYPES` (`:104`)
  ships `wait`. `_parse_wf_result` (`:1155`) says "the LAST well-formed
  block" and takes the last block whether or not it parses.
- **Naive `config.at` is read as UTC** (`:337-340`), while the schedule route
  refuses a naive `run_at` for exactly that reason
  (`scheduler_routes.py:1440-1459`). The builder sends `Z`; an API caller or
  a model-written draft may not.
- **`POST /api/workflows/review` is ungated and makes a model call** of up to
  90 s on the default provider with an 80 KB caller-supplied definition
  (`workflow_routes.py:158-175`, `workflows.py:1003-1052`). Read-only for
  state, not for spend.
- **The passcode refusal text is about the vault** ("before changing the
  vault lock", `secrets_routes.py:167-169`) when it is returned for a
  workflow save on an install with no passcode set.

#### Sound in the workflow engine

- The run executes a frozen snapshot of the definition (`:1333-1359`); an
  edit during a run cannot rename a step under it.
- `on_agent_step_complete` is idempotent, checks the session id, refuses a
  stale session, and treats `stopped`/`interrupted`/unknown as not-a-result
  (`:1659-1713`).
- Join and skip propagation are one topological pass and match R2-D2
  (`:1233-1290`); dominator validation for slots matches R2-D5 (`:379-400`).
- `resolve_decision` is the only path that completes an approval step and is
  reachable only through the gated route (`:1739-1777`).
- `notify_operator` has no recipient parameter at any layer (`:1909-1917`,
  `:1987-1997`).
- The draft path is an allowlist copy with `enabled: False` and a fixed
  manual trigger, on a tool-free call (`:839-881`, `:884-891`).
- Restart adoption fails closed and never re-dispatches an agent step
  (`:2039-2080`).
- `render_template` does not re-scan substituted text, so a step output that
  contains `{{…}}` cannot inject a slot (`:1129-1140`).

### Open to-do for this report (delete when done)

- Addendum from Dave (2026-11-16): add a "Backlog cross-check" table after the
  feature table. Candidates picked from the 91 live items so far: MC-1119,
  MC-1118, MC-1116, MC-1114, MC-1113, MC-1105, MC-1054, MC-1052, MC-1050,
  MC-1048, MC-1005, MC-968. Classify each DONE / PARTLY DONE / NOT DONE /
  OBSOLETE or DUPLICATE / NEEDS A HUMAN with file:line or commit. No PATCH.
  Final message must list keys classed DONE or OBSOLETE.
- Then: ranked summary, feature table, streamlining, prior audits, coverage.

