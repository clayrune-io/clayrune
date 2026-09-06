# OKF Agent Memory vs. Clayrune's memory layer — design comparison

**Researched 2026-09-06.** Method: shallow clone of
`github.com/okf-memory/okf-agent-memory` at `master` (HEAD `c05ce5d`, committed
2026-09-06) into a gitignored scratch dir, and direct reads of the full source
tree — 4,184 lines of Go, zero third-party dependencies (`go.mod`), plus
`docs/CONVENTION.md`, the embedded agent skill, and the benchmark suite. Our
side read from `mc/memory.py` (3,650 lines), `mc/memory_fts.py`,
`mc/positions_review.py`, `mc/memory_delivery.py`, `mc/distiller.py`, the
read-floor injection sites in `mc/blueprints/`, `docs/MEMORY_SYSTEM.md`, and one
live vault.

**Evidence tags.**

- **[OKF]** — read in the OKF source at the cited path:line.
- **[MC]** — read in this repo at the cited path:line.
- **[DOC]** — stated in one of their markdown docs, not confirmed in code.
- **[READ-ONLY]** — derived from reading the Go source; I had no Go toolchain
  available to execute it. Flagged wherever the claim would benefit from a run.

Repo provenance, for calibration: 2 commits, one author, first commit
2026-09-05, v0.1.0. This is a well-documented week-old project, not a system
with operational history behind it. That cuts both ways below — several of its
choices are clean because nothing has stressed them yet, and several of ours are
ugly because something did.

---

## 0. Bottom line up front

**The root difference is not features. It is what each system takes
responsibility for.**

OKF Agent Memory is a **format plus a convention**. It standardizes how a
knowledge unit is written, typed, provenanced and validated, gives you a fast
deterministic tool to write and query it, and then leaves *when to write* and
*when to read* entirely to the agent's judgment, enforced only by prose in
`AGENTS.md` and a skill file. Its per-turn token cost is zero, and so is its
per-turn guarantee.

Clayrune's memory layer is a **runtime**. It standardizes almost nothing about
the file format — there is no schema, no validator, and no type on a topic file —
and instead automates the two paths that OKF leaves to judgment: a Scribe that
writes at session end and mid-session without being asked, and a read floor that
injects ranked memory into every dispatch whether or not the agent thought to
retrieve. It pays a fixed per-prompt token cost for a per-prompt guarantee.

The one number that frames the whole comparison is in our own code: before the
automatic read floor existed, **"agents open a memory file in 5% of sessions"**
[MC `mc/blueprints/agent_routes.py:2641`]. OKF's entire retrieval model is the
95% case — the agent choosing to run `okf search`. Their `AGENTS.md` and skill
spend most of their length trying to make that choice reliable by instruction
[OKF `AGENTS.md:15-24`, `.agents/skills/okf-memory/discovery.md:44-48`]. That is
the design bet, stated plainly, and it is a real bet — not obviously wrong, and
much cheaper if it lands.

---

## 1. Unit of memory

### OKF

A **concept**: one non-reserved `.md` file anywhere under `knowledge/`, with
required YAML frontmatter and a required non-empty `type`
[OKF `pkg/okf/types.go:17-35`, `pkg/okf/parser.go:116-121`,
`pkg/okf/validator.go:90-92`]. Two filenames are reserved and are *not*
concepts: `index.md` (navigation) and `log.md` (dated changelog)
[OKF `pkg/okf/bundle.go:90-109`]. Linking to either from a concept body is a
hard broken-link finding, with a good stated reason — "reserved index.md/log.md
is navigation, not a concept" [OKF `pkg/okf/bundle.go:182-188`].

The frontmatter schema is closed-ish and typed: `type`, `title`, `description`,
`resource`, `tags`, `generated {by, at}`, `verified [{by, at}]`, `status`
(draft|stable|deprecated), `stale_after` (YYYY-MM-DD), `sources[]`,
`attestation` [OKF `pkg/okf/types.go:18-73`]. Unknown keys land in `Extra` and
are round-tripped rather than dropped [OKF `pkg/okf/parser.go:231-237`], which
is the convention's explicit requirement [DOC `docs/CONVENTION.md:108-109`].

Granularity is a judgment call the convention addresses directly and well:
"avoid artificial fragmentation… split when it has an independent lifecycle, is
frequently referenced independently, or would otherwise become difficult to
maintain" [DOC `docs/CONVENTION.md:167-178`].

Authorship: model or human, both writing through the same tool. There is no
extraction pipeline — nothing reads a transcript. The corpus is explicitly *not*
a transcript [DOC `docs/CONVENTION.md:46-52`].

### Clayrune

Five distinct kinds, in one per-project directory keyed on the encoded project
path [MC `mc/memory.py:261-291`], classified by *filename prefix*, not by
frontmatter [MC `mc/memory.py:1408-1430`]:

| kind | granularity | author |
|---|---|---|
| `MEMORY.md` curated region | the always-loaded index; one-line pointers | human, or the condense model |
| `MEMORY.md` managed region | one line per session: `- [date] **task** — brief` | Scribe / checkpointer only |
| topic `*.md` | one subject, prose, freeform | human (or an external MCP memory tool) |
| `position_*.md` | one ruling on one subject | agent via API, or human |
| `MEMORY_ARCHIVE.md` | append-only cold storage of evicted lines | machinery only |

Two things about that table are worth stating rather than glossing.

First, **note identity is the filename**, not a frontmatter field
[MC `docs/MEMORY_SYSTEM.md:201-204`, resolution at `mc/memory.py:521-531`].
There is no `type`, no `status`, no schema, and nothing validates a topic file.
OKF's typed frontmatter is straightforwardly better engineering here.

