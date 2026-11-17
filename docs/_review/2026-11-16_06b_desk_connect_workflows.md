# MC-1179 pass 06b: Desk Connect and Workflows

Date: 2026-11-16. Review only; no source file changed. Master as checked out in this
worktree (`3f51b0e0`). Master has since moved to `ae35fd31`; no file in scope differs between
the two (`git diff --stat`, empty).

Scope: `mc/desk_connect/` (108 Python files, 16,784 lines, plus 17 JSON data files),
`mc/workflows.py` (2,366 lines), `mc/workflow_mail.py`, `mc/blueprints/workflow_routes.py`.
There is no other `mc/workflow*.py` and no other workflow route module.

Finding-id prefix: `DCW` (`DCW-W` workflows, `DCW-C` Desk Connect).

Counts: 0 blocker, 2 high, 8 medium, 13 low. 23 findings.

Two things the brief asked about directly:

- **Can an agent-authored draft run, schedule or enable itself?** The draft route cannot: it
  returns an unsaved, disabled, manual-trigger definition and saving it is behind the passcode.
  But scheduling and running any saved workflow are open to any caller, and a definition written
  straight into the definitions file runs with no check on who wrote it (DCW-W2, high).
- **Is any credential stored or logged outside the vault in Desk Connect?** None found. All 105
  `_log` calls in `mc/desk_connect/` were opened; those that include exception text carry a
  refusal message, a class name or a truncated transform error, never a field value. Typed
  values go to `secrets_store` only; drafts, records and approval fingerprints carry names.

I would hold the next unattended workflow until DCW-W1 and DCW-W2 are fixed. Nothing in Desk
Connect is at that level; DCW-C1 and DCW-C2 are the two to fix first there.

## Ranked summary

Lens: E efficiency, L coherent logic, M methodology.

| Rank | Id | Finding | Lens | Severity |
|---|---|---|---|---|
| 1 | DCW-W1 | The 30-second reconciler completes a workflow step that is paused on a question; the next step runs on the question text and the real answer is dropped (measured) | L | high |
| 2 | DCW-W2 | Scheduling and running a workflow are open to any caller; a definition written to the file directly runs unchecked (measured) | L, M | high |
| 3 | DCW-C1 | Parameter detection's fenced-block regex is quadratic and holds the interpreter lock: one hostile README stops every server thread, 8 s at 80 KB, over an hour at the 2 MB cap (measured) | E, M | medium |
| 4 | DCW-C2 | New accounts get default-deny Read/Post consent on two of five Save paths; the X developer-app path, the one that posts by API, gets none | L | medium |
| 5 | DCW-W3 | `journal_append` and the self-HTTP actions use agent-supplied text as a path with no check; appends outside the journal directory (measured) | M | medium |
| 6 | DCW-C4 | A Python repository's dependencies are installed into the interpreter environment the server runs from; no virtual environment | L | medium |
| 7 | DCW-C3 | A wrong-shaped handshake answer raises after the connection is saved and registered; the Save returns 500 and the retry is refused (measured) | L | medium |
| 8 | DCW-W4 | Operator and approval mail pass the body on the command line; a step output over about 32,000 characters fails the send on Windows (measured) | L | medium |
| 9 | DCW-W5 | One global lock is held across agent dispatch, self-HTTP and the mail subprocess, and the scheduler tick waits on it | E, L | medium |
| 10 | DCW-C5 | A staged GitHub clone is never removed; every abandoned or failed Save leaves one on disk | L | medium |
| 11 | DCW-W6 | Run files are never pruned and each scheduler tick parses all of them four times (measured) | E | low |
| 12 | DCW-W7 | An action's side effect happens before its result is saved; a crash in between repeats it at restart | L | low |
| 13 | DCW-C6 | Typed-credential creates do not use `create_only`; a same-name vault entry made between check and write is replaced, then deleted by undo | L, M | low |
| 14 | DCW-C7 | "Saved entry unavailable" on a Setup field surfaces as a 500 with nothing useful in the log | L | low |
| 15 | DCW-W8 | `validate_workflow` says it never raises and raises on four malformed shapes (measured) | M | low |
| 16 | DCW-C8 | Every agent dispatch stat-walks every approved npm server's tree, up to 20,000 files (walk measured) | E | low |
| 17 | DCW-C10 | Repository install steps: fixed 120 s with a cold cache, and the failure log line holds no output | L | low |
| 18 | DCW-C12 | Pane-account Save is four separate writes; a failure midway leaves an account the retry refuses | L | low |
| 19 | DCW-C11 | Remote MCP: a slow tool call reads as "could not reach the server"; a transient read error discards the tool baseline; `?author=` is refused as a secret | L | low |
| 20 | DCW-C9 | The discovery proxy's tunnel counter only goes up: 64 connections per discovery, not 64 at once | L | low |
| 21 | DCW-W9 | Workflow engine, smaller items: half-validated slots, silent failed runs, an approval mail with a command that cannot work, spec against code | L | low |
| 22 | DCW-C13 | Registry documents cached for the life of the server; a moved interpreter turns every GitHub connection to "changed" | E, L | low |
| 23 | DCW-C14 | Desk Connect, smaller items: Higgsfield model cache discarded by each capture, 2 MB cleaned to keep 20,000 characters, risk label gaps | E, L | low |

