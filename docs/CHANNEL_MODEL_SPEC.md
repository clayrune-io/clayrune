# Channel Model — Conversation Rail Redesign Spec

Status: **DRAFT v1 (decisions settled by Ron in conversation, 2026-09-02)** ·
Author: spec session 2026-09-02. Records decisions already made; nothing in
§§1–9 is open for re-litigation — genuinely unresolved items live in §13 only.
Visual reference: `_scratch/channel-mockup/channel-unified.png` (+
`channel-unified-standalone.html`); see §9.2 for three figure corrections the
mockup needs before it is treated as pixel truth.

> **Supersession note.** On 2026-08-26 Ron declined showing subagent output in
> the parent chat, on the grounds that multi-speaker chat needed a product
> decision first. This spec IS that product decision; that position is
> superseded by §7.

---

## 0. The problem

The conversation rail lists CHATS — one row per session. Every dispatched
persona session orphans a row, so the list grows without bound and one agent's
history is scattered across rows that all look alike. There is no place in the
product where "Fenn, and everything Fenn has done here" exists as a thing you
can look at or type to.

Two concrete defects, found 2026-09-02:

1. **Persona sessions never render at all.** `_userInitiatedConvos`
   (`static/js/conversation.js:1604`) unconditionally drops any conversation
   whose `source` is in `{agent, api, cron}`. Persona sessions dispatched
   without an Origin header are auto-tagged `source: agent` by the heuristic at
   `mc/blueprints/agent_routes.py:5570-5573`, so they fall into that set. The
   row renderer (`conversation.js:1809-1815`) already reads `c.character` and
   would draw the face correctly — the rows just never reach it. The filter,
   not the renderer, is the defect.
2. **Figures vanish from the Floor when their session closes.** The Floor
   (`mc/blueprints/floor_routes.py:322-326`) admits only `running`/`idle`
   sessions. This is **by design** — the Floor shows who is working right now —
   and this spec does not change it. It is listed here because it is the other
   half of why an agent has no persistent presence today: the rail forgets the
   sessions, the Floor forgets the figure. This spec fixes the first; the
   second is out of scope (§12).

---

## 1. The model: a project is a channel

**A project is a CHANNEL.** Every agent that has ever participated in a
project belongs to that project's channel, permanently. Participation is not a
session property; it is a membership record that survives the session.

- **Participation is permanent.** An agent that worked in a project once is on
  that channel's roster forever (on the Bench, §3, once idle).
- **Conversations are kept forever.** Retention is already free: the
  conversation list is derived from Claude Code transcripts on disk, and the
  current 20-row cap is only a frontend display limit — the `?limit=20`
  query parameter in the conversations fetch at `static/js/agent-log.js:146`.
  No new storage, no new retention machinery. The channel view must not
  inherit that display cap.

---

## 2. Middle rail: a third mode

The middle rail has two modes today, Chats and Topics (`_mode` in
`conversation.js`, `.rail-mode-btn` styling). The redesign adds a third:
**Channel**.

**Chats mode is NOT legacy.** Every pre-existing conversation is unattributed
— it has no `character` — and Chats remains the permanent home for un-agented
history. Channel does not replace Chats; it sits beside it. (This is also the
whole migration story: §10.)

---

## 3. Channel mode shows people, not chats

The Channel rail is a roster. Two sections:

- **"In the room"** — agents with a live run, showing the existing shimmer
  activity word (the "Thinking"/"Working" treatment already shipped).
- **"Bench"** — everyone who has ever participated in this project, idle.

**Row anatomy** (per the mockup): fig avatar · agent name · role in secondary
text · relative timestamp right-aligned. The timestamp slot is **replaced** by
the existing "Waiting for you" badge when applicable — same slot, the app
cannot show both.

---

## 4. The stream: one channel, roster rows are filters

**Roster rows are FILTERS, not separate inboxes.** The right pane renders ONE
project channel: a single chronological stream with multiple speakers.

