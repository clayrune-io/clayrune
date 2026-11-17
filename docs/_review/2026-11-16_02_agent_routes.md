# Agent routes and session lifecycle review, pass 2 of 8 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `40fcd230`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, or a read-only
count) or **read, not measured**. `server` was never imported, the live server
was sent nothing but `GET`, and no process that binds a port or launches a
provider CLI was started.

Scope: `mc/blueprints/agent_routes.py`, `mc/agent_worktree.py`,
`mc/delegation_delivery.py`, `mc/delegation_settlement.py`,
`mc/delegation_transcript_reconcile.py`, `mc/delegation_waiting.py`,
`mc/delegation_wake.py`, `mc/delegation_wake_absent.py`,
`mc/delegation_retry.py`, `mc/merge_review_gate.py`, `mc/cold_review.py`,
`mc/cold_review_gate.py`, `mc/cold_review_runner.py`,
`mc/engine_pace_dispatch.py`, `mc/behavior_tail.py`,
`mc/session_mcp_config.py`, `mc/session_mcp_launch.py`,
`mc/session_provenance.py`. `mc/agent_runtime.py`, `mc/core.py` and
`server.py` were pass 1 (`docs/_review/2026-11-16_01_runtime_core.md`); its
findings are not repeated, and where a finding here is the call-site half of
one of them the pass 1 id is named.

Lenses: **E** efficiency, **L** coherent logic, **M** coding methodology.
Severity, same scale as pass 1: **blocker** = wrong result or lost data on a
normal path today; **high** = wrong result or lost data on a reachable path, or
a cost paid on every turn/dispatch/poll; **medium** = narrower trigger, or a
latent defect behind a disabled flag; **low** = hygiene.

Written incrementally, one commit per section. The ranked summary and the
coverage statement are written last; until they exist, this file is a partial
report and the last section present is where the pass stopped.

## Ranked summary

_Written at the end of the pass. Findings are in the sections below._

---


## 1. `mc/blueprints/agent_routes.py` lines 1-4733

Module helpers, process tracking, worktree hooks, provider env and auth
routes, stream observers. Six candidates from this stretch depend on a call
site further down the file and are reported in the section that reaches it.

#### R1 · medium · L/M — a provider-env file that fails to parse is read as empty, and the next save erases every other provider's keys

`mc/blueprints/agent_routes.py:2553-2559` returns `{}` for a missing file
**and** for any read or JSON error, with no log. `agent_provider_set_env`
then does load, `setdefault(name, {})[key] = …`, save
(`agent_routes.py:2648-2651`). So one unparseable file plus one "save key"
click rewrites the store with exactly one key in it: every other provider's
saved credential is gone, and the route answers `{'ok': True}`.

The store makes its own corruption reachable: `_save_provider_env_file`
(`agent_routes.py:2562-2566`) is a plain `write_text`, not
`write_json_atomic`, so a kill mid-write leaves a truncated file, which is the
unparseable case. It also hardens permissions only after the secret is already
on disk (`:2566`).

Same class as pass 1 A4 (state file read as empty then saved over). Read, not
run.

Smallest fix: write with `write_json_atomic`; make the loader distinguish
"missing" from "unreadable" and have `set_env` refuse with a 500 on the
latter instead of saving. Log the parse error.

Test: none. `tests/test_provider_env_scope.py` has no corrupt-file or
truncated-file case.

#### R3 · medium · L — an unreadable incognito project record is silently replaced with a blank one

`agent_routes.py:709-713`: if `_incognito.json` exists but fails to read or
parse, `except Exception: pass` falls through to `:720-738`, which builds a
fresh record (`'activity_log': []`, `'backlog': []`, default name) and writes
it over the file. Callers are the dispatch and send paths, so the overwrite
happens on the next incognito message, not on an explicit reset.
`core.load_project` has the opposite rule for every other project (a bad file
is left on disk untouched). Three swallowed exceptions on state-file I/O in
one function (`:712`, `:718`, `:739`), against the project policy.

Smallest fix: on a parse failure of an existing file, log and return an
in-memory record without writing.

Test: none found for the corrupt-record path.

#### R10 · medium · E — every Read, Edit, Write and Grep tool call re-parses the whole project record on the reader thread, and the feature flag is checked only after the parse

`_observe_memory_push_input` (`agent_routes.py:4479-4482`) calls
`load_project` whenever `extract_input_query` returns a query, and
`mc/memory_push.py:127-140` returns one for any `Read`/`Edit`/`Write` with a
`file_path` and any `Grep` with a pattern. That is most tool calls of a
coding turn, not "the overwhelming majority produce no query"
(`mc/memory_push.py:331-333`). The result side
(`agent_routes.py:4497-4500`) adds another parse for every tool result with an
error-looking line, and `_observe_negation_interrupt` (`:4457`) one more for
each `Agent` dispatch or docs write.

`memory_push.enabled()` is tested inside `observe()`
(`mc/memory_push.py:334`), after the caller has already loaded the project,
so `memory_push_mode: off` still pays the parse.

**Measured:** `json.loads` of the largest project record on this install
(2.69 MB) is 8.8 ms (5-run mean). A 60-tool-call turn pays roughly half a
second of parsing for a report-mode log. This is a call site of the still-open
2026-09-10 E2 (uncached `load_project`), added after that review.

Smallest fix: test `_memory_push.enabled()` before `load_project`, and pass
the fields `observe` needs from the session instead of the full record.

Test: none measures or bounds it.

#### R13 · medium · L — a Claude auth probe that times out leaves a stale "signed in" in place

`agent_routes.py:3538-3540` says an explicit check must fail closed and never
leave a stale `ok: true`. The `TimeoutExpired` branch (`:3543-3545`) and the
generic `except` (`:3548-3550`) do exactly that: they stamp `last_probe_at`
and nothing else, so `ok` keeps its previous value and the freshly stamped
time makes the stale reading look current. The generic branch also drops the
exception without a log. The probe is `subprocess.run(timeout=…)` on the CLI
shim, so on Windows a hung probe is also a call site of pass 1 A2 (the
timeout kills the shim, not the node child).

Smallest fix: call `_mark_claude_auth_error('unknown', …)` in both branches.

Test: none found for the timeout branch.

#### R2 · low · L — clearing a saved key does not "let the shell value take over"

`agent_routes.py:2653-2654` assigns `os.environ[key]` unconditionally, so a
value the server was started with is overwritten on save. `:2655-2657` then
pops the key on an empty value with the comment "let shell env take over";
the shell value was already overwritten and is gone until a restart. The
startup hydrate has the opposite, documented rule (shell-set variables win).

Smallest fix: remember the pre-save `os.environ` value per key and restore it
on clear, or correct the comment.

#### R4 · low · M — `_kill_pid` reports success whatever `taskkill` returned

`agent_routes.py:645-647` returns `True` without reading the return code, so
"access denied" and "no such process" both read as killed. `:648-649`
swallows the subprocess exception without a log.

#### R5 · low · L — second copy of the Windows liveness probe pass 1 found wrong (call-site half of A6)

`agent_routes.py:618-628` is the same `OpenProcess`-succeeds-means-alive test
as pass 1 A6: an exited process that anyone still holds a handle to reads as
alive. This copy feeds `_live_registered_processes`, which defers worktree
removal while it reports a live process. Fix with A6, in one shared helper.

#### R7 · low · E — `_compute_code_delta` dumps the full history twice to subtract one from the other

`agent_routes.py:1576` (`rev-list --branches HEAD`) and `:1581`
(`rev-list <base>`) each return every reachable commit, built into two Python
sets per isolated completion. **Measured** on this repository:
`rev-list --branches HEAD` is 4,941 commits, 203 KB, 26 ms. Small today,
linear in history. Smallest fix: one `rev-list --branches HEAD --not <base>`.

#### R9 · low · E — `_register_process` parses the whole project record to read its name

`agent_routes.py:1868-1873`, once per spawned process; exception swallowed
without a log. Call site of 2026-09-10 E2.

#### R14 · low · L — an expired captured login is killed without its process tree, and finished ones are never removed

`agent_routes.py:3719` is `proc.kill()` on a CLI shim (the node child survives
on Windows; same shape as pass 1 A2). `:3716-3717` returns early for a session
that already exited, so its entry stays in `_captured_login_sessions` until
the next login for that provider replaces it.

#### R15 · low · M — request bodies used without a type guard

`agent_routes.py:2011-2012` (`request.get_json() or {}` then `.get`: a JSON
list body is a 500), `:3871-3872` (`.strip()` on a non-string `code`),
`:2630` (`.strip()` on a non-string `key`). Each is a 500 instead of a 400,
nothing worse. Further instances are added to this finding by later sections
rather than raised separately.

#### R16 · low · E — the provider list reads the same 512 KB log tail once per runtime

