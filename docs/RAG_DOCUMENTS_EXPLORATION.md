# RAG for documents in Clayrune — exploration and MVP proposal

**MC-950 (backlog `13affa86`).** Exploratory only: nothing here is implemented, no vector DB was installed,
no paid API was called. Measured 2026-10-01 against `master` `7a2d4faf` on one Windows box (24 cores).
Author: Kestrel. Scratch harness (not committed, `_scratch/` is gitignored):
`_scratch/rag_eval/` in the agent worktree; the question set is reproduced in the appendix so the MVP can
re-create it as a test.

## Verdict

1. **The biggest document-finding failure is not ranking, it is visibility.** An agent dispatched into a
   worktree sees 143 of 563 markdown files under `docs/` (25%). 420 are gitignored and exist only in the main
   checkout: 296 `_journal/` files (which `AGENT_RULES.md` tells agents to read at the start of a cycle), 23
   `_campaign/`, 53 night/GCP reviews, and 48 design docs and audits (remote-access, committee seats, `_ws*`
   audits, `MEMORY_SYSTEM.md`, `SKILLS_CURATION_DESIGN.md`; `CLAUDE.md` names the last two). Five of my 15 questions have their only answer in
   those files; worktree grep finds **0 of 5**.
2. **Keyword search over documents, built the way `mc/memory_fts.py` already works, fixes most of it.**
   SQLite FTS5 (BM25) over heading-chunked docs puts the answer in the top 3 for **14 of 15** questions
   (strict scoring), versus about 9 of 15 for grep plus `mc-memory-search` combined, and it costs 0.5 s to
   build, 8.8 MB, and 1.7 ms per query.
3. **Semantic retrieval is a real but smaller gain, and not yet earned.** Local bge-m3 dense retrieval gets the
   answer to rank 1 on 13 of 15 (FTS5: 10 of 15) and is clearly better on vocabulary-mismatch paraphrases
   (MRR 0.70 vs 0.49, n=8). But top-3 is a wash (13 vs 14), it needs a 544 MB model and 20 minutes of CPU to
   embed the corpus once, and with n=15 a one-question gap is 6.7 points. This matches the standing Step 7
   deferral: build it behind the same acceptance set, not before.
4. **"RAG" in the generation sense needs nothing new.** The agent is the generator. What is missing is a
   retrieval tool that sees the right files, ranks them, and returns citable chunks.

## 1. What document-finding does today, measured

Tools an agent has now: its own Grep/Glob; `GET /api/project/<id>/memory/search` (BM25 over the memory dir,
plus an FTS5 cold tier over transcripts; `mc/blueprints/guide_routes.py:618`, `mc/memory_fts.py`);
`/api/search/global` (transcripts only); the Documents tab (`/api/project/<id>/documents`,
`mc/blueprints/agent_routes.py:16368`, which *lists* plans and agent-written markdown and does not search
content). **No tool ranks project documents.** `mc/memory.py::_memory_search` indexes only the memory dir, so
a docs question reaches it only when a curated note happens to repeat the fact.

**Method.** 15 questions answerable from `docs/`, written before any tool was run (appendix). Ten target
tracked docs, five target gitignored ones. Each has a distinctive answer string; the gold set is every
`docs/**/*.md` in the main checkout containing it. Two no-answer controls (Datadog, PagerDuty: 0 matches in
`docs/`). Hand keywords were picked up front as the 2–3 words a competent agent would grep, which flatters
grep because I knew the answers.

| Tool | Result on the 15 |
|---|---|
| grep, all keywords AND, **main checkout** | gold doc is in the result set 15/15, but **median 8 files returned** (range 2–154); only 6/15 return ≤5 files. Unranked. |
| grep, same, **agent worktree** | gold present 10/15; **0/5 on the gitignored docs**; median 7 files for the ten visible. |
| `/memory/search` (keywords, k=8 + 5 cold) | full answer 6/15 (Q02@3, Q04@5, Q06@1, Q11@2, Q13@2, Q15@5), partial 3 (Q01@11 and Q03@11 only as cold-tier transcript snippets, Q09@2), miss 6. Q14 surfaced the *chat-reducer-disabled* note, a different mechanism from the one asked about: topical, wrong answer. |
| Controls (Datadog, PagerDuty) | `/memory/search` returns 9–13 results for both. No tool signals "nothing here". |

