# Seat 7 — adversarial review of the memory redesign

**Hivemind** `hm_ea8bd971`, workstream `ws_007` · **2026-08-15** · backlog `dae8d6e7`
**Target:** `docs/MEMORY_REDESIGN_2026-08.md` (§1–13 seat 5, §14 seat 6) and the
supporting seat files.
**Scope:** analysis only. No production code, no `MEMORY.md`, no memory-dir file
was modified. All probes are read-only and live in `_scratch/seat7/`.

**Brief:** try to falsify the design before Ron reads it. This item has three
documented retractions in one day, all from treating a convenient measurement as
an answer without checking what it measured.

---

## 0. Verdict in one paragraph

**The diagnosis is sound and the retrieval measurements reproduce.** I re-derived
the dark-set sweep independently and got within 3 files of every published
figure; the "30 dark files → 15" correction is real; Constraint P is real;
`read_floor_topk` really is live at 3; S3 really does need no restart. **The
eviction half does not survive.** The design's safety case for evicting 67
curated lines rests on two measurements that do not measure delivery, and its one
hard gate against silent deletion (M5) returns green in exactly the scenario it
exists to catch. Measured: all 67 evicted lines are delivered on **0 of 160**
real tasks, and **30 of 67** have no surviving delivery channel of any kind.
Separately, the residency rule's prose and the code that produced its headline
numbers implement different algorithms, and the rollback mechanism the plan
depends on from S5 onward is not a rollback.

**Recommendation: approve S0–S6 and S12. Do not approve S8–S10 as written.**

---

## 1. Method, and my own error

Everything below was measured on this box today with a **server-free harness**
(`_scratch/seat7/harness.py`) that wires `mc.memory` directly — production
`_mem_corpus`, `_memory_search`, `_mem_tokens`, `_mem_split_full`,
`_mem_class_avgdl` — with `state.CONFIG` loaded from `config.json`. It never
imports `server`, so it does not trip the cloudflared reaping seat 6 found.

**I made the error this brief warns about, and caught it.** My first delivery run
reported "100% of evicted lines delivered zero times" — and it was an artifact:
I had wired `CLAUDE_HOME` to `~/.claude` when `_native_memory_path` expects the
`projects` dir, so `_memory_search` returned `[]` for everything. The tell was
that topic reachability was 0/74 in **both** arms, which is impossible. That run
is discarded. Every number below was produced after the fix and passed an
explicit falsification check, which I state alongside it.

A second artifact, also caught before reporting: `_memory_search` labels **all
2,476 archive units** `MEMORY_ARCHIVE.md`, so counting deliveries per label
measures "some archive line was delivered," not "this line was delivered." The
v2 probe gives each non-topic unit a unique label and asserts the topic hit-set
is unchanged (56 vs 56, identical) to prove relabelling is score-neutral.

