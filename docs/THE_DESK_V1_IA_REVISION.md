# The Desk v1: IA revision (Project → Campaign → Piece, plus Engagement)

**Status:** BINDING design revision, 2026-09-28. MC-977 / backlog 8f64d565. Docs only, no code changed.
**Owner:** Merrin (plan). Builders per ticket, §5. Measured against master `4511c4a`.
**Amends:** `THE_DESK_V1_UX_PASS.md` §1, §3, §4, §7, §8, §9 (tickets T4 to T8 are **ON HOLD** and replaced by §5
below) and `THE_DESK_V1_UI.md` §1, §2. Everything else in those docs (Posy task lifecycle §5, lifecycle glyphs,
"no label without a value", review 12b, video director, drag rules, vocabulary §9) still holds.
**Unchanged authority:** behaviour, permissions and approval bounds follow `THE_DESK_SIMPLIFICATION_PLAN.md` §4 and
the standing positions (per-campaign release 2026-09-22; X + LinkedIn only; voice split Ron/X, Clayrune/LinkedIn).

## Ron's ask (2026-09-28, verbatim)

> I think the entire interface is wrong in the sense that it treats a feature as full campaign of its own instead of
> treating the entire project as the campaign. Everything else inside is a promotion of feature or a story about
> artifact of that project. So the view inside should be project --> campaign promoting specific project / product /
> feature / target --> etc. Then inside each of these artifacts comes the what, how (Video editing, articles,
> pictures and overall media generating comes here), when and where. Then a unified dashboard to view all
> interactions / automated responses / suggested responses and so on.

The fixtures prove his point: `camp-2` "Restore points launch" (`desk-v1-fixtures.js:552`) is one feature of Clayrune
dressed as a whole campaign, and it re-declares the accounts, voices, cadence, reply mode and source project that
`camp-1` already declared for the same product (`:42` vs `:569`).

## Dave's reading, checked against the code

The three levels plus Engagement hold. Four corrections, each forced by something already in the code or a standing
position:

| # | Dave's reading | Correction | Why (evidence) |
|---|---|---|---|
| K1 | Project owns connected channels/accounts and voices | The **account and its learned voice are workspace assets**; the project owns the **binding** (which accounts it uses, which voice each carries for this project) | `@ron` on X is one account used by any project Ron promotes. Voice learning is keyed per voice (`mc/desk.py:451` `record_edit`, `:489` `voice_brief`); per-project copies would fork the learning loop. Voices already carry `scope: global \| <project_id>` (`desk.py:319,372`), so a Clayrune-page voice can be project-scoped and Ron's voice stays global. |
| K2 | Cadence ceilings set once per project | Ceilings stack: **account ceiling ≥ project ceiling ≥ campaign cadence** | Two projects each at "≤3/wk on @ron" put 6/wk on one account. The account ceiling is the only one that protects the account. Q1. |
| K3 | Release/review policy set once per project | **Approval stays per campaign.** The project holds the ceilings and defaults a campaign inherits; a campaign may only narrow them | The standing position requires a **bounded** approval (end date and/or post cap). A project has no end, so a per-project approval would be open-ended, which the position forbids. Q2. |
| K4 | Engagement filterable by project/campaign/channel | A conversation always has a **project**; its **campaign is optional** | A mention or discussion of the product in the wild belongs to no campaign. Today every conversation requires `campaignId` (`desk-v1-conversations.js:22,104`); that cannot represent it. |

---

## 1. Hierarchy and navigation

```
Desk Home  (all projects; not a switcher)
 ├─ Project cards  (one per project with a presence)
 ├─ Needs you  (decisions across everything; each row deep-links)
 ├─ 💬 Engagement · n   → Engagement dashboard (§7), top level, peer of Home
 └─ Shelves: Channels · Material

Project page  ‹ Desk
 ├─ Header: project name · presence state · ⏸ Pause project · ⚙ Presence (settings)
 ├─ Campaigns: cards by subject (◉ project · ▣ product · ✦ feature · ◎ audience/event) + ＋ New campaign
 ├─ Needs you (this project)  ·  Engagement strip (this project, 3 rows + Open ›)
 └─ Posy box scoped About: <project>

Campaign page  ‹ <project>
 ├─ Header: subject chip · title · state · ⏸ Pause/▶ Resume · More ▾
 ├─ Draft/Proposed: Setup (3 steps, §2.3)
 └─ Active+: tabs Overview · Pieces · Conversations · Results   (T2 frame, panels in place)

Piece page  ‹ <campaign>
 ├─ Facets: What · How · When · Where   (panels in place, same frame pattern as T2)
 ├─ Full-width review (12b) and video director (12d) are child routes of the piece, ‹ <piece>
 └─ Posy box scoped About: <piece> ▾

Presence settings  ‹ <project>      (project-level fields, §2.1)
Engagement dashboard  ‹ Desk        (filters: project · campaign · channel · state)
```

