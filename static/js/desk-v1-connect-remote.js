// Desk v1 — Connections: "Add a remote MCP server" by address, and its approval card
// (docs/DESK_SERVICE_PROFILES_SPEC.md sections 6.1 and 6.3; slice U2d). A section of the Connect flow's
// Method step (static/js/desk-v1-connect-flow.js), beside the npm one (desk-v1-connect-custom.js).
// Window-bridged module, no `import` (ground rule 1). The server side is mc/desk_connect/remote_mcp_*.py
// and mc/blueprints/desk_connect_remote_routes.py.
//
//   POST /api/desk/connect/custom/remote/review  the card for the address typed. Sends NOTHING to that
//                                                address and writes nothing.
//   POST /api/desk/connect/custom/commit         the one Save, shared with the npm kind, passcode-gated
//                                                (humanProofFetch). Carries only {request_id, fingerprint}.
//   POST /api/desk/connect/custom/remote/check   a check YOU start after the Save: initialize and the tool
//                                                list, to the approved address only.
//   POST /api/desk/connect/custom/remote/adopt   accept a changed server as the new baseline (passcode).
//
// The card is the server's own text. Any edit to the form drops the card; an exposure the card names
// (http://, a local or private address) is ticked ON the card and re-reviews, because it is part of what
// is approved. Holds no credential: a credential is a header name and the name of a Secrets entry.
// A check that reaches the server is never called verified: it shows that the server answers.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const STATE_WORD = { registered: 'Registered', pending_runtime: 'Saved, cannot run yet', setup_failed: 'Saved, setup failed' };
  const PROTOCOL_WORD = { streamable_http: 'Streamable HTTP', sse: 'SSE (the older event-stream protocol)' };
  const EXPOSURE_TEXT = {
    unencrypted_connection: 'The address is http://, not https://. The connection is not encrypted: anyone on the network path can read or change it, including a token.',
    local_or_private_target: 'The address is on this computer or a private network. The server could reach things the internet cannot.',
  };
  const STATUS_WORD = { baseline_recorded: 'Reached. This is the first look at it.', unchanged: 'Reached. It matches the last look.', adopted: 'Accepted as the new baseline.', changed: 'Reached, but it has changed since the last look.' };

  function _fresh() {
    return { open: false, url: '', protocol: '', name: '', auth: 'none', issuer: '', scopes: '', creds: [], ack: {},
      scope: 'project', projectId: '', busy: false, card: null, approved: false, error: '', status: '', saving: false,
      result: null, check: null };
  }
  let R = _fresh();

  function reset() { R = _fresh(); }

  function _projects() {
    try { return (window.DeskV1Store && window.DeskV1Store.state().projects) || []; } catch (_) { return []; }
  }

  // The card belongs to the form it was made from: any edit drops it and the approval with it.
  function _touch() { R.card = null; R.approved = false; R.error = ''; R.status = ''; R.result = null; R.check = null; }

  function _body() {
    const body = { url: R.url.trim(), scope: R.scope, auth: R.auth };
    if (R.protocol) body.protocol = R.protocol;
    if (R.name.trim()) body.server_name = R.name.trim();
    if (R.auth === 'oauth') {
      body.issuer = R.issuer.trim();
      const scopes = R.scopes.split(/[\s,]+/).filter(Boolean);
      if (scopes.length) body.scopes = scopes;
    }
    if (R.auth === 'header') {
      body.credentials = R.creds.filter((c) => c.header.trim() || c.vault.trim())
        .map((c) => ({ header: c.header.trim(), vault: c.vault.trim(), ...(c.prefix.trim() ? { prefix: c.prefix } : {}) }));
    }
    const ack = Object.keys(R.ack).filter((k) => R.ack[k]);
    if (ack.length) body.acknowledge = ack;
    if (R.scope === 'project') body.project_id = R.projectId;
    return body;
  }

  function _ready() { return !!R.url.trim() && !(R.scope === 'project' && !R.projectId) && !(R.auth === 'header' && !R.creds.length); }

  // ── the form ────────────────────────────────────────────────────────────
  function _formHTML() {
    const projects = _projects();
    const projectOpts = projects.length
      ? projects.map((p) => `<option value="${esc(p.id)}"${R.projectId === p.id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`).join('')
      : '<option value="">No project</option>';
    const creds = R.creds.map((c, i) => `
          <div class="desk-v1-cu-cred" data-cr-cred="${i}">
            <input type="text" class="desk-v1-rules-textinput" data-cr-cred-header="${i}" value="${esc(c.header)}" placeholder="Header, e.g. Authorization" maxlength="64" autocapitalize="off" spellcheck="false" aria-label="HTTP header the token goes in">
            <input type="text" class="desk-v1-rules-textinput" data-cr-cred-prefix="${i}" value="${esc(c.prefix)}" placeholder="Before the token, e.g. Bearer " maxlength="40" autocapitalize="off" spellcheck="false" aria-label="Text before the token">
            <input type="text" class="desk-v1-rules-textinput" data-cr-cred-vault="${i}" value="${esc(c.vault)}" placeholder="Vault entry name" maxlength="120" autocapitalize="off" spellcheck="false" aria-label="Vault entry that holds the token">
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-cred-remove="${i}">Remove</button>
          </div>`).join('');
    const authOpt = (v, label) => `<label class="desk-v1-cf-check"><input type="radio" name="cr-auth" value="${v}" data-cr-auth-opt ${R.auth === v ? 'checked' : ''}><span>${label}</span></label>`;
    return `
        <div class="desk-v1-cu-form" data-cr-form>
          <label class="desk-v1-conn-add-field">Server address
            <input type="text" class="desk-v1-rules-textinput" data-cr-url value="${esc(R.url)}" placeholder="https://mcp.example.com/mcp" maxlength="500" autocapitalize="off" spellcheck="false" inputmode="url"></label>
          <label class="desk-v1-conn-add-field">Protocol
            <select class="desk-v1-rules-textinput" data-cr-protocol>
              <option value=""${R.protocol === '' ? ' selected' : ''}>Choose from the address</option>
              <option value="streamable_http"${R.protocol === 'streamable_http' ? ' selected' : ''}>${esc(PROTOCOL_WORD.streamable_http)}</option>
              <option value="sse"${R.protocol === 'sse' ? ' selected' : ''}>${esc(PROTOCOL_WORD.sse)}</option>
            </select></label>
          <label class="desk-v1-conn-add-field">Server name (optional)
            <input type="text" class="desk-v1-rules-textinput" data-cr-name value="${esc(R.name)}" maxlength="64" autocapitalize="off" spellcheck="false"></label>
          <fieldset class="desk-v1-cu-scope" data-cr-auth>
            <legend>How it signs you in</legend>
            ${authOpt('none', 'It needs no sign-in')}
            ${authOpt('header', 'A token in an HTTP header, from the Vault')}
            ${authOpt('oauth', 'OAuth (recorded only: this version cannot start the sign-in)')}
          </fieldset>
          ${R.auth === 'header' ? `<div class="desk-v1-conn-add-field">Token header, by Vault name only
            ${creds}
            <div class="desk-v1-rules-hint">The token is read from the Vault when the server is used and sent only to the address above. It is never typed here and never written into the MCP configuration.</div>
            <div><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-cred-add>Add a token header</button></div>
          </div>` : ''}
          ${R.auth === 'oauth' ? `
          <label class="desk-v1-conn-add-field">Issuer address
            <input type="text" class="desk-v1-rules-textinput" data-cr-issuer value="${esc(R.issuer)}" placeholder="https://auth.example.com" maxlength="300" autocapitalize="off" spellcheck="false"></label>
          <label class="desk-v1-conn-add-field">Scopes (optional, separated by spaces)
            <input type="text" class="desk-v1-rules-textinput" data-cr-scopes value="${esc(R.scopes)}" maxlength="400" autocapitalize="off" spellcheck="false"></label>` : ''}
          <fieldset class="desk-v1-cu-scope" data-cr-scope>
            <legend>Who can use it</legend>
            <label class="desk-v1-cf-check"><input type="radio" name="cr-scope" value="project" data-cr-scope-opt ${R.scope === 'project' ? 'checked' : ''}>
              <span>One project (recommended)
                <select class="desk-v1-rules-textinput" data-cr-project ${R.scope === 'project' ? '' : 'disabled'}>${projectOpts}</select></span></label>
            <label class="desk-v1-cf-check"><input type="radio" name="cr-scope" value="global" data-cr-scope-opt ${R.scope === 'global' ? 'checked' : ''}>
              <span>Global: agents in every project can use its tools</span></label>
          </fieldset>
          ${R.error ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cr-error role="alert">${esc(R.error)}</div>` : ''}
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-close>Close</button>
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cr-review ${R.busy || !_ready() ? 'disabled' : ''}>${R.busy ? 'Reviewing…' : 'Review'}</button>
          </div>
        </div>`;
  }

  // ── the approval card ───────────────────────────────────────────────────
  function _authText(a) {
    if (a.type === 'oauth') return `OAuth. Issuer <code>${esc(a.issuer)}</code>; scopes ${a.scopes && a.scopes.length ? a.scopes.map((s) => `<code>${esc(s)}</code>`).join(' ') : 'none stated'}. Recorded only: the sign-in is not started by this version.`;
    if (a.type === 'header') return 'A token in an HTTP header, from the Vault (below).';
    return 'None: the server is reached without a sign-in.';
  }

  function _cardHTML(c) {
    const argv = [c.command.command].concat(c.command.args || []);
    const changes = (c.changes || []).length
      ? `<div class="desk-v1-cf-msg" data-cf-msg="warn" data-cr-reask role="status">This server was approved before. It needs a new approval because it changed:
            <ul class="desk-v1-cu-changes">${c.changes.map((x) => `<li data-cr-change="${esc(x.field)}">${esc(x.field)}: <code>${esc(x.from)}</code> to <code>${esc(x.to)}</code></li>`).join('')}</ul></div>` : '';
    const limits = (c.limitations || []).map((l) => `<div class="desk-v1-cf-msg" data-cf-msg="warn" data-cr-limit="${esc(l.code)}" role="status">${esc(l.message)}</div>`).join('');
    const creds = (c.credentials || []).length
      ? c.credentials.map((x) => `<div data-cr-credential>Vault entry <code>${esc(x.vault)}</code> is sent in the <code>${esc(x.header)}</code> header${x.prefix ? ` after <code>${esc(x.prefix)}</code>` : ''}, to <code>${esc(x.recipient)}</code> only.</div>`).join('')
      : 'None';
    const required = (c.exposure && c.exposure.required) || [];
    const exposure = required.map((f) => `
          <label class="desk-v1-cf-check" data-cr-exposure="${esc(f)}"><input type="checkbox" data-cr-ack="${esc(f)}" ${(c.exposure.acknowledged || []).includes(f) ? 'checked' : ''}>
            <span>${esc(EXPOSURE_TEXT[f] || f)} I accept this.</span></label>`).join('');
    const missing = ((c.exposure && c.exposure.missing) || []).length;
    const proposed = c.protocol_proposed && !R.protocol ? ` <span class="desk-v1-rules-hint">(chosen from the address: ${esc(c.protocol_proposed.basis)}; change it above if it is wrong)</span>` : '';
    return `
        <section class="desk-v1-cf-install desk-v1-cu-card" data-cr-card="${esc(c.fingerprint)}" aria-label="Approve this remote MCP server">
          <div class="desk-v1-rules-group-title">${esc(c.title)}</div>
          <div class="desk-v1-cf-badge" data-cr-origin="${esc(c.origin.code)}">${esc(c.origin.label)}</div>
          ${changes}
          <dl class="desk-v1-cf-facts">
            <div><dt>Address</dt><dd data-cr-address><code class="desk-v1-cf-wrap">${esc(c.url)}</code></dd></div>
            <div><dt>Receives</dt><dd data-cr-recipient><code>${esc(c.recipient)}</code>, and nothing else</dd></div>
            <div><dt>Protocol</dt><dd data-cr-protocol-line>${esc(PROTOCOL_WORD[c.protocol] || c.protocol)}${proposed}</dd></div>
            <div><dt>Sign-in</dt><dd data-cr-auth-line="${esc(c.auth.type)}">${_authText(c.auth)}</dd></div>
            <div><dt>Credentials</dt><dd data-cr-credentials>${creds}</dd></div>
            <div><dt>Reach</dt><dd data-cr-reach="${esc(c.reach.scope)}">${esc(c.reach.who)}</dd></div>
            <div><dt>Registered as</dt><dd data-cr-server>MCP server <code>${esc(c.server_name)}</code></dd></div>
            <div><dt>Starts</dt><dd>${esc(c.command.runnable === false ? (c.command.why || 'It cannot start on this install.') : 'A local program on this computer relays each request to the address above:')}
              <ol class="desk-v1-cu-argv" data-cr-command>${argv.map((a) => `<li><code>${esc(a)}</code></li>`).join('')}</ol></dd></div>
            <div><dt>Before you save</dt><dd data-cr-contact="${esc(c.contact.before_save)}">${esc(c.contact.text)}</dd></div>
            <div><dt>What a check shows</dt><dd data-cr-verification="${c.verification.purpose_verified ? 'verified' : 'not_verified'}">${esc(c.verification.text)}</dd></div>
          </dl>
          <ul class="desk-v1-cf-perms" data-cr-risks>${c.risks.map((r) => `<li data-cr-risk="${esc(r.code)}">${esc(r.label)}</li>`).join('')}</ul>
          ${exposure}
          ${limits}
          <label class="desk-v1-cf-check"><input type="checkbox" data-cr-approve ${R.approved ? 'checked' : ''} ${missing ? 'disabled' : ''}>
            <span>I have read this and approve saving it.</span></label>
          ${missing ? '<div class="desk-v1-rules-hint" data-cr-needs-ack>Tick each exposure above before approving.</div>' : ''}
          <div class="desk-v1-rules-hint">Saving asks for your dashboard passcode once. Nothing has been written yet and nothing has been sent to the server.</div>
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cr-save ${R.saving || !R.approved || missing ? 'disabled' : ''}>${R.saving ? 'Saving…' : 'Save'}</button>
          </div>
        </section>`;
  }

  // ── after the Save: a check you start ───────────────────────────────────
  function _checkHTML(k) {
    if (!k) return '';
    if (k.busy) return '<div class="desk-v1-rules-hint" data-cr-check="busy" role="status">Contacting the server…</div>';
    if (k.error) return `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cr-check="error" role="alert">${esc(k.error)}</div>`;
    const o = k.out;
    const diff = (o.diff || []).length
      ? `<ul class="desk-v1-cu-changes" data-cr-diff>${o.diff.map((d) => `<li data-cr-diff-item="${esc(d.what)}">${esc(d.what)}: ${esc(d.detail)}</li>`).join('')}</ul>` : '';
    const adopt = o.status === 'changed'
      ? `<div class="desk-v1-cf-actions desk-v1-cf-actions-flat"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-adopt ${k.adopting ? 'disabled' : ''}>${k.adopting ? 'Accepting…' : 'Accept these changes (asks for your passcode)'}</button></div>` : '';
    const checks = Object.keys(o.checks || {});
    return `
        <div class="desk-v1-cu-result" data-cr-check="${esc(o.status)}">
          <div class="desk-v1-cf-msg" data-cf-msg="${o.status === 'changed' ? 'warn' : 'ok'}" role="status">${esc(STATUS_WORD[o.status] || o.status)}
            ${o.status === 'changed' ? 'Its earlier check is cleared until you accept what moved.' : ''}</div>
          <dl class="desk-v1-cf-facts">
            <div><dt>Server says it is</dt><dd data-cr-observed-server>${esc(o.server.name || 'no name')} ${esc(o.server.version || '')}</dd></div>
            <div><dt>Tools offered</dt><dd data-cr-observed-tools>${o.tool_count}${o.truncated ? ' or more' : ''}${o.tools.length ? `: ${o.tools.slice(0, 12).map((t) => `<code>${esc(t)}</code>`).join(' ')}${o.tools.length > 12 ? ' and more' : ''}` : ''}</dd></div>
            <div><dt>Checks passed now</dt><dd data-cr-checks>${checks.length ? 'It answers (initialize and the tool list).' : 'None'}</dd></div>
          </dl>
          ${diff}
          <div class="desk-v1-rules-hint" data-cr-meaning>${esc(o.meaning || 'The server answered. This does not show that it does what you want it for.')}</div>
          ${adopt}
        </div>`;
  }

  function _resultHTML(r) {
    const word = STATE_WORD[r.state] || 'Saved';
    const kind = r.state === 'registered' ? 'ok' : 'warn';
    const canCheck = r.state === 'registered';
    return `
        <div class="desk-v1-cu-result" data-cr-result="${esc(r.state)}">
          <div class="desk-v1-cf-msg" data-cf-msg="${kind}" role="status"><strong>${esc(word)}</strong>: ${esc(r.message || '')}</div>
          ${r.notice ? `<div class="desk-v1-cf-msg" data-cf-msg="warn" data-cr-notice role="status">${esc(r.notice)}</div>` : ''}
          <div class="desk-v1-rules-hint">Saving does not contact the server. An agent session reaches it the first time it uses it. You can also check it now: that sends the server an opening request and asks for its tool list, and nothing else.</div>
          ${_checkHTML(R.check)}
          <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
            ${canCheck ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-check-run ${R.check && R.check.busy ? 'disabled' : ''}>Check the connection</button>` : ''}
            <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-done>Done</button>
          </div>
        </div>`;
  }

  function html(info) {
    if (!info) return '';
    if (!R.open) {
      return `<div class="desk-v1-cu desk-v1-cr-closed" data-cr="closed">
          <span class="desk-v1-rules-hint">Know a remote MCP server's address? Add it yourself (Streamable HTTP or SSE).</span>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cr-open>Add a remote MCP server</button>
        </div>`;
    }
    const saved = R.result && !!R.result.state;
    return `<div class="desk-v1-cu" data-cr="open">
        <div class="desk-v1-rules-group-title">Add a remote MCP server</div>
        ${saved || R.card ? '' : '<div class="desk-v1-rules-hint">Clayrune shows exactly what will be reached and approved before anything is saved, and sends nothing to the server until you ask.</div>'}
        ${saved ? _resultHTML(R.result) : _formHTML()}
        ${R.card && !saved ? _cardHTML(R.card) : ''}
        ${R.card && R.status ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cr-status role="alert">${esc(R.status)}</div>` : ''}
      </div>`;
  }

  // ── actions ─────────────────────────────────────────────────────────────
  async function _review(ctx) {
    if (R.busy) return;
    _touch(); R.busy = true; ctx.repaint();
    let card = null, error = '';
    try {
      card = await ctx.api('POST', '/api/desk/connect/custom/remote/review', _body());
    } catch (e) {
      error = (e && e.message) || 'The review failed.';
    }
    R.busy = false;
    if (card && card.fingerprint) R.card = card; else R.error = error || 'The review failed.';
    ctx.repaint();
  }

  async function _save(ctx) {
    const c = R.card;
    if (R.saving || !c || !R.approved || (c.exposure && c.exposure.missing && c.exposure.missing.length)) return;
    R.saving = true; R.status = ''; ctx.repaint();
    let result;
    try {
      result = await window.humanProofFetch('/api/desk/connect/custom/commit', {
        method: 'POST', body: JSON.stringify({ request_id: c.request_id, fingerprint: c.fingerprint }),
      }, { title: 'Save remote MCP server', description: `Re-enter your dashboard passcode to save the remote MCP server “${c.server_name}” at ${c.recipient}, which Clayrune has not reviewed.` });
    } catch (e) {
      result = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    R.saving = false;
    if (result === null) { ctx.repaint(); return; }          // cancelled at the passcode: nothing was sent, the card stays
    if (!result.ok) {
      R.status = (result.body && (result.body.error || result.body.message)) || `The save failed (HTTP ${result.status}).`;
      if (result.body && (result.body.code === 'review_expired' || result.body.code === 'changed_since_review')) { R.card = null; R.approved = false; R.error = R.status; R.status = ''; }
      ctx.repaint();
      return;
    }
    R.result = result.body || {}; R.card = null; R.approved = false; R.status = '';
    ctx.repaint();
  }

  function _target() {
    const r = R.result || {};
    const t = { server_name: r.server_name, scope: r.scope };
    if (r.scope === 'project') t.project_id = r.project_id;
    return t;
  }

  async function _check(ctx) {
    if (!R.result || (R.check && R.check.busy)) return;
    R.check = { busy: true }; ctx.repaint();
    try {
      const out = await ctx.api('POST', '/api/desk/connect/custom/remote/check', _target());
      R.check = { out };
    } catch (e) {
      R.check = { error: (e && e.message) || 'The check failed.' };
    }
    ctx.repaint();
  }

  async function _adopt(ctx) {
    const k = R.check;
    if (!k || !k.out || k.adopting) return;
    k.adopting = true; ctx.repaint();
    let result;
    try {
      result = await window.humanProofFetch('/api/desk/connect/custom/remote/adopt', {
        method: 'POST', body: JSON.stringify({ ..._target(), observed: k.out.observed }),
      }, { title: 'Accept remote MCP changes', description: 'Re-enter your dashboard passcode to accept what this remote MCP server now offers. Agents will use its new tools.' });
    } catch (e) {
      result = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    k.adopting = false;
    if (result === null) { ctx.repaint(); return; }
    if (!result.ok) { R.check = { error: (result.body && (result.body.error || result.body.message)) || `Accepting failed (HTTP ${result.status}).` }; ctx.repaint(); return; }
    R.check = { out: { ...result.body, meaning: 'You accepted what the server offers now. This does not show that it does what you want it for.' } };
    ctx.repaint();
  }

  // ctx: { api(method, url, body), repaint() }
  function bind(root, info, ctx) {
    const el = root.querySelector('[data-cr]');
    if (!info || !el) return;
    const open = el.querySelector('[data-cr-open]');
    if (open) open.addEventListener('click', () => { R.open = true; if (!R.projectId) { const p = _projects()[0]; R.projectId = p ? p.id : ''; } ctx.repaint(); });
    el.querySelectorAll('[data-cr-close], [data-cr-done]').forEach((b) => b.addEventListener('click', () => { reset(); ctx.repaint(); }));
    const sync = () => { const r = el.querySelector('[data-cr-review]'); if (r) r.disabled = R.busy || !_ready(); };
    const drop = () => { _touch(); sync(); el.querySelectorAll('[data-cr-card], [data-cr-status], [data-cr-result]').forEach((n) => n.remove()); };
    const field = (sel, key) => { const n = el.querySelector(sel); if (n) n.addEventListener('input', () => { R[key] = n.value; R.ack = key === 'url' ? {} : R.ack; drop(); }); };
    field('[data-cr-url]', 'url'); field('[data-cr-name]', 'name'); field('[data-cr-issuer]', 'issuer'); field('[data-cr-scopes]', 'scopes');
    const proto = el.querySelector('[data-cr-protocol]');
    if (proto) proto.addEventListener('change', () => { R.protocol = proto.value; _touch(); ctx.repaint(); });
    el.querySelectorAll('[data-cr-cred-header]').forEach((n) => n.addEventListener('input', () => { R.creds[+n.dataset.crCredHeader].header = n.value; drop(); }));
    el.querySelectorAll('[data-cr-cred-prefix]').forEach((n) => n.addEventListener('input', () => { R.creds[+n.dataset.crCredPrefix].prefix = n.value; drop(); }));
    el.querySelectorAll('[data-cr-cred-vault]').forEach((n) => n.addEventListener('input', () => { R.creds[+n.dataset.crCredVault].vault = n.value; drop(); }));
    el.querySelectorAll('[data-cr-cred-remove]').forEach((n) => n.addEventListener('click', () => { R.creds.splice(+n.dataset.crCredRemove, 1); _touch(); ctx.repaint(); }));
    const add = el.querySelector('[data-cr-cred-add]');
    if (add) add.addEventListener('click', () => { R.creds.push({ header: R.creds.length ? '' : 'Authorization', prefix: R.creds.length ? '' : 'Bearer ', vault: '' }); _touch(); ctx.repaint(); });
    el.querySelectorAll('[data-cr-auth-opt]').forEach((n) => n.addEventListener('change', () => {
      if (!n.checked) return;
      R.auth = n.value; if (R.auth === 'header' && !R.creds.length) R.creds.push({ header: 'Authorization', prefix: 'Bearer ', vault: '' });
      _touch(); ctx.repaint();
    }));
    el.querySelectorAll('[data-cr-scope-opt]').forEach((n) => n.addEventListener('change', () => { if (n.checked) { R.scope = n.value; _touch(); ctx.repaint(); } }));
    const proj = el.querySelector('[data-cr-project]');
    if (proj) proj.addEventListener('change', () => { R.projectId = proj.value; _touch(); ctx.repaint(); });
    const review = el.querySelector('[data-cr-review]');
    if (review) review.addEventListener('click', () => _review(ctx));
    el.querySelectorAll('[data-cr-ack]').forEach((n) => n.addEventListener('change', () => { R.ack[n.dataset.crAck] = n.checked; _review(ctx); }));
    const approve = el.querySelector('[data-cr-approve]');
    const save = el.querySelector('[data-cr-save]');
    if (approve) approve.addEventListener('change', () => { R.approved = approve.checked; if (save) save.disabled = R.saving || !R.approved; });
    if (save) save.addEventListener('click', () => _save(ctx));
    const run = el.querySelector('[data-cr-check-run]');
    if (run) run.addEventListener('click', () => _check(ctx));
    const adopt = el.querySelector('[data-cr-adopt]');
    if (adopt) adopt.addEventListener('click', () => _adopt(ctx));
  }

  window.DeskV1ConnectRemote = { html, bind, reset };
})();
