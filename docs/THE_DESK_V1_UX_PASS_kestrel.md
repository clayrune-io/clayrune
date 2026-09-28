# Desk v1 UX pass: Kestrel's independent review

> **INPUT, not authority (2026-09-28).** Reconciled with Merrin's spec into the single binding build spec,
> `docs/THE_DESK_V1_UX_PASS.md`, per Ron's decisions of 2026-09-28: this review's 4-step in-page setup, graduation
> rule, Posy lifecycle and extra blockers were adopted; its header project switcher (§2) was not (Ron chose one Desk
> with a `Projects: All` filter). Build from the spec; keep this file for its reasoning.

2026-09-28. **Verdict: the interface assumes a campaign exists before it helps someone make one.** Narrowing the window helps, but the primary repair is a reliable path from an intention to a reviewable plan, then a separate operating view.

## Evidence and limits

- Baseline: `d9f9ff161c1ce0f977df8e43f08b15e9d7b89830`; all source line references below refer to that revision. Reviewed independently without reading Merrin's spec or Tilda's changes.
- Inspected all three supplied images: `data/uploads/agent_4175e5c7c4.png` (expanded Home), `agent_6fce437ae7.png` (empty Proposed campaign), and `agent_77f1e42ebf.png` (mostly empty Start sheet).
- Read `docs/THE_DESK_V1_UI.md`, the relevant renderers, fixtures, and Home/rules smokes. This is a source-and-screenshot review, not a browser reproduction or usability test. No code changed; no smokes run.
- R0 remains fixture-only. Recommendations below describe interactions to validate with fixtures before real R1 persistence, generation, or publishing. A simulated task must be labelled as simulated.
- Preserve the established campaign-level approval decision: a human approves bounded plan and voice once; routine posts run within those bounds; widening requires renewed approval. Existing per-piece defaults and Start-sheet copy conflict with that decision and need reconciliation before R1.

## 1. The menu opens across the full dashboard

**Cause:** `static/js/desk-v1-shell.js:175` initially clamps width to 1080, then `:200` deliberately maximizes the modal. `static/css/desk-v1.css:205` gives Home full height; `:254` stretches the main area and `:257` divides cards into three equal columns. Together these explain the broad empty middle, remote Needs you column, and distant shelves in screenshot 1.

**Recommendation:** open at the existing comfortable 1080px width, capped to the viewport; keep maximize as an explicit choice. When maximized, constrain Home's content to roughly 1200px, with Needs you adjacent to the cards. Keep the bottom shelf accessible, but do not force a sparse Home to consume the entire desktop height. Let the calendar and media workspace use wider space when useful. On phones retain full-screen navigation and docked actions; test 390px, 1080px and 2000px widths plus 200% text zoom. The exact width is a design starting point, not a measured optimum.

This follows **HIG Layout: grouping, hierarchy and adaptability**. Reducing font size or adding decorative content to fill the gap would leave the navigation problem intact.

## 2. Project switching and campaign creation are hard to understand

**Cause:** this is partly missing behavior, not discoverability. `static/js/desk-v1-home.js:525` makes the scope button display an R1-placeholder toast; `:561` hardcodes its label. `_createProposedCampaign` at `:71` has no project identity and creates empty goals/channels; `:449` immediately navigates to it. The prompt becomes a truncated name rather than a retained brief (`:73`). The UI asks someone to know what “Propose” will produce without demonstrating it.

**Recommendation:** show a labelled `Project: <name>` switcher in the persistent Desk header, including campaign pages. R0 needs two distinct fixture projects to validate switching now. Filter campaigns, suggestions, material and Needs you consistently; preserve each project's unfinished work. Separate owning project from destination account: a project selects what is being promoted, while a channel selects where and as whom it appears. Campaign approval must also name any additional source projects.

Use `Create campaign` as the entry action with one line of help: “Tell Posy what you want to achieve; you will review the plan before it runs.” Carry the selected project into setup, allow correction there, and retain the entire brief separately from the editable short title. A compact campaign picker within the project avoids repeated trips Home. Cross-project aggregation can wait; functional switching cannot.

## 3. Campaign deletion is difficult to find

**Cause:** the campaign card is one navigation button with no management actions (`static/js/desk-v1-home.js:318`); the active summary offers only Pause (`static/js/desk-v1-campaign.js:118`), and Proposed offers Start (`static/js/desk-v1-rules.js:52`). The existing Archive menu (`static/js/desk-v1-campaign.js:433`, `:523`) applies to a content family, not the campaign. These are different objects.

**Recommendation:** provide a labelled campaign `More` menu on both its Home card and header: Rename, Duplicate plan, Archive, and Delete draft. Do not nest a menu button inside the existing card button. For an unstarted draft, explicitly name the campaign and contents being discarded and offer Undo. For a campaign with publication history, Archive is the normal removal action: stop future work, retain receipts/results, and expose an Archived view. Explain that neither action removes posts already on a platform. Any permanent history deletion needs a separate, explicit consequence summary.