`agent_routes.py:2497` calls `_recent_quota_failures(rt.name)` inside the
per-runtime loop; each call seeks and reads the last 512,000 bytes of
`clayrune.log` and regex-scans it (`:12465-12485`). Eight runtimes are
registered (`mc/agent_runtime.py:11523-11530`), so one list load is eight
reads of the same bytes. **Measured:** the scan alone is 0.6 ms per call on a
synthetic 512 KB chunk; the reads are not measured. Smallest fix: read and
group by provider once, before the loop.

**Sound in this stretch:** the env-var name allowlist
(`agent_routes.py:2634-2645`) does stop the route being used to overwrite
`PATH`/`HOME`; load and save run under `_provider_env_lock`
(`:2648-2651`). The three stream observers keep their never-raise contract
and log what they catch (`:4464-4465`, `:4487-4488`, `:4505-4506`), which is
the project exception policy applied correctly.

## 2. `mc/blueprints/agent_routes.py` lines 4733-9160

Universal capability text, `_build_agent_context`, TodoWrite sync, the two
Claude stream readers, the agent-log file helpers, the fence lookup routes,
the revive paths, `_note_claude_sid`, `_log_agent_dispatch_pending`.

#### R19 · medium · M — the operator's first name is hardcoded in prompt text sent to every agent on every install

`agent_routes.py:5103`, `:5107`, `:5114`, `:5119` (the roster block) and
`:5261` (the Floor-figure paragraph) are string literals that name one
person, inside text appended to the system prompt. The same function reads
the configured name two lines later (`:5233`, `state.CONFIG.get('user_name')`)
and uses it correctly at `:5294`. On any other install the agent is told that
someone it has never heard of "will say" the roster names and "cannot open"
a Task-tool helper. This is the case the project's own binding rule names
(nothing operator-specific in the repo; rules files were the first incident,
this is the same thing in code).

Smallest fix: substitute `user_name or 'the user'` in those five literals.

Test: none. `TestDelegatedPersonalessIdentity` pins the `:5260` paragraph's
text, so it will need the same substitution.

#### R24 · medium · L — the TodoWrite backlog key is the Claude session id, which changes on every respawn, so one todo becomes several backlog rows and the old ones stay "in progress" for good

The key is `claude_session_id or id or session_id`
(`agent_routes.py:6279-6281`, `:6697-6699`, and the note writer at
`:5899-5901`), hashed with the todo text into the row's `agent_ref`
(`:5821-5825`). `_sync_todowrite_to_backlog` documents it as a "stable
identifier" (`:5969-5970`). `_note_claude_sid` documents the opposite: "a
respawn/resume starts a NEW claude_session_id" and keeps the old ones in a
history list (`:8786-8794`).

So after any respawn (context rollover, idle eviction, a revive after a
restart) the next `TodoWrite` hashes to new refs: every todo is inserted again
as a new backlog row (`:6009-6024`), the earlier rows are never updated again,
and `_auto_snapshot_notes_on_turn` only matches rows whose
`agent_session_id` equals the current id (`:5932`), so rows left
`in_progress` under the old id stay that way. Read, not run.

Smallest fix: key on the Clayrune `session_id`, which survives respawns.

Test: none found that calls the sync twice across a changed
`claude_session_id`.

#### R26 · medium · E/L — the chat buffer is capped in one of the readers only

`agent_routes.py:6775-6778` trims `log_lines` to 1,500 when it passes 2,000,
inside the Claude Mode B loop. The Claude Mode A reader (`:6141-6436`) has no
trim, and that call is the only use of `_log_lines_trim.trim` in `mc/` (grep
of the tree, so the non-Claude readers in `mc/agent_runtime.py` have none
either). A Mode A or non-Claude session keeps one session dict across
follow-ups, so its buffer grows for the life of the conversation, and every
consumer that copies or ships the buffer grows with it.

Smallest fix: move the cap into one helper called by every reader's append
path.

Test: none asserts a bound on a Mode A or non-Claude buffer.

#### R23 · low · E/M — the per-turn backlog note writer parses the whole project record on every turn, writes the wrong text, and hides its own I/O errors

`_auto_snapshot_notes_on_turn` runs at every `result` event
(`agent_routes.py:6358`, `:6487`). With 20 or more characters of reply text it
calls `load_project` (`:5923`) before it knows whether the session owns any
backlog row; almost none do. **Measured** 8.8 ms on the largest record here.
The docstring says it appends "the last substantive assistant text"
(`:5896`); `:5918` joins every fragment of the turn and keeps the first 300
characters. Load and save failures return silently (`:5924`, `:5956`) and
the outer handler is `except Exception: pass` (`:5958-5959`), on state-file
I/O.

Smallest fix: skip unless the session has synced a TodoWrite list (one flag
set at `:6282`); take the tail; log the three handlers.

#### R17 · low · L — the context fingerprint does not cover what the context reads

`_context_fingerprint` (`agent_routes.py:1072-1140`) says it signs
"everything `_build_agent_context` reads that can change while a session is
alive", and `_respawn_sysprompt_args` reuses the stashed prompt while it holds
(`:1224-1227`). Not signed: the project record (`current_task` `:5789`, name
`:5304`, `activity_log` `:5688`) and the config keys read at `:5195`
(`context_profile_overrides`), `:5302` (`brief_replies_always_enabled`),
`:5478`, `:5513`, `:5537`, `:5558`, `:5637`, `:5669` (six feature flags).
Changing a project's current task, or switching continuity or coordination
off, leaves a long-lived chat's prompt carrying the old line or the disabled
block until an unrelated memory file changes. A stale prompt, not a wrong
action.

Smallest fix: add those keys and the project record's mtime to the hash.

#### R20 · low · L — the skills catalog decides Claude-or-not from the project default, not from the session's engine

`_skills_catalog_block` reads `project.get('provider')` or the global default
(`agent_routes.py:4961`). `_build_agent_context` has the session's effective
`provider` and uses it for its other two engine decisions (`:5193-5194`,
`:5217-5218`) but calls the catalog with the bare project (`:5415`). Dispatch
hides this by passing a project copy with the provider overwritten (`:11454`,
`:12712`); the respawn and revive builders do not (`:1181`, `:1231`, `:7966`,
`:7990`). A Claude chat on a project whose default engine is not Claude gets
the full skills catalog appended on respawn or revive and not on dispatch.

Smallest fix: pass `provider` into `_skills_catalog_block`.

#### R27 · low · L — the Mode A reader's failed-resume recovery skips the ownership check its Mode B twin has

Mode B: `agent_routes.py:6566` requires `_completion_owned_by(session,
my_proc)`. Mode A: `:6430-6435` does not, so a superseded reader that reaches
its `finally` while the session reads `error` can call
`_auto_recover_failed_resume` on a session another process owns. The
`_resume_recovery_attempted` latch is checked at `:6432` and set at `:6794`
with no lock between.

#### R28 · low · L — a failed opening write in the Mode B fresh-retry leaves a process with no reader

`_auto_recover_failed_resume` publishes the new process (`agent_routes.py:
6846-6857`), then writes the opening message (`:6885-6886`), then starts the
reader (`:6887`). If the write raises, `:6891-6894` sets `status = 'error'`
and returns: `session['proc']` is the new process, `process_alive` is True,
the tracker has it registered, and nothing waits on it, unregisters it or
runs completion. Start the reader before the write, as the revive path does
(`:8173` then `:8202`).

#### R29 · low · L/E — the fence's trigger-type lookup iterates the live session dict unguarded, and a miss parses every project's log

`agent_routes.py:7050` iterates `agent_sessions.values()` directly; the
sibling route snapshots it (`:7370`). A dispatch that inserts during the
loop raises `RuntimeError` and the hook's call is a 500. The fence treats a
failed lookup as "unknown" and falls back to the launch marker, so this
degrades safely. On a miss, `:7058-7066` reads and parses every
`*_agent_log.json`. **Measured:** 26 files, 12.6 MB, 42 ms per miss. This is
2026-09-10 E3's "trigger-type glob", still open; `:7061` swallows read errors
without a log.

#### R31 · low · E — a revive parses the transcript twice

`_revive_history_lines` calls `_transcript_buffer_lines_and_ts`
(`agent_routes.py:7650`, parse at `:7717`) and then
`_buffer_truncation_notice`, which parses the whole file again with
`max_messages=100000` only to count messages (`:7673`); `:7678` swallows its
errors. Return the total from the first parse.

#### R22 · low · L/E — the recent-conversations block reads the live session dict unguarded and parses the agent log to label five rows

`agent_routes.py:5710-5717`: a concurrent insert raises inside the loop, the
`except Exception: pass` leaves the status map half built, and conversations
are labelled from the stale log instead. `:5720` (and `:5740`) parse the full
agent log (**measured** 5.1 ms for the 2.07 MB, 500-row log here) per context
build. 2026-09-10 E3, still open at this site.