## Feature table (lens 4)

"Read" means traced through the code end to end and not run; nothing here was exercised
against a live service, by rule. 33 features: 11 work as documented, 20 work with a defect,
1 cannot work as written, 1 could not be determined.

### Workflows

| # | Feature | Main path | Likely failure path | Verdict |
|---|---|---|---|---|
| 1 | Save, edit, delete a definition | `workflow_routes.py:96, 109, 124` → Origin and passcode gate (`:69-86`) → `validate_workflow` → definitions file | malformed node shape raises instead of returning errors, 500 | works with a defect (DCW-W8) |
| 2 | Draft a workflow from a description | `workflow_routes.py:134` → tool-free model call → allowlist copy, `enabled: False`, manual trigger (`workflows.py:839-891`) | model answer rejected → error returned, nothing saved | works as documented |
| 3 | Review a workflow | `workflow_routes.py:158` → `workflows.py:1003-1052` | no gate on a 90 s model call with an 80 KB body | works with a defect (DCW-W9) |
| 4 | Run now | `workflow_routes.py:180` → `start_run` → frozen snapshot → first step | one-live-run guard returns the existing run | works with a defect (DCW-W2: no caller check) |
| 5 | Run on a schedule | `scheduler_routes.py:1576` create with `workflow_id` → tick → `start_run` | workflow deleted → schedule fire fails and logs | works with a defect (DCW-W2) |
| 6 | Agent step: dispatch, result, next step | `_advance_run` (`workflows.py:1439`) → `_dispatch_step` → `on_agent_step_complete` (`:1651`) → slots rendered into the next prompt | agent asks a question → reconciler completes the step | works with a defect (DCW-W1; pass 01 A1 for the result text) |
| 7 | Branch on outcome, join, skip | topological pass `workflows.py:1233-1290` | undeclared or missing outcome → routed to the `otherwise` port, or the branch ends if nothing is wired there (`:1719-1724`) | works as documented |
| 8 | Approval gate | step waits → `workflow_routes.py:214` (gated) → `resolve_decision` (`:1739-1777`) | the mail tells the owner to POST a route that refuses them | works with a defect (DCW-W9) |
| 9 | Action steps (backlog create and patch, journal, notify, restore point, Desk harvest) | `_execute_action` (`workflows.py:1944-2002`) | slot value with `..` or `*`; long mail body; crash before the write | works with a defect (DCW-W3, DCW-W4, DCW-W7) |
| 10 | Wait step | `workflows.py:1537-1560`, resumed by `resume_due_waits` (`:1807`) | naive `at` read as UTC (DCW-W9) | works as documented |
| 11 | Cancel a run | `workflow_routes.py:237` (gated) → `cancel_run` | waits behind the global lock (DCW-W5) | works as documented |
| 12 | Restart adoption and the 30 s reconciler | `_adopt_runs_on_startup` (`:2039-2080`), `reconcile_running_steps` (`:2333-2348`) | session paused on a question | works with a defect (DCW-W1) |
| 13 | Runs list and run detail | `workflow_routes.py:197, 206`; `scheduler_routes.py:1838-1855` | grows without bound | works with a defect (DCW-W6) |
| 14 | Tell the owner a run failed (spec R3-6) | none: `_fail_run` (`:1405-1409`) logs only | n/a | cannot work as written: not built (DCW-W9) |

### Desk Connect