- Each message is attributed with the speaker's face, name, and role.
- Consecutive same-speaker messages collapse their header (face/name shown
  once for the run of messages).
- **Session boundaries render as a thin date divider inside one continuous
  scrollback** — never as separate rows, never as separate panes. A session
  is an implementation detail of the transcript store; the channel is the
  product object.
- Clicking a roster row filters the stream to that agent's messages. Clicking
  away (deselecting) restores the full room.

---

## 5. Addressing: sticky-then-named, targeting a run

**Addressing is STICKY-THEN-NAMED.** The composer always has a current sticky
target; your message goes there.

- **Naming an agent re-points sticky** to that agent.
- **Replying to a specific message re-points sticky at that message's run.**

**Sticky targets a RUN, not an agent.** The default is one live run per agent.
A second message sent to a busy agent **stacks into her existing run** — she
receives it and sequences it herself, the way a person handling two requests
would. This is the normal path and requires **no new UI**: no queue widget, no
"agent is busy" interstitial, no second session.

---

## 6. Parallelism is an explicit, visible fork

When the user genuinely wants one agent working two things at once, they
**fork** her: a second figure appears on the roster ("Fenn · 2") with its own
state, its own run, and its own attribution on messages in the stream.

**Nothing forks implicitly.** The system will not spawn a parallel run of an
agent as a side effect of message timing, dispatch source, or load. If two
figures wear the same face, the user put them there.

(How the fork is initiated in the UI is open — §13.)

---

## 7. Stateless subagents nest

The governing principle, stated once and load-bearing:

> **A roster row is an address — if you can type to it and something resumes,
> it is a rail; if you cannot, it is history and belongs to its spawner.**

A throwaway subagent — no persistent identity, cannot be addressed after it
exits — therefore does NOT get a roster row. Its output renders as a
**collapsed card inside the message of whoever spawned it**: wearing its own
small face, summarising tool calls / duration / outcome, expandable in place.

This supersedes the 2026-08-26 decision to keep subagent output out of the
parent chat (see the supersession note at the top). The objection then was
that multi-speaker chat lacked a product decision; §§4–6 are that decision.

---

## 8. Vanilla installs seed a default agent: Claydo

Verified 2026-09-02: `default_character` is null on all twelve projects on the
reference install. A fresh install therefore renders an empty roster and the
channel model collapses on first run — a channel with nobody in it is a worse
first-run screen than the chat list it replaces.

**Decision:** vanilla installs seed one default agent, **Claydo**. Claydo
already exists in code as the guide/walkthrough host (`static/js/claydo.js`,
`mc/blueprints/guide_routes.py`, `static/js/walkthrough.js`); this merges the
guide role and the default-worker role into one character. First message on a
fresh install goes to Claydo; the roster is never empty.

**Recorded tradeoff (accepted by Ron):** once Claydo works, Claydo needs a
voice and opinions and stops being neutral brand furniture. The mascot becomes
a colleague, with everything that implies for tone consistency across the
guide, the walkthrough, and real work output.

---

## 9. Ground truth: characters and the visual reference

### 9.1 The character roster (verified 2026-09-02 via `GET /api/characters`)

Ten global characters exist, all named:

| Name | Role | Fig |
|---|---|---|
| Tobin | builder | fig:smith |
| Fenn | code-reviewer | fig:scholar |
| Dave | dave | fig:guard |
| Quill | market-researcher | fig:angler |
| Halloway | market-scout | fig:prospector |
| Marlow | prd-writer | fig:wizard |
| Wren | security-privacy-auditor | fig:chef |
| Bram | silent-failure-diagnostician | fig:gardener |
| Posy | social-media-strategist | fig:courier |
| Tilda | ui-fixer | fig:dancer |

### 9.2 The visual reference, with corrections

`_scratch/channel-mockup/channel-unified.png` (and its
`channel-unified-standalone.html`) is the **approved layout reference**: rail
sections, row anatomy, stream attribution, divider treatment, nested subagent
card.

