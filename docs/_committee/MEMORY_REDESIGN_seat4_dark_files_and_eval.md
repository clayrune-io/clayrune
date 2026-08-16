# Seat 4 — Dark-file forensics + a continuous recall evaluation harness

**Workstream:** ws_004 · **Date:** 2026-08-15 · **Backlog item:** `dae8d6e7`
**Scope:** analysis only. No production code, no memory edits. All prototypes in
`_scratch/memdark/`.

Every number below was measured on this machine on 2026-08-15 with the method
stated beside it. Where I could not measure something I say so rather than
estimating quietly. This item's journal records three retracted claims, all from
the same habit — treating a convenient measurement as an answer without checking
what it actually measured — so each section names its probe and its call
signature.

---

## 0. The one-paragraph version

The brief asked me to explain why 30 files never enter a top-k and how to fix
them. **The premise does not survive measurement.** The live dark set is 15, not
30 — the official probe omits an argument the production caller passes. Zero of
those 15 are unreachable at any rank; all are near-misses. And 14 of the 15
already have a resident one-line summary in the curated index, so their darkness
is not a knowledge loss. **Exactly one note in the corpus is reachable by neither
path.** The retrieval defect is real but roughly a tenth the size briefed, and
the genuinely load-bearing findings are elsewhere: a stale config key is holding
the ranker at half its shipped strength, the title boost is scoring negative, and
the read floor fires exactly once per session against the opening sentence.

---

## 1. Probe reproduction — and two defects in the probes themselves

### 1.1 What I ran

```
python tools/memory-eval/retrieval_probe.py
python tools/memory-eval/scorer_ab.py
```

Both unmodified, on this box, 2026-08-15.

### 1.2 `retrieval_probe.py` — reproduces exactly

| metric | brief | measured |
|---|---|---|
| sessions scanned | 259 | **264** |
| read a memory topic file | 13 (5%) | **13 (5%)** |
| ran a memory search | 7 (3%) | **7 (3%)** |
| distinct files ever opened | 10 | **10** (23 opens; 10 of them `memory.md`) |
| never opened | 66/76 | **66/76** |

**The push/pull asymmetry in the brief is confirmed.** The pull path is dead, and
any design that assumes the agent will go fetch is disproved. I treat that as
settled and do not re-litigate it.

### 1.3 `scorer_ab.py` — numbers differ, and the difference is instructive

| scorer | brief | measured |
|---|---|---|
| tasks | 170 | **175** (264 sessions, 86 trivial excluded) |
| OLD (tf) reachable | 19/76 | **19/76**, dark 57, top-3 share 94% |
| BM25 reachable | 46/76 | **48/76**, dark 28, top-3 share 49% |

The delta is **corpus drift**, not disagreement: five more sessions and five more
dispatched tasks have accrued since the brief's run.

> **DEFECT 1 — the headline metric cannot go down.** "Reachable" is a
> cumulative-ever-hit count over a task set that grows monotonically. More tasks
> means more chances for any file to enter some top-3, so this number drifts
> upward with the passage of time whether or not retrieval improves. **It is
> unusable as a regression gate in its current form.** Any continuous eval must
> pin the task suite. Addressed in §6.

> **DEFECT 2 — the probe measures a configuration that does not run.** This one
> is larger, and it invalidates the brief's central figure.

`agent_routes.py:1919`:

```python
hits = _memory_search(
    project, task,
    int(state.CONFIG.get('read_floor_topk', 6) or 6),
    expand=int(state.CONFIG.get('read_floor_link_expand', 2) or 0))
```

`scorer_ab.py:161`:

```python
new = [m._memory_search(project, t, TOPK) for t in tasks]      # no expand
```

Link expansion (shipped 2026-08-09, `read_floor_link_expand=2` live) is **on in
production and invisible to the official probe**. Measured through the live
function, no reimplementation — `_scratch/memdark/live_config_replay.py`:

| configuration | reachable | dark | slots | link-hops | top-3 share |
|---|---|---|---|---|---|
| probe as written (topk=3, expand=0) | 46/74 | 28 | 525 | 0 | 49% |
| **LIVE (topk=3, expand=2)** | **59/74** | **15** | 832 | 307 | **33%** |
| code default (topk=6, expand=2) | 66/74 | 8 | 1387 | 337 | 26% |
| topk=6, expand=0 | 62/74 | 12 | 1050 | 0 | 34% |

