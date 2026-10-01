// Desk v1 (MC-977) — IA7: Engagement dashboard (docs/THE_DESK_V1_IA_REVISION.md
// §5 row IA7, §7). Window-bridged module, no `import` (ground rule 1).
//
// Reached three ways, all through the shell's pre-registered `engagement`
// route (desk-v1-shell.js): Home's own "💬 Engagement · n" header link (no
// params — every project), a project page's Engagement strip "Open ›"
// (IA6, `{projectId}` — that project only), or a campaign's Conversations
// tab (`{campaignId}`, via the `conversations` panel alias).
//
// The `campaignId` case does NOT re-implement the list+thread+actions
// surface a second time — it hands off straight to `deskV1RenderConversations`
// (desk-v1-conversations.js), which already is "a campaign's own conversations,
// filtered to it, with an all-campaigns scope toggle" (IA_REVISION §7's own
// "Exists today" table). IA7's acceptance line "campaign Conversations tab
// embeds the SAME component ... not a copy" is satisfied literally: it is the
// same function call, not a parallel reimplementation.
//
// The project-wide/global case below is the new surface: three lanes
// (Incoming/Suggested/Sent, §7's v1 list) and filters (project · campaign,
// incl. `No campaign` · channel · source, §7's own filter list). Selecting a
// row jumps to the conversation's real home (its campaign's Conversations
// tab, same deep-link shape Home's own Needs-you row already uses) rather
// than growing a second thread/actions panel here.
//
// desk_v1_live ON (R1-W S8): the rows are the Desk's engagement feed
// (`GET /api/desk/engagement`, loaded by desk-v1-conversations.js's shared
// `deskV1EngagementGate` so both surfaces read one copy), and a "Check now"
// button runs `POST /api/desk/engagement/poll` for the project in scope. HOW an
// account is read (platform API, paid, or the browser pane, free, the default)
// is the user's choice per account on the Presence page, not here: this page
// only reports what that choice produced. Anything the chosen route could not
// read is listed under the rows, so an empty lane never reads as "no one is
// talking".
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _projects() { return _fx().projects || []; }
  function _campaigns() { return _fx().campaigns || []; }
  function _channels() { return _fx().channels || []; }
  function _allConversations() { return _fx().conversations || []; }
  function _detail(id) { return (_fx().conversationDetail || {})[id] || {}; }
  function _project(id) { return _projects().find((p) => p.id === id) || null; }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _channel(id) { return _channels().find((c) => c.id === id) || null; }

  // Same three lanes + match rules a project page's own Engagement strip
  // already established (desk-v1-project.js `_ENGAGEMENT_LANES`, IA6) — kept
  // as its own copy here (not a shared export) because the two surfaces
  // don't share a module and the definition is three short lines; both must
  // still agree with §7's wording, checked by desk-v1-engagement.mjs.
  const LANES = [
    { key: 'incoming', glyph: '💬', label: 'Incoming', match: (c) => c.state === 'needs_you' },
    { key: 'suggested', glyph: '✎', label: 'Suggested · awaiting you', match: (c) => c.state === 'needs_reply' },
    { key: 'sent', glyph: '✓', label: 'Sent', match: (c) => c.state === 'sent' },
  ];

  // Home header count (§1: "💬 Engagement · n"): every conversation, across
  // every project, still sitting in Incoming or Suggested — the two lanes
  // that need a person's action. Sent doesn't add to that count.
  //
  // Live, Home and the project pages read the same `conversations` list, and the
  // workspace read (M1) does not carry the feed: the first count asked for loads
  // it once, then redraws whatever page asked. Until it lands the count is 0,
  // which the header leaves unlabelled rather than claiming "nothing waiting".
  function deskV1EngagementCount() {
    const S = window.DeskV1Store;
    if (S.live() && typeof window.deskV1EngagementPhase === 'function' && window.deskV1EngagementPhase() === 'idle') {
      window.deskV1LoadEngagement().then(() => { if (typeof window.deskV1Render === 'function') window.deskV1Render(); });
    }
    return _allConversations().filter((c) => c.state === 'needs_you' || c.state === 'needs_reply').length;
  }

  // ── module state — one dashboard mounted at a time. Reset only when a
  // caller hands in an explicit projectId different from the current filter,
  // so switching lanes/filters in place never loses your place. ────────────
  let _state = null;
  function _ensureState(params) {
    const p = params || {};
    if (!_state) _state = { lane: 'incoming', project: p.projectId || 'all', campaign: 'all', channel: 'all', source: 'all' };
    return _state;
  }

  function _passesFilters(st, c) {
    if (st.project !== 'all' && c.projectId !== st.project) return false;
    if (st.campaign === '__none__') { if (c.campaignId) return false; }
    else if (st.campaign !== 'all' && c.campaignId !== st.campaign) return false;
    if (st.channel !== 'all' && c.channelId !== st.channel) return false;
    if (st.source !== 'all' && c.source !== st.source) return false;
    return true;
  }

  function _filtered(st) { return _allConversations().filter((c) => _passesFilters(st, c)); }

  function _laneCounts(st) {
    const base = _filtered(st);
    const counts = {};
    LANES.forEach((l) => { counts[l.key] = base.filter(l.match).length; });
    return counts;
  }

  function _rowHTML(conv) {
    const d = _detail(conv.id);
    const proj = _project(conv.projectId);
    const camp = conv.campaignId ? _campaign(conv.campaignId) : null;
    const channel = conv.channelId ? _channel(conv.channelId) : null;
    const author = d.author || (channel && channel.identity) || 'Someone';
    const age = d.ageLabel ? `<span class="desk-v1-eng-age">${esc(d.ageLabel)}</span>` : '';
    const campLabel = camp ? esc(camp.plan.title) : 'No campaign';
    return `
      <button type="button" class="desk-v1-eng-row" data-eng-conv="${esc(conv.id)}" data-eng-campaign="${esc(conv.campaignId || '')}">
        <div class="desk-v1-eng-row-meta">${esc(proj ? proj.name : '')} · ${campLabel}</div>
        <div class="desk-v1-eng-row-head"><span class="desk-v1-eng-author">${esc(author)}</span>${age}</div>
        <div class="desk-v1-eng-snippet">${esc(conv.excerpt || '')}</div>
      </button>`;
  }

  function _filterSelectsHTML(st) {
    const projOpts = _projects().map((p) => `<option value="${esc(p.id)}"${st.project === p.id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
    const camps = _campaigns().filter((c) => st.project === 'all' || c.projectId === st.project);
    const campOpts = camps.map((c) => `<option value="${esc(c.id)}"${st.campaign === c.id ? ' selected' : ''}>${esc(c.plan.title)}</option>`).join('');
    const chanOpts = _channels().map((c) => `<option value="${esc(c.id)}"${st.channel === c.id ? ' selected' : ''}>${esc(c.label)}</option>`).join('');
    return `
      <div class="desk-v1-eng-filters">
        <select class="desk-v1-eng-filter" data-eng-filter="project"><option value="all">All projects</option>${projOpts}</select>
        <select class="desk-v1-eng-filter" data-eng-filter="campaign">
          <option value="all">All campaigns</option>
          <option value="__none__"${st.campaign === '__none__' ? ' selected' : ''}>No campaign</option>
          ${campOpts}
        </select>
        <select class="desk-v1-eng-filter" data-eng-filter="channel"><option value="all">All channels</option>${chanOpts}</select>
        <select class="desk-v1-eng-filter" data-eng-filter="source">
          <option value="all">All sources</option>
          <option value="our_posts"${st.source === 'our_posts' ? ' selected' : ''}>On our posts</option>
          <option value="mentions"${st.source === 'mentions' ? ' selected' : ''}>Mentions</option>
          <option value="discussions"${st.source === 'discussions' ? ' selected' : ''}>Discussions</option>
        </select>
      </div>`;
  }

  // §7 v1 / §8 Q3 ("out of v1; Engagement shows `Replies: drafted for your
  // review`"): per-project banner off `project.presence.replies` so an
  // empty Sent lane never reads as broken, and "automated responses" reads
  // as visibly off. Kept as its own copy here (not a shared export), same
  // reasoning as the LANES duplication above. Shown only once a single
  // project is in scope — "per project" has nothing to say for "all".
  function _repliesBannerCopy(replies) {
    return (replies === 'drafts' || !replies) ? 'drafted for your review' : String(replies);
  }
  function _repliesBannerHTML(st) {
    if (st.project === 'all') return '';
    const proj = _project(st.project);
    const replies = proj && proj.presence ? proj.presence.replies : null;
    return `<div class="desk-v1-eng-replies-banner">Replies: ${esc(_repliesBannerCopy(replies))}</div>`;
  }

  let _mountEl = null;
  let _mountParams = null;
  let _poll = { busy: false, line: '' };

  function _isLive() { return window.DeskV1Store.live(); }

  // What the feed is NOT reading, per project in scope (the server's own
  // coverage line, never silenced).
  function _coverageHTML(st) {
    if (!_isLive()) return '';
    const by = _fx().engagementCoverage || {};
    const ids = st.project === 'all' ? Object.keys(by) : [st.project];
    const lines = [];
    ids.forEach((id) => (by[id] || []).forEach((g) => {
      const p = _project(id);
      lines.push(`<div class="desk-v1-eng-gap">${esc(st.project === 'all' && p ? p.name + ': ' : '')}${esc(g.label)}${g.detail && g.detail !== g.label ? ` <span class="desk-v1-eng-gap-detail">${esc(g.detail)}</span>` : ''}</div>`);
    }));
    return lines.length ? `<div class="desk-v1-eng-gaps">${lines.join('')}</div>` : '';
  }

  function _checkNowHTML(st) {
    if (!_isLive()) return '';
    const one = st.project !== 'all';
    return `<div class="desk-v1-eng-check">
      <button type="button" data-eng-check ${one && !_poll.busy ? '' : 'disabled'}
        title="${esc(one ? 'Reads new replies and mentions now. A platform API read is paid and counted against the budget; the browser pane is free.' : 'Pick one project to check it')}">${_poll.busy ? 'Checking…' : 'Check now'}</button>
      <span class="desk-v1-eng-check-line" data-eng-check-line aria-live="polite">${esc(_poll.line)}</span>
    </div>`;
  }

  function _pollLine(rep) {
    const parts = Object.entries((rep && rep.platforms) || {}).map(([plat, e]) => {
      if (e.error || e.state !== 'ok') return `${plat}: ${e.message || e.error || 'not read'}`;
      const spent = e.spent ? `, spent $${Number(e.spent).toFixed(3)}` : '';
      return `${plat}: ${e.new_items} new${spent}`;
    });
    return parts.length ? parts.join(' · ') : 'No account is connected to read.';
  }

  function _render() {
    const el = _mountEl;
    if (!el) return;
    const st = _state;
    const counts = _laneCounts(st);
    const lanesHTML = LANES.map((l) => `
      <button type="button" class="desk-v1-eng-lane${st.lane === l.key ? ' is-active' : ''}" data-eng-lane="${esc(l.key)}">
        <span aria-hidden="true">${esc(l.glyph)}</span> ${esc(l.label)}${counts[l.key] ? ` · ${counts[l.key]}` : ''}
      </button>`).join('');
    const activeLane = LANES.find((l) => l.key === st.lane) || LANES[0];
    const rows = _filtered(st).filter(activeLane.match);
    const rowsHTML = rows.length
      ? rows.map(_rowHTML).join('')
      : `<div class="desk-v1-eng-empty">Nothing in ${esc(activeLane.label.toLowerCase())} right now.</div>`;
    el.innerHTML = `
      <div class="desk-v1-engagement">
        <div class="desk-v1-eng-lanes" role="tablist">${lanesHTML}</div>
        ${_filterSelectsHTML(st)}
        ${_repliesBannerHTML(st)}
        ${_checkNowHTML(st)}
        <div class="desk-v1-eng-rows">${rowsHTML}</div>
        ${_coverageHTML(st)}
      </div>`;
    _bind(el);
  }

  function _bind(el) {
    const st = _state;
    const checkBtn = el.querySelector('[data-eng-check]');
    if (checkBtn && !checkBtn.disabled) checkBtn.onclick = async () => {
      const S = window.DeskV1Store;
      _poll = { busy: true, line: '' };
      _render();
      try {
        const rep = await S.api('POST', '/api/desk/engagement/poll', { project_id: st.project });
        _poll = { busy: false, line: _pollLine(rep) };
        await window.deskV1LoadEngagement();
      } catch (e) {
        _poll = { busy: false, line: `Could not check: ${e && e.message ? e.message : e}` };
      }
      if (el.isConnected && _mountEl === el) _render();
    };
    el.querySelectorAll('[data-eng-lane]').forEach((b) => b.onclick = () => { st.lane = b.dataset.engLane; _render(); });
    el.querySelectorAll('[data-eng-filter]').forEach((sel) => sel.onchange = () => {
      st[sel.dataset.engFilter] = sel.value;
      if (sel.dataset.engFilter === 'project') st.campaign = 'all';
      _render();
    });
    // A row deep-links to the conversation's real home — same shape Home's
    // own Needs-you "reply" row already uses (desk-v1-home.js) — rather than
    // growing a second thread/actions panel on this dashboard. A "No
    // campaign" row (K4) has nowhere to deep-link to yet, so it stays inert.
    el.querySelectorAll('[data-eng-conv]').forEach((row) => row.onclick = () => {
      const campaignId = row.dataset.engCampaign;
      const conversationId = row.dataset.engConv;
      if (campaignId && typeof window.deskV1Nav === 'function') {
        window.deskV1Nav('conversations', { campaignId, conversationId });
      }
    });
  }

  function deskV1RenderEngagement(el, params) {
    params = params || {};
    if (params.campaignId && typeof window.deskV1RenderConversations === 'function') {
      window.deskV1RenderConversations(el, params);
      return;
    }
    _mountEl = el;
    _mountParams = params;
    _ensureState(params);
    if (typeof window.deskV1EngagementGate === 'function'
        && window.deskV1EngagementGate(el, () => deskV1RenderEngagement(el, params))) return;
    _render();
  }

  window.deskV1RenderEngagement = deskV1RenderEngagement;
  window.deskV1EngagementCount = deskV1EngagementCount;
})();