Task set: 160 real dispatched tasks (first user message ≥ 25 chars, this
project's transcripts) — the same extraction `scorer_ab.py` uses; it gets 177
because it sweeps all project dirs. Live signature `topk=3, expand=2`.

---

## 2. Numbers I re-verified — what holds

| claim | source | my measurement | verdict |
|---|---|---|---|
| dark 30 → **15** at live config; probe omits `expand=` | §5 | reproduces: 31 dark at `topk=3/expand=0`, **18** at `topk=3/expand=2` (n=160 vs their 177) | **FINE** — correction is real |
| `topk=6/expand=2` → dark 8 | §5 | **9 dark, 65/74 reachable, top-3 27%** | **FINE** |
| top-3 concentration 49% → 33% → 26% | §5 | **50% → 33% → 27%** | **FINE** — near-exact |
| S3 gate: M3 rises ≥ +5 files | S3 | measured **+9** (56→65) | **FINE** |
| `read_floor_topk` live = 3, shipped default 6 | §3 | `GET /api/config` → 3 | **FINE** |
| S3 needs no restart | S3 | `read_floor_topk` ∈ `_CONFIG_EDITABLE_KEYS` (`settings_routes.py:110`), ∉ `_RESPAWN_TRIGGER_KEYS` | **FINE** (the set holds 8 keys, not 9) |
| curated region excluded from `_mem_corpus` | §3 | confirmed, `memory.py:565-567` — only the managed region is indexed | **FINE**, and it is the key insight |
| archive filter is `- [`, not `- ` | §5.4, correction #5 | confirmed `memory.py:570` | **FINE** — this catch is real and important |
| `read_floor_topk=0` is not a kill switch | §3 | confirmed `scored[:max(1, topk)]`, `memory.py:693` | **FINE** |
| tiering T1/T2/T3 = 17/12/67, 96 lines / 16,023 B | §4.3 | reproduced exactly with seat 5's rule | **FINE** as arithmetic |
| `git revert` is not a rollback for the memory dir | §14.0.1 | confirmed — `git check-ignore` says outside repository | **FINE** |
| `data/memory-eval/` not currently gitignored | S1 | confirmed, no matching rule | **FINE** |
| `reap_orphans(keep_pid=None)` kills every ledger PID | §14.0.2 | confirmed `mc_remote/cloudflared.py:317` | **FINE** — and seat 5's own `allocate.py` does `import server`, so it fired during this hivemind |
| MEMORY.md volatility (hundreds of bytes/hour) | B5 | 23,547 → 22,662 → **22,286** across today | **FINE** — good discipline to report, not gate |

**Minor variances, not defects:** seat 1's "13 task-less `_build_agent_context`
call sites" is **14** by my count (`agent_routes.py` 2788, 3049, 3061, 4853,
4859, 4869, 4885, 4981, 5109, 5236, 5482, 5488, 5542 + `guide_routes.py:587`).
Seat 2's "660 B per watermark" measures **320 B** for the single live marker
(1.4% of the index, not 2.9%) — which only strengthens seat 6's correct call to
**defer S7**.

---

## 3. CONFIRMED DEFECTS

### F1 — M5, the one hard gate against silent deletion, passes while delivery is zero

**Severity: blocking for S8–S10.**

The design says evicted lines go "into a channel that already works" and "remain
retrievable," and gates that with **M5: unit-count delta == lines evicted**,
described as "the difference between an eviction and a deletion" (§14.7.3).

M5 proves a line **became a unit**. It does not prove the unit is ever
**delivered**. So I measured delivery.

`_scratch/seat7/delivery_probe.py` — built arm B as the live corpus plus the 67
evicted lines stamped `- [2026-08-16] ` into archive line-form, exactly as §5.4
specifies, then replayed the read floor over 160 real tasks.

```
corpus A 2,565 units → corpus B 2,632 units, delta = +67     ← M5 PASSES
evicted lines delivered on ZERO of 160 tasks : 67 of 67 (100%)
mean delivery rate per evicted line          : 0.00%  (was 100%, resident)
```

**Falsification checks, run before reporting** (`verify_units.py`):

1. **Verbatim self-query:** all 67/67 injected units retrieve *themselves* at
   rank 0–1, top-3 67/67. The units are real, tokenised, scoreable. The probe is
   not broken.
2. **Best rank ever achieved over 160 real tasks, full ranking not top-k:** all
   67 score somewhere, but best-ever rank is **min 7, median 109, max 814** out
   of 2,632. **Zero lines ever reach rank 6.** Two ever reach 7–20; 37 never
   better than rank 100.
3. Topic reachability identical in both arms (56/74), so this is not
   displacement noise.

**Why, structurally:** a curated pointer line is a ~170-byte *summary* of a topic
file. It shares vocabulary with both the topic file it points to and the richer
archive entries about the same event. Both outrank it on every realistic query.
It wins only when the query is the line itself.

**Consequence:** S9's per-tranche ABORT ("M5 delta != lines evicted → stop the
entire stage") can never fire. And **M1 does not cover this either** — M1 is
defined over *notes* (topic files), and an evicted curated *line* is not a note,
so the invariant is structurally silent on the only thing S9 changes. The plan's
two hard knowledge gates are both blind to its one irreversible-in-effect move.