#### R25, R30 · low · M — dead code that still looks live

`agent_routes.py:6290-6308` and `:6708-6723` handle a native
`AskUserQuestion` tool call, which the file's own docstring says headless
Claude does not have (`:5835-5837`); both kill with `proc.kill()`, not the
tree. `attend_session` returns 410 from `if True:` (`:7113-7117`) and keeps
34 lines of unreachable handler below it (`:7118-7151`).
`_ATTEND_ONCE_ELIGIBLE_TRIGGER_TYPES` (`:7179-7181`) is a hand copy of the
fence's set; the two match today, and no test pins this copy (the tests pin
`launch_marker` against the fence, not this one).

#### Methodology notes for this stretch

- The two Claude readers duplicate about 150 lines of assistant/tool-use/
  tool-result handling (`agent_routes.py:6195-6343` against `:6611-6751`).
  R26 and R27 are what that costs: a fix landed in one copy.
- The Mode B and Mode A revive branches each build the session dict from a
  70-line literal (`:8088-8158`, `:8263-8313`) that has to be kept key for key
  by hand; the comments at `:8129-8134` record two bugs of exactly that kind.
- `_revive_from_agent_log` allocates a delegation turn (`:7857`), records a
  resume and a rollover (`:7962`, `:7965`) and writes the activity line
  (`:7977`) before the spawn; a spawn failure returns `None` (`:8083`,
  `:8258`) without undoing them and the caller then starts a fresh dispatch.
- Agent-log cost, **measured** on the 2.07 MB log: parse 5.1 ms, serialise
  5.9 ms, so one `_update_agent_log` is about 11 ms of CPU plus the write,
  under the project lock. `mc/launch_transaction.py:86`, `:111` and `:179`
  add three more full parses per launch. The per-dispatch total is counted in
  the dispatch section.
- Further unguarded request fields for R15: `agent_routes.py:7367`.

**Sound in this stretch:** `_update_agent_log` (`agent_routes.py:7425-7445`)
serialises read-modify-write behind a per-project lock and a file
transaction and never saves over a forgiving read; `_save_agent_log` is
atomic (`:7463`), so the display-read quarantine at `:6993` cannot be
triggered by a racing writer. `_load_agent_log` keeps I/O errors and parse
errors apart (`:6970-6994`). `consume_attend_once_pass` re-derives the match
under the project lock and `_CSID_LOCK` and checks the pass's bound session
(`:7378-7412`); `_note_claude_sid` takes only `_CSID_LOCK` for its write
(`:8783-8794`), which keeps the reader thread off the project lock. The
read-floor failure is logged, not swallowed (`:5583-5590`). The single-emit
gate (`:6094-6121`) is applied on every terminal write in both readers. The
revive paths refuse rather than fall back to the project checkout when an
isolated conversation's worktree cannot be restored (`:8037-8045`,
`:8079-8082`, `:8254-8257`).

---

## 3. `mc/blueprints/agent_routes.py` lines 9160-12550

Spawner and workflow wake, delegation delivery loop, usage-breakdown
checkpoints, completion logging, queued follow-up, model router, runtime
(non-Claude) dispatch, character and prior-conversation lookups, rollover
handoff, quota block. `_dispatch_via_runtime` (`:11221-11669`) was read here;
its two candidates need the Claude dispatch path for comparison and are in
section 4.

#### R38 · high · E/L — the Scribe model call runs while the project lock is held

`_log_agent_completion_body` ends by calling `_write_session_memory`
(`agent_routes.py:10592-10596`). That function is synchronous down to the
provider CLI: `mc/memory.py:4919` → `_scribe_extract` → `_scribe_summarize_text`
→ `_model_call` (`mc/memory.py:6169`, `:155`). For a transcript over the
single-call limit it is a map-reduce: one model call per chunk in a loop, then
one more to reduce (`mc/memory.py:6253-6271`).

Every caller of the completion body holds the project manager lock when it
calls it:

- Mode A reader `finally`: `agent_routes.py:6390` takes the lock, `:6416`
  calls `_log_agent_completion`.
- Mode B `finish`: `agent_routes.py:6521` takes the lock, `:6560` calls it.
- Runtime (non-Claude) exit hook: `agent_routes.py:10645` takes the lock,
  `:10686` calls it. Non-Claude runtimes exit once per turn, so this is every
  turn of every Gemini, Codex and Qwen chat.

So for the length of one or several model calls, nothing else in that project
can take the lock. The file takes it at 67 places (counted:
`get_manager(...).lock` / `manager.lock`), including dispatch, send, follow-up,
stop, interrupt and the delegation inbox (`:9726`). A user who sends a message
to a second chat in the same project while a first chat is being scribed waits
for the Scribe. Live config has `scribe_enabled` true (read from
`GET /api/config`).

**Read, not measured**: timing it needs a provider CLI, which this pass may not
start. The bound is the oneshot timeout, and pass 1 A2 found that timeout does
not kill the process tree on Windows.

