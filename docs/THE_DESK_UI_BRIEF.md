# The Desk — UI implementation brief

**For:** the local coding agent working in `mission-control`.
**Companion to:** `docs/THE_DESK_SPEC.md` (v0 + field-scan corrections). That spec owns the four functions, the five stores, staffing, voices, platforms and the authority guard. **This brief owns only the surfaces** and never contradicts it — where it adds a UX rule, the rule is a presentation layer over a spec-defined mechanism.
**Design reference:** `Simplified Dashboard.dc.html`, Turn 9 — frames **9a** Board · **9b** Queue (draft in review) · **9e** Ledger · **9c** phone Queue · **9d** rules.
**Mockup frames** (Claude Design, Turn 9) live at these paths on the operator's box — open them before building a surface, they are the target:
- 9a Board — `data/uploads/agent_aeba6dc0fc.png`
- 9b Queue, draft in review — `data/uploads/agent_92bb95528d.png`
- 9e Ledger — `data/uploads/agent_9d24a76314.png`
- 9c phone Queue + 9d rules — `data/uploads/agent_958f539f76.png`

**Also binds:** `CLAYRUNE_CONVERSATION_REDESIGN.md` (bubbles, typing dots, quick-reply chips, composer) and `WORKFLOW_BUILDER_UI_BRIEF.md` (the cadence chip opens the builder).

---

## 0. Ground rules

1. **The Desk is Posy's office.** Every surface speaks in her voice with her avatar — the Board opens with her note, teaching blocks are her messages, pushback is a reply to her, the Ledger opens with her read of the month. No anonymous system prose. Posy's avatar is the real generated one from the roster, same as the Floor.
2. **You only ever do three things:** Release · Edit in place · Push back (reply). Kill is a quiet fourth. Nothing else asks for the operator.
3. **Reuse, don't rebuild.** Stores and routes exist (`mc/desk.py`, `mc/blueprints/desk_routes.py`, `/api/desk/*`; queue CRUD on the project record; `mc/desk_brief.py` for Draft-as-voice; `desk.record_edit` learning loop). Surfaces live in `static/js/desk.js`. Chat pieces (bubble CSS, typing indicator, quick-reply chips, composer) come from the conversation redesign — import, don't fork.
4. **Theme tokens only** (`--bg`, `--surface2/3`, `--border2`, `--accent`, `--text`, `--text-dim`, `--green-text`, `--amber-text`, `--red-text`). Mockup hexes are illustrative; dark + cream must both work.
5. **No publishing without an explicit human Release**, and no UI path that lets the Desk grant itself one. The soft-lock in §3 makes releasing *harder*, never easier.
6. **Fluid.** Dropdowns over forms; one decision per screen on phone; nothing the user has to stop and think about.

---

## 1. Shell

Workspace peer to the Floor, from the sidebar. Header: `The Desk` · segmented tabs **Board · Queue (badge) · Calendar · Ledger** · right cluster:

- **Cadence chip** `● Runs weekly · Mon 07:00 · workflow ›` — reads the schedule linked to *The Desk — weekly* (`workflow_id`), green dot when enabled, struck when paused. Click opens the workflow builder modal on that workflow. Absent when no Desk workflow exists → shows `Set a cadence ›` (opens builder with the Desk template).
- `Voices ▾` (ron · X / clayrune · LinkedIn — opens the voice profile: banned words, rewrites log).
- `Accounts · N connected` (account inventory: platform, auth state, stale flag).

