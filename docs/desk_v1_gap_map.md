# Desk v1 — gap map (MC-977 step 0.1)

**Status:** 2026-09-25, Merrin (ui-program-manager). Read-only survey, no product code changed.
**Measured against:** master `0d86349`.
**Owns:** what exists today versus what `docs/THE_DESK_V1_UI.md` needs. The UI doc owns UI and build order. Behavior, permissions and data belong to the authority spec, and **that spec has not arrived**: `Clayrune_The_Desk_Specification_v1.0.docx` (→ `docs/THE_DESK_SPEC_v1.md`). Every entity below is **derived from the UI doc and the Turn 12 frames**, not from spec §13 DAT-01. Reconcile this file when the spec lands (§4 lists every spec ID the UI doc cites).
**Not the authority:** `docs/THE_DESK_SPEC.md` is the old v0 spec. It is cited below only to explain why existing code looks the way it does.

Legend: **exists** = usable as-is behind a v1 view. **partial** = the concept is present but its shape does not match what v1 needs. **missing** = nothing to build on.

---

## 1. What exists today (inventory)

| Surface | Where | What it is |
|---|---|---|
| Desk store | `mc/desk.py:114` `_empty_store` → `data/desk.json` (path `server.py:747`) | One JSON file holding `voices`, `campaigns`, `ledger`, `proposals` and `platforms`. It is a sibling of DATA_DIR, not inside it (`desk.py:26-33`). |
| Signal feed | `mc/desk.py:176` `append_signal`, `:202` `list_signals` → `data/desk_signals.jsonl` | Append-only record of project events. Harvested by `mc/desk_harvest.py:249` `harvest_all` (commits `:127`, backlog `:181`) and by the workflow action `mc/workflows.py:105,1383`. No cadence triggers it (simplification plan §1.1 row 1). |
| Voices | `mc/desk.py:318` `_empty_voice` (platform, destination, register, banned, never_claims, product_refs, rewrites); CRUD `:389-449` | Identity plus style, one record per voice. `destination` = which account on the platform (`:324-330`). |
| Learning loop | `mc/desk.py:451` `record_edit`; called from `project_routes.py:1818` (queue body PATCH) and `desk_routes.py:172` | Stores each human before/after rewrite on the voice. `voice_brief` (`:489`) feeds the last 12 rewrites to the writer. |
| Platform rules | `mc/desk.py:537-625` (seeded x/linkedin, char_limit + text) | Content rules per platform. Rendered into briefs by `mc/desk_brief.py:67` `_platform_rules_text`. |
| Proposals (triage) | `mc/desk.py:664-736`; routes `desk_routes.py:534` `/triage`, `:588-673` | Posy's per-signal suggestions, with a dismissal latch. The UI has no callers for them (plan §1.1 row 2). |
| Campaigns | `mc/desk.py:743` `CAMPAIGN_STATES = proposed/running/paused/done/dropped`; `:796` `create_campaign`; `:838` `update_campaign` | Title, thesis, agenda, voices, project_ids, `planned` (never written), visual, state. |
| Draft queue | `project_routes.py:1651` `_SOCIAL_STATUSES = pending/approved/posted/rejected/needs_changes`; POST `:1725`, PATCH `:1789`, approve `:1852`, posted `:1889`, reject `:1972` | `social_queue` on **each project record**, not in the Desk store. One row = one body for one platform. |
| Rework chain | `desk_routes.py:466` `dispatch_rework`; `desk_brief.py:305` `build_rework_brief` | A push-back creates a **new queue row** with `reworked_from`. It never revises the row in place (`desk.js:1912-1927`). |
| Story ledger | `mc/desk.py:891` `record_published`, `:917` `list_ledger`, `:929` `record_outcome`, `:948` `similar_published` | Record of what went out, plus the repeat guard. It has 0 rows ever (plan §1.1). |
| X publisher | `mc/desk_publish.py:189` `publish(item)`; receipts at `server.py:753` | Idempotent, fails closed, **unwired**, X only (`:209-213`). Token secret name is `x.oauth-token` (`:101`). |
| Post it yourself | `desk.js:1582-1709` (Open in X / LinkedIn, Copy, record permalink) | A manual-handoff path. After posting, `…/posted` (`project_routes.py:1889`) writes a ledger row. |
| Briefs | `mc/desk_brief.py:111` triage, `:198` draft, `:305` rework | The prompts Posy receives. Posy = `global:social-media-strategist`, dispatched strict (`desk_routes.py:254,453,517`). |
| Overview | `desk_routes.py:716` `/api/desk/overview` | Campaigns, hot signals, recent posts, voices, pending_drafts. |
| Desk UI | `static/js/desk.js` (2212 lines). Modal `__desk` (`:27`), tabs Board/Queue/Calendar/Ledger (`:28`), `openDesk` `:722`, `renderDesk` `:803`, Board `:1249`, Queue `:2081`, review pane `:1771`, Thread with Posy `:1896`, Calendar `:2124` (a list of past posts, not a grid), Ledger `:2149` | Everything v1 replaces. Opened from `index.html:1656` and `render-core.js:1001`. |
| Desk CSS | `static/css/app.css:8267-8964` (`.desk-*`, 238 rules) | Inside the shared stylesheet. |
| Schedules calendar | `static/js/schedule-calendar.js` (1000 lines): range math `:146`, time grid `:464`, month `:544`, swipe paging `:868` | Rows are **hours**, not channels. It shows agent schedule runs and has **no drag-to-reschedule**. The range/label/swipe helpers are reusable. The grid is not. |
| Scheduler | `mc/blueprints/scheduler_routes.py:553` `_scheduler_loop`, `:337` `_compute_next_run`; store `data/schedules.json` (`server.py:734`); kill switch `scheduler_paused` (`server.py:147`) | Dispatches **agent runs**. It has no concept of a post due at a time. Times use the **host's local timezone** (`:337-351`). |
| Persona / Bench | `mc/characters.py:299` `list_characters`, `:317` `read_character`; Floor bench via `/api/floor` (Posy's avatar is resolved there, `desk.js:53,95`) | Posy exists as a hired type. Nothing Desk-specific is stored here. |
| Secrets vault | `mc/secrets_store.py:1623` `list_secrets` (metadata only), `:1671` `set_secret` (kinds password/totp `:225`); `mc/unattended.py:63` `is_unattended_caller` | Credentials. An X/LinkedIn **connection** is only "a secret with that name exists" (`desk.js:1432` `_deskXConnected`). |
| Media | `mc/blueprints/media_routes.py:14` (agent-produced diagrams/images per project); queue `media` list with allowlist `project_routes.py:1667` `_media_violation` | No asset store. Attaching an asset is a `prompt()` for a path (`desk.js:1726`). |
| Chat pieces | CSS classes, not importable JS: bubbles `.agent-output/.agent-line`, typing `.typing-indicator/.act-dot` (`app.css:4368`), chips `.agent-question-chip` (`app.css:4627`), composer `.agent-input-row` + `micBtnHTML()` (`composer-extras.js`). `desk.js:1896-1910` already reuses them by class. | The UI doc names `CLAYRUNE_CONVERSATION_REDESIGN.md`. **No such file exists.** The real doc is `docs/CONVERSATION_REDESIGN_ACTION_PLAN.md`. |
| Pointer drag | `static/js/floor.js:630-800`: `_hireDrag` state, `floorFigDown` `:655`, activation threshold / long-press `:715`, teardown `:780`, `.fl-hire-dragging` touch-action class | The only Pointer Events drag in the app. It is **hard-wired to hiring** (tile selectors `:651`, `_hireDrop` `:841`), so it cannot be imported as it stands. |
| Needs-you inbox | `static/js/mobile.js:272,369` `_waitingOnYouHTML` (Conversation redesign §1a/§1b) | Covers **agent sessions** that are waiting, not Desk items. |
| Config flags | `server.py:75` `_load_config` defaults dict; the frontend reads `window._globalConfig` (`steward.js:31`) | The precedent for a `desk_v1` flag. It does not exist yet. |
| Theme tokens | `app.css:1-130`: all ten tokens the doc names exist in **three** tones: default (dark), `body.tone-warm` (cream), `body.tone-editorial` | The doc says "dark and cream"; there is also a third tone. |
| Tests | `tests/test_desk*.py` (8 files), `tools/smoke/desk.mjs`, `tools/smoke/drag-to-hire.mjs` | The legacy Desk and the hire drag both have smokes that must stay green. |

---

## 2. Entity gap table (derived from THE_DESK_V1_UI.md; spec §13 DAT-01 pending)

| # | Entity v1 needs | Status | Closest existing code | What is missing |
|---|---|---|---|---|
| 1 | **Content family** (CNT-01): one piece (article/video/post) with N destination versions | **missing** | A `social_queue` row (`project_routes.py:1725`) is one body on one platform. `signal_id` loosely groups rows that came from the same signal. | A family id, kind (article/video/post), title, preview, attached assets, and the list of its versions. Queue rows are also stored per project, while v1 families belong to a campaign. |
| 2 | **Version per destination**, with 15 states (§9, LIF-01/02/03, DAT-02) and revisions rN | **partial** | Queue row: `platform`, `voice`, `body`, `status` (5 values, `:1651`), `edited`/`edit_count`/`edited_lines`, `reworked_from`. | The state machine: 5 states exist and 15 are needed, and there is no Blocked/Held/Sending/Submitted/Verified/Unknown/Failed/Planned/Drafting/Skipped/Archived. There is no **revision number**: a rework mints a new row instead of rN+1 (`desk.js:1912`), so "Accept → r5" has nothing to increment. There is no `publish_at`: scheduling is a toast (`desk.js:1720`). There is no destination identity, only `platform` + `voice`. There is no format/aspect (9:16, 16:9). |
| 3 | **Campaign + states** (Proposed/Active/Paused/Completed/Archived + Draft) | **partial** | `desk.py:743,796`: states `proposed/running/paused/done/dropped`, thesis, voices, project_ids. | A **goal** (target number, metric, date, audience, tracked-or-not). An attached **channel set**: today it is derived from voices (`desk.py:771`). **Assumptions** (`? Assumed`) and a single **blocker** question (§3.5). A link to the policy record. The state names also differ: `running` ≈ Active, `done` ≈ Completed, `dropped` ≈ Archived, and nothing maps to Draft. |
| 4 | **Policy / rules record**: review mode, frequency ceiling, channels in/out, reply mode, paid, production budget; versioned; widening needs an authorized user (§8, AUT, INS-03) | **missing** | Voice `banned`/`never_claims` (`desk.py:332-333`) and platform rules (`:537`) are **content** rules, not authority. Simplification plan step 4 (`bounds_hash`, human-only approve, nonce) is designed but **not built**. | Everything. The record, its version, the widen/narrow diff, the human-only gate (`mc/unattended.py:63` is the building block), and rule chips generated from it. |
| 5 | **Approval bound to revision + destination + link + schedule scope + policy version** (CNT-06, §5 binding) | **missing** | `approve_social_queue_item` (`project_routes.py:1852`) flips `status` and stamps `decided_at`/`decided_by`. | The binding record, invalidation on a new revision, a time-shift window (for calendar reschedule), and the policy version. |
| 6 | **Claim + source validation** (KNW-02, CNT-07): claims marked in text, sources attached, reviewer verdict supports/partial/doesn't, proposed revision diff | **missing** | The nearest things are the Queue "checks" (`desk.js:1440` `_deskQueueRunChecks`: repeat guard + length) and voice `never_claims`. | Claim extraction, a claim↔source store, the validator role (a Posy reviewer dispatch), verdicts, the inline diff, and the approval gate "disabled while any claim is blocked". |
| 7 | **Production job + budget ledger** (MED-04/06, ADS-03): rate, estimate, maximum, atomic reservation, render states | **missing** | Only a per-post X cost estimate (`desk.js:1396` `_deskReleaseCost`) and a cost line in the platform-rules text. There is no video pipeline anywhere in `mc/`. | Everything. R0 runs against a simulated provider. |
| 8 | **Conversation thread** (CON-01/02/03): our posts / mentions / discussions, proposed reply as-sent, take over | **missing** | `desk.js:1896` "Thread with Posy" is a **push-back thread with the writer**, not a platform conversation. Different thing, same word. | Inbound reads (no platform read path exists), reply drafts, freshness, the take-over latch, and coverage-gap reporting. |
| 9 | **Needs-you item / hold** (§2, E13): typed (approve/watch/reply) + holds (disconnected, worker offline) with a deep-link target | **partial** | Desk count `desk.js:779` `_deskPendingCount` (pending+approved queue rows). The app-wide "Waiting on you" inbox (`mobile.js:369`) covers agent sessions only. | A typed item with a target (version/thread/job), obsolete/resolved exclusion, hold records, and a **worker heartbeat** (there is no worker to beat). |
| 10 | **Channel = destination identity** (platform + account/page) **with capability** (direct / manual handoff / held) (UX-02, §9) | **partial** | Voice `platform` + `destination` (`desk.py:318-330`); X token presence (`desk.js:1432`); manual handoff routes (`desk.js:1582-1709`) are implicitly the "✋ You publish it" capability. | A first-class channel record (identity, display badge, capability, connection health, hold reason). Capability × permission copy (§9). Settings → Connections. Destinations that are not X/LinkedIn (the frames' "Clayrune blog") have no representation at all. **See conflict C2**: v1 has no voice surface, and the learning loop is keyed on voice. |
| 11 | **Material / asset** (MED-01): upload / connect-reference / record / create, with type-size-codec validation and thumbnails | **partial** | A queue row's `media` path list with its allowlist (`project_routes.py:1667`); agent media gallery (`media_routes.py:14`). | An asset record (kind, source, validation result, preview-or-not per U11), upload processing, connect references, recording, and attachment to a family (AttachAsset). |
| 12 | **Command + undo log** (§10: AttachAsset, CreateContentRevision, add destination, ProposeCampaign; every drop → toast with Undo) | **missing** | None. Every Desk write is a direct PATCH. | A command layer with an inverse for each command. R0 can hold it client-side over fixtures. |
| 13 | **Posy instruction** (INS-01..04): scoped to a selection, before→after, affected items, Undo; confirm if it widens authority; durable instructions become rule chips | **missing** | `dispatch_rework` (`desk_routes.py:466`) is async and whole-draft, and lands as a new row. It has no scope, no before/after and no undo. | A scope model (campaign/card/version/paragraph/scene), a synchronous or streamed revision proposal, the widen check against the policy record (#4), and promotion to a durable rule. |
| 14 | **Comment** anchored to a passage, answered by Posy in a thread | **missing** | None. | An anchor model (revision + range) and the thread. |
| 15 | **Publication record / receipt** with honest outcome states (Verified / You reported / Submitted-verifying / Unknown) | **partial** | Ledger `desk.py:891` (manual) plus `desk_publish` receipts (`:272` permalink). The queue row's `posted` status + `ledger_post_id` (`project_routes.py:1919`). | Verification (read-back), the distinction between reported and verified, the Unknown outcome state, and a per-**version** link (it is per queue row today). |
| 16 | **Goal metrics + costs** (MET-01/02): measured vs delayed vs n/a; costs by category | **partial** | `record_outcome` (`desk.py:929`) stores a free-form dict. Nothing calls it (plan §1.1 row 9). | A metric source + freshness, the conversion event ("tester signup", doc §14), a forecast labelled as an estimate, and the cost categories. |
| 17 | **Scheduling in the user's timezone** (§3.3, E-series) | **missing** | The scheduler uses the host's local tz (`scheduler_routes.py:337`); the browser uses the device's tz. | A `user_timezone` setting. A phone on a remote console in another tz would currently show different times than the host schedules at. |
| 18 | **Publication worker** (one active, heartbeat, dispatch rechecks) | **missing** | `desk_publish.publish` exists but is unwired. The agent scheduler loop is the precedent. | The worker (simplification plan step 6), its heartbeat, and the offline hold. |
| 19 | **Project signals → Posy suggestion chips** (KNW, §2) | **exists** (backend) | Signal feed + triage (`desk.py:176`, `desk_routes.py:534`). | Only reshaping: triage outputs per-signal picks, and v1 wants ≤3 promote-box suggestions and a Proposed campaign (plan step 7). |
| 20 | **Voice learning** (not in the v1 UI doc) | **exists** | `record_edit` + `voice_brief`. | It needs a home in v1 (conflict C2). |

---

## 3. Reconciliation with in-flight work

(Added in the next commit.)

---

## 4. Spec IDs the UI doc cites: all **needs spec**

None of these are defined in-repo. They are listed so the reconciliation pass can walk them one by one when `docs/THE_DESK_SPEC_v1.md` lands.

- **Data:** DAT-01 (entity list: this file's §2 stands in for it), DAT-02 (state per version).
- **UX:** UX-01 (glyph + word), UX-02 (destination identity), UX-03 (ambiguous drop → which campaign), UX-04 (drop preview, nothing publishes), UX-05 (keyboard/click path, invalid targets explain).
- **Content:** CNT (approval binding), CNT-01 (family), CNT-02 (complete item), CNT-04 (show changes), CNT-05 (Publish now in ⋯), CNT-06 (new revision invalidates approval), CNT-07 (claims).
- **Media:** MED (source handling), MED-01 (four intake tiles), MED-03 (scene scope), MED-04 (render cost card), MED-05 (approve only rendered), MED-06 (job states).
- **Knowledge / instructions:** KNW (project signals), KNW-02 (claims need evidence), INS-01 (scope), INS-02 (durable → rule chip), INS-03 (widening confirms), INS-04.
- **Campaign:** CMP-03 (untracked goal is a warning, not a blocker), CMP-05 (start sheet; starting approves nothing).
- **Conversations:** CON-01 (source switch), CON-02, CON-03 (actions, take over).
- **Lifecycle / metrics / money / authority:** LIF-01, LIF-02, LIF-03 (state vocabulary), MET-01 (never fake 0), MET-02 (cost-per-outcome only when complete), ADS (production costs), ADS-03 (atomic reservation), AUT (widening needs an authorized user), INT (availability → hold).
- **Migration:** MIG-04 (legacy read-only while the flag is on).
- **Usability scenarios:** U01–U16 (R0 exit). Individually cited: U02 (drops don't publish/spend), U11 (never fabricate a preview), U14 (stale thread holds Send), U16.
- **End-to-end checks:** E01–E15. R1 exit cites E01, E02, E03, E04, E08, E10, E11, E12, E13, E15. Individually cited: E12 (missing metrics), E13 (worker offline hold).
- **Spec sections:** §5 (approval binding), §9 Scheduling (DST ambiguity), §11 (paid terms), §13 (DAT-01), §15 (releases), §17 (decisions to confirm).

---

## 5. Design-vs-codebase conflicts

Decisions for Ron are marked **(Ron)**. The rest have a recommendation the R0 plan already follows.

- **C1 (Ron): default review mode vs the standing per-campaign position.** Standing position 2026-09-22/23: Ron approves a campaign once and its posts publish on cadence without per-post taps. v1 defaults to `You approve each piece`, CMP-05 says "Starting doesn't approve any piece", and per-campaign survives only as the optional `Approve themes, then run`. Both modes fit in one policy record. The question is which one is the **default**, and whether the position is superseded. If the spec says per-piece, the position needs recording as superseded (standing-position rule).
- **C2 (Ron / spec): voices have no home in v1.** The v0 Desk is built around voices (`personal`/`product`, with a learned register and rewrites). v1 surfaces only **channels** (`𝕏 @ron`, `in · Clayrune page`). Recommendation: a channel identity *carries* a voice, 1:1 by default, and the voice editor lives under the channel in Settings → Connections. `record_edit` keeps learning per voice. Without a decision, R1 either orphans the learning loop or reinvents it.
- **C3 (Ron): scope beyond X + LinkedIn and replies.** The simplification plan's binding line reads "No reply automation. X and LinkedIn only." The frames show a **blog** destination (manual handoff) and a Conversations tab with `Send` and an `Auto-answer verified FAQ` rule (off by default). Manual destinations and human-sent replies arguably fit the old line. Auto-answer does not. Doc §14 already lists "whether R1 sends replies". Add "is auto-answer ever allowed" to that ask.
- **C4 (data model, needs spec): revision vs new row.** v1 approval binds to revision rN of one version. The backend's rework mints a **new queue row** (`reworked_from`). R0 is unaffected (fixtures). R1 needs a version+revision store, which also means leaving `social_queue` on the project record (a per-project store vs per-campaign families).
- **C5 (spec): publish failure.** The plan auto-pauses the whole campaign. v1 marks the one version `✕ Failed`/`⚠ Held`. The spec decides.
- **C6 (inputs missing):** the authority spec docx and `Simplified Dashboard.dc.html` are not in the repo. So **11a (Home)** and **11d (video intake)**, which R0 tickets 1 and 5 must match, have no frame. **Results (§7), the rules popover (§8), the Proposed state (§3.5) and phone (§11) have no frame in Turn 12 at all.** Those tickets build from the text and get a design check before merge.
- **C7 (reuse is weaker than the doc assumes):** "import the chat pieces, don't fork" works by CSS class (the legacy Desk already does this), because no importable JS renderer exists. The floor.js drag machine is hire-specific. Both need a small extraction in ticket 0 (R0 plan T0b). The floor.js extraction puts `drag-to-hire.mjs` at risk and has to re-run it.
- **C8 (host):** the Desk is a centred modal clamped to 1080px (`desk.js:741`). v1 wants full-width pages and a full-width review. Recommendation: keep the `__desk` modal-window host so minimize/restore/z-order keep working, open it maximized when `desk_v1` is on, and have v1 own the whole body with its own `‹ parent` router. That is reversible; no new top-level view.
- **C9 (minor):** there are three theme tones, not two (`tone-editorial` exists). The §13 contrast check should cover all three. There is no user timezone setting (#17). R0 adds `user_timezone`, defaulting to the host tz.
