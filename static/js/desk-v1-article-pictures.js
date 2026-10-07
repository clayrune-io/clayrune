// Desk v1 — pictures inside a Studio article (Ron 2026-10-06). The writer's body is
// text, so a picture is a line of it: `![name](desk/library/image/<folder>/<file>)`,
// the library path of a file picked with "Pick from library". The file stays in the
// library: the article REFERENCES it, nothing is copied, so nothing done in the
// article can lose a picture. Under the writer, a strip shows every picture the text
// names, and says so when a named file is no longer in the library.
// Window-bridged module, no `import` (ground rule 1).
//
//   window.DeskV1ArticlePictures.pick(bodyEl, btn)       open the chooser, insert the picture
//   window.DeskV1ArticlePictures.mount(host, getText)    the strip; returns { refresh() }
//   window.DeskV1ArticlePictures.pathsIn(text)           [{title, path}] the text names
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // A line that is only a picture.
  const LINE = /^!\[([^\]]*)\]\(([^)]+)\)$/;

  function lineFor(it) { return `![${String(it.title).replace(/[[\]]/g, '')}](${it.path})`; }

  function pathsIn(text) {
    const seen = new Set();
    const out = [];
    String(text || '').split('\n').forEach((l) => {
      const m = LINE.exec(l.trim());
      if (m && !seen.has(m[2])) { seen.add(m[2]); out.push({ title: m[1], path: m[2] }); }
    });
    return out;
  }

  // A new paragraph after the one the caret is in (an empty one is used up), or at the end.
  function insert(bodyEl, it) {
    const sel = window.getSelection();
    let blk = null;
    if (sel && sel.rangeCount && bodyEl.contains(sel.anchorNode) && sel.anchorNode !== bodyEl) {
      blk = sel.anchorNode;
      while (blk && blk.parentNode !== bodyEl) blk = blk.parentNode;
    }
    const p = document.createElement('p');
    p.textContent = lineFor(it);
    if (blk && !blk.textContent.trim()) blk.replaceWith(p);
    else if (blk && blk.nextSibling) bodyEl.insertBefore(p, blk.nextSibling);
    else bodyEl.appendChild(p);
    bodyEl.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function pick(bodyEl, btn) {
    if (!window.DeskV1LibraryPicker) return;
    window.DeskV1LibraryPicker.open({ kinds: ['image'], returnFocus: btn, onPick: (it) => {
      insert(bodyEl, it);
      window.DeskV1Kit.toast(`Added “${it.title}” from the library to the article. The article points at that file; nothing was copied.`);
    } });
  }

  function mount(host, getText) {
    let byPath = null; // path -> library item, once the library has been read
    let seq = 0;
    const tried = new Set(); // paths already looked up: a missing one is asked once, not per keystroke
    const draw = () => {
      const found = pathsIn(getText());
      if (!found.length) { host.innerHTML = ''; return; }
      host.innerHTML = `<div class="desk-v1-lr" data-sa-pics>
        <div class="desk-v1-lr-head">Pictures in this article</div>
        <div class="desk-v1-lr-row">${found.map((f, i) => {
          const it = byPath && byPath.get(f.path);
          if (byPath && tried.has(f.path) && !it) return `<div class="desk-v1-lr-item" data-sa-pic-missing="${i}"><span class="desk-v1-lr-thumb"></span><span class="desk-v1-lr-note">Not in the library any more: ${esc(f.title || f.path)}</span></div>`;
          return `<div class="desk-v1-lr-item" data-sa-pic="${i}"><button type="button" class="desk-v1-lr-thumb" data-sa-pic-open="${i}" aria-label="View ${esc(f.title || f.path)} full size">${it && it.src ? `<img src="${esc(it.src)}" alt="${esc(f.title)}">` : ''}</button><span class="desk-v1-lr-name">${esc(f.title || f.path)}</span></div>`;
        }).join('')}</div></div>`;
      host.querySelectorAll('[data-sa-pic-open]').forEach((b) => {
        b.onclick = () => {
          const it = byPath && byPath.get(found[Number(b.dataset.saPicOpen)].path);
          if (it && window.DeskV1LibraryViewer) window.DeskV1LibraryViewer.open([it], 0);
        };
      });
    };
    const refresh = () => {
      draw();
      const fresh = pathsIn(getText()).filter((f) => !tried.has(f.path));
      if (!fresh.length || !window.DeskV1LibraryPicker) return;
      const mine = ++seq;
      window.DeskV1LibraryPicker.files(['image']).then((files) => {
        if (mine !== seq || !host.isConnected) return;
        byPath = new Map(files.map((f) => [f.path, f]));
        fresh.forEach((f) => tried.add(f.path));
        draw();
      }).catch((e) => { console.warn('[desk] could not read the library for the article pictures:', e && e.message ? e.message : e); });
    };
    refresh();
    return { refresh };
  }

  window.DeskV1ArticlePictures = { pick, mount, pathsIn };
})();
