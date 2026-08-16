# Seat 5 — The design: residency policy, and facts that stop being true

**Workstream:** `ws_005` · **Date:** 2026-08-15 · **Backlog item:** `dae8d6e7`
**Scope:** analysis and written design only. `mc/memory.py`, `MEMORY.md` and the
memory dir were not modified. Probes in `_scratch/seat5/` (gitignored, read-only).

**The design itself is `docs/MEMORY_REDESIGN_2026-08.md`.** This file is the
derivation: what I measured myself, what I took from seats 1–4 without
re-deriving, the two things I got wrong and corrected, and the reasoning that
does not belong in the deliverable.

---

## 1. What I took as settled, and did not re-measure

Per the two-phase protocol, I built on the seats rather than repeating them.

| taken as given | source | status |
|---|---|---|
| pull path dead — 5% / 3%, 66/76 never opened | brief; seat 4 reproduced exactly on 264 sessions | settled |
| live dark set = 15, not 30 (`scorer_ab.py` omits `expand=`) | seats 1 and 4, independently | settled |
| `read_floor_topk` live at 3; code default 6; `config.json` shadows it | all four seats | settled |
| 14 of 15 dark files already carry a resident curated pointer; exactly 1 note reachable by neither path | seat 4 §3 | settled |
| curated region is **not** in `_mem_corpus` | seat 3 flagged, seat 1 confirmed in the E2E audit | settled |
| `fold` is an insert-only pump; `curated_rewrite_forbidden_v1` blocks removal | seat 2 F3 | settled |
| structured condense cannot fire (floor = cap−1024, trigger > cap) | seat 2 F4 | settled |
| watermarks cost 660 B each of always-loaded index | seat 2 F6 | settled |
| archive is a live index — 96.5% of units, 15–20% of slots | seat 2 §6.2 | settled |
| link expansion supplies 37% of slots; 20 link-orphans | seats 1 §9, 4 §4.4 | settled |
| new state → memory dir, non-`.md` extension; not `DATA_DIR`, not `MEMORY.md` | seat 2 §9 | settled |
| no purchase warranted; rerank measured worse locally | seats 3, 4 §4.2 | settled |

---

## 2. What I measured myself

All on this box, 2026-08-15, production functions imported (never reimplemented),
`server` wired first per seat 2's trap note.

| # | measurement | script | result |
|---|---|---|---|
| M1 | curated line inventory | `admit.py` | 96 pointer lines / 16,023 B; 59 linked / 37 link-less |
| M2 | link-less recoverability, re-verified | `admit.py` | **31 recoverable / 1 not / 5 undecidable** (was 33/37) |
| M3 | stale resident facts | inline | **0 of 96**; 5 assert a retired state, all 5 accurate |
| M4 | memsearch incident, traced through git | inline | 8 unmarked mentions in `CLAUDE.md` for **80 days** |
| M5 | `CLAUDE.md` charter growth, 29 commits / 99 days | `claudemd_growth.py` | prohibition lines 0 → 30. **O(1) predicate falsified** |
| M6 | the 2026-08-06 forced-choice curation event | `claudemd_growth.py` | prohibition survived **1.55×** everything else |
| M7 | tiering + capped allocation | `allocate.py` | T1 17 / T2 12 / T3 67 → resident **29 lines, 5,174 B** |
| M8 | the archive-filter eviction trap | inline | **28 of 67** evicted lines would be silently dropped |
| M9 | snapshot provenance check | `charter.py` | `_scratch/mem_plain.md` is a **different project's** index |

---

## 3. The derivation, in the order it actually happened

### 3.1 Starting position — where I disagreed with seat 4

Seat 4's §3 is the most important measurement in the hivemind: the curated index
and the retrieval corpus are 93% redundant, so a dark file is usually not a
knowledge loss, and shrinking curated converts benign dark files into real losses
1:1. Their conclusion — *raise ranker reach first, shrink curated second* — is
correct and I kept it as a binding ordering constraint.

