# MEMORY REDESIGN — Seat 1: E2E audit of the RETRIEVAL path as it actually runs

**Date:** 2026-08-15 · **Scope:** the read side only (`mc/memory.py` + the
injection path in `mc/blueprints/agent_routes.py`). Write path, Scribe,
Step-6 and condense are other seats.

**Method rule applied throughout:** every number below was measured on this box
by importing and calling the *production* functions (not reimplementations), or
by reading the running server's `/api/config`. Numbers I did not measure are
labelled ESTIMATE. Scripts: `_scratch/seat1_corpus.py`,
`_scratch/seat1_topk_sweep.py`, `_scratch/seat1_cost_and_turns.py`,
`_scratch/seat1_stale_floor.py`. Nothing under `mc/`, `MEMORY.md` or the memory
dir was modified.

---

## 0. Headline — the two findings that change the hivemind's premise

**A. The brief's "30 dark files" measures a configuration production does not
run.** `tools/memory-eval/scorer_ab.py:161` calls `_memory_search(project, t,
TOPK)` with no `expand=` argument. Production
(`agent_routes.py:1917-1920`) calls it with `expand=2`. Link expansion is live
and supplies **37% of read-floor slots**. Measured with expansion on, the live
dark set is **15 topic files, not 30**.

**B. `read_floor_topk` is live at 3, not the 6 the code and docs say.** The
2026-08-05 measured improvement (3→6) is inert on this machine because
`config.json` shadows the code default. Restoring it takes the dark set from 15
to **8** with zero new machinery.

Together: **23 of the 30 "dark" files in the brief are reachable today or
reachable by a one-line config edit.** The dark set is a ranking-budget
question, not a corpus-shape question. Only **2 of 74** topic files are
unreachable even at topk=20 with expansion.

**C. And the bigger constraint is delivery, not ranking.** 13 recovery/respawn
paths omit the read floor entirely, and 84% of user turns are served a read
floor computed from turn 1's task. Improving the ranker is capped by a delivery
layer that discards its output.

---

## 1. Verdict table

| Mechanism | Verdict | Note |
|---|---|---|
| `_memory_search` BM25 core | **LOAD-BEARING** | correct, healthy, well-adapted |
| Per-unit-class `avgdl` + global IDF | **LOAD-BEARING** | the adaptation is necessary and correctly sized (12.9× class spread) |
| `_mem_corpus` unit construction | **LOAD-BEARING** | with one small silent drop (§4.3) |
| Title ×3 boost into the token stream | **LOAD-BEARING** | partially undercut by the tokenizer (§5) |
| `[[wikilink]]` expansion | **LOAD-BEARING** — larger than documented | 37% of slots; +13 files at live topk |
| `_memsearch_cache` | **LOAD-BEARING** | invalidation is correct |
| `read_floor_topk` config key | **SILENTLY FAILING** | live 3 vs code default 6 (§3) |
| Read-floor injection on normal dispatch | **LOAD-BEARING** | 2 of 15 call sites |
| Read-floor injection on recovery/respawn | **DEAD** — never executes | 13 call sites pass `task=''` (§6) |
| Per-turn freshness of the read floor | **SILENTLY FAILING** | stale on 84% of turns (§7) |
| Curated region as a *retrieval* asset | **DEAD by construction** | 73% of the index, excluded from the corpus (§8) |
| `read_floor_topk=0` as a kill switch | **SILENTLY FAILING** | `scored[:max(1,topk)]` → always ≥1 |
| `_mem_tokens` docstring | **WRONG** | contradicted by its own filter (§5) |
| `docs/MEMORY_SYSTEM.md` config table | **STALE** | 4 of 11 rows differ from live (§9) |

---

## 2. The corpus, measured

`_mem_corpus(mem_dir, 'MEMORY.md', 'MEMORY_ARCHIVE.md')`, production call:

