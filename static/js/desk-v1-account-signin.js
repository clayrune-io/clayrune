// Desk v1 — Connections: the "Sign in" button on a route row of a saved account (Ron 2026-10-28:
// "nothing on the card starts a sign-in"). Window-bridged module, no `import` (ground rule 1);
// desk-v1-connection-status.js (`_routeRow`, `bindAccount`) only delegates here.
//
// ONE WORD, ONE ROW. Each route row that CAN sign in gets one button, "Sign in", so an account with a
// Browser and an API route shows two, one per row. It starts the real sign-in for THAT route, not the wizard:
//
//   Browser   opens Clayrune's browser pane on the platform's reviewed sign-in page (the address the wizard's
//             "Open sign-in" opens: `setup.signin.url` of the platform's browser sign-in route, read from the
//             same POST /api/desk/connect/types answer the saved-account reopen reads) in the account's OWN
//             named profile (the profile the row prints). Never a throwaway profile, never another profile.
//             No row without a profile is offered a button. The page is looked up when the button is PRESSED, never
//             on paint (desk-v1-connection-status.mjs pins that opening a card makes no POST); a platform whose
//             profile has no reviewed browser sign-in page (YouTube today) therefore learns that on the first
//             press and shows a note in place of the button from then on.
//   API       X's own sign-in: the existing passcode-gated held sign-in, POST /api/desk/connect/x/start-held
//             with this account's id, run by desk-v1-connect-held.js (`begin`/`start`/`bind`, so there is one
//             state machine); the sign-in finishes in its named pane and the server HOLDS the token. Its Save
//             completion is the same passcode-gated POST /api/desk/connect/commit the wizard's Save sends, with
//             this account's id and the held `{flow_id, claim}` and no typed app details (the app already saved in
//             the Vault is the one used). Nothing is stored until that second step, which the person presses.
//
// WHAT IT NEVER DOES. Open a pane the person did not ask for, sign in to anything on its own, send a password
// (none is typed here), or loosen a gate: both API steps stay behind the dashboard passcode. It adds no route.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // The API sign-ins a saved account can start from its card: per platform, the address and method the Save names.
  const API = { x: { label: 'X', url: 'https://x.com', method: 'oauth' } };

  const _pages = {};          // platform -> { url } (a reviewed sign-in page), or null (answered: it has none); absent = not known
  const _msg = {};            // `${account}:${route}` -> { kind: 'ok' | 'error', text }
  const _saving = {};         // account id -> { busy, requestId }

  function _key(ch, route) { return `${ch.id}:${route}`; }
  function _say(ch, route, kind, text) { if (text) _msg[_key(ch, route)] = { kind, text }; else delete _msg[_key(ch, route)]; }
  function _msgHTML(ch, route) {
    const m = _msg[_key(ch, route)];
    return m ? `<div class="desk-v1-cf-msg" data-cf-msg="${m.kind}" data-as-msg="${route}" role="${m.kind === 'error' ? 'alert' : 'status'}">${esc(m.text)}</div>` : '';
  }
  function _primary(f) { return f.key === 'reauth' || f.key === 'off' ? ' desk-v1-cf-primary' : ''; }
  function _held() { return window.DeskV1ConnectHeld || null; }
  // The held sign-in's state, when it is THIS account's.
  function _flow(ch) {
    const s = _held() && _held().state ? _held().state() : null;
    return s && s.accountId === ch.id && s.service === ch.platform ? s : null;
  }

  // ── the sign-in page of a browser route ────────────────────────────────
  // The reviewed page for the account's own route when it has one, else the platform's single browser sign-in page.
  // Several different pages and no way to tell which is this account's: none (a guess would open the wrong site).
  function _pageFrom(info, ch) {
    const types = ((info && info.picker) || []).find((t) => t.id === 'signin');
    const found = ((types && types.variants) || []).filter((v) => v.route_id && v.setup && v.setup.via === 'browser_signin' && v.setup.signin && v.setup.signin.url);
    const saved = ch.browser_setup && ch.browser_setup.route_id;
    const own = found.filter((v) => v.route_id === saved);
    const pool = own.length ? own : found;
    const urls = Array.from(new Set(pool.map((v) => v.setup.signin.url)));
    return urls.length === 1 ? { url: urls[0] } : null;
  }

  function _loadPage(ch) {
    const platform = ch.platform;
    return window.DeskV1Store.api('POST', '/api/desk/connect/types', { input: `https://${platform}.com` })
      .then((info) => { _pages[platform] = _pageFrom(info, ch); return _pages[platform]; })
      .catch((e) => { console.error('[account-signin] the sign-in page could not be read', e); return undefined; });
  }

  async function _signInBrowser(ch, profile, repaint) {
    _say(ch, 'browser', '', '');
    let page = _pages[ch.platform];
    if (page === undefined) { page = await _loadPage(ch); }
    if (!page) {                                  // null: answered, there is none (the row now says so); undefined: the lookup failed, a click retries
      if (page === undefined) _say(ch, 'browser', 'error', 'The sign-in page could not be looked up. Try again.');
      repaint(); return;
    }
    try { window.openBrowserPane(page.url, null, null, profile); }
    catch (e) { _say(ch, 'browser', 'error', 'The browser pane could not be opened.'); repaint(); }
  }

  // ── the API route: the held sign-in, then its Save ─────────────────────
  function _apiHTML(ch, f) {
    const flow = _flow(ch), phase = flow ? flow.phase : 'idle';
    const lbl = esc(API[ch.platform].label);
    if (phase === 'waiting') {
      return `<div class="desk-v1-cf-msg" data-cf-msg="ok" data-as-state="waiting" role="status">The sign-in page is open in its own browser pane. Finish signing in there; this page updates by itself.</div>
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-open>Open the sign-in page again</button>
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-cancel>Cancel</button>`;
    }
    if (phase === 'held') {
      const saving = (_saving[ch.id] || {}).busy;
      return `<div class="desk-v1-cf-msg" data-cf-msg="ok" data-as-state="held" role="status">Signed in to ${lbl}. It is kept in memory only, for about ${Math.max(1, Math.round((flow.ttl || 0) / 60))} more minutes, until you press Save sign-in.</div>
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-as-save ${saving ? 'disabled' : ''}>Save sign-in</button>
        <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-cancel ${saving ? 'disabled' : ''}>Discard it</button>${_msgHTML(ch, 'api')}`;
    }
    const failed = phase === 'failed' && flow.message ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-as-state="failed" role="alert">${esc(flow.message)}</div>` : '';
    return `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline${_primary(f)}" data-as-signin="api" ${phase === 'starting' ? 'disabled' : ''}>Sign in</button>${failed}${_msgHTML(ch, 'api')}`;
  }

  async function _save(ch, repaint) {
    const H = _held(), draft = H && H.draft();
    const st = _saving[ch.id] = _saving[ch.id] || {};
    if (!draft || st.busy) return;
    st.busy = true; _say(ch, 'api', '', ''); repaint();
    if (!st.requestId) st.requestId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `as-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    const api = API[ch.platform];
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/commit',
        { method: 'POST', body: JSON.stringify({ request_id: st.requestId, draft: { url: api.url, method: api.method, fields: {}, account_id: ch.id, held: draft } }) },
        { title: 'Save sign-in', description: `Re-enter your dashboard passcode to save the ${api.label} sign-in for this account.` });
    } catch (e) { res = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } }; }
    st.busy = false;
    if (res === null) { repaint(); return; }                                  // cancelled at the passcode: nothing was sent, the sign-in stays held
    const body = res.body || {};
    if (!res.ok) { _say(ch, 'api', 'error', body.error || body.message || `The sign-in could not be saved (HTTP ${res.status}).`); repaint(); return; }
    delete _saving[ch.id];
    H.consumed();                                                              // the Save stored it
    // The card reads the account row: take the server's fresh one now, never wait for "Check again".
    try { const rows = await window.DeskV1Store.api('GET', '/api/desk/accounts'); Object.assign(ch, (rows || []).find((r) => r.id === ch.id) || {}); }
    catch (e) { console.error('[account-signin] the account could not be re-read after the save', e); }
    _say(ch, 'api', 'ok', `The ${api.label} sign-in is saved.`);
    repaint();
  }

  // ── what the route row draws and binds ─────────────────────────────────
  // `f` is the facet (desk-v1-connection-status.js `facets`): `{ id: 'browser' | 'api', key, profile }`.
  function html(ch, f) {
    if (!ch || !f) return '';
    if (f.id === 'api') return API[ch.platform] ? `<div class="desk-v1-cs-fill-row" data-as-row="api">${_apiHTML(ch, f)}</div>` : '';
    if (f.id !== 'browser' || !f.profile) return '';
    if (_pages[ch.platform] === null) return '<div class="desk-v1-cs-fill-row" data-as-row="browser"><div class="desk-v1-rules-hint" data-as-none>Clayrune has no reviewed sign-in page for this site, so it cannot open one from here.</div></div>';
    return `<div class="desk-v1-cs-fill-row" data-as-row="browser"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline${_primary(f)}" data-as-signin="browser" data-as-profile="${esc(f.profile)}">Sign in</button>${_msgHTML(ch, 'browser')}</div>`;
  }

  function bind(row, ch, repaint) {
    if (!row || !ch) return;
    const browser = row.querySelector('[data-as-signin="browser"]');
    if (browser) {
      browser.addEventListener('click', () => _signInBrowser(ch, browser.dataset.asProfile, repaint));
    }
    const api = row.querySelector('[data-as-row="api"]');
    if (!api || !API[ch.platform] || !_held()) return;
    const ctx = { api: window.DeskV1Store.api, repaint };
    const start = api.querySelector('[data-as-signin="api"]');
    if (start) start.addEventListener('click', () => {
      _say(ch, 'api', '', '');
      _held().begin(ch.platform, API[ch.platform].method, API[ch.platform].label, ctx, ch.id);
      _held().start(api);
    });
    if (_flow(ch)) {
      _held().begin(ch.platform, API[ch.platform].method, API[ch.platform].label, ctx, ch.id);   // same sign-in: only the repaint target is refreshed
      _held().bind(api);
    }
    const save = api.querySelector('[data-as-save]');
    if (save) save.addEventListener('click', () => _save(ch, repaint));
  }

  window.DeskV1AccountSignin = { html, bind };
})();
