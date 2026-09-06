# Memory graph measurement — is a connection-based retrieval redesign justified?

**Measured 2026-09-06 against repo `7438137`. Read-only pass over 19 memory
vaults. No design proposal in this document — it exists only to establish the
numbers.**

Headline: **17 broken wikilinks**; hop distribution from an index line is
**196 / 15 / 4 / 0 / 20 unreachable** (hop 0 / 1 / 2 / 3+ / unreachable);
and **no, chaining beyond one hop does not buy much on this corpus** — the
graph is too shallow and the index too complete for a second hop to reach
anything. The pressure on the 24 KB budget is also, in most vaults, not coming
from the pointer index at all. Detail below.

---

## 0. Method and reproducibility

Scripts live in `_scratch/` (gitignored) and are re-runnable from the repo root:

| Script | Produces |
| --- | --- |
| `_scratch/memory_graph_measure.py` | main table: nodes, edges, broken links, hop histogram, path lengths, byte cost. `--json` for raw, `--edges N --project <substr>` for link contexts |
| `_scratch/memgraph_broken_classify.py` | broken links bucketed by cause |
| `_scratch/memgraph_broken_crossvault.py` | broken links whose target exists in a *different* vault |
| `_scratch/memgraph_vault_detail.py <label>` | one vault: degree histograms, components, seeds, demotion frontier |
| `_scratch/memgraph_seedset.py` | greedy minimum index seed-set at K = 1..4 hops, with byte saving |
| `_scratch/memgraph_delivery.py` | `delivery_stats.json` analysis — how often the existing hop actually delivered |
| `_scratch/memgraph_edge_semantics.py` | edge-relationship classification by cue text |
| `_scratch/memgraph_index_lines.py <label>` | curated index line taxonomy by bytes |
| `_scratch/memgraph_index_split.py` | curated vs managed split of every `MEMORY.md` against the mechanical floor |

**Corpus.** Every `<claude home>/projects/*/memory/` directory containing a
`MEMORY.md` and at least two `.md` files: **19 vaults, 321 markdown files.**
Vaults with a single file (stale path-encoding duplicates) were excluded and are
noted where relevant. Every measurement mirrors the real code's semantics rather
than the docs — see §1 for the `file:line` this is derived from.

**Labels.** `mission_control` is this repo. Other vaults are labelled `V02`…`V19`
with a neutral kind tag, **numbered by `MEMORY.md` size** (the §6a ordering); two
personal-project vaults are labelled `personal-1`/`personal-2` and take the
positions their size implies. No machine paths, account state, or personal
content appears below.

**Token estimates** use 4 bytes/token, the same ratio `CLAUDE.md` already uses
for the 24 KB budget ("~24KB ≈ 6k tokens"). It is an estimate, not a count.

**Secrets scan (incidental).** A pattern scan over every vault `.md` for
credential-shaped strings (`sk-…`, `ghp_…`, `AKIA…`, `xox…`, and
`key/token/secret/password = <16+ chars>`) returned **zero matches**. Stating the
fact only; no values were read or recorded.

---

## 1. How retrieval actually works today (read from the code)