The same lock span also covers `_compute_code_delta` (`:10280`, R7), the
worktree merge and removal (`:10292`), the transcript re-parse of R37
(`:10366`) and, for a queued follow-up, the whole system-prompt rebuild
(`:6419-6425` calls `_auto_dispatch_followup` inside the `with`; its own
docstring at `:10770-10773` says that phase runs "with NO lifecycle lock
held").

**Fix:** under the lock do only what must be atomic with the status flip: the
status change, the agent-log row, the spawner wake. Return the facts the rest
needs and run memory write, telemetry and usage-breakdown writes after the
`with` block. `_scribe_extract` already has its own per-project busy guard
(`mc/memory.py:6137-6140`), so it does not need this lock.

**Test:** none. `tests/test_runtime_completion_log.py` asserts the row is
written, not what is held while it is.

#### R37 · medium · E — every Mode B turn re-parses every transcript the session has ever had

`_write_usage_breakdown_turn_checkpoint` runs at each Mode B turn boundary
(`agent_routes.py:6515`) and calls `_session_cumulative_transcript_telemetry`
(`:10014`). That does, per turn (`:8876-8907`): a full project-record parse
(`:8894`), a full agent-log parse (`:8864`, via
`_session_all_claude_session_ids`), then a line-by-line JSON parse of every
transcript file the session has run under (`mc/memory.py:5795-5808`).

The same function runs again for every running session at every allowance
sample (`:10138`, called from `mc/blueprints/system_routes.py:1143`) and at
process exit (`:10366`).

**Measured** (`_extract_transcript_telemetry_multi`, one file, this box):
9.4 ms on a 0.77 MB transcript (the 90th percentile of 53,835 on disk),
737.6 ms on the largest (81.4 MB). The work grows with the conversation, so a
long chat pays more on each turn than on the last, and a rolled-over chat pays
for all of its transcripts.

The turn-boundary call is outside the lock (`:6515` is after the `with` at
`:6502`), which is right; the exit call is inside it (R38).

**Fix:** keep running totals per transcript keyed on `(path, size)` and parse
only the bytes appended since the last read. The dedup set of `message.id`
has to be kept with the totals.

**Test:** `tests/test_codex_usage_checkpoint.py` and
`tests/test_effort_continuity.py` reference the function for its result, not
its cost. Two swallowed exceptions on file reads here: `:8870` and `:8906`
return empty with no log.

#### R43 · medium · L — the 20-hour quota block lasts about an hour, and a later success does not clear it

`_model_quota_blocked` refuses a model for `_QUOTA_BLOCK_WINDOW_HOURS = 20`
after a quota failure (`agent_routes.py:12496`, `:12530`); the comment at
`:12489-12495` argues for 20 rather than 24. Its only evidence is
`_recent_quota_failures`, which reads the last 512,000 bytes of the server log
(`:12450`, `:12471`).

**Measured** on this box: the log is 518.7 MB; its last 512 KB is 4,487 lines,
4,107 of them HTTP access lines, spanning 57.6 minutes. So a block lifts after
roughly an hour of normal polling, not twenty. The docstring admits a scrolled
line "silently stops being reported" (`:12457-12459`) but the constant and its
comment state a window the mechanism cannot hold.

It is wrong in the other direction too. Only failures are logged in this
format (`:10680-10683`), "last occurrence wins" among failures (`:12485`), and
a refused dispatch produces no new line. So inside the window, a model whose
quota was restored (reset, or a top-up) stays refused: no successful run can
be recorded because no run is allowed.

Cost: the delegation inbox calls it under the project lock for every delivery
attempt (`:9758`), and R16 covers the provider list. **Measured**: the regex
pass over the tail is 0.9 ms; the cost is the 512 KB read.

**Fix:** record quota failures in a small state file (`provider`, `model`,
`ts`), cleared by the next successful turn of that model and by an explicit
user retry. That removes the log read, makes the window real and gives the
block an exit.

**Test:** `tests/test_auth_routes.py`, `tests/test_callback_pingpong.py` and
`tests/test_delegation_delivery.py` stub or call the function; none builds a
log larger than the tail, so none can see the window collapse.

#### R44 · medium · L — the rollover handoff lists finished children as still running

`_in_flight_children` skips a child only when its status is `'done'` or
`'error'` (`agent_routes.py:12321`). No session is ever given status `'done'`:
the values assigned in `agent_routes.py` and `mc/agent_runtime.py` are
`running`, `idle`, `completed`, `error` and `stopped` (counted by grep over
both files; the one `'done'` in `mc/` is an OAuth flow in
`mc/desk_oauth.py:800`).

So a child that completed, was stopped, or is a Mode B child idle after its
turn is listed. `_auto_fresh_handoff` then writes into the fresh
conversation's first message: "Still waiting on N dispatched session(s) — do
not re-dispatch these, they will notify back when done" (`:12378-12385`). The
notification already happened, in the conversation that was just rolled away.
The agent is told to wait for a wake that will not come and told not to redo
the work.

**Fix:** `if s.get('status') != 'running': continue` (and decide deliberately
about a Mode B child idle with open background tasks).

**Test:** `tests/test_context_rollover_trigger.py:145-155` pins the bug: its
"finished" fixture uses `'status': 'done'`, the same value that never occurs.

#### R41 · medium · L — eighteen places iterate the live session dict without a snapshot

`agent_sessions` is one process-wide dict. Eight sites in this file iterate
`list(agent_sessions.values())` or `.items()`; eighteen iterate the dict
directly: `agent_routes.py:5710`, `:7050`, `:11959`, `:12094`, `:12108`,
`:12145`, `:12316`, `:12902`, `:13802`, `:16280`, `:16973`, `:17046`,
`:17060`, `:17536`, `:17898`, `:18195`, `:18355`, `:18511`.

A dispatch on any thread inserts a key (`:11434` for example). The per-project
lock does not help: the dict is global and the inserting dispatch can belong
to another project. CPython raises `RuntimeError: dictionary changed size
during iteration` in the iterating thread.

`:16280` is the `/agent/status` poll and `:17898` is the conversations list:
there it is a 500 on a poll. `:12094`, `:12108`, `:12316` and `:12902` are on
the dispatch and rollover path, where it aborts a dispatch part-way. R22
(`:5710`) and R29 (`:7050`) in section 2 are two instances of this.

Narrow per call (the loop is short), but the poll runs every few seconds for
every open tab.

**Fix:** one `_sessions_snapshot(project_id=None)` helper returning a list,
used at all 26 sites.

**Test:** none can; it needs two threads.

#### R34 · low · L — the interim-report reply scan skips a reply that opens with a marker (call-site half of pass 1 A1)

`_last_reply_text` (`agent_routes.py:9160-9188`) is a second copy of the
trailing-reply scan pass 1 reported as A1. It treats any block starting with
`[` or `> ` as not-reply (`:9179-9188`). A reply whose last block opens with a
`[file:…]` or `[browser:…]` marker, a markdown link or a blockquote is passed
over and the previous text block is returned as the reply; blocks are joined
with no separator. Users: the held-notify releases at `:9449` and `:9538` and
the completion backstop at `:10200`.

**Fix:** with A1: one function, in `agent_runtime`, used by both.
**Test:** none for a marker-first reply.

#### Low findings in this stretch

- **`_router_stat`, `agent_routes.py:10949-10990`** (E/M). Runs on every Claude
  dispatch (`:13125`) and follow-up (`:14969`) whether or not the router is
  on: read, parse and atomically rewrite a stats file to add one. The
  read-modify-write is unlocked, so two dispatches lose a count. Both
  `except` blocks are silent on a state file (`:10968`, `:10989`); project
  policy says log. The sidecar is suffix-excluded
  (`mc/blueprints/project_routes.py:442`), so the DATA_DIR rule holds.
- **Guardian interim release, `:9445-9451`, `:9534-9540`** (L). The held wake is
  popped under the lock and sent outside it. A turn that completes in that gap
  enqueues the same `event_id` with a different payload; one of the two is
  refused as conflicting (`:9669-9676`). If the interim text wins, the turn's
  real answer is the one dropped. Narrow.
- **Comments that no longer describe the code** (M). `:10351-10355` and
  `:9653-9654` say the spawner wake is "fired on a thread through the ordinary
  /agent/send route"; it is a synchronous durable enqueue. `:10900` says the
  context budget is 20 KB; the code tests `40 * 1024` (`:10920`). `:10770-10773`
  is covered under R38.
- **`:11395`** (M). `effort_support` is `'supported' if provider_name == 'codex'`.
  The fact belongs on `runtime.capabilities()`; a second runtime with an effort
  knob will be reported as unsupported until someone finds this line.

#### Sound in this stretch

- **Workflow step wake.** The live path sets `_notify_workflow_sent` before the
  daemon thread runs (`:9285-9286`) and the thread only logs a failure
  (`:9639-9644`). Checked as a possible lost wake and cleared:
  `workflows.reconcile_running_steps` replays a confirmed end every 30 s
  (`mc/workflows.py:2306-2322`).
- **Spawner wake.** The latch is set only after the durable enqueue commits
  (`:9268-9272`).
- **Inbox handoff** (`:9710-9817`). Claim revalidated before and after waiting
  for the lock, shutdown admission checked under the lock, uncertain outcomes
  never replayed.
- **Delivery loop lifecycle** (`:9820-9960`). Start and stop are linearized on
  one lock; a timed-out stop keeps ownership; no join under the lock.
- **Completion replay** (`:10263-10270`, `:10510-10514`). A replayed completion
  reuses the row the dead run wrote and skips merge-back.
- **`_log_agent_completion` wrapper** (`:10194-10207`). The wake is attempted
  even when the body raises.
- **Phantom-reap guard** (`:10546-10558`) and the turn-start checkpoint on the
  queued follow-up (`:10885`).
- **Runtime start failure** (`:11628-11655`). Does not overwrite a replacement
  owner; a missing CLI trips the breaker on the first failure.
- **`_resolve_character` strict mode** (`:11708-11800`). Raises only for an
  explicitly picked persona.
- **Handoff builder** (`:12011-12081`). Says how many turns it omitted and which
  native id the turns came from.

---

## 4. `agent_routes.py` lines 12551-15460, and the delegation delivery modules

`_dispatch_agent_internal`, the dispatch / model / send / stream / followup
routes, the stdin write helpers; then `mc/delegation_delivery.py`,
`delegation_settlement.py`, `delegation_transcript_reconcile.py`,
`delegation_waiting.py`, `delegation_wake.py`, `delegation_wake_absent.py`
and `delegation_retry.py`, all read in full.

#### R51 · high · E — the delivery loop re-validates every completion ever recorded on every 5-second drain: 4-6 s of work per cycle, measured

`mc/delegation_delivery.py:788` calls `store.recover_sources()` at the top of
every `drain_once`, and the loop drains every 5 s
(`agent_routes.py:9820`, `:9879-9885`). `recover_sources`
(`delegation_delivery.py:569-581`) selects **all** rows of
`completion_sources`, a table nothing prunes except a revoke, and for each row
opens a connection (three PRAGMAs, `:94-100`) to run `_revoked` (four or five
queries and a `json.loads` of the payload, `:191-213`), then calls `enqueue`,
which opens another connection, takes `BEGIN IMMEDIATE`, re-serializes the
payload canonically, runs `_revoked` again, selects the outbox row and commits
(`:315-343`).

**Measured** on a backup copy of this install's database (44.5 MB; 2,198
completion sources holding 12.3 MB of payload; 2,262 outbox rows):
`recover_sources()` took 4.7 s, 4.1 s and 5.8 s on three consecutive calls and
recovered 0 rows each time. `reconcile()` beside it took 1 ms. So the delivery
thread spends roughly half its life here, takes the SQLite write lock about
2,200 times per cycle, and a child's report that lands just after a drain
started waits for it. The cost is linear in the number of completions the
install has ever recorded.

- **Fix:** the crash window this guards (completion recorded, enqueue not
  committed) only exists at startup, where `recover_outbox` already runs
  (`agent_routes.py:9853-9860`). Run `recover_sources` there and after a failed
  enqueue, not per drain. If it must stay in the loop, make it one anti-join
  (`completion_sources LEFT JOIN outbox USING(event_id) WHERE outbox.event_id IS NULL`)
  and delete a source row once its outbox row is terminal.
- **Tests:** none could catch it; no test seeds more than a handful of rows.

#### R48 · high · L — `/agent/send` starts a session labelled attended whoever calls it; only `/agent/dispatch` attributes the caller

