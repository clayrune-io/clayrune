# memory-eval — does the memory system actually retrieve anything?

Read-only probes behind the memory redesign (`docs/MEMORY_REDESIGN_2026-08.md`,
backlog `dae8d6e7`). Nothing here writes to the memory corpus.

```bash
python tools/memory-eval/pin_suite.py           # freeze the task suite (once)
python tools/memory-eval/pin_suite.py --check   # prove it is deterministic
python tools/memory-eval/eval.py                # the invariant + corpus report
python tools/memory-eval/eval.py --inject-canary  # prove the invariant can fail
python tools/memory-eval/retrieval_probe.py     # do agents open memory at all?
python tools/memory-eval/scorer_ab.py           # old TF vs live BM25
```

## NEVER import `server` from a probe

`_harness.py` wires `mc.memory` directly. Use it. The old `scorer_ab.py` called
`importlib.import_module("server")` merely to initialise the module, and that
import has a side effect that was traced end to end:

```
import server -> mc/blueprints/remote_routes.py imports mc_remote
  -> tunnel autostart daemon thread -> supervisor
  -> mc_remote/cloudflared.py reap_orphans() with keep_pid=None
  -> kills every cloudflared PID in the shared ledger
```

So running the eval killed the operator's tunnel. Survivable by hand; fatal for
the nightly eval the plan proposes. `_harness.assert_no_server()` is the gate,
and it checks `sys.modules` rather than grepping output — the reaper runs on a
daemon thread, so a grep races it and can pass while the import really happened.

The underlying `reap_orphans(keep_pid=None)` behaviour is a remote-access defect,
filed separately. This directory only stops the probes from tripping it.

## Measure the signature production actually uses

`_harness.live_signature()` returns `(read_floor_topk, read_floor_link_expand)`
from live config, because `agent_routes.py` reads both per context build. An
earlier pass hardcoded `topk=3` and omitted `expand=` entirely, and therefore
reported **27–30 dark files against a live figure of 15** — it was measuring a
system nobody runs. If you write a new probe, take the signature from the
harness.

## The invariant (`eval.py`)

> **No note may be both UNCITED and UNREACHABLE.**

A note reaches an agent by two channels only: the always-loaded index cites it,
or the read floor ranks it up for some real task. The third apparent channel —
the agent going and opening a file — is measured at 5% of sessions with 66 of 76
notes never opened once, so it cannot be relied on. A note with neither channel
is not archived, it is deleted with a receipt on disk.

`--inject-canary` copies the corpus, adds an uncited note nothing can rank, and
expects the violation count to rise. A check that cannot fail is not a check.

## Results

`retrieval_probe.py`, 259–268 sessions: topic file opened in **5%** of sessions,
search run in **3%**, 66 of 76 files never opened. The pull path is dead; the
read floor does effectively all the work.

`scorer_ab.py`, 180 pinned tasks, at the live signature:

| scorer | reachable | dark | zero-result | top-3 share |
|---|---|---|---|---|
| old (raw term frequency) | 19 / 76 | 57 | 0% | 94% |
| BM25 | 60 / 76 | 16 | 0% | 32% |

`eval.py`, before and after S3 (`read_floor_topk` 3 → 6), same pinned suite:

| topk | reachable | dark | M1 violations | p95 |
|---|---|---|---|---|
| 3 | 59 / 74 | 15 | 1 | 168 ms |
| 6 | **66 / 74** | **8** | 1 | 171 ms |

## Condense deadband — settled, do not re-derive

Two reviews gave contradictory accounts. Both were half right, and `eval.py`
prints this every run:

| half | trigger | floor | outcome |
|---|---|---|---|
| byte | 24576 | 23552 | **suppressed** — the floor evicts first, so it never fires |
| line | 160 | 185 | **live** — the trigger fires before the floor bites |

Any fix must cover the **line** trigger too. A byte-only fix corrects the half
that already works and leaves the broken half broken.

## Three traps, all already paid for

**Don't scan transcripts for the injected read-floor block.** It is delivered via
`--append-system-prompt-file` and transcripts do not record the system prompt. A
probe that greps for `RELEVANT MEMORY (auto-surfaced` scores only the *current*
session echoing itself back and returns a confident wrong answer. Replay the
function instead.

**Exclude trivial follow-ups.** Taking the first user message of every session
verbatim includes openers like `ok` and `continue`. Those are not dispatched
tasks and they inflated an early "29% of tasks surface nothing" figure that was
pure artifact — the real zero-result rate is 0%. `MIN_TASK_CHARS` is that filter.

**If both arms of an A/B look identical or impossible, suspect your harness.** A
reviewer wired `CLAUDE_HOME` to `~/.claude` when `_native_memory_path` expects the
`projects` dir; `_memory_search` returned `[]` for everything and reachability
came out 0/74 in *both* arms. That impossibility is what exposed the bug.

## Caveat on reachability numbers

Replay uses the corpus at its **current** state, not as it stood when each
session ran, so recently-written notes could not have been surfaced then. That
makes reachability an **upper bound**. It biases every arm identically, so
comparisons are sound even though each absolute figure is optimistic.
