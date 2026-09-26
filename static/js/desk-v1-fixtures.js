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
    { id: 'conv-1', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-x-ron',
      excerpt: 'Does the restore include the agent’s memory or just files?', state: 'needs_reply' },
    { id: 'conv-2', campaignId: 'camp-1', source: 'mentions', channelId: 'ch-li-page',
      excerpt: 'Someone linked the restore-points post in a thread about backups.', state: 'reviewed' },
    { id: 'conv-3', campaignId: 'camp-1', source: 'discussions', channelId: null,
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
    { id: 'conv-4', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-li-page',
      excerpt: 'Installer failed twice and my key is gone.', state: 'needs_you' },
    { id: 'conv-5', campaignId: 'camp-1', source: 'our_posts', channelId: 'ch-x-ron',
      excerpt: 'signed up \u{1F64C}', state: 'no_reply' },
    // state is 'needs_you' rather than 'needs_reply' on purpose: the
    // Conversations badge counts by CONVERSATION_DETAIL's own `reasonKind`
    // ('question', set below) not this field, but T1's Home aggregates
    // "N replies waiting" straight off state==='needs_reply' (desk-v1-
    // home.js:40) across ALL conversations, not just this source — using
    // 'needs_reply' here would silently bump Home's own "1 reply waiting"
    // fixture (conv-1) to 2 and break its smoke. 'needs_you' keeps this row
    // out of that unrelated aggregate while still counting for THIS badge.
    { id: 'conv-6', campaignId: 'camp-1', source: 'mentions', channelId: 'ch-x-ron',
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
  // excluded row needs a channel that exists but isn't on the campaign's
  // channelIds. camp-2 below uses 2 of the 3 pre-existing global channels
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
    name: 'Restore points launch',
    state: 'proposed', // proposed | active | paused | completed | archived | draft
    goal: {
      metric: 'beta signups', target: 60, current: 0,
      deadline: '2026-10-20', audience: 'the developer audience', tracked: false,
    },
    channelIds: ['ch-x-ron', 'ch-li-page'],
    rules: {
      reviewMode: 'each_piece', frequencyPerWeek: 2,
      repliesMode: 'drafts', paid: false,
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

  // Proposed-state detail, keyed by campaignId (same "own key, own section"
  // convention as REVIEW_DETAIL/VIDEO_DETAIL/CONVERSATION_DETAIL above).
  const PROPOSED_DETAIL = {
    'camp-2': {
      // The goal sentence's editable parts (§3.5: "dashed underlines on the
      // editable parts: target, date, audience"). `metric` is fixed prose,
      // not one of the three dashed slots.
      goalSentence: { target: 60, metric: 'beta signups', date: '2026-10-20', audience: 'the developer audience' },
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
      // The Start-sheet's authority list (CMP-05: "accounts, frequency
      // ceiling, dates, review mode, replies, paid, generation limits, stop
      // conditions", in plain language).
      authority: {
        accounts: ['𝕏 @ron', 'in · Clayrune page'],
        frequencyPerWeek: 2,
        dates: 'Now through Oct 20, 2026',
        reviewMode: 'You approve each piece',
        replies: 'Drafts for review',
        paid: 'Off',
        generationLimits: 'No video generation planned for this campaign',
        stopConditions: 'Pause automatically once the goal is reached or Oct 20 passes',
      },
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
    proposedDetail: PROPOSED_DETAIL,
  };
})();