**The live dark set is 15, not 30.** The brief's figure, and my own first
reproduction of it, are both artifacts of a missing keyword argument.

### 1.4 A third defect, in the live config rather than the probe

| source | value |
|---|---|
| `server.py:191` (code default) | `read_floor_topk: 6` |
| `agent_routes.py:1919` fallback | `6` |
| **`config.json` on this box** | **`3`** |
| `GET /api/config` (live) | **`3`** |

The 2026-08-05 session measured the 3→6 change, shipped it, and recorded it in
the journal. **It never took effect here**, because `config.json` carries an
explicit `3` that shadows the new default. The single cheapest remedy available
was already decided, already measured, and is switched off by a stale key.

---

## 2. Dark-file forensics — the diagnosis the brief asked for

### 2.1 Method

`_scratch/memdark/forensics.py` replays the live BM25 formula (same `_mem_corpus`,
same `_mem_class_avgdl`, same `k1=1.2`, `b=0.75`, same tokenizer) over the 175
real dispatched tasks, but **keeps the full ranked list instead of truncating at
top-k**. For each topic file it records the best rank it ever achieves against
any task. That is what separates a rank-4 near-miss from a genuinely
uncompetitive note — a distinction the top-k probe structurally cannot make.

Corpus measured: **topic 74, managed 15, archive 2476 = 2565 units.** Mean 2269
units score above zero per task.

### 2.2 The result that answers the brief's question

