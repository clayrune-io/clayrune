# Desk v1 R1-W: wiring plan (MC-1021, backlog 3ad3c1fb, step 1)

Author: Merrin (planner). Date: 2026-09-30. Base: master `58c01654` (desk-retire-presence `16c5fa1c` +
the MC-1019 engines scan). Read-only investigation; no code changed. Step 2 builds from this, one slice
per builder, merged one at a time, smokes after each merge (CLAUDE.md, "Run the smokes AFTER each merge").

## 0. Findings first

1. **Live today is narrower than the backlog item says.** Only three `/api/desk` calls exist in v1:
   `desk-v1-connections.js:37,123` (PATCH read settings), `:112` (GET coverage) and
   `desk-v1-project.js:548` (PATCH presence `desk_agent`). `desk-v1-retro.js` makes **no** call: its
   header (`:12`) only notes the findings routes exist. Every other read and write is `_fx()` =
   `window.DeskV1Fixtures` (`desk-v1-fixtures.js:1239`), mutated through `DeskV1Kit.commandBus`
   (`desk-v1-kit.js:285`, 62 call sites across 15 files) or by direct assignment.
2. **There is no piece store.** The item says R1-P shipped "piece shapes"; `mc/desk.py` has none. Pieces
   (fixture `families`, each with `versions[]`) exist only in the fixture. The only trace is
   `piece_id` on ledger rows (`mc/desk.py:197-215, 1347-1368`). What, Where, When, Review and Launch all
   sit on this missing store, so it is the critical path.
3. **There is no workspace account store.** Fixture `channels` (`desk-v1-fixtures.js:171`) are
   workspace accounts (retire-presence decision). The backend keeps accounts per project inside
   `presences[pid].accounts` (`mc/desk.py:292-334, 348`); Connections already works around this by
   filing an account under "the first campaign's project, else the first project"
   (`desk-v1-connections.js:30-35`).
4. **The server cannot tell what a human approved.** `update_campaign` overwrites
   `camp.approval = {bounds, bounds_hash}` with the CURRENT bounds on every PATCH
   (`mc/desk.py:1313-1317`); a widening only changes the hash. No actor, no timestamp, no snapshot of the
   approved envelope. The client keeps the real approval record (`desk-v1-rules.js:379-380`,
   `desk-v1-campaign.js:777-778`) in fixture memory. With Launch FULLY LIVE this is a blocker: the
   publisher must refuse when current bounds widen past the human-approved snapshot, and today nothing
   server-side can answer that.
5. **Vocabularies disagree.** Backend `CAMPAIGN_STATES = proposed, running, paused, done, dropped`
   (`mc/desk.py:1074`); v1 uses `draft, proposed, active, paused, completed, archived`
   (`desk-v1-kit.js:149-156`). Backend campaign has `title`, `project_id`; v1 reads `plan.title`,
   `projectId`. `create_campaign` raises when no voice exists (`mc/desk.py:1220`); a v1 draft starts
   with no voice (voice is chosen per account in Where).
6. **Cadence limits are client-only.** `plan.cadence.per_week/min_gap_h` and `plan.end.post_cap`
   (Brief limits, `desk-v1-how.js:52-75`) are bounds inputs on the server (`_campaign_bounds`,
   `mc/desk.py:1171`) but nothing server-side enforces them at post time, because nothing posts yet.
7. **X publisher exists, unwired, X only.** `mc/desk_publish.py` posts to X with receipts + idempotency,
   refuses LinkedIn (`:211-214`), and resolves vault secret `x.oauth-token` (`:101`). The vault holds
   only `x.com` and `linkedin` **website passwords** (`GET /api/secrets`, metadata, 2026-09-30); there is
   no `x.oauth-token` and no LinkedIn token. No LinkedIn publisher exists.

## 1. Per-surface inventory

