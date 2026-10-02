# Entanglement audit

1. **37 confirmed bundle-bearing files:** 29 over 1,500 lines plus eight smaller files; a lower bound, not an exhaustive count of small bundles.
2. The large-file census totals **112,589 lines**: 28 application/guard files and one development harness, all inventoried below.
3. **#1 `mc/blueprints/agent_routes.py`: 110 merges × 24 concern groups = 2,640**, spanning dispatch, providers, context, history, jobs and guardians.
4. **#2 `static/css/app.css`: 86 × 25 = 2,150**, spanning independent screens, shared chrome and their interleaved responsive rules.
5. **#3 `static/index.html`: 65 × 17 = 1,105**, combining shell markup with state, rendering, chat sizing, settings and boot code.
6. The 60-day window contains **948 first-parent commits, including 449 merges**, and **1,385 reachable non-merge commits**.
7. **20 commits explicitly record conflict resolution**; 17 are on the first-parent chain, and 15 contain a `# Conflicts:` trailer.
8. Divergent same-day branch pairs touching one file: **app.css 21, agent_routes.py 20, desk-v1.css 14**; these are pressure indicators, not conflict counts.
9. **First split: Floor lesson → its own module plus imported lesson registry**, 1–2 sessions, coordinated with the existing Workflows lesson owner.
10. The top-ten plans total **50–73 scoped sessions** if all are undertaken; source remains unchanged and this report is branch-only.

## 1. Scope, counting and reproducibility

Audit by Kestrel, 2026-10-02. Frozen base: `a2ce8d37266a8292b9db8c5badebfc89faac7af7` (master at task start, 2026-10-02 12:48:10 PDT). Work branch: `entanglement-audit`, in the assigned isolated worktree. Later merges and uncommitted work are excluded deliberately. All line ranges refer to that base, are inclusive, and are source anchors rather than exact copy/paste extraction instructions.

The rule under audit is **one independently changing unit per file; registries import entries rather than contain their implementations**. File length is a census threshold, not proof that every helper needs its own file. A bundle here means one file containing two or more concerns with separate reasons to change; the tables disclose the judgment behind that count. Shared contracts and cohesive helpers can stay together. One file per function would replace this problem with fragmentation.

**Inventory:** enumerate tracked `.py`, `.js`, `.css`, `.html` files, excluding `tests/`, `tools/smoke/`, path components `node_modules`, `vendor`, `vendored`, `dist`, `build`, and `.min.` filenames. Decode UTF-8 with replacement and count `splitlines()` (including comments and blanks). This yields 319 extension-matching candidate files and exactly 29 above **1,500** lines. Documentation/demo sources remain eligible under the requested extension rule; none exceeds the threshold. `tools/provider-live/codex_run.py` is retained because only `tools/smoke` was excluded, but explicitly classified as a development harness, not shipped application runtime. The smaller-file pass searches registry declarations, inline bodies, screen renderers and provider switches; eight additional files were confirmed.

**Window:** `[2026-08-03T19:48:10Z, base]`, exactly 60 days ending at the snapshot's commit time. Group same-day activity in PDT (UTC−07 throughout this interval). Use committer timestamps, not authors' backdated dates. M = first-parent commits with two or more parents whose net diff against parent 1 touches the file. FP = all first-parent file-touching commits, including direct commits; C = reachable non-merge file-touching commits, deduplicated by SHA. These denominators describe different views and must not be added together.

**Rename handling:** `56e27d24` moved `agent_runtime.py` → `mc/agent_runtime.py` and `distiller.py` → `mc/distiller.py`; union old and current paths and count a commit once. Runtime totals become M=60, FP=100, C=113 (current-path-only was 56/84/97); Distiller becomes 3/5/5 (current-path-only 3/4/4). Other inventoried large files have no detected path rename in the window. This avoids mistaking a move for a reduction in change pressure.

**Ranking:** score = **M × Cn**, where Cn is the number of explicitly listed concern groups. Sort descending, break exact ties by source path. Runtime and conversation both score 900; alphabetical tie-breaking is bookkeeping, not a claim that one is safer. Concern granularity is subjective: grouping all provider implementations as one concern would hide the exact bundling under review. No LOC multiplier is used. Workflow versus memory ordering could change with a finer concern taxonomy; their plans remain separately useful. The first recommended split intentionally also considers immediate work blockage and risk, not just score.

Core commands (run against the frozen base, not a moving `master`):

```text
git ls-tree -r --name-only a2ce8d37266a8292b9db8c5badebfc89faac7af7
git show a2ce8d37266a8292b9db8c5badebfc89faac7af7:<path>
git log a2ce8d37266a8292b9db8c5badebfc89faac7af7 --first-parent --since=2026-08-03T19:48:10Z --format="%x1e%H%x1f%P%x1f%cI%x1f%s" --name-only --diff-merges=first-parent
git log a2ce8d37266a8292b9db8c5badebfc89faac7af7 --no-merges --since=2026-08-03T19:48:10Z --format="%x1e%H%x1f%P%x1f%cI%x1f%s" --name-only
git log a2ce8d37266a8292b9db8c5badebfc89faac7af7 --since=2026-08-03T19:48:10Z --format="%H%n%B%x1e"
git rev-list --parents a2ce8d37266a8292b9db8c5badebfc89faac7af7
git diff --unified=0 <merge>^1 <merge> -- <path>
```

For each eligible file, count unique touching SHAs from each log. For each day with k touching merges, same-day pairs = k(k−1)/2; sum across days. For each pair of **two-parent merges**, compare their incoming parent-2 tips: count a divergent pair only if neither tip is an ancestor of the other (`git merge-base --is-ancestor A B` and the reverse both return 1). This excludes plainly sequential branches based on an already-integrated tip. It is stronger evidence than shared-day timestamps, but still does not measure wall-clock worker concurrency. Per-file pairs cannot be summed as distinct branch pairs because the same pair can share several files.

## 2. Change pressure, co-change and observed cost

### Ranked file census

Cn = concern groups; M = 60-day merge touches; FP = all first-parent touches; C = non-merge touches; D/P = same-day touching-merge days/pairs; DP = divergent incoming-tip pairs; Max = most touching merges on one day. `small` rows are below threshold. All other rows are the complete large-file census.

| Rank | File | Lines | Cn | M | Score | FP | C | D/P | DP | Max |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | `mc/blueprints/agent_routes.py` | 17,102 | 24 | 110 | 2,640 | 188 | 225 | 16/341 | 20 | 14 |
| 2 | `static/css/app.css` | 10,658 | 25 | 86 | 2,150 | 164 | 168 | 15/222 | 21 | 13 |
| 3 | `static/index.html` | 4,202 | 17 | 65 | 1,105 | 87 | 95 | 14/145 | 7 | 12 |
| 4 | `static/css/desk-v1.css` | 3,297 | 18 | 55 | 990 | 56 | 89 | 7/341 | 14 | 23 |
| 5 | `mc/agent_runtime.py` | 10,816 | 15 | 60 | 900 | 100 | 113 | 14/102 | 5 | 8 |
| 6 | `static/js/conversation.js` | 6,316 | 15 | 60 | 900 | 95 | 106 | 16/83 | 8 | 6 |
| 7 | `server.py` | 3,570 | 13 | 61 | 793 | 96 | 104 | 12/101 | 9 | 8 |
| 8 | `static/js/workflow-builder.js` | 4,864 | 14 | 28 | 392 | 32 | 34 | 5/109 | 5 | 14 |
| 9 | `mc/memory.py` | 6,696 | 16 | 20 | 320 | 43 | 58 | 6/15 | 2 | 5 |
| 10 | `mc/blueprints/desk_routes.py` | 1,965 | 14 | 21 | 294 | 30 | 32 | 3/75 | 6 | 12 |
| 11 | `mc/desk.py` | 2,969 | 15 | 19 | 285 | 24 | 25 | 3/63 | 4 | 10 |
| 12 | `mc/blueprints/browser_routes.py` | 3,736 | 13 | 19 | 247 | 25 | 36 | 5/18 | 0 | 5 |
| 13 | `mc/blueprints/system_routes.py` | 2,748 | 10 | 19 | 190 | 22 | 33 | 5/12 | 1 | 4 |
| 14 | `static/js/desk-v1-kit.js` (small) | 1,348 | 7 | 27 | 189 | 30 | 34 | 7/56 | 2 | 9 |
| 15 | `mc/blueprints/project_routes.py` | 2,831 | 11 | 17 | 187 | 34 | 35 | 4/8 | 0 | 3 |
| 16 | `steward/fence.py` | 2,068 | 11 | 12 | 132 | 18 | 32 | 3/7 | 1 | 3 |
| 17 | `tools/provider-live/codex_run.py` (harness) | 1,807 | 10 | 13 | 130 | 15 | 15 | 1/78 | 3 | 13 |
| 18 | `static/js/browser-pane.js` | 1,853 | 9 | 14 | 126 | 17 | 25 | 3/19 | 0 | 5 |
| 19 | `mc/secrets_store.py` | 2,725 | 11 | 10 | 110 | 12 | 22 | 2/2 | 0 | 2 |
| 20 | `static/js/desk.js` | 2,349 | 9 | 11 | 99 | 24 | 24 | 4/9 | 0 | 4 |
| 21 | `mc/blueprints/scheduler_routes.py` | 1,636 | 9 | 10 | 90 | 19 | 20 | 0/0 | 0 | 1 |
| 22 | `mc/workflows.py` | 2,158 | 10 | 9 | 90 | 13 | 13 | 1/1 | 0 | 2 |
| 23 | `mc/blueprints/hivemind_routes.py` | 2,115 | 11 | 8 | 88 | 13 | 13 | 2/2 | 0 | 2 |
| 24 | `static/js/claydo.js` | 2,016 | 8 | 11 | 88 | 26 | 30 | 1/1 | 0 | 2 |
| 25 | `static/js/system-status.js` | 1,512 | 8 | 10 | 80 | 12 | 17 | 3/7 | 0 | 3 |
| 26 | `static/js/desk-v1-studio.js` | 1,506 | 7 | 11 | 77 | 12 | 12 | 2/24 | 0 | 7 |
| 27 | `mc/backup.py` | 2,165 | 9 | 7 | 63 | 11 | 11 | 1/6 | 0 | 4 |
| 28 | `mc/desk_engines.py` | 2,265 | 12 | 5 | 60 | 5 | 5 | 1/6 | 0 | 4 |
| 29 | `static/js/settings-drill.js` (small) | 1,059 | 4 | 13 | 52 | 20 | 21 | 3/5 | 3 | 3 |
| 30 | `mc/distiller.py` | 2,877 | 10 | 3 | 30 | 5 | 5 | 0/0 | 0 | 1 |
| 31 | `static/js/walkthrough.js` (small) | 574 | 3 | 10 | 30 | 15 | 15 | 2/9 | 0 | 4 |
| 32 | `mc/blueprints/guide_routes.py` (small) | 1,276 | 4 | 6 | 24 | 14 | 17 | 1/1 | 1 | 2 |
| 33 | `mc/desk_publish.py` (small) | 468 | 4 | 5 | 20 | 5 | 6 | 1/3 | 0 | 3 |
| 34 | `static/js/backup-panel.js` (small) | 1,115 | 4 | 5 | 20 | 5 | 5 | 1/6 | 0 | 4 |
| 35 | `static/js/learn.js` (small) | 1,285 | 6 | 3 | 18 | 3 | 5 | 1/1 | 0 | 2 |
| 36 | `static/js/skills-panel.js` | 1,767 | 9 | 2 | 18 | 2 | 2 | 0/0 | 0 | 1 |
| 37 | `static/js/settings-sections.js` (small) | 1,456 | 4 | 4 | 16 | 4 | 5 | 1/3 | 0 | 3 |

