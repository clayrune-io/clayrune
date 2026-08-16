# MEMORY REDESIGN — Seat 3: Market scan, re-tested

**Workstream:** ws_003 · Hivemind `hm_ea8bd971` · Backlog item `dae8d6e7`
**Date of scan:** 2026-08-15. Every web source below was fetched on that date;
publication dates are given per source.
**Scope:** ANALYSIS ONLY. No production code, no `MEMORY.md`, no memory dir was
modified. Two read-only measurements were taken on this box (an import probe and
a `grep` over the memory dir); both are labelled with their method.

---

## 0. The hard constraint this scan was tested against

From `docs/_journal/dae8d6e7-*.md`, measured over 259 sessions by
`tools/memory-eval/retrieval_probe.py`:

| path | rate |
|---|---|
| sessions that READ a memory topic file | 13 / 259 (5%) |
| sessions that ran a memory SEARCH | 7 / 259 (3%) |
| topic files ever opened | 10 of 76 (23 opens; 10 of those are `memory.md` itself) |
| PUSH path (read floor, BM25, no agent action) reaches | 46 / 76 |
| PULL path reaches | 10 / 76 |

**Every system below is graded against that, not against its own benchmark.** A
design that assumes the agent will go fetch is disproved here. This is the
grading rubric, not an opinion.

---

## 1. Verdict up front