### F2 — 30 of 67 evicted lines have no surviving delivery channel; §4.4 measures vocabulary, not delivery

**Severity: blocking for S9.**

I gave the design its strongest defence: an evicted line is fine if the
*knowledge* still arrives by another channel — the target note for a linked line,
or §4.4's "recovering unit" for a link-less one. `_scratch/seat7/redundancy2.py`,
same 160 tasks, live signature, with the relabelling control described in §1.

**A. Linked lines (39 of 67)** — is the target note ever delivered?

| target delivered on | count |
|---|---|
| 10+ of 160 tasks | 10 |
| 3–9 | 11 |
| 1–2 | 13 |
| **never** | **5** |

The five: `arch_subsystems.md`, `arch_misc_tips.md`,
`discovery_mode_a_sse_followup_race.md`, `decision_mcp_fleet_efficiency.md`,
`remote_server_restart.md`. The MCP-fleet one documents a **live feature toggle**
and is cited twice in the index.

**B. Link-less lines (28 of 67)** — "the line *is* the knowledge." I recomputed
§4.4's recovering unit for each, then asked whether that unit is ever delivered.

> **25 of 28 recovering units are delivered 0 times in 160 tasks** — with
> concentration = 1.00 for most of them. §4.4's test passes perfectly while
> delivery is zero.

Casualties include the IPv6 dual-stack ~200 ms line, PowerShell `Setup-Node`
pipeline pollution, Session-JWT `entitlement.py` fails-open, the AskUserQuestion
`mc:question` protocol line, `CLI 2.1.206 -r` drops system-prompt, Android Auto
Backup / Keystore undecryptable prefs, and the tunnel 1k/account capacity limit.
These are the "gotchas that cost hours" class. **§5.4 names four of them as the
lines its archive-stamp fix rescues.** It rescues them as *units*; it does not
deliver them.

**Total: 30 of 67 evicted lines have no delivery channel at all.**

**The methodological point, which is why this belongs in this item's history.**
§4.4 tests whether a line's `df ≤ 3` tokens *concentrate* in some unit — a
vocabulary statement. It is then used to license eviction, which requires a
*delivery* statement. They disagree by 25/28. The 2026-08-05 retraction on this
very item says vocabulary overlap "said all covered — worthless, common words
dominate," and that the fix was to test against **actual retrieval units**. §4.4
did test against real units, but stopped at membership rather than rank. Same
family of error, one step further along.

**Honest caveat, stated so this is not itself a retraction later:** "never
delivered across 160 historical tasks" is not "never deliverable." A future task
about IPv6 would surface the IPv6 archive line. The asymmetry is the point:
resident = delivered on 100% of prompts unconditionally; evicted = delivered only
if a future query beats 2,631 competitors for 3 slots, and historically that has
never happened for these 30.

### F3 — the RESIDENCY RULE's prose and the code behind its headline numbers are different algorithms

**Severity: blocking for S8.**

§4.3 states: *"Lines are admitted in strict priority order until the cap is
reached; the remainder is evicted."* It then defines **T3 — everything else.
Evicted.** Those are not the same algorithm. `_scratch/seat5/allocate.py`
implements the second (`for t in (1, 2)`): T3 is never considered for admission
even when the cap has room.

Measured, same rows, same `CAP = 8192`:

| variant | resident | % of cap | evicted | saving |
|---|---|---|---|---|
| (a) as implemented — admit T1, T2 only | 29 lines / 5,174 B | 63% | **67 / 10,849 B** | ~2,712 tok |
| (b) rule as written — admit T1, T2, T3 until cap | 52 lines / 8,132 B | 99% | **44 / 7,891 B** | ~1,972 tok |

