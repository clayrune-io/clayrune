# Workflow Builder — UI implementation brief

**For:** the local coding agent working in `mission-control`.
**Companion to:** `docs/WORKFLOW_BUILDER_SPEC.md` (Q1–Q7, Revisions 2–3). That spec owns the runtime, store, validation and allow-list. **This brief owns only the authoring UI** and never contradicts the spec — where it adds a UX rule, the rule is a presentation layer over a spec-defined mechanism.
**Design reference:** `Simplified Dashboard.dc.html`, Turn 8 — frames **8a** (desktop builder), **8b** (phone), **8c** (gesture rules).

---

## 0. Ground rules

1. **Runtime is settled; don't re-litigate it.** Store `format: 2` (nodes + edges), conditional edges with `when`, reserved `otherwise` / `on_failure`, no Paths node, serial runner, dominator rule for slots, three-layer cycle detection, six-verb action allow-list. Read R2/R3 before writing a line of UI.
2. **Real files.** Builder module: `static/js/workflow-builder.js` (ES module; cross-module entry points via `window.` accessors, same interop block as `static/js/scheduler.js`). Styles: `static/css/app.css`, theme tokens only (`--bg`, `--surface2/3`, `--border2`, `--accent`, `--text`, `--text-dim`, `--amber-text`, `--red-text`, `--green-text`). The mockup's cream hexes are illustrative — the app must work in dark and cream.
3. **Reuse the Phase-2 card editors verbatim** (project select, persona picker via `reloadSchedCharacters` caching, prompt textarea, option-label list). R2-D7 says only layout + connection model change. Same here.
4. **No graph library** (R2-D7). Absolutely-positioned DOM cards + one SVG overlay for edges + CSS transform for pan/zoom. Pointer events throughout; `touch-action:none` only via a class applied during an active drag (the `floor.js` drag-to-hire fix).
5. **The one UX principle:** if the user has to stop and think what the next step is, the UI is wrong. Every decision below exists to remove a decision from the user.

---

## 1. Where it lives

- **Door:** the project **Workflows** tab (Q6). Top: this project's Clayrune workflows (state · last run · next fire · "Runs" expander), `+ New Workflow`, per-row `Edit`. Bottom, only when present: the existing Claude Code fan-out viewer under a "Claude Code fan-outs (live)" divider.
- **Room:** the builder opens as its **own modal**, like `openScheduler` (`scheduler.js:10`). Workflows are global; the tab is not the owner.
- **Modal chrome (8a top bar):** `‹ Workflows` · editable name (dashed underline, click to rename) · `saved 12s ago` (autosave stamp, mono) · right cluster: `🕘 Runs N` · `▷ Run now` · `Save`.

---

## 2. The palette is the Bench — the core idea

**Drag people, not primitives.** The left palette (200px, docked; a bottom sheet under the mobile breakpoint) lists the user's hired agents from the Bench — avatar, name, role — plus exactly two tools.

```
PEOPLE · DRAG ONTO CANVAS
  🔍 Search bench…
  [avatar] Posy      social-media-strategist   ⠿
  [avatar] Fenn      code-reviewer             ⠿
  …
  + N more · Hire someone new
─────────────────────────────
TOOLS
  ✋ Approval gate   a human decides
  ⚙ Action ▾        6 verbs · no agent
```

- **Dropping a person creates an agent step with that persona already set.** Project defaults to the persona's home room (the "already in Mission Control" / "ONLY IN …" pin on their Bench card); if none, the current project. Prompt textarea takes focus. There is no "Agent step → pick persona" intermediate.
- **Approval gate** → gate node with two default options `approve` / `push back` (editable labels).
- **Action** → action node whose verb is a **dropdown** of the R3-1 allow-list (`backlog_create`, `backlog_patch`, `desk_harvest`, `journal_append`, `notify_operator`, `restore_point_create`), human-labelled ("Create a backlog item", "Run a Desk harvest", "Notify me", …). Gated entries (future `desk_publish`) show a lock glyph and the dominator requirement as helper text.
- Palette data source: the same roster the Floor/Bench reads. Avatars are the real generated avatar images; the mockup's initials circles are placeholders.
- **Trigger is not in the palette.** Exactly one trigger card exists per workflow, created with the workflow, never deleted.

---

## 3. Cards (nodes)

All cards: white surface (`--surface3`), 12px radius, 1px `--border2`, soft shadow; selected = 2px `--accent` border + accent glow. Header row: type glyph + small-caps type label, `⋯` menu at right (Rename · Duplicate · Delete). Input port: hollow accent dot on the **left edge**, vertically at the header row. All output ports on the **right edge** (see §4).

**Trigger** — `⚡ TRIGGER`. Two dropdowns: type (`Manual` / `On a schedule`), and when scheduled, the schedule (existing schedules + `New schedule…`, which opens the existing schedule form with `workflow_id` prefilled, Q4). Footer link `📅 shows on Calendar`. One output port.

**Agent step** — avatar + `Name · Step title` (title editable inline, defaults to persona role). Dropdowns: `📁 Project`. Prompt textarea with **slot chips**: slots render as mono chips (`prev.output`, `triage.result.summary`), never raw braces. Below it an **`Insert ▾`** dropdown listing only slots that are valid here per R2-D5 (dominating ancestors; `prev.output` only when exactly one parent), plus `trigger.fired_at`, `run.id`. **OUTCOMES** section: chips the user types (`＋ outcome`), each chip grows a port; then fixed rows *otherwise* (grey hollow port) and *on failure* (red hollow port). Outcome chips are the exact `when` labels; the validator refuses `otherwise` / `on_failure` as typed labels.