R = fixture read, W = fixture write. Route = the `/api/desk` route that serves it today, or **MISSING**
(numbers refer to §2). Line numbers are master `58c01654`.

### Shell `desk-v1-shell.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaigns, projects, families (breadcrumbs, map stop resolution) | R | `:87, :97, :110, :118, :204, :310` | M1 workspace |

### Home `desk-v1-home.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| projects, campaigns, channels, families, conversations | R | `:15-19` | M1 |
| calendarSchedule (next post) | R | `:335` | M13 (version `scheduled_at`) |
| proposedExtras (Needs-you blockers) | R | `:386` | M10 (`how.suggested` + blocker) |
| recentAssets (shelf) | R | `:200` | M22 materials |
| workerHeartbeat (render worker offline banner) | R | `:504` | M27 engines (MC-1019) |
| studio (shelf pair) | R | `:702` | M22 |
| drop-to-create family | W | `:69-70, :85` | M14 |
| new campaign (drag or button) | W | `:91-94, :524-528` | `POST /api/desk/campaigns` (shape fix, §2.B) |
| homeSuggestions | none | fixture only, no consumer | delete in fixture move |

### Project `desk-v1-project.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| projects, campaigns, families, conversations, channels | R | `:17-21` | M1 |
| playbook findings | R/W | `:220, :339-391` | `POST /api/desk/findings/<id>/{reconfirm,retire,undo-reject}` (exist) |
| retros | R | `:379` | M12 |
| pause / resume project (cascades to campaigns) | W | `:487-510` | `PATCH /api/desk/presence/<pid>` (exists, `state`) + campaign PATCH each; better M1-side cascade, see §2.C |
| desk_agent | W | `:546-548` | **LIVE** `PATCH /api/desk/presence/<pid>` |

### Setup `desk-v1-setup.js` (draft creation, project field)
| Data | R/W | Where | Route |
|---|---|---|---|
| projects, campaigns, channels, families | R | `:17-21, :170` | M1 |
| create draft campaign | W | `:70-73` | `POST /api/desk/campaigns` (shape fix) |
| set campaign project | W | `:120-132` | `PATCH /api/desk/campaigns/<id>` `project_id` (exists) |

### Campaign shell + Launch `desk-v1-campaign.js`, `desk-v1-rules.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaigns, channels, projects, families, conversations | R | campaign `:24-31` | M1 |
| proposedExtras.blocker / suggestReply / campaignSuggestions | R | campaign `:725, :1020, :1202` | M10 |
| pause / resume | W | campaign `:170, :240, :895` | PATCH `state` (exists; vocab §0.5) |
| delete draft | W | campaign `:273` | `DELETE /api/desk/campaigns/<id>` (exists) |
| archive / restore | W | campaign `:320, :333` | PATCH `state` (needs `archived`) |
| answer agent question | W | campaign `:730` | PATCH `answers` (allowlist add) |
| renew term (terms, approval, approvals) | W | campaign `:769-778` | M8 |
| accept suggested pieces | W | campaign `:980-990, :1016-1038` | M14 + PATCH `how.suggested`, `when` |
| add / move / duplicate / bulk-state versions | W | campaign `:1127, :1147, :1160, :1176` | M17, M18, M14 |
| goal / deadline / audience inline edit (proposed summary) | W | rules `:149-153` | PATCH `goal`, `plan` (exist) |
| **Start campaign** (state, policyRecord, startedAt, approval, approvals) | W | rules `:364-385` | M6 |
| agent instruction box (Posy reply) | W (UI only) | rules `:465` | none yet: dispatch is a separate ticket |

### Brief `desk-v1-how.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaign, project | R | `:39-40` | M1 |
| strategy / angle / never_claim | W direct, no Undo | `:221-225` | PATCH `how` (exists) |
| limits: per_week, min_gap_h, end date, post_cap, budget | W via `_applyLimit` | `:202-239` | PATCH `plan`, `how` (exist) + server re-approval gate (M7) |
| campaign agent | W | `:178` | PATCH `how.agent` (exists; R1-A resolves it) |
| Suggest What/When/Where | (fixture `Ready` in campaign `:1016-1038`) | | M9 + M10 |