Grep plus memory search together answer about 9 of 15 within a five-file budget (my threshold; Q05, Q07, Q10
by grep, six by memory). The six it misses: Q01, Q03, Q08, Q09, Q12, Q14. Note memory search does better on the
gitignored docs (3 of 5) than on tracked ones (3 of 10 full): it lives at project level, so a worktree does not
hide it. That is the argument for serving doc retrieval from the server, not from the filesystem the agent sees.

## 2. Keyword vs semantic vs hybrid, measured

Corpus: the main checkout's `docs/**/*.md` minus journals/campaign/reviews = 190 files, 4,637 chunks
(split on `#`–`####` headings, packed to ≤1,500 chars, heading path prefixed), 3.9 MB. Strict file-level hit:
a gold file in the top-k *files* (best chunk per file). Misses are partly strict-gold artefacts (see caveats).

| Retriever | hit@1 | hit@3 | hit@5 | MRR | Notes |
|---|---|---|---|---|---|
| FTS5 BM25, question as OR terms, porter stemmer | 10 | 14 | 14 | 0.79 | heading column weighted 3x |
| FTS5 BM25, hand keywords OR | 11 | 13 | 14 | 0.81 | |
| FTS5, no stemmer | 11 | 14 | 14 | 0.84 | stemming did not help here |
| FTS5 over everything (journals, campaign, reviews; 11,057 chunks) | 10 | 13 | 14 | 0.77 | noise costs ~0.02 MRR; journals add little on this set |
| bge-m3 dense (int8 ONNX, local), question | **13** | 13 | 13 | **0.87** | misses Q11 (rank 29), Q12 (rank 12) |
| bge-m3 dense, hand keywords | 9 | 12 | 12 | 0.69 | keyword-style queries suit dense worse than sentences |
| Hybrid, plain RRF (FTS5 + dense, k=60) | 12 | 13 | 14 | 0.85 | |

**Paraphrase probe** (8 questions re-worded to share no keywords with the answer doc; I wrote them knowing the
answers, so treat as indicative): FTS5 MRR 0.49 (hit@3 5/8), dense **0.70** (6/8), plain RRF hybrid 0.65 (5/8).
Plain RRF *hurt* on two paraphrases: documents that are mediocre in both lists outrank one list's clear top.
A weighted or dense-first-with-FTS-rescue fusion would be needed; not tried.

**Caveats that change how to read this.**
- n=15. One question = 6.7 points. Ranking differences of 1–3 questions are not distinguishable from noise.
- Strict gold undercounts. Q11's FTS top hits include `DAVE_DESIGN.md`, which states "This is the deferred
  Step 7" (line 48); `SERVER_SPLIT_PLAN.md` ranks first for Q12 and is plausibly relevant. I did not re-judge.
  Strict numbers are lower bounds for every retriever.
- **No-answer detection fails for both.** Top BM25 score: controls 11.6 and 13.7, real questions 12.0–45.7.
  Top cosine: controls 0.63 and 0.56, real questions 0.47–0.76. A score threshold cannot separate "no answer"
  from "weak answer". The tool must return scores and snippets and let the agent judge, never a bare list.
- Dense queries were fast (about 20 ms each after a one-time model load); the cost is indexing, below.

### Local-first options considered

