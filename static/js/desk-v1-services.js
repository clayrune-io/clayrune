// Desk v1 — Connections: "Something else" services (Ron 2026-10-03). A service
// Clayrune has no integration for: a name, an optional link and an optional vault
// credential NAME, saved so agents can see it and use the credential. Window-bridged
// module, no `import` (ground rule 1). The server side is mc/desk_services.py.
//
// HONESTY IS THE POINT. Clayrune does not dial, read or publish to these, so the
// tile and the detail never say "Connected" and offer nothing that implies a
// publish capability: the status word is "Saved for agents", the detail says what
// that means in one line, and the credential line says whether the named vault entry
// exists. A credential VALUE is never asked for here: only the entry's name.
//
// Live: the records are the server's (GET/POST/PATCH/DELETE /api/desk/services).
// Demo (desk_v1_live OFF): kept in memory only, with the Add service undo, and no
// request is made; the toast says nothing was saved.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }
  function _live() { return window.DeskV1Store.live(); }

  const STATUS = { key: 'saved', word: 'Saved for agents' };

  let _rows = null;        // live: the server's list, null until loaded
  let _loadError = null;
  let _loading = null;
  const _demo = [];        // demo: this tab's list, gone on reload

  // The current list, or null while it is still loading (live only).
  function rows() { return _live() ? _rows : _demo; }
  function loadError() { return _loadError; }

  function load(force) {
    if (!_live()) return Promise.resolve(_demo);
    if (_rows && !force) return Promise.resolve(_rows);
    if (_loading && !force) return _loading;
    _loading = _api('GET', '/api/desk/services').then((r) => { _rows = Array.isArray(r) ? r : []; _loadError = null; return _rows; })
      .catch((e) => { _rows = []; _loadError = e && e.message ? e.message : String(e); return _rows; })
      .then((r) => { _loading = null; return r; });
    return _loading;
  }

  function byId(id) { return (rows() || []).find((s) => s.id === id) || null; }

  function _credLine(s) {
    const c = s.credential || {};
    if (!c.name) return '<span data-svc-cred-state="none">No credential named: agents see this service but have nothing to sign in with.</span>';
    return c.in_vault
      ? `<span data-svc-cred-state="found"><code>${esc(c.name)}</code>: found in Secrets.</span>`
      : `<span data-svc-cred-state="missing"><code>${esc(c.name)}</code>: nothing in Secrets has that name yet. Add it there.</span>`;
  }

  // The detail panel body for one saved service.
  function detailHTML(s) {
    const link = s.link
      ? `<a href="${esc(s.link)}" target="_blank" rel="noopener noreferrer" data-svc-link>${esc(s.link)}</a>`
      : '<span class="desk-v1-rules-hint">No link saved.</span>';
    return `
      <div class="desk-v1-conn-row" data-conn-service="${esc(s.id)}">
        <div class="desk-v1-conn-head">
          <span class="desk-v1-channel-badge">${esc(s.name)}</span>
          <span class="desk-v1-conn-status" data-conn-status data-state="saved">${esc(STATUS.word)}</span>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-svc-remove="${esc(s.id)}" aria-label="${esc(`Remove ${s.name}`)}">Remove</button>
        </div>
        <div class="desk-v1-rules-hint" data-svc-honest>Clayrune does not connect to this service or post to it. It is saved so agents can see it and use the credential you named.</div>
        <div class="desk-v1-svc-line"><span class="desk-v1-how-field-label">Link</span> ${link}</div>
        <div class="desk-v1-svc-line"><span class="desk-v1-how-field-label">Credential</span> ${_credLine(s)}</div>
        <form class="desk-v1-svc-edit" data-svc-edit autocomplete="off">
          <label class="desk-v1-conn-add-field">Link
            <input type="text" class="desk-v1-rules-textinput" data-svc-edit-link maxlength="300" value="${esc(s.link || '')}" placeholder="https://"></label>
          <label class="desk-v1-conn-add-field">Credential name in Secrets
            <input type="text" class="desk-v1-rules-textinput" data-svc-edit-cred maxlength="64" value="${esc((s.credential || {}).name || '')}" placeholder="the name of an entry in Secrets"></label>
          <div class="desk-v1-conn-add-actions">
            <button type="submit" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-svc-edit-save>Save changes</button>
            <span class="desk-v1-rules-hint" data-svc-edit-status role="status"></span>
          </div>
        </form>
      </div>`;
  }

  function bindDetail(el, s, repaint) {
    const row = el.querySelector(`[data-conn-service="${CSS.escape(s.id)}"]`);
    if (!row) return;
    const list = rows() || [];
    const rm = row.querySelector('[data-svc-remove]');
    if (rm) rm.onclick = () => {
      const idx = list.indexOf(s);
      window.DeskV1Store.write({
        label: `Removed ${s.name}`, destructive: true, repaint,
        apply: () => { const i = list.indexOf(s); if (i >= 0) list.splice(i, 1); repaint(); },
        unapply: () => { if (list.indexOf(s) < 0) list.splice(Math.min(idx, list.length), 0, s); },
        request: () => _api('DELETE', `/api/desk/services/${encodeURIComponent(s.id)}`),
        undoRequest: () => _api('POST', '/api/desk/services', { id: s.id, name: s.name, link: s.link || undefined, credential: (s.credential || {}).name || undefined }),
      });
    };
    const form = row.querySelector('[data-svc-edit]');
    if (!form) return;
    const status = form.querySelector('[data-svc-edit-status]');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const link = form.querySelector('[data-svc-edit-link]').value.trim();
      const credential = form.querySelector('[data-svc-edit-cred]').value.trim();
      status.textContent = '';
      if (!_live()) {
        s.link = link; s.credential = { name: credential, in_vault: credential ? false : null };
        window.DeskV1Kit.toast('Changed in the preview only: nothing was saved.');
        repaint();
        return;
      }
      _api('PATCH', `/api/desk/services/${encodeURIComponent(s.id)}`, { link, credential })
        .then((saved) => { Object.assign(s, saved); repaint(); })
        .catch((err) => { status.textContent = err && err.message ? err.message : 'could not save'; });
    });
  }

  // Save a new one (the Add service panel calls this). Resolves {ok, service?}.
  // Live: the row appears at once and takes the server's answer; a refusal takes
  // it back out and the server's reason is toasted by the store. Demo: memory only.
  function create(body, repaint) {
    const list = rows() || [];
    const rec = {
      id: body.id, name: body.name, link: body.link || '', kind: 'saved_for_agents', publish: false,
      credential: { name: body.credential || '', in_vault: body.credential ? (_live() ? null : false) : null },
    };
    return window.DeskV1Store.write({
      label: `Saved ${rec.name}${_live() ? '' : ' (preview: nothing was saved)'}`,
      repaint,
      apply: () => { list.push(rec); repaint(); },
      unapply: () => { const i = list.indexOf(rec); if (i >= 0) list.splice(i, 1); },
      request: () => _api('POST', '/api/desk/services', body).then((saved) => { Object.assign(rec, saved); repaint(); return saved; }),
      undoRequest: () => _api('DELETE', `/api/desk/services/${encodeURIComponent(body.id)}`),
    }).then((r) => Object.assign({ service: rec }, r));
  }

  window.DeskV1Services = { STATUS, rows, load, loadError, byId, detailHTML, bindDetail, create };
})();