```
total *.md on disk        76      1,110,482 bytes (1084.5 KB)
  MEMORY.md               23,277 bytes   (auto-loaded every prompt)
  MEMORY_ARCHIVE.md      793,414 bytes
  topic files                 74

scoring units            2,565
  topic                     74
  managed                   15   (MEMORY.md managed region only)
  archive                2,476   (one per '- [' line)
```

`_mem_class_avgdl` (token lengths, production function):

| class | n | avgdl | min | median | max |
|---|---|---|---|---|---|
| archive | 2476 | 38.5 | 2 | 43 | 81 |
| managed | 15 | 41.1 | 2 | 45 | 78 |
| topic | 74 | **496.6** | 82 | 411 | 2494 |

**The per-class adaptation is justified and correctly sized.** Topic units are
**12.9×** longer than archive units and archive units outnumber them 33:1. A
single global `avgdl` would land near ~52 tokens and score every topic file as
~10× over-length — the exact mirror of the 2026-08-05 bug. This is
LOAD-BEARING and should survive any redesign. IDF stays global, which is right:
term rarity is a corpus property, and this corpus is dominated by exact tokens
where rare-term weighting is what separates a real hit from a passing mention.

BM25 constants: `K1=1.2`, `B=0.75`, `TITLE_BOOST=3`, `LINK_DECAY_OUT=0.5`,
`LINK_DECAY_IN=0.35`. All textbook-default; no evidence they need tuning.

---

## 3. SILENTLY FAILING — `read_floor_topk` is 3, not 6

Measured three ways:

1. `curl -s localhost:5199/api/config` → `read_floor_topk = 3` (the **running
   process**, not a file).
2. `config.json` on disk → `"read_floor_topk": 3`.
3. `server.py:191` code default → `6`, with an inline comment citing the
   measurement that justified 3→6.

**Mechanism** (`server.py:275-288`): defaults are built, then
`for k, v in saved.items(): defaults[k] = v`. Persisted `config.json` values
override code defaults unconditionally, with no reconciliation and no log line.

**This is silent in three independent places:**
- The merge never reports that a saved key is shadowing a *changed* default.
- The Settings UI writes the **whole** config back, so any key freezes at its
  then-current value the first time anyone opens Settings.
- Nothing compares saved-vs-default at boot.

`read_floor_link_expand` survived at 2 *only because it is absent from
`config.json`* and falls through to the code default. That is luck, not design.

**Generalisation for the design seats:** on this install, any tuning default
changed in code after `config.json` was last written is inert. Any redesign
that ships a new default must either use a fresh key name or add a
saved-vs-default reconciliation step. Assume nothing you ship as a default is
live until `/api/config` confirms it.

---

## 4. `_memory_search` end to end — LOAD-BEARING, and correct

Path (`mc/memory.py:602-698`):

1. `_mem_tokens(query)` → terms; empty ⇒ `[]` (silent, correct).
2. Resolve memory dir; any exception ⇒ `[]` (**silent**, `except Exception:
   return []` at 651-652).
3. `_mem_corpus(...)` → cached units. Empty ⇒ `[]` (silent).
4. Global IDF over `df` of query terms only, in the always-positive form
   `ln(1 + (N-n+0.5)/(n+0.5))`. Correct — the classic form goes negative for
   terms in >half the corpus, which would let a common term *subtract*.
5. Per-unit BM25 with `norm = avgdl[cls]`, skipping units with `matched == 0`.
6. Sort by `(-score, file)` — deterministic tie-break, replayable. Good.
7. `hits = scored[:max(1, topk)]`.
8. If `expand`, append `_mem_expand_links(...)`.

### 4.1 Everything downstream of step 3 is sound

I found no scoring defect. The 2026-08-05 BM25 work did what it claimed.
Reachability is now limited by *budget* (topk) and *delivery*, not by ranking.

### 4.2 `read_floor_topk=0` cannot disable the floor