### Goal `desk-v1-results.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaign | R | `:20` | M1 |
| results (current, forecast, per-version outcomes) | R | `:131` | M11 |
| goal fields | W direct | `:189` | PATCH `goal` (exists; start gate re-checks) |
| manual entries | W direct | `:200` | PATCH `goal.entries` (exists inside `goal`) |
| resultsInsight | none | no consumer | delete in fixture move |

### Retro `desk-v1-retro.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaign, playbook, retros, ledger | R | `:20-37` | M12; `GET /api/desk/ledger` (exists); `GET /api/desk/findings` (exists) |
| fill per-post metric | W | `:155` | `POST /api/desk/ledger/<id>/outcome` (exists) |
| confirm / reject / don't-suggest-again | W | `:219, :242` | `POST /api/desk/findings/<id>/{confirm,reject,dont-suggest-again}` (exist) |

### What `desk-v1-what.js` + piece page `desk-v1-piece.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaign, channel, families | R | what `:32-34`, piece `:27-32` | M1 / M13 |
| contentPreview (excerpt), claims | R | piece `:92-94` | M13 (piece `body`, `claims`) |
| materialLibrary, existingArticles | R | what `:294, :312, :482-488, :543` | M22 |
| add / remove piece | W | what `:116-140` | M14, M15 |
| add asset to piece | W | what `:157-165, :552` | M21 |
| pick existing article | W | what `:176` | M15 (title, word_count, source ref) |
| save writer draft → in review | W | what `:378` (`DeskV1Studio.markInReview`, studio `:630`) | M18 |

### Where `desk-v1-where.js` (+ Connections for accounts)
| Data | R/W | Where | Route |
|---|---|---|---|
| campaign, channels, families | R | `:39-41, :105` | M1, M2 |
| add / remove account on campaign (archives pending versions) | W | `:130, :146` | PATCH `plan.accounts` (exists) + M18 per version, or one M18-batch inside the same request (§2.D) |
| add version / move version between accounts | W | `:157, :174` | M17, M18 |
| per-account voice for the campaign | W | `:436` | PATCH `plan.voices` (exists inside `plan`) |

### When `desk-v1-calendar.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaigns, channels, projects, families | R | `:20-24, :485-495, :873` | M1 |
| calendarSchedule / reviewDetail.whenISO | R | `:39-40, :834` | M13 (`version.scheduled_at`) |
| own slot add / accept suggested slot | W | `:193-194, :243, :464` | PATCH `when` (allowlist add) |
| reschedule (drag) | W | `:844-859` | M18 `scheduled_at` |

### Review `desk-v1-review.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| channels, campaigns, projects, families | R | `:14-37, :237` | M1 |
| reviewDetail (claims, body, edits) | R | `:44` and the claims reads `:125-635` | M13 (version `body`, `claims[]`) |
| claim accept / edit / remove / agent rephrase (revision bump) | W | `:563, :577, :588, :600` | M18 (claims + revision); agent rephrase M20 |
| **Approve / Approve and schedule / Skip / Archive** | W | `:610-619` | M19 (approve, human-only), M18 (skip, archive) |
| apply agent rewrite style | W | `:753` | M20 |

### Connections `desk-v1-connections.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| channels, projects, campaigns | R | `:23-25` | M2 |
| studio.online (content sources) | R | `:99` | M22 |
| read_via / browser_profile | W | `:123-145` | **LIVE**, presence-scoped; moves to M4 |
| coverage line | R | `:112` | **LIVE** `GET /api/desk/engagement/coverage/<pid>` |
| Connect / Reconnect (preview, authenticates nothing) | W | `:158` | M4 + publish readiness (§4) |
| Generation engines section | placeholder | `:186-188` | M27 (MC-1019) |

