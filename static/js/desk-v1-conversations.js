// Desk v1 (MC-977) — T6: Conversations (frame 12c, docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §6). Window-bridged module, no `import` (ground rule 1).
//
// desk_v1_live OFF (DEMO): Send/Ignore/Assign/Take over all mutate the in-memory
// DeskV1Fixtures conversation objects through DeskV1Store.write (apply + Undo,
// NO route call). **Send is simulated — no platform write.**
//
// desk_v1_live ON (R1-W S8, docs/desk_v1/R1W_WIRING_PLAN.md §2.F): the rows are
// the Desk's engagement feed (`GET /api/desk/engagement`, one row per reply or
// mention the reader found), the thread is `GET /api/desk/engagement/<id>`, and
// the writes are real: PATCH (ignore / assign / take over / save a draft),
// POST suggest-reply (the project's agent drafts, nothing is sent), and POST
// reply, which posts for real and so goes through the dashboard passcode prompt.
// A row's feed state is what the lane shows; an empty feed says "not connected"
// or "not read yet" through the coverage line, never "no one is talking".
//
// Reached via `deskV1Nav('conversations', {campaignId, conversationId?})` —
// Home's "1 reply waiting" row already calls this (desk-v1-home.js), and
// this file honours both params on mount. Renders straight into the shell's
// route body (same "full page, not a campaign-tab-body fill" contract T4's
// calendar established) — T2a's tab strip can later call
// `deskV1RenderConversations(el, params)` into its own Content-tab slot with
// no change needed here, identical to the calendar precedent.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution (same _fx() convention as every other desk-v1-*.js) ──
  function _fx() { return window.DeskV1Store.state(); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  // R2-5: whoever the project actually picked (or the campaign's own
  // how.agent) — fallback stays 'your agent', never the old hardcoded name.
  function _agentName(campaign) {
    return window.DeskV1Kit ? DeskV1Kit.deskAgentName({ project: _project(campaign && campaign.projectId), campaign }) : 'your agent';
  }
  function _allConversations() { return _fx().conversations || []; }
  function _detail(id) { return (_fx().conversationDetail || {})[id] || {}; }
  function _coverageGaps(campaignId) { return (_fx().conversationCoverageGaps || {})[campaignId] || []; }
  function _isLive() { return window.DeskV1Store.live(); }

  // ── Live data (R1-W S8) ─────────────────────────────────────────────────
  // The server's feed rows, mapped into the SAME shapes the fixtures hold
  // (`conversations`, `conversationDetail`, `conversationCoverageGaps`) so every
  // renderer below reads one structure in both modes. Nothing here runs with
  // desk_v1_live OFF, and a failed load is shown, never papered over with
  // demo rows (DeskV1Store's rule).
  //   phase  idle | loading | ready | error. `ready` stays while a refresh runs.
  //   raw    id -> the server's row (plus `parent_post` once its thread was read).
  const _eng = { phase: 'idle', error: null, raw: {}, inflight: null, at: 0, threadAt: {} };
  const STALE_HOLD = 'This thread has new replies since the draft below was written.';

  function _ago(iso) {
    const t = Date.parse(iso);
    if (!isFinite(t)) return '';
    const m = Math.max(0, Math.round((Date.now() - t) / 60000));
    if (m < 60) return m + 'm';
    return m < 1440 ? Math.round(m / 60) + 'h' : Math.round(m / 1440) + 'd';
  }
  function _channelIdFor(row) {
    const ch = (_fx().channels || []).find((c) => c.platform === row.platform && c.identity && c.identity === row.account);
    return ch ? ch.id : null;
  }
  function _toConversation(row) {
    return {
      id: row.id, projectId: row.project_id, campaignId: row.campaign_id || null, source: row.source,
      channelId: _channelIdFor(row), excerpt: row.excerpt || '', state: row.state,
      takenOver: !!row.taken_over, assignedTo: row.assigned_to || null,
    };
  }
  function _toDetail(row) {
    const parent = row.parent_post;
    const identity = row.account || 'your account';
    let parentPost; let comments = [];
    if (parent) {
      parentPost = { label: 'Your post', platform: parent.platform || row.platform, identity: parent.account || identity,
        ageLabel: _ago(parent.published_at), text: parent.body || '', link: parent.url || '' };
      comments = [{ author: row.author || 'Someone', platform: row.platform, ageLabel: _ago(row.created_at), text: row.excerpt || '' }];
    } else {
      parentPost = { label: row.source === 'discussions' ? 'Discussion' : (row.source === 'mentions' ? 'Mentioned by' : 'Reply on our post'),
        platform: row.platform, identity: row.author || '', ageLabel: _ago(row.created_at), text: row.excerpt || '', link: row.url || '' };
    }
    const shown = row.draft || row.reply;
    const thread = { parentPost, comments, reply: shown ? { identity, platform: row.platform, text: shown.text } : null };
    if (row.state === 'stale') thread.hold = STALE_HOLD;
    const d = { author: row.author || 'Someone', platform: row.platform, ageLabel: _ago(row.created_at), thread };
    if (row.state === 'needs_reply') { d.reasonKind = 'question'; d.reasonDetail = 'reply drafted'; }
    else if (row.state === 'needs_you') { d.reasonKind = 'needs_you'; d.reasonDetail = 'no reply drafted yet'; }
    else d.reasonKind = row.state;
    return d;
  }
  // One row in, everywhere it lives: the raw copy, the conversation object (kept
  // as the SAME object so a held reference stays current) and its detail.
  function _adoptRow(row) {
    const prev = _eng.raw[row.id];
    if (prev && prev.parent_post !== undefined && row.parent_post === undefined) row.parent_post = prev.parent_post;
    _eng.raw[row.id] = row;
    const fx = _fx();
    const conv = _toConversation(row);
    const list = fx.conversations;
    const i = list.findIndex((c) => c.id === row.id);
    if (i >= 0) Object.assign(list[i], conv); else list.push(conv);
    fx.conversationDetail[row.id] = _toDetail(row);
    return list[i >= 0 ? i : list.length - 1];
  }
  function _gapsFrom(coverage) {
    return (coverage || []).filter((c) => c.state !== 'ok')
      .map((c) => ({ label: c.message || `${c.label || c.platform}: not read`, detail: c.reason || c.message || '', vault_locked: c.vault_locked === true }));
  }

  async function _fetchEngagement() {
    const S = window.DeskV1Store;
    const rows = await S.api('GET', '/api/desk/engagement?limit=1000');
    const projects = _fx().projects || [];
    const covs = await Promise.all(projects.map((p) => S.api('GET', '/api/desk/engagement/coverage/' + encodeURIComponent(p.id))
      .then((r) => [p.id, _gapsFrom(r && r.coverage)])
      .catch((e) => [p.id, [{ label: `Could not check what is read for ${p.name || p.id}`, detail: e && e.message ? e.message : String(e) }]])));
    const gapsByProject = Object.fromEntries(covs);
    const fx = _fx();
    const prevRaw = _eng.raw;
    _eng.raw = {};
    fx.conversations = [];
    fx.conversationDetail = {};
    (Array.isArray(rows) ? rows : []).forEach((row) => {
      if (prevRaw[row.id] && prevRaw[row.id].parent_post !== undefined) row.parent_post = prevRaw[row.id].parent_post;
      _adoptRow(row);
    });
    fx.conversationCoverageGaps = {};
    fx.engagementCoverage = gapsByProject;   // project id -> what is not being read, for the dashboard
    (fx.campaigns || []).forEach((c) => { fx.conversationCoverageGaps[c.id] = gapsByProject[c.projectId] || []; });
  }

  // Reads the feed (and each project's coverage line). Resolves { phase, error }
  // once it has landed or failed; never rejects. A second call while one is out
  // shares it. `force` is not needed: a caller that wants fresh data (after a
  // poll) just calls again once the first has resolved.
  function deskV1LoadEngagement() {
    if (!_isLive()) return Promise.resolve({ phase: 'ready', error: null });
    if (_eng.inflight) return _eng.inflight;
    if (_eng.phase !== 'ready') { _eng.phase = 'loading'; _eng.error = null; }
    const p = _fetchEngagement().then(() => {
      _eng.phase = 'ready'; _eng.error = null; _eng.at = Date.now();
    }).catch((e) => {
      _eng.phase = 'error'; _eng.error = e && e.message ? e.message : String(e);
    }).then(() => { _eng.inflight = null; return { phase: _eng.phase, error: _eng.error }; });
    _eng.inflight = p;
    return p;
  }

  // The gate a live mount goes through: paints "loading" or the error (with Try
  // again) INSTEAD of the surface and returns true; false once data is there.
  // A mount while data is already `ready` renders it at once and refreshes in the
  // background when it is older than 15s, repainting through `repaint`.
  function deskV1EngagementGate(el, repaint) {
    if (!_isLive()) return false;
    // An error is not stale: it waits for "Try again", or a failing feed would be re-read in a loop.
    const stale = _eng.phase === 'idle' || (_eng.phase === 'ready' && Date.now() - _eng.at > 15000);
    if (stale && !_eng.inflight) {
      deskV1LoadEngagement().then(() => { if (el.isConnected) repaint(); });
    } else if (_eng.inflight && _eng.phase !== 'ready') {
      _eng.inflight.then(() => { if (el.isConnected) repaint(); });
    }
    if (_eng.phase === 'ready') return false;
    if (_eng.phase === 'error') {
      el.innerHTML = `<div class="desk-v1-stub" data-eng-error><div class="desk-v1-stub-body">Could not load conversations: ${esc(_eng.error)}
        <button type="button" data-eng-retry>Try again</button></div></div>`;
      el.querySelector('[data-eng-retry]').onclick = () => { deskV1LoadEngagement().then(() => { if (el.isConnected) repaint(); }); repaint(); };
    } else {
      el.innerHTML = '<div class="desk-v1-stub" data-eng-loading><div class="desk-v1-stub-body">Loading conversations…</div></div>';
    }
    return true;
  }

  // The thread: one row read when it is opened (it names the post it answers).
  function _loadThread(id, repaint) {
    if (!_isLive() || Date.now() - (_eng.threadAt[id] || 0) < 20000) return;
    _eng.threadAt[id] = Date.now();
    window.DeskV1Store.api('GET', '/api/desk/engagement/' + encodeURIComponent(id)).then((row) => {
      _adoptRow(row);
      repaint();
    }).catch((e) => {
      delete _eng.threadAt[id];
      window.DeskV1Kit.toast(`Could not open that thread: ${e && e.message ? e.message : e}`);
    });
  }

  // ── §6 source switch + reason-line vocabulary ───────────────────────────
  // Kept LOCAL to this file, not promoted into desk-v1-kit.js: §9's own
  // vocabulary table (T0b) covers version/campaign states only — a
  // conversation's reason line is a different, smaller vocabulary this
  // surface alone renders. `desk-v1-kit.js` is T0b's hot file (plan's
  // "Hot files" table: "a later need goes through a small kit PR, not an
  // edit inside a surface branch") — same reasoning T4's calendar.js already
  // used to keep its own tz helpers local rather than editing the kit.
  const SOURCES = [
    { key: 'our_posts', label: 'On our posts' },
    { key: 'mentions', label: 'Mentions' },
    { key: 'discussions', label: 'Discussions' },
  ];
  const REASON_KINDS = {
    question:  { glyph: '?', word: 'Question', cls: 'q' },
    needs_you: { glyph: '⚑', word: 'Needs you', cls: 'nu' },     // ⚑
    no_reply:  { glyph: '', word: 'No reply suggested', cls: 'nr' },
    reviewed:  { glyph: '✓', word: 'Reviewed', cls: 'rv' },       // ✓
    stale:     { glyph: '⚠', word: 'Needs a fresh look', cls: 'st' }, // ⚠
    ignored:   { glyph: '·', word: 'Ignored', cls: 'ig' },
    sent:      { glyph: '✓', word: 'Sent', cls: 'sn' },
  };
  // Badge counts (§6's "n" beside each source) only tally conversations a
  // person still needs to act on — a drafted reply waiting to send, or an
  // escalation. 'reviewed'/'stale'/'no_reply'/'ignored'/'sent' still LIST,
  // they just don't inflate the count (verified against the frame's own
  // numbers — see the fixtures file's T6 comment for the row-by-row count).
  const COUNTABLE_REASON_KINDS = new Set(['question', 'needs_you']);

  function _reasonKind(conv) {
    const d = _detail(conv.id);
    return d.reasonKind || conv.state || 'no_reply';
  }
  function _reasonLineHTML(conv) {
    const d = _detail(conv.id);
    const kind = _reasonKind(conv);
    const r = REASON_KINDS[kind] || REASON_KINDS.no_reply;
    const detail = d.reasonDetail ? ` · ${esc(d.reasonDetail)}` : '';
    const glyphHTML = r.glyph ? `<span aria-hidden="true">${esc(r.glyph)}</span> ` : '';
    return `<span class="desk-v1-conv-reason desk-v1-conv-reason-${esc(r.cls)}">${glyphHTML}${esc(r.word)}${detail}</span>`;
  }

  const PLATFORM_GLYPH = { x: '𝕏', linkedin: 'in', blog: '≡', web: '◉' };
  function _platformGlyph(p) { return PLATFORM_GLYPH[p] || '◉'; }

  // ── module state — one conversations view mounted at a time (route-driven
  // like T4's calendar); preserved across a re-render of the SAME campaign,
  // reset when navigating to a different one. ─────────────────────────────
  let _state = null;
  let _mountEl = null;

  function _ensureState(campaignId, params) {
    if (!_state || _state.campaignId !== campaignId) {
      _state = { campaignId, scope: 'campaign', source: 'our_posts', selectedId: null };
    }
    if (params && params.conversationId) {
      const conv = _allConversations().find((c) => c.id === params.conversationId);
      if (conv) { _state.selectedId = conv.id; _state.source = conv.source; }
    }
    return _state;
  }

  function deskV1RenderConversations(el, params) {
    const campaignId = (params || {}).campaignId;
    _mountEl = el;
    if (deskV1EngagementGate(el, () => deskV1RenderConversations(el, params))) return;
    const campaign = _campaign(campaignId);
    if (!campaign) {
      el.innerHTML = '<div class="desk-v1-stub"><div class="desk-v1-stub-body">No campaign selected.</div></div>';
      return;
    }
    _ensureState(campaignId, params);
    _render(campaign);
  }

  // ── scope (Workspace ▾, §6: "a Workspace ▾ all-campaigns scope") ────────
  function _inScope(st, conv) {
    return st.scope === 'all' ? true : conv.campaignId === st.campaignId;
  }
  function _bySource(st, source) {
    return _allConversations().filter((c) => _inScope(st, c) && c.source === source);
  }
  function _sourceCounts(st) {
    const counts = {};
    SOURCES.forEach((s) => {
      counts[s.key] = _bySource(st, s.key).filter((c) => COUNTABLE_REASON_KINDS.has(_reasonKind(c))).length;
    });
    return counts;
  }

  // §6: "An empty list must never read as 'no one is talking.'" — an honest,
  // specific line instead, per source.
  const EMPTY_COPY = {
    our_posts: 'Nothing waiting on your own posts right now.',
    mentions: 'No new mentions since the last check.',
    discussions: 'No discussions tracked for this campaign yet.',
  };

  function _rowHTML(conv, st) {
    const d = _detail(conv.id);
    const channel = conv.channelId ? _channel(conv.channelId) : null;
    const platform = d.platform || (channel && channel.platform) || 'web';
    const author = d.author || (channel && channel.identity) || 'Someone';
    const age = d.ageLabel ? `<span class="desk-v1-conv-age">${esc(_platformGlyph(platform))} · ${esc(d.ageLabel)}</span>` : '';
    const campLabel = st.scope === 'all' ? `<div class="desk-v1-conv-row-camp">${esc((_campaign(conv.campaignId) || {}).name || '')}</div>` : '';
    return `
      <button type="button" class="desk-v1-conv-row${conv.id === st.selectedId ? ' is-selected' : ''}"
          data-conv-id="${esc(conv.id)}" data-conv-campaign="${esc(conv.campaignId)}">
        ${campLabel}
        <div class="desk-v1-conv-row-head"><span class="desk-v1-conv-author">${esc(author)}</span> ${age}</div>
        <div class="desk-v1-conv-snippet">${esc(conv.excerpt || '')}</div>
        ${_reasonLineHTML(conv)}
      </button>`;
  }

  // §6.1 empty state, before the first publish: "Replies show up here once a
  // post is live." — overrides the per-source EMPTY_COPY only when the whole
  // campaign (every source) has nothing yet AND has never published, so a
  // campaign that's merely quiet on one source still gets its normal,
  // source-specific empty line (§6: "must never read as 'no one is
  // talking'").
  function _freshEmpty(st, counts) {
    if (st.scope !== 'campaign') return false;
    if (typeof window.deskV1CampaignHasPublished !== 'function' || window.deskV1CampaignHasPublished(st.campaignId)) return false;
    return SOURCES.every((s) => !counts[s.key]) && _allConversations().every((c) => c.campaignId !== st.campaignId);
  }

  function _listHTML(st) {
    const counts = _sourceCounts(st);
    const tabsHTML = SOURCES.map((s) => `
      <button type="button" class="desk-v1-conv-source${st.source === s.key ? ' is-active' : ''}"
          data-conv-source="${esc(s.key)}">${esc(s.label)}${counts[s.key] ? ` · ${counts[s.key]}` : ''}</button>`).join('');
    const rows = _bySource(st, st.source);
    const rowsHTML = rows.length
      ? rows.map((c) => _rowHTML(c, st)).join('')
      : `<div class="desk-v1-conv-empty">${esc(_freshEmpty(st, counts) ? 'Replies show up here once a post is live.' : (EMPTY_COPY[st.source] || 'Nothing here right now.'))}</div>`;
    const gaps = _coverageGaps(st.campaignId);
    const gapsHTML = gaps.length
      ? `<div class="desk-v1-conv-gaps">${gaps.map((g, i) => `
          <span class="desk-v1-conv-gap">${esc(g.label)} ${window.DeskV1Kit ? window.DeskV1Kit.infoIconHTML('conv-gap-' + i) : ''}${window.VaultUnlockUI ? window.VaultUnlockUI.buttonHTML(g.vault_locked ? g : g.detail) : ''}</span>`).join('')}</div>`
      : '';
    return `
      <div class="desk-v1-conv-list-pane">
        <div class="desk-v1-conv-toolbar">
          <select class="desk-v1-conv-scope" data-conv-scope>
            <option value="campaign"${st.scope === 'campaign' ? ' selected' : ''}>This campaign</option>
            <option value="all"${st.scope === 'all' ? ' selected' : ''}>All campaigns</option>
          </select>
        </div>
        <div class="desk-v1-conv-sources" role="tablist">${tabsHTML}</div>
        <div class="desk-v1-conv-rows">${rowsHTML}</div>
        ${gapsHTML}
      </div>`;
  }

  function _postBlockHTML(post, kind) {
    if (!post) return '';
    return `
      <div class="desk-v1-conv-post desk-v1-conv-post-${esc(kind)}">
        <div class="desk-v1-conv-post-head">
          <span class="desk-v1-conv-post-label">${esc(post.label || '')}</span>
          <span class="desk-v1-conv-post-identity">${esc(_platformGlyph(post.platform))} ${esc(post.identity || '')}</span>
          <span class="desk-v1-conv-post-age">${esc(post.ageLabel || '')}</span>
          ${post.link ? `<a class="desk-v1-conv-post-link" href="${esc(post.link)}" target="_blank" rel="noopener">Open on platform ↗</a>` : ''}
        </div>
        <div class="desk-v1-conv-post-text">${esc(post.text || '')}</div>
      </div>`;
  }

  function _threadHTML(conv, st) {
    if (!conv) {
      return `<div class="desk-v1-conv-thread desk-v1-conv-thread-empty"><div class="desk-v1-stub-body">Select a conversation to view it.</div></div>`;
    }
    const d = _detail(conv.id);
    const thread = d.thread || {};
    const reply = thread.reply;
    const platformLabel = reply ? `${_platformGlyph(reply.platform)}`.trim() : '';
    const holdHTML = thread.hold
      ? `<div class="desk-v1-conv-hold">${esc(thread.hold)}</div>`
      : '';
    const commentsHTML = (thread.comments || []).map((c) => `
      <div class="desk-v1-conv-comment">
        <span class="desk-v1-conv-comment-avatar" aria-hidden="true">${esc((c.author || '?').replace('@', '').slice(0, 1).toUpperCase())}</span>
        <div class="desk-v1-conv-comment-body">${esc(c.text || '')}</div>
      </div>`).join('');
    const replyHTML = reply
      ? `
        <div class="desk-v1-conv-reply${thread.hold ? ' desk-v1-conv-reply-held' : ''}" data-conv-reply>
          <div class="desk-v1-conv-reply-head">Replying as ${esc(reply.identity)} on ${esc(platformLabel)}</div>
          <div class="desk-v1-conv-reply-text" data-conv-reply-text tabindex="0" role="button">${esc(reply.text)}</div>
        </div>`
      : `<div class="desk-v1-conv-noreply">No reply drafted for this one.${_isLive() && !conv.takenOver && conv.state !== 'sent'
          ? ' <button type="button" data-conv-write>Write one</button>' : ''}</div>`;
    const takenOver = !!conv.takenOver;
    const alreadySent = conv.state === 'sent';
    // Live, a reply only goes out where the publisher can post it (X); every
    // other platform says so and leaves the reply to the person on the platform.
    const noSendPath = _isLive() && (_eng.raw[conv.id] || {}).platform !== 'x';
    const canSend = !!reply && !thread.hold && !takenOver && conv.state !== 'ignored' && !alreadySent && !noSendPath;
    const sendReason = !reply ? 'No reply drafted' : (thread.hold ? thread.hold : (takenOver ? 'Taken over — resume to send again' : (alreadySent ? 'Already sent' : (noSendPath ? 'Replies can only be sent to X from here: open the post on the platform and reply there' : ''))));
    const agentName = _agentName(_campaign(conv.campaignId));
    const reviseLabel = (_isLive() && !reply) ? `Ask ${esc(agentName)} to draft` : `Ask ${esc(agentName)} to revise`;
    const reviseOn = _isLive() ? (!takenOver && !alreadySent) : (reply && !takenOver);
    const noteHTML = (_isLive() && reply && reviseOn)
      ? '<input type="text" class="desk-v1-conv-note" data-conv-note maxlength="1000" placeholder="What to change (optional)" aria-label="What to change">' : '';

    return `
      <div class="desk-v1-conv-thread">
        <button type="button" class="desk-v1-conv-backrow" data-conv-back>&lsaquo; Back to list</button>
        ${takenOver ? `<div class="desk-v1-conv-takeover-banner">You’ve taken over — automated replies are paused for this thread.
            <button type="button" data-conv-resume>Resume</button></div>` : ''}
        ${_postBlockHTML(thread.parentPost, 'parent')}
        ${commentsHTML}
        ${holdHTML}
        ${replyHTML}
        <div class="desk-v1-conv-actions">
          <button type="button" class="desk-v1-conv-send" data-conv-send ${canSend ? '' : `disabled title="${esc(sendReason)}"`}>Send</button>
          ${noteHTML}
          <button type="button" class="desk-v1-conv-revise" data-conv-revise ${reviseOn ? '' : 'disabled'}>${reviseLabel}</button>
          <button type="button" data-conv-ignore ${takenOver ? 'disabled' : ''}>Ignore</button>
          <button type="button" data-conv-assign ${takenOver ? 'disabled' : ''}>Assign ▾</button>
          <button type="button" class="desk-v1-conv-takeover" data-conv-takeover>${takenOver ? '✅ Resume' : '✋ Take over'}</button>
        </div>
      </div>`;
  }

  function _render(campaign) {
    const el = _mountEl;
    if (!el) return;
    const st = _state;
    const selected = st.selectedId ? _allConversations().find((c) => c.id === st.selectedId) : null;
    // "desk-v1-conversations" (bare, no CSS rule of its own) is the same
    // pre-existing contract desk-v1-video.js documents at its own root:
    // desk-v1-home.mjs's A12 deep-link check (written when this route was
    // still a stub) waits on ".desk-v1-stub, .desk-v1-conversations".
    el.innerHTML = `
      <div class="desk-v1-conv-layout desk-v1-conversations"${selected ? ' data-has-selection="1"' : ''}>
        ${_listHTML(st)}
        ${_threadHTML(selected, st)}
      </div>`;
    _bind(el, campaign);
    if (selected) _loadThread(selected.id, () => { if (el.isConnected && _mountEl === el && _state === st && st.selectedId === selected.id) _render(campaign); });
    if (window.DeskV1Kit) {
      const gaps = _coverageGaps(st.campaignId);
      const texts = {};
      gaps.forEach((g, i) => { texts['conv-gap-' + i] = g.detail || ''; });
      window.DeskV1Kit.bindInfoIcons(el, texts);
    }
  }

  function _bind(el, campaign) {
    const st = _state;

    const scopeSel = el.querySelector('[data-conv-scope]');
    if (scopeSel) scopeSel.onchange = () => { st.scope = scopeSel.value; _render(campaign); };

    el.querySelectorAll('[data-conv-source]').forEach((b) => b.onclick = () => {
      st.source = b.dataset.convSource; st.selectedId = null; _render(campaign);
    });

    el.querySelectorAll('[data-conv-id]').forEach((row) => row.onclick = () => {
      const id = row.dataset.convId;
      st.selectedId = id;
      const conv = _allConversations().find((c) => c.id === id);
      if (conv) {
        if (typeof window.deskV1PatchParams === 'function') window.deskV1PatchParams({ conversationId: id, campaignId: conv.campaignId });
      }
      _render(campaign);
    });

    const backBtn = el.querySelector('[data-conv-back]');
    if (backBtn) backBtn.onclick = () => { st.selectedId = null; _render(campaign); };

    const conv = st.selectedId ? _allConversations().find((c) => c.id === st.selectedId) : null;
    if (!conv) return;
    const d = _detail(conv.id);
    const thread = d.thread || {};
    if (_isLive()) { _bindLive(el, campaign, conv, d, thread); return; }

    // Click the proposed reply to edit it in place (§6: "Click the reply to
    // edit it") — a plain textarea swap, no autosave infra (T3's own scope).
    const replyTextEl = el.querySelector('[data-conv-reply-text]');
    if (replyTextEl && thread.reply) {
      const openEditor = () => {
        const ta = document.createElement('textarea');
        ta.className = 'desk-v1-conv-reply-editor';
        ta.value = thread.reply.text;
        replyTextEl.replaceWith(ta);
        ta.focus();
        const commit = () => {
          thread.reply.text = ta.value;
          _render(campaign);
        };
        ta.addEventListener('blur', commit);
        ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ta.blur(); } });
      };
      replyTextEl.onclick = openEditor;
      replyTextEl.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openEditor(); } };
    }

    const sendBtn = el.querySelector('[data-conv-send]');
    if (sendBtn && !sendBtn.disabled) sendBtn.onclick = () => {
      const priorState = conv.state;
      window.DeskV1Kit.commandBus.run({
        label: `Sent your reply to ${d.author || 'them'}`,
        do: () => { conv.state = 'sent'; _render(campaign); },
        undo: () => { conv.state = priorState; _render(campaign); },
      });
    };

    const reviseBtn = el.querySelector('[data-conv-revise]');
    if (reviseBtn && !reviseBtn.disabled) reviseBtn.onclick = () => {
      if (!thread.reply) return;
      const priorText = thread.reply.text;
      const agentName = _agentName(campaign || _campaign(conv.campaignId));
      window.DeskV1Kit.commandBus.run({
        label: `Asked ${agentName} to revise the reply`,
        do: () => { thread.reply.text = priorText + ` (revised for tone by ${agentName})`; _render(campaign); },
        undo: () => { thread.reply.text = priorText; _render(campaign); },
      });
    };

    const ignoreBtn = el.querySelector('[data-conv-ignore]');
    if (ignoreBtn && !ignoreBtn.disabled) ignoreBtn.onclick = () => {
      const priorState = conv.state;
      window.DeskV1Kit.commandBus.run({
        label: `Ignored the conversation with ${d.author || 'them'}`,
        do: () => { conv.state = 'ignored'; _render(campaign); },
        undo: () => { conv.state = priorState; _render(campaign); },
      });
    };

    const assignBtn = el.querySelector('[data-conv-assign]');
    if (assignBtn && !assignBtn.disabled) {
      window.DeskV1Kit.bindAddToTrigger(assignBtn, () => ([
        { id: 'ron', label: 'Ron' },
        { id: 'dave', label: 'Dave' },
        { id: 'merrin', label: 'Merrin' },
        { id: '__unassign__', label: 'Unassign' },
      ]), (pick) => {
        const priorAssignee = conv.assignedTo || null;
        const label = pick === '__unassign__' ? 'Unassigned' : `Assigned to ${pick}`;
        window.DeskV1Kit.commandBus.run({
          label,
          do: () => { conv.assignedTo = pick === '__unassign__' ? null : pick; _render(campaign); },
          undo: () => { conv.assignedTo = priorAssignee; _render(campaign); },
        });
      });
      // addToMenu's own "+ New campaign" append doesn't apply to an assignee
      // picker — opt out via noAppendNew (opts already supported by the
      // kit's bindAddToTrigger → addToMenu path since T5).
    }

    // §6 Take over: "revoke automated sending in that thread immediately,
    // cancel queued replies, and flag any in-flight reply. Resume requires
    // an explicit action." The toast's Undo covers the immediate reversal;
    // the persistent Resume button (rendered in the banner above, and here
    // as the same action-row button relabelled) is the EXPLICIT resume the
    // spec asks for once that toast has expired.
    const takeoverBtn = el.querySelector('[data-conv-takeover]');
    if (takeoverBtn) takeoverBtn.onclick = () => {
      const was = !!conv.takenOver;
      window.DeskV1Kit.commandBus.run({
        label: was ? 'Resumed automated replies' : 'Took over — automated replies paused',
        do: () => { conv.takenOver = !was; _render(campaign); },
        undo: () => { conv.takenOver = was; _render(campaign); },
      });
    };
    const resumeBtn = el.querySelector('[data-conv-resume]');
    if (resumeBtn) resumeBtn.onclick = () => {
      window.DeskV1Kit.commandBus.run({
        label: 'Resumed automated replies',
        do: () => { conv.takenOver = false; _render(campaign); },
        undo: () => { conv.takenOver = true; _render(campaign); },
      });
    };
  }

  // ── Live writes (R1-W S8) ────────────────────────────────────────────────
  // Every write goes through DeskV1Store.run: the route's answer (the row) is
  // adopted, a refusal puts the draw back and toasts the server's reason, and
  // Undo sends the inverse PATCH. Nothing is shown as changed before the server
  // has said so (apply is a no-op): a reply or an ignore is not worth guessing.
  const _SETTABLE = new Set(['needs_you', 'needs_reply', 'reviewed', 'no_reply', 'ignored']);
  const _SEND_PROOF = { title: 'Send reply', description: 'Re-enter your dashboard passcode to post this reply. It goes out under your account on the platform and cannot be unsent from here.' };
  const _assignees = {};   // project id -> [{id,label}], from the agents hired on it

  function _snap(id) { return _eng.raw[id] ? JSON.parse(JSON.stringify(_eng.raw[id])) : null; }
  function _rowPath(id) { return '/api/desk/engagement/' + encodeURIComponent(id); }

  // `inverse` is the PATCH body that reverses it, or null when the change has no
  // inverse the route accepts (then it is confirmed with a plain toast, no Undo).
  function _livePatch(conv, campaign, label, body, inverse) {
    const S = window.DeskV1Store;
    const before = _snap(conv.id);
    const repaint = () => { if (_mountEl && _mountEl.isConnected && _state) _render(campaign); };
    const adopt = (row) => { _adoptRow(row); repaint(); return row; };
    return S.run({
      label,
      apply: () => {},
      unapply: () => { if (before) _adoptRow(JSON.parse(JSON.stringify(before))); },
      repaint,
      request: () => S.api('PATCH', _rowPath(conv.id), body).then(adopt),
      undoRequest: inverse ? () => S.api('PATCH', _rowPath(conv.id), inverse).then(adopt) : undefined,
      irreversible: inverse ? undefined : true,
    });
  }

  function _openEditor(host, initial, onCommit) {
    const ta = document.createElement('textarea');
    ta.className = 'desk-v1-conv-reply-editor';
    ta.value = initial;
    host.replaceWith(ta);
    ta.focus();
    let done = false;
    const commit = () => { if (done) return; done = true; onCommit(ta.value); };
    ta.addEventListener('blur', commit);
    ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ta.blur(); } });
  }

  function _saveDraft(conv, campaign, text) {
    const prior = ((_eng.raw[conv.id] || {}).draft || {}).text || '';
    const next = (text || '').trim();
    if (next === prior.trim()) { _render(campaign); return; }
    _livePatch(conv, campaign, next ? 'Saved your edit to the reply' : 'Cleared the reply',
      { draft: { text: next } }, { draft: prior ? { text: prior } : null });
  }

  // The agent saves its draft with a PATCH of its own, a while after the ask:
  // read the row again every 5s (max 2 min) until the draft changes.
  function _awaitDraft(id, before, campaign) {
    let tries = 0;
    const tick = () => {
      if (++tries > 24 || !_mountEl || !_mountEl.isConnected) return;
      window.DeskV1Store.api('GET', _rowPath(id)).then((row) => {
        const was = before && before.at;
        const now = (row.draft || {}).at;
        if (now && now !== was) {
          _adoptRow(row);
          if (_state && _state.selectedId === id) _render(campaign);
          window.DeskV1Kit.toast('A new draft is ready.');
        } else setTimeout(tick, 5000);
      }).catch(() => setTimeout(tick, 5000));
    };
    setTimeout(tick, 5000);
  }

  function _assigneeChoices(conv, rerender) {
    const pid = conv.projectId;
    if (_assignees[pid]) return _assignees[pid];
    _assignees[pid] = [];
    window.DeskV1Kit.projectAgentChoices(_project(pid)).then((cs) => {
      _assignees[pid] = cs.map((c) => ({ id: c.name, label: c.name }));
      rerender();
    });
    return _assignees[pid];
  }

  function _bindLive(el, campaign, conv, d, thread) {
    const S = window.DeskV1Store;
    const Kit = window.DeskV1Kit;
    const raw = _eng.raw[conv.id] || {};
    const repaint = () => { if (_mountEl && _mountEl.isConnected) _render(campaign); };

    // Click the reply (or "Write one") to edit it: the text is saved as the
    // row's draft on blur / Enter.
    const replyTextEl = el.querySelector('[data-conv-reply-text]');
    if (replyTextEl && thread.reply && conv.state !== 'sent') {
      const open = () => _openEditor(replyTextEl, thread.reply.text, (t) => _saveDraft(conv, campaign, t));
      replyTextEl.onclick = open;
      replyTextEl.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(); } };
    }
    const writeBtn = el.querySelector('[data-conv-write]');
    if (writeBtn) writeBtn.onclick = () => _openEditor(writeBtn.parentElement, '', (t) => {
      if (!t.trim()) { repaint(); return; }
      _saveDraft(conv, campaign, t);
    });

    const sendBtn = el.querySelector('[data-conv-send]');
    if (sendBtn && !sendBtn.disabled) sendBtn.onclick = () => {
      const text = thread.reply.text;
      const before = _snap(conv.id);
      sendBtn.disabled = true;
      S.run({
        label: `Sent your reply to ${d.author || 'them'}`,
        apply: () => {},
        unapply: () => { if (before) _adoptRow(before); },
        repaint,
        irreversible: true,
        request: async () => {
          if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
          const res = await window.humanProofFetch(_rowPath(conv.id) + '/reply',
            { method: 'POST', body: JSON.stringify({ text }) }, _SEND_PROOF);
          if (res === null) throw new Error('the dashboard passcode was not entered, so nothing was sent');
          if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || `HTTP ${res.status}`);
          _adoptRow(res.body);
          repaint();
          return res.body;
        },
      });
    };

    const reviseBtn = el.querySelector('[data-conv-revise]');
    if (reviseBtn && !reviseBtn.disabled) reviseBtn.onclick = async () => {
      const noteEl = el.querySelector('[data-conv-note]');
      const note = noteEl ? noteEl.value.trim() : '';
      const agentName = _agentName(campaign || _campaign(conv.campaignId));
      reviseBtn.disabled = true;
      try {
        await S.api('POST', _rowPath(conv.id) + '/suggest-reply', note ? { note } : {});
        Kit.toast(`${agentName} is drafting. Nothing is sent: you read it and press Send.`);
        _awaitDraft(conv.id, raw.draft || null, campaign);
      } catch (e) {
        Kit.toast(`Could not ask ${agentName}: ${e && e.message ? e.message : e}`);
        reviseBtn.disabled = false;
      }
    };

    const ignoreBtn = el.querySelector('[data-conv-ignore]');
    if (ignoreBtn && !ignoreBtn.disabled) ignoreBtn.onclick = () => {
      const prior = raw.state;
      _livePatch(conv, campaign, `Ignored the conversation with ${d.author || 'them'}`,
        { state: 'ignored' }, _SETTABLE.has(prior) ? { state: prior } : null);
    };

    const assignBtn = el.querySelector('[data-conv-assign]');
    if (assignBtn && !assignBtn.disabled) {
      Kit.bindAddToTrigger(assignBtn,
        () => _assigneeChoices(conv, repaint).concat([{ id: '__unassign__', label: 'Unassign' }]),
        (pick) => {
          const prior = raw.assigned_to || null;
          const who = pick === '__unassign__' ? null : pick;
          _livePatch(conv, campaign, who ? `Assigned to ${who}` : 'Unassigned', { assigned_to: who }, { assigned_to: prior });
        });
    }

    const toggleTakeover = () => {
      const was = !!conv.takenOver;
      const priorDraft = (raw.draft || {}).text;
      const undoBody = was ? { taken_over: true }
        : Object.assign({ taken_over: false }, priorDraft ? { draft: { text: priorDraft } } : {});
      _livePatch(conv, campaign, was ? 'Resumed automated replies' : 'Took over — automated replies paused',
        { taken_over: !was }, undoBody);
    };
    const takeoverBtn = el.querySelector('[data-conv-takeover]');
    if (takeoverBtn) takeoverBtn.onclick = toggleTakeover;
    const resumeBtn = el.querySelector('[data-conv-resume]');
    if (resumeBtn) resumeBtn.onclick = toggleTakeover;
  }

  window.deskV1RenderConversations = deskV1RenderConversations;
  window.deskV1LoadEngagement = deskV1LoadEngagement;
  window.deskV1EngagementPhase = () => _eng.phase;
  window.deskV1EngagementGate = deskV1EngagementGate;
})();
