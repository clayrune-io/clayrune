# Workflow Builder — authoring surface, wired to the scheduler

**Status:** v1 spec 2026-09-10; **revised 2026-09-11 — Q7 REVERSED by Ron
after using the shipped spine.** Marlow. Backlog: MC-871 (reopened from
`wontdo` — see "Why now"). Reference image: `data/uploads/agent_058984469b.png`
(Zapier: trigger card, numbered steps, a Paths node, connectors down a spine).

The reversal changes more than layout: drag-to-connect arrows turn the
runtime model from a tree of lists into a DAG, which changes the stored
definition format, the runner, and restart adoption. §Revision 2 (end of
file) settles those. Q1–Q6 stand except where tagged `[Superseded → R2]`;
Q7 is kept verbatim under its reversal banner — it was a deliberate call,
reversed on contact with the built result, not an oversight.

Ron's ask, verbatim: *"We decided last week that workflows menu is not really
worth keeping and canceled the existing flows. However, the workflow tab was
kept on the three dot menu. I think we should actually bring the functionality
back into this UI, so that users can create their own workflows and there
should be integration between this and our calendar function."*

---

## Why now — so nobody re-litigates it

The 2026-09-01 decline of MC-871 was measured, not taste: Claude Code's
Workflow tool had run 44 times ever on this box, all inside a two-week window,
none in the seven weeks since. A builder would have been a UI for an activity
that had stopped. That decline named its own reopen condition: *"a real
recurring job needs the cycling loop — phase 1 feeding phase 2 unattended with
nobody retyping the handoff. Build it for that job, not in the abstract."*

The Desk is that job (`docs/THE_DESK_SPEC.md`, 2026-09-09): harvest signals →
triage → draft per voice → human approves → publish → measure, on a cadence,
each phase consuming the previous phase's output. It did not exist when the
decline was written. The standing position was reopened 2026-09-10
(`position_avisualworkflowbuilderforclayrunemc871authorings.md`) on Ron's ask.

**Acceptance test for this whole design: the Desk pipeline must be expressible
in the builder.** If it is not, the design is wrong. §"Acceptance" walks the
mapping node by node.

## Problem

Clayrune can dispatch one agent, on a schedule, with a fixed prompt
(`mc/blueprints/scheduler_routes.py:547`, `_scheduler_loop`). It cannot express
*a second step*: "when that agent finishes, hand what it produced to the next
one, unless it found nothing." Today that handoff is a human reading one
session's answer and retyping it into another session — the exact failure the
reopen condition names. The system already has every primitive the loop needs
(dispatch, completion callback with the child's final text, a scheduler, a
question channel) and no way to compose them without writing code.

## Who it is for

The Clayrune operator authoring a standing pipeline over their own agents and
projects. Not a Zapier user wiring SaaS products together. One author, running
on one box, composing nouns Clayrune already owns.

## Scope — the line that matters most

Ron's reference image shows Webflow, Slack, Hubspot, Salesforce, Mailchimp,
Google Sheets. **Clayrune has no third-party integration layer, and building
one is a different product with a different maintenance burden.** The image is
a reference for the *authoring shape* — trigger card, numbered steps, Paths —
not for the palette.

**In scope:** a workflow is a canvas over Clayrune's own nouns — dispatch an
agent (with a character), wait for its result, branch on that result, gate on
a human, write a backlog item, fire a Desk harvest, run on a schedule.

**Out of scope for v1, explicitly:**

- **No third-party connectors.** No Slack, no webhooks in or out, no HTTP node,
  no OAuth to anything. An agent step can still be *told* to use whatever
  tools its normal permissions allow — the workflow layer adds no new reach.
- No loops or cycles. A workflow is a DAG that runs top to bottom and ends.
  (Unchanged by Revision 2 — now enforced three times, §R2-D3.)
- ~~No parallel fan-out and no rejoin after a branch. Paths diverge and each
  path runs to its own end.~~ **[Superseded 2026-09-11 → §R2-D2/D4]** A node
  may now have multiple parents (rejoin exists) and a node may have multiple
  unconditional children (fan-out exists in the graph). Execution stays
  SERIAL — one agent step in flight per run, ever. The DAG expresses
  dependency, not parallelism; genuine parallel work is still Hivemind's job.
- No expression language. Handoff is string templating over named slots, §Q3.
- No retry policies, no per-step timeouts config, no versioning/history of
  definitions beyond the file's git-style single current state.
- No second scheduler. §Q4.
- The workflow engine must not widen any agent's permissions. An approval
  gate cannot be removed, satisfied, or auto-answered by the runner or by any
  agent step; outward-facing actions sit behind human gates the same as
  everywhere else in Clayrune. This is the learning-system authority-guard
  principle (`mc/distiller.py`, `_authority_violation`) applied verbatim.

---

## The seven questions, settled

### Q1 — What a workflow is on disk, and where

**Decision: two stores, both siblings of `DATA_DIR`, never members.**

- **Definitions:** `data/workflows.json` — one file, a list of workflow
  records. Precedent is exact: `data/schedules.json` (`server.py:527`,
  `SCHEDULES_PATH`) and `data/desk.json` already live there, need no
  `EXCLUDED_SIDECAR_SUFFIXES` entry, and cannot 500 the restart endpoints.
  Definitions are small (a workflow is tens of lines of JSON) and read-mostly.