Second, and more uncomfortable: **no Clayrune code path writes a topic file.**
Everything the system writes automatically is a one-line log entry, a position,
or a continuity record. The actual body of knowledge — 86 topic files in the
vault I inspected — is human-authored or written by an external memory MCP, and
Clayrune only ever *reads* it. OKF, by contrast, has a first-class agent write
path for the knowledge unit itself (`okf create` / `okf update`), and its
tooling maintains the parent index and the changelog as a side effect
[OKF `pkg/okf/mutate.go:145-162`]. **That is a real capability we do not have,
and it is not a small one.**

---

## 2. Write path

### OKF: when, and what gates it

**When:** whenever the agent decides to, following the end-of-task review
checklist [DOC `docs/CONVENTION.md:314-333`, `AGENTS.md:84-89`]. There is no
automatic trigger anywhere in the codebase. The gate is entirely the model's
judgment plus the "search before write" instruction
[DOC `docs/CONVENTION.md:285-296`].

**What the write does:** `SaveConcept` sets `generated`, serializes, and
`os.WriteFile`s the whole file, then optionally updates the parent `index.md`
listing and prepends a dated `log.md` entry
[OKF `pkg/okf/mutate.go:124-165`]. Both bookkeeping steps are on by default and
opt-out only (`--no-log`, `--no-index`) [OKF `cmd/okf/main.go:338-339`].

### OKF: what can silently destroy data

The brief asked for their equivalent of our 2000-byte/50-note backlog
truncation. There is no truncation constant anywhere in the OKF codebase — I
grepped, and their write path caps nothing. What they have instead is a
different failure class: **whole-value overwrite with no existence check and no
lock.** Six specific cases, all read from source:

1. **`okf create` on an existing ID silently clobbers it.** `SaveConcept` does
   `os.WriteFile` on the joined path with no `os.Stat` guard
   [OKF `pkg/okf/mutate.go:125-143`], and neither `cmdCreate`
   [OKF `cmd/okf/main.go:364-378`] nor the MCP `okf_create` handler
   [OKF `cmd/okf/mcp.go:288-318`] checks first. The concept, its history, and
   its links are gone from the working tree in one call. Recovery is `git
   checkout` — real, but it is git's guarantee, not the tool's.

2. **`okf update --body` replaces the entire body.** Not a patch, not an
   append: `c.Body = *body` [OKF `cmd/okf/main.go:434-436`, MCP equivalent
   `cmd/okf/mcp.go:335-337`]. An agent updating one paragraph must re-emit the
   whole document correctly or it loses the rest. Their own convention says
   "MUST NOT silently overwrite the old meaning" and asks the agent to preserve
   history in a `## Historical Context` section [DOC `docs/CONVENTION.md:399-413`,
   `.agents/skills/okf-memory/update.md:15-46`] — but that is prose. The tool
   offers no mechanism.

   Contrast: our `write_position` supersedes in place and mechanically preserves
   the prior verdict under `## Previously` in code
   [MC `mc/memory.py:1295-1308`], so the "we declined in August and reversed in
   November" shape survives regardless of what the model remembers to do.

3. **`generated.by` is last-writer, unconditionally.** `SaveConcept` overwrites
   `c.Generated` with the calling actor on every save
   [OKF `pkg/okf/mutate.go:130-138`]. So a concept a human wrote by hand with
   `generated: { by: human:lead, … }` reads `agent/cli` after the first agent
   update. This is intentional — their own test asserts it as correct behavior
   [OKF `pkg/okf/scenarios_test.go:265-267`] — and the compensating design is
   that `verified[]` survives untouched [OKF `pkg/okf/scenarios_test.go:261-263`].
   Fine as a design. Worth knowing that `generated` answers "who touched this
   last", not "who authored this".

4. **Block scalars on known scalar fields are silently discarded.** The parser
   collects a key's indented continuation lines into `b.lines`
   [OKF `pkg/okf/parser.go:147-151`], but for `type`/`title`/`description`/
   `resource`/`status`/`stale_after` it reads only `b.inline` and never touches
   `b.lines` [OKF `pkg/okf/parser.go:167-178`]. A `description: |` followed by
   an indented paragraph parses to the literal string `"|"`, and the paragraph
   is dropped on the next serialize. No error, no warning. **[READ-ONLY]** — I
   could not execute this; it is a direct read of the two code paths.

5. **Unknown nested fields gain two spaces of indentation per round-trip, and
   unknown key order is randomized.** `Extra[k]` stores the raw indented lines
   [OKF `pkg/okf/parser.go:232-236`] and the serializer re-prefixes each with
   two spaces [OKF `pkg/okf/parser.go:356-359`]; the `Extra` map is iterated
   without sorting [OKF `pkg/okf/parser.go:351`], and Go randomizes map
   iteration order. So "unknown fields are preserved"
   [DOC `docs/CONVENTION.md:108-109`] holds for *content* but not for *shape*,
   and every agent write produces gratuitous diff churn in the file whose
   reviewability is the project's headline claim. **[READ-ONLY]**.

6. **YAML comments and blank lines inside frontmatter are dropped on every
   write.** The parser `continue`s on blank lines and on any line without a
   colon [OKF `pkg/okf/parser.go:143-145, 154-157`]; the serializer rebuilds
   frontmatter from the struct. Known fields also come back in a fixed order
   [OKF `pkg/okf/parser.go:292-348`], so a hand-ordered file is reordered by the
   first agent touch.