| # | Feature | Main path | Likely failure path | Verdict |
|---|---|---|---|---|
| 15 | Connect a service by API key | `desk_connect_routes.py:80` → `commit.py` → `provider_commit.py:48-107` → `providers/key_paste.py:83` → vault → verify | same-name entry made meanwhile | works with a defect (DCW-C6) |
| 16 | Fill a Setup field from a saved Secrets entry | `saved_fields.py:6-22`, `saved_key_update.py:58-59`, resolved on the server | the named entry is gone or locked | works with a defect (DCW-C7) |
| 17 | Connect X by developer app | `providers/x.py:108-119` → vault → account created → OAuth | account created with no consent policy | works with a defect (DCW-C2) |
| 18 | Browser sign-in setup and stored-login fill | `desk_connect_browser_setup_routes.py:33` → `browser_setup.py`; `desk_connect_signin_routes.py:66` → `signin_fill.py:293-366` | origin mismatch → refused before the password is read | works as documented |
| 19 | Check connection | `desk_connect_browser_check_routes.py:11` → `browser_check.py` | signed out → sign-in-again request (`browser_check.py:149-150`) | works as documented |
| 20 | Read and Post permissions | `desk_connect_permission_routes.py:16, 24` → `permission_policy.py`; enforced in `permission_check.py:52-68` | account with no policy is treated as allowed | works with a defect (DCW-C2) |
| 21 | Discover a service by its address | `desk_connect_discover_routes.py:42` → `discovery.py` → `pane_reader.py` behind `guard_proxy.py` → `classifier.py` | page with more than 64 connections | works with a defect (DCW-C9) |
| 22 | Detect parameters for a chosen MCP server or API | `desk_connect_detect_routes.py:34` → `parameter_detect.py:87` → parsers → tool-free classifier | hostile README | works with a defect (DCW-C1) |
| 23 | Install a reviewed MCP package | `mcp_catalogue.py` → `mcp_package_store.py:135-211` (sha512 before write) → `mcp_activation.py` | digest mismatch → nothing written | works as documented |
| 24 | Custom npm MCP server | `desk_connect_custom_routes.py:59, 108` → `custom_npm_closure.py` → `custom_npm_install.py:138-150` → gate → manifest | drift → card reads Changed, launch gate refuses | works with a defect (DCW-C8, cost only) |
| 25 | GitHub repository MCP server | `desk_connect_custom_routes.py:73, 84, 108` → `github_connection.py` → `github_activation.py:66-82` → `github_start_check.py` | install step fails; server answers in the wrong shape | works with a defect (DCW-C3, DCW-C4, DCW-C5, DCW-C10) |
| 26 | Remote MCP server | `desk_connect_remote_routes.py:35, 48, 69` → `remote_mcp_transport.py` → `remote_mcp_check.py` → `remote_mcp_observed.py` | slow tool call; unreadable record | works with a defect (DCW-C11) |
| 27 | Remove a custom connection | `desk_connection_removal_routes.py:13` → `custom_connection_removal.py:85-131` | corrupt config → caught, logged, 500 | works as documented |
| 28 | Routes by purpose, bindings, readiness | `desk_connect_purpose_routes.py:38, 49, 73` → `purpose_view.py`, `purpose_bindings.py`, `route_readiness.py:28-112` | unknown route → refused | works as documented |
| 29 | Instagram, YouTube, TikTok pane account | `browser_connections.py:82-85` → `pane_account.py:54-75` | failure after the account is created | works with a defect (DCW-C12, DCW-C2) |
| 30 | Instagram read | `instagram_read.py:18-20` → `route_readiness.py:42` → generic pane reader (outside this region) | n/a | could not determine: its own file says the read has never run against the site (`instagram_read.py:12-13`); see MC-1119 below |
| 31 | LinkedIn destination (which page to post as) | `linkedin_destination.py` from `desk_accounts.py` | invalid policy → refused, not re-signed | works as documented |
| 32 | Higgsfield tool and model snapshot | `higgsfield_mcp_capture.py:7-42` → `higgsfield_mcp_snapshot.py:30-62`; `higgsfield_mcp_catalogue.py:61-107` | each capture discards the model cache | works with a defect (DCW-C14) |
| 33 | Package setup help (read-only guidance) | `desk_connect_package_help_routes.py:10` → `package_setup_helper.py` → tool-free transform | reader unavailable → says so, no fallback | works as documented |

## Backlog cross-check

91 items were open, in progress or blocked at the time of the GET. 12 lie in this region.
No item was patched or noted.