- Back is always `‹ <parent name>`. Route parents: `project → home`, `campaign → project`, `piece → campaign`,
  `review/video → piece`, `presence → project`, `engagement → home`. The Needs-you deep link into a review builds the
  full stack (Home, project, campaign, piece, review) so Back walks up one level at a time.
- **Home's `Projects: All ▾` filter (UX_PASS decision 1) becomes navigation.** "One Desk, all projects" still holds;
  the filter's job moves to the project cards. Home still aggregates Needs you and the Engagement count across all
  projects. Zero projects with a presence: Home shows only `Promote a project ›` (§2.1 creates the presence).
- **Where Engagement lives:** one dashboard, route `engagement`, entered from Home's header count, a project page's
  strip (pre-filtered to the project) or a campaign's Conversations tab (the same view, pre-filtered, embedded). There
  is no second conversations surface.
- **Promote box on Home** keeps one input. Its result is now a Draft **campaign inside a project**: Posy proposes the
  project and the subject from the text (`? Assumed`, correctable at setup step 1). If the named project has no
  presence yet, setup starts with the presence (§2.1) and continues into the campaign.
- **The ongoing presence is a campaign whose subject is the project itself** (`subject.kind = 'project'`), renewed
  every term via Renew. That answers UX_PASS Q1 (b) structurally, so Q1 is closed.

## 2. What lives where

### 2.1 Project: the standing presence (set once, edited in Presence settings)

| Field | Meaning | Default |
|---|---|---|
| `project_id` | The Clayrune project (same id as `data/projects/<id>.json`) | from the promote text or Home |
| `accounts[] {channel_id, voice}` | Which workspace accounts this project may publish through, and the voice each carries **for this project** | 𝕏 → Ron (first person); LinkedIn → Clayrune page voice (2026-09-14 split) |
| `audience` | Who the project is for, in words | Posy's read of the project |
| `strategy` | The standing angle for the project: what it is, what it never claims, proof points | Posy draft from the repo/README, `? Assumed` until edited |
| `ceilings {per_week, min_gap_h}` per account | Project ceiling, ≤ the account ceiling | 3/wk, 12 h |
| `replies` | Reply policy for everything on this project's posts and mentions | `Drafted for your review` |
| `production {per_job, per_period, period, kinds[]}` | Media budget and which generation kinds are allowed | `$0`, no video generation |
| `measurement[]` | Where outcomes are measured (conversion event, source) | none (goal shows `⚠ not tracked yet`) |
| `visual_default` | Default visual requirement | a real product screenshot (`desk.py` `DEFAULT_VISUAL_REQUIREMENT`) |
| `state` | `▶ Active` / `⏸ Paused` (pauses every campaign in it) | Active |

Widening a project field (more accounts, higher ceiling, bigger budget, replies beyond drafts) is human-only
and **never widens a running campaign**: its approval hash is unchanged, so it keeps its approved, narrower bounds.
Raising a campaign to the new project value is a campaign edit and needs a new campaign approval. **Narrowing** a
project bound below a running campaign's approved value clamps that campaign at once and logs it; it stays Active.

### 2.2 Campaign: a promotion of one subject

`subject {kind: project | product | feature | audience | event, ref?, label}`, `title`, `brief`, `goal {outcome,
target?, deadline?}` (`tracked` derived from project `measurement`), `angle`, `accounts[]` (subset of the project's,
default all), `cadence.per_week` (≤ project ceiling), `end {date?, post_cap?}`, `paid` (Off in v1), `extra_sources[]`
(other projects to draw on), `setup {step, done[]}`, `approval {bounds_hash, approved_via, approved_at}`.
Spend and stop conditions stay derived.

### 2.3 Campaign setup shrinks from 4 steps to 3

Step 2 of the UX pass ("Destinations + voice") was the project's job being done per campaign.

