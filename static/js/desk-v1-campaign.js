// Desk v1 (MC-977) — T2a: Campaign page + Content list (frame 12a, + 12e;
// docs/desk_v1_r0_plan.md; THE_DESK_V1_UI.md §3.1-3.4, 3.6, §9, §10, §11).
// Window-bridged module, no `import` (ground rule 1). `desk-v1-shell.js` owns
// the campaign-page skeleton (summary / tab strip / tab body / right column /
// add tray) and mounts each slot by calling the five `deskV1FillCampaign*`
// hooks below — this file owns their content; the skeleton itself never
// changes (T0a's own comment on _renderCampaignSkeleton).
//
// Fixtures only (ground rule 3): every drop/attach/move/skip/archive mutates
// the in-memory DeskV1Fixtures objects directly through DeskV1Kit.commandBus
// — the same "client-side over fixture data with Undo" contract T1/T3/T4
// already established. Nothing here calls a backend route.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution (same _fx() convention as desk-v1-review.js/-home.js) ─
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaigns() { return _fx().campaigns || []; }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _families() { return _fx().families || []; }
  function _familiesFor(campaignId) { return _families().filter((f) => f.campaignId === campaignId); }
  function _conversations() { return _fx().conversations || []; }
  function _conversationsFor(campaignId) { return _conversations().filter((c) => c.campaignId === campaignId); }

  const _TERMINAL_STATES = new Set(['verified_published', 'you_reported', 'failed', 'skipped', 'archived']);
  const _SCHEDULED_STATES = new Set(['scheduled', 'approved', 'sending', 'submitted']);

  function _familyHasState(fam, states) { return (fam.versions || []).some((v) => states.has ? states.has(v.state) : v.state === states); }
  function _familyNeedsYou(fam) { return _familyHasState(fam, new Set(['needs_review'])); }

  // Content-tab badge count (§3.1: "Badge counts are needs-you items only") —
  // one per FAMILY with at least one needs-review version, matching frame
  // 12a's "Content 2" against the two visible needs-you cards exactly (same
  // per-family counting Home.js's own _needsYouItems() uses for its "pieces
  // to approve" row, kept local here rather than imported since no cross-
  // module import exists in static/js).
  function _needsYouCount(campaignId) { return _familiesFor(campaignId).filter(_familyNeedsYou).length; }

  // ── module state — one campaign-page mount at a time (same single-slot
  // precedent as desk-v1-review.js's `_st`). Rebuilt fresh whenever the
  // campaignId changes (a different campaign, or first mount); preserved
  // across a tab-body-only re-render (filter/view toggles) so the user's
  // List/Calendar choice and filters survive their own interactions. ────────
  let _st = null;
  function _ensureState(campaignId) {
    if (_st && _st.campaignId === campaignId) return _st;
    _st = {
      campaignId, view: 'list', filter: 'all', channelFilter: 'all',
      selection: { scope: 'campaign', label: null },
    };
    return _st;
  }

  // ────────────────────────────────────────────────────────────────────────
  // Summary bar (§3.1). The shell's own crumb already renders "‹ <parent> ·
  // <campaign name>" (desk-v1-shell.js _renderCrumb/_campaignLabel) — this
  // slot renders the rest of the bar the frame draws beside/below that: the
  // state pill + Pause, then the Goal / Channels / Rules groups. Not
  // duplicating the name avoids two "Windows beta testers" on screen.
  // ────────────────────────────────────────────────────────────────────────
  function _ruleChips(camp) {
    const r = camp.rules || {};
    const chips = [];
    chips.push(r.paid ? 'Paid' : 'Organic');
    chips.push(r.reviewMode === 'themes' ? 'Approve themes, then run' : 'You approve each piece');
    if (r.frequencyPerWeek != null) chips.push(`≤${esc(r.frequencyPerWeek)}/wk`);
    if (r.repliesMode) chips.push(r.repliesMode === 'auto_faq' ? 'Auto-answer FAQ' : 'Replies: drafts');
    return chips;
  }

  function deskV1FillCampaignSummary(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    const stateHTML = DeskV1Kit.stateLabelHTML(camp.state, { className: 'desk-v1-camp-state-pill' });
    const pct = camp.goal && camp.goal.tracked && camp.goal.target
      ? Math.max(0, Math.min(100, Math.round((camp.goal.current / camp.goal.target) * 100))) : 0;
    const goalHTML = camp.goal && camp.goal.tracked
      ? `<button type="button" class="desk-v1-camp-summary-goal" data-goal-btn>
          <span class="desk-v1-camp-summary-label">GOAL</span>
          <span class="desk-v1-camp-summary-goaltext">${esc(camp.goal.current)}/${esc(camp.goal.target)} ${esc(camp.goal.metric)}${camp.goal.deadline ? ' by ' + esc(_fmtDate(camp.goal.deadline)) : ''}</span>
          <span class="desk-v1-camp-summary-goalbar"><span style="width:${pct}%"></span></span>
        </button>`
      : `<div class="desk-v1-camp-summary-goal desk-v1-camp-summary-goal-untracked">
          <span class="desk-v1-camp-summary-label">GOAL</span>
          <span class="desk-v1-camp-summary-goaltext">⚠ not tracked yet</span>
        </div>`;
    const chans = (camp.channelIds || []).map(_channel).filter(Boolean);
    const channelsHTML = `<div class="desk-v1-camp-summary-group" data-summary-group="channels">
        <span class="desk-v1-camp-summary-label">CHANNELS</span>
        <div class="desk-v1-camp-summary-badges">${chans.length ? chans.map((ch) => DeskV1Kit.channelBadge(ch, { reviewMode: camp.rules && camp.rules.reviewMode })).join('') : '<span class="desk-v1-home-camp-nochannels">No channels yet</span>'}</div>
      </div>`;
    const rulesHTML = `<div class="desk-v1-camp-summary-group" data-summary-group="rules">
        <span class="desk-v1-camp-summary-label">RULES</span>
        <div class="desk-v1-camp-summary-badges">
          ${_ruleChips(camp).map((c) => `<span class="desk-v1-camp-rule-chip">${esc(c)}</span>`).join('')}
          <button type="button" class="desk-v1-camp-rules-edit" data-rules-edit>Edit</button>
        </div>
      </div>`;

    el.innerHTML = `
      <div class="desk-v1-camp-summary-top">
        ${stateHTML}
        <button type="button" class="desk-v1-camp-pause-btn" data-pause-btn ${camp.state !== 'active' ? 'disabled' : ''}>⏸ Pause</button>
      </div>
      <div class="desk-v1-camp-summary-groups">
        ${goalHTML}
        ${channelsHTML}
        ${rulesHTML}
      </div>`;

    const goalBtn = el.querySelector('[data-goal-btn]');
    if (goalBtn) goalBtn.onclick = () => deskV1Nav('results', { campaignId: camp.id });
    const pauseBtn = el.querySelector('[data-pause-btn]');
    if (pauseBtn) pauseBtn.onclick = () => {
      if (camp.state !== 'active') return;
      const prev = camp.state;
      DeskV1Kit.commandBus.run({
        label: `Paused “${camp.name}”`,
        do: () => { camp.state = 'paused'; deskV1FillCampaignSummary(el, params); },
        undo: () => { camp.state = prev; deskV1FillCampaignSummary(el, params); },
      });
    };
    // T2b owns the rules popover itself (docs/desk_v1_r0_plan.md T2a scope:
    // "Rule chips get an Edit hook only; the popover itself is T2b") — this
    // hook is the backward-compatible seam: undefined today, T2b defines
    // `window.deskV1OpenRulesPopover(campaignId)` without touching this file.
    const editBtn = el.querySelector('[data-rules-edit]');
    if (editBtn) editBtn.onclick = () => {
      if (typeof window.deskV1OpenRulesPopover === 'function') window.deskV1OpenRulesPopover(camp.id);
      else DeskV1Kit.toast('Editing rules lands with the rules popover (T2b).');
    };
  }

  function _fmtDate(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' }).format(new Date(iso)); }
    catch (e) { return iso; }
  }

  // ────────────────────────────────────────────────────────────────────────
  // Tab strip (§3.1: "Content [n] · Conversations [n] · Results"). Content
  // IS this route (deskV1FillCampaignTabBody below); Conversations/Results
  // are separate top-level routes (desk-v1-shell.js ROUTES, T6/T7's own
  // full-page surfaces with parent:'campaign') — clicking those tabs
  // navigates away rather than swapping a local slot, exactly like T4's
  // calendar comment describes for its own `deskV1Nav('calendar', ...)` deep
  // link. Content stays active the whole time this skeleton is on screen.
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillCampaignTabStrip(el, params) {
    const campaignId = params.campaignId;
    const contentCount = _needsYouCount(campaignId);
    // §3.1: "Badge counts are needs-you items only" — for BOTH tabs. A reply
    // waiting ('needs_reply') or a row flagged for you ('needs_you'); stale,
    // reviewed and no-reply rows don't count. A total count read 6 once T6
    // added rows; this reads 3, which is also frame 12a's number.
    const convCount = _conversationsFor(campaignId)
      .filter((c) => c.state === 'needs_reply' || c.state === 'needs_you').length;
    el.innerHTML = `
      <div class="desk-v1-camp-tabs" role="tablist">
        <button type="button" class="desk-v1-camp-tab" aria-selected="true" data-tab="content">Content${contentCount ? ` <span class="desk-v1-camp-tab-badge">${esc(contentCount)}</span>` : ''}</button>
        <button type="button" class="desk-v1-camp-tab" aria-selected="false" data-tab="conversations">Conversations${convCount ? ` <span class="desk-v1-camp-tab-badge">${esc(convCount)}</span>` : ''}</button>
        <button type="button" class="desk-v1-camp-tab" aria-selected="false" data-tab="results">Results</button>
      </div>`;
    el.querySelector('[data-tab="conversations"]').onclick = () => deskV1Nav('conversations', { campaignId });
    el.querySelector('[data-tab="results"]').onclick = () => deskV1Nav('results', { campaignId });
  }

  // ────────────────────────────────────────────────────────────────────────
  // Content tab body (§3.2, §3.3). Toolbar (filter · channel filter ·
  // List/Calendar toggle · + New piece) persists across the toggle; only the
  // body under it swaps between the grouped/filtered list and T4's calendar,
  // mounted straight into this same host via the T0a slot contract
  // (`deskV1RenderCalendar(el, params)` — desk-v1-calendar.js's own header
  // comment: "once T2a lands, its toggle can call [this] straight into its
  // Content-tab body slot with no change needed here").
  // ────────────────────────────────────────────────────────────────────────
  const FILTER_LABELS = {
    all: 'All content', needs_you: 'Needs you', scheduled: 'Scheduled',
    published: 'Published', blocked: 'Blocked', archived: 'Archived',
  };
  const FILTER_ORDER = ['all', 'needs_you', 'scheduled', 'published', 'blocked', 'archived'];

  function deskV1FillCampaignTabBody(el, params) {
    const st = _ensureState(params.campaignId);
    st.el = el;
    _renderTabBody();
  }

  function _renderTabBody() {
    const st = _st; const el = st.el;
    const camp = _campaign(st.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    el.innerHTML = `
      <div class="desk-v1-camp-content">
        <div class="desk-v1-camp-toolbar" id="desk-v1-camp-toolbar"></div>
        <div class="desk-v1-camp-listwrap" id="desk-v1-camp-listwrap"></div>
      </div>`;
    _renderToolbar(camp);
    if (st.view === 'calendar') {
      if (typeof window.deskV1RenderCalendar === 'function') {
        window.deskV1RenderCalendar(document.getElementById('desk-v1-camp-listwrap'), { campaignId: camp.id });
      } else {
        document.getElementById('desk-v1-camp-listwrap').innerHTML = '<div class="desk-v1-stub-inline">Calendar (T4) not loaded.</div>';
      }
    } else {
      _renderList(camp);
    }
  }

  function _renderToolbar(camp) {
    const st = _st;
    const host = document.getElementById('desk-v1-camp-toolbar');
    if (!host) return;
    const chFilterLabel = st.channelFilter === 'all' ? 'All channels' : ((_channel(st.channelFilter) || {}).label || 'All channels');
    host.innerHTML = `
      <div class="desk-v1-camp-toolbar-left">
        <div class="desk-v1-addto-wrap desk-v1-camp-filterwrap">
          <button type="button" class="desk-v1-camp-filter-btn" data-filter-trigger>${esc(FILTER_LABELS[st.filter] || 'All content')} ▾</button>
        </div>
        <div class="desk-v1-addto-wrap desk-v1-camp-filterwrap">
          <button type="button" class="desk-v1-camp-filter-btn" data-channel-trigger>${esc(chFilterLabel)} ▾</button>
        </div>
        <div class="desk-v1-camp-viewtoggle" role="tablist" aria-label="List or calendar view">
          <button type="button" data-view-btn="list" aria-pressed="${st.view === 'list'}">☰ List</button>
          <button type="button" data-view-btn="calendar" aria-pressed="${st.view === 'calendar'}">▦ Calendar</button>
        </div>
      </div>
      <button type="button" class="desk-v1-camp-newpiece" data-new-piece>+ New piece</button>`;

    host.querySelector('[data-filter-trigger]').onclick = (e) => {
      const items = FILTER_ORDER.map((k) => ({ id: k, label: FILTER_LABELS[k] }));
      DeskV1Kit.addToMenu(e.currentTarget, items, (id) => { st.filter = id; _renderTabBody(); }, { noAppendNew: true });
    };
    host.querySelector('[data-channel-trigger]').onclick = (e) => {
      const items = [{ id: 'all', label: 'All channels' }].concat(_channels().map((ch) => ({ id: ch.id, label: ch.label })));
      DeskV1Kit.addToMenu(e.currentTarget, items, (id) => { st.channelFilter = id; _renderTabBody(); }, { noAppendNew: true });
    };
    host.querySelectorAll('[data-view-btn]').forEach((b) => b.onclick = () => { st.view = b.dataset.viewBtn; _renderTabBody(); });
    host.querySelector('[data-new-piece]').onclick = () => _newPiece(camp);
  }

  // ── list body: grouped ("All content") or a single flat filtered list ────
  function _matchesChannel(fam, channelFilter) {
    return channelFilter === 'all' || (fam.versions || []).some((v) => v.channelId === channelFilter);
  }
  function _familyGroup(fam) {
    if (_familyNeedsYou(fam)) return 'needs_you';
    if (_familyHasState(fam, _SCHEDULED_STATES)) return 'scheduled';
    if (_familyHasState(fam, _TERMINAL_STATES)) return 'published';
    // R0's calendar-ticket fixtures (fam-followup-post: planned only;
    // fam-arm-faq: blocked only) don't fit the doc's 3 named §3.2 groups —
    // rather than drop them from "All content" (the acceptance check says
    // grouped view covers everything), they collect under this 4th, equally
    // collapsed-by-default heading. Flagged in the final report.
    return 'other';
  }
  const GROUP_TITLES = { needs_you: 'NEEDS YOU', scheduled: 'SCHEDULED', published: 'PUBLISHED', other: 'PLANNED' };
  const GROUP_ORDER = ['needs_you', 'scheduled', 'published', 'other'];
  const COLLAPSED_BY_DEFAULT = new Set(['published', 'other']);

  let _expandedGroups = new Set();

  function _renderList(camp) {
    const st = _st;
    const host = document.getElementById('desk-v1-camp-listwrap');
    if (!host) return;
    let fams = _familiesFor(camp.id).filter((f) => _matchesChannel(f, st.channelFilter));

    let bodyHTML;
    if (st.filter === 'all') {
      const groups = {};
      for (const f of fams) { const g = _familyGroup(f); (groups[g] = groups[g] || []).push(f); }
      bodyHTML = GROUP_ORDER.filter((g) => groups[g] && groups[g].length).map((g) => {
        const items = groups[g];
        const collapsed = COLLAPSED_BY_DEFAULT.has(g) && !_expandedGroups.has(g);
        return `<div class="desk-v1-camp-group">
          <div class="desk-v1-camp-group-head">
            <span class="desk-v1-camp-group-title">${GROUP_TITLES[g]} · ${items.length}</span>
            ${collapsed ? `<button type="button" class="desk-v1-camp-group-show" data-group-show="${g}">show ›</button>` : ''}
          </div>
          ${collapsed ? '' : `<div class="desk-v1-camp-cards">${items.map((f) => _familyCardHTML(f, camp)).join('')}</div>`}
        </div>`;
      }).join('') || '<div class="desk-v1-camp-empty">No content yet — drop a channel or material below, or ＋ New piece.</div>';
    } else {
      const pred = {
        needs_you: _familyNeedsYou,
        scheduled: (f) => _familyHasState(f, _SCHEDULED_STATES),
        published: (f) => _familyHasState(f, _TERMINAL_STATES),
        blocked: (f) => _familyHasState(f, new Set(['blocked'])),
        archived: (f) => _familyHasState(f, new Set(['archived'])),
      }[st.filter] || (() => true);
      const items = fams.filter(pred);
      bodyHTML = `<div class="desk-v1-camp-cards">${items.map((f) => _familyCardHTML(f, camp)).join('') || '<div class="desk-v1-camp-empty">Nothing matches this filter.</div>'}</div>`;
    }

    // pd-drop-target is NOT statically present (Dave's review: 12a has no
    // container outline at rest) — onActivate/onTeardown below toggle it
    // for the duration of a drag only.
    host.innerHTML = `<div class="desk-v1-camp-listarea" id="desk-v1-camp-listarea" data-listarea>${bodyHTML}</div>`;
    host.querySelectorAll('[data-group-show]').forEach((b) => b.onclick = () => { _expandedGroups.add(b.dataset.groupShow); _renderList(camp); });
    _wireCards(host, camp);
  }

  // ── content card (§3.2 CNT-01) ────────────────────────────────────────────
  function _kindMeta(fam) {
    if (fam.kind === 'article') return `📄 Article · ${esc(fam.wordCount || 0)} words`;
    if (fam.kind === 'video') return `▶ Video · ${fam.versions.length} version${fam.versions.length === 1 ? '' : 's'}`;
    return null;
  }
  function _previewHTML(fam) {
    if (fam.kind === 'video') {
      const primary = fam.versions.find((v) => v.state === 'verified_published') || fam.versions.find((v) => v.format) || fam.versions[0];
      const format = (primary && primary.format) || '16:9';
      const vd = (_fx().videoDetail || {})[fam.id];
      const durationSec = vd && vd.scenes ? vd.scenes.reduce((s, sc) => s + (sc.durationSec || 0), 0) : null;
      const durLabel = durationSec != null ? ` · ${Math.floor(durationSec / 60)}:${String(durationSec % 60).padStart(2, '0')}` : '';
      return `<div class="desk-v1-camp-preview desk-v1-camp-preview-video"><span class="desk-v1-camp-preview-play">▶</span><span class="desk-v1-camp-preview-caption">${esc(format)}${durLabel}</span></div>`;
    }
    const text = (_fx().contentPreview || {})[fam.id];
    return `<div class="desk-v1-camp-preview desk-v1-camp-preview-text">${text ? esc(text) : ''}</div>`;
  }
  function _versionRowHTML(fam, v) {
    const ch = _channel(v.channelId);
    const badgeHTML = ch ? DeskV1Kit.channelBadge(ch, {}) : '<span class="desk-v1-camp-nochannel">No channel</span>';
    const fmt = v.format ? ` · ${esc(v.format)}` : '';
    const stateHTML = DeskV1Kit.stateLabelHTML(v.state);
    let detail = '';
    if (v.publishedAt) detail = _fmtWhenShort(v.publishedAt);
    else if (v.publishAt) detail = _fmtWhenShort(v.publishAt);
    else if (fam.render && fam.render.jobId && fam.kind === 'video') detail = `render ${esc(fam.render.jobId.replace('render-', ''))} ${esc(fam.render.status)}`;
    return `<div class="desk-v1-camp-vrow" data-version-id="${esc(v.id)}">
      <span class="desk-v1-camp-vrow-badge">${badgeHTML}${fmt}</span>
      <span class="desk-v1-camp-vrow-state">${stateHTML}</span>
      ${detail ? `<span class="desk-v1-camp-vrow-detail">· ${esc(detail)}</span>` : ''}
    </div>`;
  }
  function _fmtWhenShort(iso) {
    try {
      const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
      return new Intl.DateTimeFormat(undefined, { timeZone: cfg.user_timezone || undefined, weekday: 'short', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
    } catch (e) { return iso; }
  }
  function _primaryAction(fam) {
    const needsReview = fam.versions.find((v) => v.state === 'needs_review');
    if (needsReview) return { label: fam.kind === 'video' ? 'Watch & review' : 'Review', kind: 'review', versionId: needsReview.id };
    if (fam.kind === 'video') return { label: 'Open', kind: 'video' };
    return { label: 'Open', kind: 'open-stub' };
  }
  function _familyCardHTML(fam, camp) {
    const meta = _kindMeta(fam);
    const action = _primaryAction(fam);
    // pd-drop-target likewise applied only during an active drag (see
    // _contentTargetAdapter.onActivate/onTeardown), not at rest.
    return `<div class="desk-v1-camp-card" data-family-id="${esc(fam.id)}">
      ${_previewHTML(fam)}
      <div class="desk-v1-camp-card-body">
        ${meta ? `<div class="desk-v1-camp-card-meta">${meta}</div>` : ''}
        <div class="desk-v1-camp-card-title">${esc(fam.title)}</div>
        <div class="desk-v1-camp-card-versions">${fam.versions.map((v) => _versionRowHTML(fam, v)).join('')}</div>
        <div class="desk-v1-camp-card-result" aria-live="polite"></div>
      </div>
      <div class="desk-v1-camp-card-actions">
        <button type="button" class="desk-v1-camp-card-primary" data-primary-action>${esc(action.label)}</button>
        <div class="desk-v1-camp-card-more">
          <button type="button" class="desk-v1-camp-card-morebtn" data-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
        </div>
      </div>
    </div>`;
  }

  function _wireCards(host, camp) {
    host.querySelectorAll('[data-family-id]').forEach((cardEl) => {
      const fam = _familiesFor(camp.id).find((f) => f.id === cardEl.dataset.familyId);
      if (!fam) return;
      const primary = cardEl.querySelector('[data-primary-action]');
      if (primary) primary.onclick = (e) => { e.stopPropagation(); _runPrimaryAction(fam, camp); };
      const more = cardEl.querySelector('[data-more-btn]');
      if (more) more.onclick = (e) => { e.stopPropagation(); _openCardMenu(e.currentTarget, fam, camp); };
      // Selecting the card (not its buttons) scopes the Posy box to it
      // (§3.4 INS-01: "the campaign, a card, a version").
      cardEl.addEventListener('click', () => _setSelection('card', fam.title));
    });
  }

  function _runPrimaryAction(fam, camp) {
    const action = _primaryAction(fam);
    if (action.kind === 'review') deskV1Nav('review', { campaignId: camp.id, versionId: action.versionId });
    else if (action.kind === 'video') deskV1Nav('video', { campaignId: camp.id, familyId: fam.id });
    else DeskV1Kit.toast('Opening a scheduled or published piece for reading lands with a read-only viewer (later ticket) — T3’s review surface only covers needs-review items in R0.');
  }

  // ── ⋯ menu (§3.2: "Add a channel version ▸ · Move to another channel ▸ ·
  // Duplicate · Skip · Archive"). Plain DOM menu, same shape as
  // desk-v1-review.js's _openMoreMenu (no full re-render, so it doesn't
  // fight an open submenu). ─────────────────────────────────────────────────
  function _openCardMenu(triggerEl, fam, camp) {
    const host = triggerEl.parentElement;
    const existing = document.querySelector('.desk-v1-camp-cardmenu');
    if (existing) existing.remove();
    if (host.querySelector(':scope > .desk-v1-camp-cardmenu')) return;
    const availableChannels = _channels().filter((ch) => !fam.versions.some((v) => v.channelId === ch.id));
    const menu = document.createElement('div');
    menu.className = 'desk-v1-camp-cardmenu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = `
      <button type="button" data-menu-add ${availableChannels.length ? '' : 'disabled'}>Add a channel version ▸</button>
      <button type="button" data-menu-move ${fam.versions.length ? '' : 'disabled'}>Move to another channel ▸</button>
      <button type="button" data-menu-dup>Duplicate</button>
      <button type="button" data-menu-skip>Skip</button>
      <button type="button" data-menu-archive>Archive</button>`;
    host.style.position = 'relative';
    host.appendChild(menu);
    const close = () => { menu.remove(); document.removeEventListener('click', closer); };
    const closer = (e) => { if (!menu.contains(e.target) && e.target !== triggerEl) close(); };
    setTimeout(() => document.addEventListener('click', closer), 0);

    const addBtn = menu.querySelector('[data-menu-add]');
    if (addBtn && availableChannels.length) addBtn.onclick = () => {
      close();
      DeskV1Kit.addToMenu(triggerEl, availableChannels.map((ch) => ({ id: ch.id, label: ch.label })),
        (chId) => _addChannelVersion(fam, chId), { noAppendNew: true });
    };
    const moveBtn = menu.querySelector('[data-menu-move]');
    if (moveBtn && fam.versions.length) moveBtn.onclick = () => {
      close();
      const fromVersion = fam.versions.length === 1 ? fam.versions[0] : null;
      const pickFrom = (v) => {
        const targets = _channels().filter((ch) => ch.id !== v.channelId);
        DeskV1Kit.addToMenu(triggerEl, targets.map((ch) => ({ id: ch.id, label: ch.label })),
          (chId) => _moveToChannel(fam, v, chId), { noAppendNew: true });
      };
      if (fromVersion) pickFrom(fromVersion);
      else DeskV1Kit.addToMenu(triggerEl, fam.versions.map((v) => ({ id: v.id, label: (_channel(v.channelId) || {}).label || v.id })),
        (vId) => pickFrom(fam.versions.find((v) => v.id === vId)), { noAppendNew: true });
    };
    menu.querySelector('[data-menu-dup]').onclick = () => { close(); _duplicateFamily(fam, camp); };
    menu.querySelector('[data-menu-skip]').onclick = () => { close(); _skipFamily(fam); };
    menu.querySelector('[data-menu-archive]').onclick = () => { close(); _archiveFamily(fam); };
  }

  function _addChannelVersion(fam, channelId) {
    const ch = _channel(channelId);
    const v = { id: fam.id + '-v-' + Date.now().toString(36), channelId, state: 'drafting', revision: 0 };
    DeskV1Kit.commandBus.run({
      label: `Added a ${ch ? ch.label : channelId} version to “${fam.title}”`,
      do: () => { fam.versions.push(v); _renderList(_campaign(fam.campaignId)); },
      undo: () => { const i = fam.versions.indexOf(v); if (i >= 0) fam.versions.splice(i, 1); _renderList(_campaign(fam.campaignId)); },
    });
  }

  // §3.2: "Moving a version to another channel is only available through
  // ⋯ → Move to another channel ▸, and it confirms: 'Move from X to Y? The X
  // version will be archived.'" — window.confirm follows the exact
  // established precedent in desk-v1-calendar.js's own reschedule-approval
  // confirm rather than inventing a second modal component for one dialog.
  function _moveToChannel(fam, version, toChannelId) {
    const fromCh = _channel(version.channelId);
    const toCh = _channel(toChannelId);
    const proceed = window.confirm(`Move from ${fromCh ? fromCh.label : 'this channel'} to ${toCh ? toCh.label : 'the new channel'}? The ${fromCh ? fromCh.label : 'old'} version will be archived.`);
    if (!proceed) return;
    const prevState = version.state;
    const prevChannel = version.channelId;
    const newVersion = { id: fam.id + '-v-' + Date.now().toString(36), channelId: toChannelId, state: prevState === 'needs_review' ? 'drafting' : prevState, revision: version.revision };
    DeskV1Kit.commandBus.run({
      label: `Moved “${fam.title}” from ${fromCh ? fromCh.label : 'a channel'} to ${toCh ? toCh.label : 'a channel'}`,
      do: () => { version.state = 'archived'; fam.versions.push(newVersion); _renderList(_campaign(fam.campaignId)); },
      undo: () => { version.state = prevState; version.channelId = prevChannel; const i = fam.versions.indexOf(newVersion); if (i >= 0) fam.versions.splice(i, 1); _renderList(_campaign(fam.campaignId)); },
    });
  }

  function _duplicateFamily(fam, camp) {
    const dup = {
      id: fam.id + '-copy-' + Date.now().toString(36), campaignId: fam.campaignId, kind: fam.kind,
      title: fam.title + ' (copy)', wordCount: fam.wordCount,
      versions: [{ id: fam.id + '-copy-v1-' + Date.now().toString(36), channelId: null, state: 'drafting', revision: 0 }],
    };
    DeskV1Kit.commandBus.run({
      label: `Duplicated “${fam.title}”`,
      do: () => { _fx().families.push(dup); _renderList(camp); },
      undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === dup.id); if (i >= 0) arr.splice(i, 1); _renderList(camp); },
    });
  }

  // Skip/Archive act on the whole card (every non-terminal version) — the
  // spec's §3.2 ⋯ menu lists them at card level, not per-version (T3 already
  // owns the per-version Skip/Archive inside review). Interpretation flagged
  // in the final report: the doc doesn't spell out card-level semantics for
  // a multi-version family.
  function _disposeFamily(fam, nextState, label) {
    const targets = fam.versions.filter((v) => !_TERMINAL_STATES.has(v.state));
    if (!targets.length) { DeskV1Kit.toast('Nothing to change — every version already concluded.'); return; }
    const prev = targets.map((v) => v.state);
    DeskV1Kit.commandBus.run({
      label: `${label} “${fam.title}”`,
      do: () => { targets.forEach((v) => { v.state = nextState; }); _renderList(_campaign(fam.campaignId)); },
      undo: () => { targets.forEach((v, i) => { v.state = prev[i]; }); _renderList(_campaign(fam.campaignId)); },
    });
  }
  function _skipFamily(fam) { _disposeFamily(fam, 'skipped', 'Skipped'); }
  function _archiveFamily(fam) { _disposeFamily(fam, 'archived', 'Archived'); }

  function _newPiece(camp) {
    const fam = {
      id: 'fam-new-' + Date.now().toString(36), campaignId: camp.id, kind: 'post',
      title: 'New piece', versions: [{ id: 'v-new-' + Date.now().toString(36), channelId: null, state: 'drafting', revision: 0 }],
    };
    DeskV1Kit.commandBus.run({
      label: `Created “${fam.title}”`,
      do: () => { _fx().families.push(fam); _renderTabBody(); },
      undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === fam.id); if (i >= 0) arr.splice(i, 1); _renderTabBody(); },
    });
  }

  // ── drag & drop (§3.2 table, §10). Reuses the Add tray's own shelf items
  // as the drag SOURCE (deskV1RenderShelfPair, T1) with a custom
  // targetAdapter whose targets are content cards + the empty list area,
  // instead of Home's campaign cards — exactly the "caller supplies its own
  // targetAdapter" seam deskV1RenderShelfPair's own comment describes. ──────
  function _setCardResultText(cardEl, text) {
    const t = cardEl.querySelector('.desk-v1-camp-card-result');
    if (t) t.textContent = text || '';
  }
  function _listAreaResultText(text) {
    const el = document.getElementById('desk-v1-camp-listarea');
    if (!el) return;
    let t = el.querySelector('.desk-v1-camp-listarea-result');
    if (!t) { t = document.createElement('div'); t.className = 'desk-v1-camp-listarea-result'; el.insertBefore(t, el.firstChild); }
    t.textContent = text || '';
    if (!text) t.remove();
  }

  function _hoverContentAt(x, y, dragData) {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest && el.closest('.desk-v1-camp-card');
    document.querySelectorAll('.desk-v1-camp-card').forEach((c) => {
      if (c !== card) { c.classList.remove('pd-drop-hover'); _setCardResultText(c, ''); }
    });
    if (card) {
      card.classList.add('pd-drop-hover');
      _setCardResultText(card, dragData.type === 'channel' ? `Drop to add ${dragData.label} as a new version` : `Drop to attach ${dragData.label} to this piece`);
      _listAreaResultText('');
      return;
    }
    const listArea = el && el.closest && el.closest('#desk-v1-camp-listarea');
    if (listArea) {
      listArea.classList.add('pd-drop-hover');
      _listAreaResultText(dragData.type === 'channel' ? `Drop to add ${dragData.label} to this campaign` : `Drop to create a new piece from ${dragData.label}`);
    } else {
      const la = document.getElementById('desk-v1-camp-listarea');
      if (la) la.classList.remove('pd-drop-hover');
      _listAreaResultText('');
    }
  }

  function _resolveContentDropAt(x, y) {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest && el.closest('.desk-v1-camp-card');
    if (card) return { type: 'card', familyId: card.dataset.familyId };
    const listArea = el && el.closest && el.closest('#desk-v1-camp-listarea');
    if (listArea) return { type: 'listarea' };
    return null;
  }

  function _handleContentDrop(resolved, dragData, camp) {
    if (resolved.type === 'card') {
      const fam = _familiesFor(camp.id).find((f) => f.id === resolved.familyId);
      if (!fam) return;
      if (dragData.type === 'channel') {
        if (fam.versions.some((v) => v.channelId === dragData.channelId)) { DeskV1Kit.toast(`${dragData.label} is already a version on “${fam.title}”.`); return; }
        _addChannelVersion(fam, dragData.channelId);
      } else {
        // Material → card: attaches the asset (no generation) — modelled
        // here as a note on the family's title-adjacent preview rather than a
        // new version, since §3.2's AttachAsset carries no state of its own.
        DeskV1Kit.commandBus.run({
          label: `Attached “${dragData.label}” to “${fam.title}”`,
          do: () => { fam.attachedAssets = (fam.attachedAssets || []).concat([dragData.asset.id]); _renderList(camp); },
          undo: () => { fam.attachedAssets = (fam.attachedAssets || []).filter((id) => id !== dragData.asset.id); _renderList(camp); },
        });
      }
    } else if (resolved.type === 'listarea') {
      if (dragData.type === 'channel') {
        if (camp.channelIds.includes(dragData.channelId)) { DeskV1Kit.toast(`${dragData.label} is already on “${camp.name}”.`); return; }
        DeskV1Kit.commandBus.run({
          label: `Added ${dragData.label} to “${camp.name}”`,
          do: () => { camp.channelIds.push(dragData.channelId); deskV1FillCampaignSummary(document.getElementById('desk-v1-camp-summary'), { campaignId: camp.id }); },
          undo: () => { const i = camp.channelIds.indexOf(dragData.channelId); if (i >= 0) camp.channelIds.splice(i, 1); deskV1FillCampaignSummary(document.getElementById('desk-v1-camp-summary'), { campaignId: camp.id }); },
        });
      } else {
        const asset = dragData.asset;
        const fam = { id: 'fam-drop-' + Date.now().toString(36), campaignId: camp.id, kind: asset.kind, title: asset.title, versions: [{ id: 'v-drop-' + Date.now().toString(36), channelId: null, state: 'drafting', revision: 0 }] };
        DeskV1Kit.commandBus.run({
          label: `Created “${fam.title}” from ${asset.title}`,
          do: () => { _fx().families.push(fam); _renderTabBody(); },
          undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === fam.id); if (i >= 0) arr.splice(i, 1); _renderTabBody(); },
        });
      }
    }
  }

  function _contentTargetAdapter(camp) {
    return {
      onActivate: () => {
        document.querySelectorAll('.desk-v1-camp-card').forEach((c) => c.classList.add('pd-drop-target'));
        const la = document.getElementById('desk-v1-camp-listarea');
        if (la) la.classList.add('pd-drop-target');
      },
      onMove: (x, y, dragData) => _hoverContentAt(x, y, dragData),
      onDrop: (x, y) => _resolveContentDropAt(x, y),
      afterDrop: (resolved) => {},
      onTeardown: () => {
        document.querySelectorAll('.desk-v1-camp-card').forEach((c) => { c.classList.remove('pd-drop-target', 'pd-drop-hover'); _setCardResultText(c, ''); });
        const la = document.getElementById('desk-v1-camp-listarea');
        if (la) la.classList.remove('pd-drop-target', 'pd-drop-hover');
        _listAreaResultText('');
      },
      addToItems: () => _familiesFor(camp.id).map((f) => ({ id: f.id, label: f.title })),
      onPick: () => {},
    };
  }

  // The Add tray's shelf pair calls afterDrop(resolved, dragData) via
  // deskV1RenderShelfPair -> _wireShelfItem's adapter contract (desk-v1-
  // home.js). This wraps _contentTargetAdapter so the campaign fixture
  // mutation actually runs, since the generic adapter above only resolves
  // WHAT was hit — the campaign-specific "what happens" stays here.
  function _addTrayAdapter(camp) {
    const base = _contentTargetAdapter(camp);
    return Object.assign({}, base, {
      afterDrop: (resolved, dragData) => { if (resolved) _handleContentDrop(resolved, dragData, camp); },
      onPick: (pickedId, dragData) => {
        // Keyboard/click "Add to…" path (UX-05) resolves to a family
        // (attach) rather than a campaign — reusing the same _handleContentDrop
        // as a synthetic card-drop keeps one code path for both entry points.
        _handleContentDrop({ type: 'card', familyId: pickedId }, dragData, camp);
      },
    });
  }

  // Wires the SAME drop targets a second time for drags that originate
  // inside the list itself (channel badges dropped straight from the
  // summary bar are out of scope; this only needs to exist once, wired from
  // the Add tray's shelf items, per _renderTabBody -> Add tray mount order).
  function _wireListDrop() { /* no-op: targets are passive; adapter above drives them from the Add tray's pointerdown */ }

  // ────────────────────────────────────────────────────────────────────────
  // Posy box (§3.4, right column). Scope label follows `_st.selection`,
  // updated by `_setSelection()` below whenever the campaign/a card is
  // selected. Suggestion + chips come from CAMPAIGN_SUGGESTIONS (T2a's own
  // fixture section) keyed by campaignId.
  // ────────────────────────────────────────────────────────────────────────
  function _setSelection(scope, label) {
    if (!_st) return;
    _st.selection = { scope, label };
    const host = document.getElementById('desk-v1-camp-rightcol');
    if (host) deskV1FillCampaignRightColumn(host, { campaignId: _st.campaignId });
  }

  function deskV1FillCampaignRightColumn(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = ''; return; }
    const st = _ensureState(camp.id);
    const sugg = (_fx().campaignSuggestions || {})[camp.id] || {};
    const scopeLabel = st.selection.scope === 'card' ? st.selection.label : camp.name;
    el.innerHTML = `<div class="desk-v1-camp-posy">${DeskV1Kit.posyBoxHTML({
      inputId: 'desk-v1-camp-posy-input', scopeLabel, suggestion: sugg.suggestion, chips: sugg.chips,
    })}</div>`;
    DeskV1Kit.bindPosyBox(el.querySelector('.desk-v1-camp-posy'), 'desk-v1-camp-posy-input', (text) => {
      DeskV1Kit.toast('Sent to Posy: “' + text + '”');
    }, {
      onScopeClick: () => _setSelection('campaign', null),
    });
  }

  // ────────────────────────────────────────────────────────────────────────
  // Add tray (§3.6): the same two shelves as Home, scoped to this campaign —
  // "reuse, don't fork" (docs/desk_v1_r0_plan.md). Channels already on the
  // campaign are hidden (`hideChannelIds`) rather than merely dimmed — CSS
  // opacity on a still-interactive shelf item would leave a drop target that
  // silently no-ops (already-attached), which is worse than not offering it.
  // ────────────────────────────────────────────────────────────────────────
  function deskV1FillCampaignAddTray(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = ''; return; }
    el.innerHTML = `
      <details class="desk-v1-camp-addtray-details">
        <summary class="desk-v1-camp-addtray-summary">+ Add ▾</summary>
        <div class="desk-v1-camp-addtray-body">
          <div class="desk-v1-camp-addtray-shelf">
            <div class="desk-v1-home-shelf-title">Channels</div>
            <div class="desk-v1-home-shelf-items" id="desk-v1-camp-addtray-channels"></div>
          </div>
          <div class="desk-v1-camp-addtray-shelf">
            <div class="desk-v1-home-shelf-title">Material</div>
            <div class="desk-v1-home-shelf-items" id="desk-v1-camp-addtray-material"></div>
          </div>
        </div>
      </details>`;
    window.deskV1RenderShelfPair({
      channelsHost: document.getElementById('desk-v1-camp-addtray-channels'),
      materialHost: document.getElementById('desk-v1-camp-addtray-material'),
    }, {
      hideChannelIds: camp.channelIds || [],
      targetAdapter: _addTrayAdapter(camp),
    });
  }

  window.deskV1FillCampaignSummary = deskV1FillCampaignSummary;
  window.deskV1FillCampaignTabStrip = deskV1FillCampaignTabStrip;
  window.deskV1FillCampaignTabBody = deskV1FillCampaignTabBody;
  window.deskV1FillCampaignRightColumn = deskV1FillCampaignRightColumn;
  window.deskV1FillCampaignAddTray = deskV1FillCampaignAddTray;
})();