`scored[:max(1, topk)]` returns 1 hit when `topk=0`. An operator setting the
key to 0 to turn the floor off gets a floor of 1 and no error. Cosmetic today
(nobody has set it), but it is a kill switch that does not kill.

### 4.3 `_mem_corpus` silently drops wrapped archive lines

`MEMORY_ARCHIVE.md`: 2,518 lines, 2,502 non-blank, **2,476** become units. The
filter is `ln.strip().startswith('- [')`, so **26 non-blank lines (6,869 bytes)
are dropped** — these are continuation lines of wrapped multi-line entries plus
one `## Archived` heading. Small (0.9% of archive bytes) and I would not fix it
on its own, but it means a long archive entry is indexed only up to its first
newline. Named here because it is silent.

`MEMORY.md` archive-style parsing is fine: `_mem_split_full` correctly isolates
the managed region and the curated region never reaches the corpus (§8).

### 4.4 `_memsearch_cache` — correct

Signature is every `*.md` file's `(name, mtime_ns, size)`, so any write, add or
delete invalidates. Keyed by `str(mem_dir)`, guarded by a lock. Unbounded across
projects, but at one entry per project that is not a real leak. No defect found.

---

## 5. WRONG DOCSTRING + a real token loss — `_mem_tokens`

The tokenizer is `[t for t in re.findall(r'[a-z0-9]+', text.lower()) if len(t) >= 3]`.
Its own docstring says:

> Splits on non-alphanumerics INCLUDING underscore, so `memory_system` yields
> `memory`+`system` and **`mc/memory.py` yields `mc`+`memory`+`py`**.

Measured:

```
_mem_tokens('mc/memory.py')  -> ['memory']          # 'mc' and 'py' both dropped
_mem_tokens('memory_system') -> ['memory','system'] # this half is right
```

The `len(t) >= 3` filter in the same function deletes both tokens the docstring
promises. **The docstring is wrong about its own worked example.**

This matters for *this* corpus specifically, which the docstring itself argues
is "mostly snake_case identifiers and file paths". Dropped: `mc`, `py`, `js`,
`ui`, `es`, `v2`, `cf`, `ai`, `os`, `go`.

Measured impact on the ×3-boosted titles: **9 of 74 topic filenames lose at
least one token**, including the discriminating one:

```
arch_mobile_ui.md                            drops ['ui']
decision_skills_curation_phase4_v2_spec.md   drops ['v2']   <- sharpest case
discovery_es_module_cross_boundary_globals.md drops ['es']
discovery_mode_a_sse_followup_race.md        drops ['a']
reference_show_image_in_chat.md              drops ['in']
feedback_no_*.md  (×3)                       drops ['no']
```

`decision_skills_curation_phase4_v2_spec.md` cannot be matched on `v2` — the
single token that distinguishes it from
`decision_skills_curation_phase4_promoted.md`. A query "phase 4 v2 spec" scores
both identically on the shared tokens.

**Verdict: SILENTLY FAILING (narrow).** Cheap candidate fix: keep 2-char tokens
when they are alphanumeric-mixed or appear in a filename; or lower the floor to
2 and let IDF suppress the noise (IDF already handles common short tokens — that
is what it is for). Worth a measured A/B, not a blind change.

---

## 6. DEAD — 13 recovery/respawn paths emit no read floor at all

`agent_routes.py:1915` gates the entire block on `if task:`. Called with
`task=''`, `_build_agent_context` produces **no `RELEVANT MEMORY` section**, no
log line, no counter.

**Call sites passing no task** (`agent_routes.py`): 2788, 3049, 4853, 4859,
4869, 4885, 4981, 5109, 5236, 5482, 5488, 5542. Every one is a recovery or
respawn path:

| line | situation |
|---|---|
| 2788 | resume failed → restart fresh |
| 3049, 4885, 5236, 5482 | auto-fresh: transcript grew too large |
| 4853 | process died with no `claude_session_id` → respawn fresh |
| 4981 | sticky-settings respawn |
| 5109 | model switch with no resume flags |

