// A closed browser-read gate is a permission issue, never evidence of failed sign-in.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
(function () {
  const KIND = 'reads_off';

  // The coverage row's gate, when this is the route that reads the account and reading is what is closed.
  function gateOf(cov, f) {
    if (!cov || cov.via !== 'pane' || (f && f.via !== 'pane') || cov.state !== 'not_connected') return null;
    const g = cov.gate;
    return g && typeof g === 'object' && g.kind === KIND ? g : null;
  }

  // The browser facet for a gated account, or null when the coverage row is not a reads-off one.
  function facet(cov, f) {
    const g = gateOf(cov, f);
    if (!g) return null;
    const site = String(g.site || '').trim();
    return Object.assign(f, { key: 'readsoff', word: W.status.unchecked, site,
      detail: `Agents cannot read ${site ? `this account on ${site}` : 'this account'} yet. Switch it on in Permissions; it asks for your passcode.` });
  }

  // The button on the browser row, only while that row is in this state.
  function html(ch, f) {
    if (!ch || !f || f.key !== 'readsoff') return '';
    return `<div class="desk-v1-cs-fill-row" data-ro-row><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-ro-open>${W.buttons.edit}</button></div>`;
  }

  function bind(row, ch, repaint) {
    const b = row && row.querySelector('[data-ro-open]');
    if (b) b.addEventListener('click', () => {
      const S = window.DeskV1ConnectionStatus;
      if (S && typeof S.reopen === 'function') S.reopen({ kind: 'account', id: ch.id, record: ch }, repaint);
    });
  }

  window.DeskV1ReadsOff = { gateOf, facet, html, bind };
})();