**And one documented guarantee that does not exist in the code.** `SECURITY.md`
states: "The Go CLI only accesses paths within the specified bundle directory;
it refuses to traverse path escapes (`../..`) outside the designated root"
[DOC `docs/SECURITY.md:88`]. There is no containment check anywhere in
`pkg/okf` or `cmd/okf`. `SaveConcept` does `filepath.Join(bundleDir, c.Path)`
[OKF `pkg/okf/mutate.go:125`] — and `Join` *cleans* the path, so a `../..`
prefix resolves outward rather than being rejected. `c.Path` is derived
straight from the caller's `concept_id`
[OKF `cmd/okf/main.go:325, 366`; over MCP, `cmd/okf/mcp.go:289, 295-306`], which
on the MCP path is a model-supplied string. This is the single most serious
finding in this read: a stated security property with no implementation, on the
write path, reachable from model output. **[READ-ONLY]** — I did not build the
binary to demonstrate it; the absence of the check is verifiable by grep and by
reading all four write entry points.

**No locking, anywhere.** Two agents (or an agent and a human's editor) writing
the same bundle are last-write-wins on the concept file, the parent index, and
`log.md`. The convention is admirably honest about this: "concurrency and
multi-agent editing" and "merge/conflict handling" are listed as *open questions
not finalized* [DOC `docs/CONVENTION.md:640-658`]. It is a v0.1 gap they have
named, not one they have hidden.

### Clayrune: when, and what gates it

Automatic, at three moments, none of which require the agent to decide anything:

- **Session end** — `_write_session_memory` runs the Scribe over the transcript
  and commits one managed line [MC `mc/memory.py:2226-2335`], dispatched from
  `agent_routes.py:4395`.
- **Mid-session, on transcript growth** — Step 6, `_maybe_checkpoint` fires past
  a KB debounce and merges a running summary into the same line
  [MC `mc/memory.py:2365-2417`, worker `:2420-2502`].
- **Condense**, when the index crosses a line or byte trigger
  [MC `mc/memory.py:1759-1846`].

All three funnel through a single leaf-locked writer,
`_commit_managed_entry` [MC `mc/memory.py:2072-2172`], which is documented
"Never raises" [MC `mc/memory.py:2088`] and holds a per-project write lock
[MC `:2107`] over an atomic temp-and-rename [MC `mc/core.py:44-51`]. That is the
locking OKF does not have.

Gates that refuse rather than degrade: the Scribe writes nothing on a thin or
refused extraction [MC `mc/memory.py:2957-2964, 3015-3017`]; the condense
payload is validated strictly pre-write so a reject leaves the file untouched
[MC `mc/memory.py:3152-3187`]; attended API writes past the byte budget hard-fail
with a 413 and the file is byte-for-byte unchanged
[MC `mc/memory.py:1919-1945`].

### Clayrune: what can silently destroy data

We are not clean here, and the shape of our problem is the mirror image of
theirs. OKF overwrites whole values; **we truncate summaries at ~20 places, and
only one of them leaves a visible marker.** The consequential ones on the
memory path:

| slice | site | logged / marked? |
|---|---|---|
| checkpoint `merged[:300]` | `mc/memory.py:2478` | no |
| Scribe final `out[:300]` | `mc/memory.py:3018` | no |
| `summary_fallback[:300]` | `mc/memory.py:2252` | no |
| watermark `running_summary[:600]` | `mc/memory.py:381, 395` | no |
| continuity owners `[:4]` — structural eviction, not truncation | `mc/memory.py:778, 1066` | no |
| condense input `archive_tail = blob[-4KB:]` | `mc/memory.py:3101, 3224` | no |
| `_SCRIBE_RESULT_CAP` head+tail elision | `mc/memory.py:2576, 2712-2714` | **yes** — inline `…[N chars elided]…` |

The compounding case is the one to name: a large transcript is map-reduced, each
chunk cut to a sentence or two, the reduce cut to 300 chars, and on a
long-running session that 300-char line is re-merged and re-cut at every
checkpoint boundary. Nothing records what was dropped. That is the same failure
class as the backlog-note case in `CLAUDE.md` — a cap that destroys in silence —
and it is still live on this path.

**Trade-off, labelled.** Truncation loses detail and keeps the record; overwrite
keeps detail and loses the record. Neither is free. OKF pays with a working tree
that can lose a whole concept to one bad call, mitigated by git. We pay with a
managed index whose entries are lossy compressions of what happened, mitigated
by an append-only archive [MC `mc/memory.py:2033-2046`] that is never truncated
[MC `mc/memory.py:18`] and an FTS index over the raw transcripts underneath.

---

## 3. Retrieval

### OKF

**Always-loaded: nothing from the corpus.** What is resident every turn is
`AGENTS.md` — roughly 50 lines of instructions the bootstrap writes, telling the
agent to search [OKF `pkg/okf/bootstrap.go:16-54`]. The corpus itself is loaded
only on an explicit tool call.

**Channel: lexical search, then load-on-demand.** Two MCP tools do the reading —
`okf_search` and `okf_show` [OKF `cmd/okf/mcp.go:106-137`] — under a four-level
"progressive disclosure" pattern: root index → search → concept → follow graph
links [DOC `.agents/skills/okf-memory/discovery.md:26-42`]. Blanket scans are
prohibited by instruction, including `grep` over `knowledge/`
[DOC `AGENTS.md:19-24`].

**The ranker is not BM25**, despite the name used throughout the README
[DOC `README.md:36, 53`] and the tool descriptions [OKF `cmd/okf/mcp.go:108`].
`Bundle.Search` [OKF `pkg/okf/search.go:37-167`] computes a smoothed IDF
[`:96`] and multiplies it by a field-weighted raw term frequency
[`:99-125`] — title ×4.0, tags ×3.5, description ×2.5, id ×2.0, body ×1.0
capped at 5 occurrences. There is **no length normalization and no k1
saturation**, which are the two things that make BM25 BM25. Consequence: a long
concept beats a short one on identical relevance, and there is no `b` parameter
to tune it.

Two more things in that function are worth naming precisely:

- **DF and TF use different matching rules.** Document frequency is computed by
  `strings.Contains` over the concatenated document [OKF `pkg/okf/search.go:57-58`],
  while term frequency is computed by prefix-match over tokens
  [OKF `pkg/okf/search.go:77-85`]. A query term that appears only as a substring
  inside a longer word inflates DF (depressing IDF for everyone) while scoring
  zero TF in that document. The two halves of the score disagree about what a
  match is.
- **DF is recomputed from scratch per query, over every document**
  [OKF `pkg/okf/search.go:53-63`] — and every MCP tool call re-parses the entire
  bundle from disk first [OKF `cmd/okf/mcp.go:239`]. At their corpus sizes this
  is irrelevant, and the microsecond numbers in the README are believable
  precisely because the corpus is small. The `< 300 µs` figure is real and also
  not the interesting question.

**Per-turn token cost: zero, unbounded on the pull.** `limit` defaults to 10
[OKF `pkg/okf/search.go:43-45`, `cmd/okf/mcp.go:248-251`], and each search result
carries description, tags, and both link lists [OKF `pkg/okf/search.go:144-154`]
— so search is bounded. `okf_show` is not: it marshals the whole `Concept`
struct [OKF `cmd/okf/mcp.go:268`], and because both `Body` and `RawContent` are
serialized [OKF `pkg/okf/types.go:33-34`], **`okf_show` returns the concept text
twice** — once as parsed body, once as the raw file including frontmatter. On a
long concept that is a straightforward doubling of the retrieval cost, and it
looks unintentional. Their own `discovery.md` documents a `show` response shape
with a single `content` field and no `raw_content`
[DOC `.agents/skills/okf-memory/discovery.md:96-117`], which suggests the docs
describe an intended shape the code does not emit.

### Clayrune

**Always-loaded: the curated index, on a budget measured in bytes.**
`_INDEX_BYTE_CAP_DEFAULT = 24 * 1024` [MC `mc/memory.py:1867`], with the floor
aiming 1 KB under for headroom [MC `mc/memory.py:1878-1879`]. The comment above
the constant is the design statement and it says the thing that matters: it is
**a chosen budget, not a harness limit** — "24KB is simply what we decided the
auto-loaded index is worth spending on EVERY prompt of EVERY session (~6k
tokens)… Line budgets can't see this one because it is bytes"
[MC `mc/memory.py:1851-1866`]. Overrun costs tokens and dilutes context; it does
not lose data.

Three different overflow behaviors, deliberately: attended writes hard-refuse
with a 413 [MC `mc/memory.py:1919-1945`]; background writers evict oldest
managed entries to the archive [MC `mc/memory.py:2152-2160`]; a curated region
that has bloated with zero managed entries to evict can only log, loudly, once
per run [MC `mc/memory.py:1817-1827`]. The install I measured sat at ~23.2 KB
against the 24 KB budget — i.e. this mechanism is currently near its trigger,
which is context for §4.

**Channel: automatic injection at dispatch, plus on-demand search.** The read
floor runs on every dispatch with a task, top-6 with 2 wikilink hops
[MC `mc/blueprints/agent_routes.py:2632-2645`], and splits into two prompt
blocks — standing positions first, then relevant memory
[MC `:2647-2671`]. Snippets are windowed to ~400 chars
[MC `mc/memory.py:1669-1674`], so the floor's cost is bounded and small.

The ranker is real BM25 with `k1=1.2`, `b=0.75` [MC `mc/memory.py:443-448`],
per-class average-document-length normalization
[MC `mc/memory.py:1715-1722`], and a corpus cached on a
`(name, mtime_ns, size)` fingerprint [MC `mc/memory.py:500-504, 1396-1406`].

**`[[wikilinks]]` are retrieval edges, not decoration.** `_mem_expand_links`
[MC `mc/memory.py:1677-1712`] takes one hop out from the lexical hits, decayed
`0.5` outbound / `0.35` inbound [MC `mc/memory.py:497-498`], and the expansion is
**additive — it never displaces a lexical match** [MC `mc/memory.py:1682-1683`].
Link-reached notes are labelled `(linked from X)` in the prompt so the agent can
weigh them [MC `mc/blueprints/agent_routes.py:2663-2670`].

**Cold tier, on demand only.** FTS5 over raw session transcripts
[MC `mc/memory_fts.py:291-346`], deliberately excluded from the read floor —
"auto-injecting them on every turn would blow the prompt budget for a channel
that is supposed to be on-demand" [MC `mc/memory_fts.py:19-27`] — and reachable
only through the explicit `/memory/search` route.

### The trade-off, named

| | OKF | Clayrune |
|---|---|---|
| resident cost / turn | ~0 corpus tokens | ~6k tokens (24 KB budget), fixed |
| retrieval happens | when the agent decides | every dispatch, unconditionally |
| bounded? | search yes; `okf show` no (and double-serialized) | yes at every stage |
| graph traversal | agent follows links manually, unbounded depth | one automatic hop, decayed, additive |

**OKF pays nothing per turn and buys nothing unless the agent acts. We pay ~6k
tokens per turn and buy a floor that does not depend on the agent acting.** Our
own comment prices the counterfactual at 5% voluntary retrieval
[MC `mc/blueprints/agent_routes.py:2641`]. If OKF's instruction-following holds
at a materially higher rate than that, their design is strictly cheaper. That
number is the one thing I would most want measured, and neither project has
measured it for the other's setup.

---

## 4. Forgetting

### OKF

Three mechanisms, all advisory:

- **`status: deprecated`** — validated as one of draft|stable|deprecated
  [OKF `pkg/okf/validator.go:13, 166-168`], and used in their contradiction
  scenario: the superseded decision is marked deprecated and a new concept links
  back to it [OKF `pkg/okf/scenarios_test.go:165-190`].
- **`stale_after: YYYY-MM-DD`** — the validator compares it to today and
  increments `StaleCount` [OKF `pkg/okf/validator.go:169-175`].
- **Manual deletion**, with the deletion recorded in `log.md`
  [DOC `docs/SECURITY.md:96-99`].

**And an explicit refusal to automate it:** "An agent SHOULD NOT automatically
delete old knowledge merely because it is no longer current"
[DOC `docs/CONVENTION.md:424-428`]. `StaleCount` is reported but is deliberately
*not* part of the strict gate — `GatePassed` keys off errors, broken links,
orphans, and v0.2 gate findings only [OKF `pkg/okf/validator.go:207-210`]. So a
bundle with 40 expired concepts passes `--strict` cleanly.

### Clayrune

We forget in the index and never in the archive. Managed entries are evicted
oldest-first past the line+byte floor, **to the archive, not to nothing**
[MC `mc/memory.py:2152-2160`]; same-day duplicate labels collapse to three
[MC `mc/memory.py:1968, 1999-2022, 2142-2146`], and that one logs
[MC `:2146`]. The archive is append-only and never truncated
[MC `mc/memory.py:2033-2046`, module docstring `:18`]. Topic files are never
deleted or edited by any code path. The two genuinely destructive operations are
`delete_position` (`path.unlink()`, path-traversal guarded)
[MC `mc/memory.py:1338-1363`] and continuity's replace-never-append owner LRU
[MC `mc/memory.py:1059-1068`].

### Does OKF solve what our standing position declined?

**No — they declined the same thing, by a different route, without the
measurement.**

Our position, in the vault, verbatim on the reason field:

> **subject:** a residency dashboard and an automatic promote/demote mover for
> memory · **position:** declined
> **reason:** "Measured 2026-08-24 over 189 real tasks. Demotion has nothing to
> move: 529 of 586 archive lines are never delivered and already cost zero
> tokens, because a line nobody retrieves takes no slot — they are in the
> coldest tier, which IS the demoted state. Promotion is real but came to two
> notes in 189 tasks… So the durable output is a weekly WATCH
> (`tools/memory-eval/delivery_review.py`) that raises each finding once, not a
> dashboard read when someone remembers to look."
> **expires_when:** "…or a demotion lever appears that actually saves tokens
> (e.g. resident index pressure against its 24KB cap, which is at 17.2KB
> today)"

The refusal is enforced structurally in three places, each stating it
independently: the delivery telemetry module — "It is not a mover. Nothing here
promotes, demotes, or edits a note. It counts."
[MC `mc/memory_delivery.py:14-18`]; the weekly review tool
[MC `tools/memory-eval/delivery_review.py:10-13`]; and the position reviewer,
which reports and never edits [MC `mc/positions_review.py:14-30`].

OKF arrives at the same place — no mover — from convention rather than from
measurement [DOC `docs/CONVENTION.md:424-428`]. **Where they are ahead is
ergonomics of the stale signal:** `stale_after` is a per-concept, machine-checked
expiry on *every* knowledge unit [OKF `pkg/okf/validator.go:169-175`]. Ours is
`expires_when` — prose, human-judged, and only on positions
[MC `mc/memory.py:1266-1318`]. Every one of our 86 topic files is
permanently, invisibly current. A `stale_after` field would have caught
`arch_memsearch.md` — a note describing a subsystem retired 2026-05-18 that went
on being retrievable for months.

The honest counterweight: their signal is advisory and ungated
[OKF `pkg/okf/validator.go:207-210`], so in practice `StaleCount` is a number in
a CLI footer that nobody is obliged to act on. And it is untested at scale — the
project is a week old with 8 concepts in its own bundle
[OKF `knowledge/index.md:9-22`]. Our position at least carries a number and a
condition for reopening. Theirs carries a `SHOULD NOT`.

**Note for the record:** that position's `expires_when` names index pressure
against the 24 KB cap as a reopening condition and cites 17.2 KB at decision
time. The install I measured is at ~23.2 KB. That clause is close to firing —
independent of anything in this comparison.

---

## 5. Multi-agent and provenance

### Multi-agent

**OKF: not addressed, and they say so.** No locking, no ownership, no per-agent
partition, no conflict resolution. `docs/CONVENTION.md:640-658` lists
"concurrency and multi-agent editing", "merge/conflict handling", and
"multi-agent coordination" among the topics "intentionally not finalized"
before the convention can be called stable. Their `docs/AGENT_TESTING.md`
"multi-agent" work is about *cross-vendor compatibility* — the same corpus read
by Claude, Cursor, Codex — not concurrent agents on one corpus. That is a
legitimate scope choice for v0.1 and it is stated, not hidden.

Their memory does reach another agent, in the strongest possible sense: it is a
committed file in a shared git repo, so it reaches every agent on every machine
that pulls. That is a genuine architectural advantage over ours — our vault
lives outside the repo and is per-install.

**Clayrune: one shared vault per project, plus one deliberate partition.** Every
agent on a project shares its notes and positions, stated as design in
`write_continuity`'s docstring: "a ruling Vector recorded must bind Dave, or
positions would not work at all" [MC `mc/memory.py:1018-1021`]. The exception is
continuity, which is owner-bucketed: an agent sees its own in-flight threads in
full, plus up to three other agents' under an explicit
"ANOTHER AGENT ON THIS PROJECT IS PART-WAY THROUGH (not yours — do not adopt or
report these as your own work)" header [MC `mc/memory.py:1113-1161`].