The doc leaves **3,018 B of its own cap deliberately empty while evicting 23
lines that would fit**. The headline saving is overstated by **37%** relative to
the rule stated beside it. An implementer working from the prose gets (b), and
S8's gate ("reproduces 17/12/67 within ±2 lines per tier") then fails —
correctly, for the wrong reason.

**And the design does not implement its own §4.1 conclusion.** §4.1 proves *"no
admission predicate can be O(1)"* and concludes *"admission must be competitive,
not absolute."* Variant (a) is an **absolute predicate** (T1 ∨ T2) with a cap
that does not bind — 63% used, ~540 days of headroom by the doc's own estimate.
So for the entire foreseeable horizon, residency is decided by the predicate §4.1
just falsified. Worse, §4.1's own measurement is that prohibition lines grow at
~0.35/day against curated growth of 0.167/day: **T1 grows about twice as fast as
the corpus it is selected from.** Variant (b) is competitive immediately and is
the one consistent with §4.1.

### F4 — the snapshot rollback is not a rollback

**Severity: blocking for S5 and S9.**

Seat 6 is right that `git revert` cannot roll back the memory dir. But the
replacement — restore `MEMORY.md` from a sha256-verified snapshot — is not a
rollback either, and it is the insurance behind every stage from S5 on.

`MEMORY.md` holds **three** things in one file (`memory.py:309-360`): the curated
region, the managed session-log entries, and the `<!-- clayrune:wm:<sid> -->`
watermark markers. Restoring the file reverts all three.

1. **Lost managed entries.** Session-log entries written between snapshot and
   restore exist only in the managed region; they reach the archive only if the
   floor evicted them. A restore deletes them. **§14.7.4's "No stage in this plan
   destroys data" is false as written.**
2. **Lost-update race.** `_commit_managed_entry` serialises writers with
   `_get_mem_write_lock`, an **in-process** lock held by the server
   (`memory.py:1047`). A standalone `tools/memory-snapshot.py --restore` does not
   hold it. `_atomic_write_text` means the file is never torn, but restore-vs-
   commit is still a lost update. The plan reasons carefully about copy **order**
   when *creating* a snapshot (that reasoning is sound) and says nothing about
   quiescing for **restore** — while S9 runs tranches 2–7 unattended.
3. **The severe one: restoring clobbers live watermarks, and supersede then fails
   silently.** Step-6 supersede reads `last_entry_hash` off the session's
   watermark record inside `MEMORY.md` (`memory.py:1052-1060`). Restore an older
   file and a live session's watermark either vanishes or reverts to a stale
   hash. Then `if prev_hash:` is false, or the hash matches nothing, no entry is
   dropped — and `_scribe_stat` increments **only on success**. No log, no
   counter on failure. Every subsequent checkpoint appends a new `_(live)_` entry
   instead of superseding: **precisely the pile-up that filled this repo's whole
   managed region with 16 copies of one steward session on 2026-08-05**, and
   precisely what S7's own ABORT criterion names as the thing to fear. The
   rollback path can create it.
4. **S0b verifies the backup, never the restore.** Its success criterion is
   "restoring the snapshot into a **temp dir** reproduces 76/76 files with
   matching sha256." That tests archive integrity, not the live restore path that
   everything from S5 on is insured by.
5. **Minor:** S9 calls the archive duplicate a restore leaves behind "harmless."
   It is a duplicated retrieval unit; it shifts archive-class `avgdl` and global
   `df`, and moves the M5 baseline for the next tranche. Small, but M5 is an
   exact-equality gate.

### F5 — "structured condense cannot fire" is false, and its supporting evidence repeats retraction #2

**Severity: corrects a §3 verdict; re-specifies S10.**

§3 asserts: *"Structured condense | silently failing — cannot fire | floor =
cap−1024, trigger = >cap; the deadband is unreachable."*

Read from `memory.py:819-858` and `988-993`, **there are two triggers**:

| | line half | byte half |
|---|---|---|
| structured trigger | `> index_line_budget` = **160** | `> index_byte_cap` = **24,576** |
| mechanical floor | `> index_line_hard_floor` = **185** | `> index_byte_floor` = **23,552** |

