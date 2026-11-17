# Agent plumbing outside the runtime core, pass 10 (MC-1179) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `90b9786f`.
Review only: no source file was changed. Every line cited was opened. A cost is
marked **measured** (a scratch script against the pure function, or a read-only
count) or **read, not measured**. `server` was never imported, the live server
was sent nothing but `GET`, no model call was made, and no process that binds a
port or launches a provider CLI was started. Nothing was written under `data/`
or to a real memory directory.

STATUS: COMPLETE. 65 findings: 0 blocker, 1 high, 15 medium, 49 low. 17 of
them are marked **latent**: they sit behind a flag that is off by default and
off on this install (`agent_survive_restart_enabled`,
`aider_restricted_adapter_enabled`) or in code that nothing constructs enabled.
Lens letters: E efficiency, L coherent logic, M methodology.

Written incrementally, one commit per section. Order: ranked summary, feature
table, backlog cross-check, streamlining, earlier audits, detail, coverage.

## Scope note

`mc/agent_runtime.py` was pass 1, `mc/blueprints/agent_routes.py` and
`mc/agent_worktree.py` were pass 2. The `mc/agent_mail_*.py` modules and the
`mc/blueprints/agent_mail_*_routes.py` modules are taken to be pass 05b's
(its report is not in this checkout); they are not read here.

## Ranked summary

Block the next release on rank 1. Ranks 2 to 5 are the ones a user or an agent
meets without doing anything unusual. Ranks 6 and 7 are measured waits of
12 and 27 seconds. Nothing below rank 16 should hold anything up.

