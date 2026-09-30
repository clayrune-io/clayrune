// Desk v1 (MC-977) — fixture data. Window-bridged module (static/js/*.js has
// no `import`; every desk-v1-*.js reads window.DeskV1Fixtures directly).
//
// R0 runs entirely on these fixtures (docs/desk_v1_r0_plan.md ground rule 3):
// no backend store, no publishing, no spend. Written ONCE here from the Turn
// 12 frames (docs/desk_v1/frames/) and the UI doc's worked example (12a) —
// one campaign, "Windows beta testers", its 3 families, 3 channels, 3
// conversations, a render job + budget, and results with deliberate
// delayed/n/a gaps (MET-01: never a fake 0). A ticket that needs more data
// adds rows inside its OWN section below — this file is not re-derived.

// ── T0a: base fixtures (docs/desk_v1_r0_plan.md ground rule 3) ─────────────

(function () {
  // ── IA1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §2.1, §5) ────
  // The standing presence one level above campaigns — Ron's own ask: "the
  // entire project is the campaign... campaign promoting a specific
  // project/product/feature/target". Two projects (clayrune, engulfing_scanner)
  // so Home's card grid and the Needs-you aggregate have more than one row to
  // prove they're cross-project, not a re-skinned single-campaign fixture.
  // `presence` carries only the §2.1 fields IA1's stub Presence route needs a
  // real project record to point at — IA3 builds the settings UI that edits
  // these; full field set (strategy, production, measurement, visual_default)
  // is that ticket's own fixture addition, not re-derived here.
  const PROJECTS = [
    {
      id: 'clayrune', name: 'Clayrune', state: 'active',
      // R2-18: the agents "hired on this project's floor", standing in for the
      // live `/api/projects` roster (a fixture id is not a Clayrune project id,
      // so `DeskV1Kit.projectAgentChoices` reads this when present). Refs the
      // install doesn't know are dropped there, so a fresh install still gets
      // `global:claydo`. `engulfing_scanner` below has none: the empty case.
      roster: ['global:claydo', 'global:dave'],
      presence: {
        accounts: [
          { channel_id: 'ch-x-ron', voice: 'Ron (first person)' },
          { channel_id: 'ch-li-page', voice: 'Clayrune page' },
          { channel_id: 'ch-blog', voice: 'Clayrune blog' },
          // R2-10 (frame 7b SOURCES tray): the second X account and the two
          // preview-only accounts (§11.6 Q1) the Where board can place.
          { channel_id: 'ch-x-clayrune', voice: 'Clayrune (company)' },
          { channel_id: 'ch-yt-clayrune', voice: 'Clayrune channel' },
          { channel_id: 'ch-discord-community', voice: 'Community server' },
        ],
        audience: 'Developers running coding agents',
        ceilings: {
          'ch-x-ron': { per_week: 3, min_gap_h: 12 },
          'ch-li-page': { per_week: 3, min_gap_h: 12 },
          'ch-blog': { per_week: 3, min_gap_h: 12 },
          'ch-x-clayrune': { per_week: 3, min_gap_h: 12 },
          'ch-yt-clayrune': { per_week: 3, min_gap_h: 12 },
          'ch-discord-community': { per_week: 3, min_gap_h: 12 },
        },
        replies: 'drafts',
        // IA revision 2 §5.3: who plans and writes for this project, asked
        // once in step 0 (R2-5, not built by this ticket — this fixture
        // just demonstrates a resolved pick). `global:claydo` is a shipped
        // default character, never the hardcoded, user-hired-only
        // `social-media-strategist` §5.3 flags as unsafe on a fresh install.
        desk_agent: 'global:claydo',
        // §5.2: renamed from `production` — `amount` is the per-period pool
        // covering both production AND publishing; `per_job`/`kinds` stay as
        // safety caps, not money pools. $100/month here is the fixture's
        // project remaining-budget case (R2-1 acceptance: a $120 earmark
        // against this project is short by $20).
        budget: { amount: 100, period: 'month', per_job: 20, kinds: [] },
        // §5.3: measurement entries gain `kind: 'manual'` (the user types
        // the current number; dated) — the only kind that exists today.
        measurement: [{ event: 'tester signups', source: 'manual count', kind: 'manual', at: '2026-09-24T18:00:00Z', value: 11 }],
      },
    },
    // K1: @ron on X is a workspace asset shared across projects, never
    // duplicated per project — this project's own accounts[] reuses the SAME
    // ch-x-ron id the clayrune project above also binds, at its own ceiling.
    {
      id: 'engulfing_scanner', name: 'Engulfing scanner', state: 'active',
      roster: [],
      presence: {
        accounts: [{ channel_id: 'ch-x-ron', voice: 'Ron (first person)' }],
        audience: 'Day traders evaluating signal tools',
        ceilings: { 'ch-x-ron': { per_week: 2, min_gap_h: 12 } },
        replies: 'drafts',
        // desk_agent deliberately absent — demonstrates R2-5's "unresolvable
        // agent" fallback case (no pick made yet), unlike clayrune above.
        budget: { amount: 40, period: 'month', per_job: 20, kinds: [] },
        measurement: [],
      },
    },
  ];

  // R2-6: camp-1's `how` (strategy/angle/agent/budget) used to be duplicated
  // between `campaign.how` (kit.js deskAgentRef's read) and a separate
  // `plan.angle` string kit.js never read — one object now, `campaign.how`
  // and `campaign.plan.how` both point at it, so kit's two different `.how`
  // readers (deskAgentRef on campaign.how, validatePlan/nextBoundsHash on
  // plan.how) see the same data instead of drifting.
  // R2-6: budget starts 'none' — R2-1 gave this object a `budget.source:
  // 'project', amount: 60` placeholder before any UI ever read or wrote it;
  // that value was never approved as a bound (the How stop's budget picker,
  // and `camp.approval.bounds` below, are both new in this ticket). Starting
  // both at 'none' means the fixture opens in the plain (non-awaiting) ⑥
  // state, so the R2-6 smoke's own budget edit is what first creates a
  // divergence from `camp.approval.bounds` — not a pre-existing one.
  const _CAMP1_HOW = {
    strategy: 'Show the beta working end to end, not just announce it.',
    angle: 'Show the restore-points and install flow working end to end so trying the beta feels low-risk.',
    // R2-6 (§11.3 item 2): matches mockup frame 4's fixture value exactly.
    never_claim: 'Feature completeness on ARM (not verified yet)',
    agent: null,
    budget: { source: 'none' },
  };

  const CAMPAIGNS = [
    {
      id: 'camp-1',
      state: 'active', // proposed | active | paused | completed | archived | draft
      // IA1 §2.2/§3#40: the field Ron's ask turns on. `subject.kind` picks
      // the project-page card glyph (◉ project · ▣ product · ✦ feature ·
      // ◎ audience/event); this campaign promotes an AUDIENCE, not a feature.
      projectId: 'clayrune',
      subject: { kind: 'audience', label: 'Windows users trying Claude Code' },
      // §4/Dave review pass 1: `target`/`deadline`/`outcome` (was `metric`)
      // are NOT duplicated here — the summary bar reads those from
      // `plan.goal` below, the single canonical copy a plan edit (rules
      // popover, Resume revalidation) actually writes to. `current` stays:
      // it's live progress, which has no field in §4's plan shape at all.
      //
      // IA revision 2 §5.1: the rest of this object (`metric` on down) is
      // the NEW measurable-goal shape, additive onto the same `goal` object
      // rather than a second copy — `current`/`entries` is the live series,
      // `metric`/`target`/`source` is what R2-4's goal editor (not built by
      // this ticket) will read/write. `plan.goal` below is untouched: the
      // R0 setup wizard and results panel still read it until R2-3/R2-4
      // migrate them off it.
      goal: {
        current: 11, metric: 'tester signups', target: 30, baseline: 0,
        unit: 'signups', horizon: 'short', deadline: '2026-10-20',
        source: 'manual', entries: [{ at: '2026-09-24T18:00:00Z', value: 11 }],
      },
      // §5.1: the approval's finite window (§9 Q2 binding: ≤90 days; a
      // `long` horizon goal above would renew terms until `goal.deadline`).
      // camp-1's own `short` horizon needs exactly one term.
      term: { index: 1, starts: '2026-09-01', ends: '2026-10-20', post_cap: null },
      how: _CAMP1_HOW,
      // §5.1: replaces `setup {step, done}` (a Draft-only concept the R0
      // setup wizard still owns via `camp.setup`, untouched here) — `map`
      // is the R2-3 stepper's own state, additive, unread by anything yet.
      map: { stop: 'launch', done: ['goal', 'how', 'what', 'when', 'where'] },
      // IA2 (THE_DESK_V1_IA_REVISION.md §3): `customChips` (row 11) is the
      // only field this object still owns — the per-piece approval toggle,
      // the weekly-count duplicate, the reply-mode toggle and the paid
      // duplicate (rows 7-10) were duplicates of a project/plan field or
      // had no replacement; deleted, not merely emptied.
      rules: {},
      // ── T2 (MC-977 UX pass §4/§6.2): Pause/Resume gates on `validatePlan`,
      // which reads `plan`, not `rules`/`goal`. `end.date` matches
      // `goal.deadline` since this campaign has always run to its goal date.
      // IA2 §3: the fixture's `name` field retired (row 2, `plan.title` is
      // the only copy); the per-campaign channel-id list retired (row 6,
      // replaced by `plan.accounts`, a subset of the project's own
      // `presence.accounts` — camp-1 uses all 3); `source_projects` retired
      // (row 14, owner is the parent project, implicit); destination voice
      // moved to `project.accounts[].voice` (row 20) so `plan.accounts` is
      // bare channel ids; `cadence.min_gap_h`
      // moved to the project's own `presence.ceilings` (row 22).
      plan: {
        brief: 'Windows users trying Claude Code should hear about the beta and sign up as testers.',
        title: 'Windows beta testers',
        audience: 'Windows users trying Claude Code',
        goal: { outcome: 'tester signups', target: 30, deadline: '2026-10-20', tracked: true },
        accounts: ['ch-x-ron', 'ch-li-page', 'ch-blog'],
        // R2-6: `angle` retired from here — `how.angle` (below) is the one
        // copy now; `how` itself is the SAME object as `campaign.how` above.
        how: _CAMP1_HOW,
        samples: [],
        cadence: { per_week: 3 },
        end: { date: '2026-10-20', post_cap: null },
        replies: 'drafts',
        paid: false,
        generation: 'No video generation planned for this campaign',
      },
      // R2-6: the bounds snapshot `DeskV1Kit.boundsWiden`/`nextBoundsHash`
      // (kit.js, shipped by R2-1, never wired to a real caller until this
      // ticket) compares the live plan against — "what was last approved".
      // Mirrors `plan`'s own accounts/cadence/end/term/budget exactly, so
      // camp-1 opens on ⑥ in its normal (non-awaiting) state; the How
      // stop's budget picker is what first moves the live bounds away from
      // this snapshot.
      approval: {
        bounds: {
          accounts: ['ch-x-ron', 'ch-li-page', 'ch-blog'],
          cadence: { per_week: 3 },
          end: { date: '2026-10-20', post_cap: null },
          term: { ends: '2026-10-20' },
          budget: { source: 'none' },
        },
      },
    },
  ];

  // Destination identity = platform + account/page, never a bare logo
  // (UX-02). `capability` drives the badge copy in §9's vocabulary (owned by
  // T0b's kit, not duplicated here): 'direct' = can publish per the review
  // mode, 'manual' = "You publish it", and `health: 'held'` overrides either
  // with "Held — <reason>".
  const CHANNELS = [
    // T8 R0 exit cosmetic fix (MC-977): a bare space between the glyph and
    // identity read as no gap at all ("𝕏@ron", T2b review screenshot) —
    // 𝕏 (U+1D54F) carries almost no right-side bearing at badge font sizes,
    // unlike the `in` two-letter glyph below. The middle-dot separator
    // already used for LinkedIn reads correctly regardless of glyph
    // metrics, so match it here instead of widening the space (font- and
    // zoom-level-dependent) or adding CSS letter-spacing (would apply to
    // every badge, including "Clayrune blog" below, which deliberately has
    // no platform glyph to space from).
    { id: 'ch-x-ron', platform: 'x', identity: '@ron', label: '𝕏 · @ron',
      capability: 'direct', health: 'ok' },
    // "in · Clayrune page direct + held" (ground rule 3): its normal
    // capability is direct, but it is CURRENTLY held — the Home/campaign
    // Needs-you hold and the A13 worker-offline check both read this.
    { id: 'ch-li-page', platform: 'linkedin', identity: 'Clayrune page', label: 'in · Clayrune page',
      capability: 'direct', health: 'held', holdReason: 'LinkedIn page disconnected' },
    { id: 'ch-blog', platform: 'blog', identity: 'Clayrune blog', label: 'Clayrune blog',
      capability: 'manual', health: 'ok' },
    // R2-10 (frame 7b SOURCES tray; §11.6 Q1 — Ron 2026-09-29: build the
    // tiles and placeholders so the mocked-up view is there). The second X
    // account is a real publishing account like @ron. `preview: true` marks
    // the accounts that render, drag, drop and remove like any other but
    // never publish, read or authenticate (v1 PUBLISHES to X + LinkedIn
    // only); `connected: false` is the one card the tray shows as
    // `not connected · Connect ›` (→ Presence) and cannot be dragged.
    { id: 'ch-x-clayrune', platform: 'x', identity: '@clayrune', label: '𝕏 · @clayrune',
      capability: 'direct', health: 'ok' },
    { id: 'ch-yt-clayrune', platform: 'youtube', identity: 'Clayrune', label: '▶ · Clayrune',
      capability: 'manual', health: 'ok', preview: true },
    { id: 'ch-discord-community', platform: 'discord', identity: 'Community', label: 'Discord · Community',
      capability: 'manual', health: 'ok', preview: true },
    { id: 'ch-reddit', platform: 'reddit', identity: 'Reddit', label: 'Reddit',
      capability: 'manual', health: 'ok', preview: true, connected: false },
  ];

  // Content family (gap map #1): one piece, N destination versions (#2).
  // Versions carry their OWN state — the point of A3/A4 is that they are
  // independent (publishing one leaves the others untouched).
  const FAMILIES = [
    // The restore-points article — 1 claim needs a source (frame 12a).
    {
      id: 'fam-restore-points', campaignId: 'camp-1', kind: 'article',
      title: 'Undo anything: restore points in Clayrune 2.1',
      wordCount: 640,
      versions: [
        { id: 'v-restore-blog', channelId: 'ch-blog', state: 'needs_review', revision: 1,
          claims: [
            { id: 'claim-1', text: 'keeps ten snapshots automatically', source: null, verdict: 'blocked' },
          ] },
      ],
    },
    // The install video — 3 versions, 3 independent states (A3): one
    // published, one needing review, one still planned.
    {
      id: 'fam-install-video', campaignId: 'camp-1', kind: 'video',
      title: 'Install in two minutes',
      versions: [
        { id: 'v-install-x', channelId: 'ch-x-ron', state: 'verified_published', revision: 3,
          format: '9:16', publishedAt: '2026-09-22T09:00:00-07:00' },
        { id: 'v-install-li', channelId: 'ch-li-page', state: 'needs_review', revision: 3, format: '16:9' },
        { id: 'v-install-blog', channelId: 'ch-blog', state: 'planned', revision: 0 },
      ],
      render: { jobId: 'render-r3', status: 'ready', revision: 3 },
    },
    // "30 Windows testers wanted" — scheduled post (frame 12a).
    {
      id: 'fam-30-testers', campaignId: 'camp-1', kind: 'post',
      title: '30 Windows testers wanted',
      versions: [
        { id: 'v-testers-li', channelId: 'ch-li-page', state: 'scheduled', revision: 1,
          publishAt: '2026-09-30T10:00:00-07:00' },
      ],
    },
  ];

  // 3 conversations across the source switch (CON-01): our posts, mentions,
  // discussions — one of each, one deliberately stale (U14 hold).
  const CONVERSATIONS = [
    // excerpt is T6's own fix (Dave's review pass): must equal the comment
    // its own thread shows below (CONVERSATION_DETAIL['conv-1'].thread.
    // comments[0].text) — a row can't promise one question and open on
    // another. T0a's placeholder text ('Does this work on ARM laptops?')
    // predates T6's thread content and never matched it.
    // IA1 (K4): a conversation always has a project; campaign is optional
    // (not exercised by these rows — all six T0a/T6 rows belong to camp-1).
    { id: 'conv-1', projectId: 'clayrune', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-x-ron',
      excerpt: 'Does the restore include the agent’s memory or just files?', state: 'needs_reply' },
    { id: 'conv-2', projectId: 'clayrune', campaignId: 'camp-1', source: 'mentions', channelId: 'ch-li-page',
      excerpt: 'Someone linked the restore-points post in a thread about backups.', state: 'reviewed' },
    { id: 'conv-3', projectId: 'clayrune', campaignId: 'camp-1', source: 'discussions', channelId: null,
      excerpt: 'A forum thread comparing beta programs mentions Clayrune in passing.',
      state: 'stale' },
  ];

  // The render job + budget (MED-04/06): rate, spend, and the period limit
  // the T5 render card's "maximum" and "Raise budget…" disable read from.
  // `perJobLimit` is the §8 rules popover's OTHER cap ("Production budget:
  // per job and per period") — a ceiling on any single render's own
  // "Maximum" (desk-v1-video.js's `estimate * 2`), separate from `limit`'s
  // rolling per-period total. T5 doesn't read it (out of that ticket's
  // scope); it exists so §8 has a real per-job number to show and edit.
  const RENDER_BUDGET = {
    period: 'month', currency: 'USD', rate: 0.40, spent: 16, limit: 150, perJobLimit: 20,
  };

  // Results with deliberate delayed/n/a gaps (MET-01: never a fake 0; MET-02:
  // cost-per-outcome only when every category is measured — `ads` here is
  // intentionally unmeasured so T7 has a real case to withhold that number).
  const RESULTS = {
    campaignId: 'camp-1',
    goal: {
      current: 11, target: 30, deadline: '2026-10-20',
      source: 'manual count', freshness: '2026-09-24T18:00:00Z',
    },
    forecast: { projected: 26, target: 30, label: 'below target' },
    versions: [
      { versionId: 'v-install-x', outcome: 'verified_published', metric: 8, metricLabel: 'signups' },
      { versionId: 'v-install-li', outcome: 'unknown', metric: null, metricLabel: 'delayed' },
      { versionId: 'v-testers-li', outcome: 'unknown', metric: null, metricLabel: 'n/a' },
    ],
    // null = not yet measured (never rendered as 0).
    costs: { ads: null, video_renders: 16, api: 2, other: 0 },
    diagnostics: { edited: 5, reviewed: 7 },
  };

  // ── T3: full-width review (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §4) ──
  // The campaign list only needs a family/version's summary shape (title,
  // state, revision) — review needs the full body, per-paragraph selection
  // targets, the claim's validation detail and the right rail's Where/When/
  // Link. Keyed by versionId so this NEVER edits the FAMILIES rows above
  // (ground rule 3: a ticket that needs more adds rows in its own section).
  // Frame 12b is fam-restore-points/v-restore-blog (title match); its base
  // claim text ("keeps ten snapshots automatically", claim-1, blocked) is
  // kept as T0a wrote it — the paragraph below is authored around that
  // claim rather than the frame's own illustrative "under ten seconds"
  // wording, so the fixture doesn't carry two conflicting claim sentences.
  const REVIEW_DETAIL = {
    'v-restore-blog': {
      paragraphs: [
        { id: 'p-lede', text: 'Every agent run now starts with a restore point. If a run goes sideways, pick the moment before it and the project comes back — files, memory and backlog together.' },
        { id: 'p-heading-why', heading: 'Why I built it' },
        { id: 'p-why', text: 'I lost an afternoon to an agent that "tidied" a migration folder. The fix wasn’t a smarter agent; it was a cheaper mistake.' },
        // The claim sentence — the only paragraph the validator/diff touch.
        // `before`/`after` are the claim's proposed revision (§4: "a
        // proposed revision as an inline diff"); `text` is what renders
        // before any revision is accepted.
        { id: 'p-claim', claimId: 'claim-1',
          text: 'Restoring keeps ten snapshots automatically on a mid-size project.',
          before: 'keeps ten snapshots automatically',
          after: 'keeps the last ten snapshots automatically — older ones roll off' },
        // Embedded media (CNT-02): the article references the OTHER
        // family's already-rendered video rather than inventing a second
        // video fixture.
        { id: 'p-embed', embedFamilyId: 'fam-install-video' },
        { id: 'p-closer', text: 'Open to Windows beta testers. ', linkText: 'Join the beta.', linkHref: '#' },
      ],
      claims: [
        { id: 'claim-1', anchorParagraphId: 'p-claim', label: 'Restore time',
          state: 'blocked', source: null, revision: 1 },
      ],
      where: 'Clayrune blog', whenISO: '2026-09-30T12:00:00-07:00', link: 'clayrune.dev/beta',
      whyChecks: [
        { label: '2.1 is public', ok: true },
        { label: 'Open to testers', ok: true },
        // Suffix is NOT baked in here — desk-v1-review.js appends "·
        // revision waiting" only once the claim is actually 'checked'; a
        // still-blocked claim (the baseline above) has no revision yet.
        { label: 'Restore time', claimId: 'claim-1' },
      ],
      comments: [],
    },
    'v-install-li': {
      // Held channel (ch-li-page) — demonstrates the video review variant
      // (§4 "same layout, with a player") plus the held-destination case
      // (§9 "Held overrides either"), which the plan's own R0 fixtures
      // deliberately set up ("in · Clayrune page direct + held").
      posterCaption: 'Install in two minutes',
      where: 'in · Clayrune page', whenISO: null, link: null,
      claims: [],
      comments: [],
    },
  };

  // ── T1: Desk Home (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §2) ──────────
  // Simulated worker heartbeat (INT availability, E13). The doc's own worked
  // example text ("Scheduling paused — worker offline since 14:02 · 2 posts
  // missed") is reused verbatim rather than invented, so the fixture and the
  // acceptance check agree by construction. Offline by default, same spirit
  // as ch-li-page's own hold and the results file's delayed/n/a rows — R0's
  // fixtures show the gap case, not an all-green happy path.
  const WORKER_HEARTBEAT = { status: 'offline', sinceLabel: '14:02', missed: 2 };

  // Recent assets on the Material shelf (MED-01/02) — thumbnails of material
  // already in the project. Reuses the existing families' titles rather than
  // inventing new content (ground rule 3 still applies inside a ticket's own
  // section: don't multiply fixture content beyond what the surface needs).
  const RECENT_ASSETS = [
    { id: 'asset-install-video', kind: 'video', title: 'Install in two minutes' },
    { id: 'asset-restore-points', kind: 'article', title: 'Undo anything: restore points in Clayrune 2.1' },
  ];

  // Up to 3 Posy suggestion chips (KNW) below the promote box — tapping one
  // fills the box, never sends (§2).
  const HOME_SUGGESTIONS = [
    'A LinkedIn cut of the install video would reach testers who missed the X post.',
    'The restore-points article has one blocked claim — add a source to unblock it.',
    '3 posts this week is the rule; two are already used.',
  ];

  // ── T2a: Campaign page + Content list (docs/desk_v1_r0_plan.md;
  // THE_DESK_V1_UI.md §3.4) — the Posy box's current suggestion + up to 3
  // one-tap chips (frame 12a), keyed by campaignId so a later campaign can
  // add its own row without touching camp-1's. Verbatim from the frame
  // rather than invented (ground rule 3's own precedent, WORKER_HEARTBEAT
  // above): "Signups are coming from the X clip. A LinkedIn cut of the same
  // clip is the cheapest next piece." + "Make the LinkedIn cut" / "Draft a
  // follow-up post".
  const CAMPAIGN_SUGGESTIONS = {
    'camp-1': {
      suggestion: 'Signups are coming from the X clip. A LinkedIn cut of the same clip is the cheapest next piece.',
      chips: ['Make the LinkedIn cut', 'Draft a follow-up post'],
    },
  };

  // Content-card left preview (frame 12a): a short excerpt for article/post
  // kinds (the article/post families carry no body text anywhere else in
  // this file — T0a's FAMILIES rows are title + meta only). Video kind needs
  // no entry here; its preview reads format + duration off the existing
  // version/VIDEO_DETAIL rows below instead of new text.
  const CONTENT_PREVIEW = {
    'fam-restore-points': 'Undo anything: restore points in Clayrune 2.1',
    'fam-30-testers': "We're looking for 30 Windows testers who run coding agents…",
  };

  // ── T4: calendar view (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §3.3) ────
  // Calendar places a chip only for a version with a real date (never invents
  // one — same MET-01 "never fake it" spirit as T3's null `whenISO` for
  // v-install-li, which this section deliberately leaves alone so that case
  // still demonstrates an honestly-unscheduled item on the grid). Most
  // versions already carry one (FAMILIES' publishedAt/publishAt, or T3's
  // REVIEW_DETAIL whenISO for v-restore-blog); the one gap this section
  // fills is v-install-blog, whose `state: 'planned'` (FAMILIES above) has no
  // date anywhere yet — a soft target time is exactly what the frame's
  // dashed `◇ Planned` chip needs, and adding it here doesn't touch or
  // contradict any existing row.
  // v-install-li stays deliberately undated (T3's whenISO: null, left as-is)
  // — MET-01's "never invented" case the harness already asserts. Blocked
  // and Needs-review are each covered by a different, already-dated version
  // below, so nothing needs this one to also carry a date.
  //
  // Dave's review (2026-09-26) also flagged two of T0a/T3's own timestamps:
  // 'v-install-x' publishedAt and 'v-testers-li' publishAt were authored
  // with a bare `Z` (UTC) suffix, so in the host's America/Los_Angeles tz
  // they displayed as 2:00 AM / 3:00 AM instead of the frame's intended
  // 9:00 / 10:00 local — fixed in place above to `-07:00` (same instant's
  // correct local-time authoring, not a new row).
  const CALENDAR_SCHEDULE = {
    'v-install-blog': '2026-10-01T15:00:00-07:00',
    'v-followup-x': '2026-10-01T11:00:00-07:00',
    'v-arm-blocked': '2026-10-01T14:00:00-07:00',
  };

  // Two new families, additive (ground rule 3 — this file is not
  // re-derived; a ticket needing more data adds its own rows). Dave's
  // review point 3: frame 12f shows all 5 chip states at once (Published,
  // Planned, Held, Needs review, Blocked); T0a/T3's existing versions cover
  // Published/Needs review/Planned/Scheduled(->Held via the held channel),
  // but nothing carries state 'blocked', and the x-ron row had only one
  // version (Published) — no Planned entry to show a second chip in that
  // row the way frame 12f's "Follow-up post" does.
  FAMILIES.push(
    { id: 'fam-followup-post', campaignId: 'camp-1', kind: 'post',
      title: 'Follow-up post',
      versions: [
        { id: 'v-followup-x', channelId: 'ch-x-ron', state: 'planned', revision: 0 },
      ] },
    { id: 'fam-arm-faq', campaignId: 'camp-1', kind: 'article',
      title: 'Windows ARM support FAQ',
      versions: [
        { id: 'v-arm-blocked', channelId: 'ch-blog', state: 'blocked', revision: 1,
          claims: [
            { id: 'claim-arm-1', text: 'runs natively on ARM64', source: null, verdict: 'blocked' },
          ] },
      ] },
  );

  // ── T5: video intake + director (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md
  // §5). Keyed by familyId, same "own section, own key, never touches T0a's
  // rows" convention T3's REVIEW_DETAIL established above — the storyboard
  // (scenes, materials, pending edits, render jobs) is detail ONLY the
  // director needs, layered onto the existing fam-install-video/RENDER_BUDGET
  // rows rather than duplicating a second video fixture.
  //
  // Scene durations are arbitrary but sum to a plausible 40s walkthrough
  // (matches the intake brief's own "40-second install walkthrough" text).
  // Scene 3 is pre-marked `edited` so the director's `•` marker and the
  // player's "edits not rendered yet" status label are both visible on
  // first render, without requiring an interaction first.
  const VIDEO_DETAIL = {
    'fam-install-video': {
      brief: 'A 40-second install walkthrough for Windows testers, ending on Join the beta.',
      materials: ['install.mp4 · source', 'clayrune-logo.png'],
      scenes: [
        { id: 'sc-1', label: 'Download', durationSec: 6 },
        { id: 'sc-2', label: 'Installer', durationSec: 14 },
        { id: 'sc-3', label: 'Restore demo', durationSec: 12, edited: true },
        { id: 'sc-4', label: 'Restore', durationSec: 5 },
        { id: 'sc-5', label: 'Join', durationSec: 5 },
      ],
      pendingEdits: [
        { id: 'pe-1', label: 'Trimmed "Restore demo" to 12s' },
      ],
      // Render jobs are keyed to family.render (T0a) by revision — 'ready'
      // job r3 is the SAME render T3's review reads via family.render.
      // `durationSec` is r3's OWN total at render time (scene 3 was 9s, not
      // yet trimmed to 12s) — a snapshot, so the storyboard's stale badge
      // ("Previous render · r3 · 0:39") reads what r3 actually contains
      // rather than recomputing from scenes edited since.
      jobs: [
        { id: 'render-r3', revision: 3, status: 'ready', formats: ['16:9', '9:16'], durationSec: 39 },
      ],
    },
  };

  // ── T6: Conversations (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §6,
  // frame 12c). New rows only (ground rule 3) — conv-1/2/3 above (T0a) are
  // left exactly as written; CONVERSATION_DETAIL below is a side table
  // keyed by id, same "own key, never touches the base row" convention as
  // T3's REVIEW_DETAIL and T5's VIDEO_DETAIL, carrying the author/platform/
  // age/reason and thread content the base CONVERSATIONS rows never needed.
  //
  // Three new rows push the per-source badge counts to the frame's own
  // numbers (verified against a 930px crop of 12c, not eyeballed): the
  // badge counts CONVERSATIONS with state 'needs_reply' or 'needs_you' only
  // — 'reviewed'/'stale'/'no_reply' don't count, which is what makes
  // "On our posts · 2" land on exactly devnull_kat + Mira Okafor while
  // sam_builds's "No reply suggested" row still LISTS but doesn't count,
  // "Mentions · 1" lands on the new conv-6 while conv-2 (reviewed) doesn't,
  // and "Discussions" carries no number because conv-3's 'stale' isn't in
  // the countable set either — all three read straight off the frame.
  CONVERSATIONS.push(
    { id: 'conv-4', projectId: 'clayrune', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-li-page',
      excerpt: 'Installer failed twice and my key is gone.', state: 'needs_you' },
    { id: 'conv-5', projectId: 'clayrune', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-x-ron',
      excerpt: 'signed up \u{1F64C}', state: 'no_reply' },
    // state is 'needs_you' rather than 'needs_reply' on purpose: the
    // Conversations badge counts by CONVERSATION_DETAIL's own `reasonKind`
    // ('question', set below) not this field, but T1's Home aggregates
    // "N replies waiting" straight off state==='needs_reply' (desk-v1-
    // home.js:40) across ALL conversations, not just this source — using
    // 'needs_reply' here would silently bump Home's own "1 reply waiting"
    // fixture (conv-1) to 2 and break its smoke. 'needs_you' keeps this row
    // out of that unrelated aggregate while still counting for THIS badge.
    { id: 'conv-6', projectId: 'clayrune', campaignId: 'camp-1', source: 'mentions', channelId: 'ch-x-ron',
      excerpt: 'Quoted the restore-points post while comparing backup tools.', state: 'needs_you' },
  );

  // reasonKind drives the local (ticket-only, not kit — see desk-v1-
  // conversations.js's own file banner for why) glyph+word reason line;
  // reasonDetail is the free-text second half §6's own examples show
  // ("? Question · reply drafted", "⚑ Needs you · account problem").
  // `thread.parentPost` is what the conversation is ABOUT — for `our_posts`
  // that's OUR post being commented on, reusing REVIEW_DETAIL's own
  // restore-points lede verbatim (frame 12c's parent-post text) rather than
  // inventing a second copy of the same sentence; for `mentions` it's the
  // third-party post that named us. `thread.reply` is the proposed reply
  // AS IT WILL BE SENT (§6) — absent where none is drafted (needs_you,
  // no_reply) or already resolved (reviewed).
  const CONVERSATION_DETAIL = {
    'conv-1': {
      author: '@devnull_kat', platform: 'x', ageLabel: '12m',
      reasonKind: 'question', reasonDetail: 'reply drafted',
      thread: {
        parentPost: { label: 'Your post', platform: 'x', identity: '@ron', ageLabel: 'Tue 09:00',
          text: 'Every agent run now starts with a restore point…', link: '#' },
        // comment author is the person's display name ('Kat'), not the row's
        // account handle ('@devnull_kat') — the comment avatar (rendering
        // code already derives its initial from THIS field, unchanged) is
        // frame 12c's 'K', not the row's 'D' (Dave's review pass).
        comments: [
          { author: 'Kat', platform: 'x', ageLabel: '12m',
            text: 'Does the restore include the agent’s memory or just files?' },
        ],
        reply: { identity: '@ron', platform: 'x',
          text: 'Both — files, memory and the backlog roll back together, so the agent doesn’t "remember" the run you undid.' },
      },
    },
    'conv-2': {
      author: 'Someone', platform: 'linkedin', ageLabel: '1d',
      reasonKind: 'reviewed',
      thread: {
        parentPost: { label: 'Mentioned in a thread', platform: 'linkedin', identity: 'a backups discussion', ageLabel: '1d',
          text: 'Someone linked the restore-points post in a thread about backups.', link: '#' },
        comments: [], reply: null,
      },
    },
    'conv-3': {
      author: 'a forum thread', platform: 'web', ageLabel: '2d',
      reasonKind: 'stale',
      thread: {
        parentPost: { label: 'Discussion', platform: 'web', identity: 'a beta-programs forum', ageLabel: '2d',
          text: 'A forum thread comparing beta programs mentions Clayrune in passing.', link: '#' },
        comments: [], reply: { identity: '@ron', platform: 'web', text: 'Thanks for the mention — happy to answer questions about the beta.' },
        // U14: thread changed since the draft → hold bar, Send disabled.
        hold: 'This thread has new replies since the draft below was written.',
      },
    },
    'conv-4': {
      author: 'Mira Okafor', platform: 'linkedin', ageLabel: '1h',
      reasonKind: 'needs_you', reasonDetail: 'account problem',
      thread: {
        parentPost: { label: 'Comment on', platform: 'linkedin', identity: 'Clayrune page', ageLabel: '1h',
          text: 'Installer failed twice and my key is gone.', link: '#' },
        comments: [], reply: null,
        // Escalated per reply policy (§6) — no draft exists to send.
        hold: 'Escalated per reply policy — account problems need a person, not a drafted reply.',
      },
    },
    'conv-5': {
      author: '@sam_builds', platform: 'x', ageLabel: '3h',
      reasonKind: 'no_reply',
      thread: {
        parentPost: { label: 'Comment on', platform: 'x', identity: '@sam_builds', ageLabel: '3h',
          text: 'signed up \u{1F64C}', link: '#' },
        comments: [], reply: null,
      },
    },
    'conv-6': {
      author: '@backup_bee', platform: 'x', ageLabel: '40m',
      reasonKind: 'question', reasonDetail: 'reply drafted',
      thread: {
        parentPost: { label: 'Mentioned by', platform: 'x', identity: '@backup_bee', ageLabel: '40m',
          text: 'Quoted the restore-points post while comparing backup tools.', link: '#' },
        comments: [],
        reply: { identity: '@ron', platform: 'x', text: 'Good comparison — happy to answer specifics if useful!' },
      },
    },
  };

  // LinkedIn has no public mentions/search API (a platform limit, not a
  // held channel — ch-li-page's own 'held' is about POSTING, a separate
  // gap) — §6's own worked example ("LinkedIn mentions not available")
  // reused verbatim as the one coverage gap R0 needs to demonstrate the
  // footer with (§6: "an empty list must never read as 'no one is
  // talking'" — this is that honest gap note, not an empty state).
  const CONVERSATION_COVERAGE_GAPS = {
    'camp-1': [
      { label: 'LinkedIn mentions not available', detail: 'LinkedIn does not offer a public mentions/search API — only comments on our own posts are covered there.' },
    ],
  };

  // ── T7: Results (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §7) ────────────
  // RESULTS (T0a, above) already carries goal/forecast/versions/costs/
  // diagnostics in full — nothing there needed adding to. The one thing §7
  // needs that T0a's RESULTS didn't model is Posy's read: "what worked, and
  // ONE proposed next experiment with one variable" — a separate small
  // fixture, own section, own key, same convention REVIEW_DETAIL/VIDEO_DETAIL/
  // CONVERSATION_DETAIL above already established. `experiment.channelId`
  // points at the campaign's existing best-performing channel (ch-x-ron,
  // the one measured signup source in RESULTS.versions) rather than
  // inventing a new one, so "Set up experiment" creates a piece on a channel
  // that's actually attached to the campaign.
  const RESULTS_INSIGHT = {
    'camp-1': {
      read: 'The install video on X is the only channel with a verified signup count so far (8). The LinkedIn cut and the testers post haven’t reported back yet.',
      experiment: {
        variable: 'send time',
        title: 'Follow-up post — 9am send-time test',
        description: 'Try posting the next X update at 9am instead of the usual mid-day slot, and compare signups.',
        channelId: 'ch-x-ron',
      },
    },
  };

  // ── T2b: Proposed state, Start sheet, rules popover, Posy instructions
  // (docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §3.5, §8). New rows only
  // (ground rule 3) — camp-1 and its channels/families above are untouched.
  //
  // Dave's review pass 2 replaced the original Reddit fixture: v1 is X +
  // LinkedIn only (Reddit deferred — position_whichidentityandwhichplatform
  // sthedesksocialworks), and the split is by VOICE, not just platform: X
  // carries Ron's own first-person builder voice (`ch-x-ron`), LinkedIn
  // carries the Clayrune company page's own distinct voice (`ch-li-page`).
  // Ron's personal LinkedIn is never a Desk destination.
  //
  // §8's worked example is "in · Ron (personal) excluded" — an included/
  // excluded row needs a channel that exists but isn't in the campaign's
  // own accounts subset. camp-2 below uses 2 of the 3 pre-existing global channels
  // (ch-x-ron, ch-li-page), leaving the 3rd (ch-blog) naturally "excluded"
  // for it — no new channel, no touching camp-1's Add-tray shelf count
  // (camp-1 already holds all 3, T0a's own invariant).

  // A second campaign, in Proposed state, so §3.5 has something real to
  // render (camp-1 is Active and stays that way — T2a/T3/T4 fixtures already
  // depend on it). `goal.tracked: false` deliberately demonstrates CMP-03's
  // "untracked goal is a warning, not a blocker". Reuses the restore-points
  // feature (fam-restore-points, T0a) as its subject rather than inventing a
  // new product surface (ground rule 3's own precedent).
  CAMPAIGNS.push({
    id: 'camp-2',
    state: 'proposed', // proposed | active | paused | completed | archived | draft
    // IA1 §2.2/§3#40 (same field/comment as camp-1 above): this campaign
    // promotes a FEATURE, not the whole project or an audience.
    projectId: 'clayrune',
    subject: { kind: 'feature', label: 'Restore points' },
    goal: { current: 0 },
    // R2-2d: a proposed campaign already parked at the Launch stop (mockup
    // row `Draft · at Launch`), same shape as camp-1's `map`.
    map: { stop: 'launch', done: ['goal', 'how', 'what', 'when', 'where'] },
    // IA2 §3: the per-piece approval toggle, weekly-count duplicate,
    // reply-mode toggle and paid duplicate all retired (see camp-1's
    // comment above) — `customChips` is the only field left.
    rules: {},
    // ── T1 (MC-977 UX pass §4): the one canonical plan object. Goal display,
    // rule chips and the Start sheet (desk-v1-rules.js) all read THIS.
    // `goal` above is reduced to `{current}` (T3 review carry-over,
    // MC-977): nothing reads metric/target/deadline/audience/tracked off
    // `camp.goal` any more, only `camp.goal.current`.
    // IA2 §3: `source_projects` retired, the per-campaign channel-id list
    // becomes `plan.accounts` (bare ids, voice moved to `project.
    // accounts[].voice`), `cadence.min_gap_h` moved to the project's own
    // `presence.ceilings` — same rescope as camp-1 above.
    plan: {
      brief: 'Restore points is live in Clayrune 2.1 — undo any agent mistake by rolling back to a saved snapshot.',
      title: 'Restore points launch',
      audience: 'the developer audience',
      goal: { outcome: 'beta signups', target: 60, deadline: '2026-10-20', tracked: false },
      accounts: ['ch-x-ron', 'ch-li-page'],
      angle: 'Frame restore points as the cheap insurance that makes trying agent changes low-risk.',
      samples: [],
      cadence: { per_week: 2 },
      end: { date: '2026-10-20', post_cap: null },
      replies: 'drafts',
      paid: false,
      generation: 'No video generation planned for this campaign',
    },
  });

  // Posy's proposed pieces (§3.5: "planned or drafting") for camp-2 — one
  // per voice, so the campaign's two channels each have their own piece
  // rather than sharing a version.
  FAMILIES.push(
    { id: 'fam-launch-x', campaignId: 'camp-2', kind: 'post',
      title: 'Restore points is live — undo any agent mistake',
      versions: [{ id: 'v-launch-x', channelId: 'ch-x-ron', state: 'drafting', revision: 0 }] },
    { id: 'fam-launch-li', campaignId: 'camp-2', kind: 'post',
      title: 'Introducing restore points in Clayrune 2.1',
      versions: [{ id: 'v-launch-li', channelId: 'ch-li-page', state: 'planned', revision: 0 }] },
  );

  // Proposed-state EXTRAS — T1 (§4) moved the goal sentence + Start-sheet
  // authority list off this map and onto `camp.plan` above (the one
  // canonical plan object); a real sequencing blocker and per-card
  // "? Assumed" notes aren't plan bounds, so they stay here, keyed by
  // campaignId (same "own key, own section" convention as REVIEW_DETAIL/
  // VIDEO_DETAIL/CONVERSATION_DETAIL above), just under a name that doesn't
  // claim to be the plan anymore.
  const PROPOSED_EXTRAS = {
    'camp-2': {
      // "Only a real blocker gets a card... ⛔ Posy's one question" (CMP-03)
      // — a real sequencing call between the two voices, not something Posy
      // can decide alone.
      blocker: {
        id: 'blocker-sequence',
        question: 'Should the X post or the LinkedIn post go out first?',
        answers: [
          { id: 'a-x-first', label: 'X first' },
          { id: 'a-li-first', label: 'LinkedIn first' },
        ],
      },
      // "? Assumed" popovers, keyed by familyId — shown on the relevant card.
      assumptions: {
        'fam-launch-x': 'Assumed this posts from @ron in first person, not the Clayrune page.',
        'fam-launch-li': 'Assumed the Clayrune page post uses its own voice, distinct from Ron’s.',
      },
    },
  };

  // ── IA1 (MC-977 IA revision, §5 row IA1): engulfing_scanner's own campaign
  // + Needs-you item, so the Home project-card grid has a SECOND project
  // with real content instead of an empty shell. State 'needs_you' (not
  // 'needs_reply') on purpose (same convention conv-4/conv-6 above already
  // established) — Home's global `_needsYouItems()` only counts
  // `needs_reply`/`needs_review`, so this row surfaces on the PROJECT page's
  // own Needs-you list without silently bumping desk-v1-home.mjs's fixed
  // "1 reply waiting" / "1 piece to approve" assertions, which enumerate
  // every fixture family/conversation regardless of project.
  CAMPAIGNS.push({
    id: 'camp-3',
    state: 'active',
    projectId: 'engulfing_scanner',
    subject: { kind: 'product', label: 'Engulfing scanner' },
    goal: { current: 4 },
    // R2-2 (§8 amended row, mockups_r2/1-home.png): the Home status board's
    // GOAL PROGRESS/PACE columns read `plan.goal.tracked` + `plan.goal.target`
    // for campaigns that predate R2-4's full measurable-goal shape (only
    // camp-1 has `goal.source` set) — `term` is additive here so this row has
    // a real elapsed fraction to pace against, same shape as camp-1's own.
    term: { index: 1, starts: '2026-09-15', ends: '2026-10-31', post_cap: null },
    // IA2 §3: same rescope as camp-1/camp-2 above.
    rules: {},
    plan: {
      brief: 'Day traders evaluating signal tools should see the scanner catch a real engulfing setup.',
      title: 'Signal alerts for day traders',
      audience: 'Day traders evaluating signal tools',
      goal: { outcome: 'signups', target: 20, deadline: '2026-10-31', tracked: true },
      accounts: ['ch-x-ron'],
      angle: 'Show one real scanned setup end to end, from alert to outcome.',
      samples: [],
      cadence: { per_week: 2 },
      end: { date: '2026-10-31', post_cap: null },
      replies: 'drafts',
      paid: false,
      generation: 'No video generation planned for this campaign',
    },
  });

  CONVERSATIONS.push(
    { id: 'conv-7', projectId: 'engulfing_scanner', campaignId: 'camp-3', source: 'our_posts', channelId: 'ch-x-ron',
      excerpt: 'Backtest looks great — does this work on futures too?', state: 'needs_you' },
  );

  // R2-2: camp-3 had no family/version at all — the Home row's NEXT POST
  // column had nothing scheduled to show. One scheduled post (mirrors
  // camp-1's fam-30-testers/camp-4's fam-discord-announce shape exactly).
  FAMILIES.push({
    id: 'fam-signal-alert', campaignId: 'camp-3', kind: 'post',
    title: 'Real engulfing setup caught live',
    versions: [
      { id: 'v-signal-x', channelId: 'ch-x-ron', state: 'scheduled', revision: 0,
        publishAt: '2026-10-05T09:00:00-07:00' },
    ],
  });

  // ── IA6 (MC-977 IA revision, §5 row IA6): a genuinely FRESH Active
  // campaign — goal.current 0, no version has ever gone out — so the
  // Overview empty states (UX_PASS §6.1: no `0/` bar; Results/Conversations
  // copy) have a real case to render against. camp-1's own "Active" fixture
  // already has 11/30 progress and publication history, so it can't stand
  // in for "before the first publish". One SCHEDULED piece (no
  // verified_published/you_reported anywhere in this campaign) gives the
  // project page's "next post across campaigns" a real slot to surface too.
  CAMPAIGNS.push({
    id: 'camp-4',
    state: 'active',
    projectId: 'clayrune',
    subject: { kind: 'feature', label: 'Community Discord' },
    goal: { current: 0 },
    // R2-2d: the mockup's `Active · just started` / pace `On track` row. Every
    // other campaign's term is a fixed date, but this one is meant to be on
    // day 0 whenever the fixtures load, so its term starts today rather than
    // on a date that ages past the "just started" window (first ~5% of term).
    term: (function () {
      const day = 24 * 3600 * 1000;
      const iso = (t) => new Date(t).toISOString().slice(0, 10);
      const now = Date.now();
      return { index: 1, starts: iso(now), ends: iso(now + 46 * day), post_cap: null };
    })(),
    rules: {},
    plan: {
      brief: 'Clayrune users looking for community should find the Discord and join.',
      title: 'Community Discord launch',
      audience: 'Clayrune users looking for community',
      goal: { outcome: 'Discord joins', target: 40, deadline: '2026-11-15', tracked: true },
      accounts: ['ch-x-ron'],
      angle: 'Announce the Discord and invite people in.',
      samples: [],
      cadence: { per_week: 2 },
      end: { date: '2026-11-15', post_cap: null },
      replies: 'drafts',
      paid: false,
      generation: 'No video generation planned for this campaign',
    },
  });
  FAMILIES.push({
    id: 'fam-discord-announce', campaignId: 'camp-4', kind: 'post',
    title: 'Join the Clayrune Discord',
    versions: [
      { id: 'v-discord-x', channelId: 'ch-x-ron', state: 'scheduled', revision: 0,
        publishAt: '2026-10-06T09:00:00-07:00' },
    ],
  });

  // ── IA6 (§5 row IA6): an ARCHIVED campaign, unreachable since IA1 removed
  // Home's archived section (§1: "archived campaigns are unreachable until
  // this lands"). `_preArchiveState` mirrors `_prePauseState`'s convention
  // (desk-v1-project.js Pause/Resume) — Restore needs to know what state to
  // put it back to, and this one never went through `_archiveCampaign`'s own
  // do() this session, so nothing else records that for it.
  CAMPAIGNS.push({
    id: 'camp-archived-1',
    state: 'archived',
    projectId: 'clayrune',
    subject: { kind: 'feature', label: 'Legacy import wizard' },
    goal: {
      current: 22, metric: 'imports completed', target: 25, baseline: 0,
      unit: 'imports', horizon: 'short', deadline: '2026-08-01', source: 'manual',
      entries: [{ at: '2026-08-01T09:00:00Z', value: 22 }],
    },
    // R2-15 (§10.1): this campaign's one term closed 1 Aug — the retro fixture
    // below (RETRO_CLOSED_1) and the R1-L outcome-loop findings (F1-F4 further
    // down) both key off `term.index`, so a restored (Restore -> `completed`)
    // view of this campaign has a real closed term to show a Retro section for.
    term: { index: 1, starts: '2026-06-01', ends: '2026-08-01', post_cap: null },
    rules: {},
    _preArchiveState: 'completed',
    plan: {
      brief: 'Users migrating from the old importer should find the new wizard.',
      title: 'Legacy import wizard launch',
      audience: 'Users migrating from the old importer',
      goal: { outcome: 'imports completed', target: 25, deadline: '2026-08-01', tracked: true },
      accounts: ['ch-x-ron'],
      angle: 'Show the wizard fixing a real broken import.',
      samples: [],
      cadence: { per_week: 2 },
      end: { date: '2026-08-01', post_cap: null },
      replies: 'drafts',
      paid: false,
      generation: 'No video generation planned for this campaign',
    },
  });

  // ── IA7 (MC-977 IA revision, §5 row IA7 · IA_REVISION §7): a mention that
  // belongs to a project but no campaign (K4: "a conversation always has a
  // project; its campaign is optional") — the Engagement dashboard's own
  // campaign filter needs one real row to prove its `No campaign` option
  // actually surfaces something, not just an empty option in the dropdown.
  CONVERSATIONS.push(
    { id: 'conv-8', projectId: 'clayrune', campaignId: null, source: 'mentions', channelId: 'ch-x-ron',
      excerpt: 'Anyone running Clayrune solo, without a team behind it?', state: 'needs_you' },
  );
  CONVERSATION_DETAIL['conv-8'] = {
    author: '@solo_dev_ok', platform: 'x', ageLabel: '2h',
    reasonKind: 'needs_you', reasonDetail: 'no campaign yet',
    thread: {
      parentPost: { label: 'Mentioned by', platform: 'x', identity: '@solo_dev_ok', ageLabel: '2h',
        text: 'Anyone running Clayrune solo, without a team behind it?', link: '#' },
      comments: [], reply: null,
    },
  };

  // ── R2-14 (§10 outcome learning loop) — fixture shapes only (kit's
  // retroVerdict() implements the sample-size table; R2-15 wires the ①
  // Retro UI, R1-L the backend). Field names are snake_case throughout,
  // matching §10's schemas verbatim and `mc/desk.py`'s existing ledger rows
  // (`campaign_id`/`project_id`/`published_at`) — unlike CAMPAIGNS/FAMILIES
  // above, these mirror a real backend store, not a frontend-only shape.

  // §8 R1-L: ledger rows gain `piece_id/format/account/term/cost`; per-post
  // `outcome` dict becomes `outcomes[]`. §10.8 Q1 (Ron, binding): goals are
  // measured DAILY as they go, so each post's `outcomes[]` is a dated daily
  // series for its per-post metric (default `clicks`), not one number —
  // `source:'manual'` is the fallback until R1-E's automatic feed read
  // lands (`source:'feed'`, §10.7). camp-1 / term 1 (still active — these
  // are the RETRO fixture's own Interim-only evidence, not a closed term).
  const LEDGER = [
    { id: 'post-101', piece_id: 'piece-fam-launch-x', format: 'post', account: 'ch-x-ron',
      campaign_id: 'camp-1', project_id: 'clayrune', term: 1, platform: 'x', voice: 'Ron (first person)',
      cost: 0.015, published_at: '2026-09-08T15:00:00Z',
      outcomes: [
        { metric: 'clicks', value: 12, at: '2026-09-08T23:59:00Z', source: 'manual' },
        { metric: 'clicks', value: 21, at: '2026-09-09T23:59:00Z', source: 'manual' },
      ] },
    { id: 'post-102', piece_id: 'piece-fam-launch-x-2', format: 'image', account: 'ch-x-ron',
      campaign_id: 'camp-1', project_id: 'clayrune', term: 1, platform: 'x', voice: 'Ron (first person)',
      cost: 0.015, published_at: '2026-09-11T15:00:00Z',
      outcomes: [
        { metric: 'clicks', value: 9, at: '2026-09-11T23:59:00Z', source: 'manual' },
      ] },
    // LinkedIn page is currently `held` (CHANNELS above) — this row predates
    // the hold. No per-post number typed yet: demonstrates §10.7's "a
    // dimension with no numbers never renders as zero", not a 0-value entry.
    { id: 'post-103', piece_id: 'piece-fam-launch-li', format: 'post', account: 'ch-li-page',
      campaign_id: 'camp-1', project_id: 'clayrune', term: 1, platform: 'linkedin', voice: 'Clayrune page',
      cost: 0, published_at: '2026-09-09T16:00:00Z', outcomes: [] },
  ];

  // R2-15: camp-archived-1's own closed term (2026-06-01 to 2026-08-01, 12
  // posts, one per week-ish) — the per-post number grid's source rows. No
  // outcomes typed yet on any of them (§10.7: "a dimension with no numbers
  // never renders as zero"), so the Retro section's paste-from-CSV grid has
  // something real to fill rather than fixture-preloaded values.
  for (let i = 0; i < 12; i++) {
    LEDGER.push({
      id: `post-arch1-${i + 1}`, piece_id: `piece-arch1-${i + 1}`,
      format: i % 3 === 2 ? 'image' : 'post', account: 'ch-x-ron',
      campaign_id: 'camp-archived-1', project_id: 'clayrune', term: 1,
      platform: 'x', voice: 'Ron (first person)', cost: 0.015,
      published_at: new Date(Date.UTC(2026, 5, 1 + i * 5, 15, 0, 0)).toISOString(),
      outcomes: [],
    });
  }

  // §10.1: `Run retro now` mid-term is labelled Interim, numbers only,
  // **never proposes findings** — camp-1's term 1 hasn't closed (CAMPAIGNS
  // above: `state:'active'`), so this is the only honest status for it.
  // `dimensions` entries carry the verdict `DeskV1Kit.retroVerdict()` would
  // return for this fixture's own LEDGER rows (2 X posts vs 1 LinkedIn post
  // — both well under the 10-per-arm floor).
  const RETRO = {
    campaign_id: 'camp-1', project_id: 'clayrune', term: 1, status: 'interim',
    computed_at: '2026-09-15T09:00:00Z',
    goal: { metric: 'tester signups', target: 30, actual: 11, baseline: 0 },
    // R2-1 renamed the project-level `production` field to `presence.budget`
    // (line 47 above) — `media_cost` here (§10.1: "production (media jobs)")
    // is a different, spend-breakdown field, named to not collide with that
    // retired key.
    spend: { publishing: 0.03, media_cost: 0, total: 0.03, ceiling: 60, cost_per_outcome: 0.003 },
    dimensions: [
      { dimension: 'platform_voice', verdict: 'too_few_posts',
        text: 'Too few posts to tell (2 and 1; need 10 each)' },
    ],
    summary: 'Interim: 11 of 30 tester signups so far, $0.03 spent. Not enough posts yet to say what’s working.',
    findings: [],
  };

  // R2-15: camp-archived-1's CLOSED term 1 retro (§10.4: "a Retro section per
  // closed term"). `dimensions` mirrors the §10.1 table's five rows; the
  // 'finding' rows reuse F1/F2/F3's own arms/effect/evidence below (one
  // retro run produced all of them) so the dimension table and the findings
  // list agree on the same numbers, the way `desk_retro.run_retro` and
  // `mc.desk.propose_finding` agree in the backend. `findings` lists only
  // the ids still `state:'proposed'` at load time (F1, F5) — confirmed
  // (F2)/rejected (F3)/stale (F4) findings belong to the project page's
  // Playbook (R2-16), not this section (§10.4's row split).
  const RETRO_CLOSED_1 = {
    campaign_id: 'camp-archived-1', project_id: 'clayrune', term: 1, status: 'closed',
    computed_at: '2026-08-02T09:00:00Z',
    metric: 'clicks',
    goal: { metric: 'imports completed', target: 25, actual: 22, baseline: 0 },
    spend: { publishing: 0.18, media_cost: 0, total: 0.18, ceiling: null, cost_per_outcome: 0.008 },
    dimensions: [
      { dimension: 'format', verdict: 'finding', confidence: 'low',
        arms: { a: 'post', b: 'image' }, effect: { ratio: 1.8, direction: 'a>b' }, n_total: 25,
        evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 14, n_b: 11 }] },
      { dimension: 'platform_voice', verdict: 'finding', confidence: 'low',
        arms: { a: 'x:ron', b: 'linkedin:clayrune_page' }, effect: { ratio: 1.4, direction: 'a>b' }, n_total: 24,
        evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 12, n_b: 12 }] },
      { dimension: 'slot', verdict: 'finding', confidence: 'medium',
        arms: { a: 'Tue/Thu 08-10', b: 'other slots' }, effect: { ratio: 2.1, direction: 'a>b' }, n_total: 41,
        evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 22, n_b: 19 }] },
      { dimension: 'angle', verdict: 'too_few_campaigns',
        text: 'Too few campaigns to tell (1 and 1; need 3 each)' },
      { dimension: 'spend_kind', verdict: 'too_few_campaigns',
        text: 'Too few campaigns to tell (1 and 1; need 3 each)' },
    ],
    summary: 'Judged on clicks per post, not on imports completed. Term closed 1 Aug: 22 of 25 imports completed, $0.18 spent.',
    // Only F1 is still `state:'proposed'` (F2/F3/F4 already decided, T0a's own
    // rows below) — R2-15's own comment above ("lists only the ids still
    // state:'proposed' at load time") means this array holds exactly that one.
    findings: ['F1'],
  };

  // Keyed by `<campaignId>:<term index>` — the Retro section (desk-v1-retro.js)
  // looks up `camp.id + ':' + camp.term.index` here rather than assuming one
  // retro per campaign, since a `long`-horizon campaign closes several terms.
  const RETROS = {
    'camp-1:1': RETRO,
    'camp-archived-1:1': RETRO_CLOSED_1,
  };

  // §10.2 finding schema, verbatim field set. States cover the full
  // proposed -> confirmed | rejected -> stale lifecycle; origins cover both
  // sides of §10.5's human-in-the-loop rule. Evidence points at
  // `camp-archived-1` (CAMPAIGNS above, `state:'completed'` pre-archive) —
  // the one fixture campaign that has actually closed a term, unlike camp-1.
  const FINDINGS = [
    // proposed, origin:unattended — a fresh unconfirmed retro output. Never
    // paired with state:'confirmed' (§10.5.2: unstamped/unattended output
    // never becomes autonomous input; only Ron's Confirm can flip the
    // origin to 'interactive').
    { id: 'F1', project_id: 'clayrune', scope: 'project', dimension: 'format',
      arms: { a: 'post', b: 'image' }, account: 'x:ron', metric: 'clicks',
      effect: { ratio: 1.8, direction: 'a>b' },
      evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 14, n_b: 11 }],
      n_total: 25, confidence: 'low',
      state: 'proposed', origin: 'unattended', decided_at: null, decided_by: null },
    // confirmed, origin:interactive — Ron confirmed as-is, so it reaches the
    // playbook and the Suggest brief (§10.3).
    { id: 'F2', project_id: 'clayrune', scope: 'project', dimension: 'slot',
      arms: { a: 'Tue/Thu 08-10', b: 'other slots' }, account: 'x:ron', metric: 'clicks',
      effect: { ratio: 2.1, direction: 'a>b' },
      evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 22, n_b: 19 }],
      n_total: 41, confidence: 'medium', maybe_why: 'Morning posts may catch more of the US dev day.',
      state: 'confirmed', origin: 'interactive', decided_at: '2026-08-05T10:00:00Z', decided_by: 'ron' },
    // rejected, origin:interactive — durable "no" (§10.5.3); REJECTIONS
    // below carries the matching suppression record.
    { id: 'F3', project_id: 'clayrune', scope: 'project', dimension: 'platform_voice',
      arms: { a: 'x:ron', b: 'linkedin:clayrune_page' }, account: null, metric: 'clicks',
      effect: { ratio: 1.4, direction: 'a>b' },
      evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 12, n_b: 12 }],
      n_total: 24, confidence: 'low',
      state: 'rejected', origin: 'interactive', decided_at: '2026-08-06T09:00:00Z', decided_by: 'ron' },
    // stale, origin:interactive — a later retro pointed the other way
    // (§10.2 states: `confirmed` -> `stale` when a newer retro contradicts
    // it); `edited_text` demonstrates Ron's own wording surviving the state
    // change.
    { id: 'F4', project_id: 'clayrune', scope: 'project', dimension: 'format',
      arms: { a: 'video', b: 'post' }, account: 'x:ron', metric: 'clicks',
      effect: { ratio: 1.5, direction: 'a>b' },
      evidence: [{ campaign_id: 'camp-archived-1', term: 1, n_a: 15, n_b: 15 }],
      n_total: 30, confidence: 'medium', edited_text: 'Video clips out-clicked plain posts early on.',
      state: 'stale', origin: 'interactive', decided_at: '2026-07-01T09:00:00Z', decided_by: 'ron' },
  ];

  // §10.5.3: `{project_id, dimension, arms, direction, evidence_key}` —
  // `evidence_key` is a hash of the sorted (campaign_id, term) set, so the
  // SAME evidence never re-proposes; new evidence (>=10 more posts from
  // outside this set) is the only way F3 can return. Matches F3 above.
  const REJECTIONS = [
    { project_id: 'clayrune', dimension: 'platform_voice',
      arms: { a: 'x:ron', b: 'linkedin:clayrune_page' }, direction: 'a>b',
      evidence_key: 'camp-archived-1:1', rejected_at: '2026-08-06T09:00:00Z' },
  ];

  window.DeskV1Fixtures = {
    projects: PROJECTS,
    campaigns: CAMPAIGNS,
    channels: CHANNELS,
    families: FAMILIES,
    conversations: CONVERSATIONS,
    renderBudget: RENDER_BUDGET,
    results: RESULTS,
    reviewDetail: REVIEW_DETAIL,
    videoDetail: VIDEO_DETAIL,
    workerHeartbeat: WORKER_HEARTBEAT,
    recentAssets: RECENT_ASSETS,
    homeSuggestions: HOME_SUGGESTIONS,
    campaignSuggestions: CAMPAIGN_SUGGESTIONS,
    contentPreview: CONTENT_PREVIEW,
    calendarSchedule: CALENDAR_SCHEDULE,
    conversationDetail: CONVERSATION_DETAIL,
    conversationCoverageGaps: CONVERSATION_COVERAGE_GAPS,
    resultsInsight: RESULTS_INSIGHT,
    proposedExtras: PROPOSED_EXTRAS,
    ledger: LEDGER,
    retros: RETROS,
    playbook: { findings: FINDINGS, rejections: REJECTIONS },
  };
})();
