# Fable-1 — independent re-derivation of the load-bearing numbers

**Date:** 2026-08-16 · **Reviewer:** independent numbers audit (different model
from all seven seats) · **Backlog item:** `dae8d6e7`
**Target claims:** the eight measurements the S0–S12 / hold-S8–S10 decision rests on.

**Method rules.** Nothing in `mc/`, `MEMORY.md`, the memory dir, or config was
modified. All probes are in `_scratch/fable1/` (`harness.py`, `turns.py`,
`delivery.py`, `channels.py`) and wire `mc.memory` directly — **`server` is never
imported** (confirmed: 0 `cloudflared` lines in probe output), because
`tools/memory-eval/scorer_ab.py:130` does `importlib.import_module("server")`,
which seat 6 measured reaping the live tunnel. I therefore did **not** run
`scorer_ab.py`; every replay below calls the production `_memory_search` /
`_mem_corpus` / `_mem_split_full` through my own harness. Task extraction is
byte-identical logic to `scorer_ab.py` (`first_user_task`, `MIN_TASK_CHARS=25`).
Corpus at measurement time: **2,567 units** (topic 74, managed 16, archive
2,477); MEMORY.md 23,270–23,474 B across the session (volatile, as B5 warns).
Both briefed traps were respected: no transcript-grep for injected floor text;
86 trivial follow-ups excluded (and my `CLAUDE_HOME` points at
`~/.claude/projects`, the seat-7 trap).

Numbers drift day-to-day because the corpus and transcript set grow (268
transcripts today vs 259/264/266 in the seat runs). I treat a claim as
CONFIRMED when my number matches within that drift and the *decision* it
supports is unchanged.

---

## 1. Verdict table

| # | claim | my measurement | verdict |
|---|---|---|---|
| 1 | 5% of sessions read a topic file, 3% search, 66/76 never opened | `python tools/memory-eval/retrieval_probe.py` (pure stdlib, safe): 268 sessions, **13 read (5%), 7 search (3%), 10 distinct / 23 opens (10 = memory.md itself), 66/76 never opened** | **CONFIRMED** — exact |
| 2 | live dark = 15/74, not 30; probe omits `expand=` | probe omission read from code (`scorer_ab.py:32` `TOPK=3`, `:161` no `expand`; production `agent_routes.py:1917-1920` passes both). Replay, 179 real tasks: topk=3/expand=0 → 47/74, dark 27; **live topk=3/expand=2 → 59/74, dark 15**; zero-result 0 | **CONFIRMED** |
| 3 | `read_floor_topk` live 3, code default 6; restore → dark 8 | `curl /api/config` → 3; `config.json:49` → 3; `server.py:191` → 6. Replay topk=6/expand=2 → **66/74, dark 8** (my dark-8 list matches seat 4's file-for-file) | **CONFIRMED** |
| 4 | curated region NOT in the ranker corpus | `mc/memory.py:565-567`: for MEMORY.md only `_mem_split(txt)[1]` (managed entries) is indexed; curated = `[0]`, discarded. Docstring at 605-607 says so explicitly. Live split: curated **16,820 B = 72.3%** of 23,270 B, 96 pointer lines | **CONFIRMED** (pivotal claim, verified in code) |
| 5 | 13 task-less recovery/respawn call sites; 84% of turns without a fresh floor | grep: exactly **13** bare `_build_agent_context(p)` sites in `agent_routes.py` (2788, 3049, 3061, 4853, 4859, 4869, 4885, 4981, 5109, 5236, 5482, 5488, 5542); gate `if task:` at 1915; 2788 has `task` in scope one line above; 4981 stashes the task-less context into `existing['_system_prompt']`. Turn count: **1,373 of 1,638 real user turns (84%)** served turn-1's floor | **CONFIRMED** — with two footnotes (§2.1) |
| 6 | `fold` 117 lifetime ops vs 96 present lines; `curated_rewrite` rejected | `data/projects/mission_control_scribe_stats.json` → `condense_entries_folded: 117`; live curated pointer lines = **96**; `memory.py:2006-2007` → `curated_rewrite_forbidden_v1`; comment at 2186: "additive-only fold has no mechanical eviction path until v2" | **CONFIRMED** |
| 7 | floor = cap−1024 prevents the condense trigger from firing; zero `[condense]` log lines | Byte half confirmed: `_index_byte_floor() = cap − 1024 = 23,552` (memory.py:908-909), `over_bytes` trigger `> 24,576` (833) — unreachable while the floor evicts. **But the trigger is two-part** (`over_lines OR over_bytes`, 828-834): the line trigger (160) sits **below** the line floor (185), so it can fire. Log: **0** `[condense]` vs 49 `[mem-dedup]` / 17 `[wm-gc]` in `data/logs/clayrune.log` | **PARTIALLY CONFIRMED / seat 7's F5 correction stands** (§2.2) |
| 8 | Seat 7's killer: 67 evicted lines delivered on 0 of 160 tasks; 30/67 no surviving channel | Fully re-derived, not re-run (§3): tier split reproduces **exactly** (T1 17/3,203 B, T2 12/1,971 B, T3 67/10,849 B); M5 passes (unit delta = 67) while **0 of 67 lines are delivered on any of 179 tasks** at the live signature; self-query 67/67 (probe valid); best-ever rank min 8 / median 105 / max 745, **zero lines ever reach rank ≤ 6**; **29 of 67 have no surviving delivery channel** (3 linked + 26 link-less; seat 7: 5 + 25 = 30) | **CONFIRMED** (29 vs 30 is task-set drift; same casualties by name) |

Supporting arithmetic checked: 3,203 + 1,971 = 5,174 B resident = 63% of the
8,192 B cap; 10,849 B ÷ 4 ≈ 2,712 tokens. All hold.

---

## 2. Detail where the verdict needs nuance

### 2.1 Claim 5 — two footnotes, neither fatal

- **The "84%" is definitional given the code, and the code premise holds for
  Claude sessions only.** Follow-up (`agent_routes.py:4813`) and interrupt
  (`:5404`) paths DO rebuild the stash with `task=message` — but both sit under
  `if session_provider != 'claude'`. For Claude sessions (the overwhelming
  norm here) follow-ups go via stdin and the floor is frozen, so the 84%
  arithmetic (total turns − sessions = stale turns) is sound.
- **Seat 7's "minor corrections" to this claim are themselves wrong, twice.**
  (a) Its 14th call site, `guide_routes.py:587`, is a **comment**, not a call —
  seat 1's 13 is correct. (b) It says `_RESPAWN_TRIGGER_KEYS` "holds 8 keys,
  not 9" — the set in `settings_routes.py` holds **9** (count them:
  agent_model, agent_effort, agent_max_turns, agent_permission_mode,
  agent_channels, agent_remote_control, use_streaming_agent,
  activity_states_enabled, brief_replies_always_enabled). Neither error touches
  seat 7's main findings, but they show even the adversarial seat is not
  self-checking its asides.

