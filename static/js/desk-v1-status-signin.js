// Desk v1 — Connections: the "Needs sign-in" status word starts the sign-in (Ron 2026-10-29: "if a card says
// 'Needs sign-in' why not just allow the sign in directly from that bar?").
//
// There is no second sign-in path here. The word becomes a button that calls DeskV1AccountSignin.start, the same
// function behind that route's "Sign in" button: the browser route opens browser sign-in in the account's own profile,
// the API route runs the passcode-gated start-held. Only the word "Needs sign-in" is ever a button; Ready, Not checked
// yet, Not working (which includes a locked vault, whose wording is its own) and the read-off state stay plain text.
// The card-level word starts the first route that Needs sign-in (Browser, then App), the first "Sign in" on the card.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _live(ch) { return !!(window.DeskV1Store && window.DeskV1Store.live() && !ch.preview); }

  // A route whose word says "Needs sign-in" and that has a sign-in to start. A locked vault is never one.
  function _startable(ch, f) {
    return !!f && !f.locked && f.word === W.status.signin && _live(ch) && !!window.DeskV1AccountSignin?.canStart(ch, f);
  }

  function _button(ch, f, tag, cls, state) {
    const service = W.serviceName(ch.platform);
    return `<button type="button" class="${cls} desk-v1-ss-btn" ${tag} data-state="${esc(state)}" data-ss-signin="${esc(f.id)}" data-ss-profile="${esc(f.profile || '')}" aria-label="Sign in to ${esc(service)}${f.id === 'api' ? ' (app)' : ''}">${esc(W.status.signin)}</button>`;
  }

  // The status word of one route row (desk-v1-connection-status.js `_routeRow`), or '' when it stays plain text.
  function routeWord(ch, f) {
    return _startable(ch, f) ? _button(ch, f, 'data-cs-word', 'desk-v1-cs-word', f.key) : '';
  }

  // The route the card-level word (and the tile pill) starts: the first one that Needs sign-in (Browser, then App), or null.
  function _cardRoute(ch, st, facets) {
    return st.kind === 'signin' ? ([facets.browser, facets.api].find((x) => _startable(ch, x)) || null) : null;
  }

  // The status word in the card head, or '' when it stays plain text. `st` is accountStatus; `facets` are the card's routes.
  function cardWord(ch, st, facets) {
    const f = _cardRoute(ch, st, facets);
    return f ? _button(ch, f, 'data-conn-status', 'desk-v1-conn-status', st.key) : '';
  }

  // `row` is the account's whole card, so the API route can find the element its sign-in draws into.
  function bind(row, ch, repaint) {
    row.querySelectorAll('[data-ss-signin]').forEach((b) => b.addEventListener('click', () => {
      window.DeskV1AccountSignin.start(ch, b.dataset.ssSignin, b.dataset.ssProfile, row, repaint);
    }));
  }

  // A saved browser-profile service (Instagram today, desk-v1-browser-service.js) has no route rows and no
  // DeskV1AccountSignin route: its sign-in is the reviewed page its own check reported (`status.signin_url`) opened in the
  // service's own profile, which is exactly what that check does when it finds the profile signed out. The word is a
  // button only when it Needs sign-in AND that page is known; otherwise it stays plain text.
  function _serviceTarget(s) {
    const c = s && s.browser_connection, url = c && c.status && c.status.signin_url;
    return url && c.browser_profile ? { url: String(url), profile: String(c.browser_profile) } : null;
  }

  // `st` is the service's status (W.from).
  function serviceWord(s, st) {
    if (!st || st.kind !== 'signin' || !(window.DeskV1Store && window.DeskV1Store.live()) || !_serviceTarget(s)) return '';
    return `<button type="button" class="desk-v1-conn-status desk-v1-ss-btn" data-conn-status data-state="${esc(st.key)}" data-ss-service="${esc(s.id)}" aria-label="Sign in to ${esc(s.name)}">${esc(W.status.signin)}</button>`;
  }

  async function startService(s) {
    const t = _serviceTarget(s);
    if (!t || typeof window.openBrowserPane !== 'function') return;
    try { await window.openBrowserPane(t.url, null, null, t.profile); }
    catch (e) { console.error('[status-signin] the browser pane could not be opened', e); window.DeskV1Kit?.toast('The browser pane could not be opened.'); }
  }

  function bindService(root, s) {
    root.querySelectorAll('[data-ss-service]').forEach((b) => b.addEventListener('click', () => startService(s)));
  }

  // ── the status pill on a grid tile (desk-v1-connections-tiles.js) ───────────────────────────────
  // Clicking a Needs sign-in pill selects the tile AND starts the sign-in the panel's head word would start (Ron 2026-10-29:
  // the first "Needs sign-in" he sees must start it). The tile is itself a <button>, so the pill is a SIBLING <button> laid
  // over the tile's own pill (desk-v1-connections-tiles.js tileHTML); a tile whose word would stay plain text in its panel
  // gets no action pill at all, so it never looks clickable.
  // Each returns the pill's aria-label, or '' when there is nothing to start.
  function accountTile(ch, st, facets) {
    return _cardRoute(ch, st, facets) ? `Sign in to ${W.serviceName(ch.platform)}` : '';
  }
  function serviceTile(s, st) {
    return st && st.kind === 'signin' && _live(s) && _serviceTarget(s) ? `Sign in to ${s.name}` : '';
  }
  function pillHTML(it) {
    return `<button type="button" class="desk-v1-conn-pill desk-v1-ss-pill" data-conn-tile-action="${esc(it.key)}" data-state="${esc(it.status.key)}" aria-label="${esc(it.signin)}">${esc(it.status.word)}</button>`;
  }
  // `scope` is the account's card element (selected a moment ago), for the App route to draw into.
  function startCard(ch, st, facets, scope, repaint) {
    const f = _cardRoute(ch, st, facets);
    if (f) window.DeskV1AccountSignin.start(ch, f.id, f.profile, scope, repaint);
  }

  window.DeskV1StatusSignin = { routeWord, cardWord, bind, serviceWord, bindService, accountTile, serviceTile, pillHTML, startCard, startService };
})();
