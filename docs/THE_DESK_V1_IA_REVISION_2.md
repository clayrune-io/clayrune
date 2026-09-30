# The Desk v1: IA revision 2 (campaign map: Brief, Goal, What, When, Where, Launch)

**Status:** DRAFT design revision, 2026-09-29. MC-977 / backlog 8f64d565. Docs only, no code changed. Becomes
binding when Ron answers §9 (three questions, each with a recommendation the tickets already assume). §9 answered
2026-09-29. **§10 (outcome learning loop, Ron's item 9) added 2026-09-29**: tickets R2-14 to R2-17 + R1-L, two open
questions in §10.8. **§11 (mockup deltas, approved 2026-09-29)** added the same day: the 24 approved frames in
`docs/desk_v1/mockups_r2/` are now the visual spec; §8 rows R2-2, R2-6 to R2-13 amended, R2-3b and R2-18 added (R2-18 reshaped 2026-09-30: Brief first, below); build
order from here in §11.4; two open questions in §11.5.
**Owner:** Merrin (plan). Builders per ticket, §8. Measured against master `5d6ec89` (IA1 to IA8 all merged:
`c229954`, `ed3410b`, `a5c7cd2`, `e528e19`, `dc5b674`, `8d75d8a`, `2bcaeb9`, `24818af`).
**Delta on:** `THE_DESK_V1_IA_REVISION.md` (2026-09-28, "IA 1"). Read that first. This doc only states what Ron's
2026-09-29 list changes; every IA 1 section not named in §1 below still holds.
**Unchanged authority:** `THE_DESK_SIMPLIFICATION_PLAN.md` §4 (approval bounds), the per-campaign release position
(2026-09-22), X + LinkedIn only, voice split Ron/X and Clayrune/LinkedIn, Ron's IA 1 answers (no per-account cap;
approval per campaign; auto-replies out of v1).

## Ron's list (2026-09-29, condensed from the journal `8f64d565-desk-v1-redesign.md`)

1. The entry point shows campaign status across ALL projects; picking a project is clear and easy.
2. A campaign starts from targets and a MEASURABLE goal; it may run months (long-term) or weeks (short-term). Track
   overall effectiveness against that goal.
3. Holistic flow: project, then its campaigns, then manage one campaign; create a new one from there.
4. In create, manage and review, always show which stage the campaign is at, and make moving between stages easy.
5. What / How / When / Where frame the CAMPAIGN (new and active). HOW = strategy. WHAT = the content (articles,
   videos, images; the pieces live here). WHEN = schedule + calendar. WHERE = platforms; drag and drop lives here.
6. Guided: a map at the top leads screen to screen. HOW is discussed with Posy or any agent the user picks (not every
   install has Posy); that agent then suggests What / When / Where. Content creation keeps its own menu and is also
   drivable from WHAT (drag an existing video in, or jump to the video page with an agent-drafted storyboard, edit,
   render, come back). Same for pictures.
7. Conversations, responses and comments get their OWN menu, not inside the campaign page. Its landing groups by
   project with counts (unread / total), a quick read of each project's social activity.
8. HOW carries an OPTIONAL budget: drawn from the project's promotion budget, or a campaign's own budget.
9. (added later the same day) A loopback that measures each campaign's effectiveness, to learn long term what worked
   and what did not. Answered by §10.

---

## 1. What changes in IA 1, section by section

| IA 1 section | Verdict | Replaced by |
|---|---|---|
| K1 to K4 (accounts are workspace assets, ceilings stack, approval per campaign, conversation has a project) | **Holds** | unchanged |
| §1 Hierarchy and navigation | **Rewritten** | §2 here: Home becomes a campaign status board, a project picker on every Desk screen, campaign page becomes the map |
| §2.1 Project presence | **Amended** | §5: `production` becomes a promotion `budget`; adds `desk_agent`; `measurement` gains a `manual` source |
| §2.2 Campaign fields | **Amended** | §4: `goal` becomes measurable with a horizon; adds `how {strategy, agent, budget}`, `term`, `map` |
| §2.3 Setup: 3 steps + step 0 | **Retired** (step 0 Presence kept) | §3: one flow, the map, in Draft state |
| §2.4 Piece: four facets | **Retired** | §4.3: the four facets move up to the campaign; a piece keeps a single drill-in page under WHAT |
| §3 Move table (40 rows) | **Holds**, with the column "To" re-read through §4's stop table (a "C" field now lives on a named stop) | §4.4 |
| §4 T1 to T3 survival | **Holds** for T1/T3; T2's tab strip is replaced by the map | §7 |
| §5 IA1 to IA8 | **Shipped; eight surfaces reworked** | §7 names each, §8 tickets |
| §6 Data deltas | **Amended** | §5 |
| §7 Engagement | **Rewritten landing**; lanes and filters hold | §6 |

## 2. Hierarchy and navigation (replaces IA 1 §1)

```
Desk header (every Desk screen):  Project: [All projects ▾]   💬 Engagement · n   ⚙
                                   └ picks a project → its project page; "All projects" → Home

Home = campaign status, all projects            (item 1)
 ├─ Needs you (all projects, project chip per row; unchanged from IA1)
 ├─ Status board: one group per project
 │    group header: project name · presence state · n campaigns · Open project ›
 │    one row per non-archived campaign: stage · title · goal progress + pace · next post · needs-you count
 └─ ＋ Promote something   (the old promote box; lands in a Draft campaign at the Goal stop)

Project page  ‹ Desk                             (item 3)
 ├─ Header: name · presence state · ⏸ Pause project · ⚙ Presence
 ├─ Campaigns: the same status rows as Home, this project only · ＋ New campaign
 ├─ Needs you (this project) · Next post · 💬 Engagement · n  (a count link out, no thread UI)
 └─ Archived campaigns toggle (IA6, kept)

Campaign page  ‹ <project>                       (items 4, 5, 6)
 ├─ Header: subject chip · title · lifecycle pill · ⏸/▶ · More ▾
 ├─ THE MAP:  ① Brief  ›  ② Goal  ›  ③ What  ›  ④ When  ›  ⑤ Where  ›  ⑥ Launch   (R2-18: Brief = the old How, route key `how`, moved first)
 └─ Stop body (in place, T2 persistent frame) + agent box (right column)

Piece page   ‹ <campaign> · What                 (piece drill-in, §4.3)
Studio       ‹ Desk, or ‹ <campaign> · What when opened from WHAT   (content creation menu, §4.2 ③)
Presence settings  ‹ <project>                   (IA3, amended §5)
Engagement   ‹ Desk                              (own menu, §6)
```

- **Project selection (item 1).** The `Project:` picker sits in the Desk header on every Desk screen, so switching
  projects is one click from anywhere, not a trip back to Home. It is navigation, not a filter: choosing a project
  opens its page; `All projects` opens Home. IA1's project cards retire (their facts move into the group header).
- **Home is status, not a switcher (item 1).** Rows are grouped by project and ordered: needs-you first, then Active
  by next post, then Draft, then Paused/Completed. This is not the flat grid Dave sent back on IA1: that grid sat
  under one hardcoded project; these groups are every project, each labelled.
- **Where drag and drop happens now.** Home stops being a drop target (its campaign cards are gone). The Channels
  shelf becomes WHERE's side tray, Material becomes WHAT's side tray (§4.2). Calendar drag to reschedule stays in WHEN.
- **Conversations leave the campaign page (item 7).** The campaign's Conversations tab is removed. The only trace is
  the header's global `💬 Engagement · n` link and the project page's count link, both of which navigate out.
- Back stays `‹ <parent name>`. New parents: `piece → campaign (stop what)`, `studio → home` or the caller passed in
  `returnTo`. Old deep links alias (§7, R2-3).

## 3. One flow, not two: setup becomes the map in Draft

**Decision: one flow.** IA4's three setup steps (1 Subject + goal, 2 Plan, 3 Review + start) are retired. Creating a
campaign and managing it use the same six stops on the same screens. Draft is simply the lifecycle state in which the
map guides.

**Why one flow:**
- Two flows put the same fields on two screens with two editors (cadence in setup step 2 AND in WHEN; accounts in step
  2 AND in WHERE). IA2 spent a ticket removing exactly that duplication (`rules.frequencyPerWeek` vs
  `plan.cadence.per_week`); a second flow rebuilds it.
- Ron's item 4 asks for easy movement between stages across create, manage AND review. That is only true if
  "fix the cadence" on a running campaign lands on the same screen it was set on.
- IA4's step 2 "Draft the plan" already produced angle, planned pieces, accounts, cadence and end in one agent task.
  Those outputs map one-to-one onto How, What, Where and When, so the task survives; only its output is spread over
  the stops it belongs to.

**How the map behaves (item 4, item 6):**

| | Draft | Proposed (agent suggestions waiting) | Active / Paused | Completed / Archived |
|---|---|---|---|---|
| Stop glyphs | `✓` done · `●` you are here · `○` not started · `⚠` needs you (each glyph + word, never colour alone) | same; stops holding unaccepted suggestions show `? 3 suggested` | same; ⑥ reads `Live since <date>` or `⚠ Awaiting approval` | read-only; ① shows final result |
| Movement | `Next: <stop> ›` / `‹ Back` at the foot of each stop; every stop is also clickable (guided, never locked) | same | free; no Next button | free, read-only |
| Lifecycle pill | `Draft` | `Proposed` | `Active` / `Paused` | `Completed` / `Archived` |
| Home / project row "stage" | `Draft · at How` | `Proposed · 3 suggestions` | `Active · on track` (pace, §4.1) | `Completed · 31 of 30` |

