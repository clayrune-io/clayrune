// Campaign accounts and read-only post overview. Composer owns post destinations.
// Account selection still writes plan.accounts, the campaign approval bound.
// Legacy What menu placement uses DeskV1WhereActions; no placement UI lives here.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _familiesFor(campaignId) { return (_fx().families || []).filter((f) => f.campaignId === campaignId && !f.archivedAt); }

  let _seq = 0;
  function _uid() { return Date.now().toString(36) + (++_seq).toString(36); }

  // ── R1-W S5: the live routes ─────────────────────────────────────────────
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  function _pieceUrl(id) { return '/api/desk/pieces/' + encodeURIComponent(id); }
  function _versionUrl(famId, vId) { return _pieceUrl(famId) + '/versions/' + encodeURIComponent(vId); }
  function _patchPlan(camp) { return window.deskV1PatchCampaign(camp, ['plan']); }
  // One write at a time per piece, shared with What (a version PATCH must not
  // overtake the POST that makes the piece).
  function _queue(famId, fn) {
    return typeof window.deskV1QueuePiece === 'function' ? window.deskV1QueuePiece(famId, fn) : fn();
  }
  // M18 will not set approved/scheduled (only the human approve route can), so an
  // Undo that would put a version back there lands it in review instead; the
  // user is told, and approves it again.
  const _M18_STATES = new Set(['drafting', 'needs_review', 'planned', 'skipped', 'archived']);
  function _restorable(state) { return _M18_STATES.has(state) ? state : 'needs_review'; }

  const PLATFORMS = {
    x: { word: 'X' },
    linkedin: { word: 'LinkedIn' },
    youtube: { word: 'YouTube' },
    discord: { word: 'Discord' },
    reddit: { word: 'Reddit' },
    blog: { word: 'Blog' },
    instagram: { word: 'Instagram' },
    tiktok: { word: 'TikTok' },
  };
  function _platform(ch) { return (ch && PLATFORMS[ch.platform]) || { word: (ch && ch.platform) || '' }; }

  // `<kind> → <platform format>` on a version card (frame 7b).
  const FORMATS = {
    video: { x: 'X clip', linkedin: 'LinkedIn native video', youtube: 'YouTube upload', discord: 'Discord clip', reddit: 'Reddit video', blog: 'Blog embed' },
    image: { x: 'X post', linkedin: 'LinkedIn post', youtube: 'Community post', discord: 'Discord image', reddit: 'Reddit image', blog: 'Blog figure' },
    article: { x: 'X thread', linkedin: 'LinkedIn native article', youtube: 'Video description', discord: 'Discord post', reddit: 'Reddit post', blog: 'Blog post' },
    post: { x: 'X post', linkedin: 'LinkedIn post', youtube: 'Community post', discord: 'Discord post', reddit: 'Reddit post', blog: 'Blog note' },
  };
  function _kindWord(kind) { return DeskV1Kit.pieceKindWord(kind); }
  function _formatWord(kind, ch) {
    const byKind = FORMATS[kind];
    return (byKind && ch && byKind[ch.platform]) || `${_platform(ch).word} post`.trim();
  }

  // The mark an account wears (column header and SOURCES tile alike) is the
  // vendor logo from desk-v1-service-icons.js, built in desk-v1-where-tiles.js.
  function _markHTML(ch) { return window.DeskV1WhereTiles.markHTML(ch); }

  // A version counts on the board while it is live and sits on a channel.
  function _isLive(v) { return !!v.channelId && v.state !== 'archived' && v.state !== 'skipped'; }
  // Already out the door (or on its way): moving it would rewrite history.
  const _LOCKED = new Set(['verified_published', 'you_reported', 'sending', 'submitted', 'unknown_outcome', 'failed']);

  function _columnChannels(camp) {
    return ((camp.plan && camp.plan.accounts) || []).map((id) => _channel(id) || { id, platform: '', identity: id, label: id });
  }
  function _boardVersions(camp, channelId) {
    const out = [];
    _familiesFor(camp.id).forEach((fam) => (fam.versions || []).forEach((v) => {
      if (_isLive(v) && v.channelId === channelId) out.push({ fam, v });
    }));
    return out;
  }

  // The tray: every connected workspace account (accounts are the workspace's,
  // connected on the Connections screen — not a per-project list), plus every
  // configured but unavailable one, whose card routes there. Preview-only
  // records and catalogue-only services are not configured connections. Read-only
  // accounts (capability `none`) have no publish route; they are listed apart
  // and can still be placed for planning.
  function _sources() {
    const all = (_fx().channels || []).filter((c) => !c.preview &&
      (window.DeskV1Store.live() || c.connected !== false || c.userAdded));
    const ro = (c) => c.capability === 'none';
    return { bound: all.filter((c) => c.connected !== false && !ro(c)),
             off: all.filter((c) => c.connected === false && !ro(c)),
             readOnly: all.filter(ro) };
  }

  // ── state ────────────────────────────────────────────────────────────────
  let _st = null;

  function _camp() { return _st ? _campaign(_st.campaignId) : null; }

  function _repaint(focusSel) {
    if (!_st || !_st.el || !_st.el.isConnected) return;
    if (_st.el.dataset.panel && _st.el.dataset.panel !== 'where') return;
    const camp = _camp();
    if (!camp) return;
    _paint(_st.el, camp);
    const summary = document.getElementById('desk-v1-camp-summary');
    if (summary && typeof window.deskV1FillCampaignSummary === 'function') window.deskV1FillCampaignSummary(summary, { campaignId: camp.id });
    if (focusSel) { const f = _st.el.querySelector(focusSel); if (f) f.focus({ preventScroll: true }); }
  }

  // ── commands (every one has an inverse: §10) ──────────────────────────────
  function _addColumn(camp, channelId) {
    const ch = _channel(channelId);
    if (!ch) return;
    if (camp.plan.accounts.includes(channelId)) { DeskV1Kit.toast(`${ch.label} is already on “${camp.plan.title}”.`); return; }
    return DeskV1Store.write({
      label: `Added ${ch.label} to “${camp.plan.title}”`,
      apply: () => { camp.plan.accounts.push(channelId); _repaint(); },
      unapply: () => { const i = camp.plan.accounts.indexOf(channelId); if (i >= 0) camp.plan.accounts.splice(i, 1); },
      repaint: () => _repaint(),
      request: () => _patchPlan(camp),
      undoRequest: () => _patchPlan(camp),
    });
  }

  // Narrowing: the column goes, and so does every version that has not yet
  // gone out on it (archived, so Undo restores the exact state). A version that
  // already published stays in history — rewriting what happened is not ours.
  function _removeColumn(camp, channelId) {
    const ch = _channel(channelId);
    const idx = camp.plan.accounts.indexOf(channelId);
    if (idx < 0) return;
    const pending = _boardVersions(camp, channelId).map((x) => x.v).filter((v) => !_LOCKED.has(v.state));
    const prev = pending.map((v) => v.state);
    const before = camp.plan.accounts.slice();
    const owner = (v) => _familiesFor(camp.id).find((f) => (f.versions || []).includes(v));
    const archive = (v) => { const fam = owner(v); return fam ? _queue(fam.id, () => _api('PATCH', _versionUrl(fam.id, v.id), { state: 'archived' })) : null; };
    const restore = (v, i) => { const fam = owner(v); return fam ? _queue(fam.id, () => _api('PATCH', _versionUrl(fam.id, v.id), { state: _restorable(prev[i]) })) : null; };
    // Live: the plan first (the likely refusal, with nothing to undo), then each
    // pending version archived (M18). A version that fails to archive puts the
    // column back on the server, so the board never keeps half a removal.
    const request = async () => {
      await _patchPlan(camp);
      const done = [];
      try {
        for (const v of pending) { await archive(v); done.push(v); }
      } catch (e) {
        try {
          await _api('PATCH', '/api/desk/campaigns/' + encodeURIComponent(camp.id) + '?shape=v1',
            { plan: Object.assign({}, camp.plan, { accounts: before }) });
        } catch (_) { /* the original error is the one to show */ }
        for (const v of done) { try { await restore(v, pending.indexOf(v)); } catch (_) { /* best effort */ } }
        throw e;
      }
    };
    const undoRequest = async () => {
      await _patchPlan(camp);
      let downgraded = 0;
      for (const [i, v] of pending.entries()) {
        await restore(v, i);
        if (_restorable(prev[i]) !== prev[i]) { v.state = 'needs_review'; downgraded++; }
      }
      if (downgraded) {
        DeskV1Kit.toast(`${downgraded} approved version${downgraded === 1 ? ' is' : 's are'} back in review: approve ${downgraded === 1 ? 'it' : 'them'} again.`);
        _repaint();
      }
    };
    DeskV1Store.write({
      label: `Removed ${ch ? ch.label : channelId} from “${camp.plan.title}”${pending.length ? ` · ${pending.length} version${pending.length === 1 ? '' : 's'} archived` : ''}`,
      destructive: true,
      apply: () => { camp.plan.accounts.splice(idx, 1); pending.forEach((v) => { v.state = 'archived'; }); _repaint(); },
      unapply: () => { camp.plan.accounts.splice(idx, 0, channelId); pending.forEach((v, i) => { v.state = prev[i]; }); },
      repaint: () => _repaint(),
      request,
      undoRequest,
    });
  }

  // A suggestion is saved once (act -> ensure), then placed. Placing must NOT
  // call back into the suggestion check: ensure() leaves a fam-suggest- id alone
  // when the store is not live or no proposal matches, so a re-check recursed
  // forever on awaited microtasks and ran the renderer out of heap (2026-10-29).
  function _addVersion(camp, fam, channelId) {
    if (fam.suggestion || String(fam.id).startsWith('fam-suggest-')) {
      return window.DeskV1SuggestionPieces.act(fam, () => _placeVersion(camp, fam, channelId));
    }
    return _placeVersion(camp, fam, channelId);
  }

  function _placeVersion(camp, fam, channelId) {
    if ((fam.versions || []).some((v) => v.channelId === channelId && _isLive(v))) {
      DeskV1Kit.toast('This piece already has a version on that channel.'); return;
    }
    const ch = _channel(channelId);
    if (!camp.plan.accounts.includes(channelId)) return;
    const v = { id: `${fam.id}-v-${_uid()}`, channelId, state: 'drafting', revision: 0 };
    return DeskV1Store.write({
      label: `Added a ${ch ? ch.label : 'new'} version of “${fam.title}”`,
      apply: () => { fam.versions.push(v); _repaint(); },
      unapply: () => { const i = fam.versions.indexOf(v); if (i >= 0) fam.versions.splice(i, 1); },
      repaint: () => _repaint(),
      request: () => _queue(fam.id, () => _api('POST', _pieceUrl(fam.id) + '/versions', { id: v.id, account_id: channelId })),
      // No route deletes a version: undoing an add archives it, which is how the
      // board already reads a removed one.
      undoRequest: () => _queue(fam.id, () => _api('PATCH', _versionUrl(fam.id, v.id), { state: 'archived' })),
    });
  }

  // Account selection has click and keyboard paths; no Where node starts a drag.
  function _wireCard(node, onActivate) {
    node.addEventListener('click', (e) => {
      if (!e.target.closest('button')) onActivate(node);
    });
    node.addEventListener('keydown', (e) => {
      if (e.target.closest('button')) return;
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onActivate(node); }
    });
  }

  function _previewTag(ch) {
    if (ch && !ch.preview && ch.publish) return window.DeskV1WhereTiles.statusChipHTML(ch);
    if (ch && !ch.preview && ch.connected === false) return `<span class="desk-v1-where-chip" data-tone="warn">${ch.capability === 'none' ? 'Read only' : 'Not connected'}</span>`;
    return ch && ch.preview ? '<span class="desk-v1-where-preview" data-preview data-short="Preview" title="Preview · not connected">Preview · not connected</span>' : '';
  }

  function _messageHTML(camp, fam, cols) {
    const pill = DeskV1Kit.pieceChannelsText(fam);
    return `<div class="desk-v1-addto-wrap desk-v1-where-msgwrap">
      <div class="desk-v1-where-msg" role="group" tabindex="0" data-where-message data-family-id="${esc(fam.id)}" data-kind="${esc(fam.kind || '')}"
           aria-label="${esc(`${_kindWord(fam.kind)}: ${fam.title}, ${pill}.`)}">
        <div class="desk-v1-where-msg-kind">${esc(_kindWord(fam.kind))}</div>
        <div class="desk-v1-where-msg-title">${esc(fam.title)}</div>
        <span class="desk-v1-where-pill" data-where-pill>${esc(pill)}</span>
        ${window.DeskV1PostPreview.buttonHTML(fam.title)}
      </div>
    </div>`;
  }

  function _versionHTML(fam, v, ch) {
    const state = DeskV1Kit.stateLabel(v.state);
    return `<div class="desk-v1-addto-wrap desk-v1-where-vwrap">
      <div class="desk-v1-where-vcard" role="group" tabindex="0" data-where-version data-family-id="${esc(fam.id)}" data-version-id="${esc(v.id)}" data-state="${esc(v.state)}"
           aria-label="${esc(`${fam.title}, ${_kindWord(fam.kind)} to ${_formatWord(fam.kind, ch)}, ${state.word}.`)}">
        <div class="desk-v1-where-vtitle">${esc(fam.title)}</div>
        ${window.DeskV1PostPreview.buttonHTML(fam.title)}
        <div class="desk-v1-where-vformat">${esc(_kindWord(fam.kind))} → ${esc(_formatWord(fam.kind, ch))}</div>
      </div>
    </div>`;
  }

  function _columnHTML(camp, ch) {
    const plat = _platform(ch);
    const items = _boardVersions(camp, ch.id);
    const count = new Set(items.map(({ fam }) => fam.id)).size;
    const body = items.length
      ? items.map(({ fam, v }) => _versionHTML(fam, v, ch)).join('')
      : `<div class="desk-v1-where-colempty" data-where-colempty>No posts selected for this account yet.</div>`;
    return `<section class="desk-v1-where-col" data-channel-id="${esc(ch.id)}" data-platform="${esc(ch.platform)}" aria-label="${esc(`${ch.identity} on ${plat.word}`)}">
      <header class="desk-v1-where-colhead">
        ${_markHTML(ch)}
        <div class="desk-v1-where-colname">
          <div class="desk-v1-where-handle" title="${esc(ch.identity)}">${esc(ch.identity)}</div>
          <div class="desk-v1-where-plat">${esc(plat.word)}</div>
          ${_previewTag(ch)}
          <div class="desk-v1-where-postcount" data-where-post-count="${count}">${count} post${count === 1 ? '' : 's'}</div>
        </div>
        <button type="button" class="desk-v1-where-colremove" data-where-remove data-channel-id="${esc(ch.id)}" aria-label="${esc(`Remove ${ch.identity} (${plat.word}) from this campaign`)}">✕</button>
      </header>
      <label class="desk-v1-where-voice">Voice <input type="text" class="desk-v1-rules-textinput" data-where-voice data-channel-id="${esc(ch.id)}" maxlength="80" value="${esc(DeskV1Kit.accountVoice(camp.plan, ch))}" placeholder="How this account sounds"></label>
      <div class="desk-v1-where-colbody">${body}</div>
    </section>`;
  }

  // The three tile shapes live in desk-v1-where-tiles.js.
  function _sourceHTML(camp, ch) { return window.DeskV1WhereTiles.sourceHTML(ch, _platform(ch), (camp.plan.accounts || []).includes(ch.id)); }
  function _offSourceHTML(ch) { return window.DeskV1WhereTiles.offHTML(ch, _platform(ch)); }
  function _readOnlySourceHTML(ch) { return window.DeskV1WhereTiles.readOnlyHTML(ch, _platform(ch)); }

  // R2-6's Suggest task can leave `how.suggested.where = {channelId, label}`:
  // shown here as a `? suggested` strip (the agent proposes, the user accepts —
  // nothing is placed until Accept).
  function _suggestHTML(camp) {
    const placement = camp.how && camp.how.suggested && camp.how.suggested.where;
    if (!placement) return '';
    const ch = placement.channelId ? _channel(placement.channelId) : null;
    const label = placement.label || (ch && ch.label) || placement.channelId || '';
    const already = ch && (camp.plan.accounts || []).includes(ch.id);
    return `<div class="desk-v1-stub-inline desk-v1-where-suggest" data-where-suggest>
      <strong>? suggested:</strong> ${esc(label)}${already ? ' — already a column' : ''}
      ${DeskV1Kit.becauseChipsHTML(placement.because, camp.projectId)}
      ${ch && !already ? '<button type="button" class="btn-secondary desk-v1-where-suggest-accept" data-where-suggest-accept>Add this column</button>' : ''}
    </div>`;
  }

  function _awaitingHTML(camp) {
    if (camp.state === 'draft' || !camp.approval || !camp.approval.bounds || typeof window.deskV1CampaignBounds !== 'function') return '';
    if (!DeskV1Kit.boundsWiden(camp.approval.bounds, window.deskV1CampaignBounds(camp))) return '';
    return `<div class="desk-v1-where-notice" data-where-awaiting role="status">⚠ Awaiting approval — a change since the last approval widens what this campaign can do.
      <button type="button" class="desk-v1-where-notice-link" data-where-to-launch>Approve it at Launch ›</button></div>`;
  }

  function _paint(el, camp) {
    const cols = _columnChannels(camp);
    const fams = _familiesFor(camp.id);
    const src = _sources();
    el.innerHTML = `<div class="desk-v1-where" data-where data-campaign-id="${esc(camp.id)}">
      ${_suggestHTML(camp)}
      ${_awaitingHTML(camp)}
      ${window.DeskV1WhereServices.noticeHTML()}
      <p class="desk-v1-where-drop-hint" data-where-drop-hint>Select campaign accounts below. This overview shows which posts go to each account.</p>
      <div class="desk-v1-where-board" data-where-board>
        <section class="desk-v1-where-msgs" data-where-messages aria-label="Messages">
          <header class="desk-v1-where-msghead"><div class="desk-v1-where-msgtitle">Messages</div><div class="desk-v1-where-msgsub">every piece from What</div></header>
          <div class="desk-v1-where-msglist">${fams.length ? fams.map((f) => _messageHTML(camp, f, cols)).join('') : '<div class="desk-v1-where-colempty" data-where-nomessages>No pieces yet. Add them in What.</div>'}</div>
        </section>
        ${cols.map((ch) => _columnHTML(camp, ch)).join('')}
        ${cols.length ? '' : '<div class="desk-v1-where-nocols" data-where-nocols>No accounts yet. Select one from Sources below.</div>'}
      </div>
      <p class="desk-v1-where-rule" data-where-rule>Post destinations are edited in the composer. This overview does not change individual assignments.</p>
      <section class="desk-v1-where-sources" data-where-sources aria-label="Sources">
        <div class="desk-v1-where-sources-title">Sources — select accounts for this campaign</div>
        <div class="desk-v1-where-sources-row">${src.bound.map((ch) => _sourceHTML(camp, ch)).join('')}${src.off.map(_offSourceHTML).join('')}${src.readOnly.map(_readOnlySourceHTML).join('')}${window.DeskV1WhereServices.html(_fx().channels || [])}</div>
      </section>
    </div>`;
    _wire(el, camp);
    window.DeskV1WhereServices.bind(el, _repaint);
    window.DeskV1WhereHighlight.mark(el);
  }

  function _wire(el, camp) {
    el.querySelectorAll('[data-where-source]').forEach((node) => {
      const chId = node.dataset.channelId;
      const ch = _channel(chId);
      if (!ch) return;
      _wireCard(node, () => {
        _addColumn(camp, chId);
        _repaint(`[data-where-source][data-channel-id="${CSS.escape(chId)}"]`);
      });
    });
    el.querySelectorAll('[data-where-service]').forEach((node) => {
      const svcId = node.dataset.whereService;
      _wireCard(node,
        () => window.DeskV1WhereServices.explain(svcId, _repaint));
    });
    el.querySelectorAll('[data-where-remove]').forEach((btn) => {
      btn.onclick = () => _removeColumn(camp, btn.dataset.channelId);
    });
    el.querySelectorAll('[data-where-connect]').forEach((btn) => {
      // Lands on that account's detail (Connections opens the account it is given).
      const card = btn.closest('[data-channel-id]');
      btn.onclick = () => window.deskV1Nav('connections', { account: card ? card.dataset.channelId : undefined });
    });
    el.querySelectorAll('[data-where-voice]').forEach((inp) => {
      inp.addEventListener('change', () => {
        const chId = inp.dataset.channelId;
        const ch = _channel(chId);
        const prev = (camp.plan.voices || {})[chId];
        const next = inp.value.trim();
        if (next === DeskV1Kit.accountVoice(camp.plan, ch)) return;
        DeskV1Store.write({
          label: `Set ${ch ? ch.identity : 'account'} voice to “${next || 'default'}”`,
          apply: () => { camp.plan.voices = camp.plan.voices || {}; if (next) camp.plan.voices[chId] = next; else delete camp.plan.voices[chId]; _repaint(); },
          unapply: () => { camp.plan.voices = camp.plan.voices || {}; if (prev) camp.plan.voices[chId] = prev; else delete camp.plan.voices[chId]; },
          repaint: () => _repaint(),
          request: () => _patchPlan(camp),
          undoRequest: () => _patchPlan(camp),
        });
      });
    });
    // Hover (mouse) or the eye button (touch) previews the real post as it will look on its platform.
    window.DeskV1PostPreview.bind(el, (node) => {
      const fam = _familiesFor(camp.id).find((f) => f.id === node.dataset.familyId);
      return fam && { fam, versionId: node.dataset.versionId || null,
        entries: (fam.versions || []).filter(_isLive).map((v) => ({ v, ch: _channel(v.channelId) })) };
    });
    DeskV1Kit.bindBecauseChips(el, camp.projectId);
    const accept = el.querySelector('[data-where-suggest-accept]');
    if (accept) accept.onclick = () => {
      const p = camp.how.suggested.where;
      _addColumn(camp, p.channelId);
    };
    const toLaunch = el.querySelector('[data-where-to-launch]');
    if (toLaunch) toLaunch.onclick = () => window.deskV1GotoCampaignPanel('launch', { campaignId: camp.id });
  }

  function deskV1RenderWhere(el, params) {
    const camp = _campaign(params.campaignId);
    if (!camp) { el.innerHTML = '<div class="desk-v1-stub-inline">Campaign not found.</div>'; return; }
    if (!camp.plan) camp.plan = {};
    if (!camp.plan.accounts) camp.plan.accounts = [];
    _st = { el, campaignId: camp.id };
    window.DeskV1WhereHighlight.set(params);
    window.DeskV1WhereServices.refresh(_repaint);
    window.DeskV1WhereTiles.loadStatus(_repaint);
    _paint(el, camp);
  }

  window.DeskV1WhereActions = {
    addVersion: async (camp, fam, channelId) => {
      if (!camp.plan.accounts.includes(channelId)) {
        const result = await _addColumn(camp, channelId);
        if (!result || !result.ok) return result;
      }
      return _addVersion(camp, fam, channelId);
    },
  };
  window.deskV1RenderWhere = deskV1RenderWhere;
})();
