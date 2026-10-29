// MC-1126: irreversible platform deletion uses confirmation + retyped passcode.
// Kept out of the command bus: it has no Undo and never changes optimistically.
(function () {
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pending = new Set();
  const name = (v, channel) => (v.receipt?.platform || channel?.platform) === 'linkedin' ? 'LinkedIn' : 'X';

  function stateHTML(v) {
    return v.deleted ? `<span data-post-deleted>Deleted from ${esc(name(v))} · ${esc(v.deleted.at)} · by ${esc(v.deleted.by)}</span>` : window.DeskV1Kit.stateLabelHTML(v.state);
  }

  function html(v, channel) {
    if (!window.DeskV1Store.live() || v.state !== 'verified_published' || v.deleted || !v.receipt ||
        !['x', 'linkedin'].includes(v.receipt.platform || channel?.platform)) return '';
    return `<button type="button" class="btn-secondary" data-post-delete="${esc(v.id)}">Delete from ${esc(name(v, channel))}</button><span role="status" data-post-delete-status="${esc(v.id)}"></span>`;
  }

  function bind(root, family, repaint) {
    root.querySelectorAll('[data-post-delete]').forEach(button => {
      const v = family.versions.find(item => item.id === button.dataset.postDelete);
      const key = family.id + '/' + v.id;
      button.disabled = pending.has(key);
      button.onclick = () => {
        if (pending.has(key)) return;
        pending.add(key); button.disabled = true;
        const reset = () => { pending.delete(key); button.disabled = false; };
        const status = root.querySelector(`[data-post-delete-status="${CSS.escape(v.id)}"]`);
        const platform = name(v);
        window.DeskV1Kit.openConfirmSheet({
          title: `Delete from ${platform}?`,
          body: `Permanently delete this post from ${platform}? This cannot be undone. Its receipt and history stay in the Desk.`,
          confirmLabel: `Delete from ${platform}`, cancelLabel: 'Cancel', onDecline: reset,
          onConfirm: async () => {
            status.textContent = 'Deleting…'; status.setAttribute('role', 'status');
            try {
              const url = `/api/desk/pieces/${encodeURIComponent(family.id)}/versions/${encodeURIComponent(v.id)}/delete-post`;
              const res = await window.humanProofFetch(url, {method:'POST', body:'{}'}, {
                title: `Delete from ${platform}`,
                description: `Re-enter your dashboard passcode to permanently delete this post from ${platform}. This cannot be undone.`,
              });
              if (res === null) { status.textContent = ''; return; }
              if (!res.ok) throw new Error(res.body?.message || res.body?.error || `HTTP ${res.status}`);
              const families = window.DeskV1Store.state().families;
              const i = families.findIndex(item => item.id === family.id);
              if (i >= 0) families[i] = res.body;
              repaint();
              window.DeskV1Kit.toast(`Deleted from ${platform}. Publication history kept.`);
            } catch (e) {
              status.textContent = e.message || String(e); status.setAttribute('role', 'alert');
            } finally { reset(); }
          },
        });
      };
    });
  }
  window.DeskV1PostDelete = {html, bind, stateHTML};
})();