### Provenance: they have the vocabulary, we have the enforcement

This is the cleanest split in the whole comparison, and OKF wins the first half
of it outright.

**OKF's trust model is explicit and typed.** `generated {by, at}` for
attribution and `verified [{by, at}]` for human review are separate fields with
separate meanings, and the convention forbids conflating them: "An agent MUST
NOT claim that a statement was human-verified when it was only generated or
inferred by an agent. `generated` and `verified` have different meanings and
MUST remain semantically distinct" [DOC `docs/CONVENTION.md:373-377`]. Actor
strings are format-validated (`producer/version`, `human:id`, `process:id`)
[OKF `pkg/okf/types.go:9, 76-81`], and the validator warns on malformed or
non-standard actors [OKF `pkg/okf/validator.go:141-145, 155-159`]. A scenario
test pins that an agent update preserves an existing human `verified` entry
[OKF `pkg/okf/scenarios_test.go:255-263`]. There is also `sources[]` with
keyed-footnote cross-checking against the body
[OKF `pkg/okf/validator.go:126-135`], and a convention rule that inferences be
recorded as inferences — "Based on A and B, the agent infers C" — rather than as
facts [DOC `docs/CONVENTION.md:381-395`].

**We have no author field on a note at all.** Nothing in `_mem_corpus`
[MC `mc/memory.py:1388-1470`], `_parse_position` [MC `:733-749`], or the
frontmatter our writers emit carries authorship. The nearest signals are
indirect: inline tags in a managed line (`_(live)_`, `_(reconciled)_`), and
continuity's shared bucket, which conflates human input with pre-owner legacy
records [MC `mc/memory.py:979-983`]. **A human-typed fact and a Scribe-inferred
one are indistinguishable in our vault.** That is a real gap and OKF's schema is
the better answer to it.