The old `What's worth saying?` / `Read the projects` buttons are gone: harvest runs on the cadence (or from the workflow's Run now); "worth saying" is what the Board *is*.

---

## 2. Board (9a)

Two-column grid, `minmax(0,1fr) 340px`; stacks on phone.

**Main column, in order:**

1. **Posy's note** — left chat bubble with avatar, timestamp `this week's note · Mon 07:04`. Body = the mentor's period focus + one curriculum observation (the spec's Mentor §4 "sets the period's focus and says why, before drafts appear"). Two buttons inside: `Review the N drafts` (→ Queue) and `Reply to Posy` (opens a thread with her; uses the chat composer). **A quiet week renders her saying so** ("Nothing worth saying this week — here's what I skipped and why"), never empty tiles.
2. **CAMPAIGN** — one card per live campaign: title, `RUNNING` badge, voice chips, *Thesis:* / *Why now:* line, a 5-segment progress bar (`out · in queue · planned`) with end date, `Open ›`. `＋ New campaign` on the section header; the form refuses a campaign without a thesis (spec).
3. **WORTH A STORY** — Posy's top 3–5 story candidates from the signal feed, ranked by `story_score`:
   - Left: score meter + mono score.
   - Middle: **human title** (Posy's one-line rewrite of the signal, not the ALL-CAPS backlog text) · project · source type · date · her one-line angle in italics.
   - Right: two buttons **`Draft as ron · 𝕏`** / **`as clayrune · in`** → `desk_brief` for that voice; the draft lands PENDING in the Queue and the button flips to `In queue ›`.
   - Low-score rows render dimmed with Posy's skip reason (*"changelog, not a story — skip"*) and a `Draft anyway ▾`.
   - `See all 120 ›` opens the raw feed (the existing list view) as a secondary screen.

**Side column:**

- **WAITING ON YOU** (accent tint): count, oldest age, split by voice, `Review them →`. This is the same item the dashboard inbox and the live heatmap show as amber; deep-link lands on the Queue with the oldest draft open.
- **LAST 30 DAYS**: `went out` · `killed by you` · **`% released edited`** (green ≥ 50%, red below, with the line *"Edit rate is the detection defense. If it drops under 50% Posy will say so."*) · X API spend. No reach numbers here — those live on the Ledger.
- **WHAT YOU KEEP CHANGING** (amber tint): the running curriculum — 2–3 numbered lessons derived from `record_edit` diffs, plus at most one open disagreement (*"Posy disagrees — read why"* → opens the recorded disagreement).

---

## 3. Queue (9b)

Layout `300px list | review pane`; the review pane is itself `minmax(0,1fr) 300px`.

**Draft list (left):** `N DRAFTS · OLDEST FIRST`. Each row: voice pill (`ron · 𝕏` black / `clayrune · in` accent), project, age, 2-line clamp of the body, **red dot if not yet edited**. Filters in the header: `All voices ▾` · `All projects ▾`. The per-project Social tab is this list pre-filtered to one project.

**Review pane — left:**

1. Meta line: voice pill · `from signal <title> ›` (deep-link to the originating signal/commit; spec Teacher §3 "every draft links to the signal") · right: `● not yet edited` (red) or `✓ edited · 3 lines` (green).
2. **Post preview, editable in place.** Rendered in platform shape: voice avatar + handle + *"as it will appear"*, mono char counter (`262 / 280` for X; LinkedIn 3,000). The body is a `contenteditable` region — click to edit, no modal, no Save button; autosave on blur → `update_social_queue_item` → `desk.record_edit` (the learning loop; do not drop it). If the draft names a needed asset, render a dashed **Needs asset** slot with `Attach ›`. Footer: *"Click to edit in place. Every edit teaches the ron voice."* · `Insert ▾` (link with tracking tag, mention, the campaign's canonical URL).
3. **Thread with Posy** — the draft's conversation, chat-bubble style from the conversation redesign:
   - Her **teaching block** is the first left bubble (`Why this, why X: …`).
   - **Push back = your right accent bubble.** The composer at the bottom is the standard chat composer (`＋ · Push back to Posy… · 🎤 · ↑`). Sending posts a pushback note and dispatches Posy to revise; while she works, the **typing indicator** shows and *"Posy is revising the draft…"*; her revision streams into the preview (highlight changed lines briefly) and her explanation lands as the next left bubble.
   - Quick-reply chips above the composer, learned from the operator's pushback history (`Shorter` · `Less technical` · `Add the cost` · `Try LinkedIn voice`). Same component as the conversation view's quick-reply chips.

**Review pane — right (decision rail):**

- **Release** — one green button, labelled with platform and cost: `Release to 𝕏 · $0.015` (`$0.20` if the body contains a URL; LinkedIn `free`). **Soft-locked (dimmed, non-clickable) until the body has been edited at least once in this queue item's history.** Under it, small red text: *"Release unlocks after your first edit — or release unedited anyway"* where the last phrase is an underlined link that confirms once and records `released_unedited: true` on the receipt. Release calls the publishing office; failure is loud and terminal (red card in the rail naming the platform and error, draft stays PENDING). Success shows the receipt (permalink, timestamp) and moves the draft to the Ledger.
- `Schedule ▾ · Tue 09:00` — release at a chosen slot (creates the linked schedule record; shows on Calendar).
- `Kill this draft` — red outline, confirms once, records the kill (feeds `killed by you` and the mentor's pushback logic).
- **CHECKS** card, computed before release, all informational except the first: `✓ Not said before (ledger)` via `similar_published()` — a hit renders `!` with the earlier post linked; `✓ Claim traces to <signal>`; `✓ No banned words for <voice>` — a hit lists them; `! Asset missing — releases without media`. These replace the red "Missing the line…" error strip.
- **SAME STORY, OTHER VOICE** (amber): whether the other voice has a draft from the same signal; `Ask Posy ›` briefs it. Never cross-posts (spec: written twice, never copied).

---

## 4. Ledger (9e)

Header filters: `Last 30 days ▾` · `All voices ▾` · `Sort: brought people ▾`.

**Main column:**

1. **Posy's read of the period** — left bubble: what did the work, what died, the lesson she is applying; `Disagree ›` opens a thread and records the disagreement.
2. **WENT OUT** table, one card-row per receipt: voice pill · date · `edited N lines` (or red `released unedited`) · asset flag · body (1-line clamp) · **SEEN** · **REPLIES** · **TO REPO** (mono number + small bar, scaled to the period max) · **VERDICT** pill: `worked` (green) · `announcement` (amber) · `suppressed?` (red — acceptance with reach far below the voice's median; the spec's silent-failure tell) · `quiet`. `+ N more · show · export CSV`.
   - *Seen* and *replies* come from the browser-pane reads (spec §1; still to build — render `—` with a tooltip until it lands).
   - *To repo* = hits on tagged links (`?ref=desk-<post_id>`) on the Clayrune site and GitHub; `Insert ▾` in the Queue always inserts the tagged form.

**Side column:**

- **WHERE PEOPLE CAME FROM**: total tagged visits · per platform/voice bars · `★ N new stars in the 48h after a post`.
- **PATTERNS · N POSTS, SO HOLD LOOSELY**: ≤5 lines, ↑/↓, plain language (*"A number in the first line: 3.4× the visits"*, *"'Introducing…' openers"*). Computed only over released posts; hidden under 4 posts. Button **`Feed these into both voices ›`** → appends the lesson to both voice profiles' rewrite guidance (the reason to measure at all).
- **SPEND**: X API this month · LinkedIn free · reads via pane $0.

No charts beyond the inline bars. This is the spec's "ledger records outcomes to inform writing" — a ledger with a verdict, not a dashboard.

---

## 5. Calendar

Unchanged from `schedule-calendar.js`: Desk releases (scheduled and sent) appear as chips with the voice pill; the cadence occurrence carries the workflow badge. Clicking a sent chip opens its Ledger row; a scheduled chip opens the draft in the Queue.

---

## 6. Phone (9c)

- Header `‹ Desk · Draft 1 of 4 · voice · project · ⋮`. **One draft per screen**; horizontal swipe moves through the stack (oldest first).
- Post preview card (tap to edit in place, same contenteditable), Posy's teaching bubble (clamped, `more`), quick-reply chips, one meta line (`from commit … · ✓ not said before · ! no asset`).
- Fixed bottom: the chat composer (`＋ · Push back to Posy… · 🎤`), then two buttons: `Kill` (red outline, 1 part) and `Release · edit first` (green, 2 parts, dimmed until edited → label becomes `Release to 𝕏 · $0.015`).
- All targets ≥ 44px. Board and Ledger stack their columns; the WENT OUT table collapses to cards with the four numbers in a row.

---

## 7. Cross-surface hooks

- **Dashboard inbox / live heatmap:** pending drafts are amber "Waiting on you" items labelled `The Desk · N drafts`; tap → phone 9c / desktop 9b with the oldest draft open.
- **Workflow builder:** the cadence chip opens *The Desk — weekly*. The Desk's `Run now` is that workflow's Run now. Pausing the schedule strikes the chip.
- **Conversation view:** `Reply to Posy`, `Disagree ›` and the Queue thread are ordinary Posy conversations tagged `desk:<draft_id|note_id>`, visible in her project chat list too.

---

## 8. Acceptance — one week, end to end

1. Monday 07:00 the workflow fires: harvest → Posy triage → drafts. Board shows Posy's note, 4 in **Waiting on you**, edit rate from last month, three story cards, one dimmed with her skip reason. Dashboard inbox shows `The Desk · 4 drafts`.
2. Open Queue. Oldest draft is selected; red `not yet edited`; Release dimmed with the unlock line.
3. Click the body, cut two sentences. On blur: autosaved, `✓ edited · 2 lines`, Release turns solid `Release to 𝕏 · $0.015`, Board's edit rate updates, voice profile records the diff.
4. Type "Add the cost line" in the composer → right bubble; typing dots; Posy's revision streams into the preview; her explanation lands as a left bubble.
5. Release → receipt (permalink, time) → draft leaves the Queue; Calendar shows the sent chip; Ledger row appears with `—` for seen/replies until the pane reads them and `0` to-repo.
6. Next week the Ledger row shows `41` to-repo, verdict `worked`; Patterns lists "number in the first line ↑"; **Feed these into both voices** updates both profiles; Posy's next Board note cites it.
7. Release a draft **unedited** via the small link → receipt flags it; Board edit-rate drops; if under 50%, Posy's note says so.

If any step needed a modal, a form, or a choice the UI could have made, fix the UI.

---

## 9. Build order

1. Shell + tabs + cadence chip (reads linked schedule) + Voices/Accounts entry points.
2. Board: Posy note bubble, campaign cards, story cards with Draft-as-voice, side rail (waiting / 30-day / curriculum).
3. Queue: list + in-place editable preview + decision rail with soft-locked priced Release + Checks; wire `record_edit`.
4. Queue thread: teaching bubble, pushback composer, Posy revision stream with typing indicator, quick-reply chips.
5. Publishing office wiring on Release (spec §1) with loud failure + receipt; Kill; Schedule.
6. Ledger: receipts table with verdicts, tagged-link counting, side rail, Feed-into-voices; pane-read columns as `—` until reads exist.
7. Phone: card stack, bottom composer + two-button bar, stacked Board/Ledger.
8. Cross-surface: inbox/heatmap items, calendar chips, `desk:` conversation tags.