| Item | Subject | Class | Evidence |
|---|---|---|---|
| MC-1054 (blocked) | MCP config and package drift | **DONE** | Part 2: `custom_package_manifest.py` (merge `613c21d9`), called at dispatch (`agent_routes.py:802`) and on the card (`custom_connection_activation.py:316`). Part 1, the piece the item says needs a human to apply: `_config_plant_guard` is on master (`steward/fence.py:3294`, paths at `:3156`, commit `c04f0bc9`, `tests/test_fence_config_plant.py`), and the patch file the item names is no longer in `docs/patches/`. Only the card UI test in the item text is left. Cost of part 2: DCW-C8. |
| MC-1052 (blocked) | One passcode for typed login | **DONE** | `89aa3a7f` is an ancestor of master. `provider_commit.py:48-49, 76-77, 107`; the login write joins the same undo stack; `signin_login_store.py:91-94` uses `create_only`. Only the owner's UI test is left. |
| MC-1114 (in progress) | Readiness ignores Post consent | **DONE** | Merges `63539d71`, `1d4104f4`. Start gate: `desk.py:1751-1752`. Account view: `desk_accounts.py:205`. Failure card links to Permissions: `desk-v1-review.js:478, 1041`. Save keeps a saved X sign-in: `x_account_attach.py`. Related defect, not the same one: DCW-C2 (an account with no policy passes this check). |
| MC-1113 (in progress) | Setup fields from saved Secrets | **DONE** | Merge `b8a86ee4` (fixes `c97f9139`). `saved_fields.py:6-22`, `saved_key_update.py`. Its error path has DCW-C7. |
| MC-1105 (in progress) | Vault entry for passwordless sign-in | **PARTLY DONE** | Merge `e8f63630`: the account and how it signs in are recorded and listed (`signin_method.py:4-15`, `mc/desk_browser_accounts.py:25-80`). The sign-in-again request has one writer, an explicit Check connection (`browser_check.py:149-150`; `desk_browser_accounts.py:3` says so). Left: a pane read that finds the session signed out during a run does not raise the request. Smallest step: call `observe` from the reader's signed-out verdict. |
| MC-1118 (in progress) | One vocabulary for Connections | **NEEDS A HUMAN** | Merged (`8c33c9ae`, `fc28e2d7`); `static/js/desk-v1-connection-words.js` exists. The front end is outside this pass; whether every screen now uses the one word set is a visual check. |
| MC-1119 (blocked) | Instagram reads through the pane | **NEEDS A HUMAN** | The declaration the item asks for is in: `instagram_read.py:18-20`, merged at `route_readiness.py:42`, and `profiles/instagram.json` lists `read_own` mentions and post metrics (status `claimed`, route support `info_only`). What is left is the live check on a signed-in account, which only the owner can do. Note DCW-C2: an Instagram account made from the wizard gets no Read policy (`pane_account.py:66`). |
| MC-1048 (in progress) | Custom npm dependency closure | **NEEDS A HUMAN** | Code on master (`f40df85d`). Closure, install, gate and manifest read fully in this pass; no defect beyond DCW-C8. The item's remaining acceptance (a real Review, Save and server start) is human-only by design. |
| MC-1074 (in progress) | Check connection before sign-in | **NEEDS A HUMAN** | Merge `8fda9f61`. `browser_check.py` read fully, sound. The owner's UI test is the only step left. |
| MC-1079 (in progress) | Agents' own tabs, shared profile | **NEEDS A HUMAN** | Merge `91b20f55`. The core is `mc/desk_browser_profiles.py`, outside this region; the Desk Connect side (`browser_setup.py`) was read. The owner's UI test is left. |
| MC-1005 (blocked) | Event triggers for workflows | **NEEDS A HUMAN** (not built) | `TRIGGER_TYPES = ('manual', 'schedule')` (`workflows.py:110`); no event bus or trigger record exists. The item waits on product decisions. Fix DCW-W2 first: an event-fired run is unattended, and today nothing checks who scheduled or started a run. |
| MC-968 (open) | Per-campaign autonomous publishing | **NEEDS A HUMAN** | Mostly outside this region. Its stated blocker is the owner creating the X developer app. DCW-C2 applies to exactly that account: `providers/x.py:119` creates it with no consent policy, so the first autonomous post would go out with no Post step. Decide before connecting. |

Outside this region, not classified here: MC-1116 (`mc/desk_pieces.py`), MC-1050 (the generic
pane digest reader), MC-1065 (vault unlock button).

## Streamlining

About 190 lines, no behaviour lost. Callers checked by grep across `mc/`, `tools/`, `server.py`
and the route modules.

| What | Where | Evidence | Lines |
|---|---|---|---|
| Version 1 registry import | `profile_compat.py:37-42, 49-108`; branch at `registry.py:69-70` | The shipped `registry.json` is version 2 (read). The registry is repo data loaded from beside the module and bundled by the build spec, not user state, so no install holds a version 1 file. Sole caller is that branch; the rest of the module (the version 1 projection the screens read) stays. | 70 |
| Two key-only providers | `providers/openai_api.py`, `providers/google_ai.py` | 42 lines each, 5 differ (names and strings; diffed). One class taking the vault name, label, hint and test id. | 32 |
| Two classifiers' shared plumbing | `classifier.py:38, 56-76, 103-125`; `parameter_classifier.py:45, 63-86, 105-124`; `parameter_parsers.py:68-74` | Same `ClassifierError`, `_fail`, `=`-run collapse, first-to-last-brace parse and duplicate-key hook, three copies of the hook. The schemas differ and stay separate. | 45 |
| Higgsfield handshake | `higgsfield_mcp_capture.py:10-24`, `higgsfield_mcp_catalogue.py:72-86` | Same deadline wrapper and initialize pair, word for word. | 14 |
| Remote bridge | `remote_mcp_bridge.py:49-54`; `remote_mcp_transport.py:255-259, 367-371` | Two `open_session` branches that differ only in an argument both accept; `_seen` duplicated in both session classes. | 8 |
| Run-file scans | `workflows.py:1206, 1807, 2054, 2110, 2133, 2155, 2333` | Seven loops that glob, read, parse and skip bad files the same way; one iterator. Also the fix for DCW-W6. | 20 |

Not found: no dead module (the four providers with no importer are loaded by name from
`providers/__init__.py:15-32`), and no always-one-value flag (the region reads no install
config flag).

## Detail

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

#### DCW-W1 · high · L: the 30-second reconciler completes a step that is paused on a question (measured)

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

#### DCW-W2 · high · L, M (authority): running and scheduling a workflow are open to any caller, and the definitions file is trusted as-is (measured)

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

#### DCW-W3 · medium · M (untrusted input): `journal_append` and the self-HTTP actions use rendered slot values as path parts with no check (measured)

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

#### DCW-W4 · medium · L: `notify_operator` and the approval mail pass the body on the command line; a long step output fails the send on Windows (measured)

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

#### DCW-W5 · medium · E, L: one global lock is held across dispatch, self-HTTP and the mail subprocess (read, not measured)

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

#### DCW-W6 · low · E: run files are never pruned, and each scheduler tick parses all of them four times (measured)

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

