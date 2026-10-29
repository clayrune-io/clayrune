// Desk v1 — the thumbnails on a piece's media strip: play/view one, remove one
// (Ron 2026-10-29: "no option to view the video by pressing the triangle", "no way to
// remove them from the post"). Window-bridged module, no `import` (ground rule 1).
//
//   window.DeskV1PieceMedia.thumbsHTML(fam)         the strip's tiles (What rows + the piece page)
//   window.DeskV1PieceMedia.wire(root, fam, repaint) make each tile open the viewer / remove it
//
// Both surfaces that draw a piece's assets (desk-v1-what.js rows, desk-v1-piece.js Media)
// call this, so a thumbnail behaves the same wherever it is.
//   * Open: the library's own viewer (DeskV1LibraryViewer.open: pictures in the app image
//     viewer, videos in its player). A piece asset is mapped to the viewer's file shape;
//     a video's `play` URL comes from the server (desk_pieces.v1_piece, the same
//     /api/serve-file URL the library read hands every video).
//   * Remove: detaches the asset from THIS post (DELETE .../assets/<id>; the library file
//     stays). No confirm, like the other Desk removals: a Store.write command with an
//     Undo. The ✕ is absent when the server would refuse the write (_writable: an
//     archived piece); a refusal from the server is toasted by the Store and rolled back.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // Mirrors desk_pieces._writable: only an archived piece refuses an asset write.
  function _writable(fam) { return !fam.archivedAt; }

  function _visual(a) {
    const attrs = `data-asset-id="${esc(a.id)}" title="${esc(a.title || '')}"`;
    if (a.kind === 'video' && a.play) {
      return `<video class="desk-v1-what-thumb desk-v1-pmed-video" ${attrs} src="${esc(a.play)}#t=0.1" preload="metadata" muted playsinline aria-hidden="true"></video>`;
    }
    if (a.src) return `<img class="desk-v1-what-thumb" ${attrs} src="${esc(a.src)}" alt="">`;
    return `<span class="desk-v1-what-thumb desk-v1-what-thumb-blank" ${attrs}>${a.kind === 'video' ? '' : '▣'}</span>`;
  }

  function _tileHTML(fam, a) {
    const isVideo = a.kind === 'video';
    const name = a.title || (isVideo ? 'video' : 'picture');
    return `<span class="desk-v1-pmed-tile" data-pmed-fam="${esc(fam.id)}">
      <button type="button" class="desk-v1-pmed-open" data-pmed-open="${esc(a.id)}" aria-label="${esc(`${isVideo ? 'Play' : 'View'} ${name}`)}" title="${esc(`${isVideo ? 'Play' : 'View'} ${name}`)}">
        ${_visual(a)}${isVideo ? '<span class="desk-v1-pmed-play" aria-hidden="true">▶</span>' : ''}
      </button>
      ${_writable(fam) ? `<button type="button" class="desk-v1-pmed-x" data-pmed-remove="${esc(a.id)}" aria-label="${esc(`Remove ${name} from this post`)}" title="Remove from this post">✕</button>` : ''}
    </span>`;
  }

  function thumbsHTML(fam) {
    return window.DeskV1Kit.pieceAssets(fam).map((a) => _tileHTML(fam, a)).join('');
  }

  function _files(fam) {
    return window.DeskV1Kit.pieceAssets(fam).map((a) => ({
      id: a.id, kind: a.kind === 'video' ? 'video' : 'image', title: a.title || '', src: a.src || null, play: a.play || null, path: a.path || null,
    }));
  }

  function _open(fam, assetId) {
    const files = _files(fam);
    const i = files.findIndex((f) => f.id === assetId);
    if (i < 0) return;
    if (window.DeskV1LibraryViewer) window.DeskV1LibraryViewer.open(files, i);
    else window.DeskV1Kit.toast('The viewer is not available.');
  }

  function _focusAfterRemove(famId, index) {
    const fid = (window.CSS && CSS.escape) ? CSS.escape(famId) : famId;
    const opens = document.querySelectorAll(`[data-pmed-fam="${fid}"] [data-pmed-open]`);
    const next = opens[Math.min(index, opens.length - 1)];
    const target = next || document.querySelector(`[data-add-media="${fid}"]`);
    if (target) target.focus({ preventScroll: true });
  }

  function _remove(fam, assetId, repaint) {
    const asset = (fam.assets || []).find((a) => a.id === assetId);
    if (!asset) return;
    const index = fam.assets.indexOf(asset);
    const base = '/api/desk/pieces/' + encodeURIComponent(fam.id) + '/assets';
    const queue = window.deskV1QueuePiece;
    window.DeskV1Store.write({
      label: `Removed “${asset.title || 'media'}” from “${fam.title}”`, destructive: true,
      apply: () => { fam.assets = (fam.assets || []).filter((a) => a !== asset); repaint(); _focusAfterRemove(fam.id, index); },
      unapply: () => {
        if ((fam.assets || []).includes(asset)) return;
        const next = (fam.assets || []).slice();
        next.splice(Math.min(index, next.length), 0, asset);
        fam.assets = next;
      },
      repaint,
      request: () => queue(fam.id, () => window.DeskV1Store.api('DELETE', base + '/' + encodeURIComponent(asset.id))),
      // Undo re-attaches the same library file under the same id (the file was never deleted).
      undoRequest: () => queue(fam.id, () => {
        if (!asset.path) throw new Error('this media has no saved file to put back');
        return window.DeskV1Store.api('POST', base, { id: asset.id, path: asset.path, title: asset.title });
      }),
    });
  }

  function wire(root, fam, repaint) {
    root.querySelectorAll(`[data-pmed-fam="${(window.CSS && CSS.escape) ? CSS.escape(fam.id) : fam.id}"] [data-pmed-open]`).forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); _open(fam, b.dataset.pmedOpen); };
    });
    root.querySelectorAll(`[data-pmed-fam="${(window.CSS && CSS.escape) ? CSS.escape(fam.id) : fam.id}"] [data-pmed-remove]`).forEach((b) => {
      b.onclick = (e) => { e.stopPropagation(); _remove(fam, b.dataset.pmedRemove, repaint); };
    });
  }

  window.DeskV1PieceMedia = { thumbsHTML, wire };
})();
