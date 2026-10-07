// Desk v1 — Connections: the Connect wizard's REMOTE MCP screens (MC-1062 ticket 11,
// docs/desk_v1/connect_flow_tickets/11-remote-screen.md; frame and rules in desk-v1-connect-wizard.js).
// Window-bridged module, no `import` (ground rule 1). It registers three screens through `registerScreen` for the
// branch `type = mcp, variant = custom-remote` (the type projection's "MCP server: remote address") and owns
// nothing else: the old "Add a remote MCP server" section (desk-v1-connect-remote.js) is untouched and still runs
// the old flow. The server side is the same routes that section calls (mc/blueprints/desk_connect_remote_routes.py).
//
//   Setup        M1r "MCP server address"  the address; the protocol override and server name are under Details.
//                M2r "Server sign-in"      none / a token from Secrets / OAuth, marked "Save only".
//                M3r "Server credentials"  only when there is a sign-in: header, prefix and a Secrets entry NAME, or the
//                                          OAuth issuer and scopes. Skipped for "none".
//   (Permissions is ticket 08's screen: for an MCP server it is the project / all-projects reach, nothing else.)
//   Review       M5r "Review server"       the server's own card as facts: exact address, who receives it, protocol, sign-in,
//                                          credential recipient, reach and every risk label, including "can change".
//                M6r "Connection risks"    only when the address is http:// or local/private: one box per exposure, each OFF,
//                                          four to a page. Ticking one reviews again (new fingerprint, approval cleared).
//                M7  "Approve connection"  the recipient and the can-change risk once more, the approval box and Save, which
//                                          asks for the dashboard passcode.
//   Result       the server's own state word and message, and, if the person asks, a check that reaches only the approved
//                address. A check that reaches the server says it ANSWERED, never that it is verified. A changed server
//                shows its diff and offers the passcode-gated accept.
//
// WHAT IT PRESERVES. The card is the server's text (POST /api/desk/connect/custom/remote/review, which sends nothing to the
// address). The Save sends only {request_id, fingerprint} to /custom/commit through humanProofFetch. Any edit to the address,
// protocol, sign-in, a credential, the reach or an exposure box drops the card and its approval: a new Review is a new card,
// and an exposure tick is never carried to a changed request. No credential is typed here: it is the NAME of a header and the
// NAME of a Secrets entry. OAuth is recorded only: this version cannot start it, so a saved OAuth server is "Saved, cannot run
// yet" and the result offers no check.
//
// WHAT IT DOES NOT DO. No OAuth sign-in and no claim that the permissions limit what the server's tools can do: a remote
// server is approved whole (design section 10, Q2 = A).
//
// ACTIVATION. The wizard is disabled until ticket 14, and the Package/Server-address choice that sets `variant =
// custom-remote` is not part of this ticket: until it exists, the screens are reachable only by
// `api.select({ variant: 'custom-remote' })`.
(function () {
  const W = window.DeskV1ConnectWizard;
  if (!W) return;
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const VARIANT = 'custom-remote';
  const MAX_CREDS = 4;                                          // remote_mcp_operation.MAX_CREDENTIALS
  const STATE_WORD = { registered: 'Registered', pending_runtime: 'Saved, cannot run yet', setup_failed: 'Saved, setup failed' };
  const PROTOCOL_WORD = { streamable_http: 'Streamable HTTP', sse: 'SSE (the older event-stream protocol)' };
  const EXPOSURE_TEXT = {
    unencrypted_connection: 'The address is http://, not https://. The connection is not encrypted: anyone on the network path can read or change it, including a token.',
    local_or_private_target: 'The address is on this computer or a private network. The server could reach things the internet cannot.',
  };
  const STATUS_WORD = { baseline_recorded: 'Reached. This is the first look at it.', unchanged: 'Reached. It matches the last look.', adopted: 'Accepted as the new baseline.', changed: 'Reached, but it has changed since the last look.' };
  const OAUTH_NOTE = 'OAuth is recorded only. This version cannot start the sign-in, so the server is saved but cannot run.';

  let _gen = 0;                                                // bumped when the branch is dropped: an answer that arrives later is ignored

  function _fresh() {
    return { view: 'address', url: '', protocol: '', name: '', auth: 'none', issuer: '', scopes: '', creds: [], ack: {}, refocus: false,
      rv: { view: 'facts', key: '', busy: false, card: null, exp: false, error: '', saving: false },
      result: null, saved: null, check: null };
  }
  let P = _fresh();

  const _isMine = (sel) => sel.type === 'mcp' && sel.variant === VARIANT;

  // ── what the person typed ───────────────────────────────────────────────
  function _creds() { return P.creds.map((c) => ({ header: c.header.trim(), prefix: c.prefix, vault: c.vault.trim() })); }
  function _halfCredential() { return _creds().find((c) => (c.header || c.vault) && !(c.header && c.vault)) || null; }
  function _addressProblem() {
    const t = P.url.trim();
    if (!t) return 'Enter the server address.';
    if (!/^https?:\/\/\S+$/i.test(t)) return 'The address must start with https:// (or http://, which needs its own acknowledgement).';
    return '';
  }
  function _credsProblem() {
    if (P.auth === 'oauth') return P.issuer.trim() ? '' : 'Enter the address of the issuer that signs you in.';
    if (P.auth !== 'header') return '';
    const half = _halfCredential();
    if (half) return `Name both the header and the Secrets entry${half.header ? ` for ${half.header}` : ''}, or remove the row.`;
    return _creds().some((c) => c.header && c.vault) ? '' : 'Name the header and the Secrets entry that holds the token.';
  }
  function _views() { return ['address', 'auth'].concat(P.auth === 'none' ? [] : ['creds']); }
  function _viewIndex() { return Math.max(0, _views().indexOf(P.view)); }
  function _problemOf(view) { return view === 'address' ? _addressProblem() : view === 'creds' ? _credsProblem() : ''; }

  // ── Setup ───────────────────────────────────────────────────────────────
  function _addressHTML() {
    return `<label class="desk-v1-conn-add-field">Server address
        <input type="text" class="desk-v1-rules-textinput" data-rs-url data-cfw-focus value="${esc(P.url)}" placeholder="https://mcp.example.com/mcp" maxlength="500" autocapitalize="off" spellcheck="false" inputmode="url"></label>
      <div class="desk-v1-cfw-fact-text" data-rs-address-note>Streamable HTTP and SSE servers work. The protocol is proposed from the address; change it under Details.</div>`;
  }
  function _addressDetailsHTML() {
    return `<label class="desk-v1-conn-add-field">Protocol
        <select class="desk-v1-rules-textinput" data-rs-protocol>
          <option value=""${P.protocol === '' ? ' selected' : ''}>Choose from the address</option>
          <option value="streamable_http"${P.protocol === 'streamable_http' ? ' selected' : ''}>${esc(PROTOCOL_WORD.streamable_http)}</option>
          <option value="sse"${P.protocol === 'sse' ? ' selected' : ''}>${esc(PROTOCOL_WORD.sse)}</option>
        </select></label>
      <label class="desk-v1-conn-add-field">Server name (optional)
        <input type="text" class="desk-v1-rules-textinput" data-rs-name value="${esc(P.name)}" maxlength="64" autocapitalize="off" spellcheck="false"></label>`;
  }

  function _authHTML() {
    const opt = (v, label, tag) => `<label class="desk-v1-cfw-option" data-cfw-option="${v}"><input type="radio" name="rs-auth" value="${v}" data-rs-auth ${P.auth === v ? 'checked' : ''}><span class="desk-v1-cfw-option-label">${esc(label)}</span>${tag ? `<span class="desk-v1-cfw-tag" data-rs-save-only>${esc(tag)}</span>` : ''}</label>`;
    return `${_prevHTML('address', 'the address')}
      <div class="desk-v1-cfw-options" role="radiogroup" aria-label="How this server signs you in">
        ${opt('none', 'No sign-in')}
        ${opt('header', 'A token from Secrets')}
        ${opt('oauth', 'OAuth', 'Save only')}
      </div>`;
  }

  function _credRowsHTML() {
    return P.creds.map((c, i) => `
        <div class="desk-v1-cfr-cred" data-rs-cred="${i}">
          <input type="text" class="desk-v1-rules-textinput" data-rs-cred-header="${i}" value="${esc(c.header)}" placeholder="Header, e.g. Authorization" maxlength="64" autocapitalize="off" spellcheck="false" aria-label="HTTP header the token goes in">
          <input type="text" class="desk-v1-rules-textinput" data-rs-cred-prefix="${i}" value="${esc(c.prefix)}" placeholder="Before the token, e.g. Bearer " maxlength="40" autocapitalize="off" spellcheck="false" aria-label="Text before the token">
          <input type="text" class="desk-v1-rules-textinput" data-rs-cred-vault="${i}" value="${esc(c.vault)}" placeholder="Secrets entry name" maxlength="120" autocapitalize="off" spellcheck="false" aria-label="Secrets entry that holds the token">
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-rs-cred-remove="${i}">Remove</button>
        </div>`).join('');
  }
  function _credsHTML() {
    const back = _prevHTML('auth', 'sign-in');
    if (P.auth === 'oauth') {
      return `${back}
        <div class="desk-v1-cfw-msg" data-rs-oauth role="status">${esc(OAUTH_NOTE)}</div>
        <label class="desk-v1-conn-add-field">Issuer address
          <input type="text" class="desk-v1-rules-textinput" data-rs-issuer data-cfw-focus value="${esc(P.issuer)}" placeholder="https://auth.example.com" maxlength="300" autocapitalize="off" spellcheck="false"></label>
        <label class="desk-v1-conn-add-field">Scopes (optional, separated by spaces)
          <input type="text" class="desk-v1-rules-textinput" data-rs-scopes value="${esc(P.scopes)}" maxlength="400" autocapitalize="off" spellcheck="false"></label>`;
    }
    return `${back}
      <div class="desk-v1-conn-add-field" data-rs-creds>Token header, by Secrets name only
        ${_credRowsHTML()}
        <div class="desk-v1-cfw-fact-text">The token is read from Secrets when the server is used and sent only to the address you entered. It is never typed here and never written into the MCP configuration.</div>
        <div class="desk-v1-cfr-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-rs-cred-add ${P.creds.length >= MAX_CREDS ? 'disabled' : ''}>Add a token header</button></div>
      </div>`;
  }

  function _prevHTML(view, what) { return `<div class="desk-v1-cfr-prev"><button type="button" class="desk-v1-cf-link" data-rs-prev="${view}">‹ Back to ${esc(what)}</button></div>`; }
  function _rprevHTML(what) { return `<div class="desk-v1-cfr-prev"><button type="button" class="desk-v1-cf-link" data-rs-rprev>‹ Back to ${esc(what)}</button></div>`; }

  const SETUP_TITLE = { address: 'MCP server address', auth: 'Server sign-in', creds: 'Server credentials' };
  const SETUP_COPY = {
    address: 'Enter the server address. Nothing contacts it before approval.',
    auth: 'Choose how this server authenticates.',
    creds: 'Credentials go only to the recipient you approve.',
  };

  // ── Review: the request, the card, the views ────────────────────────────
  function _reach() {
    const S = window.DeskV1ConnectPermissionsStep;
    return S && typeof S.reach === 'function' ? S.reach() : null;
  }
  // { body, problem }. `acknowledge` is added by _withAck: the exposure ticks are not part of what keys a card.
  function _requestBody() {
    const r = _reach();
    if (!r) return { body: null, problem: 'The Permissions step has not chosen who can use this server.' };
    if (r.scope === 'project' && !r.project_id) return { body: null, problem: 'Choose a project on the Permissions step.' };
    const body = { url: P.url.trim(), scope: r.scope, auth: P.auth };
    if (r.scope === 'project') body.project_id = r.project_id;
    if (P.protocol) body.protocol = P.protocol;
    if (P.name.trim()) body.server_name = P.name.trim();
    if (P.auth === 'oauth') {
      body.issuer = P.issuer.trim();
      const scopes = P.scopes.split(/[\s,]+/).filter(Boolean);
      if (scopes.length) body.scopes = scopes;
    }
    if (P.auth === 'header') {
      body.credentials = _creds().filter((c) => c.header || c.vault).map((c) => ({ header: c.header, vault: c.vault, ...(c.prefix.trim() ? { prefix: c.prefix } : {}) }));
    }
    return { body, problem: '' };
  }
  function _withAck(body) {
    const ack = Object.keys(P.ack).filter((k) => P.ack[k]);
    return ack.length ? Object.assign({}, body, { acknowledge: ack }) : body;
  }

  async function _review(api, body) {
    const R = P.rv, gen = _gen;
    R.busy = true; R.error = ''; R.card = null;
    api.approve('card', null);                                               // an approval is for the card it was read on, even if a later review lands on an identical one
    api.repaint();
    let card = null, error = '';
    try { card = await api.ctx.api('POST', '/api/desk/connect/custom/remote/review', _withAck(body)); }
    catch (e) { error = (e && e.message) || 'The review failed.'; }
    if (gen !== _gen) return;
    R.busy = false;
    if (card && card.fingerprint) { R.card = card; R.exp = !!(card.exposure && (card.exposure.required || []).length); }
    else R.error = error || 'The review failed.';
    api.repaint();
  }

  // Called from the Review screens' bind: a changed request is a new review, an unchanged one keeps its card.
  function _ensureReview(api) {
    const R = P.rv, { body } = _requestBody();
    if (!body) return;
    const key = JSON.stringify(body);
    if (R.key !== key) { R.key = key; R.card = null; R.exp = false; R.error = ''; R.view = 'facts'; R.saving = false; P.ack = {}; }   // an exposure tick belongs to the card it was made on
    if (!R.card && !R.busy && !R.error) _review(api, body);
  }

  function _views2() { return ['facts'].concat(P.rv.exp ? ['exposures'] : [], ['approve']); }
  function _rviewIndex() { return Math.max(0, _views2().indexOf(P.rv.view)); }
  function _approved(api) { const R = P.rv; return !!R.card && api.approval('card') === R.card.fingerprint; }
  function _missing(c) { return !!(c && c.exposure && (c.exposure.missing || []).length); }

  function _notReadyHTML() {
    const R = P.rv;
    const problem = _requestBody().problem;
    if (problem) return `<div class="desk-v1-cfw-msg" data-rs-problem role="alert">${esc(problem)}</div>`;
    if (R.busy) return '<div class="desk-v1-cfw-fact-text" data-rs-reviewing role="status">Preparing the review. Nothing is sent to the server.</div>';
    if (R.error) return `<div class="desk-v1-cfw-msg" data-rs-review-error role="alert">${esc(R.error)} <button type="button" class="desk-v1-cf-link" data-rs-rereview>Review again</button></div>`;
    return '<div class="desk-v1-cfw-fact-text" role="status">Preparing the review…</div>';
  }

  function _authText(a) {
    if (a.type === 'oauth') return `OAuth. Save only: the sign-in is not started by this version.`;
    if (a.type === 'header') return 'A token in an HTTP header, from Secrets (below).';
    return 'None: the server is reached without a sign-in.';
  }

  function _factsHTML(c) {
    const fact = (head, html, attr) => `<div class="desk-v1-cfw-fact"${attr ? ` ${attr}` : ''}><div class="desk-v1-cfw-fact-head">${esc(head)}</div><div class="desk-v1-cfw-fact-text">${html}</div></div>`;
    const changes = (c.changes || []).length
      ? `<div class="desk-v1-cfw-msg" data-rs-reask role="status">This server was approved before. It needs a new approval because it changed:
          <ul class="desk-v1-cfr-list">${c.changes.map((x) => `<li data-rs-change="${esc(x.field)}">${esc(x.field)}: <code>${esc(x.from)}</code> to <code>${esc(x.to)}</code></li>`).join('')}</ul></div>` : '';
    const limits = (c.limitations || []).map((l) => `<div class="desk-v1-cfw-msg" data-rs-limit="${esc(l.code)}" role="status">${esc(l.message)}</div>`).join('');
    const creds = (c.credentials || []).length
      ? c.credentials.map((x) => `<div data-rs-credential>Secrets entry <code>${esc(x.vault)}</code> is sent in the <code>${esc(x.header)}</code> header${x.prefix ? ` after <code>${esc(x.prefix)}</code>` : ''}, to <code>${esc(x.recipient)}</code> only.</div>`).join('')
      : 'None';
    const proposed = c.protocol_proposed && !P.protocol ? ` (chosen from the address: ${esc(c.protocol_proposed.basis)}; change it in Setup, under Details, if it is wrong)` : '';
    return `${changes}${limits}
      <div class="desk-v1-cfr-origin" data-rs-origin="${esc(c.origin && c.origin.code)}"><span class="desk-v1-cfw-tag">${esc(c.origin ? c.origin.label : 'User supplied; not reviewed by Clayrune')}</span> <strong>${esc(c.title)}</strong></div>
      ${fact('Address', `<code class="desk-v1-cf-wrap">${esc(c.url)}</code>`, 'data-rs-address')}
      ${fact('Receives', `<code>${esc(c.recipient)}</code>, and nothing else`, 'data-rs-recipient')}
      ${fact('Protocol', `${esc(PROTOCOL_WORD[c.protocol] || c.protocol)}${proposed}`, 'data-rs-protocol-line')}
      ${fact('Sign-in', esc(_authText(c.auth)), `data-rs-auth-line="${esc(c.auth.type)}"`)}
      ${fact('Credentials', creds, 'data-rs-credentials')}
      ${fact('Reach', esc(c.reach.who), `data-rs-reach="${esc(c.reach.scope)}"`)}
      ${fact('Registered as', `MCP server <code>${esc(c.server_name)}</code>`, 'data-rs-server')}
      ${fact('Before you save', esc(c.contact.text), `data-rs-contact="${esc(c.contact.before_save)}"`)}
      <ul class="desk-v1-cfr-list" data-rs-risks>${(c.risks || []).map((r) => `<li data-rs-risk="${esc(r.code)}">${esc(r.label)}</li>`).join('')}</ul>`;
  }

  // Details of the facts view: the launch line, the OAuth issuer and scopes, and what a check would show.
  function _factsDetailsHTML(c) {
    const argv = [c.command.command].concat(c.command.args || []);
    const row = (head, attr, html) => `<div class="desk-v1-cfw-fact" ${attr}><div class="desk-v1-cfw-fact-head">${esc(head)}</div><div class="desk-v1-cfw-fact-text">${html}</div></div>`;
    const oauth = c.auth.type === 'oauth'
      ? row('OAuth', 'data-rs-oauth-facts', `Issuer <code>${esc(c.auth.issuer)}</code>; scopes ${c.auth.scopes && c.auth.scopes.length ? c.auth.scopes.map((s) => `<code>${esc(s)}</code>`).join(' ') : 'none stated'}. Recorded only: the sign-in is not started by this version.`) : '';
    return `<div class="desk-v1-cfw-dgroup" data-rs-more>
        ${row('Starts', 'data-rs-start', `${esc(c.command.runnable === false ? (c.command.why || 'It cannot start on this install.') : 'A local program on this computer relays each request to the address above:')}
          <ol class="desk-v1-cu-argv" data-rs-command>${argv.map((a) => `<li><code>${esc(a)}</code></li>`).join('')}</ol>`)}
        ${oauth}
        ${row('What a check shows', `data-rs-verification="${c.verification.purpose_verified ? 'verified' : 'not_verified'}"`, esc(c.verification.text))}
      </div>`;
  }

  function _exposureItemHTML(c) {
    return (f) => `<label class="desk-v1-cf-check desk-v1-cfr-exposure" data-cfw-alt data-rs-exposure="${esc(f)}"><input type="checkbox" data-rs-ack="${esc(f)}" ${(c.exposure.acknowledged || []).includes(f) ? 'checked' : ''}>
        <span>${esc(EXPOSURE_TEXT[f] || f)} I accept this.</span></label>`;
  }
  function _exposuresHTML(api, c) {
    return `${_rprevHTML('the server facts')}
      <div data-rs-exposures>${api.paged('exposures', (c.exposure && c.exposure.required) || [], _exposureItemHTML(c))}</div>
      ${_missing(c) ? '<div class="desk-v1-cfw-fact-text" data-rs-needs-ack>Tick each exposure above to continue.</div>' : ''}`;
  }

  function _approveHTML(api, c) {
    const risk = (c.risks || []).find((r) => r.code === 'remote_server_can_change');
    const ack = ((c.exposure && c.exposure.acknowledged) || []).length;
    const lines = [`<li data-rs-sum="recipient">Requests go to <code>${esc(c.recipient)}</code> and nothing else.</li>`,
      `<li data-rs-sum="auth">${esc(_authText(c.auth))}</li>`,
      (c.credentials || []).length ? `<li data-rs-sum="credentials">${c.credentials.length} Secrets entr${c.credentials.length === 1 ? 'y is' : 'ies are'} sent to <code>${esc(c.recipient)}</code> only.</li>` : '',
      `<li data-rs-sum="reach">${esc(c.reach.who)}</li>`,
      risk ? `<li data-rs-sum="can-change">${esc(risk.label)}</li>` : '',
      ack ? `<li data-rs-sum="exposures">${ack} exposure${ack === 1 ? '' : 's'} you accepted on the previous page.</li>` : ''].filter(Boolean);
    return `${_rprevHTML(P.rv.exp ? 'the exposures' : 'the server facts')}
      <ul class="desk-v1-cfr-list" data-rs-summary>${lines.join('')}</ul>
      <label class="desk-v1-cf-check desk-v1-cfr-approve"><input type="checkbox" data-rs-approve ${_approved(api) ? 'checked' : ''} ${_missing(c) ? 'disabled' : ''}>
        <span>I have read this and approve saving it.</span></label>
      <div class="desk-v1-cfw-fact-text">Saving asks for your dashboard passcode once. Nothing has been written yet and nothing has been sent to the server.</div>
      ${P.rv.error ? `<div class="desk-v1-cfw-msg" data-rs-save-error role="alert">${esc(P.rv.error)}</div>` : ''}`;
  }

  const REVIEW_TITLE = { facts: 'Review server', exposures: 'Connection risks', approve: 'Approve connection' };
  const REVIEW_COPY = {
    facts: 'This remote server can change. Review its address and credential recipient before approving.',
    exposures: 'Approve each additional exposure to continue.',
    approve: 'Save exactly what you reviewed. Changes require a new approval.',
  };

  // The passcode-gated Save. -> true (saved: go to Result) | false (stay: cancelled, refused, or the card went stale)
  async function _save(api) {
    const R = P.rv, c = R.card;
    if (R.saving || !c || !_approved(api) || _missing(c)) return false;
    R.saving = true; R.error = '';
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/custom/commit', { method: 'POST', body: JSON.stringify({ request_id: c.request_id, fingerprint: c.fingerprint }) },
        { title: 'Save remote MCP server', description: `Re-enter your dashboard passcode to save the remote MCP server “${c.server_name}” at ${c.recipient}, which Clayrune has not reviewed.` });
    } catch (e) { res = { ok: false, status: 0, body: { error: (e && e.message) || 'could not reach the server' } }; }
    R.saving = false;
    if (res === null) { api.repaint(); return false; }                       // cancelled at the passcode: nothing was sent, the card stays
    if (!res.ok) {
      const b = res.body || {}, msg = b.error || b.message || `The save failed (HTTP ${res.status}).`;
      if (b.code === 'review_expired' || b.code === 'changed_since_review') { const cur = _requestBody().body; R.card = null; R.key = cur ? JSON.stringify(cur) : ''; R.view = 'facts'; R.error = `${msg} Review again to get a new card.`; }   // key kept: no silent re-review, the person sees why
      else R.error = msg;
      api.repaint();
      return false;
    }
    P.result = res.body || {};
    P.saved = { recipient: c.recipient, auth: c.auth.type, server_name: c.server_name };
    R.card = null; R.key = ''; R.error = ''; P.ack = {};
    return true;
  }

  // ── Result: a check you start, and the passcode-gated accept ────────────
  function _target() {
    const r = P.result || {};
    const t = { server_name: r.server_name, scope: r.scope };
    if (r.scope === 'project') t.project_id = r.project_id;
    return t;
  }
  function _canCheck() { return !!P.result && P.result.state === 'registered' && !!P.saved && P.saved.auth !== 'oauth'; }

  async function _check(api) {
    if (!_canCheck() || (P.check && P.check.busy)) return;
    const gen = _gen;
    P.check = { busy: true }; api.repaint();
    let out = null, error = '';
    try { out = await api.ctx.api('POST', '/api/desk/connect/custom/remote/check', _target()); }
    catch (e) { error = (e && e.message) || 'The check failed.'; }
    if (gen !== _gen) return;
    P.check = out ? { out } : { error: error || 'The check failed.' };
    api.repaint();
  }

  async function _adopt(api) {
    const k = P.check;
    if (!k || !k.out || k.adopting) return;
    const gen = _gen;
    k.adopting = true; api.repaint();
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/custom/remote/adopt', { method: 'POST', body: JSON.stringify({ ..._target(), observed: k.out.observed }) },
        { title: 'Accept remote MCP changes', description: 'Re-enter your dashboard passcode to accept what this remote MCP server now offers. Agents will use its new tools.' });
    } catch (e) { res = { ok: false, status: 0, body: { error: (e && e.message) || 'could not reach the server' } }; }
    if (gen !== _gen) return;
    k.adopting = false;
    if (res === null) { api.repaint(); return; }
    if (!res.ok) { P.check = { error: (res.body && (res.body.error || res.body.message)) || `Accepting failed (HTTP ${res.status}).` }; api.repaint(); return; }
    P.check = { out: { ...res.body, meaning: 'You accepted what the server offers now. This does not show that it does what you want it for.' } };
    api.repaint();
  }

  function _checkHTML(k) {
    if (!k) return '';
    if (k.busy) return '<div class="desk-v1-cfw-fact-text" data-rs-check="busy" role="status">Contacting the server…</div>';
    if (k.error) return `<div class="desk-v1-cfw-msg" data-rs-check="error" role="alert">${esc(k.error)}</div>`;
    const o = k.out;
    const diff = (o.diff || []).length
      ? `<ul class="desk-v1-cfr-list" data-rs-diff>${o.diff.map((d) => `<li data-rs-diff-item="${esc(d.what)}">${esc(d.what)}: ${esc(d.detail)}</li>`).join('')}</ul>` : '';
    const adopt = o.status === 'changed'
      ? `<div class="desk-v1-cfr-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-rs-adopt ${k.adopting ? 'disabled' : ''}>${k.adopting ? 'Accepting…' : 'Accept these changes (asks for your passcode)'}</button></div>` : '';
    const passed = Object.keys(o.checks || {}).length;
    const fact = (head, attr, html) => `<div class="desk-v1-cfw-fact" ${attr}><div class="desk-v1-cfw-fact-head">${esc(head)}</div><div class="desk-v1-cfw-fact-text">${html}</div></div>`;
    return `<div class="desk-v1-cfr-check" data-rs-check="${esc(o.status)}">
        <div class="desk-v1-cfw-msg" data-rs-check-word role="status">${esc(STATUS_WORD[o.status] || o.status)}${o.status === 'changed' ? ' Its earlier check is cleared until you accept what moved.' : ''}</div>
        ${fact('Server says it is', 'data-rs-observed-server', `${esc(o.server.name || 'no name')} ${esc(o.server.version || '')}`)}
        ${fact('Tools offered', 'data-rs-observed-tools', `${o.tool_count}${o.truncated ? ' or more' : ''}${o.tools.length ? `: ${o.tools.slice(0, 12).map((t) => `<code>${esc(t)}</code>`).join(' ')}${o.tools.length > 12 ? ' and more' : ''}` : ''}`)}
        ${fact('Checks passed now', 'data-rs-checks', passed ? 'It answers (initialize and the tool list).' : 'None')}
        ${diff}
        <div class="desk-v1-cfw-fact-text" data-rs-meaning>${esc(o.meaning || 'The server answered. This does not show that it does what you want it for.')}</div>
        ${adopt}
      </div>`;
  }

  // ── the screens ─────────────────────────────────────────────────────────
  function _focusTitle(root) { if (P.refocus) { P.refocus = false; const t = root.querySelector('[data-cfw-title]'); if (t) t.focus({ preventScroll: false }); } }

  W.registerScreen({
    id: 'remote-setup', step: 'setup', match: _isMine,
    substep: () => [_viewIndex() + 1, _views().length],
    title: () => SETUP_TITLE[P.view] || SETUP_TITLE.address,
    copy: () => SETUP_COPY[P.view] || SETUP_COPY.address,
    body: () => {
      if (!_views().includes(P.view)) P.view = 'address';
      return P.view === 'auth' ? _authHTML() : P.view === 'creds' ? _credsHTML() : _addressHTML();
    },
    details: () => (P.view === 'address' ? `<div class="desk-v1-cfw-dgroup" data-rs-address-details>${_addressDetailsHTML()}</div>` : ''),
    primary: (api) => ({
      label: 'Continue', disabled: !!_problemOf(P.view),
      run: async () => {
        const why = _problemOf(P.view);
        if (why) { api.error(why); return false; }
        const v = _views(), at = _viewIndex();
        if (at < v.length - 1) { P.view = v[at + 1]; P.refocus = true; api.repaint(); return false; }
        return true;                                                         // Continue writes nothing: Permissions, then the Review step
      },
    }),
    bind: (root, api) => {
      _focusTitle(root);
      const primary = root.querySelector('[data-cfw-primary]');
      const sync = () => { if (primary) primary.disabled = !!_problemOf(P.view); };
      const field = (sel, key) => { const n = root.querySelector(sel); if (n) n.addEventListener('input', () => { P[key] = n.value; sync(); }); };
      field('[data-rs-url]', 'url'); field('[data-rs-name]', 'name'); field('[data-rs-issuer]', 'issuer'); field('[data-rs-scopes]', 'scopes');
      const proto = root.querySelector('[data-rs-protocol]');
      if (proto) proto.addEventListener('change', () => { P.protocol = proto.value; });
      root.querySelectorAll('[data-rs-auth]').forEach((n) => n.addEventListener('change', () => {
        if (!n.checked) return;
        P.auth = n.value;
        if (P.auth === 'header' && !P.creds.length) P.creds.push({ header: 'Authorization', prefix: 'Bearer ', vault: '' });
        const of = document.querySelector('[data-cfw-stepof]');            // the counter is the frame's; patch it in place so the radio keeps its focus
        if (of) of.textContent = `Setup · ${_viewIndex() + 1} of ${_views().length}`;
      }));
      root.querySelectorAll('[data-rs-cred-header]').forEach((n) => n.addEventListener('input', () => { P.creds[+n.dataset.rsCredHeader].header = n.value; sync(); }));
      root.querySelectorAll('[data-rs-cred-prefix]').forEach((n) => n.addEventListener('input', () => { P.creds[+n.dataset.rsCredPrefix].prefix = n.value; }));
      root.querySelectorAll('[data-rs-cred-vault]').forEach((n) => n.addEventListener('input', () => { P.creds[+n.dataset.rsCredVault].vault = n.value; sync(); }));
      root.querySelectorAll('[data-rs-cred-remove]').forEach((n) => n.addEventListener('click', () => { P.creds.splice(+n.dataset.rsCredRemove, 1); api.repaint(); }));
      const add = root.querySelector('[data-rs-cred-add]');
      if (add) add.addEventListener('click', () => { if (P.creds.length < MAX_CREDS) P.creds.push({ header: P.creds.length ? '' : 'Authorization', prefix: P.creds.length ? '' : 'Bearer ', vault: '' }); api.repaint(); });
      root.querySelectorAll('[data-rs-prev]').forEach((n) => n.addEventListener('click', () => { P.view = n.dataset.rsPrev; P.refocus = true; api.repaint(); }));
    },
    discard: () => { _gen++; P = _fresh(); },
  });

  W.registerScreen({
    id: 'remote-review', step: 'review', match: _isMine,
    substep: () => [_rviewIndex() + 1, _views2().length],
    title: () => REVIEW_TITLE[P.rv.view] || REVIEW_TITLE.facts,
    copy: () => REVIEW_COPY[P.rv.view] || REVIEW_COPY.facts,
    body: (api) => {
      const R = P.rv, c = R.card;
      if (!c) return _notReadyHTML();
      if (!_views2().includes(R.view)) R.view = 'facts';
      if (R.view === 'exposures') return _exposuresHTML(api, c);
      if (R.view === 'approve') return _approveHTML(api, c);
      return _factsHTML(c);
    },
    details: () => (P.rv.card && P.rv.view === 'facts' ? _factsDetailsHTML(P.rv.card) : ''),
    primary: (api) => {
      const R = P.rv, ready = !!R.card && !R.busy && !_requestBody().problem;
      if (ready && R.view === 'approve') return { label: R.saving ? 'Saving…' : 'Save', disabled: R.saving || !_approved(api) || _missing(R.card), run: () => _save(api) };
      return {
        label: 'Continue', disabled: !ready || (R.view === 'exposures' && _missing(R.card)),
        run: async () => { const v = _views2(), at = _rviewIndex(); if (at < v.length - 1) { R.view = v[at + 1]; P.refocus = true; api.repaint(); } return false; },
      };
    },
    bind: (root, api) => {
      _ensureReview(api);
      _focusTitle(root);
      const R = P.rv;
      const again = root.querySelector('[data-rs-rereview]');
      if (again) again.addEventListener('click', () => { R.error = ''; R.key = ''; api.repaint(); });
      root.querySelectorAll('[data-rs-rprev]').forEach((n) => n.addEventListener('click', () => { const v = _views2(), at = _rviewIndex(); R.view = v[Math.max(0, at - 1)]; P.refocus = true; api.repaint(); }));
      root.querySelectorAll('[data-rs-ack]').forEach((n) => n.addEventListener('change', () => {
        P.ack[n.dataset.rsAck] = n.checked;
        const { body } = _requestBody();
        if (body) _review(api, body);                                       // the server builds a new card with this exposure (un)acknowledged: new fingerprint, approval cleared
      }));
      const approve = root.querySelector('[data-rs-approve]'), primary = root.querySelector('[data-cfw-primary]');
      if (approve) approve.addEventListener('change', () => {
        if (R.card) api.approve('card', approve.checked ? R.card.fingerprint : null);
        if (primary) primary.disabled = R.saving || !_approved(api) || _missing(R.card);
      });
    },
    discard: () => { _gen++; P = _fresh(); },
  });

  W.registerScreen({
    id: 'remote-result', step: 'result', match: _isMine,
    title: () => STATE_WORD[(P.result || {}).state] || 'Saved',
    copy: () => 'Your saved server and its status are shown below.',
    body: () => {
      const r = P.result || {}, s = P.saved || {};
      const word = STATE_WORD[r.state] || 'Saved';
      return `<div class="desk-v1-cfw-msg" data-rs-result="${esc(r.state || '')}" role="status"><strong>${esc(word)}</strong>: ${esc(r.message || '')}</div>
        ${r.notice ? `<div class="desk-v1-cfw-msg" data-rs-notice role="status">${esc(r.notice)}</div>` : ''}
        ${s.recipient ? `<div class="desk-v1-cfw-fact" data-rs-saved><div class="desk-v1-cfw-fact-head">Saved server</div><div class="desk-v1-cfw-fact-text">MCP server <code>${esc(s.server_name)}</code>. Its requests go to <code>${esc(s.recipient)}</code> only.</div></div>` : ''}
        <div class="desk-v1-cfw-fact-text">Saving does not contact the server. An agent session reaches it the first time it uses it.${_canCheck() ? ' You can also check it now: that sends the server an opening request and asks for its tool list, and nothing else.' : ''}</div>
        ${s.auth === 'oauth' ? `<div class="desk-v1-cfw-msg" data-rs-oauth-result role="status">${esc(OAUTH_NOTE)} There is no check to run.</div>` : ''}
        <div data-rs-check-slot aria-live="polite">${_checkHTML(P.check)}</div>
        ${_canCheck() ? `<div class="desk-v1-cfr-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-rs-check-run ${P.check && P.check.busy ? 'disabled' : ''}>Check the connection</button></div>` : ''}`;
    },
    // Done uses the common finish callback to refresh saved connection tiles.
    primary: (api) => ({ label: 'Done', run: async () => { api.finish(null, {}); api.repaint(); return false; } }),
    bind: (root, api) => {
      _focusTitle(root);
      const run = root.querySelector('[data-rs-check-run]');
      if (run) run.addEventListener('click', () => _check(api));
      const adopt = root.querySelector('[data-rs-adopt]');
      if (adopt) adopt.addEventListener('click', () => _adopt(api));
    },
    discard: () => { _gen++; P = _fresh(); },
  });

  // For the Review/Result tickets: non-secret facts of what is set up so far. null when nothing is.
  function summary() {
    if (!P.url.trim()) return null;
    const c = P.rv.card;
    return { kind: 'mcp-remote', address: P.url.trim(), saved: !!P.result, state: P.result ? P.result.state : null,
      lines: [`Remote server ${P.url.trim()}`, P.auth === 'oauth' ? 'OAuth (save only)' : P.auth === 'header' ? 'A token from Secrets' : 'No sign-in'].concat(c ? [c.reach && c.reach.who] : []).filter(Boolean) };
  }
  window.DeskV1ConnectRemoteStep = { summary, VARIANT };
})();