| Option | Verdict |
|---|---|
| **SQLite FTS5 / BM25** | **Use.** Already in the repo's pattern (`mc/memory_fts.py`), stdlib, nothing to install. |
| **sqlite-vec** | Natural home for vectors next to FTS5 if dense is added; one binary extension per platform (not tested here; Windows, macOS and frozen-app packaging are real work: see the macOS `build-macos.spec` datas gotcha). At 4.6k vectors, numpy brute force (4,637 × 1024 float32 = 19 MB, <10 ms) needs no extension at all. |
| **LanceDB / Chroma / FAISS** | No. A second stateful store for a corpus this small. Revisit only past ~100k chunks. |
| **bge-m3 int8 via onnxruntime** | Measured. `onnxruntime`, `tokenizers`, `numpy` and the 544 MB model were already on this box (the memsearch leftovers). Not a base dependency of Clayrune: `requirements.txt` has none of them. |
| **Ollama embeddings** | Not measured (not installed here). Would add a user-run daemon dependency. |
| **Provider embedding APIs** | Cheap but wrong default: ~1 M tokens for this corpus is on the order of $0.02–$0.13 once, per third-party price roundups (unverified against vendor pages). The cost is not the issue; **docs leave the machine**, and the engines are Claude/Codex/Gemini/Qwen, so no single provider's embeddings are neutral. Opt-in only. |

Provider neutrality is solved by *where retrieval lives*, not by which model embeds. A plain HTTP endpoint
on the Clayrune server is reachable by every engine; `data/agent_reference/CLAYRUNE_API.md:39` already
advertises `memory/search` that way and every agent's pointer card carries it. The skill wrapper is
Claude-format sugar on top.

## 3. Design questions

**Ingestion, update, deletion.** Reuse the `memory_fts.py` mechanism: a per-file `(mtime_ns, size)` fingerprint
table, unchanged files skipped, changed files get their chunk rows replaced in one transaction. Deletion:
a sweep drops rows for paths that no longer exist. Step 7's blocking objection (MEMORY.md is a continuous-write
target, so an embedding index is always stale; `decision-step7-semantic-search-deferral`) does not apply: docs
change on commit, not per turn. Sweep lazily on query (a stat over ~600 files is milliseconds) and at startup.
If dense is added, key embeddings by chunk content hash so an edit re-embeds only the changed chunks.
Measured full-embed cost: **1,220 s** for 4,637 chunks on 24 cores (about 0.26 s/chunk); a typical laptop will
be slower (untested). Incremental updates are a handful of chunks.

**Source of truth is the project path, not the worktree.** Index from the project's own `project_path`
(main checkout) on the filesystem, including gitignored docs, and serve results through the API. That is what
fixes finding 1; indexing "what git tracks" would reproduce the blind spot.

**Citations.** Each hit returns: project-relative path, heading path, start/end line, the chunk text, the
score, and a `[file:<abs path>]`-compatible link (existing `/api/serve-file` allowlist). Agents cite
`path:line`. Store the start line per chunk at ingestion (the harness did not; trivial to add).

**Per-project permissions and privacy.** Index per project, sidecar `.db` outside `DATA_DIR` (`CLAUDE.md`
"DATA_DIR pollution": a stray file there becomes a malformed project). Endpoint takes `project_id` and reads
only that project's roots; cross-project search is a separate explicit call. Reuse three existing guards, do
not invent new ones: `realpath` containment as in the Documents route (`agent_routes.py:16368` onward), the
secrets denylist shared with `/api/serve-file` (`project_routes.py:2376`; `.env`, `*.key`, `*.pem`,
`*credential*`), and the incognito exclusion `memory_fts.py:186` applies to transcripts (fail closed).
FTS5 and local dense keep everything on the machine. Nothing leaves it by default.

**Cost.** FTS5: $0, 8.8 MB per 4,637 chunks, 0.5 s build, 1.7 ms/query. Local dense: $0, +19 MB vectors,
544 MB model on disk, 1,220 s one-time on this box. Provider embeddings: cents, plus the privacy cost above.
Token cost to agents is the real recurring one: auto-injecting top-k docs into every dispatch spends prompt
budget the way the memory read floor does; on-demand search spends it only when asked.

**Not measured.** PDFs and other user uploads: `requirements.txt` has no PDF extractor (checked), so PDFs are
a new dependency and a separate phase. Embedding RAM footprint, macOS/Linux behaviour, the `.docx`/HTML case,
and multi-hundred-project scale.