#### DCW-W7 · low · L: an action's side effect happens before its result is saved (read, not measured)

`mc/workflows.py:1513-1523`. After `_execute_action` the step is marked
`completed` in memory and the loop continues; the run is written only when
the loop next blocks or ends. A crash or an uncaught exception in between
leaves the step `pending` on disk, and `_adopt_runs_on_startup` (`:2063-2066`)
calls `_advance_run`, which executes the action again: a second backlog item,
a second operator mail. Fix: `_write_run(run)` before `continue`.

#### DCW-W8 · low · M: `validate_workflow` says "Never raises" and raises on four malformed shapes (measured)

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

#### DCW-W9 · low · L: smaller items in the workflow engine

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

### Desk Connect: `mc/desk_connect/` (108 files, read fully)

Callers followed: the `desk_connect_*`, `desk_connection_removal`, `desk_saved_*` route
modules (entry points and error handling), `agent_routes.py:795-806`, `mcp_installer.py:846-848`,
`mc/desk_browser_accounts.py` (structure), `desk.py:1751-1752`, `desk_accounts.py:205`.

Reproductions are scratch scripts (not committed): a timing loop on the pure parser, a stdio
server that answers the handshake in wrong shapes, and a synthetic file tree in a temp
directory. None imports `server`, binds a port or contacts a service.

#### DCW-C1 · medium · E, M: the fenced-block regex is quadratic and holds the interpreter lock (measured)

`mc/desk_connect/parameter_parsers.py:34` (`_FENCE`), `:230` (`_candidate_blocks`), reached from
`parameter_detect.py:121, 154, 202, 216`.

The pattern is an opening fence, a lazy `(.*?)`, then a newline and a closing fence. An opener
that is not at the start of a line cannot serve as the previous opener's close, so each one
scans to the end of the text. Measured on `'a```\n' * n`:

| Text | Time |
|---|---|
| 10 KB | 0.08 s |
| 20 KB | 0.33 s |
| 40 KB | 1.3 s |
| 80 KB | 8.0 s |

The text comes from a third party: a registry README inside a document capped at 2,000,000
bytes (`parameter_sources.py:37`), a documentation page, or pasted text. At 2 MB the scan is
over an hour by extrapolation. Python's `re` keeps the interpreter lock for the whole match.
Measured: a second thread that should tick every 50 ms ticked once during a 7 s scan. So while
it runs, no request is answered, no stream moves and the scheduler does not tick. The 60 s
total (`parameter_detect.py:38`) is checked between stages only, and `_lock` (`:94`) stays
held, so every later detection gets 429 until the scan ends or the server is restarted.

The person chose the package; the README author chose the text.

Smallest fix: cap the text handed to `_candidate_blocks` (200 KB is far above any real README
configuration section) and find fences with a line scan, which is linear.

Existing tests: `tests/test_desk_connect_parameters.py` has no timing or size case.

#### DCW-C2 · medium · L: the consent default depends on which Save made the account (read)

`mc/desk_connect/permission_policy.py:4-5, 132-140`; `permission_check.py:44-45`;
`post_consent.py:28-41`.

`permission_policy` says new setup must call `initialize_new_account`, which writes a policy
with Read and Post both off. Five paths create an account. Two call it:
`browser_setup.py:377` and `saved_site_account.py:79`. Three do not:

- `providers/x.py:119`, the X developer-app connection;
- `purpose_bindings.py:371`;
- `pane_account.py:66`, Instagram, YouTube and TikTok from the wizard.

An account with no policy is `legacy`, and `permission_check.py:44-45` and
`post_consent.py:38` both read that as "no consent needed". So:

- an X account connected by browser sign-in must have Post ticked before the publisher will
  post; the same account connected by developer app, the one route that posts through the API,
  posts with no Permissions step;
- an Instagram account made from the wizard is read with no Read consent; the same account
  attached from a saved site starts denied;
- the readiness check added for MC-1114 returns "ready" for these accounts, correctly by its
  own rule, because there is no policy to be missing.

Smallest fix: call `initialize_new_account` in the three paths, or do it once inside
`create_account` so a sixth path cannot miss it. Existing legacy accounts keep their behaviour.

Existing tests: `test_desk_connect_permission_execution.py` pins the legacy behaviour and
`test_desk_pane_account.py` pins the pane path; none asserts that a new account from every
Save path starts denied.

Checked and not reported: `permission_check.py:68` hard-codes `organization` for LinkedIn
while the profile also declares `member`. The only executable LinkedIn API route covers
`organization` (`profiles/linkedin.json`, route `linkedin-oauth`), so no member account reaches
this call today. It becomes a wrong refusal the day the member API route is made available.

#### DCW-C3 · medium · L: a wrong-shaped handshake answer raises after the connection is saved (measured)

`mc/desk_connect/github_start_check.py:186, 187, 197`; `custom_connection_service.py:303`;
`mc/blueprints/desk_connect_custom_routes.py:124-126`.

