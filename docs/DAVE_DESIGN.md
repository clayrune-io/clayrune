# Dave — a project's agent, and how it gets wiser (MC-895 → MC-899)

Status: **phases 1-3 shipped, 4-5 not built.** Designed 2026-08-23; phase 1 `de443bb`, phase 2 `19c0b00`+`38d1ed2`, the human-facing surface `8d7c1bf`, phase 3 below. Updated 2026-08-24.
Builds on `MC-895` (agent types, Phase 1 shipped), `MC-892` (memory index),
`MC-885` (bounded autonomy), `MC-887` (coordination), `MC-897` (the Floor),
`MC-898` (nightly research). Sibling docs: `AGENT_TYPES_DESIGN.md`,
`AGENT_FLOOR_DESIGN.md`, `MEMORY_REDESIGN_2026-08.md`.

---

## 1. The ask

> "shift this from *MarketReplay* to *Dave@MarketReplay.Project* sent an email.
> Then the entire communication is done with Dave while Dave spins up all the
> other subagents… rather than a bunch of single unnamed (Vector) agents."

and, after we tested it:

> "we need a proactive approach to memory preservation and fact learning."

Those are one design, not two. A name without continuity is a costume, and the
costume is *worse* than no name because it promises a memory that isn't there.

## 2. The test that produced this doc

Ron named two things Dave should have known. Both were decisions made in
conversation:

1. **Obsidian** — we evaluated it and declined; our own graph machinery covers it.
2. **The nightly research agent** — it should be a simple agent, possibly just a cron job.

Then, in the very next turn, **I proposed adopting Obsidian** and invented a
justification for it. The result of the test:

| | in memory? | what failed | would more memory help? |
|---|---|---|---|
| Obsidian | **yes** — `arch_memory_link_layer.md`, and in my auto-loaded index | stored as *history*, not as a live position | **no** |
| nightly agent | **no** | never captured at all | yes |

Two different failure modes, and only one is about storage. That split is the
whole design.

## 3. What "memory" actually means here — three layers, three states

| layer | what it holds | state today |
|---|---|---|
| **Facts** | decisions, conventions, gotchas | **works, measured.** Read floor reaches 84% of turns that previously got nothing (MC-892, verified 2026-08-16) |
| **Episodic** | "when did we do X", "like we discussed" | **thin.** Session-log lines only; transcripts aren't semantically searchable. This is the deferred Step 7 |
| **Continuity** | what's in flight, what Dave owes you, what he was mid-way through | **absent.** Not a memory problem — working state, and it evaporates at session end |

The colleague feeling comes mostly from the third. A colleague back from
holiday doesn't re-read the archive; they hold a small working set and look the
rest up.

## 4. Positions — the proactive half

A **fact** is "we have a link layer". A **position** is "we evaluated Obsidian
and declined, *because* we already have the vault shape and built the graph
machinery ourselves."

Three properties, each earned from the failure above:

- **A position carries its reason.** A bare verdict is dogma; the reason is
  checkable. "We built the graph machinery ourselves" is one command and thirty
  seconds to verify — and had I checked, I'd have found it true.
- **A position carries an expiry trigger** — *what would change my mind*. That
  is what makes reviewing tractable: you test triggers, you don't re-read an
  archive.
- **A position fires on its subject, not on keyword match.** Mine was sitting
  in the auto-loaded index and still didn't stop me, because it read as
  architecture history. A position must surface when someone proposes the thing
  it settled.

**Negative decisions are the highest-value and least-capturable thing we
produce.** Every capture mechanism we have is downstream of an artifact: a
commit, a doc, a shipped surface. Deciding *not* to build something produces
none of those, so the pipeline is structurally blind to exactly the class of
knowledge that saves the most work. Re-proposing a rejected idea costs a whole
conversation — as it just did.

## 5. Bounded by construction, not by curation

Ron's objection, and it is the one that kills memory systems:

> "the count of facts and positions is an ever growing list… MB of data over
> time is almost impossible to manage on the fly."

