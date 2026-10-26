// Shared Setup field source chooser. Keeps names/parts only; values stay in inputs or on the server.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const eligible = (f) => f.kind === 'secret' || f.saved_entry;

  function html(f, typed) {
    if (!eligible(f)) return typed;
    return `<div data-cfs-field="${esc(f.key)}" class="desk-v1-saved-field">
      <label class="desk-v1-conn-add-field">${esc(f.label)}${f.required ? '' : ' (optional)'}
        <select class="desk-v1-rules-textinput" data-cfs-mode aria-label="${esc(f.label)} source">
          ${f.present ? '<option value="stored">Use the saved details</option>' : '<option value="typed">Type it here</option>'}
          <option value="saved">Choose from Secrets</option>
        </select></label>
      <div data-cfs-typed>${typed}</div>
      <div data-cfs-saved hidden>
        <p class="desk-v1-rules-hint">Choose a saved entry and the part to use for this field.</p>
        <label class="desk-v1-conn-add-field">Saved entry
          <select class="desk-v1-rules-textinput" data-cfs-name aria-label="${esc(f.label)} saved entry"><option value="">Choose an entry</option></select></label>
        <label class="desk-v1-conn-add-field">Part to use
          <select class="desk-v1-rules-textinput" data-cfs-part aria-label="${esc(f.label)} part"><option value="value">Value</option><option value="username">Username or key ID</option></select></label>
        <p class="desk-v1-rules-hint" data-cfs-message role="status"></p>
      </div></div>`;
  }

  function bind(host, fields) {
    const boxes = [...host.querySelectorAll('[data-cfs-field]')];
    if (!boxes.length) return;
    for (const box of boxes) {
      const mode = box.querySelector('[data-cfs-mode]');
      const changed = () => {
        box.querySelector('[data-cfs-typed]').hidden = mode.value === 'saved';
        box.querySelector('[data-cfs-saved]').hidden = mode.value !== 'saved';
        // Moving to a saved source clears the typed secret immediately.
        if (mode.value === 'saved') box.querySelectorAll('input').forEach((i) => { i.value = ''; });
      };
      mode.addEventListener('change', changed);
      box.addEventListener('change', () => {
        box.dispatchEvent(new CustomEvent('input', { bubbles: true, detail: { field: box.dataset.cfsField } }));
      });
      changed();
    }
    // Desk connections are workspace-wide, with no project identity: only global sources are usable here.
    fetch('/api/secrets').then((r) => { if (!r.ok) throw new Error(); return r.json(); }).then((body) => {
      if (host.dataset.cfsDisposed) return;
      const entries = (body.secrets || []).filter((s) => s.scope === 'global' && !s.name.startsWith('oauth.'));
      for (const box of boxes) {
        const field = fields.find((f) => f.key === box.dataset.cfsField);
        box.querySelector('[data-cfs-name]').innerHTML = '<option value="">Choose an entry</option>' + entries
          .filter((s) => s.name !== field.vault && s.kind !== 'totp')
          .map((s) => `<option value="${esc(s.name)}">${esc(s.name)}</option>`).join('');
        box.querySelector('[data-cfs-message]').textContent = body.locked ? 'Unlock Secrets before continuing.' : entries.length ? '' : 'Save an entry in Secrets first, or type the value here.';
      }
    }).catch(() => {
      if (host.dataset.cfsDisposed) return;
      boxes.forEach((b) => { b.querySelector('[data-cfs-message]').textContent = 'Secrets could not be loaded; close this form and try again.'; });
    });
  }

  function selection(host, f) {
    const box = host && host.querySelector(`[data-cfs-field="${f.key}"]`);
    if (!box || box.querySelector('[data-cfs-mode]').value !== 'saved') return null;
    const name = box.querySelector('[data-cfs-name]');
    return { value: name.value ? { name: name.value, part: box.querySelector('[data-cfs-part]').value } : null, focus: name };
  }

  function summary(host, f) {
    const selected = selection(host, f);
    if (!selected) return null;
    return selected.value ? `from Secrets: <code>${esc(selected.value.name)}</code> (${selected.value.part === 'username' ? 'username or key ID' : 'value'})` : 'no saved entry selected';
  }
  function replaces(host, fields) {
    return fields.some((f) => f.present && selection(host, f)?.value);
  }
  function confirmHTML(host, fields) {
    return replaces(host, fields) ? `<label class="desk-v1-cf-check"><input type="checkbox" data-cfs-confirm-replace>
      <span>Save replaces the saved key. I confirm replacing it with the selected entry from Secrets.</span></label>` : '';
  }
  window.DeskV1ConnectSavedFields = { html, bind, selection, summary, replaces, confirmHTML };
})();
