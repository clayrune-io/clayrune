# Code efficiency review — 2026-09-10

Reviewer: Fenn (code-reviewer), single sequential pass. Base: master @ 8b86093 (includes 9270d9d, the API pointer card).
Every number below was measured on this box against the real `mission_control` project on 2026-09-10, not estimated. Every line cited was opened. Nothing was fixed.
Written incrementally — a stop at any point leaves what is below verified.

Severity is about what it costs Ron: **blocker** = money or latency paid on every dispatch/turn with no reader; **high** = paid on every poll or dispatch, fixable in one place; **medium** = real but narrower trigger; **low** = hygiene.

The security pass (`2026-09-10_security.md`) is not repeated here.

## Ranked summary

| Rank | Finding | Severity |
|---|---|---|
| 1 | E1 — per-dispatch fixed floor is still 43.9 KB from `_build_agent_context` + 50.5 KB the CLI loads natively (CLAUDE.md 20.9 KB, MEMORY.md 28.8 KB) = ~94 KB (~24k tokens) before the task; the SYSTEM block (11.1 KB) is the largest MC-owned slice and ~2.7 KB of it duplicates CLAUDE.md's browser section | HIGH |
| 2 | E2 — `mission_control.json` is 2.3 MB (2.0 MB is 946 backlog items, 912 closed) and is parsed 11 ms per `load_project`, 17 ms per `load_projects`; `/api/floor` parses it 2 + once-per-live-figure times per 5 s poll (307 ms live), `/agent/status` once per 4–5 s per open modal, and every activity-log line rewrites it whole (3 rewrites per dispatch) | HIGH |
| 3 | E3 — `_load_agent_log` has no cache: 1.66 MB / 8.5 ms per call, 3 parse+write cycles per dispatch and 1 per csid backfill; `/api/session/trigger-type` and `/api/system/status` read every project's log (7.4 MB) per call | MEDIUM |
| 4 | E4 — `ClaudeRuntime.list_sessions` cache is keyed on (mtime,size) so the live session's transcript (up to 81 MB here) is re-parsed line-by-line on every call while it is running | MEDIUM |
| 5 | F1 — `/agent/status` ships every session's full `log_lines` buffer (545 KB live) on every 4–5 s poll per open modal; pollers read only status/usage | MEDIUM |
| 6 | E6 — `import server` autostarts a cloudflared connector and reaps the ledger; a scratch import during this review left connector PID 24068 on Ron's tunnel (see §3) | MEDIUM |
| 7 | E5 — `/api/floor` re-scans every character `.md` under `~/.claude/agents` and every project's `.claude/agents` per 5 s poll while the Floor is open (20 `rglob` walks, 12 ms) | LOW |
| 8 | F2 — the dashboard grid and list are `innerHTML`-rewritten and re-listened every 30 s regardless of change | LOW |
| — | Already efficient (verified): `/api/projects` strips backlog bodies; `_context_fingerprint` is stat-only; `memory_turn` is byte-budgeted (3,200 B); beacon SSE diffs before emitting; agent-log capped at 500 rows | — |

---

## 1. Token cost per prompt and per dispatch

### Method
`_scratch/eff_ctx.py` imports `server` (the same wiring `tests/test_agent_context_api_card.py` uses), loads the real `data/projects/mission_control.json`, and calls `_build_agent_context(project, task=<this review's task>, character_body=<code-reviewer.md body>, character_name='Fenn', session_id=<this session>, source='agent')`. Output split on the `\n\n` part boundaries and measured in UTF-8 bytes.

### E1 · HIGH — the fixed floor after 9270d9d, block by block

Total `_build_agent_context` output for this dispatch: **43,862 bytes**. Bare (no task, no persona, session_id set): **35,227 bytes** — that is the part every respawn and every non-Claude follow-up re-pays.

