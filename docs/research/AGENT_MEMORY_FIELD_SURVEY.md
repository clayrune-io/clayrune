# The agent-memory field — a survey organised by design decision

**Researched 2026-09-06.** Scope: everyone *except* the two systems already
covered in this directory. OKF Agent Memory is analysed in
[`OKF_AGENT_MEMORY_COMPARISON.md`](OKF_AGENT_MEMORY_COMPARISON.md) and our own
corpus is measured in [`MEMORY_GRAPH_MEASUREMENT.md`](MEMORY_GRAPH_MEASUREMENT.md);
neither is re-litigated here. **This document contains no recommendation for what
to build.** It is evidence for a design team that has to decide.

**Evidence tags.** Every claim carries one.

- **[SRC]** — read from the cited source file at the cited path. Where a claim is
  load-bearing I note that I verified it first-hand.
- **[DOC]** — stated in official documentation or an API reference that was read.
- **[PAPER]** — stated in a paper that was read; the tag says abstract or full text.
- **[2ND]** — a vendor blog, press release, or third-party write-up. A claim
  about the world, not a fact about it.

Reads were split between this session and six parallel research agents working
to the same standard. Two claims in an earlier draft of this document were
**wrong and were corrected by that process** — both are flagged in §3a, because
how they were wrong is instructive.

---

## 0. Bottom line

