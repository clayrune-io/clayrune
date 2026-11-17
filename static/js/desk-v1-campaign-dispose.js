// Campaign card Skip: persist each version before changing its display.
// PATCH cannot grant approval or restore scheduled/approved states.
// Archive stays with DeskV1PieceLifecycle (whole-post archive/restore).
(function () {
  const terminal = new Set(['verified_published', 'you_reported', 'failed', 'skipped', 'archived']);
  const restorable = new Set(['drafting', 'needs_review', 'planned']);
  const pending = new WeakSet();

  async function run(family, state, label, repaint) {
    if (pending.has(family)) return;
    const kit = window.DeskV1Kit, store = window.DeskV1Store;
    const targets = family.versions.filter(v => !terminal.has(v.state));
    if (!targets.length) { kit.toast('Nothing to change — every version already concluded.'); return; }
    pending.add(family);
    try {
      for (const version of targets) {
        const before = version.state;
        const url = '/api/desk/pieces/' + encodeURIComponent(family.id) + '/versions/' + encodeURIComponent(version.id);
        const persist = async next => {
          const saved = await store.api('PATCH', url, { state: next });
          Object.assign(version, saved.versions.find(v => v.id === version.id));
          repaint();
        };
        await store.write({
          label: `${label} “${family.title}”`, destructive: true,
          // Store owns errors/history; local state changes only after a confirmed
          // request (including Undo), so a refusal cannot paint a false state.
          apply: () => {}, unapply: () => {},
          request: () => persist(state),
          undoRequest: restorable.has(before) ? () => persist(before) : undefined,
          irreversible: restorable.has(before) ? false
            : () => `${label} “${family.title}”. Undo unavailable: the previous state cannot be restored here.`,
        });
      }
    } finally { pending.delete(family); }
  }

  function disconnected(button) {
    button.disabled = true;
    button.title = 'This action is not connected yet. Nothing was sent or changed.';
  }

  function menu(menuEl, close, family, repaint) {
    if (!window.DeskV1Store.live()) return;
    disconnected(menuEl.querySelector('[data-menu-move]'));
    if (family.kind !== 'post') disconnected(menuEl.querySelector('[data-menu-dup]'));
    menuEl.querySelector('[data-menu-skip]').onclick = () => { close(); run(family, 'skipped', 'Skipped', repaint); };
    // Archive is deliberately NOT rewired here. DeskV1PieceLifecycle already
    // persists it as a whole-post archive (POST /pieces/<id>/archive), which is
    // what sets archivedAt for the Archived filter and saves the Restore snapshot.
  }

  window.DeskV1CampaignDispose = { menu };
})();