### Conversations + Engagement `desk-v1-conversations.js`, `desk-v1-engagement.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| campaigns, projects, channels, conversations, detail, coverage gaps | R | conv `:22-32`, eng `:28-32` | `GET /api/desk/engagement[/overview]`, `/coverage/<pid>` (exist); thread detail M23 |
| send reply | W | conv `:325` | M23 (outbound post, human-only) |
| revise reply via agent | W | conv `:337` | M24 |
| ignore / assign / take over / resume | W | conv `:347, :364, :384, :392` | M23b (PATCH item) |
| mark read | — | not yet in v1 UI | `POST /api/desk/engagement/<id>/read` (exists) |

### Studio + Video `desk-v1-studio.js`, `desk-v1-video.js` (blocked, §5)
| Data | R/W | Where | Route |
|---|---|---|---|
| studio, campaigns, projects, families, channels | R | studio `:23-27, :56, :109` | M1, M22 |
| videoDetail (scenes), renderBudget | R | studio `:131, :422`; video `:33-36` | M25, M27 |
| scene move / edit | W | studio `:231, :245`; video `:525, :592, :657` | M25b |
| start storyboard (new video piece + detail) | W | video `:257-268` | M14 + M25b |
| render | W | studio `:257`; video `:685` | M26 |

### Kit `desk-v1-kit.js`
| Data | R/W | Where | Route |
|---|---|---|---|
| playbook (finding chips) | R | `:1228` | `GET /api/desk/findings` (exists) |
| `/api/characters`, `/api/projects` | R | `:34, :79` | live, not Desk routes |

## 2. Missing routes (29) and the changes to existing ones

All new routes go in `mc/blueprints/desk_routes.py`; stores in `mc/desk.py` under `_store_lock`, in
`data/desk.json` (outside `DATA_DIR`, the LOAD-BEARING rule). "Human-only" = refuses
`is_unattended_caller()` with 403, the same guard the finding state routes use.

### 2.A Read model
- **M1 `GET /api/desk/workspace`** → `{projects:[{id,name,state,roster,presence:{replies,desk_agent,state}}],
  campaigns:[V1Campaign], accounts:[Account], pieces:[Piece]}`. One bootstrap call so every surface's
  synchronous `_fx()` reads keep working against a hydrated store (§3.0). `V1Campaign` is the
  serializer in §2.B. `projects` comes from `load_projects()` + `get_presence()`.

### 2.B Campaign (existing routes need shape fixes, not new verbs)
- `POST/PATCH /api/desk/campaigns` accept and return the v1 shape through one serializer:
  `plan.title ↔ title`, `projectId ↔ project_id`, state map `active↔running, completed↔done,
  archived↔dropped`, `draft` added to `CAMPAIGN_STATES`. Recommended: translate at the route so the
  legacy Desk (`desk.mjs`, flag off) keeps its vocabulary until it retires. **Decision for Ron/next
  builder, not assumed: §6 Q1.**
- `create_campaign` must accept zero voices for a `draft` (voice is per account in Where); Start
  requires ≥1 voice per placed account.
- `update_campaign` allowlist adds `when`, `answers`, `log`. Server-owned, never client-writable:
  `approval`, `approved`, `approvals`, `terms`, `started_at`, `policy_record`.
- **M6 `POST /api/desk/campaigns/<id>/start`** (human-only) → runs `_start_gate_problems`, sets
  `state:'active'`, `started_at`, `term` index 1, and the new **`approved: {bounds, bounds_hash, at, by,
  term}`** snapshot. 409 `{problems:[...]}` on gate failure.
- **M7 `POST /api/desk/campaigns/<id>/approve`** (human-only) → re-approves widened bounds: copies current
  bounds into `approved`, appends to `approvals[]`. `GET` responses carry derived
  `awaiting_approval = bounds_widen(approved.bounds, current)` so Launch (`desk-v1-campaign.js:787`)
  stops computing it alone.
