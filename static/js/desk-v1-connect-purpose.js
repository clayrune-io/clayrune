// Desk v1 — Connections: a known service's routes grouped by PURPOSE, and one account bound to a
// different route per purpose (docs/DESK_SERVICE_PROFILES_SPEC.md sections 3.3 and 4; slice P2).
// A section of the Connect flow's Method step (static/js/desk-v1-connect-flow.js). Window-bridged
// module, no `import` (ground rule 1). The server side is mc/desk_connect/purpose_*.py and
// mc/blueprints/desk_connect_purpose_routes.py.
//
//   POST /api/desk/connect/purposes        read-only: every route by purpose, the service's accounts,
//                                          what each has bound (setup + verification), vault NAMES
//   POST /api/desk/connect/purpose/commit  the one Save, passcode-gated (humanProofFetch)
//   POST /api/desk/connect/purpose/verify  one free read-only check per bound route that has one
//
// What this screen does not do: choose for the person (a capability is bound only when its box is
// ticked, and nothing is ticked for a route that costs money), start a sign-in, create a browser
// profile, or call a route "verified" because it was saved. A credential is picked from the vault BY
// NAME; no value is ever read or shown here. Nothing is written before Save. A sign-in route can also
// store a NEW login (slice P2b: the shared form, in static/js/desk-v1-connect-signin.js, which also
// owns "Sign in with the saved login"); it rides this Save and creates the vault entry only then.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const NEW = '__new';
  const COVERED = ['documented', 'claimed'];
  const COVERAGE_WORD = { documented: 'documented', claimed: 'claimed', unknown: 'not stated', unavailable: 'not offered', not_offered: 'not offered' };
  const SETUP_WORD = { ready: 'Ready', needs_signin: 'Needs sign-in', vault_locked: 'Vault locked', pending_runtime: 'Saved; cannot run yet' };
  const VERIFY_WORD = { verified: 'Verified', partial: 'Partly verified', not_checked: 'Not checked' };
  const ROLE_FOR_AUTH = { browser_signin: 'login', api_key: 'api_key', bearer: 'api_key' };
  const ROLE_LABEL = { login: 'Stored login', api_key: 'Stored API key' };

  function _fresh() {
    return { service: '', view: null, loading: false, accountId: '', kind: '', newIdentity: '', newLabel: '',
      sel: {}, prof: {}, creds: {}, dirty: false, requestId: '', status: '', statusKind: 'ok', saving: false, checking: '', checks: {}, formShown: {} };
  }
  let P = _fresh();

  function reset() { P = _fresh(); if (window.DeskV1ConnectSignin) window.DeskV1ConnectSignin.reset(); }

  // ── reading the view ────────────────────────────────────────────────────
  function _account() { return P.view && P.accountId !== NEW ? P.view.accounts.find((a) => a.id === P.accountId) || null : null; }
  function _purposesFor(kind) { return (P.view ? P.view.purposes : []).filter((p) => p.bindable && (!kind || p.account_kinds.indexOf(kind) >= 0)); }
  function _route(purpose, id) { const p = P.view.purposes.find((x) => x.id === purpose); return p ? p.routes.find((r) => r.id === id) : null; }
  function eligible(view) { return !!view && view.purposes.some((p) => p.bindable); }

  // The draft's starting point is what the account already has bound: capability by capability.
  function _seed() {
    const a = _account();
    P.sel = {}; P.prof = {}; P.creds = {};
    if (a) {
      P.kind = a.account_kind || P.kind || (P.view.account_kinds.length === 1 ? P.view.account_kinds[0].id : '');
      a.bound.forEach((b) => {
        (P.sel[b.purpose] = P.sel[b.purpose] || {});
        b.capabilities.forEach((c) => { P.sel[b.purpose][c] = b.route_id; });
        const refs = b.refs || {};
        if (refs.browser_profile) P.prof[b.route_id] = refs.browser_profile;
        ['login', 'api_key'].forEach((role) => { if (refs[role]) (P.creds[b.route_id] = P.creds[b.route_id] || {})[role] = refs[role]; });
      });
    } else if (!P.kind && P.view.account_kinds.length === 1) P.kind = P.view.account_kinds[0].id;
    P.dirty = false; P.requestId = '';
  }

  function _touch() { P.dirty = true; P.requestId = ''; P.status = ''; }

  // ── the draft ───────────────────────────────────────────────────────────
  function _draftBindings() {
    const a = _account();
    const was = {};
    if (a) a.bound.forEach((b) => b.capabilities.forEach((c) => { was[`${b.purpose}.${c}`] = b.route_id; }));
    const out = [];
    _purposesFor(P.kind).forEach((p) => {
      const chosen = P.sel[p.id] || {};
      const byRoute = {};
      Object.keys(chosen).forEach((c) => { if (chosen[c]) (byRoute[chosen[c]] = byRoute[chosen[c]] || []).push(c); });
      Object.keys(byRoute).forEach((rid) => {
        const r = _route(p.id, rid);
        const b = { purpose: p.id, route_id: rid, capabilities: byRoute[rid].sort() };
        if (r && r.transport === 'browser' && (P.prof[rid] || '').trim()) b.browser_profile = P.prof[rid].trim();
        const cr = P.creds[rid] || {};
        const named = Object.keys(cr).filter((k) => cr[k]);
        const typed = r && r.signin && window.DeskV1ConnectSignin ? window.DeskV1ConnectSignin.read(rid) : null;
        if (typed && typed.new_login) { b.new_login = typed.new_login; const at = named.indexOf('login'); if (at >= 0) named.splice(at, 1); }   // typed or picked, never both
        if (named.length) { b.credentials = {}; named.forEach((k) => { b.credentials[k] = cr[k]; }); }
        out.push(b);
      });
      const gone = p.capabilities.map((c) => c.id).filter((c) => was[`${p.id}.${c}`] && !chosen[c]);
      if (gone.length) out.push({ purpose: p.id, route_id: null, capabilities: gone.sort() });
    });
    return out;
  }

  function _draft() {
    const a = P.accountId === NEW ? { new: { identity: P.newIdentity.trim(), label: P.newLabel.trim() || undefined } } : { id: P.accountId };
    return { service: P.view.service.id, revision: P.view.revision, account: a, account_kind: P.kind, bindings: _draftBindings() };
  }

  function _problem() {
    if (!P.accountId) return 'Pick the account these routes are for.';
    if (P.accountId === NEW && !P.newIdentity.trim()) return 'Name the new account (its handle, profile name or page name).';
    if (!P.kind) return 'Say what kind of account this is.';
    const bound = _draftBindings();
    if (!bound.length) return 'Tick at least one capability on a route.';
    const SI = window.DeskV1ConnectSignin;
    for (const b of bound) { const t = SI && b.route_id ? SI.read(b.route_id) : null; if (t && t.error) return t.error; }
    if (!P.dirty && P.accountId !== NEW) return 'Nothing has changed.';
    return '';
  }

  function _summary() {
    const lines = [];
    _draftBindings().forEach((b) => {
      const p = P.view.purposes.find((x) => x.id === b.purpose);
      const label = p ? p.label : b.purpose;
      const caps = b.capabilities.join(', ');
      if (!b.route_id) { lines.push(`Remove ${label}: ${caps}`); return; }
      const r = _route(b.purpose, b.route_id);
      const extra = [b.browser_profile ? `browser profile ${b.browser_profile}` : '', ...Object.keys(b.credentials || {}).map((k) => `${ROLE_LABEL[k] || k}: ${b.credentials[k]}`),
        b.new_login ? `New stored login: ${b.new_login.name}, stored when you press Save` : ''].filter(Boolean);
      lines.push(`${label}: ${caps} via ${r ? r.title : b.route_id}${extra.length ? ` (${extra.join('; ')})` : ''}`);
    });
    return lines;
  }

  // ── rendering ───────────────────────────────────────────────────────────
  function _accountHTML() {
    const v = P.view;
    const opts = v.accounts.map((a) => `<option value="${esc(a.id)}"${P.accountId === a.id ? ' selected' : ''}>${esc(a.label)}${a.identity && a.identity !== a.label ? ` (${esc(a.identity)})` : ''}</option>`);
    if (v.new_account_allowed) opts.push(`<option value="${NEW}"${P.accountId === NEW ? ' selected' : ''}>A new account…</option>`);
    const none = !v.accounts.length && !v.new_account_allowed
      ? `<div class="desk-v1-rules-hint" data-cp-noaccount>There is no ${esc(v.service.label)} account in the Desk yet. Add it first (Connect, then ${esc(v.service.label)}'s sign-in); then choose its routes here.</div>` : '';
    const kinds = v.account_kinds.length > 1 && !_account_kind_fixed()
      ? `<label class="desk-v1-conn-add-field">Kind of account
          <select class="desk-v1-rules-textinput" data-cp-kind>${['<option value="">Choose…</option>'].concat(v.account_kinds.map((k) => `<option value="${esc(k.id)}"${P.kind === k.id ? ' selected' : ''}>${esc(k.label)}</option>`)).join('')}</select></label>`
      : '';
    const fresh = P.accountId === NEW
      ? `<label class="desk-v1-conn-add-field">Name of the account
          <input type="text" class="desk-v1-rules-textinput" data-cp-new-identity maxlength="120" value="${esc(P.newIdentity)}" placeholder="${P.kind === 'organization' ? 'Clayrune' : 'Your name'}" autocapitalize="off" spellcheck="false"></label>
        <label class="desk-v1-conn-add-field">Label (optional)
          <input type="text" class="desk-v1-rules-textinput" data-cp-new-label maxlength="120" value="${esc(P.newLabel)}"></label>` : '';
    return `<div class="desk-v1-cp-account" data-cp-account-box>
        ${opts.length ? `<label class="desk-v1-conn-add-field">Account
          <select class="desk-v1-rules-textinput" data-cp-account><option value="">Choose an account…</option>${opts.join('')}</select></label>` : ''}
        ${none}${kinds}${fresh}</div>`;
  }
  // An account whose kind the Desk already knows (bound routes, an X account, a Company Page id) is not asked again.
  function _account_kind_fixed() { const a = _account(); return !!(a && a.account_kind); }

  function _costHTML(r) {
    return `<span class="desk-v1-cp-cost" data-cp-cost>${esc(r.cost.lines.length ? r.cost.lines.join('; ') : (r.cost.basis === 'free' ? 'No charge stated' : 'Cost not stated'))}</span>`;
  }

  function _detailsHTML(r) {
    const ev = r.evidence;
    const links = ev.links.map((l) => `<a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join(', ');
    const when = ev.last_verified ? `read ${esc(ev.last_verified)}${ev.age_days != null ? ` (${ev.age_days} days ago)` : ''}` : 'not verified against the provider';
    return `<details class="desk-v1-cp-more" data-cp-more>
        <summary>Requirements, limits and evidence</summary>
        ${r.requirements.length ? `<ul class="desk-v1-cp-list">${r.requirements.map((q) => `<li>${esc(q.text)}</li>`).join('')}</ul>` : ''}
        ${r.limits.length ? `<div class="desk-v1-cp-limits">Limits: ${r.limits.map((l) => esc(l.name)).join('; ')}</div>` : ''}
        ${r.auth.length ? `<div class="desk-v1-cp-limits">Sign-in: ${r.auth.map((a) => `${esc(a.type)}${a.scopes.length ? ` (${esc(a.scopes.join(', '))})` : ''}`).join('; ')}</div>` : ''}
        <div class="desk-v1-cp-evidence" data-cp-evidence="${esc(ev.status)}">Evidence: ${esc(ev.status)}, ${when}${links ? `. ${links}` : ''}</div>
      </details>`;
  }

  function _credPickers(r, purpose) {
    const roles = [...new Set(r.auth.map((a) => ROLE_FOR_AUTH[a.type]).filter(Boolean).concat(r.signin ? ['login'] : []))];
    const rows = [];
    if (r.transport === 'browser') {
      rows.push(`<label class="desk-v1-conn-add-field">Browser profile (a saved sign-in in the pane)
        <input type="text" class="desk-v1-rules-textinput" data-cp-prof="${esc(r.id)}" maxlength="41" value="${esc(P.prof[r.id] || '')}" placeholder="e.g. ${esc(P.view.service.id)}-main" autocapitalize="off" spellcheck="false"></label>`);
    }
    roles.forEach((role) => {
      const vault = P.view.vault.slice().sort((a, b) => Number(b.matches) - Number(a.matches));
      const cur = (P.creds[r.id] || {})[role] || '';
      const opts = ['<option value="">None: do not point at a stored entry</option>']
        .concat(vault.map((v) => `<option value="${esc(v.name)}"${cur === v.name ? ' selected' : ''}>${esc(v.name)}${v.entry_type ? ` (${esc(v.entry_type)})` : ''}</option>`));
      if (cur && !vault.some((v) => v.name === cur)) opts.push(`<option value="${esc(cur)}" selected>${esc(cur)} (not in the vault now)</option>`);
      rows.push(`<label class="desk-v1-conn-add-field">${esc(ROLE_LABEL[role])}
        <select class="desk-v1-rules-textinput" data-cp-cred="${esc(r.id)}" data-cp-role="${esc(role)}">${opts.join('')}</select></label>`);
      if (role === 'login' && r.signin && window.DeskV1ConnectSignin && !P.formShown[r.id]) {   // one typing form per route, even when two purposes use it
        P.formShown[r.id] = true;
        rows.push(window.DeskV1ConnectSignin.newLoginHTML(r.id));
      }
    });
    if (roles.length && !P.view.vault.length && !r.signin) rows.push('<div class="desk-v1-rules-hint">The vault has no entries yet. Create one in the Vault, then pick it here.</div>');
    return rows.length ? `<div class="desk-v1-cp-pickers" data-cp-pickers="${esc(r.id)}">${rows.join('')}</div>` : '';
  }

  function _routeHTML(p, r) {
    const st = r.status;
    const cov = r.coverage[P.kind] || {};
    const sel = P.sel[p.id] || {};
    const used = p.capabilities.some((c) => sel[c.id] === r.id);
    const word = st.executes ? 'Clayrune runs this' : st.bindable ? 'Saved only: not run yet' : 'Cannot be chosen here';
    const caps = p.capabilities.map((c) => {
      const got = cov[c.id];
      const ok = st.bindable && COVERED.indexOf(got) >= 0;
      const box = ok
        ? `<label class="desk-v1-cp-cap"><input type="checkbox" data-cp-cap="${esc(p.id)}:${esc(c.id)}:${esc(r.id)}"${sel[c.id] === r.id ? ' checked' : ''}> ${esc(c.label)}<span class="desk-v1-cp-covword"> ${esc(COVERAGE_WORD[got] || got)}</span></label>`
        : `<span class="desk-v1-cp-cap desk-v1-cp-cap-off" data-cp-cap-off="${esc(c.id)}">${esc(c.label)}<span class="desk-v1-cp-covword"> ${esc(got ? COVERAGE_WORD[got] || got : 'not stated')}</span></span>`;
      return box;
    });
    return `<div class="desk-v1-cp-route" data-cp-route="${esc(r.id)}" data-cp-purpose-of="${esc(p.id)}" data-cp-executes="${st.executes}" data-cp-bindable="${st.bindable}" data-cp-used="${used}">
        <div class="desk-v1-cp-route-head"><span class="desk-v1-cp-route-title">${esc(r.title)}</span>
          <span class="desk-v1-cf-badge" data-cp-word="${st.executes ? 'runs' : st.bindable ? 'saved' : 'no'}">${esc(word)}</span>${_costHTML(r)}</div>
        <div class="desk-v1-cp-guidance">${esc(r.guidance)}</div>
        ${st.why ? `<div class="desk-v1-cp-why" data-cp-why>${esc(st.why)}</div>` : ''}
        <div class="desk-v1-cp-caps">${caps.join('')}</div>
        ${used ? _credPickers(r, p.id) : ''}
        ${_detailsHTML(r)}
      </div>`;
  }

  function _stateHTML(a, p) {
    if (!a) return '';
    const mine = a.bound.filter((b) => b.purpose === p.id);
    if (!mine.length) return `<div class="desk-v1-cp-state desk-v1-rules-hint" data-cp-state="${esc(p.id)}">Nothing is chosen for this purpose yet.</div>`;
    const ver = a.verification[p.id];
    const rows = mine.map((b) => {
      const sr = _route(p.id, b.route_id);
      const refs = b.refs || {};
      const fill = sr && sr.signin && refs.login && window.DeskV1ConnectSignin
        ? window.DeskV1ConnectSignin.fillHTML(`${p.id}:${b.route_id}`, { url: sr.signin.url, login: refs.login, profile: refs.browser_profile || refs.oauth_profile }) : '';
      return `<li data-cp-bound="${esc(b.route_id)}"><strong>${esc(b.route_title)}</strong>: ${esc(b.capabilities.join(', '))}
        <span class="desk-v1-cf-badge" data-cp-setup="${esc(b.setup)}">${esc(SETUP_WORD[b.setup] || b.setup)}</span>${b.reason ? ` <span class="desk-v1-cp-why">${esc(b.reason)}</span>` : ''}${fill}</li>`;
    });
    const checked = P.checks[`${a.id}:${p.id}`];
    const msg = checked ? `<div class="desk-v1-cp-checkmsg" data-cp-checkmsg role="status">${checked.map((x) => esc(`${x.title}: ${x.message}`)).join(' ')}</div>` : '';
    return `<div class="desk-v1-cp-state" data-cp-state="${esc(p.id)}"><ul class="desk-v1-cp-list">${rows.join('')}</ul>
        <div class="desk-v1-cp-verify"><span class="desk-v1-cf-badge" data-cp-verify="${esc(ver.state)}">${esc(VERIFY_WORD[ver.state] || ver.state)}${ver.state === 'partial' ? `: ${ver.verified.length} of ${ver.bound.length}` : ''}</span>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cp-check="${esc(p.id)}" ${P.checking ? 'disabled' : ''}>${P.checking === p.id ? 'Checking…' : 'Check now'}</button></div>${msg}</div>`;
  }

  function _purposeHTML(p) {
    const a = _account();
    const routes = p.routes.filter((r) => !P.kind || Object.keys(r.coverage).length === 0 || r.coverage[P.kind]);
    return `<fieldset class="desk-v1-cp-purpose" data-cp-purpose="${esc(p.id)}">
        <legend>${esc(p.label)}</legend>
        ${p.standing_policy ? `<div class="desk-v1-rules-hint">${esc(p.standing_policy)}</div>` : ''}
        ${_stateHTML(a, p)}
        <div class="desk-v1-cp-routes">${routes.length ? routes.map((r) => _routeHTML(p, r)).join('') : '<div class="desk-v1-rules-hint">No route is documented for this kind of account.</div>'}</div>
      </fieldset>`;
  }

  function html(info) {
    if (!info || !info.service || P.service !== info.service.id) return '';
    if (!P.view || !eligible(P.view)) return '';          // an optional section: when it cannot load, the Method step is as it was
    const v = P.view;
    P.formShown = {};
    const problem = _problem();
    const lines = P.accountId && P.kind ? _summary() : [];
    return `<section class="desk-v1-cp" data-cp data-cp-service="${esc(v.service.id)}" aria-label="Routes by purpose">
        <div class="desk-v1-rules-group-title">Choose how each purpose is done</div>
        <div class="desk-v1-rules-hint">${esc(v.service.label)} can be used in more than one way. Pick the account, then the route for each purpose. Nothing is chosen for you, and nothing is saved until you press Save.</div>
        ${_accountHTML()}
        ${P.accountId && P.kind ? _purposesFor(P.kind).map(_purposeHTML).join('') : ''}
        ${lines.length ? `<div class="desk-v1-cp-summary" data-cp-summary><div class="desk-v1-cp-summary-title">Save will record</div><ul class="desk-v1-cp-list">${lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul>
          <div class="desk-v1-rules-hint">${lines.some((l) => l.indexOf("New stored login") >= 0) ? "It stores the new login in the Vault (username and password on one entry), and creates no sign-in or browser profile." : "It creates no sign-in, browser profile or vault entry, and changes none."} Saving asks for your dashboard passcode once.</div></div>` : ''}
        ${P.status ? `<div class="desk-v1-cf-msg" data-cp-status data-cf-msg="${P.statusKind}" role="${P.statusKind === 'ok' ? 'status' : 'alert'}">${esc(P.status)}</div>` : ''}
        <div class="desk-v1-cp-actions" data-cp-actions>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline desk-v1-cf-primary" data-cp-save ${problem || P.saving ? 'disabled' : ''} ${problem ? `title="${esc(problem)}"` : ''}>${P.saving ? 'Saving…' : 'Save routes'}</button>
        </div>
        ${problem && P.accountId ? `<div class="desk-v1-rules-hint" data-cp-problem>${esc(problem)}</div>` : ''}
      </section>`;
  }

  // ── loading ─────────────────────────────────────────────────────────────
  async function _load(id, ctx, keep) {
    P.loading = true;
    try {
      const view = await ctx.api('POST', '/api/desk/connect/purposes', { service: id });
      if (P.service !== id) return;
      P.view = view;
      const ids = view.accounts.map((a) => a.id);
      if (!keep) P.accountId = ids.length === 1 ? ids[0] : '';
      else if (P.accountId !== NEW && ids.indexOf(P.accountId) < 0) P.accountId = '';
      if (!keep) _seed();
    } catch (e) {
      if (P.service === id) P.view = null;
    }
    P.loading = false;
    ctx.repaint();
  }

  async function _syncAccounts(ctx) {
    if (!ctx.channels) return;
    try {
      const rows = await ctx.api('GET', '/api/desk/accounts');
      const have = ctx.channels();
      (rows || []).forEach((row) => { const mine = have.find((c) => c.id === row.id); if (mine) Object.assign(mine, row); else have.push(row); });
    } catch (e) { /* the tiles refresh the next time the screen opens */ }
  }

  async function _save(ctx) {
    if (P.saving || _problem()) return;
    if (!P.requestId) P.requestId = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() : `cp-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
    P.saving = true; P.status = ''; ctx.repaint();
    let result;
    const label = P.view.service.label;
    try {
      result = await window.humanProofFetch('/api/desk/connect/purpose/commit', {
        method: 'POST', body: JSON.stringify({ request_id: P.requestId, draft: _draft() }),
      }, { title: 'Save routes', description: `Re-enter your dashboard passcode to save how ${label} is connected.` });
    } catch (e) {
      result = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } };
    }
    P.saving = false;
    if (result === null) { ctx.repaint(); return; }            // cancelled at the passcode: nothing was sent, the draft stays
    if (!result.ok) {
      P.statusKind = 'error';
      P.status = (result.body && (result.body.error || result.body.message)) || `The save failed (HTTP ${result.status}).`;
      ctx.repaint();
      return;
    }
    const saved = result.body || {};
    if (window.DeskV1ConnectSignin) window.DeskV1ConnectSignin.reset();    // a typed login is stored now: no password stays in the page
    const split = saved.legacy_read && saved.legacy_read.split;
    await _syncAccounts(ctx);
    P.accountId = saved.account_id || P.accountId;
    P.checks = {};
    const id = P.service;
    P.statusKind = 'ok';
    P.status = `Saved. ${split ? 'Your own reads use two routes, so the single "Read via" setting was left as it was; each route is used for the capabilities you ticked. ' : ''}Nothing here has been checked yet.`;
    const msg = P.status;
    await _load(id, ctx, true);
    _seed();
    P.status = msg; P.statusKind = 'ok';
    ctx.repaint();
  }

  async function _check(purpose, ctx) {
    const a = _account();
    if (!a || P.checking) return;
    P.checking = purpose; ctx.repaint();
    try {
      const out = await ctx.api('POST', '/api/desk/connect/purpose/verify', { account_id: a.id, purpose });
      P.checks[`${a.id}:${purpose}`] = (out.routes || []).map((r) => {
        const t = a.bound.find((b) => b.route_id === r.route_id);
        const title = t ? t.route_title : r.route_id;
        const message = r.result === 'passed' ? `passed${r.proved && r.proved.length ? ` for ${r.proved.join(', ')}` : ''}${r.not_proved && r.not_proved.length ? `; not proved: ${r.not_proved.join(', ')}` : ''}.`
          : r.result === 'failed' ? `failed. ${r.message || ''}` : (r.message || 'no check is available.');
        return { title, message };
      });
    } catch (e) {
      P.checks[`${a.id}:${purpose}`] = [{ title: 'Check', message: `could not run: ${e && e.message ? e.message : 'unknown error'}.` }];
    }
    P.checking = '';
    const keepDraft = { sel: P.sel, prof: P.prof, creds: P.creds, dirty: P.dirty };
    await _load(P.service, ctx, true);
    Object.assign(P, keepDraft);
    ctx.repaint();
  }

  // ── wiring ──────────────────────────────────────────────────────────────
  function bind(root, info, ctx) {
    if (!info || !info.service) return;
    if (P.service !== info.service.id) { reset(); P.service = info.service.id; _load(P.service, ctx, false); return; }
    const el = root.querySelector('[data-cp]');
    if (!el) return;
    const again = () => ctx.repaint();
    const account = el.querySelector('[data-cp-account]');
    if (account) account.addEventListener('change', () => { P.accountId = account.value; P.status = ''; P.checks = {}; if (P.accountId === NEW) { P.sel = {}; P.prof = {}; P.creds = {}; P.kind = P.view.account_kinds.length === 1 ? P.view.account_kinds[0].id : ''; P.dirty = true; } else _seed(); again(); });
    const kind = el.querySelector('[data-cp-kind]');
    if (kind) kind.addEventListener('change', () => { P.kind = kind.value; P.sel = {}; _touch(); again(); });
    const ident = el.querySelector('[data-cp-new-identity]');
    const sync = () => { const s = el.querySelector('[data-cp-save]'); if (s) s.disabled = !!_problem() || P.saving; };
    if (ident) { ident.addEventListener('input', () => { P.newIdentity = ident.value; _touch(); sync(); }); ident.addEventListener('change', again); }
    const lab = el.querySelector('[data-cp-new-label]');
    if (lab) lab.addEventListener('input', () => { P.newLabel = lab.value; _touch(); });
    el.querySelectorAll('[data-cp-cap]').forEach((box) => box.addEventListener('change', () => {
      const [purpose, cap, route] = box.dataset.cpCap.split(':');
      (P.sel[purpose] = P.sel[purpose] || {});
      if (box.checked) P.sel[purpose][cap] = route; else delete P.sel[purpose][cap];   // one route per capability: ticking it here unticks it elsewhere
      _touch(); again();
    }));
    el.querySelectorAll('[data-cp-prof]').forEach((inp) => {
      inp.addEventListener('input', () => { P.prof[inp.dataset.cpProf] = inp.value; _touch(); sync(); });
      inp.addEventListener('change', again);      // the summary catches up when the field is left, never while it is typed in
    });
    el.querySelectorAll('[data-cp-cred]').forEach((sel) => sel.addEventListener('change', () => {
      (P.creds[sel.dataset.cpCred] = P.creds[sel.dataset.cpCred] || {})[sel.dataset.cpRole] = sel.value; _touch(); again();
    }));
    const SI = window.DeskV1ConnectSignin;
    if (SI) {
      el.querySelectorAll('[data-cs-new]').forEach((box) => {
        const key = box.dataset.csNew;
        SI.mount(box, key, `${P.view.service.id}.login`, again);
        box.addEventListener('input', () => { _touch(); sync(); });
        box.addEventListener('change', again);
      });
      el.querySelectorAll('[data-cs-fill]').forEach((box) => {
        const [purpose, rid] = box.dataset.csFill.split(':');
        const a = _account(), b = a && a.bound.find((x) => x.purpose === purpose && x.route_id === rid), route = _route(purpose, rid);
        if (!b || !route || !route.signin) return;
        const refs = b.refs || {};
        SI.bindFill(box, box.dataset.csFill, { url: route.signin.url, login: refs.login, profile: refs.browser_profile || refs.oauth_profile },
          ctx, () => ({ service: P.view.service.id, route_id: rid, account_id: a.id }));
      });
    }
    el.querySelectorAll('[data-cp-check]').forEach((b) => b.addEventListener('click', () => _check(b.dataset.cpCheck, ctx)));
    const save = el.querySelector('[data-cp-save]');
    if (save) save.addEventListener('click', () => _save(ctx));
  }

  window.DeskV1ConnectPurpose = { html, bind, reset, eligible: () => !!P.view && eligible(P.view) };
})();
