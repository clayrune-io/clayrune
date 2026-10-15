// One Remove interaction for every Desk connection. No optimistic delete or Undo:
// a refused/cancelled removal leaves its row in place; only a server success removes it.
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pending = new Set();

function html(name) {
  return `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-connection-remove aria-label="${esc(`Remove ${name}`)}">Remove</button><span role="status" data-connection-remove-status></span>`;
}

function bind(root, {name, key, url, body, passcode = false, preview = false, onRemoved}) {
  const button = root.querySelector('[data-connection-remove]');
  if (!button) return;
  button.dataset.connectionRemoveKey = key;
  button.disabled = pending.has(key);
  button.onclick = () => {
    if (pending.has(key)) return;
    pending.add(key); button.disabled = true;
    const reset = () => {
      pending.delete(key);
      document.querySelectorAll('[data-connection-remove]').forEach(node => {
        if (node.dataset.connectionRemoveKey === key) node.disabled = false;
      });
    };
    window.DeskV1Kit.openConfirmSheet({
      title: `Remove ${name}?`, body: 'Remove this connection from the Desk? Your saved login stays in the Vault.',
      confirmLabel: 'Remove', cancelLabel: 'Cancel', onDecline: reset,
      onConfirm: async () => {
        const status = root.querySelector('[data-connection-remove-status]');
        if (status) status.textContent = 'Removing…';
        try {
          if (window.DeskV1Store.live() && !preview) {
            if (passcode) {
              const res = await window.humanProofFetch(url, {method:'DELETE', body:JSON.stringify(body || {})},
                {title:`Remove ${name}`, description:'Re-enter your dashboard passcode to remove this connection. Your saved login stays in the Vault.'});
              if (res === null) { if (status) status.textContent = ''; reset(); return; }
              if (!res.ok) throw new Error(res.body?.message || res.body?.error || `Removal failed (HTTP ${res.status}).`);
            } else await window.DeskV1Store.api('DELETE', url, body);
          }
          await onRemoved();
          window.DeskV1Kit.toast(window.DeskV1Store.live() && !preview ? `Removed ${name}` : 'Removed in the preview only: nothing was saved.');
        } catch (e) {
          if (status) { status.textContent = e.message || String(e); status.setAttribute('role','alert'); }
        } finally { reset(); }
      },
    });
  };
}

export const ConnectionRemoval = {html, bind};