- **M8 `POST /api/desk/campaigns/<id>/renew`** (human-only) → `{term:{starts,ends}}`; appends to
  `terms[]`, re-stamps `approved` (mirrors `desk-v1-campaign.js:769-778`).
- **M9 `POST /api/desk/campaigns/<id>/suggest`** → dispatches the campaign's desk agent (R1-A
  `_desk_agent_ref`) with the Brief + `playbook_brief()`; returns `{session_id}`.
- **M10 `PUT /api/desk/campaigns/<id>/suggestions`** (agent-callable) → writes `how.suggested
  {what[], when[], where[], because[]}` and an optional `blocker {id, question, answers[]}`. Refuses any
  field naming a bound (reuse `_DESK_BOUND_RE`, `mc/desk.py:1639`): suggestions are never commitments.
- **M11 `GET /api/desk/campaigns/<id>/results`** → `{goal:{current,target,deadline,source,freshness},
  forecast|null, versions:[{version_id, outcome, metric|null, metric_label}]}` from ledger `outcomes[]`
  + `goal.entries`. Missing numbers stay `null` with a label (`delayed`, `n/a`), never 0 (MET-01).
- **M12 `GET /api/desk/campaigns/<id>/retro?term=`** → assembles `dimension_arms` from ledger rows
  (`piece_id/format/account/term`) and calls `desk_retro.run_retro`; `POST /api/desk/retro` stays the
  raw computation.

### 2.C Accounts (workspace-level, replaces presence-scoped accounts)
New store `store['accounts']` keyed by id. `Account = {id, platform, identity, label, capability:
'direct'|'manual', voice, read_via, browser_profile, preview, created_at}` plus DERIVED
`publish: {ready, reason, secret, unattended_ok}` (§4). Migration: lift every `presences[*].accounts[]`
row into the store once, idempotent, keep the presence copy.
- **M2 `GET /api/desk/accounts`**, **M3 `POST /api/desk/accounts`** (human-only),
  **M4 `PATCH /api/desk/accounts/<id>`** (label, voice, read_via, browser_profile; the existing
  presence read route becomes a thin alias), **M5 `DELETE /api/desk/accounts/<id>`** (human-only;
  409 while a non-archived campaign places it).
- Project pause: `PATCH /api/desk/presence/<pid>` `{state}` exists; the cascade to campaigns
  (`desk-v1-project.js:487-510`) should be server-side in the same request so a half-paused project is
  impossible. Not a new route; a behaviour on the existing one.

### 2.D Pieces and versions (new store, the critical path)
`store['pieces']` keyed by id. `Piece = {id, campaign_id, project_id, kind:'post'|'article'|'video'|'image',
title, body, word_count, source:{kind, ref}, assets:[AssetRef], claims:[{id,label,text,source|null}],
versions:[Version], created_at, updated_at}`.
`Version = {id, account_id, state (VERSION_STATES, desk-v1-kit.js:132), body, revision,
claims_state:{<claim_id>:{status, revised_text, revision}}, scheduled_at|null, slot_id|null,
approved:{at,by}|null, receipt:{post_id, permalink, posted_at}|null, failure|null}`.
- **M13 `GET /api/desk/pieces?campaign_id=`**, **M14 `POST /api/desk/pieces`**,
  **M15 `PATCH /api/desk/pieces/<id>`**, **M16 `DELETE /api/desk/pieces/<id>`** (409 once any version
  is `sending` or later).
- **M17 `POST /api/desk/pieces/<id>/versions`** `{account_id, body?}` → new `drafting` version.
- **M18 `PATCH /api/desk/pieces/<id>/versions/<vid>`** → `account_id, body, claims_state, revision,
  scheduled_at, state` limited to `drafting|needs_review|skipped|archived|planned`. It can NEVER set
  `approved|scheduled|sending|submitted|verified_published` (only M19 and the tick can).