This follows **HIG Accessibility: familiar actions and alternatives to gestures**. A drag-to-trash gesture alone would not solve the reported problem.

## 4. Asking Posy gives no credible working indication

**Cause:** there is no asynchronous task here. `static/js/desk-v1-rules.js:572` handles input synchronously; `:579` constructs “applied” text and `:584` optionally adds a rule chip. `:592` replaces the reply HTML. It neither generates a plan nor tracks accepted/running/failed work. A spinner alone would imply work that R0 does not perform.

**Recommendation:** validate an explicit fixture lifecycle: Sending, Accepted, Working, Needs your answer, Ready, Failed/cancelled. Show the submitted request immediately in a campaign conversation, with text status and an accessible live announcement. Simulate known stages such as “Preparing a draft plan” without fictional percentages or completion estimates. On completion, link to the changed plan/piece and show the actual difference; on failure, preserve the request and offer Retry. Allow cancellation where supported and suppress duplicate submission while acknowledgement is pending.

Task status must survive tab changes and remain discoverable on the Home campaign card. In R1, Accepted means the server saved the request and returned a task identity; reconnecting resumes that identity. Nothing should say “applied” until a concrete change exists. This follows **HIG Loading: communicate progress and permit other work while waiting**.

## 5. Ask Posy text disappears after switching tabs

**Cause:** there are two separate losses. Send clears the textarea before calling its handler (`static/js/desk-v1-kit.js:374`). Tab clicks navigate away (`static/js/desk-v1-campaign.js:178`); the router empties the body (`static/js/desk-v1-shell.js:120`). On return, `static/js/desk-v1-campaign.js:688` constructs a fresh composer from fixture suggestions. `_ensureState` (`:49`) stores filters and selection, not draft text or conversation. The before/after reply exists only in DOM HTML (`static/js/desk-v1-rules.js:564`).

**Recommendation:** preserve both an unsent draft and a submitted conversation/task record. Key them by project, campaign and stable target ID, not a display label or shared textarea ID. Save on input, restore on navigation, and clear the draft only after acceptance; an error retains it. Keep submitted messages and results visible independently of the draft. Scope changes must not silently retarget text already being composed. R0 can model this in client state; R1 requires durable storage and visible saving/failure states.

Acceptance should cover typing then changing tabs, sending then changing tabs, switching campaigns/projects, changing selection, and returning after a failed send. Refresh/reopen durability is an explicit separate requirement, not implied by tab preservation. This follows **HIG Entering data: minimize repeated entry**.

## 6. First campaign setup and ongoing operation need different flows

**Cause:** the UI authority's section 3.5 mandates the same page for Proposed and active campaigns. The implementation assumes `proposedDetail` exists: `static/js/desk-v1-rules.js:44` falls back to empty objects, and `:58` renders blank editable fragments around “by.” Only the seeded campaign has the rich detail (`static/js/desk-v1-fixtures.js:551`). Newly created campaigns never acquire it. `static/js/desk-v1-rules.js:173` reads authority from that separate object, shows dashes (`:180`), and still permits activation with an empty policy (`:220`, `:228`).

**Recommendation: a resumable, four-step setup within the campaign page.** Use a visible progress checklist and one primary next action. It is work on the actual campaign, not a tour overlay or an unstructured chat interview. Prefill facts from the chosen project, mark inferred values, and ask only for missing decisions. Keep Save and leave / Back available. Experienced users may collapse explanations and edit the same checklist directly; they cannot bypass required authorization.

1. **Purpose:** “What are we promoting, and who should care?” Confirm project, full brief, audience and desired outcome. Offer examples and “Help me choose.” A numeric target or tracking integration is optional; a plain-language objective is enough to draft. Treat an ongoing presence campaign as valid, not a forced launch with a fabricated signup target.
2. **Destinations and voice:** select the exact account/page, identify who is speaking, and show a sample voice. Keep connection/manual-handoff status beside each destination. Let planning continue without a connected account; require a viable publishing or handoff path before activation.
3. **First plan:** Posy proposes an understandable angle, a small initial set of actual draft examples, and a suggested cadence. Show why each serves the purpose and what evidence/material it uses. Let the user revise with plain language. Model working, clarification and failure states here. Do not jump from “create” to an empty summary.
4. **Review and start:** summarize the agreed plan and voice, accounts, allowed source projects, cadence ceiling, end date and/or post cap, spend/generation limits, reply behavior and stop conditions. Every required limit has a value or explicit “Off”; unresolved requirements link back to the appropriate step. State when the first action is planned and what happens automatically. The human approves this bounded plan once, with Pause always available.