- **Launch is the only gated stop.** ⑥ Start is disabled until `validatePlan` passes; the stop lists what is missing
  and each item links to the stop that fixes it (IA2's `missing[].step` becomes `missing[].stop`).
- **Step 0 Presence stays** (IA4), once per project, before ① when the project has no presence. It gains one
  question: who plans and writes for this project (§5, `desk_agent`).
- **Leaving mid-way** keeps the campaign in Draft at its current stop; the row reads `Draft · at When` and
  `Continue ›` lands on that stop (IA4's `Setup 2 of 3` behaviour, kept, re-worded).
- **Editing a running campaign** never leaves Active. An edit that widens an approval bound (§4.1 ②, §5) flips ⑥ to
  `⚠ Awaiting approval` and stops publishing until Ron approves again (SIMPLIFICATION §4, unchanged). Narrowing keeps
  the approval.

## 4. The campaign stops

### 4.1 The six stops

| Stop | Holds | Required to launch | Absorbs (shipped UI) |
|---|---|---|---|
| **① Goal** (item 2) | subject (kind + label, IA4 step 1), title, brief; **measurable goal**: metric, target number, baseline, unit, horizon (`short` ≤ one term, `long` = several terms), deadline, measurement source; **effectiveness panel** once Active | subject, title, brief, metric, target number, source (§9 Q1) | IA4 step 1; the Results tab (`desk-v1-results.js`) |
| **① Brief** (route `how`; items 5, 6, 8) = frame + strategy | **project** (select, optional, pre-filled from a project page), **the agent to plan with** (`how.agent`, campaign-scoped per Ron 2026-09-30, reversing the same-day per-project-only ruling; picker = agents hired on the project's floor, default `presence.desk_agent`; `deskAgentRef` resolves `how.agent` first), angle (the through-line), strategy in words (who, what argument, why these channels, what never to claim; defaults from `presence.strategy`), **optional budget** (§5.2), `Suggest What / When / Where` action | angle | IA4 step 2's plan task; the rules popover's Paid group; `presence` strategy as the inherited default |
| **③ What** (items 5, 6) = content | the pieces: list (filters kept), `◇ Planned` suggestions, `＋ Post · ＋ Article · ＋ Video · ＋ Image`; Material side tray (drag an existing asset onto a piece or the list); piece drill-in (§4.3) | ≥1 piece (planned counts) | the Content tab list (`desk-v1-campaign.js` tab body), IA5 piece page, Add tray's Material drops |
| **④ When** (item 5) = schedule | cadence per week (≤ project ceiling, K2), min gap (inherited, read-only here), term start/end, post cap, calendar (month/week) + list toggle, `Unscheduled` tray to drag onto a day | cadence, end date and/or post cap | `desk-v1-calendar.js` (was the Content tab's calendar toggle); rules popover cadence/end |
| **⑤ Where** (item 5) = platforms | one column per project account (voice shown, from the presence binding); toggle a column on/off = the campaign's `accounts[]` subset; drag a piece from `Unplaced` or another column onto a column = create/move that destination version; Channels side tray; `Connect another ›` goes to Presence (widening) | ≥1 account on; every piece placed or explicitly `✋ You publish it` | rules popover Channels group; Add tray's channel drops; IA5 Where facet |
| **⑥ Launch** | bounds table (accounts, voices, cadence, end/cap, source scope, spend ceiling, budget, term), `Change for the project ›` links (IA4), Start; once Active: approval record, Pause/Resume, `Renew term` (long horizon) | `validatePlan` ok | IA4 step 3 / `deskV1OpenStartSheet` body; `_startCampaign`; `_openResumeSheet` |

**Effectiveness (item 2), shown on ① once Active and summarised in every status row:**
- `progress = current / target`; `pace = progress ÷ fraction of horizon elapsed` → `On track` (≥0.9), `Behind`
  (<0.9), `Ahead` (>1.2). Words plus glyph, never colour alone.
- `cost per outcome = spend to date ÷ current`, only when spend > 0.
- Per-term breakdown for `long` horizons (term 1: 12 of 30, term 2: ...).
- Untracked source (no measurement, §9 Q1): the panel shows `⚠ Not measured: add a source` and the row shows
  `Active · not measured`; it never shows a `0 of 30` that reads like failure.
- `goal.current` stays derived, never stored (IA 1 §3 row 5), except `manual` sources, which store dated entries.

### 4.2 Guided agent loop (item 6) and the content-creation hand-off

1. **① Goal** is written by the user (the promote box pre-fills subject + title as `? Assumed`, as today).
2. **② How**: the right-column agent box is scoped `About: <campaign> · How` and headed by the chosen agent's own
   name and avatar (never the literal "Posy"). The user talks strategy with it. `Suggest What / When / Where` runs one
   agent task (IA4's plan task lifecycle, UX_PASS §5, unchanged: Working, Ready, Failed + Retry, Needs answer).
3. **Ready** writes suggestions, never commitments: `◇ Planned` pieces into ③, a cadence + slot proposal into ④, an
   accounts subset + per-piece placement into ⑤. Each stop shows `? n suggested` with `Accept all` / per-item
   accept / edit / discard. The map moves the user on with `Next: What ›`.
4. **③ What → content creation.** On a video or image piece:
   - `Use an existing one`: drag from the Material tray (or pick from the project's Media gallery) onto the piece.
   - `Make one`: opens **Studio** with `returnTo = {route: campaign, stop: what, campaignId, pieceId}`. The agent
     pre-fills the storyboard (video) or the brief + shot list (image) from the piece's copy and the campaign's HOW.
     The user edits, sends to render, and `‹ Back to What` returns with the render attached to the piece as its asset
     (state `⟳ Rendering` until done; the piece is usable meanwhile).
5. **Studio is also its own menu** (Desk header `⚙` sibling `🎬 Studio`, and Home): the same video director and an
   image equivalent, standalone, saving into the project's Material. Today no such menu exists: the video director
   (`desk-v1-video.js`) is reachable only as a child of a campaign or piece (`shell.js` `video.parent = 'campaign'`).
   Ron's "keeps its own menu" therefore means **building** that entry point (R2-8), not keeping one.
6. An agent of any kind only ever suggests. Launch stays a human action (SIMPLIFICATION §4); choosing a different
   agent changes who drafts, never what is allowed.

### 4.3 Piece-level editing: where it lives now

IA5's four facets on the piece are retired; the frame moved up to the campaign. The piece keeps **one drill-in page**
under ③ What, `‹ <campaign> · What`, a single scroll with three sections:

| Section | Holds | Also editable from |
|---|---|---|
| **Copy** | copy per destination version, claims + sources, `? Assumed` notes; `Review` opens 12b as a child (`‹ <piece>`) | nowhere else |
| **Media** | attached assets, render status, `Make one in Studio ›` (with `returnTo` this piece) | ③ list (drag), Studio |
| **Versions** | one row per destination: account + voice + format + **publish time** + state | publish time: ④ calendar drag; destination: ⑤ column drag |

So per-piece publish time lives on the piece's Versions rows AND as the piece's chip on ④'s calendar (one value, two
views); per-version destination lives on the Versions rows AND as the card's column in ⑤. No field has two stores.

### 4.4 IA 1 move-table rows, re-read by stop

Campaign rows (IA 1 §3, "C") now sit on: ① `plan.title`, `subject`, `brief`, `goal` (#3, #13, #17, #40);
② `angle`, `paid`, `customChips` campaign scope (#29, #25, #11); ④ `cadence.per_week`, `end` (#21, #23);
⑤ `accounts[]` subset (#19); ⑥ `approval`, spend ceiling, stop conditions, `_startCampaign` (#12, #35, #36).
`extra_sources[]` (#15) sits on ② (it is a strategy choice). `camp.setup {step, done[]}` (#37) becomes
`camp.map {stop, done[]}`. Piece rows (#30, #31, #34) sit in ③.

## 5. Data deltas (amends IA 1 §6; R0 fixtures first, R1-P backend carries the same shapes)

### 5.1 Campaign

| Field | Shape | Note |
|---|---|---|
| `goal` | `{metric, target:number, baseline?:number, unit?, horizon:'short'\|'long', deadline, source: <measurement id>\|'manual', entries?:[{at, value}]}` | replaces `{outcome, target?, deadline?, tracked}`; `outcome` text becomes `metric`; `tracked` derived from `source` |
| `term` | `{index, starts, ends, post_cap}` | the approval's finite window; a `short` goal has one term; `long` renews terms until `goal.deadline` (§9 Q2) |
| `how` | `{strategy, angle, agent?: <character id>, budget?: {source:'project'\|'own', amount, period:'term'}}` | `plan.angle` moves here; `agent` is **retired (Ron 2026-09-30: agents are assigned per project only; a project may have none)**: the agent for a campaign is always its project's `presence.desk_agent`, and a project-less draft has none |
| `map` | `{stop:'goal'\|'how'\|'what'\|'when'\|'where'\|'launch', done:[...]}` | replaces `setup {step, done}` |
| `approval` | unchanged shape; **bounds now include** `how.budget` (source + amount) and `term` | a budget raise or source switch that increases available money is a widening → voids |

### 5.2 Budget (item 8): reconciled with `presence.production` and the derived spend ceiling

Today there are two unrelated money limits: `presence.production {per_job, per_period, period, kinds}` (media jobs,
edited in Presence, `desk-v1-presence.js` `_budgetHTML`) and the **derived spend ceiling** `post cap × $0.20`
(SIMPLIFICATION §4, publishing cost, shown at approval). Ron's "promotion budget" is the umbrella over both.

- **Project:** `presence.production` is renamed `presence.budget = {amount, period, per_job, kinds}`. `amount` per
  period covers production and publishing. `per_job` and `kinds` stay as safety caps, not money pools.
- **Campaign `how.budget` absent (it is optional):** today's behaviour exactly. Spend ceiling = derived; production
  jobs draw on the project budget subject to `per_job`.
- **`source: 'project'`:** the campaign **earmarks** `amount` from the project budget at Launch (§9 Q3). Sum of live
  earmarks ≤ project `amount` for the period; Launch refuses an earmark the project cannot cover and says by how much.
- **`source: 'own'`:** a separate amount not counted against the project pool. `per_job` and `kinds` still apply
  (they protect the account and the wallet per job, whatever the pool).
- **Effective spend ceiling** = `how.budget.amount` when set, else the derived ceiling. Launch warns (does not
  block) when `amount` < derived publishing cost for the post cap, because publishing would stop early.
- **Approval bounds hold.** The ceiling that goes into `bounds_hash` is the number at approval time, in both modes, so
  a later project budget raise never widens a running campaign (IA 1 §2.1 rule, unchanged). Raising a campaign's
  amount, or switching `own → project` into a larger pool, is a widening: `⚠ Awaiting approval`. Lowering keeps
  approval. A project budget cut below live earmarks clamps those campaigns (narrowing rule) and logs it, as IA3 does
  for ceilings.
- `paid` stays Off in v1; when it arrives it spends from this same budget.

### 5.3 Presence

- `desk_agent: <character id>`: who plans and writes for this project. Asked once in step 0; default is the
  workspace's Desk agent setting if set, else the project's default agent. Never hardcoded. Today it is hardcoded
  twice in the backend (`mc/blueprints/desk_routes.py:254` and `:453`, `character='global:social-media-strategist'`)
  and in the UI (`desk-v1-kit.js:370` name row "Posy", `:392` placeholder "Tell Posy what to change…", status strings
  `:446`, `:476`, `:504`). `social-media-strategist` is a user-hired character on this install, not a shipped one, so
  on a fresh install both calls fail today with HTTP 400 (`strict_character=True` refuses an unresolvable pick,
  MC-925, `desk_routes.py:445`): voice seeding and drafting are dead without Posy.
- `budget` replaces `production` (§5.2).
- `measurement[]` entries gain `kind: 'manual'` (the user types the current number; dated). Real conversion sources
  are unchanged scope (none exist yet).

### 5.4 Conversation

Adds `read_at` (set when the thread is opened). `unread` = rows with `last_activity > read_at` or `read_at` null.

## 6. Engagement: its own menu, landing by project (replaces IA 1 §7 landing; lanes + filters hold)

**Landing = one bundle per project** (item 7), ordered by unread:

```
Clayrune                          5 unread · 3 awaiting you · 41 this week   𝕏 29 · in 12   Open ›
  latest: "does this work with WSL?" (𝕏, 2h ago)
Engulfing scanner                 0 unread · 0 awaiting you · 6 this week    𝕏 6             Open ›
Replies: drafted for your review (per project, IA7 banner kept)
```

- Counts: **unread** (§5.4), **awaiting you** (IA7's Suggested lane), **total** for the period (week default,
  toggle month). One latest-item line. Per-channel split as glyph + number.
- `Open ›` goes to IA7's existing lane view pre-filtered to that project (filters, lanes, `No campaign`, all kept).
- **Removed from the campaign page:** the Conversations tab and its embed (`desk-v1-engagement.js` `campaignId` path
  into `deskV1RenderConversations`). The campaign filter stays inside the Engagement menu.
- Home and the Desk header keep `💬 Engagement · n` where n = total unread across projects.
- **Honest data state.** No inbound read from X or LinkedIn exists (gap map #8); R1-E builds it. Until then every
  count is fixture-only. With no feed, a real bundle must read `Not connected: replies on 𝕏 aren't read yet`, never
  `0 unread`, so silence never reads as "no one is talking" (the R1-E test, now also a UI rule).

## 7. Shipped IA tickets this reworks (all eight)

| Ticket | Shipped | Reworked by | What changes |
|---|---|---|---|
| **IA1** `c229954` | Home project cards, project page, campaign under project | R2-2 | project cards → status board grouped by project; Desk-header project picker added |
| **IA2** `ed3410b` | plan split, `validatePlan` inherit + clamp, rule chips, project-prefixed draft keys | R2-1, R2-3 | `validatePlan` gains goal/budget/term bounds and `missing[].stop`; rule chips retire into stop summaries; draft keys re-scoped `project:<pid>:campaign:<id>:<stop>:` |
| **IA3** `a5c7cd2` | Presence settings, project Pause, ceiling clamp | R2-1, R2-5 | production budget → promotion budget (earmark clamp added); `desk_agent` picker; `manual` measurement |
| **IA4** `e528e19` | 3-step setup + step 0, Start gated on `validatePlan` | R2-3, R2-11 | steps 1 to 3 retired into the map; step 0 kept (+ agent question); Start moves to ⑥ |
| **IA5** `dc5b674` | piece page with 4 facets; review/video as piece children | R2-7 | facets retired; single piece drill-in (Copy · Media · Versions) under ③; review stays a piece child; video director moves to Studio |
| **IA6** `8d75d8a` | campaign Overview + empty states, project Next post, Engagement strip, archived toggle | R2-2, R2-3, R2-4 | Overview/tabs → map; UX_PASS 6.1 empty copy moves to ① and ③; project strip → count link; archived toggle kept |
| **IA7** `2bcaeb9` | Engagement dashboard, lanes, filters, Home count, campaign tab embed | R2-12 | landing becomes project bundles; campaign embed removed; lanes + filters kept |
| **IA8** `24818af` | acceptance journey + exit gate | R2-13 | journey rewritten to the map path |

T2's persistent frame (`shell.js` `_renderCampaignSkeleton`, `_gotoCampaignPanel`) **stays**: the map is its tab
strip, the stop bodies are its panels. `PANEL_ALIASES` (`shell.js:65`) extends: `results → goal`,
`content → what`, `calendar → when`, `conversations → engagement` (navigates out, filtered). T3's task lifecycle is
untouched.

**Smokes that change:** rewritten `desk-v1-home`, `-project`, `-campaign`, `-setup` (→ `-map`), `-piece`, `-results`
(→ ① checks), `-calendar` (→ ④), `-rules` (popover → ②/④/⑤/⑥ checks), `-engagement`, `-conversations`, `-journey`,
`-exit`. Untouched: `-kit`, `-review`, `-video` (director body; route parent changes only).

## 8. Tickets (R2-n = revision 2; replaces nothing still open, reworks IA1 to IA8). One builder each, merge one at a time, run the named smokes after EACH merge

| # | Ticket | Depends on | Acceptance (smoke; all 3 tones where it renders UI) |
|---|---|---|---|
| **R2-1** | Fixtures + kit shapes: campaign `goal` (measurable), `term`, `how`, `map`; presence `budget` (from `production`), `desk_agent`, `manual` measurement; conversation `read_at`; `validatePlan` returns `missing[].stop` and checks goal target + source, term, budget earmark ≤ project remaining | IA8 on master (done) | `desk-v1-kit.mjs`: plan without target → `missing` names `goal`; earmark $120 against project remaining $100 → `missing` names `how` with "short by $20"; widening `how.budget.amount` changes the bounds hash, lowering does not; fixture load has 0 `production` keys |
| **R2-2** (amended §11, frame 1) | Home status board: ONE column header for the whole page `CAMPAIGN · STAGE · GOAL PROGRESS · PACE · NEXT POST · NEEDS YOU`; each project is its own bordered block with a visible gap between blocks (Ron r2 (a)); block header = project name · agent chip `<agent> plans & writes` (or `⚠ Pick who plans for this project ›` → Presence) · `＋ New campaign`; row = title + subject line · stage glyph + word · **goal progress bar** (fill = current ÷ target) with an **elapsed tick** (position = fraction of term elapsed) and the text `11 of 30 <metric>` · pace pill · next post `<day time> · <platform>` or `Draft · at <stop>` · needs-you top-reason pill or `—`; legend line under the page title (`bar = goal reached · tick = time elapsed`); `Projects: All ▾` picker in the Desk header on every Desk route; `＋ New campaign` top right; retire IA1 project cards and Home drop targets. Needs-you SECTION vs column: §11.5 Q2 | R2-1, R2-4 (both merged) | `desk-v1-home.mjs`: 2 project blocks, each with its own header, rows under the right block, the column header rendered once; Windows beta testers (fixture clock at 57% of term) bar fill 36.7% ±1 pt, tick at 57% ±1 pt, text `11 of 30 tester signups`, pill `Behind`; bar carries `aria-label` `37% of goal, 57% of term elapsed` (never colour alone); untracked goal row shows an empty dashed bar + `not measured yet` and pace `—`, never `0 of`; Engulfing block header `Pick who plans for this project ›` lands on its Presence page; picker → Engulfing scanner from a campaign page lands on its project page in one click; 3 tones |
| **R2-3** | Campaign map frame: ① to ⑥ stepper replacing the tab strip; glyph + word per stop; lifecycle pill; `Next ›`/`‹ Back` in Draft; any stop clickable; ⑥ lists `missing` with links; retire IA4 steps 1 to 3 and the rules popover; `PANEL_ALIASES` extended | R2-1 | new `desk-v1-map.mjs`: New campaign → map at ①; Next through ⑥ with defaults; leave at ④ → row `Draft · at When` → Continue lands on ④; ⑥ Start disabled with 2 missing items, each link lands on its stop; old `results`/`content`/`calendar` deep links land on ①/③/④ |
| **R2-3b** (new §11; split from R2-3, whose frame half shipped as R2-3a `6b12dee`) | Retire the rest: IA4 setup steps 1 to 3 (`desk-v1-setup.js`), the rules popover and rule chips, and the campaign **Conversations tab + embed** (no frame 2 to 8 has one; while R2-12 is deferred, IA7's Engagement dashboard with its campaign filter is the route to them, `PANEL_ALIASES.conversations` navigates there filtered); stepper scrolls horizontally at ≤960px with the current stop kept in view (frame 10) | R2-3a (merged), R2-6 | `desk-v1-map.mjs`: New campaign renders no setup-step DOM; no rules-popover trigger on any stop; campaign page has no Conversations tab; old `conversations` deep link lands on Engagement filtered to that campaign; at 390 px the current stop's button box sits fully inside the stepper viewport on Goal and on Launch; `desk-v1-setup.mjs` and `desk-v1-rules.mjs` retired, and every live check they held is re-homed in `-map`/`-how`/`-calendar`/`-where` (the PR lists old check → new check; none dropped unnamed) |
| **R2-18** (reshaped 2026-09-30, Ron: "we start too deep") | **① Brief first**: `MAP_STOPS` = how, goal, what, when, where, launch; the old How stop is renamed **Brief** everywhere it shows (stepper, crumbs, Back/Next, Launch checklist, Home stage column) and moved first; route key stays `how` (old `goal`/`how` links still land); New campaign and `＋ New campaign` open on Brief. Brief, top to bottom: **Project** select (optional, pre-filled from a project page; a draft may stay project-less), **Agent** picker (campaign-scoped `how.agent`; options = agents hired on the selected project's floor via `/api/projects` roster + `/api/characters`, default the project's `presence.desk_agent` labelled `(project default)`; disabled with a hint until a project is picked; last option `+ Create new agent` opens Claydo's existing "Create an agent character" flow, so only the user's click creates), then R2-6's Strategy / Budget cards. `DeskV1Kit.deskAgentRef` = campaign `how.agent` first, then `presence.desk_agent`; the chat panel header shows the resolved agent and a project-less `No agent yet` text points at Brief. Launch still shows Project (editable) and Start stays gated on having one. NOT built here: the earlier Plan-chat design (seven stops, `plan_thread`, `how.suggested.goal/strategy`, `Change agent ›` writing the project) | R2-3b, R2-6 | `desk-v1-map/home/how/kit/journey/exit` updated: New campaign → 6 stops, Brief current, one empty Project select, agent picker disabled; pick a project → picker lists only its roster, default marked; pick Dave → `how.agent` = Dave and `deskAgentRef` returns Dave while `presence.desk_agent` is unchanged; `goal`/`how` deep links land; 0 visible `How` stop labels |
| **R2-4** | ① Goal: measurable goal editor (metric, target, baseline, horizon, deadline, source incl. manual entry) + effectiveness panel (progress, pace, cost per outcome, per-term rows); absorbs `desk-v1-results.js` | R2-3 | `desk-v1-results.mjs` → ① checks: manual entry 14 dated today updates progress and pace; `long` horizon shows term rows; source removed → `⚠ Not measured` |
| **R2-5** | Agent of choice: kit agent box reads name/avatar from `presence.desk_agent` (or `how.agent`) via `/api/characters`; step 0 asks it; every literal "Posy" in desk-v1 UI copy becomes the agent's name | R2-1 | `desk-v1-kit.mjs`: presence agent = Claydo → box header, placeholder and Working/Failed strings say Claydo; `rg -n "Posy" static/js/desk-v1-*.js` hits comments only; an unresolvable agent id shows `Pick who plans for this project ›`, not a broken box |
| **R2-6** (amended §11, frame 4) | How: **Strategy** card with Angle · Strategy · **Never claim** (new `how.never_claim`); **Budget (optional)** as three segmented buttons None · Project earmark · Own (`aria-pressed`), amount input for earmark/own, pool line `$<amount> / term earmarked from <project>'s $<pool>/<period> budget · $<remaining> remaining for other campaigns`; primary `Suggest What / When / Where` below both cards (re-runnable after edits) writing `how.suggested.{what, when, where}`; right agent box scoped `About: <campaign> · Brief`. **NO agent picker in R2-6** (it arrives with R2-18's Brief stop) | R2-3a, R2-5 (both merged) | `desk-v1-how.mjs` (R2-6 as merged: `[data-how-agent-trigger]` count 0, no `/api/characters` request; superseded by R2-18's picker checks): Never claim survives a stop switch; earmark $60 against a $100 pool with no other earmarks reads `$40 remaining`; earmark $120 → Launch `missing` names `how` with "short by $20"; Suggest → Ready → What shows `3 suggested`, When a cadence proposal, Where a placement; Accept all creates 3 `◇ Planned` pieces; forced Failure → Retry; own $50 on an Active campaign flips Launch to `Awaiting approval`, lowering to $40 does not; 3 tones |
| **R2-7** (amended §11, frames 5a to 5c, 15, 15a, 16a, 16b) | What: list rows = kind label · title · `on N channels` (N = version count) · per-version status summary on the right; a piece may carry **several assets** (`piece.assets[]`, thumbnails + `＋ Add media`); filters All · Needs review · Scheduled · Blocked; bottom **CONTENT TYPES** tray (Post · Article · Video · Image; the YouTube tile per §11.5 Q1): drag, or focus + Enter, creates a create-card at the top of the list (kind + editable title + ✕); **each drop is its own piece**, several of one kind allowed; **source-first**: Article card = `Browse existing` · `Write new` (→ R2-8 editor); Video card = four tiles, none preselected: Record from the product · Upload · Online source · Create new; Image card = Capture from the product · Upload · Online source · Generate (`Abstract visuals only — never the product UI`); no file picker exists until a source is chosen; chosen source shows as a chip + `Change source` and opens that source's body; **Upload body** = drop zone + `Browse this computer` + Material library folders; Record / Capture / Create / Generate / Online bodies are R2-8's; piece drill-in (Copy · Media · Versions), review 12b stays a piece child; retire IA5 facets and the Material side tray | R2-3a (merged), R2-6 | `desk-v1-piece.mjs` rewritten: fixture shows 6 pieces incl. 2 videos + 2 images and every row's `on N channels` equals its version count; `30 Windows testers wanted` shows 2 thumbnails + `＋ Add media`, adding one makes 3 and Undo returns 2; dropping Video shows 4 tiles with 0 `aria-pressed="true"` and 0 `input[type=file]`; Upload → chip `Upload` + drop zone + file input; Change source → back to 4 tiles; Video dropped twice → 2 new video pieces; keyboard path creates the same card; the Generate tile carries the abstract-only line; What → piece → Back reads `‹ <campaign> · What`; a Versions time edit shows on the When calendar the same day. `desk-v1-review.mjs` green; 3 tones |
| **R2-8** (amended §11, frames 11 to 14, 15b, 16, 16c) | Studio + the creation bodies What opens: **Studio home** (11) as its own Desk route: New video · New image · New article tiles, Recent (render %, `Draft saved`, `not attached`), Material library folders with file counts; **article writer** (12) inside the campaign frame: one tab per destination version (e.g. X thread · LinkedIn article), provenance line `Drafted by <agent> from this campaign's How`, inline `? Assumed` claim chips, `Claims & sources` side panel (source or `not yet verified`), `Save to What`; **storyboard** (13): header `‹ Back to What · New video · Storyboard`, `Returning to › <campaign> · What · <piece>` strip, scene cards (drag handle, real-capture thumbnail with number badge, title, one line, source, duration, Edit), agent side panel `Ask <agent> to change a scene…`, Render; **rendering** (14): the What row shows `⟳ Rendering 40%` + a progress bar, piece usable meanwhile; **Capture from the product** (16): `Screen:` picker + preview; **Online source** (15b, 16c): connected-account list with expandable thumbnail grid, and for an unconnected source the read-access warning + `Connect <x>` (scope per §11.5 Q1). Capture/Record use fixtures only: no capture backend is built here, and for a project with no capturable surface the tile reads `Not available for this project` | R2-7 | `desk-v1-video.mjs` extended + new `desk-v1-studio.mjs`: Studio from the header shows 3 tiles and Recent lists the fixture render at `Rendering 40%`; What → Create new opens the storyboard with ≥1 scene pre-filled from the piece copy and the `Returning to` strip names campaign + piece; scene 3 moves above 2 by drag AND by keyboard; Render → Back to What shows `⟳ Rendering` with `role=progressbar` + `aria-valuenow`; Write new opens the editor with one tab per piece version, the `? Assumed` claim appears both inline and in the side panel, Save to What returns with the piece `in review`; Capture shows a Screen picker + preview; an unconnected online source shows the warning and never a file grid; 3 tones |
| **R2-9** (amended §11, frame 6) | When: fields Cadence (`≤ n/wk from <project>'s ceiling`) · Min gap (inherited, read-only) · Term · Post cap; calendar Month · Week · List; **the user drags on the calendar to create OWN slots** (`when.slots[] {id, at, origin:'user'}`, solid, `Your slot`, may be empty); agent entries are **dashed** with piece + platform (`origin:'agent', state:'suggested'`) until accepted; legend `your own slot` / `agent-suggested`; Suggest never moves or deletes a user slot and fills user slots first; an own slot over the week's cadence or inside min gap is refused with the reason; a held piece shows `⚠ held`; the `Unscheduled` tray stays (below the fold in frame 6) | R2-3a (merged), R2-6 | `desk-v1-calendar.mjs` → When checks: cadence 5 under ceiling 3 → `≤3/wk · from <project>`; drag onto Fri 18 at 14:00 → a solid `Your slot` and a `when.slots` entry with `origin:'user'`; re-run Suggest → that slot unchanged and a suggested piece placed in it; a 4th own slot in a 3/wk week refused with `over 3/wk`; a slot 6 h after another under a 12 h min gap refused; own vs suggested also differ by word (legend + `aria-label`), not border alone; drag an unscheduled piece onto Thu sets its version time; ending earlier keeps approval, later voids it; 3 tones |
| **R2-10** (amended §11, frames 7a, 7b) | Where = a board: left **`Messages`** column (every piece from What, always listed, `on N channels` pill); one column per account **the campaign uses**, starting EMPTY unless an accepted agent suggestion placed one (Ron r1 item 4); column header = account avatar with platform badge + handle + platform + ✕; version card = title · `<kind> → <platform format>` · time with platform glyph; empty column reads `Drag a message here to add its <platform> version.`; rule line under the board: dragging from Messages ADDS a version (the message stays listed), dragging between columns MOVES it; bottom **SOURCES** tray: one card per connected account, several accounts on one platform shown separately with username + avatar, a not-connected card with `Connect ›` → Presence; dragging a source card up adds its column (on Active = widening → `Awaiting approval`), ✕ removes it (narrowing keeps approval); keyboard equivalents for all three drags; retire the campaign Add tray | R2-3a (merged), R2-7 | new `desk-v1-where.mjs`: fixture board matches frame 7b counts (Messages: Install 3, Agent 1, Home 2, Where board 1, Retro 2, Post 2; columns @ron 5, Clayrune Page 4, third column 2 or per §11.5 Q1); Retro FAQ from Messages onto @ron → still in Messages with `on 3 channels`, new card `Article → X thread`; that card dragged @ron → Clayrune Page moves it (total unchanged); @clayrune source dragged up on an Active campaign → Launch `Awaiting approval`; ✕ on a column of an Active campaign keeps approval; a new campaign's Where shows Messages + 0 account columns; 2 X accounts render as 2 source cards with distinct handles; keyboard move works; 3 tones |
| **R2-11** (amended §11, frame 8) | Launch: bounds table rows exactly Accounts + voices · Cadence / min gap · Term · Source scope · Spend ceiling · Budget · Goal / measurement · Stop conditions; a missing item shows inline on its row (`⚠ 60 beta signups, not tracked · Set a source ›`) AND in the sentence under the disabled Start (`Set 1 thing first: measurement source for the goal.`), and that stop's map glyph turns `⚠`; a waiting agent question renders as a `Needs your answer` card with answer buttons in the right column; Start; Live state; Pause/Resume; `Renew term` for long horizons (new approval, same bounds shown). The server-side mirror of the gate (journal 2026-09-29: goal target + source and term ≤ 90 d are client-only today) is briefed with this ticket | R2-4, R2-6, R2-9, R2-10 | `desk-v1-map.mjs`: Proposed campaign with no source → Start disabled, Goal glyph `⚠` + word, `Set a source ›` lands on Goal's source field; answering `X first` removes the card and records the answer on the campaign; Start with defaults → `Live since`; long goal at term end → `Renew term` → term 2 with a new approval record; project budget cut below earmark clamps and logs |
| **R2-12** (**DEFERRED** by Ron 2026-09-29: "social response handling + dashboards deferred"; frame 9 is its visual spec when it resumes; the campaign-tab removal moved to R2-3b) | Engagement menu landing: per-project bundles (unread / awaiting / total, channel split, latest line, `Not connected` state); remove campaign Conversations tab + embed; `read_at` | R2-1 (parallel), merges after R2-3 | `desk-v1-engagement.mjs`: 2 bundles, Clayrune `5 unread`; opening a thread drops it to 4; `Open ›` shows IA7 lanes filtered to that project; campaign page has no Conversations tab; a project with no feed reads `Not connected`, never `0 unread` |
| **R2-13** (amended §11) | Acceptance journey + exit gate on the new path | R2-2 to R2-11, R2-3b, R2-18, R2-14 to R2-17 (R2-12 deferred: its steps drop out until it resumes) | `desk-v1-journey.mjs`: Home → picker → Engulfing scanner → New campaign → **Plan: Change agent + chat → Goal/How cards `suggested`** → Goal: accept a long goal → How: Suggest (forced failure + Retry) → accept → What: drop Video → Create new → Studio storyboard → Render → back → When: drag an own slot → Where: drag a source up + a message into it → Launch Start → Home row `Active · on track` → **term end (fixture clock) → Needs-you `Retro ready` → Goal: paste per-post numbers → Confirm one finding, Reject one → Renew term → How: Suggest shows `Based on F<n> ›` and never the rejected one** → Delete a never-started Draft (Undo). `desk-v1-exit.mjs` exit 0 incl. map, plan, how, where, studio, **retro, playbook** × 3 tones |
| **R1-P** (amend) | backend presence/campaign/piece: add `goal` shape, `term`, `how.budget`, earmarks, `desk_agent`; migration maps `production → budget`, `outcome → metric`, `setup → map` | R2-1 merged (shapes frozen) | tests: earmark sum ≤ project budget enforced at the route; budget raise changes `bounds_hash`; migration idempotent |
| **R1-A** (new) | backend agent of choice: desk routes resolve `presence.desk_agent` instead of `global:social-media-strategist` (`desk_routes.py:254`, `:453`); unresolvable → structured error naming the project, never a silent default | R1-P | tests: a presence naming `global:claydo` dispatches Claydo; a missing character returns 409 with `pick_agent`; no route string contains `social-media-strategist` |
| **R1-E** (amend) | engagement feed + `read_at` + per-project aggregates for the landing; **(§10.7) writes per-post outcome entries `source:'feed'` where the platform exposes them, each read costed against the budget**; **per-account `read_via` = `pane` (default, free) or `api` (paid) is the user's choice, set from a Read via control in Presence** | R1-P, R1-L | tests: per-platform coverage gap → bundle `Not connected`; aggregates match lane counts; a feed entry never overwrites a typed entry for the same post + metric (both kept, source shown); a platform with no metrics read leaves per-post cells `No per-post numbers yet`, never 0 |
| **R2-14** | Outcome loop fixtures + kit shapes (§10): ledger row `piece_id/format/account/term/cost`, per-post `outcomes[{metric, value, at, source}]`, `retro` object, `finding` object + states, rejection record; kit `retroVerdict()` implementing §10.1's sample-size table | R2-1 | `desk-v1-kit.mjs`: arms 6 vs 4 posts → `Too few posts to tell (6 and 4; need 10 each)`; 12 vs 12 with 1.2× gap → `No clear difference`; one post = 60% of an arm → `One post drives this`; platform row reads `Platform + voice` with the can't-separate note; fixture load has 0 findings with `state:'confirmed'` and `origin:'unattended'` together |
| **R2-15** | ① Retro section: per closed term goal vs actual, spend + cost per outcome, dimension table with verdicts, per-post number grid (paste from CSV), proposed findings with Confirm / Edit / Reject / Don't suggest again; `Run retro now` = Interim, no findings; Needs-you row `Retro ready: n findings to confirm` | R2-4, R2-14 | new `desk-v1-retro.mjs` × 3 tones: closed term shows `Judged on clicks per post, not on signups`; paste 12 rows fills the grid; Interim shows 0 proposed findings; Confirm F3 removes the Needs-you row; Edit wording then Confirm stores `edited_text` and `origin:'interactive'`; Reject then re-run retro on the same evidence proposes nothing; a finding whose `maybe_why` says "raise the cadence" renders without that line |
| **R2-16** | Project page Playbook: confirmed findings by dimension (sentence, confidence, n, campaign links), Stale sub-list with Re-confirm / Retire, rejected collapsed with `Undo reject`; no Home line (Needs-you only) | R2-15 | `desk-v1-project.mjs` extended: 2 confirmed + 1 stale + 1 rejected fixture render in their groups; campaign link lands on that campaign's ①; Undo reject moves the finding back to `proposed`; Home has no playbook line |
| **R2-17** | How agent cites the playbook: Suggest results carry `because`; `Based on F3 ›` / `Trying: untested` chips on ③ ④ ⑤ suggestions; unknown or non-confirmed ids dropped | R2-6, R2-16 | `desk-v1-how.mjs` extended: with F3 confirmed, the ④ Tue 09:00 suggestion shows `Based on F3 ›` and opens F3's evidence; a suggestion citing rejected F5 or unknown F99 shows no chip; with a covered dimension, ≥1 of 5 suggested pieces is labelled `Trying`; accepting an F3-based slot on an Active campaign within bounds keeps approval |
| **R1-L** (new) | backend outcome loop: ledger rows gain `piece_id/format/account/term/cost`; per-post `outcomes[]` replaces the free-form `outcome` dict (migration keeps old values as one `source:'manual'` entry); `mc/desk_retro.py` computes the retro deterministically at term/campaign end (model words summary + `maybe_why` only); `store['playbook']` + state transitions (human-only routes); rejection records + `evidence_key`; `playbook_brief()` in the Suggest brief (R1-A's dispatch); `distiller.authority_violation` made public and applied with the Desk bounds pattern | R1-P, R1-A | tests: retro on a fixture term reproduces R2-14's verdicts byte-for-byte; a retro run from a steward/scheduled context creates only `proposed, origin:unattended` findings; `playbook_brief()` contains confirmed findings only (proposed/rejected/stale absent); same evidence after Reject proposes nothing, +10 posts from a new campaign re-proposes with the prior rejection date; no finding route accepts a field naming a bound (`cadence`, `budget`, `accounts`, `approval`, `end`, `post_cap` → 400); the state-change routes refuse an unattended caller; `data/desk.json` stays outside `DATA_DIR` |

**Superseded 2026-09-29 by §11.4** (the order from here; kept below for history). Original order: R2-1 → R2-3 → then R2-4, R2-5, R2-7, R2-9, R2-10 build in parallel and **merge one at a time** in that order
(smokes after each) → R2-6 → R2-8 → R2-11 → R2-2 → R2-12 → **R2-14 → R2-15 → R2-16 → R2-17** → R2-13. R2-14 may
build in parallel with anything after R2-1 (fixtures only) but merges in this slot. R1-L follows R1-A on the backend
track. The 5-user test (UX_PASS §9 footer) runs after R2-13, probing: which stage is this campaign at; is it on track;
where do you change the budget; where are replies; **what worked last term, and why did the agent suggest this slot**.

## 9. Open questions for Ron (the tickets assume each recommendation)

**Answered 2026-09-29 (Ron): all three recommendations accepted.** Q1 measurable goal required before Launch, `manual`
allowed as the source. Q2 long campaigns run in ≤90-day terms, each renewed with one tap. Q3 project-funded budgets
are earmarked at Launch, never a shared pot.

1. **Must a goal be measurable before Launch?** Your item 2 says yes; the shipped Desk treats an untracked goal as a
   warning (CMP-03, `desk-v1-fixtures.js:591`), because no real conversion source exists yet.
   **Recommend: require a target number and a source, and allow `manual` as the source** (you type the current
   number). Nothing is blocked on an integration, and every campaign has a number to be judged against.
2. **Long-term campaigns versus the 90-day approval limit.** SIMPLIFICATION §4 refuses any approval longer than 90
   days, and approval must stay finite. **Recommend: a long campaign keeps one goal for its whole horizon (say 6
   months) but runs in terms of ≤90 days; each term needs a one-tap `Renew term` approval showing the same bounds.**
   The alternative (one approval for the whole horizon) reverses the finite-approval rule and I would record it as
   superseded.
3. **Campaign budget drawn from the project pool: earmark or shared?** **Recommend: earmark.** At Launch the campaign
   reserves its amount; the sum of live earmarks cannot exceed the project budget. Shared first-come spending lets one
   busy campaign silently starve another, and no screen would show it until the money is gone.

## 10. Outcome learning loop (Ron's item 9, 2026-09-29)

> "We should have a loopback which measures the effectiveness of the campaign to learn long term what worked and
> what not."

**The gap.** The Desk learns one thing today: VOICE, from Ron's edits (`desk.record_edit` → `voice_brief`,
THE_DESK_SPEC.md "Five stores"). Nothing learns from OUTCOMES. The story ledger (`desk.record_published`) has an
`outcome` slot per post, a free-form dict written by hand through `POST /api/desk/ledger/<id>/outcome` and read by
nobody. Ledger rows carry `campaign_id`, `platform`, `voice`, `published_at`, but no `piece_id`, format, account or
cost, so even a filled-in outcome cannot be attributed to anything.

**The loop, in four steps:**

```
 ⑥ term ends / campaign ends
      │  (unattended: autonomous campaigns publish with nobody watching)
      ▼
 RETRO  computed by code, worded by a model      → ① Goal · effectiveness panel · "Retro" section
      │  goal vs actual · spend · per-dimension table · "too few to tell" where true
      ▼
 PROPOSED FINDINGS (origin: unattended)          → Needs you: "Retro ready: 3 findings to confirm"
      │  Ron: Confirm · Edit · Reject · Don't suggest again      ← the human side of the loop
      ▼
 PLAYBOOK (confirmed only, origin: interactive)  → project page · Playbook
      │
      ▼
 ② How agent's Suggest task reads the playbook   → ③ ④ ⑤ suggestions carry `Based on F3 ›` or `Trying: untested`
```

### 10.1 Retro: when, and what it holds

- **When.** At every term end (`long` horizon) and at campaign end (a `short` goal has one term, so the same event).
  `Run retro now` exists mid-term but is labelled **Interim**, shows numbers only and **never proposes findings**: a
  half-term's numbers are the noisiest there are.
- **Goal vs actual**, per term and overall: target, actual, pace at close (§4.1), delta from baseline.
- **Spend**: publishing (ledger count × platform rate) + production (media jobs), against `how.budget` or the derived
  ceiling; **cost per outcome** when spend > 0 (same rule as §4.1).
- **Per-dimension table**, one row per dimension with its arms and the per-post metric per arm:

| Dimension | Arms come from | Unit of evidence | v1 honesty rule |
|---|---|---|---|
| Piece format (post / article / video / image) | piece kind | post | |
| Platform + voice | version account | post | **one dimension, not two**: the voice split fixes Ron to 𝕏 and Clayrune to LinkedIn, so v1 cannot separate "LinkedIn worked" from "the Clayrune voice worked". The row says so. |
| Posting day / time slot | version publish time | post | compared **within one account** only (platforms have different audiences at different hours) |
| Angle / strategy | `how.angle`, strategy tag | **campaign** | n = campaigns, so almost always `Too few campaigns to tell` in v1; shown anyway so the gap is visible |
| Spend kind (none / project / own; production on/off) | `how.budget`, media jobs | campaign | cost per outcome, same n caveat |

- **Which metric judges a post.** The goal metric (say signups) is campaign-level; no post can be credited with a
  signup from manual numbers. Per-post dimensions are judged on a **per-post metric** chosen at ① (default `clicks`,
  else `engagements`), and the retro says so in words: `Judged on clicks per post, not on signups.`

**Sample-size rules (deterministic code, never the model):**

| Situation | Retro says |
|---|---|
| any arm has < 10 posts with a number | `Too few posts to tell (6 and 4; need 10 each)` |
| relative gap between arms < 30%, or mean and median disagree on direction | `No clear difference` |
| one post supplies > 50% of an arm's total | `One post drives this, not a pattern` (post linked) |
| campaign-level dimension with < 3 campaigns per arm | `Too few campaigns to tell (1 and 1; need 3 each)` |
| none of the above | a candidate finding, confidence per §10.2 |

The model only words the retro summary and an optional one-line `maybe_why`; it never picks a winner the table did not
already produce.

### 10.2 Finding and playbook

A **finding** is structured, and its sentence is rendered from the structure by code:

```
{id:'F3', project_id, scope:'project', dimension:'slot', arms:{a:'Tue/Thu 08-10', b:'other slots'},
 account:'x:ron', metric:'clicks', effect:{ratio:2.1, direction:'a>b'},
 evidence:[{campaign_id, term, n_a, n_b}], n_total, confidence:'low'|'medium'|'high',
 maybe_why?: '<model text, checked>', state, origin, decided_at, decided_by, edited_text?}
→ "On 𝕏 (Ron), Tue/Thu 08-10 got 2.1× the clicks per post of other slots (3 campaigns, n=41, medium)."
```

- **Confidence:** `low` = one campaign/term. `medium` = same direction in ≥ 2 campaigns, pooled n ≥ 20 per arm. `high`
  = ≥ 3 campaigns, pooled n ≥ 30 per arm, and no confirmed retro pointing the other way.
- **States:** `proposed` → `confirmed` (as-is or with Ron's edited wording) | `rejected`. `confirmed` → `stale` when a
  newer retro points the other way (proposed as `Contradicts F3`) or after 180 days → Ron re-confirms or retires.
  **Only Ron moves a finding between states.** Only `confirmed` findings reach an agent.
- **Scope:** per project by default; cross-project is §10.8 Q2.

### 10.3 The How agent reads the playbook

- The ② `Suggest What / When / Where` brief (R1-A's dispatch, beside `voice_brief`) gains a `PLAYBOOK` section:
  confirmed findings for this project, each with id, sentence and confidence. Proposed, rejected, stale and retired
  findings are never in it.
- Every suggestion carries `because: [finding ids]` or `because: 'untested'`. The UI renders `Based on F3 ›` (opens
  the finding with its evidence) or `Trying: untested`. A cited id that is not a confirmed finding of this project is
  **dropped** and the suggestion shows no chip, so an agent cannot invent authority for a suggestion.
- **Exploration, not just exploitation.** Where the playbook covers a dimension, the brief asks for at least 1 in 5
  suggested pieces to try something the playbook does not favour, labelled `Trying`. Without it the playbook only ever
  confirms its own first guess, and a slot that was never tried can never win.
- The agent SUGGESTS. Accepting stays Ron's tap, exactly as §4.2.

### 10.4 Where it shows

| Surface | Shows |
|---|---|
| **① Goal**, effectiveness panel | a `Retro` section per closed term (goal vs actual, spend, dimension table, per-post number grid, proposed findings with `Confirm` · `Edit` · `Reject` · `Don't suggest again`); `Interim` retro on demand |
| **Project page** | `Playbook`: confirmed findings grouped by dimension, each with confidence, n and its campaigns as links; a `Stale` sub-list; rejected findings collapsed, each with `Undo reject` |
| **Home** | **no new line.** A retro with findings to confirm is a Needs-you row (`Clayrune · Retro ready: 3 findings to confirm`), the queue that already exists; a second Home line would duplicate it |
| **② How** | `Based on F3 ›` / `Trying` chips on suggestions (§10.3) |

### 10.5 Guardrails (CLAUDE.md "Learning-system safety rails", applied here)

1. **Authority guard: a finding changes what the agent SUGGESTS, never what is allowed.** Enforced by structure, not
   wording: the finding schema has no field that can name an approval bound (accounts on/off, cadence ceiling, min
   gap, end, post cap, budget, spend ceiling, approval, term). Its only consumer is the Suggest brief, whose output
   lands as `? suggested` items; bounds are checked by `validatePlan` and the approval `bounds_hash` whatever a
   suggestion's source, so an accepted suggestion that widens a bound on an Active campaign still flips ⑥ to
   `⚠ Awaiting approval`. Second layer for machine-written text: `maybe_why` and the retro summary pass
   `distiller._authority_violation` (exposed as a public `authority_violation`, not copied) plus a Desk bounds pattern
   (`raise|increase|more` near `cadence|budget|spend|cap|ceiling|accounts|approval`); a hit drops the text and logs
   it. Findings describe ("got 2.1× the clicks"); they never prescribe ("post more often").
2. **A human on one side of every loop.** Autonomous campaigns publish unattended and their retros run unattended, so
   every retro-born finding is `origin: unattended`, `state: proposed`, invisible to agents until Ron confirms or
   edits it (which stamps `origin: interactive`). Same rule as `exploration_read_floor(consumer_unattended=True)`:
   autonomous output never becomes autonomous input. Unstamped findings fail closed.
3. **"No" is durable.** A rejection stores `{project_id, dimension, arms, direction, evidence_key}`, where
   `evidence_key` = hash of the sorted `(campaign_id, term)` set. The same finding from the same evidence is never
   re-proposed. It may return only with **new** evidence (≥ 10 more posts from campaigns/terms outside the rejected
   set), and then says so: `You rejected this on 12 Oct (1 campaign, n=12). New: +2 campaigns, n=31.`
   `Don't suggest again` suppresses that dimension + arms + direction for the project permanently (lifted only by
   `Undo reject` on the project page).

### 10.6 Where findings live: a `playbook` store in `data/desk.json`, not the Distiller

**Recommendation: host findings in the Desk store** (`store['playbook']`, beside `voices`, `campaigns`, `ledger`,
under the same `_store_lock`), render them with a `playbook_brief()` that mirrors `voice_brief()`, and **reuse** the
Distiller's guard and rejection semantics rather than its store.

Why not `mc/distiller.py` or its skill artifacts:
- The Distiller's input is session transcripts clustered through a closed-vocabulary phrase fingerprint; its output is
  SKILL.md prose that skill matching loads into ANY agent on any project. A finding is numbers tied to campaign ids,
  consumed by one brief. As a skill it would leak Desk statistics into unrelated agents' prompts, and
  `dimension=slot, arms=Tue/Thu 08-10` has no place in the fingerprint vocabulary.
- Its human gate is the promote queue, measured at 80 promoted vs 2 rejected (CLAUDE.md): a rubber stamp. A finding
  needs its evidence on screen at the moment of confirmation, which is ①'s retro, not a queue of prose.
- The Desk already runs a learning loop this way (voices in `desk.json` → `voice_brief` → drafting brief). The
  playbook is the second half of that loop, not a new subsystem.
- `data/desk.json` is a sibling of `DATA_DIR`, not a member, so the DATA_DIR pollution rule is untouched.
- The "native learning-item system" preference is about reference knowledge shared across projects. No separate
  learning-item store exists in code (only Distiller artifacts); if Ron wants cross-project findings (Q2), that is a
  `scope: 'workspace'` flag in the same store, not a second system.

Reused, not rebuilt: `_authority_violation` (made public, one regex, one test file); the origin stamp and fail-closed
read of `exploration_read_floor`; the durable-suppression pattern of `_suppress_artifact` / `_is_suppressed`.

### 10.7 Data reality: what works on manual numbers, what waits for R1-E

**Works now, manual only:**
- Goal vs actual per term, pace at close: from ①'s `manual` entries (§9 Q1, already required).
- Posting facts per dimension (format, account, slot, count): OUR records (ledger + pieces), no inbound read needed,
  once R1-L adds `piece_id`, format, account, term and cost to ledger rows.
- Spend and cost per outcome: publishing cost is known per post, production cost per media job.
- Per-post attribution **only if Ron types one number per post** at retro time: a grid, one row per published post,
  one column for the chosen per-post metric, paste-from-CSV accepted (both 𝕏 and LinkedIn show per-post numbers in
  their own analytics). Without it every per-post dimension reads `No per-post numbers yet`, and only the
  campaign-level rows (almost always `Too few campaigns`) remain. That is Q1.

**Waits for R1-E (amended below):** per-post numbers filled automatically (`source: 'feed'`). Reply counts arrive with
the engagement feed; impressions and clicks need a metrics read on top of it. Expected constraints, to be confirmed in
R1-E: 𝕏 reads are pay-per-use and must be costed against the budget; LinkedIn page analytics are expected to need the
same Community Management API approval the Clayrune page's posting already waits on.

**Confirmed in R1-E (2026-09-30, `mc/desk_engagement.py`):** X post reads are $0.005 per resource returned
(docs.x.com pricing), costed against `presence.budget` (publishing spend + read spend; budget 0 = no paid reads)
and recorded per read. The vault held no X OAuth token (`x.oauth-token`), so X reads report `Not connected` until
a human adds one. LinkedIn comments and page analytics sit behind Community Management API approval we lack, so
LinkedIn is gap-only: no call, reported `Not connected`. Built and tested against recorded responses; no live call
has been made. `non_public_metrics` (`url_link_clicks`) is wired but off by default, unverified against the live API.
**Read route is the user's choice per account (Ron, 2026-09-30):** `presence.accounts[].read_via` is `pane` (default, free, 0 against the budget: the account's own named browser profile, read-only, parsed from page text) or `api` (the paid route above). Presence carries a Read via control per X/LinkedIn account; an unreachable route always says why (`Not connected (no API token)` / `(sign in to X in the browser pane)`), and the Desk never switches route on its own. Pane post stats are read only where exact counts are visible, else recorded unavailable. LinkedIn has no pane reader yet (gap-only either way). Broad listening stays pane-only.

Until then the retro labels each number's source (`typed 3 Oct` / `from 𝕏`), and a dimension with no numbers never
renders as zero.

### 10.8 Questions for Ron (the §8 R2-14 to R2-17 and R1-L rows assume each recommendation)

**Answered 2026-09-29 (Ron):**
- **Q1, reframed:** "Goals should be measured as they go, each day brings its own data measured against the set
  goal." Progress is tracked **daily**, not collected at term end. Consequence: the automatic metrics read (R1-E) is
  what Ron actually wants, so it moves onto the critical path rather than trailing the UI. The manual per-post grid stays
  only as the fallback for a platform or metric with no read, and may be filled any day, not just at term end. The
  term-end retro still runs, over the daily series.
- **Q2:** per project by default, with `Use in all projects` (recommendation accepted).

1. **Will you type one number per post at term end?** Attribution by format, platform and posting time needs a
   per-post number, and none flows in until R1-E. **Recommend: yes, optional, one metric (clicks by default) in a
   paste-friendly grid, about 10 to 30 rows per term.** Skip it and retros are goal vs actual plus spend only until
   R1-E.
2. **Should a confirmed finding reach other projects?** **Recommend: per project by default; a confirmed finding gets
   a `Use in all projects` action, after which other projects' agents see it labelled `from <project>` with its
   confidence dropped one level.** Clayrune's 𝕏 audience is not the engulfing scanner's, so automatic sharing would
   mostly move noise.

## 11. Mockup deltas (approved 2026-09-29)

Ron approved the round 2 to 5 mockups on 2026-09-29 ("looks good for now"; social response handling and dashboards
deferred). The 24 frames in `docs/desk_v1/mockups_r2/` are now the **visual spec**: where a frame and a §1 to §7
sentence disagree, the frame wins unless §11.5 lists it as open. Generator: `_scratch/desk_mockups_r2/build.py`
(not committed). Frames 15b and 16c were re-rendered with `you@example.com` in place of a real address before
commit; nothing else in either frame changed. Feedback trail: journal `8f64d565-desk-v1-redesign.md`, from
"Round 2 mockups" onward.

### 11.1 Frame → ticket

| Frame | Shows | Built by |
|---|---|---|
| `1-home` | status board, one column header, per-project blocks, goal bar + elapsed tick, legend | **R2-2** |
| `2-plan-chat` | Plan agent chat + Change agent (NOT built; R2-18 reshaped to Brief first, 2026-09-30) | deferred |
| `3-goal` | ② Goal editor + effectiveness panel | R2-4 (merged); the `? suggested` fields are R2-18's |
| `4-how` | ③ How: Strategy card, Budget segmented, Suggest, no picker | **R2-6** (amended) |
| `5a-what-tray` | What list with `on N channels`, 2 videos + 2 images, a 2-asset post, CONTENT TYPES tray | **R2-7** |
| `5b-what-article` | Article create-card: Browse existing · Write new | **R2-7** |
| `5c-what-video` | Video create-card on the list | **R2-7** |
| `6-when` | calendar with user-dragged `Your slot` (solid) vs agent-suggested (dashed), legend | **R2-9** |
| `7a-where-empty` | Messages column + empty account columns + SOURCES tray | **R2-10** |
| `7b-where-populated` | same, populated; counts match 5a | **R2-10** |
| `8-launch` | bounds table, inline missing item, `Needs your answer` card | **R2-11** |
| `9-engagement` | per-project engagement bundles | R2-12, **deferred** (spec for when it resumes) |
| `10-goal-mobile` | stepper at 390 px, current stop in view | **R2-3b** (stepper) + R2-4 (panel) |
| `11-studio-home` | Studio route: New tiles, Recent with render %, Material library | **R2-8** |
| `12-article-write` | article writer, version tabs, claims & sources panel | **R2-8** |
| `13-video-storyboard` | storyboard, `Returning to` strip, agent side panel, Render | **R2-8** |
| `14-video-rendering` | What row `⟳ Rendering 40%` | **R2-8** |
| `15-video-browse` | Upload body: drop zone + Material library folders + clip grid | **R2-7** |
| `15a-video-choose-source` | four source tiles, none preselected | **R2-7** |
| `15b-video-online` | Online source: connected accounts + Connect warning | **R2-8** (scope: §11.5 Q1) |
| `16-image` | Capture from the product: Screen picker + preview | **R2-8** |
| `16a-image-choose-source` | four image source tiles, Generate abstract-only | **R2-7** |
| `16b-image-upload` | image Upload body | **R2-7** |
| `16c-image-online` | image Online source | **R2-8** (scope: §11.5 Q1) |

### 11.2 Rows amended in §8

R2-2, R2-6, R2-7, R2-8, R2-9, R2-10, R2-11, R2-12 (deferred), R2-13 amended in place, each marked `(amended §11)`.
**R2-3b** (retire IA4 steps, rules popover, campaign Conversations tab; mobile stepper) and **R2-18** (① Plan with
agent) are new rows. R2-18 (reshaped 2026-09-30) puts Brief first: six stops, Brief, Goal, What, When, Where, Launch;
circled numbers in §2 to §4 are the pre-Brief numbering (② How there = ① Brief now, ① Goal = ② Goal). Route names do
not change, so deep links and `PANEL_ALIASES` hold. §4.1 item 1 ("Goal is written by the user") still holds.

**Correction to the dispatch brief:** R2-2 is **not** on master. Branch `desk-r2-2` has no commits of its own (it sits
at a master commit) and master's `desk-v1-home.js` has no goal bar, elapsed tick or legend. Merged Desk revision-2
work on master `ee96fe9`: R2-3a `6b12dee`, R2-5 + R1-A `7d79c89`, R2-4 `b450b03`, R2-14 `1faa4d5` (plus R2-1's shapes).

### 11.3 What R2-6's WIP must change before it merges

Branch `desk-r2-6`, 8 WIP commits `68d7ab5`..`31ffbb0`, merges cleanly onto master; smoke green against the OLD row.
Against the amended row it must:

1. **Remove the agent picker from How.** In `static/js/desk-v1-how.js` delete the `[data-how-agent-trigger]` button,
   its click handler that writes `how.agent`, and the `/api/characters` fetch that feeds it (the file's own comment
   says the list is re-fetched because the Presence picker does not export it; that need moves to R2-18). The How
   agent box keeps resolving its name through `DeskV1Kit.deskAgentRef` read-only.
2. **Add Never claim** (`how.never_claim`, fixtures + panel) and render Budget as the three segmented buttons with
   `aria-pressed`, plus the pool line wording in the amended row.
3. **Move Suggest** below both cards as the primary button, re-runnable after edits.
4. **Rewrite `tools/smoke/desk-v1-how.mjs`**: drop the check that the picker trigger renders a label (and the
   `/api/characters` stub that exists only for it); add `[data-how-agent-trigger]` count 0, no `/api/characters`
   request from the How panel, Never claim survives a stop switch, the `$40 remaining` pool line.
5. **Keep** the rest: single-home `camp.how` fixtures, Suggest task + ③ Accept-all, ④ suggested cadence, ⑥
   awaiting-approval check, camp-1 budget `none` + `approval.bounds` snapshot, CSS.
6. Squash-merge (or merge with a non-WIP message); run `desk-v1-how`, `-map`, `-calendar`, `-kit`, `-exit` after.

**Carried into R2-18, not R2-6:** Ron's later 2026-09-30 decision (campaign-scoped agent, reversing the same-day
"agents per project only" ruling) restores the plan: `DeskV1Kit.deskAgentRef`'s precedence flips so a per-campaign
`how.agent` wins over `presence.desk_agent`, and `desk-v1-kit.mjs`'s precedence check is updated to match. The picker
is on Brief; `Change agent ›` on the chat panel is not built. A project-less draft with no `how.agent` resolves no
agent (`No agent yet`, pointing at Brief).

### 11.4 Build order from here (supersedes the order under §8)

One builder at a time; merge; run the named smokes **plus** `desk-v1-exit.mjs` before the next ticket starts.

1. **R2-6** (fix WIP per §11.3) → smokes `how`, `map`, `calendar`, `kit`, `exit`
2. **R2-2** Home (independent; Ron's first frame) → `home`, `exit`
3. **R2-3b** retire IA4 steps / rules popover / Conversations tab, mobile stepper → `map`, re-homed checks, `exit`
4. **R2-18** Brief first (project + campaign agent) → `how`, `map`, `kit`, `home`, `journey`, `exit`
5. **R2-7** What tray + source-first picker + multi-asset → `piece`, `review`, `calendar`, `exit`
6. **R2-8** Studio + creation bodies → `studio`, `video`, `piece`, `exit`
7. **R2-9** When own slots → `calendar`, `how`, `exit`
8. **R2-10** Where board → `where`, `map`, `exit`
9. **R2-11** Launch → `map`, `where`, `exit`
10. **R2-15 → R2-16 → R2-17** outcome loop, one at a time → `retro`, `project`, `how`, `exit`
11. **R2-13** journey + exit gate, then the 5-user test (§8 footer probes)

Backend track, parallel with the UI and one at a time: R1-P → R1-L → R1-E (R1-A shipped in `7d79c89`). R2-12 stays
deferred; its frame 9 waits in the folder.

### 11.5 Open questions for Ron (the mockups decide something a standing section does not)

1. **Channels and sources beyond X + LinkedIn.** The header of this doc (and the Desk spec) scope v1 to X + LinkedIn,
   and the MC-871 position records that Clayrune has no third-party integration layer. The frames add a YouTube
   column and source card (7b), a Discord source card (7b), a `YouTube` CONTENT TYPES tile (5a, 15b), and Online
   sources reading YouTube, Google Drive and Dropbox (15b, 16c). **Recommend: v1 publishes to X + LinkedIn only;
   YouTube, Discord and Reddit render as source cards reading `Coming later`, not draggable; drop the `YouTube`
   content-type tile (a Video piece gets a YouTube version in Where, so it is a destination, not a kind); the Online
   source body ships on fixtures with `Connect` disabled and a line naming the later ticket; each real connector is
   its own backlog item.** R2-7, R2-8 and R2-10 acceptance already allow either answer.
2. **Home Needs-you section versus column.** §2 keeps a `Needs you (all projects)` section above the board
   ("unchanged from IA1"), and §10 routes `Retro ready` there. Frame 1 has no section: each row carries its top
   reason in a NEEDS YOU column. **Recommend: the column replaces the section on Home; a row with several reasons
   shows the top one + `+n`; `Retro ready: n findings to confirm` becomes that campaign's pill; the project page
   keeps its own Needs-you list** (§2), so nothing becomes unreachable. R2-15's "removes the Needs-you row" then reads
   "removes the pill".

### 11.6 Ron's answers (2026-09-29)

1. **Channels beyond X + LinkedIn: build the tiles and placeholders so the mocked-up view is there.** Supersedes the
   §11.5 Q1 recommendation. YouTube, Discord and Reddit source cards, the `YouTube` content-type tile, and the Online
   source body (YouTube, Google Drive, Dropbox) all render as in the frames, on fixture data, and behave in the UI
   (drag, drop, remove) like the X/LinkedIn ones. Each carries a small `Preview · not connected` label; nothing
   publishes, reads or authenticates. ⑥ Launch lists placed preview-only accounts as `Not published in v1` and does
   not block Start on them. Real connectors stay separate backlog items. v1 still PUBLISHES to X + LinkedIn only.
2. **Home Needs-you: the column replaces the section** (Dave's call, reversible, per the §11.5 Q2 recommendation).