**Cross-check against the supplied 14-day measurements:** the identical endpoint with cutoff `2026-09-18T19:48:10Z` gives agent routes **74**, Desk CSS **55**, index **52**, app CSS **48**, server **41**, conversation **40**, runtime **37** merge touches. All seven match. These are merged-change counts, not every ordinary commit or number of agents.

### Which separate concerns were actually changed

These are selected, source-verified examples, not an automated attribution of every historical hunk to today's line numbers. Offsets drift; the concern inventory is anchored to the snapshot, while each historical claim is checked in that merge's own diff. Each pair below has divergent incoming tips and the same PDT integration date. Titles identify the job; parent-2 SHAs identify the actual integrated branch tips even if the branch names have since been deleted.

| File | Day | Merge ← incoming tip A | Merge ← incoming tip B | Independently changing concerns evidenced |
|---|---|---|---|---|
| `mc/blueprints/agent_routes.py` | 2026-09-28 | `91461c6c` ← `255d1df1` | `e12bb34e` ← `1e873c3d` | Usage/LOC checkpoint additions (`_log_agent_dispatch_pending`, `_compute_code_delta`) versus attended-pass gating (`attend_session`, `_note_claude_sid`). |
| `static/css/app.css` | 2026-10-02 | `a0e24118` ← `82b38155` | `82ca4ca3` ← `b3400e04` | Phone composer sizing versus Learn animated hand cues; one hunk each in distant style regions. |
| `static/index.html` | 2026-10-02 | `48e353d9` ← `6ffdba5e` | `95183990` ← `b94ef89f` | Desk guides module-load entry versus `sizeAgentChat` measurement logic; one hunk each. |
| `static/css/desk-v1.css` | 2026-10-02 | `48e353d9` ← `6ffdba5e` | `3a9bf781` ← `0f54c24a` | Connections wizard styles versus storyboard paste/drag-to-trash controls (one versus four hunks). |
| `mc/agent_runtime.py` | 2026-09-25 | `467aa148` ← `a12524d7` | `91b6579b` ← `4c434543` | Gemini per-session MCP settings/environment setup versus Codex fence installation and verification. |
| `static/js/conversation.js` | 2026-10-02 | `db364440` ← `59a95c05` | `fcc617da` ← `4b8018fd` | Channel working-state project filtering versus composer emoji controls. |
| `server.py` | 2026-10-01 | `31d4baa1` ← `29b740d6` | `82fb543e` ← `b8ce95de` | Desk live-mode defaults in `_load_config` versus registration for native popout windows. |
| `static/js/workflow-builder.js` | 2026-09-14 | `8c1aeb5f` ← `4b0c62a0` | `0dc39615` ← `85be2bc5` | Live canvas status/focus versus honest run-cancellation handling in `_wfCancelRun`. |
| `mc/memory.py` | 2026-09-23 | `fd2739c0` ← `2e752912` | `495aa7be` ← `0f8353a7` | Checkpoint carry and holds-while eligibility versus `TimestampedLines` initialization for condense sessions. |
| `mc/blueprints/desk_routes.py` | 2026-10-01 | `4c8b406a` ← `acdc042e` | `c598bde7` ← `23f89702` | Version approval/publication route guards versus standalone Studio material-save route. |

Additional distinct-concern checks: `f037e90f` changes worktree cleanup in agent routes while `174b8084` changes provider-discovery metadata there; `43e19dce` changes stop-hook transcript normalization in runtime while `5e827a83` changes Codex model validation. `bcec3380` changes the workflow box/inspector, while `d93e8b50` adds describe/review UI. Memory's `31f84846` adds negation/ledger integration while `eefacc6d` changes `_extract_transcript_telemetry*`. These pairs need not be simultaneous to demonstrate separate reasons to change one file.

### Cross-file co-change

Count a commit once if it touches both files (including the runtime predecessor path). This measures integration seams that a split must preserve; it does not prove those files ought to be combined.

| File pair | Non-merge commits touching both | First-parent merges touching both |
|---|---:|---:|
| `agent_routes.py` + `agent_runtime.py` | 63 | 40 |
| `conversation.js` + `app.css` | 46 | 29 |
| `conversation.js` + `index.html` | 26 | 16 |
| `desk-v1.css` + `desk-v1-studio.js` | 7 | 10 |
| `desk_routes.py` + `desk.py` | 20 | 14 |
| `workflow-builder.js` + `app.css` | 24 | 22 |

The Desk CSS/Studio row can have more merges than ordinary co-changing commits: a branch can edit CSS and JS in separate commits, then integrate both in one merge. The two columns are not interchangeable.

### Recorded conflict resolutions

Searching all reachable 60-day commit messages for `conflict|overlap|collision` produced **89 candidate messages**. Manual review retained **20 explicit merge-resolution records** and rejected the other **69** as layout overlap, ID collision, policy logic or other non-merge uses. Of the retained 20, **15 have Git-style conflict trailers**, **17 are first-parent integration commits**, and **3 are side-history merge commits**. This is a lower bound; Git does not retain an authoritative log of every conflict the operator encountered.

| Commit | Recorded conflict/resolution evidence |
|---|---|
| `48e353d9` | `CHANGELOG.md`, Desk Connect guides integration |
| `29204c9d` | `CHANGELOG.md`, `mc/blueprints/system_routes.py`, Mac self-update |
| `82fb543e` | `CHANGELOG.md`, native popout integration |
| `c598bde7` | Body explicitly says accounts routes and material POST both kept in `desk_routes.py` |
| `a879683e` | `mc/blueprints/desk_routes.py`, engagement/retro integration |
| `495fcaf1` | `mc/blueprints/agent_routes.py`, legacy attend removal/one-shot pass work |
| `ee024f00` | `mc/blueprints/settings_routes.py`, `static/js/conversation.js`, `static/js/settings-drill.js` |
| `f0448ae2` | Historical `static/js/desk-v1-fixtures.js`, master into video branch |
| `a82c1583` | Historical `static/js/desk-v1-fixtures.js`, calendar integration |
| `467aa148` | `mc/agent_runtime.py`, worktree/Gemini MCP integration |
| `258f9ac6` | `mc/blueprints/settings_routes.py`, `server.py`, vault idle-lock integration |
| `2426677c` | `tests/test_provider_live_driver.py` |
| `42de0969` | `tools/smoke/first-run-provider-chooser.mjs` |
| `84661e72` | Body records changelog, smoke package manifest and Channel smoke resolutions |
| `57a10c98` | Body records audit-document conflict resolution/section renumbering |
| `f344303d` | Body records audit-document holdback-note conflict resolution |
| `56e27d24` | Root restructure: imports and changelog resolution, 23 master commits arrived while branch was open; eight stale imports carried forward |
| `f5390b5e` | `CHANGELOG.md` |
| `0d7bb0ea` | `CHANGELOG.md` |
| `dcc79d7f` | `CHANGELOG.md` |