## 4. Choices that are Ron's (Dave decides; options and recommendation, not a decision)

| # | Choice | Options | Recommendation |
|---|---|---|---|
| D1 | Corpus | (a) project `docs/` only; (b) docs + gitignored docs + `_journal` as a lower-weight tier; (c) whole project tree text files | **(b).** Gitignored docs are the ones agents are told to read and cannot see. Whole-tree dilutes ranking and widens the secrets surface. |
| D2 | Semantic layer | (a) none; (b) local bge-m3 opt-in setting; (c) provider embeddings | **(a) now, (b) as phase 2** when the acceptance set shows FTS5 failing real paraphrase queries. Not (c) by default (privacy, no neutral provider). |
| D3 | Where it lives | (a) new `mc/doc_search.py` + `/api/project/<id>/docs/search`; (b) extend `memory_fts.py` / `/memory/search` | **(a).** MC-964 is changing the memory files; a new module copies the pattern without touching them. Keep the `/memory/search` response shape unchanged. |
| D4 | Surfacing | (a) on-demand endpoint + one line in the API pointer card; (b) auto-inject top-k at dispatch | **(a).** Measure how often agents call it before spending prompt budget on (b). |

## 5. Smallest MVP and acceptance test

**Build (phase 1, FTS5 only):**
1. `mc/doc_search.py`: heading chunker (1,500 chars, line ranges), FTS5 table `porter unicode61`, heading
   column weight 3, `(mtime_ns,size)` incremental sweep, vanished-file deletion, denylist + containment +
   incognito guards. Sidecar `doc_search.db` outside `DATA_DIR`.
2. `GET /api/project/<id>/docs/search?q=&k=` returning `{file, heading, line_start, line_end, score, snippet}`;
   one row added to `CLAYRUNE_API.md` and the pointer card.
3. A `mc-doc-search` builtin skill wrapper (thin), for Claude; other engines use the curl line.

**Acceptance (reuses the appendix set as `tests/`; all must pass):**
- Retrieval: strict hit@3 ≥ 13/15 and hit@5 ≥ 14/15 on the 15 questions (baseline measured: 14/14).
- Visibility: Q11–Q15 return their gold doc when the caller is an agent running inside a worktree.
- Latency: p95 < 50 ms with a warm index; cold first build of this corpus < 5 s.
- Incrementality: editing one file changes only that file's rows; deleting a file removes its rows; an
  untouched sweep writes nothing.
- Safety: a `.env`, a `*.key`, a path escaping the project root, and an incognito project's files are never
  returned or indexed.
- Honesty: control queries still return scored results, and the response carries the score so the agent can
  see the answer is weak (a threshold was shown not to work).
- Phase-2 trigger, copied from the Step 7 position: add dense only if the 8-question paraphrase set, extended
  with real failing queries logged by the endpoint, shows FTS5 hit@3 below 6/8 while dense clears 7/8. Log
  per query: result count, top score, whether the agent opened a returned file.

**What this does not need:** a vector DB, an embedding provider, a model download, or a change to memory.

## Appendix A. The 15 questions and controls

Gold = every main-checkout `docs/**/*.md` matching the answer regex. `vis` = gold doc is tracked (visible in
a worktree).