The byte half has the deadband as described. **The line half is the opposite**:
the trigger at 160 sits 25 lines *below* the floor at 185, so it fires well
before the floor ever acts. Condense is not structurally unable to fire.

**And it is false on other projects today.** Measured `wc -l`/`-c` over every
`~/.claude/projects/*/memory/MEMORY.md`:

| project | lines | bytes | state |
|---|---|---|---|
| DayTrading | **203** | 18,124 | over the 160 trigger **and** the 185 floor |
| ApexTrader | **188** | 19,367 | over both |
| FL3-V2 | **183** | 18,193 | over the trigger, under the floor |
| mission_control | 132 | 22,286 | neither |

mission_control is quiet only because at ~169 bytes/line the **byte** floor binds
first and holds the file under the line trigger. That is a density coincidence of
this one project, presented as a property of the code.

**The evidence repeats a retracted inference.** §3 cites "zero `[condense]` log
lines in the current window" as proof of silent failure. The 2026-08-05 journal
entry retracts exactly this: *"Zero [condense] lines means zero condense
FAILURES in that window; the success path logs nothing."* Of the 13 `[condense]`
log sites, the trigger-path ones are failure/warning only. Zero lines is
consistent with "never triggered" *and* with "triggered and succeeded silently" —
it does not discriminate.

**Seat 6's alternative account is also wrong.** S2 proposes
`condense_threshold_kb = 20` → trigger at 20,480 against a floor of 23,552, and
`_should_condense` comparing `MEMORY.md + CLAUDE.md`. That is the **legacy**
branch (`memory.py:859-876`), unreachable while `condense_mode == 'structured'`
(live, confirmed). Seat 6 flagged the disagreement and deferred it to S2 —
correct instinct; neither account is right, and §3 states one of them as a
verdict.

### F6 — cross-project: on 4 of 19 projects the rule evicts the entire front page

**Severity: blocks shipping the rule beyond mission_control.**

Applied seat 5's prohibition lexicon to every project's curated region
(production `_mem_split_full`), pointer lines ≥ 10:

| project | pointer lines | T1 | curated bytes |
|---|---|---|---|
| mission_control | 96 | **17** (17.7%) | 16,023 |
| DayTrading | 172 | 4 (2.3%) | 17,524 |
| FL3-V2 | 140 | 8 | 17,013 |
| Projects-DayTrading-engulfing-scanner | 105 | 5 | 16,730 |
| Projects (root) | 102 | 3 | 12,226 |
| **clayrune-website** | 18 | **0** | 3,512 |
| **Projects-CodeTalk** | 24 | **0** | 1,570 |
| **DayTrading-engulfing-scanner** | 69 | **0** | 6,818 |
| **Projects-FL3-V2 / others** | — | 0 in 4 projects total | — |

On the T1 = 0 projects, residency falls entirely to T2 (target is dark). On a
project whose ranker reaches everything, T2 = 0 too, and under variant (a) **the
entire curated index is evicted** — while its whole content (clayrune-website:
3,512 B) fits inside the 8,192 B cap with room to spare. **The rule evicts
content it has the budget to keep.** Variant (b) does not have this failure.

The lexicon (`BINDING`, `LOAD-BEARING`, `do NOT`) is tuned to this repo's voice;
§4.2's evidence for it comes entirely from this repo's `CLAUDE.md`. Trading
projects do not write that way. §6.3 of seat 5 already concedes the lexicon's
precision/recall was never measured against a hand label — that concession needs
to be in the main doc, not only the seat file.

### F7 — fresh install crashes the tiering rule

**Severity: low, trivially fixable, but it is the empty-corpus case nobody tested.**