`handshake` reads the server's JSON answers as objects without checking. Four JSON-valid
shapes were sent by a scratch stdio server, and each raised `AttributeError`: `error` as a
string (`:186`), `result` as a string and `serverInfo` as a list (`:187`), and a `tools/list`
`error` as a string (`:197`). `commit` calls it at `:303` with no `try`, after the record is
`registered` and the MCP config is written. The route answers 500 "could not save".

The connection is saved and registered. No start check is recorded and `_done` is not set, so
repeating the same Save is refused as "repository changed since Review" (`:268-270`): the
installed output now differs from the source inventory. The person is told it failed, it did
not, and the way back is to remove the connection and start again.

Smallest fix: `isinstance` checks at those three reads, returning the existing "unexpected
answer" result; and wrap the call at `:303` so a start-check failure is a recorded result, not
an exception.

Existing tests: the start-check tests use a well-formed server; none sends a wrong shape.

#### DCW-C4 · medium · L: a Python repository installs into the server's own interpreter (read)

`mc/desk_connect/github_install_steps.py:39`; `mc/mcp_installer.py:846-848`;
`github_activation.py:66-82`; `custom_npm_scripts.py:31-50`.

For a Python repository the dependency step is `[sys.executable, '-m', 'pip', 'install', ...]`
or a bare `uv sync`. No virtual environment is created anywhere in the path (grep: none). So
the step installs a stranger's requirements into the environment the Clayrune server runs
from: it can upgrade or downgrade a package the server imports, and the effect appears at the
next restart with nothing pointing back to this Save. The card shows the command, not where it
installs.

Two further mismatches on the same path:

- the server is then started with whichever `python` is first on PATH
  (`github_activation.resolve_command`), which need not be the interpreter that got the
  packages;
- the step runs with `script_env`, whose HOME is a temp directory deleted afterwards and whose
  PATH is the node folder plus system folders. A `--user` fallback install lands in the deleted
  home, and on POSIX a `uv` outside the system folders is not found.

Smallest fix: create a virtual environment inside the managed package folder, install into it,
and launch with its interpreter. That also gives removal something to delete.

Existing tests: the install-step tests assert the argv, not the target environment.

#### DCW-C5 · medium · L: a staged GitHub clone is never removed (read)

`mc/desk_connect/github_connection.py:27, 38-47, 67-70, 90`.

`_stages` forgets a stage at 50 entries or 30 minutes; it drops the record, not the directory.
Nothing in `mc/desk_connect/` calls `mcp_installer.cleanup_staged` (its only caller is
`mcp_routes.py:411`, the older installer). Left on disk: abandoned stages, stages that fail
midway (`:38-47`), and the directory of an approval that a re-approval replaced.

It compounds with a failed install: the step leaves partial output in the clone, after which
`checked_stage` (`:90`) and `check_submission` (`custom_connection_service.py:268`) both
refuse that stage for good ("Enter the address again"). Every failed Save therefore costs a new
clone and leaks the old one.

Smallest fix: remove the directory when the stage record is dropped, when staging fails, and
when a commit fails before the move into place.

Existing tests: none checks the staging directory after a failure.

#### DCW-C6 · low · L, M: typed-credential creates do not use `create_only` (read)

`mc/desk_connect/providers/key_paste.py:83`, `providers/x.py:108`, `reference_save.py:35`.

Each checks the vault's names earlier, then calls `set_secret` without `create_only`.
`signin_login_store.py:91-94` passes `create_only=True` and says why: "the check above is a
courtesy message; this is the guard". If another route creates the same name between the check
and the write, it is replaced; if the same Save then fails later, the undo stack deletes it.
The window is one request long and needs two people or tabs. Fix: pass `create_only=True` in
the three places.

#### DCW-C7 · low · L: "saved entry unavailable" becomes a 500 (read)

`mc/desk_connect/saved_key_update.py:58-59`; `providers/key_paste.py:70-77`;
`provider_commit.py:90-93`.

`FieldRefError` is raised outside `key_paste.apply`'s `try`, so it is not turned into a
`ProviderError`. `provider_commit` answers "could not save; see the server log", and the log
line carries the class name only. The person picked an entry that was deleted or is locked;
they should be told that. Fix: catch `FieldRefError` beside `SecretsError` and raise the
`ProviderError` with its message.

#### DCW-C8 · low · E: every dispatch walks every approved npm server's tree (walk measured)

`mc/desk_connect/custom_package_manifest.py:232-290` (`verify`), `:317-332`
(`check_for_launch`); `mc/blueprints/agent_routes.py:802`;
`custom_connection_activation.py:316`.

At each agent dispatch, for each approved npm server the session would start, the manifest
JSON is parsed and shape-checked and the whole installed tree is walked. Hashing is skipped
when size and time match, so the cost is the walk: 48 ms for 5,000 files cold (measured on a
synthetic tree), about 0.2 s at the 20,000-file cap, per server, per dispatch. The same check
runs for each connections listing. Fix: remember the last result per server for a few seconds,
or keyed on the root folder's change time plus a cheap count.

#### DCW-C9 · low · L: the tunnel counter only goes up (read)

