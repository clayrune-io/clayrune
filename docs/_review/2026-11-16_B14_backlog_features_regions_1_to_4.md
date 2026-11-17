# Backlog cross-check and feature trace, regions 1 to 4 (MC-1179 pass B14) — 2026-11-16

Reviewer: Fenn (code-reviewer), one sequential pass. Base: `master` @ `3f51b0e0`.
Review only: no source file was changed and no backlog item was patched or
noted. `server` was never imported, the live server was sent nothing but `GET`,
no model call was made, and no process that binds a port or launches a provider
CLI was started.

STATUS: IN PROGRESS. Sections are written as they are completed; the ranked
summary and coverage statement are written last. If this line is still here,
the pass was interrupted: continue from the last committed section.

Scope: the regions of passes 1 to 4 (runtime core, agent routes and session
lifecycle, memory and learning, frontend). Those four reports are not repeated;
their finding ids (S, MA, C, A, Q, G, X, L, P / R / ML / FE, CV, MM, IN, MB, BP,
WB, DK, CD, SK, SS, ST, DV, PA, MC, BK, FR, SC, LN) are cited where a feature or
a backlog item meets one. New findings from this pass use the prefix **BX**.

## Ranked summary

(written last)

## Feature table (lens 4)

Passes 1 to 4 were written before the feature lens was added, so this table is
the lens-4 result for all four regions. "Basis" says how the verdict was
reached: **traced** means the entry point and the state change were opened in
this pass; **pass N** means the verdict rests on that report's finding, whose
lines were opened there and not re-opened here. Finding ids without the BX
prefix belong to the four earlier reports.

Verdicts: **works** (as documented) / **defect** (works with a defect) /
**cannot** (cannot work as written) / **undetermined**.

### Runtime core and agent routes

| # | Feature | Main path | Most likely failure path | Verdict | Basis |
|---|---|---|---|---|---|
| F1 | Start a chat with an agent | `POST .../agent/dispatch` (`mc/blueprints/agent_routes.py:13540`) attributes the caller, applies pace routing, calls `_dispatch_agent_internal` (`:12551`), returns `session_id` | CLI missing: 500 with an install message (`:13652-13653`). Context build fails on a non-Claude engine: the agent launches with no rules, persona or memory (R39) | defect (R39, R47) | traced |
| F2 | Send a follow-up message | `POST .../agent/send` (`:13751`) appends the user line (`:13883`) and writes to the engine through the delivery seam (`mc/agent_runtime.py:5509-5560`) | Engine write fails: now refused, not reported sent (MC-1168). A failed non-Claude follow-up still answers `ok: true` on one branch (R49); the browser shows a refused send as sent (CV2) | defect (R49, CV2, R48) | traced |
| F3 | Stop an agent | `POST .../agent/stop` (`:15461`) sets `stopped` under the project lock (`_stop_session`, `:15250-15271`), kills the tree off-lock (`:15274-15285`) | On six non-Claude engines the stop is recorded as a crash with a hint (MA1); `stdin.close()` runs under the lock (R53) | defect (MA1, R53) | traced; MA1 from pass 1 |
| F4 | Interrupt and redirect mid-turn | `POST .../agent/interrupt` (`:15511`), respawn, `_finish_interrupt_respawn` (`:15496-15509`) | A follow-up during a running turn is logged as a crash on Gemini, OpenCode, Goose, Aider, Kiro (G2, X1) | defect (G2, X1) | pass 1 |
| F5 | Resume a past conversation | Dispatch with `resume_conversation_id`; Mode B revival writes the message through the delivery seam and undoes the revive on failure (`agent_routes.py:8200-8226`) | Resuming by an earlier id of a rolled chain falls back to the project default engine (R42); working directory gone: 409 refusal (`:13642-13644`) | defect (R42) | traced; R42 from pass 2 |
| F6 | Live chat stream in the browser | `GET .../agent/stream` (`:14181`) feeds `connectAgentStream` (`static/js/resume-preview.js:825`) | Reconnect catch-up drops repeated lines and shows the wrong ones (FE1); one stream per running session with no ceiling (FE5) | defect (FE1, FE5) | pass 4 |
| F7 | Status poll and chat rail | `GET .../agent/status` (`:16254`) read by `fetchAgentStatus` (`static/js/conversation.js:6108`) | Ships every session buffer per poll (R57, FE6); any JSON body is trusted (CV8) | defect (R57, FE6) | pass 2, pass 4 |
| F8 | Context rollover at a turn boundary | `_context_tokens_over_threshold` (`:12183-12195`) with `threshold_for` (`mc/rollover_threshold.py:100`), then `_auto_fresh_trigger` (`:12199`) | Handoff lists finished children as running (R44); a pin under the fresh floor is honoured for models outside the table (BX1) | defect (R44, BX1) | traced |
| F9 | Mid-turn rollover | `note_main_tokens` and `should_roll` (`mc/midturn_rollover.py:298-343`), `_maybe_midturn_roll` (`agent_routes.py:15943`); off by default (`:86`) | Background jobs die with the old process tree: now reported per job, not prevented (MC-1149 phase 1) | works, within its flag (BX1 applies) | traced |
| F10 | Agent asks the user a question (`mc:question`) | `apply_mc_tool_blocks` queues the question; the card posts the answer back as the next message | Logs "ignored" after it has queued (A7, cosmetic) | works | pass 1; route not re-opened |
| F11 | Plan approval card | `approvePlan` (`static/js/conversation.js:5323`) | Replaying an old ExitPlanMode line re-arms approval (CV3) | defect (CV3) | pass 4 |
| F12 | Dispatch a child and get its report back | `notify_session` on dispatch (`:13582`, `:13609`); delivery loop hands the completion to the spawner; waiting marker in the chat (`static/js/report-waiting.js:22-35`) | Delivery loop re-validates every completion ever recorded each 5 s (R51); receipt reconciler re-reads transcripts (R54) | defect (R51, cost only) | traced entry; pass 2 |
| F13 | Agent worktree and merge back | `merge_back` (`mc/agent_worktree.py:535`), landing gate (`:608-613`), cold review hold (`mc/cold_review_gate.py`) | One stray untracked file in shared data makes every worktree read dirty, so nothing lands (R59, live when found); a default branch not named `master` strands every branch (R61) | defect (R59, R61) | traced gate; pass 2 |
| F14 | Non-Claude engines run a turn | Runtime registry in `mc/agent_runtime.py`; shared Mode A reader | Stop and follow-up bookkeeping (MA1, X1, G2); OpenCode and Goose never use the resume they declare, each follow-up is a new native session with a 20-line tail (X4); a failed turn that exits 0 is `completed` (MA3) | defect (MA1, X4, MA3) | pass 1 |
| F15 | Tool-free model calls (Scribe, condense, summaries) | `ClaudeRuntime.oneshot` and the per-engine equivalents | Timeout does not hold on Windows through the `.cmd` shim (A2); Qwen and Gemini return a failed run as an answer (Q1, G1) | defect (A2, Q1, G1) | pass 1 |
| F16 | Long background job for an agent | `POST .../agent/<sid>/job` (`:1956`), status `GET` (`:2063`); completion wakes the session; open jobs block restart (`mc/blueprints/system_routes.py:1746`) | Server restart kills the job: now a listed blocker (MC-1160) | works | traced entry; MC-1160 |
| F17 | Restart, update, startup recovery | Restart blockers, then the boot passes in `server.py` | Startup backfill re-imports each earlier transcript of a rolled chat as a fake interrupted row (S1); desktop launch beside a running instance overwrites its exec token (S2) | defect (S1, S2) | pass 1 |
| F18 | Agent survives a server restart | `mc/agent_shim*.py`, adoption at boot | Flag `agent_survive_restart_enabled` is `False` (`mc/agent_shim_client.py:45`) | cannot, as shipped (off by design; MC-1150) | traced flag only |
| F19 | Incognito chat | `incognito` on dispatch (`:13547`) | Unreadable registry rebuilt from empty, un-marking earlier incognito transcripts (A4); unreadable project record replaced with a blank one (R3) | defect (A4, R3) | pass 1, pass 2 |
| F20 | Pace-aware engine reroute | `_engine_pace_dispatch.prepare` (`:13610-13614`), disclosure prefixed to the task (`:13616-13618`), returned in the response (`:13656`) | The 20-hour quota block lasts about an hour and a later success does not clear it (R43) | defect (R43) | traced |
| F21 | Empty turn is not "completed" | `mc/empty_turn.py` from the readers (`mc/agent_runtime.py:4972-4975`, `:6499-6505`) | Gemini branch untested by the item's own account | works (not run live) | traced call sites |
| F22 | Per-chat model pin | `POST .../agent/<sid>/model` (`:13659`) | The browser reports success on a refused request (CV6) | defect (CV6) | pass 4 |
| F23 | Transcript view, search, documents | Transcript routes (`:16621`, `:16952`, `:16997`), search (`:17295`), documents (`:18307`) | Conversation id from the URL goes into a file path unchecked (R55); a scan exception caches an empty result so documents vanish (A3); search skips worktree folders (R56) | defect (R55, A3, R56) | pass 1, pass 2 |