Only **3860** and **4070** — the normal dispatch paths — pass `task=task`.

**This is an omission, not a tradeoff.** At 2788 the variable `task` is in scope
and used on the line immediately above:

```python
_log_agent_activity(project_id, f"Resume failed, restarting fresh: {task[:80]}")
context = _build_agent_context(p)          # task in scope, not passed
```

The follow-up sites have `message` / `respawn_msg` equally available.

**Worst instance — 4981 poisons the session stash.** The sticky-settings
respawn does:

```python
_sticky_ctx = _build_agent_context(p)      # no task -> no read floor
existing['_system_prompt'] = _sticky_ctx
```

`_respawn_sysprompt_args` (564-590) *prefers* `session['_system_prompt']`. So
one settings change mid-session permanently strips the read floor from that
session's stash, and every subsequent respawn reuses the stripped copy. Silent,
sticky, and not recoverable without starting a new session.

**The perversity:** these are exactly the sessions that just lost their working
context. The agent is handed *"[Continuing from a previous conversation that
grew too large…]"* and, at that precise moment, the subsystem built to restore
context contributes nothing.

**Also silent:** `except Exception: hits = []` at 1921-1922 has no `_log`. A
corrupt memory dir or a raising `_memory_search` removes the read floor with no
trace. This violates the project's own exception-swallowing policy in
`CLAUDE.md` (file I/O on state files must `_log`). Note the coordination
read-floor 40 lines below *does* log its exception; the memory and exploration
floors do not. Inconsistent.

---

## 7. SILENTLY FAILING — the read floor is frozen at spawn

The floor is computed once, baked into the `--append-system-prompt-file` temp
file (`_sysprompt_file_args`, 530-561), and stashed on
`session['_system_prompt']`. Follow-up turns are written to the live process's
**stdin** (5041, 4222, 3135) — the system prompt is never rebuilt. On `-r`
respawn, `_respawn_sysprompt_args` prefers the stash and ignores the new
message.

Measured over 263 mission-control transcripts:

```
sessions with >=1 real user turn      260
sessions with >1 user turn             76  (29%)
total real user turns                1628
turns served turn-1's read floor     1368  (84% of all turns)
```

For the 74 multi-turn sessions with ≥2 substantive turns (1,207 later-turn
pairs), comparing the floor the agent **had** against the floor that turn's own
text **would** produce (live settings topk=3, expand=2):

```
mean fraction of the right notes the agent held   21%
later turns where the agent held NONE of them    653  (54%)
```

**Honest caveat, stated rather than buried:** "BM25 would have retrieved it" is
not proof the agent needed it — later turns often build on context already in
the conversation, and freezing is deliberate for prompt-cache reasons (the
`_respawn_sysprompt_args` docstring says so explicitly). So this is an upper
bound on the harm, not a measurement of harm.

What is *not* arguable is the label: the injected header reads **"RELEVANT
MEMORY (auto-surfaced for this task…)"**, and on 84% of turns "this task" is a
different task. That is the silent part.

`docs/MEMORY_SYSTEM.md` §6 already records a version of this as deferred tech
debt ("within-session self-recall for long Mode-B sessions", real fix = per-turn
read-floor refresh via stdin). **The framing understates it:** the doc presents
it as a long-session edge case about a session losing *its own* learning. The
measurement says the stale floor is the **median turn**, and the loss is of
*pre-existing* notes, not just self-authored ones.

---

## 8. DEAD BY CONSTRUCTION — the curated region is push-only

`_mem_corpus` indexes `_mem_split(txt)[1]` for `MEMORY.md` — the **managed
entries only**. The curated region is `[0]` and is discarded.

Measured on the live `MEMORY.md` via production `_mem_split_full`:

