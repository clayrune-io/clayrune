// Desk v1 (MC-977) — T1: Desk Home (frame 11a, not in the repo — built from
// THE_DESK_V1_UI.md §2 text per the plan's ground rule 8; a design-check
// screenshot goes to Ron before merge). Window-bridged module, no `import`
// (ground rule 1).
//
// Fixtures only (ground rule 3): every drop/propose/pause mutates the
// in-memory DeskV1Fixtures objects directly through DeskV1Kit.commandBus —
// the same "client-side over fixture data with Undo" contract T3's review
// surface already established. Nothing here calls a backend route.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution (same _fx() convention as desk-v1-review.js) ────────
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _campaigns() { return _fx().campaigns || []; }
  function _channels() { return _fx().channels || []; }
  function _families() { return _fx().families || []; }
  function _conversations() { return _fx().conversations || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _campaign(id) { return _campaigns().find((c) => c.id === id); }

  // A version is done concluding once it reaches one of these — a held
  // channel no longer "blocks" it (LIF-01/02/03).
  const _TERMINAL_STATES = new Set(['verified_published', 'you_reported', 'failed', 'skipped', 'archived']);

  // ── Needs you (§2): grouped by kind, holds tinted in the same card, each
  // row deep-links to the real review surface, never to a list. ───────────
  function _needsYouItems() {
    const items = [];
    for (const fam of _families()) {
      for (const v of fam.versions) {
        if (v.state !== 'needs_review') continue;
        items.push({
          kind: fam.kind === 'video' ? 'video' : 'piece',
          campaignId: fam.campaignId, versionId: v.id,
        });
      }
    }
    for (const c of _conversations()) {
      if (c.state !== 'needs_reply') continue;
      items.push({ kind: 'reply', campaignId: c.campaignId, conversationId: c.id });
    }
    return items;
  }

  // Channel-level holds block whatever versions are assigned to that channel
  // and haven't already concluded; the worker heartbeat is a separate,
  // channel-independent hold (§2's "Scheduling paused" example).
  function _holds() {
    const holds = [];
    for (const ch of _channels()) {
      if (ch.health !== 'held') continue;
      let count = 0;
      for (const fam of _families()) {
        for (const v of fam.versions) {
          if (v.channelId === ch.id && !_TERMINAL_STATES.has(v.state)) count++;
        }
      }
      if (count > 0) holds.push({ kind: 'channel', label: ch.holdReason || `${ch.label} held`, count });
    }
    const wh = _fx().workerHeartbeat;
    if (wh && wh.status === 'offline') {
      holds.push({ kind: 'worker', label: `Scheduling paused — worker offline since ${wh.sinceLabel}`, count: wh.missed });
    }
    return holds;
  }

  // ── campaign creation (Propose, and "+ New campaign" from an ambiguous
  // drop) — a pure factory; callers decide when/how to commit + undo so both
  // call sites can wrap it in their own commandBus entry (§10). ───────────
  function _createProposedCampaign(rawName) {
    let name = (rawName || '').trim() || 'New campaign';
    if (name.length > 60) name = name.slice(0, 57) + '…';
    return {
      id: 'camp-proposed-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      name, state: 'proposed',
      goal: { metric: '', target: null, current: 0, deadline: null, tracked: false },
      channelIds: [],
      rules: { reviewMode: 'each_piece', frequencyPerWeek: null, repliesMode: 'drafts', paid: false },
    };
  }

  // ── drop/attach commands (§10: every drop is a command with an inverse) ──
  // A channel dropped/added on a campaign becomes one of its destinations
  // (Home has no single piece of content selected, so "adds a version" per
  // §13 is T2a's content-card case; Home's own contribution to A4 is this —
  // the campaign gains the channel as a destination). Material dropped/added
  // instead creates a brand-new content family with one drafting version,
  // which IS "adds a version" in the literal sense.
  function _channelAttachCommand(camp, channelId) {
    const ch = _channel(channelId);
    return {
      label: `Added ${ch ? ch.label : channelId} to “${camp.name}”`,
      do: () => { camp.channelIds.push(channelId); },
      undo: () => { const i = camp.channelIds.indexOf(channelId); if (i >= 0) camp.channelIds.splice(i, 1); },
    };
  }
  function _assetAttachCommand(camp, asset) {
    const famId = 'fam-home-drop-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const family = {
      id: famId, campaignId: camp.id, kind: asset.kind, title: asset.title,
      versions: [{ id: famId + '-v1', channelId: null, state: 'drafting', revision: 0 }],
    };
    return {
      label: `Added “${asset.title}” to “${camp.name}”`,
      do: () => { _fx().families.push(family); },
      undo: () => { const arr = _fx().families; const i = arr.findIndex((f) => f.id === famId); if (i >= 0) arr.splice(i, 1); },
    };
  }
  function _dropCommandFor(camp, dragData) {
    return dragData.type === 'channel' ? _channelAttachCommand(camp, dragData.channelId) : _assetAttachCommand(camp, dragData.asset);
  }

  function _applyDropToCampaign(campaignId, dragData) {
    const camp = _campaign(campaignId);
    if (!camp) return;
    if (dragData.type === 'channel' && camp.channelIds.includes(dragData.channelId)) {
      DeskV1Kit.toast(`${dragData.label} is already on “${camp.name}”.`);
      return;
    }
    const cmd = _dropCommandFor(camp, dragData);
    DeskV1Kit.commandBus.run({ label: cmd.label, do: () => { cmd.do(); _renderCards(); }, undo: () => { cmd.undo(); _renderCards(); } });
  }

  function _applyDropToNewCampaign(dragData) {
    const camp = _createProposedCampaign('New campaign');
    const cmd = _dropCommandFor(camp, dragData);
    DeskV1Kit.commandBus.run({
      label: `Created “${camp.name}” and added ${dragData.label}`,
      do: () => { _fx().campaigns.push(camp); cmd.do(); _renderCards(); },
      undo: () => { cmd.undo(); const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderCards(); },
    });
    deskV1Nav('campaign', { campaignId: camp.id });
  }

  // "Which campaign? ▾ / New campaign" (UX-03) — reuses the kit's Add to…
  // menu at an arbitrary screen point rather than forking a second dropdown;
  // `addToMenu` always appends "+ New campaign" itself. `tempTrigger` carries
  // `.desk-v1-addto-wrap` so the kit's own `closest('.desk-v1-addto-wrap')`
  // resolves to itself instead of falling through to `document.body` — the
  // kit sets `host.style.position = 'relative'` on whatever it resolves to,
  // and doing that to <body> would be a real layout bug, not a cosmetic one.
  function _chooseCampaign(x, y, onPick) {
    const items = _campaigns().map((c) => ({ id: c.id, label: c.name }));
    const tempTrigger = document.createElement('div');
    tempTrigger.className = 'desk-v1-addto-wrap';
    tempTrigger.style.cssText = `position:fixed; left:${x}px; top:${y}px; width:0; height:0;`;
    document.body.appendChild(tempTrigger);
    const mo = new MutationObserver(() => {
      if (!tempTrigger.querySelector('.desk-v1-addto-menu')) { mo.disconnect(); tempTrigger.remove(); }
    });
    mo.observe(tempTrigger, { childList: true });
    DeskV1Kit.addToMenu(tempTrigger, items, onPick);
  }

  function _onCampaignPicked(pickedId, dragData) {
    if (pickedId === '__new__') _applyDropToNewCampaign(dragData);
    else _applyDropToCampaign(pickedId, dragData);
  }

  // ── drag (pointer-drag.js, T0c) — shelf items are sources, campaign cards
  // are the only targets on Home. One state slot, matching floor.js's own
  // "only one drag at a time" precedent. ───────────────────────────────────
  let _shelfDrag = null;
  let _lastShelfDragEnd = 0; // floor.js's _lastHireDragEnd precedent, same 300ms window

  function _setCardResultText(card, text) {
    const t = card.querySelector('.desk-v1-home-camp-result');
    if (t) t.textContent = text || '';
  }

  function _hoverCampaignCardsAt(x, y, dragData) {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest && el.closest('.desk-v1-home-camp-card');
    document.querySelectorAll('.desk-v1-home-camp-card').forEach((c) => {
      if (c !== card) { c.classList.remove('pd-drop-hover'); _setCardResultText(c, ''); }
    });
    if (card) { card.classList.add('pd-drop-hover'); _setCardResultText(card, `Drop to add ${dragData.label} here`); }
  }

  function _resolveDropAt(x, y) {
    const el = document.elementFromPoint(x, y);
    const card = el && el.closest && el.closest('.desk-v1-home-camp-card');
    if (card) return { type: 'card', campaignId: card.dataset.campaignId };
    const inSection = el && el.closest && el.closest('#desk-v1-home-cards');
    if (inSection) {
      const camps = _campaigns();
      if (camps.length > 1) return { type: 'ambiguous', x, y };
      if (camps.length === 1) return { type: 'card', campaignId: camps[0].id };
    }
    return null; // a miss — teardown just cleans up, no toast (matches floor.js's refused-drop silence)
  }

  function _handleDrop(resolved, dragData) {
    if (resolved.type === 'card') _applyDropToCampaign(resolved.campaignId, dragData);
    else if (resolved.type === 'ambiguous') _chooseCampaign(resolved.x, resolved.y, (id) => _onCampaignPicked(id, dragData));
  }

  function _wireShelfItem(wrapperEl, dragData) {
    if (!wrapperEl) return;
    // Swallow the click a mouse-up still fires on the source element right
    // after a real drag release (floor.js's _lastHireDragEnd comment) — a
    // capture-phase listener runs before bindAddToTrigger's own bubble-phase
    // one below, so this stops it from reopening Add to… on every drop.
    wrapperEl.addEventListener('click', (e) => {
      if (Date.now() - _lastShelfDragEnd < 300) { e.stopImmediatePropagation(); e.preventDefault(); }
    }, true);
    wrapperEl.addEventListener('pointerdown', (e) => {
      window.PointerDrag.begin(wrapperEl, e, {
        isDragActive: () => !!_shelfDrag,
        getDragState: () => _shelfDrag,
        setDragState: (s) => { _shelfDrag = s; },
        data: dragData,
        draggingClass: 'desk-v1-shelf-item-dragging',
        activeBodyClass: 'desk-v1-home-drag-active',
        ghostClass: 'pd-ghost desk-v1-home-drag-ghost',
        ghostHTML: () => `<span class="desk-v1-home-drag-ghost-inner">${esc(dragData.label)}</span>`,
        ghostRotationDeg: -3,
        // Offset from the raw cursor so the ghost never covers the card's
        // own result text (§10's explicit "position it offset... so it
        // never covers the target's result text").
        ghostOffsetX: 18, ghostOffsetY: 18,
        onActivate: () => { document.querySelectorAll('.desk-v1-home-camp-card').forEach((c) => c.classList.add('pd-drop-target')); },
        onMove: (st, x, y) => _hoverCampaignCardsAt(x, y, dragData),
        onDrop: (st, x, y) => _resolveDropAt(x, y),
        afterDrop: (st, resolved) => { if (resolved) _handleDrop(resolved, dragData); },
        onTeardown: () => {
          document.querySelectorAll('.desk-v1-home-camp-card').forEach((c) => { c.classList.remove('pd-drop-target', 'pd-drop-hover'); _setCardResultText(c, ''); });
        },
        onEnd: (st, wasDrag) => { if (wasDrag) _lastShelfDragEnd = Date.now(); },
      });
    });
    // Keyboard/click path (UX-05): focus + Enter, or the visible ＋, opens
    // Add to… — the SAME wrapper works for both the drag and this, exactly
    // as §2 requires ("Every shelf item is draggable and has a click/
    // keyboard path").
    DeskV1Kit.bindAddToTrigger(wrapperEl, () => _campaigns().map((c) => ({ id: c.id, label: c.name })), (pickedId) => _onCampaignPicked(pickedId, dragData));
  }

  // ── render: campaign cards (3-up grid, drop targets) ─────────────────────
  function _campCardHTML(c) {
    const label = DeskV1Kit.stateLabelHTML(c.state);
    const goalHTML = c.goal && c.goal.tracked
      ? (() => {
          const pct = c.goal.target ? Math.max(0, Math.min(100, Math.round((c.goal.current / c.goal.target) * 100))) : 0;
          return `<div class="desk-v1-home-camp-goal">
            <div class="desk-v1-home-camp-goalbar"><div class="desk-v1-home-camp-goalfill" style="width:${pct}%"></div></div>
            <span>${esc(c.goal.current)}/${esc(c.goal.target)} ${esc(c.goal.metric)}</span>
          </div>`;
        })()
      : '<div class="desk-v1-home-camp-goal desk-v1-home-camp-goal-untracked">No goal tracked yet</div>';
    const chans = (c.channelIds || []).map((id) => _channel(id)).filter(Boolean);
    const badges = chans.length
      ? chans.map((ch) => DeskV1Kit.channelBadge(ch, { reviewMode: c.rules && c.rules.reviewMode })).join('')
      : '<span class="desk-v1-home-camp-nochannels">No channels yet</span>';
    // .desk-v1-stub-link is kept alongside the real card class so T0a's
    // desk-v1-harness.mjs (out of this ticket's file list — ground rule 2
    // says don't touch it) keeps finding a generic "the fixture campaign
    // renders as a clickable link on Home" element by that selector; its own
    // T0a styling is overridden below by the more specific card rule.
    return `
      <button type="button" class="desk-v1-home-camp-card desk-v1-stub-link" data-campaign-id="${esc(c.id)}"
        onclick="deskV1Nav('campaign',{campaignId:'${esc(c.id)}'})">
        <div class="desk-v1-home-camp-top">${label}<span class="desk-v1-home-camp-name">${esc(c.name)}</span></div>
        ${goalHTML}
        <div class="desk-v1-home-camp-badges">${badges}</div>
        <div class="desk-v1-home-camp-result" aria-live="polite"></div>
      </button>`;
  }

  function _renderCards() {
    const host = document.getElementById('desk-v1-home-cards');
    if (!host) return;
    const camps = _campaigns();
    host.innerHTML = camps.length
      ? camps.map(_campCardHTML).join('')
      : '<div class="desk-v1-home-empty">No campaigns yet — promote something above to start one.</div>';
    // Card clicks work through the inline onclick above (no rewiring
    // needed); nothing else on a card is interactive beyond the drop
    // hover text, which the active drag's own handlers write directly.
  }

  // ── render: Needs you (right column) ────────────────────────────────────
  function _needsYouRowHTML(glyph, text, firstItem) {
    const onclick = firstItem.kind === 'reply'
      ? `deskV1Nav('conversations',{campaignId:'${esc(firstItem.campaignId)}',conversationId:'${esc(firstItem.conversationId)}'})`
      : `deskV1Nav('review',{campaignId:'${esc(firstItem.campaignId)}',versionId:'${esc(firstItem.versionId)}'})`;
    return `<button type="button" class="desk-v1-home-needsyou-row" onclick="${onclick}">
      <span class="desk-v1-home-needsyou-glyph" aria-hidden="true">${esc(glyph)}</span>
      <span class="desk-v1-home-needsyou-text">${esc(text)}</span>
    </button>`;
  }

  function _holdRowHTML(h) {
    const text = h.kind === 'worker' ? `${h.label} · ${h.count} posts missed` : `${h.label} · ${h.count} held`;
    return `<div class="desk-v1-home-needsyou-row desk-v1-home-needsyou-hold">
      <span class="desk-v1-home-needsyou-glyph" aria-hidden="true">⚠</span>
      <span class="desk-v1-home-needsyou-text">${esc(text)}</span>
      <button type="button" class="desk-v1-home-hold-fix" onclick="window.openSettings &amp;&amp; window.openSettings()">Fix</button>
    </div>`;
  }

  function _renderNeedsYou() {
    const host = document.getElementById('desk-v1-home-needsyou');
    if (!host) return;
    const items = _needsYouItems();
    const pieces = items.filter((i) => i.kind === 'piece');
    const videos = items.filter((i) => i.kind === 'video');
    const replies = items.filter((i) => i.kind === 'reply');
    const rows = [];
    if (pieces.length) rows.push(_needsYouRowHTML('✎', `${pieces.length} piece${pieces.length === 1 ? '' : 's'} to approve`, pieces[0]));
    if (videos.length) rows.push(_needsYouRowHTML('▶', `${videos.length} video${videos.length === 1 ? '' : 's'} to watch`, videos[0]));
    if (replies.length) rows.push(_needsYouRowHTML('💬', `${replies.length} repl${replies.length === 1 ? 'y' : 'ies'} waiting`, replies[0]));
    for (const h of _holds()) rows.push(_holdRowHTML(h));
    host.innerHTML = `<div class="desk-v1-home-needsyou-title">Needs you</div>` +
      (rows.length ? `<div class="desk-v1-home-needsyou-list">${rows.join('')}</div>` : '<div class="desk-v1-home-needsyou-empty">Nothing needs you right now.</div>');
  }

  // ── render: shelves (Channels · Material) ───────────────────────────────
  function _channelShelfItemHTML(ch) {
    return `<div class="desk-v1-shelf-item" data-channel-id="${esc(ch.id)}" tabindex="0" role="button" aria-haspopup="menu" aria-label="${esc(ch.label)} — drag to a campaign, or press Enter for Add to…">
      <span class="desk-v1-shelf-grip" aria-hidden="true">⠿</span>
      ${DeskV1Kit.channelBadge(ch, { reviewMode: 'each_piece' })}
      <span class="desk-v1-shelf-add" aria-hidden="true">＋</span>
    </div>`;
  }

  function _assetShelfItemHTML(asset) {
    const glyph = asset.kind === 'video' ? '▶' : '▤';
    return `<div class="desk-v1-shelf-item desk-v1-home-asset" data-asset-id="${esc(asset.id)}" tabindex="0" role="button" aria-haspopup="menu" aria-label="${esc(asset.title)} — drag to a campaign, or press Enter for Add to…">
      <span class="desk-v1-shelf-grip" aria-hidden="true">⠿</span>
      <span class="desk-v1-home-asset-thumb" aria-hidden="true">${glyph}</span>
      <span class="desk-v1-home-asset-title">${esc(asset.title)}</span>
      <span class="desk-v1-shelf-add" aria-hidden="true">＋</span>
    </div>`;
  }

  function _handleMaterialAction(action) {
    if (action === 'create-video' || action === 'record') {
      const camps = _campaigns();
      deskV1Nav('video', camps.length ? { campaignId: camps[0].id } : {});
      return;
    }
    if (action === 'upload') {
      const input = document.createElement('input');
      input.type = 'file'; input.multiple = true;
      input.onchange = () => {
        const files = Array.from(input.files || []);
        if (!files.length) return;
        DeskV1Kit.toast(`Uploaded ${files.length === 1 ? files[0].name : files.length + ' files'} (fixture only — no upload in R0).`);
      };
      input.click();
      return;
    }
    if (action === 'connect') {
      const url = window.prompt('Link to connect:');
      if (url && url.trim()) DeskV1Kit.toast('Connecting external material lands in a later ticket.');
    }
  }

  function _renderShelves() {
    const chHost = document.getElementById('desk-v1-home-shelf-channels');
    if (chHost) {
      chHost.innerHTML = _channels().map(_channelShelfItemHTML).join('') +
        '<button type="button" class="desk-v1-home-shelf-connect">＋ Connect</button>';
      _channels().forEach((ch) => _wireShelfItem(chHost.querySelector(`[data-channel-id="${ch.id}"]`), { type: 'channel', channelId: ch.id, label: ch.label }));
      const connectBtn = chHost.querySelector('.desk-v1-home-shelf-connect');
      if (connectBtn) connectBtn.onclick = () => DeskV1Kit.toast('Connecting a new destination lands with real accounts (R1).');
    }
    const matHost = document.getElementById('desk-v1-home-shelf-material');
    if (matHost) {
      const tiles = [
        { action: 'create-video', glyph: '✦', label: 'Create video' },
        { action: 'upload', glyph: '⬆', label: 'Upload' },
        { action: 'connect', glyph: '🔗', label: 'Connect' },
        { action: 'record', glyph: '⏺', label: 'Record' },
      ];
      const assets = _fx().recentAssets || [];
      matHost.innerHTML =
        `<div class="desk-v1-home-material-tiles">${tiles.map((t) => `<button type="button" class="desk-v1-home-material-tile" data-material-action="${t.action}"><span aria-hidden="true">${t.glyph}</span> ${esc(t.label)}</button>`).join('')}</div>` +
        `<div class="desk-v1-home-material-assets">${assets.map(_assetShelfItemHTML).join('')}</div>`;
      matHost.querySelectorAll('[data-material-action]').forEach((btn) => { btn.onclick = () => _handleMaterialAction(btn.dataset.materialAction); });
      assets.forEach((asset) => _wireShelfItem(matHost.querySelector(`[data-asset-id="${asset.id}"]`), { type: 'asset', asset, label: asset.title }));
    }
  }

  // ── render: promote box + suggestions ───────────────────────────────────
  function _renderSuggestions() {
    const host = document.getElementById('desk-v1-home-suggestions');
    if (!host) return;
    const chips = (_fx().homeSuggestions || []).slice(0, 3);
    host.innerHTML = chips.length
      ? `<div class="desk-v1-home-suggestion-chips agent-question-chips">${chips.map((c) => `<button type="button" class="agent-question-chip" data-suggest="${esc(c)}">${esc(c)}</button>`).join('')}</div>`
      : '';
    host.querySelectorAll('[data-suggest]').forEach((btn) => {
      btn.onclick = () => {
        const ta = document.getElementById('desk-v1-home-promote-input');
        if (ta) { ta.value = btn.dataset.suggest; ta.focus(); }
      };
    });
  }

  function _submitPromote(ta) {
    const text = (ta && ta.value.trim()) || '';
    if (!text) return;
    if (ta) ta.value = '';
    const camp = _createProposedCampaign(text);
    DeskV1Kit.commandBus.run({
      label: `Proposed “${camp.name}”`,
      do: () => { _fx().campaigns.push(camp); _renderCards(); },
      undo: () => { const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderCards(); },
    });
    deskV1Nav('campaign', { campaignId: camp.id });
  }

  function _handlePromoteIconAction(action, ta) {
    if (action === 'files') {
      const input = document.createElement('input');
      input.type = 'file'; input.multiple = true;
      input.onchange = () => {
        const files = Array.from(input.files || []);
        if (!files.length) return;
        if (ta) ta.value = (ta.value ? ta.value + ' ' : '') + `[Attached: ${files.map((f) => f.name).join(', ')}]`;
        DeskV1Kit.toast(`Attached ${files.length === 1 ? files[0].name : files.length + ' files'} (fixture only — no upload in R0).`);
      };
      input.click();
      return;
    }
    if (action === 'link') {
      const url = window.prompt('Link to promote:');
      if (url && url.trim() && ta) ta.value = (ta.value ? ta.value + ' ' : '') + url.trim();
      return;
    }
    DeskV1Kit.toast('Voice capture lands with the video director (T5).');
  }

  function _handlePromoteDrop(e, ta) {
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length) {
      const files = Array.from(dt.files);
      if (ta) ta.value = (ta.value ? ta.value + ' ' : '') + `[Attached: ${files.map((f) => f.name).join(', ')}]`;
      DeskV1Kit.toast(`Attached ${files.length === 1 ? files[0].name : files.length + ' files'} (fixture only — no upload in R0).`);
      return;
    }
    const uri = dt && (dt.getData('text/uri-list') || dt.getData('text/plain'));
    if (uri && uri.trim() && ta) ta.value = (ta.value ? ta.value + ' ' : '') + uri.trim();
  }

  function _bindPromoteBox(el) {
    const drop = el.querySelector('#desk-v1-home-promote-drop');
    const ta = el.querySelector('#desk-v1-home-promote-input');
    const btn = el.querySelector('.desk-v1-home-promote-btn');
    if (drop) {
      // Native OS file/link drop (HTML5 DnD), the established precedent for
      // OS-originated drops elsewhere (project-actions.js's attDrop,
      // render-core.js) — distinct from pointer-drag.js, which is only for
      // INTRA-app gestures (§10) and never fires for a real OS drag.
      drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('drag-over'); });
      drop.addEventListener('dragleave', () => drop.classList.remove('drag-over'));
      drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('drag-over'); _handlePromoteDrop(e, ta); });
    }
    if (ta) {
      ta.addEventListener('paste', (e) => {
        const files = e.clipboardData && e.clipboardData.files;
        if (files && files.length) {
          e.preventDefault();
          DeskV1Kit.toast(`Attached ${files.length === 1 ? files[0].name : files.length + ' files'} from paste (fixture only — no upload in R0).`);
        }
      });
      ta.addEventListener('keydown', (e) => { if (typeof window.handleInputEnter === 'function') window.handleInputEnter(e, () => _submitPromote(ta), null); });
    }
    if (btn) btn.onclick = () => _submitPromote(ta);
    el.querySelectorAll('.desk-v1-home-promote-icon-btn').forEach((b) => { b.onclick = () => _handlePromoteIconAction(b.dataset.promoteAction, ta); });
  }

  // ── header (project scope · Settings · Pause all) ───────────────────────
  function _bindHeader(el) {
    const settingsBtn = el.querySelector('.desk-v1-home-settings-btn');
    if (settingsBtn) settingsBtn.onclick = () => { if (window.openSettings) window.openSettings(); };
    const scopeBtn = el.querySelector('.desk-v1-home-scope');
    // R0 fixtures model exactly one project — a real switcher needs the
    // multi-project fixture data R1 adds (layout choice, see final report).
    if (scopeBtn) scopeBtn.onclick = () => DeskV1Kit.toast('Project switching lands with real multi-project data (R1).');
    const pauseBtn = el.querySelector('.desk-v1-home-pause-btn');
    if (pauseBtn) pauseBtn.onclick = () => {
      const camps = _campaigns().filter((c) => c.state === 'active');
      if (!camps.length) { DeskV1Kit.toast('No active campaigns to pause.'); return; }
      const prevStates = camps.map((c) => c.state);
      DeskV1Kit.commandBus.run({
        label: `Paused ${camps.length} campaign${camps.length === 1 ? '' : 's'}`,
        do: () => { camps.forEach((c) => { c.state = 'paused'; }); _renderCards(); },
        undo: () => { camps.forEach((c, i) => { c.state = prevStates[i]; }); _renderCards(); },
      });
    };
  }

  // ── simulated worker heartbeat (A13): Home shows no heartbeat chip ever —
  // only a periodic re-check of Needs you, so an offline worker surfaces as
  // a hold row within one interval instead of a persistent status pill.
  // `window.DESK_V1_HOME_HEARTBEAT_MS` and the manual tick hook below exist
  // so a test can assert the mechanism without sleeping through real time.
  const HEARTBEAT_MS = 4000;
  let _heartbeatTimer = null;
  function _startHeartbeat(el) {
    if (_heartbeatTimer) clearInterval(_heartbeatTimer);
    _heartbeatTimer = setInterval(() => {
      if (!document.body.contains(el)) { clearInterval(_heartbeatTimer); _heartbeatTimer = null; return; }
      _renderNeedsYou();
    }, HEARTBEAT_MS);
  }
  window.DESK_V1_HOME_HEARTBEAT_MS = HEARTBEAT_MS;
  // Test-only: forces one heartbeat re-check without waiting HEARTBEAT_MS.
  window.__deskV1HomeTickHeartbeatNow = () => _renderNeedsYou();

  function deskV1RenderHome(el) {
    el.innerHTML = `
      <div class="desk-v1-home">
        <div class="desk-v1-home-header">
          <button type="button" class="desk-v1-home-scope">Clayrune &#9662;</button>
          <div class="desk-v1-home-header-actions">
            <button type="button" class="desk-v1-home-settings-btn">&#9881; Settings</button>
            <button type="button" class="desk-v1-home-pause-btn">&#9208; Pause all</button>
          </div>
        </div>
        <div class="desk-v1-home-promote">
          <div class="desk-v1-home-promote-drop" id="desk-v1-home-promote-drop">
            <textarea id="desk-v1-home-promote-input" class="desk-v1-home-promote-input" rows="2"
              placeholder="What would you like to promote? Type, drop a file or link, or paste."></textarea>
            <button type="button" class="btn-dispatch desk-v1-home-promote-btn">Propose</button>
          </div>
          <div class="desk-v1-home-promote-phone-actions">
            <button type="button" class="desk-v1-home-promote-icon-btn" data-promote-action="files">&#128206; Files</button>
            <button type="button" class="desk-v1-home-promote-icon-btn" data-promote-action="link">&#128279; Link</button>
            <button type="button" class="desk-v1-home-promote-icon-btn" data-promote-action="say">&#127908; Say it</button>
          </div>
          <div class="desk-v1-home-suggestions" id="desk-v1-home-suggestions"></div>
        </div>
        <div class="desk-v1-home-main">
          <div class="desk-v1-home-cards" id="desk-v1-home-cards"></div>
          <div class="desk-v1-home-needsyou" id="desk-v1-home-needsyou"></div>
        </div>
        <div class="desk-v1-home-shelves">
          <div class="desk-v1-home-shelf">
            <div class="desk-v1-home-shelf-title">Channels</div>
            <div class="desk-v1-home-shelf-items" id="desk-v1-home-shelf-channels"></div>
          </div>
          <div class="desk-v1-home-shelf">
            <div class="desk-v1-home-shelf-title">Material</div>
            <div class="desk-v1-home-shelf-items" id="desk-v1-home-shelf-material"></div>
          </div>
        </div>
      </div>`;
    _bindHeader(el);
    _bindPromoteBox(el);
    _renderSuggestions();
    _renderCards();
    _renderNeedsYou();
    _renderShelves();
    _startHeartbeat(el);
  }

  window.deskV1RenderHome = deskV1RenderHome;
})();