The historical Desk fixture file has since moved out of production; it is conflict evidence, not a current production bundle. Changelog conflicts likewise are not counted as application-code bundles. The strongest measured direct-cost example is `56e27d24`: 23 intervening commits and eight import repairs, explicitly documented in the merge body. No elapsed-hours or avoided-conflicts estimate is inferred from these records.

## 3. Concern inventory

### Every eligible file over 1,500 lines

Ranges include adjacent constants, setup, helpers and comments where appropriate. A semicolon indicates a concern scattered across the file. The concern count used in ranking is exactly the number of rows in each file's table. These are coarse independently changing ownership groups, not an assertion that every range is currently a self-contained module.

#### `mc/blueprints/agent_routes.py` — 17,102 lines, 24 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Session ownership, wiring and process helpers | 1-579 |
| Incognito scratch projects | 580-630 |
| MCP and skill selection | 631-820 |
| Routing and prompt refresh | 821-1133;9659-9886 |
| Manager/worktree lifecycle and code delta | 1134-1668 |
| Managed jobs | 1669-1813 |
| Router statistics API | 1814-1864 |
| Image upload | 1865-1951 |
| Provider discovery, auth, install and allowance | 1952-3985 |
| Prompt context, plans and tool observers | 3986-5477 |
| Turn tools, questions and backlog synchronization | 5478-5736 |
| Stream readers and recovery | 5737-6416 |
| Agent log persistence and attendance gates | 6417-7067 |
| Cold revival and transcript buffers | 7068-7880 |
| Transcript telemetry and dispatch records | 7881-8261 |
| Delegation delivery and notifications | 8262-8887 |
| Usage checkpoints and completion | 8888-9658 |
| Dispatch, persona and handoff | 9887-12111 |
| Model/send/followup and SSE routes | 12112-13535 |
| Stop/interrupt/delete and stdin control | 13536-14239 |
| Plan-file operations | 14240-14331;16271-16395 |
| Status, history, search and conversation listing | 14332-16270 |
| Document discovery/reading and usage API | 16396-16740 |
| Session guardian and idle eviction | 16741-17102 |

#### `mc/agent_runtime.py` — 10,816 lines, 15 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Runtime contracts and events | 1-369;808-1398 |
| Protocol tool blocks and transcript normalization | 370-807 |
| Registry and transform authorization/dispatch | 1399-1696;10802-10816 |
| Claude hooks, incognito registry and transcript discovery | 1697-1954 |
| Claude runtime | 1955-3437 |
| Process/platform helpers | 3438-3541 |
| Gemini MCP/env and runtime | 3542-5273 |
| Mode A dispatch/reader/interrupt | 5274-5416;5930-6341 |
| Provider token counters and CLI cost accounting | 5417-5651;5695-5929 |
| Qwen defaults and runtime | 5652-5694;6507-7576 |
| Codex fence, rollout discovery and runtime | 6342-6506;7577-9457 |
| OpenCode runtime | 9458-9815 |
| Goose runtime | 9816-10186 |
| Aider runtime | 10187-10497 |
| Kiro runtime | 10498-10801 |

#### `static/css/app.css` — 10,658 lines, 25 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Theme, density and appearance tokens | 1-227 |
| Sidebar/header/navigation chrome | 228-731 |
| Project grid/list/cards | 732-1031 |
| Modal, menu, resize and tray primitives | 1032-1404 |
| Usage strip, controls and settings | 1405-2274 |
| Feed/inbox and mobile dashboard | 2275-3006 |
| Agent panel, rails, split view and Channel | 3007-3684 |
| Composer, slash completion and mobile chat | 3685-4446 |
| Chat bubbles, attribution, flow and rich output | 4447-5412 |
| Agent log, rules and memory panels | 5413-5573 |
| Floor figures, Bench and face picker | 5574-6085;8594-8708 |
| Path/folder/project editors | 6086-6262;7325-7357 |
| Scheduling and calendars | 6263-7038 |
| Steward, plans, token/provider/model/attendance controls | 7039-7324 |
| Agent console | 7358-7446 |
| Tour | 7447-7670 |
| Command palette | 7671-7791 |
| Terminal popout | 7792-7822 |
| Hivemind and run history | 7823-8042;8493-8585 |
| Claydo and persona workshops | 8043-8374 |
| Media gallery and feature flags | 8375-8492;8586-8593 |
| Legacy Desk screens | 8709-9446 |
| Workflow canvas, inspector and run status | 9447-10476 |
| Chat window popout | 10477-10522 |
| Learn cues, lesson bubble and hub | 10523-10658 |

#### `mc/memory.py` — 6,696 lines, 16 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Wiring/provider transforms and transcript discovery | 1-515 |
| Memory paths, index sections and watermarks | 516-735 |
| Links, supersession graph and archive dedupe | 736-1143 |
| Positions schema and holds-while evaluation | 1144-1562 |
| Continuity ownership and rendering | 1563-2045 |
| Position/topic CRUD | 2046-2294 |
| Corpus, BM25 retrieval and supersession | 2295-2843 |
| Topic minting, overlaps and resolution | 2844-3258 |
| Negation obligations and ledger | 3259-3692 |
| Link expansion and retrievability | 3693-3921 |
| Session-log migration, caps and demotion | 3922-4397 |
| Managed writes, archive and watermark GC | 4398-4819 |
| Mid-task checkpoint workers | 4820-5223 |
| Scribe statistics/rendering and transcript telemetry | 5224-5654 |
| Habit extraction and summarization | 5655-6043 |
| Structured condense planning/apply/dispatch | 6044-6696 |

#### `static/js/conversation.js` — 6,316 lines, 15 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Speaker attribution and character selection | 1-354 |
| Provider/model picker and in-chat switching | 355-598 |
| Attendance, incognito and mobile options | 599-820 |
| Session lists and Hivemind workers | 821-928 |
| Brainstorm starters and panel composition | 929-2002 |
| Rail search, hidden/deleted conversations and live status | 2003-2825 |
| Channel roster and hire navigation | 2826-3157 |
| Threads/topics board and backlog sweep | 3158-3808 |
| Conversation opening and rollover history | 3809-4199 |
| Tabs, split-view and cross-project selection | 4200-4627 |
| Activity, subagents, fork and dispatch notices | 4628-4948 |
| Transcript line rendering and dates | 4949-5206 |
| Plan approval and question forms | 5207-5547 |
| Send/stop and project refresh | 5548-5999 |
| Status polling and public window surface | 6000-6316 |

#### `static/js/workflow-builder.js` — 4,864 lines, 14 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Node/action vocabulary and defaults | 1-212 |
| Agent/engine resolution and palette identity | 213-574 |
| Mount/load/state/undo and workflow tabs | 575-1009 |
| Graph analysis, field references and insertion | 1010-1381 |
| DOM synchronization and canvas/trigger layout | 1382-1746 |
| Palette, hiring and provider refresh | 1747-1999 |
| Node boxes and inspector | 2000-2398 |
| Agent/approval/action/wait editors | 2399-2763 |
| Trigger/schedule editor | 2764-3035 |
| Node/edge editing, gestures and connectors | 3036-4207 |
| Save, validation and run controls | 4208-4458 |
| Describe/review workflow | 4459-4640 |
| Live run polling and focus/cancel | 4641-4803 |
| Dirty/saved state and window exports | 4804-4864 |

#### `static/index.html` — 4,202 lines, 17 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Early theme/single-tab boot and library loading | 1-283 |
| Performance HUD | 284-448 |
| SVG icon/provider sprite | 449-518 |
| Desktop shell | 519-791 |
| Overlays, mobile navigation and search | 792-945 |
| Global shared state, constants and provider cache | 946-1277 |
| Domain/project data loading and ordering | 1278-1440 |
| Grid, sidebar, filters and view rendering | 1441-1870 |
| Chat sizing/status and modal repaint preservation | 1871-2499 |
| Toast notifications and shared utilities | 2500-2683 |
| Usage, flags and input preferences | 2684-2847 |
| Appearance/background and session metrics | 2848-3118 |
| Settings persistence and model selection | 3119-3215 |
| Run rows and transcript viewer | 3216-3418 |
| Polling, native bridge and app boot | 3419-3839 |
| Freshness, reconnect and presence | 3840-4063 |
| Module loading order and version-reload notice | 4064-4202 |

