# The Floor — seeing who is doing what (MC-897)

Status: **design, not built.** 2026-08-23.
Backs `MC-897`; consumes `MC-895` (agent types, Phase 1 shipped) and `MC-887`
(coordination bus, Phases 0–1 shipped). Related: `MC-871` (visual workflow).

Reference: Alex Finn, *"OpenClaw is 100x better with this tool (Mission
Control)"* — https://www.youtube.com/watch?v=RhLpV6QDBFE. Watched in full
2026-08-23 (frames under `_scratch/vid/`, gitignored).

---

## 1. Ron's read is correct

> "this all still fits under existing UI, but there is no visual way to see the
> mode of operation between the personas… when working on a big project we need
> some way of visualization to see who's doing what."

Yes. Everything MC-895 shipped is a **pill in a chat header**: it tells you who
is in *this* conversation. There is no view anywhere that answers "who is
working, on what, right now" across the system. The closest thing is the
project grid, which shows one status word per project and nothing about who.

## 2. What the reference actually does

Two distinct views, not one:

**Team** — a layered org chart. Each agent is a role card: avatar, name, role
("Chief of Staff"), one-line description, colour-coded capability tags, a link
to a fuller role card. Cards are grouped into bands — `OPERATIONS (Mac Studio
2)` (grouped *by machine*), then a `↓ INPUT SIGNAL —— OUTPUT ACTION ↓` divider,
then a `META LAYER`. Structure, not state.

**Office** — the same roster rendered spatially and live. A top-down pixel room:
sprites at desks, a cluster around a meeting table, a speech bubble over the
one that is talking ("Build Council — S…"). Underneath the floor, a **roster
strip**: one small card per agent showing avatar, name, "Click for memory", and
a **status line** — `☕ Idle` in grey, or the current task in green
(`📋 Build Council - Socie…`). The working agent's card carries a green border.
To the right, a **Live Activity** feed. Along the top, demo controls: *All
Working / Gather / Run Meeting / Watercooler*.

The thing that makes it read instantly is not the pixel art. It is that
**every agent is on screen at once, with its state attached** — nine agents,
eight idle, one green. You get the answer without reading anything.

## 3. The trap — why a literal port would be a dead room

In the reference, agents are **always-on daemons** across three machines. They
exist continuously; idle is a real state they sit in, and "Gather" moves things
that were already there.

In Clayrune an agent is a **dispatched session**. It exists while it runs and
then it is gone. Our agent *types* (Marlow, Quill, Fenn) are definitions on
disk, not processes. Port the office literally and you get a room of nine
sprites that are permanently idle, because nothing is running unless someone
dispatched it — charming for a day, then furniture.

So the view has to be built on what we actually have live, not on an
always-on fiction. Which, it turns out, is quite a lot:

| Reference concept | What Clayrune already has |
|---|---|
| Agent, working / idle | `live_agent` per project: `{state, task, reason}` — right now 4 of 20 projects have one |
| Who *could* work | Characters (MC-895) — the type roster, with engine and self-chosen name |
| Live Activity feed | `/api/recent-runs` (cross-project, trigger-aware) |
| Who is aware of whom | Coordination roster — `/api/project/<id>/coord/roster` (MC-887, live) |
| Speech bubble | Activity states — `thinking` / `working` text, shipped behind `activity_states_enabled` |
| Machines / grouping bands | **Projects.** This is our natural grouping, and it is better than theirs |
| Scheduled to wake | The scheduler's next-run times |

Nothing here needs new state. It needs one view that puts existing state in one
place.

## 4. The design

**Rooms are projects. Figures are live sessions. The bench is the roster.**

```
┌──────────────────────────────────────────────┬──────────────────┐
│  ▸ mission_control          ▸ market_replay  │  Live Activity   │
│    ┌──────────┐               ┌──────────┐   │                  │
│    │ 🦉 Fenn  │◀── coord edge  │ 🔍 Quill │   │  09:41 Fenn      │
│    │ working  │               │ working  │   │   finished MC-42 │
│    └──────────┘               └──────────┘   │  09:38 Quill      │
│                                              │   started research│
│  ▸ find_ron_a_job           ▸ daytrading     │  …               │
│    ┌──────────┐               ┌──────────┐   │                  │
│    │ ✍ Marlow │               │  (idle)  │   │                  │
│    └──────────┘               └──────────┘   │                  │
├──────────────────────────────────────────────┴──────────────────┤
│  BENCH — types with nothing running                             │
│  ✍ Marlow (prd-writer)  🔍 Quill (market-researcher)  🦉 Fenn … │
└─────────────────────────────────────────────────────────────────┘
```