- **Corpus construction** — `mc/memory.py:1388` `_mem_corpus()`. Every `*.md` in
  the memory dir becomes scoring *units* in four classes: `topic` (a whole file),
  `position` (a whole `position_*.md`), `archive` (one `- [` line of
  `MEMORY_ARCHIVE.md`), `managed` (one entry of `MEMORY.md`'s managed region).
  `continuity.md` is skipped (already injected verbatim).
  **The curated region of `MEMORY.md` is not in the corpus at all** — it is
  auto-loaded, so it is excluded by construction.
- **Links are parsed only for `topic` units** — `mc/memory.py:1466`:
  `'links': _mem_link_targets(text) if cls == 'topic' else []`.
- **The graph is topic-only on both ends** — `mc/memory.py:553`
  `_mem_link_graph()` builds `by_key` from `cls == 'topic'` units only, so a
  wikilink pointing at a `position_*.md`, at `MEMORY.md`, or at the archive
  resolves to nothing. Unresolvable targets are silently dropped here; the
  docstring says so explicitly ("a dangling link costs retrieval nothing but is
  still visible to a human" via `tools/memory-link-check.py`).
- **Link key canonicalisation** — `mc/memory.py:521` `_mem_link_key()` strips
  every non-alphanumeric character and lowercases. `arch-mobile-ui`,
  `arch_mobile_ui` and `Arch Mobile UI` all key the same. **`.md` inside a
  wikilink does not**, because `md` survives as letters (see §3).
- **Ranking** — `mc/memory.py:1495` `_memory_search()`. BM25, IDF global, length
  normalised **per unit class**, title tokens folded into topic documents at
  `bm25_title_boost`. Positions get a reserved slot behind a coverage gate.
- **Expansion is exactly one hop, both directions, with decay** —
  `mc/memory.py:1677` `_mem_expand_links()`, called once at
  `mc/memory.py:1643`. Constants at `mc/memory.py:497-498`:
  `_LINK_DECAY_OUT = 0.5`, `_LINK_DECAY_IN = 0.35`. Expansion results are
  **appended**, never substituted, so they cannot displace a lexical hit. There
  is no recursion and no second frontier.
- **Dispatch call site** — `mc/blueprints/agent_routes.py:2634-2638`:
  `topk = read_floor_topk` (live config **6**),
  `expand = read_floor_link_expand` (live config **2**), `record='read_floor'`.
  The scheduler loop mirrors this at `mc/blueprints/scheduler_routes.py:502-505`.
  The explicit `/memory/search` route uses the same function
  (`mc/blueprints/guide_routes.py:699`).
- **Cold tier is out of the loop.** `mc/memory_fts.py:1-38` states plainly that
  the FTS5 transcript index is **not** part of the read floor; only the explicit
  search route appends it.
- **Delivery telemetry** — `mc/memory_delivery.py:92` `record()` counts, per
  corpus unit, `n` (times delivered) and `via` (times delivered *by link
  expansion*), against a `tasks` denominator. This is the only ground truth we
  have about which pointers are load-bearing.
- **The byte budget** — `mc/memory.py:1870` `_index_byte_cap()` reads
  `index_byte_budget` (live config **24576**). `_index_byte_floor()` at
  `mc/memory.py:1878` is **cap − 1024 = 23552**. `_enforce_index_cap()`
  (`mc/memory.py:1919`) hard-raises `MemoryCapExceeded` on the attended write
  paths; background writers use `_index_overflow()` non-raising.
- **The mechanical floor evicts managed entries only** — `mc/memory.py:2025`
  `_over_floor()` and the eviction loop at `mc/memory.py:2154`. Its docstring:
  *"the curated region is never touched by machinery."* This matters a great
  deal and is the subject of §6.

**Consequence worth stating up front:** the index and the graph use **two
different link syntaxes**. Index pointers are markdown links
(`[Label](topic_file.md)`); graph edges are `[[wikilinks]]` inside topic bodies.
The graph layer cannot see index pointers and the index cannot see edges. There
is no code path that walks from an index line into the graph.

---

## 2. The graph as it exists

All 19 vaults, topic files only (the only class that participates):

| | count |
| --- | --- |
| markdown files | 321 |
| topic nodes (graph participants) | 235 |
| position files (parsed, but never graph nodes) | 46 |
| raw `[[wikilink]]` mentions in topic bodies | 283 |
| **distinct resolved edges** | **258** |
| self-links | 0 |
| **broken links (target file does not exist)** | **17** |
| wikilinks resolving to an existing but non-graph file | 0 |
| wikilinks written in a non-source file (position / index / archive — invisible to retrieval) | 7 |
| connected components | 70 |
| orphans (no inbound edge) | 107 / 235 = **46%** |
| isolated (no edge at all, in or out) | 55 / 235 = **23%** |

Per vault (top by node count):

| vault | files | topics | edges | broken | orphans | components |
| --- | --- | --- | --- | --- | --- | --- |
| `mission_control` | 104 | 86 | 108 | 3 | 37 | 19 |
| V09 (trading) | 66 | 64 | 58 | 4 | 35 | 23 |
| V04 (product website) | 31 | 18 | 25 | 4 | 7 | 2 |
| V11 (cad) | 18 | 15 | 22 | 0 | 3 | 2 |
| V07 (app) | 22 | 13 | 20 | 0 | 2 | 1 |
| V05 (app) | 13 | 11 | 8 | 0 | 5 | 5 |
| personal-1 | 8 | 7 | 6 | 0 | 3 | 4 |
| V06 (product/cloud) | 7 | 6 | 8 | 0 | 2 | 1 |
| V03 (trading tool) | 10 | 5 | 3 | 4 | 3 | 3 |
| 10 further vaults | 42 | 10 | 0 | 2 | 10 | 10 |

**Degree distribution, `mission_control`** (86 nodes, 108 edges):

- out-degree: `0×29, 1×24, 2×20, 3×10, 4×2, 6×1` — mean 1.26, max 6.
- in-degree: `0×37, 1×23, 2×17, 3×3, 4×3, 5×1, 10×1, 15×1` — mean 1.26, max 15.

The in-degree tail is two nodes: an operational caveat note at **15 inbound**
and a second at **10 inbound**. Together they absorb 23% of all inbound edges in
the vault. Both are procedural reminders ("check live state", "restart needs
approval") linked from everywhere; both are already index lines. A chained walk
from almost any node lands on them, which is a property of the corpus worth
knowing before designing traversal.

**Components, `mission_control`:** 19 — one giant component of 66 nodes, two of
size 2, and 16 singletons. The graph is one hairball plus dust, not a set of
navigable clusters. Aggregate across all vaults: 70 components for 235 nodes.

---

## 3. Broken links — 17, and none of them fail loudly

This is the number that was most wanted. All 17, bucketed by cause:

| cause | n | what it means |
| --- | --- | --- |
| **cross-vault** | 5 | the target note *exists*, in a sibling project's vault. `_mem_link_graph` is per-vault, so it can never resolve. Two trading projects link at each other across the boundary. |
| **truly absent** | 6 | no file with that key exists anywhere. |
| **name drift** | 2 | target exists in the same vault under a drifted slug — e.g. a link to `…-ignores-append-system-prompt` where the file was later shortened to `…_ignores_append.md`, and a link to `…-closed-the-category` where the file gained a `_2026-08` date suffix. |
| **`.md` written inside the wikilink** | 2 | `[[reference-naming-origin.md]]` — **the target file exists and is right there in the same vault.** `_mem_link_key()` strips punctuation but keeps the letters, so the key becomes `referencenamingoriginmd` and misses `referencenamingorigin`. Pure syntax trap. |
| **link to a non-note entity** | 2 | `[[preference-94fe9918]]` (a learning artifact) and `[[mc-clayrune-apis]]` (a skill). Neither namespace is in the vault. |

So **4 of 17 point at a file that exists in the same vault and are broken only
by spelling** (`.md` suffix ×2, slug drift ×2), and **5 more point at a real
note in another vault**. Nine of seventeen are addressing failures, not missing
content.

Nothing gates any of this. `tools/memory-link-check.py` exists and its docstring
says exactly why ("Obsidian renders a broken link red on sight; nothing here
does, which is how 6 of them survived unnoticed until 2026-08-09"), but it is a
manual tool with no hook, no test, and no scheduled run. The count has grown from
6 to 17 since it was written.

Separately: **7 wikilinks are written in files that are not graph sources** —
2 in position files, 3 in archive lines, 1 in an index. Those are invisible to
retrieval regardless of whether they resolve, because `_mem_corpus` only attaches
`links` to `topic` units.

---

## 4. Reachability — the core question

**Definition.** *Seeds* = topic files named by a curated `MEMORY.md` index line
(markdown link, wikilink, or bare `*.md` mention). Distance = BFS over the
wikilink graph treating edges as undirected, because `_mem_expand_links` walks
both `out` and `in` (`mc/memory.py:1694`).

**All 19 vaults, 235 topic nodes:**

| distance from nearest index line | nodes | share |
| --- | ---: | ---: |
| hop 0 — named directly by an index line | **196** | 83.4% |
| hop 1 — reachable by today's expansion | **15** | 6.4% |
| hop 2 | **4** | 1.7% |
| hop 3+ | **0** | 0.0% |
| unreachable from any index line | **20** | 8.5% |

**`mission_control` alone (86 topics):** hop 0 = 76, hop 1 = 7, hop 2 = 1,
hop 3+ = 0, unreachable = 2.

**The hop-2+ population across the entire corpus is four files. In this repo's
vault it is one file.** That is the size of the prize for chaining, measured
directly.

The 20 unreachable nodes are not distributed: **13 of them are one vault (V07)
whose `MEMORY.md` contains no pointer to any of its own notes at all** — it is a
flat prose index — and **5 more are a second vault (V03) with the same shape**.
Those are islands created by an index that never pointed anywhere, not by a
traversal depth limit. No amount of chaining reaches them, because there is no
seed to chain from.

### The important caveat on this whole section

**Distance-from-an-index-line is a measure of curation coverage, not of
retrieval coverage.** The read floor does not traverse from index lines; it
traverses from BM25 hits, and **every topic file is a BM25-scorable corpus unit
in its own right.** Reachability via the graph is therefore a *second* path to
notes that already have a first one.

The delivery telemetry confirms this empirically. For `mission_control`, over
**349 recorded read-floor tasks since 2026-08-24** (`delivery_stats.json`,
corpus 788 units):

- 205 distinct units were delivered at least once, across 2,778 delivery slots.
- **1 of 86 topic files has never been delivered.** The corpus is not cold.
- 693 of 2,778 slots (24.9%) were filled by link expansion — but that figure is
  **mechanical, not a relevance signal**: `topk=6` plus `expand=2` means 2 of 8
  slots are expansion slots by construction, and the same ~25% appears in every
  vault where expansion fired (25.0%, 25.0%, 24.0%, 24.9%). It measures the
  config, not the graph.
- The signal that *is* meaningful: **54 units have ever arrived via a link, and
  only 2 have ever arrived *only* via a link.** In 349 tasks, one-hop expansion
  surfaced two notes that BM25 never found on its own.

---

## 5. Do chains exist to walk?

**Pairwise shortest paths, all vaults, connected pairs only** (3,254 unordered
pairs across 70 components):

| length | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 | 13 | 14 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| pairs | 232 | 481 | 626 | 572 | 423 | 303 | 214 | 151 | 109 | 78 | 43 | 17 | 4 | 1 |

Long paths do exist — the `mission_control` giant component has a diameter of 12.
**But they are long paths between two notes, not paths from an index line to an
unreached note.** §4 already showed that 83% of notes sit at hop 0 and only 4
across the whole corpus sit at hop 2. The chains are there; there is nothing at
the far end of them that the index does not already name.

### The one place depth would pay: index demotion

Reframe the question as *"if the read floor could chain K hops, how few index
pointers would still cover the same set of notes?"* Greedy minimum seed-set
(`_scratch/memgraph_seedset.py`), `mission_control`, over the 67 pointer lines
(11,961 B) that cover 76 of 86 topics:

| chain depth | index lines needed | pointer bytes | saved vs today |
| --- | ---: | ---: | ---: |
| K = 1 (**today's capability**) | 29 | 4,870 B | **−7,091 B (−59%)** |
| K = 2 | 19 | 3,181 B | −8,780 B (−73%) |
| K = 3 | 15 | 2,519 B | −9,442 B (−79%) |
| K = 4 | 14 | 2,398 B | −9,563 B (−80%) |

The second-largest vault (V09, trading, 57 pointer lines / 11,198 B) behaves the
same: K=1 → −54%, K=2 → −68%, K=3 → −73%.

**Read that table carefully.** The first hop is worth 7.1 KB. The second hop adds
1.7 KB. The third adds 0.7 KB. **83% of the total available saving is already
available at the hop depth we shipped in August 2026 and have never used for
demotion.** The gap is not traversal depth; it is that nothing demotes an index
line when its target becomes reachable.

**What this table cannot tell us.** It assumes a surviving seed is *hit* by BM25
for the same tasks that today's demoted line would have covered. Demoting a line
removes it from always-loaded context, so its subtree becomes reachable only when
the ranker lands on the seed. We have no measurement of that hit rate — the
delivery sidecar records what arrived, never what a task needed and missed. Any
demotion number above is an upper bound on the saving and says nothing about the
recall cost.

---

## 6. Index efficiency, and a correction to the premise

### 6a. Where the 24 KB actually goes

The brief's framing is that an ever-growing pointer index is what pushes
`MEMORY.md` toward 24 KB. **For 4 of the 6 most-pressured vaults, that is not
what is happening.** `MEMORY.md` has two regions and only one of them is
pointers:

| vault | file B | % of 24 KB budget | curated B | % of 23,552 B floor | managed B | managed entries |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `mission_control` | 23,443 | 95.4% | **19,719** | 83.7% | 3,724 | 6 |
| personal-2 | 22,725 | 92.5% | **0** | 0.0% | 22,725 | 88 |
| V03 (trading tool) | 22,681 | 92.3% | **19,505** | 82.8% | 3,176 | 9 |
| V04 (product website) | 22,401 | 91.1% | **3,527** | 15.0% | 18,874 | 51 |
| V05 (app) | 22,381 | 91.1% | **17,877** | 75.9% | 4,504 | 13 |
| V06 (product/cloud) | 22,142 | 90.1% | **1,516** | 6.4% | 20,626 | 61 |
| V07 (app) | 21,273 | 86.6% | **3,136** | 13.3% | 18,137 | 38 |
| V08 (trading) | 19,696 | 80.1% | **19,617** | 83.3% | 79 | 0 |
| V09 (trading) | 18,457 | 75.1% | **17,114** | 72.7% | 1,343 | 4 |
| personal-1 | 14,889 | 60.6% | 878 | 3.7% | 14,011 | 37 |
| V11 (cad) | 7,551 | 30.7% | 1,369 | 5.8% | 6,182 | 14 |
| 8 further vaults | ≤3,385 each | ≤13.8% | ≤561 | — | — | — |

**Total always-loaded index across all 19 vaults: 230,176 B ≈ 57,500 tokens.**

The mechanical floor (`_over_floor`, `mc/memory.py:2025`) evicts **managed
entries only**, oldest first, to keep the whole file under 23,552 B. So the
session-log region *expands to fill whatever the curated region leaves*. A
project at 95% of budget is at its **designed steady state**, not in distress —
`mission_control` sits 109 B under the floor, which is the floor doing its job.

The number that actually matters is the middle column: **curated bytes against
the 23,552 B floor**, because when curated crosses it the machinery has nothing
left to evict and the attended write path starts returning 413. `mission_control`
is at **83.7%**, V08 at 83.3%, V03 at 82.8%, V05 at 75.9%. Four vaults are
genuinely approaching an unrecoverable index; the other two of the "top six" are
not — their pressure is 84–100% session log, which no graph mechanism touches.

### 6b. Not every curated line is a pointer

`mission_control`'s 19,719 B curated region, by line kind:

| line kind | lines | bytes | % of file |
| --- | ---: | ---: | ---: |
| pointer into this vault | 67 | 11,961 | 51.0% |
| bullet prose, **no pointer at all** | 35 | 5,631 | 24.0% |
| dated inline fact, no pointer | 9 | 1,438 | 6.1% |
| headings | 8 | 301 | 1.3% |
| pointer to a repo doc (`docs/*.md`) | 2 | 260 | 1.1% |

**7,069 B — 36% of the curated region — has no link target of any kind.** These
are one-line facts written directly into the index rather than into a note. No
graph mechanism, at any hop depth, can demote a line that points nowhere. The
same shape appears in V09 (38 prose lines / 4,107 B) and is the *entire* curated
region in V07 (30 prose lines, zero pointers) and V03.

Across all 19 vaults: **186 in-vault pointer lines (34,451 B) against 436
pointer-free prose lines (61,608 B)**, plus 33 lines (6,732 B) pointing at repo
docs rather than notes. There are more than twice as many index lines that point
at no note as point at one, and they cost nearly twice the bytes — **59% of all
curated index bytes in the corpus are prose with no link target.**

### 6c. Demotion candidates

142 of the 196 index-named topic files (**72%**) are reachable at exactly one hop
from *some other* index-named file — i.e. today's expansion would already surface
them if the ranker hit that other seed. For `mission_control`, 60 of 76.

That is the naive count. The greedy seed-set in §5 is the honest one, because
demoting one line changes what the remaining lines cover: 29 of 67 lines suffice
at K=1, not 16.

**What the data cannot tell us here.** Nothing in the corpus records *why* a
pointer was promoted to the index. A line may be there because the author judged
it must be in front of every agent unconditionally — a binding constraint, not a
convenience — and the graph cannot distinguish that from redundancy. Demotion
candidacy is a structural property; load-bearingness is an editorial judgement
this measurement does not have access to.

---

## 7. Edge semantics — are these typed relationships?

Classified all 283 wikilink mentions by the cue text in the ~90 characters
before the link (`_scratch/memgraph_edge_semantics.py`):

| relationship implied by the cue | n | share |
| --- | ---: | ---: |
| see-also (`See`, `Related:`, `see also`, `per`, `detail in`) | 158 | 55.8% |
| untyped (bare, or 2nd/3rd item in a `Related: [[a]], [[b]], [[c]]` list) | 109 | 38.5% |
| continues / builds-on | 7 | 2.5% |
| companion / sibling | 4 | 1.4% |
| parent / surrounding-system | 2 | 0.7% |
| constraint / caveat | 2 | 0.7% |
| **supersedes** | **1** | 0.4% |

Real examples, verbatim fragments:

- **see-also, the dominant form** — *"See [[project-memory-system-redesign]] for
  the surrounding system."*
- **untyped by position** — *"Related: [[arch_misc_tips]] (static cache headers),
  [[arch_overview]], [[feedback-verify-volatile-state]]."* One cue, three edges;
  only the first is typed by anything.
- **the single supersedes** — *"Related: [[research-competitor-gtm-channels]]
  (the 2026-08-02 snapshot this supersedes), …"* — and note the type is buried in
  a parenthetical *inside* a `Related:` list, indistinguishable from its
  neighbours without reading the prose.
- **a genuine typed edge, expressed only in prose** — *"Continues
  [[decision_learning_loop_closed]]"* and *"sibling to Phase 5's skill
  self-install rung ([[decision_fix2_skill_starvation]])"*.
- **caused-by, expressed as a footnote** — *"See [[feedback-verify-volatile-state]]
  (this is incident #2 of the verify-before-asserting lesson)."*

**Verdict on typed edges: 94% of edges are see-also or untyped.** The
distinguishable relationships exist — supersedes, continues, companion,
caused-by — but there are **16 of them in the entire 283-mention corpus**, and
the one `supersedes` is a single instance. A typed-edge scheme has almost nothing
to exploit today. It would be building the vocabulary first and hoping authors
adopt it, not harvesting structure that is already there.

One structural note that a typed scheme *would* find real signal in: the two
highest in-degree nodes in `mission_control` (15 and 10 inbound) are both
procedural caveats. Every edge into them is semantically "and mind this rule",
which is a different relationship from "and here is the companion note" — and
under undirected traversal they act as hubs that shorten paths between otherwise
unrelated notes. That is a measured property, not a proposal.

---

## 8. Cost

- **Always-loaded index, all 19 vaults: 230,176 B ≈ 57,500 tokens.** No single
  session loads all of them; each session loads its own project's file.
- **`mission_control`: 23,443 B ≈ 5,900 tokens per prompt**, of which 19,719 B is
  curated and 3,724 B is the 6-entry session log.
- Six vaults are ≥90% of the 24,576 B budget. Measured against the operative
  number — curated bytes vs the 23,552 B mechanical floor — **four** are in real
  trouble: `mission_control` (83.7%), V08 (83.3%), V03 (82.8%), V05 (75.9%).
  The other two are 84–100% session log and will simply keep evicting.
- Supporting bulk, not always loaded: 752,531 B of topic files, 58,201 B of
  position files, 2,259,950 B of archives.

---

## 9. What the data does not support

Stated plainly, because several of these limits are load-bearing on any
conclusion drawn from the above.

1. **We cannot measure retrieval *misses*.** `delivery_stats.json` records what
   arrived in a prompt. Nothing records what a task needed and did not get. Every
   statement about the read floor "working" is a statement about coverage, never
   about precision or about the counterfactual.
2. **The 24.9% via-link delivery rate is a config artifact**, not evidence the
   graph is valuable. `topk=6 + expand=2` guarantees 25% of slots are expansion
   slots. The only defensible signal is `only_via = 2 of 205 units over 349
   tasks`.
3. **Demotion savings are upper bounds.** §5's seed-set assumes BM25 hits the
   surviving seed. Unmeasured, and unmeasurable from this data.
4. **Load-bearing vs dead-weight index lines cannot be settled structurally.**
   Delivery counts tell us a note was surfaced; they do not tell us whether it
   changed the agent's behaviour, and they say nothing about the ~36% of curated
   bytes that are prose with no target.
5. **Reachability was measured undirected.** Directed-out-only reachability would
   be strictly smaller. Undirected is the honest model because
   `_mem_expand_links` walks both directions — but it means "reachable" includes
   backlink-only paths that carry a 0.35 decay rather than 0.5.
6. **Nineteen vaults, one operator, one authoring style.** The two vaults with
   zero index pointers (V07, V03) and the two with 19 KB of pointer-free prose
   (V08, V03) show the authoring convention is not uniform even here. Nothing
   about how a *stranger's* vault would look is measurable from this sample.
7. **Cross-vault edges are counted as broken and that is a judgement.** Five
   links point at real notes in a sibling project. Under the current per-vault
   graph they are broken. Whether cross-vault edges *should* resolve is a design
   question this pass does not answer.
8. **The archive is out of scope of the graph entirely.** 2.26 MB of archive
   lines carry no links, participate in no traversal, and are reachable only by
   BM25 — and archive lines take 87 of `mission_control`'s 205 delivered units.
   A graph redesign that only covers topic files leaves the largest class of
   delivered content untouched.

---

## 10. Summary of measured findings

1. **17 broken wikilinks** across 19 vaults, none of which fail visibly. Nine are
   addressing failures (2 caused by writing `.md` inside the link, 2 by slug
   drift, 5 pointing across a vault boundary), 6 point at nothing that exists,
   2 point at non-note namespaces. `tools/memory-link-check.py` catches these but
   is manual and unhooked; the count has grown from 6 to 17 since it was written.
2. **Hop distribution: 196 at hop 0, 15 at hop 1, 4 at hop 2, 0 at hop 3+, 20
   unreachable.** For `mission_control`: 76 / 7 / 1 / 0 / 2. The hop-2+
   population across the whole corpus is four files.
3. **18 of the 20 unreachable notes are two vaults whose index points at nothing
   at all.** They are curation gaps, not traversal-depth gaps.
4. **One-hop expansion has surfaced 2 notes in 349 tasks that BM25 could not find
   on its own.** 85 of 86 topic files in this vault have been delivered at least
   once. The corpus is reachable; BM25 is the reach mechanism, not the graph.
5. **The graph is one hairball plus dust:** 70 components for 235 nodes; 46% of
   nodes have no inbound edge; 23% have no edge at all. Mean degree 1.26.
6. **94% of edges are see-also or untyped.** Sixteen typed relationships exist in
   283 mentions; exactly one `supersedes`.
7. **The 24 KB pressure is mostly not the pointer index.** Only 4 of 19 vaults
   have a curated region above 70% of the mechanical floor. In two of the six
   most-pressured vaults, 84–100% of the file is the auto-evicting session log.
   And 36% of `mission_control`'s curated bytes are prose lines with no target,
   which no graph mechanism can demote.
8. **83% of the achievable index saving is available at one hop** — the hop we
   already have. K=1 → −59% of pointer bytes; K=2 adds 14 points; K=3 adds 6.

**Does chaining beyond one hop buy us anything here? No — not on this corpus.
Four files in the entire vault set sit at hop 2, none at hop 3+, and the second
hop is worth about 1.7 KB of index on the largest vault against 7.1 KB for the
first. The measurable problems are elsewhere: 17 silently broken edges, two
vaults whose index points at nothing, 36% of the index being untargetable prose,
and a one-hop expansion that has been shipped since August and never used to
demote a single index line.**
