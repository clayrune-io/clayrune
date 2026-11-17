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