**But their rule has no mechanism.** "Never forge human verification" appears
three times in prose [DOC `AGENTS.md:31`,
`.agents/skills/okf-memory/SKILL.md:21`, `docs/SECURITY.md:58`] and nowhere in
code. `SaveConcept` writes whatever actor string the caller passes
[OKF `pkg/okf/mutate.go:131-138`], and the CLI exposes it as free text —
`--actor` with default `agent/cli` [OKF `cmd/okf/main.go:337, 408, 471`]. An
agent running `okf create x --actor human:ron` produces a concept attributed to
a human, and `validate --strict` passes it, because `human:ron` is a
well-formed standard actor [OKF `pkg/okf/types.go:12-15`]. The `verified[]`
array is likewise round-tripped verbatim with no check on who wrote it. Over
MCP the actor is hardcoded to `"agent/mcp"`
[OKF `cmd/okf/mcp.go:309, 339, 355`], so *that* channel cannot forge — but
their own `AGENTS.md` instructs agents to use the CLI
[DOC `AGENTS.md:61-80`], where it can. The enforcement is accidental and
channel-dependent.

**Our provenance is narrower and load-bearing.** Where OKF's provenance
*describes*, ours *governs*:

- **The unattended origin gate.** Artifacts carry `origin: interactive |
  unattended`, stamped at session end from an allowlist that fails closed —
  interactive only when `trigger_type == 'manual'` and the task text carries no
  unattended marker [MC `mc/distiller.py:1575-1589`, stamped at
  `mc/memory.py:2280-2296`]. At read time, `exploration_read_floor(…,
  consumer_unattended=True)` withholds unattended-origin artifacts from steward
  cycles, and unstamped artifacts are treated as unattended
  [MC `mc/distiller.py:2267-2272`]. The stated reason is exact: "a steward cycle
  distils its own transcript into an exploration, the next steward cycle reads
  it back as established fact, and the system trains on its own output with no
  ground truth anywhere in the circuit" [MC `mc/distiller.py:2229-2237`].