But the same section can be read as an argument *for keeping* curated, and I think
that reads the coupling backwards. **The redundancy exists because residency is
curated's only channel.** Seat 1 §8 and seat 3 §5 both establish that
`_mem_corpus` indexes only the managed half of `MEMORY.md`. So curated content is
delivered exactly one way: unconditionally, to every prompt, forever. Of course it
is redundant with the corpus — it has no other way to arrive.

Change that one fact — make evicted charter lines retrieval units — and the
redundancy stops being load-bearing. The question then stops being *how much
curated can we afford* and becomes **which lines must be delivered even when the
task does not predict them**.

That is the question the brief asked, and it is answerable.

### 3.2 The hypothesis I posted, and why I tested it rather than shipping it

I proposed: residency belongs to **prohibitions**, on an asymmetric-loss argument
(a ranking miss on a positive fact costs re-derivation; a ranking miss on a
prohibition costs the forbidden action). And I claimed this would be O(1) in
project age, because standing "no"s are a property of the operator's policy rather
than of the topic count.

The asymmetric-loss half is sound. **The O(1) half was an assertion dressed as an
argument, and this item's history is three retractions from exactly that habit**,
so I tested it before writing it down.

### 3.3 The test, and the falsification (M5)

`CLAUDE.md` is the only always-loaded, human-authored, version-controlled charter
on this box, with an explicitly stated residency bar. 29 commits over 99 days.

Prohibition lines: 0 → 30. In the mature window 2026-05-27 → 2026-08-05 they grew
at ~0.35/day against curated-pointer growth of 0.167/day. **Prohibitions
accumulate at least as fast as topics.** Hypothesis falsified.

I posted the retraction to the bus before writing the deliverable.

### 3.4 What the falsification taught, which is the actual design

The failure generalises, and that is the useful part:

> **No admission predicate can be O(1).** A predicate over a growing corpus admits
> a growing set, whatever the predicate is.

That indicts more than my rule. It indicts the item's own proposal 2 (evict on
access statistics), any relevance threshold, and any editorial bar. **O(1) is not
reachable by choosing a better test for what belongs in. It is reachable only by a
fixed budget plus a total order — admission must be competitive, not absolute.**

Which means the item's diagnosis is subtly off. It says the curated region is a
*flat* list and therefore O(topics), and proposes to fix the flatness with a tree.
The flatness is not the problem. **The absence of a cap is.** A tree with no cap is
still O(topics); a flat list with a cap is O(1). The item's own proposal would
have produced an O(1) *front page* only by pushing the growth into leaves nobody
opens — which is Constraint P again, from a different direction.

So the predicate does not decide admission. **It decides priority inside a cap.**
That is Letta's memory-block model (seat 3, steal #6) and it is why that steal
outranks the others for this specific problem.

### 3.5 The evidence that survived — and it is better than what I lost (M6)

The same `CLAUDE.md` history contains a genuine forced-choice curation event on
2026-08-06: a human halved an always-loaded file (`docs/CLAUDE_MD_ARCHIVE.md`
records what moved and why).

Prohibition lines survived at **73.7%** against **47.7%** for everything else —
**1.55×** — and prohibition share of the file rose from 7.3% to 10.8%.

**When a human was actually forced to choose what keeps permanent residency, what
survived was disproportionately prohibition content.** That is a much stronger
warrant for the priority order than a growth claim would have been, because it
observes the decision rather than predicting it. The rule I am proposing is the
one Ron already applied under pressure; the design just makes it mechanical and
repeatable.

### 3.6 Why the rule is mechanical rather than editorial

The brief was explicit that editorial judgement is the least-trusted piece of the
current design. Each tier is a regex or a replayable measurement:

