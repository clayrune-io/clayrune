# The Desk v1 — UX pass: binding build spec (items 2, 4, 6 + review blockers)

**Status:** BINDING build spec, 2026-09-28. **IA revision 2026-09-28: T4 to T8 ON HOLD, replaced by `THE_DESK_V1_IA_REVISION.md` §5 (IA1 to IA8); §1, §3, §4, §7 amended there.** MC-977 / backlog 8f64d565. R0 stays parked until ticket T8 passes.
**Owner:** Merrin (plan). Builders per ticket, §9. Doc only; no code changed by this revision.
**Inputs (kept, not authority):** Merrin's first spec (this file at `e23cec8`) and Kestrel's independent review,
`docs/THE_DESK_V1_UX_PASS_kestrel.md` (`c60953b`). Where they disagreed, Ron decided (below).
**Authority:** amends `THE_DESK_V1_UI.md` §1, §2, §3.4, §3.5 (those sections now point here). Behaviour, permissions and
approval bounds still follow `THE_DESK_SPEC.md` and `THE_DESK_SIMPLIFICATION_PLAN.md` §4. Items **1, 3, 5** (modal
size, delete/archive campaign, Posy text lost on tab switch) are Tilda's, in flight in session `3ad45d19f2bc`; this
doc builds on them and does not re-spec them.

## Decisions (Ron, 2026-09-28, binding)