- **M19 `POST /api/desk/pieces/<id>/versions/<vid>/approve`** (human-only, the approval gate) →
  `{scheduled_at?}`; sets `approved` or `scheduled`, stamps `approved:{at,by}`. Refuses: campaign not
  `active`; `awaiting_approval`; account `publish.ready=false`; unresolved blocking claim; video not
  rendered (mirrors `desk-v1-review.js:432`).
- **M20 `POST /api/desk/pieces/<id>/versions/<vid>/revise`** `{style|claim_id}` → agent dispatch; the
  result lands via M18 as a new revision, `needs_review`.
- **M21 `POST /api/desk/pieces/<id>/assets`** → attach a library asset or an upload (upload path under
  `data/uploads/desk/`, the `/api/serve-image` allowlist).
- Failed-post retry is NOT a separate route: M19 on a `failed` version re-approves it (a human click).

### 2.E Materials
- **M22 `GET /api/desk/materials`** → `{library:{video[],image[]}, articles[], online:{video[],image[]},
  recent[]}`. Sources: `data/uploads/desk/`, the project's screenshots, existing published articles.
  Online sources stay "not connected" until a drive connector exists (out of R1-W scope).

### 2.F Conversations
- **M23 `POST /api/desk/engagement/<id>/reply`** (human-only) → outbound reply through the publisher,
  same idempotency/receipt path as posts.
- **M23b `PATCH /api/desk/engagement/<id>`** → `{state:'ignored'|..., assigned_to, taken_over}`;
  `upsert_engagement_item` already refuses to overwrite human-touched fields (`mc/desk.py:1426`).
- **M24 `POST /api/desk/engagement/<id>/suggest-reply`** → agent dispatch, draft only.

