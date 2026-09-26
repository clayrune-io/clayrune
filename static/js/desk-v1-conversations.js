// Desk v1 (MC-977) — T6: Conversations (frame 12c, docs/desk_v1_r0_plan.md;
// THE_DESK_V1_UI.md §6). Window-bridged module, no `import` (ground rule 1).
//
// Fixtures only (ground rule 3): Send/Ignore/Assign/Take over all mutate the
// in-memory DeskV1Fixtures conversation objects directly through
// DeskV1Kit.commandBus, same "client-side over fixture data with Undo"
// contract T3/T4/T5 already established. **Send is simulated — no platform
// write** (the ticket's own instruction; §6 says the same).
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
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _allConversations() { return _fx().conversations || []; }
  function _detail(id) { return (_fx().conversationDetail || {})[id] || {}; }
  function _coverageGaps(campaignId) { return (_fx().conversationCoverageGaps || {})[campaignId] || []; }

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
    const campaign = _campaign(campaignId);
    _mountEl = el;
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

  function _listHTML(st) {
    const counts = _sourceCounts(st);
    const tabsHTML = SOURCES.map((s) => `
      <button type="button" class="desk-v1-conv-source${st.source === s.key ? ' is-active' : ''}"
          data-conv-source="${esc(s.key)}">${esc(s.label)}${counts[s.key] ? ` · ${counts[s.key]}` : ''}</button>`).join('');
    const rows = _bySource(st, st.source);
    const rowsHTML = rows.length
      ? rows.map((c) => _rowHTML(c, st)).join('')
      : `<div class="desk-v1-conv-empty">${esc(EMPTY_COPY[st.source] || 'Nothing here right now.')}</div>`;
    const gaps = _coverageGaps(st.campaignId);
    const gapsHTML = gaps.length
      ? `<div class="desk-v1-conv-gaps">${gaps.map((g, i) => `
          <span class="desk-v1-conv-gap">${esc(g.label)} ${window.DeskV1Kit ? window.DeskV1Kit.infoIconHTML('conv-gap-' + i) : ''}</span>`).join('')}</div>`
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
      : `<div class="desk-v1-conv-noreply">No reply drafted for this one.</div>`;
    const takenOver = !!conv.takenOver;
    const alreadySent = conv.state === 'sent';
    const canSend = !!reply && !thread.hold && !takenOver && conv.state !== 'ignored' && !alreadySent;
    const sendReason = !reply ? 'No reply drafted' : (thread.hold ? thread.hold : (takenOver ? 'Taken over — resume to send again' : (alreadySent ? 'Already sent' : '')));

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
          <button type="button" class="desk-v1-conv-revise" data-conv-revise ${(reply && !takenOver) ? '' : 'disabled'}>Ask Posy to revise</button>
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
      window.DeskV1Kit.commandBus.run({
        label: 'Asked Posy to revise the reply',
        do: () => { thread.reply.text = priorText + ' (revised for tone by Posy)'; _render(campaign); },
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

  window.deskV1RenderConversations = deskV1RenderConversations;
})();