| Bytes | Block | Task-dependent? | Notes |
|---|---|---|---|
| 11,108 | `--- SYSTEM ---` | no | see breakdown below |
| 8,556 | `--- SHARED_RULES.md ---` | no | read verbatim, `agent_routes.py:2615` |
| 5,023 | `--- AGENT_RULES.md ---` | no | read verbatim, `:2612` |
| 3,043 | `--- THE ROSTER ---` | no | 10 characters + a 1.5 KB fixed trailer explaining Task-vs-dispatch |
| 2,915 | `--- CHARACTER ---` | per persona | body + a 700 B fixed trailer |
| 2,852 | `--- RELEVANT MEMORY ---` | yes | read floor, topk=6 |
| 2,220 | `--- STANDING POSITIONS ---` | yes | |
| 1,312 | `--- RECORDING A POSITION ---` | no | directive, `mc/memory.py` render_position_capture |
| 1,272 | `--- WHAT A DELEGATION COSTS ---` | no | `:2916-2945` |
| 1,085 | `--- CONTINUITY ---` | no | |
| 1,085 | `--- CLAYRUNE API (pointer card) ---` | no | post-9270d9d |
| 846 | Recent conversations | no | |
| 738 | `--- REPLY SHAPE ---` (behavior tail) | no | `mc/behavior_tail.py`, also re-sent per turn |
| 542 | `--- SIBLING ACTIVITY ---` | yes | |
| 528 | "You appear on the Floor…" | no | |
| 337 | Recent activity | no | |
| 360 | name / user / project / root / current task lines | no | |

**Task-independent bulk: ~35.2 KB of the 43.9 KB (80%).** The task-scoped retrieval (memory, positions, siblings, explorations) is 5.6 KB and is the part that earns its keep.

**What the CLI loads on top, natively, per session** (not in `_build_agent_context`, but paid on the same dispatch):

| Bytes | File |
|---|---|
| 28,828 | `~/.claude/projects/<mc>/memory/MEMORY.md` — **over its own 24 KB budget by 4.8 KB** (`index_byte_budget`). Reported only; standing position governs eviction. |
| 20,881 | `mission-control/CLAUDE.md` (copied into each worktree as project instructions) |
| 820 | `~/.claude/CLAUDE.md` |

So the MC-controlled + MC-authored fixed floor is **~94 KB ≈ 23–24k tokens per dispatch before the task line**, of which ~44 KB is `_build_agent_context`. (The CLI's own tool schemas, the ~40-entry skill listing and MCP tool descriptions are on top of that and outside this review's scope.)

#### E1a · `--- SYSTEM ---` (11,108 B) — the largest MC-owned slice, and where the remaining pointer-card wins are

Line-by-line (bytes):

| B | Line (`_clayrune_universal_capabilities`, `agent_routes.py:2130` unless noted) |
|---|---|
| 931 | Browser: `/api/browser/read` paragraph |
| 781 | Management surfaces (MCP/Skills/Scheduler/Settings/Memory → "use the UI") |
| 715 | Files: `[file:…]` marker |
| 693 | Images: absolute-path rendering |
| 683 | MANDATORY process registration (`:2660`) |
| 617 | Plans: no EnterPlanMode |
| 604 | Backlog paragraph (`:2673`) |
| 604 | Scheduler: LOCAL scheduler bullet |
| 558 | Browser: intro |
| 522 | Hivemind paragraph (`:2681`) |
| 520 | Browser: drive-it-yourself bullet |
| 514 | Browser: profiles bullet |
| 511 | Diagrams (mermaid) |
| 469 | Questions: mc:question protocol (2 lines, 469+195+139) |
| 406 | API discovery |
| 317 | Project memory / mc-memory-search |
| 271 | Scheduler: /schedule skill bullet |
| 179 | Scheduler rule of thumb |
| 215+154+105+159 | Memory / Archive / Rules / Terminal path lines |

**Duplication, measured:** the five Browser lines total **2,656 B** and restate `CLAUDE.md` §"Browser pane — name a profile when you log in" (which the CLI already loads for this project, 3,947 B for that section) — profiles, adopt-on-reuse, `DELETE` is sign-out, `/api/browser/read` envelope, all said twice per dispatch. The Backlog paragraph (604 B) and Hivemind paragraph (522 B) restate endpoints that the pointer card (1,085 B) already says to Read the reference for; and the Hivemind line tells every agent, including a code reviewer, how to create a hivemind. The Scheduler trio (1,054 B) is likewise an on-demand topic.

