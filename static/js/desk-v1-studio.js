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
// Live capture is owned by desk-v1-studio-capture.js; the fixture Screen
// body below is used by demo capture and the unchanged Record source.
// Online sources list fixture accounts and their Connect is
// disabled (§11.6 Q1: they render and behave, nothing authenticates or reads);
// Render sets `family.render` to a fixture `rendering 40%` and nothing ticks.
// Every mutation is a `DeskV1Kit.commandBus` command with an inverse (§10).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _studio() { return _fx().studio || {}; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }
  // A standalone Studio video (MC-1024) is its own storyboard owner: the draft
  // being worked on is found before the campaign pieces, so the scene commands
  // below run unchanged on it.
  function _family(id) {
    if (_sc && _sc.item && _sc.item.id === id) return _sc.item;
    return _items().find((f) => f.id === id) || (_fx().families || []).find((f) => f.id === id) || null;
  }
  function _isLive() { return window.DeskV1Store.live(); }
  // What Studio itself made, newest first. Demo: on the fixture's `studio`;
  // live: on the hydrated state, so it lasts the session (the files last).
  function _items() { const s = _studio(); return s.items || (s.items = []); }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _campTitle(camp) { return (camp && camp.plan && camp.plan.title) || 'Campaign'; }

  let _sc = null; // the mounted standalone creation page (MC-1024)
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

  // A standalone video's agent (MC-1024 follow-up): the one picked on the item
  // (`item.agent`, a `scope:name` ref), else what the box always resolved.
  // Only agents provisioned on ALL projects are offered, i.e. characters with
  // scope global, read fresh from GET /api/characters each time a storyboard
  // opens so one created since still shows. null until that read lands.
  let _globalAgents = null;
  function _loadGlobalAgents() {
    return fetch('/api/characters').then((r) => r.json()).then((list) => {
      _globalAgents = (Array.isArray(list) ? list : [])
        .filter((c) => c.scope === 'global')
        .map((c) => ({ ref: `global:${c.name}`, name: c.agent_name || c.display_name || c.name, avatar: c.avatar || '' }));
    }).catch((e) => { console.warn('[desk] could not read the agent list:', e && e.message ? e.message : e); if (!_globalAgents) _globalAgents = []; });
  }
  function _pickedAgent(fam) {
    if (!fam || !fam.agent) return null;
    const hit = (_globalAgents || []).find((a) => a.ref === fam.agent);
    if (hit) return { ref: hit.ref, name: hit.name, avatar: hit.avatar };
    return window.DeskV1Kit ? DeskV1Kit.resolveDeskAgent(fam.agent) : null;
  }
  function _sbAgent(ctx) {
    const picked = _sb && _sb.standalone ? _pickedAgent(ctx.fam) : null;
    return picked && picked.name ? picked : _agentRec(ctx.camp);
  }

  // ── Studio home (frame 11) ───────────────────────────────────────────────
  const NEW_TILES = [
    { id: 'video', label: 'New video', hint: 'Storyboard → render', glyph: '🎬' },
    { id: 'image', label: 'New image', hint: 'Capture or generate', glyph: '🖼' },
    { id: 'article', label: 'New article', hint: 'Draft with an agent', glyph: '📄' },
  ];
  const RECENT_ICON = { rendering: '⟳', article: '📄', image: '🖼', video: '🎬' };

  // The material library behind Studio home, read live from M22 (demo shows the
  // fixture's own folders). `error` is a real failure shown as one, never an
  // empty library.
  const _lib = { error: null, recent: [], boards: [], loaded: false };

  // Studio's own items: what it made (a standalone item has no campaign), as
  // Recent rows. A video still rendering reads its percentage off the item.
  function _itemMeta(it) {
    if (it.render && it.render.status === 'rendering') return { state: 'rendering', meta: `Rendering ${it.render.progress != null ? it.render.progress : 0}% · not attached` };
    if (it.status === 'saved') return { state: 'saved', meta: 'Saved to the Material library · not attached' };
    return { state: 'draft', meta: 'Draft saved just now · not attached' };
  }

  // Recent: Studio's own items, the videos still rendering (read off the
  // families so the percentage is the What row's own number), then the fixture
  // drafts that have no family. Live, the newest library files stand in for the
  // fixture drafts.
  function _recentRows() {
    const rows = [];
    _items().forEach((it) => {
      const m = _itemMeta(it);
      rows.push({ src: 'item', path: it.path || null, id: it.id, icon: m.state === 'rendering' ? RECENT_ICON.rendering : (RECENT_ICON[it.kind] || '•'), title: it.title, kind: it.kind, campaignId: null, meta: m.meta, state: m.state });
    });
    (_fx().families || []).forEach((f) => {
      if (!(f.render && f.render.status === 'rendering')) return;
      const camp = _campaign(f.campaignId);
      rows.push({ src: 'family', id: f.id, icon: RECENT_ICON.rendering, title: f.title, kind: f.kind, campaignId: f.campaignId,
        meta: `Rendering ${f.render.progress != null ? f.render.progress : 0}%${camp ? ' · ' + _campTitle(camp) : ''}`, state: 'rendering' });
    });
    if (window.DeskV1StudioArticle) window.DeskV1StudioArticle.rows().forEach((r) => rows.push(r));
    (_studio().recent || []).forEach((r) => {
      const camp = _campaign(r.campaignId);
      const meta = r.status === 'draft'
        ? `Draft saved ${r.savedAgo || 'just now'}${camp ? ' · ' + _campTitle(camp) : ''}`
        : `Rendered · ${camp ? 'for ' + _campTitle(camp) : 'not attached'}`;
      rows.push({ src: 'fixture', id: r.id, icon: RECENT_ICON[r.kind] || '•', title: r.title, kind: r.kind, campaignId: r.campaignId, meta, state: r.status });
    });
    if (_isLive()) {
      const mine = new Set(_items().map((it) => it.id));
      _lib.boards.filter((b) => !mine.has(b.id)).forEach((b) => rows.push({
        src: 'board', id: b.id, icon: RECENT_ICON.video || '•', title: b.title || 'New video', kind: 'video', campaignId: null,
        meta: `Draft · ${b.scenes} scene${b.scenes === 1 ? '' : 's'} · not attached`, state: 'draft' }));
      const seen = new Set(_items().map((it) => it.path).filter(Boolean));
      _lib.recent.filter((r) => !seen.has(r.path)).forEach((r) => rows.push({
        src: 'file', path: r.path, id: r.path, icon: RECENT_ICON[r.kind] || '•', title: r.title, kind: r.kind, campaignId: null,
        meta: 'Saved to the Material library · not attached', state: 'saved' }));
    }
    return rows;
  }

  // Studio home's library shelf: demo reads the fixture's folders, plus a
  // `Studio` folder once something was saved; live reads M22's folders.
  function _libFolders() {
    if (_isLive()) {
      const ml = _fx().materialLibrary || {};
      return ['video', 'image'].flatMap((k) => (ml[k] || []).map((m) => ({ id: m.id, title: m.title, files: m.files, items: m.items })));
    }
    const made = _items().filter((it) => it.status === 'saved');
    return (_studio().library || []).concat(made.length ? [{ id: 'lib-studio', title: 'Studio', files: made.length, items: made }] : []);
  }

  // A folder's files for the open-folder view. Live: M22's own `items[]`; demo:
  // the fixture folder's `items`, and the `Studio` folder is what Studio saved.
  let _libOpen = null; // the folder open on Studio home, or null for the shelf
  function _folderFiles(f) {
    return (f.items || []).map((it) => ({ id: it.id, title: it.title, src: it.src || null, kind: it.kind, play: it.play || null, path: it.path || null }));
  }

  function _libHTML() {
    if (_isLive() && _lib.error) return `<div class="desk-v1-camp-empty" data-studio-lib-error>Could not load the material library: ${esc(_lib.error)}</div>`;
    const lib = _libFolders();
    const open = _libOpen && lib.find((m) => m.id === _libOpen);
    if (open) {
      const files = _folderFiles(open);
      return `<div class="desk-v1-studio-folderview" data-studio-folderview="${esc(open.id)}">
        <div class="desk-v1-studio-folderbar">
          <button type="button" class="desk-v1-studio-folder-back" data-studio-lib-back>‹ Material library</button>
          <span class="desk-v1-studio-folderbar-name">${esc(open.title)} · ${files.length} ${files.length === 1 ? 'file' : 'files'}</span>
        </div>
        ${files.length ? `<div class="desk-v1-studio-files" data-studio-files>${files.map((f) => `
          <div class="desk-v1-studio-file" data-studio-file="${esc(f.id)}">
            <span class="desk-v1-studio-file-thumb">${f.src ? `<img src="${esc(f.src)}" alt="" loading="lazy">` : `<span aria-hidden="true">${f.kind === 'video' ? '▶' : '🖼'}</span>`}</span>
            <span class="desk-v1-studio-file-name">${esc(f.title)}</span>
          </div>`).join('')}</div>` : '<div class="desk-v1-camp-empty" data-studio-folder-empty>No files in this folder yet.</div>'}
      </div>`;
    }
    return lib.map((m) => `
        <button type="button" class="desk-v1-studio-folder" data-studio-folder="${esc(m.id)}">
          <span class="desk-v1-studio-folder-glyph" aria-hidden="true">📁</span>
          <span class="desk-v1-studio-folder-name">${esc(m.title)}</span>
          <span class="desk-v1-studio-folder-count">${esc(m.files)} files</span>
        </button>`).join('');
  }

  // The accessors Recent's delete (desk-v1-studio-delete.js) works through.
  const _recentCtx = {
    isLive: _isLive,
    items: _items,
    lib: () => _lib,
    fixtureRecent: () => _studio().recent || [],
    articles: () => (window.DeskV1StudioArticle ? window.DeskV1StudioArticle.list() : []),
    campaignTitle: (id) => { const c = _campaign(id); return c ? _campTitle(c) : 'a campaign'; },
    el: () => _studioEl,
    repaint: () => { if (_studioHomeOnScreen(_studioEl)) { _studioEl.innerHTML = _studioHTML(); _wireStudio(_studioEl); } },
    reload: () => _loadLibrary().then(() => _recentCtx.repaint()),
  };
  let _rows = [];

  function _studioHTML() {
    const rows = _rows = _recentRows();
    return `<div class="desk-v1-studio" data-studio>
      <h2 class="desk-v1-studio-title">Studio</h2>
      <div class="desk-v1-studio-tiles" role="group" aria-label="Start something new">${NEW_TILES.map((t) => `
        <button type="button" class="desk-v1-studio-tile" data-studio-new="${esc(t.id)}">
          <span class="desk-v1-studio-tile-glyph" aria-hidden="true">${esc(t.glyph)}</span>
          <span class="desk-v1-studio-tile-name">${esc(t.label)}</span>
          <span class="desk-v1-studio-tile-hint">${esc(t.hint)}</span>
        </button>`).join('')}</div>
      <h3 class="desk-v1-studio-sub">Recent</h3>
      <div class="desk-v1-studio-recent" data-studio-recent>${rows.length ? rows.map((r) => `
        <div class="desk-v1-studio-recent-item" data-studio-recent-item="${esc(r.id)}">
          <button type="button" class="desk-v1-studio-recent-row" data-studio-recent-row="${esc(r.id)}" data-state="${esc(r.state)}"${r.campaignId ? ` data-campaign-id="${esc(r.campaignId)}"` : ''}>
            <span class="desk-v1-studio-recent-icon" aria-hidden="true">${esc(r.icon)}</span>
            <span class="desk-v1-studio-recent-main">
              <span class="desk-v1-studio-recent-title">${esc(r.title)} · ${esc(r.kind)}</span>
              <span class="desk-v1-studio-recent-meta">${esc(r.meta)}</span>
            </span>
          </button>
          ${window.DeskV1StudioDelete ? window.DeskV1StudioDelete.buttonHTML(r, _recentCtx) : ''}
        </div>`).join('') : '<div class="desk-v1-camp-empty">Nothing made yet.</div>'}</div>
      <h3 class="desk-v1-studio-sub">Material library</h3>
      <div class="desk-v1-studio-lib" data-studio-lib>${_libHTML()}</div>
    </div>`;
  }

  // Studio is a standalone workshop (Ron 2026-10-01, MC-1024): the Video, Image
  // and Article tiles open their creation page directly, with no campaign. What
  // they make is kept in Studio (Recent, the Material library) and used on a
  // campaign later. The article page is desk-v1-studio-article.js (2026-10-06).
  function _paintLib(el) {
    const box = el.querySelector('[data-studio-lib]');
    if (!box) return;
    box.innerHTML = _libHTML();
    box.classList.toggle('desk-v1-studio-lib-open', !!box.querySelector('[data-studio-folderview]'));
    _wireLib(el);
  }

  // A folder opens in place on the shelf, with a way back to the folders.
  function _wireLib(el) {
    const shown = _libOpen && _libFolders().find((m) => m.id === _libOpen);
    if (shown && window.DeskV1LibraryViewer) window.DeskV1LibraryViewer.wireShelf(el, _folderFiles(shown));
    el.querySelectorAll('[data-studio-folder]').forEach((b) => {
      b.onclick = () => {
        _libOpen = b.dataset.studioFolder;
        _paintLib(el);
        const back = el.querySelector('[data-studio-lib-back]');
        if (back) back.focus({ preventScroll: true });
      };
    });
    const back = el.querySelector('[data-studio-lib-back]');
    if (back) back.onclick = () => {
      const id = _libOpen;
      _libOpen = null;
      _paintLib(el);
      const f = el.querySelector(`[data-studio-folder="${CSS.escape(id)}"]`);
      if (f) f.focus({ preventScroll: true });
    };
  }

  function _wireStudio(el) {
    _wireLib(el);
    el.querySelectorAll('[data-studio-new]').forEach((btn) => {
      btn.onclick = () => {
        window.deskV1Nav('studio-create', { kind: btn.dataset.studioNew });
      };
    });
    // A row with a campaign goes to that campaign's What; one with none is a
    // Studio item, so it opens the item, not a campaign.
    el.querySelectorAll('[data-studio-recent-row]').forEach((row) => {
      row.onclick = () => {
        if (row.dataset.campaignId) window.deskV1GotoCampaignPanel('what', { campaignId: row.dataset.campaignId });
        else window.deskV1Nav('studio-create', { itemId: row.dataset.studioRecentRow });
      };
    });
    if (window.DeskV1StudioDelete) window.DeskV1StudioDelete.wire(el, _rows, _recentCtx);
  }

  function _campaignsToUse() {
    return (_fx().campaigns || []).filter((c) => ['active', 'proposed', 'draft', 'paused'].includes(c.state));
  }

  let _studioEl = null;
  function deskV1RenderStudio(el) {
    _studioEl = el;
    _libOpen = null;
    el.innerHTML = _studioHTML();
    _wireStudio(el);
    if (_isLive()) _loadLibrary().then(() => {
      if (_studioEl === el && _studioHomeOnScreen(el)) { el.innerHTML = _studioHTML(); _wireStudio(el); }
    });
  }

  // The route container is shared: `studio-create` (or any other route) paints into
  // the SAME element the Studio home used. A library read that lands after the user
  // has already opened New video must not paint the home back over it, so a late
  // repaint only happens while the Studio home is still what the element shows.
  function _studioHomeOnScreen(el) {
    return !!el && el.isConnected && !!el.querySelector(':scope > [data-studio]');
  }

  // M22, read when Studio home opens live. The same read hands What its library,
  // so the cached copy on `materialLibrary` is the one both show.
  function _loadLibrary() {
    return window.DeskV1Store.api('GET', '/api/desk/materials').then((m) => {
      _fx().materialLibrary = (m && m.library) || { video: [], image: [] };
      const lib = _fx().materialLibrary;
      const all = ['video', 'image'].flatMap((k) => (lib[k] || []).flatMap((f) => f.items || []));
      _lib.recent = ((m && m.recent) || []).map((r) => { const hit = all.find((i) => i.path === r.id); return { kind: r.kind, title: r.title, path: r.id, src: hit ? hit.src : null }; });
      _lib.error = null;
      // Live Studio drafts the server holds a storyboard for (they outlive the page).
      return window.DeskV1Store.api('GET', '/api/desk/studio/storyboards').then((b) => { _lib.boards = (b && b.storyboards) || []; })
        .then(() => window.DeskV1StudioArticle && window.DeskV1StudioArticle.load())
        .then(() => window.DeskV1StudioDelete && window.DeskV1StudioDelete.loadUsage());
    }).catch((e) => { _lib.error = e && e.message ? e.message : String(e); });
  }

  // ── Storyboard (frame 13) ────────────────────────────────────────────────
  // The scenes live on `videoDetail[familyId].scenes` — the same place the T5
  // director reads — with `line` / `source` / `thumb` added per scene.
  //
  // A standalone Studio video opens with the same four samples, but only scene 1
  // is a real scene: 2 to 4 are `placeholder` examples (dimmed, left out of the
  // render and the saved item) until the user edits them.
  function ensureStoryboard(fam) {
    const vd = _fx().videoDetail || (_fx().videoDetail = {});
    const d = vd[fam.id] || (vd[fam.id] = { brief: '', materials: [], scenes: [], pendingEdits: [], jobs: [] });
    if (!d.scenes.length) {
      const isDefaultTitle = /^new video$/i.test(String(fam.title || '').trim());
      const standalone = !!(_sc && _sc.item && _sc.item.id === fam.id);
      d.scenes = (_studio().storyboard || []).map((s, i) => ({
        id: 'sc-' + _uid(), label: s.label, durationSec: s.durationSec, source: s.source, thumb: s.thumb,
        line: i === 0 && !isDefaultTitle ? fam.title : (s.line || 'One installer, no admin prompt.'),
        placeholder: standalone && i > 0,
      }));
    }
    return d;
  }
  function _isReal(s) { return !s.placeholder; }
  function _realScenes(detail) { return detail.scenes.filter(_isReal); }
  // What Render and the saved item carry: the real scenes only.
  function _scenePayload(detail) {
    return _realScenes(detail).map((s) => ({ id: s.id, label: s.label, line: s.line, durationSec: s.durationSec, source: s.source, thumb: s.thumb }));
  }

  let _sb = null; // the mounted storyboard
  let _sbDrag = null;

  function _sbCtx() {
    if (!_sb) return null;
    const fam = _family(_sb.familyId);
    // Standalone (Studio's own video): no campaign, so the "campaign" is just
    // the product picked on the page, which is all the agent lookup reads.
    const camp = _sb.standalone ? { projectId: _sc ? _sc.productId : '' } : _campaign(_sb.campaignId);
    if (!fam || !camp) return null;
    return { fam, camp, detail: ensureStoryboard(fam) };
  }

  // A standalone draft joins Studio's items (and so Recent) the first time it is
  // changed, so opening the page and leaving leaves nothing behind.
  function _registerItem(fam) {
    if (_sb && _sb.standalone && !_items().includes(fam)) _items().unshift(fam);
  }

  // Live (R1-W S9a, MC-1020), the storyboard is saved on the server per OWNER: a
  // standalone Studio item, or the video piece a campaign's What opened. Every
  // scene change below goes through _sceneCmd: demo keeps the plain commandBus
  // command; live applies it, PUTs the whole list (guarded by the rev the page
  // last read), and puts the page back on the server's list if that is refused.
  // Saves what is typed in the open scene editor (title + instructions) through
  // _editScene, so it is one undoable change like any other. False when the
  // instructions are over the limit: the editor stays open with the text in it.
  // `quiet` (a blur) shows no error and does not steal the focus.
  let _savingEditor = false;
  let _quietSave = false;
  function _saveOpenEditor(quiet) {
    if (!_sb || !_sb.editing || _savingEditor || !_sb.el.isConnected) return true;
    const id = _sb.editing;
    const li = Array.from(_sb.el.querySelectorAll('.desk-v1-sb-scene')).find((x) => x.dataset.sceneId === id);
    const labelEl = li && li.querySelector('[data-scene-edit-label]');
    const lineEl = li && li.querySelector('[data-scene-edit-line]');
    if (!labelEl || !lineEl) return true;
    if (lineEl.value.trim().length > window.DeskV1Story.MAX_LINE) {
      if (!quiet) window.DeskV1Story.lineOver(li, true);
      return false;
    }
    const ctx = _sbCtx();
    const s = ctx && ctx.detail.scenes.find((x) => x.id === id);
    if (!s) return true;
    _savingEditor = true;
    _quietSave = !!quiet;
    try { _editScene(id, { label: labelEl.value.trim() || s.label, line: lineEl.value.trim() }); } finally { _savingEditor = false; _quietSave = false; }
    return true;
  }

  function _sbOwner() { return { kind: _sb.standalone ? 'studio' : 'piece', id: _sb.familyId }; }
  function _sceneCmd(ctx, spec) {
    // Whatever is typed in the open scene editor is saved before any other scene
    // change repaints the list; over the limit, the change waits and the editor stays.
    if (!_saveOpenEditor()) return Promise.resolve({ ok: false, error: 'the scene you are editing is over the instruction limit: shorten it first' });
    if (!_isLive()) return DeskV1Kit.commandBus.run(spec);
    return window.DeskV1Store.storyboard.command(Object.assign({
      owner: _sbOwner(), detail: ctx.detail, repaint: () => _paintScenes(),
      extra: _sb.standalone ? () => ({ title: ctx.fam.title }) : undefined,
    }, spec));
  }
  // Read the saved storyboard into `detail` and repaint. An answer that lands
  // after the page moved on is dropped.
  function _loadBoard(mine) {
    const ctx = _sbCtx();
    if (!ctx) return;
    // The story box stays disabled until the saved board has landed, so typing
    // cannot be overwritten by the read.
    mine.loading = true;
    if (mine.el.isConnected) _repaintSb();
    window.DeskV1Store.storyboard.load(_sbOwner(), ctx.detail).then(() => {
      mine.loading = false;
      if (_sb === mine && mine.el.isConnected) _repaintSb();
    }).catch((e) => {
      mine.loading = false;
      if (_sb !== mine) return;
      mine.loadError = e && e.message ? e.message : String(e);
      if (mine.el.isConnected) _repaintSb();
    });
  }

  // Scenes are numbered among the real ones, so an example never takes a number.
  function _sceneHTML(s, scenes, editing) {
    const ph = !!s.placeholder;
    const n = ph ? 0 : scenes.filter(_isReal).indexOf(s) + 1;
    const name = ph ? 'Example scene' : `Scene ${n}`;
    const title = editing
      ? `<div class="desk-v1-sb-edit">
          <input type="text" class="desk-v1-sb-edit-input" data-scene-edit-label value="${esc(s.label)}" aria-label="${name} title">
          ${window.DeskV1Story.lineEditorHTML(s.line, name)}
        </div>`
      : `<div class="desk-v1-sb-scenetitle" data-scene-title>${ph ? '<span class="desk-v1-sb-example-chip" data-scene-example>Example</span> ' : `Scene ${n} · `}${esc(s.label)}</div>
         <div class="desk-v1-sb-line">${esc(s.line || '')}</div>`;
    const del = _sb && (_sb.standalone || _isLive())
      ? `<button type="button" class="desk-v1-sb-delete" data-scene-delete aria-label="Delete ${ph ? 'example scene' : 'scene ' + n}: ${esc(s.label)}" title="Delete this scene">🗑</button>`
      : '';
    return `<li class="desk-v1-sb-scene${ph ? ' desk-v1-sb-scene-example' : ''}${editing ? ' desk-v1-sb-scene-editing' : ''}${_sb && _sb.selected === s.id ? ' desk-v1-sb-selected' : ''}" data-scene-id="${esc(s.id)}" data-scene-label="${esc(s.label)}"${ph ? ' data-scene-placeholder' : ''}>
      <button type="button" class="desk-v1-sb-handle" data-scene-handle aria-label="Move ${ph ? 'example scene' : 'scene ' + n}: ${esc(s.label)}. Arrow Up or Arrow Down reorders" title="Drag, or press Arrow Up / Arrow Down">⠿</button>
      <div class="desk-v1-sb-thumb">${s.thumb || !_isLive() ? `<img src="${esc(s.thumb || '')}" alt="${ph ? 'Example capture' : 'Capture for scene ' + n}">` : ''}<span class="desk-v1-sb-num" data-scene-num>${ph ? '·' : n}</span></div>
      <div class="desk-v1-sb-body">
        ${title}
        <div class="desk-v1-sb-source">${esc(s.source || '')}</div>
      </div>
      <div class="desk-v1-sb-meta">
        <span class="desk-v1-sb-dur">${esc(_mmss(s.durationSec || 0))}</span>
        <button type="button" class="desk-v1-sb-editbtn" data-scene-edit>${editing ? 'Done' : 'Edit'}</button>
        ${_isLive() ? `<button type="button" class="desk-v1-sb-editbtn" data-scene-picture aria-label="${s.picture ? 'Replace' : 'Add'} the picture for ${ph ? 'example scene' : 'scene ' + n}: ${esc(s.label)}">${s.picture ? 'Replace picture' : 'Add picture'}</button>` : ''}
        ${_isLive() && window.DeskV1LibraryPicker ? window.DeskV1LibraryPicker.buttonHTML('data-scene-library', 'desk-v1-sb-editbtn', `Pick from library: the picture for ${ph ? 'example scene' : 'scene ' + n}: ${s.label}`) : ''}
        ${_isLive() ? `<button type="button" class="desk-v1-sb-editbtn" data-scene-paste aria-label="Paste a picture from the clipboard into ${ph ? 'example scene' : 'scene ' + n}: ${esc(s.label)}" title="Paste a picture from the clipboard">Paste</button>` : ''}
        ${del}
      </div>
    </li>`;
  }

  // The Studio timeline bar (MC-1024 follow-up): the director's own scene strip
  // (desk-v1-video.js, DeskV1VideoStrip) over the REAL scenes, widths in
  // proportion to duration. The reorder drag is that strip's; its drop commits
  // through the same _moveScene as the list, so the two stay in sync.
  function _timelineHTML(detail) {
    const real = _realScenes(detail);
    if (!real.length || !window.DeskV1VideoStrip) return '';
    const total = real.reduce((sum, s) => sum + (s.durationSec || 0), 0);
    return `<div class="desk-v1-sb-timeline-title">Timeline · ${esc(_mmss(total))}</div>
      <div class="desk-v1-video-scenestrip desk-v1-sb-timeline" data-sb-timeline aria-label="Timeline of real scenes. Drag a tile to reorder">${window.DeskV1VideoStrip.html(real)}</div>`;
  }

  // The standalone Render button and the note above the list read the real
  // scenes, not the sample rows.
  function _noScenesHTML(detail) {
    if (_sb && _sb.loadError) return `<div class="desk-v1-camp-empty" data-sb-load-error>Could not load this storyboard: ${esc(_sb.loadError)}</div>`;
    if (_isLive() && _sb && !detail.scenes.length) return `<div class="desk-v1-camp-empty" data-sb-no-scenes>No scenes yet. Add the first one below, then add its picture.</div>`;
    if (!(_sb && _sb.standalone) || _realScenes(detail).length) return '';
    const msg = detail.scenes.length
      ? 'No real scenes yet. The rows below are examples: edit one to make it a scene.'
      : 'No scenes yet. Scenes come from product captures, which are not wired yet.';
    return `<div class="desk-v1-camp-empty" data-sb-no-scenes>${msg}</div>`;
  }

  function _renderStatusHTML(fam) {
    const r = fam.render;
    if (!(r && r.status === 'rendering')) return '';
    const tail = _sb && _sb.standalone
      ? 'preview only: the render engine is not wired yet, so this does not finish.'
      : 'back on What the piece stays usable while it renders.';
    return `<div class="desk-v1-sb-rendering" data-sb-rendering>⟳ Rendering ${esc(r.progress != null ? r.progress : 0)}% — ${tail}</div>`;
  }

  function _sbHTML() {
    const ctx = _sbCtx();
    if (!ctx) return `<div class="desk-v1-stub"><div class="desk-v1-stub-body">This storyboard no longer exists.</div></div>`;
    const { fam, camp, detail } = ctx;
    const agent = _sbAgent(ctx);
    const agentName = agent.name || 'Your agent';
    const rendering = fam.render && fam.render.status === 'rendering';
    const strip = _sb.standalone
      ? `<div class="desk-v1-sb-returning" data-sb-standalone>Studio · <strong>not attached to a campaign</strong> <span>· use it in one from the Material library afterwards</span></div>`
      : `<div class="desk-v1-sb-returning" data-returning-to>Returning to › <strong>${esc(_campTitle(camp))}</strong> <span>· What · ${esc(fam.title)}</span></div>`;
    return `<div class="desk-v1-sb" data-storyboard data-family-id="${esc(fam.id)}">
      ${strip}
      <div class="desk-v1-sb-head">
        <h2 class="desk-v1-sb-title">New video · storyboard</h2>
        ${_isLive() ? '' : `<button type="button" class="btn-add" data-sb-render${rendering || (_sb.standalone && !_realScenes(detail).length) ? ' disabled' : ''}>Render</button>`}
      </div>
      <div data-sb-notice>${_noScenesHTML(detail)}</div>
      ${_renderStatusHTML(fam)}
      <div class="desk-v1-sb-layout">
        <div class="desk-v1-sb-main">
          ${_isLive() && !_sb.loadError ? window.DeskV1Story.html(!_sb.loading) : ''}
          ${_sb.standalone ? `<div class="desk-v1-sb-timeline-wrap" data-sb-timeline-wrap>${_timelineHTML(detail)}</div>` : ''}
          <div class="desk-v1-sb-listrow">
            <ol class="desk-v1-sb-scenes" data-scenes aria-label="Scenes">${detail.scenes.map((s) => _sceneHTML(s, detail.scenes, _sb.editing === s.id)).join('')}</ol>
            ${_sb.standalone ? `<div class="desk-v1-sb-bincol">
              <button type="button" class="desk-v1-sb-undo" data-sb-undo disabled aria-label="Nothing to undo" title="Nothing to undo"><span aria-hidden="true">&#8630;</span></button>
              <div class="desk-v1-sb-bin" data-sb-trash role="img" aria-label="Bin: drag a scene here to delete it" title="Drag a scene here to delete it"><span aria-hidden="true">🗑</span></div>
            </div>` : ''}
          </div>
          ${_isLive() && !_sb.loadError ? '<div class="desk-v1-sb-addrow"><button type="button" class="btn-secondary" data-scene-add>Add scene</button></div>' : ''}
          ${_isLive() && !_sb.loadError ? '<div data-sb-engine></div>' : ''}
        </div>
        <aside class="desk-v1-sb-agent" data-sb-agent>${_agentBoxHTML(ctx)}</aside>
      </div>
    </div>`;
  }

  // The agent box: face + name, then (standalone only) the picker, then the ask
  // line. Painted into the aside, and again on its own when a pick or the agent
  // list lands, so a typed question is not lost to a full repaint.
  function _agentBoxHTML(ctx) {
    const agent = _sbAgent(ctx);
    const agentName = agent.name || 'Your agent';
    const avatar = agent.name && typeof window.avatarHTML === 'function' ? window.avatarHTML(agent.avatar, 28) : '<span class="desk-v1-sb-agent-avatar" aria-hidden="true">🤖</span>';
    const defName = _agentRec(ctx.camp).name;
    const picker = _sb.standalone ? `
          <label class="desk-v1-sc-label" for="sb-agent-pick">Agent</label>
          <select class="desk-v1-cap-select desk-v1-sb-agent-pick" id="sb-agent-pick" data-sb-agent-pick${_globalAgents ? '' : ' disabled'}>
            <option value=""${ctx.fam.agent ? '' : ' selected'}>Default${defName ? ' · ' + esc(defName) : ''}</option>${(_globalAgents || []).map((a) =>
              `<option value="${esc(a.ref)}"${a.ref === ctx.fam.agent ? ' selected' : ''}>${esc(a.name)}</option>`).join('')}
          </select>` : '';
    return `<div class="desk-v1-sb-agent-head" data-sb-agent-head>${avatar}<strong>${esc(agentName)}</strong></div>${picker}
          ${window.DeskV1StoryChat.html(agentName, _isLive())}`;
  }

  // A standalone item carries its REAL scenes (the examples are not part of
  // what is saved), kept current on every scene repaint.
  function _syncItemScenes(ctx) {
    if (_sb && _sb.standalone) ctx.fam.scenes = _scenePayload(ctx.detail);
  }

  // A save on blur (`_quietSave`) leaves the rows as they are: rebuilding them would
  // replace the button the user is in the middle of clicking. Everything around
  // the list still refreshes.
  function _paintScenes(focusSel) {
    if (!_sb || !_sb.el.isConnected) return;
    const ctx = _sbCtx();
    const ol = _sb.el.querySelector('[data-scenes]');
    if (!ctx || !ol) return;
    _syncItemScenes(ctx);
    if (!_quietSave) {
      ol.innerHTML = ctx.detail.scenes.map((s) => _sceneHTML(s, ctx.detail.scenes, _sb.editing === s.id)).join('');
      _wireScenes(ol);
    }
    if (_sb.standalone) {
      const wrap = _sb.el.querySelector('[data-sb-timeline-wrap]');
      if (wrap) { wrap.innerHTML = _timelineHTML(ctx.detail); _wireTimeline(); }
      const note = _sb.el.querySelector('[data-sb-notice]');
      if (note) note.innerHTML = _noScenesHTML(ctx.detail);
      const render = _sb.el.querySelector('[data-sb-render]');
      if (render) render.disabled = !_realScenes(ctx.detail).length || !!(ctx.fam.render && ctx.fam.render.status === 'rendering');
    }
    _markSelected();
    window.DeskV1Story.grow(ol);
    if (focusSel) { const f = _sb.el.querySelector(focusSel); if (f) f.focus({ preventScroll: true }); }
    // The scenes changed, so the price shown for the render is out of date.
    const eng = _isLive() && _sb.el.querySelector('[data-sb-engine]');
    if (eng && eng.__engRefresh) eng.__engRefresh();
  }

  // The timeline's reorder is the director strip's drag, committing through the
  // same move the list uses.
  function _wireTimeline() {
    const strip = _sb && _sb.el.querySelector('[data-sb-timeline]');
    if (!strip || !window.DeskV1VideoStrip) return;
    // A tile can also be dragged onto the bin: the same delete as the row's.
    window.DeskV1VideoStrip.wire(strip, (fromId, toId) => _moveScene(fromId, toId), {
      trashSel: '[data-sb-trash]',
      onTrash: (sceneId) => _deleteScene(sceneId),
      onSelect: (sceneId) => _selectScene(sceneId, true),
      activeBodyClass: 'desk-v1-sb-drag-active',
      ghostClass: 'pd-ghost desk-v1-video-scene-ghost',
    });
  }

  function _deleteScene(sceneId) {
    const ctx = _sbCtx();
    if (!ctx) return;
    const { detail } = ctx;
    const idx = detail.scenes.findIndex((s) => s.id === sceneId);
    if (idx < 0) return;
    const scene = detail.scenes[idx];
    _registerItem(ctx.fam);
    _sceneCmd(ctx, {
      label: `Deleted scene “${scene.label}”`,
      destructive: true,
      do: () => {
        const i = detail.scenes.indexOf(scene);
        if (i >= 0) detail.scenes.splice(i, 1);
        if (_sb && _sb.editing === sceneId) _sb.editing = null;
        _paintScenes();
      },
      undo: () => { detail.scenes.splice(Math.min(idx, detail.scenes.length), 0, scene); _paintScenes(); },
    });
  }

  function _moveScene(fromId, toId) {
    const ctx = _sbCtx();
    if (!ctx || fromId === toId) return false;
    const arr = ctx.detail.scenes;
    const from = arr.findIndex((s) => s.id === fromId);
    const to = arr.findIndex((s) => s.id === toId);
    if (from < 0 || to < 0) return false;
    _registerItem(ctx.fam);
    const before = arr.map((s) => s.id);
    const apply = (ids) => { ctx.detail.scenes = ids.map((id) => arr.find((s) => s.id === id)); };
    const after = before.slice();
    const [moved] = after.splice(from, 1);
    after.splice(to, 0, moved);
    const label = arr[from].label;
    _sceneCmd(ctx, {
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
    const prev = { label: s.label, line: s.line, placeholder: !!s.placeholder };
    if (prev.label === patch.label && prev.line === patch.line) return;
    _registerItem(ctx.fam);
    _sceneCmd(ctx, {
      label: `Edited scene “${prev.label}”`,
      do: () => { s.label = patch.label; s.line = patch.line; s.placeholder = false; _paintScenes(); },
      undo: () => { s.label = prev.label; s.line = prev.line; s.placeholder = prev.placeholder; _paintScenes(); },
    });
  }

  // Live only (R1-W S9a): a storyboard starts empty, so scenes are added by hand.
  // The new scene opens in edit mode so it is named at once.
  function _addScene() {
    const ctx = _sbCtx();
    if (!ctx) return;
    const { detail } = ctx;
    const scene = { id: 'sc-' + _uid(), label: 'New scene', line: '', durationSec: 3, edited: false, picture: null, thumb: '', source: '' };
    _registerItem(ctx.fam);
    _sceneCmd(ctx, {
      label: 'Added a scene',
      do: () => { detail.scenes.push(scene); if (_sb) { _sb.editing = scene.id; _sb.selected = scene.id; } _paintScenes(`[data-scene-id="${scene.id}"] [data-scene-edit-label]`); },
      undo: () => {
        const i = detail.scenes.findIndex((s) => s.id === scene.id);
        if (i >= 0) detail.scenes.splice(i, 1);
        if (_sb && _sb.editing === scene.id) _sb.editing = null;
        _paintScenes();
      },
    });
  }

  // Live only: one picture for one scene. The file goes to the material library
  // first (it is kept there even if the scene is later removed, so Undo can bring
  // it back); the scene change is then an ordinary command.
  async function _setScenePicture(sceneId, file) {
    const ctx = _sbCtx();
    const s = ctx && ctx.detail.scenes.find((x) => x.id === sceneId);
    if (!s || !file) return;
    let ref;
    try { ref = await window.DeskV1Store.storyboard.uploadPicture(_sbOwner(), file); } catch (e) {
      DeskV1Kit.toast(`The picture was not added: ${e && e.message ? e.message : e}`);
      return;
    }
    _applyScenePicture(sceneId, ref);
  }

  // The scene change itself: `ref` is a library picture ({path, kind, title, src}),
  // freshly uploaded by Add picture or already there (Pick from library).
  function _applyScenePicture(sceneId, ref) {
    const ctx = _sbCtx();
    const s = ctx && ctx.detail.scenes.find((x) => x.id === sceneId);
    if (!s || !ref) return;
    const prev = { picture: s.picture || null, thumb: s.thumb || '' };
    _registerItem(ctx.fam);
    _sceneCmd(ctx, {
      label: `${prev.picture ? 'Replaced' : 'Added'} the picture for “${s.label}”`,
      do: () => { s.picture = ref; s.thumb = ref.src || ''; _paintScenes(); },
      undo: () => { s.picture = prev.picture; s.thumb = prev.thumb; _paintScenes(); },
    });
  }

  // Live only: Pick from library. The scene points at the file already in the
  // library (the storyboard keeps a library path, never a copy), so nothing is uploaded.
  function _pickScenePicture(sceneId, btn) {
    if (!window.DeskV1LibraryPicker) return;
    window.DeskV1LibraryPicker.open({ kinds: ['image'], returnFocus: btn, onPick: (it) => {
      _applyScenePicture(sceneId, { path: it.path, kind: 'image', title: it.title, src: it.src });
      DeskV1Kit.toast(`Used “${it.title}” from the library. The scene points at that file; nothing was copied.`);
    } });
  }

  // ── Pasted pictures (Ron 2026-10-02) ─────────────────────────────────────────
  // A pasted picture goes up through the SAME call as Add picture (the material
  // library, the server's own type and size limits), then ONE command: the first
  // picture becomes the target scene's picture, each further one a new scene right
  // after it. With no target scene every picture is a new scene at the end.
  const _PASTE_EXT = { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/gif': 'gif', 'image/webp': 'webp' };
  function _pastedFile(file, i) {
    const ext = _PASTE_EXT[file.type];
    if (!ext) return null;
    // A clipboard screenshot is always called "image.png"; a copied FILE keeps its own name.
    if (file.name && !/^image\.\w+$/i.test(file.name) && /\.(png|jpe?g|gif|webp)$/i.test(file.name)) return file;
    const stamp = new Date().toTimeString().slice(0, 8).replace(/:/g, '');
    return new File([file], `pasted-picture-${stamp}${i ? '-' + (i + 1) : ''}.${ext}`, { type: file.type });
  }

  async function _addPastedPictures(files, targetId) {
    const mine = _sb;
    if (!mine || !_isLive() || !files.length) return;
    const refs = [];
    let refused = '';
    for (let i = 0; i < files.length; i++) {
      const f = _pastedFile(files[i], i);
      if (!f) { refused = 'only PNG, JPEG, GIF and WebP pictures can be added'; continue; }
      try { refs.push(await window.DeskV1Store.storyboard.uploadPicture(_sbOwner(), f)); } catch (e) { refused = e && e.message ? e.message : String(e); }
    }
    if (refused) DeskV1Kit.toast(`${refs.length ? 'Some pasted pictures were' : 'The pasted picture was'} not added: ${refused}`);
    const ctx = _sb === mine ? _sbCtx() : null;
    if (!ctx || !refs.length) return;
    const { detail } = ctx;
    const target = (targetId && detail.scenes.find((x) => x.id === targetId && _isReal(x))) || null;
    const prev = target ? { picture: target.picture || null, thumb: target.thumb || '' } : null;
    const added = (target ? refs.slice(1) : refs).map((ref) => ({
      id: 'sc-' + _uid(), label: 'New scene', line: '', durationSec: 3, edited: false, picture: ref, thumb: ref.src || '', source: '',
    }));
    _registerItem(ctx.fam);
    _sceneCmd(ctx, {
      label: refs.length > 1 ? `Pasted ${refs.length} pictures`
        : target ? `${prev.picture ? 'Replaced' : 'Added'} the picture for “${target.label}”` : 'Pasted a picture as a new scene',
      do: () => {
        if (target) { target.picture = refs[0]; target.thumb = refs[0].src || ''; }
        detail.scenes.splice(target ? detail.scenes.indexOf(target) + 1 : detail.scenes.length, 0, ...added);
        _paintScenes();
      },
      undo: () => {
        added.forEach((a) => { const i = detail.scenes.indexOf(a); if (i >= 0) detail.scenes.splice(i, 1); });
        if (target) { target.picture = prev.picture; target.thumb = prev.thumb; }
        _paintScenes();
      },
    });
  }

  // The Paste button: the async Clipboard API, which is what phones and the https
  // tunnel allow. A refusal is said in one line and points at Add picture.
  async function _pasteFromClipboard(sceneId) {
    const mine = _sb;
    const refusal = 'This browser would not let Clayrune read the clipboard. Use Add picture instead.';
    if (!(navigator.clipboard && navigator.clipboard.read)) { DeskV1Kit.toast(refusal); return; }
    const files = [];
    try {
      for (const item of await navigator.clipboard.read()) {
        const type = item.types.find((t) => /^image\//.test(t));
        if (type) files.push(new File([await item.getType(type)], 'image.' + (_PASTE_EXT[type] || 'png'), { type }));
      }
    } catch (e) {
      DeskV1Kit.toast(refusal);
      return;
    }
    if (_sb !== mine) return;
    if (!files.length) { DeskV1Kit.toast('There is no picture on the clipboard. Copy one first, or use Add picture.'); return; }
    _addPastedPictures(files, sceneId);
  }

  // Ctrl/Cmd+V on the storyboard (the page, not a text field): the clipboard's
  // pictures go into the selected scene. Anything else, pasted text included, is
  // left alone, so normal paste into the title boxes and the ask line is untouched.
  document.addEventListener('paste', (e) => {
    if (!_sb || !_sb.el.isConnected || !_isLive() || !_sb.el.getClientRects().length) return;
    const t = e.target;
    if (t && t.closest && t.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"]')) return;
    const files = Array.from((e.clipboardData && e.clipboardData.files) || []).filter((f) => /^image\//.test(f.type));
    if (!files.length) return;
    e.preventDefault();
    _addPastedPictures(files, _sb.selected || null);
  });

  // Selection: the scene a paste lands in. A click on a row or a timeline tile
  // selects it; a second click on the row's own body (not a button) lets go.
  function _markSelected() {
    if (!_sb || !_sb.el.isConnected) return;
    const tiles = Array.from(_sb.el.querySelectorAll('[data-scene-id]'));
    if (_sb.selected && !tiles.some((n) => n.dataset.sceneId === _sb.selected)) _sb.selected = null;
    tiles.forEach((n) => {
      const on = n.dataset.sceneId === _sb.selected;
      n.classList.toggle('desk-v1-sb-selected', on);
      if (n.classList.contains('desk-v1-sb-scene')) { if (on) n.setAttribute('aria-current', 'true'); else n.removeAttribute('aria-current'); }
    });
    window.DeskV1StoryChat.onSelect();
  }
  function _selectScene(sceneId, keep) {
    if (!_sb) return;
    _sb.selected = (!keep && _sb.selected === sceneId) ? null : sceneId;
    _markSelected();
  }

  // The Undo button beside the bin: the header button's own undo, so a delete
  // is one click from where it happened now that no toast offers it.
  function _syncSbUndo() {
    const btn = _sb && _sb.el.isConnected && _sb.el.querySelector('[data-sb-undo]');
    const bus = window.DeskV1Kit && window.DeskV1Kit.commandBus;
    if (!btn || !bus) return;
    const cmd = bus.peek();
    btn.disabled = !cmd;
    const title = cmd ? 'Undo: ' + (cmd.label || 'the last change') : 'Nothing to undo';
    btn.title = title;
    btn.setAttribute('aria-label', title);
  }
  let _sbBusHooked = false;
  function _hookSbUndo(el) {
    const bus = window.DeskV1Kit && window.DeskV1Kit.commandBus;
    if (bus && !_sbBusHooked) { bus.onChange(_syncSbUndo); _sbBusHooked = true; }
    const btn = el.querySelector('[data-sb-undo]');
    if (btn) btn.onclick = () => { if (bus && bus.canUndo()) bus.undoLast(); };
    _syncSbUndo();
  }

  function _startRender() {
    const ctx = _sbCtx();
    if (!ctx) return;
    const { fam, detail } = ctx;
    const prev = fam.render || null;
    const scenes = _scenePayload(detail); // the examples are not rendered
    if (_sb.standalone && !scenes.length) return;
    _registerItem(fam);
    DeskV1Kit.commandBus.run({
      label: `Started rendering “${fam.title}”`,
      do: () => { fam.render = { jobId: 'render-' + _uid(), status: 'rendering', revision: 1, progress: 40, scenes }; _repaintSb(); },
      undo: () => { fam.render = prev; _repaintSb(); },
    });
  }

  function _repaintSb() {
    if (!_sb || !_sb.el.isConnected) return;
    const ctx = _sbCtx();
    if (ctx) _syncItemScenes(ctx);
    _sb.el.innerHTML = _sbHTML();
    _wireSb(_sb.el);
  }

  // A pick (or the agent list landing) repaints only the agent box, so what was
  // typed into its ask line survives.
  function _paintAgentBox() {
    if (!_sb || !_sb.el.isConnected) return;
    const ctx = _sbCtx();
    const box = _sb.el.querySelector('[data-sb-agent]');
    if (!ctx || !box) return;
    const keep = box.querySelector('[data-sb-ask]');
    const typed = keep ? keep.value : '';
    box.innerHTML = _agentBoxHTML(ctx);
    _wireAgentBox(box, ctx);
    const ask = box.querySelector('[data-sb-ask]');
    if (ask && typed) ask.value = typed;
  }

  function _pickAgent(ref) {
    const ctx = _sbCtx();
    if (!ctx) return;
    const prev = ctx.fam.agent || null;
    if ((ref || null) === prev) return;
    _registerItem(ctx.fam);
    const name = ref ? (_pickedAgent({ agent: ref }) || {}).name || ref : 'the default agent';
    DeskV1Kit.commandBus.run({
      label: `Picked ${name} as the agent`,
      do: () => { ctx.fam.agent = ref || null; _paintAgentBox(); },
      undo: () => { ctx.fam.agent = prev; _paintAgentBox(); },
    });
  }

  function _wireAgentBox(box, ctx) {
    const pick = box.querySelector('[data-sb-agent-pick]');
    if (pick) pick.onchange = () => _pickAgent(pick.value);
    if (_isLive()) window.DeskV1StoryChat.wire(box, _storyBridge());
  }

  // What desk-v1-story.js is handed: the page's scene list and commands, never the
  // module's own state. `token` is null once the storyboard is left or reopened.
  function _storyBridge() {
    const mine = _sb;
    return {
      token: () => (_sb === mine ? mine : null),
      ctx: () => (_sb === mine ? _sbCtx() : null),
      owner: () => _sbOwner(),
      extra: () => (mine.standalone ? { title: _sbCtx().fam.title } : undefined),
      registerItem: () => { const c = _sbCtx(); if (c) _registerItem(c.fam); },
      projectId: () => { const c = _sbCtx(); return (c && c.camp.projectId) || ''; },
      agentRef: () => { const c = _sbCtx(); const a = c && _sbAgent(c); return a && a.name ? a.ref : null; },
      agentName: () => { const c = _sbCtx(); return (c && _sbAgent(c).name) || 'your agent'; },
      selected: () => (_sb === mine ? mine.selected : null),
      stopEditing: () => { if (_sb === mine) mine.editing = null; },
      repaint: () => _paintScenes(),
      command: (spec) => { const c = _sbCtx(); _registerItem(c.fam); return _sceneCmd(c, spec); },
    };
  }

  function _sceneIdAt(x, y) {
    const hit = document.elementFromPoint(x, y);
    const li = hit && hit.closest && hit.closest('.desk-v1-sb-scene[data-scene-id]');
    return li && _sb.el.contains(li) ? li.dataset.sceneId : null;
  }
  function _overTrash(x, y) {
    const hit = document.elementFromPoint(x, y);
    const z = hit && hit.closest && hit.closest('[data-sb-trash]');
    return !!(z && _sb.el.contains(z));
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
        const trash = _sb.el.querySelector('[data-sb-trash]');
        if (trash) trash.classList.toggle('pd-drop-hover', _overTrash(x, y));
      },
      onDrop: (s, x, y) => {
        if (_overTrash(x, y)) return { trash: true };
        const over = _sceneIdAt(x, y);
        return over ? { over } : null;
      },
      afterDrop: (s, target) => {
        if (!target) return;
        if (target.trash) _deleteScene(sceneId); else _moveScene(sceneId, target.over);
      },
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
      const del = li.querySelector('[data-scene-delete]');
      if (del) del.onclick = () => _deleteScene(id);
      const pic = li.querySelector('[data-scene-picture]');
      if (pic) pic.onclick = () => {
        const input = document.createElement('input');
        input.type = 'file';
        input.accept = 'image/png,image/jpeg,image/gif,image/webp';
        input.setAttribute('data-scene-picture-input', '');
        input.onchange = () => { if (input.files && input.files[0]) _setScenePicture(id, input.files[0]); };
        input.click();
      };
      const fromLib = li.querySelector('[data-scene-library]');
      if (fromLib) fromLib.onclick = () => _pickScenePicture(id, fromLib);
      const paste = li.querySelector('[data-scene-paste]');
      if (paste) paste.onclick = () => { _selectScene(id, true); _pasteFromClipboard(id); };
      li.addEventListener('click', (e) => {
        if (li.hasAttribute('data-scene-placeholder')) return;
        _selectScene(id, !!(e.target.closest && e.target.closest('button, input, textarea, select, a')));
      });
      const edit = li.querySelector('[data-scene-edit]');
      edit.onclick = () => {
        // Whatever is open is saved first: this scene's own editor (Done) or another's.
        if (!_saveOpenEditor()) return;
        if (_sb.editing === id) {
          _sb.editing = null;
          _paintScenes(`[data-scene-id="${id}"] [data-scene-edit]`);
        } else {
          _sb.editing = id;
          _paintScenes(`[data-scene-id="${id}"] [data-scene-edit-label]`);
        }
      };
      // Leaving the instructions box saves it, so nothing typed waits on Done.
      const lineEl = li.querySelector('[data-scene-edit-line]');
      if (lineEl) lineEl.addEventListener('blur', () => { _saveOpenEditor(true); });
    });
  }

  function _wireSb(el) {
    const ctx = _sbCtx();
    if (!ctx) return;
    // Live, Render is the engine panel's (estimate, passcode, a real job); the
    // fixture button above exists in demo only.
    const demoRender = el.querySelector('[data-sb-render]');
    if (demoRender) demoRender.onclick = _startRender;
    const engineHost = el.querySelector('[data-sb-engine]');
    if (engineHost) window.DeskV1Engines.mountVideoRender(engineHost, { owner: _sbOwner(), projectId: ctx.camp.projectId || undefined });
    const add = el.querySelector('[data-scene-add]');
    if (add) add.onclick = _addScene;
    _wireScenes(el.querySelector('[data-scenes]'));
    window.DeskV1Story.mount(el.querySelector('[data-sb-story-panel]'), _storyBridge());
    if (_sb.standalone) { _wireTimeline(); _hookSbUndo(el); }
    _markSelected();
    _wireAgentBox(el.querySelector('[data-sb-agent]'), ctx);
  }

  function deskV1RenderStoryboard(el, params) {
    params = params || {};
    _sb = { el, campaignId: params.campaignId, familyId: params.familyId, editing: null, selected: null, standalone: false };
    el.innerHTML = _sbHTML();
    _wireSb(el);
    if (_isLive()) _loadBoard(_sb);
  }

  // ── Source bodies What opens (frames 15b, 16, 16c) ───────────────────────
  function captureAvailable(projectId) {
    if (_isLive()) return !!projectId;
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
        <span class="desk-v1-cap-note">Demo screen.</span>
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
    if (source === 'capture') return _isLive() ? window.DeskV1StudioCapture.bodyHTML(ctx) : _screenBodyHTML(card, camp, 'Capture this screen');
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
    const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="320" height="200" viewBox="0 0 320 200"><defs>` +
      `<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="hsl(${a},70%,42%)"/><stop offset="1" stop-color="hsl(${b},70%,28%)"/></linearGradient></defs>` +
      `<rect width="320" height="200" fill="url(#g)"/>` +
      `<circle cx="${60 + (h % 120)}" cy="${50 + (h % 70)}" r="${46 + (h % 30)}" fill="hsl(${c},80%,60%)" opacity=".45"/>` +
      `<circle cx="${200 + (h % 90)}" cy="${120 + (h % 50)}" r="${38 + (h % 26)}" fill="hsl(${b},80%,65%)" opacity=".4"/></svg>`;
    return 'data:image/svg+xml;utf8,' + encodeURIComponent(svg);
  }

  // api: { attach(asset), repaint(), openStoryboard() }
  function wireSourceBody(cardEl, ctx, api) {
    if (cardEl.querySelector('[data-product-capture]')) { window.DeskV1StudioCapture.wire(cardEl, ctx, api); return; }
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
      targets = ((camp && camp.plan && camp.plan.accounts) || []).map((a) => _channel(typeof a === 'string' ? a : (a && a.channel_id)))
        .filter((ch) => ch && TAB_LABEL[ch.platform] && !seen.has(ch.platform) && seen.add(ch.platform))
        .slice(0, 2).map((ch) => ({ id: 'tab-' + ch.id, platform: ch.platform, label: _tabLabel(ch) }));
    }
    if (!targets.length) targets = [{ id: 'tab-draft', platform: 'default', label: 'Draft' }];
    // A piece that already carries text (a Studio article added to the campaign)
    // opens with that text in its first tab, not the canned sample.
    fam.draft = { status: 'drafting', tabs: targets.map((t, n) => ({ id: t.id, label: t.label, body: (n === 0 && fam.body) || (DRAFT_BODY[t.platform] || DRAFT_BODY.default)(fam.title) })) };
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

  // `camp` is null for a Studio article (no campaign behind it); `opts` then
  // carries what the campaign wording would have said: {provenance, saveLabel,
  // backLabel, extraHTML} (desk-v1-studio-article.js).
  function writerHTML(fam, camp, tabIdx, opts) {
    opts = opts || {};
    const draft = ensureDraft(fam, camp);
    const i = Math.min(Math.max(tabIdx || 0, 0), draft.tabs.length - 1);
    const tab = draft.tabs[i];
    const agent = _agentName(camp);
    const how = (camp && camp.how && (camp.how.angle || camp.how.strategy)) || '';
    const prov = opts.provenance != null ? esc(opts.provenance) : `Drafted${agent ? ' by ' + esc(agent) : ''} from this campaign's How${how ? ` (positioning: “${esc(how)}”)` : ''}.`;
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
          <button type="button" class="btn-add" data-writer-save>${esc(opts.saveLabel || 'Save to What')}</button>
          <button type="button" class="btn-secondary" data-writer-back>${esc(opts.backLabel || 'Back to What')}</button>${opts.extraHTML || ''}
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
  // state on its draft. Returns the inverse; the inverse also carries what it
  // changed (`changed`: [{id, from}], `draftFrom`) for persistInReview.
  function markInReview(fam) {
    const draft = fam.draft;
    const prev = { status: draft.status, states: (fam.versions || []).map((v) => [v, v.state]) };
    const changed = [];
    draft.status = 'in_review';
    (fam.versions || []).forEach((v) => {
      if (v.state === 'drafting' || v.state === 'planned') { changed.push({ id: v.id, from: v.state }); v.state = 'needs_review'; }
    });
    const undo = () => { draft.status = prev.status; prev.states.forEach(([v, s]) => { v.state = s; }); };
    undo.changed = changed;
    undo.draftFrom = prev.status;
    return undo;
  }

  // R1-W S4 (live only; resolves null at once in demo mode): markInReview's
  // server side. Each version it moved goes through M18 (drafting|planned ->
  // needs_review, never approved), the piece's draft status through M15;
  // `reverse` writes the previous values back (the Undo). Bodies are NOT sent:
  // the writer's tabs are canned text until Studio itself is wired, and a body
  // PATCH here would overwrite a version's real text with it.
  async function persistInReview(fam, undoMark, reverse) {
    const store = window.DeskV1Store;
    if (!store || !store.live() || !undoMark) return null;
    const base = '/api/desk/pieces/' + encodeURIComponent(fam.id);
    const queue = window.deskV1QueuePiece || ((id, fn) => fn());
    return queue(fam.id, async () => {
      for (const c of undoMark.changed || []) {
        await store.api('PATCH', `${base}/versions/${encodeURIComponent(c.id)}`, { state: reverse ? c.from : 'needs_review' });
      }
      await store.api('PATCH', base, { draft_status: reverse ? (undoMark.draftFrom || null) : 'in_review' });
      return null;
    });
  }

  // ── Standalone creation (MC-1024) ────────────────────────────────────────
  // The `studio-create` route: {kind: 'video'|'image'} opens a new one, {itemId}
  // opens one Studio already made. No campaign anywhere on the page. What it
  // makes is saved to the Material library (live: M22's Studio folder; demo: the
  // fixture library), and `Use in a campaign` is offered once it is finished.
  // Rendering is still the demo path (slice S9 wires the engines), so a Studio
  // video stays at `Rendering 40%` and reaches the library only once it can
  // render for real.
  const CREATE_SOURCES = {
    image: [
      { id: 'capture', label: 'Capture from the product', hint: 'A real screenshot of the app', glyph: '📸' },
      { id: 'online', label: 'Online source', hint: 'YouTube channel, Google Drive, Dropbox', glyph: '🌐' },
      { id: 'generate', label: 'Generate', hint: 'Abstract visuals only — never the product UI', glyph: '✨' },
    ],
  };
  const ENGINE_SOURCE = { id: 'engine', label: 'Generate with an engine', hint: 'Your Higgsfield, Gemini or OpenAI account', glyph: '🎨' };
  const PRODUCT_KEY = 'desk_v1_studio_product';
  const _IMG_EXT = { 'image/png': '.png', 'image/jpeg': '.jpg', 'image/gif': '.gif', 'image/webp': '.webp' };

  // The product the capture reads: the only project, else the last one used.
  function _defaultProduct() {
    const ps = _fx().projects || [];
    if (ps.length === 1) return ps[0].id;
    try {
      const last = localStorage.getItem(PRODUCT_KEY);
      if (last && ps.some((p) => p.id === last)) return last;
    } catch (e) { /* storage blocked: no default */ }
    return '';
  }

  function _itemById(id) {
    const own = _items().find((it) => it.id === id);
    if (own) return own;
    const rec = (_studio().recent || []).find((r) => r.id === id);
    if (rec) return { id: rec.id, kind: rec.kind, title: rec.title, status: rec.status === 'rendered' ? 'saved' : 'draft', src: rec.src || null, path: null };
    const b = _lib.boards.find((x) => x.id === id);
    if (b) return { id: b.id, kind: 'video', title: b.title || 'New video', status: 'draft', render: null };
    const l = _lib.recent.find((r) => r.path === id);
    if (l) return { id: l.path, kind: l.kind, title: l.title, status: 'saved', src: l.src, path: l.path };
    return null;
  }

  function studioCreateLabel(params) {
    if (window.DeskV1StudioArticle && window.DeskV1StudioArticle.handles(params)) return window.DeskV1StudioArticle.label(params);
    const it = params && params.itemId ? _itemById(params.itemId) : null;
    if (it) return it.title;
    return params && params.kind === 'video' ? 'New video' : 'New image';
  }

  function _productRowHTML() {
    const ps = _fx().projects || [];
    return `<div class="desk-v1-sc-product" data-sc-product-row>
      <label class="desk-v1-sc-label" for="sc-product">Product</label>
      <select class="desk-v1-cap-select" id="sc-product" data-sc-product>
        <option value=""${_sc.productId ? '' : ' selected'}>None</option>${ps.map((p) =>
          `<option value="${esc(p.id)}"${p.id === _sc.productId ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}</select>
      <span class="desk-v1-sc-hint">Optional. Only used to capture from a product.</span>
    </div>`;
  }

  function _imageSetupHTML() {
    // Live, a fourth source: a real engine (R1-W S9b). Demo has none: nothing there can spend.
    const srcs = CREATE_SOURCES.image.concat(_isLive() ? [ENGINE_SOURCE] : []);
    if (!_sc.source) {
      return `<div class="desk-v1-what-sources" role="group" aria-label="Choose a source">${srcs.map((s) => `
        <button type="button" class="desk-v1-what-source" data-sc-source="${esc(s.id)}" aria-label="${esc(`${s.label}. ${s.hint}`)}" title="${esc(s.hint)}">
          <span class="desk-v1-what-source-glyph" aria-hidden="true">${esc(s.glyph)}</span>
          <span class="desk-v1-what-source-name">${esc(s.label)}</span>
          <span class="desk-v1-what-source-hint">${esc(s.hint)}</span>
        </button>`).join('')}</div>`;
    }
    const src = srcs.find((s) => s.id === _sc.source);
    const ctx = { card: _sc.card, fam: { kind: 'image' }, camp: { projectId: _sc.productId } };
    const body = _sc.source === 'capture' && !_sc.productId
      ? '<div class="desk-v1-what-later" data-sc-need-product>Pick a product above to capture from it.</div>'
      : (_sc.source === ENGINE_SOURCE.id ? '<div data-sc-engine></div>' : sourceBodyHTML(_sc.source, ctx));
    return `<div class="desk-v1-what-chiprow"><span class="desk-v1-what-chip" data-sc-chip>${esc(src ? src.label : _sc.source)}</span>` +
      `<button type="button" class="desk-v1-what-change" data-sc-change-source>Change source</button></div>${body}`;
  }

  function _itemViewHTML(it) {
    const blank = it.kind === 'video' ? '▶' : (RECENT_ICON[it.kind] || '•');
    const needsFile = _isLive() && !it.path;
    return `<h2 class="desk-v1-studio-title" data-sc-item-title>${esc(it.title)}</h2>
      <div class="desk-v1-sc-item" data-sc-item data-item-id="${esc(it.id)}">
        <div class="desk-v1-sc-preview">${it.src ? `<img src="${esc(it.src)}" alt="${esc(it.title)}">` : `<span class="desk-v1-sc-blank" aria-hidden="true">${esc(blank)}</span>`}</div>
        <div class="desk-v1-sc-itemmeta"><span>${esc(it.kind)}</span><span data-sc-saved>Saved to the Material library · not attached to a campaign</span></div>
      </div>
      <div class="desk-v1-sc-actions">
        <button type="button" class="btn-add" data-sc-use${needsFile ? ' disabled' : ''}>Use in a campaign ›</button>
        <button type="button" class="btn-secondary" data-sc-another>Make another</button>
      </div>`;
  }

  function _paintCreate() {
    if (!_sc || !_sc.el.isConnected) return;
    const it = _sc.item;
    const view = it && it.status === 'saved' ? 'item' : (_sc.kind === 'video' ? 'storyboard' : 'sources');
    let inner;
    if (view === 'item') inner = _itemViewHTML(it);
    else if (view === 'storyboard') {
      inner = `<h2 class="desk-v1-studio-title">New video</h2>
        <div class="desk-v1-sc-product">
          <label class="desk-v1-sc-label" for="sc-title">Title</label>
          <input type="text" class="desk-v1-sb-edit-input" id="sc-title" data-sc-title value="${esc(it.title)}">
        </div>${_productRowHTML()}<div data-sc-body></div>`;
    } else inner = `<h2 class="desk-v1-studio-title">New image</h2>${_productRowHTML()}${_imageSetupHTML()}`;
    _sc.el.innerHTML = `<div class="desk-v1-studio-create" data-studio-create data-kind="${esc(_sc.kind)}" data-view="${view}">${inner}</div>`;
    _wireCreate(view);
  }

  function _wireCreate(view) {
    const el = _sc.el;
    const prod = el.querySelector('[data-sc-product]');
    if (prod) prod.onchange = () => {
      _sc.productId = prod.value;
      _sc.card.ui = {};
      if (prod.value) { try { localStorage.setItem(PRODUCT_KEY, prod.value); } catch (e) { /* storage blocked: not remembered */ } }
      _paintCreate();
    };
    if (view === 'storyboard') {
      _sb = { el: el.querySelector('[data-sc-body]'), campaignId: null, familyId: _sc.item.id, editing: null, selected: null, standalone: true };
      _sb.el.innerHTML = _sbHTML();
      _wireSb(_sb.el);
      const mine = _sb;
      _loadGlobalAgents().then(() => { if (_sb === mine) _paintAgentBox(); });
      // Live: an item Studio already made is read from the server once; a fresh
      // draft has nothing saved yet.
      if (_isLive() && _sc.needsLoad) { _sc.needsLoad = false; _loadBoard(mine); }
      const title = el.querySelector('[data-sc-title]');
      title.onchange = () => {
        const fam = _sc.item;
        const prevTitle = fam.title;
        fam.title = title.value.trim() || 'New video';
        title.value = fam.title;
        _registerItem(fam);
        if (_isLive() && fam.title !== prevTitle) {
          window.DeskV1Store.storyboard.save(_sbOwner(), _sbCtx().detail, { title: fam.title }).catch((e) => {
            fam.title = prevTitle;
            title.value = prevTitle;
            DeskV1Kit.toast(`The title was not saved: ${e && e.message ? e.message : e}`);
          });
        }
        if (typeof window.deskV1PatchParams === 'function') window.deskV1PatchParams({ itemId: fam.id });
      };
      return;
    }
    if (view === 'item') {
      const it = _sc.item;
      el.querySelector('[data-sc-another]').onclick = () => {
        deskV1RenderStudioCreate(el, { kind: it.kind });
        if (typeof window.deskV1PatchParams === 'function') window.deskV1PatchParams({ kind: it.kind, itemId: null });
      };
      const use = el.querySelector('[data-sc-use]');
      use.onclick = () => {
        const camps = _campaignsToUse();
        if (!camps.length) { DeskV1Kit.toast('Start a campaign first, then use this in one.'); return; }
        DeskV1Kit.addToMenu(use, camps.map((c) => ({ id: c.id, label: _campTitle(c) })), async (campId) => {
          if (typeof window.deskV1WhatUseLibraryItem !== 'function') return;
          if (await window.deskV1WhatUseLibraryItem(campId, it)) window.deskV1GotoCampaignPanel('what', { campaignId: campId });
        }, { noAppendNew: true });
      };
      return;
    }
    el.querySelectorAll('[data-sc-source]').forEach((b) => b.onclick = () => { _sc.source = b.dataset.scSource; _sc.card.ui = {}; _paintCreate(); });
    const change = el.querySelector('[data-sc-change-source]');
    if (change) change.onclick = () => { _sc.source = null; _paintCreate(); };
    const engineHost = el.querySelector('[data-sc-engine]');
    if (engineHost) {
      window.DeskV1Engines.mountImageGenerate(engineHost, { key: 'studio', projectId: _sc.productId || undefined, onReady: () => { _loadLibrary(); } });
      return;
    }
    if (_sc.source) {
      wireSourceBody(el, { card: _sc.card, fam: { kind: 'image' }, camp: { projectId: _sc.productId } },
        { attach: _saveMade, repaint: _paintCreate, openStoryboard() {} });
    }
  }

  // What Studio made goes to the Material library. Demo: the fixture library
  // (one tile per item, so a What pick attaches exactly it) and an Undo. Live:
  // a file in M22's Studio folder; there is no delete route, so a live save is a
  // plain confirmation, not an Undo that would only un-draw it. A refusal rolls
  // the page back to the sources and says why.
  async function _srcBlob(src) {
    if (!src) throw new Error('there is no image to save yet');
    if (/^data:image\/svg/i.test(src)) {
      return new Promise((resolve, reject) => {
        const img = new Image();
        img.onload = () => {
          const c = document.createElement('canvas');
          c.width = 1280; c.height = 800;
          c.getContext('2d').drawImage(img, 0, 0, c.width, c.height);
          c.toBlob((b) => (b ? resolve(b) : reject(new Error('could not encode the image'))), 'image/png');
        };
        img.onerror = () => reject(new Error('could not draw the generated image'));
        img.src = src;
      });
    }
    const res = await fetch(src);
    if (!res.ok) throw new Error(`could not read the image (HTTP ${res.status})`);
    const b = await res.blob();
    if (!_IMG_EXT[b.type]) throw new Error('only png, jpg, gif and webp images can be saved to the library');
    return b;
  }

  function _saveMade(asset) {
    const kind = asset.kind === 'video' ? 'video' : 'image';
    const item = { id: 'studio-' + _uid(), kind, title: asset.title, status: 'saved', src: asset.src || null, path: null };
    const tile = { id: 'mat-' + item.id, title: item.title, files: 1, thumb: item.src };
    const shelf = () => {
      const ml = _fx().materialLibrary || (_fx().materialLibrary = { video: [], image: [] });
      return ml[kind] || (ml[kind] = []);
    };
    window.DeskV1Store.write({
      label: `Saved “${item.title}” to the Material library`,
      apply: () => {
        _items().unshift(item);
        if (_sc) _sc.item = item;
        if (!_isLive()) shelf().push(tile);
        _paintCreate();
      },
      unapply: () => {
        const i = _items().indexOf(item); if (i >= 0) _items().splice(i, 1);
        const arr = shelf(); const j = arr.indexOf(tile); if (j >= 0) arr.splice(j, 1);
        if (_sc && _sc.item === item) _sc.item = null;
      },
      repaint: () => _paintCreate(),
      request: async () => {
        if (asset.path) return asset; // Capture already saved the real file in Studio.
        const blob = await _srcBlob(asset.src);
        const name = (String(item.title).replace(/[^A-Za-z0-9 _-]+/g, '').trim().slice(0, 60) || 'studio') + _IMG_EXT[blob.type];
        const fd = new FormData();
        fd.append('file', blob, name);
        fd.append('title', item.title);
        return window.DeskV1Store.api('POST', '/api/desk/materials', fd);
      },
      irreversible: () => `Saved “${item.title}” to the Material library`,
    }).then((r) => {
      if (!r || !r.ok || !r.result) return;
      Object.assign(item, { path: r.result.path, src: r.result.src || item.src });
      if (typeof window.deskV1WhatInvalidateMaterials === 'function') window.deskV1WhatInvalidateMaterials();
      _paintCreate();
    });
  }

  function deskV1RenderStudioCreate(el, params) {
    params = params || {};
    if (window.DeskV1StudioArticle && window.DeskV1StudioArticle.handles(params)) { _sc = null; window.DeskV1StudioArticle.render(el, params); return; }
    const item = params.itemId ? _itemById(params.itemId) : null;
    const kind = item ? item.kind : (params.kind === 'video' ? 'video' : 'image');
    _sc = { el, kind, item, productId: _defaultProduct(), source: null, card: { id: 'studio-create', ui: {} }, needsLoad: !!item && kind === 'video' };
    // A new video starts as an unregistered draft so its storyboard has an owner;
    // it joins Recent only once it is changed (see _registerItem).
    if (!item && kind === 'video') _sc.item = { id: 'studio-' + _uid(), kind: 'video', title: 'New video', status: 'draft', render: null };
    _paintCreate();
  }

  window.deskV1RenderStudio = deskV1RenderStudio;
  window.deskV1RenderStudioCreate = deskV1RenderStudioCreate;
  window.deskV1StudioCreateLabel = studioCreateLabel;
  window.deskV1RenderStoryboard = deskV1RenderStoryboard;
  window.DeskV1Studio = {
    captureAvailable, sourceBodyHTML, wireSourceBody,
    ensureStoryboard, ensureDraft, writerHTML, wireWriter, markInReview, persistInReview,
    campaignsToUse: _campaignsToUse, campaignTitle: _campTitle, campaign: _campaign, project: _project,
  };
})();