`agent_dispatch` derives `trigger_type` from the caller and then overrides the
body's hints with OS-level attribution
(`agent_routes.py:13583-13602`, `_caller_attribution.resolve_dispatch`). The
comment at `:13586-13596` says why: an agent-spawned session must not be
"indistinguishable from a human clicking +New chat", because `trigger_type` is
the one signal the fence, the secrets vault's unattended check and the config
gate trust.

`agent_send` reaches the same dispatcher twice with no `trigger_type` and no
`source`: the fresh path (`:14045-14047`) and the native-resume path
(`:13811-13813`). `_dispatch_agent_internal` defaults `trigger_type='manual'`
(`:12552`). `_caller_attribution` is referenced exactly once in the file
(`:13601`). So a managed agent that POSTs `/agent/send` with no `session_id`,
or with one that resolves to nothing, gets a child session marked `manual`:
unfenced, counted as attended by `is_unattended_caller`
(`mc/unattended.py:83`). The same route also drops `character`, so the child
runs as the project default persona.

- **Fix:** in `agent_send`, run the same `resolve_dispatch` before either
  `_dispatch_agent_internal` call and pass `trigger_type`/`source` through.
  Better, move attribution into `_dispatch_agent_internal` for every call that
  has a request context, so a third route cannot repeat this.
- **Tests:** `tests/test_caller_attribution.py` and
  `test_caller_attribution_followup.py` exercise the attribution function and
  the dispatch route; neither posts to `/agent/send`. Not exercised live (it
  would mean a POST to the running server).

#### R45 · high · L — `runtime_callbacks` is dropped on the Claude streaming branch, so a Hivemind orchestrator's exit hook never runs

`_dispatch_agent_internal` stores `runtime_callbacks` on the Mode A session
dict (`agent_routes.py:13491`) and on the throwaway `_pending_session`
(`:13243`), but not on the Mode B dict built at `:13290-13370`. Mode B's
finish calls `_run_runtime_callbacks` (`:6562`), which reads
`session['_runtime_callbacks']` (`:10758`) and finds nothing. The
`housekeeping` key has the same gap (`:13490`, Mode A only).

Mode B is chosen whenever `use_streaming_agent` is on (default True,
`server.py:129`; `agent_routes.py:12891-12892`) and the caller did not pass a
`prepared_context`. The `mode='A'` argument the runtime seam accepts
(`mc/blueprints/hivemind_routes.py:1060`) is not read by
`_claude_dispatch_hook` (`server.py:3341-3388`). The caller this hits is the
Hivemind orchestrator (`hivemind_routes.py:1308-1343`): its `_on_exit` is the
only thing that removes the id from `_hivemind_orchestrating` on success
(`:1311`), and `_spawn_orchestrator` returns `None` while the id is still in
that set (`:1199-1202`). A Mode B process also does not exit at turn end, so
even a stored callback would not fire until eviction.

- **Fix:** store `_runtime_callbacks` (and `housekeeping`) on the Mode B dict
  too, and either honour `mode='A'` in the dispatch hook or fire the callback
  at turn completion for Mode B.
- **Tests:** `tests/test_claude_dispatch_hook_contract.py:37` checks only that
  the kwarg is forwarded; `tests/test_hivemind_worktree_dispatch_hook.py:132`
  forces `use_streaming_agent` False, which is the configuration where the bug
  cannot occur. Read, not run: Hivemind itself is another pass's scope.

#### R42 · medium · L — resuming a conversation by an earlier id of a rolled chain falls back to the project's default engine

`_prior_conversation_provider` (`agent_routes.py:11906-11924`) matches a log
row on `claude_session_id` or `provider_session_id` only. Its sibling
`_prior_character` had to learn the `claude_session_ids` lineage
(`:11833-11840`) after "Dave's chat came back as Vector". When the resumed id
is an earlier link of a rolled conversation, or its row has been evicted from
the 500-row log (pass 1 **S1**), the owner set is empty, `provider_override`
stays `''` (`:12647`, `:12664`) and `resolve_provider` falls to the character's
engine, then the project default (`:12707`). On a project or persona whose
default engine is not Claude, a Claude conversation is dispatched to the other
runtime with a Claude session id as its `resume_id` (`:12870`).

- **Fix:** match `claude_session_ids` as `_prior_character` does, and when no
  row matches but `_find_transcript_file` finds a Claude transcript, answer
  `'claude'`.
- **Tests:** no test resumes by a lineage id on a non-Claude-default project.

#### R47 · medium · L — Claude Mode A passes the whole prompt in argv through a `.cmd` shim, with no length guard

Six sites build `[claude, …, '-p', <full text>]`: `agent_routes.py:6861`,
`:8233`, `:10798`, `:13416`/`:13419`, `:15191`, `:15869-15881`.
**Measured:** `_resolve_claude()` resolves to a `.CMD` shim on this box, so the
launch goes through `cmd.exe` and its roughly 8,191-character line limit.
`mc/agent_runtime.py:5439-5449` states that limit and provides
`_guard_win_argv_length`; it is called for Goose and Kiro only
(`agent_runtime.py:10783`, `:10853`, `:11429`, `:11455`, `:11487`) and never
from `agent_routes.py`.

Mode A is what runs for every `prepared_context` dispatch (Hivemind workers,
`:12891`) and for any project with streaming off. One trigger is certain: a
Mode A auto-fresh prepends a handoff of up to 8,000 characters of turns plus
header and footer to the task (`:12924`, `:15178`).

- **Fix:** send the Mode A prompt over stdin (`-p` with no argument, then
  write and close, which `_bounded_stdin_write(close_after=True)` already
  supports), or call the guard and fail with a named error.
- **Tests:** none builds a Mode A command with a long prompt. Not in pass 1.

#### R49 · medium · L — a failed non-Claude follow-up answers `{'ok': True}`

`agent_routes.py:14624-14636`: when `runtime.write_followup` (or the stash
refresh, or the token rollover before it) raises anything other than
`VendorFenceError` or `PromptDeliveryError`, the handler appends a chat line,
sets `status = 'error'`, and then **falls out of the `except`** to
`_log_agent_activity(… "Agent follow-up …")` and
`return jsonify({'ok': True, 'session_id': session_id})`. The comment three
lines up (`:14631-14632`) records that `ok: True` on an undelivered message
"is what let the emailed-answer relay mark the question answered". An
`OSError` from the respawn takes exactly that path. The exception is not
written to the server log either.

- **Fix:** end the `except` with a 502 `{'ok': False, 'delivered': False}` for
  every exception, and `_log` it.
- **Tests:** `tests/test_agent_send_non_claude_revive.py` covers the revive
  path; nothing raises a plain exception out of `write_followup`.

#### R50 · medium · E — follow-up work done under the project lock that the Claude spawn path was careful to move out

The Claude branches build the command under the lock and spawn after it
(`agent_routes.py:14775`, `:14807`, `:15034`). Three things in the same route
do not follow that:

- **Non-Claude follow-up, `:14541-14636`, entirely under the lock:**
  `_native_followup_context.refresh_stash` (a context rebuild, `:14592`),
  `_mode_a_token_rollover` (`:14617`), `_bridge_images_for_blind_model`
  (`:14621`, one vision **model call per image** when the session's model
  cannot see, `mc/vision_bridge.py:213-224`) and `runtime.write_followup`
  (kill and respawn).
- **Claude dead-process branch:** `_auto_fresh_handoff` (`:14730`) parses the
  transcript of a conversation that is, by definition of the trigger, over the
  size limit; `_fresh_context_for` runs at `:14685`, `:14691`, `:14703`,
  `:14736` and `:14851`. Mode A does the same roll in its worker thread,
  outside the lock (`:15154-15180`).
- **Native-resume branch of `/agent/send`, `:13801-13826`:** a whole-transcript
  `extract_chat_turns` and the complete `_dispatch_agent_internal` (context
  build, worktree add, spawn) inside `with get_manager(project_id).lock`.

Read, not measured (measuring needs a provider process). Every route that
takes the manager lock for that project waits, including `/agent/stop`.

- **Fix:** same shape the Claude branch already uses: decide and mark under the
  lock, do the build, the bridge and the spawn after it.
- **Tests:** none times or asserts lock scope.

#### R53 · medium · L — three direct `stdin.close()` calls under the project lock, against the rule written for the 2026-09-23 freeze

`_bounded_stdin_close` exists because an inline close "froze while holding the
project lock … and every subsequent /agent/send for the project piled up
behind that lock"; its docstring says never to call `proc.stdin.close()`
directly from code holding `get_manager(...).lock`
(`agent_routes.py:15423-15433`). Three sites still do: `:14772` and `:14864`
(both inside the `with` opened at `:14541`), and `_stop_session` at `:15265`,
whose own docstring says it must be called with the lock held. The hang is
bounded today, because the write watchdog kills the child after 20 s
(`:15293`, `:15381-15399`), or later while the startup grace loop keeps
extending (`:15388`). For that long, the project's stop and send routes wait.

