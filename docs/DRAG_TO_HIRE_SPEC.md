# Drag-to-Hire — Spec

Status: **DRAFT v1 for Ron's approval** · Author: Marlow (prd-writer), 2026-09-09.
Every open question in the brief is settled below with a marked
**Recommendation** — approving this doc is approving those choices.

Parent model: `docs/CHANNEL_MODEL_SPEC.md` (project = channel, roster rows are
addresses). Governing position: split-by-lifespan
(`position_whethereachagentgetsitsownchatthreadorallshareth.md`) — hired staff
get their own room; hiring is a human act.

---

## 1. The problem

There is no gesture that puts an agent on a project. Today an agent joins a
project's channel only as a side effect of having worked there (Bench
membership is derived from attributed conversations — channel spec §10). Ron
wants the direct act: grab a figure on the Floor, drop it on a project tile,
and that agent is now on that project's roster with its own rail row, channel
open, ready to talk.

**Settled and not reopened here:** the drop HIRES — permanent roster
membership, its own rail row in that project. The chat opening is a side
effect of hiring, not the point of it.

## 2. Who it is for

Ron, operating the Floor — on desktop with a mouse, and from his phone. The
phone is not a degraded case: every behavior in this spec must be reachable
by touch and by a non-drag path (§8).

## 3. What hiring IS — the central technical decision

Two candidate mechanisms:

**(a) Copy the global character to project-local** — write the `.md` into
`<project_path>/.claude/agents/`, where `list_characters()` already
shadow-flags it.

**(b) A membership record** — the character file stays where it is; the
project records "this character is on my roster."

**Decision: (b), the membership record.** Copying is wrong on four grounds:

1. **It forks the artifact.** `mc/characters.py` is explicit that the file is
   the single artifact (engine keys live in frontmatter *"so the file stays
   the single artifact"*). Hire Fenn onto four projects by copying and an
   edit to Fenn's prompt now reaches one of five files.
2. **It writes into the user's repo.** `<project_path>/.claude/agents/` is
   inside the project's working tree. A hire gesture that silently adds a
   committable file to someone's repo violates least surprise, and on a
   public repo brushes the "nothing operator-specific in the repo" rule.
3. **Un-hire becomes deletion.** Removing a copied file that may have
   accumulated local edits is data loss; removing a membership record is not.
4. **Shadowing changes behavior.** A project-local copy *shadows* the global
   in `list_characters()` — hiring would silently pin the agent to the prompt
   as of hire date. Nobody asked for version pinning.

Project-local characters (created deliberately via the character editor)
remain exactly what they are. Hiring does not create them.

### 3.1 The membership record — shape and home

**Decision: a field on the existing project record, NOT a new sidecar.**
The DATA_DIR rule (CLAUDE.md) makes every new `data/projects/*` file a
foot-gun that must be suffix-excluded in `load_projects()` or it 500s both
restart endpoints. Membership is small, per-project state — precisely what
the project record (`data/projects/<id>.json`) already holds (backlog,
activity_log, default_character). No new file, no exclusion list change,
no new failure mode.

```json
"roster": [
  {"character": "global:code-reviewer", "hired_at": "2026-09-09T…",
   "hired_by": "drag", "removed_at": null}
]
```

- `character` is `<scope>:<name>` — the same ref format dispatch already
  accepts (`"character":"global:<type>"`).
- Entries are never deleted; un-hire sets `removed_at` (§5). The record is
  the authority for *deliberate* membership state, which derived
  participation cannot express.

### 3.2 Relation to the derived Bench

Channel spec §10 derives Bench membership from attributed conversations. The
merged rule after this feature:

> **Bench = (derived participants ∪ hired members) − explicitly removed.**

Hiring puts an agent on the Bench *before* any conversation exists. Removal
(§5) overrides derivation — an un-hired agent with history stays out of the
roster until re-hired, though the history itself remains (§5).

### 3.3 What hiring does NOT change — non-goals

- **Dispatch is not gated.** Any global type remains callable on any project
  via Task or dispatch, hired or not. Hiring is presence, not permission.
- **`_roster_block()` (agent_routes.py:2392) is unchanged in v1.** Agents
  keep seeing the full callable roster. Filtering the prompt roster to hired
  members is a possible v2, out of scope here.
- **The drag does not move work.** Dragging a figure whose session is
  running does not interrupt, transfer, or fork that session. The payload is
  the character ref, never the session.
- **No auto-hire on dispatch.** Working in a project still creates *derived*
  membership only, per the channel spec. This spec adds the deliberate path;
  it does not make every dispatch a hire.

## 4. API

- `POST /api/project/<pid>/roster/hire` body `{"character":"global:<name>"}` —
  idempotent. Creates or revives (`removed_at → null`) the entry. Returns
  `{roster: […], already_hired: bool}`. 404 for an unresolvable ref —
  including a `project:` ref whose file lives in a *different* project (§6).
- `DELETE /api/project/<pid>/roster/<scope>:<name>` — sets `removed_at`.
- Roster ships inside the existing project payload; no new GET.

## 5. Un-hiring

**Recommendation:** context-menu item **"Remove from this project"** on the
agent's rail row (Bench section), with a confirm step naming the agent and
project. Effects:

- The rail row disappears; the membership entry gets `removed_at`.
- **Conversations are KEPT, untouched.** They are derived from transcripts
  (channel spec §1 — kept forever) and remain visible in the channel stream
  and Chats, correctly attributed. Nothing is orphaned or archived; the
  *address* goes away, the *history* does not — exactly the §7 rail/history
  line.
- Re-hiring (any path) revives the entry; the row returns with its history
  intact.
- A removed agent with a LIVE run is not silently evicted: removal while
  "In the room" is refused with "Fenn is working here right now — remove
  after her run ends."

## 6. Drop semantics — the cases

1. **Global character → any project:** hire. Membership written, modal opens
   in Channel mode with that agent's row selected (filter + sticky re-point,
   per channel spec §4–5).