`mc/desk_connect/guard_proxy.py:163-167`. `_tunnels` is incremented per CONNECT and never
decremented (three uses, no `-= 1`). The docstring (`:19`) says "`max_tunnels` at once". The
code allows 64 connections for the life of one discovery, refused ones included; the 65th gets
429, and a heavy page stops loading partway. Fix: decrement when the tunnel closes, or correct
the docstring and raise the number.

#### DCW-C10 · low · L: install steps: 120 s, a cold cache, and an empty log line (read)

`mc/desk_connect/github_activation.py:72-80`; `custom_npm_scripts.TIMEOUT_S`.

Each step has a fixed 120 s limit with `npm_config_cache` and HOME pointing at a fresh temp
directory, so every install starts cold. A large dependency set fails the Save and, by DCW-C5,
the stage. When a step exits non-zero its output is discarded (`rc,_=scripts.spawn(...)`,
`:73`) and the error raised is "A selected install step failed. See the server log."; the log
line at `:79` then prints that same sentence. The log holds nothing about why. Fix: log the
last lines of the step's output (it runs with no Clayrune secrets in its environment), and
make the limit depend on the step.

#### DCW-C11 · low · L: remote MCP, three smaller items (read)

- `remote_mcp_transport.py:156-158`: a response-phase timeout (a tool call that runs past the
  120 s idle limit) is reported as `connect_failed`, "Could not reach the server", although
  the request was delivered and may have taken effect.
- `remote_mcp_observed.py:67-76`: `_read` moves the record aside on any `OSError`, a transient
  one included, and from the read-only list path. The next check then records a new baseline,
  which is the comparison tool-change detection depends on. `custom_connection_store` fails
  closed for the same condition.
- `remote_mcp_operation.py:45`: `_SECRET_QUERY_KEYS` is a substring match, so an address with
  `?author=`, `?design=` or `?session_id=` is refused as holding a secret.

#### DCW-C12 · low · L: pane-account Save is four separate writes (read)

`mc/desk_connect/pane_account.py:54-75`; `browser_connections.py:82-85, 107-108`.

`save` is check, create, update, stamp under four separate lock acquisitions. A failure after
`create_account` leaves an account with the default profile and no sign-in method, and the
retry answers 409 "already an account with a different browser profile". Separately,
`commit` returns from the pane branch before the trim loop, so `_done` grows by one entry per
such Save until a non-pane Save trims it. Fix: one locked function for the four steps; trim
before the early return.

#### DCW-C13 · low · E, L: two lifetime assumptions (read)

- `custom_npm_closure.py:54-55`: `_docs` and `_facts` check their age on read and never evict,
  so every package document ever resolved stays in memory for the life of the server; a
  version-list document can be megabytes.
- `github_activation.py:33-37`: `matches` compares the saved launch line with `sys.executable`
  and the absolute gate path. A moved install or a new interpreter path turns every GitHub
  connection to "changed". The remote kind has `strict=False` for this case
  (`remote_mcp_activation.py:131-168`).

#### DCW-C14 · low · E, L: Desk Connect, smaller items (read)

- `higgsfield_mcp_snapshot.py:50-56` rewrites the snapshot without the `models` key, so every
  tool capture discards the 24-hour model cache that `higgsfield_mcp_catalogue.py:99-107`
  wrote into the same file. Two catalogue refreshes for different models are read, network
  call, write with no lock; the last writer wins. The cost is a repeated read-only call.
- `parameter_classifier.py:84-86`: `_clean` processes the whole text, up to 2 MB, one
  character at a time, then keeps 20,000.
- `parameter_schema.py:178-190`: `runs_arbitrary_code` does not label `env bash ...`,
  `npx -c "..."` or `node --eval=...`. It is a label on a card that shows the command
  verbatim, not a gate.
- `verification.py:94`: `_records` is read without `_lock` in the status path.

#### Sound in Desk Connect

- Sign-in fill reads the origin from the browser, types in an isolated world, and fetches the
  password only when a password field is showing, at most once (`signin_fill.py:293-366`).
- Discovery fails closed with no fallback fetch (`pane_reader.py`, `guard_proxy.py`,
  `net_guard.py`).
- A reviewed MCP package is checked against its sha512 before anything is written and is
  extracted member by member (`mcp_package_store.py:135-211`).
- Custom npm: the closure takes dependency edges from the archive, not the registry; a range
  never reaches the launch line; the launch gate refuses before the wrapper reads any secret
  and re-hashes every file; the approval fingerprint binds the request id; the store fails
  closed; removal deletes only a direct child of the managed root and refuses links.
- GitHub: the gate re-hashes reviewed source before every start and resolves a bare program
  name outside the repository folder.
- Remote MCP: no redirect is followed, one origin is pinned, the name is resolved once per
  request and the vetted address is the one connected to; no header value reaches an error;
  the bridge scrubs the parsed tree, not the serialised text; the check never calls a tool and
  `adopt` is bound to the digest the person was shown; OAuth is never reported connected.