- **The authority guard.** `_authority_violation` refuses any artifact whose
  text expands the agent's own permissions, deterministically, before the
  artifact reaches the human queue [MC `mc/distiller.py:1636-1641`, called at
  `:1674`]. It exists because one sentence in one session became a globally
  loaded PREFERENCE skill telling every agent to stop asking permission
  [MC `mc/distiller.py:1599-1606`].

**Scope honesty, stated because it matters:** that rail governs the Distiller's
proposed artifacts. It does **not** govern `MEMORY.md`. A steward cycle's Scribe
entry, its checkpoints, its positions and its continuity all land in the shared
vault unstamped, and `_memory_search` has no origin filter of any kind. So the
autonomous→autonomous circuit is cut on one channel and open on the other. OKF
has no such gate on any channel, but it also has no autonomous writer to gate.

---

## 6. Failure modes, and which ones are silent

### OKF

| failure | silent? | citation |
|---|---|---|
| `okf create` clobbers an existing concept | **yes** — reports "Successfully created" | `pkg/okf/mutate.go:125-143`, `cmd/okf/main.go:388` |
| `okf update --body` drops the rest of the body | **yes** | `cmd/okf/main.go:434-436` |
| block scalar on a known field discarded | **yes** | `pkg/okf/parser.go:167-178` |
| unknown nested field re-indented +2, key order shuffled | **yes** (visible only as diff churn) | `pkg/okf/parser.go:232-236, 351-359` |
| frontmatter comments / blank lines stripped | **yes** | `pkg/okf/parser.go:143-157` |
| concurrent writes, last-write-wins | **yes** | no locking; open question at `docs/CONVENTION.md:649-650` |
| path escape outside the bundle root | **yes**, and contradicts a documented guarantee | `pkg/okf/mutate.go:125` vs `docs/SECURITY.md:88` |
| agent never runs `okf search` at all | **yes** — the defining silent failure of the design | no automatic path exists |
| concept goes stale | **no** — `StaleCount` reported, but ungated | `pkg/okf/validator.go:169-175, 207-210` |
| unparseable frontmatter | **no** — hard validation error | `pkg/okf/parser.go:119-121`, `validator.go:80-88` |
| broken link / orphan | **no** — gates `--strict` | `pkg/okf/bundle.go:199-215`, `validator.go:208-210` |