- **Fix:** replace the three calls with `_bounded_stdin_close(proc, context=…)`.
- **Tests:** none; the interrupt route's own close was converted (`:15701`).

#### R54 · medium · E — the receipt reconciler re-reads every transcript newer than the oldest parked report, every five minutes

`mc/delegation_transcript_reconcile.py:26`, run from the delivery loop
(`agent_routes.py:9878`). The full sweep scans each parent's transcript
directories and skips only files older than the oldest candidate row
(`:264`, `:272`). Rows parked with no handoff boundary never resolve without a
person, so that lower bound never moves forward.

**Measured** with the module's own read-only `--dry-run` on this install: 85
parked rows (63 eligible) across 6 projects, 7,297 transcript files scanned,
1,773 skipped by mtime, 6.98 s, 0 matched. Every sweep starts each file at
byte 0.

Two more costs in the same loop, read, not measured: `settle` → `evaluate` →
`read_snapshot` reads a whole parent transcript, `json.loads` every line and
hashes it (`mc/delegation_settlement.py:73-98`) **under the project manager
lock** (`settlement.py:379`, guard wired at `agent_routes.py:9864`), once per
parked row with no cache across rows sharing a transcript; and the row
signature includes the transcript's mtime and size
(`delegation_transcript_reconcile.py:177-189`), so a parked row whose parent
is still writing is re-read on every 5-second tick.

- **Fix:** leave rows whose check verdict is final for a machine
  (`no_boundary`, `ambiguous_receipt`) out of the sweep; remember
  `(path, size, mtime) → scanned-up-to offset`; take the snapshot outside the
  lock and re-check identity under it.
- **Tests:** correctness is well covered; nothing bounds the scan.

#### R39 · medium · L — a context-build failure refuses a Claude dispatch but launches a non-Claude one with no rules at all

The Claude path builds context with no `try` (`agent_routes.py:13058`,
`:13075`, `:13094`): a failure fails the dispatch. `_dispatch_via_runtime`
catches the same failure, logs it and continues with `system_prompt = ''`
(`:11450-11470`): no `AGENT_RULES.md`, no shared rules, no persona, no memory,
and nothing in the chat says so. One reachable trigger: `_build_agent_context`
reads the two user-edited rules files with `read_text(encoding='utf-8')` and
no `errors=` (`:5326`, `:5328`), so a single non-UTF-8 byte raises. With that
byte in a rules file, every Claude dispatch on the project is refused and
every Codex or Gemini dispatch runs unruled. (`:5437` reads a sibling file
with `errors='replace'`.)

- **Fix:** read both files with `errors='replace'`, and make the two paths
  agree: refuse in both, or launch in both with a visible chat line.
- **Tests:** none feeds a non-UTF-8 rules file to either path.

#### R52 · low · L — a report delivered by respawning an evicted Mode B parent is filed as "outcome unknown"

`_process_inbox_locked` demands a write acknowledgment from any Mode B parent
(`agent_routes.py:9765`). When the parent's process is gone (idle eviction
leaves `status='idle'`, `process_alive=False`, `:18888-18889`),
`agent_followup` takes the respawn branch and answers
`{'ok': True, 'resumed': True}` with no `stdin_write_ack` (`:15145`); the
respawn's own write waits for an ack only for the limit-resume caller
(`:15117`), not for `durable_ack`, which contradicts `:14431-14433`.
`_finish_handoff` then raises `DeliveryUncertain('parent stdin write
acknowledgment is unknown')` (`:9801-9804`) while the respawn thread delivers
the message. The row carries a handoff boundary, so the reconciler confirms it
from the transcript later (`delegation_settlement.py:207-209`); until then the
parent's chat shows a delivery it cannot confirm, and if the respawn also
rolled to a fresh conversation the boundary names the old transcript.

This install has 27 inbox rows parked `uncertain` with exactly that error; the
data cannot say how many came through the respawn branch.

- **Fix:** pass `wait_for_ack` on the respawn write when `durable_ack` is set
  and return the ack in the body.
- **Tests:** `tests/test_delegation_delivery.py:286-325` covers a live parent
  only (`process_alive: True`).

#### R32 · low · M — five hand-synced session dict literals

Revive B `agent_routes.py:8088-8158`, revive A `:8263-8313`, runtime
`:11359-11420`, dispatch B `:13290-13370`, dispatch A `:13433-13496`. Each is
60-80 keys kept in step by hand. R45 is one divergence this produced
(`_runtime_callbacks`, `housekeeping`); another is that revive B seeds
`_memory_turn` delivery state (`:8179`) and revive A does not.

- **Fix:** one `_new_session_dict(mode, **overrides)` and five call sites.

#### Lesser findings

- **R6, `:1385`, `:12857`, `:13157`** (L, low). The isolation gate is
  `_live_agent_count(pid) < 1` (`:1349-1356`), read with no lock, and the new
  session is published later. Two first dispatches that arrive together both
  count zero and both get the shared tree.
- **R40, `:11434-11438`** (L, low). `_dispatch_via_runtime` publishes the
  session as `running`, `process_alive` before `_log_agent_dispatch_pending`,
  which raises in strict mode; the caller re-raises with no cleanup
  (`:12887-12889`). A failed log write leaves a running session with no
  process.
- **R46, `:13522`** (L, low). The Mode A launch logs `' '.join(cmd)`, and the
  command carries `-p <task>`: the full prompt, incognito included, lands in
  the server log. Latent while streaming is the default.
- **Mode A `--continue`, `:15184`** (L, low). With no `claude_session_id` the
  Mode A follow-up runs `claude --continue`, the most recent conversation in
  that directory, which in a shared tree can be another chat's. Mode B treats
  the same case as "cannot continue, start fresh" (`:14682-14693`). The roll
  decision itself is written twice (`:14711-14741`, `:15154-15182`), and Mode A
  mutates `proc`/`status` with no lock (`:15213-15222`).
- **Send vs follow-up on a failed revive** (L, low). `agent_send` swallows any
  revive exception and falls through to a **fresh** dispatch under a new
  session id (`:13982-13984`, `:14014-14016`), the outcome the MC-1168 comment
  at `:13977` rules out for delivery errors. `agent_followup` has no generic
  `except` on the same call and returns 500 (`:14491-14507`).
- **Unlogged 500s** (M, low). `agent_dispatch` `:13654-13655` and `agent_send`
  `:14061-14062` return `dispatch failed: {e}` with no `_log`; the status code
  for a `ValueError` is picked by `'not found' in str(e)` (`:13650`, `:14056`).
- **Request-body guards** (M, low), adding to R15: `.strip()` on a non-string
  `task` (`:13543`), `resume_conversation_id` (`:13546`), `message`
  (`:14393`); an unhashable `session_id` reaches `in agent_sessions`
  (`:14474`). Each is a 500 instead of a 400.
- **Agent-log parses per resume dispatch** (E, low; prior-audit E3). Counted by
  reading: `_prior_conversation_provider` twice (`:12647`, `:12664`),
  `_prior_character` (`:12681`), `_prior_conversation_settings` three times
  (`:12761`, via `:12766`, `:13030`), `_dispatch_clock_task` (`:11154`), two
  under the lock (`:13208`, `:13224`), three in the launch transaction
  (`mc/launch_transaction.py:86`, `:111`, `:179`). About twelve full parses at
  5 ms each on a 2 MB log (**measured** earlier), so about 60 ms per resume. A
  follow-up into a live process still pays the two in `reserve`.
- **`delegation_waiting.waiting_by_parent`** (E, low). Called on every
  `/agent/status` poll (`agent_routes.py:16279`) and Floor poll.
  **Measured** on a database copy: 5.3-5.8 ms median, fetching 86 rows and
  428 KB of payload to return 3 reports; `no_boundary` rows are discarded
  (`mc/delegation_waiting.py:139-143`) after their payload has been parsed
  (`:133`). Filter them in SQL.
- **`delegation_transcript_reconcile.py:198-200`** (L, low). `json.loads` of a
  stored boundary is uncaught; one corrupt value fails the whole tick, which
  is logged once and then reconciles nothing until the row is fixed.

#### Sound in this stretch

- **Send routing** (`agent_routes.py:13849-13892`). One decision under the
  lock; the prompt is pre-persisted so a status race cannot drop it; the
  conversation-owner rewrite (`:13836-13842`) stops a second process on one
  chat.
- **SSE stream** (`:14187-14374`). Cursor-overshoot reset, per-stream question
  de-duplication, no turn-complete while a question is pending.
- **`_bounded_stdin_write`** (`:15296-15420`). Writer and watchdog off the
  caller's thread, ownership checked before the session is marked failed,
  three-way ack that distinguishes "failed before write" from "unknown".