**1. Supersession is not solved. The field's own literature says so, with counts.**
*Always-On Agents* coded **435 works** and tallied how many address each stage of
the state lifecycle: retrieve 269, write 200, forget 66, **rollback 27**. Its
summary sentence: work "concentrates on accumulating and retrieving state more
often than on governing, recovering, or relinquishing it," and "none in the
corpus reports recovery success or cost after corruption" [PAPER, full text,
[arXiv:2606.30306](https://arxiv.org/abs/2606.30306), 2026-06-29]. A second
2026 study benchmarked **thirteen** agent-memory configurations and found that
**none** implements supersession with a preserved reason, and none prevents
re-proposal by mechanism [PAPER,
[arXiv:2606.15903](https://arxiv.org/html/2606.15903v1), 2026-06-14].

**2. The systems with the best supersession *representation* score worst on the
benchmarks that test it.** Zep/Graphiti has the most principled model in any
shipped system — four correctly separated timestamps, superseded facts closed
rather than deleted, full history retained [SRC, verified first-hand]. On
MemoryAgentBench's FactConsolidation task, which tests exactly "a fact changed,
do you use the new one," it scores **7.0%**. Mem0 scores 18%. **BM25 keyword
search scores 48%** and a `max(serial)` in Python beats every system by 20–33
points [PAPER, full text,
[arXiv:2507.05257](https://arxiv.org/abs/2507.05257);
[arXiv:2606.01435](https://arxiv.org/abs/2606.01435)]. The data model is not what
is failing. Nothing in the read path is obliged to honour it.

**3. Nobody joins the two halves of the problem, and the half everyone dropped
was solved in 1986.** Preserving *why* something was superseded, and *using that
record to block re-proposal*, exist separately and never together. de Kleer's
ATMS recorded a **nogood** — a contradictory assumption set — and thereafter
refused to re-explore it; that idea survives as conflict-driven clause learning
in SAT solvers and left knowledge representation behind [PAPER,
[ATMS chapter](https://www.dbai.tuwien.ac.at/staff/wotawa/atmschapter1.pdf)].
Every 2026 system treats supersession as a **retrieval-ranking** problem — make
the current fact easier to find — rather than an **admissibility** problem —
check a proposal against what was already rejected. Michael Nygard's ADRs state
the preservation half perfectly and have no machinery: "If a decision is
reversed, we will keep the old one around, but mark it as superseded… It's still
relevant to know that it **was** the decision, but is **no longer** the
decision" [DOC, [cognitect.com, 2011-11-15](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)].

**4. The two biggest consumer vendors moved in opposite directions in the same
month, and one of them documents the failure mode in its own help centre.** On
2026-06-04 OpenAI **retired the user-managed saved-memories list** for a
synthesised profile the user cannot see or directly edit, and its stated reason
names the problem exactly: "Memories could also contradict one another, such as
'I'm training for a marathon' and 'I sprained my ankle,' which made
personalization less accurate" [DOC,
[Memory FAQ](https://help.openai.com/en/articles/8590148-memory-faq)]. Five
weeks later, on 2026-07-10, Anthropic went the *other* way — from a rolling daily
summary to "a set of individual, categorized entries that Claude reads and
updates during your conversations" [DOC,
[release notes](https://support.claude.com/en/articles/12138966-release-notes)].
And Google states the industry's position on negative knowledge in plain help-centre
prose: "**Instructions for Gemini to forget or avoid topics in chats doesn't
always work perfectly. To make sure Gemini stops mentioning a topic, delete any
chats about it from your Gemini Apps Activity**" [DOC,
[support.google.com](https://support.google.com/gemini/answer/16598625)]. That is
a vendor documenting that "stop suggesting X" is best-effort and the only reliable
remedy is destroying the source data.

**5. The inconvenient one: two independent controlled studies find the LLM
extraction step is net negative.** Holding the pipeline fixed and swapping only
the stored representation, verbatim chunks beat LLM-extracted typed artifacts by
**+15.9 points on LoCoMo and +22.0 on LongMemEval-S**; a one-hop semantic graph
did not close the gap [PAPER,
[arXiv:2601.00821](https://arxiv.org/abs/2601.00821)]. A separate controlled
study found agent self-memory **underperformed basic retrieval, 42% vs 47%**,
and mem0 matched cloud RAG on only 2 of 6 question types **at 50x the cost**
[PAPER, [arXiv:2606.29914](https://arxiv.org/abs/2606.29914)]. The production
version of the same result: an audit of **10,134 mem0 entries over 32 days found
97.8% junk**, and upgrading the extractor from a 2B local model to Claude Sonnet
made it *worse per token* — "a better model follows the extraction prompt more
faithfully, which means it extracts more indiscriminately" [2ND,
[mem0#4573](https://github.com/mem0ai/mem0/issues/4573)].

---

## 1. Decision one — what is the unit of memory, and who authors it

### 1a. The landscape

| System | Unit | Authored by | Typed? |
|---|---|---|---|
| Anthropic memory tool | a **file** under `/memories`, free text | the model, always | no |
| Claude Code auto memory | a **topic file** + one index line | the model | **yes** — `user` / `feedback` / `project` / `reference` |
| Claude Code CLAUDE.md | a markdown instruction file | **the human, always** | no |
| Graphiti / Zep | episode → entity node → **edge carrying a `fact` sentence** | LLM extraction only | node/edge labels, advisory |
| mem0 | an extracted **statement**, 15–80 words, one vector row | LLM extraction only | no |
| Cognee | `DataPoint` nodes/edges from an ECL pipeline | LLM, with optional ontology | **yes, optionally enforced** |
| Letta v1 (archived) | core-memory `Block`, archival `Passage` | agent tools or developer API | `label` on blocks |
| Letta v2 (`letta-code`) | a **markdown file in a git repo** | the agent | by directory, LLM-chosen |
| MCP reference `memory` server | entity / relation / **observation string** | the model | free-string types |
| LangGraph `BaseStore` | an **item**: `(namespace, key) → dict` | the developer's code | no |
| LlamaIndex `Memory` | a **block**: static, fact-extraction, or vector | mixed by block type | by block class |
| CrewAI | a **discrete fact** in a scope path | the crew, post-execution | LLM-inferred |
| Generative Agents | an **observation** in an append-only stream | the agent | no |
| A-MEM | a **note** with 7 attributes | LLM, at write time | keywords + tags |
| Hindsight | a record in one of **four networks** | mixed | world / experience / observation / opinion |

### 1b. The one place authorship is a first-class design axis

Claude Code is the only system found that **states the human-versus-model
authorship split as a design axis and builds two separate mechanisms around it**
[DOC, [code.claude.com/docs/en/memory](https://code.claude.com/docs/en/memory)]:

| | CLAUDE.md files | Auto memory |
|---|---|---|
| Who writes it | **You** | **Claude** |
| What it contains | Instructions and rules | Learnings and patterns |
| Loaded into | Every session | Every session (first 200 lines or 25KB) |

Auto memory types each note with a `type` frontmatter field: `user` (role,
expertise, working preferences), `feedback` (corrections you give Claude and
approaches you confirm), `project` (ongoing work and decisions not derivable
from code or git history), `reference` (where to find information outside the
project) [DOC]. There is also a stated *exclusion* rule, which is rarer than it
sounds: "Claude skips anything it can derive from the codebase… It also skips
anything your CLAUDE.md files already say" [DOC].

**Reading:** a four-value type vocabulary plus a stated exclusion rule is more
structure than most research systems have, shipped in a product rather than
proposed in a paper. The `feedback` type is the closest thing in any shipped
system to a durable record of a human correction — though §3f shows it is not
enforced as one.

Two other systems make a partial distinction. **Hindsight** separates four
networks so as to give "visibility into what an agent knows versus what it
believes," with "evolving opinions with confidence scores" [PAPER,
[ACL 2026 System Demonstrations](https://aclanthology.org/2026.acl-demo.27/)] —
a knows-versus-believes axis, not human-versus-model. **Cognee** carries
`ProvenanceEntry.is_automated: bool` alongside `agent_type`, `agent_id` and
`role` [SRC], which is the only explicit automated-versus-not boolean found
anywhere — and it ships **disabled by default** (§5b).

### 1c. Where the unit is a whole file, and what that costs

Anthropic's memory tool makes the unit a **file** with no schema. The
declaration is the entire configuration — `{"type": "memory_20250818", "name":
"memory"}` — and there is no input schema to define [DOC,
[memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool)].
Six commands: `view`, `create`, `str_replace`, `insert`, `delete`, `rename`.
Storage is the developer's: "Claude requests file operations, and your
application executes them."

Two properties match the failure class documented in the OKF comparison:

- **`create` silently clobbers, by explicit sanction.** Claude's tool
  description says `create` "creates or overwrites," and the docs add:
  "Returning the error is the reference behavior, and overwriting instead is a
  valid implementation choice" [DOC].
- **`str_replace` with `new_str` omitted is a pure deletion.** "when it's
  omitted, `old_str` is deleted without a replacement" [DOC]. Nothing records
  what was removed.

**Letta v2 is the interesting variant of the file-as-unit design**, because it
solves the audit problem with an existing tool: memory is a **git repository**
of markdown at `.letta/memory/`, and a "defrag" subagent periodically splits,
merges, deletes and "resolves contradictions," committing through a worktree
with no squash and no gc, so full history is retained [SRC]. Constraints
(`maxDepth: 2`, `maxFileCharacters: 20_000`) are enforced by a **pre-commit
hook** — one of very few hard write-time gates in this entire survey. **The
caveat is decisive:** nothing surfaces that history to the agent. There are no
exported history-reading functions, and `git log` appears once, in a defrag
prompt, as a post-commit check [SRC]. It is an audit trail for humans, not a
retrieval path.

### 1d. LLM extraction as the authoring path, and who has given up on it

The dominant 2025–2026 pattern is that an LLM reads a transcript and emits the
unit. mem0, Graphiti, Cognee, CrewAI, LlamaIndex's `FactExtractionMemoryBlock`
and A-MEM all do this. Two organisations have now walked away from parts of it,
and one wrote down why.

**LangChain wrote down why.** Its v0.3 migration guide separates deprecated
memory classes into groups with *different* stated reasons, and the group
covering `ConversationEntityMemory`, `BaseEntityStore` and the entity stores
gets this [DOC, verified at the `v0.3` tag,
[`migrating_memory/index.mdx`](https://raw.githubusercontent.com/langchain-ai/langchain/v0.3/docs/docs/versions/migrating_memory/index.mdx)]:

> "These abstractions have received limited development since their initial
> release. This is because they generally require significant customization for
> a specific application to be effective, making them less widely used than the
> conversation history management abstractions.
>
> For this reason, there are no migration guides for these abstractions."

**Reading:** the structured, model-populated knowledge store failed on
*economics*, not correctness. That is a judgement about exactly the category
most 2026 memory systems now occupy, from a maintainer who shipped one and
withdrew it.

**Dark:** that rationale is no longer published.
`python.langchain.com/docs/versions/migrating_memory/` 308-redirects to a
generic overview page that does not contain it, while the deprecation message
still shipped in code points at that dead URL [DOC, checked 2026-09-06]. The
text survives only in git.

**mem0 deleted its graph memory entirely.** PR
[#4805](https://github.com/mem0ai/mem0/pull/4805), merged 2026-04-14, is
+10,200 / **−17,228** across 120 files. Removed: `mem0/graphs/**`,
`graph_memory.py`, `memgraph_memory.py` and the TypeScript equivalents; graph
store drivers for **Neo4j, Memgraph, Kuzu, Apache AGE and Neptune** dropped;
`MemoryConfig` no longer has a `graph` field [SRC]. The removal was noticed only
because `AGENTS.md` still advertised the subsystem
([#6591](https://github.com/mem0ai/mem0/issues/6591), 2026-07-25).

The numbers that make it look rational are in **mem0's own paper** [PAPER,
[arXiv:2504.19413](https://arxiv.org/abs/2504.19413), Tables 1–2] — plain Mem0
vs the graph variant Mem0g on LoCoMo:

| Metric | Mem0 | Mem0g | Delta |
|---|---|---|---|
| Single-hop (J) | 67.13 | 65.71 | **−1.42** |
| **Multi-hop (J)** | 51.15 | 47.19 | **−3.96** |
| Overall (J) | 66.88 | 68.44 | +1.56 |
| Search p95 | 0.200s | 0.657s | **3.3x slower** |
| Tokens / conversation | ~7k | ~14k | **2x** |

**Reading:** the graph bought 1.56 points overall and *lost* 3.96 on multi-hop —
the reasoning task graphs are specifically sold for — at 3.3x latency and 2x
tokens. A year later the code was deleted.

**Stated plainly as a caveat:** PR #4805's body does not mention graph removal
at all. It is framed as a v3 pipeline port, and no changelog says "we removed
graphs because they lost." The deletion is primary; the causal story is a
reading.

### 1e. Notes that rewrite each other

**A-MEM** (NeurIPS 2025) is the closest academic analogue to a linked-note
vault. Each note carries seven attributes: content, timestamp, LLM keywords, LLM
tags, an LLM contextual description, links, embedding [PAPER, full text,
[arXiv:2502.12110](https://arxiv.org/html/2502.12110v1)]. Links form by
embedding retrieval as a filter, then an LLM call to "analyze potential
connections."

Its "memory evolution" step is the part to flag: when a new memory arrives, for
each nearest neighbour the system decides whether to update its context,
keywords and tags, and the evolved memory **"then replaces the original memory
m_j in the memory set"** [PAPER]. No record of what changed, and no mention
anywhere in the paper of deletion, invalidation or supersession. The corpus is
continuously rewritten by a model with no diff — the same whole-value-overwrite
class as `create` and `str_replace`, applied automatically at every write.

---

## 2. Decision two — retrieval, and who bounds the per-turn token cost

### 2a. Who chose what

| System | Always-injected | On-demand | Who bounds cost |
|---|---|---|---|
| Claude Code | MEMORY.md index + CLAUDE.md | topic files | **the harness**, with an error |
| Anthropic memory tool | nothing | all of it | the developer, by convention |
| Anthropic context editing / compaction | n/a | n/a | **the server**, numeric trigger |
| Letta v1 | **100% of core memory blocks** | archival + recall tools | nobody automatically |
| Letta v2 | **filename tree only** | `recall` subagent | pre-commit hook + tree caps |
| LlamaIndex | short-term buffer | flushed blocks | **the framework**, token ratios |
| AutoGen v0.4 | `update_context` every turn | — | **nobody** |
| LangGraph `BaseStore` | nothing | `asearch(limit=N)` | developer |
| Google ADK | `preload_memory` tool | `load_memory` tool | developer |
| Graphiti / Zep | nothing | hybrid search | caller, by result count only |
| mem0 | nothing | `search()` | developer |
| MCP memory server | nothing | substring scan | nobody |

### 2b. The clearest bounded resident budget in the field

Claude Code's auto memory converges independently on almost exactly the shape
our own measurement document describes [DOC,
[code.claude.com/docs/en/memory](https://code.claude.com/docs/en/memory)]:

> "The first 200 lines of `MEMORY.md`, or the first 25KB, whichever comes first,
> are loaded at the start of every conversation. Content beyond that threshold is
> not loaded at session start."

> "Claude Code doesn't load topic files… at startup. Claude reads them on demand
> using its standard file tools."

Overflow escalates in three stages, and the last is honest about data loss:

> "If the file is near a limit, Claude Code reminds Claude to shorten it… If the
> file is over a limit, the write still succeeds, but Claude Code returns an
> error telling Claude to rewrite the index, because **everything past the limit
> is dropped on the next load**."

**Reading, as criticism rather than compliment:** the write succeeds and the
truncation is deferred to the next *read*. Between the over-limit write and the
next session start, the file contains content that will silently not load. The
error to the model is the only thing standing between that and invisible loss.

**Letta v1 is the opposite pole and shows the cost.** Core memory is 100%
resident: every non-hidden block's full value is written into `<memory_blocks>`
in the system prompt every turn, with `chars_current` and `chars_limit` so the
agent can see how full it is [SRC]. The limits are not the ~2,000 characters of
the MemGPT paper — `constants.py` sets `CORE_MEMORY_BLOCK_CHAR_LIMIT = 100000`,
roughly 25k tokens **per block**, with no cap on block count [SRC]. The bound is
whatever the developer sets, summed.

### 2c. Server-side context management, with numbers

Anthropic is the only vendor found that moved per-turn cost bounding into the
API with declared numeric triggers [DOC,
[context editing](https://platform.claude.com/docs/en/build-with-claude/context-editing),
[compaction](https://platform.claude.com/docs/en/build-with-claude/compaction)]:

| Strategy | Trigger default | Behaviour |
|---|---|---|
| `clear_tool_uses_20250919` | 100,000 input tokens | clears old tool results, `keep` 3 pairs |
| `clear_thinking_20251015` | model-dependent | clears prior thinking blocks |
| `compact_20260112` | 150,000 input tokens (min 50,000) | server-side summarisation |

It is applied "before the prompt reaches Claude" while "your client application
maintains the full, unmodified conversation history," and cleared results are
not silently vanished: "The API replaces each cleared result with placeholder
text indicating to Claude that it was removed," with an `applied_edits` block
reporting `cleared_tool_uses` and `cleared_input_tokens` [DOC].

**Reading:** a truncation that leaves a marker and reports a count is a
materially different object from one that does not. This is the only place in
the survey where a vendor does that as a matter of API contract.

### 2d. Retrieval that is naive in ways the README does not say

The **MCP reference `memory` server** is the most-copied data model in the
ecosystem and its retrieval is substring matching: `searchNodes` lowercases the
query and fields and calls `.includes()`, after `loadGraph()` reads the entire
JSONL from disk on every query [SRC].

**Graphiti's** is genuinely hybrid — cosine similarity, BM25 and breadth-first
graph search, fused by reciprocal rank fusion, `DEFAULT_SEARCH_LIMIT = 10`,
`DEFAULT_MIN_SCORE = 0.6` [SRC]. But **nobody in the library bounds token cost**:
a grep of `search.py`, `search_helpers.py` and `search_config.py` for
`token|budget|max_tokens|truncat` returns zero hits [SRC]. The only bound is a
result *count*, and `EntityEdge.fact` is an arbitrary-length LLM-written
sentence.

**Generative Agents** (2023) still supplies the scoring template most of the
field uses: `α_recency·recency + α_importance·importance + α_relevance·relevance`
with "all α's are set to 1"; decay factor 0.995 per sandbox hour; importance a
1–10 LLM rating; reflection triggering at accumulated importance 150, "roughly
two or three times a day" [PAPER, full text,
[arXiv:2304.03442](https://arxiv.org/html/2304.03442v2)]. **CrewAI** ships a
direct descendant: `composite = semantic_weight * similarity + recency_weight *
decay + importance_weight * importance` [DOC].

### 2e. The best-specified budget, and the one with none

**LlamaIndex** puts the split in named parameters: `token_limit` (30000),
`chat_history_token_ratio` (0.7), `token_flush_size` (3000), and long-term
blocks carry a `priority` where `priority=0` means never truncated [DOC].

**AutoGen v0.4** is the counter-example: `update_context` is called every turn
and appends retrieved memories to the model context, with a `score_threshold`
but **no token budget of any kind** [DOC]. Always-injected retrieval, unbounded
cost.

**Pydantic AI** declines the problem explicitly, which is honest rather than an
omission: "Deciding where those bytes live, which conversation they belong to,
and when to reload them is left to your application" [DOC].

### 2f. The empirical case for bounding resident context

This is the best-evidenced area in the whole survey, and unlike the memory
benchmarks it is largely non-vendor.

- **Anthropic, 2026-05-12.** *Classifier Context Rot: Monitor Performance
  Degrades with Context Length*, Martin and Roger, tested 5K–1M tokens.
  **Opus 4.6 with thinking drops from 99.7% recall at 100K tokens to 69% at
  800K** on needle injection; subtle attacks 98.6% → 88%; 2x to 30x higher miss
  rates overall [PAPER, [arXiv:2605.12366](https://arxiv.org/html/2605.12366v1)].
  This isolates one variable — benign padding — on frontier models, on a task
  they demonstrably do at short length.
- **NoLiMa** (Adobe Research, ICML 2025) removes lexical overlap between needle
  and question. **At 32K, 11 models drop below 50% of their short-context
  baseline; GPT-4o falls from 99.3% to 69.7%** [PAPER,
  [arXiv:2502.05167](https://arxiv.org/abs/2502.05167)]. This is the direct
  rebuttal to "needle-in-a-haystack is solved": standard NIAH is passable by
  literal string matching.
- **Chroma, 2025-07-14**, 18 models: a *single* distractor already reduces
  accuracy; **shuffled haystacks consistently outperform the logically coherent
  originals**; the trivial repeated-words task degrades in all 18 [2ND,
  [trychroma.com](https://www.trychroma.com/research/context-rot)]. Note Chroma
  sells a retrieval database, so the conclusion is commercially convenient; the
  methodology is public and the Anthropic paper echoes it.
- **Lost in the Middle** (TACL 2023): "performance is often highest when
  relevant information occurs at the beginning or end of the input context, and
  significantly degrades when models must access relevant information in the
  middle" [PAPER, [arXiv:2307.03172](https://arxiv.org/abs/2307.03172)].
- **Anthropic's own framing**, 2025-09-29: "as the number of tokens in the
  context window increases, the model's ability to accurately recall information
  from that context decreases"; models have an "attention budget" and "every new
  token introduced depletes this budget"; "Context, therefore, must be treated
  as a finite resource with diminishing marginal returns" [DOC,
  [Effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)].

---

## 3. Decision three — forgetting and supersession

This is the crux, and it gets the most source reading.

### 3a. Graphiti's bi-temporal model — and two corrections

Graphiti is the most-cited answer to supersession, so the implementation was
read rather than the documentation. **An earlier draft of this document got two
things wrong about it. Both corrections are stated here because the shape of the
error matters.**

**The field declarations, verbatim** [SRC, verified first-hand,
[`edges.py`](https://raw.githubusercontent.com/getzep/graphiti/main/graphiti_core/edges.py)]:

```python
class Edge(BaseModel, ABC):
    created_at: datetime

class EntityEdge(Edge):
    fact: str
    episodes: list[str]        # provenance: episode uuids
    expired_at: datetime | None  # 'datetime of when the node was invalidated'
    valid_at: datetime | None    # 'datetime of when the fact became true'
    invalid_at: datetime | None  # 'datetime of when the fact stopped being true'
```

`created_at` / `expired_at` is **transaction time**; `valid_at` / `invalid_at`
is **valid time**. A textbook bi-temporal schema, and the best-specified
supersession representation in any shipped agent-memory system.

**Correction 1 — there IS an LLM contradiction step.** An earlier draft claimed
the live path performs no semantic contradiction test, on the basis that
`prompts/invalidate_edges.py` was deleted on 2026-01-31 (commit `c36723c7`, PR
#1191, −64 lines plus 200 lines of tests, stated reason "not used in
add_episode flow") [SRC]. **That deletion is real and the conclusion drawn from
it was wrong.** The prompt was *folded into* `prompts/dedupe_edges.py::resolve_edge`,
which returns [SRC, verified first-hand]:

```python
class EdgeDuplicate(BaseModel):
    duplicate_facts: list[int]
    contradicted_facts: list[int]
```

with prompt text "2. CONTRADICTION DETECTION: Determine which facts the NEW FACT
contradicts from either list" [SRC].

**So the actual mechanism is two-stage: an LLM decides *whether* two facts
contradict; a deterministic date rule decides *which one expires*.** The LLM is
shown two lists — `<EXISTING FACTS>` and `<FACT INVALIDATION CANDIDATES>` — as
**bare fact strings with an index, with no indication of which entities each
fact connects** [SRC]. The date rule then filters its verdict
[SRC, `edge_operations.py::resolve_edge_contradictions`]:

```python
        elif (edge_valid_at_utc is not None
              and resolved_edge_valid_at_utc is not None
              and edge_valid_at_utc < resolved_edge_valid_at_utc):
            edge.invalid_at = resolved_edge.valid_at
            edge.expired_at = edge.expired_at if edge.expired_at is not None else utc_now()
```

Three consequences verified in code, none documented:

1. **If either `valid_at` is `None`, nothing is invalidated at all.** Both
   branches require non-`None` on both sides, and the extraction prompt says
   "Leave both fields `null` if no explicit or resolvable time is stated" [SRC].
2. **A fact can be born retired.** If a candidate has a later `valid_at`, the
   *new* edge is written already expired, with the comment "Expire new edge since
   we have information about more recent events" [SRC].
3. **Nothing is deleted.** `entity_edges = resolved_edges + invalidated_edges`
   and both lists go to the save path [SRC].

**Correction 2 — the candidate pool is unscoped, and this is a measured
production bug.** The duplicate search is scoped with
`SearchFilters(edge_uuids=[...])`; the invalidation-candidate search passes a
bare `SearchFilters()`, so **any semantically similar edge in the whole
`group_id` is eligible to be retired** [SRC]. The purpose-built helper
`get_edge_invalidation_candidates`, which restricted candidates to edges sharing
an endpoint, still exists but is reachable only from tests [SRC].

The measured effect, from two independent third-party reports on different
database backends [2ND,
[graphiti#1728](https://github.com/getzep/graphiti/issues/1728), opened
2026-08-04, **still open with no maintainer reply**]:

- FalkorDB, ~**3,950 facts, 1,616 (41%) carrying an `invalid_at`**; a hand-audit
  of four found **three were collateral, not genuine change**.
- Neo4j, different reporter, 109 episodes over 6 days: **887 facts, 143 (16.1%)
  invalidated**, concentrated at 49.2% in one `group_id`, arriving in
  microsecond-identical batches — three `TESTED_WITH` facts about three
  *different* models retired together.

The reporter's summary of why this is dangerous: "The retired fact stays in the
graph but is stamped with an `invalid_at` date, so searches downrank or drop it
and the knowledge is effectively gone — silently, with no signal that anything
was lost."

**And the LLM judge itself has been measured.** With `gpt-4.1-nano` as
`small_model` (chosen for cost), a graded eval of 5 cases × 3 replicates scored
the stock `EdgeDuplicate` schema **7/15**, and **1/3** on the clear two-fact
contradiction; adding a `reasoning: str` field before the arrays scored 14/15
[2ND, [graphiti#1666](https://github.com/getzep/graphiti/issues/1666)]. A later
comment reports **0/30** on contradictions with a different small model.

**Do invalidated edges stop being retrieved? No.** `SearchFilters` exposes
`valid_at`, `invalid_at`, `created_at` and `expired_at`, **all defaulting to
`None`** [SRC, verified first-hand]; across 2,048 lines of `search_utils.py`,
`expired_at` and `invalid_at` appear **only in RETURN projections, never in a
predicate** [SRC]. And the context formatter hands them to the model with an
explanation [SRC, verified first-hand, `search_helpers.py`]:

> "These are the most relevant facts and their valid and invalid dates. Facts are
> considered valid between their valid_at and invalid_at dates. Facts with an
> invalid_at date of "Present" are considered valid."

**This is the whole supersession contract.** Graphiti gives an auditable,
queryable temporal record — genuinely the best here — and then delegates the
behavioural decision to the model reading two timestamps in a JSON blob. There
is no mechanical suppression at any point. §3i shows what that costs.

**One more gap worth naming:** at the MCP surface, `search_memory_facts` exposes
only `valid_at_after/before` and `invalid_at_after/before`. **`created_at` and
`expired_at` are not exposed** [SRC], so an agent can ask "what was true in the
world during window W" but **cannot** ask "what did the system believe on date
D." The transaction axis is stored and not queryable.

### 3b. mem0: it stopped trying

The two-phase ADD/UPDATE/DELETE/NOOP reconciliation that mem0's paper and every
blog post describe **was removed on 2026-04-14 (v2.0.0)**. The changelog:
"Replaced 2-LLM-call pipeline with additive extraction using
`ADDITIVE_EXTRACTION_PROMPT`. Memories accumulate via `linked_memory_ids`: no
more UPDATE/DELETE events" [DOC,
[docs.mem0.ai/changelog/sdk](https://docs.mem0.ai/changelog/sdk)].

Verified first-hand in `mem0/memory/main.py`: the add path is a single
`llm.generate_response` call, with `# Phase 1: Existing memory retrieval` feeding
`# Phase 2: LLM extraction (single call)` using `ADDITIVE_EXTRACTION_PROMPT`
[SRC]. `DEFAULT_UPDATE_MEMORY_PROMPT` and `get_update_memory_messages` remain in
`configs/prompts.py` and are **imported by nothing** [SRC]. The new prompt opens
"Your sole operation is ADD," and adds: "When in doubt, extract. A slightly
redundant memory is far less costly than a missing one."

**mem0 states the consequence itself**, in its migration guide:

> "The ADD-only model means memories accumulate over time. When information
> changes, the new fact is stored alongside the old one. Retrieval handles
> ranking: the most relevant, current information surfaces first."

**Reading: that claim does not hold, and the code shows why.** The OSS ranker is
`combined = (semantic + bm25 + entity_boost) / max_possible` — **there is no
recency, temporal, or age term of any kind** [SRC]. Decay and temporal ranking
are platform-only; the OSS SDK hardcodes "The decay parameter is not supported by
the OSS Memory SDK" [SRC]. So "the most current information surfaces first" is a
property the shipped ranking function cannot produce. Independently reproduced in
[#4956](https://github.com/mem0ai/mem0/issues/4956) (open), where stale
employment facts outrank current ones.

**The contradiction signal is generated and then discarded.** The prompt
instructs the LLM to emit `linked_memory_ids` with an explicit
**"Contradiction: New information that conflicts with an existing memory"** link
type — and the persistence loop copies `data`, `text_lemmatized`, `hash`,
`created_at`, `updated_at`, `attributed_to`, and **not** `linked_memory_ids`
[SRC]. The memory-to-memory contradiction link is never written.

**Deduplication is `hashlib.md5` exact-string matching** against the top-10
nearest existing memories [SRC]. A maintainer confirmed the mechanism: "over a
growing store, recall quality drops, the 10-slot window covers an ever-smaller
fraction of stored facts, and the hash-only fallback catches zero paraphrases"
[2ND, [#5850](https://github.com/mem0ai/mem0/issues/5850)].

**The history table exists and is nearly useless as a supersession record.** It
holds `old_memory`, `new_memory`, `event`, `is_deleted`, `actor_id`, `role`
[SRC] — but `history(memory_id)` is a point lookup requiring an ID you already
have, it is never consulted during search, and `_delete_memory` hard-deletes
from the vector store, so a deleted memory's ID is unfindable by search and its
history unreachable [SRC, verified first-hand].

**The consequence, verified first-hand.** The input to the write decision is
`self.vector_store.search(..., top_k=10, ...)` [SRC]. Deleted memories are gone
from the vector store, so they are **structurally invisible to the decision that
would prevent re-adding them**. **mem0 cannot durably reject.** A memory a user
deleted will be re-extracted the next time the subject arises.

Maintainer position, on the record: "Both memories being stored is
intentional… Closing as the behavior is by design in v3" [2ND,
[#4896](https://github.com/mem0ai/mem0/issues/4896), closed one day after
opening]; and on a later report, "a design/feature gap rather than a bug…
Routing this as a product decision" [2ND,
[#5867](https://github.com/mem0ai/mem0/issues/5867), open].

**Note the shared blind spot:** both mem0 (`top_k=10`) and Graphiti (hybrid
search candidates) gate supersession on **embedding proximity**. A new fact that
genuinely contradicts an old one but does not land near it is never considered
by either. Neither documents this.

### 3c. Cognee: the most machinery, all of it off by default

Cognee has the richest supersession surface and ships it disabled.
`get_default_tasks` is exactly `classify_documents` → `extract_chunks_from_documents`
→ `extract_graph_and_summarize` → `add_data_points`; **contradiction detection,
the provenance ledger and temporal supersession are all spliced in conditionally
and all three default to OFF** [SRC].

- **`resolve_temporal_contradictions`** is fully deterministic, no LLM: ranks by
  `updated_at`, latest wins, tags losers with `superseded=True`, `superseded_by`,
  `supersession_reason` [SRC]. Its docstring is unusually honest about its own
  limits: "Most cognee relationships (`knows`, `mentions`, …) are legitimately
  many-valued and must never be collapsed, and there is no cardinality metadata
  to tell them apart." The caller must declare
  `functional_relationships={"ceo_of"}` explicitly or it is a no-op.
- **`close_node`** stamps `valid_to` and provides `is_valid(node, at_ms)` — and
  **is called by no pipeline task**; it appears in four files, one of which is a
  test [SRC]. A manual developer API.
- **`detect_contradictions`** writes a first-class **`contradicts` edge** between
  conflicting facts, carrying `reason` (LLM prose) and `confidence` (0–1,
  thresholded). Its docstring: "Each contradiction is surfaced twice instead of
  silently coexisting" [SRC]. Nothing is deleted or rewritten.

**Reading:** the `contradicts` edge is genuine negative *structure* — a
queryable "these two cannot both hold" that Graphiti has no equivalent of. But
`superseded` appears in **zero files under `cognee/modules/retrieval/`** [SRC],
so like Graphiti, Cognee returns superseded facts unless you filter them — and
unlike Graphiti it does not even tell the reader model the flag exists.

### 3d. Letta: destroy in place, and no archival delete at all

`core_memory_replace(label, old_content, new_content)` is a literal Python
`str.replace()` on the block value, with the docstring "To delete memories, use
an empty string for new_content." **The old text is destroyed in place. No
tombstone, no version, no history** [SRC].

**Archival memory has no deletion tool at all** — zero delete definitions across
every function set — and the insert docstring says "Archival memory is
permanent… persists indefinitely" [SRC]. The `Passage` schema has an
`is_deleted` tombstone the agent cannot reach.

**A live data-loss bug sits on the overflow path.**
[letta#3270](https://github.com/letta-ai/letta/issues/3270), opened 2026-04-01
by a Letta employee and **still open**: configured `sliding_window_percentage:
0.15` (retain 85%), actual result "only the summary message remained," also at
0.25 and 0.50 [2ND]. And the overflow mechanism puts retention on the model's
judgement — the warning string tells the agent to save what matters and "Do NOT
tell the user about this system alert" [SRC].

**Context for anyone citing Letta:** `letta-ai/letta` **archived its entire
Python server on 2026-08-16** (commit #3430, "chore: archive the legacy server
repository"); main now contains only a README, and current source is
`letta-ai/letta-code` in TypeScript with the git-backed markdown design of §1c
[SRC]. "Letta memory" means two different architectures depending on the month.

### 3e. The one framework with a native consolidation step

Google's ADK is the only framework here with a shipped mechanism for reconciling
a new memory against existing ones. With `enable_consolidation: True`,
`VertexAiMemoryBankService` uses the `memories.generate` API to "consolidate the
new memory items with existing related memories, preventing redundancy and
building a more coherent knowledge base" [DOC,
[adk.dev/sessions/memory](https://adk.dev/sessions/memory/)].

**Reading:** redundancy control, not supersession. Opt-in, proprietary to
Vertex, LLM-adjudicated. Nothing says the replaced content is retained or the
reason recorded, and it is not a rejection mechanism.

### 3f. Negative knowledge — absent from every agent system, present in two human conventions

I looked for a mechanism meaning "this was considered and rejected; do not
propose it again," and checked the page where it would live in each system:
LangGraph's stores page, OpenAI's sessions page, the MCP memory README and
`index.ts`, Anthropic's memory-tool command list, Claude Code's memory page,
Letta's memory prompts. **Absent from all of them.**

In the Claude Code case the docs affirmatively hand it back [DOC]:

> "if two rules contradict each other, Claude may pick one arbitrarily. Review
> your CLAUDE.md files… periodically to remove outdated or conflicting
> instructions."

**The distinction that matters, and that no agent system represents:**

- *"Alice worked at Acme until 2024"* — an **expired fact**. Was true, stopped
  being true.
- *"We evaluated Acme and ruled it out"* — a **rejected proposal**. Never true,
  and the rejection itself is the durable knowledge.

In Graphiti the second can only exist as English inside a `fact` string,
invisible to every mechanism and eligible to be silently retired by an unrelated
episode that mentions the same entity [SRC].

**The systems that do model rejection are human governance conventions.**

**Architecture Decision Records.** Nygard's original post states the principle
[DOC, [cognitect.com, 2011-11-15](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions)]:

> "If a decision is reversed, we will keep the old one around, but mark it as
> superseded… It's still relevant to know that it **was** the decision, but is
> **no longer** the decision."

The MADR 4.0.0 template (released 2024-09-17) has a status vocabulary of
`{proposed | rejected | accepted | deprecated | … | superseded by ADR-0123}`, a
mandatory **"Considered Options"** section listing every alternative evaluated,
and **"Pros and Cons of the Options"** recording "Good, because…", "Neutral,
because…", "Bad, because…" for each [DOC,
[adr.github.io/madr](https://adr.github.io/madr/)]. That is a complete answer to
both halves — supersession with the reason, *and* the options ruled out with
why. **And it has essentially no tooling**: MADR's own site notes "There is
currently no tooling supporting MADR 3.0.0." Nothing validates the vocabulary,
nothing checks that a `superseded by` pointer resolves, and no agent-memory
system examined reads this format.

**PEP 1** states the principle even more directly [DOC,
[peps.python.org/pep-0001](https://peps.python.org/pep-0001/)]:

> **Rejected** — "Perhaps after all is said and done it was not a good idea. It
> is still important to have a record of this fact."

It pairs bidirectional `Superseded-By` and `Replaces` headers and requires that
a rejection carry a `Resolution` header linking to the decision. A 25-year-old
human-enforced process that satisfies requirements no system in this survey
meets.

**And one engineering pattern does both halves.** Martin Fowler's **Retroactive
Event** [DOC, [martinfowler.com, 2005-12-12](https://martinfowler.com/eaaDev/RetroactiveEvent.html)]:
"we reverse the event and mark it as rejected… Events marked as rejected are
ignored by all further processing, so they aren't re-processed during replay or
reversed in future rewinds. **They stay in the event log to maintain history.**"
Fowler's own assessment of its adoption: "I haven't seen Retroactive Event very
often and there's a good reason for that" — it requires Event Sourcing plus
reversibility as prerequisites.

**Kafka log compaction** supplies the operational lesson: a tombstone is a
message with a null payload, and `delete.retention.ms` (default 24h) bounds how
long it survives. A consumer that lags past that window never sees the delete and
reconstructs stale state [DOC,
[Confluent](https://docs.confluent.io/kafka/design/log_compaction.html)]. **The
deletion marker is itself data with a retention requirement.**

**Datomic** is the closest commercial system to "supersession with the reason
preserved": datoms are immutable, `:db/retract` is a first-class fact, and every
transaction is *its own entity* so you can attach "the purpose of the
transaction, the application that executed it, the provenance of the data it
added, or the user who caused it to execute" [DOC, docs.datomic.com]. It gives
(a) cleanly and nothing for (b) — nothing stops a later transaction re-asserting
a retracted datom, and nothing consults retraction history at write time.

### 3g. What the formal literature already knew, and what it measured

**AGM belief revision (1985)** defines expansion (add, remove nothing),
contraction (remove *p*, removing as little else as possible), and revision, tied
together by the Levi identity *K* ∗ *p* = (*K* ÷ ¬*p*) + *p* [DOC,
[SEP, Logic of Belief Revision](https://plato.stanford.edu/entries/logic-belief-revision/)].
**Contraction is the hard one, and the reason is structural:** removing one
belief *underdetermines what else must go*. Many subsets of *K* fail to entail
*p*, and logic alone cannot choose. Partial meet contraction pushes the choice
into a selection function γ; **epistemic entrenchment** (Gärdenfors & Makinson,
TARK '88) grounds γ by ranking beliefs by explanatory value. The Recovery
postulate, *K* ⊆ (*K* ÷ *p*) + *p*, is "the most debated postulate of belief
change."

**Reading:** AGM is the formal statement of why this problem has no purely
mechanical answer. Deciding what a superseded decision drags down is a *choice*,
and the theory's own conclusion is that the choice must come from outside the
logic.

**Truth maintenance solved the other half.** Doyle's TMS (1979, *Artificial
Intelligence* 12(3):231–272) records the **justification** for every belief, not
just the belief; nodes are IN or OUT derived from justifications, and
withdrawing an assumption propagates automatically [PAPER, citation verified via
[dblp](https://dblp.org/rec/journals/ai/Doyle79.html); **the original text could
not be retrieved** — every access route returned 403/405/empty, so the mechanism
description is reconstructed from secondary sources]. de Kleer's **ATMS** (1986)
adds the **nogood database**, which "prevents re-exploration by immediately
recognizing when a problem solver encounters an assumption set matching a
recorded contradiction" [PAPER,
[TU Wien ATMS chapter](https://www.dbai.tuwien.ac.at/staff/wotawa/atmschapter1.pdf)].
The lineage is older: Sussman, on the 1975 original, describes extracting "the
set of base assumptions that the contradiction depended upon" and making the
code "remember not to reassume any such 'nogood' set again" [DOC,
[Northeastern history page](https://www.khoury.northeastern.edu/home/lieber/clause-learning/non-chrono-backtrack.html)].

**Why it did not survive, and where it went.** Complexity is documented — the
Clause Maintenance System's computation task is Σ<sup>p</sup><sub>2</sub>-complete,
and ATMS labels grow exponentially with consistent environments [PAPER, TU Wien
chapter]. And the substrate does not fit: **a TMS needs the reasoner to emit
explicit, discrete, inspectable inference steps, which is exactly what a
transformer does not expose.** But the idea did survive in disguise — nogood
learning is the direct ancestor of **conflict-driven clause learning** in modern
SAT solvers. "Never re-propose the rejected thing" got absorbed into constraint
solving and left knowledge representation behind.

**Non-monotonic reasoning supplies the vocabulary for the negative-knowledge
question** [DOC, [SEP, Non-monotonic Logic](https://plato.stanford.edu/entries/logic-nonmonotonic/)]:
*not known to be true* means the proposition is absent; *known to be false*
means ¬P is stored or derived. **The closed-world assumption collapses these** —
if P ∉ KB, infer ¬P.

**Reading:** an agent memory that stores only adopted decisions runs an implicit
CWA on decisions: absence reads as "never considered." A rejected option is
precisely the case where that inference is backwards, because rejection means it
*was* considered and lost.

**Why knowledge bases avoid negatives, and why the argument does not transfer.**
OWL 2 has `owl:NegativePropertyAssertion`, but its RDF encoding is a reified
blank node — **four triples for one negative fact versus one for the positive**
[DOC, [W3C OWL 2 Primer](https://www.w3.org/TR/owl2-primer/#Negative_Property_Assertions)].
The research line is Arnaout, Razniewski, Weikum and Pan: "All popular KBs
capture virtually only positive statements… Due to the abundance of such invalid
statements, any effort to compile them needs to address ranking by saliency"
[PAPER, [arXiv:2001.04425](https://arxiv.org/abs/2001.04425), *Journal of Web
Semantics* 71 (2021)]. The set of true negatives about any entity is effectively
unbounded and almost all of it is worthless, so a salience criterion is required
and no general one exists.

**Reading, and this is the load-bearing one:** a rejected *decision* is not an
arbitrary negative drawn from an infinite space. It is a **bounded, enumerated**
negative — someone proposed it, someone rejected it, and the proposal *is* the
salience criterion. The reason knowledge bases cannot store negatives does not
apply to decision records.

### 3h. The 2026 systems that get one half

| System | (a) supersession + reason? | (b) blocks re-proposal? |
|---|---|---|
| **LatticeMind** [PAPER, [arXiv:2608.08236](https://arxiv.org/abs/2608.08236)] | **Yes.** Status `{Proposed, Confirmed, Contested, Superseded}`; losers "preserved as CONTESTED or SUPERSEDED **with full provenance**," evidence and timestamps retained | **No.** Superseded is terminal, but nothing checks a *fresh* item against superseded content. 0.97 on ConflictBank vs 0.63 single-agent |
| **TEPA** [PAPER, [arXiv:2608.07429](https://arxiv.org/abs/2608.07429)] | **No.** States `{Hypothesis, Active, Revoked}`; counts contradictions (Beta-Bernoulli, revoke at θ=0.3), stores **no rationale** | **Partially.** Revoked precedents leave ordinary retrieval "while keeping it available for audit" — but revocation is reversible |
| **SSGM** [PAPER, [arXiv:2603.11768](https://arxiv.org/abs/2603.11768)] | **No.** Rejects conflicting writes; records no rationale | The **one paper found that cites TMS by name** — a write-time consistency gate (reject if ΔM ∧ M_core ⊧ ⊥), not a justification network |
| **MemClaw** [PAPER, [arXiv:2606.24535](https://arxiv.org/html/2606.24535v1)] | **Partial.** `supersedes_id`, old row retained marked inactive; no reason field | Detection is structural (single-valued RDF predicates), async ~6s post-commit |
| **Zep / Graphiti** | **Half.** Window closed not deleted, history auditable — but source episode and ingestion time only, **not why** | **No.** 7.0% on FactConsolidation |
| **mem0** | **No.** ADD-only; contradiction link generated and discarded | **No** |

**MemClaw is worth its own paragraph** because it is the only paper here with a
production implementation and honest self-criticism. Its four named failure
modes — unauthorized leakage ("An agent retrieves memory outside its authorized
scope"), stale propagation, **contradiction persistence ("Conflicting memories
coexist without resolution")**, and provenance collapse ("Retrieved memories
cannot be traced to their origin") — are a good vocabulary for a shared vault
[PAPER]. Reported: contradiction detection 1.000 (90/90); provenance chain
completeness 1.000 over 50 chains at depth four. Reported *against itself*: a
**search leak rate of 0.439**, self-evaluation bias, no baseline comparison, only
200 trials — and one instructive bug class, **"Synchronous dedup gate pre-empts
asynchronous detector for near-identical contradictory writes"**: the dedup step
swallows exactly the contradictions the detector exists to catch.

### 3i. The measurements — how badly this actually goes

**MemoryAgentBench, FactConsolidation** [PAPER, full text,
[arXiv:2507.05257](https://arxiv.org/abs/2507.05257)]. Its fourth competency is
"Selective Forgetting," defined as "The skill to revise, overwrite, or remove
previously stored information when faced with contradictory evidence." Facts are
explicitly serial-numbered and agents are *told* higher serials are newer.

| System | single-hop | multi-hop |
|---|---|---|
| GPT-5-mini (400K ctx) | 78.0% | 28.0% |
| HippoRAG-v2 | 54.0% | 5.0% |
| **BM25** | **48.0%** | 3.0% |
| Cognee | 28.0% | 3.0% |
| MemGPT | 28.0% | 3.0% |
| **Mem0** | **18.0%** | 2.0% |
| **Zep / Graphiti** | **7.0%** | 3.0% |

"All existing methods fail on the multi-hop situation (with achieving at most
28% accuracy)." A deterministic `max(serial)` in Python scores **82%** and beats
every system by 20–33 points [PAPER,
[arXiv:2606.01435](https://arxiv.org/abs/2606.01435)], which names the failure
**prior suppression**: told "newer overrides older," the model still answers
"ice hockey" for Finland's national sport rather than the higher-serial
counterfactual.

**STALE**, testing *implicit* conflict where later observations invalidate
earlier memories without explicit negation [PAPER, full text,
[arXiv:2605.06527](https://arxiv.org/html/2605.06527v1), 2026-05-07]:
Gemini-3.1-pro 55.2%; **Zep, LiCoMemory, A-mem and Mem0 all below 10%**. The
sharpest sub-number: Gemini-3.1-pro scores 92.0% on the co-referential subtype
and **30.0% on the propagated subtype**. Its headline: "models often retrieve
updated evidence but fail to act on it in downstream behavior."

**BEAM** [PAPER, [arXiv:2510.27246](https://arxiv.org/abs/2510.27246), ICLR
2026; 100 conversations, 2,000 questions, up to 10M tokens]. Best score by any
evaluated method:

| Ability | 100K | 500K | 1M | 10M |
|---|---|---|---|---|
| **Contradiction resolution** | **0.037** | **0.042** | **0.050** | **0.025** |
| Knowledge update | 0.450 | 0.278 | 0.414 | 0.325 |

> "all methods—including ours—perform strongest in abstention and weakest in
> contradiction resolution, indicating that contradiction detection remains a
> challenging open problem."

**The caveat that matters:** BEAM evaluated long-context LLMs, a RAG baseline,
and the paper's own LIGHT framework. **mem0, Zep and Letta were not evaluated**
[PAPER]. A secondary 2026 paper describes these as abilities where "*all*
systems score worst" [PAPER,
[arXiv:2604.11364](https://arxiv.org/html/2604.11364v2)]; that generalises
further than BEAM's scope supports.

**Knowledge editing does not offer an escape.** Sequential weight edits collapse
the model: ROME on GPT-J declines catastrophically "as early as 100 edits";
MEMIT on GPT-J at ~1400; MEMIT drops LLaMA-8B from 51.9 to **0.0** on TriviaQA
and 77.9 to **0.0** on GSM8K after 50 edits, producing "unreadable or nonsensical
outputs" [PAPER, [arXiv:2401.07453](https://arxiv.org/abs/2401.07453);
[arXiv:2506.03490](https://arxiv.org/abs/2506.03490)]. Edits also fail to
propagate: on multi-hop questions ROME falls **88.3% → 7.4%** and MEMIT
**96.2% → 7.0%**, against **40.5%** for the *unedited* model — editing a fact
makes reasoning about it worse than not editing it [PAPER,
[arXiv:2305.14795](https://arxiv.org/abs/2305.14795)]. And **in-context editing
— just putting the new fact in the prompt — beats every weight-editing method by
20–40 points** on ripple-effect evaluation [PAPER,
[arXiv:2307.12976](https://arxiv.org/abs/2307.12976), TACL 2024].

**Machine unlearning is suppression, not removal.** Fine-tuning on accessible
facts recovers 88% of pre-unlearning accuracy [PAPER,
[arXiv:2410.08827](https://arxiv.org/abs/2410.08827)]; **4-bit quantization
raises retained "forgotten" knowledge from 21% to 83%** [PAPER,
[arXiv:2410.16454](https://arxiv.org/abs/2410.16454), ICLR 2025]; ten unrelated
fine-tuning examples recover most hazardous capabilities [PAPER,
[arXiv:2409.18025](https://arxiv.org/abs/2409.18025), TMLR]; and the mechanistic
account is that low-rank updates "do not overwrite existing knowledge but
instead redistribute it," acting as "targeted suppression mechanisms," with
extraction rates above 85% under white-box elicitation [PAPER,
[arXiv:2606.23276](https://arxiv.org/abs/2606.23276)].

**Reading:** if a superseded decision must stay superseded, it has to live in an
explicit, inspectable store. Parameters will not hold it.

### 3j. Forgetting by decay, and the argument against it

CrewAI's recency term and Generative Agents' 0.995 hourly factor both forget by
decay. The *Missing Knowledge Layer* paper's criticism is that decay is applied
to the wrong category [PAPER, full text,
[arXiv:2604.11364](https://arxiv.org/html/2604.11364v2)]: "Facts do not expire;
they get *superseded* by newer evidence, which is a qualitatively different
operation from forgetting." It names systems — NornicDB's 7/69/693-day
half-lives get "a paper's findings do not become less true after 69 days," and
mem0 "applies identical CRUD operations to facts and experiences."

**Reading:** decay is a retrieval-ranking device used as a correctness device.
It makes a wrong memory less likely to surface without making it not-wrong, and
makes a durably-true memory less likely to surface for no reason.

**Dark, and worth stating:** that paper argues supersession is the central
operation and cites **neither truth maintenance nor AGM belief revision**. It
cites Graphiti's bi-temporal model instead. The same is true of *Always-On
Agents* (435 works) and of LatticeMind and TEPA. The modern field is
re-deriving a solved half without knowing it was solved.

---

## 4. Decision four — growth over time

### 4a. The honest headline: the field has almost no longitudinal data

The longest production observations found anywhere:

| Duration | Store size | Source |
|---|---|---|
| **32 days** | 10,134 entries | [mem0#4573](https://github.com/mem0ai/mem0/issues/4573) |
| ~5 weeks | 1,035 memories | same thread, different reporter |
| ~1 week | 6,592 LanceDB versions | [cognee#4684](https://github.com/topoteretes/cognee/issues/4684) |
| 6 days | 269 memories | same mem0 thread |
| unstated | ~3,950 facts, 41% invalidated | [graphiti#1728](https://github.com/getzep/graphiti/issues/1728) |

**There is no public data on multi-year agent-memory growth, and essentially
none past about five weeks.** No vendor and no academic group has published a
curve of retrieval precision or latency against store size. This is corroborated
from inside the literature: of 435 coded works, "none in the corpus reports
recovery success or cost after corruption" [PAPER, arXiv:2606.30306].

### 4b. The one substantial production audit

[mem0#4573](https://github.com/mem0ai/mem0/issues/4573), "What we found after
auditing 10,134 mem0 entries: 97.8% were junk," opened 2026-03-27. One agent,
one human, Qdrant, **32 days**. Extraction: gemma2:2b days 1–20, Claude Sonnet
4.6 days 21–32.

- Phase 0 removed 2,468 (hash duplicates, hallucinated clusters, **668 copies of
  a single hallucination**); Phase 1 cosine dedup flagged 2,943 more (**37.6%**);
  Phase 2 read the remaining 6,264 by hand: 96.9% still junk.
- **224 of 10,134 survived. 186 needed complete rewriting. 38 were kept as-is.**
- Junk taxonomy: system-prompt restating ~3,200 (52.7%), heartbeat noise ~700,
  architecture dumps ~500, transient task state ~450, hallucinated user profiles
  ~315, identity confusion ~200, **security/privacy leaks ~130**.

**The two findings that matter most:**

1. **A better model made it worse.** Per-batch junk rates 97.7 / 98.1 / 98.5 /
   97.4 / 95.4 / 97.6 (gemma2:2b) → 89.6% (Sonnet). The author: "A better model
   follows the extraction prompt more faithfully, which means it extracts more
   indiscriminately."
2. **The recall→re-extraction feedback loop.** One hallucinated "prefers Vim"
   preference reached **808 stored entries**, because retrieved memories were fed
   back into extraction indistinguishably from new content.

**Caveats, stated:** the reporting account was one month old, the body has an
internal inconsistency (668 vs 808), and the raw store was never published. A
maintainer engaged substantively and called it "one of the most detailed
real-world write-ups on memory system behavior in production we've seen" —
**70 days later**.

### 4c. Degradation across scale, self-reported

| System | 100K | 1M | 10M | Source |
|---|---|---|---|---|
| Exabase M-1 | 76.9 | 75.0 | 68.0 | [2ND, [exabase.io, 2026-08-07](https://exabase.io/blog/exabase-m1-achieves-state-of-the-art-on-beam-benchmark)] |
| Hindsight | 73.4 | 73.9 | 64.1 | [2ND, [vectorize.io, 2026-04-02](https://hindsight.vectorize.io/blog/2026/04/02/beam-sota)] |
| mem0 | — | 64.1 | 48.6 | [2ND, [mem0.ai, 2026-04-01](https://mem0.ai/blog/state-of-ai-agent-memory-2026)] |
| Honcho | 63.0 | 63.1 | 40.6 | [2ND] |
| LIGHT baseline | — | — | 26.6 | [PAPER, arXiv:2510.27246] |

mem0 states the implication about its own numbers, to their credit: "The BEAM 1M
to BEAM 10M drop (64.1 -> 48.6) is a ~25% performance loss as context scales
10x" [2ND].

A parallel measurement on *context* length rather than store size, from
**HaluMem** — noting that it is authored by MemTensor, who make MemOS, and MemOS
wins every table, so it is not neutral; all systems were run identically, so the
degradation curve is still informative [PAPER,
[arXiv:2511.03506](https://arxiv.org/abs/2511.03506)]:

| System | Extraction recall, Medium → Long | Update accuracy, Medium → Long |
|---|---|---|
| Mem0 | 42.91% → **3.23%** | 25.50% → **1.45%** |
| Zep | — | 47.28% → 37.35% |
| MemOS (authors' own) | 74.07% → 81.90% | 62.11% → 65.25% |

### 4d. The failure modes that show up in maintenance

**Graphiti removed its own scaling fix.** PR #859 added HNSW vector indexing on
2025-08-25; PR #894 removed it **eleven days later**, 2025-09-05. Measured
consequence on 48,647 entity nodes / 76,264 edges at 2560 dims: one
`edge_similarity_search` takes **~41 seconds**, versus **~82 ms** for the
identical top-20 through a manually created Neo4j HNSW index — **~500x**. On
current main, `db.index.vector` has zero hits [2ND,
[graphiti#1793](https://github.com/getzep/graphiti/issues/1793), open].

**Silent write loss.** `add_memory` returns 200 "queued for processing,"
extraction exceeds `max_tokens`, four retries, episode discarded, nothing
queryable tells the caller: "I only noticed because I happened to verify the
graph contents by hand rather than trusting the success response" [2ND,
[graphiti#1707](https://github.com/getzep/graphiti/issues/1707), open].

**Entity duplication with no published operating point.** 13,975 entity nodes
containing **four separate nodes named exactly "DGIA"**; pairwise cosine between
their name embeddings 0.5246–0.6270, so 5 of 6 pairs fall below
`NODE_DEDUP_COSINE_MIN_SCORE = 0.6` and the deterministic exact-name matcher
never sees them [2ND, [graphiti#1734](https://github.com/getzep/graphiti/issues/1734);
constant verified [SRC]]. A separate review notes that a false merge "is neither
auditable nor reversible from the graph" — `add_episode` unpacks
`resolve_extracted_nodes` as `nodes, uuid_map, _`, discarding the merge record
[2ND, [graphiti#1771](https://github.com/getzep/graphiti/issues/1771), **zero
comments**; the reviewers disclose they are a competing vendor and apply the same
rubric to themselves, failing it].

**Cognee: one week of writes, 6,592 uncompacted versions.** LanceDB
`merge_insert` creates a new table version and fragment per upsert and nothing in
normal operation calls `optimize()`: 6,592 version files → 48, 6,557 fragments →
27, **937 MB → 411 MB** after manual compaction [2ND,
[cognee#4684](https://github.com/topoteretes/cognee/issues/4684), open].

**mem0's ADD-only architecture created its own bug class.** Duplicates from a
hardcoded top-10 dedup window — "8 near-identical memories about a single fact in
~1 hour" [2ND, [#7123](https://github.com/mem0ai/mem0/issues/7123)]; entity-link
decay of **91%** at 100 stale links, silent [2ND,
[#7226](https://github.com/mem0ai/mem0/issues/7226)]; a TOCTOU race where two
concurrent `add()` calls both pass hash dedup [2ND,
[#6531](https://github.com/mem0ai/mem0/issues/6531)]; batch-embedding partial
failure that logs a WARNING and drops the memory with no exception to the caller
[2ND, [#5245](https://github.com/mem0ai/mem0/issues/5245)]; and FIFO
message-buffer eviction leaking a *previous session's* content into the current
extraction prompt [2ND, [#7195](https://github.com/mem0ai/mem0/issues/7195)].
mem0 also **has no expiration or decay mechanism**, raised and unanswered [2ND,
[#5330](https://github.com/mem0ai/mem0/issues/5330)].

**The MCP reference `memory` server** is a useful natural experiment: a data
model published in 2024, widely copied, still being brought to basic correctness.
Six fixes landed on **2026-09-03** [SRC], including
`fix(memory): stop reporting deletions that did not happen (#4738)` and
`fix(memory): serialize graph mutations to prevent concurrent write race (#4555)`.
"Reporting deletions that did not happen" is a trust bug in a memory system — the
caller believes something was forgotten and it was not. Its accumulation
semantics are the default many people inherit: `addObservations` deduplicates
only by **exact string equality within one entity** [SRC], so "The user prefers
Python" and "The user now prefers Go" coexist indefinitely.

### 4e. Growth as an extraction bill

Graphiti's non-bulk `add_episode` makes 1 `extract_nodes` + 1 batched
`dedupe_nodes` + **one `extract_attributes` call per entity node** + 1
`extract_edges` + **per edge** 1 `resolve_edge`, plus a timestamp call when
`valid_at`/`invalid_at` came back null [SRC]. **The per-episode LLM cost is
O(entities + facts), not constant.**

Published figures are thin: mem0's own paper gives ~7,000 tokens per
conversation for vector mem0 and ~14,000 for the graph variant [PAPER,
arXiv:2504.19413]; a controlled third-party study put mem0 at **50x the cost** of
cloud RAG for statistically indistinguishable accuracy [PAPER, arXiv:2606.29914];
and an academic graph-RAG comparison measured LightRAG consuming **over 757M
tokens on HotpotQA versus 62M for naive RAG** [PAPER,
[arXiv:2503.04338](https://arxiv.org/pdf/2503.04338)].

**Dark:** cost per stored memory is unpublished for every extraction system here.
mem0's own instrumentation issue — "include token usage in responses" — has been
**open since 2025-05-28** [2ND,
[#2820](https://github.com/mem0ai/mem0/issues/2820)], so users cannot measure it.

---

## 5. Decision five — enforcement and provenance

### 5a. Who validates structure

Almost nobody, and the pattern is consistent: schema validation exists where the
unit is a database row and is absent where the unit is text.

| System | Structural validation |
|---|---|
| Anthropic memory tool | none — free-text files |
| Claude Code auto memory | none on content; `type` and `modified` frontmatter written by the harness |
| Anthropic Skills | field-level: `name` ≤64 chars charset-restricted, `description` ≤1024 [DOC] |
| Letta v2 | **pre-commit hook** rejecting writes past depth/character limits [SRC] |
| Graphiti | pydantic models; entity-type check verifies only that a custom type does not shadow a built-in field name [SRC] |
| Cognee | `ontology_valid` bool; **`strict` mode drops unmatched nodes** (opt-in) [SRC] |
| MCP memory server | added 2026-09-03 [SRC] |
| LangGraph `BaseStore` | none — plain dicts |
| CrewAI | none; scope, category, importance all LLM-inferred |

**Graphiti's "custom entity types" are advisory in a way worth stating.** The
sole validation on an extracted edge is that the returned entity names exist in
the node list; otherwise `logger.warning(...); continue`. **There is no check
that a relation name belongs to a declared edge type**, and the extraction prompt
instructs the model to "derive a `relation_type` from the relationship predicate
in SCREAMING_SNAKE_CASE" [SRC]. The relation namespace is whatever the model
invents.

**Nobody gates broken references.** No agent-memory system found fails a build, a
write, or a retrieval on a dangling link between memory units.

### 5b. Human-authored versus model-inferred

Four systems make any distinction:

1. **Claude Code**, by *mechanism* — CLAUDE.md is the human's file, auto memory
   is Claude's, separate systems and storage [DOC]. The cleanest split here. Its
   provenance *field* is thin: a `modified` ISO 8601 timestamp on files that
   already have frontmatter, requiring v2.1.214+ [DOC]. **When**, not **who**.
2. **Cognee**, by *field* — `ProvenanceEntry.is_automated: bool` with
   `agent_type`, `agent_id`, `role`, plus `source_pipeline` / `source_task` /
   `source_node_set` / `source_user` / `source_content_hash` on every
   `DataPoint`, and a `checksum` / `previous_checksum` / `sequence_id` hash chain
   for tamper evidence [SRC]. **The richest provenance layer in the survey — and
   `COGNEE_PROVENANCE_MODE` defaults to `lightweight`, with the full W3C PROV-O
   ledger gated behind `PROVENANCE_TRACKING`, default off** [SRC].
3. **Hindsight**, by *schema* — four networks separating "what an agent knows
   versus what it believes," with confidence scores [PAPER]. Knows-versus-believes,
   not human-versus-model.
4. **mem0**, by *field* — `attributed_to ∈ {user, assistant}`, plus `actor_id`
   and `role` on history rows [SRC]. But `attributed_to` records **who spoke**;
   the extraction is LLM-inferred either way. No confidence scores; a grep for
   confidence/verified/asserted returns nothing [SRC].

**Graphiti has none.** A grep of `nodes.py`, `edges.py` and `edge_operations.py`
for `provenance|human|manual|asserted|confidence` returns **zero hits** [SRC].
Its only provenance is `EntityEdge.episodes` — the originating episode uuids.
Everyone else — LangGraph, LlamaIndex, CrewAI, AutoGen, the MCP memory server,
the Anthropic memory tool — has nothing at all.

**Letta v2 has an authorship field that is unauthenticated.** Commits are
authored as `<agentId>@letta.com` with `letta.agentId` in git config, but
`GIT_DISABLE_COMMIT_SIGNING_ARGS` forces `commit.gpgsign=false` on every
invocation [SRC].

### 5c. Provenance as an architecture

Two 2026 papers treat provenance as the primary design axis. **Eywa** organises
around "evidence before belief": immutable source evidence stored before derived
facts, extracted memories validated "against typed signals and source support,"
and retrieval through "a deterministic multi-route read path with **zero LLM
calls inside retrieval**" [PAPER, abstract,
[arXiv:2605.30771](https://arxiv.org/abs/2605.30771)]. Its stated motivation is
diagnosability: existing systems "collapse source evidence, extracted facts,
retrieved context, and answer policy into one opaque prompt path, making failures
difficult to diagnose." **MemClaw** stores "writer identity, source system,
derivation history, and modification lineage" and reports 100% reconstruction of
depth-four derivation chains [PAPER]. **Neither distinguishes human from model.**

### 5d. The stated limit of instruction-based enforcement

Anthropic documents the boundary more directly than anyone, and it applies to
every text-based memory system here [DOC]:

> "Claude treats them as context, not enforced configuration. To block an action
> regardless of what Claude decides, use a PreToolUse hook instead."

> "CLAUDE.md content is delivered as a user message after the system prompt, not
> as part of the system prompt itself… there's no guarantee of strict compliance,
> especially for vague or conflicting instructions."

**Reading:** every "the agent should not re-propose X" mechanism in this survey —
Graphiti's date-range prose, the memory tool's "keep it organized" prompt,
Cognee's unsurfaced `superseded` flag — is context, not enforcement, and inherits
this limit. The two hard write-time gates found anywhere are Letta v2's
pre-commit hook and Cognee's opt-in `strict` ontology mode.

---

## 6. Shipped consumer and coding products

Libraries can defer hard questions. Products cannot, because a user eventually
types "no, that's wrong." This section is about what happens then.

### 6a. The correction primitive is `delete`, almost everywhere

| Product | Unit | Author | Retrieval | Correct a wrong one | Provenance shown |
|---|---|---|---|---|---|
| **ChatGPT** | synthesised profile paragraphs (was: numbered dated sentence) | model | **everything, every message** | edit box that **does not reach the store**; else delete every source | **yes** — sources + why |
| **Claude** | markdown file per topic, `[stated]` lines, `[[wikilinks]]` | model + user | **index every turn, bodies on demand** | per-topic edit and delete | **yes** — citations to source chats |
| **Gemini** | instruction string; else **the whole chat** | split: instructions user, memory model | injected but **gated** unless triggered | **no list, no edit** — delete the source chats | ask the model |
| **M365 Copilot** | instruction / saved memory / inference | both — **asks first** | not disclosed | **delete only, no edit** | no |
| **Meta AI** | profile fact | both | not disclosed | delete only | no |
| **Perplexity** | dated categorised fact | **model only** | every new conversation | delete only | category + date |
| **Grok** | **a past conversation** | contested | per-response | remove reference, no edit | **yes** — "Referenced chats" |
| **Cursor** | `.mdc` rule file (Memories removed) | user (was both) | 4 modes; `alwaysApply` every turn | edit the file | no |
| **Windsurf / Devin** | memory item; rule file | both | auto; **per-mode cost table published** | edit; being retired toward Skills | no |
| **GitHub Copilot** | repo fact / user preference | model | **citation-validated against current branch** | delete; **28-day unused decay**; down-vote on forget | **yes** — code citations |

**Not one of the ten has a durable "I decided against X, stop suggesting X"
primitive.** The entire supersession vocabulary across the consumer market is:
delete the entry, edit the file, or delete the conversation the memory came
from.

*Sourcing for the table:* ChatGPT, Claude, Gemini and GitHub Copilot rows are
sourced in §6b–§6f below. M365 Copilot from
[support.microsoft.com](https://support.microsoft.com/en-us/topic/manage-copilot-memory-in-microsoft-365-copilot-b3231eae-9e60-4b3c-ac58-81fddbe56279)
and [learn.microsoft.com](https://learn.microsoft.com/en-us/copilot/microsoft-365/copilot-personalization-memory)
— note memories are stored in a hidden Exchange mailbox folder, memory actions
generate **no Purview audit entries**, and the doc is still labelled "in preview"
[DOC]. Meta AI from
[about.fb.com, 2025-01-27](https://about.fb.com/news/2025/01/building-toward-a-smarter-more-personalized-assistant/)
[DOC]. Cursor rules from [cursor.com/docs/rules](https://cursor.com/docs/rules.md)
[DOC]. Windsurf from
[docs.devin.ai](https://docs.devin.ai/desktop/cascade/memories) [DOC] — the only
vendor found publishing a **per-mode context-cost table** (Always On = "Every
message"; Model Decision = "Description always; full content on demand") with
hard caps of 6,000 characters global and 12,000 per workspace rule. **Perplexity
and Grok rows are the weakest in this document**: every Perplexity primary source
returned HTTP 403 and every xAI surface was unreachable, so both are assembled
from secondary reporting — see §9.

### 6b. The one published number on whether stated preferences are honoured

OpenAI's own memory eval, extracted from the chart data in its announcement
[DOC, [openai.com, 2026-06-04](https://openai.com/index/chatgpt-memory-dreaming/)]:

| | 2024 (saved memories) | 2025 (Dreaming V0) | 2026 (Dreaming V3) |
|---|---|---|---|
| Carrying forward context | 41.5% | 67.9% | **82.8%** |
| **Following preferences and constraints** | 31.4% | 55.3% | **71.3%** |
| **Staying current over time** | **9.4%** | 52.2% | **75.1%** |

**Reading:** after the largest memory rewrite OpenAI has shipped, on its own
benchmark, **roughly three in ten stated preferences are still not followed**.
And "staying current over time" — the marathon-then-sprained-ankle case — started
at 9.4%. This is the vendor with the strongest incentive to make the number look
good, so treat it as an optimistic ceiling on profile-synthesis memory.

### 6c. A correction UI that does not reach the store

Two independent parties reached the same conclusion about ChatGPT's memory-summary
edit box. By prompt archaeology [2ND,
[shloked.com, 2026-06-06](https://www.shloked.com/chatgpt-memory-2026)]:

> "The Memory Summary is a user-facing artifact, not what gets inserted into
> context during your ChatGPT conversations… it never makes its way into the
> model's context."
> "Those edits are indeed passed to the model, but **as part of your recent
> conversation history**."

And by black-box testing [2ND, USER REPORT,
[OpenAI community, 2026-08-02](https://community.openai.com/t/allow-users-to-edit-chat-memory-summaries/1388761)]:
"In my own testing, none of the memory summary items appeared to change after
using this feature."

**Reading:** one method is prompt archaeology and the other is behavioural, and
they agree. Alongside it, the documented deletion contract requires the user to
"delete every source where it appears, including past chats, archived chats,
files, the memory summary, and disconnect any connected apps," and warns that
"deleted memories may continue to be referenced for a few days" [DOC, Memory
FAQ]. **Correction in the largest deployed memory system is a request, not a
write.**

### 6d. Claude's design, and why it is the closest analogue to a linked-note vault

The consumer Claude memory store is markdown files with YAML frontmatter
(`name`, `description`, `sources`, `aliases`), `[[wikilinks]]` between them, and
per-line `[stated]` tags marking facts the user said directly — with an explicit
exclusion list covering "conclusions you drew… your research output… your
enrichment of what they said" [2ND, LEAKED PROMPT, widely mirrored;
**treat as a leaked artifact, not official documentation**]. Retrieval is a
**hybrid**: a `<memory_listing>` giving "each file's path, one-line summary,
aliases, and sources" is supplied every turn, with bodies fetched on demand —
"When relevance is uncertain, read the file, reading is cheap and the user sees
the call; the cost is in mis-applying, not in reading."

**Supersession is instructed, with the history retained in the text itself:**

> "If the existing file says 'PM on search team' and you just learned they moved
> to infra, the new file says 'PM on infra team (previously search)'. **History
> is useful.**"

Two documented gaps, both from official sources [DOC,
[support.claude.com](https://support.claude.com/en/articles/11817273-use-claude-s-chat-search-and-memory-to-build-on-previous-context)]:
"When a conversation expires or is deleted, related memory entries generated from
it won't be removed" — the exact inverse of Gemini. And an unusually candid
banner acknowledging data loss in the July 2026 migration, offering a manual
export-and-paste recovery path with a hard expiry date.

### 6e. Gemini solved contamination by refusing to personalise

Gemini passes a `user_context` block alongside every prompt and then gates it
with an all-caps system-prompt rule [2ND,
[shloked.com, 2025-11-19](https://www.shloked.com/gemini-memory)]:

> "**MASTER RULE: DO NOT USE USER DATA.** Information in the user_context block
> is RESTRICTED by default… You are only authorized to access and use
> user_context data if the user's current prompt contains a direct trigger
> phrase requesting personalization."

**Reading:** this is the only product that solved memory contamination, and it
solved it by making memory opt-in per turn. The cost is that the memory almost
never fires. It is the extreme end of the same axis Anthropic's index-plus-fetch
design sits in the middle of and OpenAI's inject-everything design sits at the
other end of.

**And the uncorrectable-wrong-memory case in its purest form**: a security
researcher demonstrated delayed-tool-invocation memory poisoning, where a
poisoned document plants instructions that lie dormant until the user says "yes,"
at which point Gemini writes attacker-chosen facts into Saved Info. **Google
classified it as "abuse-related risk with low likelihood and low impact"** [2ND,
[embracethered.com, 2025-02-10](https://embracethered.com/blog/posts/2025/gemini-memory-persistence-prompt-injection/)].
A memory the user never authored, in a store with no per-item delete, whose only
removal path is finding a source chat the user does not know exists.

### 6f. The two mechanisms worth studying, both from GitHub Copilot

GitHub ships the only two freshness mechanisms found in any product [DOC,
[docs.github.com](https://docs.github.com/en/copilot/concepts/agents/copilot-memory)]:

> "Repository-level facts are stored with **citations pointing to the code that
> supports them**. When Copilot finds a fact relevant to its current work, **it
> checks those citations against the current branch to confirm the information is
> still accurate. Only validated facts are used.**"

> "any stored fact or preference that goes unused is automatically deleted after
> **28 days**."

**Reading:** citation-validation is the only mechanism in this survey where a
memory's continued validity is checked against ground truth rather than against a
timestamp or a model's judgement. It works because code is a checkable
referent — which is also its limit.

It still leaks: memories from a different, similarly-named repo cited in a brand
new empty repo, with no way to delete them [2ND,
[copilot-cli#3945](https://github.com/github/copilot-cli/issues/3945), open]; and
a wrong memory addressing the user by the wrong name, where the CLI wrongly told
them there was no way to clear it [2ND,
[copilot-cli#1443](https://github.com/github/copilot-cli/issues/1443), open].

### 6g. The rule-adherence problem, which is not a memory problem

The heaviest documented complaint volume in the coding-agent space is not about
storage or retrieval. It is about **stored rules being loaded and then ignored**.
The clearest statement of it is a user's [2ND,
[claude-code#33603](https://github.com/anthropics/claude-code/issues/33603),
2026-03-12, open, 19 comments]:

> "**Every rule in this system was added in direct response to a specific
> documented failure. Every rule has been violated again after being added.**"

A second report names the shape precisely: "Memory entries are loaded into
context but functionally ignored… **The memory system becomes write-only, data
goes in but doesn't reliably influence behavior**" [2ND,
[claude-code#47351](https://github.com/anthropics/claude-code/issues/47351),
closed as stale].

**And compaction is a documented boundary where standing rules are lost.** An
agent followed AGENTS.md's "never commit directly to main" early in a session,
then after compaction **committed directly to main twice**; the filed root cause
is that "standing repo instruction files aren't treated as pinned/reloaded,
they're just facts that can get compressed away" [2ND,
[copilot-cli#4687](https://github.com/github/copilot-cli/issues/4687),
2026-09-01, open]. Claude Code is the only product found that documents its
compaction contract: project-root CLAUDE.md is re-read from disk and re-injected;
nested files and path-scoped rules are not [DOC].

### 6h. Two evaluations that disagree about always-loaded context, and both are right

- **Against.** Across SWE-bench with both LLM-generated and developer-committed
  context files, "providing context files does not generally improve task success
  rates" while "increasing inference cost by over 20% on average." But the same
  paper reports, and coverage usually drops this: "**instructions in the context
  files are well followed by coding agents**" [PAPER,
  [arXiv:2602.11988](https://arxiv.org/abs/2602.11988)]. The failure is relevance,
  not adherence.
- **For, and directly against on-demand retrieval.** Baseline 53%, Skills with
  default descriptions 53%, Skills with explicit instructions 79%, **an AGENTS.md
  docs index 100%** — because "**In 56% of eval cases, the skill was never
  invoked**" [2ND,
  [vercel.com, 2026-01-27](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)].
  Their conclusion: "For general framework knowledge, passive context currently
  outperforms on-demand retrieval."

**Reading:** these are compatible, and together they state the actual trade.
Always-injected context wins on recall *precisely because* on-demand retrieval
fails to fire in over half of cases; it loses on cost and on relevance. That
56%-never-invoked figure is the single most useful number here for anyone
choosing between a resident budget and a search tool, and it is measured on a
skills mechanism rather than on a memory one — so it bounds the *upper* end of
voluntary retrieval reliability, in a setting designed to make invocation easy.

---

## 7. The graveyard — what was tried and abandoned

| What | When | Stated reason |
|---|---|---|
| **LangChain entity / KG memory** | deprecated 0.3.1 | **"require significant customization… making them less widely used… no migration guides"** [DOC] |
| **LangChain buffer / summary memory** | deprecated 0.3.1 | "lacked built-in support for multi-user, multi-conversation scenarios"; "weren't designed for newer chat model APIs" [DOC] |
| **mem0 graph memory** | deleted 2026-04-14, −17,228 lines | **none stated.** Their own paper: −3.96 multi-hop, 3.3x latency, 2x tokens [SRC + PAPER] |
| **mem0 UPDATE/DELETE pipeline** | removed 2026-04-14 | "Memories accumulate… no more UPDATE/DELETE events" [DOC] |
| **Graphiti HNSW indexing** | added 2025-08-25, removed 2025-09-05 | none stated. Cost: 41s vs 82ms at 48k nodes [2ND] |
| **Graphiti `invalidate_edges.py`** | deleted 2026-01-31 | "not used in add_episode flow" — folded into `dedupe_edges.resolve_edge` [SRC] |
| **Letta Python server** | archived 2026-08-16 | "chore: archive the legacy server repository" [SRC] |
| **AutoGen Teachability** | dropped in the v0.4 rewrite | listed as not yet carried over; rewrite was for "observability, flexibility, interactive control, and scale" [DOC] |
| **CrewAI's four memory types** | collapsed into one `Memory` | **none found** — no deprecation notice or changelog entry [DOC] |
| **LlamaIndex `ChatMemoryBuffer`** | deprecated, still the default | replaced by the more flexible `Memory` class [DOC] |
| **LangMem** | last release 0.0.30, 2025-10-27 | none — **stalled, not deprecated**; still pinned to the superseded 0.3 line [DOC, PyPI] |
| **Cursor Memories** | shipped 0.51 (2025-05-30), headlined in 1.0, **removed in 2.1.17 (Nov 2025)** | none — **the removal is not in the changelog**. Staff in a forum thread: "You can export your memories and move them into Rules." In 2.2 the agent still reported 32 live memories with **no UI to delete them** [2ND] |
| **OpenAI saved-memories list** | retired 2026-06-04 | "**Memories could also contradict one another**… which made personalization less accurate" [DOC] |
| **Windsurf Cascade Memories** | being retired toward Rules / Skills | "For knowledge you want Cascade to reliably reuse, write it as a Rule or add it to AGENTS.md… rather than relying on auto-generated Memories" [DOC] |
| **AutoGPT vector memory backends** | moved out of core, 2023 | removal is primary; the reason is a 2023 blog post with no link. **Treat the reason as unsourced** |
| **Claude Code RAG + local vector DB** | early versions | "we found pretty quickly that agentic search generally works better" — attributed to Boris Cherny on X; **read in a secondary blog, not verified at source** |

**Two patterns worth naming.** First, **announced removals do not happen**:
LangChain's `removal="1.0.0"` became `langchain-classic` with `removal="2.0.0"`
and all the memory modules still present [SRC], and LlamaIndex still defaults to
the class it deprecated [DOC]. Second, **the reasons are usually missing**: of
thirteen entries above, four have a clear stated reason, two have a reason that
is a reading of the vendor's own numbers, and the rest have none.

---

## 8. What the benchmark numbers actually support

### 8a. LoCoMo has an audited scoring ceiling of 93.6%

An independent audit of the answer key [2ND, reproducible repo at
[dial481/locomo-audit](https://github.com/dial481/locomo-audit), dataset
SHA256-pinned, audit dated February 2026]:

- **156 ground-truth issues across 1,540 non-adversarial questions; 99 are
  score-corrupting (6.4%)** — 33 hallucinated facts, 26 wrong date calculations,
  24 wrong-speaker attributions, 13 ambiguous, 3 incomplete.
- **"The theoretical maximum score for a perfectly correct system is ~93.6%."**
- **The judge accepts 62.81% of intentionally wrong but topically adjacent
  answers.**
- **Per-category comparisons are not usable:** category sizes run 96 to 841
  (8.8x), and Wilson-score CIs make **56% of adjacent-pair comparisons
  statistically indistinguishable**.
- **22.5% of the dataset is never evaluated by anyone** — 446 adversarial
  questions whose formatter references a missing field on 444 of them.
- **Published scores exceed the corrupted ceiling** — one system reports
  single-hop 95.96% against a category ceiling of 95.72%.
- **A plain full-context baseline with a CoT answer prompt scores 92.62%**,
  above the memory system it was compared against. The audit's reading: "The
  answer prompt, not the memory system, explains the score."

**Scale:** LoCoMo-10 is **ten LLM-generated conversations** averaging ~9K tokens
[PAPER, [arXiv:2402.17753](https://arxiv.org/abs/2402.17753)]. Every LoCoMo score
in circulation is an average over ten synthetic two-person conversations that fit
whole in a modern context window.

**Against that ceiling, mem0 self-reports LoCoMo 92.5** [2ND].

### 8b. The public dispute, where one system produced three numbers

| Date | Event |
|---|---|
| 2025-05-06 | Zep publishes a critique of mem0's paper, claims **84%** for itself, alleges three misconfigurations (user role assigned to both participants; timestamps in message text rather than the `created_at` field; sequential rather than parallel search, inflating latency) [2ND, [blog.getzep.com](https://blog.getzep.com/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/)] |
| 2025-05-08 | mem0's CTO opens an issue on Zep's own repo: corrected evaluation gives Zep **58.44% ± 0.20**; charges that adversarial questions were counted in the numerator but excluded from the denominator [2ND, [getzep/zep-papers#5](https://github.com/getzep/zep-papers/issues/5)] |
| 2025-05-12 | Zep **concedes the calculation error**: "The corrected score is 75.14% +/- 0.17." Stands by the rest, asks mem0 for datasets |
| 2025-05-19 | Zep closes the issue for inactivity; mem0 never replied |

**Reading:** one system, one benchmark, three published numbers — **84%, 75.14%,
58.44%** — and nobody neutral has reproduced any of them. Note also that Zep,
while defending its score, calls LoCoMo "the flawed LoCoMo evaluation": the
defender of the number agrees the benchmark is bad.

**A second, independent charge.** Letta reported that "our research team (the
same team behind MemGPT) was unable to determine a way to backfill LoCoMo data
into MemGPT/Letta without significant refactoring… Mem0 did not respond to
requests for clarification" — verifiable as
[mem0#3004](https://github.com/mem0ai/mem0/issues/3004), opened by Letta's CTO
2025-06-21, **0 comments, closed unanswered**. Letta then showed a plain
filesystem agent using `grep` and no memory system scoring **74.0%**, above
mem0's graph variant [2ND, [letta.com](https://www.letta.com/blog/benchmarking-ai-agent-memory/)].

**And the self-inflicted wound is in mem0's own Table 2:** the full-context
baseline scores **72.90**, above Mem0 (66.88) and Mem0-graph (68.44). The
headline latency and token savings are measured against a configuration that
beats mem0 on accuracy [PAPER, arXiv:2504.19413]. The paper has **never been
revised** — v1 only, 2025-04-28.

**Three independent reproduction failures** are on record: [mem0#2800](https://github.com/mem0ai/mem0/issues/2800)
(2025-05-26, "significantly lower than the ones I see in the paper");
[mem0#3944](https://github.com/mem0ai/mem0/issues/3944) (2026-01-28, score ~0.20,
traced to the pipeline stamping memories with the *current* date instead of the
dataset timestamps); [EverOS#73](https://github.com/EverMind-AI/EverOS/issues/73)
(2026-02-05, **38.38% vs a claimed 93%**).

### 8c. Zep's own margin is thinner than it reads

Zep's DMR result is 94.8% against MemGPT's 93.4% — but the **full-conversation
baseline in the same table scores 94.4%**, so the margin over "just put the
transcript in the prompt" is **0.4 points**, and 0.2 with gpt-4o-mini [PAPER,
[arXiv:2501.13956](https://arxiv.org/abs/2501.13956), Table 1]. **The authors say
so themselves:** "showing marginal improvements over both MemGPT and the
respective full-conversation baselines. However, these results must be
contextualized: each conversation contains only 60 messages… Our analysis
revealed significant weaknesses in the benchmark's design."

On LongMemEval, "up to 18.5%" is a **relative** improvement (71.2 vs 60.2), not
percentage points, and the 90% latency reduction is **115k tokens → 1.6k tokens**
fed to the same model — a context-compression result, not evidence that graph
retrieval is fast. The paper also notes the Zep runs carried extra network
latency the baselines did not [PAPER, Table 2].

**Table 3 is the inconvenient one.** Per-question-type relative delta vs
full-context: single-session-preference +77.7%/+184%, temporal-reasoning
+48.2%/+38.4% — but **knowledge-update −3.36% with gpt-4o-mini** and +6.52% with
gpt-4o, the two weakest deltas in the table [PAPER]. `knowledge-update` is
precisely the category the bi-temporal machinery exists to serve.

### 8d. The vendor leaderboard is not a leaderboard

Three vendors claimed state of the art on BEAM within four months, all
self-reported, **none publishing a per-ability breakdown** — so the one ability
the benchmark's authors flag as unsolved is the one nobody reports [2ND, both
posts checked]. Neither Hindsight nor Exabase discloses who ran the evaluation.
And the "public tracker" at `benchmarks.hindsight.vectorize.io` presents itself
as "the industry standard," lists only Hindsight's results, claims they are
"verified," describes no verification policy, and **is operated by the vendor of
one of the entrants** [2ND].

The two vendors also publish irreconcilable numbers for the same pair of systems:
Zep's own comparison page claims 94.7% vs 91.6% for mem0 on LoCoMo and 87 ms vs
3,060 ms p50 retrieval, where mem0's paper reports Zep at 65.99 and 513 ms p50
[2ND; the Zep page **carries no date**, so its numbers are not cited as
evidence here — the *fact of the contradiction* is the finding].

### 8e. Independent, non-vendor head-to-heads do exist

This is the one place where "every benchmark in this space is vendor-run" turns
out to be false, and the independent results are the least flattering:

- **Fidelity Before Structure** [PAPER,
  [arXiv:2601.00821](https://arxiv.org/abs/2601.00821)] swaps *only* the stored
  representation inside one fixed pipeline: **verbatim chunks 43.9% vs
  LLM-extracted typed artifacts 28.0% on LoCoMo (+15.9), and 67.4% vs 45.4% on
  LongMemEval-S (+22.0)**. A one-hop semantic graph did not close the gap.
  Accuracy correlated with how much original text remained in storage.
- **MemDelta** [PAPER, [arXiv:2606.29914](https://arxiv.org/abs/2606.29914)]:
  swapping *only the embedding model* shifted accuracy **+6.2 points**, enough to
  flip which system wins; baseline rankings reverse by model; **mem0 matched
  cloud RAG on 2 of 6 question types at 50x the cost**; **agent self-memory
  underperformed basic retrieval, 42% vs 47%**.
- **MemFail** (Berkeley) [PAPER,
  [arXiv:2605.26667](https://arxiv.org/abs/2605.26667)], evaluating Mem0, A-MEM,
  SimpleMem, StructMem — this is the auto-summarisation postmortem in controlled
  form: "Conditional-Facts (Hard) induces summary failures: **all systems
  over-compress**, either altering the original message or stripping away
  precise, critical details"; "failures stem almost entirely from incorrect
  summarization or retrieval"; and "**scaling either the number of retrieved
  memories or the strength of the underlying LLM yields little improvement, and
  in several cases degrades performance**."
- **Does Memory Need Graphs?** [PAPER,
  [arXiv:2601.01280](https://arxiv.org/abs/2601.01280)]: "many performance
  differences are driven by foundational system settings rather than specific
  architectural innovations." **Dark:** the quantitative tables would not extract
  from either HTML or PDF.

---

## 9. What could not be established

1. **No Reddit evidence, at all.** `reddit.com` was blocked to both fetcher and
   search tool throughout. Zero threads from r/ChatGPT, r/Bard, r/cursor or
   r/perplexity_ai. The user-complaint evidence in §6 comes from vendor forums
   with real view/reply counts and from GitHub issues instead, which are better
   sourced but differently biased — they over-represent developers.
2. **Token cost of memory injection is unpublished by all ten consumer
   products.** This was checked for each. The only published proxies anywhere are
   Windsurf's 6,000/12,000-character rule caps, Claude Code's 200-line / 25KB
   index cap, and Cursor's 500-line rule guidance.
3. **Several consumer primary sources were unreachable.** OpenAI's help centre
   403s automated fetchers (the Memory FAQ was read via a Wayback snapshot);
   Perplexity's blog, help centre and changelog all 403; Meta's and xAI's help
   pages were unreachable. Consequently: whether Meta AI memories can be edited
   rather than only deleted, whether Grok's memories are model- or user-authored,
   and Perplexity's reported recall figures are all **unestablished**.
4. **Claude's consumer memory internals in §6d come from a leaked system
   prompt**, not from Anthropic documentation. The behaviour it describes is
   consistent with the official help-centre articles cited alongside it, but the
   frontmatter schema, the `[stated]` tagging and the `<memory_listing>`
   mechanism are **not officially documented** and should be treated as a leaked
   artifact.
5. **Whether Cursor Memories exists in any form in Cursor 3.x.** Staff said
   removed in 2.1.17 (2025-11-24); the same staff member appeared to describe it
   as current in August 2026; there is no docs page either way.
6. **No public longitudinal data past ~5 weeks exists** (§4a). This is reported
   as a finding, but it is possible such data exists behind closed doors.
7. **No published curve of retrieval precision or latency against store size**,
   from any vendor or academic group.
8. **Cost per stored memory is unpublished** for every extraction system.
9. **Doyle 1979's own text could not be retrieved** — ScienceDirect 403, MIT
   DSpace 405, PhilPapers 403. The citation is verified; the mechanism
   description is reconstructed from secondary sources.
10. **No scholarly retrospective on why TMS disappeared.** The complexity results
   exist; a causal account was not found.
11. **LongMemEval per-system knowledge-update subset accuracy** could not be
   extracted from any primary source across six access routes. The only
   per-subset figures found are vendor self-reported and unaudited.
12. **TOKI** [PAPER, [arXiv:2606.06240](https://arxiv.org/abs/2606.06240)], a
   bitemporal operator algebra citing Snodgrass, was only partially extracted. It
   is the paper most likely to be a counterexample to §0's third finding.
13. **No independent reproduction of Zep's DMR or LongMemEval numbers** was
   found; the paper has no v2.
14. **Two claims are secondhand and flagged as such:** the Boris Cherny quote on
    Claude Code dropping RAG (read in a blog, not on X), and AutoGPT's reason for
    dropping vector backends (removal primary, reason unsourced). The widely
    repeated "$33,000 GraphRAG indexing" figure has **no Microsoft primary
    source** and should not be used.
15. **Vendor contamination in issue threads.** Several mem0 threads contain
    visible promotion for competing products from new accounts. Claims tagged
    [SRC] were read from source; the issue-tracker claims tagged [2ND] carry the
    normal reliability of a public bug report.
16. **The WebSearch budget was exhausted** (200 calls) partway through, after
    which discovery relied on direct fetches and the GitHub API. Some gaps above
    follow from that rather than from the field being silent — in particular,
    first-person "we built agent memory and it didn't work" narrative writeups
    are absent, though the GitHub-issue equivalents are better sourced anyway.

---

## Appendix — one-line index

| System | Verdict on supersession |
|---|---|
| **Graphiti / Zep** | Bi-temporal fields, retained not deleted; LLM decides *whether*, a date rule decides *which*; unscoped candidate pool retires 16–41% of facts in the field; expired edges still retrieved by default; final judgement delegated to the model. 7.0% on FactConsolidation [SRC + 2ND + PAPER] |
| **mem0** | ADD-only since 2026-04-14; contradiction link generated and discarded; ranker has no time term; deleted memories can be silently re-added [SRC] |
| **Cognee** | The most machinery — deterministic supersession, `contradicts` edges with reason and confidence, `close_node` — **all off by default, and `superseded` appears nowhere in retrieval** [SRC] |
| **Letta v1** | `str.replace()` destroys in place; archival memory has **no delete tool at all**; live compaction bug wipes context [SRC + 2ND] |
| **Letta v2** | Git history is the audit trail; nothing surfaces it to the agent [SRC] |
| **Anthropic memory tool** | No mechanism; `str_replace` / `delete` prompted, not enforced; suggested forgetting is "periodically delete memory files that haven't been accessed in a long time" [DOC] |
| **Claude Code auto memory** | No mechanism; contradiction explicitly handed back to the human [DOC] |
| **Google ADK / Vertex** | Opt-in LLM consolidation; redundancy control, not supersession [DOC] |
| **MemClaw** | `supersedes_id`, old row retained inactive; structural detection; self-evaluated, no baseline [PAPER] |
| **LatticeMind** | Full provenance on superseded items; **re-proposal explicitly unaddressed** [PAPER] |
| **TEPA** | Revoked precedents leave retrieval; **no rationale stored**; revocation reversible [PAPER] |
| **LlamaIndex / LangGraph / CrewAI / AutoGen / MCP server** | None. Compression, key-overwrite, decay-demotion, append-only, exact-string dedup respectively [DOC + SRC] |
| **A-MEM / Generative Agents** | Notes rewritten in place with no diff; append-only stream; no deletion or invalidation in either paper [PAPER] |
| **ChatGPT** | User-managed list retired 2026-06-04 for a synthesised profile; the replacement edit box **does not write to the store**; deletion requires finding every source [DOC + 2ND] |
| **Claude (consumer)** | Best-in-class correction — per-topic edit and delete, citations to source chats, supersede-with-history instructed ("previously search"). Still a prose fact, not a suppression object [DOC + 2ND] |
| **Gemini** | No memory list and no per-item edit; Google documents that "stop mentioning a topic" **doesn't always work perfectly** and the only reliable remedy is deleting the source chats [DOC] |
| **GitHub Copilot** | The only two freshness mechanisms in the survey: **facts carry code citations re-validated against the current branch**, and unused facts decay after 28 days [DOC] |
| **M365 Copilot / Meta AI / Perplexity** | Delete only, no edit. Microsoft claims automatic merging with no user-visible record of what was merged [DOC] |
| **Cursor / Windsurf** | Auto-memory shipped then withdrawn toward rule files; Cursor left 32 memories reachable by the agent with no delete UI [2ND] |
| **MADR / ADR / PEP 1** | **Full answer to both halves.** No tooling; human-enforced [DOC] |
| **Fowler's Retroactive Event** | **Full answer**: rejected events stay in the log *and* are ignored by all further processing. Fowler: "I haven't seen [it] very often" [DOC] |
| **ATMS nogoods (1986) / CDCL** | **Blocks re-proposal mechanically.** Reason is a minimal inconsistent assumption set, not an explanation [PAPER] |
| **AGM belief revision (1985)** | Contraction is formally hard and underdetermined; the choice must come from outside the logic [DOC] |