### Memory and learning

| # | Feature | Main path | Most likely failure path | Verdict | Basis |
|---|---|---|---|---|---|
| F24 | Session summary into memory (Scribe) | Turn end or close, Scribe call, archive and index commit | A task with a newline in its first 80 characters loses its summary at the next commit: 1,226 of 7,727 entries (ML15); Scribe call runs under the project lock (R38) | defect (ML15, R38) | pass 3, pass 2 |
| F25 | Relevant memory delivered to a session | Read floor at dispatch and per turn | A scheduled or agent-dispatched session is treated as an attended reader and receives unattended-origin notes (ML4, ML9) | defect (ML4, ML9) | pass 3 |
| F26 | Index stays within its byte budget (condense) | `_index_byte_trigger` (`mc/memory.py:4418-4423`) fires the structured condense under the floor | The shipped-default `agent` condense mode fires on a trigger that cannot clear and launches an agent with a prompt for a layout that no longer exists (ML16) | cannot, in the default mode (ML16); works in structured mode | traced trigger; pass 3 |
| F27 | Standing positions | Position route writes, firing on subject, weekly review | A position recorded through the route is always stamped `unattended`, so no steward run sees one: 0 of 115 (ML12) | defect (ML12) | pass 3 |
| F28 | Habit statement becomes a note | Terminal Scribe: `_habit_source_statements` (`mc/memory.py:5929-5971`) into `_scribe_classify_habits` (`:6179`) | Every message after the first is stored in a shape the reader skips (BX2) | defect (BX2) | traced, measured |
| F29 | Distiller proposes, human promotes or rejects | `POST /api/distiller/promote` (`mc/blueprints/distiller_routes.py:111`), `reject` (`:215`) | Promote never runs the authority guard (ML2); the guard is a phrase list, 7 of 9 paraphrases pass (ML3); reject has no caller check (ML11); the pre-generation "no" check never matches for skills (ML10) | defect (ML2, ML3, ML10, ML11) | pass 3 |
| F30 | Install, edit, delete a skill | `POST /api/skills` (`mc/blueprints/skills_routes.py:126`), `PUT` (`:162`), `DELETE` (`:193`); editor `saveSkillFromEditor` (`static/js/skills-panel.js:871`) | Any caller can install a global skill, no human check (ML1, blocker); the editor Save drops other frontmatter and a failed load turns Save into a wipe (SK4) | defect (ML1, SK4) | pass 3, pass 4 |
| F31 | Import a skill from Git, folder, paste | Import routes (`skills_routes.py:276-567`), quarantine (`:599-615`) | "Force install anyway" is dead for every Git import (SK3); picking a nested skill fails on Windows (SK2) | defect (SK2, SK3) | pass 4 |
| F32 | Continuity across sessions | Continuity record written at close, origin line and liveness marks at start (`mc/session_provenance.py:44`, `mc/continuity_liveness.py:77`) | `write_continuity` is an unlocked read-modify-write (ML17) | defect (ML17) | traced marks; pass 3 |
| F33 | Mid-task memory push | Push observer on file tool calls | Report mode by design: it delivers nothing, runs a search on the reader thread per call, and wrote an uncapped 129.6 MB log (ML24) | cannot deliver (report mode); cost defect (ML24) | pass 3 |
| F34 | Topic notes and memory note route | Note routes, `resolve_mint` | A request field is written raw into frontmatter, a newline re-stamps the note `interactive` (ML5); `slug` goes into a path unchecked (ML14) | defect (ML5, ML6, ML14) | pass 3 |