- **Run state:** `data/workflow_runs/<run_id>.json` — one file per run,
  rewritten at every step transition. A run must survive a server restart
  (§Q5), so its state is on disk, not in a dict. A directory keeps each run's
  writes independent and makes GC trivial (delete files older than N days,
  keep the last K per workflow).

Nothing under `data/projects/`. The CLAUDE.md DATA_DIR rule is satisfied by
placement, not by exclusion.

A workflow record: `id`, `name`, `description`, `enabled`, the node list
(§Q2), and `created`/`updated` stamps. Workflows are **global objects** like
schedules — each *step* names a project, because a pipeline like the Desk's
crosses projects by nature.

### Q2 — Node types, kept minimal

**Decision: five node types. Resist the sixth.**

| Node | What it does | Runs as |
|---|---|---|
| **Trigger** | Exactly one per workflow: `manual` or `schedule` (§Q4). Not a step — it is the card at the top of the spine. | — |
| **Agent step** | Dispatch an agent: project + character + prompt template. The runner waits for completion and captures the result (§Q3). The workhorse; most workflows are two or three of these. | existing dispatch path |
| **Paths** | Branch on the previous agent step's declared outcome (§Q3). Two or more labelled branches plus a mandatory **otherwise** branch. Each branch is its own vertical list; no rejoin. | runner, deterministic |
| **Approval gate** | Park the run until a human decides. Options are authored labels ("release", "push back"); the decision routes like a Paths node. Delivered over the existing question channel (`mc/question_channel.py`) — chat form when attended, email when not — plus a Waiting card on the run view. | human only |
| **Clayrune action** | A deterministic call from a fixed allowlist, no agent in the loop. v1 allowlist is three entries: create a backlog item, PATCH a backlog item's status/text, run a Desk harvest (`POST /api/desk/signals/harvest`, `mc/blueprints/desk_routes.py:104`). Growing this list is a spec change, not a config change. | server, allowlisted |

What is deliberately absent: an HTTP node (connector-land through a side
door), a code/eval node (arbitrary execution authored in a UI), a timer/wait
node (that is what the trigger's schedule is for), a notification node (the
question channel and run view already surface state).

**[Superseded in part, 2026-09-11 → §R2-D6]** The **Paths node is retired**:
branching is now conditional edges leaving an agent step — same `wf:result`
outcome mechanism, same mandatory otherwise (now a port). The other four
node types stand, and the deliberately-absent list above still binds.

### Q3 — How a step's output reaches the next step's input

This is the reason the feature exists, so it gets the most precise answer.

**Base mechanism — free text flows forward.** When an agent step completes,
the runner captures the child's last real assistant text — the same
extraction the spawner callback already uses (`_last_reply_text`,
`mc/blueprints/agent_routes.py:4412`, with its MC-935 exclusions for status
lines and the task seed). That text is stored on the run record as the step's
`output`. A later step's prompt template references it by slot:

- `{{steps.<name>.output}}` — the full captured text of a named earlier step.
- `{{prev.output}}` — shorthand for the immediately preceding step.
- `{{trigger.fired_at}}`, `{{run.id}}` — the only non-step slots in v1.

Substitution is plain string replacement at dispatch time. No expressions, no
filters, no conditionals in templates. An unresolvable slot **fails the run at
that step, loudly** — a silently-empty substitution is a fabricated prompt.

