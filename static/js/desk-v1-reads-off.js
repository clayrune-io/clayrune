// Desk v1 — Connections: "Reading is off" (Ron 2026-10-28: a LinkedIn card whose browser sign-in worked still said
// "Needs sign-in", because the sign-in's profile was not on the agent-read list). Window-bridged module, no `import`
// (ground rule 1); desk-v1-connection-status.js (`browserFacet`, `accountStatus`, `_routeRow`, `bindAccount`) only
// delegates here.
//
// ITS OWN STATE. The coverage row of a browser-pane account carries `gate: { kind: 'reads_off', profile, site }` when
// reading is not allowed yet (mc/desk_read_gate.py): the profile is switched off for agent reads, or it is on but this
// site is not on its list. That is not a sign-in problem and is never drawn as one. The facet is `key: 'readsoff'`,
// word "Reading is off", one sentence saying what to do, and ONE button.
//
// THE ONE ACTION. "Open permissions" opens the account's own Edit settings at its Permissions step
// (DeskV1ConnectionStatus.reopen: the same door the Edit settings button uses), where ticking "Read this account's
// activity" and saving is the human-only write (desk-v1-connect-permissions-step.js -> desk-v1-connect-browser-permission.js
// `apply` -> PUT /api/browser/profiles/<name>/agent-read behind the passcode prompt). This module writes nothing, has no
// writer of its own and never touches the policy file; an agent has no path through it.
//
// AFTER SIGN IN. The gate is checked before the sign-in, so a person who presses Sign in on a card with reading off
// and finishes in the pane lands on this state with its button, not on a stale "Needs sign-in".
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
    return Object.assign(f, { key: 'readsoff', word: 'Reading is off', site,
      detail: `Agents cannot read ${site ? `this account on ${site}` : 'this account'} yet. Switch it on in Permissions; it asks for your passcode.` });
  }

  // The button on the browser row, only while that row is in this state.
  function html(ch, f) {
    if (!ch || !f || f.key !== 'readsoff') return '';
    return '<div class="desk-v1-cs-fill-row" data-ro-row><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-ro-open>Open permissions</button></div>';
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