`recoverable()` does `max(sum(1 for t in d if t in s) for s in utoks)` over the
corpus units. On an empty memory dir, `_mem_corpus` returns `[]` and this raises
`ValueError: max() iterable argument is empty`. Verified against a fresh temp
dir. `_mem_class_avgdl` returns `{}` there, which `_memory_search` handles
correctly; the tiering prototype does not. A fresh install has an empty corpus by
definition, and prototype code of this kind becomes production code.

The **desired** empty-corpus behaviour is worth stating explicitly: with 0 units,
every line is non-deliverable, so everything is T2 and stays resident. That is
the right fail-closed default — it just needs to be written down and tested
rather than reached by an exception.

---

## 4. OVERSTATED — true but framed beyond the evidence

**O1 — "with no note becoming unreachable" (§1, §4.3).** True only because
"note" means topic file, and eviction touches no topic file. The clause sits
directly under the eviction table where every reader will take it as a safety
guarantee about the evicted content. Given F1/F2 it should be deleted or
rewritten.

**O2 — "evicted into a channel that already works" (§1).** The channel works for
topic files. It does not work for 170-byte pointer lines competing against 2,476
archive units, measured.

**O3 — "eviction is reversible by copying the line back" (§8.4).** True for the
*content*; not true for the *system state*, per F4. And "reversible" understates
what a rollback costs when it is unattended.

**O4 — the 540-day cap-binding estimate.** The doc already labels it an estimate
and says the growth signal is 6 days and one pointer. That honesty is right. But
it is then used in §4.3 to argue "the cap is not binding today, so installing the
mechanism costs nothing now" — an argument that only holds under variant (a),
where the cap is *not the mechanism doing the work*. Under variant (b) the cap
binds immediately (99% of cap today).

**O5 — Constraint P applied to the design's own eval.** The doc rightly forbids
depending on agent-initiated fetches. Its own Check 3 (cross-surface
contradiction) and every soft gate escalate to "the human queue" / the item
journal — a channel whose read rate nobody measured. That is fine and correct for
safety (a human must be on one side of the loop), but the doc should not imply
these findings will be acted on promptly. The memsearch incident it cites ran for
80 days precisely because a written record went unread.

**O6 — "seat 2's 660 B per watermark."** Measures 320 B live. Conclusion (defer
S7) unaffected and strengthened.

---

## 5. Checked against the load-bearing `CLAUDE.md` rules

| rule | verdict |
|---|---|
| **DATA_DIR pollution / `EXCLUDED_SIDECAR_SUFFIXES`** | **FINE, and well handled.** §9 puts residency/eval state in the memory dir with a **non-`.md`** extension, which satisfies both the `load_projects()` rule and `_mem_corpus`'s `*.md` glob. It correctly notes a new `.md` there would become a topic unit and pollute IDF/`avgdl`. Pinned suite goes to `data/memory-eval/`, outside `DATA_DIR`. This is the strongest section of the doc. |
| **Nothing operator-specific in the repo** | **FINE, with one live gap.** S1 correctly identifies that `data/memory-eval/` holds verbatim operator task text, is **not** currently gitignored (I reproduced that), must be added, and that gitignoring alone was insufficient for `SHARED_RULES.md` because `build-macos.spec` bundled it anyway. Verified: the spec today bundles only `data/agent_reference`. The human gate on S1 is correctly placed. |
| **Unattended agents never write backlog notes** | **FINE.** S11 explicitly routes eval output to `docs/_journal/dae8d6e7-*.md` and never to the note API. Correct. |
| **Authority guard — learning may never expand the agent's own authority** | **FINE in direction, with a caveat worth stating.** Eviction can only *reduce* what the agent is told; it cannot author, delete or promote a charter line. No path grants autonomy or removes a gate. **Caveat:** §9 says "T1 is the prohibition tier, so the class of line the authority guard exists to protect is the *last* thing evicted, never the first." *Last* is not *never*. Once the cap binds, machinery evicts T1 lines (ties by ascending length). Combined with F1 — evicted lines are delivered 0/160 — that means the machinery can **silently stop delivering a binding prohibition**. The doc's answer is that the archive preserves it; F1 shows preservation is not delivery. Today no project's T1 approaches 8,192 B, so this is a designed-in future behaviour, not a live one. It should be closed explicitly: **T1 must never be evicted; if T1 alone exceeds the cap, escalate to the human instead.** |
| **Durable "no"** | **FINE.** The tombstone-in-place convention (§6.4) is the right answer and is well argued — deletion is what lets a prohibition be reinstated. §6's rejection of bitemporal facts is sound, and the memsearch-was-in-`CLAUDE.md`-for-80-days finding is the strongest single piece of evidence in the whole document. Subject to the T1 caveat above. |
| **A human on one side of every loop** | **FINE.** The eval escalates and never acts; authorship stays human while `fold` and eviction are machine-driven, so the resident region is not a closed autonomous loop. The reasoning in §9.2 is explicit and correct. |
| **Server restart requires Ron's approval** | **FINE.** Two restarts, both batched, both flagged. And S3 — the highest-value change — correctly needs none; I verified that from code. |