2. **Second drop of an already-hired agent:** **Recommendation: no-op hire,
   same side effect** — the modal opens on that agent's channel, with a
   quiet toast "Fenn is already on this project." Idempotent, never an
   error. The gesture means "work with Fenn here"; both halves of that are
   still true.
3. **Drop onto the project the agent is currently working in:** identical to
   case 2 (a working agent is a fortiori a participant). If it was a derived
   participant, the drop *upgrades* it to explicit membership — a harmless,
   meaningful write.
4. **Project-local character → a DIFFERENT project:** **Recommendation:
   refused, visibly.** During the drag, other projects' tiles do not light
   up for a `project:`-scoped figure and show the not-allowed treatment
   (§7); a forced drop does nothing. A project-local character is scoped by
   its file location; hiring it elsewhere would require copying (rejected,
   §3) or cross-project reach (forbidden by the project-boundary rule). The
   escape hatch is deliberate: promote the character to global in the
   character editor, then hire it anywhere.

## 7. The drag — "fade to background", concretely

Drag source: any Floor figure carrying a character (`fl-fig` elements,
floor.js). Figures of characterless sessions (plain Vector runs) are not
draggable — there is nothing to hire.

On drag start, the app enters **hire mode** (a class on the Floor root):

- **Dim:** the Floor scenery, non-draggable figures, and all chrome drop to
  ~0.35 opacity. No reflow, no movement — opacity and pointer-events only,
  so a cancelled drag restores by removing one class.
- **Targets:** valid project tiles stay at full opacity and gain a standing
  highlight ring. The hovered tile scales slightly (~1.03) and brightens.
- **Non-droppable targets are visibly dead:** for a `project:`-scoped
  figure, foreign tiles dim WITH the background and show `not-allowed` on
  hover. Dead targets look dead before the drop, not after.