#### `mc/blueprints/browser_routes.py` — 3,736 lines, 13 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Session/view state and wiring | 1-249 |
| Origin checks, download lifecycle and paths | 250-464 |
| Profile storage and leak sweeping | 465-677 |
| Chromium discovery, tabs and window targeting | 678-904 |
| UA override and mobile emulation | 905-1257 |
| Window sizing and view application | 1258-1497 |
| CDP event/target/screencast loop | 1498-2068 |
| Launch and graceful teardown | 2069-2499 |
| Launch/stream HTTP routes | 2500-2650 |
| Keyboard, zoom and input translation | 2651-3086 |
| File chooser, evaluation and selection | 3087-3247 |
| Visible-text filtering and read envelope | 3248-3534 |
| Headless profile reader and profile/status routes | 3535-3736 |

#### `server.py` — 3,570 lines, 13 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Directory and configuration loading/defaults | 1-686 |
| HTTP setup, static/CORS and shared imports | 687-1188 |
| Git sync and worktree startup GC | 1189-1284 |
| Agent log migration and transcript backfill | 1285-1446 |
| Unscribed sessions, telemetry/FTS and memory maintenance | 1447-1726 |
| Service dependency wiring and blueprint registration | 1727-2197 |
| PWA manifest/assets/index/version routes | 2198-2296 |
| Shutdown resource cleanup | 2297-2366 |
| Port/single-instance guard | 2367-2577 |
| Remaining lifecycle wiring and auth hooks | 2578-2961 |
| Claude runtime hook bridge | 2962-3171 |
| HTTP serving and ordered boot | 3172-3490 |
| Guardrail hook installation | 3491-3570 |

#### `static/css/desk-v1.css` — 3,297 lines, 18 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Shell, project/piece skeleton and navigation | 1-271 |
| Shared labels, menus, agent box and toast/Undo | 272-375 |
| Pointer drag primitives and legacy banner | 376-394 |
| Home/status board | 395-669 |
| Campaign/content list | 670-897 |
| Start/proposal/rules controls | 898-1071 |
| Review screen | 1072-1306 |
| Calendar and When fields | 1307-1647 |
| Video intake/director | 1648-1856 |
| Conversations | 1857-1989 |
| Results and related setup surfaces | 1990-2219 |
| Map stepper and brief/How | 2220-2351 |
| Where/account board | 2352-2522 |
| What/material choices | 2523-2693 |
| Studio/storyboard/create | 2694-2983 |
| Playbook and evidence chips | 2984-3030 |
| Cross-screen mobile overrides | 3031-3228 |
| Connections and guided setup | 3229-3297 |

#### `mc/desk.py` — 2,969 lines, 15 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Store, migrations and locking | 1-338 |
| Presence and account read settings | 339-471 |
| Campaign bounds hashing/widening | 472-572 |
| Signal ingestion/scoring | 573-714 |
| Voice CRUD and edit learning | 715-947 |
| Platform rules | 948-1063 |
| Proposals | 1064-1148 |
| Campaign CRUD and suggestions | 1149-1645 |
| Approval/start/renew gates | 1646-1870 |
| Goal/results calculation and v1 projection | 1871-2155 |
| Workspace/project state | 2156-2263 |
| Publication ledger and outcomes | 2264-2348 |
| Engagement/read coverage and feed outcomes | 2349-2607 |
| Repetition detection | 2608-2650 |
| Findings, rejection, staleness and playbook | 2651-2969 |

#### `mc/distiller.py` — 2,877 lines, 10 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Provider wiring and configuration | 1-301 |
| Fingerprints, stats and vocabulary | 302-590 |
| Extraction prompts and model pipeline | 591-1024 |
| Signal normalization/persistence/archive | 1025-1193 |
| Candidate aggregation and cost gates | 1194-1524 |
| Summary cache, suppression and authority/origin gates | 1525-1743 |
| Artifact generation/rendering/reframe | 1744-2196 |
| Push stats and queue listing | 2197-2328 |
| Exploration read floor and loop health | 2329-2598 |
| Artifact read/promote/reject and metadata | 2599-2877 |

#### `mc/blueprints/project_routes.py` — 2,831 lines, 11 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Project storage, concurrency and sidecar exclusions | 1-615 |
| Project list/update/pin/summary/delete | 616-1071 |
| Backlog handles, CRUD, notes and links | 1072-1532 |
| Roster hire/unhire | 1533-1673 |
| Social queue and approval/publication state | 1674-2039 |
| GitHub/code sync routes | 2040-2166 |
| Attachment upload/image/file serving | 2167-2531 |
| Import from project/changelog parsing | 2532-2646 |
| Project/shared rules | 2647-2728 |
| Memory editing and cap handling | 2729-2802 |
| Grid order/layout | 2803-2831 |

#### `mc/blueprints/system_routes.py` — 2,748 lines, 10 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Wiring, loop/slash diagnostics | 1-150 |
| Process management and reaper | 151-406;459-499 |
| CLI automatic updates | 407-458 |
| System status persistence/heartbeat | 500-647 |
| Agent-log usage backfill | 648-812 |
| Provider allowance fetching | 813-1067 |
| Usage sampling/pruning and reports | 1068-1658 |
| Restart/shutdown orchestration | 1659-2025;2678-2748 |
| Git/frozen release update discovery | 2026-2355 |
| Update polling/download/apply | 2356-2677 |

#### `mc/secrets_store.py` — 2,725 lines, 11 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Vault contracts, paths, token and type metadata | 1-361 |
| Keyring/DPAPI mirror and master-key recovery | 362-837 |
| Passphrase cryptography and idle lock | 838-1032 |
| Locked/tamper notifications and legacy-key quarantine | 1033-1351 |
| Passphrase/recovery management | 1352-1524 |
| Windows ACL/reparse hardening | 1525-1866 |
| Encryption and atomic store persistence | 1867-1968 |
| Audit and dispensed-value redaction | 1969-2055 |
| Metadata/secret management | 2056-2305 |
| Unattended access policy and credential resolution | 2306-2601 |
| Encrypted backup import/export | 2602-2725 |

#### `static/js/desk.js` — 2,349 lines, 9 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Fetch/store and agent-choice overlay | 1-208 |
| Cadence/workflow integration | 209-347 |
| Harvest/draft/new campaign | 348-543 |
| Voices and platform-rule editors | 544-790 |
| Legacy shell/read-only mode and board | 791-1457 |
| Queue filters, checks and release actions | 1458-1906 |
| Review pane | 1907-2063 |
| Rework thread with planner | 2064-2217 |
| Queue/calendar/ledger rendering and exports | 2218-2349 |

#### `mc/desk_engines.py` — 2,265 lines, 12 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Engine/model descriptors and registry | 1-383 |
| HTTP transport, connection and credential resolution | 384-569 |
| Assets, request validation and estimates | 570-718 |
| Higgsfield API adapter | 719-812 |
| Higgsfield MCP adapter | 813-977 |
| Veo adapter | 978-1047 |
| Gemini image adapter | 1048-1094 |
| OpenAI image adapter and adapter registry | 1095-1187 |
| Job store, budget/caps and request parsing | 1188-1430 |
| Submission and polling | 1431-1708 |
| Output library ingestion | 1709-1800 |
| ffmpeg planning, render jobs and clip joining | 1801-2265 |

#### `mc/backup.py` — 2,165 lines, 9 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Paths/configuration, format errors and destination guards | 1-246 |
| Project/git/memory discovery and category enumeration | 247-689 |
| Archive creation, cancellation and listing | 690-985 |
| Schedule/retention state | 986-1087 |
| Full-install restore and remapping | 1088-1220 |
| Project export | 1221-1501 |
| Project import/dry-run and replacement safety copy | 1502-1815 |
| Restore-point CRUD/retention | 1816-2016 |
| Diff and rollback | 2017-2165 |

#### `mc/workflows.py` — 2,158 lines, 10 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Schema, engine constraints and wiring | 1-176 |
| Definition migration/store/CRUD | 177-341 |
| DAG and vocabulary validation | 342-678 |
| Toolless draft generation/layout | 679-957 |
| Review/compile and context/template/result protocol | 958-1179 |
| Run persistence and graph frontier | 1180-1297 |
| Run start/cancel/advance and step dispatch | 1298-1600 |
| Completion, human decisions and wait resumes | 1601-1839 |
| Action implementations and operator notifications | 1840-1965 |
| Startup adoption and recovery reconciliation | 1966-2158 |

#### `mc/blueprints/hivemind_routes.py` — 2,115 lines, 11 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Wiring and manifest/workstream store | 1-203 |
| Worktree integration worker | 204-279 |
| Findings/bus/decisions/questions/context stores | 280-480 |
| Dependency/state reconciliation | 481-572 |
| Hivemind lifecycle routes | 573-813 |
| Workstream CRUD | 814-927 |
| Worker handoff/context/runtime spawning | 928-1328 |
| Orchestrator dispatch/auto-spawn | 1329-1592 |
| Bus/SSE and knowledge endpoints | 1593-1788 |
| Human escalation/intervention/review | 1789-1893 |
| Orchestrator loop and run history | 1894-2115 |

