// Desk v1 (MC-977) — R2-7: the ③ What stop of the campaign map
// (docs/THE_DESK_V1_IA_REVISION_2.md §8 row R2-7, §11.4 item 5, §11.6 Q1;
// frames 5a/5b/5c, 15, 15a, 16a, 16b). Window-bridged module, no `import`
// (ground rule 1). `desk-v1-campaign.js`'s `deskV1FillCampaignTabBody` calls
// `deskV1FillWhat(el, params, camp)` for panel 'what'.
//
//   filters  All · Needs review · Scheduled · Blocked
//   rows     kind label · title · `on N channels` · per-version status
//            (+ the piece's asset thumbnails and `＋ Add media`)
//   tray     CONTENT TYPES: Post · Article · Video · Image · YouTube, dragged
//            (or focus + Enter) into the campaign
//
// Each drop is its OWN piece — several of one kind are allowed — and opens a
// create-card at the top of the list (kind + editable title + ✕). The card is
// source-first (§8): an Article asks `Browse existing` / `Write new`, a Video
// four sources, an Image four sources, none preselected, and no file picker
// exists in the DOM until the Upload source is chosen. The chosen source shows
// as a chip + `Change source`. Upload's body is built here (drop zone,
// `Browse this computer`, Material library folders); Record / Capture /
// Create / Generate / Online and the article writer are R2-8's and live in
// desk-v1-studio.js (`window.DeskV1Studio`), which this file calls. If that
// module is absent a source falls back to a labelled placeholder so nothing
// pretends to work.
//
// Every mutation is a command with an inverse (§10), run through
// `DeskV1Store.write`: desk_v1_live ON also calls the piece routes (R1-W S4:
// M14 create, M15 patch/pick-an-article, M16 delete, M21 attach/upload, M22
// materials); OFF is demo mode, local only, no request. The piece shape is read
// through `DeskV1Kit.piece*` so What and Where agree on `on N channels`.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }
  function _channel(id) { return (_fx().channels || []).find((c) => c.id === id) || null; }
  function _familiesFor(campaignId) { return (_fx().families || []).filter((f) => f.campaignId === campaignId); }
  function _bridge() { return window.deskV1CampaignWhatBridge || {}; }

  // ── R1-W S4: the live routes ─────────────────────────────────────────────
  function _isLive() { return window.DeskV1Store.live(); }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  function _pieceUrl(id) { return '/api/desk/pieces/' + encodeURIComponent(id); }

  // One write at a time per piece: a create-card's title edit, an upload and a
  // delete can be clicked faster than the route answers, and a PATCH that
  // overtakes the POST that makes the piece would 404. The chain never rejects
  // (each caller still gets its own result); a failed link does not block the
  // next one.
  const _chain = {};
  function _after(pieceId) { return _chain[pieceId] || Promise.resolve(); }
  function _queue(pieceId, fn) {
    const p = _after(pieceId).then(fn);
    _chain[pieceId] = p.catch(() => {});
    return p;
  }
  window.deskV1AfterPieceSaved = _after; // the piece page waits on it too
  window.deskV1QueuePiece = _queue;      // Studio's save-to-review runs through it

  // The material library and the existing-article list are server data (M22),
  // read when a What is opened live. The fixture's own are used in demo mode.
  // `error` is a real failure shown as one, never an empty library.
  const _mats = { for: null, into: null, promise: null, error: null };
  function _ensureMaterials(campaignId) {
    if (!_isLive()) return Promise.resolve();
    if (_mats.promise && _mats.for === campaignId && _mats.into === _fx()) return _mats.promise;
    _mats.for = campaignId;
    _mats.into = _fx();
    _mats.error = null;
    _mats.promise = _api('GET', '/api/desk/materials?campaign_id=' + encodeURIComponent(campaignId)).then((m) => {
      const st = _fx();
      st.materialLibrary = (m && m.library) || { video: [], image: [] };
      st.existingArticles = (m && m.articles) || [];
    }).catch((e) => { _mats.error = e && e.message ? e.message : String(e); });
    return _mats.promise;
  }

  let _seq = 0;
  function _uid() { return Date.now().toString(36) + (++_seq).toString(36); }

  // ── vocabulary ───────────────────────────────────────────────────────────
  const FILTERS = [
    { id: 'all', label: 'All' },
    { id: 'needs_review', label: 'Needs review' },
    { id: 'scheduled', label: 'Scheduled' },
    { id: 'blocked', label: 'Blocked' },
  ];
  const _SCHEDULED = new Set(['scheduled', 'approved', 'sending', 'submitted']);
  const FILTER_PRED = {
    all: () => true,
    needs_review: (f) => (f.versions || []).some((v) => v.state === 'needs_review'),
    scheduled: (f) => (f.versions || []).some((v) => _SCHEDULED.has(v.state)),
    blocked: (f) => (f.versions || []).some((v) => v.state === 'blocked'),
  };

  // The tray. `youtube` is a Video create-card with the YouTube source hint
  // (§11.6 Q1: the tile renders and behaves; nothing connects or publishes).
  const TYPES = [
    { id: 'post', kind: 'post', label: 'Post', glyph: '💬' },
    { id: 'article', kind: 'article', label: 'Article', glyph: '📄' },
    { id: 'video', kind: 'video', label: 'Video', glyph: '🎬' },
    { id: 'image', kind: 'image', label: 'Image', glyph: '🖼' },
    { id: 'youtube', kind: 'video', label: 'YouTube', glyph: '▶', preview: true },
  ];

  // Source tiles per kind (frames 15a/16a). `body` marks which source has a
  // body in this ticket; the rest are R2-8's.
  const SOURCES = {
    video: [
      { id: 'record', label: 'Record from the product', short: 'Record', hint: 'Screen recording of a live run', glyph: '📹' },
      { id: 'upload', label: 'Upload', hint: 'This computer or the material library', glyph: '⬆', body: true },
      { id: 'online', label: 'Online source', short: 'Online', hint: 'YouTube channel, Google Drive, Dropbox', glyph: '🌐' },
      { id: 'create', label: 'Create new', short: 'Create', hint: 'Storyboard it in Studio', glyph: '🎬' },
    ],
    image: [
      { id: 'capture', label: 'Capture from the product', short: 'Capture', hint: 'A real screenshot of the app', glyph: '📸' },
      { id: 'upload', label: 'Upload', hint: 'This computer or the material library', glyph: '⬆', body: true },
      { id: 'online', label: 'Online source', short: 'Online', hint: 'YouTube channel, Google Drive, Dropbox', glyph: '🌐' },
      { id: 'generate', label: 'Generate', hint: 'Abstract visuals only — never the product UI', glyph: '✨' },
    ],
    article: [
      { id: 'browse', label: 'Browse existing', short: 'Browse', hint: 'Articles already in this project', glyph: '📚', body: true },
      { id: 'write', label: 'Write new', short: 'Write', hint: 'Open the article writer', glyph: '✎' },
    ],
  };
  const R2_8_NOTE = {
    record: 'Recording from the product opens in Studio (R2-8).',
    capture: 'Capturing from the product opens in Studio (R2-8).',
    online: 'Online sources (YouTube, Google Drive, Dropbox) open in Studio (R2-8).',
    create: 'Create new opens the storyboard in Studio (R2-8).',
    generate: 'Generate opens in Studio (R2-8). Abstract visuals only — never the product UI.',
    write: 'Write new opens the article writer (R2-8).',
  };

  // ── state — one What mount per campaign; survives its own repaints ───────
  const _states = {};
  function _state(campaignId) {
    if (!_states[campaignId]) _states[campaignId] = { campaignId, filter: 'all', creates: [], writer: null, el: null };
    return _states[campaignId];
  }
  let _cur = null; // the state of the mount currently on screen
  let _lastDragEnd = 0; // swallow the click a mouse-up fires right after a drag (where.js precedent)

  function _camp() { return _cur ? _campaign(_cur.campaignId) : null; }
  function _isMounted() { return !!(_cur && _cur.el && _cur.el.isConnected); }

  // ── commands ─────────────────────────────────────────────────────────────
  function _newTitle(kind) { return `New ${String(kind || 'piece').toLowerCase()}`; }

  function _addCreate(camp, type) {
    const st = _state(camp.id);
    const fam = {
      id: 'fam-new-' + _uid(), campaignId: camp.id, kind: type.kind, title: _newTitle(type.kind),
      assets: [], versions: [],
    };
    const card = { id: 'create-' + _uid(), familyId: fam.id, typeId: type.id, source: null, existingPick: null };
    DeskV1Store.write({
      label: `Added ${/^[aeiou]/i.test(type.label) ? 'an' : 'a'} ${type.label.toLowerCase()} piece`,
      apply: () => { _fx().families.push(fam); st.creates.unshift(card); _repaint(`[data-what-create="${card.id}"] [data-what-title]`); },
      unapply: () => {
        const arr = _fx().families; const i = arr.findIndex((f) => f.id === fam.id); if (i >= 0) arr.splice(i, 1);
        const j = st.creates.indexOf(card); if (j >= 0) st.creates.splice(j, 1);
      },
      repaint: () => _repaint(),
      request: () => _queue(fam.id, () => _api('POST', '/api/desk/pieces',
        { id: fam.id, campaign_id: camp.id, kind: fam.kind, title: fam.title })),
      undoRequest: () => _queue(fam.id, () => _api('DELETE', _pieceUrl(fam.id))),
    });
  }

  function _removeCreate(camp, card) {
    const st = _state(camp.id);
    const fam = _familiesFor(camp.id).find((f) => f.id === card.familyId);
    const famIdx = _fx().families.indexOf(fam);
    const cardIdx = st.creates.indexOf(card);
    DeskV1Store.write({
      label: `Removed the new ${fam ? fam.kind : 'piece'}`,
      destructive: true,
      apply: () => {
        const i = _fx().families.indexOf(fam); if (i >= 0) _fx().families.splice(i, 1);
        const j = st.creates.indexOf(card); if (j >= 0) st.creates.splice(j, 1);
        _repaint();
      },
      unapply: () => {
        if (fam && famIdx >= 0) _fx().families.splice(Math.min(famIdx, _fx().families.length), 0, fam);
        st.creates.splice(Math.min(cardIdx, st.creates.length), 0, card);
      },
      repaint: () => _repaint(),
      request: () => (fam ? _queue(fam.id, () => _api('DELETE', _pieceUrl(fam.id))) : Promise.resolve(null)),
      undoRequest: () => (fam ? _queue(fam.id, () => _api('POST', '/api/desk/pieces',
        { id: fam.id, campaign_id: camp.id, kind: fam.kind, title: fam.title })) : Promise.resolve(null)),
    });
  }

  // A title typed into a create-card. Demo mode keeps the plain local edit it
  // always was (no Undo toast per keystroke-commit); live also PATCHes it.
  function _renameCreate(fam, input) {
    const prev = fam.title;
    const next = input.value.trim() || _newTitle(fam.kind);
    input.value = next;
    if (!_isLive()) { fam.title = next; return; }
    if (next === prev) return;
    const patch = (title) => () => _queue(fam.id, () => _api('PATCH', _pieceUrl(fam.id), { title }));
    DeskV1Store.write({
      label: `Renamed “${prev}”`,
      apply: () => { fam.title = next; },
      unapply: () => { fam.title = prev; },
      repaint: () => _repaint(),
      request: patch(next),
      undoRequest: patch(prev),
    });
  }

  // The card finishes into an ordinary list row once the piece has content.
  function _closeCreate(camp, card) {
    const st = _state(camp.id);
    const j = st.creates.indexOf(card);
    if (j >= 0) st.creates.splice(j, 1);
  }

  // The M21 call for one asset, and the server's own answer written back onto
  // the optimistic asset (its stored path and thumbnail URL). `upload` is a File
  // (multipart, saved under data/uploads); otherwise the asset must already be a
  // file under data/uploads (`asset.path`, a material-library file). An asset
  // with neither (a Studio source that has produced no file) is REFUSED here, so
  // the user is told it was not saved instead of seeing a thumbnail that is gone
  // on reload.
  function _assetRequest(fam, asset, upload, repaint) {
    return () => _queue(fam.id, async () => {
      let out;
      if (upload) {
        const fd = new FormData();
        fd.append('file', upload, upload.name || 'upload');
        fd.append('id', asset.id);
        fd.append('title', asset.title || upload.name || '');
        out = await _api('POST', _pieceUrl(fam.id) + '/assets', fd);
      } else if (asset.path) {
        out = await _api('POST', _pieceUrl(fam.id) + '/assets', { id: asset.id, path: asset.path, title: asset.title });
      } else {
        throw new Error('this source has not produced a file that can be saved yet');
      }
      const saved = ((out && out.assets) || []).find((a) => a.id === asset.id);
      if (saved) Object.assign(asset, { kind: saved.kind, title: saved.title, path: saved.path, src: saved.src });
      if (upload) _mats.promise = null; // the file now sits in the library's Uploads folder
      if (repaint) repaint(); // the thumbnail is now the server's, not the local blob
      return out;
    });
  }
  function _assetUndo(fam, asset) {
    return () => _queue(fam.id, () => _api('DELETE', _pieceUrl(fam.id) + '/assets/' + encodeURIComponent(asset.id)));
  }

  function _attachAsset(camp, fam, asset, card, upload) {
    const st = _state(camp.id);
    const cardIdx = card ? st.creates.indexOf(card) : -1;
    DeskV1Store.write({
      label: `Added “${asset.title}” to “${fam.title}”`,
      apply: () => {
        fam.assets = (fam.assets || []).concat([asset]);
        if (card) _closeCreate(camp, card);
        _repaint();
      },
      unapply: () => {
        fam.assets = (fam.assets || []).filter((a) => a.id !== asset.id);
        if (card && cardIdx >= 0 && st.creates.indexOf(card) < 0) st.creates.splice(Math.min(cardIdx, st.creates.length), 0, card);
      },
      repaint: () => _repaint(),
      request: _assetRequest(fam, asset, upload, () => _repaint()),
      undoRequest: _assetUndo(fam, asset),
    });
  }

  function _pickExistingArticle(camp, fam, art, card) {
    const st = _state(camp.id);
    const cardIdx = st.creates.indexOf(card);
    const prev = { title: fam.title, wordCount: fam.wordCount, source: fam.source || null };
    const patch = (title, words, source) => () => _queue(fam.id, () => _api('PATCH', _pieceUrl(fam.id),
      { title, word_count: words == null ? null : words, source }));
    DeskV1Store.write({
      label: `Picked “${art.title}”`,
      apply: () => {
        fam.title = art.title; fam.wordCount = art.words; fam.source = { kind: 'article', ref: art.id };
        _closeCreate(camp, card); _repaint();
      },
      unapply: () => {
        fam.title = prev.title; fam.wordCount = prev.wordCount; fam.source = prev.source;
        if (st.creates.indexOf(card) < 0) st.creates.splice(Math.min(cardIdx, st.creates.length), 0, card);
      },
      repaint: () => _repaint(),
      request: patch(art.title, art.words, { kind: 'article', ref: art.id }),
      undoRequest: patch(prev.title, prev.wordCount, prev.source),
    });
  }

  // The piece leaves its create-card for the list the moment its storyboard
  // opens, so coming back shows it as a row (with its render status).
  function _openStoryboard(camp, fam) {
    const card = _state(camp.id).creates.find((c) => c.familyId === fam.id);
    if (card) _closeCreate(camp, card);
    window.deskV1Nav('storyboard', { campaignId: camp.id, familyId: fam.id });
  }

  // Studio's New video / image / article tile: queue an empty create-card on
  // the campaign's What before navigating there.
  function deskV1WhatStartCreate(campaignId, typeId) {
    const camp = _campaign(campaignId);
    const type = TYPES.find((t) => t.id === typeId);
    if (camp && type) _addCreate(camp, type);
  }

  // ── repaint ──────────────────────────────────────────────────────────────
  function _repaint(focusSel) {
    if (!_isMounted()) return;
    const camp = _camp();
    if (!camp) return;
    _paint(_cur.el, camp);
    if (focusSel) { const f = _cur.el.querySelector(focusSel); if (f) f.focus({ preventScroll: true }); }
  }

  // ── view ─────────────────────────────────────────────────────────────────
  function _fmtTime(iso) {
    try {
      const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
      const parts = new Intl.DateTimeFormat('en-US', { timeZone: cfg.user_timezone || undefined, weekday: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(iso));
      const get = (t) => (parts.find((p) => p.type === t) || {}).value || '';
      return `${get('weekday')} ${get('hour')}:${get('minute')}`;
    } catch (e) { return ''; }
  }

  // The short handle a status chip uses to name the account a version is on
  // (frame 5a: `✓ 𝕏 published · ⚠ LinkedIn in review · ○ blog planned`).
  const CHANNEL_SHORT = { x: '𝕏', linkedin: 'LinkedIn' };
  function _channelShort(ch) {
    if (!ch) return '';
    return CHANNEL_SHORT[ch.platform] || String(ch.identity || ch.platform || '').replace(/^Clayrune\s+/i, '').toLowerCase();
  }

  // Per-version status summary, right-aligned on the row. A video still
  // rendering says so (frame 5a: `⟳ rendering`) instead of listing versions.
  function _statusHTML(fam) {
    if (fam.render && fam.render.status === 'rendering') {
      const n = fam.render.progress != null ? Number(fam.render.progress) : 0;
      return `<span class="desk-v1-what-status-item" data-state="sending"><span class="desk-v1-state-glyph" aria-hidden="true">⟳</span> Rendering ${esc(n)}%</span>` +
        `<span class="desk-v1-what-progress" role="progressbar" aria-label="Rendering ${esc(fam.title)}" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${esc(n)}"><span style="width:${esc(n)}%"></span></span>`;
    }
    // An article saved from the writer is handed to review as a whole, even
    // before Where has placed it on an account.
    if (fam.draft && fam.draft.status === 'in_review') {
      const { glyph } = DeskV1Kit.stateLabel('needs_review');
      return `<span class="desk-v1-what-status-item" data-state="needs_review"><span class="desk-v1-state-glyph" aria-hidden="true">${esc(glyph)}</span> in review</span>`;
    }
    const live = DeskV1Kit.pieceVersions(fam);
    if (!live.length) return '<span class="desk-v1-what-status-item desk-v1-what-status-none">no versions yet</span>';
    const multi = live.length > 1;
    return live.map((v) => {
      const { glyph, word } = DeskV1Kit.stateLabel(v.state);
      const ch = multi ? _channelShort(_channel(v.channelId)) : '';
      const when = _SCHEDULED.has(v.state) && v.publishAt ? ` ${esc(_fmtTime(v.publishAt))}` : '';
      const w = String(word).toLowerCase().replace(/^verified /, '');
      return `<span class="desk-v1-what-status-item" data-state="${esc(v.state)}" data-version-id="${esc(v.id)}">` +
        `<span class="desk-v1-state-glyph" aria-hidden="true">${esc(glyph)}</span>${ch ? ` ${esc(ch)}` : ''} ${esc(w)}${when}</span>`;
    }).join('');
  }

  function _thumbHTML(a) {
    return a.src
      ? `<img class="desk-v1-what-thumb" data-asset-id="${esc(a.id)}" src="${esc(a.src)}" alt="${esc(a.title || 'Asset')}" title="${esc(a.title || '')}">`
      : `<span class="desk-v1-what-thumb desk-v1-what-thumb-blank" data-asset-id="${esc(a.id)}" title="${esc(a.title || '')}" aria-label="${esc(a.title || 'Asset')}">${esc(a.kind === 'video' ? '▶' : '▣')}</span>`;
  }

  function _rowHTML(fam) {
    const assets = DeskV1Kit.pieceAssets(fam);
    const media = assets.length
      ? `<div class="desk-v1-what-media" data-what-media>${assets.map(_thumbHTML).join('')}` +
        `<button type="button" class="desk-v1-what-addmedia" data-add-media="${esc(fam.id)}" aria-haspopup="menu">＋ Add media</button></div>`
      : '';
    return `<div class="desk-v1-camp-card desk-v1-what-row" data-what-row data-family-id="${esc(fam.id)}" data-kind="${esc(fam.kind || '')}">
      <span class="desk-v1-what-kind">${esc(DeskV1Kit.pieceKindWord(fam.kind).toUpperCase())}</span>
      <div class="desk-v1-what-main">
        <button type="button" class="desk-v1-what-title" data-primary-action>${esc(fam.title)}</button>
        <div class="desk-v1-what-on" data-what-on>${esc(DeskV1Kit.pieceChannelsText(fam))}</div>
        ${media}
        <div class="desk-v1-camp-card-result" aria-live="polite"></div>
      </div>
      <div class="desk-v1-what-status" data-what-status>${_statusHTML(fam)}</div>
      <div class="desk-v1-camp-card-more">
        <button type="button" class="desk-v1-camp-card-morebtn" data-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
      </div>
    </div>`;
  }

  function _sourceTilesHTML(card, kind) {
    return `<div class="desk-v1-what-sources" role="group" aria-label="Choose a source">${SOURCES[kind].map((s) => `
      <button type="button" class="desk-v1-what-source" data-what-source="${esc(s.id)}" aria-label="${esc(`${s.label}. ${s.hint}`)}" title="${esc(s.hint)}">
        <span class="desk-v1-what-source-glyph" aria-hidden="true">${esc(s.glyph)}</span>
        <span class="desk-v1-what-source-name" data-short="${esc(s.short || s.label)}">${esc(s.label)}</span>
        <span class="desk-v1-what-source-hint"${s.id === 'generate' ? ' data-what-abstract-only' : ''}>${esc(s.hint)}</span>
      </button>`).join('')}</div>`;
  }

  // Live, a library tile is one FILE (a piece is given a file, not a folder), so a
  // folder's files are listed flat, the folder name standing where the file count
  // does in demo. A video has no thumbnail URL (/api/serve-image serves images).
  function _libTilesHTML(kind) {
    const lib = (_fx().materialLibrary || {})[kind === 'video' ? 'video' : 'image'] || [];
    if (!_isLive()) {
      return lib.map((m) => `
        <button type="button" class="desk-v1-what-folder" data-what-folder="${esc(m.id)}">
          <img class="desk-v1-what-folder-thumb" src="${esc(m.thumb)}" alt="">
          <span class="desk-v1-what-folder-name">${esc(m.title)}</span>
          <span class="desk-v1-what-folder-count">${esc(m.files)} files</span>
        </button>`).join('');
    }
    if (_mats.error) return `<div class="desk-v1-what-later" data-what-lib-error>Could not load the material library: ${esc(_mats.error)}</div>`;
    const files = lib.flatMap((m) => (m.items || []).map((it) => ({ it, folder: m.title })));
    if (!files.length) return '<div class="desk-v1-what-later" data-what-lib-empty>The material library has no files yet. Upload one from this computer.</div>';
    return files.map(({ it, folder }) => `
        <button type="button" class="desk-v1-what-folder" data-what-libfile="${esc(it.path)}">
          ${it.src ? `<img class="desk-v1-what-folder-thumb" src="${esc(it.src)}" alt="">` : '<span class="desk-v1-what-folder-thumb desk-v1-what-thumb-blank" aria-hidden="true">▶</span>'}
          <span class="desk-v1-what-folder-name">${esc(it.title)}</span>
          <span class="desk-v1-what-folder-count">${esc(folder)}</span>
        </button>`).join('');
  }

  function _uploadBodyHTML(kind) {
    return `<div class="desk-v1-what-upload">
      <div class="desk-v1-what-dropzone" data-what-dropzone tabindex="0" role="group" aria-label="Drop a file here">
        <div class="desk-v1-what-dropzone-text">Drop ${kind === 'video' ? 'a video' : 'an image'} here</div>
        <button type="button" class="btn-secondary" data-what-browse>Browse this computer</button>
        <input type="file" class="desk-v1-what-file" data-what-file accept="${kind === 'video' ? 'video/*' : 'image/*'}" hidden>
      </div>
      <div class="desk-v1-what-lib-title">Material library</div>
      <div class="desk-v1-what-lib" data-what-lib>${_libTilesHTML(kind)}</div>
    </div>`;
  }

  function _browseBodyHTML() {
    const arts = _fx().existingArticles || [];
    if (_isLive() && _mats.error) return `<div class="desk-v1-what-later" data-what-lib-error>Could not load existing articles: ${esc(_mats.error)}</div>`;
    if (_isLive() && !arts.length) return '<div class="desk-v1-what-later" data-what-lib-empty>No other articles in the Desk yet.</div>';
    return `<div class="desk-v1-what-browse" data-what-browse-list>${arts.map((a) => `
      <button type="button" class="desk-v1-what-existing" data-what-existing="${esc(a.id)}">
        <span class="desk-v1-what-existing-title">${esc(a.title)}</span>
        <span class="desk-v1-what-existing-meta">${esc(a.words)} words</span>
      </button>`).join('')}</div>`;
  }

  function _createHTML(card, fam, camp) {
    const type = TYPES.find((t) => t.id === card.typeId) || TYPES[0];
    const kind = fam.kind;
    const srcList = SOURCES[kind];
    let bodyHTML = '';
    if (srcList && !card.source) {
      bodyHTML = _sourceTilesHTML(card, kind);
    } else if (srcList) {
      const src = srcList.find((s) => s.id === card.source);
      const chip = `<div class="desk-v1-what-chiprow"><span class="desk-v1-what-chip" data-what-chip>${esc(src ? src.label : card.source)}</span>` +
        `<button type="button" class="desk-v1-what-change" data-what-change-source>Change source</button></div>`;
      let inner;
      if (card.source === 'upload') inner = _uploadBodyHTML(kind);
      else if (card.source === 'browse') inner = _browseBodyHTML();
      else {
        const studioBody = window.DeskV1Studio ? window.DeskV1Studio.sourceBodyHTML(card.source, { card, fam, camp }) : null;
        inner = studioBody || `<div class="desk-v1-what-later" data-what-later>${esc(R2_8_NOTE[card.source] || 'Opens in Studio (R2-8).')}</div>`;
      }
      bodyHTML = chip + inner;
    } else {
      bodyHTML = `<div class="desk-v1-what-later" data-what-later>Write the post on its piece page.</div>
        <div class="desk-v1-what-createactions"><button type="button" class="btn-secondary" data-what-open>Open piece</button></div>`;
    }
    return `<div class="desk-v1-what-create" data-what-create="${esc(card.id)}" data-family-id="${esc(fam.id)}" data-kind="${esc(kind)}">
      <div class="desk-v1-what-create-head">
        <span class="desk-v1-what-kind">${esc(DeskV1Kit.pieceKindWord(kind).toUpperCase())}</span>
        <input type="text" class="desk-v1-what-create-title" data-what-title value="${esc(fam.title)}" aria-label="Title of the new ${esc(kind)}">
        ${type.preview ? '<span class="desk-v1-where-preview" data-preview>Preview · not connected</span>' : ''}
        <button type="button" class="desk-v1-what-create-x" data-what-remove aria-label="Remove this new ${esc(kind)}">✕</button>
      </div>
      ${bodyHTML}
    </div>`;
  }

  function _trayHTML() {
    return `<div class="desk-v1-what-tray" data-what-tray>
      <div class="desk-v1-what-tray-title">CONTENT TYPES<span class="desk-v1-what-tray-drag"> — DRAG ONE INTO THE CAMPAIGN</span></div>
      <div class="desk-v1-what-tray-row">${TYPES.map((t) => `
        <div class="desk-v1-what-type" role="button" tabindex="0" data-what-type="${esc(t.id)}"
             aria-label="${esc(`${t.label}. Press Enter to add a ${t.label.toLowerCase()} to this campaign`)}">
          <span class="desk-v1-what-type-glyph" aria-hidden="true">${esc(t.glyph)}</span>
          <span class="desk-v1-what-type-name">${esc(t.label)}</span>
          ${t.preview ? '<span class="desk-v1-where-preview" data-preview>Preview</span>' : ''}
        </div>`).join('')}</div>
    </div>`;
  }

  // The article writer (frame 12) takes the list's place inside the campaign
  // frame; Save / Back return to the list.
  function _paintWriter(el, camp, st) {
    const fam = _familiesFor(camp.id).find((f) => f.id === st.writer.familyId);
    if (!fam || !window.DeskV1Studio) { st.writer = null; return false; }
    el.innerHTML = `<div class="desk-v1-what" data-what>${window.DeskV1Studio.writerHTML(fam, camp, st.writer.tab)}</div>`;
    window.DeskV1Studio.wireWriter(el.querySelector('[data-writer]'), fam, camp, st.writer.tab, {
      onTab: (i) => { st.writer.tab = i; _repaint(`[data-writer-tab="${i}"]`); },
      onBack: () => { st.writer = null; _repaint(); },
      onSave: () => {
        let undoMark = null;
        DeskV1Store.write({
          label: `Saved “${fam.title}” to What`,
          apply: () => { undoMark = window.DeskV1Studio.markInReview(fam); st.writer = null; _repaint(); },
          unapply: () => { if (undoMark) undoMark(); },
          repaint: () => _repaint(),
          request: () => window.DeskV1Studio.persistInReview(fam, undoMark),
          undoRequest: () => window.DeskV1Studio.persistInReview(fam, undoMark, true),
        });
      },
    });
    return true;
  }

  function _paint(el, camp) {
    const st = _state(camp.id);
    if (st.writer && _paintWriter(el, camp, st)) return;
    const all = _familiesFor(camp.id);
    const openIds = new Set(st.creates.map((c) => c.familyId));
    const pred = FILTER_PRED[st.filter] || FILTER_PRED.all;
    const rows = all.filter((f) => !openIds.has(f.id) && pred(f));
    const creates = st.creates.map((c) => ({ c, f: all.find((f) => f.id === c.familyId) })).filter((x) => x.f);
    const banner = typeof _bridge().suggestedBannerHTML === 'function' ? _bridge().suggestedBannerHTML(camp) : '';
    el.innerHTML = `<div class="desk-v1-what" data-what>
      <div class="desk-v1-what-panel" data-what-list id="desk-v1-camp-listarea">
        <div class="desk-v1-what-filters" role="group" aria-label="Filter pieces">${FILTERS.map((f) =>
          `<button type="button" class="desk-v1-what-filter" data-what-filter="${f.id}" aria-pressed="${st.filter === f.id}">${esc(f.label)}</button>`).join('')}</div>
        ${banner}
        <div class="desk-v1-what-creates" data-what-creates>${creates.map((x) => _createHTML(x.c, x.f, camp)).join('')}</div>
        <div class="desk-v1-what-rows" data-what-rows>${rows.map(_rowHTML).join('') ||
          `<div class="desk-v1-camp-empty">${all.length && st.filter !== 'all' ? 'Nothing matches this filter.' : creates.length ? '' : 'No pieces yet — drag a content type from the tray below.'}</div>`}</div>
      </div>
      ${_trayHTML()}
    </div>`;
    _wire(el, camp, st);
  }

  // ── wiring ───────────────────────────────────────────────────────────────
  function _attachFromFile(camp, fam, card, file, kind) {
    const isImg = kind === 'image' || /^image\//.test(file.type || '');
    const src = isImg && typeof URL !== 'undefined' && URL.createObjectURL ? URL.createObjectURL(file) : null;
    _attachAsset(camp, fam, { id: 'asset-' + _uid(), kind: kind === 'video' ? 'video' : 'image', title: file.name || 'Upload', src }, card, file);
  }

  function _wire(el, camp, st) {
    el.querySelectorAll('[data-what-filter]').forEach((b) => b.onclick = () => { st.filter = b.dataset.whatFilter; _repaint(`[data-what-filter="${st.filter}"]`); });

    const acceptBtn = el.querySelector('[data-suggested-accept-all]');
    if (acceptBtn && typeof _bridge().acceptSuggested === 'function') acceptBtn.onclick = () => _bridge().acceptSuggested(camp);
    DeskV1Kit.bindBecauseChips(el, camp.projectId);

    // rows
    el.querySelectorAll('[data-what-row]').forEach((rowEl) => {
      const fam = _familiesFor(camp.id).find((f) => f.id === rowEl.dataset.familyId);
      if (!fam) return;
      const primary = rowEl.querySelector('[data-primary-action]');
      if (primary && typeof _bridge().runPrimary === 'function') primary.onclick = (e) => { e.stopPropagation(); _bridge().runPrimary(fam, camp); };
      const more = rowEl.querySelector('[data-more-btn]');
      if (more && typeof _bridge().openCardMenu === 'function') more.onclick = (e) => { e.stopPropagation(); _bridge().openCardMenu(e.currentTarget, fam, camp); };
      const add = rowEl.querySelector('[data-add-media]');
      if (add) add.onclick = (e) => { e.stopPropagation(); openAddMedia(add, fam, () => _repaint()); };
      rowEl.addEventListener('click', () => { if (typeof _bridge().setSelection === 'function') _bridge().setSelection('card', fam.id, fam.title); });
    });

    // create-cards
    el.querySelectorAll('[data-what-create]').forEach((cardEl) => {
      const card = st.creates.find((c) => c.id === cardEl.dataset.whatCreate);
      const fam = _familiesFor(camp.id).find((f) => f.id === cardEl.dataset.familyId);
      if (!card || !fam) return;
      const title = cardEl.querySelector('[data-what-title]');
      title.onchange = () => _renameCreate(fam, title);
      cardEl.querySelector('[data-what-remove]').onclick = () => _removeCreate(camp, card);
      cardEl.querySelectorAll('[data-what-source]').forEach((b) => b.onclick = () => {
        card.source = b.dataset.whatSource;
        // Write new opens the article writer, Create new the storyboard: each
        // is a page of its own, not a body inside the card.
        if (card.source === 'write' && window.DeskV1Studio) { _closeCreate(camp, card); st.writer = { familyId: fam.id, tab: 0 }; _repaint(); return; }
        if (card.source === 'create' && typeof window.deskV1Nav === 'function') { _openStoryboard(camp, fam); return; }
        if (_isLive() && (card.source === 'upload' || card.source === 'browse')) {
          _ensureMaterials(camp.id).then(() => _repaint('[data-what-change-source]'));
          return;
        }
        _repaint('[data-what-change-source]');
      });
      if (window.DeskV1Studio) {
        window.DeskV1Studio.wireSourceBody(cardEl, { card, fam, camp }, {
          attach: (asset) => _attachAsset(camp, fam, asset, card),
          repaint: () => _repaint(),
          openStoryboard: () => _openStoryboard(camp, fam),
        });
      }
      const change = cardEl.querySelector('[data-what-change-source]');
      if (change) change.onclick = () => { card.source = null; _repaint(`[data-what-create="${card.id}"] [data-what-source]`); };
      const open = cardEl.querySelector('[data-what-open]');
      if (open) open.onclick = () => { _closeCreate(camp, card); _repaint(); if (typeof _bridge().runPrimary === 'function') _bridge().runPrimary(fam, camp); };

      // Upload body
      const file = cardEl.querySelector('[data-what-file]');
      const browse = cardEl.querySelector('[data-what-browse]');
      if (browse && file) browse.onclick = () => file.click();
      if (file) file.onchange = () => { if (file.files && file.files[0]) _attachFromFile(camp, fam, card, file.files[0], fam.kind); };
      const zone = cardEl.querySelector('[data-what-dropzone]');
      if (zone) {
        ['dragenter', 'dragover'].forEach((t) => zone.addEventListener(t, (e) => { e.preventDefault(); zone.classList.add('pd-drop-hover'); }));
        zone.addEventListener('dragleave', () => zone.classList.remove('pd-drop-hover'));
        zone.addEventListener('drop', (e) => {
          e.preventDefault(); zone.classList.remove('pd-drop-hover');
          const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
          if (f) _attachFromFile(camp, fam, card, f, fam.kind);
        });
      }
      cardEl.querySelectorAll('[data-what-libfile]').forEach((b) => b.onclick = () => {
        const lib = (_fx().materialLibrary || {})[fam.kind === 'video' ? 'video' : 'image'] || [];
        const it = lib.flatMap((m) => m.items || []).find((x) => x.path === b.dataset.whatLibfile);
        if (it) _attachAsset(camp, fam, { id: 'asset-' + _uid(), kind: it.kind, title: it.title, path: it.path, src: it.src }, card);
      });
      cardEl.querySelectorAll('[data-what-folder]').forEach((b) => b.onclick = () => {
        const lib = (_fx().materialLibrary || {})[fam.kind === 'video' ? 'video' : 'image'] || [];
        const m = lib.find((x) => x.id === b.dataset.whatFolder);
        if (m) _attachAsset(camp, fam, { id: 'asset-' + _uid(), kind: fam.kind === 'video' ? 'video' : 'image', title: m.title, src: m.thumb }, card);
      });
      // Browse existing (article)
      cardEl.querySelectorAll('[data-what-existing]').forEach((b) => b.onclick = () => {
        const art = (_fx().existingArticles || []).find((a) => a.id === b.dataset.whatExisting);
        if (art) _pickExistingArticle(camp, fam, art, card);
      });
    });

    // tray
    el.querySelectorAll('[data-what-type]').forEach((node) => {
      const type = TYPES.find((t) => t.id === node.dataset.whatType);
      node.addEventListener('click', (e) => {
        if (Date.now() - _lastDragEnd < 300) { e.stopImmediatePropagation(); e.preventDefault(); return; }
        _addCreate(camp, type);
      });
      node.addEventListener('pointerdown', (e) => _beginDrag(node, e, type));
      node.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); _addCreate(camp, type); }
      });
    });
  }

  // ── drag: a content type onto the list panel ─────────────────────────────
  let _drag = null;
  function _hoverList(x, y) {
    const hit = _listHit(x, y);
    const list = _cur && _cur.el && _cur.el.querySelector('[data-what-list]');
    if (list) list.classList.toggle('pd-drop-hover', hit);
  }
  function _listHit(x, y) {
    const el = document.elementFromPoint(x, y);
    return !!(el && el.closest && el.closest('[data-what-list]'));
  }
  function _beginDrag(node, e, type) {
    if (!window.PointerDrag) return;
    window.PointerDrag.begin(node, e, {
      isDragActive: () => !!_drag,
      getDragState: () => _drag,
      setDragState: (s) => { _drag = s; },
      data: { kind: 'content-type', typeId: type.id, label: type.label },
      draggingClass: 'desk-v1-what-dragging',
      activeBodyClass: 'desk-v1-what-drag-active',
      ghostClass: 'pd-ghost desk-v1-home-drag-ghost',
      ghostHTML: () => `<span class="desk-v1-home-drag-ghost-inner">${esc(type.label)}</span>`,
      ghostRotationDeg: -3,
      ghostOffsetX: 18, ghostOffsetY: 18,
      onActivate: () => { const l = _cur && _cur.el && _cur.el.querySelector('[data-what-list]'); if (l) l.classList.add('pd-drop-target'); },
      onMove: (s, x, y) => _hoverList(x, y),
      onDrop: (s, x, y) => (_listHit(x, y) ? { type: 'list' } : null),
      afterDrop: (s, target) => { if (target) { const camp = _camp(); if (camp) _addCreate(camp, type); } },
      onTeardown: () => { const l = _cur && _cur.el && _cur.el.querySelector('[data-what-list]'); if (l) l.classList.remove('pd-drop-target', 'pd-drop-hover'); },
      onEnd: (s, wasDrag) => { if (wasDrag) _lastDragEnd = Date.now(); },
    });
  }

  // ── `＋ Add media`: pick a Material library folder (rows and the piece page) ─
  // `onDone` repaints whichever surface opened it.
  function openAddMedia(triggerEl, fam, onDone) {
    if (_isLive()) {
      _ensureMaterials(fam.campaignId).then(() => _openAddMediaLive(triggerEl, fam, onDone));
      return;
    }
    const lib = _fx().materialLibrary || {};
    const isVideo = fam.kind === 'video';
    const folders = (isVideo ? lib.video : lib.image) || [];
    const items = folders.map((m) => ({ id: m.id, label: `${m.title} (${m.files})` }));
    if (!items.length) { DeskV1Kit.toast('No material folders to add from yet.'); return; }
    DeskV1Kit.addToMenu(triggerEl, items, (id) => {
      const m = folders.find((x) => x.id === id);
      if (!m) return;
      const asset = { id: 'asset-' + _uid(), kind: isVideo ? 'video' : 'image', title: m.title, src: m.thumb };
      _addMedia(fam, asset, onDone);
    }, { noAppendNew: true });
  }

  function _addMedia(fam, asset, onDone) {
    const repaint = () => { if (onDone) onDone(); };
    DeskV1Store.write({
      label: `Added “${asset.title}” to “${fam.title}”`,
      apply: () => { fam.assets = (fam.assets || []).concat([asset]); repaint(); },
      unapply: () => { fam.assets = (fam.assets || []).filter((a) => a.id !== asset.id); },
      repaint,
      request: _assetRequest(fam, asset, null, repaint),
      undoRequest: _assetUndo(fam, asset),
    });
  }

  // Live: the menu lists the library's FILES (a piece carries files), each one
  // an M21 attach by path. A failed materials read is said, not shown as empty.
  function _openAddMediaLive(triggerEl, fam, onDone) {
    if (_mats.error) { DeskV1Kit.toast(`Could not load the material library: ${_mats.error}`); return; }
    const lib = _fx().materialLibrary || {};
    const files = ((fam.kind === 'video' ? lib.video : lib.image) || [])
      .flatMap((m) => (m.items || []).map((it) => ({ it, folder: m.title })));
    if (!files.length) { DeskV1Kit.toast('The material library has no files to add yet.'); return; }
    DeskV1Kit.addToMenu(triggerEl, files.map(({ it, folder }) => ({ id: it.path, label: `${folder} / ${it.title}` })), (path) => {
      const hit = files.find((x) => x.it.path === path);
      if (!hit) return;
      _addMedia(fam, { id: 'asset-' + _uid(), kind: hit.it.kind, title: hit.it.title, path: hit.it.path, src: hit.it.src }, onDone);
    }, { noAppendNew: true });
  }

  // MC-1024: Studio's `Use in a campaign`. A library item Studio made becomes a
  // piece of its own in the campaign's What (one piece, one asset), the same two
  // writes a create-card's library pick makes (M14 create, M21 attach by path).
  // Live needs the item's FILE: an item with none is refused here, before the
  // piece is made, so no empty piece is left behind. Resolves true once written.
  async function useLibraryItem(campaignId, item) {
    const camp = _campaign(campaignId);
    if (!camp || !item) return false;
    const kind = item.kind === 'video' ? 'video' : 'image';
    if (_isLive() && !item.path) {
      DeskV1Kit.toast(`“${item.title}” has no saved file yet, so it cannot be added to a campaign.`);
      return false;
    }
    const fam = { id: 'fam-new-' + _uid(), campaignId: camp.id, kind, title: item.title, assets: [], versions: [] };
    const asset = { id: 'asset-' + _uid(), kind, title: item.title, path: item.path || undefined, src: item.src || null };
    const r = await DeskV1Store.write({
      label: `Added “${item.title}” to “${(camp.plan && camp.plan.title) || 'the campaign'}”`,
      apply: () => { fam.assets = [asset]; _fx().families.push(fam); _repaint(); },
      unapply: () => { const i = _fx().families.indexOf(fam); if (i >= 0) _fx().families.splice(i, 1); },
      repaint: () => _repaint(),
      request: async () => {
        await _queue(fam.id, () => _api('POST', '/api/desk/pieces', { id: fam.id, campaign_id: camp.id, kind, title: fam.title }));
        return _assetRequest(fam, asset, null, () => _repaint())();
      },
      undoRequest: () => _queue(fam.id, () => _api('DELETE', _pieceUrl(fam.id))),
    });
    return !!(r && r.ok);
  }
  // Studio saves a file into the library behind this campaign's cached read.
  function invalidateMaterials() { _mats.promise = null; }

  // ── entry ────────────────────────────────────────────────────────────────
  function deskV1FillWhat(el, params, camp) {
    const st = _state(camp.id);
    st.el = el;
    _cur = st;
    _paint(el, camp);
  }
  // Repaint the mounted What body (campaign.js's card-menu commands call this).
  function deskV1RepaintWhat() { _repaint(); }

  window.deskV1FillWhat = deskV1FillWhat;
  window.deskV1RepaintWhat = deskV1RepaintWhat;
  window.deskV1WhatAddMedia = openAddMedia;
  window.deskV1WhatUseLibraryItem = useLibraryItem;
  window.deskV1WhatInvalidateMaterials = invalidateMaterials;
  window.deskV1WhatStartCreate = deskV1WhatStartCreate;
})();
