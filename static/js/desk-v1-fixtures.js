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
  const CAMPAIGNS = [
    {
      id: 'camp-1',
      name: 'Windows beta testers',
      state: 'active', // proposed | active | paused | completed | archived | draft
      goal: {
        metric: 'tester signups', target: 30, current: 11,
        deadline: '2026-10-20', tracked: true,
      },
      channelIds: ['ch-x-ron', 'ch-li-page', 'ch-blog'],
      rules: {
        reviewMode: 'each_piece', // 'each_piece' | 'themes'
        frequencyPerWeek: 3,
        repliesMode: 'drafts', // 'drafts' | 'auto_faq'
        paid: false,
      },
    },
  ];

  // Destination identity = platform + account/page, never a bare logo
  // (UX-02). `capability` drives the badge copy in §9's vocabulary (owned by
  // T0b's kit, not duplicated here): 'direct' = can publish per the review
  // mode, 'manual' = "You publish it", and `health: 'held'` overrides either
  // with "Held — <reason>".
  const CHANNELS = [
    { id: 'ch-x-ron', platform: 'x', identity: '@ron', label: '𝕏 @ron',
      capability: 'direct', health: 'ok' },
    // "in · Clayrune page direct + held" (ground rule 3): its normal
    // capability is direct, but it is CURRENTLY held — the Home/campaign
    // Needs-you hold and the A13 worker-offline check both read this.
    { id: 'ch-li-page', platform: 'linkedin', identity: 'Clayrune page', label: 'in · Clayrune page',
      capability: 'direct', health: 'held', holdReason: 'LinkedIn page disconnected' },
    { id: 'ch-blog', platform: 'blog', identity: 'Clayrune blog', label: 'Clayrune blog',
      capability: 'manual', health: 'ok' },
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
          format: '9:16', publishedAt: '2026-09-22T09:00:00Z' },
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
          publishAt: '2026-09-30T10:00:00Z' },
      ],
    },
  ];

  // 3 conversations across the source switch (CON-01): our posts, mentions,
  // discussions — one of each, one deliberately stale (U14 hold).
  const CONVERSATIONS = [
    { id: 'conv-1', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-x-ron',
      excerpt: 'Does this work on ARM laptops?', state: 'needs_reply' },
    { id: 'conv-2', campaignId: 'camp-1', source: 'mentions', channelId: 'ch-li-page',
      excerpt: 'Someone linked the restore-points post in a thread about backups.', state: 'reviewed' },
    { id: 'conv-3', campaignId: 'camp-1', source: 'discussions', channelId: null,
      excerpt: 'A forum thread comparing beta programs mentions Clayrune in passing.',
      state: 'stale' },
  ];

  // The render job + budget (MED-04/06): rate, spend, and the period limit
  // the T5 render card's "maximum" and "Raise budget…" disable read from.
  const RENDER_BUDGET = {
    period: 'month', currency: 'USD', rate: 0.40, spent: 16, limit: 150,
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
        { id: 'p-closer', text: 'Free for Windows beta testers. ', linkText: 'Join the beta.', linkHref: '#' },
      ],
      claims: [
        { id: 'claim-1', anchorParagraphId: 'p-claim', label: 'Restore time',
          state: 'blocked', source: null, revision: 1 },
      ],
      where: 'Clayrune blog', whenISO: '2026-09-30T12:00:00-07:00', link: 'clayrune.dev/beta',
      whyChecks: [
        { label: '2.1 is public', ok: true },
        { label: 'Free for testers', ok: true },
        { label: 'Restore time · revision waiting', claimId: 'claim-1' },
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

  window.DeskV1Fixtures = {
    campaigns: CAMPAIGNS,
    channels: CHANNELS,
    families: FAMILIES,
    conversations: CONVERSATIONS,
    renderBudget: RENDER_BUDGET,
    results: RESULTS,
    reviewDetail: REVIEW_DETAIL,
  };
})();