#### `steward/fence.py` — 2,068 lines, 11 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Decision contracts, paths and block vocabulary | 1-148 |
| Shell/PowerShell normalization | 149-431 |
| Inert prose masking and network tokenization | 432-609 |
| HTTP tool mutation classification | 610-848 |
| Shell segments, continuations and network detection | 849-1138 |
| Enabling constructs and command classification | 1139-1280 |
| Patch/write normalization and install-dir guard | 1281-1358 |
| Vault and local-auth file guards | 1359-1585 |
| Action policy dispatch | 1586-1667 |
| Session/transcript unattended detection | 1668-1821 |
| Attend-once pass constraints and hook entry point | 1822-2068 |

#### `static/js/claydo.js` — 2,016 lines, 8 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Floating button and character change broadcast | 1-159 |
| Session restore and help modal | 160-315 |
| Mode/state selection and request streaming | 316-679 |
| Response markers, rendering and UI actions | 680-763 |
| Builder cards and Brainstorm/Learn handoff | 764-971 |
| Artifact editor and project insertion | 972-1104 |
| Save-character workshop | 1105-1452 |
| Persona editor | 1453-2016 |

#### `mc/blueprints/desk_routes.py` — 1,965 lines, 14 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Wiring, agent resolution, presence/workspace | 1-200 |
| Signal harvest | 201-251 |
| Voice CRUD/seeding | 252-390 |
| Platform rules | 391-422 |
| Campaign CRUD/results/retro and approval | 423-668 |
| Pieces/versions/revision/materials | 669-859 |
| Storyboard CRUD/uploads | 860-922 |
| Accounts and human-only connection management | 923-992 |
| Engines, connection flows and render jobs | 993-1171 |
| Ledger/publication outcomes | 1172-1220 |
| Engagement/read/reply/poll routes | 1221-1459 |
| Retrospective findings and approval/rejection | 1460-1596 |
| Draft/rework/triage/proposals | 1597-1904 |
| Brief/repetition/overview | 1905-1965 |

#### `static/js/browser-pane.js` — 1,853 lines, 9 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Clipboard and ordered input queue | 1-248 |
| Coordinate mapping | 249-277 |
| Pane DOM, lifecycle and bound input/IME/touch handlers | 278-1141 |
| Minimize/maximize geometry | 1142-1275 |
| Downloads | 1276-1321 |
| Tabs and mobile switcher | 1322-1443 |
| Dialogs and file chooser | 1444-1522 |
| Close, sessions and saved-profile picker | 1523-1727 |
| Status polling, discovery and restoration | 1728-1853 |

#### `tools/provider-live/codex_run.py` — 1,807 lines, 10 concern groups

Development/live-validation harness; included by requested path scope, separate from application runtime.

| Concern | Inclusive line ranges |
|---|---|
| CLI/environment/contracts and probe prompts | 1-249 |
| API client | 250-335 |
| Disposable instance and process ownership | 336-606 |
| Guardrail scenario | 607-749 |
| Chat/followup/restart scenarios | 750-827 |
| Image, notify, workflow and schedule scenarios | 828-1008 |
| Memory/hire/stop/question/MCP/usage/allowance scenarios | 1009-1153 |
| Cross-provider dispatch/workflow/handoff scenarios | 1154-1320 |
| Inline scenario registry and native evidence | 1321-1538 |
| Evidence writer, CLI selection and execution/preflight | 1539-1807 |

#### `static/js/skills-panel.js` — 1,767 lines, 9 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Extensions shell and persona browser | 1-265 |
| Skills listing and usage loading | 266-369;640-750 |
| Distiller queue/read/promote/reframe/reject | 370-639 |
| Skill editor and lifecycle actions | 751-971 |
| Plugin/quarantine and shared import UI | 972-1193 |
| Paste import | 1194-1249 |
| Folder import | 1250-1338 |
| Git import | 1339-1492 |
| Cross-project skill browser/install and exports | 1493-1767 |

#### `mc/blueprints/scheduler_routes.py` — 1,636 lines, 9 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Wiring and schedule persistence | 1-160 |
| Durable slot claims/pruning | 161-219 |
| Cron/cadence/date calculation | 220-450 |
| Steward task/context builder | 451-557 |
| Scheduling loop | 558-957 |
| Resume selection and continuation | 958-1145 |
| List/render/character validation | 1146-1310 |
| Schedule CRUD | 1311-1494 |
| Run-now and run history | 1495-1636 |

#### `static/js/system-status.js` — 1,512 lines, 8 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Status fetching, pill and health/config/MCP tabs | 1-272 |
| Allowance bars and breakdown rendering | 273-792 |
| Panel switching and usage/report fetching | 793-1005 |
| Quota-reset terminal action | 1006-1027 |
| Status popover/refresh | 1028-1115 |
| Usage modal/report windows | 1116-1233 |
| Usage bar popup/cache/positioning | 1234-1426 |
| Bottom usage strip and exports | 1427-1512 |

#### `static/js/desk-v1-studio.js` — 1,506 lines, 7 concern groups

| Concern | Inclusive line ranges |
|---|---|
| Studio library/home and agent selection | 1-296 |
| Storyboard data/scene rendering | 297-514 |
| Scene commands, image paste and Undo | 515-749 |
| Render/agent box and scene drag/trash UI | 750-934 |
| Screen/online/generation/create source bodies | 935-1097 |
| Writing, claims and review handoff | 1098-1263 |
| Standalone video/image creation and library save | 1264-1506 |

### Smaller bundles and inline implementation registries

Eight additional file-level bundles below the threshold. Some registries are already data-only; their problem is that the implementations they select remain colocated. Do not label a compact metadata table an inline implementation when it is not one.

#### `static/js/learn.js` — 1,285 lines

| Concern | Inclusive line ranges |
|---|---|
| Lesson registry and Floor copy | 18-73 |
| Floor target resolution | 179-229 |
| Visual cue/hand engine | 230-670 |
| Floor navigation/verification within step engine | 671-848 |
| Completion, progress and launch | 849-1051 |
| Controls, hub and first-Floor offer | 1052-1285 |

`LESSONS` at 41 is data, but its Floor definition lives with Floor-specific `_resolveStep` (201), `_verify` (819), `_openFloorSurface` (742), and a single-lesson hub (1148). Move descriptor AND behavior into `learn-lessons/floor.js`; engine imports the registry. This is the concrete lesson/engine bundle, not evidence that a Workflows lesson is already present.

#### `static/js/walkthrough.js` — 574 lines

| Concern | Inclusive line ranges |
|---|---|
| Step descriptors with inline skip/target callbacks | 10-105 |
| Demo tile/modal/menu bodies | 108-249 |
| Tour engine/navigation/positioning and exports | 250-574 |

`WT_STEPS` at 10 contains inline skip/target callbacks as well as copy. Separate step descriptors/demo factories from the engine, using imported entries; group a coherent tour sequence instead of creating a file for every sentence.

#### `static/js/settings-drill.js` — 1,059 lines

| Concern | Inclusive line ranges |
|---|---|
| Category registry and navigation/search | 1-205 |
| Settings load and engine fallback controls | 206-300 |
| All category screen bodies in one renderer | 301-945 |
| Face and backup scheduling settings | 946-1059 |

`SETTINGS_CATS` at 15 is metadata-only, but `_renderSettings` (301–945) embeds all six category bodies. Extract one render/hydrate unit per category and make the registry point at those imports. Keep drill/search state here.

#### `static/js/settings-sections.js` — 1,456 lines

| Concern | Inclusive line ranges |
|---|---|
| Local access/passcode | 1-175 |
| Remote access/status/device/session controls | 176-413;1002-1456 |
| PWA install and push settings | 414-680;843-1001 |
| Installed app/service worker diagnostics | 681-842 |

Separate local access, remote access/session management, push enrollment and installed-app diagnostics. Keep passcode/host-only rules attached to each operation; do not weaken them while moving UI.

#### `static/js/desk-v1-kit.js` — 1,348 lines

| Concern | Inclusive line ranges |
|---|---|
| Agent identity/cache | 1-137 |
| Vocabulary/badges/copy | 138-251 |
| Announcements/toast and Undo bus | 252-359 |
| Popover/menu/confirmation primitives | 360-609 |
| Planner box and task lifecycle | 610-1009 |
| Plan validation and approval bounds | 1010-1188 |
| Retro statistics and piece/evidence helpers | 1189-1348 |

A shared kit has accumulated planner task lifecycle, undo history, approval-bound validation and retro statistics. Move these to `desk-v1-agent-box.js`, `desk-v1-undo.js`, `desk-v1-plan-policy.js`, `desk-v1-retro-stats.js`; retain genuinely shared labels/popovers in kit.

#### `static/js/backup-panel.js` — 1,115 lines

| Concern | Inclusive line ranges |
|---|---|
| Shared shell/job state and archive creation | 1-579 |
| Full-install restore screen | 580-693 |
| Project import screen | 694-931 |
| Project export screen | 932-1115 |

Four independently changing flows share one file: full backup, full restore, project import, project export. Keep shell/jobs as shared service; create a module for each screen using a tab registry.

#### `mc/desk_publish.py` — 468 lines

