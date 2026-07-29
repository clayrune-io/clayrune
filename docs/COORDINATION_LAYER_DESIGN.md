# Cross-Agent Coordination Layer — Design

**Status:** DRAFT v1 (design only — no product code changed)
**Backlog:** `9518ec62` (this) · sibling `b264200a` (per-agent worktree isolation)
**Date:** 2026-07-29
**Author:** Vector (side task for Ron)

---

## 1. The problem (stated precisely)

Two or more agents run **on the same project at the same time**, working on
**interdependent** features — *not* the same files. Today they are **blind to
each other**:

- Agent B re-implements something Agent A just finished.
- Agent B picks an approach that Agent A's just-landed change made obsolete or
  contradictory.
- Neither knows the other exists, let alone what it *intends* to do next.

This is a **coordination / awareness** problem, distinct from the
**file-conflict / isolation** problem tracked in `b264200a`. And the two pull in
opposite directions: **per-agent git-worktree isolation makes coordination
WORSE** — it buys edit-safety by putting each agent in its own checkout, so a
sibling's landed commit is *even less* visible than it was in a shared tree.

The industry has not solved this. The goal here is a **Clayrune-shaped** slice
that is useful, safe, and reuses what we already have — not a general solution.

**Design tenet:** isolation and awareness must be co-designed. Isolation without
awareness is the failure mode we are fixing; awareness without isolation is the
clobbering we already know about (`b264200a`). The coordination layer is the
awareness half; it assumes worktree isolation is (or will be) the safety half,
and the two are specified together in §5(d).

---

## 2. What already exists (investigation)

### 2.1 Hivemind primitives — `mc/blueprints/hivemind_routes.py` (1830 LOC, 28 routes)

Hivemind is a **persistent multi-agent collaborative-intelligence** subsystem.
Everything is **file-backed JSONL/markdown** under `HIVEMIND_DIR/<hm_id>/` — no
DB, no in-memory bus. State survives restarts. The pieces:

| Primitive | Storage | What it provides | Wiring |
|---|---|---|---|
| **Manifest** | `manifest.json` | goal, status, config (max_concurrent_workers, models, retries) | `_hm_load/save_manifest` |
| **Workstreams** | `workstreams/<ws>.json` | a unit of work: title, description, **dependencies** (DAG), priority, status, `current_agent_session_id` | `_hm_load/save/list_workstream(s)` |
| **Message bus** | `bus/messages.jsonl` | append-only `{from,to,type,content,title,references,timestamp}`; types incl. `finding_report`, `question`, `escalation`, `directive`, `status_update` | `_hm_append/read_bus_message`; POST `/bus/post`, GET `/bus/poll/<ws>?since=`, `/bus/history`, SSE `/bus/stream` |
| **Findings** | `workstreams/<ws>_findings.jsonl` | structured discoveries with confidence/evidence/tags; auto-appended when a `finding_report` hits the bus | `_hm_append/read_findings` |
| **Knowledge base** | `knowledge/{synthesis.md, decisions.jsonl, open_questions.jsonl}` | rolled-up synthesis, decision log, open-question tracker | `_hm_read/write_synthesis`, `_hm_append/read_decisions`, question resolve |
| **Handoff** | `workstreams/<ws>_handoff.md` | Phase-2 doc a finishing worker leaves for the next worker on that workstream | `_hm_read/write_handoff` |
| **Accumulated context** | `workstreams/<ws>_context.md` | free-form carry-over context per workstream | `_hm_read/write_context` |
| **SSE fan-out** | in-mem `_hivemind_sse_queues` | pushes bus/finding/status events to *browser* listeners | `_hm_push_sse` |
| **Server orchestrator** | daemon thread, 10 s tick | dependency resolver + worker scheduler + finished-worker reaper + retry + final-synthesis trigger | `_hivemind_orchestrator_loop` |

**How a worker actually consumes context (the load-bearing detail):**
`_hm_build_worker_context()` assembles a **system-prompt string** — handoff +
accumulated context + recent findings + bus messages *filtered to this ws* +
relevant decisions + a "YOUR CAPABILITIES (use curl …)" block listing the bus
endpoints. That string is injected **at spawn** via `--append-system-prompt`
(claude) or prepended to the task (other providers). The worker is a
**single-shot `claude -p … --max-turns N`** subprocess (`_hm_spawn_worker_session`).