### Frontend

| # | Feature | Main path | Most likely failure path | Verdict | Basis |
|---|---|---|---|---|---|
| F35 | Dashboard boot and project grid | `index.html` boot, project poll, `openProjectModal` (`static/js/modal-manager.js:208`) | Stored fields written into markup unescaped, script runs on render (FE13, blocker); every load fetches the whole Desk (FE21) | defect (FE13, FE21) | pass 4 |
| F36 | Composer: type, attach, send | `dispatchAgent` (`static/js/resume-preview.js:305`), `uploadAgentImages` (`static/js/composer-extras.js:460`) | Upload fails: the message is sent anyway without the attachment (CV7); a message restored after a failed send is erased by the next rebuild (FE14) | defect (CV7, FE14, CV2) | pass 4 |
| F37 | Chat tabs: pin and close | Tab controls in the chat header | The controls exist only in markup that is never rendered (CV5) | cannot (CV5) | pass 4 |
| F38 | Floor: figures, hire, un-hire | Floor render, hire drag, un-hire handler | An agent's self-chosen name runs as script on click (FE2); un-hire takes a persona name inside a quoted handler (CV1) | defect (FE2, CV1) | pass 4 |
| F39 | Workflow builder | Canvas edit and save | Adding a person blanks the persona of the step whose editor is open (WB1, blocker, measured) | defect (WB1) | pass 4 |
| F40 | Settings | Section render, save, human gate | Removing any push device unsubscribes this browser (ST2); the sign-in session "rename" link is broken markup (ST1). No field exists for `context_rollover_by_character`, `verify_command` or `cold_review_required` (this pass) | defect (ST1, ST2) | pass 4; gaps checked here by grep |
| F41 | Skills panel | List, editor, browse other projects | "Install" from another project copies the text and reports installed (SK5); frontmatter name reaches quoted handlers (SK1) | defect (SK1, SK4, SK5) | pass 4 |
| F42 | MCP server manager | List, per-project toggle, edit | Double-click on a toggle turns every server off (MC3); Edit then Save rewrites the entry from a lossy form (MC4); env-var name from a repo runs on first keystroke (MC1) | defect (MC1, MC3, MC4) | pass 4 |
| F43 | Backup, export, restore | Backup panel | Export with the vault included cannot be submitted, the button never re-enables (BK3); a failed size preview retries in a tight loop (BK2) | cannot, for vault export (BK3); defect otherwise | pass 4 |
| F44 | First-run setup | Setup steps, provider sign-in terminal | "Back up now" writes to the old default, not the chosen destination (FR2); closing a re-run saves steps never shown (FR3) | defect (FR2, FR3) | pass 4 |
| F45 | Schedule calendar | Calendar render from schedules | A weekly schedule is never placed on the calendar (SC1, measured) | cannot, for weekly schedules (SC1) | pass 4 |
| F46 | Mobile layout | `static/js/mobile.js`, `sizeAgentChat` | Inbox badge resets on every project close (MB1); pane height after keyboard close is unverified on a device (MC-988) | defect (MB1); undetermined for MC-988 | pass 4; needs a device |
| F47 | Installed app update | `forceUpdateServiceWorker` (`static/js/installed-app-update.js:3`), streams paused around `update()` (`:16`) | A blocked update: timeout and action recovery are covered by the fixture the item cites | works in a browser tab; undetermined when installed | traced; needs a device |
| F48 | Browser pane (frontend half) | `openBrowserPane`, input forwarding | Can open twice at once (BP2); the fallback project is a hardcoded id (BP1); a pop-out re-opens and can stop the main window's session (BP3) | defect (BP1, BP2, BP3) | pass 4 |
| F49 | Pop-out chat window | `POST /api/popout/claim` (`mc/blueprints/popout_routes.py:73`), `release` (`:96`) | Mac bridge never run on a Mac (MC-1027); BP3 above | undetermined on Mac; defect (BP3) | traced routes; pass 4 |
| F50 | Stop-hook draft collapse | Collapse rule at `static/js/rich-text.js:407` | A short hook follow-up hiding the answer: fixed by MC-1028, not seen live | works (not seen live) | traced rule |
| F51 | Report-waiting marker | Status poll paints it (`static/js/conversation.js:6136`) | The rebuild erased it 150 ms after paint: fixed by `c14c4725`; not observed on a real busy parent | works (not seen live) | traced |
| F52 | Vault unlock link and popup | `static/js/rich-text.js:237-238`, `static/js/vault-unlock-popup.js:3` | Card stays "locked" after unlock: fixed by `30cff5cb`, `ddfcf287`; re-test not recorded | works (not seen live) | traced |
| F53 | Desk (frontend half) | `static/js/desk-v1-*.js` | Skip and Move change only the browser copy in live mode, a skipped post still publishes (DV10, blocker); autosave writes storyboard A into B (DV1); edits keyed by paragraph position (DV6) | cannot, for Skip in live mode (DV10); defect otherwise | pass 4 |
| F54 | Usage breakdown screen | System status view | Fetched for views that do not show it and every 60 s for the life of the tab (SS1) | defect (SS1, cost) | pass 4 |