| Rank | Id | Finding | Lens | Severity |
|---|---|---|---|---|
| 1 | AP1 | The process guard hook lets through seven ordinary spellings of an image-name kill and seven of a listener-PID kill (measured): the binding process-hygiene rule is enforced for the documented spellings only | L | high |
| 2 | AP37 | An "ambiguous" launch failure refuses every later turn of that conversation for good, nothing clears the marker, and three of four ordinary spawn refusals are classed ambiguous on Windows (measured) | L | medium |
| 3 | AP46 | The daily CLI updater checks "in use" once and then replaces the binary for up to 10 minutes with no launch interlock; a session started in that window fails in exactly the way AP37 makes permanent | L | medium |
| 4 | AP39 | Any tool result whose text contains the background-announcement phrase is recorded as a background job (measured, and seen live in this session's own rollover block) | L | medium |
| 5 | AP40 | After a mid-turn rollover a dispatched worker is told its only brief is "historical; may be long finished, not the current task" (seen live) | L | medium |
| 6 | AP60 | A resume whose transcript is missing repeats one search once per agent worktree: 12.5 s inside the dispatch request, against 0.05 s for the one lookup that already covered everything (measured) | E | medium |
| 7 | AP52 | "Import from Claude Code" reads every transcript directory before discarding agent worktrees: 26.7 s for 5,718 directories on a request thread (measured) | E | medium |
| 8 | AP5 | On Windows, an interpreter path with a space makes the guard hook a parse error under PowerShell, which Codex and Gemini read as "allow" (measured) | L | medium |
| 9 | AP43 | The vendor context mirror (`AGENTS.md` and its siblings) is written into an isolated worktree, which then reads as dirty in any project that does not ignore those names | L | medium |
| 10 | AP4 | The "never kill the Clayrune listener" half of the guard finds no listener on Linux or macOS, and none on a port set only in `config.json` (with AP33) | L | medium |
| 11 | AP6 | The split-brain guard recognises a live prior server only when its image starts with `python`; the packaged macOS app is not that, so a second launch reaps the first one's children | L | medium |
| 12 | AP50 | The macOS swap helper decides "the new app is up" through a route that makes two GitHub requests first; on a slow or offline network a good update is rolled back | L | medium |
| 13 | AP24 | With survive-restart switched on, nothing is ever adopted: the reader allowlist is empty by design, so the flag buys a shim and a checkpoint write every 1 to 3 s and no surviving session (latent) | L | medium |
| 14 | AP58 | The daily updater upgrades Aider; the restricted Aider adapter refuses any version but the pinned one. With both on, unattended Aider stops the first day a release ships (latent) | L | medium |
| 15 | AP16 | A draining older shim writes its manifest over the newer shim's on exit; reconnect and adoption then see dead PIDs (latent) | L | medium |
| 16 | AP20 | On macOS the shim log tail launches `ps` about 20 times a second per running agent while a turn is quiet (latent, read) | E | medium |
| 17 | AP34 | `snapshot()` is used as a read and rewrites the whole agent log (up to 2.1 MB here) every time; deleting a plan rewrites every project's log | E | low |
| 18 | AP38 | Each turn start parses the whole agent log three times under the project lock (about 14 ms plus a 1.6 MB rewrite here, measured) | E | low |
| 19 | AP57 | Half of Codex mid-turn usage ticks are recorded "unavailable" (2,674 against 2,635 complete, measured) because the tick must match the allowance sample's time exactly | L | low |
| 20 | AP47 | A failed `--version` is parsed as a version when its error text holds three dotted numbers: a hung CLI under an nvm path reads as installed and current (measured) | L | low |
| 21 | AP41 | A queued follow-up is popped before delivery; an exception in delivery loses it with nothing in the chat | L | low |
| 22 | AP33 | The spawn factory exports `CLAYRUNE_PORT`, the guard reads only `MC_PORT`, and `server.py` never sets it (second half of AP4) | L | low |
| 23 | AP2 | The guard matches the whole command text, so a search for, or a commit message quoting, a blocked spelling is refused (measured, and hit twice by this review) | L | low |
| 24 | AP55 | A failed Claude usage read is never cached: every caller pays the 6 s timeout again while the endpoint is down, including the dispatch pace check | E | low |
| 25 | AP44 | Every unattended launch re-proves the fence from scratch: one interpreter launch for Claude, a 2,000-module source walk plus four launches for Gemini and Qwen, per spawn | E | low |
| 26 | AP8 | The orphan sweep's dead-chain walk passes through a live shell, so an idle interactive CLI in a console whose launcher exited meets all five criteria | L | low |
| 27 | AP9 | Sweep criterion 1 is met by any process whose command line contains a CLI package directory | L | low |
| 28 | AP31 | A shim launch that misses its deadline with no manifest leaves a marker that blocks every later spawn until a file is deleted by hand (latent) | L | low |
| 29 | AP32 | A capture stream not closed in 10 s is settled as failed and the shim is still launched on the partial prompt (latent) | L | low |
| 30 | AP15 | The shim's lifetime loop can block for ever on the send lock held by a write to a hung CLI; the orphan timeout then never fires (latent) | L | low |
| 31 | AP28 | The completion retry skips a live session with an owed stamp until the session is evicted (latent) | L | low |
| 32 | AP23 | `ShimProc.kill` checks identity and then calls the unchecked tree kill; with no recorded create time Stop silently does nothing (latent) | L | low |
| 33 | AP21 | `send` after a second timeout keeps the socket and the sequence number; a late ACK then confirms the wrong payload (latent, not reachable from the server today) | L | low |
| 34 | AP17 | A oneshot shim is tree-killed 12 hours after a restart whether or not it was adopted (latent) | L | low |
| 35 | AP18 | The shim manifest is written from several threads through one temp name with no lock (latent; same pattern as pass 1 C1) | M | low |
| 36 | AP25 | Nothing ever deletes a shim session directory; `out.log` is append-only and every boot re-reads all of them (latent) | E | low |
| 37 | AP22 | `LogTail._cut` is quadratic on a long line: 0.78 s for one 8 MB line (measured, latent) | E | low |
| 38 | AP26 | Two per-launch maps in the adopter grow for the life of the process (latent) | E | low |
| 39 | AP30 | On POSIX a failed `exec` in the trampoline writes nothing; the server waits the whole ready timeout (latent) | L | low |
| 40 | AP36 | Every conversation-store call opens a connection and re-validates the schema (latent: the store is off) | E | low |
| 41 | AP35 | The provider-neutral conversation journal is constructed with `enabled=False` and about 2,100 lines run only under test (store half of pass 1 L1) | L/M | low |
| 42 | AP13 | `"timeout_minutes": "nan"` on the job route removes the job's time cap (measured arithmetic) | L | low |
| 43 | AP11 | The job table is never pruned and keeps every `Popen` handle for the life of the server | E/L | low |
| 44 | AP12 | `_tail` reads a whole job log to return its last 4 KB | E | low |
| 45 | AP14 | A non-string `shell` on the job route is a 500 | L | low |
| 46 | AP48 | A registered process whose command line contains a dot-directory named after a CLI blocks that CLI's update for as long as it lives | L | low |
| 47 | AP49 | The CLI-update POST runs the whole pass, up to minutes, on the request thread | E | low |
| 48 | AP51 | A crash during a macOS update download leaves a hidden staging directory of a few hundred megabytes that nothing sweeps | E | low |
| 49 | AP53 | The import scan offers every folder a CLI was ever started in: 188 candidates here (product note) | L | low |
| 50 | AP54 | One non-numeric counter in any router-stats file makes the aggregate route return 500 | M | low |
| 51 | AP56 | The Claude version probe inherits stdin; on a console-less server the model floor is silently not applied | M | low |
| 52 | AP61 | `recorded_cwd` JSON-decodes a whole transcript when the provider never records a directory | E | low |
| 53 | AP3 | The guard runs a `netstat` subprocess (53.5 ms, measured) on every shell tool call before it knows a PID kill is present | E | low |
| 54 | AP10 | The background-job liveness scan's 3 s budget is checked between processes only, on the stream-reader thread | E | low |
| 55 | AP7 | Three bare `except` on the child-PID ledger's read and write | M | low |
| 56 | AP27 | `_holds_work` reads a failed job-table lookup as "holds nothing"; its sibling fails closed and logs | M | low |
| 57 | AP19 | A spec without a usable `argv` kills the shim with no `exit.json` | M | low |
| 58 | AP29 | General workflow idle-run recovery lives in a shim-named module and would go with it | M | low |
| 59 | AP62 | The Windows PTY close tree-kills after terminating the root, so the tree kill can only be a no-op | L | low |
| 60 | AP63 | The third branch of the transform kill helper is unreachable | M | low |
| 61 | AP64 | `state.py`'s docstring states a rule the module no longer follows | M | low |
| 62 | AP65 | Every transform HTTP call builds a new session, so no connection is reused | E | low |
| 63 | AP59 | The audited Aider version is a literal in three files | M | low |
| 64 | AP42 | One `print` where the module's diagnostics should use the server log | M | low |
| 65 | AP45 | The CLI whose version decides fence `onFailure` support is taken to be `argv[0]` (question, not a defect) | L | low |

One open question that is not a finding: **Q1** (`vendor_fence_hook.py:24-30`),
whether Qwen's native edit tool is named `edit`, which the alias table does not
map. See N18.

## Feature table (lens 4)

Main path and most likely failure path traced in the code for each. "Latent"
means the feature is behind a flag that is off by default.

| # | Feature | Entry point | Verdict |
|---|---|---|---|
| 1 | Process-hygiene guard hook on all four vendors | `process_guard.py:80-113`, installed by `guardrail_hooks.py` | Works with a defect: AP1 (bypass spellings), AP5 (spaced interpreter path on Windows), AP4 and AP33 (listener half off Windows and on a config-only port) |
| 2 | Boot-time reap of the previous server's children | `process_ledger.py:147-205` | Works with a defect: AP6 (packaged macOS app) |
| 3 | Orphaned-CLI sweep (Windows) | `process_sweep.py:350-405`, `system_routes.py:347-409` | Works as documented. AP8 and AP9 are narrowings for the owner to accept or not; a no-op off Windows, as documented |
| 4 | Kill-on-close job for child trees (Windows) | `windows_process_job.py` | Works as documented |
| 5 | Long job route with a wake-up turn | `agent_routes.py:1956-2060`, `agent_jobs.py:186-289` | Works with a defect: AP13 (NaN removes the cap), AP11, AP14 |
| 6 | Background task and job tracking for the rollover block | `background_tasks.py`, `bg_job_liveness.py` | Works with a defect: AP39 (phantom jobs) |
| 7 | Mid-turn context rollover with a carried state block | `midturn_rollover.py:336-576` | Works with a defect: AP40 (wrong label on a worker's only brief), AP39. This review was rolled over several times by it and resumed each time |
| 8 | Send while a turn is running (steer or queue) | `mid_turn_send.py:73-171` | Works with a defect: AP41 |
| 9 | Turn clock stamp and gap briefing | `turn_clock.py` | Works as documented |
| 10 | Launch transaction and uncertain-launch protection | `launch_transaction.py:60-290` | Works with a defect: AP37 (the protection has no exit) |
| 11 | Isolated agent launches never run in the shared tree | `isolated_launch.py:142-159`, `agent_worktree_followup.py` | Works as documented |
| 12 | Spawn factory (plain `Popen` path, flag off) | `agent_spawn.py:379-389` | Works as documented |
| 13 | Agent runs survive a server restart (latent) | `agent_shim*.py`, `agent_spawn.py:398-473` | Cannot work as written: AP24. The shim launches and logs; no session is ever adopted, so nothing survives. Also AP15, AP16, AP20, AP31 |
| 14 | Fail-closed fence for unattended Claude launches | `fence_fail_closed.py:270-295`, `launch_marker.py:56-100` | Works as documented (cost: AP44) |
| 15 | Fence for unattended Codex, Gemini and Qwen | `vendor_fence.py:47-175`, `vendor_fence_hook.py` | Works as documented for Codex and Gemini; Qwen's edit tool name could not be determined (Q1: Qwen may not be launched by this pass) |
| 16 | Project context mirrored for non-Claude engines | `vendor_context_sync.py:60-101` | Works with a defect: AP43 |
| 17 | Daily update of the agent CLIs | `cli_update.py:330-420`, `system_routes.py:412-459` | Works with a defect: AP46, AP47, AP48. Live state: three CLIs current, one waiting on an in-use binary, two not installed |
| 18 | In-place update of the macOS app | `mac_update.py:500-568`, `system_routes.py:2560-2628` | Could not determine: no macOS host. Read: sound gates, one defect (AP50) |
| 19 | Import projects from Claude Code | `claude_import_routes.py:31`, `claude_projects_scan.py:150-213` | Works with a defect: AP52 (27 s here) |
| 20 | Router statistics aggregate | `agent_router_stats_routes.py` | Works with a defect: AP54 |
| 21 | Model floor by Claude CLI version | `claude_cli_min_version.py` | Works as documented (AP56 on a console-less server) |
| 22 | Claude usage limits source | `claude_usage_source.py:78-139` | Works with a defect: AP55 |
| 23 | Codex live usage and mid-turn usage ticks | `codex_live_usage.py`, `codex_usage_checkpoint.py` | Works with a defect: AP57 (half the ticks unavailable) |
| 24 | Codex run errors shown in the chat | `codex_run_error.py` | Works as documented |
| 25 | Restricted Aider adapter (latent) | `aider_launch.py`, `aider_runner.py`, `aider_guard.py` | Could not determine live: Aider is not installed here. Read: fails closed correctly; one defect (AP58) |
| 26 | Allow-once pass and replay | `attend_once_pass.py`, `attend_once_replay.py` | Works as documented |
| 27 | Caller attribution (who is calling a route) | `caller_attribution.py` | Works as documented |
| 28 | Resume in the right directory, with the right context | `resume_cwd.py`, `resume_context.py`, `resume_target.py` | Works with a defect: AP60 (12.5 s on a miss) |
| 29 | Deadlines and sandbox for tool-free transforms | `transform_*.py` | Works as documented |
| 30 | Terminal pop-out PTY | `pty_backend.py` | Works as documented (AP62 is a dead step, not a failure) |
| 31 | Agent log transaction and snapshot | `agent_log_file.py` | Works with a defect: AP34 (cost) |
| 32 | Provider-neutral conversation journal | `conversation_store.py`, `execution_lifecycle.py` | Cannot work as written: AP35. Constructed disabled; no user-facing effect either way |

Totals: 13 work as documented, 15 work with a defect, 2 cannot work as written
(both off by default), 2 could not be determined.

## Backlog cross-check

Read 2026-11-16 by `GET`: 95 items are open, in_progress or blocked. Eleven
have their subject in this region. Items whose subject is another region's
file were left to that pass even where they touch a module here (MC-1178
rollover threshold, MC-1146 continuity block, MC-1109 keep-awake, MC-1016
restart child, MC-1014 nvm path, MC-1030 install gate). No item was patched
or noted.

| Key | Status | Subject | Class | Evidence |
|---|---|---|---|---|
| MC-1160 | in_progress | Restart blockers list open jobs | DONE | `system_routes.py:1736-1746` emits a `job_running` row per open job. Left: the owner's look at the Restart dialog |
| MC-1161 | in_progress | Adoption refuses sessions owning jobs | DONE | `agent_shim_adopt.py:812-820`, `:834` (fails closed, logs); dialog text at `static/js/update-power.js:280`; commit `663a305d`, merge `7b9fdbff` is an ancestor. The adoption half cannot be UI-tested while AP24 holds; the dialog text can. AP27 is a loose end in the same function family |
| MC-1102 | in_progress | Qwen standalone install updatable | DONE | `cli_install.py:92-93`, `:124-125`; `cli_update.py:218-222`, `:260-261`. Live update state: Qwen installed equals latest, status `up_to_date`, two minor versions past the one the item names. Nothing left to build |
| MC-1017 | blocked | Stop must not kill server | DONE in code, NEEDS A HUMAN | `mc/proc_kill.py:1-22`; the shim keeps the same own-group rule without importing it (`agent_shim.py:259-266`, `:348`, `:362`) and spawns the CLI as a session leader (`:569`). Left: the Linux machine test the item names |
| MC-1150 | in_progress | Agent runs survive server restart | PARTLY DONE | P1 to P4 are on master (the item's own state line). What it calls "pending a live proof and the decision to turn it on" cannot pass as written: `agent_shim_adopt.py:69` is an empty allowlist, so `assess` refuses every session (`:506-509`) and nothing is adopted (AP24). Smallest next step: make one reader path restorable, or record that the flag keeps nothing alive yet. Before any live proof also fix AP16 and AP31 |
| MC-1149 | in_progress | Rollover must not kill jobs | PARTLY DONE | Phase 1 is on master (`b68e951c` is an ancestor; `midturn_rollover.py:415`). It has a defect the item does not know about: AP39 lists jobs that never existed and tells the agent to check their output files; AP40 sits in the same block. Phase 2 stays conditional. This review started no long job outside the job route, so its rollovers are no evidence either way on the original loss |
| MC-1136 | in_progress | Restricted Aider adapter, default off | DONE in code, one new defect | Adapter and pin are on master (`aider_source_pin.py:11-51`, `vendor_fence.py:56-78`). Before the owner turns it on: AP58, the daily updater will move Aider off the pinned version. Left: the owner's test |
| MC-998 | in_progress | Codex usage sampling calibration | PARTLY DONE | The item records "118 complete, monotonic" ticks. Over the whole store the count is 2,635 complete against 2,674 unavailable (measured, AP57): complete ticks are correct, half are missing. Smallest fix is in AP57 |
| MC-975 | blocked | Codex unattended sandbox on Windows | NEEDS A HUMAN, reopen condition met | The item is parked "until codex-cli upgrades" from the version that crashed. The live update state shows Codex nine minor versions newer. The elevated probe the item names can be rerun; only the owner can run it |
| MC-1004 | blocked | Per-action allow, block, approve rules | NEEDS A HUMAN | Nothing built: no rules store in `mc/` or `steward/` (grep). The reusable piece exists and is sound (`attend_once_pass.py`, `attend_once_replay.py`). Waits on product decisions |
| MC-1174 | open | Stop check for multi-unit plans | NOT DONE | No stop check exists (grep for the terms finds one unrelated docstring). Not the same defect as any finding here |

Classed DONE: MC-1160, MC-1161, MC-1102, and MC-1017 and MC-1136 in code
(each still carries a human test). None classed OBSOLETE or DUPLICATE.

## Streamlining

Callers were checked by grep over `mc/`, `server.py`, `app.py`, `tools/`,
`steward/` and `tests/`, not guessed.

| # | What | Where | Evidence | Lines |
|---|---|---|---|---|
| ST1 | The conversation journal is built disabled | `conversation_store.py` (1,478), `conversation_contract.py` (102), `conversation_views.py` (107), `execution_lifecycle.py` (421) | The only constructor call passes `enabled=False` (`server.py:1070-1072`); every service entry returns first when disabled; `conversation_views` has no importer outside tests; the legacy attempt API (`:523-655`), the five migrations (`:346-482`) and the capture pipeline (`:1209-1380`) have no caller even with the service on (AP35) | about 2,100 if parked on a branch; about 550 (legacy API, migrations, capture pipeline, views) with no decision needed |
| ST2 | Adoption code that only tests run | `agent_shim_adopt.py:640-803` | `RESTORABLE_READER_PATHS` is empty and only tests add to it (AP24) | 165, if the reader path is not going to be finished |
| ST3 | Unreachable third branch of the transform kill | `transform_process_deadline.py:36-52` | Both callers are in the file and always pass `job` or `pgid` (AP63) | 17 |
| ST4 | Dead tree-kill step on PTY close | `pty_backend.py:226-240` | The root is already terminated (AP62); reorder or delete | 14 |
| ST5 | Three readers of the Claude `--version` text | `cli_update.py:151`, `claude_cli_min_version.py:74-91`, `fence_fail_closed.py:96`, `:136` | Three regexes, three subprocess wrappers, three caches; AP47 and AP56 are each a bug one copy has and another does not | about 40 |
| ST6 | Two `git` runners | `turn_clock.py:249-254`, `midturn_rollover.py:394-398` | Same job; one has the no-stdin, no-window flags, the other does not | about 10 |
| ST7 | Candidate loop that can only repeat a miss | `resume_cwd.py:125-138` | The runtime lookup already covers every worktree transcript directory (AP60) | about 10, and 12 s |
| ST8 | Two answers to "does this session own a job" | `agent_shim_adopt.py:558-559`, `:812-820` | AP27 | about 8 |
| ST9 | The audited Aider version as three literals | `aider_launch.py:44`, `aider_runner.py:56`, `aider_source_pin.py:11` | AP59 | 2 |
| ST10 | Incident narrative carried in docstrings | `guardrail_hooks.py` (about 330 of 493 lines) | The same measurements are in the evidence doc the docstrings cite | about 300 comment lines |

Flags that are one value everywhere: `agent_survive_restart_enabled` (default
False, absent from this install's config) gates about 3,100 lines of shim
family; `aider_restricted_adapter_enabled` (default off) gates about 460; the
lifecycle service's `enabled` is a literal False. None is a deletion candidate
without a product decision; they are listed so the carrying cost is visible.

Total with no decision needed: about 650 code lines plus about 300 comment
lines. With the two decisions (park the journal, drop the unfinished adoption
path): about 2,350 code lines.

## Earlier audits: what is still open in this scope

- `docs/_review/2026-09-10_efficiency.md` **E3** (the agent log has no cache;
  several parse-and-write cycles per dispatch) is still open. AP34 and AP38
  are the same cost seen from `agent_log_file.py` and `launch_transaction.py`,
  with this pass's measurements.
- `docs/_review/2026-09-10_security.md` section 0 (six different "is this
  unattended" detectors): the modules in this region all read one set,
  `launch_marker.UNATTENDED_TRIGGER_TYPES` (`vendor_fence.py:42-43`,
  `fence_fail_closed.py:75-77`, `launch_marker.py:72`, `:97`). Nothing to add.
  That review's F6 is in `steward/fence.py`, not in this region.
- `docs/ENTANGLEMENT_AUDIT.md` names `mc/state.py` only as an identity risk
  for a route split; AP64 is the docstring side of that. No other file in this
  region is listed there.
- Earlier 2026-11-16 passes, other halves named in the detail: pass 1 **S2**
  (AP6), **C1** (AP18), **L1** (AP35); pass 2 **R59** (AP43). The reports for
  passes 05b, 06a, 06b, 09 and B14 are not in this checkout and were not read.

## Detail (working notes, in the order the files were read)

### N1. `mc/process_guard.py` (113 lines, read fully)

- **AP1 (L, medium) `process_guard.py:20-31`** — the guard misses ordinary
  spellings of the two things it exists to stop. Measured with
  `guard_command()` in a scratch script (listener pid 4242). Image-name kills
  ALLOWED: the `/FI "IMAGENAME eq x"` filter form of the Windows kill tool,
  `Stop-Process -ProcessName x` (an alias of `-Name`), `spps -Name x`,
  `Get-Process x | kill`, `gps x | spps`, `(Get-Process x).Kill()`, the
  CIM `Terminate` form. Listener-PID kills ALLOWED: `kill -TERM 4242`,
  `kill 111 4242` and `kill -9 111 4242` (`:30` captures only the first
  number), a second `/PID` on one command (`:28` needs the tool name before
  every match, so `finditer` sees one), `Stop-Process 4242` (positional),
  `Stop-Process -Id 111,4242`, and any indirect form (`kill $(lsof -ti :port)`).
  Fix: for PID patterns, find the command word and then scan every integer
  in that command segment; add `-ProcessName`, `/FI ... IMAGENAME`, and the
  `kill`/`spps` aliases on the right of a `Get-Process`/`gps` pipe.
  `tests/test_process_guard.py` holds none of these spellings (grep), so no
  existing test catches it.
- **AP2 (L, low) `process_guard.py:21-25`** — the patterns run over the whole
  command text, so a command that only MENTIONS a blocked spelling is refused:
  `grep -rn "pkill" docs/` and a `git commit -m` whose message quotes the
  rule are both blocked (measured). Live evidence: this review's own heredoc
  writes (a scratch probe, then these notes) were refused by the hook twice.
  Fix: match only at a command position (start, or after `;`, `&&`, `|`, `(`).
- **AP3 (E, low) `process_guard.py:100`** — `clayrune_listener_pids()` (a
  `netstat -ano` subprocess) runs on EVERY shell tool call, before
  `guard_command` knows whether the command contains a PID kill at all.
  Measured: 53.5 ms per call on this box, on top of interpreter start, per
  Bash/PowerShell call, per agent, on four vendors. Fix: pass a callable and
  call it only when a `_PID_PATTERNS` match exists.
- **AP4 (L, medium) `process_guard.py:56-77`** — listener detection is
  Windows-only in effect. `:72` needs the literal `LISTENING` and `:76` takes
  the last column as the PID; Linux and macOS `netstat` print `LISTEN` and no
  PID column with these flags, so the set is always empty there and the
  "never kill the Clayrune listener" half of the guard cannot fire on two of
  three platforms (read, not measured: no such host here). Also `:57-60` knows
  port 5199 and `MC_PORT` only; a port set in `config.json` alone
  (`server.py:683`) is invisible to the hook unless the launch exports it
  (checked under agent_spawn). Fix: `psutil.net_connections` or `lsof -ti`
  off Windows; export the bound port into every agent env.

### N2. `mc/guardrail_hooks.py` (493 lines, read fully)

- **AP5 (L, medium) `guardrail_hooks.py:252-256`** — `guard_shell_command`
  quotes a token that contains a space and emits `"<py>" <script> ; exit
  $LASTEXITCODE`. In PowerShell a leading quoted string is an expression, so
  the line is a parse error. Measured (scratch, real `powershell.exe
  -NoProfile -Command`): quoted interpreter -> exit 1 with a parser error;
  the same line with a leading `. ` -> exit 2, guard ran. Codex and Gemini
  run hooks through PowerShell on Windows and map anything but 2 to "run the
  tool" (`:124-130`, `:368-370`), so on an install whose interpreter path has
  a space (a Windows user name with a space, or Python under `Program Files`)
  the process guard is a silent no-op on those vendors. The file already
  holds the fix for the fence command (`:437-440`, the `. ` prefix) and did
  not apply it here; the docstring calls the case "a disclosed, untested gap"
  (`:197-198`, `:228-229`). A space in the SCRIPT path only is fine (measured:
  exit 2). Fix: same `. ` prefix when `tokens[0]` is quoted and `os.name ==
  'nt'`. No test covers a spaced interpreter through a real shell.
- Sound: one builder for the command string (`:178`), codex inline injection
  without a file (`:271`), self-test judged on exit 2 plus a token (`:492`).
- Note (not a finding): 493 lines, of which about 330 are incident narrative
  in docstrings. The measurements belong in `docs/GUARDRAIL_PARITY_EVIDENCE.md`
  (which they already cite); the code is about 110 lines.

### N3. `mc/process_ledger.py` (205 lines, read fully)

- **AP6 (L, medium) `process_ledger.py:174-179`** — the split-brain guard
  only recognises a live prior server whose image name starts with `python`.
  The packaged macOS app runs the server as a thread of the app process
  (`app.py:541`, executable name `Clayrune`, `installer/build-macos.spec:213`),
  so there the guard never holds: a second launch beside a live one reads the
  first one's ledger and tree-kills every child whose image and creation time
  still match (`:193-197`). This is the process half of pass 1 **S2**
  (`app.py` boots with `check_port=False` and no bind first). Fix: compare
  against the ledger writer's own recorded image (store `mc_image` and
  `mc_create_time` beside `mc_pid` at `:122-123`) instead of a name prefix.
  Read, not measured. `tests/test_process_ledger.py` covers the identity
  match (`:138-140`) and has no case with a non-`python` server image, so it
  cannot catch this.
- **AP7 (M, low) `process_ledger.py:124-125`, `:161-162`, `:204-205`** —
  three bare `except Exception: pass/return` on a state-file write and read.
  Project policy says these log. A ledger that silently stops being written
  is exactly the case where the next boot reaps nothing and nobody knows why.
- Sound: `_should_reap_entry` (`:128-144`) fails toward not killing on any
  missing identity; kill is by PID tree with image + creation-time match.

### N4. `mc/process_sweep.py` (405 lines, read fully), call site `system_routes.py:347-409`

- This module kills PIDs Clayrune did not start. That is an approved exception
  (standing position cited at `:8`), all five criteria are implemented as the
  docstring says, enumeration failure fails closed at both samples
  (`:366`, `:385`), kill is by PID tree only (`:400`). Not a hygiene finding.
- **AP8 (L, low) `process_sweep.py:235-260`** — `chain_is_dead` walks THROUGH a
  live shell. A CLI whose parent is a live `cmd.exe`/`powershell.exe`/`bash`
  whose own launcher has exited reads as "chain dead" (`:249-251` after the
  wrapper step at `:255-257`). A person's interactive CLI session left open
  for 24 hours in a console that was started by a script or shortcut that
  exited, idle at its prompt for the 2 s CPU sample, meets all five criteria
  and is killed. The CPU criterion (`:327-345`, 0.05 s over 2 s) does not
  separate "abandoned" from "waiting for the user". Whether that is inside
  what the owner approved is his call; smallest narrowing: treat a live
  wrapper that owns a console window (or any live wrapper that is not
  `node`/`conhost`) as a live owner.
- **AP9 (L, low) `process_sweep.py:140-149`, `:223-232`** — criterion 1 is met
  by any process whose command line merely contains a CLI package directory
  (an editor or pager opened on a file inside it). With a dead chain and 24 h
  age it becomes a kill candidate. Narrow: require the dir to be the script
  argument of a `node` image, not any substring position.
- Non-Windows: `list_processes` returns `[]` (`:95-96`), so the sweep is a
  no-op on macOS and Linux and reports `ok`. Documented at `:79-80`.

### N5. `mc/windows_process_job.py` (223 lines, read fully)

- Sound. Handle types declared, kill-on-close set before assignment, the
  suspended-root resume fails closed on anything but one thread. No finding.
  (`set_kill_on_close` re-declares three ctypes structures per call, `:32-53`;
  cosmetic.)

### N6. `mc/bg_job_liveness.py` (202 lines, read fully)

- **AP10 (E, low) `bg_job_liveness.py:110-125`** — the 3 s budget is checked
  between descendants only; one `open_files()` call is not bounded, and the
  scan runs on the stream-reader thread for each background tool_result
  (caller in midturn_rollover, checked there). Read, not measured.
- Sound: holders carry pid + create_time so a recycled pid cannot read as
  alive (`:141-143`); every public function catches its own errors.

### N7. `mc/background_tasks.py` (254 lines, read fully)

- Sound. Pure functions over session keys; the CLI's own list is authoritative
  (`:118-137`), timeout-moved shell calls are told apart by the tool_use input
  (`:89-94`). No finding. (`drain` `:209-218` leaves `INTERIM_KEY`,
  `LAST_END_AUTO_KEY` and `TOOL_USES_KEY` set; whether a stale `INTERIM_KEY`
  matters is decided in `agent_routes.py`, pass 2's file. Not followed.)

### N8. `mc/agent_jobs.py` (321 lines, read fully), route `agent_routes.py:1956-2060`

- **AP11 (E/L, low) `agent_jobs.py:41`, `:251-252`, `:283-289`** — `_jobs`
  is never pruned. Every job ever started keeps its record AND its live
  `Popen` object (`'proc'`, `:245`) for the life of the server; on Windows
  that is one open process handle per finished job. `open_job_count`,
  `describe_open_jobs` and `list_open_jobs` (`:146-179`) scan all of them
  under the lock. Fix: drop `proc` when the watcher finishes and prune
  finished jobs older than a day.
- **AP12 (E, low) `agent_jobs.py:49-54`** — `_tail` reads the whole log file
  to return the last 4 KB. A full test-suite log is megabytes. Fix: seek.
- **AP13 (L, low) `agent_routes.py:2032-2040` + `agent_jobs.py:259-262`** —
  `"timeout_minutes": "nan"` passes the route's `<= 0` check, survives
  `min(nan, cap)` (measured in Python: `min(float('nan'), 120.0)` is `nan`),
  and in the watcher `max(0.0, nan*60)` is `0.0`, which `:262` turns into "no
  timeout". An agent-supplied NaN removes the cap. Fix: `math.isfinite`.
  (The route line is pass 2's file; the consequence is here.)
- **AP14 (L, low) `agent_jobs.py:99`** — a non-string `shell` (a number, a
  list) raises `AttributeError` on `.strip()`, which the route does not catch
  (`agent_routes.py:2050` catches `ShellResolutionError` only): 500.
- Known and documented, not a new finding: jobs live only in memory
  (`:170-172`); the restart endpoints block on open jobs
  (`system_routes.py:1728-1745`), a crash or forced restart loses the wake.
- Sound: argv invocation with `pipefail` (`:136`), WSL launcher rejected
  (`:106-109`), stdin is DEVNULL, tree kill is supplied by the caller, the
  capture hooks cannot change a job (`test_run_capture.py:197-216`).

### N9. `mc/agent_shim.py` (936 lines, read fully)

The whole shim family (N9 to N12, about 3,100 lines) is behind
`agent_survive_restart_enabled`, default False (`agent_shim_client.py:45`) and
absent from this install's config (checked, read-only). Every finding in
N9 to N12 is therefore LATENT here.

- **AP15 (L, low, latent) `agent_shim.py:602-611`, `:777-797`, `:914-917`** —
  the lifetime loop can block for ever. `_do_send` holds `_send_lock` across
  `cli.stdin.write` (`:796`); if the CLI is hung and the payload exceeds the
  pipe buffer the write never returns. Once the owner is gone the loop reaches
  `A_CLOSE_STDIN` (a hung CLI is idle and silent, so the idle test passes),
  calls `_close_stdin()` on the main thread (`:917`), and that waits on
  `_send_lock` (`:603`). The loop never ticks again, so `orphan_max` (`:458`)
  never kills the hung CLI: shim and CLI live until the machine reboots. This
  is the 2026-09-23 stdin deadlock (`agent_routes.py:15302-15310`) moved into
  the shim, without the watchdog that fixed it there. Fix: `acquire(timeout=)`
  in `_close_stdin`, or run the close off the main loop. Read, not measured
  (the interactive path needs a listening socket, which this pass may not open).
- **AP16 (L, medium, latent) `agent_shim.py:920`** — the final
  `_write_manifest()` is unconditional. `write_spec` (`agent_shim_client.py:
  214-218`) deletes `shim.json` for a relaunch in the SAME directory, and the
  new shim writes its own; an older shim for that directory that is still
  draining (waiting for its owner connection to close, up to `exit_grace_s`,
  60 minutes by default) then writes its OLD manifest over the new one when it
  finishes. In-memory the new run is safe (`ShimProc` keeps the manifest it
  was given, `agent_shim_client.py:608-611`), but everything that re-reads
  the directory is now looking at dead pids: `ShimClient.connect` on a
  reconnect (`:317-319` -> "shim not alive") and adoption after a restart
  (`inspect`, `:253-265`). The live CLI is then invisible to the next server.
  Fix: before the final write, re-read `spec.json` and skip if its
  `launch_id` is no longer this shim's. Read, not measured.
- **AP17 (L, low, latent) `agent_shim.py:455-459`, `:874-876`, `:894`** — a
  oneshot shim has no socket, so after a server restart nothing can ever
  re-attach: its only owner is `spec.owner_pid`, the dead server. It stays
  "orphaned" from the restart on and is tree-killed at `orphan_max` (12 h)
  whether or not the new server adopted it and is reading its log. Only a
  turn that runs 12 hours past a restart is hit.
- **AP18 (M, low, latent) `agent_shim.py:389-407`** — one tmp name per process
  per target (`.shim.json.tmp<pid>`), written from the serve threads
  (`_claim_owner` -> `:662`) and the main thread with no lock around the
  write. Same pattern as pass 1 **C1**; two owner claims at once can tear or
  lose the manifest. The manifest dict is built under `_lock`, the file write
  is not.
- **AP19 (M, low) `agent_shim.py:877-884`** — `run()` catches `OSError` from
  `_spawn_cli`, but `spec['argv']` missing or not a list raises
  `KeyError`/`TypeError` there (`:562`) and the shim dies with a traceback to
  DEVNULL and no `exit.json`; the server waits out the 20 s ready timeout and
  then cannot tell "slow" from "dead" (`agent_shim_client.py:797-800`).
- Sound: identity is (pid, create time) from one reader with per-platform
  tolerance (`:159-188`); kill-if-ours holds the handle / pidfd across the
  kill (`:273-368`); no remote kill command; token compared in constant time
  (`:708`); sequence numbers re-checked under the write lock (`:777-814`).

### N10. `mc/agent_shim_client.py` (816 lines, read fully)

- **AP20 (E, medium, latent) `agent_shim_client.py:639-656` with
  `agent_shim.py:130-141`** — on macOS `proc_create_time` is a `ps`
  subprocess. `ShimProc.poll()` calls `process_state(self.pid, ...)` whenever
  there is no `exit.json` (`:652`), and `LogTail.__next__` calls
  `self._finished()` -> `poll()` on every idle poll, every 50 ms (`:525`,
  `:537`). That is about 20 `ps` launches per second per running agent for
  the whole of every quiet stretch of a turn. Read, not measured (no macOS
  host; the design doc itself says this reader is unmeasured on a Mac).
  Fix: sample liveness at most once a second in `poll()`; `exit.json` is the
  fast path and needs no process probe.
- **AP21 (L, low, latent) `agent_shim_client.py:381-405`** — `send` is not
  idempotent after a TIMEOUT, only after a dropped connection. If the second
  attempt times out (`:386-388`) the method raises but leaves `self._sock`
  connected and `self._seq` un-advanced. If the shim's blocked write later
  completes it sends `ACK seq` into that socket. The next `send` reuses the
  same `seq` for a NEW payload, reads the stale `ACK`, and reports success;
  the shim sees `seq <= last_seq` and drops the new payload as a duplicate
  (`agent_shim.py:786-788`). Not reachable from the server today: a failed
  write marks the session errored and the proc is replaced
  (`agent_routes.py:15354-15363`). Fix: `self._drop()` before raising at `:388`.
- **AP22 (E, low, latent) `agent_shim_client.py:473-492`, `:539-543`** —
  `LogTail._cut` walks the buffer byte by byte in Python and re-slices the
  remainder per line; a partial line is re-scanned from byte 0 after every
  read. Measured: 2.5 MB/s on 100-byte lines, 0.78 s for one 8 MB line
  (0.07 s for 2 MB, so quadratic). `_read_more` re-opens the file every poll.
  Fix: `buf.find(b'\n', scan_from)` and keep the handle open.
- **AP23 (L, low, latent) `agent_shim_client.py:668-671`** — `ShimProc.kill`
  checks identity, then calls the unchecked `kill_tree`. The module's own
  `kill_tree_if_ours` (`agent_shim.py:371`) exists to close that window. And
  when the manifest has no `cli_ct` (the shim could not read it,
  `agent_shim.py:576`), `_cli_alive()` is always False and `kill()` silently
  does nothing: Stop has no effect on that run.
- Sound: spawn refuses rather than work around a job that forbids breakaway
  (`:144-149`); `launch` never reports "nothing running" unless it verified it
  (`:784-800`, `cli_running`); spec carries env NAMES only (`:201`).

### N11. `mc/agent_shim_adopt.py` (937 lines, read fully)

- **AP24 (L, medium, latent) `agent_shim_adopt.py:69`, `:506-509`, `:848-849`** —
  `RESTORABLE_READER_PATHS` is an empty frozenset and nothing in `mc/` adds to
  it (grep: only tests monkeypatch it, `tests/test_agent_shim_adopt.py:47`,
  and `tests/test_agent_shim_adopt_blockers.py:340` pins it empty). `assess`
  therefore refuses every directory before it looks at a process, and
  `is_adoptable_session` is always False. Consequences, flag ON: no session
  is ever adopted, spared or moved out of the restart blockers; `_attach`,
  `start_all`, `start_one`, `_start_reader`, `_register_adopted_cli`
  (`:640-803`, about 165 lines) and the adoptable half of `partition_blockers`
  run only under test. The docstring says so (`:57-68`, a recorded decision),
  so this is not a bug in the module; it is the answer to "does survive-restart
  work": turning the flag on buys the shim, a trampoline, a checkpoint write
  per live session every 1 to 3 s (`:371-387`, a JSON round trip of the whole
  session dict plus 2,000 log lines), and no survivable session. Fix: none
  here; either finish one reader path or state in the settings text that the
  flag does not yet keep anything alive.
- **AP25 (E, low, latent) `agent_shim_adopt.py:622-623`,
  `agent_shim_completion.py:124-125`, `agent_spawn.py:417`** — nothing ever
  deletes a session directory under `agent_shims` (grep over `mc/`: the only
  removals are `shim.json`/`exit.json` on relaunch, `agent_shim_client.py:216`,
  and `prompt.txt`, `agent_shim.py:593`). `out.log` is append-only across
  turns, so with the flag on each session keeps its whole raw output for
  ever, and every boot reads `spec.json` + `shim.json` + `checkpoint.json`
  (`claim_all`) and `checkpoint.json` again (`replay_pending`) for every
  session that ever ran. Fix: prune directories whose shim and CLI are gone
  and whose checkpoint has no pending stamp, older than N days.
- **AP26 (E, low, latent) `agent_shim_adopt.py:268-269`, `:339-344`** —
  `_WRITE_LOCKS` and `_LAST_OFFSET` gain one entry per (session, launch) and
  are kept "for the life of the process" by design; a per-turn engine makes a
  new launch id every turn. Small entries; unbounded count.
- **AP27 (M, low) `agent_shim_adopt.py:558-559`** — `_holds_work` swallows a
  failed job-table read as "holds nothing" while its sibling `_owns_open_job`
  (`:812-820`) fails closed and logs. Two answers to one question.
- Sound: `write_checkpoint` takes the reader boundary, then one lock over
  snapshot and replace, and drops a snapshot behind what is on disk
  (`:289-321`); `protected_pids` re-verifies identity when the reaper asks
  (`:586-591`); the ledger entry keeps the manifest's create time, never a
  fresh sample (`:746-770`).

### N12. `mc/agent_shim_completion.py` (196), `agent_shim_completion_retry.py` (107), `agent_shim_tramp.py` (106): read fully

- **AP28 (L, low, latent) `agent_shim_completion_retry.py:77-79` with
  `agent_shim_completion.py:78-81`** — the retry tick skips a session that is
  live in this process and carries a stamp, on the grounds that "its own
  thread acks it". When the ack was called with `owed=True`
  (`agent_routes.py:10171`, `:10206`) that thread has finished and did NOT
  ack, and the stamp stays on the live dict. The retry then skips it on every
  tick until the session is evicted from `agent_sessions`; only then does the
  disk check run. The module's stated purpose (do not wait for the next boot)
  is not met for that case. Fix: skip only while the session's status is
  still `running`. Read, not measured.
- **AP29 (M, low) `agent_shim_completion_retry.py:97-104`** — the idle-run
  scan is a general workflow recovery (advance a `running` run that nothing
  is driving) and is not gated on the survive-restart flag, yet it lives in a
  module named for the shim and is reached only through
  `scheduler_routes.py:906`. Anyone who removes or disables the shim family
  removes workflow recovery with it. Fix: move the scan beside
  `workflows.advance_idle_run`.
- **AP30 (L, low, latent) `agent_shim_tramp.py:62-76`** — on POSIX the parent
  ignores the first child's exit status (`:65`) and a failed `execv` in the
  grandchild exits 127 with nothing written; `_record_error` (`:48-53`) only
  covers the Windows spawn and the first `fork`. The server then waits out
  the whole ready timeout to learn what the trampoline knew at once.
- Sound: replay is serialized by one lock and clears a stamp only when the
  runner returned True (`agent_shim_completion.py:141-146`, `:183-190`);
  `begin` is a two-attribute check and returns for a plain Popen session, so
  the default install pays nothing per turn (`:62-63`).

### N13. `mc/agent_spawn.py` (473), `agent_spawn_interactive.py` (172), `agent_uncertain_launch.py` (119): read fully

- **AP31 (L, low, latent) `agent_uncertain_launch.py:64-78`, `:99-110`,
  `agent_spawn.py:384-387`** — when a shim launch misses its deadline and no
  manifest for that launch ever lands (the shim died before its first write),
  `_attempt` can never confirm anything, the marker stays, and every later
  spawn for that session raises, with the flag on OR off. The only way out is
  deleting `uncertain.json` by hand; the error text says so, and no route or
  UI surface names the file (grep over `mc/` and `static/`: the marker is
  read only here). One missed deadline ends a chat until someone opens a
  file manager. Fix: when the launch's `spec.json` is older than the shim's
  own `orphan_max`, treat the launch as gone and clear the marker.
- **AP32 (L, low, latent) `agent_spawn.py:343-347`** — if the delivery code
  has not closed the capture stream after 10 s, the factory logs "sending
  what was written", settles the capture as FAILED, and still launches the
  shim with the partial payload (`:414-426`). The CLI runs a turn on a
  truncated prompt while the caller is told delivery failed. Fix: raise
  instead of launching.
- **AP33 (L, low) `process_guard.py:57-60` with `agent_spawn.py:109-111`** —
  second half of AP4, confirmed: the spawn factory exports the bound port to
  every agent as `CLAYRUNE_PORT`, the guard reads only `MC_PORT`, and
  `server.py` never sets `MC_PORT` (only `app.py:475` does). A dev-mode
  server on a port set in `config.json` has no protected listener at all.
  Fix: one line, read `CLAYRUNE_PORT` in the guard.
- Sound: with the flag off the factory is the old `Popen` with the old
  arguments plus one failed `open()` for the marker (`:379-389`); the
  fallback never runs the delivery code twice (`:443-473`); a prior shim that
  cannot be proven gone is never overwritten (`:398-412`,
  `agent_spawn_interactive.py:88-113`).

### N14. `mc/agent_worktree_followup.py` (56), `agent_log_file.py` (110), `agent_log_maintenance.py` (18): read fully

- **AP34 (E, low) `agent_log_file.py:96-101`, `agent_log_maintenance.py:7-11`
  with `agent_routes.py:7444`** — `snapshot()` is used as a READ at three call
  sites (`agent_routes.py:7540`, `:13724`, `:16126`) but always rewrites the
  whole log: `_update_agent_log` saves unconditionally after the mutator, and
  `claim_rows` changes nothing once every row carries an attempt id. Measured
  (read-only `ls`): the three largest agent logs on this install are 1.6, 2.0
  and 2.1 MB. So one message into a non-live or human-joinable session is a
  full parse plus a full atomic rewrite of up to 2 MB before any work, the
  model-pin route does it twice (`:13724` then `:13727`), and deleting a plan
  rewrites EVERY project's log whether or not it referenced the plan
  (`:16123-16126`). Fix: have `_update_agent_log` skip the save when the
  mutator reports no change.
- Sound: `transaction` is re-entrant per thread and releases the OS lock on
  close, including after a crash (`agent_log_file.py:20-58`); `merge_rows`
  applies a change only if the row is still what the snapshot saw (`:87-90`);
  `resolve_followup_cwd` no longer drops an isolated session into the shared
  tree (`agent_worktree_followup.py:48-49`).

### N15. `mc/conversation_contract.py` (102), `conversation_store.py` (1,478), `conversation_views.py` (107), `execution_lifecycle.py` (421)

Read: `conversation_views.py` fully; `conversation_store.py` lines 178-300,
485-522, 762-850, 1385-1478 plus every public method's call sites (script over
`mc/`, `server.py`, `app.py`, `tests/`); `conversation_contract.py` and
`execution_lifecycle.py` at structure level only.

- **AP35 (L/M, low) `server.py:1070-1072` -> `runtime_lifecycle_service.py:40`,
  `:88`, `:112`** — the whole provider-neutral conversation journal is
  unreachable at run time. The only constructor call hard-codes
  `enabled=False`, and every entry of the service returns `None`/`()` first
  when disabled. This is the store half of pass 1 **L1** (which reported the
  service wiring). What this pass adds, callers counted not guessed: of the
  store's public methods, these have NO caller outside the store and the tests
  even if the service were switched on: the legacy attempt API
  (`begin_attempt`, `append_event`, `get_conversation`, `delete_conversation`,
  `restore_conversation`, `:523-655`), all five migrations (`:346-482`), the
  whole capture pipeline (`bind_capture_source` to `seal_capture_source` and
  `record_coverage`, `:1209-1380`), the three history readers (`:1385-1478`),
  and `claim_attempt`, `bind_native_handle`, `save_runtime_launch_facts`,
  `bind_runtime_native_source`, `reconcile_attempt`, `change_engine`,
  `append_evidence(_batch)`, `list_lifecycle_conversations`.
  `conversation_views.py` has no importer at all outside tests (grep). About
  2,100 lines of store, contract, views and reducer are exercised only by
  their tests. Not a defect in the code; it is carrying cost, and it reads as
  live. See Streamlining.
- **AP36 (E, low, latent) `conversation_store.py:491-509`** — every store call
  opens a new SQLite connection, sets a pragma, and re-validates the schema
  (two pragmas plus a `sqlite_master` scan, `:184-186`) before its one query;
  `bridge_factory` adds `ensure_project_generation_schema` (another
  connection) and `project_generation` (a third) per dispatch
  (`runtime_lifecycle_service.py:50-53`). Read, not measured (the service is
  off). Fix when it is switched on: validate once per `ConversationStore`.
- Sound (in the ranges read): writes use `BEGIN IMMEDIATE`, a missing file is
  never created by a read (`:492-494`), history chunks are cut in SQL so a
  large payload is not loaded (`:1449-1454`), a project revoke bumps the
  generation and tombstones every conversation in one transaction (`:807-831`).

### N16. `mc/launch_transaction.py` (291, read fully), `isolated_launch.py` (244, read fully), `launch_marker.py` (100, read fully), `execution_policy.py` (300, read fully)

- **AP37 (L, medium) `launch_transaction.py:82-83`, `:89-90`, `:152-171`,
  `:187-197`, `:283-290` with `agent_spawn.py:237-242`** — an "ambiguous"
  launch failure ends the conversation for good, and most ordinary spawn
  refusals are classed ambiguous. `_unknown()` sets
  `session['_launch_uncertain']` and writes `launch_outcome='unknown'` on the
  agent-log row; `reserve()` then refuses every later turn for that session,
  live (`:82`) or rebuilt from the row after a restart (`:89`), with "previous
  launch outcome requires review; not retried". Nothing clears either marker:
  grep over `mc/` and `static/` finds one writer and two readers and no
  `pop`, no route, no UI text. The "review" has no surface. What gets there:
  only `FileNotFoundError` is marked a proven refusal (`agent_spawn.py:237`);
  `spawn_boundary` marks everything else ambiguous. Measured on Windows
  (scratch `subprocess.Popen` probe, no provider CLI): missing executable ->
  `FileNotFoundError`; missing cwd -> `NotADirectoryError`; a non-executable
  file -> `OSError` winerror 193; a directory as the target ->
  `PermissionError`. So three of four refusals where no child can exist take
  the terminal path, and the comment at `agent_spawn.py:238` ("missing
  executable/cwd") is wrong for cwd on Windows. A CLI binary that is half
  replaced or locked while it updates is exactly the winerror 193 / access
  denied case. Fix, smallest first: (1) a human "this did not start, clear
  it" action that pops both markers; (2) class `NotADirectoryError`,
  `PermissionError` and winerror 193 raised by the `Popen` constructor before
  `proc` is bound as refusals. `tests/test_launch_transaction.py:85-86` pins
  that the marker is set; no test asks how it is ever unset. Whether a later
  whole-row save by another path drops the durable marker was not traced.
- **AP38 (E, low) `launch_transaction.py:86`, `:111`, `:179`** — each turn start
  parses the whole agent log three times (strict read in `reserve`, the
  forgiving `_load_agent_log` for `destination`, strict read again in
  `launch`) on top of the read-modify-write inside the action, all under the
  project manager lock and the log file lock. Measured, read-only, on the
  largest log on this install: 1.6 MB, 500 rows, 4.6 ms per parse, 6.4 ms per
  dump; so about 14 ms of parsing plus a 1.6 MB rewrite per turn start under
  the project lock. Fix: keep the row found at `:86` and pass it to `launch`.
- Sound: `restore` compares the live owner and the durable row before undoing
  either (`:129-150`); a failed save of the unknown marker is logged, not
  swallowed (`:170-171`); `isolated_launch.launch_cwd` never returns the
  project checkout for an isolated session and refuses outside the lifecycle
  exclusion (`isolated_launch.py:142-159`); `launch_env` removes an inherited
  unattended marker for attended chats (`launch_marker.py:73-76`);
  `execution_policy.authorize_execution` is pure, fails closed on every
  missing or stale record, and has no shared state (`:242-287`).

### N17. `mc/midturn_rollover.py` (576), `mid_turn_send.py` (187), `turn_clock.py` (383): read fully

This review was itself rolled over twice by this module, so two findings below
carry live evidence: the state block this session was handed.

- **AP39 (L, medium) `bg_job_liveness.py:57-76` with
  `agent_routes.py:6337-6341`, `:6748-6749` and `midturn_rollover.py:198-203`** —
  a phantom background job is recorded whenever ANY tool's output merely
  contains the announcement phrases. `note_bg_job_result` is called for every
  tool_result with no check on the tool name, `parse_tool_result` searches the
  whole text for "moved to the background" / "Output is being written to:",
  and `:199-203` adds a job entry when the first phrase is present. Measured:
  feeding `parse_tool_result` the line-numbered text of
  `bg_job_liveness.py` itself (whose docstring quotes the CLI message) returns
  a job with `moved_on_timeout=True` and an output path that begins with a
  line number and a tab. Live: after this review read that file, its rollover
  state listed two "Read: (foreground call moved to the background ...)" jobs,
  one with exactly that unreadable path, and told the agent to check the
  output file before relaunching a job that never existed. Costs: a wrong
  instruction in the one block the fresh session trusts; a descendant scan of
  up to 3 s on the stream-reader thread per false match (AP10); and each
  phantom takes one of the 10 job slots (`:204`), so real jobs are evicted.
  Fix: act only when the tool is a shell tool, and anchor the phrases to the
  start of the result. `tests/test_bg_job_liveness.py` feeds only genuine
  announcements, so it cannot catch this.
- **AP40 (L, medium) `midturn_rollover.py:471-479` with
  `agent_routes.py:13878-13933`** — for a dispatched one-prompt worker, the
  case this module says it was built for (`:3-6`), the state block marks the
  agent's only instruction as probably stale. `note_user_message` is called
  from the follow-up and steer sites only (five call sites, all in the send
  route), never when a session is dispatched, so `_mt_latest_user_msg` is
  unset and the block prints "Latest user message (current direction): (not
  recorded; use the recent conversation)" directly above "First message of
  this chat (historical; may be long finished, not the current task)". Live:
  that is the block this session received, twice, while its first message was
  the entire brief. Fix: when no later message was recorded, label the first
  message as the current task. No test renders the block for a session with a
  task and no follow-up and asserts the label.
- **AP41 (L, low) `mid_turn_send.py:165-171` with `agent_routes.py:10697-10701`** —
  `drain_next` pops the queued message before it delivers it. A refusal
  writes a chat line (`:170`), but an exception from the delivery (the
  follow-up handler raising) is caught by the caller and written to the
  server log only: the user's queued message is gone and the chat shows
  nothing. Fix: catch in `drain_next` and write the same "[queued follow-up
  not sent ...]" line.
- **AP42 (M, low) `mid_turn_send.py:51`** — `print` instead of `_log` (the
  one diagnostic in the module bypasses the server log's timestamping).
- Note, not a finding: `turn_clock.commits_since` (`:249-254`) runs `git log`
  without the `stdin=DEVNULL` / no-window flags that `midturn_rollover._git`
  documents as required on a server with no stdin (`:394-398`). This install's
  server logs hold no `[turn-clock] git log failed` line (grep, count 0), so
  it works here; the two helpers should be one.
- Note: for an isolated agent the briefing's commit list is its own worktree
  HEAD (`turn_clock.py:250`), so commits landed on `master` during the gap,
  the ones that matter to it, are not listed.
- Sound: pending tool ids are scoped to the current process, so a replaced
  process cannot hold the gate shut (`midturn_rollover.py:101-113`); a roll
  needs 20k tokens of growth over the fresh prefix or over the last failed
  attempt, and gives up out loud after three failures (`:336-369`); the roll
  log is outside the project-records directory (`:491-499`); `decide` puts
  every interrupt guard above the steer/queue choice (`mid_turn_send.py:
  73-98`); `turn_clock.render` cannot raise into a live stdin write
  (`:358-377`) and measures from the last assistant output, not the last
  status change. The turn stamp is visible on this session's own first message.

### N18. `mc/fence_fail_closed.py` (295), `vendor_fence.py` (182), `vendor_fence_probe.py` (171), `vendor_fence_hook.py` (70), `vendor_context_sync.py` (101): read fully

- **AP43 (L, medium) `vendor_context_sync.py:99-101` with
  `agent_routes.py:11321-11324` and `agent_worktree.py:1040-1042`** — Clayrune
  itself makes an isolated vendor worktree dirty. Before a Codex, Gemini or
  Qwen session starts, the sync writes `AGENTS.md` / `GEMINI.md` / `QWEN.md`
  into the session's run directory, which for an isolated session is its
  worktree (the call-site comment says so). The worktree's own dirty check is
  `git status --porcelain` with only the shared runtime paths excluded, so the
  mirror counts: a new untracked file where the project has none, a modified
  tracked file where the project tracks a generated one and `CLAUDE.md` has
  moved on. This repo hides it (`.gitignore:91-93` ignores all three names);
  any other project with a `CLAUDE.md` does not. Same consequence class as
  pass 2 **R59** (a tree that reads dirty does not land and is not cleaned
  up), different cause. Read, not reproduced (needs a second repository and a
  vendor dispatch). Fix: add the runtime's context file name to the
  `:(exclude)` list when its first line is the generated-from marker, or write
  the name into the worktree's `info/exclude`. No test runs the sync and the
  dirty check together.
- **AP44 (E, low) `fence_fail_closed.py:281`, `:193-195`,
  `vendor_fence.py:171-175`, `vendor_fence_probe.py:128-148`** — every
  unattended launch re-proves the fence from scratch and nothing is memoised
  except the CLI version. Claude: one interpreter launch for the self-test per
  spawn, plus one per differing project fence entry, by the module's own note
  possibly while a project lock is held (`fence_fail_closed.py:63`). Gemini
  and Qwen: a Python walk that reads the source of up to 2,000 modules of the
  installed CLI to find two exports, then a Node process that imports the
  CLI core and runs the hook three times (three more interpreter launches),
  per spawn. A workflow or hivemind fan-out pays this once per worker, and a
  per-turn engine pays it per turn. Read, not measured: the fence refused the
  timing probe in this guarded session. Fix: memoise a pass for a short time
  keyed by (interpreter and fence file stamps, CLI binary stamp, settings
  digest); a changed stamp re-tests.
- **AP45 (L, low) `fence_fail_closed.py:290`** — the CLI whose version decides
  `onFailure` support is taken to be `out[0]`. Whether every Claude spawn site
  passes the CLI itself first (not a wrapper such as a shell or `node`) was
  not traced; if one does not, `--version` of the wrapper is parsed by the
  same three-number regex (`:96`, `:136`) and will usually read as newer than
  2.1.295. The effect is an `onFailure` key on a CLI that ignores it, so it
  is harmless today; it stops being harmless the day the version gates
  something that matters. Labelled a question, not a defect.
- **Open question Q1 `vendor_fence_hook.py:24-30`** — the alias table maps
  `edit_file` and `replace` to `Edit` but has no entry for a tool named
  `edit`. If Qwen's native edit tool reports that name, a Qwen file edit
  reaches the fence under a name the fence's Write/Edit rules do not key on.
  Not checked: Qwen is not installed on this host and this pass may not launch
  a provider CLI. One look at an installed Qwen's tool list settles it.
- Sound: an unverified vendor is refused, not run unfenced, and the session is
  left in a visible error (`vendor_fence.py:47-64`); the per-launch settings
  copy is refused unless its source is Clayrune-owned (`:137-138`); the hook
  adapter returns 2 on every load or translation failure and restores stdin
  (`vendor_fence_hook.py:62-66`); `read_many_files` is refused rather than
  half-translated (`:39-42`); the fail-closed settings copies are
  content-addressed, and this install holds 7 of them, so they do not grow
  per launch (read-only `ls`). The context sync never overwrites a
  hand-authored mirror (`vendor_context_sync.py:90-95`).

### N19. `mc/cli_update.py` (478), `cli_update_retry.py` (36), `cli_install.py` (135), `blueprints/cli_update_routes.py` (18): read fully; daemon and in-use probe at `system_routes.py:412-459`

- **AP46 (L, medium) `cli_update.py:347-357`** — check, then update, with
  nothing in between. `check_one` asks "is anything using this CLI" once
  (`:347-352`) and then runs the updater for up to 600 s (`:357`,
  `UPDATE_TIMEOUT_S`). No flag says "this CLI is being replaced", and no
  launch path asks: the only readers of this module outside its own route are
  `provider_summary` for the provider row (`agent_routes.py:2523`) and the Qwen
  install text (grep over `mc/`, `server.py`). A dispatch, a scheduled run or a
  follow-up that starts that CLI during the window launches a binary that is
  half written, which is the MC-991 case (`:17-19`) entered from the other
  side: on Windows the updater's rename then fails on the now-running exe, and
  the new session's spawn fails with access-denied or "not a valid
  application". Those are exactly the errors **AP37** classes as an ambiguous
  launch, so the conversation that lost the race is refused for good. Read,
  not measured (needs a real updater run). Fix: an `updating` set under
  `_lock`, set before `:357` and cleared after `:358`; the spawn factory waits
  or refuses with a retryable message while the name is in it. No test covers
  a launch during an update: every test drives `check_one` with a fixed
  `in_use_fn`.
- **AP47 (L, low) `cli_update.py:151-153`** — a failed `--version` is read as
  a version if its error text holds three dotted numbers. The condition
  `rc == 0 or out` is true for any non-empty output, and `_run` puts the argv
  into its timeout text (`:127`). Measured (scratch, injected `run_fn`): a
  timeout whose argv path is `.../.nvm/versions/node/v20.11.1/bin/codex`
  returns installed version `20.11.1`, and `check_one` then stores
  `up_to_date` against a latest of `0.158.0`. So a CLI that hangs on
  `--version` under an nvm prefix is reported healthy and current for 23
  hours, every day. The same reader produces `installed_after` (`:358`), so a
  binary broken by an update can read as "updated". Fix: `_ver(out) if rc ==
  0 else None`. `tests/test_cli_update.py` stubs return clean version strings
  (`:36-46`), so no existing test catches it.
- **AP48 (L, low) `system_routes.py:427-434`** (the in-use probe this module
  is given) — a registered process counts as "using" a CLI when the CLI's
  name appears as a word in its name or command preview, and a path segment
  that starts with a dot passes the word test: any registered long-running
  process whose command line contains a `.claude`, `.codex` or `.gemini`
  directory blocks that CLI's update for as long as it lives. It fails
  toward not updating, so it is safe; it is also silent starvation, shown
  only as "Update waiting ... Retries hourly". Read, not measured. Fix: match
  the resolved binary path or the install root (`install_root`, `:255`), not a
  word.
- **AP49 (E, low) `blueprints/cli_update_routes.py:14-18`** — the POST runs
  the whole pass on the request thread: per CLI up to 60 s for `--version`,
  60 s for `npm view`, 600 s for the updater and 60 s to re-read the version,
  times six CLIs. The caller is a scheduled script; a pass that updates one
  npm CLI outlives any ordinary HTTP timeout and the script reports a failure
  for a pass that succeeded. `run_once` already refuses a second concurrent
  pass (`:384`), so the route could start the pass on a thread and return the
  snapshot. Read, not measured.
- Sound: an unknown install method is reported, never guessed (`:338-342`);
  enumeration failure counts as "in use" (`:349-350`); nothing is killed; the
  hourly retry keeps the daily registry answer instead of asking again
  (`cli_update_retry.py:27-36`); the state file is outside the project-records
  directory and written atomically; `install_method` resolves the symlink
  before reading the path (`cli_install.py:86-90`).
- Live state on this install (read-only): Claude was one patch behind and
  `skipped_in_use` because idle persistent sessions hold the binary, last
  updated two days earlier. The design works; on an install where a chat is
  always open the update only lands in a gap.

### N20. `mc/mac_update.py` (568, read fully), routes `system_routes.py:2211-2262`, `:2278-2324`, `:2560-2628`

- **AP50 (L, medium) `mac_update.py:75`, `:429-439` with
  `system_routes.py:2289`, `:2211-2243`** — the swap helper decides "the new
  app is up" by polling `/api/system/update/status`, and on a frozen install
  that route makes two GitHub requests (8 s timeout each, no cache) before it
  returns the local commit the helper is waiting for. The helper allows 60 s
  from launch (`wait_up_s`), which must also cover first-launch verification
  of a new bundle and the server boot. On a slow, captive or offline network
  each answer costs up to 16 s; if boot plus one answer exceeds 60 s the
  helper stops the new app (`:450`), restores the old one and records "the
  new version did not start", for an update that was fine. Every poll that
  does get through also spends the unauthenticated GitHub API allowance the
  update check itself depends on. Read, not measured (no macOS host). Fix: a
  local-only probe (the bundled build info, no network) for the helper, or
  have the status route skip the remote fetch when asked. No test runs the
  helper against a slow status route: `tests/test_mac_update*.py` stub the
  HTTP answer.
- **AP51 (E, low) `mac_update.py:517`, `:539`, `:451`** — nothing sweeps a
  stale staging directory. A crash or force-quit during the download leaves
  `.clayrune-update-<stamp>` (the zip, later the extracted app) beside the
  bundle; grep over `mc/`, `app.py`, `server.py` finds no reader of these
  names outside this module. Hidden, a few hundred megabytes each, in the
  applications folder. Fix: at `start_job`, remove siblings matching the
  staging pattern older than a day.
- Process hygiene, checked because the brief asks: `stop_new_app` (`:379-395`)
  signals processes it did not start by PID. It selects them by exact
  executable path equal to the bundle it has just moved into place, after the
  old app's PID is confirmed gone, and only on the rollback path. That is a
  path match on a path the helper owns, not an image-name kill. Not a finding.
- Sound: nothing is replaced before codesign, Gatekeeper notarization, team
  and bundle-id gates pass (`:202-232`); the running app must itself be signed
  or the job stops before the download (`:520`); the download is HTTPS only,
  size-capped and hashed while streaming (`:275-297`); deletion targets are
  checked to be inside the bundle's parent (`:364-369`); the route is
  human-only with the passcode re-typed (`system_routes.py:2575-2583`); the
  app's shutdown has a 10 s hard exit (`system_routes.py:2048-2052`), so the
  job cannot sit in `restarting` for ever.

### N21. `mc/claude_projects_scan.py` (213, read fully), `blueprints/claude_import_routes.py` (40, read fully), `blueprints/agent_router_stats_routes.py` (70, read fully)

- **AP52 (E, medium) `claude_projects_scan.py:165-181`** — the scan pays full
  price for every transcript directory before it asks the one question that
  discards most of them. Per directory: a glob, a `stat` per transcript, a
  sort, then up to 256 KB read from the newest transcript and a regex over it
  (`:167-175`); only then `is_agent_worktree(cwd)` (`:179`). Clayrune itself
  creates one transcript directory per agent worktree. **Measured** on this
  install, calling `scan()` read-only: 5,718 directories, 26.7 s, 188
  candidates; 1,357 were dropped as worktrees and 640 as missing folders
  after their transcripts had been read. The route runs it synchronously on a
  request thread (`claude_import_routes.py:31`) and the dialog awaits it
  (`static/js/claude-import.js:50`). On a fresh install the directory is small
  and this is instant, which is the case the feature was written for; on any
  install that has dispatched isolated agents for a few weeks the dialog
  hangs for tens of seconds. Fix: skip a directory whose NAME contains the
  encoded worktree segment (`--clayrune-agents-`) before any I/O, and read
  the head in 16 KB first (the `cwd` is on the first lines). No test has more
  than a handful of directories.
- **AP53 (L, low) `claude_projects_scan.py:198-200`** — when two transcript
  directories resolve to one folder, the second adds its session count but is
  never checked for being the registered or the newest spelling; harmless.
  What is not harmless: 188 "candidates" on this install. Most are scratch
  and temp folders a CLI was once started in. The list is capped at 300 and
  sorted by recency, so the dialog is usable, but "import your projects"
  offers every directory an agent ever ran a nested CLI in. Labelled a
  product note, not a defect.
- **AP54 (M, low) `agent_router_stats_routes.py:53-56`** — `int(v or 0)` on a
  value read from a state file: one non-numeric counter in any project's
  router-stats file makes the whole aggregate route return 500. The file read
  two lines up is guarded (`:46-49`, silently); the arithmetic is not.
- Sound: the scan reads only the head of a transcript and never writes; a
  folder that is the home directory, a filesystem root, the install itself or
  already registered is dropped and counted (`:179-194`); there is no add
  route, so an import goes through the same create-project checks as a
  hand-added project (`claude_import_routes.py:8-11`).

### N22. `mc/claude_cli_min_version.py` (107), `claude_resume_snapshot.py` (32), `claude_signin_channel.py` (41), `claude_usage_source.py` (192): read fully

- **AP55 (E, low) `claude_usage_source.py:78-89`** — a failed usage read is
  never cached, only a good one (`:86-88`). While the usage endpoint is
  unreachable, throttled, or the stored token has expired, every caller pays
  the full 6 s timeout again: the two usage routes
  (`system_routes.py:1201`, `:1325`), the allowance reader
  (`provider_allowance.py:80`) and, on the dispatch path, the pace check for a
  named character (`engine_pace_dispatch.py:15`). Its Codex twin already
  caches failures for exactly this reason (`codex_live_usage.py:14-15`,
  `:92-93`). Also no single-flight: callers arriving together after the 60 s
  expiry each make the request. Read, not measured (one timeout line in the
  server log sampled). Fix: store `ts` on failure too, with a shorter TTL.
- **AP56 (M, low) `claude_cli_min_version.py:89-91`** — the version probe
  inherits stdin. `cli_update._run` and `midturn_rollover._git` both say why
  that is unsafe on a server started without a console (the launch raises
  before the child runs). Here the exception is logged and the model gate
  then passes the model through, because an unreadable version is "no
  evidence" (`:58`). So on a console-less server the floor for a new model is
  silently not applied. The server log sampled holds no such failure line
  (grep, count 0), so it works on this install; one keyword argument closes
  it. Related duplication: three modules each run and parse the Claude
  `--version` text their own way (`cli_update.py:151`, here `:74`,
  `fence_fail_closed.py`); see Streamlining.
- Sound: an unknown sign-in or usage-source value lands on the stricter side
  (`claude_signin_channel.py:36-37`, `claude_usage_source.py:69-70`); a runner
  document is size-capped, age-checked and reduced to numbers and short
  strings (`:122-139`, `:174-189`); the snapshot flag is added to resumes only
  and is idempotent (`claude_resume_snapshot.py:20-32`).

### N23. `mc/codex_live_usage.py` (94), `codex_usage_checkpoint.py` (178), `codex_run_error.py` (204): read fully

- **AP57 (L, low) `codex_usage_checkpoint.py:120-126` with
  `usage_breakdown_sampler.py:111`** — a Codex mid-turn usage tick is
  "complete" only when the session's own rollout holds a token record at
  exactly the allowance sample's source time. That time is the event time of
  the newest rollout on the machine, or the probe time of the live app-server
  read; so of several Codex sessions running at once at most the one whose
  rollout produced the sample can match, and after a live probe none can.
  **Measured**, read-only, on this install's usage store: Codex sample ticks
  2,635 complete against 2,674 unavailable (Claude: 23,348 against 123). The
  server logs name the reason: 150 of 216 logged transitions are "no token
  observation at allowance source time". The module is doing what its
  docstring says (never move an older reading to a newer time); the result is
  that mid-turn Codex usage attribution is missing half the time. Fix, if the
  number matters: accept the newest token record at or before the boundary
  and store ITS time as the tick's `observed_at`, which keeps the rule and
  loses nothing. Each tick also re-reads the whole rollout from byte 0
  (`:59-60`).
- Sound: a live probe is single-flight and failures are cached
  (`codex_live_usage.py:77-94`); the rollout error reader takes only the last
  `task_complete`, refuses one that finished before this run began, and reads
  a bounded tail (`codex_run_error.py:92-120`); tracing lines are matched on
  timestamp + level + module path, so a reply that starts with "ERROR" is not
  swallowed (`:36-38`); every counter in a usage record is type- and
  order-checked before it is believed (`codex_usage_checkpoint.py:29-49`,
  `:107-119`).

### N24. `mc/aider_guard.py` (252), `aider_launch.py` (46), `aider_runner.py` (107), `aider_source_pin.py` (58): read fully; reached from `vendor_fence.py:56-78`

- **AP58 (L, medium, latent) `aider_source_pin.py:11-17`, `:45-51` with
  `cli_update.py:55`, `cli_install.py:128-134`** — two modules in this region
  undo each other. The restricted Aider adapter runs only against
  `aider-chat` 0.86.2 on CPython 3.12 with six dependencies at exact versions,
  and refuses on any drift. The daily CLI updater is on by default, lists
  `aider` among the CLIs it keeps current, and upgrades it with pip, pipx or
  uv to whatever is newest. Nothing in `cli_update.py` consults the adapter
  flag or the pin (read fully; no reference). So on an install where the
  owner turns on `aider_restricted_adapter_enabled`, the first daily pass that
  sees a newer release moves Aider off the audited version and every
  unattended Aider run is refused from then on with "version drift", until
  someone downgrades by hand, and the next pass upgrades it again. Latent
  here: the flag is off and Aider is not installed. Fix: `check_one` reports
  `pinned` and does not update a CLI whose adapter pin is active. No test
  runs the two together.
- **AP59 (M, low) `aider_launch.py:44`, `aider_runner.py:56`,
  `aider_source_pin.py:11`** — the audited version is written as a literal in
  three files. The next audit bump that changes one or two of them makes
  every launch fail the proof comparison. One constant.
- Sound: the guard is installed before any coder exists and fails closed on
  any internal-shape drift (`aider_guard.py:107-110`, `:190-193`); every
  subprocess goes through one checked constructor with the environment diffed
  against the parent (`:210-250`); the source tree is hashed, and extension
  modules or stray bytecode are refused (`aider_source_pin.py:20-33`); an
  upstream-caught refusal still ends the run non-zero (`aider_runner.py:100`).

### N25. `mc/attend_once_pass.py` (134), `attend_once_replay.py` (82), `background_model_policy.py` (27), `caller_attribution.py` (305): read fully

- No finding. `caller_attribution` was checked for the obvious way round it (a
  request to the machine's own LAN address is not loopback, so it is reported
  unattributed and the caller's hints stand): that request is stopped earlier
  by the local-auth gate, which only exempts loopback and tunnelled sessions
  (`blueprints/local_auth.py:271-291`), so an agent without the passcode
  cannot use it. The lookup never raises, an unavailable lookup with no
  `Origin` fails closed (`:285-290`), and a missing dependency is announced at
  boot (`:294-305`). The pass record is pure, never extends or stacks
  (`attend_once_pass.py:107-116`), and a replay matches one tool call id plus
  one action digest (`attend_once_replay.py:56-71`).

### N26. `mc/resume_context.py` (100), `resume_cwd.py` (178), `resume_target.py` (149): read fully

- **AP60 (E, medium) `resume_cwd.py:125-138` with
  `agent_runtime.py:2520-2550`** — when a Claude conversation's transcript is
  not found, the lookup repeats a search that had already covered everything.
  `transcript_cwd` asks the runtime for the transcript under the project and
  then under EVERY directory in `.clayrune/agents`; but the runtime's lookup
  for the project path already globs every agent-worktree transcript
  directory, so the later candidates can only repeat the miss, and each
  repeat is another glob over the whole transcript store. **Measured**,
  read-only, on this install (288 worktree directories, 5,718 transcript
  directories): one miss through `transcript_cwd` takes 12.5 s; a single
  runtime lookup takes 0.05 s. It runs inside the dispatch request
  (`agent_routes.py:12826`) and the revive path (`:7903`). A miss is the
  resume-target incident this region already documents
  (`resume_target.py:3-9`: an id that never got a transcript) and any
  conversation whose transcript was pruned. Fix: for Claude, one runtime
  lookup with the project path, no candidate loop. For the hit case the cost
  is one lookup; no test has more than a few worktree directories.
- **AP61 (E, low) `resume_cwd.py:35-49`** — `recorded_cwd` JSON-decodes every
  line until it finds a `cwd`. For a provider whose transcript never records
  one (the Qwen case the caller handles at `:134-135`), that is a full decode
  of the whole transcript on every resume, to return nothing. Fix: stop after
  the first N lines, or test the substring before decoding.
- Sound: a transcript is never taken as proof of its directory; a missing or
  unusable tree refuses, keeps the user's message on the row and starts no
  process (`resume_cwd.py:52-65`, `:141-178`); a Codex transcript from another
  project cannot relocate this one (`:117-123`); the delivered-context stamp
  moves only after the pipe accepted the bytes (`resume_context.py:47-61`);
  a session id is not adopted from a pre-stdin hook event, and a quoted
  "No conversation found" line in a reply is not mistaken for the CLI's own
  error (`resume_target.py:41-70`).

### N27. `mc/state.py` (207), `transform_deadline.py` (22), `transform_http_deadline.py` (115), `transform_process_deadline.py` (123), `transform_sandbox.py` (62), `pty_backend.py` (255): read fully

- **AP62 (L, low) `pty_backend.py:221-240`** — the Windows close does its
  "defense in depth" in the wrong order. It terminates the root first
  (`:223`), then asks the OS tree-kill tool for that PID's tree (`:237`). With
  the root gone the tool finds no such process and no descendants, so the
  second step can only ever be a no-op: it backs up nothing in the one case
  the comment describes (the platform cascade not reaping the children). The
  sibling module states the rule the other way round
  (`transform_process_deadline.py:36`, never hand an exited root to the tree
  kill). The PID is not reusable while the PTY object holds its handle, so
  this is not a stranger-kill; it is a dead step with a 10 s timeout on every
  terminal close. Fix: tree-kill first while the root is alive, then
  terminate. Read; a scratch run closed a PTY in 0.29 s and left no child,
  which does not separate the two steps.
- **AP63 (M, low) `transform_process_deadline.py:36-52`** — unreachable. Both
  callers are in this file (`:107`, `:117`) and pass `job` on Windows (or the
  function has already raised, `:74-89`) and `pgid` plus `reader` elsewhere,
  so the third branch, including its own tree-kill call, never runs. See
  Streamlining.
- **AP64 (M, low) `state.py:7-13`** — the module docstring says objects here
  are never rebound and that four named globals "deliberately stayed behind"
  in `server.py`. All four now live here and are rebound (`:149-150`,
  `:175-176`, and `CONFIG` at `:23`). The rule that actually protects them is
  "always read through `state.<name>`"; a `from mc.state import CONFIG` at
  module level would bind the empty dict for ever. Grep finds one such
  import, inside a function, so nothing is broken today; the docstring
  teaches the wrong rule.
- **AP65 (E, low) `transform_http_deadline.py:41-81`** — every transform call
  builds four classes, an adapter pair and a new session, so no connection is
  ever reused and each call pays a fresh TLS handshake. By design for
  cancellation; a per-thread session with the tracking hook would keep both.
- Sound: the transform child is created suspended and put in a kill-on-close
  job before it can run (`transform_process_deadline.py:65-78`); the deadline
  includes the cleanup reserve; the HTTP watchdog shuts the socket down
  rather than waiting on an idle timeout (`transform_http_deadline.py:101-110`);
  the neutral directory keeps project context out of a tool-free transform
  (`transform_sandbox.py:41-62`).

## Coverage

In scope: 65 modules under `mc/` (15,769 lines) and three route modules under
`mc/blueprints/` (128 lines).

- **Read fully (62 modules, 13,768 lines, plus the three route modules):**
  `process_guard`, `process_ledger`, `process_sweep`, `windows_process_job`,
  `guardrail_hooks`, `bg_job_liveness`, `background_tasks`,
  `background_model_policy`, `agent_jobs`, `agent_shim`, `agent_shim_client`,
  `agent_shim_adopt`, `agent_shim_completion`, `agent_shim_completion_retry`,
  `agent_shim_tramp`, `agent_spawn`, `agent_spawn_interactive`,
  `agent_uncertain_launch`, `agent_worktree_followup`, `agent_log_file`,
  `agent_log_maintenance`, `conversation_views`, `launch_transaction`,
  `launch_marker`, `isolated_launch`, `execution_policy`, `midturn_rollover`,
  `mid_turn_send`, `turn_clock`, `fence_fail_closed`, `vendor_fence`,
  `vendor_fence_probe`, `vendor_fence_hook`, `vendor_context_sync`,
  `cli_update`, `cli_update_retry`, `cli_install`, `mac_update`,
  `claude_projects_scan`, `claude_cli_min_version`, `claude_resume_snapshot`,
  `claude_signin_channel`, `claude_usage_source`, `codex_live_usage`,
  `codex_usage_checkpoint`, `codex_run_error`, `aider_guard`, `aider_launch`,
  `aider_runner`, `aider_source_pin`, `attend_once_pass`, `attend_once_replay`,
  `caller_attribution`, `resume_context`, `resume_cwd`, `resume_target`,
  `state`, `transform_deadline`, `transform_http_deadline`,
  `transform_process_deadline`, `transform_sandbox`, `pty_backend`;
  `blueprints/agent_router_stats_routes`, `blueprints/claude_import_routes`,
  `blueprints/cli_update_routes`.
- **Read partly:** `conversation_store.py` lines 178-300, 485-522, 762-850 and
  1385-1478 (344 of 1,478), plus the call sites of every public method. The
  rest was not read line by line: the store is unreachable at run time (AP35).
- **Structure level only:** `conversation_contract.py` (102) and
  `execution_lifecycle.py` (421), for the same reason.
- **Followed into other regions only as far as a finding needed:** the job
  route, the send route, the model-pin and plan-delete call sites and the
  context-sync call site in `agent_routes.py`; the sweep, CLI-update daemon,
  restart-blocker and update-status routes in `system_routes.py`; the
  transcript lookup in `agent_runtime.py:2520-2550`; `local_auth.py:271-291`;
  `runtime_lifecycle_service.py`; `proc_kill.py:1-60`.
- **Not reached:** the `mc/agent_mail_*.py` modules (55 files) and their route
  modules, taken to be pass 05b's. Tests were read only where a finding asks
  "would a test catch this". No macOS or Linux host, no Qwen and no Aider were
  available, so every finding about those is read, not measured, and says so.

Measured in scratch scripts or by read-only counts: AP1, AP2, AP3, AP5, AP13,
AP22, AP34 (file sizes), AP37, AP38, AP39, AP47, AP52, AP57, AP60. Seen live in
this session: AP2, AP39, AP40. Everything else is read, not measured.