**Why this is scoped to ANALYSIS, not live parallel DEV — three structural reasons:**

1. **Consumption is spawn-time-static + agent-initiated pull.** A worker's
   awareness of siblings is a snapshot frozen into its system prompt at launch.
   It is *told* it can `curl …/bus/poll/<ws>` to get newer messages, but nothing
   makes it — and a single-pass `-p` worker rarely loops back to poll. There is
   **no push** of a sibling's new message into a running worker's context.
2. **Coupling is via DAG dependencies, not live signals.** Workstreams are
   sequenced by `dependencies: [ws_ids]`; the orchestrator only spawns a
   downstream worker once its upstreams are `completed`. Interdependent work is
   handled by **serializing** it (A finishes → hands off → B starts), not by
   letting A and B run concurrently and stay in sync.
3. **The output is knowledge, not code.** Workers "Report findings … do NOT
   write to project MEMORY.md … your findings go to the bus only." The
   explicit contract is *analyze and report*, and the RULES block even forbids
   the MEMORY write. Nothing in Hivemind touches the working tree, does commits,
   or reconciles edits.

So Hivemind gives us a **durable, file-backed, HTTP-addressable pub/sub bus +
shared knowledge base + a DAG scheduler + an SSE fan-out** — but it is bolted to
a *serialize-by-dependency, report-don't-edit, pull-don't-push* model. The
coordination layer needs the **primitives** (bus + knowledge + SSE) with a
different **delivery + semantics** layer on top.

### 2.2 Read-floor injection — `_build_agent_context()` (agent_routes.py:1551)

This is the model the task calls "read-floor style." At **dispatch** (and at
`-r`/`--continue` respawn via `_respawn_sysprompt_args`), the server assembles
the system prompt from: rules → API reference → **`RELEVANT MEMORY`** (ranked
grep over memory via `_memory_search`, top-K=`read_floor_topk`) →
**`RELEVANT PAST EXPLORATIONS`** (distiller `exploration_read_floor`) → recent
activity/conversations.

**Critical property for this design:** the read-floor is **built once per
process spawn**. It is *not* a live channel. In **Mode B** (the default runtime,
persistent process), a running agent's system prompt is fixed for the life of
the process; new context only enters via (a) a **new user message**
(`agent/send` → interrupt-and-resume or followup), or (b) a **respawn** that
rebuilds the prompt. There is **no mid-turn push** into a live agent today.
`agent/send` with `decision == 'interrupt'` is the one existing vector that
injects text into a *running* Mode-B session — but it **interrupts the current
turn** to do it.

### 2.3 Per-project agent isolation — `ProjectAgentManager` (agent_routes.py:621)

One manager per `project_id`: its own `RLock`, its own `session_ids` set, its own
guardian thread. The invariant is **"no lock is ever shared across
project_ids"** so a hung kill in one project can't stall another. Crucially:
**this isolates projects from each other, and it isolates *state/dispatch*
races — it does NOT isolate file edits.** Two agents in the same project share
one `project_path` working tree; the per-project lock does nothing about two
agents editing the tree at once. That gap is exactly `b264200a`.

### 2.4 Worktree machinery — `project_sync.py` (512 LOC) + harness worktrees

Two *different* worktree concepts exist, and the backlog conflates them:

- **MC code-sync worktree (`project_sync.py`).** A **single hidden worktree per
  install** at `<project>/.clayrune/sync-tree/` on branch
  `clayrune/sync/<install_id>`. It exists to sync one machine's repo with a
  GitHub remote across *installs* (`_ahead_behind`, `_commits_between`, incoming
  = other installs' sync branches). It is **install-scoped, not agent-scoped**,
  and is a **read-only spike** (no auto-commit, no accept/cherry-pick, no
  conflict UI — explicitly out of scope per its header). The reusable parts are
  its **git subprocess wrapper** (`git_run`, `GitError`, argv-injection guards),
  `ensure_worktree`, `fetch`, `_ahead_behind`, `_commits_between`, `_dirty`.
- **Harness worktrees (`.claude/worktrees/agent-*`).** Claude Code's own
  `EnterWorktree`/`ExitWorktree` — a per-agent isolated checkout the *harness*
  manages (visible in the repo now: `agent-ad5012f1e01197183`, `be-final`,
  `fe-worker2`). This is the natural substrate for `b264200a`'s per-agent
  isolation, and it is what makes coordination *harder* (§1): a sibling's commit
  lands in its own worktree, invisible to the others.

**Neither** provides per-agent-worktree lifecycle *inside MC* today. `b264200a`
proposes building it "from the code-sync machinery"; realistically it is a thin
MC wrapper around `git worktree add/remove` (the `ensure_worktree` pattern),
one per live agent session, plus a merge/reconcile step.

### 2.5 The gap, in one sentence

We have a **durable file-backed pub/sub bus + shared knowledge + SSE fan-out
(Hivemind)** and a **static per-spawn read-floor**, but **no mechanism that
pushes a sibling agent's live intentions/landed-changes into another agent's
context mid-work**, and **no per-agent worktree + continuous-rebase substrate**
to make those landed changes materially available. The coordination layer is
exactly those two missing mechanisms, built on the existing primitives.

---

## 3. Design goals & non-goals

**Goals**
- Sibling agents on the same project publish **INTENTIONS** ("about to change X")
  and **COMPLETIONS** ("shipped Y"), and *receive* siblings' events **during**
  their own work.
- Delivery is **read-floor-shaped**: low-friction, injected into context, not a
  protocol the agent must remember to poll.
- A sibling's *landed code* propagates into other agents' worktrees mid-flight
  (awareness → actual availability).
- Compose cleanly with per-agent worktree isolation (`b264200a`).
- **Reuse Hivemind primitives**; add only what is genuinely missing.

**Non-goals**
- Not solving arbitrary distributed merge conflict resolution (frontier-level).
- Not a planner that decomposes a feature (that is Hivemind's orchestrator; this
  layer is peer-to-peer, planner-optional).
- Not cross-*project* coordination (the ProjectAgentManager invariant stands —
  coordination is strictly **intra-project**, keyed on `project_id`).
- Not machine-to-machine (that is `project_sync`); this is agents on **one**
  install sharing **one** working tree lineage.

---

## 4. Core concepts

Two event kinds on a per-project bus:

- **INTENTION** — `{agent, intent, targets:[paths/modules/symbols], summary,
  ttl, ts}`. "I am about to touch `mc/blueprints/agent_routes.py:agent_send` to
  add X." Published **before** the work; expires (TTL) or is superseded by a
  COMPLETION from the same agent.
- **COMPLETION** — `{agent, change, targets, commit_sha?, summary, ts}`.
  "I landed Y (sha abc123) touching those targets." Published **after** a
  commit / logical unit of work.

A sibling receiving these can: **defer** (someone's already on it), **adapt**
(their change changed my assumptions), or **coordinate** (ask via the bus).

**Targets are the join key.** Overlap between one agent's INTENTION targets and
another's is what makes an event *relevant* — the ranking signal for the
read-floor, analogous to how `_memory_search` ranks memory by task relevance.

---

## 5. Mechanisms

### (a) Intention / completion pub-sub bus

**Reuse:** the Hivemind bus verbatim in shape — append-only JSONL, HTTP POST to
publish, SSE to fan out. **Do not** reuse the `hivemind_id` scoping; scope on
`project_id` instead. Concretely, a **coordination session** per project:

```
data/coordination/<project_id>/
  events.jsonl          # append-only INTENTION/COMPLETION/COORD events
  agents.json           # live roster: session_id -> {label, worktree, started, last_seen}
```

*(Under `data/`, NOT `data/projects/` — respects the DATA_DIR-pollution
load-bearing rule; `data/projects/*.json` is projects-only.)*

New endpoints (mirror Hivemind's bus surface, project-scoped):
- `POST /api/project/<pid>/coord/publish` — body `{type: intention|completion,
  agent, targets, summary, ...}`. Appends to `events.jsonl`, pushes SSE.
- `GET  /api/project/<pid>/coord/events?since=&targets=` — pull (for agents that
  want to poll, and for the read-floor builder).
- `GET  /api/project/<pid>/coord/stream` — SSE (browser + server consumers).
- `POST /api/project/<pid>/coord/register` / `heartbeat` — roster upkeep.

**Publish side — how agents emit without remembering a protocol.** Two tiers:
1. **Automatic (preferred).** The server publishes on the agent's behalf from
   signals it already sees: on **dispatch**, emit an INTENTION derived from the
   task; on **detected commit** (see (c)) or **turn_complete**, emit a
   COMPLETION derived from the diff. Zero agent burden — this is the tier that
   makes the layer actually work, mirroring the "publish on behalf" spirit of
   how findings auto-append when a `finding_report` hits the Hivemind bus.
2. **Explicit (opt-in richness).** A `coord-publish` capability block (like
   Hivemind's "YOUR CAPABILITIES") lets an agent post a richer, forward-looking
   INTENTION ("next I will refactor the SSE slot manager") the server can't infer.

### (b) Subscription + mid-work injection (the hard part)

The read-floor is per-spawn-static (§2.2). Live coordination needs siblings'
events to reach an agent **during** its work. Three delivery modes, in
increasing intrusiveness — the design uses **all three, matched to event
severity**:

1. **Read-floor at every context build (baseline, free).** Extend
   `_build_agent_context()` with a **`SIBLING ACTIVITY`** section: query the
   coord bus for **open INTENTIONS + recent COMPLETIONS from other live agents in
   this project**, ranked by target-overlap with this agent's task/targets,
   top-K. This lands at dispatch **and at every respawn** (`_respawn_sysprompt_args`
   already re-injects). In Mode B, every followup turn that respawns picks it up.
   Cost: ~free (prompt-cache). Covers the common case: "when a sibling starts a
   turn, it sees who else is active."
2. **Turn-boundary digest (cheap, non-disruptive).** At the **start of each new
   turn** for a running session (Mode B followup), prepend a short system-note
   with any sibling events since that agent's last turn. No interrupt — it rides
   the turn the agent was going to take anyway. This is the primary *live* path.
3. **Interrupt-inject (rare, high-severity only).** For a **hard conflict** — a
   sibling published a COMPLETION whose targets directly overlap this agent's
   *open* INTENTION — use the existing `agent/send` **interrupt-and-resume**
   vector (§2.2) to inject: *"⚠ Sibling <label> just landed <change> on <targets>
   you declared intent to modify. Re-check before continuing."* This is
   deliberately reserved for genuine collisions because it interrupts the turn.

**Relevance gating is essential** — an unfiltered firehose trains agents to
ignore the section (the same failure mode `PushNotification`/AGENT_RULES warns
about for email). Gate on **target-overlap** + **liveness** (only other
*currently-running* sessions) + **recency/TTL**.

### (c) Continuous sync / rebase (awareness → availability)

An INTENTION/COMPLETION tells an agent a change *happened*; (c) makes the change
*present in its tree*. Assuming per-agent worktrees (§5d):

- **Detect a landed change.** After any agent's turn, if its worktree has a new
  commit (poll `git rev-parse HEAD` per worktree — reuse `project_sync.git_run`),
  publish a COMPLETION with the `commit_sha` and the diff's touched paths
  (`git diff --name-only`, feeds `targets`).
- **Propagate to siblings.** A **coordination daemon** (modeled on
  `_hivemind_orchestrator_loop` — a 10 s-tick daemon thread with
  `obs.heartbeat`) attempts, for each *other* live agent's worktree, a
  **`git rebase`/`merge` of the landed commit at that agent's next turn
  boundary** (never mid-turn — rebasing a tree an agent is actively editing is
  the clobber we're avoiding). Clean → the sibling silently gains the work.
  Conflict → **do not auto-resolve**; publish a COORD event + surface it via
  (b) mode-3 so the sibling agent (or Ron) reconciles. Reuse `_dirty`,
  `_ahead_behind`, `_commits_between` for the state math.
- **Cadence.** Rebase attempts are **turn-gated**, not time-gated: the daemon
  marks a worktree "sync-pending" when a sibling lands work, and the actual
  rebase happens in the quiescent window between that agent's turns (detectable
  via session `status` transitioning off `running`).

### (d) Composition with per-agent worktree isolation (`b264200a`)

This layer **assumes** and **co-specifies** per-agent worktrees. Proposed shape
(the missing piece `b264200a` calls for, sketched here so the two designs fit):

- On dispatch of a **concurrent** agent (2nd+ live session in a project), MC
  creates a per-agent worktree `<project>/.clayrune/agents/<session_id>/` on a
  branch `clayrune/agent/<session_id>` — a thin wrapper around
  `git worktree add -b …`, exactly `ensure_worktree`'s pattern. The agent's
  `cwd` becomes that worktree.
- The **roster** (`agents.json`) maps `session_id → worktree path + branch`, so
  the coordination daemon knows every tree to watch and rebase.
- **Isolation gives edit-safety; the coordination bus gives awareness; the
  turn-gated rebase gives availability.** The three together are the answer to
  "isolation makes coordination worse": isolation is retained, and the
  coordination layer is what buys the awareness back.
- **Single-agent projects pay nothing** — no 2nd session ⇒ no worktree, no bus
  chatter, no daemon work for that project. The layer only engages when ≥2 live
  agents share a project.

### Reuse map (what we take vs. build)

| Need | Reuse from | Build new |
|---|---|---|
| Append-only event log | Hivemind `_hm_append/read_bus_message` pattern | project-scoped `events.jsonl` helpers |
| Publish/poll/stream endpoints | Hivemind `/bus/*` routes | project-scoped `/coord/*` routes |
| SSE fan-out | `_hm_push_sse` + `_hivemind_sse_queues` | project-keyed queue dict |
| Background reconciler | `_hivemind_orchestrator_loop` (daemon + heartbeat + reaper shape) | coordination daemon (turn-gated rebase) |
| Context injection | `_build_agent_context` read-floor + `_respawn_sysprompt_args` | `SIBLING ACTIVITY` section + relevance ranking |
| Mid-turn inject vector | `agent/send` interrupt-and-resume | severity gate (mode-3 only) |
| Git plumbing | `project_sync.git_run/ensure_worktree/_dirty/_ahead_behind/_commits_between` | per-agent worktree lifecycle + turn-gated rebase |
| Liveness/roster | `ProjectAgentManager.iter_sessions` / `agent_sessions` | `agents.json` roster + heartbeat |

**Genuinely missing (must build):** (1) per-project (not per-hivemind) bus
scoping; (2) target-overlap relevance ranking; (3) the **push** paths — read-floor
`SIBLING ACTIVITY` + turn-boundary digest + severity-gated interrupt; (4)
per-agent worktree lifecycle inside MC; (5) turn-gated continuous rebase.

---

## 6. Phased implementation plan

**Phase 0 — PoC: awareness-only, no worktrees, no rebase (smallest useful slice).**
Prove the loop end-to-end with the least surface.
- Project-scoped coord bus: `events.jsonl` + `/coord/publish` + `/coord/events`.
- **Auto-publish** an INTENTION on dispatch (from the task) and a COMPLETION on
  `turn_complete` (from `git diff --name-only` on the shared tree).
- Read-floor `SIBLING ACTIVITY` section in `_build_agent_context`, gated on
  other-live-agents + target-overlap.
- **Value at end of Phase 0:** two agents dispatched on one project each *see*
  the other's declared intent and landed files at their next turn. No isolation,
  no rebase yet — pure awareness. This alone kills the most common duplication.
- **Risk contained:** read-only w.r.t. the tree; worst case is a noisy prompt
  section (mitigated by the relevance gate + a `coordination_enabled` global
  default-OFF flag).

**Phase 1 — live delivery.** Turn-boundary digest (mode-2) + severity-gated
interrupt (mode-3) via `agent/send`. Roster + heartbeat + SSE stream for the UI.

**Phase 2 — per-agent worktrees (needs `b264200a`).** Per-session worktree
lifecycle; roster maps session→worktree; agents run isolated. Coordination bus
now spans isolated trees (the case that matters).

**Phase 3 — continuous rebase.** Coordination daemon; turn-gated
merge/rebase of siblings' landed commits into other worktrees; conflict →
COORD event, never auto-resolve.

**Phase 4 — polish.** UI panel (live sibling map, event timeline — reuse the
Hivemind SSE UI shape); operator controls; per-project override of the global flag.

Each phase is independently shippable and default-OFF until proven, matching the
Distiller/Scribe "best-effort, never load-bearing" posture.

---

## 7. Risks

- **Notification fatigue.** If `SIBLING ACTIVITY` is noisy, agents learn to
  ignore it. → Hard relevance gate (target-overlap + liveness + TTL); start with
  a high bar, loosen only on evidence.
- **Interrupt disruption.** Mode-3 interrupts a live turn; overuse wrecks agent
  flow. → Reserved strictly for target-overlapping COMPLETION-vs-open-INTENTION
  collisions.
- **Rebase clobbering.** Auto-rebasing a tree mid-edit is the exact harm we're
  avoiding. → **Turn-gated only**; never touch a `running` worktree; conflicts
  escalate, never auto-merge.
- **Stale roster.** Hard-killed sessions leak roster entries (cf. the watermark
  GC gap that truncated the memory index). → Heartbeat + a startup reconcile
  sweep (mirror `_hm_reconcile_stale_on_startup` / `_gc_stale_watermarks`).
- **DATA_DIR pollution.** A stray file in `data/projects/` 500s the restart
  blockers. → Coordination state lives under `data/coordination/`, never
  `data/projects/`.
- **Cost.** Extra git polling + a daemon per active-multi-agent project. →
  Turn-gated (not tight-loop) polling; engage only when ≥2 live agents share a
  project.
- **Intention accuracy.** Auto-derived INTENTIONS from a task string may be
  vague. → Coarse targets are still useful for overlap; explicit publish tier
  sharpens when the agent opts in.

---

## 8. Open questions (need Ron's decision)

1. **Worktree ordering.** This design assumes `b264200a` (per-agent worktrees)
   lands to make Phases 2–3 real. Do we build coordination **awareness-first**
   (Phase 0–1 on the *shared* tree, worktrees later) — or block on `b264200a`
   and build them together? *(Recommendation: awareness-first — Phase 0 delivers
   value with zero worktree work and de-risks the rest.)*
2. **Auto-publish vs. explicit-only.** Is server-side auto-publish of
   INTENTIONS/COMPLETIONS acceptable (it's the tier that makes this frictionless),
   or do you want agents to publish **explicitly** so nothing is inferred? *(Rec:
   auto-publish as default, explicit as enrichment.)*
3. **Interrupt budget.** Are you OK with the layer **interrupting a live turn**
   (mode-3) on a hard conflict, or should even hard conflicts wait for the next
   turn boundary? *(Rec: allow interrupt, strictly target-overlap-gated.)*
4. **Planner vs. peer-to-peer.** Do we want an orchestrator that *decomposes*
   interdependent features into aware subtasks (Hivemind-style), or keep this
   purely peer-to-peer (agents dispatched independently, coordinating as
   equals)? *(Rec: peer-to-peer first; a planner is a separate, later layer that
   can sit on top.)*
5. **Reuse Hivemind wholesale, or fork the primitives?** Extend
   `hivemind_routes.py` to a project-scoped mode, or a new sibling module
   (`coordination_routes.py`) that copies the bus/SSE/daemon patterns? *(Rec: new
   module — Hivemind's DAG/serialize/report-don't-edit semantics are load-bearing
   for *its* use and we don't want to entangle them; copy the patterns, don't
   overload the routes.)*
6. **Scope of "targets."** Files, modules, or symbols? Symbol-level overlap is
   the most precise relevance signal but the hardest to derive. *(Rec: start
   file/path-level from `git diff --name-only`; add symbol-level later.)*

---

## 9. Summary

Clayrune already has the coordination **substrate** — Hivemind's durable
file-backed bus, shared knowledge, SSE fan-out, and a daemon-loop pattern — plus
a read-floor injection model and git plumbing from code-sync. What's missing is
**not infrastructure but delivery + semantics**: a project-scoped
intention/completion bus, **push** of relevant sibling events into a running
agent's context (read-floor + turn-digest + severity-gated interrupt), per-agent
worktree isolation, and a turn-gated rebase that turns awareness into
availability. Phase 0 (awareness-only, shared tree, auto-published, read-floor
delivered) is a small, low-risk slice that already kills the most common
duplication — and de-risks everything after it.
```