Their loudest half is genuinely good. **Broken links and orphans FAIL the strict
gate.** Ours are a report tool nobody is obliged to run
[MC `tools/memory-link-check.py`, per `arch_memory_link_layer`], with no gate at
all. If a wikilink target is renamed here, the edge silently disappears from
retrieval and nothing says so.

Also worth stating as a straight win for them: because the corpus is a committed
directory, `git diff` and PR review are the audit trail
[DOC `docs/ALTERNATIVES.md:43`]. Every one of the silent write failures above is
*visible in a diff before it lands*, if someone reviews it. That is a real
mitigation and it covers most of the list.

### Clayrune

| failure | silent? | citation |
|---|---|---|
| checkpoint / Scribe summary truncation at 300 chars | **yes** | `mc/memory.py:2478, 3018` |
| `_maybe_checkpoint` and `_checkpoint_worker` exceptions | **yes** — bare `except: pass` | `mc/memory.py:2416-2417, 2497-2498` |
| whole corpus fails to load → empty read floor | **yes** — `except OSError: return []` | `mc/memory.py:1400-1401` |
| `exploration_read_floor` failure | **yes**, both sides | `mc/distiller.py:2292-2293`, `agent_routes.py:2687-2688` |
| `_checkpoint_prev_offset` read failure → whole transcript re-rendered as delta | **yes** | `mc/memory.py:2361-2362` |
| dangling wikilink | **yes** — no gate | `tools/memory-link-check.py` is opt-in |
| steward read-floor deliveries untelemetered | **yes** — undercounts "never delivered" | `scheduler_routes.py:502-505` omits `record=` |
| steward read floor renders positions as ordinary notes | **yes** | `scheduler_routes.py:506-514` vs `agent_routes.py:2647-2661` |
| config keys read from CONFIG but absent from defaults *and* the editable list | **yes** — PUT returns 200 with `updated: []` | trap documented at `mc/memory.py:455-458`; e.g. `read_floor_position_reserve`, `positions_enabled`, `continuity_enabled` |
| two `MEMORY.md` writers bypass the leaf lock | **yes** — bare `write_text`, no lock | `project_routes.py:1882, 1908` vs the contract at `mc/memory.py:11-18` |
| read-floor search failure | **no** — deliberately un-silenced, with the reason inline | `agent_routes.py:2639-2645` |
| attended write over budget | **no** — 413 with numbers | `mc/memory.py:1919-1945` |
| watermark leak past the budget | **no** — GC'd at startup, logged | `mc/memory.py:2175-2223` |
| topic file corrupt / malformed | **n/a** — nothing validates one | no validator exists |

The last row is the honest bottom of our design. **We have no schema and no
validator on the knowledge unit, so there is no such thing as an invalid topic
file here.** That is not resilience; it is the absence of a check. OKF would
catch a missing `type`, a malformed actor, an expired `stale_after`, a dangling
link and an orphan on the same file, in ~4 ms.

---

## 7. Where OKF is better

Stated plainly, because a comparison shaped to flatter the home team is worse
than no comparison.

1. **A typed, machine-checkable schema on the knowledge unit.** Required `type`,
   validated `status`, ISO-checked dates, format-checked actor strings, keyed
   source footnotes cross-referenced against the body
   [OKF `pkg/okf/validator.go:38-212`]. We have none of this on a topic file.

2. **A provenance vocabulary that separates attribution from verification.**
   `generated` vs `verified` as distinct, non-interchangeable fields
   [OKF `pkg/okf/types.go:37-47`, `docs/CONVENTION.md:373-377`]. We cannot
   distinguish a human-typed fact from a Scribe-inferred one at all.

3. **A link graph the tooling maintains, and a gate that fails on breakage.**
   `okf relate` writes the edge and logs it [OKF `pkg/okf/mutate.go:168-217`];
   `buildGraph` computes inbound, outbound, broken links and orphans
   [OKF `pkg/okf/bundle.go:158-215`]; `--strict` fails the build on any of them
   [OKF `pkg/okf/validator.go:208-210`]. Our wikilinks are real retrieval edges
   but nothing gates them, and a rename silently deletes an edge.

4. *(Bonus, since it kept coming up.)* **The corpus is in the repo.** It travels
   with a clone, reviews in a PR, and reaches every agent on every machine.
   Ours is per-install and outside the repo, by a deliberate decision, but the
   cost of that decision is real.

5. *(Bonus.)* **A machine-checked per-unit expiry (`stale_after`) on every
   concept**, where ours exists only on positions and only as prose.

---

## 8. Where we do things they do not

1. **Retrieval that does not depend on the agent choosing to retrieve.**
   Automatic read-floor injection on every dispatch, ranked, bounded, split into
   a positions block and a memory block
   [MC `mc/blueprints/agent_routes.py:2632-2671`], plus a one-hop additive
   wikilink expansion [MC `mc/memory.py:1677-1712`] and an on-demand FTS5 cold
   tier over raw transcripts [MC `mc/memory_fts.py:291-346`]. OKF's read path
   is entirely voluntary.

2. **A standing-ruling type that outranks notes on its subject and cannot be
   recorded without a reason.** `write_position` refuses a verdict with no
   `reason` — "a verdict without one cannot be re-evaluated"
   [MC `mc/memory.py:1281-1288`]; positions get a 6× subject boost
   [MC `mc/memory.py:654, 1457`], reserved slots outside the ordinary cut
   [MC `mc/memory.py:1612-1640`], a trigger-coverage gate so a common word cannot
   make one fire on everything [MC `mc/memory.py:694-716, 1584-1596`], and their
   own prompt block above relevant memory
   [MC `mc/blueprints/agent_routes.py:2647-2661`]. A review loop closes it, and
   an agent's `tripped=False` cannot clear a flag a human has not seen
   [MC `mc/positions_review.py:230-242`]. OKF has `status: deprecated` and a
   convention paragraph; it has no unit that says "this was decided, do not
   re-propose it" and no mechanism that makes such a unit outrank an ordinary
   note in retrieval.

