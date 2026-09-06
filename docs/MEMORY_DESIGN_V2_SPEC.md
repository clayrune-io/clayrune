# Clayrune Memory Layer — V2 Design Spec

Status: **DRAFT v2 (2026-09-06)** · Author: hivemind `hm_b02a8f17`
(workstreams ws_001–ws_008), synthesised after a four-document adversarial
review. Companion to — and **not** a re-derivation of —
`docs/research/MEMORY_GRAPH_MEASUREMENT.md` (graph shape, 19 vaults) and
`docs/research/OKF_AGENT_MEMORY_COMPARISON.md` (validated format vs runtime).
Extends `docs/MEMORY_SYSTEM.md`, which stays the map of the shipped system and
whose open item 6 (retrieval bound to the session, not the turn) this spec
closes. This is a **design spec, not an implementation**: no code, no retrieval
behaviour change, and no vault edit is authorised by this document.

> **What this document is for.** On 2026-09-06 an agent re-investigated a memory
> design this project had already completed on 2026-08-15/16 — 346 KB of design
> and committee audit, including a 14-row table of explicitly rejected
> alternatives with reasons. The memory layer did not surface any of it. The
> chain broke at five independent points, each sufficient on its own (§2).
> **Every mechanism in this spec exists to make that trace impossible, and §2.4
> states plainly which parts of it are eliminated and which are only made less
> likely.**

> **v2 changelog — twenty must-fix items from the adversarial review are applied
> inline, not appended.** The four upstream proposals did not compose: they
> assumed two write postures for one act, three overlapping resident negation
> surfaces, five names for one expiry concept, two incompatible supersession
> mechanisms, and two mutually exclusive positions on whether a usage signal may
> exist anywhere in the system. Each is resolved in place; the register in §15
> maps every item to the section that closes it. **The arithmetic that changed:
> the composed always-loaded ceiling is ~79.1 KB, not the 58.9 KB the budget
> workstream advertised, and the headline saving is 8.5%, not 32% (§9.3). The
> saving is not the argument; O(1)-in-corpus-size is.**

---

## 0. The three requirements, verbatim

The design is judged against these three and nothing else. Each is stated with
the measurement that shows the current design fails it.

**R-1 — Nothing unbounded in the prompt.** *Measured:* `MEMORY.md` on the
reference vault is 23,443 B against a 24,576 B budget, of which 19,719 B is the
curated region and 3,724 B the auto-managed session log
(`MEMORY_GRAPH_MEASUREMENT` §6a). Across 19 vaults the always-loaded index totals
230,176 B — a **sum across projects, never a per-prompt cost**, and this spec
must not be read as claiming otherwise.

**R-2 — Discovery must not depend on being listed.** *Measured:* 46% of the 235
topic nodes have no inbound edge, 23% have no edge at all, and 20 are unreachable
from any curated index line at any hop depth (`MEMORY_GRAPH_MEASUREMENT` §4).

**R-3 — Structure must be enforced, not requested.** *Measured:* 17 broken
wikilinks corpus-wide, 9 of which point at files that exist; 94% of 258 edges
untyped or see-also; **exactly one `supersedes` edge in the entire corpus**
(`MEMORY_GRAPH_MEASUREMENT` §3, §7). `tools/memory-link-check.py` has zero
callers anywhere in the repo and its own docstring says 6 broken links.

---

## 1. The problem, stated from measured evidence

Three corrections to the brief come first, because the design that follows is
shaped by them and a reader who skips this section will read §9 as an
overreaction to the wrong number.

### 1.1 R-1 — the unbounded thing is the curated index, not the session log

The brief says the auto-managed session log grows forever and shares a fixed
~24 KB budget with the curated index. The first half is true of the **archive
file on disk**, not of the prompt, and the second half has a direction the brief
does not state.

`_over_floor` (`mc/memory.py:2025-2030`) fires on `lines > 185` **or**
`bytes > 23,552`, and its docstring is explicit: *"Both floors evict managed
entries only (oldest first, verbatim to archive) — the curated region is never
touched by machinery."*

**The two regions do not share a budget. The curated index takes what it wants
and the session log pays for all of it.** The log expands into whatever curated
leaves and is then evicted from the oldest end. Six managed entries in 3,724 B is
not distress; it is the designed steady state.

The item that is genuinely unbounded in the prompt is therefore the **curated
index**, and it is unbounded in the worst way — one pointer line per note,
forever, at a measured **185 B per line** (186 in-vault pointer lines / 34,451 B
corpus-wide):

| notes | index bytes at 185 B/line | vs 24,576 B budget |
| ---: | ---: | ---: |
| 44 | 8,140 | 33% |
| 130 | 24,050 | **98% — full** |
| 1,000 | 185,000 | 7.5× over |
| 5,000 | **925,000** | **38× over** |

**This has already broken, silently, and the corpus records how.** The reference
vault holds 86 topic files and only 67 pointer lines, and **59% of curated index
bytes corpus-wide are prose with no link target at all**
(`MEMORY_GRAPH_MEASUREMENT` §6b). The one-line-per-note convention stopped holding
somewhere around 130 notes; what filled the space instead was prose that no
demotion mechanism at any hop depth can remove, because there is nothing at the
other end of it.

**Condition 1.** The spec's R-1 target is the curated index. The session log still
leaves the prompt (§9.1), but on the evidence of §9.2 — it is 87% of the corpus
and earns 5.6% of read-floor slots — not because it is the unbounded item.

### 1.2 R-2 — 59% of the index is not a failing pointer index, it is a knowledge cache

Set the 59% against `mc/memory.py:1408-1430` and the docstring at
`mc/memory.py:1497-1501`: **the curated region of `MEMORY.md` is not in the
retrieval corpus at all**, excluded by construction because the CLI auto-loads it.

That gives the sharpest available statement of R-2:

> 59% of the curated index is knowledge that exists nowhere else, and it is the
> only content in Clayrune that is resident in every prompt and simultaneously
> unreachable by every retrieval path. It cannot be ranked, hit by BM25, reached
> by a hop, or found by the cold FTS tier. Its only delivery mechanism is being
> resident. It is not pointer-dependent — **it is the pointer.**

Two jobs follow, with opposite answers, and conflating them is what makes the
migration look like a 235-note audit when it is not (§11):

- **Job A — the 41% that are pointers. Demotable.** 85 of 86 topic files in the
  reference vault were delivered at least once over 349 read-floor tasks, and 196
  of 235 nodes corpus-wide sit at hop 0. The seed-set measurement prices removal
  at **−7,091 B (−59% of pointer bytes) at the one hop already shipped**
  (`MEMORY_GRAPH_MEASUREMENT` §5).
- **Job B — the 59% that are not pointers. Not demotable at any depth.** Deleting
  the line deletes the knowledge. The only correct move is to **mint each one into
  a note**, converting resident-and-unrankable bytes into retrievable-and-evictable
  units. That is the single transformation serving R-1 and R-2 at once, and it is
  the larger half of the migration (~436 lines corpus-wide).

### 1.3 R-2 — content-addressing does not yet survive paraphrase or homonyms

Four live probes against the running search route, 2026-09-06 — queries an agent
about to re-propose a negated thing would actually issue:

| query | what came back |
| --- | --- |
| *"should we adopt a vector database for memory"* | `project_gcp_setup.md` 10.82, `discovery_bare_context_rebuild_drops_persona.md` 10.12, `agent_name.md` 8.95 |
| *"how do we stop re-doing work we already did"* | six `feedback_*` notes; nothing about memory or prior work |
| *"two-level index lazy load"* | `discovery_img_lazy_display_none_deadlock.md` 18.43, `arch_settings_modal.md` 17.40 |
| *"memory design prior work redesign"* | `project_memory_system_redesign.md` at rank 5, score 10.10 |

Row 1 asks for a thing the August design explicitly rejected, and returns nothing
about it — with `agent_name.md` at rank 3 because this project's default agent is
*named Vector*. Row 3 is the exact proposal August negated; the warm tier returns
two pure lexical collisions on *lazy* and *level*. Row 4 is the best case, and the
hit is the frozen node that says the engagement closed in May.

**The structural reason, which no tuning constant fixes:** a note is written in
the vocabulary of the person who *understood* the thing; the query arrives in the
vocabulary of the person who does *not yet* understand it. BM25 over note bodies
is necessary and not sufficient. §7.3 supplies the deterministic bridge.

### 1.4 R-3 — a check nobody is obliged to run is not a check

`tools/memory-link-check.py` fails three ways, and each kills a different remedy:

1. **No caller.** Zero references in `tests/`, in any of the seven
   `.github/workflows/`, in any hook, in the scheduler, or in `server.py`.
2. **Its own docstring is stale evidence of that.** It says six broken links; the
   measured count is 17. The tool that exists to make rot visible rotted, in the
   one field a reader would trust.
3. **A shipped skill already instructs an agent to run it.**
   `data/skills/builtin/mc-position-review/SKILL.md` names it as the model of cheap
   verification. The instruction is already deployed to every position-review
   cycle, and the count still went 6 → 17 in 22 days. **Adding more instruction is
   a remedy this system has already tried and measured.**

And CI cannot be the answer: the vault lives outside the repo, per-install, so a
CI runner has no vault to check. **Any enforcement must sit in the runtime — at
the write, in the corpus build, or in a local scheduled job.**

### 1.5 The finding that decides where enforcement can go: there is no write path

Every Clayrune writer that touches a vault directory:

| writer | target |
| --- | --- |
| `write_continuity` (`mc/memory.py:1102`) | `continuity.md` |
| `write_position` / `delete_position` (`:1317` / `:1362`) | `position_*.md` |
| `_append_to_archive` (`:2046`) | `MEMORY_ARCHIVE.md` |
| `_commit_managed_entry` (`:2170`, leaf-locked) | `MEMORY.md` |
| `_gc_stale_watermarks` (`:2216`), `_condense_apply` (`:3374`) | `MEMORY.md` |
| `save_memory` / `append_memory` (`mc/blueprints/project_routes.py:1880/1908`) | `MEMORY.md` |

**Not one writes a topic file.** The 235 topic notes were written by the agent's
own `Write`/`Edit` tool straight to the filesystem, or by the engram MCP server —
**178 of 235 (77%)** carry engram's `metadata: node_type / originSessionId` block,
which no Clayrune code emits or reads.

**Condition 2.** "Validate on the write path" is not an edit to existing code.
**The write path must be created before it can be gated**, and something must make
the ungated path unattractive. §10 specifies both.

---

## 2. The acceptance test — the 2026-09-06 re-investigation

This spec is judged by walking one trace and naming, at every step, which
mechanism intervenes. **A design that cannot name a mechanism for every step has
not solved the failure.**

### 2.1 The trace, and the five independent breaks

| # | link | one-line statement |
|---|---|---|
| B1 | write | The artifact never got a memory **node**. 346 KB of design produced 7 archive log lines and nothing else. |
| B2 | graph | Those 7 lines are class `archive`, **structurally incapable** of holding or receiving an edge (`mc/memory.py:1466`, `:553`). |
| B3 | node freshness | The node the index *does* point at is the vault's 2nd-most-delivered file (n=129 of 349 tasks, **37%**) and it says the memory engagement **closed on 2026-05-18**. |
| B4 | tier | The prior work **is** keyword-findable — but only in the FTS transcript tier, excluded from the automatic path by design (`mc/memory_fts.py:19-27`). |
| B5 | trigger | For the actual turn there was **no retrieval event at all**: the read floor fires at fresh dispatch, and this session had been resumed for five days on a task string of *"Hi Dave"*. |
| B6 | salience | Downstream of all five: the 14 reasoned negations were never expressed as retrievable objects, only as a markdown table inside the artifact that could not be found in the first place. |

Reproduced live, read-only, 2026-09-06: `_memory_search` was called with the exact
read-floor arguments on three queries, one of them a near-verbatim restatement of
the prior design's own title. **24 delivered slots, zero surfaced the August
artifact or any of its archive lines.**

### 2.2 The nine questions, and the mechanism that answers each

| # | class | question the spec must answer | mechanism | § |
|---|---|---|---|---|
| 1 | FC-1 | A hivemind closes with an 83 KB design. What mints the node, and what happens if nothing does? | Deterministic mint trigger on artifact close, fail-open; the model's judgement is an *additive* source only | §6.5 |
| 2 | FC-6 | That design contains 14 negations with reasons. What turns each into a retrievable record, and what refuses the close if it does not? | Negation obligation with **accounted waivers** | §5.2 |
| 3 | FC-3 | A node for the same subject exists and says "closed". What links old → new, what type does the edge carry, what does the agent read at the head? | `supersedes:` declared by the successor; head substitution; materialised negation block | §6.2–§6.4 |
| 4 | FC-2 | The summary prose still lands in a log. Is the log class edge-capable, or explicitly non-retrievable? | **Waived, in writing, with the argument** | §13.4 |
| 5 | FC-8 | 22 days pass, links dangle. What refuses the write that breaks a link, and where does the error surface? | G1 fail-closed 4xx to the waiting caller; G2 raw-write deny; G3 quarantine | §10.3 |
| 6 | FC-7 | A five-day session receives a new question. What computes retrieval for THIS turn, and what is the per-turn budget? | **Server-side per-turn refresh at the four stdin-write sites**, not a hook | §9.6 |
| 7 | FC-4 | The query shares one low-IDF token with the target. What matches, given no human wrote a pointer? | Default triggers + df-exempt trigger **phrases** | §7.3 |
| 8 | FC-5 | The answer is in the FTS tier. Does the auto path reach it? | **Miss-triggered cold probe** — one hit, ~520 B, only when the warm floor has already failed | §9.4 |
| 9 | FC-9 | The agent is about to propose re-measuring the graph. What makes the negation block that, rather than sit in context being ignored as on 2026-08-23? | Negation ledger (prior) + write-act interrupt (backstop), advisory until measured | §5.3–§5.4 |

### 2.3 Pass condition

> Replaying the trace under this design, an agent receiving *"compare OKF agent
> memory to our memory layer"* **on a five-day-old session** is told, **before it
> plans**, that the August artifact exists, that it already rejected "Anthropic's
> memory tool as a replacement" and eight other external-substrate options with
> reasons, and that the wikilink layer was the mechanism that measured best —
> **without a human having written a pointer, and within a stated token budget.**

Step by step under V2:

| # | when | mechanism | effect |
|---|---|---|---|
| 1 | Aug 2026 | **Mint (§6.5)** | The hivemind close mints `design_memory_redesign_2026-08.md` **unconditionally** — a deterministic trigger, not a judgement. The overlap detector hands the caller one candidate: `project_memory_system_redesign`. |
| 2 | Aug 2026 | **Mint RESOLVE (§6.5)** | The node lands immediately with `supersedes: unresolved` and `mint_candidates: [project-memory-system-redesign]`. Nothing is blocked, nothing is lost. The binary question is posed on the next attended turn and answered in one word. |
| 3 | Aug 2026 | **Negation obligation (§5.2)** | Section 11 of the artifact is already a 14-row table of subject + reason. The close requires each row to be POSTed as a negation record **or waived with an accounted reason**. 14 records, ~200 B each. |
| 4 | Aug 2026 | **Closure, derived (§6.3)** | Once step 2 resolves, `project_memory_system_redesign.md` becomes a non-head node. Its 37% delivery rate now delivers the August conclusion instead of *"engagement closed 2026-05-18"*. **One frontmatter line on the successor redirects 129 deliveries.** |
| 5 | 09-06, turn arrives | **Per-turn refresh (§9.6)** | The negation and notes blocks are recomputed **for this message**, server-side, at the stdin-write site. The five-day-old floor keyed on *"Hi Dave"* is no longer what the agent sees. |
| 6 | 09-06, same turn | **Ledger (§5.3)** | Even with no edge: on 2026-09-06 the August negations are among the 20 most recent, so three lines (~210 B) are resident — *two-level index · Anthropic memory tool as a replacement · vector index, all declined 2026-08-16* — and the agent is handed the slugs. |
| 7 | 09-06, same turn | **Head substitution (§6.3)** | The floor did really deliver `arch_memory_link_layer.md` in the real trace. With the typed edge present, the head is admitted by substitution in a slot that already existed. **Hop 1, from a node actually delivered.** |
| 8 | 09-06, warm miss | **Cold probe (§9.4)** | The real trace's warm floor returned 7 units, none naming the target — exactly the miss condition. One cold FTS hit (~520 B, 3.2–3.4 ms) is appended; the cold tier was measured as the **only** tier returning the August artifact on all three replay queries. |
| 9 | counterfactual | **Write-act interrupt (§5.4)** | Had the agent written *"propose a two-level index"* into a plan file or a `docs/` spec, the record is returned verbatim: *"Moves ~70% of the index behind an action that happens in 5% of sessions. Deletes, does not page. Disproved by measurement."* |

### 2.4 The honest verdict — what is eliminated and what is not

**Eliminated for a fresh dispatch and for prospective artifacts:** FC-1 (with the
deterministic trigger, §6.5), FC-3 (the best single move in the design), FC-4
(with the phrase rule, §7.3), FC-6 (with waiver accounting, §5.2), FC-8, FC-9.

**Eliminated for the traced session shape only if §9.6 ships server-side.** Every
resident block — the read floor, the positions block, the continuity block, and
the new negation ledger — is rendered by `_build_agent_context`, which is **not
re-entered on a stdin write to a live Mode-B process**. A negation POSTed on day 3
of a five-day session is invisible to that session forever, *including one the
session itself just wrote*. **The ledger inherits the exact staleness class it was
built to backstop.** The write-act interrupt is immune to session age, but it
fires when the proposal is *written down*, not when it is conceived — and the pass
condition says *before it plans*.

**Condition 3.** §9.6's per-turn refresh is built **server-side at the four stdin
composition sites**, not as a `UserPromptSubmit` hook. The hook is the cheap
option and is **unverified on this path** — every observation of it firing is on a
fresh dispatch, the case that already works. The server-side path works on every
provider, which matters because `MEMORY.md` already never reaches non-Claude
providers at all (`agent_routes.py:2583-2587`). **Without it, the pass condition
is met for a fresh dispatch and not for the traced session, and the spec says so
rather than claiming the failure is eliminated.**

**Not met at all:** FC-2 — the archive class stays retrievable and edge-incapable.
Waived in writing, with its argument, in §13.4. FC-5 is met by the bounded cold
probe in §9.4.

**Explicit non-goal.** The design is **not** required to have prevented the
September measurement. That work caught a real 0 → 17 broken-link regression the
August design shipped blind to. It is required to have prevented **re-deriving the
first ~60%** — the code facts, constants and hop semantics an existing committee
seat had already established with `file:line` citations 22 days earlier.

---

## 3. Principles

Eight, each inherited from something this project already measured or already
suffered. Everything below obeys them.

**P1 — Standing is a default a successor revokes, never a privilege a pointer
grants.** If standing were earned, every mechanism computing it would be the
curated index with extra steps, and R-2 would be violated by the definition
itself.

**P2 — Fail-closed where a caller is waiting; fail-open with a log and a
quarantine where nobody is.** Copied verbatim from the house precedent:
`_enforce_index_cap` (`mc/memory.py:1919`) **raises** so an attended route returns
413 with a remedy string; `_index_overflow` (`:1903`) does not, because
`_commit_managed_entry` is documented *"Never raises"*. A background writer cannot
repair, so refusing it destroys memory in order to enforce a schema.

**P3 — Deterministic rules beat inference, because a misfire is then a line you
can read and fix.** `mc/memory.py:655-676` records that two statistical gates were
tried for position triggers and both were fragile. Let a model's judgement *add*;
never let it be the only thing that can create.

**P4 — A bound that bites silently gets worked around by the agent it
constrains.** `_append_note_to_backlog_item` truncated at `text[:2000]` and kept
`notes[-50:]`; the steward noticed and started splitting findings into "(cont.)"
notes, spending extra slots out of the same 50 and accelerating its own data loss.
**Every bound in this spec emits a log line naming the project, the count, and
where the content went.**

**P5 — A provenance field the actor supplies is a field the actor can forge.**
Server-stamped from session identity, never a writable parameter. Precedent:
MC-923 made `--unattended` server-detected rather than self-reported, fail-closed.

**P6 — Enforce the slot, not the vocabulary.** 16 typed relationships in 283
mentions is not a vocabulary to enforce; it is a sample too small to design one
from. Default to `see-also`, round-trip unknown types, close the vocabulary later
from evidence.

**P7 — Report-only until measured.** No gate refuses anything until its
false-rejection rate has been observed against the real corpus. This applies to
every gate in §10 **and to the write-act interrupt in §5.4**, which is the item
most likely to be exempted by accident.

**P8 — Relocate, never delete.** The archive is append-only by module contract
(`mc/memory.py:18`). Superseded lines move to a sibling file; demoted pointers
leave the index and the note stays. Pruning is an explicit operator action with a
count, never machinery.

---

## 4. The record

One schema layer, added as a **pure superset** of what exists. Every new field is
optional; every existing field keeps being read. The 46 existing position files
and the 235 existing topic notes remain valid records on day one.

### 4.1 The note record (topic files)

```yaml
---
name:        <slug>                    # existing, 231/235 present — now a resolution alias
description: <one line>                # existing, 231/231 present — now boosted ×2 and feeds triggers
type:        <class>                   # existing on 53; derived from filename prefix on migration
triggers:    <comma list>              # NEW on notes; defaults from name + description (§7.3)
supersedes:  <slug> | unresolved       # NEW, optional — declared by the SUCCESSOR only
mint_candidates: [<slug>, …]           # NEW, machine-written when supersedes is unresolved
unrelated_to: <slug>                   # NEW — the cheap answer to the mint question
holds_while: <machine predicate>       # NEW, optional — closed grammar (§4.5)
revisit_if:  <free prose>              # NEW, optional
aka:         [<old-stem>, …]           # NEW — forwarding record for renames (§4.4 R4)
external_ref: [{vault, stem}]          # NEW — a cross-vault reference that is not an edge
origin:      interactive|unattended|legacy   # NEW, SERVER-STAMPED, never caller-supplied
generated:   { by: <actor>, at: <iso8601> }  # NEW, server-stamped
verified:    [ { by: human:<id>, at: <iso8601> } ]  # NEW, derived (§4.3), append-only
---
```

Body unchanged. `[[wikilinks]]` stay untyped see-also, which is what they are good
at. **The one typed relation lives in frontmatter, not in the link syntax** — see
§6.1 for why that sidesteps the format problem entirely.

### 4.2 The negation record (positions, extended)

```yaml
---
name:        <slug>                    # unchanged, derived
subject:     <the question that was settled>          # unchanged — supersession keys on this
claim:       <the proposal in the words a re-proposer would use>   # NEW
position:    declined | adopted | reversed            # unchanged
reason:      <why — MANDATORY, unchanged>
evidence:    <newline list: file, commit, doc#section> # NEW
durability:  principle | measured | provisional       # NEW
holds_while: <machine predicate>       # NEW, optional; forbidden on durability: principle
revisit_if:  <free prose>              # RENAME of expires_when; the old name is a permanent read alias
triggers:    <comma list>              # unchanged — explicit lists stay df-exempt
supersedes:  <slug>                    # NEW, optional — may name a TOPIC note
pin:         true                      # NEW, optional — ledger override, capped at 5
decided:     <date>                    # unchanged
---
<body>

## Previously                          # mechanically generated, unchanged
```