```
whole file        23,143 bytes
CURATED region    16,820 bytes  (73%)   <- NOT in the retrieval corpus
managed entries   15 lines, 4,909 bytes -> 15 units
watermarks        2
curated '- ' lines    96
   carry a .md / [[ ]] target   61  (10,296 bytes)
   NO target (line IS the knowledge) 35  (5,631 bytes)
```

(I checked the 3 apparent broken targets by hand — all are regex artifacts of
prose containing `.md` or `[[…]]`. **Zero genuinely broken curated links.**)

**This is the central asymmetry for the design.** The curated region is 73% of
the always-loaded index and is *simultaneously* the only part of the corpus
that cannot be retrieved. It is pure resident cost with zero retrieval value:
you pay ~4,200 tokens/prompt for it (ESTIMATE, 16,820 bytes ÷ 4) and the ranker
cannot reach a single byte of it.

Consequences the design must respect:

- **Evicting a curated line that carries a link is safe** — the target note is
  in the corpus and reachable (61 of 96 lines).
- **Evicting one of the 35 link-less lines deletes it from the system**, unless
  the same fact is independently in a topic file or archive line. That is the
  set the 2026-08-05 journal entry examined (retracted, then re-derived: 33 of
  37 recoverable from archive units). Recheck against the current 35 before
  acting; do not reuse that number.
- I confirmed the loss is of the **line as a retrieval unit**, not of its
  vocabulary: sampled curated lines contain tokens appearing nowhere else in
  the corpus (`greenlights`, `substeps`, `accessors`, `perma`, `outlives`) but
  those are phrasing, not facts.

**Cheapest structural move available, and it is not in the item's proposal:**
make the curated region *also* a retrieval unit class. Indexing each curated
pointer line as its own unit (they are line-shaped, exactly like archive units)
would let the ranker surface a curated line on the push path — which is the
path that actually runs. That inverts the item's two-level proposal: instead of
moving the index behind an agent action that happens 5% of the time, it makes
the index reachable by the mechanism that fires 100% of the time. Cost is ~96
extra units on 2,565 (+3.7%). **I recommend the design seats evaluate this
before any paging scheme.**

---

## 9. `[[wikilink]]` expansion — LOAD-BEARING, and bigger than documented

Link graph, production `_mem_link_graph`:

```
topic nodes                        74
raw [[wikilink]] targets           92
resolved edges                     86   (6 lost to self-links/duplicates)
DANGLING (silently dropped)         0
nodes with >=1 out-link            44
nodes with >=1 in-link             37
ISOLATED (no in, no out)           20   <- invisible to expansion
```

The graph is **healthy** — zero dangling targets. `_mem_link_key`'s
punctuation-insensitive matching is doing its job; `tools/memory-link-check.py`
remains the human-facing reporter.

Contribution at live settings (topk=3, expand=2), 174 real tasks:

```
total read-floor slots            827
slots filled by link expansion    305  (37%)
topic files reachable only via a link hop at k=3   13
```

**Expansion is not a garnish; it is over a third of the payload and a fifth of
the reachable corpus.** `docs/MEMORY_SYSTEM.md` describes it correctly in
mechanism but nobody has measured its share until now. Any redesign that drops,
rewrites or re-slugs `[[wikilinks]]` must re-run this measurement first.

The **20 isolated nodes** are the population that expansion structurally cannot
help. That is a concrete, cheap, human-actionable list: adding one inbound link
to each would put them on the expansion path.

---

## 10. The sweep — reachability and cost at each setting

