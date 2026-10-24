// Whole-post archive/restore and reversible delete. What owns only the mount.
(function () {
  const copy = (v) => JSON.parse(JSON.stringify(v));
  const repaint = () => window.deskV1RepaintWhat();
  const url = (fam, action) => '/api/desk/pieces/' + encodeURIComponent(fam.id) + '/' + action;
  const sent = new Set(['sending', 'submitted', 'verified_published', 'you_reported', 'unknown_outcome', 'failed']);
  const demoSnapshots = new WeakMap();
  function archive(fam, restore) {
    return window.DeskV1SuggestionPieces.act(fam, () => {
      const before = Object.assign({ archivedAt: null }, copy(fam));
      let snapshot = demoSnapshots.get(fam);
      if (!restore) { snapshot = copy(fam.versions || []); demoSnapshots.set(fam, snapshot); }
      const adopt = (saved) => { if (saved) Object.assign(fam, saved); repaint(); return saved; };
      return window.DeskV1Store.write({
        label: `${restore ? 'Restored' : 'Archived'} “${fam.title}”`, destructive: !restore,
        apply: () => {
          fam.archivedAt = restore ? null : new Date().toISOString();
          if (!restore) (fam.versions || []).forEach((v) => { if (!sent.has(v.state)) v.state = 'archived'; });
          else if (snapshot) fam.versions = copy(snapshot);
          repaint();
        },
        unapply: () => { Object.assign(fam, copy(before)); }, repaint,
        request: () => window.deskV1QueuePiece(fam.id, () => window.DeskV1Store.api('POST', url(fam, restore ? 'unarchive' : 'archive')).then(adopt)),
        undoRequest: () => window.deskV1QueuePiece(fam.id, () => window.DeskV1Store.api('POST', url(fam, restore ? 'archive' : 'unarchive')).then(adopt)),
      });
    });
  }
  function remove(fam) {
    return window.DeskV1SuggestionPieces.act(fam, () => {
      const list = window.DeskV1Store.state().families;
      const index = list.indexOf(fam);
      return window.DeskV1Store.write({
        label: `Deleted “${fam.title}”`, destructive: true,
        apply: () => { const i = list.indexOf(fam); if (i >= 0) list.splice(i, 1); repaint(); },
        unapply: () => { if (!list.includes(fam)) list.splice(Math.min(index, list.length), 0, fam); }, repaint,
        request: () => window.deskV1QueuePiece(fam.id, () => window.DeskV1Store.api('POST', url(fam, 'trash'))),
        undoRequest: (result) => window.DeskV1Store.api('POST', '/api/desk/studio/trash/' + encodeURIComponent(result.token) + '/restore')
          .then(() => window.DeskV1Store.api('GET', '/api/desk/pieces?campaign_id=' + encodeURIComponent(fam.campaignId)))
          .then((rows) => { const saved = rows.find((r) => r.id === fam.id); if (saved) Object.assign(fam, saved); repaint(); }),
      });
    });
  }
  function menu(menu, close, fam) {
    const arch = menu.querySelector('[data-menu-archive]');
    arch.textContent = fam.archivedAt ? 'Restore post' : 'Archive';
    arch.onclick = () => { close(); archive(fam, !!fam.archivedAt); };
    const del = document.createElement('button');
    del.type = 'button'; del.dataset.menuDeletePiece = ''; del.textContent = 'Delete post';
    del.onclick = () => { close(); remove(fam); };
    menu.appendChild(del);
  }
  window.DeskV1PieceLifecycle = { archive, remove, menu };
})();