3. **Provenance that governs machine behavior rather than describing it.** The
   unattended origin gate that refuses to feed autonomous output back to an
   autonomous consumer [MC `mc/distiller.py:2267-2272`], and the deterministic
   authority guard that refuses any artifact expanding the agent's own
   permissions before it reaches a human queue
   [MC `mc/distiller.py:1636-1641, 1674`]. OKF's provenance rules are all
   `MUST NOT` sentences addressed to a model.

4. *(Bonus.)* **Automatic capture.** Session-end Scribe, mid-session
   checkpointing, condense [MC `mc/memory.py:2226-2335, 2365-2502, 1759-1846`].
   Nothing in OKF writes memory unless an agent decides to.

5. *(Bonus.)* **Concurrency safety on the write path** — a per-project leaf lock
   over an atomic temp-and-rename [MC `mc/memory.py:2107`, `mc/core.py:44-51`],
   against no locking at all on their side.

---

## 9. On their benchmarks

Stated accurately because the numbers are quotable and will be quoted.

The suite compares one prompt built from a ~3,000-token monolith against one
prompt built from a single retrieved concept, on a 10-concept synthetic corpus,
using one hand-written query at top-1
[OKF `cmd/okf-benchmark/main.go:868`; corpus at `benchmarks/data/knowledge/`].
The headline `-80.1%` (3,034 → 603 tokens) is arithmetically true and true *by
construction*: it measures the value of sending one concept instead of ten. It
is not a measure of retrieval quality, and with `limit=1` a retrieval miss
delivers the model nothing.

The genuinely interesting row is the Gemma-12B result: 1/4 policy checks passed
on the monolith, 4/4 on the single concept
[DOC `benchmarks/README.md`, "attention lost"]. That is an n=1 observation of
lost-in-the-middle degradation, and it argues for bounded resident context —
which is the same conclusion behind our 24 KB budget
[MC `mc/memory.py:1851-1866`], not an argument against it.

The README's `< 300 µs` search and `~4 ms` validation figures are believable and
uninteresting at these corpus sizes; the ranker recomputes document frequency
over every document on every query and re-parses the bundle on every MCP call
[OKF `pkg/okf/search.go:53-63`, `cmd/okf/mcp.go:239`], which is fine at 10
concepts and is not what the numbers are being used to imply.

---

## 10. Summary table

| dimension | OKF Agent Memory | Clayrune |
|---|---|---|
| unit | typed concept, required `type`, validated frontmatter | five untyped kinds distinguished by filename prefix |
| who writes the knowledge unit | agent or human, via one tool | human / external MCP only — **no MC writer** |
| what MC writes automatically | nothing | session line, checkpoint, position, continuity |
| write trigger | agent judgment + prose convention | automatic at session end, on transcript growth, on condense |
| write safety | full overwrite, no existence check, no lock; git is the backstop | leaf lock + atomic rename; lossy summaries |
| silent data loss shape | whole-value overwrite, dropped block scalars, reshaped unknown fields | uncounted truncation at ~20 sites |
| always-loaded cost | 0 corpus tokens | ~24 KB / ~6k tokens, budgeted in bytes |
| retrieval trigger | agent runs a tool | automatic on every dispatch |
| ranker | weighted TF·IDF (called BM25; no length norm, no k1) | real BM25 + per-class length norm + cached corpus |
| graph | maintained by the tool; broken links + orphans **fail `--strict`** | wikilinks are real edges, one decayed hop, **no gate** |
| forgetting | `deprecated` + `stale_after`, counted, ungated; no mover by convention | archive eviction (never deletes); no mover, by measured position |
| provenance schema | `generated` vs `verified`, actor format validated | **none on a note** |
| provenance enforcement | prose only; `--actor` is free text on the CLI | unattended origin gate + deterministic authority guard (Distiller channel only) |
| multi-agent | explicitly unfinished (`CONVENTION.md:640-658`) | one shared vault + owner-partitioned continuity |
| portability | in the repo; travels with a clone | per-install, outside the repo |
| maturity | 2 commits, one author, v0.1.0 | years of incident history encoded in the code |

---

## Appendix — what I could not establish

- **[READ-ONLY] items** in §2 (path escape, block-scalar loss, `Extra`
  re-indentation, `Extra` key reordering) are reads of the Go source with no Go
  toolchain available to execute. Each cites the exact lines; each would take
  about ten minutes to confirm or refute with `go run`.
- **Retrieval hit rate under OKF's voluntary model.** Neither project has
  measured how often an agent actually runs `okf search` when instructed to. Our
  5% figure [MC `mc/blueprints/agent_routes.py:2641`] is for our harness before
  the read floor, not for theirs, and is not transferable.
- **`okf validate --strict` on a large corpus.** Their own bundle has 8
  concepts [OKF `knowledge/index.md:9-22`]; the orphan rule
  [OKF `pkg/okf/bundle.go:209-215`] requires every concept to be linked, which
  is cheap at 8 and unknown at 800.
- **Whether `okf_show`'s double serialization of body + raw content**
  [OKF `pkg/okf/types.go:33-34`, `cmd/okf/mcp.go:268`] is intentional. Their own
  docs show a single-field response shape
  [DOC `.agents/skills/okf-memory/discovery.md:96-117`], which suggests not.