- **T1** — regex over the line text.
- **T2** — for a linked line, membership in the dark set over the pinned suite
  (deterministic replay of `_memory_search`). For a link-less line, the df ≤ 3
  distinctive-token concentration test (M2's method).
- **T3** — the complement.

Nobody has to decide whether a line is *important*. The rule decides whether
evicting it is **safe**, and among the unsafe ones, which is most costly to miss.
That distinction is the whole trick: *importance* is not mechanically decidable
and pretending otherwise is how the current design got its least-trusted
component. *Safety of eviction* is mechanically decidable, and it is sufficient.

### 3.7 The result (M7), and why the headroom matters

| tier | lines | bytes |
|---|---|---|
| T1 prohibition | 17 | 3,203 |
| T2 non-deliverable | 12 | 1,971 |
| T3 evictable | 67 | 10,849 |
| total | 96 | 16,023 |

At `charter_byte_cap = 8192`: **29 lines / 5,174 B resident (63% of cap), 3,018 B
headroom, 67 lines / 10,849 B evicted, ~2,712 tokens/prompt saved.**

The cap is **not binding today**, and that is the argument for installing it now.
It costs nothing at present and it is the only thing that makes the region O(1)
later. Installing a cap after it binds means installing it during a forced
choice — which is how you get a rushed, editorial, unverifiable cut.

`charter_byte_cap = 8192` is a **proposal, not a measurement**. It is chosen so
T1+T2 fit with ~37% headroom while restoring the managed region to ~15 KB
(≈47 entries, ~2 weeks of session log, against today's ~16 entries / ~3 days,
which seat 2 measured as already full). It is a budget decision and therefore
Ron's.

### 3.8 The trap I walked into (M8)

I then tried to specify the eviction target and found the sharpest thing in the
whole design.

`mc/memory.py:568-570` splits the archive into units with
`ln.strip().startswith('- [')`. **37 of 96 curated lines start with plain `- `** —
they are exactly the link-less ones, where the line *is* the knowledge. Of my own
67-line eviction set, **28 would be silently dropped**: not resident, never a
unit, no error, no log. It would look like a successful migration.

The fix is to stamp evicted lines into archive form (`- [YYYY-MM-DD] …`), which
matches the existing filter with no change to `_mem_corpus`, no new unit class,
and no new `avgdl` class — answering seat 3's caveat 4/5 and seat 2's warning
about new `.md` files in the memory dir. The date stamp is also seat 3's steal #3
(temporal hygiene) obtained free at the only moment it is knowable.

**Two notes on this that matter beyond my design.** First, it applies to seat 4's
recommendation #9 and to the item's own proposal equally — any evict-curated
scheme hits it. Second, it partially qualifies the reassurance from M2: *31 of 37
link-less lines are recoverable from a single archive unit* is about the **fact**
surviving elsewhere, not about the evicted **line** being indexed. Both must be
true and today only one is.

Hence the hard gate: unit-count delta must equal lines evicted, or the stage rolls
back.

### 3.9 The bitemporal question, and why the brief's example proves the opposite (M3, M4)

The brief asserts our index carries stale entries and cites the retired memsearch
line. I checked all 96 resident lines. **Five assert a retired/disabled/deferred
state and all five are accurate.** The memsearch line is a correctly-marked
tombstone: *"verified non-functional; do NOT use it."*

Then I traced the incident through git. From 2026-05-18 to 2026-08-05,
`CLAUDE.md` carried **8 mentions of memsearch with zero retirement markers**,
including line 281's live instruction to *"query memsearch for the topic before"*.
memsearch was retired 2026-05-18. That is **80 days** of an always-loaded file
instructing every agent to use a subsystem the memory system had already correctly
marked dead.

So the failure was never staleness in the store. It was a **contradiction between
two always-loaded surfaces that nobody compared**. Both had valid timestamps.
Both were internally consistent. `valid_to` does not detect that.

This is the cleanest kind of design answer: the expensive mechanism is aimed at a
failure we do not have, and the failure we *do* have is caught by a grep. It also
survives Constraint P, which the bitemporal version does not — a `valid_to` field
must be **read** to matter, and reading is the 5% path. **A bitemporal field is a
fact we would be deleting.**

Rejected, on four grounds (wrong target / no demand / cost / we already have the
push-delivered half). Full argument in the deliverable §6.

### 3.10 The contamination (M9)

While building the per-snapshot linked/link-less split I got 0 of 105 pointer
lines carrying a link from `_scratch/mem_plain.md` — impossible for our index.
It is a **different project's** memory file: 42 trading-content hits against 11
mission-control, headings `## Incidents & Lessons` / `## Session Log` rather than
ours, and a link count of zero because that older format has no links.

Seat 2 used it as their 2026-07-31 datapoint and inferred a −2,853 B human
curation event from it. Their **adopted 28 B/day rate survives** (it came from the
clean 08-09 → 08-15 window against a genuine backup, which I reproduce exactly),
but the sawtooth inference does not — and that removes the only evidence that
humans periodically drain the region. Both curated datapoints we actually hold go
up, and seat 2's F3 is now the only measured directional force on curated.

Reported to the bus. Not a criticism of seat 2 — the file is named `mem_plain.md`,
sits in our own `_scratch/`, and dates plausibly. I caught it only because my rule
needed a per-snapshot link split and 0-of-105 was impossible.

---

## 4. Answers to the four questions, in one place

**(a) What earns permanent residency.** Nothing earns it as a property. Residency
is a **capped competitive allocation**: hard `charter_byte_cap`, priority T1
(prohibition, by lexicon) → T2 (non-deliverable, by replayable measurement) → T3
(evicted). Applied today: **29 lines / 5,174 B stay, 67 lines / 10,849 B leave,
~2,712 tokens/prompt saved, 3,018 B of headroom.** O(1) comes from the cap, not
from the predicate — a predicate cannot deliver it, which I established by
falsifying my own (§3.3–3.4). The 37 link-less lines do **not** block this: 31 of
37 recoverable, reproducing the 2026-08-05 retraction (§3.8 for the caveat that
matters).

**(b) How to kill the dark files.** They are 15, not 30, and none is
vocabulary-orphaned. Class A (14 of 15) needs **nothing** — correctly dark given
residency. Class B (1) and Class C (5 orphans of 20) need **one wikilink each**,
zero resident bytes. Class D needs **one config key** (`read_floor_topk` → 6).
Class E is **empty — delete nothing**. Plus three free ranker constants
(`b` → 1.00, title ×1, archive quota) worth another ~9 files at zero token cost.
Ranking reaches essentially the whole dark set; note rewriting reaches ~none of it.

**(c) Bitemporal facts.** **Rejected**, and not only on cost. Zero stale resident
facts; ~12 superseded facts corpus-wide; the cited example proves the memory
system was *right* and `CLAUDE.md` was wrong for 80 days; the machinery costs
LLM + embedding calls per write plus a graph DB and shipped with its own
valid-time plumbing broken. Replaced by a **required tombstone convention** plus a
**cross-surface contradiction grep** in the nightly eval — which would have caught
memsearch in May. `modified:` frontmatter kept as hygiene, explicitly not sold as
bitemporality.

**(d) Is recall evaluated continuously.** **Yes, and it must be**, because the
archive grows 2,691 B/day and shifts `avgdl`/IDF, so recall can regress with no
code change. Four checks; hard gates are the **uncited-and-unreachable
invariant**, **zero-result = 0%**, and the **evicted-line unit-count assertion**.
Reachability and concentration are soft. The behavioural gold set is **reported and
never gated** (n=9, contaminated). The pinned immutable suite is what makes any of
this able to fail.

---

## 5. Safety rails — how the design stays inside them

**Authority guard.** The design adds exactly one automated capability: *evicting a
charter line into the archive*. It cannot author, delete, or promote a charter
line. The machinery can only ever **reduce what the agent is told**, never expand
**what the agent may do**. There is no path by which it grants autonomy, removes
an approval gate, or widens a capability set. T1 being the prohibition tier means
the class the guard exists to protect is the *last* thing evicted.

I also note the loop that the design deliberately does **not** close: `fold` is
already machine-driven. If eviction were machine-driven *and* charter authorship
were machine-driven, the resident region would become a fully autonomous
write-read loop. Authorship stays human, so it does not.

**Durable "no".** Preserved mechanically by the tombstone convention: a retired
fact is rewritten in place, never deleted — because deletion is what lets a
prohibition be silently reversed, the same shape as `_suppress_artifact`
re-proposing `preference-1ba8d678`. An evicted "no" is still in the archive and
still retrievable; a deleted one is gone. This is also seat 4's Class-E argument
for `arch_memsearch.md`, arrived at independently.

**Human on one side of every loop.** The eval **escalates and never acts**. Hard
gates fail a build; soft gates warn to the run log and the item journal; Check 3
raises contradictions to the human queue and never edits `CLAUDE.md`. Per the
standing rule, unattended output goes to `docs/_journal/<item-id>-<slug>.md`,
never to the backlog note API.

**DATA_DIR pollution.** Nothing new in `data/projects/`. Residency/eval state →
memory dir with a **non-`.md`** extension (satisfies both the `load_projects()`
glob and `_mem_corpus`'s `*.md` glob — a `.md` there would become a retrieval unit
and pollute class `avgdl` and global IDF). Pinned suite → `data/memory-eval/`,
outside `DATA_DIR`, **gitignored** (verbatim operator task text), and
**`build-macos.spec` must not package it** — the `SHARED_RULES.md` edge, where
gitignoring was insufficient because a build spec bundled the file anyway.
**Nothing new inside `MEMORY.md`**; watermarks move *out* rather than the pattern
being extended.

**LangMem-style procedural memory** (the agent editing its own instructions) is
rejected on safety, not scale — seat 3 reached this independently and it is the
exact capability `_authority_violation()` refuses.

---

## 6. Where I could be wrong

1. **`charter_byte_cap = 8192` is unmeasured.** Reasoned from the managed-region
   deficit, not derived. If Ron wants a longer session-log horizon the cap should
   go *down*, and T2's 1,971 B is the first thing that should shrink (via §3 of
   the migration, not by fiat).
