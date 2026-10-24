// Removing an assignment uses the existing version archive transition and Undo.
(function () {
  function wire(el, camp, repaint) {
    el.querySelectorAll('[data-where-remove-version]').forEach((button) => {
      button.onclick = (event) => {
        event.stopPropagation();
        const fam = window.DeskV1Store.state().families.find((f) => (f.versions || []).some((v) => v.id === button.dataset.whereRemoveVersion));
        const version = fam && fam.versions.find((v) => v.id === button.dataset.whereRemoveVersion);
        if (!version) return;
        const before = version.state;
        const back = ['drafting', 'needs_review', 'planned', 'skipped'].includes(before) ? before : 'needs_review';
        const patch = (state) => window.deskV1QueuePiece(fam.id, () => window.DeskV1Store.api('PATCH',
          '/api/desk/pieces/' + encodeURIComponent(fam.id) + '/versions/' + encodeURIComponent(version.id), { state }));
        window.DeskV1Store.write({
          label: `Removed “${fam.title}” from this channel`, destructive: true,
          apply: () => { version.state = 'archived'; repaint(); },
          unapply: () => { version.state = before; }, repaint,
          request: () => patch('archived'),
          undoRequest: () => patch(back).then(() => {
            version.state = back; repaint();
            if (back !== before) window.DeskV1Kit.toast('Restored to review. Approve this version again.');
          }),
        });
      };
    });
  }
  window.DeskV1WhereRemoval = { wire };
})();