**Approval gate** — amber surface. `✋ APPROVAL · YOU`, helper "Asks in chat, emails if you're away." Options list = ports (`＋ option`). No otherwise/on-failure rows — a gate cannot fail or produce a non-answer.

**Action** — `⚙ ACTION`, verb dropdown, verb-specific fields (e.g. project + text for backlog_create; nothing for desk_harvest), one-line helper, one success output port + one red *on failure* port.

---

## 4. Ports, edges, branching

- Ports: 12px dots on the card edge; ≥40px invisible hit target. Filled accent = connected output; hollow accent = unconnected input; hollow grey = *otherwise*; hollow red = *on failure*.
- **Unconnected output port = a visible run-end.** Render a short stub line and a small square, labelled *ends run*. Never an error, never hidden (R2-D6).
- Edges: cubic paths in one absolutely-positioned SVG sized to the canvas (not stretched). Accent stroke for taken/unconditional; red dashed for `on_failure`. Hover an edge → small `×` at its midpoint to delete.
- **Every output port has a `＋` on hover/tap.** It opens the **"After *label*, run…"** popover: `⚙ Action ▾` · `✋ Approval gate` · **PEOPLE** (avatar row, searchable). Picking auto-places the new card to the right (or below on phone) and wires the edge. This is the 80% path; drawing arrows is for rewiring.
- **Drop a palette item onto an existing card** → same as its `＋`: place after, wire.
- **Drag port → port to connect/rewire.** Valid targets glow while dragging; invalid ones (self, would-create-cycle, gated-action-without-gate) dim.
- **Refusals (R2-D3, R2-D5, R3-2) surface as a snap-back + dark toast**, naming the cards: "Draft → Triage would create a loop — connection snapped back." / "Draft uses `triage.output`; removing this edge makes Triage skippable." A doomed graph is never drawn.

---

## 5. Canvas

Dotted-grid background; pan by dragging empty space (or two-finger); pinch/ctrl-wheel zoom; bottom-right `− 100% ＋ | ⤢ fit`. Persist `x,y` per node (R2-D1) and viewport per workflow. Bottom-left hint text on an empty canvas: *drop anywhere · drag a port to connect · ＋ on a port adds & wires the next step*. Empty workflow = trigger card only, with its output `＋` pulsing once.

**Layout defaults:** desktop places new cards to the right of their parent, stacking branches downward; phone places below. Auto-tidy is deferred (spec, Open).

---

## 6. Phone (8b)

- Palette becomes a **bottom sheet** ("Add to canvas · tap, or drag up"): avatar row (44px avatars) + the two tool tiles. **Tap** on a person adds them after the selected card and wires — dragging is optional.
- Flow reads top-to-bottom; output ports move to the card's **bottom edge**, inputs to the top edge.
- Tapping a card opens its editor as a sheet with the same dropdowns; the card itself shows name, avatar, outcome chips only.
- Header: `‹` · name + schedule line · `Save`. Zoom controls stacked top-right. All targets ≥44px.

---

## 7. Save / Run / feedback

- **Save always succeeds for structurally valid graphs** (no cycles, no slot violations, gated actions dominated — the three things the validator refuses). An *incomplete* graph (trigger with nothing attached, gate with no options, empty prompt) saves as a **draft** with a `Draft` badge in the tab list and cannot be enabled on a schedule.
- **Run now** runs the full check and, on failure, selects and scrolls to the offending card with a red outline + inline message. Never a modal alert.
- Autosave on every structural change (debounced); the `saved Ns ago` stamp is the only indicator.
- **Runs**: opens the run history for this workflow (existing surface); the run view greys skipped nodes and labels the decision that skipped them (R2-D2 / build step 5).

---

## 8. Acceptance — the Desk, authored with no arrows drawn

1. New Workflow → trigger card. Trigger dropdown → *On a schedule* → *Weekly · Mon 07:00*.
2. Trigger `＋` → Action ▾ → *Run a Desk harvest*. Placed + wired.
3. Harvest `＋` → People → Posy. Card appears with Posy set, project = Clayrune website. Type prompt; `Insert ▾` offers `prev.output`. Type outcomes *worth drafting*, *nothing*. Both grow ports; *otherwise* and *on failure* already present.
4. *worth drafting* `＋` → Posy → "Draft per voice". *nothing* left unconnected → shows *ends run*.
5. Draft `＋` → Approval gate → options *release* / *push back*.
6. Triage *on failure* `＋` → Action ▾ → *Notify me*.
7. Attempt to drag Draft's output onto Triage's input → snap-back + loop toast.
8. Save → not a draft. Calendar shows the chip. Run now → runs.

Zero arrows were hand-drawn. If any step above required a choice the UI could have made, fix the UI.

---

## 9. Build order (UI only; runner order is the spec's)

1. Modal shell + trigger card + palette from Bench roster.
2. Cards with reused editors; edge-anchored ports; SVG edges; pan/zoom.
3. `＋` popover + drop-onto-card auto-place/wire.
4. Port-drag connect with live validity + refusal toasts (calls the same validator as save).
5. Outcome chips → ports; slot chips + `Insert ▾` filtered by dominators.
6. Draft state, Run-now inline errors, autosave stamp.
7. Phone: bottom sheet, vertical ports, tap-to-add, editor sheets.