---

## 6. Complexity budget — ~1 MB, one user

The rejections in §11 are **right and well argued**: no vector store, no graph
DB, no rerank model, no embedding service, no LLM-per-write extraction. The
locally-measured rerank regression (46 → 41) is worth more than the entire market
scan, and the "no ML stack on this box at all" observation is decisive. The
bitemporal rejection on Graphiti's per-write LLM+embedding cost is correctly
labelled as 100×-scale complexity and rejected explicitly rather than omitted.

**What I would still flag as over-built for the scale:**

1. **The eviction machinery itself (S8–S10) — ~19 agent-hours, 2 new config keys,
   one restart, 3 new test files — to save ~2,712 tokens/prompt** (or ~1,972
   under variant (b)), with the safety case falsified in F1/F2. This is the most
   expensive and least proven part of the plan.
2. **S0–S3 is 11 hours and delivers the largest measured win** (dark 18 → 9,
   +9 files reachable), reversible by one `PUT`. The value is heavily
   front-loaded, and the plan already says so.
3. **Nine new config keys** for a single-user memory index is a lot of surface.
   `bm25_b` / `bm25_title_boost` are one-line constants whose A/B is a paired
   offline run; they do not need to be live-tunable settings a user can break.
   Consider landing them as constants with a test, not as config.
4. **Suite-v1 pinning, paired runs, nightly scheduling** is proportionate and I
   would keep it — it is cheap, and §7's point that BM25 scores drift as the
   archive grows (~2,691 B/day) is correct and is the real justification.

**Net:** the plan is not over-engineered in its *rejections*. It is
over-committed in S8–S10, which is exactly where the evidence is weakest.

---

## 7. Where the design assumes an action the 5%-pull data says will not happen

I looked specifically for Constraint P violations inside the design itself. The
core design honours it: everything moves onto the push path. Three residues:

1. **The eviction target is nominally push-path but empirically isn't (F1).**
   An archive unit at median best-rank 109 is not "pushed" in any operative
   sense. This is the design's own constraint violated by its own central move —
   not by routing content behind a tool call, but by routing it behind a ranking
   contest it loses.
2. **The `mc-memory-search` skill is still named in the injected header**
   ("use the mc-memory-search skill to dig deeper", `agent_routes.py:1932`). It
   fires in 3% of sessions. Harmless, but the doc should not count it.
3. **Escalation to "the human queue"** (O5). Correct for safety, but it is a
   pull path with an unmeasured read rate.

---

## 8. Corrections that must be folded into the main doc

1. **§1 / §4.3** — delete or rewrite "with no note becoming unreachable." Add
   the measured delivery result: evicted lines are delivered on 0 of 160 tasks;
   30 of 67 have no surviving delivery channel.
2. **§4.4** — state that recoverability was measured as *vocabulary
   concentration*, not delivery, and that it must not be read as licensing
   eviction. Add: 25 of 28 recovering units are never delivered.