- **Reader vs sender lock order.** Checked as a possible deadlock (a reader
  waiting on the manager lock while a send holds it across a pipe write) and
  cleared: every write is on a helper thread, and the durable caller defers
  its ack (`:9777`, `:14460-14466`).
- **Launch transaction** (`mc/launch_transaction.py:79-200`). Strict re-read
  before spawn, compare-and-swap restore on a proven refusal, unknown outcome
  recorded and never retried.
- **Turn reservation with no launch** (`:14582`, `:14816`): the
  reservation only wraps the turn-state side effects; no leak.
- **Delivery store.** `BEGIN IMMEDIATE` claim and fenced finish; `accept`
  refuses an event that is not byte-identical to a trusted outbox row
  (`delegation_delivery.py:357-361`); the status list never returns a payload;
  one drain thread, so `reconcile` cannot mark a claim that is still running.
- **Settlement.** Ownership evidence never counts as a receipt; a positive
  match needs the whole header line in a user record newer than the report;
  absence needs boundary, completion token, unchanged prefix digest and a
  terminal record; `authorize_absence` re-evaluates inside the retry
  transaction and spends a one-shot budget.
- **Wakers.** Transition-driven; `blocked → pending` is guarded in the
  statement; `uncertain` is never replayed automatically.

## 5. `agent_routes.py` lines 15461-18968

Stop, interrupt and delete routes, `/agent/status`, the agent log and
conversation listings, transcript reconstruct and search, plans and documents,
usage, and the session guardian. Read fully.

#### R55 · medium · M — four transcript routes put the conversation id from the URL into a file path with no check

`get_project_transcript` (`agent_routes.py:16621`, Claude branch),
`reconstruct_from_transcript` (`:16952`), `get_transcript_full_buffer`
(`:16997`) and `delete_conversation` (`:17032`) pass `<claude_session_id>`
straight to `_find_transcript_file`, which ends in
`_CLAUDE_HOME / e / f'{session_id}.jsonl'` (`mc/agent_runtime.py:2524`). The
Codex branch of the first route does validate, with a UUID regex (`:16590`);
the Claude branch and the other three routes do not.

Werkzeug's default converter only excludes `/`. **Measured** with an isolated
Flask test client (no server import): a request for
`…/conversation/..%5C..%5Cother%5Cabc` reaches the view with the id
`..\..\other\abc`, and on Windows the joined path resolves two directories
above the project's transcript folder.

- The three GET routes then parse and return any `*.jsonl` reachable that way,
  including another project's transcripts.
- `delete_conversation` renames the file to `*.jsonl.deleted` and first
  unlinks an existing `.deleted` copy (`:17074-17078`). The live-session
  check before it compares the raw id (`:17046-17049`), so it does not stop
  this.

POSIX hosts are not affected (a backslash is an ordinary filename character
there).

- **Fix:** one guard at the top of all four routes, the same pattern as
  `:16590` or `[A-Za-z0-9_-]+`; better, once in `transcript_path`.
- **Tests:** none sends an id with a separator to these routes.

#### R58 · medium · L — the guardian's CPU-idle baseline is kept per session, never reset, and not tied to a process

`_proc_is_cpu_idle` stores `_guardian_prev_cpu_total` and
`_guardian_prev_cpu_time` on the session (`agent_routes.py:18618-18621`).
Nothing else touches them (those four lines are the only references in the
repository). The hung check only samples once a session has been silent past
its timeout (`:18833`), and its docstring promises "First sample always
returns False" (`:18592-18594`). That holds once per session, not once per
process or per silent stretch:

1. **After a respawn or rollover** the session has a new process. Its first
   sample past the timeout subtracts the old process's total from the new
   one's. The result is negative, which reads as idle, and State 2 kills the
   process (`:18833-18849`) on that tick. A process that is silent but busy,
   the exact case the CPU check exists to spare, gets no confirmation tick.
2. **A later silent stretch in the same process** is compared with the sample
   left by the previous stretch, possibly hours old, so the rate is averaged
   over the whole gap instead of the last ten seconds.
3. Children that exited between two samples drop out of the total
   (`:18610-18615`), so a tool run made of short-lived processes can also
   read negative.

- **Fix:** store the pid with the baseline; treat the sample as a first sample
  when the pid differs or the baseline is older than a few ticks.
- **Tests:** would not catch it. `tests/test_guardian_claude_hung_timeout.py:37`
  and `tests/test_claude_retry_watchdog.py:328` both replace
  `_proc_is_cpu_idle` with a constant.

#### R56 · medium · L — chat search and the workflow progress scan skip the folder that worktree-isolated agents write to

`_search_project_transcripts` (`agent_routes.py:17228-17231`) and
`_scan_project_workflows` (`:16700-16703`) each build their own list of
transcript folders: the encoded project path and its underscore-to-dash
variant. The runtime's own lookup also covers
`<encoded>--clayrune-agents-*`, the folder a worktree-isolated agent's CLI
writes to (`mc/agent_runtime.py:2530-2551`), and the conversation list globs
those too (`tests/test_claude_runtime.py:489`, `:1689`). With
`worktree_isolation_enabled` on (it is on in the install this was read
against), an isolated agent's chat is listed in the rail and opens, but
`/search-chats` and `/api/search/global` never find it, and its Claude Code
workflow tree never appears on `/workflows`.

- **Fix:** one runtime method that returns the transcript folders for a
  project, used by the lookup, the list, the search and the workflow scan.
- **Tests:** the worktree folder is covered for lookup and listing only.

#### R57 · medium · E — `/agent/status` still ships every session's whole chat buffer, and the two rail endpoints rebuild everything per call

All figures **measured** with GET on a running install, three runs each.

| endpoint | size | time |
|---|---|---|
| `/agent/status` (4 sessions) | 105,765 bytes, of which 87,069 (82%) is `log_lines` and `log_line_ts` | 0.22-0.47 s |
| `/conversations?limit=10` | 33 KB | 0.69-0.78 s |
| `/agent/log` | 2,137,185 bytes | 0.32-0.35 s |

- `/agent/status` (`agent_routes.py:16280-16305`) copies and sends each
  session's full buffer on every poll; the stream endpoint already sends lines
  incrementally by cursor (`:14187-14374`). This is F1 of the 2026-09-10
  efficiency review, still open.
- `/conversations` parses the agent log three times in one request (`:17930`,
  then again inside the Codex row builder at `:17525` and the non-Claude row
  builder at `:17685`), lists transcripts twice (`:17926` and the lineage scan
  at `:17833`), and reads a persona file from disk per row (`:17393`).
- `/agent/log` (`:16494-16551`) returns all 500 rows with every field plus a
  lineage scan. The rail fetches it whole on each refresh
  (`static/js/agent-log.js:241`); the `?session_id=` filter (`:16512`) is used
  only by dispatch callbacks.

The two rail fetches are event-driven and coalesced in flight, not on a timer
(`static/js/agent-log.js:233-305`), so the cost is per turn completion.

- **Fix:** send `log_lines` from `/agent/status` only on request or after a
  `log_epoch` change; pass the already-parsed log into the two row builders;
  resolve each persona once per request; give the rail a slim agent-log view.
- **Tests:** none (performance).

#### Lesser findings

- **Unlocked iteration, more sites** (L, low; same defect as R41).
  `agent_sessions.items()` or `.values()` iterated with no snapshot at
  `:16280`, `:16973`, `:17046`, `:17060`, `:17536`, `:17898`, `:18195`,
  `:18355`, `:18511`. Each is a `RuntimeError: dictionary changed size during
  iteration` away from a 500 when a dispatch lands mid-request.
- **`delete_plans`, `:16133-16136`** (E, low). `changed = True` is set for any
  row that has a `plan_files` list, whether or not anything was removed from
  it. Deleting one plan therefore rewrites the agent log of every project that
  has ever recorded a plan. Fix: compare the filtered list with the original.
- **`agent_session_delete`, `:16013-16019`** (E/L, low). `_kill_pid(tree=True)`
  (a `taskkill` subprocess) and `_stop_session` (the stdin close of R53) run
  under the project lock. The comment at `:16031-16032` says the reader has
  already logged completion by the time the session is popped; the wait above
  it gives up after 5 s and only logs (`:16024-16028`).
- **`delete_conversation`, `:17042-17085`** (E/L, low). The project load, an
  agent-log parse, one SQLite revoke per alias and the rename all run under
  the project lock. The live check (`:17046`) matches any session row with
  that id, including a finished one still in memory, so deleting a chat that
  just ended answers 409 "conversation is live".
- **`api_recent_runs` `:17129-17134`, `api_usage` `:18471-18487`** (M/E, low).
  Both read every project's agent log with their own `json.loads`, bypassing
  `_load_agent_log`, and both drop a parse failure with a bare `continue`
  (project policy: a JSON state load must log). A corrupt log silently
  vanishes from the usage totals. Measured 0.08-0.14 s per call.
