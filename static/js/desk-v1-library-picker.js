// Desk v1 — ONE "Pick from library" chooser, used wherever content is made
// (Ron 2026-10-06): a storyboard scene's picture, an image's start/reference
// picture, a picture inside an article. Same chooser, same wording everywhere.
// Window-bridged module, no `import` (ground rule 1).
//
//   window.DeskV1LibraryPicker.label                    'Pick from library' (the button text)
//   window.DeskV1LibraryPicker.buttonHTML(attrs, cls, aria)   a button carrying that wording
//   window.DeskV1LibraryPicker.open({ kinds, onPick, returnFocus })
//   window.DeskV1LibraryPicker.files(kinds)             Promise of every file the library holds
//
// It lists EVERY folder of the Material library (Storyboards and Uploads included),
// with a search over file and folder names. Choosing a file calls
// `onPick({ path, kind, title, src, folder })` and closes; the caller says what it
// did with it. Nothing is copied here: the item IS the library file, `path` relative
// to data/uploads as the library lists it. `kinds` is ['image'] (default) or
// ['image','video']; each tile has a View button that opens it in the library viewer
// (desk-v1-library-viewer.js) without choosing it.
(function () {
  const LABEL = 'Pick from library';

  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  let _open = null; // the one open chooser

  function buttonHTML(attrs, cls, aria) {
    return `<button type="button" class="${esc(cls || 'btn-secondary')}" ${attrs || ''}${aria ? ` aria-label="${esc(aria)}"` : ''}>${LABEL}</button>`;
  }

  // Every file of the kinds asked for, each tagged with its folder. Live reads the
  // library fresh (a picture made a minute ago must be there); demo reads the fixture.
  function _read() {
    const store = window.DeskV1Store;
    if (store.live()) return store.api('GET', '/api/desk/materials').then((m) => (m && m.library) || {});
    return Promise.resolve(store.state().materialLibrary || {});
  }
  function _flatten(lib, kinds) {
    const folders = [];
    const files = [];
    kinds.forEach((k) => (lib[k] || []).forEach((f) => {
      const items = (f.items || []).map((it) => Object.assign({}, it, { kind: it.kind || k, folder: f.title }));
      folders.push({ key: `${k}:${f.title}`, title: f.title, count: items.length });
      items.forEach((it) => files.push(Object.assign(it, { folderKey: `${k}:${f.title}` })));
    }));
    return { folders, files };
  }

  function _close() {
    const o = _open;
    if (!o) return;
    _open = null;
    document.removeEventListener('keydown', o.onKey, true);
    o.el.remove();
    if (o.returnFocus && o.returnFocus.isConnected) o.returnFocus.focus({ preventScroll: true });
  }

  function _tileHTML(it, n) {
    return `<div class="desk-v1-lp-tile" data-lp-tile="${esc(it.id)}">
      <button type="button" class="desk-v1-lp-pick" data-lp-pick="${n}" aria-label="Use ${esc(it.title)} from ${esc(it.folder)}">
        <span class="desk-v1-lp-thumb">${it.src ? `<img src="${esc(it.src)}" alt="" loading="lazy">` : `<span aria-hidden="true">${it.kind === 'video' ? '▶' : '🖼'}</span>`}</span>
        <span class="desk-v1-lp-name">${esc(it.title)}</span>
        <span class="desk-v1-lp-folder">${esc(it.folder)}</span>
      </button>
      <button type="button" class="desk-v1-lp-view" data-lp-view="${n}" aria-label="View ${esc(it.title)} full size" title="View full size">⤢</button>
    </div>`;
  }

  function open(opts) {
    opts = opts || {};
    _close();
    const kinds = opts.kinds && opts.kinds.length ? opts.kinds : ['image'];
    const noun = kinds.length > 1 ? 'files' : 'pictures';
    const st = { query: '', folder: '', files: [], folders: [], error: null, loading: true, shown: [] };
    const el = document.createElement('div');
    el.className = 'desk-v1-lp-overlay';
    el.setAttribute('data-lp', '');
    el.innerHTML = `<div class="desk-v1-lp" role="dialog" aria-modal="true" aria-labelledby="desk-v1-lp-title">
      <div class="desk-v1-lp-head">
        <h2 class="desk-v1-lp-title" id="desk-v1-lp-title">${LABEL}</h2>
        <button type="button" class="desk-v1-lp-close" data-lp-close aria-label="Close" title="Close (Esc)">✕</button>
      </div>
      <input type="search" class="desk-v1-sb-edit-input desk-v1-lp-search" data-lp-search placeholder="Search the library" aria-label="Search the library" autocomplete="off">
      <div class="desk-v1-lp-folders" data-lp-folders role="group" aria-label="Folders"></div>
      <div class="desk-v1-lp-body" data-lp-body></div>
    </div>`;
    const returnFocus = opts.returnFocus || document.activeElement;
    const search = el.querySelector('[data-lp-search]');
    const body = el.querySelector('[data-lp-body]');
    const folderBar = el.querySelector('[data-lp-folders]');
    const dialog = el.querySelector('.desk-v1-lp');

    const paint = () => {
      folderBar.innerHTML = st.folders.length ? [{ key: '', title: 'All folders', count: st.files.length }].concat(st.folders).map((f) =>
        `<button type="button" class="desk-v1-lp-chip" data-lp-folder="${esc(f.key)}" aria-pressed="${st.folder === f.key}">${esc(f.title)} <span>${f.count}</span></button>`).join('') : '';
      if (st.loading) { body.innerHTML = '<div class="desk-v1-camp-empty" data-lp-loading>Loading the library…</div>'; return; }
      if (st.error) { body.innerHTML = `<div class="desk-v1-camp-empty" data-lp-error>Could not load the library: ${esc(st.error)}</div>`; return; }
      const q = st.query.trim().toLowerCase();
      st.shown = st.files.filter((f) => (!st.folder || f.folderKey === st.folder)
        && (!q || `${f.title} ${f.folder}`.toLowerCase().includes(q)));
      body.innerHTML = st.shown.length
        ? `<div class="desk-v1-lp-grid" data-lp-grid>${st.shown.map(_tileHTML).join('')}</div>`
        : `<div class="desk-v1-camp-empty" data-lp-empty>${st.files.length ? `No ${noun} match that search.` : `The library has no ${noun} yet. Add one from Studio, or upload one.`}</div>`;
      body.querySelectorAll('[data-lp-pick]').forEach((b) => {
        b.onclick = () => {
          const it = st.shown[Number(b.dataset.lpPick)];
          if (!it) return;
          _close();
          if (typeof opts.onPick === 'function') opts.onPick({ path: it.path, kind: it.kind, title: it.title, src: it.src || '', folder: it.folder });
        };
      });
      body.querySelectorAll('[data-lp-view]').forEach((b) => {
        b.onclick = () => { if (window.DeskV1LibraryViewer) window.DeskV1LibraryViewer.open(st.shown, Number(b.dataset.lpView)); };
      });
    };
    search.oninput = () => { st.query = search.value; paint(); };
    folderBar.onclick = (e) => {
      const b = e.target.closest && e.target.closest('[data-lp-folder]');
      if (!b) return;
      st.folder = b.dataset.lpFolder;
      paint();
    };
    el.querySelector('[data-lp-close]').onclick = _close;
    let downOnBackdrop = false;
    el.addEventListener('mousedown', (e) => { downOnBackdrop = e.target === el; });
    el.addEventListener('click', (e) => { if (e.target === el && downOnBackdrop) _close(); });
    const onKey = (e) => {
      // A viewer opened from a tile sits above the chooser and owns Esc while it is up.
      if (document.querySelector('.mermaid-viewer-overlay, .desk-v1-lv-overlay')) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); _close(); return; }
      if (e.key !== 'Tab') return;
      const f = Array.from(dialog.querySelectorAll('button:not([disabled]), input')).filter((n) => n.offsetParent !== null);
      if (!f.length) return;
      const first = f[0];
      const last = f[f.length - 1];
      if (!dialog.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(el);
    _open = { el, onKey, returnFocus };
    paint();
    search.focus({ preventScroll: true });

    const mine = _open;
    _read().then((lib) => {
      if (_open !== mine) return;
      const flat = _flatten(lib, kinds);
      st.files = flat.files; st.folders = flat.folders; st.loading = false;
      paint();
    }).catch((e) => {
      if (_open !== mine) return;
      st.error = e && e.message ? e.message : String(e); st.loading = false;
      paint();
    });
  }

  // Every library file of the kinds asked for, `{path, kind, title, src, folder}` (the
  // article's picture strip looks its paths up here).
  function files(kinds) {
    return _read().then((lib) => _flatten(lib, kinds && kinds.length ? kinds : ['image']).files);
  }

  window.DeskV1LibraryPicker = { label: LABEL, buttonHTML, open, close: _close, files };
})();
