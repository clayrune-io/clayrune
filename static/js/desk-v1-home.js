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
  function _projects() { return _fx().projects || []; }
  function _campaigns() { return _fx().campaigns || []; }
  function _channels() { return _fx().channels || []; }
  function _families() { return _fx().families || []; }
  function _conversations() { return _fx().conversations || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _campaign(id) { return _campaigns().find((c) => c.id === id); }
  function _project(id) { return _projects().find((p) => p.id === id); }

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
        const camp = _campaign(fam.campaignId);
        items.push({
          kind: fam.kind === 'video' ? 'video' : 'piece',
          campaignId: fam.campaignId, versionId: v.id,
          projectId: camp && camp.projectId,
        });
      }
    }
    for (const c of _conversations()) {
      if (c.state !== 'needs_reply') continue;
      items.push({ kind: 'reply', campaignId: c.campaignId, conversationId: c.id, projectId: c.projectId });
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
    let title = (rawName || '').trim() || 'New campaign';
    if (title.length > 60) title = title.slice(0, 57) + '…';
    return {
      id: 'camp-proposed-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6),
      state: 'proposed',
      goal: { current: 0 },
      rules: {},
      plan: {
        title,
        goal: { outcome: '', target: null, deadline: null, tracked: false },
        accounts: [],
        cadence: { per_week: null },
        end: { date: null, post_cap: null },
        paid: false,
      },
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
      label: `Added ${ch ? ch.label : channelId} to “${camp.plan.title}”`,
      do: () => { camp.plan.accounts.push(channelId); },
      undo: () => { const i = camp.plan.accounts.indexOf(channelId); if (i >= 0) camp.plan.accounts.splice(i, 1); },
    };
  }
  function _assetAttachCommand(camp, asset) {
    const famId = 'fam-home-drop-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
    const family = {
      id: famId, campaignId: camp.id, kind: asset.kind, title: asset.title,
      versions: [{ id: famId + '-v1', channelId: null, state: 'drafting', revision: 0 }],
    };
    return {
      label: `Added “${asset.title}” to “${camp.plan.title}”`,
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
    if (dragData.type === 'channel' && camp.plan.accounts.includes(dragData.channelId)) {
      DeskV1Kit.toast(`${dragData.label} is already on “${camp.plan.title}”.`);
      return;
    }
    const cmd = _dropCommandFor(camp, dragData);
    DeskV1Kit.commandBus.run({ label: cmd.label, do: () => { cmd.do(); _renderProjectCards(); }, undo: () => { cmd.undo(); _renderProjectCards(); } });
  }

  function _applyDropToNewCampaign(dragData) {
    const camp = _createProposedCampaign('New campaign');
    const cmd = _dropCommandFor(camp, dragData);
    DeskV1Kit.commandBus.run({
      label: `Created “${camp.plan.title}” and added ${dragData.label}`,
      do: () => { _fx().campaigns.push(camp); cmd.do(); _renderProjectCards(); },
      undo: () => { cmd.undo(); const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderProjectCards(); },
    });
    deskV1Nav('campaign', { campaignId: camp.id });
  }

  function _onCampaignPicked(pickedId, dragData) {
    if (pickedId === '__new__') _applyDropToNewCampaign(dragData);
    else _applyDropToCampaign(pickedId, dragData);
  }

  // ── drag (pointer-drag.js, T0c) — one state slot, matching floor.js's own
  // "only one drag at a time" precedent. ───────────────────────────────────
  let _shelfDrag = null;
  let _lastShelfDragEnd = 0; // floor.js's _lastHireDragEnd precedent, same 300ms window

  // IA1 (§1's hierarchy): Home no longer renders a campaign-card grid — that
  // moved to the project page — so a shelf item dragged on Home has nothing
  // left to hover/land on and every drop is a miss (matches floor.js's own
  // silent-refusal precedent for a drop with no target). The click/keyboard
  // path (UX-05's `bindAddToTrigger`, `addToItems`/`onPick` below) still
  // attaches a channel/asset to any campaign across every project — that
  // capability doesn't require a visible card, only the picker menu.
  function _homeShelfAdapter() {
    return {
      onActivate: () => {},
      onMove: () => {},
      onDrop: () => null,
      afterDrop: () => {},
      onTeardown: () => {},
      addToItems: () => _campaigns().map((c) => {
        const proj = _projects().length > 1 ? _project(c.projectId) : null;
        return { id: c.id, label: proj ? `${c.plan.title} — ${proj.name}` : c.plan.title };
      }),
      onPick: (pickedId, dragData) => _onCampaignPicked(pickedId, dragData),
    };
  }

  function _wireShelfItem(wrapperEl, dragData, adapter) {
    if (!wrapperEl) return;
    adapter = adapter || _homeShelfAdapter();
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
        onActivate: adapter.onActivate,
        onMove: (st, x, y) => adapter.onMove(x, y, dragData),
        onDrop: (st, x, y) => adapter.onDrop(x, y, dragData),
        afterDrop: (st, resolved) => adapter.afterDrop(resolved, dragData),
        onTeardown: adapter.onTeardown,
        onEnd: (st, wasDrag) => { if (wasDrag) _lastShelfDragEnd = Date.now(); },
      });
    });
    // Keyboard/click path (UX-05): focus + Enter, or the visible ＋, opens
    // Add to… — the SAME wrapper works for both the drag and this, exactly
    // as §2 requires ("Every shelf item is draggable and has a click/
    // keyboard path").
    DeskV1Kit.bindAddToTrigger(wrapperEl, () => adapter.addToItems(), (pickedId) => adapter.onPick(pickedId, dragData));
  }

  // ── reusable shelf pair (T2a's Add tray, docs/desk_v1_r0_plan.md: "the Add
  // tray reuses T1's shelf renderer (desk-v1-home.js) - reuse, don't fork").
  // Home's own _renderShelves below is now a thin call into this with its
  // existing adapter/data — byte-identical output, nothing behavioural
  // changes for Home. A caller on another surface supplies its own
  // `targetAdapter` (what a drop target IS there) and may narrow which
  // channels/assets show (`hideChannelIds`, `channels`, `assets`) and which
  // action a tile/Connect button runs (`onMaterialAction`, `onConnect`).
  function deskV1RenderShelfPair(hosts, opts) {
    hosts = hosts || {};
    opts = opts || {};
    const adapter = opts.targetAdapter || _homeShelfAdapter();
    if (hosts.channelsHost) {
      const hide = new Set(opts.hideChannelIds || []);
      const visible = (opts.channels || _channels()).filter((ch) => !hide.has(ch.id));
      hosts.channelsHost.innerHTML = visible.map(_channelShelfItemHTML).join('') +
        (opts.hideConnect ? '' : '<button type="button" class="desk-v1-home-shelf-connect">＋ Connect</button>');
      visible.forEach((ch) => _wireShelfItem(hosts.channelsHost.querySelector(`[data-channel-id="${ch.id}"]`), { type: 'channel', channelId: ch.id, label: ch.label }, adapter));
      const connectBtn = hosts.channelsHost.querySelector('.desk-v1-home-shelf-connect');
      if (connectBtn) connectBtn.onclick = opts.onConnect || (() => DeskV1Kit.toast('Connecting a new destination lands with real accounts (R1).'));
    }
    if (hosts.materialHost) {
      const tiles = [
        { action: 'create-video', glyph: '✦', label: 'Create video' },
        { action: 'upload', glyph: '⬆', label: 'Upload' },
        { action: 'connect', glyph: '🔗', label: 'Connect' },
        { action: 'record', glyph: '⏺', label: 'Record' },
      ];
      const assets = opts.assets != null ? opts.assets : (_fx().recentAssets || []);
      hosts.materialHost.innerHTML =
        `<div class="desk-v1-home-material-tiles">${tiles.map((t) => `<button type="button" class="desk-v1-home-material-tile" data-material-action="${t.action}"><span aria-hidden="true">${t.glyph}</span> ${esc(t.label)}</button>`).join('')}</div>` +
        `<div class="desk-v1-home-material-assets">${assets.map(_assetShelfItemHTML).join('')}</div>`;
      const onMaterialAction = opts.onMaterialAction || _handleMaterialAction;
      hosts.materialHost.querySelectorAll('[data-material-action]').forEach((btn) => { btn.onclick = () => onMaterialAction(btn.dataset.materialAction); });
      assets.forEach((asset) => _wireShelfItem(hosts.materialHost.querySelector(`[data-asset-id="${asset.id}"]`), { type: 'asset', asset, label: asset.title }, adapter));
    }
  }

  // ── IA1 (§5 row IA1, §1's hierarchy): project cards. §1 states Home's grid
  // is one card PER PROJECT, not per campaign — the campaign-card grid below
  // stays exactly as T1-T3 built it (ground rule 2: desk-v1-home.mjs, out of
  // this ticket's file list, still asserts on it directly), so this is
  // additive: a new row above it, the first hop of the new Home -> project
  // -> campaign path IA1's own acceptance test exercises.
  //
  // Dave's review pass 3 (§1: Home shows project cards, no campaign grid):
  // the flat campaign grid + Archived section that used to sit below this
  // row are RETIRED from Home, not just superseded — campaigns (active and
  // archived) are only reachable from here by way of a project's own page
  // (desk-v1-project.js). This card is now the sole "at a glance" surface
  // Home offers per project, so it carries what the old campaign cards used
  // to show in aggregate: how many campaigns, how many are active, and the
  // earliest planned post across them (omitted where the fixture has none —
  // §"no label without a value" from THE_DESK_V1_UI.md still holds). ───────
  function _campaignsFor(projectId) { return _campaigns().filter((c) => c.projectId === projectId); }

  function _nextPostFor(projectId) {
    const campIds = new Set(_campaignsFor(projectId).map((c) => c.id));
    const schedule = _fx().calendarSchedule || {};
    let best = null;
    for (const fam of _families()) {
      if (!campIds.has(fam.campaignId)) continue;
      for (const v of fam.versions) {
        const when = schedule[v.id];
        if (!when || v.state !== 'planned') continue;
        const t = new Date(when).getTime();
        if (!best || t < best.t) best = { t, when };
      }
    }
    return best && best.when;
  }

  function _fmtNextPost(iso) {
    try { return new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso)); }
    catch (e) { return iso; }
  }

  function _projectCardHTML(p) {
    const camps = _campaignsFor(p.id).filter((c) => c.state !== 'archived');
    const count = camps.length;
    const activeCount = camps.filter((c) => c.state === 'active').length;
    const nextPost = _nextPostFor(p.id);
    // T3 row (§4): every draft key under this project is prefixed
    // `project:<pid>:` — this card's own "⟳ Posy working" badge is the
    // one place that prefix match actually reads, per anyPosyWorking's doc
    // comment ("the query a Home card uses").
    const working = window.DeskV1Kit && DeskV1Kit.anyPosyWorking(`project:${p.id}:`);
    return `
      <div class="desk-v1-home-project-card" data-project-id="${esc(p.id)}" role="button" tabindex="0">
        <span class="desk-v1-home-project-name">${esc(p.name)}</span>
        <span class="desk-v1-home-project-meta">${count} campaign${count === 1 ? '' : 's'}${activeCount ? ` · ${activeCount} active` : ''}</span>
        ${nextPost ? `<span class="desk-v1-home-project-nextpost">Next post ${esc(_fmtNextPost(nextPost))}</span>` : ''}
        ${working ? `<span class="desk-v1-home-project-posyworking">${esc(DeskV1Kit.deskAgentWorkingLabel({ project: p }))}</span>` : ''}
      </div>`;
  }

  function _renderProjectCards() {
    const host = document.getElementById('desk-v1-home-projects');
    if (!host) return;
    const projects = _projects();
    host.innerHTML = projects.length
      ? projects.map(_projectCardHTML).join('')
      : '<div class="desk-v1-home-empty">No projects yet.</div>';
    host.querySelectorAll('.desk-v1-home-project-card').forEach((cardEl) => {
      const projectId = cardEl.dataset.projectId;
      const go = () => deskV1Nav('project', { projectId });
      cardEl.addEventListener('click', go);
      cardEl.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); go(); }
      });
    });
  }

  // ── render: Needs you (right column) ────────────────────────────────────
  function _needsYouRowHTML(glyph, text, firstItem) {
    // Each kind deep-links to its own review surface (§2: "12b, 12c, or
    // 12d, not to a list") — video rows go to the director (12d/T5), never
    // the piece-review surface, even though both carry campaignId+versionId.
    const onclick = firstItem.kind === 'reply'
      ? `deskV1Nav('conversations',{campaignId:'${esc(firstItem.campaignId)}',conversationId:'${esc(firstItem.conversationId)}'})`
      : firstItem.kind === 'video'
      ? `deskV1Nav('video',{campaignId:'${esc(firstItem.campaignId)}',versionId:'${esc(firstItem.versionId)}'})`
      : `deskV1HomeGotoReview('${esc(firstItem.campaignId)}','${esc(firstItem.versionId)}')`;
    return `<button type="button" class="desk-v1-home-needsyou-row" onclick="${onclick}">
      <span class="desk-v1-home-needsyou-glyph" aria-hidden="true">${esc(glyph)}</span>
      <span class="desk-v1-home-needsyou-text">${esc(text)}</span>
    </button>`;
  }

  // IA1 (§5 row IA1's acceptance: "Needs-you deep link into a review builds
  // the 5-deep stack") — a "piece to approve" row used to jump straight from
  // Home to 'review' (a 2-deep stack). §1's hierarchy now runs Home ->
  // project -> campaign -> piece -> review, so this pushes the full chain;
  // each deskV1Nav call is one stack frame and they run synchronously (no
  // paint between them), so the visible result is still landing directly on
  // the review surface — only Back now walks up through piece/campaign/
  // project instead of straight to Home. Video/reply rows are untouched:
  // the acceptance names "a review" specifically, and desk-v1-home.mjs's own
  // deep-link checks for those two assert only the surface reached, which a
  // longer stack wouldn't change anyway.
  function deskV1HomeGotoReview(campaignId, versionId) {
    const camp = _campaign(campaignId);
    const projectId = camp && camp.projectId;
    if (projectId) deskV1Nav('project', { projectId });
    deskV1Nav('campaign', { campaignId, projectId });
    deskV1Nav('piece', { campaignId, versionId, projectId });
    deskV1Nav('review', { campaignId, versionId, projectId });
  }
  window.deskV1HomeGotoReview = deskV1HomeGotoReview;

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
      ${DeskV1Kit.channelBadge(ch)}
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
    deskV1RenderShelfPair({
      channelsHost: document.getElementById('desk-v1-home-shelf-channels'),
      materialHost: document.getElementById('desk-v1-home-shelf-material'),
    });
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
      label: `Proposed “${camp.plan.title}”`,
      do: () => { _fx().campaigns.push(camp); _renderProjectCards(); },
      undo: () => { const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderProjectCards(); },
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
    const engagementBtn = el.querySelector('.desk-v1-home-engagement-btn');
    if (engagementBtn) engagementBtn.onclick = () => deskV1Nav('engagement', {});
    const pauseBtn = el.querySelector('.desk-v1-home-pause-btn');
    if (pauseBtn) pauseBtn.onclick = () => {
      const camps = _campaigns().filter((c) => c.state === 'active');
      if (!camps.length) { DeskV1Kit.toast('No active campaigns to pause.'); return; }
      const prevStates = camps.map((c) => c.state);
      DeskV1Kit.commandBus.run({
        label: `Paused ${camps.length} campaign${camps.length === 1 ? '' : 's'}`,
        do: () => { camps.forEach((c) => { c.state = 'paused'; }); _renderProjectCards(); },
        undo: () => { camps.forEach((c, i) => { c.state = prevStates[i]; }); _renderProjectCards(); },
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

  // §1 header count: "💬 Engagement · n" — every conversation across every
  // project still sitting in Incoming or Suggested (desk-v1-engagement.js
  // owns the actual definition; this just reads it).
  function _engagementCountSuffix() {
    const n = typeof window.deskV1EngagementCount === 'function' ? window.deskV1EngagementCount() : 0;
    return n ? ` &middot; ${n}` : '';
  }

  function deskV1RenderHome(el) {
    el.innerHTML = `
      <div class="desk-v1-home">
        <div class="desk-v1-home-header">
          <button type="button" class="desk-v1-home-scope">Clayrune &#9662;</button>
          <div class="desk-v1-home-header-actions">
            <button type="button" class="desk-v1-home-engagement-btn">&#128172; Engagement${_engagementCountSuffix()}</button>
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
        <div class="desk-v1-home-projects" id="desk-v1-home-projects"></div>
        <div class="desk-v1-home-needsyou" id="desk-v1-home-needsyou"></div>
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
    _renderProjectCards();
    _renderNeedsYou();
    _renderShelves();
    _startHeartbeat(el);
  }

  window.deskV1RenderHome = deskV1RenderHome;
  // T2a (docs/desk_v1_r0_plan.md) reuses this shelf pair for its Add tray —
  // "reuse, don't fork" — instead of duplicating the channel/material shelf
  // markup and drag wiring on desk-v1-campaign.js.
  window.deskV1RenderShelfPair = deskV1RenderShelfPair;
})();