**Structured mechanism — outcomes for branching.** Free text cannot drive a
Paths node. When an agent step feeds a Paths node, the runner appends a short
fixed instruction to the step's prompt: end your reply with a fenced
` ```wf:result ` block containing `{"outcome": "<one of: …>", "summary":
"…"}`, where the outcome vocabulary is exactly the downstream branch labels.
Precedent for fenced protocol blocks is the `mc:question` flow, already
parsed on this codebase. The runner parses the **last** such block:

- Parsed, outcome matches a branch → that branch runs. `summary` and any
  extra keys become `{{steps.<name>.result.<key>}}` slots.
- Missing or unparseable → the **otherwise** branch runs. Every Paths node
  has one; the builder refuses to save a Paths node without it. This is the
  fail-closed default: an agent that did not declare an outcome is routed to
  the branch the author designated for exactly that, never guessed at.

**What is explicitly not passed:** transcripts, tool logs, file contents. If
a step wants a later step to see a file, it says so in its output text and
the later agent reads the file itself — agents already share the filesystem.
The workflow layer moves *conclusions*, not artifacts.

### Q4 — Calendar and scheduler: what Ron actually gets

**Decision: a workflow is a thing a schedule can invoke. There is no second
scheduler and no separate workflow clock.**

- A schedule record gains one optional field, `workflow_id`, mutually
  exclusive with `task` (`create_schedule_from_spec`,
  `mc/blueprints/scheduler_routes.py:1099`, validates the pair). Every
  existing schedule type — daily, interval, once, cron — works unchanged,
  because firing is unchanged.
- At fire time, `_scheduler_loop` (`scheduler_routes.py:547`) starts a
  workflow run instead of dispatching a raw task. "Run Now"
  (`scheduler_routes.py:1216`) and the run-history endpoint work the same way.
- **The calendar gets workflows for free.** `schedule-calendar.js` reads the
  same `/api/schedules` payload as the list view and adds no endpoint
  (`static/js/schedule-calendar.js:3-8`). A workflow-invoking schedule renders
  on the same grid as any other occurrence, with a workflow badge and the
  workflow's name, and obeys both existing rules: struck-through when paused
  or disabled, never an invented occurrence. Clicking its chip opens the
  workflow's run history rather than the schedule form's task box.
- The trigger card in the builder is the *authoring* face of this: choosing
  "on a schedule" creates or edits the linked schedule record through the
  existing CRUD. One store, two views. The master kill-switch
  (`scheduler_paused`) therefore stops workflow cadences with zero new code.

What Ron gets, concretely: the Desk pipeline as a chip on the same calendar
as every other scheduled run, pausable by the same switch, with its run
history behind the same "Runs" affordance.

### Q5 — Mid-run: failure, approval, restart, concurrency

- **Failure is terminal and loud.** A step that errors (dispatch refused,
  agent ended in error, unresolvable slot) halts the run at that step with
  state `failed`, the step's error on the run record, and a run-view card
  saying which step and why. No automatic retry in v1 — the Desk spec's
  publishing rule ("a publish failure is loud and terminal, no blind retry")
  generalises. A human can re-run the workflow; there is no resume-from-step
  in v1.
- **Approval parks the run.** State `waiting`, persisted to the run file,
  surfaced on the run view and delivered over the question channel (email
  when unattended — the same path scheduled agents already use for
  `mc:question`). The decision resumes the run on its chosen branch. A
  waiting run holds no process and survives restarts by construction.
- **Restart adoption fails closed.** On startup the runner scans
  `data/workflow_runs/` for non-terminal runs. `waiting` runs are simply
  still waiting. A run whose current step was `running` is checked against
  the child's completion record (the agent log the spawner callback already
  writes); if the child completed, the run advances normally. If the child
  cannot be confirmed complete, the run is marked `interrupted` — never
  silently re-dispatched, because an agent step may have had side effects.
  Interrupted is a terminal state a human can see and re-run from.
- **One live run per workflow.** A trigger fire while a run is live is
  skipped and logged, exactly the steward-cycle guard's behaviour
  (`_steward_cycle_running`, `scheduler_routes.py:440`). Concurrent runs of
  one pipeline are how two drafts of the same story get published.

### Q6 — The existing read-only Workflows tab

The tab currently renders live **Claude Code** Workflow-tool fan-outs,
reconstructed read-only from on-disk subagent journals
(`/api/project/<id>/workflows`, `mc/blueprints/agent_routes.py:8022`;
scanner at `:7950`; tab body `static/js/render-core.js:962`). That is a
different noun from what this spec builds, and it still works.

**Decision: the tab becomes the home of Clayrune workflows; the CC viewer
stays inside it as a secondary section.** Top of the tab: this project's
workflows (any workflow with a step in this project), each with state, last
run, next scheduled fire, and a "Runs" expander — plus "+ New Workflow" and
per-row "Edit", both opening the builder. Bottom, only when present: the
existing CC fan-out trees under a "Claude Code fan-outs (live)" divider.
They are transient 24-hour reconstructions; when none exist the section is
absent, not empty. No rename, no second tab, no orphaned menu entry.

The builder itself opens as its **own modal**, like the Scheduler
(`openScheduler`, `static/js/scheduler.js:10`) — because workflows are
global, cross-project objects and a project tab is the wrong owner for
authoring them. The tab is the door; the modal is the room.

### Q7 — Authoring UI: a spine, not a graph

> **REVERSED — Ron, 2026-09-11, after using the shipped spine UI (Phase 2,
> `3c67e73`).** His words: *"It is definitely a step in the right direction.
> However, I would like it to be more intuitive. I think it should be blocks
> that appear on the side and the user drags them onto the canvas and places
> them in the order he wants. User should then be able to connect them in the
> order he wants (drag lines / arrows) and create dependencies based on
> that."*
>
> The decision below was deliberate: it read Zapier's own editor — the
> reference image — as a vertical list, and it was right about that. It was
> reversed by the person it was built for, on contact with the built result.
> The replacement (palette, drag-to-place, drag-to-connect) and its runtime
> consequences are settled in §Revision 2. The original text is kept below,
> unedited, for the record.

Zapier's own editor — the reference image included — is **not** a free
canvas. It is a vertical list: trigger card, numbered step cards, connectors
down the spine, and a Paths node that splits into side-by-side columns.

**Decision: build exactly that and nothing more.** A vertical spine of cards
rendered with ordinary DOM and CSS connectors. A Paths node splits the spine
into columns, one per branch, each column its own vertical list running to
its end. No drag-to-connect edges, no free node positioning, no pan/zoom
canvas, no graph library. Since v1 has no rejoin, the layout is a tree of
lists — CSS grid does it.

Each card edits in place: an agent step reuses the pieces the schedule form
already has (project select, persona picker with `reloadSchedCharacters`'s
caching pattern, prompt textarea — `static/js/scheduler.js:333-395`); a
Paths card is a list of labelled branches plus the fixed otherwise row; an
approval card is a list of option labels.

Module mechanics: `static/js/workflow-builder.js`, an ES module like its
siblings — top-level declarations are module-scoped, so every handler target
and cross-module entry point is exposed via `window.` accessors, following
the interop block pattern at `static/js/scheduler.js:626-650`.

---

## Acceptance: the Desk pipeline, expressed

| Desk phase | Builder node |
|---|---|
| Runs on a cadence | Trigger: schedule (weekly, via the linked schedule record) |
| Harvest signals | Clayrune action: Desk harvest |
| Triage / score | Agent step: Posy, prompt over `{{prev.output}}`; declares outcome `worth_drafting` or `nothing` |
| Nothing worth saying produces nothing | Paths: `nothing` → end. A requirement of THE_DESK_SPEC §2, expressible here as a branch to a terminal, not a hack |
| Draft per voice | Agent step on the `worth_drafting` branch: Posy briefs per voice; drafts land PENDING in the existing Queue |
| Human approves | Approval gate ("release" / "push back") — the permanent gate, per the field scan; the runner cannot remove or answer it |
| Publish | Not yet built (THE_DESK_SPEC "Still to build"). When it exists it is one more allowlisted Clayrune action behind the gate — the workflow shape does not change |
| Measure | Agent step after publish, on the next cadence; reads the ledger |

Every phase maps to a v1 node; the one unmappable phase (publish) is
unmappable because the underlying capability does not exist yet, not because
the builder cannot express it. The acceptance test passes.

## Server surface (names only, no code)

- `GET/POST /api/workflows`, `PUT/DELETE /api/workflows/<id>` — definition CRUD.
- `POST /api/workflows/<id>/run` — start a run (manual trigger and Run Now).
- `GET /api/workflows/<id>/runs`, `GET /api/workflow-runs/<run_id>` — history
  and one run's step-by-step state.
- `POST /api/workflow-runs/<run_id>/decision` — resolve an approval gate.
- Runner: a small module (`mc/workflows.py` + `mc/blueprints/workflow_routes.py`)
  that drives steps off the **existing** completion callback
  (`_maybe_notify_spawner`, `agent_routes.py:4428` — generalised to also
  notify a waiting run, the same latch, both Mode A exit and Mode B turn
  boundary). No polling loop, no new process.

## Open — named, not hidden

- **SETTLED (Ron, 2026-09-10): an agent step may NOT create or edit workflow
  definitions, attended or unattended. The CRUD ships non-agent-callable.**
  The spec had proposed attended-yes/unattended-no, reading the authority
  guard as a question of who is watching. It is not — it is a question of
  what the machinery may author. A workflow definition dispatches agents,
  with characters, on a schedule, with the full tool fleet; an agent writing
  one is self-expansion in a new costume, and a human approving a pipeline
  they did not design is the same rubber stamp `_authority_violation()`
  exists to pre-empt (80 promoted vs 2 rejected, measured 2026-07-11). The
  human-in-the-loop half of the rail is satisfied by the human AUTHORING the
  workflow. See `position_whetheranagentsessionmaycreateoreditworkflowdefi`.
- **Open: run-history retention numbers.** Recommendation: keep last 50 runs
  per workflow or 30 days, whichever is more, mirroring schedule-runs
  paging (`scheduler_routes.py:1276`).
- **Open: whether the per-step persona pin should survive a persona being
  deleted.** Recommendation: mirror the schedule form's `missing` badge
  behaviour (`scheduler.js:189-195`) — run with no persona, mark the card.

---

*v1 build order (Phases 1–2 shipped before the reversal): runner + stores →
scheduler `workflow_id` + calendar badge → tab list → builder modal.
Superseded by §Revision 2's build order below.*

---

# Revision 2 (2026-09-11) — the canvas, and the DAG underneath

Q7 is reversed (banner above, Ron's words verbatim). This section settles
what the reversal forces. Everything here is a decision with its
recommendation marked; Ron approves choices, he does not do design.

**What survives the reversal, unchanged and binding:** definition CRUD stays
non-agent-callable (`position_whetheranagentsessionmaycreateoreditworkflowdefi`;
enforced at `workflow_routes._refuse_if_agent_caller`). Storage stays a
sibling of `DATA_DIR`, never a member. No cycles. No third-party connectors,
no HTTP/code/eval nodes. The `wf:result` handoff (Q3). Approval gates are
human-only and the runner can never satisfy one. One live run per workflow.
The scheduler/calendar wiring (Q4). The Desk pipeline is still the
acceptance test. Touch must work — Ron authors from his phone.

## R2-D1 — Store: nodes + an explicit edge list. Not `depends_on`.

**Decision: flat `nodes` array plus an `edges` array; `_next` is deleted.**

```json
{ "format": 2,
  "nodes": [ {"name":"triage","type":"agent","project_id":"…","prompt":"…","x":220,"y":140} ],
  "edges": [ {"from":"harvest","to":"triage"},
             {"from":"triage","to":"draft","when":"worth_drafting"} ] }