### Feature totals

54 features. **Works: 8** (F9, F10, F16, F21, F47 in a tab, F50, F51, F52; four of
them not seen live). **Works with a defect: 39.** **Cannot work as written: 7**
(F18 by design, F26 in the default mode, F33 by design, F37, F43 vault export,
F45 weekly, F53 Skip). Two of the 39 carry an undetermined half that needs a
device (F46, F49).

What the owner should take from it: the main loop (start, send, stream, stop,
resume, roll) works every day and every step of it carries a finding, mostly
about what is recorded or shown rather than what runs. The features that do
not work at all are small and specific, and four of the seven are one-line
classes of fix (CV5, BK3, SC1, DV10). The two blockers that sit on an ordinary
path are ML1 and FE13.

## Backlog cross-check

Source: `GET /api/project/mission_control/backlog` taken 2026-11-16, 91 items
open / in_progress / blocked. MC-1179 to MC-1183 are skipped (this review's own
items). Items whose subject lies outside regions 1 to 4 are listed after the
table as OUT OF REGION and not classified. "on master" means the cited commit
is an ancestor of the reviewed `master` (checked with
`git merge-base --is-ancestor`, see Detail: commit ancestry).

Classes: DONE / PARTLY DONE / NOT DONE / OBSOLETE / DUPLICATE / NEEDS A HUMAN.
"DONE (code), NEEDS A HUMAN" means the code ask is met on master and only an
owner-side acceptance (device test, UI look, product call) keeps the item open.