| Step | Asks | Required to leave |
|---|---|---|
| **1 Subject + goal** | What this promotes (subject chips + brief, `📎`/`🔗`), short title, outcome | subject, brief, title, outcome in words |
| **2 Plan** | `Draft the plan` (Posy task, UX_PASS §5): angle, 3 **planned pieces** (not "samples"), accounts subset (pre-ticked from the project), cadence, end | ≥1 planned piece |
| **3 Review + start** | Bounds table (UX_PASS §3.1) with inherited rows marked `from <project>` and a `Change for the project ›` link | `validatePlan` ok |

If the project has no presence yet, a **Presence** step 0 runs first (accounts + voices, audience, ceilings), once.

### 2.4 Piece: four facets

A piece is today's content family (`fam-*`, CNT-01). Its versions become the Where facet.

| Facet | Holds | Today's surface it absorbs |
|---|---|---|
| **What** | message, angle for this piece, copy per version, claims + sources, `? Assumed` notes | content card body, review 12b text, `proposedExtras.assumptions` |
| **How** | production: article writing, video (director 12d, render card, budget), images/screenshots, material attached | video intake/director, Material drop onto a card |
| **When** | publish time per version, calendar position, hold window | calendar chip, `publishAt` on versions |
| **Where** | one row per destination version: account + voice + format + state | versions list on the content card |

## 3. Field-by-field move table

Every field the campaign carries today: the fixture campaign object (`desk-v1-fixtures.js:16-62`, `:552-586`),
`camp.plan` (UX_PASS §4), `validatePlan` bounds (`desk-v1-kit.js:703`), setup steps 1 to 4 (UX_PASS §3/§3.1), rule
chips (`desk-v1-campaign.js:63`, `desk-v1-rules.js:38`), the rules popover (`desk-v1-rules.js:509`), the Start sheet
(`:266`), and the backend record (`mc/desk.py:796`). **C** stays on campaign · **P** moves to project · **Pc** moves
to piece · **S** split · **R** retired.