1. **Projects: one Desk, all projects, a `Projects: All ▾` filter.** Not a header switcher. (Merrin's model.)
2. **Setup: a 4-step resumable checklist INSIDE the campaign page** — Purpose; Destinations + voice; First plan;
   Review + start — with one next action. Not a separate 7-step `setup` route. (Kestrel's model.)
3. **Graduation:** `✎ Draft` (setup) → `◇ Proposed` (populated plan) → `▶ Active` **only** on explicit human approval of
   a bounded plan.
4. **No per-piece approval for a first campaign** (Merrin's old Q2, dropped). The standing per-campaign release policy
   holds: Ron approves a bounded plan once; posts publish on cadence behind Pause and a published log.
5. **Delete vs Archive** (Tilda building): Delete only for never-started campaigns; Archive for anything with publication
   history; both in a labelled `More` menu, with Undo.
6. **Also in scope** (Kestrel's blockers): Resume after Pause; tabs switch panels inside a persistent campaign frame;
   one canonical plan object feeds goal display AND Start; Start blocked until every required bound has a value or an
   explicit Off; a blank-campaign + second-project acceptance journey smoke.

## 0. The defects (screenshots `data/uploads/agent_{4175e5c7c4,6fce437ae7,77f1e42ebf}.png`)

| Screen | Wrong | Cause |
|---|---|---|
| Home | `Clayrune ▾` looks like a switcher, does nothing; nothing says "start a campaign here" | `desk-v1-home.js:523` toast stub |
| New campaign | Goal `__ by __`, no channels, Posy "reply" instant and echoes text | `_createProposedCampaign` (`home.js:71`) makes an empty shell and opens the ongoing layout; `rules.js:43` labels with no values; Posy handler synchronous (`rules.js:572`) |
| Start sheet | every row `—`; title is the raw prompt | `rules.js:173-187` reads a separate `proposedDetail.authority`, falls back to `—`, still permits Start (`:220`) |

Common fault: **a campaign is shown as ready before it has anything in it.** Rule for every surface in this pass:
**no label without a value** — an unset optional is omitted or stated as its default in words, never `—` or a blank.

## 1. Lifecycle

| State | Frame shows | Enters | Leaves |
|---|---|---|---|
| `✎ Draft` | Setup checklist, steps 1–3 | `Create campaign ›` on Home, or `＋ New campaign` | step 3 returns a plan with ≥1 sample AND step 1 required set → Proposed |
| `◇ Proposed` | Setup checklist, opens on step 4 | graduation; or a widening edit to an Active plan | `Start campaign` confirmed with a valid plan → Active |
| `▶ Active` | Operating view (§6) | Start confirmed | Pause; end date / post cap reached → Completed; widening edit → Proposed (stops publishing) |
| `⏸ Paused` | Operating view + `▶ Resume` | Pause | Resume (§6.2) |
| `✓ Completed` | Operating view, read-only + `Renew ›` | end/cap reached | Renew = step 4 with changed terms only → Active |
| `Archived` | read-only history | `More › Archive` (any campaign with publication history) | `More › Restore` |

- Setup progress (`camp.setup = {step, done[]}`) is UI state, separate from the lifecycle state.
- Viewing a summary or getting a Posy reply is **not** graduation. No result or published post is needed to go Active.
- `More › Delete` exists only while the campaign has never been Active (Draft/Proposed). Neither action removes posts
  already on a platform; the confirm says so.

## 2. Campaign frame (persistent)

One frame for every state. Header: `‹ Desk` · campaign short title · state (glyph + word) · project chip · `More ▾`
(Tilda) · `⏸ Pause` / `▶ Resume` (Active/Paused only). Below it, one panel at a time:

- Draft / Proposed: the **Setup** panel (§3). No tabs, no goal bar, no results widgets.
- Active / Paused / Completed: tabs `Overview · Content · Conversations · Results`. **Tabs switch the panel in place**:
  route stays `campaign` with `params.panel`; header, Posy box and the panel's scroll/selection state persist.
  `conversations`, `results`, `calendar` stop being separate routes; `deskV1Nav('results', p)` still works (maps to
  `campaign` + `panel:'results'`) so existing deep links and smokes keep resolving.
- Full-width review (`review`), video (`video`) and `rules` stay child routes: those are deliberate departures with `‹`.

## 3. Setup: 4-step checklist inside the campaign (Draft → Proposed)

**Shape.** A vertical checklist: `✓ done · ● current · ○ to do · ⚠ needs attention`, each step expandable, any done
step reopenable in any order. One primary button, always the next concrete action ("Choose where it will run ›").
Posy's guesses are pre-filled and marked `? Assumed` until touched, so accepting defaults always yields a valid
plan. Every change saves immediately; `Save and leave` returns Home; the Draft card reads `✎ Draft · Setup 2 of 4 ·
Continue ›`. Explanations sit behind ⓘ and can be collapsed (remembered). It is work on the real campaign: no tour
overlay, no chat interview. Experienced users may edit the checklist directly; nobody bypasses step 4.

| Step | Asks | Required to leave the step | Posy's default | Plan fields (§4) |
|---|---|---|---|---|
| **1 Purpose** | What are we promoting (full brief, kept whole; `📎`/`🔗` attach), short title, owning project, `Also draw on:` other projects, who should care (3 chips + text), outcome (chips: signups · installs · stars · visits · just get it seen · ongoing updates; optional number + date) | brief, title, ≥1 project, objective in words | title = Posy's short title, never the raw sentence; project = Home filter, else the only one; outcome "Just get it seen" | `brief`, `title`, `source_projects`, `audience`, `goal` |
| **2 Destinations + voice** | Destination identities (badge + capability: publishes · `✋ You publish it` · `⚠ Held`), `＋ Connect`; per destination, the voice with a one-line sample | nothing to continue planning; a viable path (connected or `✋`) is required at step 4 | the project's connected destinations; 𝕏 → Ron (first person), LinkedIn → Clayrune page (2026-09-14 split) | `destinations[] {account, voice}` |
| **3 First plan** | `Draft the plan` → Posy task (§5, stage "Preparing a draft plan"). Result: the angle in one paragraph, **3 sample posts** (editable, `Ask Posy to redo`, a "why this serves the goal" line, the material/evidence used), suggested cadence and end | plan returned with ≥1 sample | cadence ≤3/wk, min gap 12 h; ends 30 days or 12 posts | `angle`, `samples[]`, `cadence`, `end` |
| **4 Review + start** | The bounds, §3.1 | every required bound valid | — | approval |

- Missing measurement is a note (`⚠ not tracked yet · How?`), never a blocker (CMP-03). An ongoing-presence campaign
  with no numeric target is valid.
- Zero connected destinations: step 2 still offers the manual route (Open in X / Copy, SIMPLIFICATION 2b) as a real,
  selectable `✋ You publish it` destination. Setup never dead-ends on a missing API connection.
- Step 3 failure keeps every answer and the request; `Retry` / `Edit request`. A real blocker shows as Posy's one
  question (`⛔` card, two answer buttons) — `Needs your answer` in §5.
- Revising the plan in plain language uses the Posy box scoped `About: the plan`.
- Sample visuals: a real project screenshot (smoke capture or upload) or none. Posy never generates product imagery.

### 3.1 Step 4: Review + start (replaces `deskV1OpenStartSheet`)

Rendered in-page, not a modal. Top: the plan in one sentence, each clause a link to its step:
"Posy will post up to 3 a week on 𝕏 · @ron as Ron, for 30 days or 12 posts, whichever comes first, to reach Windows
users trying Claude Code. Goal: 30 tester signups by Oct 20 (⚠ not tracked yet)." Unset optional clauses are omitted.

| Bound (SIMPLIFICATION §4) | Required | Shown as when unset |
|---|---|---|
| Destinations + voices | yes | `⚠ Not set · Set it ›` (→ step 2) |
| Source projects | yes | `⚠ Not set · Set it ›` (→ step 1) |
| Cadence ceiling + min gap | yes | `⚠ Not set · Set it ›` (→ step 3) |
| End date and/or post cap | ≥1 | `⚠ Not set · Set it ›` (→ step 3) |
| Spend ceiling | derived | `Up to $2.40 (12 posts × X link rate)` — never "free" |
| Replies | value or Off | default `Drafted for your review` |
| Paid promotion | value or Off | default `Off` |
| Generation limits | value or Off | default `No video generation` |
| Stop conditions | derived | `Ends at 30 days or 12 posts; Pause stops it any time` |

- Then: "First post: Tue 09:00 on 𝕏 · @ron" and "Starting lets Posy publish within these limits. Pause stops it at
  any time." (per-campaign release, 2026-09-22). The old "Starting doesn't approve any piece" line is retired.
- `Start campaign` (header and step 4) is **disabled** while `validatePlan` reports anything missing, with the reason
  under it: "Set 2 things first: destinations, end date." Confirm → Active; R1 writes the approval record
  (`bounds_hash`, `approved_via:'ui'`, nonce, SIMPLIFICATION step 4/4b).

## 4. One canonical plan object

`camp.plan` is the only copy. Goal display, summary bar, rule chips, step 4, Resume/Renew and Home cards all **derive**
from it; `proposedDetail`, `detail.authority` and `goalSentence` are deleted (fixtures migrated).

```
plan = { brief, title, source_projects[], audience, goal:{outcome, target?, deadline?, tracked},
         destinations:[{account, voice}], angle, samples[], cadence:{per_week, min_gap_h},
         end:{date?, post_cap?}, replies, paid, generation }          // spend + stop conditions derived
DeskV1Kit.validatePlan(plan) → { ok, missing:[{bound, step, label}] }
```

- Editing the goal date edits `goal.deadline`; every surface re-renders from it (fixes `rules.js:85-90`).
- `validatePlan` is the single gate used by the checklist state, the Start button, Resume and Renew.
- Widening vs narrowing is decided by comparing plan bounds (SIMPLIFICATION §4): narrowing keeps Active; widening an
  Active plan → Proposed with the changed terms highlighted, publishing stops.

## 5. Posy is working (every Posy box: setup step 3, campaign, review, video director)

| State | Box shows | Input |
|---|---|---|
| **Sending** | the request as a bubble immediately, `Sending…` | Send disabled; draft kept |
| **Accepted** | `Posy has it` (R1: server saved it and returned `ask_id`) | draft cleared **now**, not before |
| **Working** | typing indicator (`data-act` thinking/writing/tool) + stage text ("Preparing a draft plan"); after 10 s `Still working · 0:14`; after 30 s "Taking longer than usual. You can leave; the answer will wait here." · `Cancel` | typing allowed; 2nd Send on same scope: "Posy is still on the last one." |
| **Needs your answer** | Posy's one question with answer buttons. Every widening change lands here, never applied | answer buttons |
| **Ready** | before → after, affected items, link to the changed plan/piece, `Undo`. The word "applied" appears only here and only with a concrete diff; no diff → "Posy answered; nothing changed." | enabled |
| **Failed** | `⚠ Posy couldn't finish: <reason>. Nothing was changed.` · `Retry` · `Edit request` (text restored). Timeout (180 s) adds `Keep waiting`; Cancelled is Failed with reason "cancelled" | enabled |

- No percentages, no completion estimates. Status text is in an `aria-live="polite"` region.
- **Reuse, don't fork:** expose `_actIndicatorInner` (`conversation.js:4436`) once as `window.actIndicatorHTML(kind)`.
  Chat-piece source: `docs/CONVERSATION_REDESIGN_ACTION_PLAN.md`.
- **One store, extending Tilda's item-5 draft store** (not a second one), keyed `(projectId, campaignId, targetId)` —
  stable ids, never a label or shared textarea id: `{draftText, asks:[{id, clientReqId, state, stage, startedAt,
  request, result, error}]}`. Survives tab/route changes; Home card shows `⟳ Posy working`; a result landing
  elsewhere toasts "Posy finished: <campaign> · Open". A selection change never retargets text already being typed:
  the scope chip stays on the original target and offers `Switch to <new>`.
- **R0 is simulated and says so:** fixture mode shows `Simulated reply (R0)` in the box footer. Latency 1.5–4 s;
  hooks `window.__deskV1PosyForce = 'fail' | 'timeout' | 'slow' | 'question'` (`slow` = 35 s scripted clock, the
  smoke never sleeps). Refresh/reopen durability is **not** R0 (tab/route survival is); it is R1-b.

**R1 contract (R1-a):**
```
POST   /api/desk/campaigns/<id>/ask  {text, scope:{kind,id}, client_req_id}
  202 {ask_id, session_id}   dispatches Posy (same path as dispatch_rework, desk_routes.py:466)
  409 {reason:'in_flight', ask_id}   one per scope; client re-attaches
GET    /api/desk/asks/<ask_id> → {state: accepted|working|needs_answer|ready|failed|cancelled|timed_out,
        stage, activity, started_at, question?, result?:{applied, before, after, affected[], undo_token}, error?}
DELETE /api/desk/asks/<ask_id> → cancel
```
- `client_req_id` makes Send idempotent across reconnects; client polls 2 s while visible. Server owns the 180 s
  timeout; a late result is stored as "Posy finished late · Review", never auto-applied. `activity` comes from the
  session's `activity_state` (`5c70f37`). `/ask` only proposes; it can never apply a widening change or approve.

## 6. Operating view (Active / Paused / Completed)

### 6.1 Overview panel (default)
`Next action` (e.g. "Next post Tue 09:00 on 𝕏 · @ron · Open") · `Needs you` (this campaign's items) · `Recent outcome`,
then the queue and the published log. Routine release follows cadence: no per-post approval ritual. Changes within
the plan show their effect and are logged.
Empty states before the first publish: goal `30 signups by Oct 20 · starts counting at the first post` (no `0/30`
bar); Results "Results start after the first post goes out. Next: Tue 09:00 on 𝕏 · @ron."; Conversations "Replies
show up here once a post is live." Results always distinguish measured zero, delayed and untracked (MET-01).

### 6.2 Pause / Resume
- `⏸ Pause` (header, Home card `More`): stops at once, Undo toast.
- `▶ Resume` on Paused (header and Home card `More`) opens a short sheet: upcoming work affected ("3 posts resume:
  Tue 09:00 …"; missed slots are skipped, never posted late), then `Resume`. Before resuming, `validatePlan` +
  expiry check: end date passed, cap reached, or a destination held → Resume routes to step 4 with only the changed
  terms, not a silent restart.

## 7. Projects and creating a campaign (Home)

- Header `Clayrune ▾` → **`Projects: All ▾`**: checklist of projects with ≥1 signal or campaign, plus All. Persisted in
  `localStorage` (`desk_v1_project_filter`). Filters campaign cards, Needs you, suggestions and Material consistently.
- Owning project ≠ destination: the project chip says what is promoted; the channel badge says where and as whom. Cards
  show the project chip when >1 project is visible; the campaign header always shows it.
- R0 fixtures gain a second project (`engulfing_scanner`, one Active campaign, one Needs you item).
- Promote box titled **Start a campaign**, helper: "Tell Posy what you want to achieve; you'll review the plan before
  it runs." Button **`Create campaign ›`** creates a `✎ Draft` with the full brief saved and opens step 1, project =
  current filter (correctable). Suggestion chips fill the box; they don't send.
- The campaign grid always ends with a dashed `＋ New campaign` card (blank Draft, step 1).
- **Zero campaigns:** Home shows only the promote box at reading width with 3 example chips; Needs you and shelves
  hidden until non-empty; Channels shows only `＋ Connect a channel` when none exist.
- **Deferred (flagged scope, not this pass):** a campaign picker in the campaign header; `More › Rename` and
  `Duplicate plan`. Each needs its own ask.

## 8. Files

| File | Change | Ticket |
|---|---|---|
| `static/js/desk-v1-kit.js` | `camp.plan` helpers + `validatePlan`; Posy task store/lifecycle on Tilda's store | T1, T3 |
| `static/js/conversation.js` | expose `window.actIndicatorHTML` (no behaviour change) | T3 |
| `static/js/desk-v1-shell.js` | `campaign` route gets `params.panel`; old panel routes alias to it | T2 |
| `static/js/desk-v1-campaign.js` | persistent frame, Overview panel, Pause/Resume, empty states | T2, T6 |
| `static/js/desk-v1-setup.js` (new) | Setup panel: checklist, steps 1–4, graduation | T4, T5 |
| `static/js/desk-v1-rules.js` | delete Proposed summary/content + `deskV1OpenStartSheet`; rule chips read `camp.plan` | T1, T5 |
| `static/js/desk-v1-home.js` | project filter, Create campaign, New card, zero-campaign Home, Draft cards | T7 |
| `static/js/desk-v1-fixtures.js` | `proposedDetail` → `camp.plan`; 2nd project; Draft + Paused fixtures; Posy script | T1–T7 |
| `static/css/desk-v1.css` | frame, checklist, step 4, working states | T2–T7 |
| `tools/smoke/desk-v1-*.mjs` | `desk-v1-setup.mjs`, `desk-v1-journey.mjs` new; others updated per ticket | each |

## 9. Tickets — one builder each, in this order; merge one at a time; run the named smokes after EACH merge

| # | Ticket | Depends on | Acceptance (smoke; all 3 tones where it renders UI) |
|---|---|---|---|
| **T1** | Canonical `camp.plan` + `validatePlan`; migrate fixtures; delete `proposedDetail`/`authority`/`goalSentence` | **Tilda's items 1/3/5 branch merged to master first**; T1 branches from that merge | `desk-v1-rules.mjs`: editing goal date changes deadline in summary bar + rules chips; a plan missing end → `validatePlan.missing` names it; `rg proposedDetail static/` = 0. `desk-v1-home/campaign/kit.mjs` still green |
| **T2** | Persistent campaign frame; in-place panels; Pause/Resume (§6.2); header `More` placement | T1 | `desk-v1-campaign.mjs`: tab click keeps header + Posy DOM node (same element), no route push; Pause → Resume sheet → Active; expired end → Resume opens step 4. `desk-v1-results/conversations/calendar.mjs` green via aliases |
| **T3** | Posy task lifecycle §5 on Tilda's store; `actIndicatorHTML`; R0 simulated label | Tilda item 5 (via T1 base) | `desk-v1-kit.mjs`: Send → Sending bubble < 100 ms; forced fail/timeout/question show exact §5 copy, text restored on fail; navigate away mid-ask and back → still Working; Home card `⟳ Posy working`; no "applied" text in any non-Ready state |
| **T4** | Setup panel steps 1–3, Draft persistence, Draft → Proposed | T1, T2, T3 | `desk-v1-setup.mjs`: Create → accept defaults → step 3 plan → state Proposed; leave at step 2 → Home card `Setup 2 of 4` → Continue lands on step 2 with answers; zero connected destinations completes via `✋` |
| **T5** | Step 4 Review + start; Proposed → Active; widening → Proposed | T4 | `desk-v1-setup.mjs`: step 4 has 0 `—` and 0 empty label/value pairs; missing bound disables Start with reason and `Set it ›` jumps to the step; title = short title; Start → Active. `desk-v1-rules.mjs`: widening an Active plan → Proposed |
| **T6** | Overview panel + Active empty states | T2, T5 | `desk-v1-campaign.mjs`: fresh Active shows no `0/` bar; Results/Conversations show §6.1 copy; lint A12 clean |
| **T7** | Home: `Projects: All` filter, 2nd project, Create campaign, New card, zero-campaign Home, Draft cards | T4 | `desk-v1-home.mjs`: filter hides the other project's cards + Needs you and persists across reload; zero-campaign fixture shows only promote box + examples; Create opens step 1 with brief + filtered project |
| **T8** | Acceptance journey + exit gate + UI doc greyscale shots | T1–T7 | new `desk-v1-journey.mjs`: blank campaign in the **second** project → setup → interrupt at step 2 → resume → forced Posy failure + Retry → Start → Pause → Resume → Delete a never-started Draft (Undo) → Archive an Active. `desk-v1-exit.mjs` exit 0 incl. setup/step-4 × 3 tones |
| R1-a | `/ask` backend per §5 | R1 start | tests: 202/409/idempotent replay, cancel, server timeout, late result not applied |
| R1-b | Durable Posy drafts/asks across refresh | R1-a | test: refresh mid-ask resumes the same `ask_id` |

T3 touches only kit/conversation and may be *built* in parallel with T2, but still merges after T2 with smokes between.

**Not verified until people run it:** the 5-user "create and review a draft in under 5 minutes" test
(`desk_v1_r0_exit.md`), plus Kestrel's probes: each user names the project/account, explains what Start authorizes,
recovers a request after navigating away, and stops/resumes the campaign. Run after T8. Also check keyboard focus
restoration, live-region announcements, phone action reachability at 390 px, 200% zoom.

## 10. Open question for Ron (nonblocking)

**Q1. First usability scenario:** (a) a bounded launch ("Windows installer, 30 days"); (b) ongoing project updates.
**Recommend (b)** (Kestrel): it also exercises renewal, Pause/Resume and routine management, not just setup.

Resolved and closed: projects model, setup shape, stepped-vs-chat (checklist on the campaign, not a chat interview),
first-campaign review mode (dropped), delete vs archive.