3. **§5.4 / §14.7.3 / M5** — demote M5 from "the difference between an eviction
   and a deletion" to what it is: a plumbing assertion that a line became a unit.
4. **§4.3** — disambiguate the RESIDENCY RULE. Publish variant (a) or (b)
   explicitly and recompute the headline from the one chosen. If (a), state that
   residency is predicate-driven for ~540 days and reconcile that with §4.1.
5. **§3** — replace the condense row: byte trigger unreachable on this project;
   **line trigger reachable and firing on DayTrading, ApexTrader, FL3-V2 today**.
   Remove "zero `[condense]` log lines" as evidence (retraction #2's exact
   inference). S10's deadband fix must target the line trigger too.
6. **§9** — close the T1 eviction gap: T1 must never be evicted; over-cap T1
   escalates to a human.
7. **§14.7.4** — strike "No stage in this plan destroys data."
8. **S0b** — add a live-restore drill (with a session running) asserting
   supersede still fires afterwards; gate restore on `_has_running_agent` or
   route it through the server's `_get_mem_write_lock`; make restore
   **region-scoped** (curated only), which is a true rollback of what S9 changes.
9. **§4.2 / §6.3** — state in the main doc that the prohibition lexicon was
   validated only against this repo, and give the cross-project T1 rates.
10. **New** — specify empty-corpus and T1 = 0 behaviour with an explicit
    fail-closed-toward-residency default, and fix the `max()` crash.
11. **Minor** — 14 task-less call sites, not 13; watermark 320 B, not 660 B;
    `_RESPAWN_TRIGGER_KEYS` holds 8 keys, not 9.

---

## 9. Recommendation to Ron

**Approve as written:** S0, S0b (with the restore drill added), S1, S2, S3, S4,
S6, S12. These are instrument, ranking and injection work, all reversible by one
API call or one `git revert`, and S3 alone is the largest measured win in the
plan (+9 files reachable, dark 18 → 9, no restart, one `PUT` to undo).

**Approve with the F4 fixes first:** S5 (21 wikilinks). The content change is
right and cheap; only its rollback is unsound.

**Do not approve yet:** S8, S9, S10. The eviction is the one irreversible-in-
effect move, and its safety case does not hold as measured. If Ron wants the
~2,000–2,700 tokens/prompt, the honest route is:

- fix the rule ambiguity and pick variant (b), which is competitive, consistent
  with §4.1, and evicts 44 lines rather than 67;
- add a **per-line delivery gate** to S9: no line is evicted unless its knowledge
  is delivered on ≥ 1 suite task afterwards **by some unit**. On today's numbers
  that blocks the 30 in F2;
- re-run S8's dry run under that gate and publish the surviving list.

**Defer:** S7 (watermarks — 320 B against a real supersede risk; seat 6's own
recommendation, and the measurement supports it more strongly than seat 6 knew).

**One structural observation to close on.** The document's best insight is that
the curated region "is resident because residency is the only channel it has."
My measurements say that is not a historical accident to be undone — it is
**load-bearing**. For 30 of these 67 lines, residency is the only channel that
works *today*, and the ranker cannot take them over. The right conclusion may not
be "evict what the ranker can reach" but "**the index is the delivery mechanism
for facts too small to win a ranking contest**," and the cap should be spent on
them deliberately. Variant (b) — fill the cap by ascending length, prohibitions
first — does exactly that, and it is the doc's own stated rule.

---

## Appendix — probes

All in `_scratch/seat7/` (gitignored), read-only, server-free:

| file | what it does |
|---|---|
| `harness.py` | wires `mc.memory` without importing `server`; no supervisor starts |
| `delivery_probe.py` | tiering (seat 5's rule verbatim) + per-line delivery rate over 160 tasks |
| `verify_units.py` | falsification checks: verbatim self-query, best-ever rank in the full ranking |
| `redundancy2.py` | linked-target and link-less recovering-unit delivery, with the relabelling control |
| `delivery.json`, `redundancy2.json`, `verify_units.json` | raw outputs |
