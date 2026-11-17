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

(in progress)

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
| MC-1178 | open | Rollover thrash, per-character pin | PARTLY DONE | Parts (1) and (2) are on master: `d124f33e`, `1cfb6d82`. `mc/rollover_threshold.py:57-96` resolves a per-model cost optimum and ignores a pin below fresh floor + 60k (measured: Fable 5.2, floor 90k, pin 100k gives 277,349, reason `pin_ignored_below_floor`); the roll log records character, model and floor (`mc/midturn_rollover.py:502-523`). Left: the guard is skipped whenever the model is not in `MODEL_TABLE` (BX1); part (3) Settings warning and part (4) a Settings field for `context_rollover_by_character` do not exist (no hit for the key anywhere under `static/`); the "rollover paused" chat notice was replaced by a once-per-pin log line (`rollover_threshold.py:52-54`), which the user never sees. Smallest next step: apply the floor guard on the no-table branch (`rollover_threshold.py:76-77`), then the Settings field. |
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
<!-- BACKLOG-ROWS-END -->

## Streamlining

(in progress)

## Detail

### BX1 — rollover pin floor guard is skipped for any model outside the Claude table (medium, lens 2)

`mc/rollover_threshold.py:76-77`. When `inputs_for_session()` returns `None`
the function returns `max(MIN_THRESHOLD, pin)` and never compares the pin with
the fresh floor. `mc/rollover_model_table.py:70-76` recognises only canonical
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

<!-- DETAIL-END -->

### Commit ancestry (measured)

Every commit a picked backlog item names was tested against the reviewed
`master`. All are ancestors except: MC-975 `2492b181`, `d0cdbbe` (not
ancestors), MC-1046 `c072fce3` (not an ancestor; the item says the branch is
gone), MC-1108 `02bf1966` (not an ancestor; still on its agent branch), MC-1160
`af6cc23f` (object missing from this clone).

## Coverage

(written last)