Correct about the quantity, and it applies to the wrong one. Measured
2026-08-23 on this project:

| | size | cost per turn |
|---|---|---|
| `MEMORY.md` (resident) | **21.9 KB** | every prompt, forever |
| topic notes | 288 KB | only when retrieved |
| archive | **792 KB** | only when retrieved |
| **vault** | **1.1 MB**, growing ~3.9 MB/yr | — |

**72% of the vault is already cold, and the index did not grow while the vault
grew to 14× its size.** Retention and residency are already decoupled here.
Disk is free; the only scarce resource is those 22 KB.

Which gives the governing rule:

> **Never delete to save tokens. Demote.**

That is precisely why MC-892's eviction failed its safety review: it treated
leaving-residency as *deletion*. 29–30 of the 67 proposed lines had **no
surviving delivery channel**, so removal was the only lever. The bug was not
the editor's judgement — it was that demotion wasn't available.

Two mechanisms keep this bounded without a curator:

- **Fixed slots for continuity.** N open threads, N commitments, one
  current-understanding paragraph — each **replaced**, never appended. Eviction
  is structural, so the remover problem never arises.
- **Delivery telemetry for everything else.** MC-892 already measured the right
  signal: *0 of 67 lines delivered on 179 real tasks*. Residency becomes a
  cache keyed on whether something actually gets retrieved, not on anyone's
  judgement of importance. Retrieved often → promote. Never → demote, keep.

## 6. Evidence that the pipeline needs the proactive half

Found while checking this design (shipped `bbcfe3d`, CHANGELOG `[2026-08-23b]`):

- **76% of the archive was superseded** — 1,684 of 2,222 lines, worst group 47
  copies, 1,561 of them mid-session `_(live)_` checkpoints.
- **One task in eight got no topic notes at all** — six slots, six session-log
  lines. Asked "do we have a `/goal` command?", the floor returned six lines
  from one afternoon: the first *"found no /goal command"*, the last
  *"verified working"*. Nothing preferred the later one.
- Fixed by read-time dedupe + turning on `read_floor_archive_quota` (2):
  starved tasks **17 → 0**, archive share 34% → 15%.

The lesson generalises: **memory that accumulates without supersession
eventually serves the agent its own first drafts.** A position that is
*replaced* rather than appended is immune to this by construction — which is
the same argument as §5, arrived at from the opposite direction.

## 7. Who owns what memory

The invariant from `AGENT_TYPES_DESIGN.md` §5 stands, with one refinement Ron
surfaced:

- A **global** type (Fenn, Quill, Marlow) **never owns memory**. It works
  everywhere, so owning memory would make it a silent cross-project leak
  channel — client A's context appearing in client B's session with nothing in
  any UI showing it happened.
- A **project-scoped Dave** is different. Dave's memory *is* the project's
  memory: same store, same boundary, no leak. That is exactly what makes him a
  colleague rather than a mask.

So "uber-level agent" needs no new storage concept, and neither does Dave.

## 8. Delegation

Dave may spin up others, under two rules already agreed (`AGENT_TYPES_DESIGN`
§6a):

- **Authority never escalates.** A delegated agent runs at
  `min(caller, callee)` on `ask < bounded < steward`. Otherwise `autonomy` is
  not a limit, it is a suggestion any agent can launder away by calling
  something with a bigger number.
- **Unattended origin is inherited, never reset.** A Dave-triggered session has
  no steward marker in its own task text, so detection cannot rest on
  `STEWARD_TASK_MARKER` alone — the spawning session's origin travels with the
  dispatch, or a delegated agent counts as interactive and closes a learning
  loop with no human on either side.

And one product rule: **hierarchy is for delegation, not for inspection.** Dave
is the first point of contact, never the only one. The project modal's rail
lists every hired agent, and subagents appear nested under whoever spawned them
(`Dave → Fenn`) so you can always go argue with the reviewer directly.

## 9. Build order