```

Why edges and not per-node `depends_on`: a branch is a property of the
CONNECTION (this arrow fires on outcome `worth_drafting`), not of either
endpoint — `depends_on` on the child cannot say which outcome of the parent
routes here without smuggling edge data into the node. And the canvas draws
nodes and arrows; the store should hold exactly what the canvas draws.
`when` is optional: absent = unconditional; a label = an outcome (agent
step) or a choice (approval gate); the literal `"otherwise"` is reserved
(§R2-D6). Compilation (`compile_workflow`) becomes: build adjacency, Kahn
toposort, yield per-node `_parents`/`_children` (children carrying their
`when` labels). The runner never walks raw JSON, same as today.

**Migration:** no stored definitions exist on this box (verified 2026-09-11,
`data/workflows.json` absent) — but Phase 1 is on the release channel, so
other installs may hold v1 records. The loader upgrades mechanically: a
record without `format` is a v1 nested tree; its compile already yields a
unique linear order, which is emitted as nodes + edges (Paths branches →
conditional edges, branch labels → `when`), default positions assigned in a
column, `format: 2` stamped on next save. Deterministic, lossless, no
migration tool. `STORE_VERSION` in `mc/workflows.py` is currently never
written to disk — the `format` field per record fixes that as a side effect.

## R2-D2 — Join semantics: ALL parents, with skip-propagation

The question v1 dodged by having no rejoin, and the one that silently hangs
a run if answered lazily.

**Decision: a node with multiple incoming edges runs when EVERY parent is
terminal for this run — `completed` or `skipped` — and at least one is
`completed`.** FIRST-parent-wins is rejected: it is nondeterministic under
any future parallelism and immediately raises "does the node run twice?".

**The dead branch cannot hang the join, by construction:** at the moment an
outcome or approval choice is decided, the runner marks every node reachable
ONLY through the untaken edges as `skipped`, transitively, by plain graph
reachability. Deterministic, immediate, no timeout, no waiting-forever
state. If ALL of a node's parents end up skipped, the node itself is skipped
— skip propagates through joins to each branch's natural end. The run view
greys skipped nodes and names the decision that killed them.

## R2-D3 — Cycle detection: at connect, at save, at run

A cycle saved and caught only at run time is a workflow that looks fine and
never works. **Decision: three layers, same check.**

1. **At connect (the one the user sees):** the drag that would close a cycle
   is refused at drop — the edge snaps back and a toast names the two nodes
   ("draft → triage would create a loop"). A doomed graph is never drawn.
2. **At save:** `validate_workflow` toposorts; a cycle is a hard validation
   error listing the member nodes. This guards direct API writers, not just
   the canvas.
3. **At run start:** `compile_workflow` re-checks — defence in depth against
   a hand-edited store file. The run fails at step zero, loudly.

## R2-D4 — "Ready", the frontier, and restart adoption in a graph

**Ready** = the R2-D2 rule. The run record replaces `current_step` with
`frontier`: the list of ready-but-not-started node names. It is derived
state — recomputable at any time from `steps` statuses plus the compiled
graph — persisted only for run-view legibility.

**Execution stays serial.** The runner takes ONE frontier node at a time, in
topological order, ties broken by stored node order. At most one agent step
is ever in flight per run. This is what keeps the reversal cheap where it
matters: Q5's one-live-run reasoning, the completion-callback latch, and
single-witness restart adoption all survive intact.

**Restart adoption:** since at most one step can be `running`, adoption is
the same shape as today — confirm that step against the agent_log
(`completed`/`idle` → replay through `on_agent_step_complete`; anything else
→ the run is `interrupted`, fail-closed, never silently re-dispatched).
Then recompute the frontier from persisted step states and continue. The
list-walk is gone; the invariant is untouched.

## R2-D5 — Slot scope: a slot must name a DOMINATING ancestor

In a DAG, `{{steps.X.output}}` can name a step that was skipped.
**Decision: a slot in node N is valid iff X is an ancestor of N on EVERY
path from the trigger to N** (X dominates N). A dominator can never be
skipped while N runs, so a valid slot always resolves.

- The builder enforces it at the same strength the spine enforced reorder:
  an edge add/delete or node delete that breaks an existing slot reference
  is refused, with the reference named ("draft uses {{steps.triage.output}};
  this edge removal makes triage skippable").
- The runtime keeps Q3's loud unresolved-slot failure as the backstop.
- `{{prev.output}}` is valid only in a node with exactly one parent; the
  builder refuses it elsewhere. At a join, "previous" is ambiguous and an
  ambiguous slot is a fabricated prompt.

## R2-D6 — Paths retired; conditional edges are the one way to branch

Two ways to branch is one too many. **Decision: the Paths node type is
deleted from the palette; an agent step whose outgoing edges carry `when`
labels IS the branch.** The `wf:result` mechanism (Q3) is unchanged — the
outcome vocabulary appended to the prompt is exactly the outgoing `when`
labels. Approval gates work identically: their options are the `when` labels
on their outgoing edges; the decision stays human-only.

**Mandatory otherwise survives as a PORT.** Any node with a conditional
outgoing edge always shows an `otherwise` port; missing or unparseable
output routes there. An unconnected port is a deliberate, visible run-end —
the canvas renders a stop stub on it. The fail-closed property is preserved:
where a non-answer goes is always authored and always visible, never
guessed. An unconnected declared-outcome port means the same thing (the
Desk's `nothing` outcome is exactly this — a stopped branch you can see).

## R2-D7 — The canvas: hand-rolled SVG + DOM. No graph library.

**Decision: palette docked left (bottom sheet under the mobile breakpoint),
drag-to-place onto the canvas, drag-to-connect from an output port to an
input port. Node positions persist per node (`x`,`y`, R2-D1).**

Q7 banned a graph library; the reversal does not un-ban it, because the
reasons are independent of layout:

- **React Flow / @xyflow** — needs React and a bundler; `static/js` is plain
  ES modules served as-is, no build step. Not adoptable without changing how
  the frontend ships.
- **Drawflow / LiteGraph** — vanilla-JS, but each is thousands of lines with
  its own styling regime, vendored into a repo that ships to strangers'
  machines (CLAUDE.md: what we commit, others run). Ownership cost exceeds
  what they buy.
- What a library buys here is small on this codebase: pointer-drag already
  exists (`floor.js` drag-to-hire); edges are one absolutely-positioned SVG
  overlay drawing cubic paths between port coordinates; pan/zoom is a CSS
  transform on the canvas container. Honest estimate: the canvas is the
  largest UI piece of the feature, and still smaller than owning a
  dependency.

**Touch is first-class, not adapted:** pointer events throughout; the known
mobile trap (`touch-action: none` blocks page scroll — the drag-to-hire
fix) handled the same way, a dynamic class applied only during an active
drag; ports get ≥ 40 px hit targets; pinch-zoom on the container.

**What Phase 2 built is not discarded:** the spine builder's card editors
(project select, persona picker, prompt textarea, option labels —
`static/js/workflow-builder.js`) are reused verbatim inside canvas nodes.
R2 replaces only their layout and connection model. The spine stays live
until the canvas lands — no interregnum with no builder.

## Acceptance re-walked: the Desk, in edge form

Trigger (schedule) → harvest (action) → triage (agent) —`worth_drafting`→
draft (agent) → approval gate —`release`→ publish (future action) →
measure. Triage's `nothing` port unconnected: the Desk's "nothing worth
saying produces nothing" requirement, now literally visible on the canvas
as a stopped branch. Triage's `otherwise` port also unconnected — a
non-answer ends the run, authored. Every phase maps; the acceptance test
passes with fewer node types than v1 needed.

## Build order, revised

1. Store `format: 2` + v1 upgrade-on-load; compiler → adjacency/toposort.
2. Runner: frontier, skip-propagation, join rule, restart adoption (D2/D4).
3. Validation: cycles at save, dominator rule for slots (D3/D5).
4. Canvas replacing the spine layout; card editors reused (D7).
5. Run view: skipped-node greying, edge-decision display.

## Open — named, not hidden

- **Open: whether pan/zoom state (viewport) persists per workflow.**
  Recommendation: yes, two numbers on the record; trivial and it is what
  makes a phone reopen usable.
- **Open: auto-tidy (one-click layout of a messy graph).** Recommendation:
  defer; positions are author-owned in v2, revisit after real use.

---

# Revision 3 (2026-09-11) — the full vocabulary: actions, nodes, triggers, failure

Ron's ask: map the whole action surface — "think of all possible scenarios."
This section does the enumeration honestly and then cuts it, because an
allowlist that mirrors the API is not an allowlist. Every candidate below is
derived from what the server can already do (`data/agent_reference/
CLAYRUNE_API.md`, the blueprints), not imagined. Every recommendation is
marked; every rejection carries its reason.

**The test an action must pass, restated:** an action node runs with NO agent
in the loop, unattended. So it must be (a) deterministic — same inputs, same
call, no judgement; (b) inward-facing or gated (§R3-2); (c) incapable of
widening what workflows can do (§R3-3); (d) clean on data ownership
(DATA_DIR, Scribe) and secrets (no credential parameters, ever — a template
slot that could carry a token is a transcript leak by construction).

## R3-1 — The candidate table, complete

Verdicts: **IN** (v2 allowlist) · **GATED** (allowlisted only behind a
mandatory approval dominator, §R3-2) · **OUT** (excluded, reason given).

| Candidate action | Maps to | Deterministic? | v2 | Why |
|---|---|---|---|---|
| `backlog_create` | `POST /api/project/<pid>/backlog` | yes | **IN** (ships today) | Inward record write; the Desk and any triage pipeline files follow-ups with it. |
| `backlog_patch` (status/text) | `PATCH …/backlog/<id>` | yes | **IN** (ships today) | State change on an item is always allowed, even unattended (AGENT_RULES). |
| `desk_harvest` | `POST /api/desk/signals/harvest` | yes | **IN** (ships today) | Deterministic scan; first phase of the acceptance pipeline. |
| `journal_append` | module fn: append to `docs/_journal/<item>-<slug>.md` (small helper in `mc/workflows.py`; no HTTP route exists or is needed) | yes | **IN** (new) | The sanctioned unattended log path. A pipeline that runs on a cadence needs a durable record of what each run did, and backlog notes are forbidden to unattended machinery — this is the replacement the 2026-08-15 rule itself names. |
| `notify_operator` | `mc/question_channel.py` email path — the channel approval gates already use; recipient fixed by server config, **not a node parameter** | yes | **IN** (new) | "Tell Ron X happened" without parking the run. Deterministic send to one authored-nowhere recipient. This is not general mail (see `mail_send` below); the recipient not being authorable is what keeps it inward-facing — it is the operator's own interrupt channel, and it answers the separately-raised alerting ask together with §R3-6. |
| `restore_point_create` | `POST /api/backup/restore-point/<pid>` | yes | **IN** (new) | Cheap, reversible, inward. A pipeline about to mutate project records snapshots first — the reversibility rule practiced, not just obeyed. |
| `backlog_note` | `POST …/backlog/<id>/note` | yes | **OUT** | BINDING 2026-08-15: unattended machinery never writes backlog notes. A workflow run is unattended machinery. `journal_append` is the sanctioned equivalent. |
| `backlog_link` / attachments | `POST …/links`, `…/attachments` | yes | **OUT** | Deterministic and harmless, but no pipeline needs them. The allowlist grows on demonstrated need, not on harmlessness. |
| `desk_triage` | `POST /api/desk/triage` | **no** — dispatches Posy (`desk_routes.py:534`) | **OUT** | Not an action at all: the route spawns an agent. In a workflow this is an **agent step**; wrapping it as an action would fire an unwitnessed agent outside the completion latch, breaking R2-D4's serial, witnessed model. |
| `desk_draft` | `POST /api/desk/draft` | **no** — dispatches Posy (`desk_routes.py:386`) | **OUT** | Same reason, same remedy: agent step. |
| `desk_publish` | does not exist yet (THE_DESK_SPEC "Still to build") | yes when built | **GATED** (future) | The first outward-facing action. Enters the allowlist only with the `gated` flag; see §R3-2 for why gated rather than excluded. |
| `desk_ledger_record` | `POST /api/desk/ledger` | yes | **OUT** (until publish) | Bookkeeping for a publish; ships as part of the `desk_publish` action when that exists, not separately. |
| `mail_send` (arbitrary recipient/subject) | `tools/night-review/send_mail.py` / SMTP | yes mechanically | **OUT** | Outward-facing with an authorable recipient — that is publishing, and a template slot feeding a recipient field is an exfiltration primitive. `notify_operator` covers the only legitimate case. |
| `memory_append` (session log) | `POST /api/project/<pid>/memory/append` | yes | **OUT** | MEMORY.md is Scribe-owned; and the 413-over-budget response demands trim-and-retry judgement no deterministic node has. Line 3 of the brief, applied. |
| `memory_position` | `POST …/memory/positions` | yes mechanically | **OUT** | A position is a ruling that outranks notes in every future prompt. Writing one requires the judgement that a question is *settled* — agent work at minimum, arguably human work. Machinery must not author rulings. |
| `browser_read` | `POST /api/browser/read` | **no** — live web | **OUT** | Untrusted third-party text piped by template into a later agent's prompt with no judgement between: a prompt-injection relay. An agent step reads the browser under the envelope discipline; an action never does. |
| `agent_dispatch` (fire-and-forget) | `POST …/agent/dispatch` | no | **OUT** | The agent node IS this, with a completion latch. An unlatch(ed) dispatch is a run the workflow cannot witness. |
| `hivemind_create`/`start` | `/api/hivemind/*` | no | **OUT** | Unattended agent fan-out with no gate — the burn-rate rule, and not deterministic work. |
| `schedule_create`/`edit`/`pause` | `/api/schedules` CRUD | yes | **OUT — authority guard** | A schedule can invoke a workflow (Q4). Machinery that writes schedules writes triggers — self-scheduling is self-expansion. Rejected whole: even pause-only blurs the line for one marginal convenience. |
| `workflow_*` CRUD, sub-run via API | `/api/workflows*` | yes | **OUT — authority guard** | The position (`position_whetheranagentsession…`) refuses this to agents at the route layer; an action doing it would be the same violation with fewer keystrokes. A workflow must not author what workflows can do. |
| `skill_*`, `mcp_*`, roster/character writes | `/api/skills`, `/api/mcp`, roster routes | yes | **OUT — authority guard** | Each one changes what future agents can do or who they are. The constitutional bright line (`_authority_violation`), verbatim. |
| `terminal_launch` | `POST /api/terminal/launch` | runs arbitrary command | **OUT — authority guard** | A code/eval node through the side door — Q2 banned it by name. |
| `github_sync` | `POST …/github/sync` | yes | **OUT** | Writes to an external service. No pipeline needs it; if one ever does, it enters as GATED, not IN. |
| `system_restart`/`update`, `process_kill` | `/api/system/*`, `/api/processes/*` | yes | **OUT** | Restart requires explicit human approval (standing rule); killing PIDs is the process-hygiene incident waiting to recur. |
| `backup_rollback` | `POST /api/backup/rollback/…` | yes | **OUT** | Destructive overwrite of current state. Creating a restore point is reversible; using one is not. Human-only. |
| `config_patch` | `PUT /api/config` | yes | **OUT — authority guard** | Config includes the flags that govern agents and workflows themselves. |

**v2 allowlist, final: `backlog_create`, `backlog_patch`, `desk_harvest`,
`journal_append`, `notify_operator`, `restore_point_create`.** Six verbs,
zero gated entries until `desk_publish` exists. Growing the list remains a
spec change, not a config change (Q2, unchanged).

## R3-2 — Outward-facing actions: gated, not excluded — and the gate is structural

The brief's question: is outward-facing (publish, mail, push, pay) excluded
entirely, or gated behind a mandatory approval node?

**Decision: gated — because exclusion would break the acceptance test, and
the gate can be made structural rather than conventional.** The Desk's
publish phase is the reason this feature exists; excluding outward actions
forever means the pipeline's last step is permanently manual retyping, the
exact failure the reopen condition names. SHARED_RULES' reversibility rule
does not say "never take irreversible steps" — it says they happen only on
Ron's go-ahead, emailed when unattended. An approval gate delivered over the
question channel IS that go-ahead, on the sanctioned channel, recorded on
the run.

The mechanism, so it cannot rot into convention: each allowlist entry
carries a `gated` flag. `validate_workflow` refuses to save — and
`compile_workflow` refuses to run — any graph where a gated action is not
**dominated** by an approval gate (every path from the trigger to the action
passes through one). The dominator machinery already exists (R2-D5); this
reuses it. An author cannot wire publish around the human even by hand-editing
the store file, and the runner still cannot answer the gate (Q5, the
position). One gate can dominate several gated actions — approving "release"
once covers the publish and its ledger write.

What stays excluded even with a gate: anything with an authorable external
recipient or target (`mail_send`, `github_sync` today, payment forever). A
gate proves a human said "go"; it does not sanitize where the action points.
Gated entries must point somewhere the server already owns.

## R3-3 — The authority guard, applied to the vocabulary

Rejected on this basis alone, enumerated so nobody re-litigates them one at
a time: workflow CRUD, schedule CRUD (including pause), skills CRUD, MCP
CRUD, roster/character writes, config writes, terminal launch. The common
shape: each writes something that governs what agents or workflows can do or
be. A workflow that can write triggers, definitions, skills, or tools is a
workflow that can widen workflows — self-expansion wearing a new costume,
same as the position's reasoning for the CRUD refusal. This holds even
though a human authored the workflow: the human authored *this* pipeline,
not the pipeline it could author for itself.

## R3-4 — Node types: the palette does not grow in v2

Evaluated, per the brief. The runner is serial, single-live-run, frontier +
skip-propagation, advancing only on callbacks — it has no clock. That fact
prices everything here.

- **Wait/delay — OUT.** A delay needs a timer that survives restart: a new
  persistence shape, a new adoption path, for a runner that today only wakes
  on completion callbacks and decisions. The trigger's schedule already owns
  cadence; "continue tomorrow" is two workflows on two schedules, or one
  schedule at the later hour. The Desk never waits mid-run — it waits
  between runs. Reopen if a pipeline genuinely needs an intra-run delay.
- **Notify — an ACTION, not a node.** It completes instantly and parks
  nothing; nodes that deserve types are the ones with distinct runtime
  behaviour (park, dispatch, branch). `notify_operator` (R3-1) covers it.
- **Sub-workflow call — DEFER.** Costs: run-in-run state the adoption scan
  doesn't model, one-live-run semantics ambiguous across parent and child,
  cycle detection across definitions instead of within one. Buys: reuse no
  second pipeline yet demands — the Desk is one graph. Reopen when two real
  workflows share a real sub-graph.
- **Foreach — OUT.** The handoff moves prose plus a small result dict
  (Q3); there is no list type to iterate. Per-item fan-out of agent work is
  Hivemind's job, and a foreach over agent steps inside a serial runner is
  a hidden loop — Q2's no-cycles rule approached from behind.

## R3-5 — Triggers: `manual` and `schedule` stand; events are named, not built

An event trigger (backlog item changed, agent finished, file landed, queue
item approved) is real only when three things exist, none of which do:

1. **Emit points** — a single `emit_event(type, payload)` called at the
   mutation sites (backlog PATCH, the completion callback, Desk queue
   transitions). The completion latch (`_maybe_notify_spawner`) is the one
   near-real candidate; file-landed would additionally need a watcher
   process, which is a new resident cost.
2. **A trigger record** — `{event_type, filter}` on the workflow, with
   defined matching semantics.
3. **Self-trigger suppression** — a workflow whose own `backlog_patch`
   action re-fires its own backlog trigger is an infinite loop the DAG
   rules cannot see, because it crosses run boundaries. One-live-run blunts
   the storm but does not prevent the cycle.

**Decision: defer the whole class.** Manual + schedule expresses the Desk
and every named second pipeline. When an event trigger is built, it starts
with `agent_finished` (the latch exists) and ships suppression on day one.

## R3-6 — Failure is a first-class path: `on_failure` edges, and the runner tells someone

`otherwise` is not error handling and was never claimed to be: it routes an
agent that *answered without declaring a valid outcome*. A step that
**fails** — dispatch refused, agent errored, action HTTP error, unresolvable
slot — currently kills the run with a log line (`_fail_run`, workflows.py:733)
and tells no one. Ron has separately asked for alerting on failures; the
vocabulary should express this, and the runner should backstop it.

**Decision, two layers:**

1. **A reserved `on_failure` edge label**, alongside `otherwise`. Any node —
   including an action node — may have outgoing edges with `when:
   "on_failure"`; they fire when that step fails, and the step's error text
   is exposed as `{{steps.<name>.error}}`. This does not violate the
   no-conditional-edges-on-actions rule, because that rule exists to keep
   actions out of the *judgement* business — an outcome is judged, a failure
   is a fact the runner already holds. Skip-propagation handles both
   directions for free: on success the failure branch dies, on failure the
   success branch dies (R2-D2, unchanged). No `on_failure` edge → the run
   fails exactly as today. The canonical use: failure edge → `notify_operator`
   → end. The builder renders the port red; it is never mandatory.
2. **A runner backstop, not authored:** when a run ends `failed` or
   `interrupted` and no `on_failure` edge consumed the failure, the runner
   sends one operator-channel notification naming the workflow, the step,
   and the error. Config-gated (`workflow_failure_notify`, default ON),
   deliberately not a node — an author forgetting to wire error handling
   must not mean silence, and the email-brevity rule shapes the message.
   Per-failure, never per-cycle, so it does not train the inbox-ignoring
   failure mode PushNotification warns about.

`on_failure` joins `otherwise` as the only reserved `when` values; the
validator refuses either as an authored outcome label.

## Acceptance, re-walked once more

Harvest (action) → triage (Posy, agent) —`worth_drafting`→ draft (Posy,
agent) → approval gate —`release`→ publish (**future gated action**, its
gate the dominator R3-2 requires) → ledger write (folded into publish) →
measure (agent, next cadence). Failure on any step: `on_failure` →
`notify_operator`, or the runner backstop. Every phase maps; the pipeline's
log lands in `journal_append`, not backlog notes. The acceptance test still
passes, now including the phase v1 could not express.

---

*Spec only, all three revisions. No implementation in this change.*