| Concern | Inclusive line ranges |
|---|---|
| Receipt store | 1-204 |
| X transport and verification | 205-228;254-289;315-323 |
| LinkedIn transport | 229-253;290-314 |
| Shared verify/publish gate and dispatch | 324-468 |

X and LinkedIn transport/response validation coexist with receipt persistence and common publish authorization. Create `mc/desk_publish_x.py` and `mc/desk_publish_linkedin.py`; inject transport behind a registry while one common gate and receipt writer stays authoritative.

#### `mc/blueprints/guide_routes.py` — 1,276 lines

| Concern | Inclusive line ranges |
|---|---|
| Guide/workshop context and request dispatch | 1-470 |
| Scribe telemetry, continuity, positions and memory-search APIs | 471-668 |
| Onboarding/Ideas workspace seeding | 669-1012 |
| Brainstorm artifact transfer and idempotency | 1013-1276 |

Guide/workshop requests, memory/positions APIs, onboarding/Ideas seeding and Brainstorm transfer have different owners and safety constraints. Split route contributors plus `mc/onboarding_seed.py`; preserve transfer idempotency and project permissions.

### Sub-file bundles already counted in the large census

| Location | Concrete implementation bundle | Proposed seam |
|---|---|---|
| `mc/agent_runtime.py:1955-10816` | Eight provider classes and their inline registration calls, plus common process/protocol machinery | One imported provider per module; shared contracts/Mode A helpers; registry stores instances/factories |
| `mc/desk_engines.py:295-370,719-1183` | Descriptor table plus five adapter bodies: Higgsfield API, Higgsfield MCP, Veo, Gemini image, OpenAI image | Descriptor/adapter per engine module; small registry; common request/budget gate |
| `static/js/workflow-builder.js:2399-2685,4328-4396` | Agent, approval, action and wait editors; action field/render and required-field switches spread across the file | Imported block/action descriptors with implementations in their own modules; preserve server validation |
| `mc/workflows.py:1840-1961` | Six `_execute_action` branches: backlog create/patch, Desk harvest, journal append, notify operator, restore point | Action handlers behind a data registry; dispatcher retains policy/result protocol |
| `static/js/desk.js:825-2349` | Legacy Board, Queue/Review, Calendar, Ledger plus rework thread | Keep legacy entry facade; separate legacy screens only when changing them, avoid expanding retired UI |
| `static/js/desk-v1-studio.js:266-1506` | Studio library, storyboard editor, four source bodies, writer/review handoff and standalone image/video creation | Screen/component modules; a shared storyboard store and imported source registry |
| `static/js/claydo.js:764-2016` | Help handoff, artifact save workshop and persona editor | Separate editor/workshop modules, retaining shared modal/chat state |
| `static/js/skills-panel.js:370-1767` | Distiller review, skill editing, and paste/folder/Git/cross-project import implementations | Imported feature panels; shared quarantine/import client and human approval gate |
| `tools/provider-live/codex_run.py:1321-1409` | `mk_cells` builds scenario entries alongside all scenario implementations | Imported probe modules, one case per independently changing scenario; keep process ownership centralized |

These rows are **not** added again to the 37-file total. Provider tables, block vocabularies and action maps are opportunities to create implementation seams, not a demand to split every constant.

**Existing good boundaries to retain:** `static/js/desk-v1-shell.js:19-76` routes to separate Home, Project, Connections, Review, Video, Engagement and other renderers. That registry is primarily references, not embedded screen bodies. Studio/storyboard/create still converge on one file, as inventoried above. `settings-drill.js:15-22` categories and `workflow-builder.js:2447-2455` action labels are already data. `mc/desk_pieces.py`, `mc/desk_accounts.py`, `mc/desk_storyboard.py`, `mc/runtime_lifecycle_service.py`, `mc/runtime_attempt_owner.py` and the existing memory leaf modules are precedents to reuse. A report that called all of Desk or all runtime lifecycle one unsplit monolith would overstate the problem.

## 4. Top-ten split plans

These are proposals, not implemented changes. Target names below are new except explicitly named existing facades. A session means one scoped extraction, its focused tests, review fixes and documentation; estimates include an integration/review session, exclude feature work, and are engineering estimates rather than measured throughput. Keep behavior and stored formats unchanged. Do not combine a split with an API, security-policy or UI redesign. All new/moved `mc/` modules must pass scoped Pyright basic; all UI extractions need the relevant boot/interaction smoke on the extracted branch.

### 1. Agent routes: 8–12 sessions, high risk

- **Target files:** `mc/blueprints/agent_provider_routes.py` (discovery/auth/install), `agent_job_routes.py`, `agent_history_routes.py` (status/conversations/search/plans/documents), `agent_upload_routes.py`; `mc/agent_context.py`, `mc/agent_stream_reader.py`, `mc/agent_log_store.py`, `mc/agent_delegation.py`, `mc/agent_usage.py`, `mc/agent_guardian.py`. Move worktree teardown into the existing `mc/agent_worktree.py` owner where its lifecycle interface permits, rather than adding a second worktree service.
- **What stays:** `mc/blueprints/agent_routes.py` owns the dispatch/send/followup/interrupt HTTP facade and dependency composition initially. Keep ownership/generation checks connected to the existing `mc/runtime_attempt_owner.py` and `mc/runtime_lifecycle_service.py`; do not duplicate those services.
- **Seam:** explicit service interfaces and `register_routes(bp, services)` contributors. Reuse the original Blueprint initially so Flask endpoint names remain `agent_routes.*`. Extract provider installation and job routes first, then log reads, then context/stream/completion; do not move the state-machine core first.
- **Risks:** locks and shared dictionaries in `mc/state.py` must retain identity; delayed callbacks cannot retain a replaced session owner. Wiring order and test monkeypatch targets are real compatibility contracts. `tests/test_agent_routes.py:181-205` pins Blueprint ownership, not just URLs. Extracting functions with `from X import fn` can silently bypass tests patching the old owner. Agent-log quarantine, attend-once, provider fallback and exactly-once completion/notify semantics must survive. Any new sidecar must live outside project records or join the exclusion list.
- **Pinned checks:** `tests/test_agent_routes.py`, `test_agent_jobs.py`, `test_agent_log_durability.py`, `test_agent_spawn_notify.py`, `test_agent_send_non_claude_revive.py`, `test_runtime_lifecycle_project_generation_dispatch.py`, `test_runtime_completion_log.py`, `test_agent_worktree_teardown_guards.py`, `test_provider_architecture_guard.py`; run all relevant attendance/fence tests when those functions move. Update path-based allowlist entries to the exact moved functions, never widen the allowance.
- **Parallelism:** independent of Desk routes and CSS extraction; serialize against runtime-contract or server-wiring changes. Within this file, one extraction owner until stable contributor seams exist.

### 2. Application CSS: 4–6 sessions, medium risk

- **Target files:** under `static/css/`, `theme.css`, `dashboard.css`, `modal.css`, `settings.css`, `chat-rail.css`, `chat-composer.css`, `chat-transcript.css`, `chat-status.css`, `floor.css`, `scheduler.css`, `agent-log.css`, `memory-panel.css`, `tour.css`, `terminal.css`, `claydo.css`, `persona-editor.css`, `media.css`, `hivemind.css`, `desk-legacy.css`, `workflow-canvas.css`, `workflow-inspector.css`, `chat-popout.css`, `learn.css`. Treat these as extraction destinations, not a requirement to create empty files immediately.
- **What stays:** `app.css` becomes the ordered stylesheet entry point plus genuinely shared reset/primitives. A feature's responsive rules belong with that feature; an undifferentiated new `mobile.css` would recreate the same bundle.
- **Seam:** ordered CSS imports or explicit ordered links, with one owner of the load manifest. Start with contiguous Learn/popout/legacy Desk blocks. For interleaved rules, preserve relative selector precedence; do not casually reorder all desktop rules before all mobile rules.
- **Risks:** specificity and cascade depend on original order, including repeated selectors and late overrides. Extracting files changes URL bases if moved to a subdirectory. Preserve CSS custom properties, 960px/700px behavior, reduced motion, DOM retention and cached-page delivery. The CSS split does not itself fix global selector collisions.
- **Pinned checks:** `tools/smoke/boot-smoke.mjs`, `learn-floor.mjs`, `workflow-builder.mjs`, `drag-to-hire.mjs`, `desk.mjs` and affected mobile composer checks; compare representative desktop/phone screenshots before and after. Run on a fixture/static server serving the branch, not a live main checkout mistaken for the branch.
- **Parallelism:** can run beside backend extractions and the separate Desk CSS split. Serialize shared `index.html`/CSS-manifest wiring with #3 and have one person assign style order.

### 3. HTML entry point: 5–7 sessions, high risk