- Parameter detection: prose reaches a model only through the tool-free transform, nothing
  takes its place when that is unavailable, the answer must match an exact shape and every
  value must appear in the text that was sent; env and header values are never copied; vault
  placeholders in a detected command or argument are refused at approval
  (`custom_connection_operation.py:85, 94`).
- `x_card_signin` confirms the signed-in user against the account before the token is held;
  `saved_mcp_draft` copies credential names, never values; `browser_check` builds its script
  from reviewed profile selectors only and re-checks the final URL when it takes evidence.
- Profile validation rejects unknown fields and never returns a half-valid profile; a route's
  "last verified" date comes from its evidence, never from conversion time.

## Earlier passes and prior audits

Other halves, by id:

- Pass 01 **A1** (`_collect_trailing_reply_text` drops lines that start with `[`) and DCW-W1:
  A1 makes a real completion's output wrong, W1 makes a non-completion count as one.
- Pass 04 **WB3** (three slot grammars in the builder) and DCW-W9: the server validates
  `steps.*` and `prev.*` only, so the builder's disagreements are not caught at save.
- Pass 04 **WB5** (Run now runs what is saved) and DCW-W2: the same route, seen from the
  caller's side.
- Pass 02 **R56** (the workflow progress scan skips worktree folders) is in `agent_routes`;
  nothing in `workflows.py` depends on it.

Still open from before:

- `docs/ENTANGLEMENT_AUDIT.md`: `mc/workflows.py` has grown from 2,158 to 2,366 lines and
  `_execute_action` is still one function with a branch per action (`:1944-2002`). The action
  registry it proposed is not built. Nothing under `mc/desk_connect/` is on its list, and the
  directory follows the one-unit-one-file rule.
- The 2026-09-10 efficiency and security reviews have no finding in this scope. The efficiency
  review lists the Workflows tab's 3 s poll as scoped and self-cancelling; that is front end.

## Open choices for the owner

Not picked here.

1. **DCW-W2, manual Run.** Either gate `POST /api/workflows/<id>/run` like the other write
   routes (an agent can then never start a workflow, including a human-approved one), or keep
   it open and say so in the standing rule. Schedule create and run-now for a workflow row
   should be gated either way. Recommendation: gate all three; an agent that needs a workflow
   run can ask.
2. **DCW-C2, existing accounts.** Fixing the three Save paths changes new accounts only.
   Whether to also give existing no-policy accounts a default-deny policy is a product call:
   it is the safe reading of the rule and it will stop accounts that post today until Post is
   ticked. Recommendation: new accounts now; existing ones after a notice on the account card.
3. **DCW-C4, Python repositories.** Build the virtual environment, or refuse Python
   repositories until it exists. Recommendation: refuse until built; the current behaviour
   changes the server's own environment.

## Coverage

Read fully (every line):

- `mc/workflows.py` (2,366), `mc/blueprints/workflow_routes.py` (253), `mc/workflow_mail.py` (15).
- All 108 Python files under `mc/desk_connect/`, including `providers/` (16,784 lines).
- `docs/WORKFLOW_BUILDER_SPEC.md` (Rev 2), before judging intent.

Read at structure level (keys, counts, selected entries, not every line):

- The 17 JSON files: `registry.json`, `mcp_catalogue.json`, and the 15 profiles. `instagram.json`
  and `linkedin.json` were read for purposes, routes and coverage.

Read partly, as callers:

- `mc/blueprints/scheduler_routes.py` 690-720, 895-917, 1440-1640, 1678-1710, 1838-1855.
- `mc/blueprints/agent_routes.py` 795-806, 6291-6307, 9204-9289, 9597-9640.
- `mc/blueprints/secrets_routes.py` 132-179; `mc/model_upgrade.py` 372-387;
  `mc/mcp_installer.py` 846-848; `steward/fence.py` 3118-3160 and the guard's definition line.
- The Desk Connect route modules: route decorators and error handling in all of them; full
  bodies of `desk_connect_detect_routes.py` and `desk_connect_custom_routes.py` 108-130.
- `mc/desk_browser_accounts.py`: function list and the request writer only.

Not reached:

- The front end for either feature (`workflow-builder.js`, the `desk-v1-connect-*` files):
  pass 04's region. Two front-end lines were opened to classify MC-1114.
- The tests were grepped for the presence or absence of a case; they were not run, and no
  test file was read in full.
- Nothing was run against a live service, a real vault or the real data directory.

Measured: DCW-W1, W2, W3, W4, W6, W8, DCW-C1, C3, and the walk in C8. Everything else is read,
not measured, and says so.

Candidates checked and dropped, so they are not looked at twice:

- `custom_npm_install.py:146-147` holds every archive of a closure in memory at once. The
  closure caps the total at 96 MB (`custom_npm_closure.py:48`). Bounded.
- `custom_connection_removal.py:99`, `json.loads` on a corrupt MCP config: caught and logged by
  the route (`desk_connection_removal_routes.py:29`).
- `custom_connection_service.py:57, 276`, `_name_locks` grows by one lock per connection name
  and is never pruned. A few hundred bytes per name; not worth a finding.
- A note about two accounts sharing one browser profile, written before a context rollover,
  could not be reconstructed from the code on re-reading.
