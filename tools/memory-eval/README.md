# memory-eval — does the memory system actually retrieve anything?

Two read-only probes that answer the question the standing gate
(`decision_step7_semantic_search_deferral`) asks for — **build search-precision
telemetry before leaning harder on retrieval** — without building a telemetry
subsystem. The signal is already on disk in the Claude CLI transcripts.

```bash
python tools/memory-eval/retrieval_probe.py    # do agents open/search memory at all?
python tools/memory-eval/scorer_ab.py          # how good is the read-floor ranking?
```

Both are read-only and write nothing.

## `retrieval_probe.py` — the tool-call side

Counts real `Read` / `Grep` / memory-search tool calls against the memory corpus
across every transcript. Answers "do agents follow the front-page pointers?"

Baseline, 2026-08-05, 209 mission_control sessions: topic file opened in **6%**
of sessions, memory search run in **3%**, 11 topic-file opens total. The
on-demand paths are effectively dead; the read floor does nearly all the work.

## `scorer_ab.py` — the ranking side

`_memory_search` is a **pure deterministic** ranked search: same task text +
same corpus → same top-K. So the read floor can be replayed exactly against the
first user message (= dispatched task) of every past session, and two scorers
compared on identical inputs. It reimplements the old term-frequency scorer
verbatim alongside the current one, so the comparison is measured rather than
recalled.

Result that motivated the BM25 rewrite (142 real tasks):

| scorer | topic files reachable | dark | top-3 units' share of slots |
|---|---|---|---|
| old (raw term frequency) | 17 / 75 | 58 | 93% |
| BM25 | 40 / 75 | 35 | 53% |

Under the old scorer the three files winning almost every query were exactly the
three **largest** — length was beating relevance.

## Two traps, both already paid for

**Don't scan transcripts for the injected read-floor block.** It is delivered
via `--append-system-prompt-file` and transcripts do not record the system
prompt. A probe that greps for `RELEVANT MEMORY (auto-surfaced` scores only the
*current* session echoing itself back, and reports a confident, wrong answer
(6% of sessions, 3 files). Replay the function instead — that is what
`scorer_ab.py` does.

**Exclude trivial follow-ups.** Taking the first user message of every session
verbatim includes sessions whose opener is `ok` or `continue`. Those aren't
dispatched tasks, and they inflated an early "29% of tasks surface nothing"
figure that was pure artifact — the real zero-result rate is 0% for both
scorers. `MIN_TASK_CHARS` in `scorer_ab.py` is that filter.

## Caveat on every number here

Replay uses the corpus at its **current** state, not as it stood when each
session ran, so recently-written notes could not have been surfaced then. That
makes reachability an **upper bound**. It biases both scorers identically, so
the A/B comparison is sound even though each absolute figure is optimistic.