- **Target files:** `static/js/app-state.js`, `provider-cache.js`, `project-data.js`, `dashboard-view.js`, `project-modal-refresh.js`, `toast.js`, `session-metrics.js`, `settings-persistence.js`, `run-history-view.js`, `app-bootstrap.js`, `app-presence.js`; static shell fragments only if the existing HTML serving path can deliver them without changing boot behavior.
- **What stays:** semantic shell markup, minimal pre-paint theme/duplicate-tab guard, ordered library/module entry points and the reload-version notice. Move performance HUD to `static/js/perf-hud.js`. A file of script tags referencing already separate features is acceptable composition.
- **Seam:** extract classic-script state accessors first, then convert callers to explicit imports or deliberately preserved `window` accessors. `index.html:946-4063` is a classic script, unlike most loaded feature modules. Preserve this distinction during migration; moving lexical globals into an ES module alone breaks existing callers.
- **Risks:** module evaluation order/TDZ, inline event handlers, Capacitor native events, reconnect ownership, stale loaded pages, and preserved workflow/editor subtrees in `refreshModalById`. No duplicate timers, SSE connections or `openModals` stores. One owner controls the bootstrap entry and registration order.
- **Pinned checks:** `tools/smoke/boot-smoke.mjs` including dispatch guard, `workflow-builder.mjs`, `split-view-cross-project.mjs`, `agent-log-epoch-reconcile.mjs`, `provider-neutral-rail-cache.mjs`, `mobile-chats-agentlog-fetch.mjs`; `tests/test_entrypoint_boot.py` for server delivery. Explicitly exercise initial load and repaint during active editing.
- **Parallelism:** serialize against #5/#6 window/state changes and #2 load-order wiring. Pure new leaf modules may be prepared independently, but their cutover is sequential.

### 4. Desk CSS: 2–3 sessions, medium risk

- **Target files:** `static/css/desk-v1-shell.css`, `desk-v1-kit.css`, `desk-v1-home.css`, `desk-v1-campaign.css`, `desk-v1-review.css`, `desk-v1-calendar.css`, `desk-v1-video.css`, `desk-v1-conversations.css`, `desk-v1-results.css`, `desk-v1-map.css`, `desk-v1-where.css`, `desk-v1-what.css`, `desk-v1-studio.css`, `desk-v1-connections.css`, `desk-v1-evidence.css`.
- **What stays:** `desk-v1.css` becomes the ordered import manifest. Move the scattered 3031–3228 mobile overrides into the correct screen file only after a cascade-order comparison; a temporary explicitly ordered compatibility tail is safer than a blind reorder.
- **Seam:** mirror the existing JS screen ownership, preserving shared tokens/kit selectors. This corrects the current situation where screen JS edits are independent but all their styles append to one file.
- **Risks:** sticky actions, scroller height, Posy column sizing, popover clipping and late phone overrides. A stylesheet per area is not enough when that area contains eighteen independently changing surfaces; give the screens their own files.
- **Pinned checks:** `tools/smoke/desk-v1-home.mjs`, `desk-v1-campaign.mjs`, `desk-v1-review.mjs`, `desk-v1-calendar.mjs`, `desk-v1-studio.mjs`, `desk-storyboard-paste-trash.mjs`, `desk-connect-guides.mjs`, plus live-mode counterparts where applicable. Assert scrolling and footer accessibility at phone and desktop sizes, not just successful rendering.
- **Parallelism:** independent of backend #1/#7/#9/#10 and of `app.css` body edits once the style manifest contract is fixed. Do not run two Desk CSS movers against the same source concurrently.

### 5. Runtime providers: 6–9 sessions, high risk

- **Target files:** `mc/runtime_contracts.py`, `runtime_registry.py`, `runtime_transforms.py`, `runtime_protocol.py`, `runtime_mode_a.py`, `runtime_usage.py`; `mc/runtimes/claude.py`, `gemini.py`, `qwen.py`, `codex.py`, `opencode.py`, `goose.py`, `aider.py`, `kiro.py`, plus a small package initializer.
- **What stays:** `mc/agent_runtime.py` temporarily re-exports the documented API and owns composition/registration. Contracts and shared helpers must not import provider implementations; provider modules import only contracts/helpers. Keep existing lifecycle/attempt-owner modules authoritative.
- **Seam:** imported runtime classes behind the existing registry, preserving provider names, capabilities and instantiation order. First lift contract types/shared Mode A machinery, then one provider per extraction. The registry imports entries; it does not contain provider bodies.
- **Risks:** provider process/env differences, native transcript paths, costs and token scopes, unattended Codex fence and sandbox behavior, transform authorization, import-time auto-registration, and patches applied to facade globals. The allowlist in `tests/test_provider_architecture_guard.py:63,117-135` is path-sensitive; moving code must preserve the guard, not exempt a whole new directory.
- **Pinned checks:** `tests/test_claude_runtime.py`, `test_provider_runtimes.py`, `test_agent_runtime_sole_provider_default.py`, `test_cross_provider_handoff.py`, `test_authorized_runtime_bridge.py`, `test_provider_architecture_guard.py` and provider-specific fence tests. Existing monkeypatches of `installed_runtimes` (`test_agent_runtime_sole_provider_default.py:35`) must still intercept the caller or move to explicit injection.
- **Parallelism:** individual provider extractions can run independently only after the contracts/registry seam is landed and the facade registration edit has one owner. Serialize the initial seam with #1/#7; provider extraction can then coexist with their leaf work.

### 6. Conversation UI: 6–8 sessions, high risk

- **Target files:** `static/js/composer-person.js`, `composer-model.js`, `conversation-attendance.js`, `conversation-rail.js`, `channel-rail.js`, `topics-board.js`, `conversation-history.js`, `conversation-split.js`, `conversation-activity.js`, `conversation-lines.js`, `conversation-questions.js`, `conversation-send.js`.
- **What stays:** `conversation.js` composes `agentPanelHTML`, owns a narrow conversation-view contract, and temporarily exposes existing inline-handler entry points. Reuse existing `agent-log.js`, `resume-preview.js`, `composer-extras.js` and `chat-search.js`; do not absorb them into a new conversation megafile.
- **Seam:** pass view/session state through a single owner and expose narrow getters/setters. Extract Topics board first because it has its own window and clear public entry points; then rail/Channel, then split/history, lastly transcript/send/polling.
- **Risks:** top-level module variables are not globals; explicit window exposure is required until callers import modules. Preserve speaker attribution, optimistic/delivered message state, question DOM deduplication, date stamps, split panes across projects, attendance gates and timer cleanup. The prior `_channelPersonFilter`/`_channelExpanded` merge incident shows why syntax checks alone are inadequate.
- **Pinned checks:** `tools/smoke/boot-smoke.mjs`, `agent-stream-dedupe.mjs`, `split-view-cross-project.mjs`, `subagent-visibility.mjs`, `channel-working-project-scope.mjs`, `drag-to-hire.mjs`, `provider-neutral-rail-cache.mjs`, `agent-log-epoch-reconcile.mjs`. Run the relevant checks after each merge, before combining the next extraction.
- **Parallelism:** independent of backend leaf splits and Desk CSS. Serialize against #3's shared-state/modal cutover; avoid simultaneous composer edits in this original file.

### 7. Server composition: 4–6 sessions, high risk

- **Target files:** `mc/config_defaults.py`, `config_loader.py`, `startup_backfill.py`, `startup_memory.py`, `startup_worktrees.py`, `server_http.py`, `server_lifecycle.py`, `runtime_hooks.py`, `application_wiring.py`.
- **What stays:** `server.py` is the composition root: resolve paths, load config, construct Flask, register/wire services, then boot. A small registration list is legitimate; moving its list into a second monolith gains little.
- **Seam:** explicit `wire(dependencies)` and ordered startup tasks. Extract backfills and HTTP helpers before moving runtime hooks. Keep a compatibility facade only for actual importers; migrate tests and production callsites together.
- **Risks:** import-time effects, circular wiring, one-server port ownership, token creation ordering, start/stop ordering, frozen resource roots and hidden Windows launch behavior. Never start a second live server to validate an extraction; fixture boot tests and an isolated port/data root are required.
- **Pinned checks:** `tests/test_entrypoint_boot.py`, `test_port_conflict.py`, `test_guardrail_hooks_boot.py`, `test_load_projects_sidecar_exclusions.py`, `test_memory_turn_wiring.py`, `test_runtime_lifecycle_bridge.py`; frozen update tests when moving resource discovery. New/moved `mc/` modules must pass scoped Pyright basic without masking the existing baseline.
- **Parallelism:** leaf startup modules can run beside CSS/Desk work. Central wiring cutover must follow, not compete with, #1/#5/#9 service interfaces.

### 8. Workflow builder: 5–7 sessions, high risk

