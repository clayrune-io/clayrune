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
    DeskV1Kit.commandBus.run({ label: cmd.label, do: () => { cmd.do(); _renderStatusBoard(); }, undo: () => { cmd.undo(); _renderStatusBoard(); } });
  }

  function _applyDropToNewCampaign(dragData) {
    const camp = _createProposedCampaign('New campaign');
    const cmd = _dropCommandFor(camp, dragData);
    DeskV1Kit.commandBus.run({
      label: `Created “${camp.plan.title}” and added ${dragData.label}`,
      do: () => { _fx().campaigns.push(camp); cmd.do(); _renderStatusBoard(); },
      undo: () => { cmd.undo(); const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderStatusBoard(); },
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

  // ── R2-2 (§8 amended row, mockups_r2/1-home.png): the status board. Home's
  // grid used to be one card PER PROJECT with campaigns hidden a hop away
  // (IA1); the amended row folds the campaign grid back onto Home itself —
  // one column header for the page, one bordered block per project, one row
  // per (non-archived) campaign — so §1's Home -> project -> campaign hop
  // still exists for Back/deep-link purposes (a row click pushes 'project'
  // then 'campaign', same construction as deskV1HomeGotoReview below) but
  // both levels are visible without a click. Archived campaigns stay
  // reachable only from the project page (IA1's retirement holds). ────────
  function _campaignsFor(projectId) { return _campaigns().filter((c) => c.projectId === projectId); }

  const _SUBJECT_GLYPH = { project: '◉', product: '▣', feature: '✦', audience: '◎' };
  const _PLATFORM_WORD = { x: 'X', linkedin: 'LinkedIn', blog: 'Blog' };
  function _platformWord(ch) { return (ch && _PLATFORM_WORD[ch.platform]) || (ch && ch.label) || ''; }

  // A campaign is "just started" once it's Active but barely into its own
  // term — dividing a near-zero progress by a near-zero elapsed fraction
  // would read as "Behind" (frame `4-community-discord-launch` row: 0 of 40,
  // day 1, reads "On track", not a false alarm) for every fresh campaign, so
  // both the STAGE qualifier and the PACE pill below special-case it instead
  // of running the ratio.
  const _JUST_STARTED_ELAPSED = 0.05;

  // Mirrors desk-v1-results.js's own `_paceState`/`_fractionElapsed`
  // (progress ÷ elapsed; >1.2 ahead, >=0.9 on track, else behind) — change
  // one, change both. Kept separate rather than shared because Home reads a
  // WIDER "tracked" condition than the Goal stop's effectiveness panel does
  // (below).
  function _paceState(pace) {
    if (pace == null) return null;
    if (pace > 1.2) return { key: 'ahead', word: 'Ahead' };
    if (pace >= 0.9) return { key: 'on_track', word: 'On track' };
    return { key: 'behind', word: 'Behind' };
  }
  function _fractionElapsed(term) {
    if (!term || !term.starts || !term.ends) return null;
    const start = new Date(term.starts).getTime();
    const end = new Date(term.ends).getTime();
    if (!(end > start)) return null;
    return Math.max(0, Math.min(1, (Date.now() - start) / (end - start)));
  }

  // R2-4's full measurable-goal shape (`goal.source`/`entries`) only exists
  // on campaigns that ticket has actually touched — today, only camp-1. The
  // status board still owes every OTHER active/proposed campaign a real
  // GOAL PROGRESS/PACE reading, so it reads the older `plan.goal.tracked` +
  // `plan.goal.target` (set at Create for every campaign) as a fallback
  // source of truth, with `goal.current` staying the one live number either
  // shape uses.
  function _homeGoalOf(camp) {
    const planGoal = (camp.plan && camp.plan.goal) || {};
    const goal = camp.goal || {};
    const tracked = !!planGoal.tracked || goal.source === 'manual';
    const target = planGoal.target != null ? planGoal.target : goal.target;
    const metric = goal.metric || planGoal.outcome || '';
    let current = goal.current || 0;
    if (goal.source === 'manual' && goal.entries && goal.entries.length) {
      current = goal.entries.reduce((a, b) => (new Date(b.at).getTime() > new Date(a.at).getTime() ? b : a)).value;
    }
    return { tracked, target, metric, current };
  }

  function _stageHTML(camp) {
    const { glyph, word } = DeskV1Kit.stateLabel(camp.state);
    const elapsed = camp.state === 'active' ? _fractionElapsed(camp.term) : null;
    const justStarted = elapsed != null && elapsed < _JUST_STARTED_ELAPSED;
    return `<span class="desk-v1-state-label desk-v1-home-row-state" data-state="${esc(camp.state || '')}">
      <span class="desk-v1-state-glyph" aria-hidden="true">${esc(glyph)}</span>
      <span class="desk-v1-state-word">${esc(word)}${justStarted ? ' &middot; just started' : ''}</span>
    </span>`;
  }

  // Bar fill = current/target; the elapsed tick is a second, independent
  // reading (position = fraction of `camp.term` elapsed) — the row's own
  // `aria-label` is the ONE place both numbers are stated in one sentence,
  // since the visual tick has no text of its own (A15: never colour alone,
  // and here not position alone either).
  function _goalBarHTML(camp) {
    const g = _homeGoalOf(camp);
    if (!g.tracked || !g.target) {
      return `<div class="desk-v1-home-goal">
        <div class="desk-v1-home-goal-bar desk-v1-home-goal-bar-untracked" aria-hidden="true"></div>
        <div class="desk-v1-home-goal-text desk-v1-home-goal-untracked">not measured yet</div>
      </div>`;
    }
    const progressRatio = g.target ? g.current / g.target : 0;
    const progressPct = Math.max(0, Math.min(100, Math.round(progressRatio * 100)));
    const elapsed = _fractionElapsed(camp.term);
    const elapsedPct = elapsed == null ? null : Math.round(elapsed * 100);
    const ariaLabel = elapsedPct == null ? `${progressPct}% of goal` : `${progressPct}% of goal, ${elapsedPct}% of term elapsed`;
    return `<div class="desk-v1-home-goal">
      <div class="desk-v1-home-goal-bar" role="img" aria-label="${esc(ariaLabel)}">
        <div class="desk-v1-home-goal-fill" style="width:${progressPct}%"></div>
        ${elapsedPct != null ? `<div class="desk-v1-home-goal-tick" style="left:${elapsedPct}%"></div>` : ''}
      </div>
      <div class="desk-v1-home-goal-text">${esc(g.current)} of ${esc(g.target)} ${esc(g.metric)}</div>
    </div>`;
  }

  function _pacePillHTML(camp) {
    const g = _homeGoalOf(camp);
    if (!g.tracked || !g.target) return '<span class="desk-v1-home-pace-empty">&mdash;</span>';
    const elapsed = _fractionElapsed(camp.term);
    let paceSt = null;
    if (elapsed != null && elapsed < _JUST_STARTED_ELAPSED) paceSt = { key: 'on_track', word: 'On track' };
    else if (elapsed != null && elapsed > 0) paceSt = _paceState((g.current / g.target) / elapsed);
    if (!paceSt) return '<span class="desk-v1-home-pace-empty">&mdash;</span>';
    return `<span class="desk-v1-home-pace-pill" data-pace="${esc(paceSt.key)}">${esc(paceSt.word)}</span>`;
  }

  // NEXT POST reads the earliest still-pending (scheduled/planned) version
  // across the campaign's own families; a campaign with nothing scheduled
  // yet falls back to where it stands on the map (`Draft · at <stop>`) so
  // the cell never reads simply blank.
  function _nextPostForCampaign(camp) {
    const schedule = _fx().calendarSchedule || {};
    let best = null;
    for (const fam of _families()) {
      if (fam.campaignId !== camp.id) continue;
      for (const v of fam.versions) {
        if (v.state !== 'scheduled' && v.state !== 'planned') continue;
        const iso = v.publishAt || schedule[v.id];
        if (!iso) continue;
        const t = new Date(iso).getTime();
        if (!best || t < best.t) best = { t, iso, channelId: v.channelId };
      }
    }
    return best;
  }
  function _fmtNextPostShort(iso) {
    try {
      const d = new Date(iso);
      const wk = new Intl.DateTimeFormat(undefined, { weekday: 'short' }).format(d);
      const hm = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
      return `${wk} ${hm}`;
    } catch (e) { return iso; }
  }
  function _nextPostHTML(camp) {
    const best = _nextPostForCampaign(camp);
    if (best) {
      const ch = _channel(best.channelId);
      return `<span class="desk-v1-home-nextpost">${esc(_fmtNextPostShort(best.iso))} &middot; ${esc(_platformWord(ch))}</span>`;
    }
    const stopWord = camp.map && camp.map.stop && DeskV1Kit.MAP_STOP_WORDS[camp.map.stop];
    return stopWord
      ? `<span class="desk-v1-home-nextpost desk-v1-home-nextpost-draft">Draft &middot; at ${esc(stopWord)}</span>`
      : '<span class="desk-v1-home-nextpost-empty">&mdash;</span>';
  }

  // NEEDS YOU (§11.6 item 2: the column REPLACES the old Home-wide section —
  // this is the per-campaign reading that section's flat counts never had).
  // Priority, highest first: (1) a Proposed campaign's own sequencing
  // blocker (CMP-03) — nothing else matters until that's answered; (2) a
  // held channel among the campaign's own accounts — an infra problem that
  // blocks publishing regardless of what's approved; (3)/(4)/(5) pieces to
  // approve / videos to watch / replies waiting, same three kinds the old
  // section counted, now scoped to this campaign. Within (3)-(5) the row
  // shows the top non-empty kind's own count plus "+n" for the other kinds
  // present — never a raw item count across kinds.
  function _campaignNeedsYou(camp) {
    if (camp.state === 'proposed') {
      const extras = (_fx().proposedExtras || {})[camp.id];
      if (extras && extras.blocker) return { kind: 'blocker', text: 'Needs your answer' };
    }
    const heldCh = (camp.plan.accounts || []).map(_channel).find((ch) => ch && ch.health === 'held');
    if (heldCh) return { kind: 'held', text: `${_platformWord(heldCh)} disconnected` };
    const pieces = []; const videos = [];
    for (const fam of _families()) {
      if (fam.campaignId !== camp.id) continue;
      for (const v of fam.versions) {
        if (v.state !== 'needs_review') continue;
        (fam.kind === 'video' ? videos : pieces).push(v.id);
      }
    }
    const replies = _conversations().filter((c) => c.campaignId === camp.id && (c.state === 'needs_you' || c.state === 'needs_reply'));
    const buckets = [];
    if (pieces.length) buckets.push({ kind: 'piece', versionId: pieces[0], text: `${pieces.length} piece${pieces.length === 1 ? '' : 's'} to approve` });
    if (videos.length) buckets.push({ kind: 'video', versionId: videos[0], text: `${videos.length} video${videos.length === 1 ? '' : 's'} to watch` });
    if (replies.length) buckets.push({ kind: 'reply', conversationId: replies[0].id, text: `${replies.length} repl${replies.length === 1 ? 'y' : 'ies'} waiting` });
    if (!buckets.length) return null;
    const top = buckets[0];
    const extra = buckets.length - 1;
    return Object.assign({}, top, { text: extra ? `${top.text} +${extra}` : top.text });
  }

  function _needsYouPillHTML(camp) {
    const item = _campaignNeedsYou(camp);
    if (!item) return '<span class="desk-v1-home-needsyou-empty">&mdash;</span>';
    // _goToNeedsYou reads campaignId/projectId off this button's own dataset
    // (bound via btn.dataset in _bindStatusBoard) — the row's data-campaign-id
    // is on an ancestor div the click handler never consults, so these two
    // must be repeated here or every deep-link silently gets `undefined`.
    const attrs = [`data-needsyou-kind="${esc(item.kind)}"`, `data-campaign-id="${esc(camp.id)}"`, `data-project-id="${esc(camp.projectId)}"`];
    if (item.versionId) attrs.push(`data-version-id="${esc(item.versionId)}"`);
    if (item.conversationId) attrs.push(`data-conversation-id="${esc(item.conversationId)}"`);
    return `<button type="button" class="desk-v1-home-needsyou-pill" ${attrs.join(' ')}>
      <span aria-hidden="true">&#9888;</span> ${esc(item.text)}
    </button>`;
  }

  function _rowHTML(camp) {
    const kind = (camp.subject && camp.subject.kind) || '';
    const glyph = _SUBJECT_GLYPH[kind] || '';
    return `<div class="desk-v1-home-row" data-campaign-id="${esc(camp.id)}" data-project-id="${esc(camp.projectId)}" role="button" tabindex="0">
      <div class="desk-v1-home-row-campaign">
        <div class="desk-v1-home-row-title">${esc(camp.plan.title)}</div>
        <div class="desk-v1-home-row-subject">${esc(glyph)} ${esc(kind)} &middot; ${esc((camp.subject && camp.subject.label) || '')}</div>
      </div>
      <div class="desk-v1-home-row-stage">${_stageHTML(camp)}</div>
      <div class="desk-v1-home-row-goal">${_goalBarHTML(camp)}</div>
      <div class="desk-v1-home-row-pace">${_pacePillHTML(camp)}</div>
      <div class="desk-v1-home-row-next">${_nextPostHTML(camp)}</div>
      <div class="desk-v1-home-row-needsyou">${_needsYouPillHTML(camp)}</div>
    </div>`;
  }

  function _blockHeaderAgentHTML(project) {
    const resolved = DeskV1Kit.resolveDeskAgent(DeskV1Kit.deskAgentRef({ project }));
    const label = resolved.name ? `${resolved.name} plans & writes` : DeskV1Kit.UNRESOLVED_AGENT_LABEL;
    return `<button type="button" class="desk-v1-home-block-agent${resolved.name ? '' : ' desk-v1-home-block-agent-unresolved'}" data-project-id="${esc(project.id)}">
      <span aria-hidden="true">&#129302;</span> ${esc(label)}
    </button>`;
  }

  function _projectBlockHTML(p) {
    const camps = _campaignsFor(p.id).filter((c) => c.state !== 'archived');
    return `<div class="desk-v1-home-block" data-project-id="${esc(p.id)}">
      <div class="desk-v1-home-block-head">
        <button type="button" class="desk-v1-home-block-name" data-project-id="${esc(p.id)}">${esc(p.name)}</button>
        ${_blockHeaderAgentHTML(p)}
        <button type="button" class="desk-v1-home-block-newcamp" data-project-id="${esc(p.id)}">&#65291; New campaign</button>
      </div>
      <div class="desk-v1-home-block-rows">
        ${camps.length ? camps.map(_rowHTML).join('') : '<div class="desk-v1-home-block-empty">No campaigns yet in this project &mdash; use &#65291; New campaign to start one.</div>'}
      </div>
    </div>`;
  }

  // Scheduling-paused (A13) has no per-campaign or per-project home in the
  // new row shape (it's a worker-wide condition) — surfaced as a banner
  // above the board rather than silently dropped. Held-CHANNEL holds (the
  // rest of A13) DO have a natural home: the affected campaign's own
  // NEEDS YOU cell, via `_campaignNeedsYou` above.
  function _workerBannerHTML() {
    const wh = _fx().workerHeartbeat;
    if (!wh || wh.status !== 'offline') return '';
    return `<div class="desk-v1-home-worker-banner">
      <span aria-hidden="true">&#9888;</span>
      <span>Scheduling paused &mdash; worker offline since ${esc(wh.sinceLabel)} &middot; ${esc(wh.missed)} posts missed</span>
      <button type="button" class="desk-v1-home-hold-fix" onclick="window.openSettings &amp;&amp; window.openSettings()">Fix</button>
    </div>`;
  }

  function _newCampaignForProject(projectId) {
    const camp = _createProposedCampaign('New campaign');
    camp.projectId = projectId;
    DeskV1Kit.commandBus.run({
      label: `Created "${camp.plan.title}"`,
      do: () => { _fx().campaigns.push(camp); _renderStatusBoard(); },
      undo: () => { const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderStatusBoard(); },
    });
    deskV1Nav('project', { projectId });
    deskV1Nav('campaign', { campaignId: camp.id, projectId });
  }

  function _goToNeedsYou(ds) {
    const { needsyouKind, campaignId, projectId, versionId, conversationId } = ds;
    if (needsyouKind === 'piece') { deskV1HomeGotoReview(campaignId, versionId); return; }
    deskV1Nav('project', { projectId });
    deskV1Nav('campaign', { campaignId, projectId });
    if (needsyouKind === 'video') deskV1Nav('video', { campaignId, versionId });
    else if (needsyouKind === 'reply') deskV1Nav('conversations', { campaignId, conversationId });
    // 'held' / 'blocker': the campaign page itself is where that reason renders.
  }

  function _bindStatusBoard(host) {
    host.querySelectorAll('.desk-v1-home-block-name').forEach((btn) => {
      btn.onclick = () => deskV1Nav('project', { projectId: btn.dataset.projectId });
    });
    host.querySelectorAll('.desk-v1-home-block-agent').forEach((btn) => {
      btn.onclick = () => deskV1Nav('presence', { projectId: btn.dataset.projectId });
    });
    host.querySelectorAll('.desk-v1-home-block-newcamp').forEach((btn) => {
      btn.onclick = () => _newCampaignForProject(btn.dataset.projectId);
    });
    host.querySelectorAll('.desk-v1-home-row').forEach((rowEl) => {
      const { campaignId, projectId } = rowEl.dataset;
      const go = () => { deskV1Nav('project', { projectId }); deskV1Nav('campaign', { campaignId, projectId }); };
      rowEl.addEventListener('click', (e) => { if (!e.target.closest('.desk-v1-home-needsyou-pill')) go(); });
      rowEl.addEventListener('keydown', (e) => {
        if ((e.key === 'Enter' || e.key === ' ') && !e.target.closest('.desk-v1-home-needsyou-pill')) { e.preventDefault(); go(); }
      });
    });
    host.querySelectorAll('.desk-v1-home-needsyou-pill').forEach((btn) => {
      btn.onclick = (e) => { e.stopPropagation(); _goToNeedsYou(btn.dataset); };
    });
  }

  // Page-level "+ New campaign" (mockups_r2/1-home.png, top right of the
  // page title) is ambiguous about WHICH project until picked — reuses the
  // same picker construction as the crumb's Projects picker (shell.js), then
  // hands off to the same `_newCampaignForProject` a block header's own
  // "+ New campaign" already uses.
  function _bindNewCampaignPageBtn(host) {
    const btn = host.querySelector('.desk-v1-home-newcamp-page-btn');
    if (!btn) return;
    DeskV1Kit.bindAddToTrigger(btn, () => _projects().map((p) => ({ id: p.id, label: p.name })),
      (projectId) => _newCampaignForProject(projectId), { noAppendNew: true });
  }

  function _renderStatusBoard() {
    const host = document.getElementById('desk-v1-home-board');
    if (!host) return;
    const projects = _projects();
    const titleRow = `<div class="desk-v1-home-board-title">
      <h2 class="desk-v1-home-board-heading">The Desk</h2>
      <button type="button" class="btn-dispatch desk-v1-home-newcamp-page-btn">&#65291; New campaign</button>
    </div>`;
    const legend = `<div class="desk-v1-home-legend"><span class="desk-v1-home-legend-bar" aria-hidden="true"></span> bar = goal reached <span class="desk-v1-home-legend-sep" aria-hidden="true">|</span> tick = time elapsed</div>`;
    const colHead = `<div class="desk-v1-home-board-head">
      <div>CAMPAIGN</div><div>STAGE</div><div>GOAL PROGRESS</div><div>PACE</div><div>NEXT POST</div><div>NEEDS YOU</div>
    </div>`;
    host.innerHTML = titleRow + legend + _workerBannerHTML() + (projects.length
      ? colHead + `<div class="desk-v1-home-board-blocks">${projects.map(_projectBlockHTML).join('')}</div>`
      : '<div class="desk-v1-home-empty">No projects yet.</div>');
    _bindStatusBoard(host);
    _bindNewCampaignPageBtn(host);
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

  // ── render: promote box ───────────────────────────────────────────────
  function _submitPromote(ta) {
    const text = (ta && ta.value.trim()) || '';
    if (!text) return;
    if (ta) ta.value = '';
    const camp = _createProposedCampaign(text);
    DeskV1Kit.commandBus.run({
      label: `Proposed “${camp.plan.title}”`,
      do: () => { _fx().campaigns.push(camp); _renderStatusBoard(); },
      undo: () => { const arr = _fx().campaigns; const i = arr.findIndex((c) => c.id === camp.id); if (i >= 0) arr.splice(i, 1); _renderStatusBoard(); },
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

  // ── crumb-tools (Engagement · Settings) ──────────────────────────────────
  // R2-2c (Dave's review pass 4, §2's one-row header): the project scope
  // picker already lives in the shell's crumb on EVERY Desk route
  // (`_projectsPickerHTML`, desk-v1-shell.js) — Home used to duplicate it
  // with its own "Clayrune ▾" second row. Engagement + Settings now render
  // into the shell's shared #desk-v1-crumb-tools slot instead, the same
  // per-route pattern desk-v1-review.js's `_renderCrumbTools` already
  // established, so Home collapses into the ONE header row §2 specifies.
  // "Pause all" is dropped outright, not relocated: the project page already
  // has its own Pause project (desk-v1-project.js `_pauseProject`), and no
  // R2 frame or spec line gives Home a cross-project pause of its own.
  function _renderCrumbTools() {
    const host = document.getElementById('desk-v1-crumb-tools');
    if (!host) return;
    host.innerHTML = `
      <div class="desk-v1-home-crumbtools">
        <button type="button" class="desk-v1-home-engagement-btn">&#128172; Engagement${_engagementCountSuffix()}</button>
        <button type="button" class="desk-v1-home-settings-btn">&#9881; Settings</button>
      </div>`;
    const settingsBtn = host.querySelector('.desk-v1-home-settings-btn');
    if (settingsBtn) settingsBtn.onclick = () => { if (window.openSettings) window.openSettings(); };
    const engagementBtn = host.querySelector('.desk-v1-home-engagement-btn');
    if (engagementBtn) engagementBtn.onclick = () => deskV1Nav('engagement', {});
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
      _renderStatusBoard();
    }, HEARTBEAT_MS);
  }
  window.DESK_V1_HOME_HEARTBEAT_MS = HEARTBEAT_MS;
  // Test-only: forces one heartbeat re-check without waiting HEARTBEAT_MS.
  window.__deskV1HomeTickHeartbeatNow = () => _renderStatusBoard();

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
        <div class="desk-v1-home-board" id="desk-v1-home-board"></div>
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
    _renderCrumbTools();
    _bindPromoteBox(el);
    _renderStatusBoard();
    _renderShelves();
    _startHeartbeat(el);
    // Block headers resolve an agent per PROJECT (`_blockHeaderAgentHTML`),
    // outside any `.desk-v1-posy-box` — kit.js's own `_repaintDeskAgentBoxes`
    // can't reach them, so a fetch that resolves AFTER this paint (the
    // common case: /api/characters is still in flight on first Home render)
    // would leave every block header showing UNRESOLVED_AGENT_LABEL forever.
    DeskV1Kit.onAgentsReady(() => { if (document.body.contains(el)) _renderStatusBoard(); });
  }

  window.deskV1RenderHome = deskV1RenderHome;
  // T2a (docs/desk_v1_r0_plan.md) reuses this shelf pair for its Add tray —
  // "reuse, don't fork" — instead of duplicating the channel/material shelf
  // markup and drag wiring on desk-v1-campaign.js.
  window.deskV1RenderShelfPair = deskV1RenderShelfPair;
})();