**Method:** production `mc.memory._memory_search` (imported, not
reimplemented), replayed over the first user message of every mission-control
transcript on this box against the current corpus. 263 sessions → **174 real
tasks** (86 trivial follow-ups excluded, `MIN_TASK_CHARS=25`). Denominator is
**74 topic files** (the 2 container files are excluded — they are always
reachable and inflate the brief's 76-file denominator).

| topk | expand | reach | dark | zero-result | slots | via-link | top-3 share | mean tokens/prompt |
|---|---|---|---|---|---|---|---|---|
| 3 | 0 | 46/74 | 28 | 0% | 522 | 0 | 49% | ~319 |
| **3** | **2** | **59/74** | **15** | **0%** | **827** | **305** | **33%** | **~530** ← **LIVE** |
| 6 | 0 | 62/74 | 12 | 0% | 1044 | 0 | 34% | ~437 |
| 6 | 2 | 66/74 | 8 | 0% | 1379 | 335 | 27% | ~874 ← code default |
| 10 | 2 | 69/74 | 5 | 0% | 2083 | 343 | 23% | ~1315 |
| 20 | 2 | 72/74 | 2 | 0% | 3827 | 347 | 17% | ESTIMATE ~2600 |

Token figures are ESTIMATES (rendered block chars ÷ 4), computed from the real
rendered block over all 174 tasks. For scale: **`MEMORY.md` itself is ~5,819
tokens/prompt** (23,277 bytes ÷ 4).

**Reading of this table:**

- **Zero-result is 0% at every setting.** The corpus always returns something.
  The failure mode is never "nothing"; it is "not the right thing".
- The 30-dark premise dissolves. Live is 15; the already-decided config is 8.
- **Only 2 topic files are unreachable at topk=20 with expansion:**
  `agent_name.md`, `arch_terminal_popout.md`. There is no large structurally
  unreachable population to design around.
- Concentration is no longer pathological: top-3 units hold 33% of slots live
  (was 93% pre-BM25).
- **The cost of reaching more is small against the resident index.** Going
  live→code-default costs ~344 extra tokens/prompt and halves the dark set. The
  curated region costs ~4,200 tokens/prompt and is unretrievable. **The index,
  not the read floor, is where the token budget is being spent badly.**

---

## 11. `docs/MEMORY_SYSTEM.md` — which claims are STALE

Checked the doc's config table row-by-row against the running server:

| key | doc | LIVE | verdict |
|---|---|---|---|
| `read_floor_topk` | 6 | **3** | differs (§3) |
| `scribe_checkpoint_enabled` | false | **True** | differs |
| `scribe_checkpoint_kb` | 0 | **8** | differs |
| `condense_mode` | agent | **structured** | differs |
| `read_floor_link_expand` | 2 | 2 | ok |
| `index_line_budget` / `index_line_hard_floor` | 160 / 185 | 160 / 185 | ok, and still live in code (`memory.py:829`, `memory.py:1046`) |
| others (scribe_*) | — | — | ok |

The table is headed "Default", so it is not *lying* — but nothing in the doc
warns that a persisted `config.json` silently overrides it, and 4 of 11 rows do
not describe this install.

**Omission that matters most to this hivemind:** `index_byte_budget` — the key
that actually governs the ceiling this whole backlog item is about — **is absent
from the config table entirely**, while the superseded-in-practice
`index_line_budget` / `index_line_hard_floor` are documented. Both line keys are
still read by code, so this is a documentation gap, not dead code.

**Claims verified CORRECT** (worth recording, since the brief said to treat the
doc as stale until checked):
- Leg B shape: ranked search + deterministic top-k floor injected at dispatch. ✅
- Link-layer mechanism section (§"The link layer") — resolution tolerance,
  one-hop traversal, decay constants, additive-never-substituted, topic-files-only,
  dangling-dropped-silently. All ✅ against source.
- §6 records the frozen-read-floor tech debt and names the right fix (per-turn
  refresh via stdin). ✅ mechanism, ✗ severity framing (§7).

**Drifted counts:** the doc says "97 [wikilinks] across 46 of 73 notes";
measured today 92 targets across 74 notes, 44 with out-links. Minor drift, not
a defect.

---

## 12. What I recommend the design seats carry forward

Ordered by measured value per unit of work. Items 1–3 are config/plumbing, not
redesign.

1. **Reconcile `read_floor_topk` to 6.** One config edit. Dark 15 → 8. Cost
   ~344 tokens/prompt against a ~5,800-token resident index. Already decided and
   measured in 2026-08; it simply never took effect. *Success criterion: `/api/config`
   reports 6 and the sweep reproduces 66/74.*
2. **Pass the task on the 13 recovery/respawn call sites**, and stop writing a
   task-less context into `session['_system_prompt']` at 4981. Restores the read
   floor to the sessions that need it most. *Success criterion: no
   `_build_agent_context` call site reachable from a dispatch/respawn path has
   an empty task; assert by test.*
3. **Log the swallowed exception at 1921** and add a saved-vs-default
   reconciliation warning at config load. Both are the project's own stated
   policy; both currently hide failures of this subsystem.
4. **Fix the measuring instrument before designing against it.** `scorer_ab.py`
   must pass `expand=read_floor_link_expand` and read `read_floor_topk` from
   live config, or every future A/B repeats this seat's finding A. This is the
   fourth time on this item that a confident number came from a probe that
   measured something other than production.
5. **Index the curated region as a retrieval unit class** (§8). Puts 73% of the
   resident index onto the push path — the path that fires 100% of the time —
   instead of behind a pull that fires 5% of the time. ~+3.7% units. This is the
   move the push/pull asymmetry actually argues for, and it is the opposite of
   the item's original two-level proposal.
6. **Per-turn read-floor refresh via stdin** (§7). Largest measured gap (84% of
   turns) but the most build. Gate it on evidence of harm, per the doc's own
   discipline — this seat measured staleness, not damage.
7. **Add one inbound link to each of the 20 isolated notes.** Human-scale,
   reversible, puts them on the expansion path that already carries 37% of slots.
8. **Consider lowering the tokenizer floor to 2 chars** (§5), A/B'd. Fixes the
   `v2` / `ui` / `mc` / `py` class. Fix the docstring regardless — it is
   currently wrong about its own example.

**What I would NOT do**, on this evidence: anything that moves index content
behind an agent-initiated fetch; anything that assumes the dark set is 30;
anything that reworks BM25 scoring, which I found healthy and correctly adapted.

---

## 13. Numbers in this document, and how each was obtained

| Number | Method |
|---|---|
| live config values | `curl localhost:5199/api/config` (running process) |
| corpus sizes, unit counts, avgdl, link graph | `_scratch/seat1_corpus.py` — imports `mc.memory`, calls `_mem_corpus` / `_mem_class_avgdl` / `_mem_link_graph` |
| reach/dark/slots/via sweep | `_scratch/seat1_topk_sweep.py` — production `_memory_search` over 174 real tasks |
| token cost per setting | `_scratch/seat1_cost_and_turns.py` — renders the real block, chars÷4 (**ESTIMATE**) |
| turn counts, 84% figure | `_scratch/seat1_cost_and_turns.py` — 263 transcripts |
| 21% overlap / 54% zero | `_scratch/seat1_stale_floor.py` — 1,207 later-turn pairs |
| curated/managed split, link coverage | production `_mem_split_full` on the live `MEMORY.md` |
| tokenizer behaviour | direct calls to production `_mem_tokens` |
| call-site inventory | read of `mc/blueprints/agent_routes.py`, each site inspected in context |
| doc-vs-live table | scripted diff of the doc's markdown table against `/api/config` |

**Shared caveat on every replay figure:** the corpus is replayed at its
*current* state, so notes written after a given session could not actually have
been surfaced at the time. This biases all rows of the sweep identically, so
the comparisons are sound while each absolute number is an upper bound. Same
caveat `scorer_ab.py` carries.

**Volatility note:** `MEMORY.md` is being written by live sessions during this
audit. I measured it at 23,277 bytes and again at 23,143 bytes minutes apart.
The eviction floor is 23,552 (`index_byte_budget` 24,576 − 1,024). Treat any
single byte-count on this file as a sample, not a constant.