### 2.2 Claim 7 — the synthesis row is right for this project, wrong as a mechanism

Read from `memory.py:819-858` and `988-993`, verbatim:

- `_should_condense` (structured): trigger = `n_lines > 160` **OR**
  `bytes > 24,576`.
- `_over_floor`: evict when `lines > 185` **OR** `bytes > 23,552`.

So the **byte** deadband is exactly as the synthesis says (floor 1,024 B under
the trigger — the floor's success starves the trigger). The **line** trigger is
the opposite shape: 160 < 185, it fires first. mission_control is quiet on both
only because at ~174 B/line the byte floor binds at ~134 lines, under the line
trigger — a density coincidence, as seat 7 said. Cross-project sizes reproduce
seat 7's table: DayTrading **203** lines / 18,124 B, ApexTrader **188** /
19,367, FL3-V2 **183** / 18,193, mission_control 134 / 23,474 (all measured
`wc` this run) — three projects are over the line trigger *today*.

**One correction to seat 7 itself:** its F5 says the `[condense]` trigger-path
log sites are "failure/warning only," reviving retraction #2's caveat. False in
current code: **`memory.py:2226` logs `[condense] <pid>: structured ok` on
success.** So "0 `[condense]` lines in the window" genuinely means condense did
not run in the window (not merely "no failures") — the synthesis's *evidence*
is better than seat 7 allows, while its *mechanism sentence* is still only the
byte half. Also note `condense_structured_ok: 46` lifetime in
mission_control's stats — condense has fired 46 times historically, so "cannot
fire" was never true as a lifetime statement. S10's deadband fix must address
the line trigger too, exactly as seat 7 concludes.

### 2.3 Claim 8 — how my derivation is independent

I did **not** re-run seat 7's scripts. I re-implemented the pipeline from the
design doc's own prose: seat 5's T1 lexicon (regex copied from
`_scratch/seat5/allocate.py`, which I also read — confirming F3 by inspection:
`for t in (1, 2)` never considers T3 for admission, and the file does
`importlib.import_module('server')`, confirming the reaper fired during the
hivemind), my own dark-at-(6,2) set recomputed from scratch, my own df≤3
recovering-unit computation, my own corpus-B construction (memory-dir copy in
`_scratch/fable1/memcopy/`, 67 lines stamped `- [2026-08-16] `, production
`_mem_corpus` over the copy via monkeypatched path getters), and
snippet-membership matching to attribute archive-unit deliveries (biased, if at
all, *toward* finding deliveries — which makes the 0/67 result conservative).
Falsification controls all passed: self-query 67/67 at top-3; topic hit-sets
identical in both arms (59 = 59), so no displacement artifact; unit delta
exactly 67 (M5 green while delivery is zero — F1's exact point).

The no-channel casualty list matches seat 7's by name: IPv6 dual-stack,
PowerShell Setup-Node pipeline pollution, Session-JWT entitlement fails-open,
AskUserQuestion `mc:question`, CLI 2.1.206 `-r` system-prompt drop, Android
APK/WebView, GCP egress. These are the "gotchas that cost hours" class; under
S8–S10 as written they would stop being delivered anywhere.

---

## 3. What this does to the recommendation

**Nothing I measured weakens the S0–S6 + S12 approval; everything I measured
strengthens the S8–S10 hold.**

- The push/pull asymmetry (claims 1–3) reproduces exactly, so Constraint P and
  the ordering constraint stand on solid numbers.
- The two cheap wins are real as stated: `read_floor_topk` 3→6 is live-editable
  (`_CONFIG_EDITABLE_KEYS` contains it; not a respawn-trigger key) and takes
  dark 15 → 8 on my replay. Claim 4 (curated excluded from the corpus) is
  verified in code and is the correct central insight.
- Seat 7's falsification of the eviction half is not just reproduced but
  reproduced **independently, with a differently-built probe, to within one
  line** (29 vs 30 no-channel). Its conclusion — M5 is a plumbing assertion,
  not a delivery guarantee; residency is load-bearing for ~30 lines — is the
  strongest-evidenced statement in the whole package. S8–S10 must not land
  without a per-line **delivery** gate (rank-based, not membership-based).
- Two of seat 7's side-corrections are wrong (13-not-14 call sites; 9-not-8
  respawn keys) and one of its F5 sub-claims is wrong (success *does* log),
  but none of the three carries any decision weight.

**Verdict: approve S0–S6 + S12; hold S8–S10; defer S7 — unchanged, now
independently re-derived.**

---

## 4. Every number's provenance

| number | exact source |
|---|---|
| 13/268 read, 7/268 search, 66/76 never opened | `python tools/memory-eval/retrieval_probe.py` (verified stdlib-only before running) |
| 47/59/66 reachable, dark 27/15/8, zero-result 0, top-3 48/32/26% | `_scratch/fable1/harness.py` — production `_memory_search`, 179 tasks, denominators = 74 topic files |
| read_floor_topk 3 vs 6 | `curl -s localhost:5199/api/config`; `config.json:49`; `server.py:191` |
| curated exclusion | read of `mc/memory.py:540-599` (esp. 565-567) |
| curated 16,820 B / 72.3% / 96 pointer lines (59 `- [` + 37 `- `) | production `_mem_split_full` on live MEMORY.md, inline script |
| 13 call sites; gate at 1915; 4981 stash | `grep -n "_build_agent_context(" mc/blueprints/*.py` + context reads at 2788, 4795-4830, 4975-4990, 5390-5412 |
| 84% (1,373/1,638) | `_scratch/fable1/turns.py`, 268 transcripts |
| fold 117 / folded-vs-96 | `data/projects/mission_control_scribe_stats.json`; `memory.py:2006-2007, 2186` |
| condense triggers/floors | `memory.py:819-858, 900-909, 988-993`; `[condense]` sites via grep incl. success log at 2226 |
| 0 `[condense]` / 49 `[mem-dedup]` / 17 `[wm-gc]` | `grep -c` over `data/logs/clayrune.log` |
| cross-project line/byte table | `wc -l` / `wc -c` over `~/.claude/projects/*/memory/MEMORY.md` |
| T1/T2/T3 = 17/12/67; 0/67 delivered; best rank min 8 / med 105; M5 delta 67; self-query 67/67 | `_scratch/fable1/delivery.py` |
| 29/67 no surviving channel (3 linked + 26 link-less) | `_scratch/fable1/channels.py` |
| S3 no-restart | `settings_routes.py:102-176` (`read_floor_topk` at 110; `_RESPAWN_TRIGGER_KEYS` at 168) |

**UNVERIFIED (not tested, stated rather than implied):** seat 5's CLAUDE.md
prohibition-growth table (§4.1) and the 2026-08-06 survival rates (git
archaeology, not re-run); seat 2's 2,691 B/day archive growth rate; seat 4's
gold-set n=9 numbers (already flagged "reported, never gated" — I did not
re-derive them); the ~344-tokens/prompt cost of topk 3→6 (chars÷4 estimate —
mechanism plausible, not re-measured); seat 6's measured reaper output (I
verified the import chain and `reap_orphans(keep_pid=None)` exists, but did not
reproduce the teardown — deliberately, on the live system).