| # | Field (where today) | To | New shape / note |
|---|---|---|---|
| 1 | `id` | C | unchanged |
| 2 | `name` (fixture) | R | duplicate of `plan.title`; title is the only copy |
| 3 | `plan.title` / setup step 1 title | C | |
| 4 | `state` | C | project gets its own presence state (§2.1) |
| 5 | `goal.current` | C | derived from Results, never stored |
| 6 | `channelIds` | R | duplicate of `plan.destinations`; replaced by `accounts[]` subset (#18) |
| 7 | `rules.reviewMode` + chip "You approve each piece" + popover group + Start-sheet row | R | contradicts the per-campaign release position; no replacement |
| 8 | `rules.frequencyPerWeek` + `≤N/wk` chip | R | duplicate of `plan.cadence.per_week` (#21) |
| 9 | `rules.repliesMode` + chip | P | `project.replies` (#24) |
| 10 | `rules.paid` + Organic/Paid chip | R | duplicate of `plan.paid` (#25) |
| 11 | `rules.customChips` (INS-02 durable instructions) | S | scope-tagged: campaign by default, `Apply to project` moves it to `project.strategy` |
| 12 | `policyRecord` (set by `_startCampaign`, `rules.js:346`) | C | becomes `approval {bounds_hash,…}` per SIMPLIFICATION §4 |
| 13 | `plan.brief` / backend `thesis` | C | backend `thesis` maps to `brief` |
| 14 | `plan.source_projects` / setup "owning project" / backend `project_ids` / `validatePlan` bound | S | owner = parent project (implicit, bound retired); other projects → `extra_sources[]` on campaign |
| 15 | setup "Also draw on" | C | `extra_sources[]` |
| 16 | `plan.audience` / setup "who should care" | S | `project.audience` default; campaign sets it only when `subject.kind = audience` |
| 17 | `plan.goal {outcome, target, deadline}` / setup outcome chips | C | |
| 18 | `plan.goal.tracked` | P | derived from `project.measurement` |
| 19 | `plan.destinations[].account` / setup step 2 destinations / `validatePlan` bound | S | pool → `project.accounts`; campaign keeps `accounts[]` subset (default all) |
| 20 | `plan.destinations[].voice` / setup step 2 voice / backend `voices`, `voice` | P | `project.accounts[].voice`; learning stays per workspace voice (K1) |
| 21 | `plan.cadence.per_week` / step 3 cadence / `validatePlan` bound | C | ≤ project ceiling ≤ account ceiling (K2) |
| 22 | `plan.cadence.min_gap_h` | P | per account in `project.ceilings`; spacing protects the account, not the campaign |
| 23 | `plan.end {date, post_cap}` / step 3 end / `validatePlan` bound / "Ends" chip | C | the bound that keeps approval finite |
| 24 | `plan.replies` / step 4 Replies row | P | `project.replies` |
| 25 | `plan.paid` / step 4 Paid row / popover Paid group | C | Off in v1; bounded by the project spend ceiling |
| 26 | `plan.generation` / step 4 Generation row | P | `project.production.kinds`; the job itself lives in piece How |
| 27 | popover "Production budget" (`renderBudget`, global fixture) | P | `project.production.per_job/per_period` |
| 28 | popover "Channels" include/exclude | S | project: which accounts exist for it; campaign: subset |
| 29 | `plan.angle` / step 3 angle / backend `agenda` | C | the campaign's through-line; each piece may refine it in What |
| 30 | `plan.samples[]` / step 3 "3 sample posts" | Pc | become real pieces in `◇ Planned` (What filled, When empty) |
| 31 | backend `planned[]` | Pc | same as #30 |
| 32 | backend `visual` | S | `project.visual_default`; override in piece How |
| 33 | `proposedExtras.blocker` (Posy's one question) | C | setup only |
| 34 | `proposedExtras.assumptions` (per family) | Pc | piece What |
| 35 | step 4 Spend ceiling (derived) | C | derived per campaign, checked against project |
| 36 | step 4 Stop conditions (derived) | C | derived |
| 37 | `camp.setup {step, done[]}` (spec'd, not built) | C | 3 steps now (§2.3) |
| 38 | step 1 "owning project" picker | R | the campaign is created inside a project; picker only on the Home promote path |
| 39 | step 2 `＋ Connect` | P | Presence settings and Channels shelf |
| 40 | new: `subject` | C | the field Ron's ask turns on |

**Top-line counts (40 rows):** stays on campaign **17** · moves to project **8** · moves to piece **3** · split
project/campaign(/piece) **6** · retired **6**. Of `validatePlan`'s 4 required bounds, 2 stay campaign (cadence,
end), 1 becomes a project check (destinations pool non-empty; campaign subset non-empty), 1 retires (source project).

## 4. What already merged survives

| Merged | What | Verdict | Change |
|---|---|---|---|
| **T1** `06a91d0`, `d3a7817` | `camp.plan` object (fixtures `:42`, `:569`) | **Rescope** | split into `project.presence` + a thinner `camp.plan` per §3; one canonical copy per level, no duplicates |
| | `validatePlan` (`desk-v1-kit.js:703-719`) | **Keep, rescope** | add `validatePresence(project)`; `validatePlan(plan, project)` computes effective bounds (inherit + clamp); `source_projects` bound removed; `missing[].step` renumbered to §2.3 |
| | `_ruleChipsFor` (`desk-v1-rules.js:38`), `_ruleChips` (`desk-v1-campaign.js:63`) | **Rescope** | chips read effective bounds; inherited chips marked `from <project>`; review-mode chip deleted |
| | `deskV1FillProposedSummary` (`rules.js:110`), `deskV1OpenStartSheet` (`:266`) | **Retire** | were already slated for T5; replaced by setup step 3. Start sheet still prints "Starting doesn't approve any piece" and `—` fallbacks today (`:306-313`), both retired copy |
| | `_startCampaign` (`rules.js:346`) | **Keep** | writes `approval` instead of `policyRecord` |
| | rules popover (`rules.js:403`, body `:509`) | **Rescope** | splits: campaign keeps cadence/end/accounts subset/paid; replies, production budget, voices move to Presence settings; review mode deleted |
| | goal-date edit writes `plan.goal.deadline` + `plan.end.date` | **Keep** | |
| **T2** `750ec21`, `bae7bc8` | persistent frame + in-place panels (`desk-v1-shell.js:45` aliases, `:92` `_gotoCampaignPanel`, `:174`, `:207`) | **Keep** | same pattern reused for the project page and the piece page |
| | `ROUTES.campaign.parent = 'home'` (`shell.js:21`) | **Rescope** | parent becomes `project`; `_campaignLabel` unchanged; add `project`, `piece`, `presence`, `engagement` routes |
| | Pause/Resume, `_openResumeSheet` (`desk-v1-campaign.js:212`), `_planExpiryReason` (`:187`) | **Keep** | also runs when a project is resumed (each paused campaign rechecked); Resume's fallback to the rules popover (`:228`) becomes setup step 3 |
| | Delete/Archive (`campaign.js:252-345`) | **Keep** | project-level Archive is out of v1 |
| | `conversations` panel alias | **Rescope** | the panel embeds Engagement filtered to the campaign (§7) |
| **T3** `6dc6acf`, `34c5e82` | draft/ask store `_posyDrafts` (`desk-v1-kit.js:411`), `bindPosyBox` (`:631`), `anyPosyWorking` (`:593`), `window.actIndicatorHTML` (`conversation.js:6070`) | **Keep** | untouched lifecycle |
| | draft keys: `campaign:<id>:<scope>:<label>` (`campaign.js:936`), `review:<vid>` (`review.js:464`), video (`video.js:472`) | **Rescope** | prefix every key `project:<pid>:`; replace the campaign key's `label` with a stable target id (UX_PASS §5 already required ids, never labels). Home's project card calls `anyPosyWorking('project:<pid>:')` |

## 5. Tickets (replace UX_PASS §9 T4 to T8). One builder each, in order, merge one at a time, run the named smokes after EACH merge

| # | Ticket | Depends on | Acceptance (smoke; all 3 tones where it renders UI) |
|---|---|---|---|
| **IA1** | Fixtures: `PROJECTS` (clayrune, engulfing_scanner) with presence records; campaigns gain `projectId` + `subject`; `camp-1` → subject audience "Windows users trying Claude Code", `camp-2` → subject feature "Restore points"; engulfing_scanner gets 1 Active campaign + 1 Needs you item; conversations gain `projectId`, `campaignId` optional. Routes `project`, `presence` (stub), `engagement` (stub), `piece` (stub); campaign parent → project; Home shows project cards | T1–T3 on master (done) | new `desk-v1-project.mjs`: Home → project card → campaign → Back reads `‹ Clayrune` then `‹ Desk`; Needs-you deep link into a review builds the 5-deep stack. `desk-v1-home/campaign/rules/kit.mjs` green |
| **IA2** | Split `camp.plan` per §3; `validatePresence` + `validatePlan(plan, project)` with inherit + clamp; delete `rules.reviewMode/frequencyPerWeek/repliesMode/paid`, `channelIds`, `name`; rule chips from effective bounds; T3 draft keys prefixed + stable ids | IA1 | `desk-v1-rules.mjs`: campaign cadence 5 under project ceiling 3 → effective 3, chip `≤3/wk · from Clayrune`; plan missing end → `missing` names it with step 2; `rg "reviewMode\|channelIds\|proposedDetail" static/js/desk-v1-*` = 0; `desk-v1-kit.mjs` ask survives project → campaign → project nav |
| **IA3** | Presence settings page: accounts + voice per account (with one-line sample), audience, strategy, ceilings, replies, production budget, measurement; project Pause; narrowing clamps running campaigns and logs it | IA2 | `desk-v1-project.mjs`: lower project ceiling 3 → 2 clamps camp-1 chip to `≤2/wk` with a log line; adding an account requires the confirm sheet ("widens"); project Pause pauses both clayrune campaigns, Resume runs each through `validatePlan` |
| **IA4** | Campaign setup, 3 steps (§2.3) + step 0 Presence when missing; Draft → Proposed → Active; planned pieces created as `◇ Planned` pieces; replaces the old `deskV1OpenStartSheet`/Proposed summary | IA2, IA3 | new `desk-v1-setup.mjs`: New campaign in engulfing_scanner → accept defaults → Proposed with 3 Planned pieces → step 3 shows 0 `—`, inherited rows labelled `from Engulfing scanner` → Start → Active; leave at step 2 → card `Setup 2 of 3` → Continue lands on step 2; zero connected accounts completes via `✋ You publish it` |
| **IA5** | Piece page: facets What · How · When · Where as in-place panels; review 12b and video 12d become piece children (`‹ <piece>`); content card primary action opens the piece | IA1 (builds parallel to IA2–IA4, merges after IA4) | new `desk-v1-piece.mjs`: card → piece → switch 4 facets keeps header + Posy DOM node; What → Review → Back reads `‹ <piece title>`; How → video director → Back returns to How. `desk-v1-review/video/calendar.mjs` green |
| **IA6** | Operating view: campaign Overview panel + Active empty states (old T6); project page Overview (next post across campaigns, Needs you, Engagement strip) | IA4, IA5 | `desk-v1-campaign.mjs`: fresh Active shows no `0/` bar, Results/Conversations show UX_PASS §6.1 copy; `desk-v1-project.mjs`: project Next post = earliest across its campaigns; lint A12 clean |
| **IA7** | Engagement dashboard v1 (§7): route `engagement`, filters project · campaign · channel · state, three lanes; campaign Conversations tab embeds it filtered; Home header count | IA1 (builds parallel), merges after IA6 | new `desk-v1-engagement.mjs`: Home count = sum of Suggested + Needs you across both projects; project filter hides the other project's rows; a mention with no campaign shows under the project with `No campaign`; campaign tab shows only that campaign's rows (same DOM component). `desk-v1-conversations.mjs` green |
| **IA8** | Acceptance journey + exit gate + greyscale shots (old T8, new path) | IA1–IA7 | new `desk-v1-journey.mjs`: Home → **engulfing_scanner** → new feature campaign → setup interrupted at step 2 → resume → forced Posy failure + Retry → Start → open a piece → What/How/When/Where → Pause project → Resume → Engagement filtered to that project → Delete a never-started Draft (Undo). `desk-v1-exit.mjs` exit 0 incl. project, presence, setup, piece, engagement × 3 tones |
| R1-a | `/ask` backend (UX_PASS §5, unchanged) | R1 start | as before |
| R1-P | Backend presence store + campaign subject + piece facets + migration (§6) | IA2 merged (shapes frozen) | tests: v1→v2 store migration idempotent; unassigned campaign surfaces a Needs-you row, never a guessed project; widening a project bound never changes a running campaign's `bounds_hash` |
| R1-E | Engagement feed: inbound read per platform, suggested replies store, sent log | R1-P | tests: coverage gaps reported per platform; empty feed never reads as "no one is talking" |

UX_PASS T4 (setup 1–3) → IA4; T5 (step 4) → IA4; T6 → IA6; T7 (Home filter, Create, New card, zero Home) → IA1 + IA4;
T8 → IA8. The 5-user test (UX_PASS §9 footer) runs after IA8, with one added probe: each user says which project a
campaign belongs to and where to change the reply policy.

## 6. Data model deltas (R1-P; R0 carries the same shapes in fixtures)

`mc/desk.py` today: one store (`_empty_store`, `:114`) with `voices`, `campaigns`, `ledger`, `proposals`, `platforms`;
campaign = `{title, thesis, agenda, voices, voice, project_ids, planned, visual, state}` (`:796`), states
`proposed/running/paused/done/dropped` (`:743`); routes `/api/desk/campaigns` GET/POST/PATCH/DELETE
(`desk_routes.py:306-345`), `/api/desk/overview` (`:715`). No project-level record exists.

| Delta | Shape | Where |
|---|---|---|
| **Presence record** (new) | `store['presences'][project_id] = {project_id, accounts:[{channel_id, voice}], audience, strategy, ceilings:{<channel_id>:{per_week, min_gap_h}}, replies, production:{per_job, per_period, period, kinds}, measurement:[], visual_default, state, updated_at}` | `desk.py`: `get/upsert_presence`, `presence_effective_bounds(project_id)`; routes `GET/PUT /api/desk/projects/<pid>/presence`. Widening PUT is human-only via `mc.unattended.is_unattended_caller` (same gate as SIMPLIFICATION §4) |
| **Account record** (gap map #10, now required) | `store['accounts'][channel_id] = {platform, identity, capability, health, ceiling:{per_week, min_gap_h}}` | v1 only `x`, `linkedin` direct; anything else is `manual` |
| **Campaign** | add `project_id` (one, required), `subject {kind, ref, label}`, `goal`, `accounts[]`, `cadence`, `end`, `extra_sources[]`, `setup`, `approval`; keep `title`; `thesis` → `brief`, `agenda` → `angle`; drop `voices`/`voice` (derived from project binding), `planned` (→ pieces), `project_ids` (→ `project_id` + `extra_sources`) | `create_campaign` requires `project_id` + `subject`; `update_campaign` allowed set updated; `campaign_platforms` (`:771`) reads the presence binding |
| **States** | `draft, proposed, active, paused, completed, archived` (map `running→active`, `done→completed`, `dropped→archived`) | `CAMPAIGN_STATES` |
| **Piece** (new, replaces `planned` strings and the per-project `social_queue` row as the unit) | `{id, campaign_id, project_id, kind, title, what:{angle, assumptions[], claims[]}, how:{assets[], jobs[], visual}, versions:[{id, channel_id, voice, format, state, revision, publish_at, approval}]}`; When = `versions[].publish_at` | `store['pieces']`; `social_queue` rows keep working for the legacy Desk (MIG-04) until R1 cutover |
| **Conversation** (new) | `{id, project_id, campaign_id?, piece_id?, channel_id, source, state, suggested_reply?, sent_by: you\|auto\|null}` | R1-E |

**Migration note.** Store `version` 1 → 2, lazily on read, idempotent. Live store today has **1 campaign**
(`running`, `project_ids: []`, voices `personal`/`product`) and 0 ledger rows. Rules: `project_ids[0]` becomes
`project_id`; an **empty** `project_ids` gets `project_id: null` and a Needs-you item "Pick the project for <title>"
(never a guessed project); `subject` defaults to `{kind:'project', ref: project_id}`; `voices` seed the presence's
`accounts` binding for that project if it has none; `planned[]` strings become `◇ Planned` pieces; `visual` becomes
the piece How override only when it differs from the default; states map as above. Old fields stay readable for one
release, then drop. The one live campaign is `running` without an approval record, so it migrates to `proposed`
(publishing requires an approval hash that never existed; this changes nothing today because nothing publishes yet).

## 7. Engagement dashboard

**Exists today:**

| Source | Where | Fit |
|---|---|---|
| Conversations surface with source switch (our posts / mentions / discussions), reason lines, thread, Send (simulated) / Revise / Ignore / Assign / Take over | `desk-v1-conversations.js` (`SOURCES :35`, row `:125`, list `:142`) | the core of v1; already has an all-campaigns scope (`_inScope`, `:103`) |
| Coverage-gap footer | `desk-v1-conversations.js:151`, fixture `conversationCoverageGaps` | keep; now per project |
| Home Needs-you "N replies waiting" | `desk-v1-home.js:28-47` (`needs_reply` only) | becomes Engagement's Suggested lane count |
| App-wide "Waiting on you" | `mobile.js:369` | agent sessions only; not a source |
| Legacy "Thread with Posy" | `desk.js:1896` | a writer push-back thread, not engagement; not a source |

**Missing:** any inbound read from X or LinkedIn (gap map #8); a store of suggested replies; a log of sent replies
(by you or automatic); a project key on conversations (K4); per-platform coverage facts.

**v1 (R0 fixtures, R1 drafts only):**
- Three lanes, each glyph + word: `💬 Incoming` (new since you last looked) · `✎ Suggested · awaiting you` (Posy's
  drafted reply, as it will be sent) · `✓ Sent` (by you; `auto` only if Q3 allows it).
- Filters: project · campaign (incl. `No campaign`) · channel · source. State persisted per session.
- A `Replies: drafted for your review` banner per project from `project.replies`, so an empty Sent lane never reads as
  broken, and "automated responses" visibly shows as off.
- Out of v1: auto-answer, cross-platform identity merge, sentiment, assignment to people other than you.

## 8. Open questions for Ron (3)

1. **One account, several projects.** `@ron` on X can carry Clayrune and Engulfing scanner. Do we cap posts per
   **account** across projects (weekly ceiling in Settings → Connections; each project's ceiling must fit under it)?
   **Recommend: yes.** Without it, two projects at 3/wk each put 6/wk on one account, and no screen shows it.
2. **Where approval lives.** Keep approval **per campaign**, bounded by end/post cap, with the project holding ceilings
   and defaults (widening the project never widens a running campaign), or move to one **per-project** approval with a
   rolling weekly cap and no end? **Recommend: per campaign.** The standing position requires a finite approval; the
   ongoing presence becomes a subject-`project` campaign renewed each term. Picking per-project reverses that
   position and I would record it as superseded.
3. **Automated responses in v1.** Your dashboard names "automated responses". v1 today drafts replies only (C3,
   R1 scope). Keep auto-send out of v1 and show a visible `Replies: drafted for your review` state, or allow
   `Auto-answer verified FAQ` per project in v1? **Recommend: out of v1**, add after R1-E proves the inbound feed.