Best-rank distribution of the 28 dark files (at topk=3, expand=0, so directly
comparable to the brief's framing):

| best rank ever achieved | files |
|---|---|
| 4 | 9 |
| 5 | 5 |
| 6 | 2 |
| 7 | 3 |
| 8 | 2 |
| 14–38 | 7 |
| **never scored at any rank** | **0** |

**Not one file is vocabulary-orphaned.** Every dark file matches its best task
and scores finitely. The brief offered six hypotheses — short, oddly-named,
vocabulary-mismatched, genuinely niche, redundant with an archive entry, or
beaten by an over-weighted unit class. Measured against this distribution:

| hypothesis | verdict |
|---|---|
| oddly-named / vocabulary-mismatched | **falsified as a cause of darkness.** No file fails to match. |
| genuinely niche → correctly dark | **falsified as *unreachable*.** All are reachable; see §3 for the version of this that survives. |
| too short | **partially true, as a ranking bias, not a matching failure.** See §2.3. |
| beaten by an over-weighted unit class | **true but minor** — worth 9 of 28 files, and the class is *archive*, not topic. §4. |
| redundant with an entry that already wins | **true, and it is the decisive finding** — but the winner is the *curated index*, not the archive. §3. |

### 2.3 The residual bias is length, and it is measurable

| population | median size |
|---|---|
| files that win at least one top-3 slot (46) | **3817 bytes** |
| files that never win (28) | **2186 bytes** |

And the worst-ranked dark files are the smallest: `agent_name.md` (688 B, best
rank 38), `arch_terminal_popout.md` (1169 B, rank 37), `arch_misc_tips.md`
(896 B, rank 32). BM25's length normalization at `b=0.75` reduced the old raw-tf
size bias but did not eliminate it. §4 measures the fix.

### 2.4 The topk sweep, and what it costs

| topk | reachable | dark |
|---|---|---|
| 3 (**live**) | 46/74 | 28 |
| 4 | 55/74 | 19 |
| 5 | 60/74 | 14 |
| **6 (shipped default)** | **62/74** | **12** |
| 8 | 67/74 | 7 |
| 10 | 67/74 | 7 |
| 20 | 71/74 | 3 |
| 40 | 74/74 | 0 |

The knee is at 5–6, consistent with the 2026-08-05 measurement. Three extra
slots cost roughly 300 tokens per prompt against a ~6k-token index — about 5%.

---

## 3. The finding that reframes the item: the two layers are 93% redundant

### 3.1 Method

Split `MEMORY.md` with the live `m._mem_split`, take the CURATED region, and ask
of every topic file whether the curated region cites it.

### 3.2 Result

| | count |
|---|---|
| topic files | 74 |
| **cited by a resident curated pointer** | **69 (93%)** |
| not cited — reachable only by the ranker | 5 |
| of the **15 live-dark** files, cited | **14 of 15** |
| of the 8 dark at topk=6+expand=2, cited | 7 of 8 |
| **both dark AND uncited** | **1** |

The single note reachable by neither path:

```
discovery_esc_no_apostrophe_inline_handler.md
```

It records that `esc()` at `static/index.html:2071` escapes `& < > "` but not
`'`, so a user string in a single-quoted inline handler silently breaks the
element with no console error. That is a real gotcha and it is currently invisible
to both delivery mechanisms.

### 3.3 What this means

I verified it by reading the dark files rather than trusting token overlap.
`agent_name.md` (Ron picked "Vector"), `project_remote_access_domains.md` (four
clayrune TLDs, canonical spelling with the "y"), `feedback_no_fluff_phrasing.md`,
`clayrune_scheduler.md`, `arch_terminal_popout.md` — every one has a curated line
in `MEMORY.md` carrying the actual point. **The file adds detail; the index
already delivered the fact.**

So for 14 of 15 dark files, the ranker declining to spend a top-k slot on a note
whose gist is already in the prompt is **correct behaviour, not a defect**.

Three consequences:

1. **The dark-file problem is largely a non-problem — and it is a non-problem
   *because of* the expensive thing.** The curated region is doing precisely the
   job it costs 16.5 KB/prompt to do. Any redesign that shrinks it converts
   benign dark files into real losses at a 1:1 rate. **The two knobs are coupled
   and must move in this order: raise ranker reach first, verify, shrink curated
   second.** Shrinking first is exactly the silent-deletion failure this item was
   filed about.

2. **The real safety invariant is cheap and checkable.** No note may be
   simultaneously (a) absent from the curated region and (b) unreachable by the
   ranker over the pinned suite. Today exactly one note violates it. This is a
   far better regression target than "dark count" — it measures the thing we
   actually care about, which is whether any knowledge is reachable by *neither*
   path.

3. **It supplies the migration's safe ordering.** Evicting a curated line is safe
   **iff** that note is ranker-reachable on the pinned suite. Measured: at
   topk=6+expand=2, 66 of 74 notes are ranker-reachable and 62 of those also hold
   a curated pointer. **62 curated lines could be dropped today without creating
   a single unreachable note** — most of the 16.5 KB, evictable by measurement
   rather than editorial judgement.

This replaces the item's proposal 2 ("evict on access statistics"). That proposal
is unsafe for the reason the 2026-08-05 review already gave — a badly-labelled
note and an irrelevant note both read as cold, so the eviction signal and the
failure mode are the same event. **Evict on proven ranker reachability instead:**
same buffer-pool intuition, but the signal is a deterministic replayable
measurement rather than a behavioural counter over a 5% base rate.

---

## 4. Offline ranker variants — measured, `mc/memory.py` untouched

`_scratch/memdark/variants.py`, 175 tasks, 74 topic files. The control row
reproduces the live probe exactly, which is the check that the offline
reimplementation is faithful.

| | config | reachable | dark | top-3 share |
|---|---|---|---|---|
| **control** | live BM25 topk=3 | 46/74 | 28 | 49% |
| | topk=6 | 62/74 | 12 | 34% |
| | topk=8 | 67/74 | 7 | 30% |
| **length `b`** | b=0.40 | 39/74 | 35 | 56% |
| | b=0.75 (live) | 46/74 | 28 | 49% |
| | b=0.90 | 53/74 | 21 | 45% |
| | **b=1.00** | **58/74** | **16** | **41%** |
| **title boost** | **×0** | **49/74** | **25** | 48% |
| | ×1 | 48/74 | 26 | 48% |
| | ×3 (live) | 46/74 | 28 | 49% |
| | ×6 | 45/74 | 29 | 49% |
| | ×10 | 44/74 | 30 | 49% |
| **archive** | score ×0.5 | 55/74 | 19 | 43% |
| | score ×0.25 | 55/74 | 19 | 43% |
| | dropped entirely | 56/74 | 18 | 43% |
| | quota ≤1 per top-k | 51/74 | 23 | 41% |
| **links** | expand=2 (**live**, topk=3) | 59/74 | 15 | 33% |
| | expand=2, topk=6 | 66/74 | 8 | 26% |
| **rerank** | BM25 top-25 → coverage rescore | 41/74 | 33 | 55% |
| **combos** | topk6 + quota | 65/74 | 9 | 28% |
| | topk6 + b=1.0 | 66/74 | 8 | 31% |
| | **topk6 + quota + expand2** | **67/74** | **7** | **23%** |

### 4.1 The title boost is scoring negative

Turning it **off** gains 3 files (46→49), and the effect is monotone across
0/1/3/6/10. Mechanism: `_mem_corpus` appends title tokens ×3 to the token stream,
which inflates the document **length** in the BM25 denominator. On a 600-byte
note the title is a large fraction of the document — **the boost meant to help
short notes taxes them hardest.**

The 2026-08-05 rationale for indexing the title *into the stream* rather than as
a post-hoc bonus remains correct (a bonus cannot rescue a note with no body hit,
because a document that fails the match test is never scored). It is the
**multiplier** that is wrong. Append once, or exclude title tokens from the length
count. Two-line change, +2–3 files, zero token cost.

### 4.2 Reranking made it decisively worse

46 → 41. A query-coverage rescore promotes documents touching many query terms
shallowly over documents hitting one rare term hard — the wrong trade for a
corpus of file paths, function names and SHAs where **the rare exact token is the
signal**. Recorded as a measured negative so it is not re-proposed, and it weakens
the general market-scan case for a rerank stage on *this* corpus.

### 4.3 `b=1.00` is the best free lever

46→58 reachable, dark 28→16, concentration 49%→41%, at **zero extra prompt
tokens**. Consistent with §2.3's measured residual length bias: `b=0.75` is
under-correcting for a corpus this heterogeneous.

### 4.4 Link expansion is the most underrated mechanism in the system

It is the **only** lever measured here that improves reachability *and* reduces
concentration at the same time — every other lever trades one against the other.
307 of 832 surfaced slots (37%) are wikilink hops. And it is a **push-path**
mechanism: the hop happens inside the read floor, needs no agent action, and is
therefore immune to the 5%-pull-rate constraint that kills the two-level index
proposal. This is our own local knowledge graph and it is already working.

Corollary: **link density is the highest-yield content-side investment**, and it
is precisely targetable. Measured: **20 of 74 topic files are link-orphans**
(degree 0 in, 0 out) and therefore unreachable by expansion by construction. Five
of the eight residual-dark files are orphans. One link each is the fix.

### 4.5 Query expansion from task text — not separately tested, and why

`_mem_tokens` already splits on non-alphanumerics including underscore, so
`mc/memory.py` and `project_memory_system_redesign` are already decomposed at both
index and query time. The obvious expansions happen for free. Stating this rather
than omitting it.

---

## 5. Classification of all dark files, with per-class remedy

Under the **live** configuration (topk=3, expand=2) — the honest baseline.

| file | bytes | best rank | out/in links | fixed by topk=6 |
|---|---|---|---|---|
| `feedback_proactive_default.md` | 1988 | 4 | 1/1 | yes |
| `discovery_gemini_cli_hang_upstream.md` | 1749 | 4 | 2/1 | yes |
| `remote_access_device_naming.md` | 3788 | 4 | 0/1 | yes |
| `arch_subsystems.md` | 4471 | 5 | 0/1 | yes |
| `discovery_mode_a_sse_followup_race.md` | 2251 | 5 | 1/1 | yes |
| `feedback_no_location_prompts.md` | 593 | 6 | 0/0 | yes |
| `arch_misc_tips.md` | 896 | 32 | 0/1 | yes |
| `clayrune_scheduler.md` | 2003 | 7 | 0/0 | **no** |
| `feedback_no_fluff_phrasing.md` | 1200 | 7 | 0/0 | **no** |
| `project_remote_access_domains.md` | 1252 | 8 | 0/0 | **no** |
| `decision_capability_artifact_addendum.md` | 3125 | 14 | 2/0 | **no** |
| `discovery_fable5_post_turn_exit_blocked_pill.md` | 2122 | 15 | 1/0 | **no** |
| `discovery_esc_no_apostrophe_inline_handler.md` | 1621 | 20 | 1/0 | **no** |
| `arch_terminal_popout.md` | 1169 | 37 | 0/0 | **no** |
| `agent_name.md` | 688 | 38 | 0/0 | **no** |

### 5.1 The classes, and the remedy for each

**Class A — benign redundancy (14 of 15).** Dark, but carries a resident curated
pointer. Its fact is already pushed on every prompt; the file holds the detail.
**Remedy: none. Do not rename, do not merge, do not delete.** These are correctly
dark *given* the curated region. They become real losses only if the curated
region is shrunk first — which is why §3 orders the migration the way it does.

**Class B — genuinely lost (1 of 15).** `discovery_esc_no_apostrophe_inline_handler.md`:
no curated pointer, no ranker reach, one out-link and no in-links. **Remedy: give
it a curated line, or a wikilink from `arch_overview.md` / a frontend note.**
Either fixes it; the wikilink is cheaper (zero resident bytes) and is the one I
recommend.

**Class C — link orphans (5 of the 8 residual dark, 20 of 74 corpus-wide).**
`agent_name`, `arch_terminal_popout`, `clayrune_scheduler`,
`feedback_no_fluff_phrasing`, `project_remote_access_domains` all have degree
(0,0) and cannot be reached by expansion at any setting. **Remedy: one wikilink
each, in or out.** Cheap, no resident cost, and it is the only remedy that moves
the mechanism identified in §4.4.

**Class D — ranking margin (16 of 28 at topk=3; 7 of 15 live).** Best rank 4–6.
**Remedy: restore `read_floor_topk` to its shipped default of 6.** A config key.

**Class E — deletion candidates: zero.** I found **no** note in this corpus that
is genuinely dead. `arch_memsearch.md` documents a retired subsystem, but its
*purpose is to stop agents reinstating it* — CLAUDE.md carries a matching
"Retired — do not reinstate" section precisely because the contradiction persisted
for months. Deleting it would recreate the failure it exists to prevent. **I
recommend deleting nothing.**

### 5.2 The ceiling the brief asked me to quantify

| bucket | share of the 28-file dark set |
|---|---|
| fixable by **ranking/config changes alone** | **20 of 28 (71%)** — measured jointly: topk=6 + expand=2 takes the dark set from 28 to 8 |
| fixable by **content edits** (one wikilink each) | **5 of 28 (18%)** — the degree-(0,0) residuals |
| **correctly dark** (benign redundancy, no action) | **14 of 15 under live config** |
| **genuinely lost, needs action** | **1 of 74 (1.4%)** |
| **correctly deleted** | **0** |

Stated as the brief framed it: **ranking changes reach essentially the whole dark
set; note rewriting is needed for approximately none of it; and "correctly dark"
is a large category but only because the curated index is paying for it.**

---

## 6. A continuous recall evaluation

Recall stops being hand-checked only if the eval is cheap, pinned, and gated. The
current probes fail all three: they take ~20 minutes over 300 MB of transcripts,
their metric drifts upward with time, and nothing consumes their output.

### 6.1 What it runs

Three checks, in ascending cost.

**Check 1 — the invariant (fast, the only hard gate).**
For every topic file, assert **NOT** (uncited by the curated region **AND**
unreachable over the pinned suite). Today: 1 violation. This is the
silent-knowledge-loss detector and the only thing that should ever fail a build.

**Check 2 — reachability + concentration on a pinned suite.**
Replay `_memory_search` **with the live call signature read from config**, not a
hardcoded one, over a frozen task suite. Report reachable / dark / top-3 share /
zero-result-rate. Compare against a committed baseline.

**Check 3 — the behavioural gold set (reported, never gated).** See §6.5.

### 6.2 On what corpus of real tasks

**A pinned suite of 175 tasks, frozen today**, extracted once by
`_scratch/memdark/cache_tasks.py` and thereafter never regenerated implicitly.
This is what fixes DEFECT 1: with the task set frozen, reachability can go *down*,
so it can gate.

Growth is handled by **versioning, not appending**: `suite-v1` (175 tasks) stays
immutable; a `suite-v2` may be cut later, and the two are never compared. A
release that cuts a new suite must publish both numbers for one cycle.

Practical note: extraction takes ~20 min over the transcripts. Caching the suite
turns each subsequent run into a few seconds, which is what makes this runnable
nightly.

### 6.3 The metric, and the regression gate

| check | metric | gate |
|---|---|---|
| 1 | count of notes uncited **and** unreachable | **HARD: must not increase.** Any increase = a note just became invisible to both paths. |
| 2 | `reachable@live-config` on suite-v1 | **SOFT: must not drop by >2 files** vs the committed baseline. |
| 2 | top-3 concentration | **SOFT: must not rise by >5 points.** Guards the failure mode that motivated BM25 — a few units eating every slot. |
| 2 | zero-result rate | **HARD: must stay 0%.** It has been 0% for both scorers across every run; a non-zero value means the corpus stopped answering some task at all. |
| 3 | recall@floor on the gold set | **REPORTED ONLY, never gated** (n=9; see §6.5). |

Soft = writes a warning to the run log and the item journal. Hard = non-zero exit.

### 6.4 Where state lives — the DATA_DIR rule

`DATA_DIR = data/projects` (`server.py:444`), and `load_projects()` treats every
`*.json` there as a project. A stray file becomes a malformed project and 500s
`_get_active_restart_blockers`, taking down both restart endpoints.

**Therefore: nothing from this eval goes in `data/projects/`.** State goes in a
sibling directory, structurally outside `DATA_DIR`, so no suffix exclusion is
needed and no future refactor can accidentally re-include it:

```
data/memory-eval/
  suite-v1.json        pinned tasks — VERBATIM RON TASK TEXT
  baseline-v1.json     committed reachability numbers for suite-v1
  runs/<date>.json     per-run results, append-only
```

**`data/memory-eval/` must be gitignored.** The suite is verbatim user task text —
operator-specific by definition, and the CLAUDE.md rule ("would this be wrong on a
stranger's machine?") applies directly. `data/SHARED_RULES.md` is the precedent,
including its sharp edge: gitignoring is not sufficient if a build spec bundles
the file, so **`build-macos.spec` must not be taught to package this directory.**

The *harness* (`tools/memory-eval/`) is checked in. The *data* is not. A fresh
install with no suite is the correct default; the first run cuts one.

### 6.5 The behavioural gold set — a negative result, reported honestly

I attempted a relevance ground truth from behaviour: the sessions where an agent,
*having already been handed a read floor*, went and opened a memory topic file
anyway. That open is a labelled positive.

Measured: 12 sessions, 43 opens. Recall@floor 16% live. **I do not believe that
number and it should not be used.** Two independent problems:

1. **Contamination.** Four of the twelve sessions opened 5–11 files each, and
   reading their task text, all four were sessions *about the memory system* — a
   hivemind write-path audit, an Obsidian-second-brain evaluation, two
   continuations. They were enumerating the corpus, not looking a fact up.
   Counting those as relevance labels would penalise the floor for 34 files it was
   never meant to surface, and would reward any ranker that dumps the whole corpus.
   Excluding them leaves **8 sessions / 9 labels**.

2. **Mislabelling.** On the targeted subset, inspecting misses one at a time:

   | first user message | file the agent later opened | query/doc term overlap |
   |---|---|---|
   | "Can you confirm if the latest version we have here is also available as update to all other users?" | `decision_learning_definition.md` | all, here, the |
   | "can you give me quick summary of all subjects we are handling atm under this project?" | `discovery_claude_resume_ignores_append.md` | all, best, format, list, this, under, you |
   | "[Steward cycle] … come up with publish plan for promoting Clayrune" | `project_gcp_setup.md` | — |

   In every case the note is unrelated to the *opening* message and entirely
   related to where the session **ended up**. The ranker did not fail; it was
   asked the wrong question.

For the record, the targeted-subset numbers (n=9, far too small to gate):

| config | recall@floor |
|---|---|
| topk=3, expand=0 (probe) | 2/9 = 22% |
| **LIVE topk=3, expand=2** | **3/9 = 33%** |
| topk=6, expand=2 | 5/9 = 56% |
| topk=8, expand=2 | 6/9 = 67% |

The ordering is monotone and consistent with §4, which is weak evidence the signal
is real. The *rate* is not trustworthy at n=9. **Report it; never gate on it.**

### 6.6 What the gold-set failure actually uncovered

Chasing those mislabels found something bigger than the eval question, and it is
verified in code rather than inferred.

`_build_agent_context(project, incognito=False, task='', character_body='')` —
`agent_routes.py:1761`. The read floor is gated on `if task:` at line 1915.

Every follow-up path rebuilds context as **`_build_agent_context(p)` with no
`task` argument** — `agent_routes.py:2788, 3049, 3061, 4853, 4859, 4869, 4885`.
So `task` is the empty-string default, the gate is False, and **no read floor is
injected at all**. On the healthy `-r` resume path no context is rebuilt either,
and per the CLAUDE.md gotcha `-r` ignores `--append-system-prompt` regardless.

**The memory push fires exactly once, at dispatch, ranked against the first user
message, and is never refreshed however far the work drifts.**

This reframes the hivemind's central asymmetry. Push-good / pull-dead is correct
but incomplete: **the push path is also one-shot.** A multi-hour session gets one
memory injection sized to its opening sentence. The 5% pull rate is then not
evidence that agents don't want memory — it is what you would expect when the only
refresh mechanism is one the agent must invoke by hand.

I have **not** measured the benefit of fixing this and claim no number for it. I
flag it because our headline metric — reachability against the first user message —
**cannot see this axis at all**, and because the natural fix costs one
deterministic ranked grep per turn with no model: Step-6 checkpointing already
runs mid-session and already holds the transcript delta.

---

## 7. Recommendations, in dependency order

Each is independently verifiable against the two probes, and each is reversible.

| # | change | measured effect | cost | reversal |
|---|---|---|---|---|
| 1 | `read_floor_topk` 3 → 6 (its shipped default) | dark 15 → 8 | ~300 tok/prompt | edit one config key |
| 2 | Pin suite-v1 + commit baseline; fix the probe to read live config | none (measurement) | none | delete the dir |
| 3 | Add the uncited-**and**-unreachable gate | catches the 1 live violation | none | remove the check |
| 4 | Wikilink `discovery_esc_no_apostrophe_inline_handler.md` | 1 → 0 genuinely-lost notes | 0 resident bytes | revert the edit |
| 5 | One wikilink each for the 5 degree-(0,0) residual-dark files | expansion becomes able to reach them | 0 resident bytes | revert |
| 6 | Title boost ×3 → ×1, or exclude title tokens from `len` | +2–3 files | none | one constant |
| 7 | `b` 0.75 → 1.00 | dark 28 → 16 at topk=3 | none | one constant |
| 8 | Archive soft down-weight or per-class quota | +9 files; concentration 49% → 41% | none | one constant |
| 9 | **Only then** evict curated lines, one at a time, gated on #3 | up to 62 lines ≈ most of 16.5 KB | −tokens | re-add the line |
| 10 | Re-rank the floor per turn/checkpoint | **unmeasured** — flagged, not recommended yet | one grep/turn | feature flag |

**#1–#8 are config keys, constants, and wikilinks.** None is a new subsystem. Taken
together they take the live system from 15 dark to ~7 and cut top-3 concentration
from 33% to ~23%, and they must all land *before* #9 touches the curated region —
because §3 shows that shrinking curated first converts benign redundancy into real
loss at a 1:1 rate.

**On scale, explicitly:** this corpus is 1083 KB for one user. Nothing above needs
a vector store, an embedding service, a graph database, or a rerank model. The one
market-scan idea I tested that *is* fashionable — a rerank stage — measured
**worse** (§4.2). The mechanism that measured best is the `[[wikilink]]` layer we
already built.

---

## 8. Artifacts

All prototypes in `_scratch/memdark/` (gitignored), all read-only against the
corpus:

| file | what it does |
|---|---|
| `cache_tasks.py` | extracts + freezes the 175-task suite |
| `forensics.py` | full-rank replay; best-rank per file; topk sweep |
| `live_config_replay.py` | calls `_memory_search` exactly as `_build_agent_context` does |
| `variants.py` | the offline ranker sweep of §4 |
| `classify.py` | live dark set + link degree + curated-pointer coverage |
| `goldset.py` / `goldset_split.py` | the behavioural gold set and its contamination split |

`mc/memory.py`, `MEMORY.md`, and the memory directory were not modified.