- **Silent swallows on file I/O** (M, low): `:17275` and `:17344` (search),
  `:18369` (`list_written_markdown`).
- **`/agent/status` comment and sorts** (L/M, low). The comment at
  `:16405-16408` says subagents are "computed without a scan" for any session
  that is not running; the gate at `:16220` scans idle sessions too (correct,
  per the note above it). Of the three sorts at `:16424-16430` the first has
  no effect on the result beyond tie order.
- **Guardian recovery** (L, low). `_guardian_attempt_recovery` changes
  `recovery_attempts` and `guardian_state` with no lock (`:18655-18669`) and
  is called from the guardian thread and from a thread the reset route starts
  (`:16464`). It also sleeps 2 s on the per-project guardian thread
  (`:18674`), during which no other session in the project is checked.
- **Non-Claude interrupt** (L, low; second site of R49). `:15639-15649` falls
  through to `{'ok': True}` after a generic exception, and the comment at
  `:15599-15601` describes a `runtime.interrupt()` call that is not there.
- **Dead stubs** (M, low): `_session_guardian_loop` (`:18690`) and
  `_start_session_guardian` (`:18964`).
- **Request bodies** (M, low; more of R15): `delete_plans` builds
  `Path(plan_path)` from an unchecked list item (`:16105`).

#### Sound in this stretch

- **`agent_stop`** (`:15461-15493`): status set under the lock, kill outside
  it. The opposite order is pass 1's MA1, in the runtime; this route does not
  repeat it.
- **Plan and document reads** (`:16045-16090`, `:18259-18304`): realpath
  containment, markdown only, secret-file denylist, drive-root projects
  excluded.
- **Plans cache** (`:18148-18161`): keyed on the log's mtime; every consumer
  only reads the cached list.
- **Spawner of record** (`:17410-17488`): one helper for display and revive,
  legacy field trusted only on a dispatch-marked row, two-node cycle rejected.
- **Conversation list union** (`:18086-18110`): live rows and requested ids
  survive the final cut; Codex and agent-log rows cannot double-list.
- **Search and incognito** (`:17249`, `:17340`): both direct disk readers
  re-apply the incognito exclusion.
- **Guardian state checks** (`:18763-18826`): every status write re-checks the
  process identity and the respawn window under the lock; eviction re-runs its
  predicate under the lock; the stuck-flag nudges fire once per episode.

---

## 6. `mc/agent_worktree.py`

Read in full (1-1077), with `_sync._dirty` (`mc/project_sync.py:291-293`) and
the landing call site `agent_routes.py:1784-1808`.

#### R59 · high · L — one stray untracked file in the shared data folder makes every agent worktree "dirty", so nothing lands and nothing is cleaned up

`mc/agent_worktree.py:577` (also `:516`, `:934`, `:1063`).

`merge_back` asks `_sync._dirty(wts)` and returns `'dirty'` before it looks at
a single commit. `_sync._dirty` is `git status --porcelain`, untracked files
included (`project_sync.py:291-293`). Every agent worktree links
`data/projects` to the main checkout's directory, so whatever is untracked and
not ignored in the MAIN tree's `data/projects` shows up in every worktree's
status. The module knows this: `dirty_outside_runtime` (`:1030-1042`) exists
for exactly that reason, and its docstring says "a plain `_sync._dirty` marks
every worker dirty". Only the Hivemind integration
(`hivemind_integration.py:230`, `hivemind_integration_cycle.py:181,186`) and
the display layer (`preserved_worktrees.py:445-459`) call it. The four gates
that decide what happens to an ordinary agent's work still use the plain one:

| line | gate | effect when the leak is present |
|---|---|---|
| `:577` | `merge_back` | returns `'dirty'`; nothing merged |
| `:516` | `has_unmerged_work` | `True`; `remove` refuses (`:844-845`) |
| `:934` | `_fingerprint` | the gc cache records "dirty" |
| `:1063` | `sync_into` | skipped |

**Measured**, in this review's own worktree, which has no uncommitted change of
its own: `git status --porcelain` prints one line, a 0-byte
`data/projects/.<project>_agent_log.json.<rand>.tmp<pid>`. That name is the
temp file `mc/atomic_json.py:149` creates beside its target; a process killed
between `mkstemp` and the rename leaves it, and `.gitignore:16-25` covers
`*.json`, `*_agent_log.json`, `*_agent_log.lock` and one `.jsonl` pattern, not
this. `git check-ignore` on it returns 1. So while that file exists, every
isolated agent on the install ends its turn with "Agent worktree needs manual
merge (dirty)" (`agent_routes.py:1799-1804`), and the gc keeps the worktree.
On this install the worktree root held 270 directories and the gc cache 669
entries, 661 of them flagged dirty. That count is a ceiling, not an
attribution: some of those were dirty for their own reasons.

Smallest fix: call `dirty_outside_runtime(project, wt)` at the four lines
above. Separately, have `atomic_json` sweep its own orphaned temp files, or
add `data/projects/.*.tmp*` to `.gitignore`; either removes this trigger but
not the class.

Test: none. `tests/test_hivemind_worktree_isolation.py` covers
`dirty_outside_runtime` on the Hivemind path; no test puts an untracked file
in the main tree's `data/projects` and then calls `merge_back`.

#### R60 · medium · L — the gc cache claims an unchanged worktree cannot change verdict, and it can

`mc/agent_worktree.py:924-926` (docstring), `:977-982`, `:1007-1012`.

The cache key is the worktree's own `[HEAD, dirty]`. The verdict also depends
on things outside the worktree: the base ref (the branch was merged by hand
since, so it is now removable), the main tree being dirty (`:604`, `'skipped'`)
or on another branch (`:607`), and a conflict that someone has since resolved.
All of those come back from `remove()` as a `refused:` message, and only
`deferred:` is excluded from caching (`:1007`). The startup pass runs with
`use_cache=True` (`server.py:1351`). Result: a worktree refused once for a
reason that has since gone away stays "preserved" until its own HEAD moves,
which for a finished session is never.

The cache is also never pruned: 669 entries against 270 directories
(**measured**, read-only).

Smallest fix: add the base ref's SHA to the fingerprint, and do not cache a
refusal whose reason is about the main tree. Drop entries whose directory is
gone at save time. Test: none found for a verdict that changes with the base.

#### R61 · medium · L — a repository whose default branch is not `master` strands every agent's work, silently

`mc/agent_worktree.py:528-532`, `:579-581`; `agent_routes.py:1787-1808`.

`_base_ref` falls back to the literal `'master'`. Nothing detects the
repository's real default branch; `worktree_base_branch` is a setting
(`project_safety_settings.py:22`) the user has to know to set. On a `main`
repository with no setting, `merge_back` returns `('skipped', 'unknown base
ref master')`. The call site handles `clean`, `nothing`, `conflict`, `dirty`
and the three review states; `'skipped'` falls through every branch, so there
is no activity line and no log line. `has_unmerged_work` then returns `True`
(`:519-521`), so the worktree is never removed either. The same silence
covers the "main tree dirty" and "main tree is on another branch" skips
(`:604`, `:607`). The module docstring (`:546-547`) names stranded work as the
failure the feature exists to prevent.

Smallest fix: an `else` at `agent_routes.py:1808` that logs the status and
detail to the activity feed; and resolve the base from
`git symbolic-ref refs/remotes/origin/HEAD` (falling back to the main tree's
current branch) before the literal. Test: none for `'skipped'` at the call
site.

#### Lesser findings

- **low · L** `:873-879` — the removal fallback runs
  `shutil.rmtree(ignore_errors=True)` and then returns `(True, 'removed')`
  whatever is left on disk; the branch is deleted regardless.
- **low · L** `:634-641` — any merge failure is labelled `conflict`, including
  a timeout and "untracked working tree file would be overwritten".
- **low · L** `:474-476` — `create()` returns an existing worktree without
  re-running `link_runtime` or `retarget_mcp`. A crash between `git worktree
  add` and the link step leaves a worktree with no runtime links for the rest
  of its life.
- **low · L** `:401-409` — `retarget_mcp` is a plain substring replace of the
  main path. A sibling directory that shares the prefix is rewritten too; a
  path that differs only in drive-letter case is not matched and the function
  still returns `True`.
- **low · M** `:906`, `:918` — `_load_gc_cache` and `_save_gc_cache` swallow
  JSON state-file errors with no log (project policy: log).

#### Sound in this file

`sever_links` never descends into a link and refuses when the walk cannot be
verified; `prepare_delete` precedes every delete; `archive_scratch` verifies
sizes and refuses on failure; `has_unmerged_work` fails closed on any
exception; the merge lands the reviewed SHA and the hold is re-checked inside
the landing environment (`:621-624`); every path built from a session id goes
through `_SAFE_ID`.