- **The ghost:** the figure's face follows the pointer at reduced opacity;
  the original stays in place (the drag copies a ref, §3.3).
- **Cancel:** Esc, or release outside any tile. Restores everything, writes
  nothing, opens nothing.

## 8. Keyboard and touch — hard requirement

**Recommendation: implement the drag on Pointer Events, not HTML5
drag-and-drop.** The existing precedents (`attDrop` render-core.js:533,
`createDrop` :900) are OS-file drops, where HTML5 DnD is mandatory; for an
intra-app gesture it is the wrong tool — it does not fire on touch at all,
which would fail the phone requirement structurally. One
pointerdown/move/up implementation covers mouse and touch.

- **Touch:** long-press (~400ms, with haptic tick where available) on a
  figure enters hire mode — same fade, same targets. Then either drag the
  ghost, or simply **tap a highlighted tile** (tap-to-place — drops on a
  phone grid are fat-fingered; a tap on an already-highlighted target is
  not). Tap anywhere dim = cancel.
- **Keyboard / no-drag path:** the figure's existing menu gains **"Hire to
  project…"**, opening a project picker that calls the same endpoint.
  Every outcome in §6 must be reachable through this path; the drag is an
  accelerator, never the only door.

## 9. The one-gesture tension, addressed

The split-by-lifespan position holds *because* each rail row exists by a
human decision — N-inboxes is bounded by deliberate hires. Drag-to-hire
lowers hiring from a deliberate act to a flick, which erodes that bound.
Three mitigations, all specified:

1. **Symmetry:** un-hire is one context-menu action (§5). A cheap "yes"
   is safe when "no" is equally cheap and lossless.
2. **The threshold is surfaced, not crossed silently:** the position's own
   reopen trigger is ~4 hired characters per project. When a drop takes a
   project to 5+, the confirmation toast says so: "That's 5 agents on
   apex_trader — the rail is getting crowded." No block, just the signal
   that the standing position says to watch for.
3. **No accidental entry:** hire mode requires a real drag (8px pointer
   travel) or a long-press — a stray tap on a figure still just opens it.

## 10. Implementation notes (for the builder, not code)

- `static/js/floor.js` has **no drag handlers today** — this is greenfield.
  Pointer Events per §8; suppress the figure's click-open when a drag
  crossed the 8px slop.
- `static/js/*.js` are ES modules: no top-level globals. Anything
  render-core needs (e.g. marking tiles as targets) crosses via `window.`
  accessors, per the established pattern.
- Hire-mode dimming is one CSS class on a shared ancestor + descendant
  rules; tile highlight/hover are classes toggled from the pointer handlers.
- Backend: the two routes in §4 plus the `roster` field merge in the Bench
  derivation. No schema migration — absent field means empty list.
- Post-drop open: existing project-modal open path, then Channel mode with
  the agent's row selected (Phase 1 of the channel spec is the dependency;
  if Channel mode has not shipped, fall back to opening the modal with that
  agent's most recent conversation, and say so in the release note).

## 11. Out of scope

- Dragging agents BETWEEN projects (a move gesture). Un-hire + hire covers it.
- Dragging a Bench row (rail → tile). Floor figures only, v1.
- Filtering `_roster_block()` by hired membership (§3.3).
- Any change to Floor eligibility (`running`/`idle` gate stays — channel
  spec §12).
- Hiring throwaway subagents. They have no address (channel spec §7);
  their nested figures are not drag sources.

## 12. Open questions — genuinely open

- **Open: whether the drop should ever ask before hiring** (a confirm sheet
  on first-ever hire of an agent, teaching the gesture's weight). v1 ships
  without it; revisit if accidental hires actually occur.
- **Open: what the tile shows about existing membership during hire mode**
  (e.g. the agent's face already on the tile edge for case-2 drops).
  Cosmetic; defer to build.