### 2.G Studio / video (gated on §5)
- **M25 `GET` / M25b `PUT /api/desk/pieces/<id>/storyboard`** → `{scenes:[Scene], pending_edits[]}`.
- **M26 `POST /api/desk/pieces/<id>/render`** → `GenerationRequest` (scan §"Recommended connector
  shape") → `Job`; estimate checked against the campaign budget before submit.
- **M27 `GET /api/desk/engines`** → connected `EngineDescriptor[]` + per-engine readiness + the local
  worker heartbeat (replaces fixture `workerHeartbeat`, `renderBudget`).

Count: M1-M27 plus M23b and M25b = **29 new routes** (27 numbered + 2 sub-numbered). Plus the existing
route changes in §2.B/§2.C. The publish tick (§4) is a server thread, not a route.

## 3. Slices

### 3.0 Fixtures stay for the harness only (applies to every slice)
- Every smoke already intercepts ALL requests (`tools/smoke/desk-v1-harness.mjs:106`,
  `page.route('**/*', ...)`; 20 of 20 desk-v1 smokes do). So the harness can SERVE the fixtures as
  API responses instead of the page loading them.
- S0 adds `static/js/desk-v1-store.js`: `DeskV1Store.load()` calls M1, holds the same object shape
  `_fx()` returns today, and `DeskV1Store.run({label, request, apply, undoRequest, unapply})` wraps
  `commandBus`: apply optimistically, call the route, roll back + toast the server's error on failure.
  Each surface's one-line `_fx()` becomes `DeskV1Store.state()`.
- `desk-v1-fixtures.js` moves to `tools/smoke/fixtures/`; a new `tools/smoke/desk-v1-fixture-api.mjs`
  answers M1/M2/M13/... from it with in-memory mutation, so existing assertions survive. The
  `<script>` at `static/index.html:4007` is removed in the slice that wires the last surface.
- **Production never falls back to fixtures.** A failed load renders an explicit error state, not demo
  data (SUBSTITUTION rule). Until S5 lands, a `desk_v1_live` config flag (default off) picks the
  store's source, so Ron never sees real campaigns beside fixture pieces.

### 3.1 Order (Studio last; nothing before S9 depends on MC-1019/MC-1020)
| # | Slice | Files | Routes | Smokes after merge | pytest |
|---|---|---|---|---|---|
| S0 | Store + fixture move + workspace read | new `desk-v1-store.js`, `desk-v1-shell.js`, `index.html`, `tools/smoke/fixtures/*`, harness | M1 | all `desk-v1-*.mjs` (no behaviour change), `desk.mjs` | new `test_desk_workspace.py`, `test_desk_v1_flag.py` |
| S1 | Home + Project + Setup | `desk-v1-home.js`, `-project.js`, `-setup.js` | campaigns POST/PATCH/DELETE shape fix, presence pause cascade, findings (exist) | `desk-v1-home`, `-project`, `-campaign`, `-journey`, `desk.mjs` | `test_desk.py`, `test_desk_routes.py` (+ serializer, zero-voice draft) |
| S2 | Campaign shell + Brief + Launch Start/approve/pause/renew | `-campaign.js`, `-how.js`, `-rules.js`, `-kit.js` | M6, M7, M8, PATCH allowlist | `desk-v1-map`, `-how`, `-campaign`, `-kit` | `test_desk_start_gate.py` + new `test_desk_approval.py` (snapshot only via human routes; unattended 403; widen → awaiting) |
| S3 | Goal | `-results.js` | M11, PATCH goal | `desk-v1-results`, `-map` | new `test_desk_results.py` (null never 0) |
| S4 | What + piece page | `-what.js`, `-piece.js`, `-studio.js` (`markInReview` only) | M13-M18, M21, M22 | `desk-v1-piece`, `-campaign`, `-review` | new `test_desk_pieces.py` |
| S5 | Where + Connections accounts | `-where.js`, `-connections.js` | M2-M5, M17-M18 | `desk-v1-where`, `-connections`, `-exit` | new `test_desk_accounts.py` (presence migration idempotent) |
| S6 | When | `-calendar.js` | PATCH `when`, M18 `scheduled_at` | `desk-v1-calendar`, `-piece` | `test_desk_pieces.py` (schedule) |
| S7 | Review + Launch FULLY LIVE (§4) | `-review.js`, `-connections.js`, `mc/desk_publish.py`, new `mc/desk_tick.py`, `server.py` wire | M19, M20, Brief Suggest M9/M10 | `desk-v1-review`, `-map`, `-connections`, `-exit`, `-journey` | `test_desk_publish.py`, `test_desk_receipt.py`, new `test_desk_tick.py`, `test_desk_publish_linkedin.py` |
| S8 | Retro + Conversations/Engagement | `-retro.js`, `-conversations.js`, `-engagement.js` | M12, M23, M23b, M24 | `desk-v1-retro`, `-conversations`, `-engagement`, `-journey` | `test_desk_retro.py`, `test_desk_engagement.py`, `test_desk_engagement_pane.py` |
| S9 | Studio + Video (after §5) | `-studio.js`, `-video.js`, Connections engines | M25, M25b, M26, M27 | `desk-v1-studio`, `-video`, `-connections` | new `test_desk_render.py` |

S2 before S4 is deliberate: the approval snapshot (§0.4) must exist before any piece can reach M19.
S7 is the first slice that can cost money; everything before it is reversible data.

## 4. Launch FULLY LIVE (Ron 2026-10-01): publishing path and where credentials show

**Path.** Human clicks Approve (M19, human-only) → version `approved`/`scheduled` → `mc/desk_tick.py`
(server thread, every 60 s) picks due versions → checks, in order: campaign `active`; not
`awaiting_approval`; cadence `per_week`, `min_gap_h`, `post_cap` and `term.ends` against the ledger;
account `publish.ready` → `sending` → `desk_publish.publish(item)` (idempotent on `version.id`) →
receipt → `submitted`, `record_published(piece_id, format, account, term, cost)` →
`verified_published` once the permalink resolves. Any refusal → `held` with the reason shown on the
version; nothing silently skips. No dry-run mode. `capability: 'manual'` accounts (blog) produce a
publishing task + the share-intent link (standing position 2026-09-23), not an API call.

**Unattended consequence Ron must know:** a scheduled post goes out from the tick, which is an
unattended caller. `secrets_store.get_secret_value(..., unattended=True)` honours each secret's
`allow_unattended`. A secret with it off posts on "Approve now" but every scheduled post holds.
Connections shows this per account.

**X.** Secret `x.oauth-token` (`mc/desk_publish.py:101`); absent from the vault today (only the `x.com`
website password exists). Connections, X account row: `Publishing: not connected (no X API token in the
vault)` + `Open Secrets ›` (the human path; agents never create credentials, CLAUDE.md vault rule 3).
Read via stays a separate line (`desk-v1-connections.js:49-60`). Where's account tile and Review's
Approve button show the same reason (`desk-v1-review.js:427-432` already has the disabled-reason slot).

**LinkedIn.** Needs a new publisher path in `mc/desk_publish.py` (organization post as
`urn:li:organization:<id>`; exact endpoint and version header verified against LinkedIn docs at build
time, not assumed here) and two vault entries: an OAuth token (proposed name `linkedin.oauth-token`)
and the organization id (on the account record, not a secret). The scope `w_organization_social` is
NOT approved yet (Community Management API review). Until it is, the `Clayrune page` account reports
`publish.ready=false, reason:'LinkedIn app review pending (w_organization_social)'` and renders as
**Not connected** on Connections, Where (tile) and Review (Approve disabled with that reason). This
replaces the fixture's `health:'held'` (`desk-v1-fixtures.js:186-187`) with a real computed state.

## 5. What MC-1020 and MC-1019 must settle before S9

**MC-1020 (storyboard editing, 3834729e)**
1. The persisted `Scene` shape: `{id, label, line, duration_sec, picture: AssetRef|null, edited}` and
   whether `pending_edits[]` is stored or client-only.
2. Insert-at-any-position and remove semantics with Undo (server ordering by array index vs a sort key).
3. Where per-scene uploaded pictures live (`data/uploads/desk/`? project path?) so M21/M22 and
   `/api/serve-image` agree.
4. Which piece owns the storyboard when a video piece has several versions (one storyboard per piece,
   recommended).

**MC-1019 (generation engines, c7ac1e8c)**
1. Adopt the scan's `EngineDescriptor` / `GenerationRequest` / `Job` (scan §"Recommended connector
   shape") as the M26/M27 contract, or amend it.
2. Vault entry names per engine (Higgsfield key id+secret, Google, OpenAI).
3. Budget source for renders: the campaign's `how.budget` (Presence pool retired) vs a per-job limit;
   the scan's rule 8 still names `renderBudget.perJobLimit`, which no longer has a home.
4. Local engine: Remotion DROPPED (Ron 2026-10-01: every external service is signed and paid by the
   customer through a vendor API; Remotion has none, and on a hosted pod its licence would land on us).
   Open for S9: what stitches a multi-scene video (scan rule 7) now that there is no local compositor.
5. Output retention: download on `ready` (Veo deletes after 48 h) into the M22 library path.

Slices S0-S8 depend on neither.

## 6. Decisions to put to Ron (none blocks S0)
1. **Campaign state vocabulary**: translate at the route (recommended: legacy Desk keeps working) or
   migrate the store to the v1 words and retire legacy `desk.mjs` paths.
2. **`desk_v1_live` flag**: confirm fixtures stay the default until S5 (recommended), versus wiring
   surface by surface live.
3. **`allow_unattended` on the X token**: required for scheduled posts; Ron sets it when creating the
   secret.