2. **The 540-day cap-binding estimate** rests on a 6-day, one-pointer growth
   signal. Order of magnitude only, and I say so in the deliverable.
3. **The prohibition lexicon is a regex and regexes have edges.** It matched 17 of
   96 lines. I did not measure its precision/recall against a hand-labelled set —
   that is a real gap, and the mitigation is that a false negative merely evicts a
   line into a channel where it is still retrievable, while a false positive costs
   ~188 bytes of headroom. **The rule fails soft in both directions**, which is why
   I am comfortable proposing it unmeasured. If it failed hard I would not be.
4. **Adding ~67 short units to the 2,476-unit archive class shifts its `avgdl`.**
   Reusing the existing class avoids inventing a fourth (seat 3's caveat), but it
   does not make the perturbation zero. Stage 8's tranching makes it observable.
5. **I did not measure whether evicting T3 actually degrades agent behaviour.**
   Nobody in this hivemind measured behaviour; seat 4 tried and reported the
   attempt as a negative result. The invariant is a proxy for harm, not a
   measurement of it. Stated rather than buried.

---

## 7. Artifacts

`_scratch/seat5/` — read-only, gitignored:

| file | what it does |
|---|---|
| `charter.py` | per-snapshot curated inventory; caught the `mem_plain.md` contamination |
| `claudemd_growth.py` | 29-commit `CLAUDE.md` history; the falsification (M5) and the curation-event survival rates (M6) |
| `admit.py` | link-less recoverability re-verification (M2), stale-fact scan (M3) |
| `allocate.py` | tiering + capped allocation (M7) |
| `allocation.json`, `charter_rows.json`, `claudemd_rows.json`, `admit.json` | outputs |

Reused without modification: `_scratch/memdark/classify.json` (seat 4's dark set
at topk=6 + expand=2), `_scratch/memory_backup_2026-08-09/` (verified genuine).

`mc/memory.py`, `MEMORY.md` and the memory directory were not modified.
