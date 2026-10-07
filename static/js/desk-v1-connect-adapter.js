// Desk v1 — Connections: the Details step of a known-host method (docs/DESK_CONNECT_BY_URL_SPEC.md,
// slice 2). The server's inspect answer carries, for a method a provider can really set up
// (`connector`), its guide steps and its field SPECS (never a value). This module draws those
// fields, keeps the typed values only in the open form's own <input>s, and reads them once into
// the Save request. Where a password lives: the same rule as the generic credential form
// (static/js/secret-form.js): the form's DOM node survives Back and a repaint, nothing is
// copied into a variable, storage, a draft or a log, and `clear()` empties it. Window-bridged
// module, no `import` (ground rule 1).
//
// Field spec: { key, label, kind: 'text'|'secret'|'checkbox', required, hint, vault?, present?, default? }
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  let _host = null;                // the form's DOM node: the one place a value lives
  let _key = '';                   // which service+method the node was built for
  let _fields = [];

  function _id(f) { return `cfa-${f.key}`; }
  function _hint(f) { return f.hint ? `<div class="desk-v1-rules-hint">${esc(f.hint)}</div>` : ''; }

  function _fieldHTML(f) {
    if (f.kind === 'checkbox') {
      return `<label class="desk-v1-cf-check"><input type="checkbox" id="${_id(f)}" data-cfa-field="${esc(f.key)}" ${f.default ? 'checked' : ''}>
          <span>${esc(f.label)}${f.hint ? `<span class="desk-v1-rules-hint">${esc(f.hint)}</span>` : ''}</span></label>`;
    }
    if (f.kind === 'secret' && f.present) {
      return `<div class="desk-v1-cf-present" data-cfa-present="${esc(f.key)}"><strong>${esc(f.label)}</strong>: already stored in the Vault as <code>${esc(f.vault)}</code>; it is used as it is, so it is not asked again.</div>`;
    }
    const secret = f.kind === 'secret';
    return `<label class="desk-v1-conn-add-field" for="${_id(f)}">${esc(f.label)}${f.required ? '' : ' (optional)'}
          <input type="${secret ? 'password' : 'text'}" id="${_id(f)}" class="desk-v1-rules-textinput" data-cfa-field="${esc(f.key)}" data-cfa-kind="${f.kind}"
            maxlength="8192" autocomplete="${secret ? 'new-password' : 'off'}" autocapitalize="off" spellcheck="false"></label>${_hint(f)}`;
  }

  function guideHTML(connector) {
    const steps = (connector && connector.guide) || [];
    if (!steps.length) return '';
    return `<ol class="desk-v1-cf-guide" data-cfa-guide>${steps.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`;
  }

  // Build the fields once for this service+method, in the slot it is first needed in; later
  // calls move the same node, so Back and a repaint keep what was typed.
  function mount(slot, connector, key) {
    if (!slot || !connector) return;
    if (_host && _key !== key) clear();
    if (!_host) {
      _key = key;
      _fields = connector.fields || [];
      _host = document.createElement('div');
      _host.className = 'desk-v1-cf-adapter';
      _host.dataset.cfaHost = '1';
      _host.innerHTML = _fields.map(_fieldHTML).join('');
    }
    slot.appendChild(_host);
  }

  function _el(f) { return _host ? _host.querySelector(`[data-cfa-field="${f.key}"]`) : null; }

  // `{ fields }` for the Save request, or `{ error }` naming the first required field left empty.
  function read() {
    const out = {};
    for (const f of _fields) {
      if (f.kind === 'secret' && f.present) continue;
      const el = _el(f);
      if (!el) continue;
      if (f.kind === 'checkbox') { out[f.key] = !!el.checked; continue; }
      const v = f.kind === 'secret' ? el.value : el.value.trim();
      if (!v) { if (f.required) return { error: `${f.label} is required.`, focus: el }; continue; }
      out[f.key] = v;
    }
    return { fields: out };
  }

  // Review's rows for the typed fields, with no secret value in them.
  function summaryHTML() {
    const rows = _fields.map((f) => {
      const el = _el(f);
      let val;
      if (f.kind === 'secret') {
        val = f.present ? `already stored as <code>${esc(f.vault)}</code>, used as it is`
          : (el && el.value ? 'entered, hidden: shown nowhere' : 'none');
      } else if (f.kind === 'checkbox') {
        val = el && el.checked ? 'yes' : 'no';
      } else {
        const t = el ? el.value.trim() : '';
        val = t ? esc(t) : 'none';
      }
      return `<div><dt>${esc(f.label)}</dt><dd data-cfa-r="${esc(f.key)}">${val}</dd></div>`;
    });
    return `<dl class="desk-v1-cf-facts" data-cfa-summary>${rows.join('')}</dl>`;
  }

  function mounted() { return !!_host; }

  // Leave the flow or change method: no value stays typed.
  function clear() {
    if (_host) _host.querySelectorAll('input').forEach((i) => { if (i.type !== 'checkbox') i.value = ''; });
    _host = null; _key = ''; _fields = [];
  }

  window.DeskV1ConnectAdapter = { guideHTML, mount, read, summaryHTML, mounted, clear };
})();