**`claim` vs `subject`.** `subject` is the *question* ("Obsidian as the memory
substrate") and is what the ruling is filed under and what supersession keys on.
`claim` is the *sentence someone re-proposing would write* ("adopt Obsidian as
the memory substrate"). Today one field serves both filing and matching. §5.4
matches on `claim`; `subject` stays the key.

**`durability` — negated-forever vs negated-under-conditions**, which is the
distinction the framing asks for made into a field:

| value | meaning | expiry |
| --- | --- | --- |
| `principle` | negated on a value or an invariant, not a measurement | **never auto-expires**; only a human reopens. `holds_while` is forbidden. Example: the authority guard — learning may change *how* the agent works, never *what it is allowed to do* |
| `measured` | negated because a measured fact holds | auto-flags the moment the predicate flips or stops being evaluable. Example: *"cross-encoder rerank — measured worse locally, 46 → 41"* |
| `provisional` | negated on current cost or priority, expected to be re-litigated | soft clock; the existing 7-day review cadence |

### 4.3 Provenance — server-stamped, and one derived field

The comparison report's finding is the whole argument: OKF's `SaveConcept` writes
whatever actor string the caller passes and the CLI exposes it as free text, so a
human-attributed concept passes `--strict`. *"Never forge human verification"*
appears three times in their prose and zero times in their code.

**Condition 4.** `origin` and `generated` are stamped by the server from session
identity and are **not writable parameters**. `origin` reuses the Distiller's
existing allowlist (`mc/distiller.py:1575-1589`) verbatim rather than defining a
second one: `interactive` only when `trigger_type == 'manual'` and the task text
carries no unattended marker.

**Condition 5 — `verified[]` is DERIVED, not human-appended.** A field only a
human can fill, in a system whose founding premise is that the human will not
curate, stays empty forever and the guard rail that depends on it never fires. It
is derived server-side: **a note written in a session with `trigger_type: manual`
where the human sent a subsequent message is human-witnessed.** The machinery
already exists — MC-923's `CLAUDE_CODE_SESSION_ID` + session trigger-type lookup,
fail-closed. Every machine write preserves existing `verified[]` entries verbatim.

**The `legacy` exception, with its measured bound stated so nobody "fixes" it into
an outage.** 83 topic notes in the reference vault carry engram's
`originSessionId`, and **only 16 of 83 (19%) still join to the agent log**, which
caps at 500 entries and rolls. Adopting the Distiller's *"unstamped is treated as
unattended, fail closed"* rule verbatim would on day one make **~81% of the vault
invisible to every steward and scheduled cycle** — a correctness improvement that
delivers a strictly worse memory layer. Therefore:

> Legacy notes migrate as `origin: legacy`. **Legacy is read as
> attended-equivalent and is ineligible to carry `verified[]`.** Residual risk is
> bounded and measured: of 500 logged sessions on the reference project, 53
> `schedule` + 15 hivemind = **68 (13.6%) are unattended**, so at most ~14% of
> legacy notes could have autonomous origin. Fail-closed applies from the
> migration date forward, where the stamp is server-derived and complete.

Three consumers, and the first closes a hole open today:

- **C1 — the unattended read filter.** `mc/distiller.py:2229-2237` states the
  circuit: *"a steward cycle distils its own transcript into an exploration, the
  next steward cycle reads it back as established fact, and the system trains on
  its own output with no ground truth anywhere in the circuit."* That rail governs
  Distiller artifacts only; `_memory_search` has **no origin filter of any kind**.
  The autonomous→autonomous circuit is cut on one channel and open on the other.
- **C2 — supersession authority.** A `generated` note may not silently outrank a
  `verified` one on the same subject. This is the guard rail on §6's chains and on
  head substitution, and `origin` is the only field that can detect it.
- **C3 — the position-review surface.** `verified[]` with a date distinguishes
  *last written* from *last checked by a person*.

### 4.4 Identity and resolution

The measurement reports 17 broken links, "9 of which point at files that exist".
That phrasing hides three different failures with three different fixes:

| class | n | what actually failed | fix locus |
| --- | ---: | --- | --- |
| **A — extension in target** | 2 | `_mem_link_key` (`mc/memory.py:521`) strips the dot but keeps the letters, so `[[x.md]]` keys as `xmd`. Target is in the same directory. | the canonicaliser |
| **B — rename without forwarding** | 2 | identity **is** the filename, so a rename is an unannounced primary-key change | identity record |
| **C — cross-vault** | 5 | target exists in a sibling project's vault; `_mem_link_graph` is per-vault and the syntax cannot name another vault | link grammar / policy |
| **D — no candidate anywhere** | 8 | 6 genuinely absent + 2 pointing at foreign namespaces (a Distiller artifact, a skill) | authoring |

**Only class D is a dangling pointer.** A, B and C are resolution failures: the
knowledge is present and the addressing scheme cannot reach it. Treating all 17 as
one bucket produces a design that nags authors about nine problems they did not
cause.

- **R1 — a note's identity is its filename stem, unchanged.** Measured: **zero
  link-key collisions on filename stems across all 19 vaults**, so the existing
  tolerant canonicaliser is not currently ambiguous and does not need tightening.
- **R2 — canonicalisation strips a trailing `.md` before keying.** One rule change
  in `_mem_link_key`. Retroactively repairs class A with zero authoring change and
  zero migration. **Describe it as a resolution bug fix**, so nobody records it as
  a new authoring convention.
- **R3 — resolution consults, in order: (1) filename stem, (2) frontmatter
  `name:`, (3) the forwarding record.** Measured safe across 19 vaults and 277
  files carrying `name:`: **0** aliases would collide with another alias, **0**
  would shadow an existing filename key, and **83** contribute a new unique key.
- **R4 — a rename writes a forwarding record.** The old stem is appended to the
  successor's `aka:`. A rename that does not write one is a rename the runtime
  refuses **at G1** — with the limit in §10.6 stated honestly: G2 covers agent
  `Write`/`Edit` only, so a filesystem `mv`, an editor rename, or an MCP write
  cannot be refused.
- **R5 — cross-vault links are out of scope and are rejected at write**, naming
  the resolving vault in the error, and recorded as `external_ref:`. Rejecting
  five links we cannot honour beats shipping five edges that look alive and are
  not. §12 rejects a cross-project graph on its own evidence.
- **R6 — vault identity resolves through a registry, not a derivation.** The
  project record owns the vault id; the encoded path is a lookup key into it.
  *Why this is not optional:* `_encode_project_path` (`mc/memory.py:115`) derives
  the vault directory from the project path, so a session whose cwd was a scratch
  or worktree subdirectory **minted a new vault**. Three phantom vaults exist, and
  **82 session-log entries were written to disk that no future session will ever
  load**; one project is split across two path encodings and neither knows about
  the other.

  **Condition 6 — an unregistered path registers on first use with the merge
  candidate reported; it is never a hard error at session start.** This project
  uses git worktrees, and the phantom vaults were minted by exactly those cwds. A
  hard error would fail the first session in every new worktree, fresh clone and
  renamed directory. **The defect being fixed is *silent* forking; a report cures
  that without an outage.**

- **R7 — slug near-collisions are surfaced at write, not just exact ones.** A
  position's stem is `_mem_link_key(subject)[:48]`, a truncated derivation of free
  text, and this vault already holds the near-miss:
  `position_showingsubagentoutputinsidetheparentchatunderits` versus
  `…intheparentchatunderthesuba`, **diverging at character 27** — two rulings on
  one question. Under a design that makes supersession routine, a truncation
  collision attaches a chain to the wrong ancestor, silently. On write, report any
  existing subject whose slug shares a long prefix and require the author to pick
  **same subject** (supersede) or **different subject** (explicit distinct slug) —
  the same forced binary as the mint question, reusing the mechanism rather than
  adding one.

### 4.5 One expiry concept, one cadence

The upstream proposals produced five names for one idea (`expires_when`,
`revisit_if`, `holds_while` in two incompatible forms, `review_by`, `stale_after`)
and two incompatible evaluation cadences. Settled:

**Condition 7.** There is exactly **one machine predicate (`holds_while`)** and
**one prose field (`revisit_if`)**. `expires_when` is kept as a **read alias
forever**, so the 46 existing position files need zero migration. `review_by` does
not exist; a date is expressed as `days_since_decided > N`, already in the
registry. **Evaluation happens at corpus build and is cached on the corpus
signature**; the weekly positions-review job *reads* the cached value rather than
computing it.

**The grammar, deliberately tiny:** `<metric> <op> <number>`, `op` in `< <= > >=`,
joined by `and` / `or`, no nesting. `metric` is drawn from a registry of things the
runtime already produces:

`index_bytes` · `index_headroom_bytes` · `corpus_units` · `topic_notes` ·
`positions` · `broken_links` · `delivered(<unit>)` · `days_since_decided`

**Condition 8.** A **non-empty unparseable** `holds_while` is refused with the
registry listed in the error and `revisit_if` named as the alternative. An
**absent** one is never refused. Since the field is optional, refusing every
imperfect value punishes only the authors who try.

**The case for the grammar, from this vault.** A live position decided 2026-08-24
carries `expires_when:` *"…resident index pressure against its 24 KB cap, which is
at 17.2 KB today"*. The same file measured live on 2026-09-06: **23,627 B against
24,576 B — 96.1% of cap**, 17.2 → 23.6 KB in thirteen days. **The stated re-open
condition is not approaching; it is satisfied**, and the ruling that declines a
promote/demote mover is now being enforced by its own staleness. Nobody noticed
because `positions_review.py` cannot evaluate free English, and its 7-day clock
plus content-hash re-arm fires on elapsed time and human edits, never on the
world — while the predicate is expressed against a metric the memory routes
already compute every turn for their own 413 path.

> **Honest limit, stated rather than buried.** Of the 15 live positions, roughly
> **4** have a clause reducible to a metric today. A grammar covering 4 of 15 is
> still worth building, because those 4 are exactly the ones whose reasons are
> *measurable* and therefore the ones that expire **while nobody is looking**. A
> judgement clause expires when a human changes their mind, and the human is
> present when that happens. That asymmetry is the whole justification. Do not
> argue it on coverage.

### 4.6 Config keys — including four that are silently broken today

**Condition 9.** `positions_enabled`, `read_floor_position_reserve`,
`position_trigger_max_df` and `continuity_enabled` are read from `CONFIG` but
appear in **neither the defaults dict nor `_CONFIG_EDITABLE_KEYS`** — silently
unconfigurable, with `PUT` returning `200 {"updated": []}`. Every new key below is
registered in both, and the existing four are fixed in the same pass.

| key | scope | default | governs |
| --- | --- | --- | --- |
| `memory_index_byte_cap` | project | 24,576 → 8,192 (terminal, §11 gate) | §9.1 |
| `session_log_ring` | project | 20 | §9.2 B2 |
| `negation_ledger_max` | project | 20 | §5.3 |
| `negation_pin_max` | project | 5 | §5.3 |
| `read_floor_negation_reserve` | project | 4 | §9.4 |
| `negation_interrupt_mode` | global | `report` → `advisory` → `block` | §5.4 |
| `negation_interrupt_max_hits` | global | 2 | §5.4 |
| `memory_gate_mode` | project | `report` → `enforce` | §10.5 |
| `memory_cold_probe_enabled` | project | true | §9.4 |
| `memory_cold_probe_k` | project | 1 | §9.4 |
| `memory_fetch_calls_per_turn` | project | 3 | §9.5 |
| `memory_mint_on_close` | project | true | §6.5 |
| `trigger_phrase_bigrams` | project | true | §7.3 |

---

## 5. Negation — how a killed idea reaches the agent about to re-propose it

### 5.1 Verdict on the existing positions API

**The positions API is sufficient in mechanism and empty in coverage. Keep the
object, extend the record, replace the trigger.**

*Sufficient in mechanism* — it has three properties nothing else in the system
has, and all three are right:

1. **its own prompt block**, rendered above the note block. Mixing a ruling into
   ordinary memory is what let the agent re-propose Obsidian on 2026-08-23 with
   the position in context;
2. **supersede-by-subject in place**, mechanically re-emitting the prior verdict,
   date, reason and body under `## Previously` (`mc/memory.py:1294-1308`) —
   **fifteen lines of code produced the best-shaped record in the entire corpus**;
3. **a mandatory `reason`** that raises with its rationale inline: *"a verdict
   without one cannot be re-evaluated."*

*Empty in coverage* — the August design contains **14 reasoned negations** and
**zero** of them are positions. The vault holds 15 positions; four touch memory
and all four are knob-level rulings dated 2026-08-24, written by a delivery-
telemetry follow-up rather than by the design that produced the 14. **Storage was
never the gap. The gap is that nothing obliges a negation to become one.**

*And the trigger has to go* — three independent kills:

- **Kill 1 — the match is against the user's task string; the re-proposal is the
  agent's.** A position is admitted only via the reserve, gated on a non-empty
  intersection of `subject_terms` with the **task** terms
  (`mc/memory.py:1584-1596`). The re-proposal is formed tens of thousands of tokens
  later. On the 2026-09-06 replay the task string was *"compare okf agent memory to
  our memory layer"*: `memory` has **DF 17.8%**, above `position_trigger_max_df =
  0.10`, and `mc/memory.py:694-716` names that exact term as one the filter
  deliberately loses.
- **Kill 2 — a live session never re-queries.** The floor is built at dispatch or
  revival only. The traced session ran five days on a floor keyed to *"Hi Dave"*.
  **The healthier the session, the staler its negations.**
- **Kill 3 — presence is not interruption, and we have the incident.** 2026-08-23:
  the Obsidian position **was** in the prompt and the agent proposed Obsidian
  anyway. Separating the block was the fix; nobody has shown separation is
  *sufficient* — one incident, one mitigation, no second trial. Additionally the
  scheduler path renders every hit as `[file] snippet` with **no positions block at
  all**, so unattended cycles still run in the pre-2026-08-23 shape known to fail.

*And "just inject them all" is arithmetically dead.* The full render of the 15
live positions measures **14,156 B** — more than half the 24 KB index budget, on a
four-month-old project. **Condition 10: any negation-delivery design must be O(1)
in the number of negations.**

**Two holes in the object itself, both closed here:**

- **Positions are excluded from the link graph.** `mc/memory.py:553` builds
  `by_key` from `cls == 'topic'` only, so a `[[wikilink]]` aimed at a position
  resolves to nothing and is silently dropped. **A negation cannot currently be
  reached from the topic it negates.** Fix: positions become graph citizens on
  both ends.
- **A position whose frontmatter fails to parse is silently and permanently
  unretrievable.** `_parse_position` returns `{}`, so `subject_terms` is empty, so
  the coverage gate is false, so it is never admitted — and it still looks correct
  to a human. V2 adds fields, so it adds parse surface. **Validate at write *and*
  at corpus build, and report** (§10.3, G4).
- **`write_position` takes no write lock.** It is atomic per file (temp+rename),
  but two concurrent supersessions of one subject are last-write-wins and **the
  loser's `## Previously` chain is lost**. Contrast `_commit_managed_entry`, which
  is leaf-locked. Today that is one file in one vault; under a design that makes
  supersession routine it is a **data-loss path**. This is the single most concrete
  code-level prerequisite in the spec.

### 5.2 What obliges a negation to exist

**Condition 11 — the negation obligation.** When an artifact of declared weight
closes (§6.5's trigger list), any heading in it matching
`rejected|declined|negated|not doing|alternatives considered` produces one
negation record per row, **or a waiver**.

**Condition 12 — a waiver is itself a record.** It carries subject, reason and
artifact, is counted, and appears in the same report as the negations. **A waiver
that costs the same as a POST is not an escape hatch.** This is the difference
between an obligation and a request, and it is the reason the 14 rows in the
August table do not simply get waived fourteen times.

### 5.3 Delivery layer 1 — the Negation Ledger (resident, bounded, O(1) forever)

A resident block, separate from the notes block. One stub line per negation, **no
reason**:

```
x  a two-level lazy index for the memory index — declined 2026-08-16 [twolevellazyindex]
```

About 70 B a line. **Cap: 20 lines / ~1.5 KB** (`negation_ledger_max`).

**Condition 13 — the ledger is the ONLY resident negation surface.** The upstream
proposals produced three for one object. `## Standing positions — subjects only`
is **deleted from `MEMORY.md`**: it is the same artifact built by a different
mechanism, in a region that is not in the retrieval corpus at all. Keep the ledger
(machine-ranked, bounded) and the query-gated reserve (§9.4). Two surfaces, not
three.

Membership ranking, in order:

1. `pin: true` — human override, capped at 5 (`negation_pin_max`);
2. **confirmed re-proposal pressure** — interrupt fires *where the agent withdrew
   or superseded*, over the last 180 days, descending;
3. recency of `decided`, descending — **with ≥5 of the 20 slots reserved for pure
   recency**;
4. subject ascending (deterministic tie-break).

**Condition 14 — rank by CONFIRMED interrupts, never by raw hit count, and reserve
5 slots for recency.** Raw hit count makes the 20 permanently-resident negations
the 20 with the most generic triggers — the MC-898 phenomenon at ledger scale,
self-reinforcing, with a new ruling never able to displace an old noisy one. The
reserved recency slots are what make the replay's step 6 work at all.

Cold start (day one, zero confirmed hits) is rule 3: the 20 most recent negations.
That is the correct default and it is what carries the 2026-09-06 replay.

**The line deliberately carries no reason.** The ledger's job is to make the agent
*know a ruling exists* and hand it a slug. The reason arrives via the interrupt or
an explicit read. A reason in the ledger is precisely what makes 15 positions cost
14 KB.

**Its failure mode, stated:** a negation outside the top 20 relies entirely on
§5.4. Mitigation is posture, not mechanism. And `DELETE` stays: a ruling that was
simply *wrong* is removed, not demoted, for the reason the API already gives —
reversing a still-live question is a POST that supersedes, which keeps the prior
reasoning.

### 5.4 Delivery layer 2 — the write-act interrupt, advisory until measured

A `PreToolUse` hook on the four acts that *constitute* proposing:

1. `Write` / `Edit` under `docs/**`
2. the plan-file write
3. a backlog `POST`
4. an `mc:question` emission

Precedent is shipped and load-bearing: `steward/fence.py` is a stdlib-only
`PreToolUse` hook that hard-blocks with exit 2 + stderr, self-gates by session
type, and **fails open on any parse error**.

**Condition 15 — it ships in `report` mode, is promoted to `advisory` (exit 0,
stderr injected as a warning), and may only be promoted to `block` after a
measured false-positive rate.** `negation_interrupt_mode` is a switch, not a
milestone, and P7 applies to it with no exemption. The reasons this is not
optional:

- **No df gate is specified for it.** The df gate exists on the read-floor reserve
  *because* MC-898's subject term `agent` fired on **108 of 188 tasks (57%)**.
- **The match target is 10–100× larger.** The reserve matches trigger terms
  against a task string of tens of tokens; this matches against a whole document
  body. Set intersection over a longer document is strictly more likely to hit.
- **It scales the wrong way.** 15 positions today, ~135 per project and ~400
  corpus-wide at three years — and the design's whole purpose is to grow that
  number fast.
- **The borrowed doctrine does not transfer.** *"A false-block merely makes the
  steward pause"* is true of a narrow hand-written path denylist. It is not true
  of a ~3,200-term content matcher gating every `docs/` write.

**Condition 16 — match the `claim` as a phrase, conjunctively, within a window;
never as a bag of triggers.** `claim` exists in the schema precisely as "the
sentence someone re-proposing would write." Cap at **2 hits per turn**
(`negation_interrupt_max_hits`) so one write cannot produce a wall of interrupts.
**Log every fire and every withdrawal** — that is the only way the promotion in
Condition 15 ever gets measured, and it is also the input to Condition 14.

**On a hit** the stderr carries the record verbatim: subject, verdict, `reason`,
`evidence` pointers, and the current `holds_while` evaluation. The agent withdraws
or reopens with a stated reason — and reopening is a POST that supersedes
in place, so the `## Previously` stack grows and the next agent reads the reversal.

> **Honest limit.** This fires when the proposal is **written down**, not when it
> is conceived. It costs one wasted planning pass, not one wasted investigation.
> Against the 2026-09-06 baseline — an entire conversation re-derived — that is a
> large win and it is not a complete one. Do not claim it prevents all rework.

**Provider scope, stated plainly.** This is a Claude-CLI hook, as is G2. If the
design stays hook-based, two of its enforcement mechanisms degrade to today's
behaviour on every other provider. The delivery guarantee does not, because §9.6
is server-side.

### 5.5 What this costs

| component | today | 3 years / ~400 negations | scaling |
| --- | --- | --- | --- |
| records on disk | 15 files / 18 KB | ~135 per project, ~400 corpus-wide, ~135 KB | linear, irrelevant |
| **resident prompt cost** | capture directive 1,500 B + reserve ≤900 B = **~2.4 KB** | ledger 1,500 B + reserve 1,800 B = **~3.3 KB** | **constant.** Delta ≈ +0.9 KB, forever |
| interrupt matcher | — | ~400 records × ~8 triggers ≈ 3,200 terms | CPU free; **precision is the cost, and it is unmeasured** — see §13.3 |
| ledger ranking | — | O(N log N), N ≈ 400, at corpus build | free |

**Do negations accumulate unboundedly in the prompt? No.** The ledger is 20 lines
at 5,000 memories exactly as at 100; what changes is *which* 20. **Where the
negation set does become bloat is the matcher, not the records** — and that is why
Condition 15 exists.

---

## 6. The growth path — conclusion plus negations, without replaying history

### 6.1 Why typed edges never appeared, and the one thing that fixes it

Three mechanical reasons, stacked, any one sufficient:

1. **There is no syntax for a typed edge.** `_mem_link_targets` parses `[[target]]`
   as `raw.split('|',1)[0].split('#',1)[0].strip()` — **the pipe alias and the
   anchor are both discarded.** There is nowhere in a wikilink to put a
   relationship type. The single `supersedes` edge in the corpus is English prose
   in a parenthetical inside a `Related:` list, because prose is the only place it
   could go.
2. **Nothing consumes a type.** `_mem_link_graph` returns flat filename lists;
   `_mem_expand_links` scores `h['score'] * decay`. The only distinction an edge
   can carry today is direction. Typing an edge costs tokens and changes zero
   behaviour; **94% untyped is the measured decay rate of unrewarded discipline.**
3. **The write moment is wrong, and this is the fatal one.** A `supersedes` edge
   would have to land on the **old** note at the moment the new work happens —
   and the old note is exactly the note nobody is looking at. Nobody edited
   `project_memory_system_redesign.md` in August because nobody was writing in it.

**Condition 17 — add exactly ONE typed relation, in frontmatter, declared by the
SUCCESSOR, never as an edit to the predecessor.** The author writes
`supersedes: <slug>` in the **new** note; they already have the slug in hand,
because they had to read the old note in order to supersede it. The runtime derives
the rest: the forward edge typed, the back edge (`superseded_by`) **never written
to disk**, and `head(x)` by following it transitively — cycle-guarded by a visited
set, depth capped at 8 with a log line on hitting the cap.

**In-repo precedent, cited deliberately:** the backlog link model already works
exactly this way — *"Only the direction you post is stored; the inverse is rendered
on the other item automatically, so never post both halves."*

### 6.2 Two growth paths, one for each object — do not merge them

**Condition 18.** **A position's growth path is its `## Previously` stack; a note's
growth path is its `supersedes` chain; head substitution applies to notes only.**

This is not a stylistic split. `write_position` supersedes **by subject, in
place**: it rewrites the *same file* and re-emits the prior verdict under
`## Previously`. No new file, no new slug, **no chain** — `head(n) = n` for every
position, always. Any design that treats a position as a chain node is describing
code that does not exist. **Do not replace the in-place mechanism:** it is fifteen
lines of code and it produced the best-shaped record in the corpus.

### 6.3 Head substitution — match on the tail, deliver the head, attributed

```
standing(n) ∈ { STANDING, LAPSED, OUTPACED }

OUTPACED(n)  ⟺  ∃ m ≠ n  such that  n ∈ supersedes(m)
LAPSED(n)    ⟺  n declares holds_while and eval(holds_while) = false
STANDING(n)  ⟺  otherwise                                   ← the default
```

When BM25 lands on an `OUTPACED` note, the read floor delivers **`head(n)` in its
slot**, attributed. **The stale node keeps its scoring unit for matching** — its
text still contains the words people search for, which is exactly why it ranks —
but its delivered snippet is replaced by a redirect stub:

```
SUPERSEDED 2026-08-16 by design_memory_redesign_2026-08.md.
CONCLUSION: keep the [[wikilink]] layer; bounded session-log split; write-time link gate.
NEGATED en route (7): two-level lazy index (moves ~70% of the index behind an act that
happens in 5% of sessions) · vector/embedding index (weakest on exact tokens; no ML stack
present) · cross-encoder rerank (measured WORSE, 46 → 41) · any vector or graph DB ·
external memory tool as a replacement (pull-only, no ranked push) · procedural-memory
extraction (rejected on SAFETY) · deleting any note (zero genuinely dead notes found).
FULL: design_memory_redesign_2026-08.md
```

Four properties, each the reason to prefer this over demoting or deleting the
stale node:

- **The stale node is a good query magnet — spend it.** It is the second-hottest
  unit in the vault at 37%. Deleting or demoting throws away lexical pull that
  already works; the redirect makes that pull deliver the *current* conclusion.
- **Zero extra slots.** The redirect replaces the tail's own snippet.
- **No chain walk at read time.** The negation list is **materialised on the head**
  (§6.4) and the redirect is one dictionary lookup on a precomputed map. Depth is
  paid once, at write time.
- **Bounded.** Cap the list at 7 entries × 120 chars ≈ 900 B; a stub is ~1.1 KB
  against the ~400 B snippet it replaces. Beyond 7, keep the entries with the
  highest confirmed re-proposal pressure and append `(+N more, see <head>)`.

> **The honest number: the redirect is +~700 B per delivered superseded node.** It
> is justified because it is delivered *instead of*, not *in addition to*, and the
> thing it replaces was actively wrong.

**Condition 19 — substitute, then dedupe by head, then backfill.** At three years
one head can supersede many predecessors, so several BM25 hits in one query
collapse to the **same** head and the floor would deliver it 2–4 times against a
fixed 6-slot budget. After substitution, deduplicate by head and backfill freed
slots from the next-ranked non-substituted candidates. Without this the slot budget
silently shrinks as chains grow — the design degrading exactly as the corpus
matures.

**Substitution is attributed, never silent.** The agent is told which note it
matched and that it was outpaced, and the outpaced note stays fetchable on demand.
That is the requirement made concrete: **the head carries the conclusion, the
head's negation block carries what was ruled out, and the intermediate nodes are
reachable but not delivered.**

**Condition 20 — a `generated` note may not silently outrank a `verified` one on
the same subject.** When head substitution would replace a `verified` predecessor
with a `generated` successor, the substitution is **reported alongside both**
rather than performed. This is C2 from §4.3, and it is why `verified[]` had to be
derived rather than human-appended.

### 6.4 The materialised negation block

At corpus build (already cached by signature), compute the supersession closure and
regenerate a derived `negations:` block on each head whenever its chain changes.
This is what makes "read the conclusion plus the negations without replaying the
history" a lookup rather than a traversal.

### 6.5 The mint — WRITE and RESOLVE, split

The upstream proposals put the mint in two incompatible postures: refused until the
caller supplies `supersedes:` (needs a synchronous caller), and performed at Scribe
time (nobody is waiting). Under P2 both cannot hold. Settled:

**Condition 21 — minting is deterministic, not a judgement.** A **hivemind close**,
a **committee close**, a **backlog item moving to `done`**, or a **`docs/` artifact
above a size threshold created in the session** mints a node **unconditionally**.
The node may be thin — subject, date, artifact path, pointer to its
rejected-alternatives section — because **a thin node is a graph citizen with
triggers and a `supersedes` slot, and 346 KB produced none.** The session-end
Scribe's judgement (*"did this session settle something that deserves a node?"*)
**adds** nodes; it is never the only thing that can create one. Scribe already ran
in the traced failure and produced the 7 archive lines; a design whose entire delta
is that Scribe now also makes a judgement call is a design that fails the same way
when it judges *no*.

**Condition 22 — the mint WRITES fail-open and RESOLVES later.**

- **WRITE.** The node lands immediately. The overlap detector runs and, if it
  finds a candidate, the node carries `supersedes: unresolved` and
  `mint_candidates: [slug, …]` (top 3). **Nothing is lost, nothing is blocked.**
- **RESOLVE.** The binary question — `supersedes: <slug>` or
  `unrelated_to: <slug>` — is posed **on the next attended turn in that project**,
  as a reserved-slot item answerable in one word. That is the only place a caller
  is genuinely waiting.
- **An unresolved mint is fully retrievable and fully STANDING**, but is
  **ineligible to be a head**, so it cannot silently outrank the predecessor it
  might supersede.
- **G1 (the attended note write) stays fail-closed.** Refusing is cheap and
  correct where a caller is waiting.

**Condition 23 — the overlap detector uses subject/trigger overlap ALONE.** The
upstream version required *"and a non-trivial delivery count"*. That is a
popularity signal (§7.5) with a concrete bad consequence: a **cold** predecessor
never triggers the gate, so a new note silently duplicates a never-delivered note
instead of superseding it, and the cold note stays cold forever while its near-twin
accumulates deliveries. **That is the popularity bias the design exists to remove,
installed in the one gate that decides what supersedes what.** Report the top 3
candidates; `unrelated_to` is the cheap answer.

**Explicitly out of scope: Scribe and the checkpointer may not infer supersession.**
They cannot, and they must not guess. Stated here so nobody builds it.

Cost: one corpus scan per mint. Mints are rare by construction — the aspiration is
a handful a week. Free at 5,000 memories.

---

## 7. Standing — how a small unlinked memory keeps its standing without a pointer

### 7.1 The invariant

> **INV-STANDING.** An unlinked, unlisted, never-pointed-at note that no successor
> has superseded, and whose declared conditions still hold, carries **full standing
> and full rank**. It is penalised for nothing. Being an orphan is not a defect,
> and no gate, score, or report may treat it as one.

The polarity is the entire design (P1). Every input to `standing(n)` in §6.3 is
**file-local or forward-declared**: a note's standing is computable from its own
frontmatter plus the set of successors that named it. Nothing consults `MEMORY.md`,
nothing consults delivery counts, nothing consults in-degree.

Three enforceable consequences:

- **Condition 24 — no in-degree term anywhere in ranking.** Not as a prior, not as
  a tie-breaker. 46% of nodes have no inbound edge and 23% have no edge at all; any
  in-degree term demotes a quarter of the corpus for a property that has nothing to
  do with whether the note is true.
- **Condition 25 — no orphan gate.** OKF's `buildGraph` treats an unlinked concept
  as an orphan and `--strict` **fails the build on it** (`pkg/okf/validator.go:207-210`).
  Adopting it would fail 107 of our 235 notes on day one and would make *"no human
  wrote a pointer to me"* a build error — **R-2's failure enshrined as a check.**
  This reads like a gap next to OKF until you notice it contradicts our own R-2, so
  the non-adoption is recorded explicitly here and in §12.
- **Condition 26 — reachability is a property of the CORPUS, not the GRAPH.** Every
  unit is a first-class scoring document. Edges and pointers may modify **rank**;
  they may never modify **reachability**.

**LAPSED fails open, deliberately.** Measured across the 86 topic notes in the
reference vault, **zero declare a checkable condition** (`name:` 86/86,
`description:` 86/86, `type:` 17/86, `triggers:` positions only). So `holds_while`
is a new optional field and its absence means STANDING. A LAPSED note is
**rank-penalised ×0.6 and flagged with a caveat line, never withheld**. Withholding
on an unverified staleness signal would hide notes silently, which is the exact
class of failure this design exists to end. OKF made the same call from the other
direction: their `StaleCount` is reported and deliberately excluded from the strict
gate, so a bundle with 40 expired concepts passes `--strict` cleanly.

### 7.2 What replaces "being listed": arrival vocabulary, at zero authoring cost

The system already solved this once, for positions only. `_position_triggers`
(`mc/memory.py:677-684`): an explicit `triggers:` field names **arrival
vocabulary**, beats the subject's own words, and is **exempt from the df rarity
test** because *"a human naming a term is stating intent."* The comment block above
it records that two statistical gates were tried first and both were fragile.

Live probe row 1 in §1.3 is the proof it works: both live positions fired on a
query whose warm results were garbage, because their triggers matched arrival
vocabulary the note bodies do not contain.

**Condition 27 — `triggers:` becomes a field on every note, defaulting from
`name` + `description` tokens minus the 26-word stopword list.** `description:` is
**86/86 populated** and is currently tokenised at weight ×1 with no boost. **All
235 notes acquire arrival vocabulary without a human typing anything.** This is the
mechanism that makes an unlisted note findable, and it is the reason the migration
is not an audit.

### 7.3 The df gate applies to single terms only — trigger phrases are exempt

The default-trigger rule and the df gate compose badly, and the failure lands on
exactly the subject that failed. **`memory` has DF 17.8%**, above the 0.10 gate.
Default triggers for `project_memory_system_redesign.md` are
`{project, memory, system, redesign}`; the gate drops `memory`, and very likely
`system` and `project` too — all high-DF in a vault about a software project. The
memory-design node ends up with roughly `{redesign}` as its arrival vocabulary.

This is structural, not an edge case: **the gate suppresses terms that are common
precisely because the vault is about that subject, and the queries that fail are
the ones about the vault's dominant subject.** MC-898 (`agent`, 57% of tasks) is the
same phenomenon seen from the false-positive side.

**Condition 28 — the df gate applies to single terms only. A multi-term trigger
PHRASE requires all terms present within a window and is exempt.** Conjunction
supplies the rarity a single high-DF term lacks. The 2026-09-06 query was *"compare
okf agent memory to our memory layer"*: the phrase `memory layer` is present
verbatim; only the bare token is lost. Adjacent-token bigrams are emitted
automatically from `name` + `description` (`trigger_phrase_bigrams`, default on),
so this stays zero-authoring.

**Condition 29 — compute df per unit class.** Archive lines are 87% of the corpus
and echo prompts, inflating the df of every subject word and making the topic-note
gate stricter than anyone chose.

### 7.4 The retrievability gate — the guarantee checked, not asserted

Standing without retrievability is a note that is technically fine and practically
invisible. So:

```
retrievable(n) ⟺ n ∈ top_k( query = triggers(n) ∪ tokens(description(n)),
                            k = read_floor_topk )
```

Checked **at write time for the one note being written** (one search, O(N)) and as
a **full-vault sweep on a schedule** (O(N²) unit-scorings — §13.2 prices it; never
per-write). A note that cannot retrieve itself under its own declared vocabulary is
flagged with the specific miss, and **the fix is to add `triggers:`, not to add a
pointer.** If the widened default (the body's *N* rarest terms) still fails, it is
reported — never repaired by writing an index line.

> **Honest limit, stated because it will be quoted.** This proves self-retrieval
> under the note's *own* vocabulary. It does not prove recall under a stranger's
> query, and nothing in this system can: `MEMORY_GRAPH_MEASUREMENT` §9.1 is explicit
> that *"nothing records what a task needed and did not get."* The gate is a
> **floor**, exactly analogous to the read floor itself — it removes the
> "invisible by construction" class and makes no claim about precision.

### 7.5 Residency is not retrieval rank — the distinction that makes both bans hold

The upstream proposals contradicted each other here: one forbade recency, in-degree,
delivery count and *"any residency signal"*; another ranked a resident block by hit
count and recency; a third capped the index at "the N highest-value" without ever
naming the competition function. Left unresolved, *"it competes"* collapses to *"a
human picks the 30"* — R-2's failure, in the always-loaded prompt.

**Condition 30 — the ban in Conditions 24/26 is a ban on RETRIEVAL RANK.
Residency — which items are unconditionally present before any query exists — is a
different decision, and it legitimately needs a usage signal, because its job is to
pre-empt a query that has not happened yet.** With that sentence written, both bans
hold without contradiction.

**Condition 31 — index membership is decided by mint-recency and head-of-chain
status only. Never delivery count.** Both inputs are file-local; neither is a
popularity signal. Delivery count is what would make the frozen node (n=129, 37%,
saying *"engagement closed 2026-05-18"*) **permanently resident** — that node is
the measured proof of what residency-by-popularity looks like.

**Condition 32 — `## Binding constraints (human-authored only)` is not built.** A
never-demotable, human-authored-only section that returns 413 when full is a
curator, named, in the always-loaded prompt. Worse, it recreates the
`notes[-50:]` incident: a 2 KB human-only section under demotion pressure gets
gamed the same way, by the same actors. **Binding constraints are not a
memory-layer object** — they are `CLAUDE.md` / `AGENT_RULES.md` content and already
have a home with its own review discipline. `MEMORY.md` therefore becomes **100%
machine-maintained**, which makes §9.1's reversal of *"the curated region is never
touched by machinery"* clean rather than half-reversed.

---

## 8. The one-hop finding, engaged

**Recommendation: keep the current one-hop expansion — additive, bidirectional,
decayed 0.5 out / 0.35 in — unchanged. Do not build hop 2, hop 3, or unbounded
traversal. Spend the entire graph budget on making that one hop TYPED.**

The brief permits keeping part of the current design if it is argued from evidence.
The argument is stronger than *"today's corpus is too small"*, which is the form
that would expire the moment the corpus grows.

**8.1 The 196/15/4/0 distribution is a statement about seeds, not depth.** The
reference vault has 67 curated pointer lines naming 76 of its 86 topic files — a
seed set nearly 1:1 with the corpus. When 83% of nodes are hop 0, hop 2 has almost
nothing left to reach. That is arithmetic about curation coverage.

**8.2 Seeds cannot scale, and the byte budget is why.** 67 pointer lines cost
11,961 B = **178.5 B per line**. At a 24,576 B budget, even an index that was
nothing but pointers holds at most **~137 seeds** — and R-1 pushes that number
*down*, not up.

**8.3 Density does not grow with N.** 258 edges / 235 nodes = **1.10 authored links
per note**; mean undirected degree 2.51 in the reference vault. An author writes
roughly one wikilink per note whether the vault holds 86 notes or 5,000 — the
convention is **per-note**, so density is a constant. The hop-2 frontier per hit
stays at ~degree² ≈ 6 candidates at 5,000 nodes, essentially unchanged.

**8.4 The projection makes depth worse, not better.** Holding the authoring
convention constant, ~137 seeds, mean degree ~2.4, discounted by the measured 23%
isolated / 46% no-inbound:

| | today (235 nodes) | projected (5,000 nodes) *if the curated index remained the seed set* |
| --- | ---: | ---: |
| hop 0 | 196 (83.4%) | ~137 (2.7%) |
| hop 1 | 15 (6.4%) | ~250 (5%) |
| hop 2 | 4 (1.7%) | ~600 (12%) |
| **unreachable at any depth** | **20 (8.5%)** | **~4,000 (80%)** |

**Condition 33 — the right-hand column is labelled with its assumption and must
never be quoted without it.** It projects the pre-2026-08 graph-from-seeds model,
which this design explicitly rejects (§7.1, Condition 26). Under V2 the seeds are
the top-*k* BM25 hits drawn from all *N*, so 80%-unreachable is not the V2
prediction — it is the measured reason the seed model cannot be the discovery
mechanism. It does not change the recommendation.

**8.5 And 88% of the growth is edge-incapable regardless.** The class that reaches
5,000 first is the **archive**, and `mc/memory.py:1466` attaches links only to
`cls == 'topic'` while `:553` builds the graph from topics only. An archive unit has
zero in-edges and zero out-edges *by construction*. No hop depth touches one of them.

**8.6 What carries the load instead: content-addressed ranking, because its reach
is O(1) in seeds.** 85 of 86 topic files were delivered at least once across 349
tasks. Set that against the graph's measured contribution: **54 units have ever
arrived via a link, and only 2 have ever arrived *only* via a link**, in 349 tasks.
And note the arithmetic trap in the headline: 693/2,778 (24.9%) of deliveries "via
link expansion" is **mechanical** — `topk=6 + expand=2` makes 2 of 8 slots
expansion slots by construction, and the same ~25% appears in every vault.

**8.7 So what is the one hop actually for? Type.** An edge is the only construct in
the system that asserts a relationship **between** two notes — *this was
superseded*, *this was negated* — a claim no amount of reading either note's text
can produce. That is precisely what content-addressing structurally cannot do, and
precisely what the traced failure needed: **the 2026-09-06 miss was one typed hop
from a node already delivered in 37% of tasks.** We have never once used the first
hop for the only job it is uniquely good at.

**8.8 What is and is not being added.** No traversal is added at all. The redirect
in §6.3 is a lookup on a precomputed map, not a hop; the head is admitted by
**substitution**, not expansion. One thing is asked of the existing hop: if a
delivered node has an outgoing typed `superseded_by` edge, its head is admitted in a
link-expansion slot ahead of lexical candidates — **bounded at 1 of the 2 slots that
already exist** (`read_floor_link_expand=2`, shipped August 2026).

**8.9 The falsifier, written as a machine-checked predicate rather than a promise.**
The founding evidence of this whole spec is that unhooked checks do not run. So:

**Condition 34 — every falsifier in this spec is POSTed as a position carrying a
`holds_while` predicate, so the existing weekly positions-review job trips it
automatically.** Three, at minimum:

| falsifier | predicate |
| --- | --- |
| keep one hop, build no depth | `holds_while: topic_notes < 1000` — at 1,000 topic notes, re-measure the hop distribution; the recommendation expires if hop-2-only nodes exceed 5% of the corpus or any chain exceeds length 2 |
| the corpus cache holds | `holds_while: topic_notes < 1500` — §13.3's first break |
| the archive quota stays at 2 | `holds_while: delivered(archive) < 0.15` — the existing position's own reopening clause, made evaluable |

This is the best available demonstration that the design works on itself, and it
costs nothing new.

> You cannot measure the value of chain-walking on a corpus containing one chain of
> length one. **The measurement is a true statement about a corpus with no chains;
> it is not a true statement about chains.** The honest form of the claim, which
> keeps faith with both the measurement and the months-to-years framing: *chaining
> beyond one hop is unjustified today and must not be built speculatively; what is
> justified today is making the FIRST hop carry supersession.*

---

## 9. The prompt budget

### 9.1 The split — three surfaces, three independent budgets

| surface | file | in the prompt? | budget | who enforces |
| --- | --- | --- | --- | --- |
| **Index** | `MEMORY.md` | yes, every turn (CLI auto-load) | **8,192 B, terminal (§11 gate)** | demotion, logged |
| **Journal** | `SESSION_LOG.md` (new) | **no** | **20-entry ring** | rotation, logged |
| **Cold store** | `MEMORY_ARCHIVE.md` | no | unbounded on disk, bounded in corpus | read-time dedupe, offline compaction |

`MEMORY.md` loses its managed region entirely: the sentinel-walled
`clayrune:managed` block, its entries and the `clayrune:wm:<sid>` watermarks all
move to `SESSION_LOG.md`, which is **not** auto-loaded by the CLI and **not**
injected by `_build_agent_context`.

**Why the log leaves the prompt rather than getting a smaller cap** — three
independent lines of evidence:

1. **It does not earn its slots.** Measured over 349 tasks / 2,778 delivery slots:

   | corpus class | units delivered | slots | share | p50 *n* |
   | --- | ---: | ---: | ---: | ---: |
   | topic | 65 | 1,712 | 61.6% | 18 |
   | position | 14 | 217 | 7.8% | 14 |
   | archive | 87 | 134 | 4.8% | 1 |
   | managed (session log) | 19 | 21 | 0.8% | 1 |
   | (pre-schema, `cls` null) | 20 | 694 | 25.0% | 35 |

   The log — archive plus managed — is **688 of 789 corpus units (87%)** and takes
   **155 of 2,778 slots (5.6%)**, filling **0.44** of the 2 slots its quota permits.
2. **In the prompt it has already caused a real failure.** An earlier Stage-4
   "memory-in" attempt injected the full index for non-Claude agents, *"but its
   Session Log is a wall of past prompts, which Gemini read as a live task list"*
   (`agent_routes.py:2583-2587`). Removed for correctness, not cost. The argument
   generalises to every provider.
3. **Its prompt job is already done by a bounded block.** *"What am I part-way
   through, and what did I promise"* is `render_continuity`, capped by construction
   (`_CONT_MAX_*`, `mc/memory.py:771-784`), measured 1,136 B live.

**Condition 35 — the index after the split is fixed-shape, machine-maintained,
and contains no human-only region.**

```
# <Project> — Memory Index
<masthead: 1 paragraph, what this file is and is not>      ≤   600 B
## Pointers (N highest-value; N is a CONSTANT ~30)          ≤ 4,000 B
## Recent heads (mint-recency + head-of-chain, §7.5)        ≤ 3,000 B
                                                      total ≤ 7,600 B
```

**Condition 36 — the invariant this reverses, stated plainly.**
`mc/memory.py:2027-2029` — *"the curated region is never touched by machinery"* —
**cannot survive the split.** Once `MEMORY.md` holds nothing but curated content, a
cap with nothing else to evict either evicts curated or is not a cap. The eviction
is a **demotion, not a deletion**: the pointer line goes, the note stays, reachable
by BM25 and by the one hop. Every demotion logs:
`[mem-index] <project>: demoted pointer "<label>" → <file> (index at N/8192 B; note remains searchable)`.

**Condition 37 — the demoter MUST refuse to demote a line with no resolvable
target, and 8,192 B is a terminal state gated on the minting phase, not a day-one
cap.** 59% of curated index bytes are prose with nothing at the other end;
demotion of a targetless line **is deletion**. Such a line is not demotable, it is
*mintable*, and **the refusal list is the work list** (§11, phase D4). Until D4
completes, the cap is the current budget and the metric that matters is
minted-lines-remaining.

### 9.2 The journal's bounded lifecycle

Four bounds, each observable (P4). Every one emits a log line naming the project,
the count, and where the content went.

- **B1 — supersession at write. KEEP AS IS.** `supersede_sid` drops this session's
  previous entry, identified by `last_entry_hash` on its watermark
  (`mc/memory.py:2113-2121`). Already correct, already logs `entry_superseded`.
- **B2 — ring cap: the last 20 entries**, oldest-first rotation into
  `MEMORY_ARCHIVE.md`. Twenty because the measured mean live entry is **510 B**, so
  20 × 510 ≈ **10.2 KB on disk and 0 B in the prompt**. **This is the bound that
  must start logging:** today's ordinary eviction (`mc/memory.py:2154-2160`) emits
  nothing at all — it logs on the dedup path and on the protected-entries stall, but
  not on the case that runs every day. Required line:
  `[mem-log] <project>: rotated N entr(y|ies) to archive (ring cap 20, oldest first)`.
  **An entry-count ring, not a byte cap:** it is legible to a human ("the last 20
  sessions"), and the measured 325 → 510 B drift in mean entry size is exactly what
  a byte cap silently converts into fewer entries.
- **B3 — `(day, task)` dedupe stays on the READ path.** Measured: the archive stores
  **2,780** lines and the corpus ever sees **682** — 75.5% are bytes on disk no query
  can return. Moving that key to the write path was proposed and is **rejected
  here**: read-time dedupe already makes those lines unreturnable, so the only real
  benefit is disk (8.8 MB → 2.2 MB at three years), and the cost is that a heuristic
  which today can be retuned at read time becomes a permanent write-time partition.
  **The entire durable footprint of 346 KB of design was 7 archive lines under a
  handful of `(day, task)` keys, one truncated mid-word** — "keep only the LAST per
  key" would have discarded some. **Condition 38: if disk matters, run compaction
  offline over lines older than N days, relocating to a sibling file outside the
  corpus glob (P8), never on the write path.**
- **B4 — promotion is minting at write, never counting after the fact.** The obvious
  rule (*a log line delivered N times becomes a note*) was designed and killed by the
  telemetry. Only **4** archive/managed units in 349 tasks were delivered 5+ times,
  and here is the complete list: a brevity directive (n=11), two restart summaries
  (n=9, n=9), and a brainstorm prompt (n=6). **Delivery-count promotion would mint
  notes out of session noise.** §12 carries it with these four rows as the reason.

### 9.3 Always-loaded, every turn — the ceiling

Two columns: measured today, and the V2 ceiling. The right column is a **ceiling
with every block firing**, not a typical value.

| # | item | today (B) | V2 cap (B) | ~tokens | bound mechanism | O(corpus)? |
| --- | --- | ---: | ---: | ---: | --- | --- |
| 1 | `CLAUDE.md` | 19,311 | **uncapped** | 4,830 | none — see below | no |
| 2 | `SHARED_RULES.md` | 8,900 | **uncapped** | 2,225 | none | no |
| 3 | `AGENT_RULES.md` | 3,511 | **uncapped** | 878 | none | no |
| 4 | `CLAYRUNE_API.md` | 18,308 | **uncapped** | 4,577 | none | no |
| 5 | `MEMORY.md` index | 19,721 | **8,192** | 2,048 | demotion, logged (§9.1) | **no, after §9.1** |
| 6 | session log in prompt | 3,778 | **0** | 0 | moved to `SESSION_LOG.md` | no |
| 7 | `--- SYSTEM ---` awareness | ~4,096 | 4,096 | 1,024 | generated, fixed shape | no |
| 8 | continuity | 1,136 | 2,048 | 512 | fixed slots (`_CONT_MAX_*`) | no |
| 9 | position-capture directive | ~1,500 | 1,500 | 375 | fixed string | no |
| 10 | **NEGATION LEDGER** (20 lines) | — | **1,500** | 375 | `negation_ledger_max` | no |
| 11 | NEGATIONS reserve (4 slots) | ≤900 | 1,800 | 450 | reserve × 450 B | no |
| 12 | STANDING NOTES (topk 6) | ≤2,640 | 3,000 | 750 | topk × 500 B | no |
| 13 | TYPED HOP (expand 2) | ≤880 | 1,000 | 250 | expand × 500 B | no |
| 14 | PAST EXPLORATIONS (topk 2) | ≤800 | 800 | 200 | topk × 400 B | no |
| 15 | sibling activity (topk 3) | ≤1,000 | 1,000 | 250 | topk × 333 B | no |
| 16 | skills catalog (non-Claude) | varies | 4,096 | 1,024 | cap | no |
| | **CEILING** | **≈86,400** | **≈79,062** | **≈19,766** | | **none** |

**Condition 39 — the honest arithmetic, because the wrong number will be quoted.**
**86,400 → ~79,062 B is an 8.5% reduction, not 32%.** The upstream 58,920 B figure
was reachable only by capping rows 1–4 and by omitting the two blocks the negation
design adds. **The saving is not the point and the spec does not sell it.** The
point is that after the split **every memory-layer row has a named bound and a log
line, and the memory layer's ceiling is O(1) in corpus size** — the same ~19.8 KB
at 100, 5,000 and 50,000 memories — against a curated index that is otherwise
925 KB at 5,000 notes (38× budget).

**Condition 40 — rows 1–4 are reported as a finding, not capped by this spec.**
They are 50,030 B of the 86,400 B ceiling and they have no cap, no budget key and no
eviction rule of any kind. **R-1 is violated by five items; two are the memory
layer's.** Capping `CLAUDE.md` at 12,288 B would mean deleting 7 KB from the file
holding this project's load-bearing invariants (the `DATA_DIR` pollution rule, the
authority guard, the secrets-vault rules), and no proposed cap for these four was
measured — it was asserted. `SHARED_RULES.md` is additionally **shared across all
19 projects**, so capping it is a global decision and not the memory layer's to
make. File it as separate work; state the omission rather than let it read as an
oversight.

**A note on the per-turn cost of the negation layer.** Row 10 is +1,500 B and row 11
is +900 B over today. Row 6 gives back 3,778 B and row 5 gives back 11,529 B. The
memory layer's own rows total **≈19,840 B / ~4,960 tokens**, all bounded, all flat
in N.

### 9.4 What is fetched automatically, per turn

Three channels, auto-issued, no agent decision:

| channel | query | admission | slots | budget |
| --- | --- | --- | ---: | ---: |
| **NEGATIONS** | turn text matched against **trigger vocabularies only** — a deterministic gate, no scoring | reserved, trigger-gated | 4 | 1,800 B |
| **STANDING NOTES** | turn text, BM25 over the warm corpus | ordinary rank | 6 | 3,000 B |
| **TYPED HOP** | one hop off the standing hits, `superseded_by` prioritised | additive, never substituted | 2 | 1,000 B |
| **COLD PROBE** | fires **only** when the warm floor returns fewer than *k* hits above a score floor | 1 hit, appended | 1 | ~520 B |
| | | | **13** | **≤ 6,320 B ≈ 1,580 tokens** |

**Capped in BYTES, not slots.** The existing index cap records why: *"Line budgets
can't see this one because it is bytes."* A slot budget is not a token budget; the
snippet window plus a label is where the bytes actually are.

**Condition 41 — negations are admitted through a deterministic trigger gate with
reserved slots, never through a score multiplier.** `mc/memory.py:1604-1626`
already pulls positions out of `scored` before the top-*k* cut and re-inserts them
at the front: *"Positions never ride the ordinary ranking. The reserve, with its
coverage gate, is their ONLY admission path."* Extend the eligible class from
`position_*.md` to any unit carrying a negation record. The three properties in
§5.1 carry over unchanged.

**Condition 42 — the cold probe closes FC-5 at a bounded cost.** The cold FTS tier
was measured as the **only** tier returning the August artifact on all three replay
queries, in under a second — including the exact two-level-index proposal August
negated. It stays agent-initiated in general (the FTS index is 22 MB against 916 KB
of archive, and auto-injecting it would blow the budget for a channel that is
supposed to be on-demand), **but when the warm floor has already failed, exactly one
cold hit is appended.** Measured 3.2–3.4 ms, ~520 B, on a subset of turns. That is
precisely the 2026-09-06 condition: 7 units delivered, none naming the target.

**Ranking inputs, in order.** (1) admission class; (2) **BM25, unchanged** — global
IDF in the always-positive form, per-unit-class length normalisation; this is
measured machinery and the pre-BM25 scorer left *55 of 75 topic files never in a
top-3 for any task*, so do not touch it; (3) field folds: filename ×3 (today),
`description:` ×2 (new), `triggers:` ×6 (new, matching the existing position subject
boost); (4) standing multiplier: STANDING ×1.0, LAPSED ×0.6 with a caveat line,
OUTPACED → head substitution; (5) archive quota, live value 2, unchanged.

**Condition 43 — recency is explicitly rejected as a ranking input.** *Relevant if
nothing outpaced it* is not *relevant if recent*. A recency prior would systematically
demote exactly the small isolated strands INV-STANDING protects. **Supersession is
the only time signal that earns a place in the ranker, because it is a claim
somebody made, not a timestamp.**

### 9.5 What is fetched on demand, and its budget

Nothing on-demand is capped today: the search route and the memory-search skill
return what they return, and an agent reading a topic note with `Read` gets the whole
file — the largest in this corpus is 19.4 KB against a 3,404 B mean.

| fetch kind | today | V2 per-call cap | ~tokens | measured basis |
| --- | --- | ---: | ---: | --- |
| curated search | uncapped `k` | `k ≤ 10` × 440 B = 4,400 B | 1,100 | 8 hits rendered 3,466–3,580 B at `k=6+2` |
| cold FTS tier | `session_fts_cold_k` 5 | 5 × 520 B = 2,600 B | 650 | measured 2,605 / 2,616 B JSON |
| read one topic note | whole file | 8,192 B, head+tail, truncation logged | 2,048 | mean 3,404 B, median 2,660 B, max 19,400 B |
| read a position file | whole file | 4,096 B | 1,024 | 15 files / 18,165 B = 1,211 B mean |
| chain walk (intermediate nodes behind a substituted head) | n/a | 4,096 B | 1,024 | redirect stub ~1.1 KB |
| **per call** | — | **≤ 8,192 B** | **≤ 2,048** | |
| **per turn** | — | **≤ 3 calls / 24,576 B** | **≤ 6,144** | |

**Condition 44 — the per-turn fetch cap is a budget, not a lock.** The fourth call
returns its content *plus* a line saying the turn's fetch budget is spent and the
agent should synthesise. An agent that keeps fetching instead of concluding is the
failure mode this bounds; a hard refusal would be worse than the disease.

**The honest worst case:** 79,062 + 24,576 = **103,638 B ≈ 25,900 tokens**, which is
**above** today's always-loaded-only ceiling of 86,400 B. The upstream claim that the
worst case lands below today's ceiling does not survive the corrected arithmetic. The
defensible statement is narrower and still worth making: **the always-loaded floor
falls 8.5% and stops growing with the corpus, and the on-demand tier acquires a cap
where today it has none at all** — today's true worst case is unbounded.

### 9.6 Binding retrieval to the turn — the mechanism, server-side

**Condition 45 — the per-turn refresh is composed server-side at the four stdin
write sites, and it REPLACES the dispatch floor's blocks for turns after the
first.** Clayrune already builds the user-message envelope written to `proc.stdin`
at four sites in `mc/blueprints/agent_routes.py`. Recomputing the negation and notes
blocks for *that message* and prepending them needs no hook, works on every
provider, and is where the code already is. Replacing rather than appending makes it
**net-zero in the budget table** and removes the largest unpriced item in the
upstream composition.

A `UserPromptSubmit` hook is the cheap option and may ship alongside, but it is
**not** the guarantee: it is unverified on this path (every observation of it firing
is on a fresh dispatch), and it is Claude-CLI-only.

**Cost, named rather than waved through.** Per-message retrieval raises corpus-cache
miss-rate exposure; `_mem_corpus` globs and re-tokenises the whole vault on a miss,
which is §13.3's first break. **Graceful degradation, specified:** if the cost does
not hold, refresh only when the message differs materially from the one the current
blocks were keyed on — which still fixes the five-day-stale case, which is the case
that matters.

---

## 10. Enforcement of structure at the write path

### 10.1 The four gates

| gate | where | when it runs | posture |
| --- | --- | --- | --- |
| **G1 — mint / update** | new `POST /api/project/<id>/memory/note` (+ `PATCH`) | every attended note write | **fail-closed**, 4xx with a machine-readable body |
| **G2 — raw-write deny** | `PreToolUse` hook on `Write`/`Edit` whose path is inside a vault dir | every agent tool call | **fail-open on error, deny on match** |
| **G3 — corpus admission** | `_mem_corpus` (`mc/memory.py:1388`) | every read-floor build | **fail-open**: quarantine the unit, log, never raise |
| **G4 — background writer** | `write_position`, `write_continuity`, Scribe / checkpointer | every autonomous write | **fail-open**: repair what is repairable, log, never raise |

**G1 is the gate.** It is where a malformed note cannot land, and the only place a
violation can be reported to something able to fix it. Copying the existing 413
shape verbatim — `current_bytes` / `budget_bytes` / `overflow_bytes` plus a remedy
string, with *"a refused write leaves the on-disk file byte-for-byte untouched"* —
buys the property the link checker never had: **the error is delivered to the actor
that caused it, at the moment it acted.** That, not the check itself, is the
difference between this design and `tools/memory-link-check.py`.

**G2 is what makes G1 non-optional.** Without it the mint endpoint is a polite
suggestion, and §1.4 measures what happens to those.

**G3 is the backstop for writers we do not own.** The engram MCP server writes 77%
of the corpus through machinery Clayrune neither controls nor should try to. G3
catches whatever G1 and G2 miss, at read time, for every writer that will ever
exist. It repairs nothing and is fail-open by construction — the correct trade on
the dispatch path, where raising means *no memory at all* rather than slightly wrong
memory. `mc/memory.py:1400-1401` already returns `[]` on corpus load failure; **that
is the failure G3 must not join** (Condition 47).

**G4 exists because `write_position` has no validator today** (§5.1) and because it
takes no write lock. V2 adds fields to the one file class with no check at all.

### 10.2 Disposition per violation class

"Auto-repair" is used only where the repair is deterministic and the alternative is
losing an edge.

| class | detected by | disposition | why this one |
| --- | --- | --- | --- |
| missing / unparseable frontmatter | G1, G3 | **G1 reject; G3 quarantine** | 231 of 235 notes (98.3%) already parse, so rejection costs almost nobody anything. G3 cannot reject — that would delete a note from retrieval over a YAML typo. |
| missing required field (`name`, `description`, `type`, `origin`) | G1 | **reject, 422, body names the field** | `name` and `description` are at 100% coverage. `type` and `origin` are supplied by the endpoint, not the author, so rejecting a field the server itself fills is impossible by construction. |
| **class A** — `.md` inside the wikilink | G1, G3 | **auto-repair, silently, at resolution** | It is a canonicaliser bug (R2). There is nothing for an author to fix and no information in reporting it. |
| **class B** — rename without forwarding | G1, on the rename | **reject until `aka:` is written** | The rename is the only moment the information exists. Afterwards nobody can reconstruct which stem used to point here. |
| **class C** — cross-vault target | G1 | **reject at write, naming the resolving vault; record as `external_ref:`** | §12 rejects a cross-project graph. Accepting a link we will never traverse is how we got five of them. |
| **class D** — target resolves nowhere | G1 | **reject, 422, listing near-miss candidates** | The one case where the author has the answer and the machine does not. Near-miss candidates make the fix one keystroke. |
| broken pointer in the curated index | G1 on the index routes | **reject, same 413 shape** | Measured: 190 markdown pointer links in curated regions, **3 broken** — one missing note, two escaping the vault with `../../../`. Those routes already validate byte size, so the call site exists. |
| duplicate / near-duplicate identity | G1 | **reject; require an explicit slug** | Zero exact collisions today. R7 extends this to long-prefix near-misses, which is the case that actually occurred. |
| **edge type unknown** | G1 | **accept, record as untyped, never reject** | P6. A closed vocabulary enforced on day one against a corpus with no vocabulary rejects almost every write. |
| `origin` / `verified` mismatch with session identity | G1 | **overwrite with the server-derived value; never reject** | P5. The caller does not get a vote, so there is nothing to reject. |
| expired `holds_while` / `revisit_if` | none — **reported, never gated** | **fail-open by design** | OKF reports `StaleCount` and deliberately excludes it from `GatePassed`, so a bundle with 40 expired concepts passes `--strict`. Right call: staleness is a judgement about the world, and a build that fails because reality moved is a build whose check gets disabled. |

**Condition 46 — `supersedes` is the one required type, and only when the detector
flags a predecessor.** That is not vocabulary enforcement; it is a forced binary
answer (`supersedes:` or `unrelated_to:`) with the candidate pre-supplied by the
server. Under Condition 22 the *write* is not blocked on the answer — only headship
is. **A field an author *may* fill is a field authors do not fill; the 94% is what
that measures.**

**Condition 47 — G3 validates only units whose file changed since the last build.**
This requires the per-file `(mtime_ns, size)` corpus cache that §13.3 names as the
first break. **G3's design depends on that fix; do not let G3 ship onto the
whole-directory signature**, or an already-bad scaling property gets worse and then
gets blamed on validation.

### 10.3 Report-only until measured, per gate, per project

**Condition 48.** `memory_gate_mode` defaults to `report` for every gate in every
project. A gate never observed against the real corpus does not get to refuse
writes. Flipping to `enforce` is a **decision with a measured false-rejection rate
in front of it**, not a milestone in a build plan. The same discipline governs
`negation_interrupt_mode` (Condition 15). The false-rejection rate cannot be
estimated in advance and no number in this spec should be read as one.

### 10.4 Behaviour during partial migration — invariants, not a plan

1. **V1 notes stay readable and retrievable, indefinitely.** A note without V2
   frontmatter is a valid corpus unit. `_mem_corpus` already tolerates anything —
   `cls` comes from the filename prefix and frontmatter is never parsed for topics —
   and that tolerance is preserved deliberately. **There is no flag day.**
2. **G1 and G2 apply to writes, not to reads.** Nothing already on disk can become
   invalid.
3. **G3 quarantine is per-unit and never fatal.**
4. **Missing V2 fields degrade to today's behaviour, never to an error.** No
   `origin` → `legacy` → read as attended-equivalent. No triggers → BM25 only,
   exactly as today. No edge type → `see-also`, exactly as today. **Every
   degradation path lands on the current system**, which is the property that makes
   a partial migration safe to stop half-way and leave stopped.
5. **Re-running any phase over an already-migrated vault is a no-op.**

### 10.5 What enforcement can and cannot claim

**Condition 49 — the claim is made in the weak form.** A `PreToolUse` deny covers
the agent's tool calls. It does not cover the engram MCP server, a script the user
runs, or an editor. G3 is the backstop and G3 is fail-open. So the honest claim is
**"malformed notes are caught before they are ranked"**, never *"malformed notes
cannot land"*. The same weakening applies to R4: a rename made by `mv` or by an
editor cannot be refused, only detected later.

**Condition 50 — enforcement bounds the RATE, not the STOCK.** Measured rot rate:
6 → 17 broken links in the ~28 days after 2026-08-09. Three years at that rate,
ungated, is roughly **400 broken links**. Gated at mint, class D goes to
approximately zero new ones and the existing 8 stay 8. That is the clearest
quantitative case for enforcement in this spec, and it is also its limit:
**enforcement stops the bleeding; it does not heal anything already written.**
Every "reported" disposition in §11 must be read that way — reports are fine, and
they must not read as remedies.

---

## 11. Migration — 235 notes, 19 vaults, re-runnable, lossless

Design constraints, in priority order:

1. **Nothing is deleted.** Not the 20 unreachable nodes, not the 8 class-D links,
   not the phantom vaults' 82 archive lines.
2. **Re-runnable.** Every phase recomputes desired state and writes only the diff.
   Precedent: `tools/backlog-journal-export.py` was built re-runnable for the same
   reason.
3. **No phase requires a human to read 235 notes.** A migration that needs curation
   is the thing being removed.
4. **Both formats coexist for the whole migration** (§10.4).

**And the reframe that changes the size of the job.** This is **not** "audit 235
notes." Most of it is auto-convertible (below), and the large half is **minting the
~436 pointer-free curated prose lines** identified in §1.2 — content that exists
nowhere else and cannot be demoted at any hop depth.

### 11.1 What is auto-convertible — most of it

Measured across 235 topic files in 19 vaults:

| field | present today | migration action |
| --- | ---: | --- |
| frontmatter block | 231 (98.3%) | 4 files get one synthesised |
| `name` | 231 (100% of those) | kept, and **promoted from decoration to a resolution alias** (R3) |
| `description` | 231 (100%) | kept; feeds default triggers (Condition 27) |
| `type` | 53 (23%) | derived from the filename prefix — `arch_`, `decision_`, `discovery_`, `feedback_`, `reference_`, `research_`, `project_` are an existing consistent convention. Unmatched → `type: note`. |
| `origin` | 0 | stamped `legacy` (§4.3); 16 upgradeable from the agent-log join |
| `generated` | 0 | `by: legacy:import`, `at:` = file mtime |
| `verified` | 0 | empty. **Never synthesised.** Fabricating human verification during a migration is the exact failure the comparison names. |
| edge type slot | 16 of 283 typed | all untyped edges become `see-also` explicitly. Lossless re-encoding: it is what 55.8% already say in prose and what the other 38.5% already default to. |

**The 37 drifted `name:` fields are not repaired.** Under R3 they become working
aliases instead of rot. This inverts the 2026-08-09 decision, which
hand-canonicalised 19 drifted names — **and 37 have drifted since**, which is the
evidence that hand-canonicalisation does not hold. Making the field load-bearing is
what stops the drift; rewriting it again is what we already tried.

### 11.2 Phase order

| phase | does | needs a human? | re-runnable |
| --- | --- | --- | --- |
| **M0** | R2 canonicaliser fix ships; `write_position` takes the write lock; the four unregistered config keys are registered | no | n/a (code) |
| **M1** | vault registry populated (R6); phantom and split vaults **reported**, not merged | no | yes |
| **D0** | default `triggers:` from `name` + `description`, df-gated per Conditions 28/29 | no | yes |
| **D1** | run the **retrievability gate** over all 235 — **the failures are the work list** | no | yes |
| **M2** | frontmatter synthesis: `type`, `origin: legacy`, `generated`, edge-type slots | no | yes |
| **D2** | the nine addressable broken links: class A by R2 (no file edited), class B `aka:` from git history, class C → `external_ref:` | no | yes |
| **M4** | mint-gate overlap detector over all 235 in **report-only** mode | no | yes |
| **M5/D3** | `supersedes` declared on M4's flagged pairs | **yes**, on N pairs | convergent (§11.5) |
| **D4** | **mint the ~436 pointer-free curated prose lines** — the large half | no (model pass, gate-checked) | yes |
| **M6** | G1/G2 switch from `report` to `enforce`; interrupt from `report` to `advisory` | decision only | n/a |
| **M7** | phantom-vault archive import (line-hash dedupe); split-vault merge of **non-colliding** stems | reported collisions only | yes |

**D1 is the phase that makes the cost measurable before it is paid.** It replaces
"audit 235 notes" with "audit the N that fail," and only gate failures get
hand-authored `triggers:`. **M4 does the same for supersession:** it converts "a
model pass over 235 notes" into "a model pass over the N pairs the detector flags."
Both are the same discipline, and it is the discipline Condition 48 asks the gates
to adopt.

**M6 is a switch, not a milestone.** Everything before it runs in report-only mode,
so the enforcement design is measured against the real corpus before it is allowed
to refuse anything.

### 11.3 The one hand edit, named as one

**Condition 51 — the design is curator-free prospectively; the historical instance
is repaired by hand, and the spec says so in those words.** The 2026-09-06 replay's
steps 4 and 7 depend on a **hand-written** `supersedes` line on
`project_memory_system_redesign.md` pointing at a minted node for the August
redesign. One frontmatter line, on the vault's second-hottest unit (n=129, 37%),
which currently tells every agent that the memory-design engagement closed in May.
It is the highest-value single edit in the corpus and it is **not** evidence that
the mechanism is retroactive.

Alongside it: the 14 rows of the August artifact's §11 become negation records —
~3 KB on disk, one scripted pass over an existing table, done once.

**Condition 52 — M5 has a stated fallback.** If M4 flags a large *N*, declare
nothing and accept undeclared historical chains. **The design works prospectively
without them.** Do not let a large flag list turn the migration into the curation
job it exists to remove.

### 11.4 What is dropped, and what is deliberately left alone

- **Nothing in a note's body. Ever.**
- **Five cross-vault links** lose their *edge* status and become `external_ref:`
  entries carrying vault and stem. The text stays; the false promise of a
  traversable edge goes.
- **Two class-A links** stop existing as breakages and start resolving. Not a drop,
  a repair.
- **The 8 class-D links are left in place, unrepaired, and reported.** They are
  prose asserting a relationship to something that does not exist. Deleting the
  reference destroys the author's statement; inventing a target is worse. They
  become a standing report that G1 refuses to *add to*, which is how the count stops
  growing (Condition 50).
- **The 20 unreachable nodes get nothing.** 18 of the 20 are two vaults whose
  `MEMORY.md` contains no pointer to any of their own notes at all — curation gaps,
  not traversal gaps. **Writing 20 index pointers to "fix" them would enshrine the
  exact dependency R-2 exists to remove.** They acquire default triggers from
  `name` + `description` like every other note, at zero authoring cost, and that is
  the entirety of their remedy.
- **The three phantom vaults' 82 archive lines** are appended to the parent
  project's archive with an *(imported from a stale vault key)* tag, and the source
  directories are left on disk untouched. Re-runnable via line-hash dedupe.
- **Colliding stems in the split vault are reported, never merged automatically.**
  Two notes sharing a stem in two vaults is the one case where the machine cannot
  know which is current, and guessing there loses content.

### 11.5 The one non-idempotent phase, stated

**M5 is convergent, not idempotent.** A model pass over flagged pairs is re-runnable
but not deterministic; a second run can produce a different declaration. Mitigation:
**M5 writes only where no `supersedes` or `unrelated_to` already exists**, so a
second run cannot overwrite a first answer. Every other phase is idempotent.

---

## 12. Explicitly not worth building

Blunt, as asked. Every rejection is argued from a measurement or from an incident
this project already had.

1. **Hop 2, hop 3, or unbounded traversal.** §8. Seeds do not scale (~137 ceiling),
   density is a per-note authoring constant (1.10 links/note), and 88% of growth is
   edge-incapable by construction. *Enforcement-side reason on top:* every additional
   hop multiplies the blast radius of a **wrong** edge. Today a mistyped `supersedes`
   misleads one neighbour; at depth 3 it redirects a subtree — and we are about to
   write typed edges for the first time in this corpus' history.
2. **An orphan gate.** Condition 25. It would fail 107 of 235 notes on day one and
   make "no human wrote a pointer to me" a build error.
3. **Embeddings, a vector index, or any graph DB.** Standing negations from the
   August design (*"weakest on exact tokens"*, *"a database process for 76 markdown
   files belonging to one person"*), plus a standing deferral whose gate is *"build
   search-precision telemetry first"* — and **that telemetry still does not exist**:
   the delivery sidecar records what arrived and nothing records what a task needed
   and missed. **Re-opening a deferral whose gating measurement has not been taken is
   itself the re-derivation failure this spec exists to eliminate.** Reopening
   requires a superseding position with a measurement attached, not a design
   document. And §1.3's problem is arrival *vocabulary*, which explicit triggers
   solve deterministically and cheaply.
4. **A recency prior in ranking.** Condition 43.
5. **In-degree, delivery count, or any residency signal in RETRIEVAL RANK**
   (Conditions 24/26) — and never delivery count in **residency** either
   (Condition 31).
6. **Delivery-count promotion of log lines into notes.** Designed, then killed by
   the telemetry. The complete list of qualifying units at *n* ≥ 5 over 349 tasks is
   two restart summaries, a brevity directive, and a brainstorm prompt (§9.2 B4). It
   would mint noise.
7. **Typing the existing 258 wikilinks.** 94% untyped see-also is *correct* —
   see-also is a real relation, and with 46% orphans the scarce thing is edges, not
   edge types. Add exactly one typed relation, in frontmatter (Condition 17).
8. **A general typed-relation ontology** (`causes`, `refines`, `contradicts`,
   `exemplifies`…). The corpus produced **one** typed edge in 258 when the cost was
   writing a single word. Nothing suggests a richer vocabulary would be used.
9. **A closed edge-type vocabulary enforced on day one.** P6. Sixteen typed
   relationships in 283 mentions is a sample too small to design a vocabulary from.
10. **Mining negations from transcripts.** Most "no" in a conversation is not a
    decision ("no, use tabs"), so a scan buries the twenty entries that matter under
    noise. That reasoning is already in the codebase and is not being reversed.
11. **Model evaluation of `revisit_if`.** A model asked *"has this condition
    changed?"* against ~400 prose clauses every week is an unbounded token cost with
    no ground truth — and the positions-review path already cites the rule that
    alerts on flat continuations train the reader to ignore them. Machine predicates
    for the measurable minority; the human for the rest.
12. **A second store, or a negations database.** Positions *are* the store.
    Extending the frontmatter is the entire data-model change.
13. **A graph database.** 235 nodes, 258 edges, mean degree 1.26. This fits in a
    Python dict and already does. At 5,000 topic notes and constant density it is
    ~5,500 edges. *Enforcement-side reason:* a second store is a second thing that
    can drift from the files, and this entire spec exists because our one existing
    derived structure drifted silently.
14. **Auto-summarisation of notes.** We have the incident: `reply_summarize_enabled`
    rewrote finished replies to Haiku summaries, is OFF, and is not to be re-enabled.
    And `docs/MEMORY_SYSTEM.md` states the governing property — *"condense only
    compresses, it doesn't verify. A confidently-wrong agent conclusion becomes
    durable, cross-session, self-reinforcing memory (this happened)."* **It is also
    directly counter to the negation requirement:** the detail a summariser drops
    first is exactly the reasoning behind a rejected option, because that reasoning
    reads as digression.
15. **A UI for curation.** The premise of this spec is that memory must survive
    *without a human curating it*. A UI is a tool for a curator; building one bets
    the curator will exist, and the measured evidence is that they do not — 17 broken
    links, 37 drifted `name:` fields, 3 broken index pointers, three phantom vaults,
    and a link checker never once run automatically, all accumulated on a project
    whose operator is unusually diligent about exactly this. **A read-only report
    surface is fine and cheap**; anything requiring a human to act on it inherits the
    link checker's fate.
16. **A promote/demote mover or a residency dashboard.** Declined 2026-08-24 on
    measurement over 189 tasks. **Flagged, not re-proposed:** that position's own
    reopening clause names *"resident index pressure against its 24 KB cap, which is
    at 17.2 KB today"* and the file measured **23,627 B** on 2026-09-06 — the clause
    is satisfied. Reopening it is a decision made by POSTing a superseding position
    with the measurement attached, not by quietly building the mover. Condition 34
    is what makes clauses like this trip automatically in future.
17. **A CI job or a scheduled run of the link checker as THE enforcement.** Named
    explicitly because it is the *obvious* fix and it is the wrong one: it reports to
    a log nobody has to read, hours after the write, to an actor that no longer
    exists — and CI cannot even see the vault (§1.4). **If the only enforcement we
    ship is a scheduled checker, we will have rebuilt `memory-link-check.py` with a
    cron entry and the count will still grow.** Acceptable *in addition* to G1/G2 as
    a drift detector; never as the enforcement.
18. **Cross-project global memory.** Tempting — five broken links are cross-vault and
    point at genuinely useful notes. Rejected on four grounds: **(a)** we cannot keep
    one vault's identity straight yet (§4.4 R6: three phantom vaults, one project
    split across two encodings); **(b)** provenance does not compose, because
    `trigger_type` is per-session in one project's log, so a note crossing a boundary
    loses the only context that makes its stamp meaningful; **(c)** the blast radius
    of a bad note becomes every project — the authority-guard lesson in a different
    costume, where global scope was the amplifier and the durable fix was a *global
    suppression* list, not more global sharing; **(d)** the measured need is 5 links
    out of 258 edges (1.9%), and `external_ref:` preserves the human-readable
    reference at approximately zero cost. Cross-project *learning artifacts* already
    have their own governed channel.
19. **Tuning `read_floor_archive_quota`.** Not binding: the archive fills 0.44 of its
    2 permitted slots per task, and a standing position measured 188 real tasks at
    quota 0/1/2/3/6 with identical results. Leave it at 2; stop tuning it.
20. **A byte cap on the session log.** An entry-count ring is the right bound
    (§9.2 B2).
21. **Rewriting the 37 drifted `name:` fields.** §11.1. We did this in August for 19
    notes and 37 have drifted since.
22. **Semantic retrieval as an answer to scale.** None of the breaks in §13.3 is a
    ranking-quality problem, and embeddings make the corpus-rebuild break **worse**,
    not better.
23. **Fixing the byte half of the condense deadband on its own.** The byte trigger
    (24,576) sits above the byte floor (23,552) so the floor always evicts first and
    the byte trigger is dead; the line trigger (160) sits below the line floor (185)
    and is live. **After the split the managed region is not in `MEMORY.md` at all**,
    so both halves need re-deriving against the new file rather than patching. A
    byte-only fix corrects the half that already works.

---

## 13. Mandatory honesty — the cost at 5,000 memories and 3 years

### 13.1 The canonical growth table

One table, used by every section. **The denominator is stated on every row**, and
every per-corpus cost in this spec is priced against the **minting-ON** rows —
because minting is what this design turns on.

| class | scope | today | +3 years (1,095 d) | crosses 5,000 |
| --- | --- | ---: | ---: | --- |
| archive units (post read-time dedupe) | 1 vault | 682 | ~7,650 | **~1.8 years** |
| archive lines (raw, pre-dedupe) | 1 vault | 2,780 | ~29,500 | ~0.25 years |
| topic notes, current (broken) write path | 1 vault | 86 | ~920 | ~16 years |
| topic notes, current write path | 19 vaults | 235 | ~1,400–2,900 | not at this rate |
| **topic notes, minting ON** | 1 vault | 86 | **~7,650** | **~2.1 years** |
| positions | 1 vault | 15 | ~135 | — |
| positions | 19 vaults | 46 | ~400 | — |

Two things follow, and the second is uncomfortable:

1. **5,000 memories is not speculative.** On today's design it arrives in **under
   two years**, in the archive class, with no design change at all — and that class
   is 87% of the corpus and 5.6% of the delivered value.
2. **The fix for R-2's write side is what creates the 5,000-node problem.** If
   closing an artifact mints a node, topic growth converges on the task rate and the
   topic class also reaches ~7,650 in three years. **Any projection that assumes
   0.76 notes/day is assuming the write path stays broken.** The upstream estimate
   of 1,400–2,900 notes at three years is a projection of the *unfixed* write path;
   costs priced against it are priced against a corpus 2–5× smaller than the one
   this design creates.

### 13.2 The cost table at 5,000 memories

Extrapolated from a **measured** constant, not a guess: `_mem_corpus` tokenise +
index throughput is **14.24 MB/s**, stable across 19 vaults from 100 KB to 580 KB of
unit text.

| metric | today (measured) | at 5,000 memories, ~year 2 | how it scales |
| --- | ---: | ---: | --- |
| index size, one-line-per-note convention | 19,721 B | **925,000 B** | linear, 185 B/note |
| index size, after §9.1 | — | **≤ 8,192 B** | **constant** |
| corpus text volume | 579,548 B | ~17.0 MB | linear |
| `_mem_corpus` cold rebuild | **37.1 ms** | **~1.19 s** | linear at 14.24 MB/s |
| BM25 query, warm cache | **4.3–8.9 ms** | ~95–200 ms | linear in units |
| read-floor block rendered | **3,466–3,580 B** | **same** | **O(1)** — the snippet window makes it a function of `topk`, never of N |
| FTS index on disk | 22.0 MB (indexing 685 MB of transcripts, 3.2%) | ~210 MB per vault | 3.2% of transcript bytes |
| FTS cold query | 3.2–3.4 ms | ~15–40 ms | sublinear (FTS5 inverted index) |
| always-loaded tokens/turn | ~21,600 | **~19,766** | **constant** |
| memory-layer share of that | ~7,900 | **~4,960** | **constant** |
| tokens per on-demand fetch | uncapped | **≤2,048** | constant |
| negation records on disk | 18 KB | ~135 KB corpus-wide | linear, irrelevant |
| negation resident cost | ~2.4 KB | **~3.3 KB** | **constant** |
| interrupt matcher terms | — | ~3,200 | **linear in negations** |
| G1 / G2 / G4 per write | — | microseconds | **O(1) in N, forever** |
| G3 per corpus build | — | proportional to **changed** files (Condition 47) | O(changed), not O(N) |
| retrievability gate, per write | — | one search, O(N) | negligible |
| retrievability gate, full sweep | — | O(N²) ≈ 25M unit-scorings | tens of seconds, **scheduled, never per-write** |
| head-of-chain closure | — | tens of chains, depth ≤ 4, at corpus build | free |
| **human curation calls required** | — | **0** | that is the requirement |

### 13.3 Where the design breaks first, in order

**Break 1 — `_mem_corpus` cache invalidation, at ~1,500–2,000 topic notes, about
one year with minting on.** The cache signature is a tuple over **every `*.md` in
the vault** (`mc/memory.py:1395-1399`), keyed on the whole signature, so any write to
any file invalidates all N units and re-tokenises the entire vault — and Step-6
checkpointing writes `MEMORY.md` on an 8 KB transcript cadence, several times per
session, at ~24 sessions/day. Today that costs 37 ms and is invisible. At 17 MB it
costs **~1.19 s per dispatch, on the critical path, before the agent sees its first
token**, plus a `glob` + `stat` over 5,000 files merely to *compute* the signature
that says nothing changed.

**Condition 53 — the per-file `(mtime_ns, size)` corpus cache is a PREREQUISITE, not
a nice-to-have.** Three independent workstreams found this break separately; G3
depends on it (Condition 47) and §9.6's per-turn refresh raises exposure to it.
**Note this break is independent of the split** — moving the log out of the prompt
does not move it out of the corpus.

**Break 2 — recall from the fixed-count index. This is the largest unpriced risk in
the spec and it is stated in those words.** §9.1 caps pointers at ~30 lines. The
greedy seed-set measurement says 29 index lines cover 76 of 86 topics *at the hop we
already ship*, so 30 is not arbitrary — it is **the measured coverage point for 86
notes**. At 5,000 notes **nobody has measured what 30 seeds cover**, and the ground
truth is explicit that all such savings are upper bounds because *"the delivery
sidecar records what arrived, never what a task needed and missed."*

**Break 3 — the write-act interrupt's false-positive rate.** Unmeasured, and growing
linearly in exactly the quantity this design tries to grow: ~400 records × ~8
triggers ≈ 3,200 terms matched against whole document bodies. Conditions 15/16 are
the containment; without them this is the mechanism most likely to be disabled by an
annoyed operator, at which point the negation layer loses its only session-age-immune
backstop.

**Break 4 — FTS incremental reindex at startup.** The per-file `(mtime, size)`
fingerprint is exact and cheap per file, but at three years it runs over ~3,000
transcript files at startup. The query stays fast; the startup scan does not.

**Break 5 — disk.** FTS ~210 MB per vault; transcripts ~6.6 GB at three years at the
measured 6.0 MB/day. Not a correctness problem, but it is the number an operator will
notice first.

### 13.4 What the design does not claim

1. **FC-2 is waived, not met, and here is the argument.** The archive class stays
   retrievable (4.8% of slots, 87% of the corpus) and structurally edge-incapable —
   the exact combination the forensics names as guaranteeing silent orphans. The
   defensible position: **archive units are a log, not knowledge claims**; their
   retrievability is a lexical convenience worth 4.8% of slots; and this design's
   answer to *"a log line was the only record"* is that **minting means it is no
   longer the only record**. Making the log class edge-capable would mean attaching a
   graph to ~7,650 units of prompt echo, which §12.6's telemetry says is noise. The
   waiver is stated so no future reader records it as an oversight.
2. **Recall is unmeasured and unmeasurable from what we record.** The retrievability
   gate proves self-retrieval under a note's own vocabulary and nothing more.
3. **Head substitution can in principle hide something the agent wanted.** Mitigated
   by attribution and on-demand chain fetch (§9.5), not eliminated.
4. **`LAPSED` is only as good as the conditions people declare**, and today zero
   topic notes declare one. If the field goes unused, `LAPSED` is dead code and
   standing reduces to STANDING-or-OUTPACED. **That is an acceptable degradation and
   it is stated as one rather than assumed away.** The `holds_while` grammar covers
   roughly 4 of 15 live positions today (§4.5).
5. **The 5,000-node graph projection assumes the authoring convention holds.** If
   typed supersession changes how people write links, density changes and §8.4 must
   be re-run. Condition 34's first falsifier is the trip wire.
6. **Enforcement bounds the rate, not the stock** (Condition 50), and G2's coverage
   is not provable (Condition 49).
7. **The `legacy` provenance exception is a judgement call.** ~14% is an upper bound
   on how much autonomous-origin content it lets through, derived from a 500-entry
   log that has already rolled. Defensible; not proven.
8. **Three of the delivery and enforcement mechanisms are Claude-CLI hooks** (the
   write-act interrupt, G2, and the optional `UserPromptSubmit` refresh). On every
   other provider they degrade to today's behaviour. The *delivery guarantee* does
   not, because §9.6 is server-side — which is the whole reason Condition 3 chose the
   server-side path.
9. **Nineteen vaults, one operator, one authoring style.** The 98.3% frontmatter
   coverage that makes this migration cheap is a property of *this* corpus, largely
   because one MCP server wrote 77% of it in one shape. **Nothing here predicts a
   stranger's vault**, and a fresh install has none of those notes at all.
10. **The curator is not fully gone; it is bounded and named.** Five residual human
    touchpoints, all bounded: the mint RESOLVE answer (one word, on an attended turn);
    `pin: true` (capped at 5); M5's flagged pairs (with Condition 52's fallback of
    declaring nothing); the one hand edit in Condition 51; and the reported-only
    classes in §11.4. **Everything else is machine-decided or machine-defaulted.**

### 13.5 What the next redesign has to change

One change, and it addresses breaks 1 and 4 together: **persist the corpus.**
Tokenised units, term frequencies and BM25 statistics move into SQLite beside the
FTS index — which already proves the pattern at 22 MB, incremental,
`(mtime, size)`-fingerprinted, and surviving restarts. That converts a 1.19 s
O(corpus) rebuild into an O(changed files) update and buys the next order of
magnitude.

At that point `MEMORY.md` stops being a file the machinery maintains and becomes a
**materialised view** over the store — rendered to its cap at dispatch from whatever
currently ranks highest, rather than *edited toward* the cap by demotion. That is a
larger change than this spec should attempt, and it is the correct next one.

---

## 14. Minimum viable cut

**In scope for V2:**

- The record superset (§4.1, §4.2) with `expires_when` as a permanent read alias.
- Server-stamped `origin` / `generated` and **derived** `verified[]` (§4.3), with the
  `legacy` exception and its ~14% bound documented in code comments.
- Identity rules R1–R7 (§4.4), including the R2 canonicaliser fix and the vault
  registry with register-on-first-use.
- `holds_while` grammar + registry, evaluated at corpus build, cached on the corpus
  signature (§4.5).
- The four config-key fixes and all thirteen new keys registered in both the defaults
  dict and `_CONFIG_EDITABLE_KEYS` (§4.6).
- Negation obligation with accounted waivers (§5.2); the Negation Ledger (§5.3); the
  write-act interrupt **in `report` mode only** (§5.4).
- `supersedes` declared by the successor; head substitution with dedupe-by-head;
  materialised negation block (§6.2–§6.4).
- Deterministic mint trigger; mint WRITE/RESOLVE split (§6.5).
- Default triggers with phrase exemption and per-class df (§7.2, §7.3); the
  retrievability gate at write and on a schedule (§7.4).
- The index/journal split, the 20-entry ring with its log line, and the demoter's
  refusal-to-demote-targetless-lines rule (§9.1, §9.2).
- Per-turn refresh **server-side at the four stdin sites** (§9.6).
- G1–G4 in `report` mode (§10), with G3 gated on Condition 53's per-file cache.
- `write_position` takes the write lock; position frontmatter validated at write and
  at corpus build.
- Migration phases M0–M2, D0–D2, M4 (§11.2).
- The three falsifiers POSTed as positions (Condition 34).

**Out of scope, deliberately deferred:**

- Flipping any gate or the interrupt to enforcing/blocking — gated on measured
  false-rejection rates (Conditions 15, 48).
- The 8,192 B index cap as a live value — terminal, gated on D4 (Condition 37).
- D4 itself (minting the ~436 orphan prose lines) — the large half, sequenced after
  D1's work list exists.
- M5's model pass and M7's vault merges.
- Persisting the corpus in SQLite (§13.5).
- Capping rows 1–4 of the budget table (Condition 40) — separate work, and
  `SHARED_RULES.md` is a global decision.
- Everything in §12.

---

## 15. Condition register — the twenty must-fix items, and where each is closed

| # | sev | issue as raised | resolution | § / Condition |
| --- | --- | --- | --- | --- |
| M1 | S1 | Everything resident is frozen at dispatch; the one per-turn fix is unverified | Server-side per-turn refresh at the four stdin sites; hook optional, never the guarantee; provider scope stated | §9.6, Cond 3, Cond 45 |
| M2 | S1 | The write-act interrupt is a 3,200-term hard block with no df gate and no measured precision | Ships `report`, then `advisory`; phrase-matched conjunctively; ≤2 hits/turn; every fire and withdrawal logged | §5.4, Cond 15, 16 |
| M3 | S1 | The index needs a residency ranker that §7.1 forbids and §5.3 already uses | Residency vs retrieval-rank distinction written down; membership = mint-recency + head-of-chain only, never delivery count; third resident surface deleted | §7.5, Cond 30, 31, 13 |
| M4 | S1 | The mint gate cannot be both fail-closed and a background Scribe act | WRITE (fail-open, `supersedes: unresolved`) split from RESOLVE (binary question on next attended turn); detector drops the delivery-count condition | §6.5, Cond 22, 23 |
| M5 | S1 | FC-1 satisfied by a model judgement where it demanded a guarantee | Deterministic mint trigger; the model's judgement is additive only; a thin node is still a graph citizen | §6.5, Cond 21 |
| M6 | S1 | `## Binding constraints (human-authored only)` is a curator in the always-loaded prompt | Section not built; binding constraints are `CLAUDE.md` / `AGENT_RULES.md` content; index becomes 100% machine-maintained | §7.5, Cond 32 |
| M7 | S2 | The df gate destroys arrival vocabulary for the exact subject that failed (`memory`, DF 17.8%) | df gate applies to single terms only; multi-term trigger phrases exempt; auto-emitted bigrams; df computed per unit class | §7.3, Cond 28, 29 |
| M8 | S2 | FC-5 waived by a bet; FC-2 left as-is; both read as answered | FC-5 met by a miss-triggered cold probe (1 hit, ~520 B); **FC-2 waived in writing with its argument** | §9.4 Cond 42; §13.4(1) |
| M9 | S2 | `verified[]` is human-appendable only, so C2 never fires | `verified[]` is derived server-side from session trigger-type + subsequent human message | §4.3, Cond 5, 20 |
| M10 | S2 | The 8 KB index cap deletes knowledge unless minting completes first | Demoter refuses to demote a targetless line (the refusal list *is* the work list); 8,192 B is terminal, gated on D4 | §9.1, Cond 37 |
| M11 | S3 | Positions supersede **in place**; the chain does not grow | Position growth path = `## Previously` stack; note growth path = `supersedes` chain; head substitution applies to notes only | §6.2, Cond 18 |
| M12 | S3 | The composed ceiling is ~80.9 KB, not 58.9 KB | Table recomputed to **79,062 B** (per-turn refresh replaces rather than adds); 8.5% saving stated; rows 1–4 reported, not capped | §9.3, Cond 39, 40 |
| M13 | S3 | Three growth models answer the central question three ways | One canonical growth table, post-minting, denominator on every row; all per-corpus costs priced against the minting-ON rows | §13.1 |
| M14 | S3 | Five names for one expiry concept; two evaluation cadences | One predicate (`holds_while`), one prose field (`revisit_if`), `expires_when` a permanent read alias; evaluated at corpus build, cached; only non-empty unparseable values refused | §4.5, Cond 7, 8 |
| M15 | S3 | R6 hard-errors every worktree, fresh clone and renamed directory | Register-on-first-use with the merge candidate reported; never a hard error | §4.4, Cond 6 |
| M16 | S3 | Write-path `(day, task)` dedupe trades a reversible read-time decision for an irreversible one | Read-time dedupe stays the corpus rule; compaction, if wanted, is offline over old lines only | §9.2 B3, Cond 38 |
| M17 | S3 | "80% unreachable at 5,000" projects a model the same document rejects | Column labelled with its assumption and may not be quoted without it | §8.4, Cond 33 |
| M18 | S3 | Both falsifiers are prose promises with no evaluator | Each falsifier is POSTed as a position with a `holds_while` predicate, so the existing weekly job trips it | §8.9, Cond 34 |
| M19 | S3 | Head substitution has no post-substitution dedupe; slots silently shrink as chains grow | Substitute, dedupe by head, backfill from next-ranked non-substituted candidates | §6.3, Cond 19 |
| M20 | S3 | Identity is a lossy 48-char hash of free text, and supersession keys on it | Long-prefix near-misses reported at write; author picks same-subject (supersede) or different-subject (explicit slug) | §4.4 R7 |

**Carried close to verbatim from the review's "what survives" list**, because each
is load-bearing and should not be re-argued: the one-hop reasoning (§8);
`supersedes` declared by the successor (Cond 17); head substitution's match-the-tail
/ deliver-the-head shape (§6.3); INV-STANDING and the standing polarity (§7.1);
rejecting the orphan gate and the cross-project graph (§12); server-stamped
provenance with the measured `legacy` exception (§4.3); fail-closed-where-a-caller-
waits (P2); enforce-the-slot-not-the-vocabulary (P6); keeping the read floor's shape,
the archive quota at 2, `relocate never delete`, and continuity's fixed-slot
replace-never-append pattern (§9.2, §12.19, P8); and both migrations' report-only
discipline (§10.3, §11.2).

---

## 16. Build sequence

Ordered so that each step produces a measurement the next step consumes, and so that
no gate refuses anything before it has been observed.

1. **Prerequisites, code-only, no behaviour change.** Per-file `(mtime_ns, size)`
   corpus cache (Cond 53). `write_position` takes the write lock. R2 canonicaliser
   fix. The four unregistered config keys registered, plus the thirteen new ones.
2. **The record and the stamps.** Schema superset, server-stamped `origin` /
   `generated`, derived `verified[]`, `holds_while` grammar evaluated at corpus build.
   Position frontmatter validated at write and at build, reported.
3. **Discovery, zero authoring.** D0 default triggers with the phrase exemption and
   per-class df; then **D1**, whose failures are the work list for everything that
   follows.
4. **The split.** `SESSION_LOG.md`, the 20-entry ring with its log line, the demoter
   with its refusal rule. Index cap stays at the current budget.
5. **Per-turn delivery.** Server-side refresh at the four stdin sites, replacing the
   dispatch blocks for turns after the first. Cold probe on warm-miss.
6. **Supersession.** `supersedes` on the successor, head resolution, substitution with
   dedupe, materialised negation block, priority expansion in one of the two existing
   hop slots.
7. **Minting.** Deterministic trigger, WRITE/RESOLVE split, overlap detector in
   report-only mode (**M4**) — which produces the flagged-pair list for M5.
8. **Negation.** Obligation with accounted waivers, the ledger, the interrupt in
   `report` mode. POST the three falsifiers as positions.
9. **Measure, then decide.** Interrupt false-positive rate; G1 rejection rate;
   D1 gate-failure count; M4 flagged-pair count. **Only now** are Conditions 15 and 48
   decidable.
10. **D4 and the terminal cap.** Mint the ~436 orphan prose lines; only then does
    8,192 B become the live index cap.

