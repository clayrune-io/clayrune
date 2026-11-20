# memory-eval — does the memory system actually retrieve anything?

Read-only probes behind the memory redesign (`docs/MEMORY_REDESIGN_2026-08.md`,
backlog `dae8d6e7`). Nothing here edits a memory note. A few probes write
their own sidecar: `delivery_backfill.py` and `delivery_review.py` (delivery
counters / review state), `fact_canary_probe.py` (its state file),
`eval.py --notify` (the health rules' state, outside the memory directory), and
`fts_recall_probe.py` builds a cold-search database beside the index.

```bash
python tools/memory-eval/pin_suite.py           # freeze the task suite (once)
python tools/memory-eval/pin_suite.py --check   # prove it is deterministic
python tools/memory-eval/eval.py                # the invariant + corpus report
python tools/memory-eval/eval.py --inject-canary  # prove the invariant can fail
python tools/memory-eval/retrieval_probe.py     # do agents open memory at all?
python tools/memory-eval/scorer_ab.py           # old TF vs live BM25
python tools/memory-eval/fact_canary_probe.py   # MC-971: does THIS fact reach the user's own wording?
```

## Structure is not the same question as delivery (`fact_canary_probe.py`, MC-971)

`eval.py` + `delivery_review.py` measure the corpus's SHAPE (note reachability,
delivery counts). Neither would have caught the Codex top-up miss (MC-964):
the fact was never uncited-and-unreachable by M1's definition, and it never
showed up as a promotion gap, because the miss was about ONE specific fact and
ONE specific way the user phrases things, not about the corpus overall.

`fact_canary_probe.py` asks the narrower question directly: pin real facts
(`data/memory-eval/fact_canaries.jsonl`, gitignored) each with the date they
were recorded and 2-3 queries in the user's own wording (never the fact's own
words — an oracle query that reuses the fact's vocabulary proves nothing
about whether the user's phrasing reaches it), and check whether the unit that
should answer lands within the live per-turn delivery depth
(`max(topk*2, topk+4)` — the same count `agent_routes.py` actually asks for,
not a hardcoded topk`).

The Codex top-up canary is pinned to the ORIGINAL archive record (a `needle`
match on "after adding codex credits" in `MEMORY_ARCHIVE.md`), not to the
topic note the user's complaint caused someone to write two minutes later — the
topic note already ranks fine today, which would silently launder the exact
failure this canary exists to keep visible. **It must fail until Step A
(content-containment dedupe) ships; a pass means the harness stopped
measuring the real defect.**

Also here: `contradiction_pairs.jsonl` (a stale fact vs. its correction —
passes only when the correction outranks the stale line for a shared query)
and a real-world miss scan over the last 7 days of user messages for "I told
you" / "we already" / "you forgot" / "as I said". **That scan strips injected
context first** (`_strip_injected_context`) — the first real run flagged a
`--- STANDING POSITIONS ---` reminder block whose own prose said "because we
already ARE that vault shape" as a live miss. That block is harness
boilerplate, not the user, and `tests/test_fact_canary_probe.py` pins the
regression.

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

## The health rules (`memory_health.py`, called by `eval.py`)

The invariant count alone stopped working as an alarm once auto-written mint
notes filled it: it read as known noise, and hand-written notes went
unreachable inside it for weeks with every weekly entry ending "no email". So
`eval.py` now prints the count split by who wrote the note (`M1 by kind:
hand-written | positions | mints | system files`, the old total stays for the
trend line) and hands the run to four fixed rules. "System files" are the
session log and the continuity record, which the memory system rewrites itself:
the invariant lists them as topic files, and they are nobody's note. Each threshold is a named constant with its
reason beside it in `memory_health.py`:

| rule | finding when |
|---|---|
| (a) hand-written unreachable | any note that is not `mint_*`, `position_*` or a system file is in the M1 list |
| (b) planted-fact recall | more than 10 points under the stored baseline in 2 consecutive `--notify` runs |
| (c) contradiction pass rate | 0 of the pinned pairs pass |
| (d) memory snapshot | the newest one under `~/.clayrune/memory-snapshots` is older than 26 hours, or there is none |

```bash
python tools/memory-eval/eval.py             # prints what it would send, records nothing
python tools/memory-eval/eval.py --notify    # mails each new finding, records it as raised
python tools/memory-eval/memory_health.py --set-recall-baseline <percent>   # once, for rule (b)
```

One mail per rule, through `tools/night-review/send_mail.py`, subject
`[Clayrune memory] <finding>`, body under 200 words with the verdict first. A
finding is mailed once and stays quiet until it clears and recurs; the hash and
flagged-set are `delivery_review.py`'s, reused. Rule (a) keeps one hash per
note, so a new note going dark behind two known ones is still reported.

For an unreachable hand-written note the mail carries a drafted fix: the
best-ranked reachable hand-written note that should link to it. **The run
writes nothing into the memory directory**; a person applies the link. Its only
file is `data/memory-eval/health_state.json` (gitignored), which also holds the
recall baseline. Without a stored baseline rule (b) prints `NOT MEASURED`, it
does not adopt the current figure, because adopting a figure that has already
slid would hide the slide.

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