- **A figure per live session**, not per type — two chats on one project are
  two figures, which is the truth and is exactly what the pill cannot show.
- **The status line is the real activity string** we already stream
  (`thinking` / `working` / the task's first line), not a decorative label.
- **Click a figure → open that session.** This is Ron's "engaging with them
  through the visual view", and it is one call to the existing
  `openProjectModal` + session tab.
- **Click a bench card → dispatch that type**, with a project picker. The
  bench is where a type stops being a file and becomes a thing you can start.
- **Coordination edges** between figures on the same project, drawn from the
  live `coord/roster` — this is the only part that shows *relationship* rather
  than state, and it is the half MC-897 asks for that a status list cannot do.

### Two views, same as the reference

**Floor** (above) is state. **Team** is structure: the character roster as role
cards — name, role, engine pill, capability tags — grouped by scope
(global / per-project) rather than by machine. Team is cheap: it is the
Personas panel with a layout, and it is the natural home for "meet your team".

## 4a. Mockup

Drawn from the real state of this machine on 2026-08-23: 4 projects live
(2 working, 2 idle), 16 quiet, and **no project has a default agent type set**,
which is why nobody on the floor has a name yet. That is the honest starting
picture, and it makes the gap visible on its own.

### Frame 1 — today

```
┌ THE FLOOR ───────────────────────────── 4 live · 16 quiet · ⟳ 30s ┐
│                                                                   │
│ ┌ Mission Control ───────────┐ ┌ Day Trading Scanner ───────────┐ │
│ │ ● no type · opus (routed)  │ │ ● no type · sonnet (routed)    │ │
│ │   working                  │ │   working                      │ │
│ │   "editing agent_routes.py"│ │   "running pytest -q"          │ │
│ │                      12m   │ │                           4m   │ │
│ └────────────────────────────┘ └────────────────────────────────┘ │
│                                                                   │
│ ┌ MarketReplay ──────────────┐ ┌ Find Ron a Job ────────────────┐ │
│ │ ○ no type · sonnet         │ │ ○ no type · sonnet             │ │
│ │   idle — waiting on you    │ │   idle — waiting on you        │ │
│ │   "find the link to the…"  │ │   "I have an internal Googl…"  │ │
│ │                       2h   │ │                          20h   │ │
│ └────────────────────────────┘ └────────────────────────────────┘ │
│                                                                   │
│ ▸ 16 quiet projects — Clayrune Cloud, Engulfing Dashboard, …      │
│                                                                   │
├ BENCH — types with nothing running ───────────────────────────────┤
│  ✍ Marlow      prd-writer         fable · high                    │
│  🔍 Quill      market-researcher  opus  · max                     │
│  🦉 Fenn       code-reviewer      sonnet· high                    │
│  📈 (unnamed)  market-analyst     —                               │
│                              click a type → dispatch it somewhere │
└───────────────────────────────────────────────────────────────────┘
```

`●` working · `○` idle · the quoted line is the **live activity string** we
already stream, not a label. The age in the corner is what makes a forgotten
20-hour session obvious — today it is invisible unless you open that modal.

### Frame 2 — once types are assigned, and two agents share a project

```
┌ THE FLOOR ───────────────────────────── 5 live · 15 quiet · ⟳ 30s ┐
│                                                                   │
│ ┌ Mission Control ────────────────────────────────────────────┐   │
│ │  ┌──────────────────────────┐    ┌─────────────────────────┐│   │
│ │  │ ● 🦉 Fenn  code-reviewer │◀──▶│ ● ✍ Marlow  prd-writer  ││   │
│ │  │   sonnet · high          │coord│   fable · high          ││   │
│ │  │   "reviewing MC-142"     │    │   "drafting the spec"   ││   │
│ │  └──────────────────────────┘    └─────────────────────────┘│   │
│ │   two chats, one project — the header pill cannot show this │   │
│ └─────────────────────────────────────────────────────────────┘   │
│                                                                   │
│ ┌ MarketReplay ──────────────┐ ┌ Find Ron a Job ────────────────┐ │
│ │ ● 🔍 Quill  researcher     │ │ ○ ✍ Marlow  prd-writer         │ │
│ │   opus · max               │ │   fable · high                 │ │
│ │   "reading 3 competitor…"  │ │   idle — waiting on you        │ │
│ └────────────────────────────┘ └────────────────────────────────┘ │
│                                                                   │
│ ▸ 15 quiet                                                        │
├ BENCH ────────────────────────────────────────────────────────────┤
│  📈 (unnamed)  market-analyst     —                               │
└───────────────────────────────────────────────────────────────────┘
```

Marlow appears **twice** — once working on Mission Control, once idle on Find
Ron a Job. That is correct and it is the point: a figure is a *session*, not a
type. A type is a definition and can be in several places at once.

### What a click does

| target | action |
|---|---|
| a figure | open that project's modal on that session |
| a room header | open the project |
| a bench card | dispatch that type — project picker, then the composer |
| the coord line | (Phase 3) show what the two published to each other |
| `▸ N quiet` | expand the collapsed rooms |

### Team view — the other half

```
┌ TEAM ─────────────────────────────────────────────────────────────┐
│  GLOBAL — available on every project                              │
│  ┌───────────────────────┐ ┌───────────────────────┐              │
│  │ ✍ Marlow              │ │ 🔍 Quill              │              │
│  │   prd-writer          │ │   market-researcher   │              │
│  │   Turns a rough idea  │ │   Competitive and     │              │
│  │   into a PRD.         │ │   market research.    │              │
│  │   ⚙ fable · high      │ │   ⚙ opus · max        │              │
│  └───────────────────────┘ └───────────────────────┘              │
│                                                                   │
│  MISSION CONTROL — project-scoped                                 │
│  (none yet)                                                       │
└───────────────────────────────────────────────────────────────────┘
```

Grouped by **scope** rather than by machine (the reference groups by machine
because it runs on three; we do not). This is the Personas panel with a layout
and the engine surfaced — the cheapest phase in the plan, and the natural home
for "meet your team".

## 5. What earns its keep, and what is decoration

Worth saying plainly, because the reference is *very* charming and charm is not
the same as use:

- **Durable:** one screen answering "who is working, on what, across every
  project" — today that requires opening 20 project modals. That is a real gap
  and the Inbox only covers the blocked half.
- **Durable:** click-to-engage, and click-to-dispatch-a-type from the bench.
- **Durable:** coordination edges, because nothing else surfaces them at all.
- **Decoration:** pixel sprites, desks, plants, a watercooler. Fun in a demo,
  and the first thing that feels stale on day three. Recommend a clean card
  layout for v1 with the *spatial* grouping intact — rooms, bench, edges — and
  treat sprite art as a skin that can land later if the view proves itself.
- **Explicitly not ours:** "Run Meeting" / "Watercooler". Those are simulated
  social behaviour for a demo. We have a real coordination bus; staging small
  talk on top of it would be theatre over a working mechanism.

## 6. Build order

| Phase | Scope | Size |
|---|---|---|
| **1** | Floor view: rooms = projects, one card per live session with real status, click-to-open. Read-only, polls the endpoints that already exist | medium |
| **2** | Bench: types with nothing running; click → dispatch with a project picker | small |
| **3** | Coordination edges from `coord/roster` | small |
| **4** | Team view: role cards from the character roster, grouped by scope | small |
| **5** | Optional sprite skin, if the view has earned it | medium, deferred by default |

Phase 1 alone replaces "open 20 modals to find out what is running".

## 7. Open questions

1. **Where does it live** — a new sidebar entry ("Floor"), or a third view mode
   on the dashboard beside grid and list? Leaning sidebar: it is cross-project,
   and the dashboard's view modes are all *project-grid* layouts.
2. **Idle projects** — show every project as an empty room, or only projects
   with something live? Twenty mostly-empty rooms is the dead-room failure in
   §3 wearing a different hat. Leaning: live first, with a collapsed "quiet"
   section.
3. **Refresh** — poll `/api/projects` (already 30s) or open an SSE stream. The
   per-origin connection cap (`arch_sse_slot_management`) makes a *new*
   always-on stream a real cost; polling is probably right for v1.