**Graduation:** incomplete information is `Draft / Setup`; a populated plan awaiting approval is `Proposed / Ready to review`; a validated plan plus explicit human approval becomes `Active`. Use the existing lifecycle vocabulary, with setup progress as separate UI state. Viewing a summary or receiving a Posy reply is not graduation. No measured result or first published post is required to enter the operating view.

**Empty campaign:** show “Let's build your campaign,” its saved brief, checklist progress and one concrete next action (for example “Choose where it will run”). If generation is in progress, show that task. If it failed, show recovery. Do not show blank goal arithmetic, performance widgets or an actionable Start sheet. Missing measurement is an explanatory note, not a blocker. These recommendations explicitly revise section 3.5, rather than pretending the current authority already requires a wizard.

**Ongoing campaign:** open to “Next action / Needs you / Recent outcome,” followed by the queue and publication history. Keep Content, Conversations and Results available in a persistent campaign frame, plus plan/rules, Pause/Resume and Archive. Routine release follows the approved cadence; avoid a new approval ritual for every post. Changes within the plan show their effect and are logged; widening any bound presents the changed terms for human approval. A recurring campaign renews its date/post cap through a short review of changed terms, not the entire novice setup. Results distinguish measured zero, delayed and untracked.

**Useful precedents (official documentation checked 2026-09-28; not hands-on product tests):**

- **Mailchimp:** its [regular-email creation checklist](https://mailchimp.com/help/create-and-send-regular-email/) makes recipients, sender, subject and content concrete tasks, editable in any order before final review. Copy visible completeness and direct correction links, not email-specific fields. The implication for Desk is a readiness checklist, not a blank summary that asks for consent.
- **Buffer:** [Home's First steps](https://support.buffer.com/en-us/articles/using-buffer-home-WXHZkDiPYb) puts channel connection and first-post creation in the workspace. Its [publishing guide](https://support.buffer.com/en-us/articles/getting-started-with-buffers-publishing-features-adhqleECyT) leads from channels to creating and scheduling. Copy the concrete first outcome and persistent place to continue, then transition to queue management. These are design inferences from documented flows.

This follows **HIG Onboarding: teach through doing, keep help contextual, defer nonessential setup**. Required approval is a campaign action; instructional help around it can be optional.

## Additional blockers to managing a campaign

- **Pause has no lasting Resume path.** `static/js/desk-v1-campaign.js:118` disables Pause when inactive; `:130` only handles active campaigns. Home similarly handles only active campaigns (`static/js/desk-v1-home.js:528`). Undo is not ongoing management. Add explicit Resume, show upcoming work affected, and revalidate expired authority before resuming.
- **Two descriptions of the same plan can disagree.** Goal editing uses `goalSentence` and updates only selected fields (`static/js/desk-v1-rules.js:85`); changing the date label at `:90` does not update the canonical deadline. Start reads separate `detail.authority` (`:173`), while rule controls edit campaign data. Derive display and approval summary from one validated plan. New campaigns currently lose even their temporary `goalSentence` edits on rerender because the fallback object is never attached.
- **Tabs behave as departures.** Content's tab buttons push full-page routes (`static/js/desk-v1-campaign.js:178`; `static/js/desk-v1-shell.js:34`). Keep project/campaign identity, tabs and Posy available while changing the active panel. A “tab” should not require breadcrumb travel to reach its siblings.
- **Fixture success is not a creation-flow pass.** The Start-sheet smoke opens rich `camp-2` directly (`tools/smoke/desk-v1-rules.mjs:239`). Add an acceptance journey beginning with a blank campaign and a second project, including interruption, recovery, deletion and Pause/Resume. Preserve per-version status and exact destination identities; those existing strengths matter once a plan exists.

## Verification needed before un-parking R0

Run the existing smokes after the changes, plus the blank-campaign journey above. Observe the UI authority's five first-time users creating and reviewing a draft within five minutes; also ask each to identify the project/account, explain what Start authorizes, recover a request after navigation, and stop/resume the campaign. Do not treat those usability outcomes as already verified. Check keyboard focus restoration, text status announcements, phone action reachability, and both themes without claiming contrast measurements from screenshots.

## Top three recommendations, ranked

1. **Replace empty Proposed with saved setup and a readiness gate.** One canonical plan feeds the populated approval summary; a campaign cannot start with missing authority.
2. **Make Posy accountable across navigation.** Preserve drafts, submitted requests, honest task state and concrete results; validate the failure path in fixtures.
3. **Finish the management frame.** Working project/campaign switching, compact layout, contextual deletion/archive and durable Pause/Resume belong in R0 acceptance, not deferred R1 polish.

## Questions for Ron (nonblocking)

1. Which should be the first usability scenario: a bounded launch, or ongoing project updates? Recommendation: ongoing updates, because they exercise renewal and routine management as well as setup.
2. Should a campaign with publication history disappear through Archive, with Delete reserved for unstarted drafts? Recommendation: yes, preserving results and publication receipts.
