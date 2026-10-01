// Desk v1 (MC-977) — R2-8: Studio + the creation bodies What opens
// (docs/THE_DESK_V1_IA_REVISION_2.md §8 row R2-8, §11.4 item 6, §11.6 Q1;
// frames 11 to 14, 15b, 16, 16c). Window-bridged module, no `import`
// (ground rule 1). Three surfaces live here:
//
//   Studio home   route `studio`      `deskV1RenderStudio(el)`
//   Storyboard    route `storyboard`  `deskV1RenderStoryboard(el, {campaignId, familyId})`
//   bodies        `window.DeskV1Studio`, called by desk-v1-what.js: Record ·
//                 Capture · Online source · Generate · Create-new card bodies
//                 and the article writer (which renders inside the campaign
//                 frame, in place of the What list).
//
// Fixtures only: no capture backend, no connector and no renderer exists
// behind any of this. Capture / Record attach the fixture screen's own
// thumbnail; Online sources list fixture accounts and their Connect is
// disabled (§11.6 Q1: they render and behave, nothing authenticates or reads);
// Render sets `family.render` to a fixture `rendering 40%` and nothing ticks.
// Every mutation is a `DeskV1Kit.commandBus` command with an inverse (§10).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Fixtures || {}; }
  function _studio() { return _fx().studio || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }
  function _family(id) { return (_fx().families || []).find((f) => f.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _campTitle(camp) { return (camp && camp.plan && camp.plan.title) || 'Campaign'; }

  let _seq = 0;
  function _uid() { return Date.now().toString(36) + (++_seq).toString(36); }
  function _mmss(totalSec) { const m = Math.floor(totalSec / 60), s = Math.round(totalSec % 60); return m + ':' + String(s).padStart(2, '0'); }

  // The desk agent's name for a campaign, or null while unresolved (copy then
  // drops the "by <agent>" clause rather than naming a placeholder).
  function _agentName(camp) {
    return window.DeskV1Kit ? DeskV1Kit.deskAgentName({ project: _project(camp && camp.projectId), campaign: camp }, null) : null;
  }
  function _agentRec(camp) {
    if (!window.DeskV1Kit) return { name: null, avatar: '' };
    return DeskV1Kit.resolveDeskAgent(DeskV1Kit.deskAgentRef({ project: _project(camp && camp.projectId), campaign: camp }));
  }

  // ── Studio home (frame 11) ───────────────────────────────────────────────
  const NEW_TILES = [
    { id: 'video', label: 'New video', hint: 'Storyboard → render', glyph: '🎬' },
    { id: 'image', label: 'New image', hint: 'Capture or generate', glyph: '🖼' },
    { id: 'article', label: 'New article', hint: 'Draft with an agent', glyph: '📄' },
  ];
  const RECENT_ICON = { rendering: '⟳', article: '📄', image: '🖼', video: '🎬' };

  // Recent: the videos still rendering (read off the families so the percentage
  // is the What row's own number), then the fixture drafts that have no family.
  function _recentRows() {
    const rows = [];
    (_fx().families || []).forEach((f) => {
      if (!(f.render && f.render.status === 'rendering')) return;
      const camp = _campaign(f.campaignId);
      rows.push({ id: f.id, icon: RECENT_ICON.rendering, title: f.title, kind: f.kind, campaignId: f.campaignId,
        meta: `Rendering ${f.render.progress != null ? f.render.progress : 0}%${camp ? ' · ' + _campTitle(camp) : ''}`, state: 'rendering' });
    });
    (_studio().recent || []).forEach((r) => {
      const camp = _campaign(r.campaignId);
      const meta = r.status === 'draft'
        ? `Draft saved ${r.savedAgo || 'just now'}${camp ? ' · ' + _campTitle(camp) : ''}`
        : `Rendered · ${camp ? 'for ' + _campTitle(camp) : 'not attached'}`;
      rows.push({ id: r.id, icon: RECENT_ICON[r.kind] || '•', title: r.title, kind: r.kind, campaignId: r.campaignId, meta, state: r.status });
    });
    return rows;
  }

  function _studioHTML() {
    const rows = _recentRows();
    const lib = _studio().library || [];
    return `<div class="desk-v1-studio" data-studio>
      <h2 class="desk-v1-studio-title">Studio</h2>
      <div class="desk-v1-studio-tiles" role="group" aria-label="Start something new">${NEW_TILES.map((t) => `
        <button type="button" class="desk-v1-studio-tile" data-studio-new="${esc(t.id)}" aria-haspopup="menu">
          <span class="desk-v1-studio-tile-glyph" aria-hidden="true">${esc(t.glyph)}</span>
          <span class="desk-v1-studio-tile-name">${esc(t.label)}</span>
          <span class="desk-v1-studio-tile-hint">${esc(t.hint)}</span>
        </button>`).join('')}</div>
      <h3 class="desk-v1-studio-sub">Recent</h3>
      <div class="desk-v1-studio-recent" data-studio-recent>${rows.length ? rows.map((r) => `
        <button type="button" class="desk-v1-studio-recent-row" data-studio-recent-row="${esc(r.id)}" data-state="${esc(r.state)}"${r.campaignId ? ` data-campaign-id="${esc(r.campaignId)}"` : ''}>
          <span class="desk-v1-studio-recent-icon" aria-hidden="true">${esc(r.icon)}</span>
          <span class="desk-v1-studio-recent-main">
            <span class="desk-v1-studio-recent-title">${esc(r.title)} · ${esc(r.kind)}</span>
            <span class="desk-v1-studio-recent-meta">${esc(r.meta)}</span>
          </span>
        </button>`).join('') : '<div class="desk-v1-camp-empty">Nothing made yet.</div>'}</div>
      <h3 class="desk-v1-studio-sub">Material library</h3>
      <div class="desk-v1-studio-lib" data-studio-lib>${lib.map((m) => `
        <div class="desk-v1-studio-folder" data-studio-folder="${esc(m.id)}">
          <span class="desk-v1-studio-folder-glyph" aria-hidden="true">📁</span>
          <span class="desk-v1-studio-folder-name">${esc(m.title)}</span>
          <span class="desk-v1-studio-folder-count">${esc(m.files)} files</span>
        </div>`).join('')}</div>
    </div>`;
  }

  // A Studio tile makes a piece INSIDE a campaign (the piece lives on its What
  // list), so it first asks which one, then lands on that campaign's What with
  // the create-card already open.
  function _wireStudio(el) {
    el.querySelectorAll('[data-studio-new]').forEach((btn) => {
      btn.onclick = () => {
        const typeId = btn.dataset.studioNew;
        const camps = (_fx().campaigns || []).filter((c) => ['active', 'proposed', 'draft', 'paused'].includes(c.state));
        if (!camps.length) { DeskV1Kit.toast('Start a campaign first, then make pieces for it here.'); return; }
        DeskV1Kit.addToMenu(btn, camps.map((c) => ({ id: c.id, label: _campTitle(c) })), (campId) => {
          if (typeof window.deskV1WhatStartCreate === 'function') window.deskV1WhatStartCreate(campId, typeId);
          window.deskV1GotoCampaignPanel('what', { campaignId: campId });
        }, { noAppendNew: true });
      };
    });
    el.querySelectorAll('[data-studio-recent-row]').forEach((row) => {
      row.onclick = () => { if (row.dataset.campaignId) window.deskV1GotoCampaignPanel('what', { campaignId: row.dataset.campaignId }); };
    });
  }

  function deskV1RenderStudio(el) {
    el.innerHTML = _studioHTML();
    _wireStudio(el);
  }

  // ── Storyboard (frame 13) ────────────────────────────────────────────────
  // The scenes live on `videoDetail[familyId].scenes` — the same place the T5
  // director reads — with `line` / `source` / `thumb` added per scene.
  function ensureStoryboard(fam) {
    const vd = _fx().videoDetail || (_fx().videoDetail = {});
    const d = vd[fam.id] || (vd[fam.id] = { brief: '', materials: [], scenes: [], pendingEdits: [], jobs: [] });
    if (!d.scenes.length) {
      const isDefaultTitle = /^new video$/i.test(String(fam.title || '').trim());
      d.scenes = (_studio().storyboard || []).map((s, i) => ({
        id: 'sc-' + _uid(), label: s.label, durationSec: s.durationSec, source: s.source, thumb: s.thumb,
        line: i === 0 && !isDefaultTitle ? fam.title : (s.line || 'One installer, no admin prompt.'),
      }));
    }
    return d;
  }

  let _sb = null; // the mounted storyboard
  let _sbDrag = null;

  function _sbCtx() {
    if (!_sb) return null;
    const fam = _family(_sb.familyId);
    const camp = _campaign(_sb.campaignId);
    if (!fam || !camp) return null;
    return { fam, camp, detail: ensureStoryboard(fam) };
  }

  function _sceneHTML(s, i, editing) {
    const title = editing
      ? `<div class="desk-v1-sb-edit">
          <input type="text" class="desk-v1-sb-edit-input" data-scene-edit-label value="${esc(s.label)}" aria-label="Scene ${i + 1} title">
          <input type="text" class="desk-v1-sb-edit-input" data-scene-edit-line value="${esc(s.line || '')}" aria-label="Scene ${i + 1} line">
        </div>`
      : `<div class="desk-v1-sb-scenetitle" data-scene-title>Scene ${i + 1} · ${esc(s.label)}</div>
         <div class="desk-v1-sb-line">${esc(s.line || '')}</div>`;
    return `<li class="desk-v1-sb-scene" data-scene-id="${esc(s.id)}" data-scene-label="${esc(s.label)}">
      <button type="button" class="desk-v1-sb-handle" data-scene-handle aria-label="Move scene ${i + 1}: ${esc(s.label)}. Arrow Up or Arrow Down reorders" title="Drag, or press Arrow Up / Arrow Down">⠿</button>
      <div class="desk-v1-sb-thumb"><img src="${esc(s.thumb || '')}" alt="Capture for scene ${i + 1}"><span class="desk-v1-sb-num" data-scene-num>${i + 1}</span></div>
      <div class="desk-v1-sb-body">
        ${title}
        <div class="desk-v1-sb-source">${esc(s.source || '')}</div>
      </div>
      <div class="desk-v1-sb-meta">
        <span class="desk-v1-sb-dur">${esc(_mmss(s.durationSec || 0))}</span>
        <button type="button" class="desk-v1-sb-editbtn" data-scene-edit>${editing ? 'Done' : 'Edit'}</button>
      </div>
    </li>`;
  }

  function _renderStatusHTML(fam) {
    const r = fam.render;
    if (!(r && r.status === 'rendering')) return '';
    return `<div class="desk-v1-sb-rendering" data-sb-rendering>⟳ Rendering ${esc(r.progress != null ? r.progress : 0)}% — back on What the piece stays usable while it renders.</div>`;
  }

  function _sbHTML() {
    const ctx = _sbCtx();
    if (!ctx) return `<div class="desk-v1-stub"><div class="desk-v1-stub-body">This storyboard no longer exists.</div></div>`;
    const { fam, camp, detail } = ctx;
    const agent = _agentRec(camp);
    const agentName = agent.name || 'Your agent';
    const avatar = agent.name && typeof window.avatarHTML === 'function' ? window.avatarHTML(agent.avatar, 28) : '<span class="desk-v1-sb-agent-avatar" aria-hidden="true">🤖</span>';
    const rendering = fam.render && fam.render.status === 'rendering';
    return `<div class="desk-v1-sb" data-storyboard data-family-id="${esc(fam.id)}">
      <div class="desk-v1-sb-returning" data-returning-to>Returning to › <strong>${esc(_campTitle(camp))}</strong> <span>· What · ${esc(fam.title)}</span></div>
      <div class="desk-v1-sb-head">
        <h2 class="desk-v1-sb-title">New video · storyboard</h2>
        <button type="button" class="btn-add" data-sb-render${rendering ? ' disabled' : ''}>Render</button>
      </div>
      ${_renderStatusHTML(fam)}
      <div class="desk-v1-sb-layout">
        <ol class="desk-v1-sb-scenes" data-scenes aria-label="Scenes">${detail.scenes.map((s, i) => _sceneHTML(s, i, _sb.editing === s.id)).join('')}</ol>
        <aside class="desk-v1-sb-agent" data-sb-agent>
          <div class="desk-v1-sb-agent-head">${avatar}<strong>${esc(agentName)}</strong></div>
          <p class="desk-v1-sb-agent-text">Scenes are pulled from real product captures. Pick a scene and tell me what to change.</p>
          <input type="text" class="desk-v1-sb-agent-input" data-sb-ask placeholder="Ask ${esc(agentName)} to change a scene…" aria-label="Ask ${esc(agentName)} to change a scene">
        </aside>
      </div>
    </div>`;
  }

  function _paintScenes(focusSel) {
    if (!_sb || !_sb.el.isConnected) return;
    const ctx = _sbCtx();
    const ol = _sb.el.querySelector('[data-scenes]');
    if (!ctx || !ol) return;
    ol.innerHTML = ctx.detail.scenes.map((s, i) => _sceneHTML(s, i, _sb.editing === s.id)).join('');
    _wireScenes(ol);
    if (focusSel) { const f = _sb.el.querySelector(focusSel); if (f) f.focus({ preventScroll: true }); }
  }

  function _moveScene(fromId, toId) {
    const ctx = _sbCtx();
    if (!ctx || fromId === toId) return false;
    const arr = ctx.detail.scenes;
    const from = arr.findIndex((s) => s.id === fromId);
    const to = arr.findIndex((s) => s.id === toId);
    if (from < 0 || to < 0) return false;
    const before = arr.map((s) => s.id);
    const apply = (ids) => { ctx.detail.scenes = ids.map((id) => arr.find((s) => s.id === id)); };
    const after = before.slice();
    const [moved] = after.splice(from, 1);
    after.splice(to, 0, moved);
    const label = arr[from].label;
    DeskV1Kit.commandBus.run({
      label: `Moved scene “${label}”`,
      do: () => { apply(after); _paintScenes(`[data-scene-id="${fromId}"] [data-scene-handle]`); },
      undo: () => { apply(before); _paintScenes(); },
    });
    return true;
  }

  function _editScene(sceneId, patch) {
    const ctx = _sbCtx();
    const s = ctx && ctx.detail.scenes.find((x) => x.id === sceneId);
    if (!s) return;
    const prev = { label: s.label, line: s.line };
    if (prev.label === patch.label && prev.line === patch.line) return;
    DeskV1Kit.commandBus.run({
      label: `Edited scene “${prev.label}”`,
      do: () => { s.label = patch.label; s.line = patch.line; _paintScenes(); },
      undo: () => { s.label = prev.label; s.line = prev.line; _paintScenes(); },
    });
  }

  function _startRender() {
    const ctx = _sbCtx();
    if (!ctx) return;
    const { fam } = ctx;
    const prev = fam.render || null;
    DeskV1Kit.commandBus.run({
      label: `Started rendering “${fam.title}”`,
      do: () => { fam.render = { jobId: 'render-' + _uid(), status: 'rendering', revision: 1, progress: 40 }; _repaintSb(); },
      undo: () => { fam.render = prev; _repaintSb(); },
    });
  }

  function _repaintSb() {
    if (!_sb || !_sb.el.isConnected) return;
    _sb.el.innerHTML = _sbHTML();
    _wireSb(_sb.el);
  }

  function _sceneIdAt(x, y) {
    const hit = document.elementFromPoint(x, y);
    const li = hit && hit.closest && hit.closest('[data-scene-id]');
    return li && _sb.el.contains(li) ? li.dataset.sceneId : null;
  }

  function _beginSceneDrag(handle, e, sceneId) {
    if (!window.PointerDrag) return;
    window.PointerDrag.begin(handle, e, {
      isDragActive: () => !!_sbDrag,
      getDragState: () => _sbDrag,
      setDragState: (s) => { _sbDrag = s; },
      data: { kind: 'scene', sceneId },
      draggingClass: 'desk-v1-sb-dragging',
      activeBodyClass: 'desk-v1-sb-drag-active',
      ghostClass: 'pd-ghost desk-v1-home-drag-ghost',
      ghostHTML: () => `<span class="desk-v1-home-drag-ghost-inner">${esc((_sbCtx().detail.scenes.find((s) => s.id === sceneId) || {}).label || 'Scene')}</span>`,
      ghostRotationDeg: -2,
      ghostOffsetX: 18, ghostOffsetY: 18,
      onMove: (s, x, y) => {
        const over = _sceneIdAt(x, y);
        _sb.el.querySelectorAll('.desk-v1-sb-scene').forEach((li) => li.classList.toggle('pd-drop-hover', li.dataset.sceneId === over && over !== sceneId));
      },
      onDrop: (s, x, y) => { const over = _sceneIdAt(x, y); return over ? { over } : null; },
      afterDrop: (s, target) => { if (target) _moveScene(sceneId, target.over); },
      onTeardown: () => { _sb.el.querySelectorAll('.pd-drop-hover').forEach((li) => li.classList.remove('pd-drop-hover')); },
    });
  }

  function _wireScenes(ol) {
    const ctx = _sbCtx();
    if (!ctx) return;
    ol.querySelectorAll('.desk-v1-sb-scene').forEach((li) => {
      const id = li.dataset.sceneId;
      const handle = li.querySelector('[data-scene-handle]');
      handle.addEventListener('pointerdown', (e) => _beginSceneDrag(handle, e, id));
      handle.addEventListener('keydown', (e) => {
        if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
        e.preventDefault();
        const arr = _sbCtx().detail.scenes;
        const i = arr.findIndex((s) => s.id === id);
        const j = e.key === 'ArrowUp' ? i - 1 : i + 1;
        if (j < 0 || j >= arr.length) return;
        _moveScene(id, arr[j].id);
      });
      const edit = li.querySelector('[data-scene-edit]');
      edit.onclick = () => {
        if (_sb.editing === id) {
          const label = li.querySelector('[data-scene-edit-label]').value.trim();
          const line = li.querySelector('[data-scene-edit-line]').value.trim();
          const s = ctx.detail.scenes.find((x) => x.id === id);
          _sb.editing = null;
          _editScene(id, { label: label || s.label, line });
          _paintScenes(`[data-scene-id="${id}"] [data-scene-edit]`);
        } else {
          _sb.editing = id;
          _paintScenes(`[data-scene-id="${id}"] [data-scene-edit-label]`);
        }
      };
    });
  }

  function _wireSb(el) {
    const ctx = _sbCtx();
    if (!ctx) return;
    el.querySelector('[data-sb-render]').onclick = _startRender;
    _wireScenes(el.querySelector('[data-scenes]'));
    const ask = el.querySelector('[data-sb-ask]');
    ask.addEventListener('keydown', (e) => {
      if (e.key !== 'Enter') return;
      const text = ask.value.trim();
      if (!text) return;
      e.preventDefault();
      DeskV1Kit.toast(`Sent to ${_agentName(ctx.camp) || 'your agent'}: “${text}”`);
      ask.value = '';
    });
  }

  function deskV1RenderStoryboard(el, params) {
    params = params || {};
    _sb = { el, campaignId: params.campaignId, familyId: params.familyId, editing: null };
    el.innerHTML = _sbHTML();
    _wireSb(el);
  }

  // ── Source bodies What opens (frames 15b, 16, 16c) ───────────────────────
  function captureAvailable(projectId) {
    return !!((_studio().captureScreens || {})[projectId] || []).length;
  }
  function _screens(camp) { return (_studio().captureScreens || {})[camp && camp.projectId] || []; }
  function _ui(card) { return card.ui || (card.ui = {}); }

  function _screenBodyHTML(card, camp, verb) {
    const screens = _screens(camp);
    if (!screens.length) return `<div class="desk-v1-what-later" data-what-unavailable>Not available for this project</div>`;
    const ui = _ui(card);
    const cur = screens.find((s) => s.id === ui.screen) || screens[0];
    return `<div class="desk-v1-cap" data-cap>
      <div class="desk-v1-cap-bar">
        <label class="desk-v1-cap-label" for="cap-${esc(card.id)}">Screen:</label>
        <select class="desk-v1-cap-select" id="cap-${esc(card.id)}" data-cap-screen>${screens.map((s) =>
          `<option value="${esc(s.id)}"${s.id === cur.id ? ' selected' : ''}>${esc(s.label)}</option>`).join('')}</select>
      </div>
      <div class="desk-v1-cap-preview" data-cap-preview><img src="${esc(cur.thumb)}" alt="Preview of ${esc(cur.label)}"></div>
      <div class="desk-v1-cap-actions">
        <button type="button" class="btn-add" data-cap-take>${esc(verb)}</button>
        <span class="desk-v1-cap-note">Preview only: no capture backend yet, so this attaches the fixture screen.</span>
      </div>
    </div>`;
  }

  function _onlineBodyHTML(card, kind) {
    const accounts = (_studio().online || {})[kind === 'video' ? 'video' : 'image'] || [];
    const ui = _ui(card);
    const openId = ui.open !== undefined ? ui.open : ((accounts.find((a) => a.connected) || {}).id || null);
    return `<div class="desk-v1-online" data-online>${accounts.map((a) => {
      if (!a.connected) {
        return `<div class="desk-v1-online-warn" data-online-unconnected="${esc(a.id)}">
          <div class="desk-v1-online-warn-text">⚠ ${esc(a.label)} is not connected. Connecting grants Clayrune read access to that account. Review what gets shared before you connect —
            <button type="button" class="desk-v1-online-allows" data-online-allows aria-expanded="false">what this allows ›</button></div>
          <div class="desk-v1-online-scope" data-online-scope hidden>Preview of the scope: read-only. Clayrune would list the files in ${esc(a.label)} and copy the ones you pick; it would never post or change anything there.</div>
          <button type="button" class="desk-v1-online-connect" data-online-connect="${esc(a.id)}">Connect ${esc(a.label)} ›</button>
          <div class="desk-v1-online-later" data-online-later><span class="desk-v1-where-preview" data-preview>Preview · not connected</span> Connect it on the Connections screen.</div>
        </div>`;
      }
      const open = openId === a.id;
      return `<div class="desk-v1-online-acct" data-online-acct="${esc(a.id)}">
        <button type="button" class="desk-v1-online-head" data-online-toggle="${esc(a.id)}" aria-expanded="${open}">
          <span class="desk-v1-online-avatar" data-tone="${esc(a.tone || '')}" aria-hidden="true">${esc(a.glyph)}</span>
          <span class="desk-v1-online-name"><strong>${esc(a.label)}</strong><span>${esc(a.account)} · connected</span></span>
          <span class="desk-v1-where-preview" data-preview>Preview</span>
          <span class="desk-v1-online-caret" aria-hidden="true">${open ? '▾' : '▸'}</span>
        </button>
        ${open ? `<div class="desk-v1-online-grid" data-online-grid>${(a.thumbs || []).map((t, i) => `
          <button type="button" class="desk-v1-online-thumb" data-online-pick="${esc(a.id)}:${i}" aria-label="Use ${esc(a.label)} ${kind} ${i + 1}"><img src="${esc(t)}" alt=""></button>`).join('')}</div>` : ''}
      </div>`;
    }).join('')}
      <button type="button" class="desk-v1-online-another" data-online-another disabled aria-disabled="true">+ Connect another source</button>
    </div>`;
  }

  function _generateBodyHTML() {
    return `<div class="desk-v1-gen" data-gen>
      <p class="desk-v1-gen-rule" data-gen-abstract-only>Abstract visuals only — never the product UI.</p>
      <div class="desk-v1-gen-row">
        <input type="text" class="desk-v1-gen-prompt" data-gen-prompt placeholder="Describe an abstract visual: colours, mood, shapes" aria-label="Describe an abstract visual">
        <button type="button" class="btn-add" data-gen-go>Generate</button>
      </div>
    </div>`;
  }

  function _createBodyHTML(fam) {
    const n = ((_fx().videoDetail || {})[fam.id] || {}).scenes;
    return `<div class="desk-v1-what-later" data-what-storyboard>${n && n.length ? `Storyboard · ${n.length} scenes.` : 'Storyboard not started.'}
      <button type="button" class="btn-secondary" data-what-open-storyboard>Open storyboard ›</button></div>`;
  }

  // What's source body for a source this module owns, or null for the rest.
  function sourceBodyHTML(source, ctx) {
    const { card, fam, camp } = ctx;
    if (source === 'capture') return _screenBodyHTML(card, camp, 'Capture this screen');
    if (source === 'record') return _screenBodyHTML(card, camp, 'Record this screen');
    if (source === 'online') return _onlineBodyHTML(card, fam.kind);
    if (source === 'generate') return _generateBodyHTML();
    if (source === 'create') return _createBodyHTML(fam);
    return null;
  }

  // Abstract generated art: soft gradient blobs seeded by the prompt. Nothing
  // here can depict the product (standing position: generated imagery is for
  // abstract visuals only).
  function _abstractSvg(seed) {
    let h = 2166136261;
    for (const ch of String(seed || 'abstract')) { h ^= ch.charCodeAt(0); h = Math.imul(h, 16777619) >>> 0; }
    const hue = (n) => (h >>> n) % 360;
    const a = hue(0), b = hue(7), c = hue(13);
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 200"><defs>` +
      `<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${a},70%,42%)"/><stop offset="1" stop-color="hsl(${b},70%,28%)"/></linearGradient></defs>` +
      `<rect width="320" height="200" fill="url(#g)"/>` +
      `<circle cx="${60 + (h % 120)}" cy="${50 + (h % 70)}" r="${46 + (h % 30)}" fill="hsl(${c},80%,60%)" opacity=".45"/>` +
      `<circle cx="${200 + (h % 90)}" cy="${120 + (h % 50)}" r="${38 + (h % 26)}" fill="hsl(${b},80%,65%)" opacity=".4"/></svg>`;
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  }

  // api: { attach(asset), repaint(), openStoryboard() }
  function wireSourceBody(cardEl, ctx, api) {
    const { card, fam, camp } = ctx;
    const ui = _ui(card);
    const kind = fam.kind === 'video' ? 'video' : 'image';

    const sel = cardEl.querySelector('[data-cap-screen]');
    if (sel) sel.onchange = () => {
      ui.screen = sel.value;
      const s = _screens(camp).find((x) => x.id === sel.value);
      const img = cardEl.querySelector('[data-cap-preview] img');
      if (s && img) { img.src = s.thumb; img.alt = `Preview of ${s.label}`; }
    };
    const take = cardEl.querySelector('[data-cap-take]');
    if (take) take.onclick = () => {
      const screens = _screens(camp);
      const s = screens.find((x) => x.id === ui.screen) || screens[0];
      if (!s) return;
      api.attach({ id: 'asset-' + _uid(), kind, title: `${kind === 'video' ? 'Screen recording' : 'Capture'} · ${s.label}`, src: s.thumb });
    };

    cardEl.querySelectorAll('[data-online-toggle]').forEach((b) => b.onclick = () => {
      ui.open = b.getAttribute('aria-expanded') === 'true' ? null : b.dataset.onlineToggle;
      api.repaint();
    });
    cardEl.querySelectorAll('[data-online-pick]').forEach((b) => b.onclick = () => {
      const [acctId, idx] = b.dataset.onlinePick.split(':');
      const acct = ((_studio().online || {})[kind] || []).find((a) => a.id === acctId);
      if (!acct) return;
      api.attach({ id: 'asset-' + _uid(), kind, title: `${acct.label} · ${kind} ${Number(idx) + 1}`, src: (acct.thumbs || [])[Number(idx)] || null });
    });
    // Connecting happens on the Connections screen only (MC-977 2026-10-01).
    cardEl.querySelectorAll('[data-online-connect]').forEach((b) => b.onclick = () => window.deskV1Nav('connections'));
    cardEl.querySelectorAll('[data-online-allows]').forEach((b) => b.onclick = () => {
      const scope = b.closest('[data-online-unconnected]').querySelector('[data-online-scope]');
      const show = scope.hidden;
      scope.hidden = !show;
      b.setAttribute('aria-expanded', String(show));
    });

    const gen = cardEl.querySelector('[data-gen-go]');
    if (gen) gen.onclick = () => {
      const prompt = (cardEl.querySelector('[data-gen-prompt]').value || '').trim() || 'Abstract visual';
      api.attach({ id: 'asset-' + _uid(), kind: 'image', title: `Abstract · ${prompt}`, src: _abstractSvg(prompt) });
    };

    const open = cardEl.querySelector('[data-what-open-storyboard]');
    if (open) open.onclick = () => api.openStoryboard();
  }

  // ── Article writer (frame 12) ────────────────────────────────────────────
  const TAB_LABEL = { x: 'X thread', linkedin: 'LinkedIn article', blog: 'Blog post' };
  function _tabLabel(ch) { return ch ? (TAB_LABEL[ch.platform] || ch.label || ch.identity) : 'Draft'; }

  const DRAFT_BODY = {
    x: (t) => `1/ Why does this matter? Windows onboarding still throws a [[cl-smartscreen|SmartScreen warning]] on first launch — we want real machines to prove the fix.\n` +
      `2/ What we're asking: install, run one project for a week, tell us where it broke.\n3/ [[cl-testers|30 testers]] gets us enough signal to ship the signed installer with confidence.`,
    default: (t) => `${t}\n` +
      `[[cl-friction|Install friction is the #1 churn driver]] for Windows testers, and the first thing they meet is a [[cl-smartscreen|SmartScreen warning]] on first launch.\n` +
      `We want [[cl-testers|30 testers]] to install, run one project for a week, and tell us where it broke.`,
  };

  // One tab per destination version; a piece with no versions yet gets one tab
  // per account the campaign uses (x / linkedin / blog, first two), as plain
  // drafts — placing a piece on an account is Where's job, not the writer's.
  function ensureDraft(fam, camp) {
    if (fam.draft && fam.draft.tabs && fam.draft.tabs.length) return fam.draft;
    let targets = (fam.versions || []).filter((v) => v.channelId && v.state !== 'archived' && v.state !== 'skipped')
      .map((v) => ({ id: v.id, platform: (_channel(v.channelId) || {}).platform, label: _tabLabel(_channel(v.channelId)) }));
    if (!targets.length) {
      const seen = new Set();
      targets = ((camp.plan && camp.plan.accounts) || []).map((a) => _channel(typeof a === 'string' ? a : (a && a.channel_id)))
        .filter((ch) => ch && TAB_LABEL[ch.platform] && !seen.has(ch.platform) && seen.add(ch.platform))
        .slice(0, 2).map((ch) => ({ id: 'tab-' + ch.id, platform: ch.platform, label: _tabLabel(ch) }));
    }
    if (!targets.length) targets = [{ id: 'tab-draft', platform: 'default', label: 'Draft' }];
    fam.draft = { status: 'drafting', tabs: targets.map((t) => ({ id: t.id, label: t.label, body: (DRAFT_BODY[t.platform] || DRAFT_BODY.default)(fam.title) })) };
    return fam.draft;
  }

  const CLAIM_RE = /\[\[([a-z0-9-]+)\|([^\]]+)\]\]/g;
  function _claimsIn(body) {
    const out = []; const seen = new Set(); let m;
    CLAIM_RE.lastIndex = 0;
    while ((m = CLAIM_RE.exec(body))) { if (!seen.has(m[1])) { seen.add(m[1]); out.push({ id: m[1], text: m[2] }); } }
    return out;
  }
  function _bodyHTML(body) {
    return String(body).split('\n').map((line) => {
      let html = ''; let last = 0; let m;
      CLAIM_RE.lastIndex = 0;
      while ((m = CLAIM_RE.exec(line))) {
        html += esc(line.slice(last, m.index));
        const claim = (_studio().claims || {})[m[1]];
        const assumed = claim && !claim.source;
        html += `<span class="desk-v1-claim${assumed ? ' desk-v1-claim-assumed' : ''}" data-claim="${esc(m[1])}" contenteditable="false">${esc(m[2])}</span>` +
          (assumed ? '<span class="desk-v1-claim-chip" data-claim-chip contenteditable="false">? Assumed</span>' : '');
        last = m.index + m[0].length;
      }
      html += esc(line.slice(last));
      return `<p>${html || '<br>'}</p>`;
    }).join('');
  }
  // contenteditable DOM back to the marker text it came from.
  function _serialize(root) {
    const lines = [];
    root.childNodes.forEach((blk) => {
      let line = '';
      const walk = (n) => {
        if (n.nodeType === 3) { line += n.nodeValue; return; }
        if (n.nodeType !== 1) return;
        if (n.matches && n.matches('[data-claim-chip]')) return;
        if (n.dataset && n.dataset.claim) { line += `[[${n.dataset.claim}|${n.textContent}]]`; return; }
        if (n.tagName === 'BR') return;
        n.childNodes.forEach(walk);
      };
      walk(blk);
      lines.push(line);
    });
    return lines.join('\n');
  }

  function _claimsPanelHTML(body) {
    const claims = _claimsIn(body);
    return `<h3 class="desk-v1-writer-claims-title">Claims &amp; sources</h3>` + (claims.length ? claims.map((c) => {
      const meta = (_studio().claims || {})[c.id] || { text: c.text, source: null };
      const assumed = !meta.source;
      return `<div class="desk-v1-writer-claim" data-claim-row="${esc(c.id)}">
        <div class="desk-v1-writer-claim-text">“${esc(meta.text)}”${assumed ? ' <span class="desk-v1-claim-chip">? Assumed</span>' : ''}</div>
        <div class="desk-v1-writer-claim-src">${assumed ? esc(meta.note || 'not yet verified') : 'source: ' + esc(meta.source)}</div>
      </div>`;
    }).join('') : '<div class="desk-v1-camp-empty">No factual claims in this draft.</div>');
  }

  function writerHTML(fam, camp, tabIdx) {
    const draft = ensureDraft(fam, camp);
    const i = Math.min(Math.max(tabIdx || 0, 0), draft.tabs.length - 1);
    const tab = draft.tabs[i];
    const agent = _agentName(camp);
    const how = (camp.how && (camp.how.angle || camp.how.strategy)) || '';
    const prov = `Drafted${agent ? ' by ' + esc(agent) : ''} from this campaign's How${how ? ` (positioning: “${esc(how)}”)` : ''}.`;
    return `<div class="desk-v1-writer" data-writer data-family-id="${esc(fam.id)}">
      <div class="desk-v1-writer-main">
        <div class="desk-v1-writer-tabs" role="tablist" aria-label="Destination versions">${draft.tabs.map((t, n) =>
          `<button type="button" role="tab" class="desk-v1-writer-tab" data-writer-tab="${n}" aria-selected="${n === i}">${esc(t.label)}</button>`).join('')}</div>
        <div class="desk-v1-writer-card">
          <div class="desk-v1-writer-title">${esc(fam.title)}</div>
          <div class="desk-v1-writer-prov" data-writer-provenance>${prov}</div>
          <div class="desk-v1-writer-body" data-writer-body contenteditable="true" role="textbox" aria-multiline="true" aria-label="${esc(tab.label)} draft">${_bodyHTML(tab.body)}</div>
        </div>
        <div class="desk-v1-writer-actions">
          <button type="button" class="btn-add" data-writer-save>Save to What</button>
          <button type="button" class="btn-secondary" data-writer-back>Back to What</button>
        </div>
      </div>
      <aside class="desk-v1-writer-claims" data-writer-claims aria-label="Claims and sources">${_claimsPanelHTML(tab.body)}</aside>
    </div>`;
  }

  // handlers: { onTab(i), onSave(), onBack() }
  function wireWriter(root, fam, camp, tabIdx, handlers) {
    const draft = ensureDraft(fam, camp);
    const tab = draft.tabs[Math.min(Math.max(tabIdx || 0, 0), draft.tabs.length - 1)];
    root.querySelectorAll('[data-writer-tab]').forEach((b) => b.onclick = () => handlers.onTab(Number(b.dataset.writerTab)));
    const body = root.querySelector('[data-writer-body]');
    body.addEventListener('input', () => {
      tab.body = _serialize(body);
      root.querySelector('[data-writer-claims]').innerHTML = _claimsPanelHTML(tab.body);
    });
    root.querySelector('[data-writer-save]').onclick = handlers.onSave;
    root.querySelector('[data-writer-back]').onclick = handlers.onBack;
  }

  // Save to What: the draft is handed to review. Placed versions still in
  // drafting / planned become `needs_review`; a piece with none carries the
  // state on its draft. Returns the inverse.
  function markInReview(fam) {
    const draft = fam.draft;
    const prev = { status: draft.status, states: (fam.versions || []).map((v) => [v, v.state]) };
    draft.status = 'in_review';
    (fam.versions || []).forEach((v) => { if (v.state === 'drafting' || v.state === 'planned') v.state = 'needs_review'; });
    return () => { draft.status = prev.status; prev.states.forEach(([v, s]) => { v.state = s; }); };
  }

  window.deskV1RenderStudio = deskV1RenderStudio;
  window.deskV1RenderStoryboard = deskV1RenderStoryboard;
  window.DeskV1Studio = {
    captureAvailable, sourceBodyHTML, wireSourceBody,
    ensureStoryboard, ensureDraft, writerHTML, wireWriter, markInReview,
  };
})();
