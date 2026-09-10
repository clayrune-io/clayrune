# Workflow Builder — authoring surface, wired to the scheduler

**Status:** v1 spec, 2026-09-10. Marlow. Backlog: MC-871 (reopened from
`wontdo` — see "Why now"). Reference image: `data/uploads/agent_058984469b.png`
(Zapier: trigger card, numbered steps, a Paths node, connectors down a spine).

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
- No parallel fan-out and no rejoin after a branch. Paths diverge and each
  path runs to its own end. (Fan-out is what Hivemind is for.)
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

*Spec only. No implementation in this change. Build order, if approved:
runner + stores → scheduler `workflow_id` + calendar badge → tab list →
builder modal.*