| ID | vis | Question | Answer regex | Gold doc(s) |
|---|---|---|---|---|
| Q01 | y | Why was our Mac no-Python installer CI check giving a false picture? | `unconditionally prepends` | incidents/ci-job-that-tested-nothing.md |
| Q02 | y | How can an agent's git commits end up on master while its own branch looks untouched? | `resets the working directory to the worktree` | incidents/agent-commits-wrong-branch.md, 1 journal |
| Q03 | y | What does the Cloudflare Worker cost per month, and is tunnel bandwidth metered? | `\$5/mo flat` | remote-access/08-tunnel-capacity-and-roadmap.md, remote-access/03-control-plane-api.md, 1 journal |
| Q04 | y | Why can't the Origin header tell us whether a request came from a human or an agent? | `not a proof of anything` | HUMAN_PROOF_GUARD_SPEC.md |
| Q05 | y | How long does the first page load take and how many requests does it make? | `420 HTTP requests` | LOAD_TIME_INVESTIGATION.md |
| Q06 | y | Which route did the Higgsfield spike recommend for Desk Studio? | `Recommend route A` | desk_v1/HIGGSFIELD_MCP_SPIKE.md |
| Q07 | y | Why did we pick a SQLite state migration instead of only adding a full-text index? | `over an FTS-only` | SQLITE_MIGRATION_SPEC.md |
| Q08 | y | Why did our test suite stay green when a renamed tab threw an error on every click? | `nobody clicked the button` | incidents/tests-that-never-clicked.md, incidents/README.md |
| Q09 | y | How many memory topic files were really never retrieved once link expansion was counted? | `15 topic files, not 30` | _committee/MEMORY_REDESIGN_seat1_retrieval_audit.md |
| Q10 | y | What incident motivated the no-downgrade guidance on browser read errors? | `confusing error \(HTTP 415\)` | UNTRUSTED_INPUT_SURFACE.md |
| Q11 | **n** | Is semantic search over memory built, and what holds it back? | `Step 7 . bge-m3 retrieval.{0,100}deferred` | MEMORY_SYSTEM.md |
| Q12 | **n** | Are we scheduling sprints to split up server.py? | ``No standalone .extraction sprints`` | MAINTENANCE_PROTOCOL.md |
| Q13 | **n** | What problem was the auto-model router designed to fix? | `burned by trivial requests being routed to Opus` | DISPATCH_AND_ROUTING_ANALYSIS.md |
| Q14 | **n** | Why does the live chat show only a tiny tail of the conversation while the transcript on disk is intact? | `Key fact:\*\* no data is lost` | CHAT_REDUCER_REDESIGN_SPEC.md |
| Q15 | **n** | Where is the clayrune.io installer site hosted? | `host on \*\*Cloudflare Pages\*\*` | HOSTING.md |
| C1 | | How do we configure Datadog dashboards for Clayrune server latency? | none (0 matches for `datadog`) | none |
| C2 | | How do we set up PagerDuty on-call escalation for steward failures? | none (0 matches for `pagerduty`) | none |

Hand keywords used for the grep/memory columns (picked before running): Q01 CI/Python/PATH; Q02
commits/master/worktree; Q03 Worker/tunnel/cost; Q04 Origin/header/human; Q05 page load/requests; Q06
Higgsfield/route; Q07 SQLite/migration/FTS; Q08 tests/click/rename; Q09 dark/files/expansion; Q10
downgrade/415/curl; Q11 Step 7/semantic/deferred; Q12 extraction/server.py; Q13 router/Opus/Haiku; Q14
chat/reducer/transcript; Q15 clayrune.io/hosting/Cloudflare.

Paraphrase probes (Q01, Q02, Q05, Q08, Q10, Q12, Q13, Q14) were re-worded with no shared keywords, e.g. Q08:
"Hundreds of automated checks passed yet the UI was broken, why?"

## Appendix B. Reproduction notes

- Chunker: split on `#{1,4}` headings, pack paragraphs to 1,500 chars, prefix `title > heading`. Noise
  set excluded from the primary index: `_journal/`, `_campaign/`, `night-review-*`, `gcp-cost-review-*`.
- FTS5: `fts5(head, body, file UNINDEXED, tokenize='porter unicode61')`, `-bm25(c, 3.0, 1.0)`, query =
  question stop-word-stripped, tokens ≥3 chars, OR-joined.
- Dense: `gpahal/bge-m3-onnx-int8`, `dense_vecs` output (CLS), L2-normalised, cosine, 512-token truncation,
  best chunk per file. Hybrid: reciprocal rank fusion, k=60.
- Scratch processes started for this work were mine only and have exited (embed job; the duplicate launch
  was stopped by PID).
