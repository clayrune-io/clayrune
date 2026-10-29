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
//   API       X's passcode-gated start-held with this existing account's id. The server makes one
//             /2/users/me lookup: matching handle saves immediately; mismatch offers Discard only;
//             lookup or save failure stays held with a passcode-gated manual Save fallback.
//             The wizard still holds its token for its explicit Save. No vendor token reaches this module.
//
// Status messages live above the shared action row; only buttons go inside that row.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
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
    try { await window.openBrowserPane(page.url, null, null, profile); }
    catch (e) { _say(ch, 'browser', 'error', 'The browser pane could not be opened.'); repaint(); }
  }

  // ── the API route: the held sign-in, then its Save ─────────────────────
  function _apiHTML(ch, f, messages) {
    const flow = _flow(ch), phase = flow ? flow.phase : 'idle';
    if (messages) {
      let state = '';
      if (phase === 'waiting') state = '<div class="desk-v1-cf-msg" data-cf-msg="ok" data-as-state="waiting" role="status">The sign-in page is open in its own browser pane. Finish signing in there; this page updates by itself.</div>';
      if (phase === 'held') state = `<div class="desk-v1-cf-msg" data-cf-msg="${flow.saveAllowed ? 'warn' : 'error'}" data-as-state="held" role="${flow.saveAllowed ? 'status' : 'alert'}">${esc(flow.message || `Signed in to ${API[ch.platform].label}. It is kept in memory only until you press Save sign-in.`)} It expires in about ${Math.max(1, Math.round((flow.ttl || 0) / 60))} minutes.</div>`;
      if (phase === 'failed' && flow.message) state = `<div class="desk-v1-cf-msg" data-cf-msg="error" data-as-state="failed" role="alert">${esc(flow.message)}</div>`;
      return state + _msgHTML(ch, 'api');
    }
    if (phase === 'waiting') return '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-open>Open sign-in</button><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-cancel>Cancel</button>';
    if (phase === 'held') {
      const saving = (_saving[ch.id] || {}).busy;
      return `${flow.saveAllowed ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-as-save ${saving ? 'disabled' : ''}>Save sign-in</button>` : ''}<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cfh-cancel ${saving ? 'disabled' : ''}>Discard it</button>`;
    }
    return `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline${_primary(f)}" data-as-signin="api" ${phase === 'starting' ? 'disabled' : ''}>${W.buttons.signin}</button>`;
  }

  async function _saved(ch, repaint) {
    delete _saving[ch.id];
    try { const rows = await window.DeskV1Store.api('GET', '/api/desk/accounts'); Object.assign(ch, (rows || []).find((r) => r.id === ch.id) || {}); }
    catch (e) { console.error('[account-signin] the account could not be re-read after the save', e); }
    _say(ch, 'api', 'ok', `The ${API[ch.platform].label} sign-in is saved.`);
    repaint();
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
    H.consumed();
    await _saved(ch, repaint);
  }

  // ── what the route row draws and binds ─────────────────────────────────
  // `f` is the facet (desk-v1-connection-status.js `facets`): `{ id: 'browser' | 'api', key, profile }`.
  function html(ch, f, messages = false) {
    if (!ch || !f) return '';
    if (f.id === 'api') return API[ch.platform] ? (messages ? _apiHTML(ch, f, true) : `<div class="desk-v1-cs-fill-row" data-as-row="api">${_apiHTML(ch, f, false)}</div>`) : '';
    if (f.id !== 'browser' || !f.profile) return '';
    if (messages) return _msgHTML(ch, 'browser') + (_pages[ch.platform] === null ? '<div class="desk-v1-rules-hint" data-as-none>Clayrune has no reviewed sign-in page for this site, so it cannot open one from here.</div>' : '');
    if (_pages[ch.platform] === null) return '';
    return `<div class="desk-v1-cs-fill-row" data-as-row="browser"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline${_primary(f)}" data-as-signin="browser" data-as-profile="${esc(f.profile)}">${W.buttons.signin}</button></div>`;
  }

  function bind(row, ch, repaint) {
    if (!row || !ch) return;
    const browser = row.querySelector('[data-as-signin="browser"]');
    if (browser) {
      browser.addEventListener('click', () => _signInBrowser(ch, browser.dataset.asProfile, repaint));
    }
    const api = row.querySelector('[data-as-row="api"]');
    if (!api || !API[ch.platform] || !_held()) return;
    const ctx = { api: window.DeskV1Store.api, repaint, saved: () => _saved(ch, repaint) };
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
