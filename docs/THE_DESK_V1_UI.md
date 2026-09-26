# The Desk v1 — implementation instructions

**For:** the local coding agent (Claude Code) working in `mission-control`.
**Authority:** `uploads/Clayrune_The_Desk_Specification_v1.0.docx` (copy it into the repo as `docs/THE_DESK_SPEC_v1.md`) owns behavior, permissions, lifecycles and data. **This document owns the UI and the order of work.** Where they disagree, the spec wins and you should flag the conflict.
**Design reference:** `Simplified Dashboard.dc.html`, **Turn 12** (12a–12f) plus the parts of **Turn 11** kept by Turn 12: Home + shelves (11a), and video intake (11d, with 12d's cost fixes). **Superseded, don't build:** Turn 9 (Board/Queue/Ledger, Release soft-lock, workflow chip), Turn 10's tab set and form-style composer, Turn 11's connected-graph board (11b).
**Replaces:** `THE_DESK_UI_BRIEF.md` (Turn 9). Keep that file for history, but mark it superseded at the top.

---

## 0. Before writing code (required)

1. **Map what exists** into `docs/desk_v1_gap_map.md`: `mc/desk.py`, `mc/blueprints/desk_routes.py`, `/api/desk/*`, `mc/desk_brief.py`, `desk.record_edit`, `static/js/desk.js`, `schedule-calendar.js`, the scheduler, persona/Bench store, credential service, and existing publication records. For each spec entity (§13 DAT-01), note: *exists / partial / missing*, with file + line.
2. **Don't break the current Desk.** Build v1 behind a feature flag `desk_v1` (settings, default off). Legacy views stay read-only when the flag is on (MIG-04). Only one publication worker is ever active.
3. **Theme tokens only** (`--bg`, `--surface2/3`, `--border2`, `--accent`, `--text`, `--text-dim`, `--green-text`, `--amber-text`, `--red-text`). The mockup's cream hexes are illustrative. Dark and cream themes must both pass.
4. **Reuse the chat pieces** from `CLAYRUNE_CONVERSATION_REDESIGN.md` (bubbles, typing dots, quick-reply chips, composer). Import them; don't fork.
5. **Status is glyph + word, always** (UX-01). Colour only reinforces. The fixed vocabulary is in §9.

---

## 1. Information architecture

```
Desk Home (11a)
 ├─ "What would you like to promote?" box  → creates a Proposed campaign
 ├─ Needs you inbox (incl. holds)          → deep-links to the exact item
 ├─ Campaign cards (drop targets)
 └─ Shelves: Channels · Material           (drag sources)

Campaign page (12a)
 ├─ Summary bar: goal+progress · channel badges · rule chips · Pause
 ├─ Tabs: Content · Conversations · Results
 │    Content has a List / Calendar toggle (12a / 12f)
 ├─ Posy box, scoped to the selection     (persistent, right column)
 └─ Add tray: Channels · Material          (bottom, collapsible)

Full-width review (12b)   ← from a content card, a calendar chip, or Needs you
Video intake + director (11d / 12d)   ← from shelf, Add tray, or ＋ New piece
Conversation thread (12c) ← Conversations tab, or Needs you
```

- **No workflow graph is ever shown in the Desk.** Cadence is a rule chip ("≤3/wk"). If the Turn-8 builder powers the loop underneath, it stays invisible here.
- **No tabs at the Desk Home level.** Home → campaign → item, one level at a time. Back is always `‹ <parent name>`.
- Posting frequency, review mode, paid on/off and reply mode are **rule chips** that open a popover. They are never form fields on the page.

---

## 2. Desk Home (frame 11a)

**Header:** `The Desk` · project scope dropdown (`Clayrune ▾`) · right: `⚙ Settings` · `⏸ Pause all`.
- **Do not show worker heartbeat on Home.** Heartbeat and connection health live in Settings → Connections. If the worker is offline or scheduling is unavailable, add a **Needs you hold** ("Scheduling paused — worker offline since 14:02 · 2 posts missed") so the user is interrupted only when it matters (INT availability, E13).

**Promote box**
- One input. It accepts typed text, a dropped file or link, and paste. There are no separate Add files / Add link buttons; dropping onto the box, or ＋ in the Material shelf, covers both.
- `Propose` → `ProposeCampaign` command → navigate to the new campaign in **Proposed** state (12a layout, see §3.5).
- Below the box, up to 3 **Posy suggestion chips** from project signals (KNW). Tapping one fills the box; it doesn't send.

**Campaign cards** (3-up grid): state (glyph + word), name, goal progress bar with numbers, channel badges.
- **Drop target** for channel badges and material. While dragging over a card, the card shows a dashed accent border and the exact result, e.g. "Drop to add **in · Clayrune page** here" (UX-03/04). On drop, show a toast with **Undo**.
- If the project has more than one campaign and the drop target is ambiguous (for example, dropped on the project header rather than a card), ask "Which campaign? ▾ / New campaign" (UX-03).

**Needs you** (right column). Group decisions by kind: `✎ N pieces to approve`, `▶ N videos to watch`, `💬 N replies waiting`. **Holds** go in the same card with a tinted background, e.g. `⚠ LinkedIn page disconnected · 2 held · Fix`. Counts exclude resolved and obsolete items. Each row deep-links to the full review surface (12b, 12c, or 12d), not to a list.

**Shelves** (bottom, full width, two panes)
- **Channels:** one badge per *destination identity* (platform + account/page), never a bare logo (UX-02). Each badge carries a capability glyph: none = can publish directly, `✋` = you publish (manual handoff), `⚠` = held/disconnected. Last item: `＋ Connect`.
- **Material:** four intake tiles, **✦ Create video · ⬆ Upload · 🔗 Connect · ⏺ Record** (MED-01), then recent assets as thumbnails.
- Every shelf item is draggable (`⠿`) **and** has a click/keyboard path: focus + Enter, or its ＋, opens `Add to… ▾` (list of campaigns + "New campaign") (UX-05).
- While an item is dragging, the source badge shows as a dashed placeholder.

---

## 3. Campaign page — Content (frame 12a)

### 3.1 Summary bar
`‹ Desk` · campaign name · state · `⏸ Pause`, then three groups:
- **Goal:** "30 tester signups by Oct 20", a mini progress bar and the current number. Clicking it opens Results.
- **Channels:** badges attached to this campaign.
- **Rules:** chips such as `Organic` · `You approve each piece` · `≤3/wk` · `Edit`. `Edit` opens the rules popover (§8).

Then tabs: `Content [n]` · `Conversations [n]` · `Results`. Badge counts are *needs-you* items only.

### 3.2 Content list
Toolbar: `All content ▾` (All · Needs you · Scheduled · Published · Blocked · Archived) · `All channels ▾` · `☰ List | ▦ Calendar` · `＋ New piece`.
- **The "All content" view shows grouped sections with headings**: NEEDS YOU · SCHEDULED · PUBLISHED (collapsed, `show ›`). A specific filter shows **only** matching items. Never mix groups under a single filter label.

**Content card** = one *content family* (CNT-01):
- A left preview: an article thumbnail, a video frame with aspect ratio and length, or post text.
- Kind + meta (`📄 Article · 640 words`, `▶ Video · 3 versions`), then the title.
- **Versions list, one row per destination variant**: `[badge: channel · format]  [state glyph + word]  · [time or detail]`. For example:
  - `𝕏 @ron · 9:16` — `✓ Published` · Tue 09:00
  - `in · page · 16:9` — `✋ Watch & review` · render r3 ready
  - `＋ Clayrune blog` — (dashed, drop slot while dragging)
- **There is no card-level status.** State belongs to each version (DAT-02).
- Primary action on the right is `Review` / `Watch & review` when any version needs you, otherwise `Open`. The `⋯` menu holds: `Add a channel version ▸` · `Move to another channel ▸` · `Duplicate` · `Skip` · `Archive`.

**Drop rules on the Content list**
| Drop | Result |
|---|---|
| Channel badge → content card | **Adds a new version** for that channel (Posy drafts or cuts it). Existing versions are untouched. |
| Material → a content card | Attaches the asset to that family (AttachAsset). No generation. |
| Material → empty list area / `＋ New piece` | Creates a new piece from that material. |
| Channel badge → empty list area | Adds the channel to the campaign (summary bar). No content is created. |

**Moving** a version to another channel is only available through `⋯ → Move to another channel ▸`, and it confirms: "Move from X to Y? The X version will be archived." Every drop previews its result while hovering and toasts with **Undo** afterwards. Nothing that is dropped publishes or spends money (UX-04, U02).

### 3.3 Calendar view (frame 12f)
- Toggle within Content. Header: `This campaign ▾ | All campaigns`, `‹ week ›`, `Week ▾ | Month`.
- **Rows = channels, columns = days.** Today is highlighted. Weekend columns are muted.
- Chip = one version: time, state glyph + word, title. States: `✓ Published` · `✓ Scheduled` · `✋ Needs review` · `⛔ Blocked` · `⚠ Held` · `◇ Planned` (dashed).
- A row header shows channel hold state (`⚠ held · reconnect`).
- **Drag a chip to another day or time to reschedule.** Allow it only if the version's approval permits time shifts within the approved window (CNT approval binding). Otherwise the drop asks "Moving this needs approval again — continue?" and invalidates approval.
- Click a chip to open the full-width review (12b).
- Always use the **user's configured timezone** from settings. Don't show a zone label unless the item's destination timezone differs; handle DST ambiguity per §9 Scheduling.

### 3.4 Posy box (right column, persistent)
- Posy's current suggestion as a bubble, plus 1–3 one-tap chips (e.g. "Make the LinkedIn cut"). Tapping a chip creates the piece or version in the list, with Undo.
- Instruction input with a **scope dropdown** `About: <selection> ▾` that follows the current selection: the campaign, a card, a version, a paragraph, or a scene (INS-01).
- Posy's reply to an instruction shows **before → after**, the affected items, and **Undo**. If the change widens authority (more accounts, paid, more frequency), it asks for confirmation instead of applying (INS-03/04). Durable instructions become visible rule chips (INS-02).

### 3.5 Proposed state (a new campaign)
Same page, with state `◇ Proposed` and primary button `Start campaign` in the header.
- The goal is a single editable sentence (dashed underlines on the editable parts: target, date, audience).
- Content cards are Posy's proposed pieces (planned or drafting).
- **Assumptions:** show inline on the relevant card or chip as `? Assumed` with a popover to confirm or edit. Don't lay them out as a form.
- **Blockers:** only a real blocker gets a card at the top of the list (`⛔ Posy's one question` with two answer buttons). Missing tracking shows as a warning on the Goal ("⚠ not tracked yet"), not a blocker (CMP-03).
- `Start campaign` opens a **review sheet** that lists the ongoing authority in plain language (accounts, frequency ceiling, dates, review mode, replies, paid, generation limits, stop conditions) and states: "Starting doesn't approve any piece" (CMP-05). Confirm → `Active`, and create a policy record.

### 3.6 Add tray
Bottom of the campaign page, collapsible (`＋ Add ▾`). It holds the same two shelves as Home, scoped to this campaign. Channels already attached are hidden or dimmed.

---

## 4. Full-width review (frame 12b)

Opens **full width** (replaces the page; no drawer over an inactive board). Header: `‹ <campaign>` · kind + `n of m to review` · `👁 Review | ✎ Edit text` · `Show changes ✓` · `‹ ›` (steps through needs-you items).

**Body** (reading width about 640px for articles; large player for video):
- The complete item (CNT-02): headings, links, images, embedded media.
- `Show changes` highlights additions and deletions since the user last reviewed (CNT-04).
- **Edit text** mode makes the body editable in place, with autosave and conflict handling if another editor changed the revision.
- **Selecting any passage** (in either mode) shows a floating toolbar **below the selection**: `Ask Posy ▾` · `✎ Edit` · `💬 Comment`. `Ask Posy ▾` offers "Shorter · Less technical · Rephrase · Custom…", scoped to the selection.
- Paragraph comments are anchored; Posy answers in a thread next to them.

**Claims** (KNW-02, CNT-07)
- Any claim without supporting evidence is marked in the text and gets an inline block bar: `⛔ No source for this.` + `Fix with Posy` · `Remove` · `Add source`.
- **Adding a source does not clear the block.** Run a validation step (the Posy reviewer role): does the source support the claim *as written*? Show the result inline:
  - Supports it → `✓ Source supports it` → block clears.
  - Partially → `⟳ Source checked` with the finding and a **proposed revision as an inline diff** (strikethrough old / highlighted new), plus `Accept → rN` · `Edit it myself` · `Remove sentence`.
  - Doesn't support it → `⛔ Source doesn't support this` + the reason.
- Accepting creates a **new revision** and invalidates any prior approval (CNT-06 binding).

**Right rail** (about 300px)
- For manual-handoff destinations, first: `✋ You publish this one. Clayrune can't post to <destination>. Approving creates a task with the text, images and link ready to paste.`
- Where · When (dropdown, user timezone) · Link · (Why: goal), kept short.
- Claims list with state glyphs.
- Posy box scoped `About: This article ▾`.
- **Actions**, at the bottom:
  - Primary label depends on capability × schedule:
    - Direct publish + time set → `Approve and schedule`
    - Manual handoff → `Approve and create publishing task`
    - No time → `Approve`
  - **Disabled while any claim is blocked or a revision is waiting**, with the reason shown under the button ("Accept or edit the restore-time revision first").
  - Secondary: `Request changes` (opens the Posy box) · `Skip` · `Archive`. `Publish now…` sits in `⋯`, never next to the primary button (CNT-05).
- Approval binds revision + destination + link + schedule scope + policy version (§5 binding). Say this once in an ⓘ tooltip; don't print it permanently.

**Video review** uses the same layout, with a player in place of the article. The primary button is only available on a **rendered** output (MED-05). A storyboard can't be approved for publication.

---

## 5. Video intake + director (frames 11d, 12d)

**Intake sheet** (from shelf tiles, Add tray, or `＋ New piece → Video`): four tiles `✦ Create · ⬆ Upload · 🔗 Connect · ⏺ Record`, a brief field ("A 40-second install walkthrough…"), and chips for the attached material.
- Create → `Make a storyboard`. Beneath it: "**No video-generation charge yet.**" followed by AI usage cost behind ⓘ. **Never write "free"** unless the business model guarantees it.
- Upload shows processing status; validate type, size and codec before accepting (MED source handling).
- Connect first creates a reference and reports what's possible: preview / import / link only. Never fabricate a preview (U11).

**Director** (full-width page, `‹ Content`):
- A segmented control shows what's in view: `Source · Storyboard · Renders` (and `Channel exports` when more than one exists).
- The player carries a **status label whenever what's shown differs from current edits**: `Previous render · r2 · 0:48 — edits not rendered yet`.
- Scene strip: widths proportional to duration. Drag to reorder, drag an edge to trim, drop a screenshot between scenes to insert. Edited scenes show a `•`.
- Posy box scoped `Scene 3 ▾ | Whole video | <version>` (MED-03). Replies state which versions are affected.
- **Pending edits** card: each change plus the new total length.
- **Render card** (MED-04, ADS production costs):
  - `Rate` (unit + price) · `Estimate` (units × rate = total) · `Maximum`.
  - One line explaining what the maximum covers ("covers one retry per version if a render fails; won't exceed without asking").
  - `Video budget left` and `After, at most`, both from the campaign's production ledger.
  - Button: `Render · up to $X`. If the maximum exceeds the remaining budget, disable it and offer `Raise budget…` (goes to rules; widening needs an authorized user).
  - Below: "You watch it before anything is published."
- Rendering creates a ProductionJob with an atomic budget reservation (ADS-03). The job states are shown in the Renders tab (MED-06).

---

## 6. Conversations (frame 12c)

A tab on every campaign, also reachable from Desk Home → Needs you, and from a `Workspace ▾` scope for all campaigns.
- **Source switch at the top of the list** (a segmented control, always visible): `On our posts · n` · `Mentions · n` · `Discussions · n` (CON-01). Each has its own permissions.
- A list row shows author, platform + age, a snippet, and a reason line with glyph: `? Question · reply drafted`, `⚑ Needs you · account problem` (escalated per reply policy), or `No reply suggested`.
- The list footer states coverage gaps ("LinkedIn mentions not available ⓘ"). **An empty list must never read as "no one is talking."**
- Thread view (max-width 720px): parent post (with open-on-platform link) → comments in order → the **proposed reply as it will be sent**, headed `Replying as <identity> on <platform>`. Click the reply to edit it.
- Actions: `Send` · `Ask Posy to revise` · `Ignore` · `Assign ▾` · `✋ Take over` (right-aligned, always visible) (CON-02/03).
- Freshness shows behind ⓘ normally. If the thread changed since the draft, or context is incomplete, **show a hold bar and disable Send** (U14).
- Take over: revoke automated sending in that thread immediately, cancel queued replies, and flag any in-flight reply. Resume requires an explicit action.

---

## 7. Results (tab)

- **Goal first:** big number / target, progress, source + freshness behind ⓘ. The forecast is labelled as such: **"Projected 26 of 30 · below target"** with "estimate ⓘ" (never "on pace for").
- **Posy's read** as a bubble: what worked, and **one** proposed next experiment with one variable. `Set up experiment` · `Not now`. Accepting creates a planned piece or experiment on the Content list.
- **What went out:** per *version* rows: channel badge · title · status (`✓ Verified published` / `✋ You reported` / `⟳ Submitted · verifying` / `? Unknown outcome`) · supporting metrics · goal contribution. Missing data shows `delayed` or `n/a` — **never 0 unless it's a measured zero** (MET-01, E12).
- **Costs, kept separate:** ads · video renders · API · other. Show cost-per-outcome **only** when every category is measured (MET-02).
- **Diagnostics** (small, last): edit counts ("You edited 5 of 7 before approval"). This is a diagnostic, never a goal or gate.

---

## 8. Rules popover (from the summary bar)

Plain language, grouped. Every change shows its effect before applying. Widening authority needs an authorized user (AUT, INS-03).
- Review mode: `You approve each piece` / `Approve themes, then run` (lists the themes, accounts and windows).
- Frequency ceiling: `Up to N a week`, with an ⓘ saying it's a ceiling, not a quota.
- Channels: included / excluded accounts (e.g. "in · Ron (personal) excluded").
- Replies: `Drafts for review` / `Auto-answer verified FAQ on <account>` (off by default).
- Paid: `Off` / on → opens paid terms (§11 of spec; out of scope for R1).
- Production budget: per job and per period.

---

## 9. Vocabulary (use exactly these words)

- **Capability × permission on channel badges:**
  - `Publishes after approval` — can post, and review mode = each piece.
  - `Publishes automatically` — only when rules authorize it.
  - `✋ You publish it` — manual handoff.
  - `⚠ Held — <reason>` — disconnected, rate limited or expired.
  - **Never** "posts automatically" while review mode is on.
- **Version states:** `◇ Planned` · `✎ Drafting` · `✋ Needs review` · `⛔ Blocked` · `✓ Approved` · `✓ Scheduled` · `⟳ Sending` · `⟳ Submitted` · `✓ Verified published` · `✋ You reported` · `? Unknown outcome` · `✕ Failed` · `⚠ Held` · `Skipped` · `Archived` (LIF-01/02/03). Never use "Released".
- **Campaign states:** `◇ Proposed` · `▶ Active` · `⏸ Paused` · `✓ Completed` · `Archived` (+ `✎ Draft` for manually started and incomplete).
- **Money:** never say "free". Say "No <X> charge yet", and disclose other usage behind ⓘ.
- **Explanations** go behind ⓘ. Keep only what the current decision needs on screen.

---

## 10. Drag-and-drop implementation

- Use Pointer Events, not the HTML5 DnD API (it doesn't fire on touch). Reuse the pointer state machine from `floor.js` drag-to-hire. Apply `touch-action:none` via a class **only during an active drag**.
- Drag ghost: a compact copy of the badge or thumbnail, rotated −3°, with an accent shadow. **Position it offset from the cursor so it never covers the target's result text.**
- Drop targets advertise themselves with a dashed accent border and one line of result text. Invalid targets dim and show why on hover (UX-05).
- Every drop → a command (AttachAsset, CreateContentRevision, add destination) → an optimistic UI update → a toast with Undo (reverses the command) → a screen-reader announcement.
- Keyboard: focus an item, press Enter, and pick the target from `Add to… ▾`. Focus returns to the origin afterwards.

---

## 11. Phone

- Home: the promote box with `📎 Files · 🔗 Link · 🎙 Say it` (no drag), Needs you, then campaign cards.
- Campaign: summary collapses to name + goal line + state; tabs stay; the Posy box becomes a composer docked at the bottom.
- Content cards are full width with the versions list kept. Tapping opens full-width review; actions sit in a bottom bar.
- Calendar on phone defaults to an agenda list grouped by day.
- Hit targets ≥44px.

---

## 12. Build order (maps to spec §15 releases)

**R0 — interaction validation (fixtures, flag on):**
1. Home: promote box, Needs you, campaign cards, shelves, drag + click paths, with fixtures.
2. Campaign page: summary, tabs, content list with per-version states, grouped "All content", Posy box scoping.
3. Full-width review: article + video player, edit mode, selection toolbar, claim block + source validation + revision diff, capability-aware primary button.
4. Calendar view with reschedule drag (fixture approvals).
5. Video intake + director with the cost card (simulated provider).
6. Conversations with the source switch, reply review and take over (simulated).
7. Results with honest data states.
Exit: U01–U16 pass on fixtures; five first-time users create and review a draft campaign in under 5 minutes (spec usability target, measured by testing, not assumed).

**R1 — controlled organic operation:** real project profile, durable campaigns and policy records, approvals bound to revisions, scheduler dispatch rechecks, two owned destinations, manual handoff tasks, basic metrics, replies drafted only, imported-video review. Exit: one live lifecycle verified; E01–E04, E08, E10–E13, E15 pass.

**R2+:** per the spec (continuous promotion, media generation + paid pilot, broader distribution).

---

## 13. Acceptance checks specific to this UI (in addition to spec U01–U16, E01–E15)

- [ ] No screen in the Desk renders a node graph or connecting lines.
- [ ] "All content" shows labelled groups; each specific filter shows only its items.
- [ ] A content family with 3 versions shows 3 independent states; publishing one leaves the others unchanged.
- [ ] Dropping a channel on a card adds a version; the existing versions are untouched; Undo removes it.
- [ ] Move is only reachable via ⋯ and confirms.
- [ ] Selecting text in review shows Ask Posy · Edit · Comment without covering the selection or the next line.
- [ ] Add source → validation runs → the block clears only on "supports" or on accepting a revision; the revision number increments.
- [ ] Manual destinations never show "Approve and schedule".
- [ ] Render button shows the maximum; disabled when maximum > remaining budget.
- [ ] Forecasts are labelled as estimates; missing metrics never show as 0.
- [ ] Conversations source switch is always visible; coverage gaps are printed.
- [ ] No "posts automatically" copy while review mode = each piece; no "free".
- [ ] Worker offline → a Needs you hold appears within one heartbeat interval; Home has no heartbeat chip.
- [ ] All times render in the user's configured timezone.
- [ ] Both themes pass contrast; status is readable in greyscale.

---

## 14. Decisions to confirm with Ron before R1 (from spec §17)

First two owned destinations · hosted vs always-on local worker · whether R1 sends replies or only drafts them · production budget ceilings · the conversion event for "tester signup" · retention periods · whether calendar reschedule may shift time without re-approval (default: yes, within the approved window).
