// Desk v1 (MC-977) — R2-10: the ④ Where stop of the campaign map, a board
// (docs/THE_DESK_V1_IA_REVISION_2.md §8 row R2-10, frames 7a + 7b, §11.6 Q1).
// R2-19 (Ron 2026-09-30): Where owns CHANNEL PLACEMENT only — which accounts,
// which messages go to each. A version card carries no time; times belong to
// the ⑤ When stop, which reads the versions placed here.
// Window-bridged module, no `import` (ground rule 1). `desk-v1-shell.js`'s
// `_renderCampaignPanel` calls `deskV1RenderWhere(el, params)` for panel
// 'where'.
//
//   Messages | one column per account the campaign USES | (nothing else)
//   ----------------------------------------------------------------------
//   SOURCES: one card per connected account, dragged UP to add its column
//
// Three drags, each with a keyboard/click equivalent (UX-05):
//   message  -> account column   ADDS a version; the message stays listed
//   version  -> other column     MOVES it (same version, new account)
//   source   -> the board        ADDS that account's column (plan.accounts)
// Every drop is a `DeskV1Kit.commandBus` command with an inverse (§10), so
// each one toasts with Undo. Every command runs through `DeskV1Store.write`
// (R1-W S5): desk_v1_live ON also calls the routes (the campaign's `plan` for a
// column or a voice, M17/M18 for a version); OFF is demo mode, local only.
//
// Approval: the board never computes "Awaiting approval" itself. Adding a
// column is a plain `plan.accounts.push`; Launch (desk-v1-campaign.js
// `_renderLaunchPanel`) already compares `camp.approval.bounds` against the
// live bounds through `DeskV1Kit.boundsWiden`, where a new account is a
// widening and a removed one is not — so "✕ keeps approval" and "a source
// dragged up on an Active campaign → Awaiting approval" both fall out of
// that one rule instead of a second copy of it here.
//
// Reads the R2-7 piece shape (`piece.{id,kind,title,assets[],versions[]}`,
// `version.{id,channelId,state,publishAt}`) through `DeskV1Kit.piece*` — the
// same helpers What's list rows use — so a piece's "on N channels" is one
// number on both surfaces (the count of its live versions), and the kind word
// is What's. Assets (`piece.assets[]`) are What's and the piece page's; the
// board never draws them.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _familiesFor(campaignId) { return (_fx().families || []).filter((f) => f.campaignId === campaignId); }

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
  const _APPROVED = new Set(['approved', 'scheduled']);
  function _restorable(state) { return _M18_STATES.has(state) ? state : 'needs_review'; }

  const PLATFORMS = {
    x: { word: 'X', glyph: '𝕏' },
    linkedin: { word: 'LinkedIn', glyph: 'in' },
    youtube: { word: 'YouTube', glyph: '▶' },
    discord: { word: 'Discord', glyph: 'D' },
    reddit: { word: 'Reddit', glyph: 'R' },
    blog: { word: 'Blog', glyph: '✎' },
  };
  function _platform(ch) { return (ch && PLATFORMS[ch.platform]) || { word: (ch && ch.platform) || '', glyph: '•' }; }

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

  // Avatar: first letter of the handle on a colour picked from a stable hash
  // of the channel id, so one account keeps its colour across every surface.
  const AVATAR_COLORS = ['#5b6ee1', '#d9607a', '#2f6fbf', '#c4503a', '#6b4fb8', '#3a9d8a', '#b07a2a'];
  function _avatarColor(id) {
    let h = 0;
    for (let i = 0; i < String(id).length; i++) h = (h * 31 + String(id).charCodeAt(i)) >>> 0;
    return AVATAR_COLORS[h % AVATAR_COLORS.length];
  }
  function _avatarHTML(ch) {
    const m = String((ch && ch.identity) || '?').match(/[A-Za-z0-9]/);
    const plat = _platform(ch);
    return `<span class="desk-v1-where-avatar" style="background:${_avatarColor(ch ? ch.id : '')}" aria-hidden="true">` +
      `<span class="desk-v1-where-avatar-letter">${esc((m ? m[0] : '?').toUpperCase())}</span>` +
      `<span class="desk-v1-where-avatar-badge" data-platform="${esc(ch ? ch.platform : '')}">${esc(plat.glyph)}</span></span>`;
  }

  // A version counts on the board while it is live and sits on a channel.
  function _isLive(v) { return !!v.channelId && v.state !== 'archived' && v.state !== 'skipped'; }
  // Already out the door (or on its way): moving it would rewrite history.
  const _LOCKED = new Set(['verified_published', 'you_reported', 'sending', 'submitted']);

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
  // not-connected one, whose card routes there.
  function _sources() {
    const all = _fx().channels || [];
    return { bound: all.filter((c) => c.connected !== false), off: all.filter((c) => c.connected === false) };
  }

  // ── state ────────────────────────────────────────────────────────────────
  let _st = null;
  let _lastDragEnd = 0; // swallow the click a mouse-up fires right after a drag (home.js precedent)

  function _camp() { return _st ? _campaign(_st.campaignId) : null; }

  function _repaint(focusSel) {
    if (!_st || !_st.el || !_st.el.isConnected) return;
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
    DeskV1Store.write({
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

  function _addVersion(camp, fam, channelId) {
    const ch = _channel(channelId);
    if (!camp.plan.accounts.includes(channelId)) return;
    const v = { id: `${fam.id}-v-${_uid()}`, channelId, state: 'drafting', revision: 0 };
    DeskV1Store.write({
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

  function _moveVersion(camp, fam, v, toChannelId) {
    const to = _channel(toChannelId);
    if (!v || v.channelId === toChannelId || !camp.plan.accounts.includes(toChannelId)) return;
    if (_LOCKED.has(v.state)) { DeskV1Kit.toast(`“${fam.title}” is already published — it can't move to another account.`); return; }
    const from = _channel(v.channelId);
    const prevChannel = v.channelId;
    const prevState = v.state;
    // A reviewed or scheduled version was approved for ITS account and format;
    // on another account it is a fresh draft again.
    const nextState = (prevState === 'needs_review' || prevState === 'approved' || prevState === 'scheduled') ? 'drafting' : prevState;
    // M18 refuses to change an approved version's account in place: it goes back
    // to review first (which withdraws the approval), then moves as a draft.
    const request = () => _queue(fam.id, async () => {
      if (_APPROVED.has(prevState)) await _api('PATCH', _versionUrl(fam.id, v.id), { state: 'needs_review' });
      const body = { account_id: toChannelId };
      if (nextState !== prevState) body.state = nextState;
      return _api('PATCH', _versionUrl(fam.id, v.id), body);
    });
    const undoRequest = () => _queue(fam.id, async () => {
      const back = _restorable(prevState);
      const out = await _api('PATCH', _versionUrl(fam.id, v.id), { account_id: prevChannel, state: back });
      if (back !== prevState) {
        v.state = back;
        DeskV1Kit.toast(`“${fam.title}” is back in review: approve it again.`);
        _repaint();
      }
      return out;
    });
    DeskV1Store.write({
      label: `Moved “${fam.title}” from ${from ? from.label : 'a column'} to ${to ? to.label : 'a column'}`,
      apply: () => { v.channelId = toChannelId; v.state = nextState; _repaint(); },
      unapply: () => { v.channelId = prevChannel; v.state = prevState; },
      repaint: () => _repaint(),
      request,
      undoRequest,
    });
  }

  // ── drags ────────────────────────────────────────────────────────────────
  // One slot, like floor.js and home.js. Targets are resolved by
  // elementFromPoint at release: account columns for a message or a version,
  // the whole board for a source.
  let _drag = null;

  function _targetAt(x, y, data) {
    const el = document.elementFromPoint(x, y);
    if (!el || !el.closest) return null;
    if (data.kind === 'source') return el.closest('[data-where-board]') ? { type: 'board' } : null;
    const col = el.closest('.desk-v1-where-col[data-channel-id]');
    return col ? { type: 'column', channelId: col.dataset.channelId } : null;
  }

  function _hoverAt(x, y, data) {
    const target = _targetAt(x, y, data);
    _st.el.querySelectorAll('.pd-drop-hover').forEach((n) => n.classList.remove('pd-drop-hover'));
    if (!target) return;
    const node = target.type === 'board'
      ? _st.el.querySelector('[data-where-board]')
      : _st.el.querySelector(`.desk-v1-where-col[data-channel-id="${CSS.escape(target.channelId)}"]`);
    if (node) node.classList.add('pd-drop-hover');
  }

  function _beginDrag(node, e, data) {
    if (!window.PointerDrag) return;
    window.PointerDrag.begin(node, e, {
      isDragActive: () => !!_drag,
      getDragState: () => _drag,
      setDragState: (s) => { _drag = s; },
      data,
      draggingClass: 'desk-v1-where-dragging',
      activeBodyClass: 'desk-v1-where-drag-active',
      ghostClass: 'pd-ghost desk-v1-home-drag-ghost',
      ghostHTML: () => `<span class="desk-v1-home-drag-ghost-inner">${esc(data.label)}</span>`,
      ghostRotationDeg: -3,
      ghostOffsetX: 18, ghostOffsetY: 18,
      onActivate: () => {
        if (!_st) return;
        const sel = data.kind === 'source' ? '[data-where-board]' : '.desk-v1-where-col[data-channel-id]';
        _st.el.querySelectorAll(sel).forEach((n) => n.classList.add('pd-drop-target'));
      },
      onMove: (st, x, y) => { if (_st) _hoverAt(x, y, data); },
      onDrop: (st, x, y) => _targetAt(x, y, data),
      afterDrop: (st, target) => { if (target) _applyDrop(data, target); },
      onTeardown: () => {
        if (_st && _st.el) _st.el.querySelectorAll('.pd-drop-target, .pd-drop-hover').forEach((n) => n.classList.remove('pd-drop-target', 'pd-drop-hover'));
      },
      onEnd: (st, wasDrag) => { if (wasDrag) _lastDragEnd = Date.now(); },
    });
  }

  function _applyDrop(data, target) {
    const camp = _camp();
    if (!camp) return;
    if (data.kind === 'source' && target.type === 'board') { _addColumn(camp, data.channelId); return; }
    if (target.type !== 'column') return;
    const fam = _familiesFor(camp.id).find((f) => f.id === data.familyId);
    if (!fam) return;
    if (data.kind === 'message') _addVersion(camp, fam, target.channelId);
    else if (data.kind === 'version') _moveVersion(camp, fam, (fam.versions || []).find((x) => x.id === data.versionId), target.channelId);
  }

  // Wires one card for its drag AND its click/Enter path. The click after a
  // real drag release is swallowed in the capture phase (home.js's shelf-item
  // precedent) so a drop never also opens the menu.
  function _wireCard(node, data, onActivate) {
    node.addEventListener('click', (e) => {
      if (Date.now() - _lastDragEnd < 300) { e.stopImmediatePropagation(); e.preventDefault(); }
    }, true);
    node.addEventListener('pointerdown', (e) => {
      if (e.target.closest && e.target.closest('button')) return; // a ✕ / Connect inside the card is its own control
      _beginDrag(node, e, data);
    });
    node.addEventListener('click', (e) => {
      if (e.target.closest && e.target.closest('button')) return;
      onActivate(node);
    });
    node.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onActivate(node); }
    });
  }

  // Keyboard/click: pick the destination from a menu.
  function _openMenu(node, items, onPick, refocusSel) {
    if (!items.length) return false;
    DeskV1Kit.addToMenu(node, items, (id) => { onPick(id); _repaint(refocusSel); }, { noAppendNew: true });
    return true;
  }

  // ── view ─────────────────────────────────────────────────────────────────
  function _previewTag(ch) {
    return ch && ch.preview ? '<span class="desk-v1-where-preview" data-preview data-short="Preview" title="Preview · not connected">Preview · not connected</span>' : '';
  }

  function _messageHTML(camp, fam, cols) {
    const pill = DeskV1Kit.pieceChannelsText(fam);
    return `<div class="desk-v1-addto-wrap desk-v1-where-msgwrap">
      <div class="desk-v1-where-msg" role="button" tabindex="0" data-where-message data-family-id="${esc(fam.id)}" data-kind="${esc(fam.kind || '')}"
           aria-haspopup="menu" aria-label="${esc(`${_kindWord(fam.kind)}: ${fam.title}, ${pill}. Press Enter to add a version to an account.`)}">
        <div class="desk-v1-where-msg-kind">${esc(_kindWord(fam.kind))}</div>
        <div class="desk-v1-where-msg-title">${esc(fam.title)}</div>
        <span class="desk-v1-where-pill" data-where-pill>${esc(pill)}</span>
      </div>
    </div>`;
  }

  function _versionHTML(fam, v, ch) {
    const state = DeskV1Kit.stateLabel(v.state);
    return `<div class="desk-v1-addto-wrap desk-v1-where-vwrap">
      <div class="desk-v1-where-vcard" role="button" tabindex="0" data-where-version data-family-id="${esc(fam.id)}" data-version-id="${esc(v.id)}" data-state="${esc(v.state)}"
           aria-haspopup="menu" aria-label="${esc(`${fam.title}, ${_kindWord(fam.kind)} to ${_formatWord(fam.kind, ch)}, ${state.word}. Press Enter to move it to another account.`)}">
        <div class="desk-v1-where-vtitle">${esc(fam.title)}</div>
        <div class="desk-v1-where-vformat">${esc(_kindWord(fam.kind))} → ${esc(_formatWord(fam.kind, ch))}</div>
      </div>
    </div>`;
  }

  function _columnHTML(camp, ch) {
    const plat = _platform(ch);
    const items = _boardVersions(camp, ch.id);
    const body = items.length
      ? items.map(({ fam, v }) => _versionHTML(fam, v, ch)).join('')
      : `<div class="desk-v1-where-colempty" data-where-colempty>Drag a message here to add its ${esc(plat.word)} version.</div>`;
    return `<section class="desk-v1-where-col pd-drop-zone" data-channel-id="${esc(ch.id)}" data-platform="${esc(ch.platform)}" aria-label="${esc(`${ch.identity} on ${plat.word}`)}">
      <header class="desk-v1-where-colhead">
        ${_avatarHTML(ch)}
        <div class="desk-v1-where-colname">
          <div class="desk-v1-where-handle">${esc(ch.identity)}</div>
          <div class="desk-v1-where-plat">${esc(plat.word)}</div>
          ${_previewTag(ch)}
        </div>
        <button type="button" class="desk-v1-where-colremove" data-where-remove data-channel-id="${esc(ch.id)}" aria-label="${esc(`Remove ${ch.identity} (${plat.word}) from this campaign`)}">✕</button>
      </header>
      <label class="desk-v1-where-voice">Voice <input type="text" class="desk-v1-rules-textinput" data-where-voice data-channel-id="${esc(ch.id)}" maxlength="80" value="${esc(DeskV1Kit.accountVoice(camp.plan, ch))}" placeholder="How this account sounds"></label>
      <div class="desk-v1-where-colbody">${body}</div>
    </section>`;
  }

  function _sourceHTML(camp, ch) {
    const plat = _platform(ch);
    const used = (camp.plan.accounts || []).includes(ch.id);
    return `<div class="desk-v1-where-source${used ? ' is-used' : ''}" role="button" tabindex="0" data-where-source data-channel-id="${esc(ch.id)}" data-platform="${esc(ch.platform)}"
         aria-label="${esc(`${ch.identity} on ${plat.word}${used ? ', already on this campaign' : '. Press Enter to add its column'}`)}">
      ${_avatarHTML(ch)}
      <div class="desk-v1-where-source-handle">${esc(ch.identity)}</div>
      <div class="desk-v1-where-source-plat">${esc(plat.word)}</div>
      ${_previewTag(ch)}
    </div>`;
  }

  // A live account carries `publish` (R1-W S5): the tile names the account and
  // says why it cannot be used, the reason the server computed (no token in the
  // vault, LinkedIn app review pending). A demo tile has neither and stays as it was.
  function _offSourceHTML(ch) {
    const plat = _platform(ch);
    const pub = ch.publish || null;
    return `<div class="desk-v1-where-source desk-v1-where-source-off" data-where-source-off data-channel-id="${esc(ch.id)}" data-platform="${esc(ch.platform)}"${pub && pub.reason ? ` title="${esc(pub.reason)}"` : ''}>
      <span class="desk-v1-where-avatar desk-v1-where-avatar-off" aria-hidden="true"><span class="desk-v1-where-avatar-letter">?</span><span class="desk-v1-where-avatar-badge" data-platform="${esc(ch.platform)}">${esc(plat.glyph)}</span></span>
      <div class="desk-v1-where-source-handle">${pub ? esc(ch.identity) : 'not connected'}</div>
      <div class="desk-v1-where-source-plat">${esc(plat.word)}</div>
      ${pub ? `<div class="desk-v1-where-source-reason" data-where-reason>Not connected${pub.reason ? `: ${esc(pub.reason)}` : ''}</div>` : ''}
      <button type="button" class="desk-v1-where-connect" data-where-connect>Connect ›</button>
    </div>`;
  }

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
      <div class="desk-v1-where-board" data-where-board>
        <section class="desk-v1-where-msgs" data-where-messages aria-label="Messages">
          <header class="desk-v1-where-msghead"><div class="desk-v1-where-msgtitle">Messages</div><div class="desk-v1-where-msgsub">every piece from What</div></header>
          <div class="desk-v1-where-msglist">${fams.length ? fams.map((f) => _messageHTML(camp, f, cols)).join('') : '<div class="desk-v1-where-colempty" data-where-nomessages>No pieces yet. Add them in What.</div>'}</div>
        </section>
        ${cols.map((ch) => _columnHTML(camp, ch)).join('')}
        ${cols.length ? '' : '<div class="desk-v1-where-nocols" data-where-nocols>No accounts yet. Drag one up from SOURCES to add its column.</div>'}
      </div>
      <p class="desk-v1-where-rule" data-where-rule>Dragging from Messages always ADDS a version (the message stays listed) · dragging a version between columns MOVES it.</p>
      <section class="desk-v1-where-sources" data-where-sources aria-label="Sources">
        <div class="desk-v1-where-sources-title">Sources — drag a channel up into the campaign</div>
        <div class="desk-v1-where-sources-row">${src.bound.map((ch) => _sourceHTML(camp, ch)).join('')}${src.off.map(_offSourceHTML).join('')}</div>
      </section>
    </div>`;
    _wire(el, camp);
  }

  function _wire(el, camp) {
    el.querySelectorAll('[data-where-message]').forEach((node) => {
      const famId = node.dataset.familyId;
      const fam = _familiesFor(camp.id).find((f) => f.id === famId);
      if (!fam) return;
      _wireCard(node, { kind: 'message', familyId: famId, label: fam.title }, (n) => {
        const items = _columnChannels(camp).map((ch) => ({ id: ch.id, label: `Add ${ch.identity} (${_platform(ch).word}) version` }));
        if (!_openMenu(n, items, (chId) => _addVersion(camp, fam, chId), `[data-where-message][data-family-id="${CSS.escape(famId)}"]`)) {
          DeskV1Kit.toast('Add an account column first: drag one up from SOURCES.');
        }
      });
    });
    el.querySelectorAll('[data-where-version]').forEach((node) => {
      const famId = node.dataset.familyId;
      const vId = node.dataset.versionId;
      const fam = _familiesFor(camp.id).find((f) => f.id === famId);
      const v = fam && (fam.versions || []).find((x) => x.id === vId);
      if (!v) return;
      _wireCard(node, { kind: 'version', familyId: famId, versionId: vId, label: fam.title }, (n) => {
        const items = _columnChannels(camp).filter((ch) => ch.id !== v.channelId)
          .map((ch) => ({ id: ch.id, label: `Move to ${ch.identity} (${_platform(ch).word})` }));
        if (!items.length) { DeskV1Kit.toast('There is no other account column to move it to.'); return; }
        _openMenu(n, items, (chId) => _moveVersion(camp, fam, v, chId), null);
      });
    });
    el.querySelectorAll('[data-where-source]').forEach((node) => {
      const chId = node.dataset.channelId;
      const ch = _channel(chId);
      if (!ch) return;
      _wireCard(node, { kind: 'source', channelId: chId, label: ch.label }, () => {
        _addColumn(camp, chId);
        _repaint(`[data-where-source][data-channel-id="${CSS.escape(chId)}"]`);
      });
    });
    el.querySelectorAll('[data-where-remove]').forEach((btn) => {
      btn.onclick = () => _removeColumn(camp, btn.dataset.channelId);
    });
    el.querySelectorAll('[data-where-connect]').forEach((btn) => {
      btn.onclick = () => window.deskV1Nav('connections');
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
    _paint(el, camp);
  }

  window.deskV1RenderWhere = deskV1RenderWhere;
})();