| Item | Status | Subject | Class | Evidence and smallest next step |
|---|---|---|---|---|
| MC-1178 | open | Rollover thrash, per-character pin | PARTLY DONE | Parts (1) and (2) are on master: `d124f33e`, `1cfb6d82`. `mc/rollover_threshold.py:56-97` resolves a per-model cost optimum and ignores a pin below fresh floor + 60k (measured: Fable 5.2, floor 90k, pin 100k gives 277,349, reason `pin_ignored_below_floor`); the roll log records character, model and floor (`mc/midturn_rollover.py:502-523`). Left: the guard is skipped whenever the model is not in `MODEL_TABLE` (BX1); part (3) Settings warning and part (4) a Settings field for `context_rollover_by_character` do not exist (no hit for the key anywhere under `static/`); the "rollover paused" chat notice was replaced by a once-per-pin log line (`rollover_threshold.py:50-53`), which the user never sees. Smallest next step: apply the floor guard on the no-table branch (`rollover_threshold.py:77-78`), then the Settings field. |
| MC-1174 | open | Stall guard outside /goal | NOT DONE | No code: no module, flag or hook names a stall guard (`grep` for `stall_guard` / `MC-1174` under `mc/`, `static/`, `server.py` finds only unrelated text). The item's own open question (where plan state lives) is a design decision, so the smallest next step is that decision, not a fix. |
| MC-1168 | in_progress | Delivery reported when nothing written | DONE | Both halves are on master: `8551e4a1`, `0c39990e`, `51d0e400`. (A) Codex follow-up now goes through `_start_prompt_delivery` and `_require_prompt_delivered` (`mc/agent_runtime.py:5509-5560`, used at `:9858-9863`), which raises `PromptDeliveryError` instead of returning. (B) Mode B revival writes through the same seam and, on failure, rejects the gate, undoes the revive and raises `ReviveDeliveryError` (`mc/blueprints/agent_routes.py:8200-8226`). Ten `tests/test_runtime_delivery_truth*.py` files plus `tests/mc1168_r12/`. The item text still says "Tobin dispatched". |
| MC-1165 | in_progress | Cold reviewer on each handoff | DONE (code), NEEDS A HUMAN | `mc/cold_review.py`, `mc/cold_review_gate.py`, `mc/cold_review_runner.py` are on master (`c9e21f9b`, `d1bba7a8` and nine earlier rounds), wired into `merge_back` through `mc/agent_worktree.py:51` and `mc/merge_review_gate.py:177,218`. Opt-in per project (`cold_review_required`, human-only in `mc/project_safety_settings.py`). No Settings control for it exists under `static/` (no hit for the key), so turning it on is a config edit. Owner: decide which projects require it. |
| MC-1164 | in_progress | Merge only when checks pass | DONE (code), NEEDS A HUMAN | `b8886776` / `646b94b5` on master. `mc/landing_gate.py:125-134` returns `None` (historical path) when `verify_command` is unset, otherwise blocks on any failure; called from `mc/agent_worktree.py:608-613`. Contract `docs/LANDING_GATE.md` exists. Off until `verify_command` is set per project, and there is no Settings field for it under `static/`. Owner: set the command on the projects that should be gated. |
| MC-1161 | in_progress | Adoption drops open agent jobs | DONE | `7b9fdbff` / `663a305d` on master. `mc/agent_shim_adopt.py:823-834` refuses a session that owns an open job (`_owns_open_job`, `:812-817`); the dialog says "running session(s) or job(s)" (`static/js/update-power.js:280`). Adoption flag is off by default, so the only visible change is the wording. Close on the UI look. |
| MC-1160 | in_progress | Restart blockers ignore agent jobs | DONE | `739cdedf` / `0427ebea` on master. Open jobs are listed as `job_running` rows (`mc/blueprints/system_routes.py:1746`) from `mc/agent_jobs.py:146,155`. Remaining step is the owner's UI test named in the item. |
| MC-1150 | in_progress | Agent runs survive server restart | PARTLY DONE | P1 to P4 are merged (`6f49582e`, `ad73ef94`, `6ca14879`, `92d4cf58`): `mc/agent_shim*.py` (6 modules), checkpoint, boot adoption, completion replay. The item text still says "NEXT: P1". The flag `agent_survive_restart_enabled` defaults to `False` (`mc/agent_shim_client.py:45`, comment: "off until P4 is live-proven"), so the user-facing ask (restart while an agent runs) is not delivered. Left: the live proof and the decision to turn the flag on. |
| MC-1149 | in_progress | Rollover kills background jobs | PARTLY DONE | Phase 1 on master (`b68e951c`): `mc/long_job_guidance.py` imported at `mc/blueprints/agent_routes.py:153`; the roll state block renders per-job fate (`mc/midturn_rollover.py:456-461`). Phase 2 (broker) is conditional by the item's own text. Correct as parked; nothing to do until a roll kills a long job again. |
| MC-1146 | in_progress | Continuity block reports stale state | DONE | `fbe08ee7` / `704542cc` on master. Origin line: `mc/session_provenance.py:44` ("user-started new chat"), called at `mc/blueprints/agent_routes.py:5489`; liveness marks: `mc/continuity_liveness.py:77` ("LOST IN RESTART" / "STATE MISMATCH" / "UNVERIFIED"); recent activity scoped at `agent_routes.py:5690`. Only the owner's fresh-chat check is left. |
| MC-1136 | in_progress | Restricted Aider adapter | DONE (code), NEEDS A HUMAN | `bd922c36` / `ead141bf` on master. `mc/aider_launch.py:11-16`: flag `aider_restricted_adapter_enabled`, default off, strict `is True`. Nothing left to build; the owner decides whether to enable it. |
| MC-1134 | open | One owner for MEMORY.md index | NOT DONE | No commit names BUILTINS-6 or MC-1134. The size half is already met by default: `index_byte_budget` defaults to 24,576 bytes (`mc/memory.py:1332`), under the 25,000 cap the item names. The ownership half (vendor auto memory and Scribe both writing one file) is untouched; ML findings in pass 3 on index writes are the same surface. Smallest next step: the design decision the plan doc asks for. |
| MC-1109 | in_progress | Keep machine awake setting | DONE (code), NEEDS A HUMAN | `9bca0fe6` / `c4e7140a` on master; `mc/wake_lock.py`, toggle at `static/js/settings-ownership.js:17`. Left per the item: an overnight check, battery, Linux hardware. |
| MC-1108 | blocked | Claydo default on fresh installs | NEEDS A HUMAN | `02bf1966` is not on master; it sits on `clayrune/agent/c20d1091c4e9`, 3 commits ahead and **1,009 commits behind** the reviewed master (measured, `git rev-list --count`). The recorded review pass and the 342-test run are for a tree that is now 1,009 commits old; they do not cover the merge result. Blocked on VM installer validation, which only the owner can unblock. Smallest next step: rebase or re-merge and re-run the suite before the VM pass, or the VM pass validates a stale tree. |
| MC-1102 | in_progress | Qwen standalone install updatable | DONE | `1b9b174c` on master; `mc/cli_install.py:20` routes Qwen through `mc/qwen_cli_install`. The item's own state line says the version target is superseded and no build is left. |
| MC-1079 | in_progress | Agents get own browser tab | DONE (code), NEEDS A HUMAN | `91b20f55` on master; `mc/browser_agent_tabs.py` (`agent-tab-*` handles). Browser pane is outside regions 1 to 4, so only the commit and module were checked. Owner UI test per `docs/BROWSER_AGENT_TABS.md`. |
| MC-1076 | in_progress | Agents cannot write safety settings | DONE | `4554673d` on master. `mc/project_safety_settings.py:16-29` lists 12 human-only keys (more than the 8 the item counts: `verify_command`, `verify_timeout_seconds`, `cold_review_required` were added since); enforced at `mc/blueprints/project_routes.py:712`. Owner UI test only. |
| MC-1071 | in_progress | Pace-aware engine routing | PARTLY DONE | `c3766e06` on master; `mc/engine_pace.py`, `mc/engine_pace_dispatch.py`, imported at `mc/blueprints/agent_routes.py:136`. The dispatch result and the agent picker carry the reroute (`static/js/conversation.js:378`). Left by the item's own words: the Floor figure does not say a run was rerouted. |
| MC-1065 | in_progress | Vault unlock button and link | DONE (code), NEEDS A HUMAN | The failed UI test the item records was answered by `30cff5cb` and `ddfcf287` (2026-10-07, both on master): the engine card re-reads itself after unlock. Link handling at `static/js/rich-text.js:237-238`, popup `static/js/vault-unlock-popup.js:3`. The item text still ends at "Fix dispatched"; the re-test was never recorded. |
| MC-1063 | in_progress | Child report waiting marker | DONE (code), NEEDS A HUMAN | `c14c4725`, `8a2fde45`, `289f3a2c` on master. Marker painted from the status poll (`static/js/conversation.js:6136`, `static/js/report-waiting.js:22-35`). Left: nobody has watched the marker appear and clear on a real busy parent. |
| MC-1046 | open | Local model engine support | OBSOLETE (as a core item) | `c072fce3` is not on master and its branch is gone; the item says "DO NOT DISPATCH", handed to an outside contributor with the brief in `docs/LOCAL_LLM_SUPPORT.md`. Nothing in the code to check. It is a parked wish, not open work; keeping it "open" inflates the list. |
| MC-1033 | in_progress | Mobile child chat hides composer | DONE (code), NEEDS A HUMAN | `5c827b69`, `36eee4f2`, `cd49a192`, `4617a407` all on master (the item says `4617a407` "remains unmerged": that is out of date). Left: a physical phone check, which only the owner can do. |
| MC-1032 | blocked | Codex resumed chat render | DONE (code), NEEDS A HUMAN | `9dc40c11`, `e7a5ded3`, `6a7cc8ce`, `0cbced3f` on master; the `[tool: N tool calls]` row is built at `mc/agent_runtime.py:9271`. "blocked" is wrong: nothing blocks it, one visual case is untested because no such chat existed on 2026-10-16. |
| MC-1030 | blocked | Passcode gate on install-launch | DONE (code), NEEDS A HUMAN | `ab3a84b7` / `5e559d45` on master; both routes exist (`mc/blueprints/agent_routes.py:3188`, `:3283`). Left: the first-run set-a-passcode branch on a fresh install (VM). Not blocked on code. |
| MC-1028 | blocked | Stop-hook collapse hides answer | DONE (code), NEEDS A HUMAN | `43e19dce` / `898c756c` on master; the collapse rule is at `static/js/rich-text.js:407` and `static/js/conversation.js:5061`. Owner UI look only. Pass 4 findings on the draft block (see its CV section) are separate defects, not this ask. |
| MC-1027 | open | Pop out is real window | DONE (code), NEEDS A HUMAN | `82fb543e` on master; `mc/blueprints/popout_routes.py:73,96`. Left: the Mac pywebview bridge on a real Mac. |
| MC-1018 | blocked | POSIX respawn leaks grandchildren | DONE | `a10b0aa1` / `e893b7c0` on master. `mc/agent_runtime.py:3689-3695` routes POSIX kills through `kill_tree`. The item itself says no separate UI test is needed, so nothing holds it open. |
| MC-1017 | blocked | Stop kills server on POSIX | DONE (code), NEEDS A HUMAN | `84d677b9` / `3f4dee41` on master. Guard at `mc/proc_kill.py:94` (never signals `os.getpgrp()`), `kill_tree` at `:142`. Left: the Linux VM Stop check. |
| MC-1016 | blocked | Linux restart inherits dead terminal | DONE (code), NEEDS A HUMAN | `a16fbb27` on master; the restart child's output handling is at `mc/blueprints/system_routes.py:1891-1919`. Note the code now discards stdout (`DEVNULL`, `:1919`) where the item text says "output to data/logs/clayrune.log": the comment at `:1891-1897` gives the reason (a second writer on the log is denied). Left: Linux install retry. |
| MC-1014 | blocked | nvm-installed CLIs not found | DONE (code), NEEDS A HUMAN | All four commits on master. `nvm_bin_dirs` is used by the five runtime fallbacks (`mc/agent_runtime.py:2155, 4061, 7166, 8474, 10127`); `augment_unix_path` runs at boot (`server.py:3621-3622`) and in the registry path merge (`mc/blueprints/agent_routes.py:2357`). Left: a real Linux run; tests only patch `sys.platform`. |
| MC-998 | in_progress | Usage breakdown dashboard, Codex sampling | DONE (code), NEEDS A HUMAN | `700d2e8`, `78d5ae9c`, `3a360feb` on master. The item's latest state line already records complete Codex ticks after the restart; what is left is more samples (time, not code) and the owner's UI acceptance. |
| MC-988 | blocked | Mobile pane stays half height | PARTLY DONE, NEEDS A HUMAN | The item's state line (2026-09-29) is out of date: `fc65e53c` (2026-10-02, on master) moved the recovery decision into the resize event itself and fixed the diagnostic so it can fire. Five MC-988 commits are on master. Whether the bug is gone can only be judged on the owner's fold phone. If it has not recurred since 2026-10-02, close. |
| MC-976 | blocked | Browser pane parity phase 2 | DONE (code), NEEDS A HUMAN | All five commits on master; all eight gaps built per the item. Browser pane is outside regions 1 to 4 (not traced). Left: upload, right-click and Ctrl+F on a real device. Not blocked on code. |
| MC-975 | blocked | Codex unattended sandbox on Windows | NEEDS A HUMAN | Part A (`2c36bfa8`, `57a10c9`) on master. Part B is an upstream runner crash; the probe kit (`2492b181`, `d0cdbbe`) is deliberately unmerged. Reopen condition is a codex-cli upgrade plus an elevated probe run, both owner-side. The item was parked at codex-cli 0.160.1; if the installed CLI is newer, the probe is due. |
| MC-964 | in_progress | Memory overhaul, retrieval failures | PARTLY DONE | Steps A to F and T1 to T5 are on master (`2d80a26d` to `684cd9f2`, `c5f4c25b`, `bf1e8bc5`). The two follow-ups the item names are both still present in code: BX2 (`mc/memory.py:5963`, the terminal habit classifier never sees a follow-up message) and BX3 (`mc/log_lines_trim.py:22-23`, any output line starting with `> ` is pinned forever). Smallest next step: `line.lstrip()` in `_habit_source_statements`, and require the user label in `is_user_line`. Related: R26 (pass 2), the trim runs in one reader only. |
| MC-944 | open | Memory V2 spec, ten steps | PARTLY DONE (tracked twice) | Steps 1 to 4 (`58fe56b`, `69faf88`), 6 (`dcd7685d`, `5eecaeb7`), 7 (`d858a1fd`, `0a421134`), 8 (`64b300c9` to `fd462034`) and 10 (`6b7e6471`) have commits on master. No commit names step 9 (measure each gate's false-positive rate), and every gate still ships in report mode, so nothing the spec added blocks anything yet. The item says its remainder is driven from MC-964 Step F: one of the two should carry the open work, not both. |
| MC-916 | open | Provenance checking for memory writes | NOT DONE | Spec only (`docs/MEMORY_PROVENANCE_SPEC.md`, `b5f5d954` on master). No memory module implements it. The item names its own next step (a graded classifier sample before code). Pass 3's ML findings on unauthenticated memory writes are the defect this item would close. |
| MC-903 | open | Named agent that gets wiser | PARTLY DONE | Phases 1 to 4: all seven named commits on master. Phase 5 (episodic search) has no code: no file under `mc/` mentions it. The item notes the deferral it depended on now has its telemetry, so the next step is a decision to build or drop, not a fix. |
| MC-895 | open | Agent types with per-type model | PARTLY DONE | Phase 1: four commits on master. Phases 2 to 5 are unbuilt: no binding module or `{project, trigger}` map exists under `mc/` (grep for binding names finds nothing). Phase 4 is MC-885 and phase 5 is MC-868, so this item's own remainder is phases 2 and 3 only. |
| MC-892 | open | Memory index has no remover | PARTLY DONE | Item 3 on master (`adcae27b`, `add08294`): `mc/memory.py:4418-4423`, the condense trigger sits 1,024 bytes under the floor. Items 1 and 2 (eviction with a per-line delivery gate) have no commit and by the owner's ruling need a written plan first. The item's first line ("Remaining = whatever UI test the state line names") contradicts its second ("No UI test"). |
| MC-890 | blocked | Distiller auto mode, rollback first | NEEDS A HUMAN | Step 0 on master (`97a4639`). The item is blocked on five owner decisions (D-A1 to D-A5), not on code. Pass 3 findings on the distiller queue are relevant input to those decisions, in particular anything on the promote path. |
| MC-885 | open | Completion drive, bounded autonomy | PARTLY DONE | The prompt half shipped: `mc/behavior_tail.py:72` ("Your task is a GOAL, not a topic ..."), re-stated every turn. No mechanism outside the model checks completion, which is exactly MC-1174. These two and MC-895 phase 4 describe one feature from three sides; one item should own it. |
| MC-869 | open | Revisit MCP fleet efficiency | NEEDS A HUMAN | The trim path exists (`mc/agent_runtime.py:2234-2238`). What the item asks for is a re-measurement on live trading sessions and a rollout decision; neither is a code change. Open since 2026-06-03 with "soon" in its text. |
| MC-868 | open | Per-turn model switching | NOT DONE | No code. Depends on an auto-router that has since shipped, so its stated blocker is gone; whether it is still wanted is a product call. Duplicate in substance of MC-895 phase 5. |
| MC-713 | in_progress | In-app installed app update | DONE (code), NEEDS A HUMAN | All five commits on master. `static/js/installed-app-update.js:1-16` pauses agent streams around `update()` (`static/js/agent-stream-pause.js:13`), the fix for the 2026-10-16 hang. Left: a real installed desktop and phone app, owner only. |
| MC-1045 | blocked | Empty turn reads as completed | DONE (code), NEEDS A HUMAN | `82cc17ab` / `296d8884` on master. `mc/empty_turn.py` is called from the readers (`mc/agent_runtime.py:4972-4975`, `:5216`, `:6499-6505`). Left: a live run that ends with no reply. Not blocked on code. |

**Desk items (17).** The Desk backend belongs to passes 5 and 6; these rows
record only what a commit check and a look at the Desk files under `static/`
show, so that an item whose text is behind the code is not missed. They are
not a feature trace of the Desk.

| Item | Status | Subject | Class | Evidence |
|---|---|---|---|---|
| MC-1175 | in_progress | Posy cannot see scene pictures | DONE (code), text stale | `9d78f64c` (2026-11-12) on master; the item still says "Tobin dispatched". Needs the owner's look in Studio. |
| MC-1118 | in_progress | Connections one vocabulary | DONE (code), text stale | `f1a8f093` (2026-10-28, "fixes and complete validation results") on master; copy lives in `static/js/desk-v1-connect-copy.js`. The item still says "Sol_Tobin dispatched". |
| MC-1116 | in_progress | Re-approving widened campaign | DONE (code), NEEDS A HUMAN | `ffe41be5` on master. Owner UI check named in the item. |
| MC-1114 | in_progress | Readiness ignores post permission | DONE (code), text stale | Six commits name it, latest `f96028e2` (2026-10-28) and `09daa80e`, all on master. The item carries no state line at all. |
| MC-1113 | in_progress | Setup fields from the vault | DONE (code), text stale | `3b287cfe`, `c97f9139` (2026-10-26) on master; picker is `static/js/desk-v1-connect-vault-picker.js`. No state line on the item. |
| MC-1112 | in_progress | Campaign What and Where fixes | PARTLY DONE | `b179518b` and `2b0fc24d` on master cover lifecycle (delete, archive, undo: `static/js/desk-v1-what.js:257-277`) and item 9. Item 3 (media inside the article body) was not found in those two commits; passes 5 and 6 should confirm. |
| MC-1074, MC-1070, MC-1068, MC-1067, MC-1064 | in_progress | Check connection; scene subset render; async render; model discovery; spending guard | DONE (code), NEEDS A HUMAN | Every named commit is on master (`8fda9f61`, `4166b3f7`, `a405a363`, `c412b52d`, `1704183e`, `6f6631d8`). Each waits only on the owner's UI script. MC-1067 additionally has an unexplained 21-second quote (measurement, not code, per the item). |
| MC-1054 | blocked | MCP config and package drift | DONE (code), NEEDS A HUMAN | Part 2 `a46b73c0` on master. Part 1 no longer waits on a human: the fence covers `~/.claude.json` and `.mcp.json` since `c04f0bc9` (2026-10-12; `steward/fence.py:3118-3131`). "blocked" is stale. |
| MC-1052 | blocked | One passcode prompt on Details | DONE (code), NEEDS A HUMAN | `1fb20c81` on master. Not blocked; one UI test. |
| MC-1050, MC-1048 | in_progress | Pane digest reader; custom MCP connect | DONE (code), NEEDS A HUMAN | `01d0f983`, `86ef7dea`, `e41c0b32`, `6f1a474a`, `f63d3548` on master. Left: human-only install and a live read. |
| MC-977 | blocked | Desk IA revision 2 | PARTLY DONE | 184 commits name it. Everything but R2-12 is built per the item, and R2-12 is deferred by the owner. "blocked" describes a deferral, not a blocker. |
| MC-968 | open | Per-campaign autonomous publishing | PARTLY DONE | Steps 0 and 2 to 7 on master per the item; not re-verified here. |

**Out of region, not classified (23):** MC-1176 (steward on agent-mail),
MC-1171 (masked test verdicts, deferred), MC-1153, MC-1152, MC-1151, MC-1119,
MC-1110 (Desk channels and phone lane), MC-1140, MC-1132 (steward role,
hivemind roster), MC-1105 (vault entry type), MC-1005, MC-1004 (event
triggers, per-action rules), MC-999, MC-960, MC-948, MC-936, MC-920, MC-919,
MC-879, MC-878, MC-875, MC-735, MC-727 (feedback worker, dev harness, tunnel,
repo transfer, webhooks, vendor hedge, remote access, mobile APK).

### What the table says about the backlog itself

Of the 46 in-region items, **26 have their code ask met on master** (7 DONE
outright, 19 open only for an owner-side acceptance). Counted another way: 13
of the 46 are
marked `blocked`, and for 10 of those nothing is blocked; the item is waiting
for one person to look at a screen or a device. The `blocked` status has come
to mean "waiting on the owner", which hides the three that really are stuck
(MC-1108 on VM access, MC-975 on an upstream crash, MC-890 on five decisions).

Stale text is the second pattern. Nine items say work is "dispatched" or
"NEXT: P1" while the commits are already on master (MC-1168, MC-1150,
MC-1065, MC-1033, MC-988, MC-1175, MC-1118, MC-1114, MC-1113). An agent that
reads the item and not the log will re-dispatch finished work.

Three items describe one feature (MC-885, MC-1174, MC-895 phase 4) and two
pairs track the same remainder (MC-944 with MC-964 Step F; MC-868 with MC-895
phase 5).
<!-- BACKLOG-ROWS-END -->

## Streamlining

(in progress)

## Detail

### BX1 — rollover pin floor guard is skipped for any model outside the Claude table (medium, lens 2)

`mc/rollover_threshold.py:77-78`. When `inputs_for_session()` returns `None`
the function returns `max(MIN_THRESHOLD, pin)` and never compares the pin with
the fresh floor. `mc/rollover_model_table.py:72-78` recognises only canonical
Claude ids (optional date suffix and `[1m]`), so an alias or a model newer than
the table falls through.

Measured (pure function, scratch script): pin 100,000, fresh floor 90,000:
`claude-fable-5-2` gives 277,349 (`pin_ignored_below_floor`); `claude-opus-5-6`,
`claude-unknown-9`, `opus`, `''` and a non-Claude id all give 100,000 (`pin`).
With `midturn_rollover_enabled` on, a Claude session whose observed model is
one release newer than the table re-rolls every `MIN_GROWTH_TOKENS` (20k), the
exact thrash MC-1178 was opened for. Non-Claude providers are not exposed to
the mid-turn loop (`mc/midturn_rollover.py:319` returns early) but still roll
at every follow-up above the pin.

Smallest fix: when `floor` is known and `pin < floor + MIN_THRESHOLD`, ignore
the pin on the no-table branch too. Test: `tests/` has cases for the table
models only; none passes a model missing from the table together with a floor
(read, by grep for `pin_ignored_below_floor`; not exhaustive).

An existing test pins the current behaviour
(`tests/test_rollover_threshold.py:131`,
`test_unknown_model_or_floor_preserves_legacy_pin`), so the legacy pin for an
unknown model is a recorded choice, not an oversight. The consequence above
still follows from it; the fix has to change that test.

### BX2 — the terminal habit classifier never sees a follow-up message (high, lens 2)

`mc/memory.py:5962-5964`. `_habit_source_statements` keeps a line only when
`line.startswith('> ')`. The first message of a chat is stored in that shape
(`mc/blueprints/agent_routes.py:10893`, `:12978`), but every later message is
stored as `"\n> {label}: {message}\n"` (`agent_routes.py:13883`, `:14581`,
`:14745`, `:14815`, `:14955`, and the revive seed at `:8010`). A string that
begins with a newline does not start with `"> "` (measured: `False`), so
`_scribe_classify_habits` (`memory.py:6179`), the only path that mints a habit
note, classifies the opening message of a chat and nothing after it. A
standing habit stated in message two of a long chat, which is the MC-964
incident shape, is never minted.

The send-time observer (`agent_routes.py:7471`, `mc/habit_report.py`) does see
follow-ups, but it is report mode: it writes a candidate row and mints
nothing.

`mc/log_lines_trim.py:22-23` handles the same two shapes correctly with
`line.lstrip()`, and its docstring names both, so the two readers of one
convention disagree.

Smallest fix: `s = line.lstrip()` before the prefix test in
`_habit_source_statements`. Test: `tests/test_habit_classifier.py:115-123`
feeds only the `"> Ron: ..."` shape, so it passes on the defect. MC-964's
journal lists this as an open follow-up; it is still open on master.

### BX3 — the chat-buffer trim pins every line that starts with "> " (low, lens 1)

`mc/log_lines_trim.py:22-23`, `:35`. `is_user_line` is true for any string
whose stripped text starts with `"> "`. Agent output that opens with a
markdown blockquote matches, so those lines are counted as the human's and
survive every trim (`pinned`, `:35`). On a long chat with an agent that quotes
often, the buffer the cap exists to bound grows without limit, and the quoted
lines are also offered to nothing useful: the classifier filters them out by
label. Read, not measured.

Smallest fix: pin only lines whose text after `"> "` (and an optional
`"[queued] "`) starts with the configured user label. Test: none covers an
agent blockquote line. Other half of R26 (pass 2): R26 is the trim missing
from most readers; BX3 is the trim keeping too much where it does run.

<!-- DETAIL-END -->

### Commit ancestry (measured)

Every commit a picked backlog item names was tested against the reviewed
`master`. All are ancestors except: MC-975 `2492b181`, `d0cdbbe` (not
ancestors), MC-1046 `c072fce3` (not an ancestor; the item says the branch is
gone), MC-1108 `02bf1966` (not an ancestor; still on its agent branch), MC-1160
`af6cc23f` (object missing from this clone).

## Coverage

(written last)
