// Desk v1 — the Material library's viewer (Ron 2026-10-06: "nothing can be done
// with" the library thumbnails). Window-bridged module, no `import` (ground rule 1).
//
//   window.DeskV1LibraryViewer.open(files, index)      show one file of a folder
//   window.DeskV1LibraryViewer.wireShelf(root, files)  make an open folder's tiles open it
//
// `files` is a folder's files in shelf order: `{id, kind, title, src, play, path}`.
//   * a PICTURE opens in the app's own image viewer (`_openImageViewer`, mermaid.js:
//     zoom, pan, save, Esc/X, previous/next through the folder, a modal frame on a
//     phone). Reused, not rebuilt.
//   * a VIDEO opens in a player here (that viewer shows images only). Its file is
//     `play`, the /api/serve-file URL the library read hands every video, so the
//     browser streams it with ranges. Previous/next step through the folder's videos,
//     Esc and the X close it.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  let _player = null; // the one open video player

  function _closePlayer() {
    const p = _player;
    if (!p) return;
    _player = null;
    document.removeEventListener('keydown', p.onKey, true);
    const v = p.el.querySelector('video');
    if (v) { try { v.pause(); } catch (e) { /* already gone */ } v.removeAttribute('src'); v.load(); }
    p.el.remove();
    if (p.returnFocus && p.returnFocus.isConnected) p.returnFocus.focus({ preventScroll: true });
  }

  function _playerHTML(it, i, n) {
    const body = it.play
      ? `<video class="desk-v1-lv-video" data-lv-video src="${esc(it.play)}" controls autoplay playsinline preload="metadata"></video>`
      : '<div class="desk-v1-lv-nofile" data-lv-nofile>This video has no file to play here.</div>';
    return `<div class="desk-v1-lv-bar">
        <span class="desk-v1-lv-title" data-lv-title>${esc(it.title)}</span>
        <span class="desk-v1-lv-count" data-lv-count>${n > 1 ? `${i + 1} / ${n}` : ''}</span>
        <button type="button" class="desk-v1-lv-btn" data-lv-close aria-label="Close the player" title="Close (Esc)">✕</button>
      </div>
      <div class="desk-v1-lv-stage">${body}
        ${n > 1 ? `<button type="button" class="desk-v1-lv-nav desk-v1-lv-prev" data-lv-prev aria-label="Previous video" title="Previous (←)">‹</button>
        <button type="button" class="desk-v1-lv-nav desk-v1-lv-next" data-lv-next aria-label="Next video" title="Next (→)">›</button>` : ''}
      </div>`;
  }

  function _openVideo(files, index) {
    _closePlayer();
    const vids = files.filter((f) => f.kind === 'video');
    const start = vids.indexOf(files[index]);
    let i = start >= 0 ? start : 0;
    if (!vids.length) return;
    const el = document.createElement('div');
    el.className = 'desk-v1-lv-overlay';
    el.setAttribute('role', 'dialog');
    el.setAttribute('aria-modal', 'true');
    el.setAttribute('aria-label', 'Video player');
    el.setAttribute('data-lv-player', '');
    const returnFocus = document.activeElement;
    const go = (d) => {
      i = ((i + d) % vids.length + vids.length) % vids.length;
      el.querySelector('.desk-v1-lv-box').innerHTML = _playerHTML(vids[i], i, vids.length);
      wire();
    };
    const wire = () => {
      el.querySelector('[data-lv-close]').onclick = _closePlayer;
      const prev = el.querySelector('[data-lv-prev]');
      const next = el.querySelector('[data-lv-next]');
      if (prev) prev.onclick = () => go(-1);
      if (next) next.onclick = () => go(1);
      el.querySelector('[data-lv-close]').focus({ preventScroll: true });
    };
    el.innerHTML = `<div class="desk-v1-lv-box">${_playerHTML(vids[i], i, vids.length)}</div>`;
    let downOnBackdrop = false;
    el.addEventListener('mousedown', (e) => { downOnBackdrop = e.target === el; });
    el.addEventListener('click', (e) => { if (e.target === el && downOnBackdrop) _closePlayer(); });
    const onKey = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); _closePlayer(); return; }
      if (e.target && e.target.closest && e.target.closest('video')) return; // the player's own arrow keys seek
      if (e.key === 'ArrowLeft') go(-1);
      else if (e.key === 'ArrowRight') go(1);
    };
    document.addEventListener('keydown', onKey, true);
    document.body.appendChild(el);
    _player = { el, onKey, returnFocus };
    wire();
  }

  function open(files, index) {
    const list = (files || []).filter(Boolean);
    const it = list[index];
    if (!it) return;
    if (it.kind === 'video') { _openVideo(list, index); return; }
    if (typeof window._openImageViewer === 'function' && it.src) { window._openImageViewer(it.src); return; }
    window.DeskV1Kit && window.DeskV1Kit.toast(it.src ? 'The picture viewer is not available.' : 'This picture has no file to show.');
  }

  // An open folder's tiles become buttons that open the viewer on that file.
  function wireShelf(root, files) {
    const list = files || [];
    root.querySelectorAll('[data-studio-file]').forEach((tile) => {
      const i = list.findIndex((f) => String(f.id) === tile.dataset.studioFile);
      if (i < 0) return;
      tile.setAttribute('role', 'button');
      tile.setAttribute('tabindex', '0');
      tile.setAttribute('aria-label', `Open ${list[i].title}`);
      tile.classList.add('desk-v1-lv-tile');
      tile.onclick = () => open(list, i);
      tile.onkeydown = (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(list, i); } };
    });
  }

  window.DeskV1LibraryViewer = { open, wireShelf };
})();