| system | buy? | steal | reject, and why |
|---|---|---|---|
| **Letta / MemGPT** | No | memory *blocks* (labelled, size-capped, individually addressable resident regions); sleep-time agents (background writer) | archival paging — it is the pull path, and Letta's own best result came from a **forced** pull |
| **Zep / Graphiti** | No | the *idea* of invalidating superseded facts | the temporal knowledge graph — multiple LLM calls per write + Neo4j/FalkorDB, for ~12 superseded facts |
| **RAPTOR** | No | **collapsed-tree retrieval** — summaries ranked in the same pool as leaves | the tree itself — UMAP+GMM+BIC over 76 docs is meaningless, and needs an ML stack we don't have |
| **mem0** | No | session-start automatic injection (validates our read floor); **entity matching** as a third retrieval signal | LLM-per-write extraction/consolidation pipeline; vector store; managed platform |
| **LangGraph / LangMem** | No | hot-path vs background write distinction (we already have it) | **procedural memory** — the agent rewriting its own instructions. Rejected on *safety*, not scale |
| **Cursor memories** | No | nothing we don't already have | account/project-level auto-notes with no ranked push path |
| **Claude Code auto-memory** | n/a (it's what we run under) | **path-scoped rules** (`paths:` frontmatter) — a mid-session push trigger keyed on file access; the `modified:` ISO-8601 frontmatter timestamp | its topic-file model is pure on-demand — **strictly weaker than our read floor** |
| **Hybrid BM25+vector+rerank** | No | nothing yet | vector half is weakest exactly on our token type; cross-encoder is 568M params / 2–4 GB / ~350 ms CPU |

**The 2026-08-05 conclusion ("no purchase needed, BM25 over 75 files would do")
is UPHELD — but its reasoning was wrong-shaped and is replaced below.**

---

## 2. Re-test of the 2026-08-05 "no purchase needed" verdict

The prior scan said: *the corpus is small, so a vector DB is overkill and BM25
would do.* That is a scale argument. The scale argument is true but weak — it
would flip the moment the corpus grew. The stronger argument, which the prior
scan did not make, is a **corpus-type** argument that does not flip.

### 2a. Measured adoption cost on this box (2026-08-15)

Method: `python -c "import importlib.util …"` over candidate modules, plus
reading `requirements.txt`.

```
sentence_transformers -   transformers -   torch -   onnxruntime -
numpy -   sklearn -   tokenizers -   faiss -   rank_bm25 -   llama_cpp -
```

**All absent.** `requirements.txt` in full: `flask`, `pywebview`, `pythonnet`,
`cryptography`, `keyring`, `requests`, `rfc8785`, `pywebpush`, `firebase-admin`,
`Pillow`, `websocket-client`, `psycopg2-binary`. There is not even `numpy`.

So "add a small local embedding model" is not a config change. It is introducing
an ML runtime (`onnxruntime` + `tokenizers` + a 100–400 MB model file) into a
Flask app that ships as a signed/notarized `.app`, a Windows installer, and a
Linux install — for a 1 MB corpus belonging to one person. That cost was never
stated in the prior scan and it is the decisive number.

*(Related: `build-macos.spec` already bit us once by bundling a file that was
"present on the builder's disk" — CLAUDE.md, 2026-07-12. A model file is exactly
that shape of hazard.)*

### 2b. Are small local embeddings now cheap enough to matter?

Cheaper, yes; free, no. EmbeddingGemma (Google, 308M params) runs "on less than
200MB of RAM with quantization" and uses Matryoshka Representation Learning so
embeddings truncate to 128/256/512 dims
([developers.googleblog.com/en/introducing-embeddinggemma/],
[huggingface.co/blog/embeddinggemma]; the widely-cited `<15 ms` figure is
**EdgeTPU**, not CPU — no CPU-only benchmark was found, so treat CPU latency as
unmeasured).

That is genuinely cheap *at inference*. It is not cheap *at integration*, per
2a. And it is aimed at the wrong problem, per 2c.

### 2c. The corpus-type argument (the one that doesn't flip)

Multiple independent 2026 write-ups converge on BM25 **beating** dense retrieval
on identifier-dense corpora:

- "BM25 outperformed state-of-the-art dense retrieval on financial documents —
  on every metric except Recall@20"
  ([digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026]).
- "Embedding models struggle with rare exact terms — if the model has never seen
  an identifier like `AZ-4471` during training, the query vector carries little
  signal"
  ([tianpan.co/blog/2026-04-12-hybrid-search-production-bm25-dense-embeddings],
  2026-04-12).
- On code specifically: "queries specified exact APIs (`MethodNotAllowed
  exception`) but embeddings retrieved semantically similar but incorrect classes
  (`NotImplementedError`, `HTTPException`), lacking lexical anchoring."
- On engineering docs: identifiers like `Bridge-A` and `Pier-3` "are directly
  matched by BM25, whereas embedding-based retrieval maps these identifiers into
  semantic vector space where vectors cluster closely together, causing retrieval
  failure."

Our retrieval units are `mc/memory.py`, `_build_agent_context`,
`index_byte_budget`, `_mem_class_avgdl`, `feedback_grep_memory_dir.md`, commit
SHAs like `e9e44b6`. **Dense retrieval is weakest precisely there.** This is also
the most likely explanation for why the earlier memsearch (Zilliz/Milvus)
experiment failed here and had to be retired.

### 2d. Where the prior scan was too generous to hybrid

The prior scan endorsed "hybrid BM25+vector+rerank is the current default."
True as an industry default; the measured lift decomposes badly for us:

- Vector half alone: WANDS NDCG **0.7497** hybrid vs **0.6983** BM25-only =
  **+7.4%** ([digitalapplied.com], 2026).
- The headline number — "+17.4% relative Recall@5 (0.816 vs 0.695)" — comes from
  adding a **cross-encoder reranker** on top of hybrid, not from the vector half.
- Cross-encoder cost: `bge-reranker-v2-m3` is **568M params, 2–4 GB RAM**; one
  report measured **350 ms on CPU**, dropping to 80 ms only with a GPU
  ([futureagi.com/blog/best-rerankers-for-rag-2026],
  [localaimaster.com/blog/reranking-cross-encoders-guide], both 2026). The small
  end (`Qwen3-Reranker-0.6B`, `ms-marco-MiniLM-L-12-v2`) is cheaper but still an
  ML runtime.

We inject the read floor **synchronously at dispatch**. Paying ~350 ms plus 2–4
GB RSS to reorder 6 results out of a 76-file corpus is not a trade worth making.

### 2e. Revised verdict

> **UPHELD.** Not because the corpus is small, but because the corpus is
> **token-exact**: BM25 is the *stronger* retriever for it, and the vector half
> of hybrid would have to earn its place against the specific queries we issue.
> **REJECT:** vector index, hybrid RRF, cross-encoder rerank, any vector DB
> (Neo4j / FalkorDB / pgvector / Milvus).

**One caveat I am not hiding.** Our 30 dark files are dark *under BM25*. This
scan does **not** establish that a vector half would leave them dark — nobody has
measured that here, and I did not measure it. The cheap gate before anyone
revisits this: check whether the dark files' distinctive tokens (df ≤ 3) ever
occur in the 170 task strings in `scorer_ab.py`. If their vocabulary never
appears in task text, embeddings *might* reach them; if they are simply never
relevant, nothing will. **That measurement, not an argument, should gate any
future purchase.**

---

## 3. Letta / MemGPT — does the paging survive a 5% pull rate?

**No, not unmodified.** And Letta's own evidence says so.

### What they publish

- **Memory blocks** ([letta.com/blog/memory-blocks/], pub 2025-05-14): a block is
  a labelled, size-capped, individually persisted unit of agent context with a
  `block_id`, an explicit character/token limit, and an optional description that
  guides its use. The context window is *compiled* from DB state per request.
- **Core vs archival**: the leaderboard writeup
  ([letta.com/blog/letta-leaderboard/], pub 2025-05-29) separates **core memory
  read** (in-context) from **archival memory read**, where facts are "hidden from
  the agent unless it uses the archival memory search method." *They measure them
  separately precisely because the archival path can silently fail.*
- **Sleep-time agents** ([docs.letta.com/guides/agents/architectures/sleeptime/],
  fetched 2026-08-15): background subagents "review recent conversations,
  consolidate useful lessons, and update memory without interrupting your active
  work," with an optional "agent reviews before applying" gate. The primary agent
  is not given core-memory-edit tools; the sleep-time agent holds them.

### The disqualifying evidence

[letta.com/blog/benchmarking-ai-agent-memory/] (pub **2025-08-12**), Letta's own
benchmark of its filesystem approach against Mem0 / LangMem / Zep on LoCoMo:

- Letta filesystem + GPT-4o-mini: **74.0%**; Mem0's reported top: **68.5%**.
- **The harness required the pull.** The agent was "given tools for semantic
  search (`search_files`), text matching (`grep`), and answering questions
  (`answer_question`)" with a requirement to "**start by calling `search_files`
  and continue searching through files until it decides to call
  `answer_question`**."
- Letta's stated reason the filesystem won: "simpler tools are more likely to be
  in the training data of an agent and therefore **more likely to be used
  effectively**."

That second quote is an explicit admission that **tool-call likelihood, not
retrieval quality, is the binding variable** — which is exactly our 5%/3%
finding, arrived at from the vendor side.

So the 74.0% is a measurement of retrieval quality *under compelled pull*. Our
5%/3% is the *uncompelled* rate. They are not the same quantity and the benchmark
number does not transfer.

**Live leaderboard note (fetched 2026-08-15):** `leaderboard.letta.com` now shows
a *Filesystem Suite* and a *Skills Suite*; the core-vs-archival memory breakdown
described in the May 2025 blog post is no longer displayed. I could not retrieve
per-benchmark archival-vs-core numbers, so I am **not** asserting a gap size —
only that they built the split deliberately and have since moved the public
scoreboard toward filesystem/agentic-tool-use.

### (1) Steal · (2) Overkill · (3) Push/pull

1. **Steal:** the **memory-block model** — labelled, size-capped, individually
   addressable resident regions with a per-block byte budget. Our curated region
   is the opposite: one undifferentiated 16.5 KB blob with a single global
   budget, which is why `condense` "is not allowed to shrink it" is even a
   sentence anyone had to write. Also steal **sleep-time compute** as validation
   of the shape we already have (Scribe / Step-6 / condense): a background writer
   improving resident memory is the correct investment under a 5% pull rate.
2. **Overkill — explicitly:** the archival tier, the agent-facing paging tools,
   and the Letta server/DB. We would be adding a service to run a pull path our
   own data says fires 3% of the time. Also overkill: their embedding config
   (see `letta-ai/letta` issue #3210, where archival tools hardcoded
   `openai/text-embedding-3-small` and failed with `NotImplementedError` on
   self-hosted servers — a reminder that the archival tier drags an embedding
   dependency with it).
3. **Push/pull:** core blocks + sleep-time = **push, survives**. Archival paging
   = **pull, does not survive**. Adopting "MemGPT-style paging" wholesale would
   import the half that fails here.

---

## 4. Zep / Graphiti — does bitemporal invalidation pay off at our scale?

**The mechanism is right. The machinery is ~1000× oversized. The cheap version is
nearly free here.**

### What it is

[help.getzep.com/graphiti/getting-started/overview] (fetched 2026-08-15): a
temporal knowledge graph. Edges carry validity intervals plus ingestion time
(bi-temporal); on conflict Graphiti **invalidates rather than deletes**, so
point-in-time queries work and superseded facts stop being served as true.
Retrieval is hybrid semantic + BM25 full-text + graph traversal with optional
distance-based reranking, "with no LLM-in-the-loop reranking." Backends: Neo4j,
FalkorDB, or Amazon Neptune. Claimed 94.7% @ 155 ms on LoCoMo, 90.2% @ 162 ms on
LongMemEval.

### Why the machinery is overkill — with the reason, not silently

The cost is at **ingest**, not query, and it is per-write:

- `getzep/graphiti` **issue #1193** ("Custom extraction and lower LLM Costs"):
  "a single activity can trigger multiple LLM calls and embedding calls (node
  extraction, edge extraction, deduplication, etc.), which becomes very expensive
  at scale."
- `getzep/graphiti` **issue #1299** ("Option to have `add_episode` without LLM
  extraction"): users "found the LLM extraction at the end to be an overkill
  (slow and costly)."
- Default `SEMAPHORE_LIMIT` is 10 concurrent operations specifically to avoid
  provider 429s.

So every memory write becomes an entity-extraction + edge-extraction + dedup LLM
pipeline, plus a graph database process, to hold **76 markdown files for one
user**. That is the textbook case of complexity that only pays at 100× our scale.
It also drags in the embeddings and the DB that §2 already rejected.

**REJECT the graph, unambiguously.**

### Does superseded-fact invalidation pay off here?

**Measured on the live corpus, 2026-08-15.** Method:
`grep -ricE "retired|superseded|no longer|deprecated|RETRACTED|reversed|was wrong|outdated"`
over all 76 `*.md` in the memory dir.

| | |
|---|---|
| total marker hits | **50** |
| hits inside `MEMORY_ARCHIVE.md` | **38** (append-only history — such prose is expected and correct there) |
| files outside the archive carrying any marker | **17** |
| files carrying more than 2 | **4** |

So we have **on the order of a dozen genuinely superseded facts**, not thousands.
A bitemporal graph is not warranted by that volume.

**But the failure is real, and it is in the resident region.** `MEMORY.md`'s
always-loaded curated section still carries a pointer line for
`arch_memsearch.md`, whose own frontmatter reads *"RETIRED 2026-05-18 … Do NOT
'use the recall skill at task start' — it does nothing."* CLAUDE.md records that
this contradiction was carried unnoticed **for months** and was caught by the
2026-08-06 night review. That is exactly the Zep failure mode — a stale fact
served as current — occurring in the text we pay for on *every single prompt*.

### The cheap version, and why it is cheapest here

Steal Claude Code's approach instead ([code.claude.com/docs/en/memory]): a single
ISO-8601 `modified` field in the note's YAML frontmatter, so age is visible to
both the human reader and the machinery.

**Measured on this box 2026-08-15:** of 76 files, **74 already begin with a YAML
frontmatter block** (`name` / `description` / `metadata.node_type` /
`metadata.type` / `originSessionId`). The two without are `MEMORY.md` and
`MEMORY_ARCHIVE.md`. Files carrying a `modified`/`updated`/`date` field: **zero**.

So adding recency is a **new key in a block that already exists on 97% of the
corpus** — not a schema migration. It buys the one thing bitemporality actually
delivers at our scale ("is this fact stale?") without the graph, the
extraction LLM calls, or the database.

**What I am *not* claiming:** that a timestamp invalidates anything by itself.
Nothing auto-expires from a date. The enforcement half must still be a rule —
e.g. condense or night-review flags any curated line whose target file's
frontmatter says RETIRED, or whose `modified` date is older than *N* months, for
human review. **That flag is the part that would have caught memsearch in May
instead of August.**

**One more thing anyone proposing to build bitemporality should read first:**
`getzep/graphiti` **issue #1489** (opened 2026-05-15, since closed; PRs #1490 /
#1491) found that the MCP `add_memory` tool "discards caller-supplied temporal
context, instead hardcoding ingestion time," and `delete_episode` left orphan
edges and entities. The reference implementation had its valid-time plumbing
wrong in a shipping path. Bitemporal correctness is not free even for the people
who invented the pitch.

### (3) Push/pull

Graphiti is a **library with a retrieval API**, so the application decides when to
call it. It does not depend on agent-initiated paging, and the 5% pull rate does
not disqualify it. It is disqualified on cost, not on shape.

---

## 5. RAPTOR — the summarization tree

**RAPTOR argues *against* the item's two-level proposal. This is the most
important single result in the scan.**

### What the paper actually says

arXiv **2401.18059**, submitted **2024-01-31**, v1 only; ICLR 2024.

- Leaves: contiguous chunks of **100 tokens**.
- Clustering: **UMAP** dimensionality reduction + **Gaussian Mixture Models**,
  cluster count chosen by **BIC**, **soft** clustering (a node can sit in several
  clusters).
- Summarizer: **gpt-3.5-turbo**. Average summary **131 tokens** over children
  averaging **85.6 tokens**; compression ratio **0.28**.
- Build cost and build time scale **linearly** in document length.
- Headline: **+20 points absolute** on QuALITY with GPT-4; QASPER F1 **55.7**,
  beating BM25 by 5.5 and DPR by 2.7.

### The part the prior scan missed

RAPTOR has **two** retrieval strategies, and the paper picks the non-obvious one:

> "Collapsed tree with 2000 tokens produces the best results, so we use this
> querying strategy for our main results."

- **Collapsed tree** = flatten the whole tree; rank leaves *and* summaries
  together in **one pool**; let the retriever choose the granularity.
- **Tree traversal** = descend level by level — open the category to reach the
  leaf. **This is the strategy that lost.**

The single most-cited hierarchical-summary paper tested the exact shape of the
item's proposed two-level index and reported that the **flat-pool alternative
beats it**. That is independent corroboration of our own 5%-pull probe from a
completely different direction — retrieval accuracy rather than agent behaviour.
Both say the same thing: **do not put a traversal step between the agent and the
knowledge.**

Follow-up work concedes the redundancy cost too: recursive summarization
"introduces a large amount of redundant summaries unrelated to the answer, which
increases computational overhead" (motivating DTCRS, arXiv 2604.07012).

### (1) Steal · (2) Overkill

1. **Steal: collapsed-tree retrieval.** Summaries belong *in the ranked pool*,
   not behind a traversal.
2. **Overkill — explicitly:** the tree construction. UMAP + GMM + BIC over **76
   documents** is not a meaningful clustering problem — BIC model selection on 76
   points is unstable and UMAP is a manifold method built for thousands of
   points. It also needs `numpy` + `scikit-learn` + `umap-learn` + an embedding
   model, none of which exist here (§2a). And the summarization half is something
   **we already own**: `condense` *is* our recursive summarizer and the curated
   region *is* our summary layer, hand-maintained by a human who is better at it
   than gpt-3.5-turbo was. RAPTOR would automate a layer we already have while
   adding an ML stack. **Reject the tree.**

### The code finding this exposed — flagged for independent verification

Reading RAPTOR made me check how our own summary layer enters retrieval.
`mc/memory.py:559-573`, `_mem_corpus`:

```python
if f.name == mem_name:
    for e in _mem_split(txt)[1]:                    # MANAGED entries only
        units.append((f'{f.name}#managed', e, 'managed'))
elif f.name == arch_name:
    for ln in txt.splitlines():                     # one unit per archive line
        if ln.strip().startswith('- '):
            units.append((f.name, ln.strip(), 'archive'))
else:
    units.append((f.name, txt, 'topic'))            # whole file
```

`_mem_split(txt)[1]` is the **managed** region. And `_mem_split_full`'s own
docstring (`mc/memory.py:313-314`) states it outright: *"entries = lines starting
with `- [` (curated pointer lines … are never collected — they're above the
sentinel)."*

**If I am reading that correctly:** the CURATED region — 16,854 bytes, 72% of the
always-resident index, ~69 hand-written pointer lines, the most curated artifact
in the system — **is not a retrieval unit at all.** It is never scored, never
ranked, never returned by a memory search. Its *only* delivery mechanism is
unconditional residency.

That reframes the cost question. The curated region is not expensive because it
is flat, and not merely because it is resident — **it is resident because
residency is the only channel it has.**

RAPTOR's collapsed tree is the direct answer: index those ~69 lines as their own
retrieval units (they are already one-line, self-contained and title-bearing —
close to ideal short units) and residency becomes a **per-line ranking decision**
instead of an all-or-nothing 16.5 KB tax. A line that keeps winning the read
floor earns residency; a line that never ranks *and* is never resident is dead by
evidence rather than by judgement — which is the eviction signal the item wanted,
without the self-amplifying label risk the 2026-08-05 review identified (a badly
labelled note is never *opened*, but it can still be *ranked*).

**Two honest caveats.**
- I am one reader of this code. `ws_001` owns the E2E audit — **please confirm
  independently** before anything is built on it. Confidence: medium.
- `_mem_class_avgdl` exists precisely because mixing whole topic files with
  one-line archive units broke length normalization once. Adding a fourth class
  of ~69 very short units needs the same per-class treatment or it will distort
  scores. **I have not measured** what indexing curated lines would do to the
  46/76 reachability figure. That is a `scorer_ab.py` experiment, not a claim.

---

## 6. mem0

[mem0.ai/blog/state-of-ai-agent-memory-2026] — article dated **2026-07-18**,
fetched 2026-08-15.

April 2026 algorithm: LoCoMo **92.5** at **6,956 tokens/query**; LongMemEval
**94.4** at 6,787; BEAM(1M) **64.1**; BEAM(10M) **48.6**. Two changes drove it:
**single-pass hierarchical extraction** that "treats agent-generated facts equally
with user-stated facts," and **multi-signal retrieval** fusing "semantic
similarity, BM25 keyword matching, and entity matching" into one score. Retrieval
happens "at the start of a new session" and is "injected into the context window."

### (1) Steal

- **The confirmation that automatic session-start injection is a legitimate
  *primary* channel, not a fallback.** The system currently posting the best
  public numbers pushes rather than pages. Our read floor is not behind the state
  of the art — **it is the state-of-the-art shape.**
- **Entity matching as a third signal** alongside BM25. Our entities are file
  paths, function names, config keys and SHAs. A cheap exact-token/entity boost is
  a plausible route at the 30 dark files **without embeddings**. This is the one
  concrete retrieval upgrade on the whole scan that costs nothing but code.
- **~7k tokens per retrieval as calibration.** Our ~6k-token resident index plus a
  topk=6 read floor sits in the same budget class. We are not obviously
  overspending — which matters, because it means the redesign's goal should be
  *what* is resident, not simply *less*.

### (2) Overkill — explicitly

The managed platform, the vector store, and the LLM-per-write extraction /
consolidation pipeline (ADD / UPDATE / DELETE / NOOP routing; one LLM call per
add; per [emergentmind.com/topics/mem0], "lacks heuristic or closed-form
novelty-gating, potentially incurring high write-time LLM call costs"). At 76
notes for one user that is a lot of moving parts to decide something a human
already decided when they wrote the note. **Reject the pipeline; keep the
retrieval shape.**

### One thing to explicitly NOT adopt

"Treats agent-generated facts equally with user-stated facts." Our learning-safety
rails deliberately do the **opposite** — artifacts carry `origin:
interactive|unattended` and unattended-origin output is withheld from steward
cycles, so autonomous output can never become autonomous input (CLAUDE.md,
load-bearing). Mem0's symmetry is a correctness choice for a consumer memory;
ours is a constitutional one.

### (3) Push/pull

**Push-shaped. Survives a 5% pull rate.** The only system scanned whose primary
channel is automatic injection.

### Benchmark caveat — stated, not buried

These numbers are self-reported and the field is in open dispute. Mem0's paper
reported Zep at 65.99; Zep rebutted with a corrected 75.14 alleging
misconfiguration; Zep's rebuttal was itself corrected from a claimed 24% margin
to 10% after Mem0's CTO documented an arithmetic error (Category-5 answers in the
numerator but not the denominator). Independent write-ups
([essays.bloo-mind.ai/posts/2026-05-20-mem-eval/], 2026-05-20;
[llms3.com/blog/when-the-benchmarks-stopped-agreeing-july-2026], July 2026)
conclude that no vendor memory benchmark is comparable across vendors right now.
**Use these to confirm a system is not grossly broken, not to rank them.** No
number in this section is a reason to buy anything.

---

## 7. LangGraph / LangMem — REJECT on safety, not scale

[langchain.com/blog/langmem-sdk-launch] (pub **2025-02-18**). Three memory types:
semantic (facts/preferences), episodic (past interactions — "LangMem doesn't yet
support opinionated utilities" for it), and **procedural**, the differentiator,
which saves "learned procedures as **updated instructions in the agent's
prompt**." Memory forms **in the background** via reflection/consolidation rather
than in the hot path. Backed by the LangGraph store (Postgres / vector / KV).

**Procedural memory is the exact capability our authority guard exists to
forbid.** CLAUDE.md, load-bearing: *"Learning may change **how** the agent works;
it must NEVER change **what** the agent is allowed to do."* That rail was pinned
after one session's sentence ("Full autonomy, no permission/go-ahead needed, by
any means necessary") became a global always-loaded PREFERENCE skill instructing
every agent in every project to stop asking permission — six such artifacts had
accumulated and were quarantined on 2026-07-11.

LangMem's headline feature is the mechanism that incident was about. **Do not
adopt it, and do not adopt it in disguise**: any proposal where the memory system
writes into the agent's *instruction* surface hits the same rail, regardless of
what it is called. Their semantic half is unremarkable and we already have it.

*Health note:* repo active into June 2026, but the latest PyPI release is
**0.0.30, published 2025-10-27** — no package release in ~7 months.

**Steal exactly one idea:** the hot-path vs background distinction — which is the
same convergence as Letta's sleep-time agents. Both put the write side on a
background process so the foreground agent never has to fetch. **We already have
this** (Scribe + Step-6 + condense), and under a 5% pull rate it is the correct
half of the system to invest in.

**(3) Push/pull:** background consolidation is push-shaped and survives; the
procedural half is rejected before the question arises.

---

## 8. Cursor's memory

Cursor Memories shipped in Cursor 2.x: the agent saves short notes across chats;
they are auto-generated (Cursor watches a chat, extracts a durable preference,
stores it), scoped per-project and per-user, **account-level, capped and
summarized** — described as closer to ChatGPT's account-wide memory than to a
project memory store. Promotion to a shared `.cursor/rules/` file is manual.
There is also a community pattern (Eric Zakariasson, **2026-04-13**) of pointing
the agent at its own past transcripts to propose `.cursor/rules/` and
`.cursor/skills/` files, reviewed as a diff.

*(Source caveat, stated plainly: I could not retrieve Cursor's own docs page for
Memories — `cursor.com/docs/context/memories` served the **Rules** page instead.
Everything above is from secondary write-ups and the Cursor forum, so treat the
mechanism detail as second-hand. I am not asserting injection semantics or caps.)*

1. **Steal:** nothing we don't already have. The transcript-mining-into-rules
   pattern is our Distiller, which is further along and has safety rails Cursor's
   flow does not.
2. **Overkill / not applicable:** account-level memory is a multi-project,
   multi-user concern. We are one user with per-project memory dirs already.
3. **Push/pull:** notes are small and resident; there is **no ranked push path**
   over a topic corpus at all. Nothing here helps the 30 dark files.

---

## 9. Claude Code's own memory — the biggest steal on the scan

Read [code.claude.com/docs/en/memory] in full, fetched 2026-08-15. This is the
system Clayrune runs *under*, so it is both prior art and constraint.

### 9a. Their design is ours — and ours is ahead on the push path

Verbatim:

> "Each project gets its own memory directory at
> `~/.claude/projects/<project>/memory/`" containing "`MEMORY.md` — Concise
> index, loaded into every session" plus topic files.
>
> "Topic files like `debugging.md` or `patterns.md` **are not loaded at startup.
> Claude reads them on demand** using its standard file tools when it needs the
> information."
>
> "The first **200 lines** of `MEMORY.md`, or the first **25KB**, whichever comes
> first, are loaded at the start of every conversation. **Content beyond that
> threshold is not loaded at session start.**"

So Anthropic's own answer is a resident index + **pure on-demand leaves** — the
exact two-level design our probe disproved — with **no ranked push path
whatsoever**. Our BM25 read floor (46/76 files reached with zero agent action) is
a capability Claude Code does not have.

**Conclusion: do not port their model to us; that would be a regression.**

Two calibration points fall out:

- Their limit is 25 KB; our `index_byte_budget` is 24,576 bytes. **Independent
  convergence within 2%** — mild evidence that the *budget* is not the thing to
  change.
- They handle the ceiling with a feedback loop we could copy directly: after a
  write, if `MEMORY.md` is near a limit, Claude Code "reminds Claude to shorten
  it: keep one line per entry, move detail into topic files, and merge or drop
  stale entries"; if over, "the write still succeeds, but Claude Code returns an
  error telling Claude to rewrite the index, because everything past the limit is
  dropped on the next load." Note that they measure **only the content that
  loads** — frontmatter and block-level HTML comments are stripped first.

### 9b. The one mechanism worth stealing — path-scoped rules

`.claude/rules/*.md` with YAML frontmatter:

```markdown
---
paths:
  - "src/api/**/*.ts"
---
```

> "These conditional rules only apply when Claude is working with files matching
> the specified patterns. **Path-scoped rules trigger when Claude reads files
> matching the pattern**, not on every tool use."

**This is a push path keyed on FILE ACCESS, not on task text.** Our read floor
keys only on the first user message at dispatch — one shot, fired *before* any
code is opened. A path trigger fires **mid-session**, when the agent actually
opens `mc/memory.py`, and pushes the note about it **unrequested**.

That is a second, orthogonal, **zero-pull** retrieval signal, and it is the most
plausible mechanism on this entire scan for killing the 30 dark files — many dark
notes are about one specific subsystem file, which is precisely the trigger
condition. It also degrades gracefully: a note with no `paths:` behaves exactly
as today.

Cost: a `paths`/globs key in topic-file frontmatter (which 74/76 files already
have) plus a `PostToolUse`-style hook. **No embeddings, no DB, no LLM.**

Two implementation warnings from their own docs, worth inheriting for free:
brace expansion is budgeted (1,000 expanded patterns / 4 MiB per rule, because
many brace groups once stalled the CLI at startup), and a malformed `[` in a glob
must fail to *nothing* rather than failing the read (before v2.1.207 one invalid
pattern broke the Read tool for every file the rule was evaluated against).

### 9c. Cheap temporal metadata

> "When Claude writes a memory file that begins with YAML frontmatter, Claude Code
> records the write time in a `modified` frontmatter field as an ISO 8601
> timestamp. The timestamp shows how current the fact is, both to you and to
> Claude when it reads the memory back." (v2.1.214+)

This is the five-line version of Zep's bitemporality — see §4.

### 9d. Two minor freebies

- **Block-level HTML comments are stripped before injection**, so they cost zero
  tokens. A free channel for human-maintainer notes *inside* the index.
- **`/doctor`'s trim rule** is a usable editorial policy for what earns permanent
  residency, from Anthropic's own tooling: it "cuts content Claude can derive from
  the codebase, such as directory layouts, dependency lists, and architecture
  overviews, and **keeps pitfalls, rationale, and conventions that differ from
  tool defaults**." That is a defensible answer to the design's open question
  *"what earns permanent residency."*

### (3) Push/pull

`CLAUDE.md` and path-scoped rules = **push, survive**. Auto-memory topic files =
**pull, and with no mitigation at all** — no forced-pull prompt, no ranker. Their
topic-file tier is the weakest pull path on the scan.

---

## 10. Anthropic's Memory tool (API) — the forced-pull precedent

[platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool], fetched
2026-08-15. Tool version `memory_20250818`, now **GA** (no beta header).
Client-side: Claude requests `view` / `create` / `str_replace` / `insert` /
`delete` / `rename` under `/memories`; your application executes them. Nothing is
auto-injected.

**The relevant part.** From *Prompting guidance* — when the memory tool is
present in `tools`, **the API automatically adds this to the system prompt**:

> ```
> IMPORTANT: ALWAYS VIEW YOUR MEMORY DIRECTORY BEFORE DOING ANYTHING ELSE.
> MEMORY PROTOCOL:
> 1. Use the `view` command of your `memory` tool to check for earlier progress.
> 2. ... (work on the task) ...
>    - As you make progress, record status / progress / thoughts etc in your memory.
> ASSUME INTERRUPTION: Your context window might be reset at any moment, so you
> risk losing any progress that is not recorded in your memory directory.
> ```

Anthropic built a pure pull memory, found the natural pull rate insufficient, and
**hard-wired an unconditional first-action pull into the system prompt,
server-side** — not left to the developer. That is the industry's answer to our
exact 5%/3% problem, and it is the cheapest intervention on this whole scan: a
mandated first-turn listing costs roughly one tool call, not a redesign.

**Do not oversell it.** The forced pull is unconditional (every task pays it),
there is no published compliance measurement, and it only forces *that* a pull
happens — the agent still chooses *which* file to open. Our probe shows 10 of 23
opens were `memory.md` itself: even when agents pull, they pull the index. So the
ceiling on a directory-listing mandate is bounded by ranking quality, which is the
push path again.

**Other stealables from the same page:**
- `view_range` paging on long files. Our topic files reach 20.3 KB
  (`project_memory_system_redesign.md`) and we have no partial-read convention.
- Their security guidance: "Track memory file sizes and cap how large a file can
  grow… Periodically delete memory files that haven't been accessed in a long
  time." Note this is exactly the **access-statistics eviction** the item proposed
  and that the 2026-08-05 review flagged as self-amplifying (a badly labelled note
  is never opened, so the counter reads it as cold). Anthropic recommends it
  without addressing that failure mode. **Our review is ahead of their doc here.**
  §5's ranking-based signal avoids the trap; an open-count-based one does not.
- The **multisession software development pattern**: an *initializer* session sets
  up a progress log and feature checklist **before** substantive work, rather than
  files accruing ad hoc. That is a structural answer to curated-region drift.

**REJECT** the memory tool as a *replacement* for our system: it is pull-only with
no ranked push path, i.e. strictly worse than our read floor on the one axis we
measured.

---

## 11. Hybrid BM25 + vector + rerank — consolidated

Covered in §2c–2d. Summary:

- **Production pattern (2026):** BM25 + dense → Reciprocal Rank Fusion → cross-
  encoder reranker → LLM. RRF operates on *ranks*, not scores, which is why naïve
  weighted score-averaging fails in production.
- **Measured lifts:** hybrid over BM25-only **+7.4%** NDCG (WANDS); cross-encoder
  on top of hybrid **+17.4%** Recall@5.
- **Our position:** the vector half is weakest on exactly our token type; the
  reranker is the expensive component and we inject synchronously at dispatch.
  **Reject both.**
- **The one thing worth taking from the hybrid literature** is not the vector
  half — it is mem0's third signal, **entity matching** (§6), which is a lexical
  technique and needs no model.

---

## 12. Answers to the brief's three questions, consolidated

### (1) Worth stealing, ranked by value-per-unit-cost

| # | steal | from | cost | targets |
|---|---|---|---|---|
| 1 | **Path-scoped push** — `paths:` glob in topic frontmatter, fires when the agent reads a matching file | Claude Code `.claude/rules/` | frontmatter key + a hook | the **30 dark files**, via a signal orthogonal to task text |
| 2 | **Collapsed-tree retrieval** — index curated pointer lines as retrieval units so residency becomes a per-line ranking decision | RAPTOR | scorer change + a 4th `avgdl` class | the **16.5 KB O(topics) curated region** |
| 3 | **`modified:` ISO-8601 frontmatter** + a staleness flag for review | Claude Code / Zep-lite | ~5 lines; 74/76 files already have frontmatter | **superseded facts** carried as true (the memsearch case) |
| 4 | **Entity matching** as a third retrieval signal beside BM25 | mem0 | pure lexical, no model | the **30 dark files** |
| 5 | **Forced first-turn pull** in the system prompt | Anthropic memory tool | one prompt line | raises the **3% search rate**, bounded by ranking quality |
| 6 | **Memory blocks** — labelled, individually byte-budgeted resident regions instead of one 16.5 KB blob | Letta | index format change | makes "what earns residency" answerable per block |
| 7 | **`/doctor`'s editorial rule** — keep pitfalls, rationale and conventions that differ from tool defaults; cut what's derivable from the code | Claude Code | policy, not code | **what earns permanent residency** |
| 8 | Free: HTML comments cost no tokens; `view_range` partial reads for the 20.3 KB outlier | Claude Code / Anthropic | trivial | index hygiene |

### (2) Overkill — stated explicitly, with the reason

| rejected | reason |
|---|---|
| Vector index / embeddings | Weakest on exact tokens, which is most of our corpus; and this box has **no ML stack at all** — adding one means shipping a model file through a signed `.app` and three installers for 1 MB of markdown |
| Cross-encoder reranker | 568M params, 2–4 GB RAM, ~350 ms CPU, to reorder 6 of 76 results synchronously at dispatch |
| Hybrid RRF | +7.4% measured lift, and the lift is on natural-language queries, not identifier queries |
| Any vector/graph DB (Neo4j, FalkorDB, Milvus, pgvector) | A database process for 76 markdown files belonging to one person |
| Zep/Graphiti temporal knowledge graph | Multiple LLM + embedding calls **per write** (issues #1193, #1299), for ~12 genuinely superseded facts |
| RAPTOR tree construction | UMAP + GMM + BIC over 76 points is statistically meaningless; and `condense` already is our summarizer |
| mem0 extraction/consolidation pipeline | One LLM call per write with no novelty gating, to classify facts a human already curated |
| Letta archival tier + server | Adds a service to run a pull path that fires 3% of the time |
| **LangMem procedural memory** | **Rejected on safety, not scale** — the agent rewriting its own instructions is the exact capability the authority guard forbids, after a real incident on this machine |
| Cursor-style account-level memory | Solves a multi-user/multi-project problem we do not have |

All of these are rejected **explicitly**, per the brief, rather than omitted.

### (3) Push/pull scorecard against the 5% constraint

**Pull-dependent — degraded or defeated at a 5% pull rate:**
Letta archival memory · Anthropic memory tool (mitigated by a forced system-prompt
pull) · **Claude Code auto-memory topic files (no mitigation at all)** · RAPTOR
tree-traversal mode.

**Push-shaped — survive:**
mem0 (session-start injection) · Letta core blocks + sleep-time writes · Claude
Code `CLAUDE.md` and path-scoped rules · RAPTOR collapsed-tree mode · LangMem
background consolidation.

**Neither — application-driven:** Zep/Graphiti (retrieval is an API the app calls;
disqualified on cost, not shape).

**The structural finding:** every system that puts real weight on the push path
does it in exactly one of two ways — (a) automatic ranked injection at session
start, or (b) a background writer that improves resident memory. **Nobody has a
third answer.** We already have both: the read floor is (a), Scribe/Step-6/
condense is (b). The design should invest in those two, plus **path-scoped
triggering as a genuinely third, mid-session push signal** — the one mechanism on
this scan we do not already have in some form.

---

## 13. What this seat did NOT establish

Stated plainly, because this item's history includes three retracted
over-confident claims.

1. **Whether the 30 dark files would be reachable by embeddings.** Not measured.
   The gate before any future purchase is the df ≤ 3 vocabulary-overlap test
   described in §2e — a `scorer_ab.py` experiment, not an argument.
2. **The size of Letta's core-vs-archival gap.** The live leaderboard no longer
   displays that breakdown; I retrieved the framing, not the numbers.
3. **Cursor Memories' actual injection semantics and caps.** Their own docs page
   served the Rules page instead; §8 is second-hand and labelled as such.
4. **Whether indexing curated lines as retrieval units improves or harms the
   46/76 reachability figure.** Unmeasured. `_mem_class_avgdl` history says a new
   unit class can distort scores; this must be A/B'd before it is built.
5. **That the curated region is absent from the retrieval corpus** — §5 reads the
   code and the docstring as saying so, but `ws_001` owns the E2E audit and should
   confirm independently. Marked medium confidence, not high.
6. **CPU latency for EmbeddingGemma.** The published `<15 ms` figure is EdgeTPU.
   No CPU-only benchmark was found; I did not run one (nothing is installed).
7. **Any vendor benchmark as a ranking.** The LoCoMo dispute (§6) means none of
   the published scores are cross-comparable. None was used as a reason to buy.

---

## Sources

Fetched or searched 2026-08-15. Publication dates as shown on the source.

**Letta / MemGPT**
- [Memory Blocks: The Key to Agentic Context Management](https://www.letta.com/blog/memory-blocks/) — pub 2025-05-14
- [Benchmarking AI Agent Memory: Is a Filesystem All You Need?](https://www.letta.com/blog/benchmarking-ai-agent-memory/) — pub 2025-08-12
- [Letta Leaderboard](https://www.letta.com/blog/letta-leaderboard/) — pub 2025-05-29; live board at [leaderboard.letta.com](https://leaderboard.letta.com/)
- [Sleep-time agents](https://docs.letta.com/guides/agents/architectures/sleeptime/)
- [letta-ai/letta issue #3210 — archival tools hardcode embedding model](https://github.com/letta-ai/letta/issues/3210)

**Zep / Graphiti**
- [Graphiti overview](https://help.getzep.com/graphiti/getting-started/overview)
- [getzep/graphiti issue #1193 — Custom extraction and lower LLM Costs](https://github.com/getzep/graphiti/issues/1193)
- [getzep/graphiti issue #1299 — add_episode without LLM extraction](https://github.com/getzep/graphiti/issues/1299)
- [getzep/graphiti issue #1489 — historical backfill temporal-correctness gaps](https://github.com/getzep/graphiti/issues/1489) — opened 2026-05-15, closed

**RAPTOR**
- [RAPTOR (arXiv 2401.18059)](https://arxiv.org/abs/2401.18059) — submitted 2024-01-31; [HTML v1](https://arxiv.org/html/2401.18059v1)
- [DTCRS: Dynamic Tree Construction for Recursive Summarization (arXiv 2604.07012)](https://arxiv.org/pdf/2604.07012)

**mem0**
- [The State of AI Agent Memory 2026](https://mem0.ai/blog/state-of-ai-agent-memory-2026) — article dated 2026-07-18
- [Mem0 architecture summary](https://www.emergentmind.com/topics/mem0)
- [The Benchmark Theatre](https://essays.bloo-mind.ai/posts/2026-05-20-mem-eval/) — 2026-05-20
- [When the Benchmarks Stopped Agreeing](https://llms3.com/blog/when-the-benchmarks-stopped-agreeing-july-2026) — July 2026

**LangGraph / LangMem**
- [LangMem SDK launch](https://www.langchain.com/blog/langmem-sdk-launch) — pub 2025-02-18

**Cursor**
- [Cursor Rules guide](https://skillwright.app/blog/cursor-rules-guide) · [Auto-generate rules from chat history](https://aicatchup.com/skills/cursor-rules-from-chat-history) — Zakariasson prompt 2026-04-13
  *(Cursor's own Memories doc could not be retrieved; see §8 caveat.)*

**Claude Code / Anthropic**
- [How Claude remembers your project](https://code.claude.com/docs/en/memory)
- [Memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)

**Hybrid retrieval / rerankers / local embeddings**
- [Hybrid Search: BM25, Vector & Reranking Reference 2026](https://www.digitalapplied.com/blog/hybrid-search-bm25-vector-reranking-reference-2026)
- [Hybrid Search in Production: Why BM25 Still Wins on the Queries That Matter](https://tianpan.co/blog/2026-04-12-hybrid-search-production-bm25-dense-embeddings) — 2026-04-12
- [Best Rerankers for RAG in 2026](https://futureagi.com/blog/best-rerankers-for-rag-2026/) · [Reranking & Cross-Encoders for RAG](https://localaimaster.com/blog/reranking-cross-encoders-guide)
- [Introducing EmbeddingGemma](https://developers.googleblog.com/en/introducing-embeddinggemma/) · [HF: Welcome EmbeddingGemma](https://huggingface.co/blog/embeddinggemma)

**Local measurements (this box, 2026-08-15)**
- Python import probe over 10 ML/retrieval modules — all absent; `requirements.txt` read in full.
- `grep -ricE` supersession-marker census over 76 memory files; frontmatter presence census (74/76); `modified|updated|date` field census (0/76).
- `mc/memory.py:309-346`, `:555-599` read directly (`_mem_split_full`, `_mem_corpus`).