| Phase | Scope | State |
|---|---|---|
| **1** | **Continuity record** — fixed-slot, per-project, written at turn end: open threads, commitments, current understanding | **shipped** `de443bb`. Injected directly into every prompt; excluded from the read floor, so it is delivered rather than retrieved |
| **2** | **Positions** — capture verdict + reason + expiry trigger; surface on subject, not keyword | **shipped** `19c0b00` (note class, trigger firing, routes) + `38d1ed2` (the system-prompt directive that gives the route a caller) |
| **2b** | **A human surface** — read and correct both, in the Memory modal | **shipped** `8d7c1bf`. Not in the original order, and it should have been: a memory layer nobody can inspect is one nobody can correct |
| **3** | **The reviewer** — walk the standing positions, test whether any reason has expired, report to Ron | **shipped**. `mc/positions_review.py` + `tools/position-review.py` + the `mc-position-review` skill + a weekly schedule |
| **4** | **Delivery telemetry → residency** — promote what gets retrieved, demote what never does | not built. Removes the curator from the loop, and unblocks MC-892 |
| **5** | **Episodic retrieval** (deferred Step 7) | not built. Serves "like we discussed". Prerequisite: search-precision telemetry, i.e. Phase 4 |

Phase 1 alone is what makes Dave stop being a costume.

## 9a. What phase 3 turned out to be (2026-08-24)

**It is the same job as MC-898**, which asked for a daily sweep of "what has
changed in the field". An open-ended sweep has no stopping condition and no way
to separate an interesting finding from a relevant one. The standing positions
supply the query set: *has anything changed that trips one of our own rulings?*
That question has an answer, and testing a named condition is cheap where
re-reading an archive is not.

Three properties the implementation exists to guarantee, each earned:

- **The reviewer reports; it never edits.** An unattended agent rewriting the
  rulings that steer every other agent is the authority-guard violation in a
  different hat. `record_review` writes only its own sidecar, and a sidecar
  cannot change what any prompt says. Enforced by test, stated in the skill.
- **A flag is raised once.** Keyed to the position's *content* hash, so it
  re-arms when a human edits the reason and stays silent while a known
  condition keeps holding. A nightly "still tripping" mail is how the channel
  stops being read, and then you lose the night it mattered.
- **A position with no `expires_when` is still reviewed** — more worth a look,
  not less. It is a ruling nobody has revisited since the day it was made, and
  the useful output is "this needs a trigger", not a stale-check.

**Cadence is weekly, not nightly.** Positions rest 7 days between reviews and
there are a handful today, so a nightly pass would mostly print "nothing due".
The rest-interval is the real cadence control; the schedule just has to fire
often enough to keep up with it.

**Found while building it:** `schedule_type: "weekly"` had no branch in
`_compute_next_run`. It stored fine, returned 201, read `enabled: true`, and
never ran — the existing weekly MEMORY HEALTH CHECK had `next_run: null` and
zero runs, ever. The UI never offered "weekly", but the API reference in every
agent's prompt did, so agents kept choosing it. Fixed, plus a 400 on any type
the engine cannot schedule, because accepting one silently is the whole bug.

## 10. Open questions

1. **Where the continuity record lives.** A topic note in the vault (gets
   retrieval, link expansion, and the Obsidian shape for free) or a separate
   always-resident block? Leaning: a note with a reserved slot in residency, so
   it inherits the machinery instead of forking it.
2. **Who writes it.** The Step-6 checkpointer already runs mid-session and
   already writes the archive lines we just had to dedupe — it is the natural
   author, and fixing it to *supersede* rather than append would fix both
   problems with one change.
3. **What a position looks like on disk.** Frontmatter on a topic note
   (`position: declined`, `reason:`, `expires_when:`), or its own note class in
   the corpus with its own scoring? The class matters because positions should
   outrank ordinary notes when their subject comes up.
4. **`Dave@MarketReplay.Project` as an address.** Take the *identity*; the
   email-as-interaction-model is a separate and much larger decision, and I'd
   rather we make it deliberately than inherit it from a metaphor.