It gets three figures WRONG, and must not be treated as figure truth:

- Quill is drawn as **navigator** → correct fig is **angler**.
- Wren is drawn as **warden** → correct fig is **chef**.
- The subagent is drawn as **apothecary** → subagents wear their own small
  face per §7, not a fixed apothecary fig.

Cite the mockup for layout; cite §9.1 for figures.

---

## 10. Migration: existing un-attributed conversations

There is no data migration. The story is structural:

- **Pre-existing conversations stay in Chats.** They carry no `character` and
  the system must not invent one — retroactive attribution would be a lie
  about who said what, and the renderer would draw a face that was never
  there. Chats mode is their permanent home (§2).
- **Attributed conversations appear in Channel from the moment the feature
  ships.** The channel is derived from the same transcript store; a
  conversation with a `character` is channel material, one without is Chats
  material. No backfill job, no cutover date, no dual-write.
- An agent's Bench membership (§3) is derived from the attributed
  conversations that exist — so the roster populates organically as agents
  work, and is empty-plus-Claydo (§8) on a fresh install.

---

## 11. Build order — smallest shippable first

Each phase ships independently and is useful without the phases after it.

- **Phase 0 — the unhide.** Stop dropping `source: agent` persona sessions in
  `_userInitiatedConvos` (`conversation.js:1604`) when they carry a
  `character`. One-line class of change; the row renderer already does the
  rest (§0.1). Independently useful — persona history becomes visible in
  Chats today — and it blocks nothing and is blocked by nothing.
- **Phase 1 — the roster.** Add the Channel rail mode: "In the room" / Bench
  sections, row anatomy per §3, membership derived per §10. Clicking a row
  opens that agent's history (filter semantics can be rough here).
- **Phase 2 — the unified stream.** The right pane becomes the single
  chronological channel: multi-speaker attribution, collapsed same-speaker
  headers, thin date dividers across session boundaries, roster-row filtering
  per §4. Lift the 20-row display cap for the channel view (§1).
- **Phase 3 — addressing.** Sticky-then-named composer targeting per §5,
  including stacking a second message into a busy agent's run.
- **Phase 4 — forks and nested subagents.** Explicit fork ("Fenn · 2") per
  §6 (UI entry point pending §13); collapsed subagent cards per §7.
- **Phase 5 — Claydo seeding.** Default-agent seeding on vanilla installs per
  §8. Sequenced last because it depends on the channel being a good first-run
  surface, but it has no code dependency on phases 3–4 and may land earlier
  if first-run polish demands it.

---

## 12. Out of scope — explicitly

- **Floor persistence.** The Floor keeps its `running`/`idle` gate
  (`floor_routes.py:322-326`). Figures leaving the Floor at session close is
  by design; the Bench (§3) is where persistent presence lives.
- **Numeric progress indicators on an agent.** No percentage, no progress
  bar, no ETA. That signal does not exist in the product and this spec
  deliberately refuses to fake one. The shimmer activity word is the only
  liveness signal.
- **Retroactive attribution of old conversations** (§10).
- **Any change to how sessions are stored or how transcripts are derived.**
  The channel is a view over the existing store.

---

## 13. Open questions

- **Open: how a fork is initiated in the UI.** §6 fixes the semantics
  (explicit, visible, second roster figure) but not the gesture — context
  menu on the roster row, a composer affordance, or something else.
- **Open: whether a filtered channel view still allows composing.** If yes,
  filtering doubles as addressing (clicking Fenn both filters and re-points
  sticky); if no, the filter is read-only history. Interacts with §5.
- **Open: what happens to the sticky target when the sticky agent's run
  ends.** Candidates: sticky falls back to the agent (next message starts or
  resumes a run), falls back to the room, or goes empty until re-pointed.
- **Open: whether Topics mode survives long-term** or folds into the channel
  as a lens over the same stream. No decision; Topics ships unchanged in
  every phase above.
