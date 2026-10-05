// Desk v1 — Connections: the Result step of a known-host method (docs/DESK_CONNECT_BY_URL_SPEC.md,
// slice 2, "Result"). After an accepted Save this shows what was written, opens the provider's
// sign-in in its named browser pane when there is one and follows it, and offers ONE explicit
// "Check it now" (POST /api/desk/connect/verify: a free read-only call). The status wording
// comes from the server, which derives it: a stored key or a finished sign-in is never
// "Verified" on its own, only a check that passed. Nothing here holds a credential value: the
// Save response carries names and states only. Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const POLL_LIMIT_MS = 10 * 60 * 1000;      // the spec's ten-minute sign-in ceiling
  let _token = 0;                            // a newer flow (or reset) ends an older poll
  let _ui = null;
  let _ctx = null;

  function reset() { _token++; _ui = null; _ctx = null; }
  function active() { return !!_ui; }

  async function _openPane(signin) {
    try {
      if (typeof window.openBrowserPane === 'function') await window.openBrowserPane(signin.auth_url, null, null, signin.profile);
    } catch (_) { /* the pane failing to open does not end the sign-in: "Open the sign-in again" retries */ }
  }

  async function _refresh(res) {
    try {
      const body = { service: res.service.id, method: res.method, check: false };
      if (res.account_id) body.account_id = res.account_id;
      _ui.status = await _ctx.api('POST', '/api/desk/connect/verify', body);
    } catch (_) { /* keep the status shown */ }
  }

  async function _poll(res, token) {
    const deadline = Date.now() + POLL_LIMIT_MS;
    while (Date.now() < deadline && token === _token) {
      await new Promise((r) => setTimeout(r, window.__deskGuidePollMs || 1500));
      if (token !== _token) return;
      let f;
      try { f = await _ctx.api('GET', `/api/desk/connect/flows/${encodeURIComponent(res.signin.flow_id)}`); } catch (_) { continue; }
      if (f.status === 'done') {
        await _refresh(res);
        if (token !== _token) return;
        _ui.signin = { state: 'done', message: 'Signed in.' };
        _ctx.repaint();
        return;
      }
      if (f.status === 'error' || f.status === 'unknown') {
        _ui.signin = { state: 'failed', message: f.message || (f.status === 'unknown' ? 'The sign-in is no longer waiting.' : 'The service did not accept the sign-in.') };
        _ctx.repaint();
        return;
      }
    }
    if (token === _token && _ui && _ui.signin && _ui.signin.state === 'waiting') {
      _ui.signin = { state: 'failed', message: 'The sign-in took too long.' };
      _ctx.repaint();
    }
  }

  // The accepted Save response. ctx: { api, repaint, done(res) }
  function start(res, ctx) {
    _token++;
    _ctx = ctx;
    _ui = { res, message: '', busy: false, signin: null, status: res.status || null };
    if (res.signin) {
      _ui.signin = { state: 'waiting', message: 'The sign-in page is opening in its own browser pane. Finish signing in there; this page updates by itself.' };
      _openPane(res.signin);
      _poll(res, _token);
    } else if (res.setup && res.setup.state === 'failed') {
      _ui.signin = { state: 'failed', message: res.setup.message || 'The sign-in could not be started.' };
    } else if (res.setup && res.setup.state === 'done' && res.setup.message) {
      _ui.setup = res.setup.message;      // slice 4: a curated MCP package was registered; there is no sign-in to follow
    } else if (res.setup && res.setup.state === 'waiting' && res.setup.message) {
      _ui.setup = res.setup.message;      // slice 4: saved, but the server cannot start yet (passphrase vault, MC-1047)
      _ui.setupState = 'waiting';
    }
  }

  function _statusWord() {
    const si = _ui.signin;
    if (si && si.state === 'waiting') return { state: 'sign_in_required', label: 'Sign-in required' };
    if (si && si.state === 'failed' && !(_ui.status && (_ui.status.state === 'signed_in' || _ui.status.state === 'verified'))) {
      return { state: 'setup_failed', label: 'Saved; setup failed' };
    }
    const st = _ui.status || {};
    return { state: st.state || 'not_connected', label: st.label || 'Not connected' };
  }

  function html() {
    if (!_ui) return '';
    const r = _ui.res;
    const word = _statusWord();
    const st = _ui.status || {};
    const stored = (r.stored || []).length ? r.stored.map((n) => `<code>${esc(n)}</code>`).join(', ') : 'nothing new (what was already in Secrets is used as it is)';
    const acct = r.account ? `<div><dt>Account</dt><dd data-cf-result-account>${esc(r.account.label || r.account.identity)}</dd></div>` : '';
    const proof = st.state === 'verified'
      ? `<div><dt>Verified by</dt><dd data-cf-result-proof>${esc(st.capability || '')}${st.identity ? ` as <code>${esc(st.identity)}</code>` : ''}${st.at ? `, ${esc(String(st.at).replace('T', ' ').slice(0, 16))} UTC` : ''}</dd></div>` : '';
    const si = _ui.signin;
    const signin = si ? `<div class="desk-v1-cf-msg" data-cf-msg="${si.state === 'failed' ? 'error' : 'ok'}" data-cf-signin="${esc(si.state)}" role="${si.state === 'failed' ? 'alert' : 'status'}">${esc(si.message)}</div>` : '';
    const setup = _ui.setup ? `<div class="desk-v1-cf-msg" data-cf-msg="${_ui.setupState === 'waiting' ? 'warn' : 'ok'}" data-cf-setup="${_ui.setupState || 'done'}" role="status">${esc(_ui.setup)}</div>` : '';
    const msg = _ui.message ? `<div class="desk-v1-cf-msg" data-cf-msg="${_ui.messageKind || 'ok'}" data-cf-result-message role="status">${esc(_ui.message)}</div>` : '';
    const canCheck = ['key_stored', 'signed_in', 'verified', 'check_failed', 'unknown'].indexOf(st.state) >= 0;
    const buttons = [
      si && si.state !== 'done' && r.signin ? '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfr-signin>Open the sign-in again</button>' : '',
      canCheck ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfr-check ${_ui.busy ? 'disabled' : ''}>${_ui.busy ? 'Checking…' : 'Check it now'}</button>` : '',
      '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cfr-done>Done</button>',
    ].join('');
    return `
        <div class="desk-v1-cf-known" data-cf-result-saved>Saved: <strong>${esc(r.service.label)}</strong></div>
        <dl class="desk-v1-cf-facts" data-cf-result>
          <div><dt>Status</dt><dd data-cf-result-status="${esc(word.state)}">${esc(word.label)}</dd></div>
          ${acct}
          <div><dt>Stored in Secrets</dt><dd data-cf-result-stored>${stored}</dd></div>
          ${proof}
        </dl>
        ${signin}${setup}${msg}
        <div class="desk-v1-rules-hint">Saving a key or signing in does not prove it works. Only “Check it now” can mark it Verified, and changing the credential clears that.</div>
        <div class="desk-v1-cf-actions" data-cf-actions>${buttons}</div>`;
  }

  function bind(root) {
    if (!_ui) return;
    const r = _ui.res;
    const check = root.querySelector('[data-cfr-check]');
    if (check) check.addEventListener('click', async () => {
      if (_ui.busy) return;
      _ui.busy = true; _ui.message = ''; _ctx.repaint();
      try {
        const body = { service: r.service.id, method: r.method };
        if (r.account_id) body.account_id = r.account_id;
        const out = await _ctx.api('POST', '/api/desk/connect/verify', body);
        _ui.status = out;
        _ui.message = out.message || ''; _ui.messageKind = out.state === 'check_failed' ? 'error' : 'ok';
      } catch (e) {
        _ui.message = e && e.message ? e.message : 'The check could not run.'; _ui.messageKind = 'error';
      }
      _ui.busy = false; _ctx.repaint();
    });
    const again = root.querySelector('[data-cfr-signin]');
    if (again) again.addEventListener('click', () => { if (r.signin) _openPane(r.signin); });
    const done = root.querySelector('[data-cfr-done]');
    if (done) done.addEventListener('click', () => { const ctx = _ctx; reset(); ctx.done(r); });
  }

  window.DeskV1ConnectResult = { start, html, bind, reset, active };
})();