- **Target files:** `static/js/workflow-state.js`, `workflow-graph.js`, `workflow-palette.js`, `workflow-inspector.js`, `workflow-schedule.js`, `workflow-canvas.js`, `workflow-edges.js`, `workflow-review.js`, `workflow-run-view.js`; `workflow-blocks/agent.js`, `approval.js`, `action.js`, `wait.js`, plus `workflow-blocks/registry.js` containing imported descriptors only. Give action-specific field/render/validate behavior entries their own modules as those actions evolve.
- **What stays:** `workflow-builder.js` mounts the one editor and composes these parts. `_wfState` remains one store, not a copy per module. DOM snapshot/undo logic must share that store.
- **Seam:** descriptors provide defaults, ports, editor rendering, own-field serialization and validation. Separate graph helpers from DOM operations. Keep server-side `mc/workflows.py` validation authoritative; client validation is feedback, not authorization.
- **Risks:** node/edge references, branching `otherwise` port, inspector versus box representation, pointer/touch coordinates, undo boundaries, unsaved draft behavior, late async review results, and preservation through project-modal repaint. Data tables at 2447/4328 are not themselves bad; adding a block currently also requires edits to several body switches in the same file.
- **Pinned checks:** `tools/smoke/workflow-builder.mjs`, `tests/test_workflows.py`, `test_workflow_step_reconcile.py`, `test_workflow_runtime_notify.py`; exercise save/reopen, drag/wire, undo, approval and run cancel. Keep the new Learn Workflows owner's integration separate from this refactor.
- **Parallelism:** graph helpers and backend-independent Desk work can run together. Initial state seam precedes block-owner parallel work; `index.html` mounting changes serialize with #3.

### 9. Memory engine: 7–10 sessions, very high risk

- **Target files:** `mc/memory_paths.py`, `memory_sections.py`, `memory_links.py`, `memory_positions.py`, `memory_continuity.py`, `memory_search.py`, `memory_mint.py`, `memory_negation.py`, `memory_writer.py`, `memory_checkpoint.py`, `memory_scribe.py`, `memory_telemetry.py`, `memory_condense.py`.
- **What stays:** `mc/memory.py` is a compatibility/wiring facade. Existing `memory_fts.py`, `memory_turn.py`, `memory_push.py`, `memory_delivery.py` remain separate owners. Do not conflate the learning Distiller with Scribe or replace established retrieval with a new mechanism.
- **Seam:** extract pure parsing/ranking first; then continuity/positions, finally writer/Scribe/checkpoint/condense. One writer owns lock acquisition, watermark merge, byte caps and atomic replace. Pass path/provider dependencies explicitly so tests cannot accidentally touch real memory.
- **Risks:** lock ordering, byte versus character counts, archive/curated separation, live-watermark ownership, origin/authority checks, negation/supersession behavior, per-owner continuity, token accounting, and callback/provider context. `tests/test_memory_module.py:350` patches `_scribe_call`; a moved implementation cannot silently ignore that patch. New per-project sidecars must preserve `EXCLUDED_SIDECAR_SUFFIXES` in project loading.
- **Pinned checks:** `tests/test_memory_module.py`, `test_memory_search_bm25.py`, `test_memory_link_graph.py`, `test_memory_index_cap.py`, `test_memory_provider_transform.py`, `test_memory_v2_step7.py`, `test_memory_v2_step8.py`, `test_load_projects_sidecar_exclusions.py`, and `test_distiller_safety.py` whenever origin/authority boundaries are moved. Read `docs/MEMORY_SYSTEM.md` and `docs/SKILLS_CURATION_PHASE4_SPEC_V2.md` before implementation.
- **Parallelism:** pure parsers can coexist with unrelated UI/backend work. Serialize writer/checkpoint/condense changes; do not split memory writers and server memory wiring on unrelated branches at the same time.

### 10. Desk routes: 3–5 sessions, high risk at human-only seams

- **Target files:** `mc/blueprints/desk_campaign_routes.py`, `desk_voice_routes.py`, `desk_piece_routes.py`, `desk_storyboard_routes.py`, `desk_account_routes.py`, `desk_engine_routes.py`, `desk_engagement_routes.py`, `desk_retro_routes.py`, `desk_planning_routes.py`.
- **What stays:** `desk_routes.py` composes the contributors and minimal overview/presence routes. The existing leaf modules (`desk_pieces.py`, `desk_accounts.py`, `desk_storyboard.py`, `desk_engines.py`, `desk_retro.py`, etc.) retain their business logic. The large `mc/desk.py` store is a separate later split, not a prerequisite for route separation.
- **Seam:** register contributors against the existing Blueprint using injected agent dispatch, account lookup and human-proof dependencies. A later Blueprint-per-group migration must explicitly preserve all URL/method/decorator behavior and update endpoint ownership tests.
- **Risks:** human-only decorators must remain on publishing, approval, credentials and spend operations; import relocation cannot detach them. Preserve campaign hash binding, optimistic revision checks, upload containment, idempotency and agent-choice behavior. Do not relocate credential values into settings or split secret usernames from their vault entries.
- **Pinned checks:** `tests/test_desk_routes.py`, `test_desk_review_routes.py`, `test_desk_start_gate.py`, `test_desk_approval.py`, `test_desk_accounts.py`, `test_desk_storyboard.py`, `test_desk_engines.py`, `test_desk_engagement_pane.py`, `test_desk_retro.py`; affected `tools/smoke/desk-v1-live-*.mjs` and `desk-connect-guides.mjs`.
- **Parallelism:** independent of #1/#5/#9 and both CSS body extractions, provided central registration is handled once. Coordinate with the existing Connections owner; this report does not assign a competing implementation.

## 5. Order, first split and parallel lanes

**First split: separate the Floor lesson from Learn's engine.** Create `static/js/learn-lessons/floor.js` for its descriptor, selectors, prepare/verify hooks and offer; put imported descriptors in `static/js/learn-lessons/registry.js`. Keep `learn.js` as progress/cue/navigation engine and `learn-practice.js` as the practice transport. Inject practice adapters rather than letting new lessons add more `if (step.id === ...)` branches to the engine. Move the Floor-only hub/offer assumptions too: extracting just the literal `LESSONS` object does not complete the split.

Estimate **1–2 sessions**, with `tools/smoke/learn-floor.mjs` proving the same evidence-based completion, resume, mobile Hire, failure handling and zero real-project mutation. This ranks below the hottest files because it has only three merged changes so far, but it removes the immediate Floor/Workflows lesson scheduling bottleneck at low blast radius. The snapshot contains one Floor lesson, not two already-shipped lessons. Coordinate this seam with the existing Workflows lesson job instead of starting a second lesson implementation. The first high-volume split after that is Desk CSS, whose screen boundaries already exist.

| Lane | Can run alongside | Must be serialized |
|---|---|---|
| Learn descriptor/engine seam | Backend leaf extraction; Desk CSS | Existing Learn lesson writer; one owner of `learn.js` |
| #4 Desk CSS | #1 agent leaf services; #9 pure memory parsers; #10 Desk route contributors | Other edits to `desk-v1.css`; manifest cutover |
| #2 app CSS | #4 once manifest ownership fixed; backend work | #3 HTML stylesheet wiring |
| #1 agent routes | #10 Desk routes; independent CSS | #5 runtime contracts and #7 central wiring |
| #5 provider adapters, after contracts | Each other in separate provider files; CSS | Shared registry/facade edits and Mode A contract changes |
| #6 conversation UI | #8 pure workflow graph helpers; backend leaf work | #3 shared state/modal refresh; composer edits |
| #8 workflow block bodies, after state seam | Each other in separate block files | Shared store/undo and builder registry cutover |
| #9 memory pure helpers | Unrelated frontend/Desk services | Writer/checkpoint/condense; #7 memory startup wiring |
| #7 server root | Only leaf preparation with agreed injection API | All actual central service registrations |

This is a dependency/ownership map, not authorization to spawn multiple workers. One owner performs each shared-entry-point cutover. Run affected smokes **after each merge and before the next**, including independent branches: a clean Git merge does not establish runtime compatibility. On a worktree, use the existing main-checkout smoke dependencies through a removable link; do not install a second browser stack or recursively delete the junction target.

The ten complete splits total **50–73 scoped agent-sessions** before parallel savings; Learn adds 1–2. Do not launch that as a single program by default. Take the first split, measure the next fortnight's shared-file touches, and select the next independently useful extraction. Session ranges are not labor-hour estimates and do not justify a rewrite.

## 6. Validation and limits

- Source snapshot, all inventory line bounds, scores and history counts were checked mechanically; representative changed functions were checked with zero-context merge diffs. No source, config, credentials, runtime data or test fixtures were changed.
- Every >1,500-line eligible file is inventoried. The eight additional smaller bundles are a confirmed lower bound from registry/screen/provider searches, not proof that the other eligible files contain no bundles. Counts are file-level bundles, not all possible pairs of concerns or all possible target modules.
- The 363 concern groups in large files and 36 in smaller files are review judgments with explicit ranges. Adjacent helpers and setup remain with their owning concern; scattered intervals are deliberately shown. These are not 399 mutually independent jobs. Some large functions still contain several sub-concerns, particularly the browser pane's event-handler closure.
- Git records final commits/trees and ancestry, not failed local merges, time lost waiting, abandoned branches, agent start/end times or every conflict resolution. Same-day divergent tips establish overlapping branch histories, not proof that agents executed simultaneously. Conflict notes provide a lower bound. File co-change does not by itself imply unwanted coupling.
- Refactor test lists are proposed acceptance gates; **application tests were not run** for this documentation-only audit. None of the proposed splits is implemented or verified. The report is committed only on `entanglement-audit`; no merge, push, server restart or runtime launch is part of this task.
