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

  // The status word in the card head, or '' when it stays plain text. `st` is accountStatus; `facets` are the card's routes.
  function cardWord(ch, st, facets) {
    if (st.kind !== 'signin') return '';
    const f = [facets.browser, facets.api].find((x) => _startable(ch, x));
    return f ? _button(ch, f, 'data-conn-status', 'desk-v1-conn-status', st.key) : '';
  }

  // `row` is the account's whole card, so the API route can find the element its sign-in draws into.
  function bind(row, ch, repaint) {
    row.querySelectorAll('[data-ss-signin]').forEach((b) => b.addEventListener('click', () => {
      window.DeskV1AccountSignin.start(ch, b.dataset.ssSignin, b.dataset.ssProfile, row, repaint);
    }));
  }

  window.DeskV1StatusSignin = { routeWord, cardWord, bind };
})();