**Cost:** ~5.9 KB (browser 2.7 + backlog/hivemind 1.1 + scheduler 1.1 + management-surfaces 0.8 + diagrams 0.5) of SYSTEM is either duplicated in CLAUDE.md or reference material that could sit behind the same pointer card 9270d9d introduced. ≈ 1,500 tokens per dispatch.
**Trigger:** every dispatch, every respawn, every non-Claude follow-up turn (`_build_agent_context` is rebuilt per turn for non-Claude, `:2135` comment).
**Fix:** fold Browser/Backlog/Hivemind/Scheduler/Diagrams/Management into `data/agent_reference/CLAYRUNE_API.md` sections and leave one-line pointers in `_clayrune_universal_capabilities`, exactly the shape 9270d9d used. Keep the four MANDATORY/behavioural ones (process registration, plans, questions, images) inline — they change what the agent does, not what it looks up.

#### E1b · Roster + delegation-cost + character trailers: 3.5 KB of fixed prose per dispatch

`_roster_block` (`:2460-2496`) appends a **1,520 B** fixed trailer (Task-tool vs dispatch, `notify_session`) after the ~10-line roster; `--- WHAT A DELEGATION COSTS ---` (`:2916-2945`) is another **1,272 B**; the CHARACTER trailer (`:2625-2637`) is **~700 B**. All three are constant text. They are delivered to every session with a `session_id`, including hired specialists (a reviewer, a UI fixer) whose task never delegates.
**Cost:** ~3.5 KB / ~870 tokens per dispatch.
**Fix:** one shared "delegation" paragraph (~600 B) instead of two, and skip the roster+delegation blocks when `source='agent'` and the character declares no delegation (the delegated worker is the one least likely to fan out and the one Ron's burn-rate rule most wants not to).

#### E1c · SHARED_RULES.md opens with 705 B explaining what is no longer in it

`data/SHARED_RULES.md` line 1 (705 B) says the reply-shape rule moved to `behavior_tail.py` on 2026-09-07 and "do not restate them here". That paragraph is itself restated in every prompt, on every project. Operator data, so not a code fix — reported so Ron can trim it in the Rules editor.

#### E1d · The 738 B behavior tail — by design, no finding
`_build_agent_context` appends `_TAIL_TEXT` as the last part (`:2965`) for the first turn; later Mode-B turns get it via stdin (`:6853`, `:6971`). One copy per turn, which is the stated design (attention distance). Listed so nobody re-derives it.

#### E1e · MEMORY.md is 28.8 KB against a 24 KB budget
Reported, not proposed: `index_byte_budget` is 24 KB by choice (`discovery_index_byte_cap_curated_bloat`), the file is 28,828 B today. The Session Log section alone is ~3.9 KB of five 2026-09-10 entries. The standing position governs eviction; the number is here so the position's "17.2 KB today" reason is known to be stale.

#### Per-turn cost (Claude Mode B)
Per user turn the server prepends the memory-turn block (hard budget `memory_turn_budget_bytes`=3,200 B, `mc/memory_turn.py:69,96`) plus the 738 B tail. That is bounded and task-scoped — **efficient as designed**. The `_memory_search` it runs each turn is measured in §2.

#### Hivemind workers (`_hm_build_worker_context`, `hivemind_routes.py:819-926`)
Task-scoped blocks are capped (`handoff[:4000]`, `ctx[:4000]`, findings 20×~250, bus 15×~250, decisions 10×~250 ≈ ≤ 19 KB worst case) and then `_clayrune_universal_capabilities` (≈ 9.5 KB of the 11.1 KB SYSTEM above) + the pointer card are appended. A worker that posts findings to a bus gets the browser-pane, mermaid, scheduler and hivemind-creation paragraphs. Same fix as E1a; ~2k tokens per worker spawn.

---

## 2. Repeated work at runtime

### Method
`_scratch/eff_load.py` times the exact parse `load_projects()` does (every non-sidecar `*.json` under the real `data/projects/`). Live endpoint latencies are `curl -w %{time_total}` against the running server on this box (single sample each, idle server, 13 live sessions).

| Measured | Value |
|---|---|
| Project record files parsed by `load_projects()` | 19 files, 3,336,061 B |
| `load_projects()`-equivalent parse | **17.4 ms** |
| `load_project('mission_control')` | **11.0 ms** (file is 2,307,746 B) |
| What makes `mission_control.json` 2.3 MB | `backlog`: 2,003,996 B — 946 items, 3,806 notes, **912 of 946 closed** |
| `_load_agent_log('mission_control')` | 8.5 ms (1,662,367 B, 500 rows = 3.3 KB/row) |
| All `*_agent_log.json` | 19 files, 6.83 MB |
| `_memory_search` (115 files, 1.36 MB) | 26 ms |
| `memory_turn.refresh_for_turn` | 20 ms, 3,100 B delivered of 3,200 B budget |
| `_context_fingerprint` | 1.7 ms (stat-only, as its docstring claims) |
| `list_sessions(limit=5)` warm | 9 ms |
| `list_sessions(limit=200)` cold | **998 ms, 359.6 MB parsed** |

### E2 · HIGH — `mission_control.json` is a 2.3 MB file on hot paths, and 87% of it is closed backlog

`load_project` / `load_projects` (`project_routes.py:126-131,172`) do a full `json.loads` of every record every call. There is no cache and no mtime check. The dominant record is `mission_control.json` at 2.3 MB, of which 2.0 MB is 946 backlog items, 912 of them `done`/`wontdo`, each carrying its full note history (3,806 notes).

Hot callers, verified:

| Caller | Cadence | Cost |
|---|---|---|
| `floor_routes.py:416` `load_projects()` and `:502` again, **plus `:340` once per live figure** (`_figure_subagents` calls `load_projects()` to look up one `project_path`) | every 5 s while the Floor is open (`floor.js:570`) | measured **307 ms per `/api/floor`** with 13 live sessions ≈ 15 × 17 ms; the roster scan (`list_characters` × 20) is only 12 ms of it |
| `agent_routes.py:7703` `load_project(project_id)` in `/agent/status` | every 4 s per open modal with a running helper (`conversation.js:3771`), every 5 s per open modal with no SSE attached (`index.html:3360` → `_reconcileAgentBuffer` → status), every 15 s per running session (`index.html:3283-3338`) | 11 ms per call, for `agent_model` + `pinned_conversations` |
| `project_routes.py:305-311` `_log_agent_activity` = `load_project` + `json.dumps(indent=2)` + rewrite | **3 times per dispatch** (`agent_worktree.py:294` "worktree created", `agent_routes.py:878` "isolated in worktree", `:6099` "Agent dispatched") and once per follow-up / stop / interrupt (23 sites in `agent_routes.py`) | 3 × (parse 2.3 MB + serialise + write 2.3 MB) to append one 100-byte line to a 20-entry list |
| `beacon/aggregator.py:129` `load_projects()` | every 3 s per connected beacon SSE client (`beacon_routes.py:57-68`) | 17 ms per tick |
| `/api/projects` | every 30 s per tab (`index.html:2969`) | 27 ms live; already strips the backlog to counts |

**Failure scenario:** open the Floor and one project modal with a running helper on this box → `mission_control.json` is parsed ~18 times per 5 s (≈ 40 MB/s of JSON decode on the server thread) to answer questions whose inputs (project_path, agent_model, backlog counts) change a few times a day.
**Fix (one place):** an mtime+size-keyed cache in `load_project`/`load_projects` (the same shape `_SESSION_ROW_CACHE` already uses for transcripts, `agent_runtime.py:1428-1436`); `save_project` invalidates. Separately, `_figure_subagents` should take the project map its caller already built (`floor_routes.py:416-419`) instead of reloading everything per figure. The 912 closed items with full notes are a data-shape problem the note-cap journal export already addressed for live items; archiving closed items' `notes` out of the record would cut the file by roughly 1.9 MB.

### E3 · MEDIUM — the agent log is parsed and rewritten whole, three times per dispatch, and globbed across all projects for lookups

`_load_agent_log` (`agent_routes.py:3791`) has no cache; `_save_agent_log` (`:3840`) rewrites the full 1.66 MB with `indent=2`. Per dispatch: `_log_agent_dispatch_pending` (`:4403-4419`, parse + write), `_note_claude_sid` (`:4340-4345`, parse + write when the csid lands), `_log_agent_completion` (`:4662-4676`, parse + write), and `_prior_character` (`:5419`) parses it again on every resume to find one row. `_build_agent_context` (`:2883`) parses it a fifth time per dispatch to label the "Recent conversations" rows.

Cross-project globs: `/api/session/trigger-type` (`:3830-3837`) reads **every** project's log (19 files, 6.83 MB) when the session is not live — this is the endpoint `with-secret.py` calls per credential use; `_mc_usage_from_agent_logs` (`system_routes.py:474-516`) does the same 6.83 MB read per `/api/system/usage` (lazy, Usage-tab only — fine).
**Fix:** same mtime cache as E2; and in `_log_agent_completion` do the pending→completed update in one read-modify-write. Rows are 3.3 KB because each carries `summary[:1000]` + `task[:300]` + usage; the 500-row cap keeps this from growing, so this stays MEDIUM.

### E4 · MEDIUM — the live transcript is re-parsed in full every time it is listed

`ClaudeRuntime.list_sessions` (`agent_runtime.py:1428-1436`) caches rows keyed on `(mtime, size)`. Correct for append-only files, but the file that is *always* in the top 5 is the running session's, and its key changes every turn, so the whole file is re-read and every line JSON-parsed. Transcripts here: 316 files / 473 MB in the project dir, 125 / 251 MB in worktree dirs, **largest 81.4 MB**, median 223 KB. Warm `limit=5` took 9 ms with the top files at 1.1/0.9/7.7/1.8/10.4 MB; a turn later the 10.4 MB one is re-parsed in full.

Triggers: `_build_agent_context` (`agent_routes.py:2868`) on every dispatch/respawn; `/conversations` on first modal open (client caches, `conversation.js:879-888`) and on resume actions (`resume-preview.js:233,920,948`); the 998 ms / 360 MB cold pass at startup (`server.py:995-999`, `agent_log_backfill_max_per_project=200`) is paid once per restart per project.
**Fix:** the scanner only needs `first_user`, `last_user`, `turns`; keep the cached row and continue parsing from the previously seen byte offset (`seek(prev_size)`) when `size > prev_size` and `mtime` advanced — append-only makes that safe, and it turns an 81 MB re-read into a tail read.

### Already efficient (verified, not inferred)
- `/api/projects` (`project_routes.py:320-360`) strips backlog and social bodies to counts; 83 KB live.
- `_context_fingerprint` is stat-only, 1.7 ms.
- `memory_turn` is hard-budgeted at 3,200 B and cost 20 ms per turn; `_memory_search` is 26 ms over 115 files — no finding.
- Beacon SSE diffs the digest before emitting (`beacon_routes.py:62-66`).
- `_backfill_agent_log_from_transcripts` skips csids already logged.

---

## 3. Wasted I/O and process cost

### E5 · LOW — `/api/floor` re-walks every character directory per poll
`floor_routes.py:499-508` calls `list_characters()` once globally and once per project with a path (19 here), each an `rglob('*.md')` + `read_text` per file (`characters.py:227-240`). Measured 12.3 ms for the whole set — small, but it is a 20-directory walk every 5 s that changes only when someone saves a character. A stat-keyed cache on `_scan_dir` removes it. LOW because the measured cost is one-tenth of E2's share of the same request.

### Subprocess spawns — none on a polled path
Grepped every polled route: `subprocess` appears only in `generate_project_summary` (`project_routes.py:549`, user action), `system_status_refresh` (`system_routes.py:726`, explicit refresh), and `_git` (`:1102`, update flow). `/api/system/status` serialises an in-memory dict (`:464-471`, 1.6 ms live). No subprocess in a loop was found. **This area is clean.**

### Whole-file transcript reads that are fine
`_extract_transcript_telemetry` (`memory.py:2828-2840`) streams the file line by line once at completion (`agent_routes.py:4599`) and once per un-scribed row at startup (`server.py:1223`). `_session_too_large` (`memory.py:143`) is a stat. No finding.

### E6 · MEDIUM (process hygiene, found by accident) — importing `server` starts a cloudflared connector
`mc_remote/__init__.py:59-77` runs `_maybe_register()` at import, which spawns a daemon thread that calls `tunnel_supervisor.maybe_start()` → `CloudflaredProcess.start()` whenever an enrollment exists under the data dir. `start()` first runs `reap_orphans()` (`cloudflared.py:318-335`), which kills **every PID in the ledger** except its own (`keep_pid=None` from this path), then spawns a new connector.

**Observed on this box during this review:** the measurement harness (`_scratch/eff_ls.py`, `import server` against the real data dir — exactly what `tests/test_agent_context_api_card.py:42` does, but without conftest's `MC_DATA_DIR` isolation) printed `reaped 1 orphaned cloudflared connector(s)` and left `cloudflared.exe` **PID 24068** (created 13:21:47 local, parent .venv python 40772, since exited) registered on the `ronl.clayrune.io` tunnel alongside the server's own connector (PID 52120, parent 45032, the live `server.py`). The ledger `~/.clayrune/cloudflared_pids.json` is now `[]`, so no future reap will find 24068. The tunnel reports `online: true`; the server's own connector was not killed. I did not kill 24068: the process-hygiene rule is kill only by a PID you got back at spawn, and I did not.

**Cost:** any script or test that imports `server` outside conftest's isolation spends ~1 s spawning a connector, may kill a sibling server's connector, and leaks one per run. **Fix:** gate `_maybe_register()`'s autostart on an explicit opt-in the real server sets (`server.py` main, or an env such as `MC_REMOTE_AUTOSTART=1`), never on import; and have `reap_orphans` record the new PID before returning so a leaked connector stays reapable.

---

## 4. Frontend: work per poll

### F1 · MEDIUM — `/agent/status` ships every session's full output buffer on every poll
Live payload: **544,985 B** for 13 sessions; `log_lines` is 535,965 B of it, 234,160 B for the largest single session (`agent_routes.py:7719`, filtered but unsliced; the buffer itself is capped at 1,500 lines per session, `:3636`). Polled by `_subagentPollStart` every 4 s (`conversation.js:3771`), by the freshness tick every 5 s for each open modal's active tab when no SSE is attached (`index.html:3360-3400` → `resume-preview.js:461`), and by the dashboard loop every 15 s per running session (`index.html:3283-3338`). All three read only `status`/`usage`/`active_subagents`; the lines arrive over SSE (`resume-preview.js:619`) and via `_reconcileAgentBuffer`'s cursor.
**Cost:** ~0.5 MB every 4–5 s per open modal on this box (≈ 6–8 MB/min of JSON to build, send, and parse on the client; 43 ms server side per call).
**Fix:** accept `?since=<cursor>` (the SSE endpoint already has `since`, `:6384`) or `?lines=0` from the status pollers; `_reconcileAgentBuffer` is the only caller that wants lines and it knows its cursor.

### F2 · LOW — the dashboard grid and list are rewritten wholesale every 30 s
`fetchProjects` (`index.html:1214-1226`) → `render()` (`:1276`) runs `renderProjects` (`col.innerHTML = slots.map(...)`, `:1445`) and `renderListView` (`render-core.js:984`, then `addEventListener` per row, `:987`) with no change check against the previous payload. The modal renderer already diffs and skips inactive tabs (`render-core.js:433-447`, 41–46 ms saved); the grid does not. Cost is a 19-card rebuild every 30 s per tab — cheap on desktop, the kind of thing that shows on the phone. Fix: hash the trimmed `/api/projects` payload and skip `render()` when unchanged (the beacon stream already does exactly this server-side).

### Polling inventory (verified)
| Timer | Interval | Where | Note |
|---|---|---|---|
| `/api/projects` + `/api/config` | 30 s | `index.html:2969` | fine |
| Floor `/api/floor` | 5 s (min 3) while open | `floor.js:570` | 307 ms server-side today (E2) |
| `/agent/status` freshness tick | 5 s per open modal | `index.html:3360` | F1 |
| `/agent/status` subagent poll | 4 s while a helper is visible | `conversation.js:3771` | F1 |
| `/agent/status` running sessions | 15 s per running session | `index.html:3283` | F1 |
| Session metrics tick (DOM only) | 1 s | `index.html:2677` | no fetch |
| Beacon | SSE (3 s server tick) + 60 s fallback | `beacon.js:66,384` | fine |
| System status | 60 s | `system-status.js:611` | 1.6 ms |
| Presence ping | 15 s | `index.html:3563` | not traced |
| Workflows tab | 3 s while tab open | `agent-console.js:535` | scoped, self-cancels |
| Browser pane status | 5 s while open | `browser-pane.js:643` | scoped |
| Mobile inbox badge | 30 s | `mobile.js:643` | fine |

---

## What was not covered
- `mc/distiller.py` and `mc/backup.py` end-of-session / backup paths (batch, not per-turn or per-poll).
- The CLI's own per-turn cost (tool schemas, MCP descriptions, the ~40-skill listing) — outside MC's code.
- Hivemind worker context was read, not run (no live hivemind measured; numbers in §1 are the code's own caps).
