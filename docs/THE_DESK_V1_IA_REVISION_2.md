# The Desk v1: IA revision 2 (campaign map: Goal, How, What, When, Where, Launch)

**Status:** DRAFT design revision, 2026-09-29. MC-977 / backlog 8f64d565. Docs only, no code changed. Becomes
binding when Ron answers §9 (three questions, each with a recommendation the tickets already assume). §9 answered
2026-09-29. **§10 (outcome learning loop, Ron's item 9) added 2026-09-29**: tickets R2-14 to R2-17 + R1-L, two open
questions in §10.8.
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
 ├─ THE MAP:  ① Goal  ›  ② How  ›  ③ What  ›  ④ When  ›  ⑤ Where  ›  ⑥ Launch
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
| **② How** (items 5, 6, 8) = strategy | angle (the through-line), strategy in words (who, what argument, why these channels, what never to claim; defaults from `presence.strategy`), the agent to plan with (picker, default `presence.desk_agent`), **optional budget** (§5.2), `Suggest What / When / Where` action | angle | IA4 step 2's plan task; the rules popover's Paid group; `presence` strategy as the inherited default |
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
| `how` | `{strategy, angle, agent?: <character id>, budget?: {source:'project'\|'own', amount, period:'term'}}` | `plan.angle` moves here; `agent` overrides the project default for this campaign only |
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
| **R2-2** | Home status board (grouped by project, stage + goal pace + next post + needs-you per row) + Desk-header project picker on every Desk route; retire IA1 project cards and Home drop targets | R2-1, R2-4 | `desk-v1-home.mjs`: both projects' groups render; camp with 11/30 at 50% elapsed reads `Behind`; picker → Engulfing scanner lands on its project page from a campaign page in one click; untracked goal row reads `not measured`, never `0 of` |
| **R2-3** | Campaign map frame: ① to ⑥ stepper replacing the tab strip; glyph + word per stop; lifecycle pill; `Next ›`/`‹ Back` in Draft; any stop clickable; ⑥ lists `missing` with links; retire IA4 steps 1 to 3 and the rules popover; `PANEL_ALIASES` extended | R2-1 | new `desk-v1-map.mjs`: New campaign → map at ①; Next through ⑥ with defaults; leave at ④ → row `Draft · at When` → Continue lands on ④; ⑥ Start disabled with 2 missing items, each link lands on its stop; old `results`/`content`/`calendar` deep links land on ①/③/④ |
| **R2-4** | ① Goal: measurable goal editor (metric, target, baseline, horizon, deadline, source incl. manual entry) + effectiveness panel (progress, pace, cost per outcome, per-term rows); absorbs `desk-v1-results.js` | R2-3 | `desk-v1-results.mjs` → ① checks: manual entry 14 dated today updates progress and pace; `long` horizon shows term rows; source removed → `⚠ Not measured` |
| **R2-5** | Agent of choice: kit agent box reads name/avatar from `presence.desk_agent` (or `how.agent`) via `/api/characters`; step 0 asks it; every literal "Posy" in desk-v1 UI copy becomes the agent's name | R2-1 | `desk-v1-kit.mjs`: presence agent = Claydo → box header, placeholder and Working/Failed strings say Claydo; `rg -n "Posy" static/js/desk-v1-*.js` hits comments only; an unresolvable agent id shows `Pick who plans for this project ›`, not a broken box |
| **R2-6** | ② How: strategy + angle + agent picker + optional budget (none / project earmark / own) + `Suggest What / When / Where` task writing `? suggested` items into ③ ④ ⑤ | R2-3, R2-5 | new `desk-v1-how.mjs`: Suggest → Ready → ③ shows `3 suggested`, ④ a cadence proposal, ⑤ a placement; Accept all on ③ creates 3 `◇ Planned` pieces; forced Failure → Retry; budget own $50 on an Active campaign flips ⑥ to `Awaiting approval`, lowering to $40 does not |
| **R2-7** | ③ What: pieces list + filters + create menu + Material tray drag; piece drill-in (Copy · Media · Versions), review 12b stays a piece child; retire IA5 facets | R2-3 | `desk-v1-piece.mjs` rewritten: ③ → piece → Back reads `‹ <campaign> · What`; Versions row time edit shows on ④ calendar the same day; drag an asset onto a piece attaches it with Undo. `desk-v1-review.mjs` green |
| **R2-8** | Studio: standalone content-creation route (video director + image) in the Desk header; `returnTo` round trip from ③ with agent-pre-filled storyboard; render attaches to the piece | R2-7 | `desk-v1-video.mjs` extended: Studio from header saves to Material; from ③ `Make one` opens with storyboard pre-filled (≥1 scene from the piece copy), edit, Render, `‹ Back to What` shows the piece `⟳ Rendering` |
| **R2-9** | ④ When: cadence (≤ project ceiling, clamp note), term start/end/post cap, calendar + list, `Unscheduled` tray drag onto a day | R2-3 | `desk-v1-calendar.mjs` → ④ checks: cadence 5 under ceiling 3 → `≤3/wk · from <project>`; drag an unscheduled piece onto Thu sets its version time; ending earlier keeps approval, later voids it |
| **R2-10** | ⑤ Where: account columns from the presence binding, on/off = `accounts[]`, drag piece between `Unplaced` and columns (create/move version), Channels tray, `Connect another ›` to Presence; retire the campaign Add tray | R2-3 | new `desk-v1-where.mjs`: drag a piece onto LinkedIn creates a version with the Clayrune voice; switching 𝕏 off on an Active campaign keeps approval (narrow), switching it back on voids it; keyboard move (no drag) works |
| **R2-11** | ⑥ Launch: bounds table incl. goal, term, budget; Start; Live state; Pause/Resume; `Renew term` for long horizons (new approval, same bounds shown) | R2-4, R2-6, R2-9, R2-10 | `desk-v1-map.mjs`: Start with defaults → `Live since`; long goal at term end → `Renew term` → term 2 with a new approval record; project budget cut below earmark clamps and logs |
| **R2-12** | Engagement menu landing: per-project bundles (unread / awaiting / total, channel split, latest line, `Not connected` state); remove campaign Conversations tab + embed; `read_at` | R2-1 (parallel), merges after R2-3 | `desk-v1-engagement.mjs`: 2 bundles, Clayrune `5 unread`; opening a thread drops it to 4; `Open ›` shows IA7 lanes filtered to that project; campaign page has no Conversations tab; a project with no feed reads `Not connected`, never `0 unread` |
| **R2-13** | Acceptance journey + exit gate on the new path | R2-2 to R2-12, R2-14 to R2-17 | `desk-v1-journey.mjs`: Home → picker → Engulfing scanner → New campaign → ① long goal → ② talk + Suggest (forced failure + Retry) → accept → ③ Make video in Studio → back → ④ drag → ⑤ drag → ⑥ Start → Home row `Active · on track` → **term end (fixture clock) → Needs-you `Retro ready` → ① paste per-post numbers → Confirm one finding, Reject one → Renew term → ② Suggest shows `Based on F<n> ›` and never the rejected one** → Engagement bundle → Delete a never-started Draft (Undo). `desk-v1-exit.mjs` exit 0 incl. map, how, where, studio, **retro, playbook** × 3 tones |
| **R1-P** (amend) | backend presence/campaign/piece: add `goal` shape, `term`, `how.budget`, earmarks, `desk_agent`; migration maps `production → budget`, `outcome → metric`, `setup → map` | R2-1 merged (shapes frozen) | tests: earmark sum ≤ project budget enforced at the route; budget raise changes `bounds_hash`; migration idempotent |
| **R1-A** (new) | backend agent of choice: desk routes resolve `presence.desk_agent` instead of `global:social-media-strategist` (`desk_routes.py:254`, `:453`); unresolvable → structured error naming the project, never a silent default | R1-P | tests: a presence naming `global:claydo` dispatches Claydo; a missing character returns 409 with `pick_agent`; no route string contains `social-media-strategist` |
| **R1-E** (amend) | engagement feed + `read_at` + per-project aggregates for the landing; **(§10.7) writes per-post outcome entries `source:'feed'` where the platform exposes them, each read costed against the budget** | R1-P, R1-L | tests: per-platform coverage gap → bundle `Not connected`; aggregates match lane counts; a feed entry never overwrites a typed entry for the same post + metric (both kept, source shown); a platform with no metrics read leaves per-post cells `No per-post numbers yet`, never 0 |
| **R2-14** | Outcome loop fixtures + kit shapes (§10): ledger row `piece_id/format/account/term/cost`, per-post `outcomes[{metric, value, at, source}]`, `retro` object, `finding` object + states, rejection record; kit `retroVerdict()` implementing §10.1's sample-size table | R2-1 | `desk-v1-kit.mjs`: arms 6 vs 4 posts → `Too few posts to tell (6 and 4; need 10 each)`; 12 vs 12 with 1.2× gap → `No clear difference`; one post = 60% of an arm → `One post drives this`; platform row reads `Platform + voice` with the can't-separate note; fixture load has 0 findings with `state:'confirmed'` and `origin:'unattended'` together |
| **R2-15** | ① Retro section: per closed term goal vs actual, spend + cost per outcome, dimension table with verdicts, per-post number grid (paste from CSV), proposed findings with Confirm / Edit / Reject / Don't suggest again; `Run retro now` = Interim, no findings; Needs-you row `Retro ready: n findings to confirm` | R2-4, R2-14 | new `desk-v1-retro.mjs` × 3 tones: closed term shows `Judged on clicks per post, not on signups`; paste 12 rows fills the grid; Interim shows 0 proposed findings; Confirm F3 removes the Needs-you row; Edit wording then Confirm stores `edited_text` and `origin:'interactive'`; Reject then re-run retro on the same evidence proposes nothing; a finding whose `maybe_why` says "raise the cadence" renders without that line |
| **R2-16** | Project page Playbook: confirmed findings by dimension (sentence, confidence, n, campaign links), Stale sub-list with Re-confirm / Retire, rejected collapsed with `Undo reject`; no Home line (Needs-you only) | R2-15 | `desk-v1-project.mjs` extended: 2 confirmed + 1 stale + 1 rejected fixture render in their groups; campaign link lands on that campaign's ①; Undo reject moves the finding back to `proposed`; Home has no playbook line |
| **R2-17** | How agent cites the playbook: Suggest results carry `because`; `Based on F3 ›` / `Trying: untested` chips on ③ ④ ⑤ suggestions; unknown or non-confirmed ids dropped | R2-6, R2-16 | `desk-v1-how.mjs` extended: with F3 confirmed, the ④ Tue 09:00 suggestion shows `Based on F3 ›` and opens F3's evidence; a suggestion citing rejected F5 or unknown F99 shows no chip; with a covered dimension, ≥1 of 5 suggested pieces is labelled `Trying`; accepting an F3-based slot on an Active campaign within bounds keeps approval |
| **R1-L** (new) | backend outcome loop: ledger rows gain `piece_id/format/account/term/cost`; per-post `outcomes[]` replaces the free-form `outcome` dict (migration keeps old values as one `source:'manual'` entry); `mc/desk_retro.py` computes the retro deterministically at term/campaign end (model words summary + `maybe_why` only); `store['playbook']` + state transitions (human-only routes); rejection records + `evidence_key`; `playbook_brief()` in the Suggest brief (R1-A's dispatch); `distiller.authority_violation` made public and applied with the Desk bounds pattern | R1-P, R1-A | tests: retro on a fixture term reproduces R2-14's verdicts byte-for-byte; a retro run from a steward/scheduled context creates only `proposed, origin:unattended` findings; `playbook_brief()` contains confirmed findings only (proposed/rejected/stale absent); same evidence after Reject proposes nothing, +10 posts from a new campaign re-proposes with the prior rejection date; no finding route accepts a field naming a bound (`cadence`, `budget`, `accounts`, `approval`, `end`, `post_cap` → 400); the state-change routes refuse an unattended caller; `data/desk.json` stays outside `DATA_DIR` |

Order: R2-1 → R2-3 → then R2-4, R2-5, R2-7, R2-9, R2-10 build in parallel and **merge one at a time** in that order
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
