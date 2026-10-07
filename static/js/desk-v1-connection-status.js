// Desk v1 — Connections: the ACTUAL status of each connection (MC-1062 ticket 13b,
// docs/desk_v1/connect_flow_tickets/13b-management-status.md). Window-bridged module, no `import`
// (ground rule 1). desk-v1-connections.js and its tile grid only delegate here.
//
// WHAT IT OWNS
//   Accounts   one tile status and one detail block built from the account's ROUTES, not from publishing:
//                Browser  a saved profile (the saved sign-in, or the legacy read profile). Its word comes from
//                         the free coverage read when it has one: "Reading by browser", "Needs sign-in", or,
//                         with no evidence either way, "Browser profile saved". Clayrune cannot see inside a
//                         profile, so none of these says the profile is signed in.
//                API      X's own sign-in (`publish.ready` is its derived state). A signed-in API is "API signed in".
//                Publishing stays its own line (desk-v1-connections.js `_publishHTML`) and no longer decides
//                the tile: a browser connection that works is not "Not connected" because LinkedIn's publishing
//                gate is closed. An account with both keeps both rows, each with its own profile/login names.
//   MCP        every approved npm and remote server (POST /api/desk/connect/custom/connections: local reads
//              only), one tile each. The server's own state word is kept apart: Registered / Saved, cannot run
//              yet / Saved, setup failed / Changed since approved / Package missing / Credential missing.
//              Drift (changed package files) and an observed remote change stay visible; the recovery actions
//              are the existing ones: review again (the setup editor), accept an observed change (its own
//              passcode), and a check the person starts.
//
// WHAT IT NEVER DOES
//   Open a browser, spend money, write, or contact a server on its own. Refreshing reads saved state and the
//   no-network coverage route. "Check the connection" on a remote server contacts ONLY its approved address, only
//   when the person presses it, and is the existing route. Nothing here is Verified: a registry flag, a
//   registered record or an answering server is never promoted to it (that word belongs to
//   /api/desk/connect/verify, which this module never calls).
//
// THE EDITOR. Setup and permissions are edited in the Connect wizard (tickets 02 to 14). While it is disabled the
// Read via controls in desk-v1-connections.js stay; once it is enabled they give way to a summary and a Change
// button, so there is one editor, not two. `setReopen(fn)` is how ticket 14 points Change at the wizard; until then
// it opens the Add service panel.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
  function _api(method, url, body) { return window.DeskV1Store.api(method, url, body); }

  // ── accounts ────────────────────────────────────────────────────────────
  function _setup(ch) { const s = ch && ch.browser_setup; return s && typeof s === 'object' && s.refs && typeof s.refs === 'object' ? s : null; }

  // `cov` is the platform's row from GET /api/desk/engagement/coverage/<project>, or null (not loaded / none).
  function browserFacet(ch, cov) {
    const setup = _setup(ch), refs = setup ? setup.refs : {};
    const profile = String(refs.browser_profile || ch.browser_profile || '');
    if (!profile) return null;
    const f = { id: 'browser', profile, login: String(refs.login || ''), route: setup ? String(setup.route_id || '') : '', via: ch.read_via === 'api' ? 'api' : 'pane' };
    const c = cov && cov.via === 'pane' && f.via === 'pane' ? cov : null;      // the coverage row describes the route that reads the account
    if (c && c.state === 'not_connected') return Object.assign(f, { key: 'reauth', word: 'Needs sign-in', detail: c.reason || c.message || 'The last read hit the sign-in page.' });
    if (c && c.state === 'ok') return Object.assign(f, { key: 'ok', word: 'Reading by browser', detail: c.last_ok_at ? `Last read ${String(c.last_ok_at).replace('T', ' ').slice(0, 16)} UTC.` : '' });
    if (c && c.state === 'not_read_yet') return Object.assign(f, { key: 'saved', word: 'Browser set up, not read yet', detail: c.reason || 'Nothing has been read through it yet.' });
    return Object.assign(f, { key: 'saved', word: 'Browser profile saved', detail: f.via === 'api' ? 'This account is read through the X API. The profile is kept for signing in.' : 'Clayrune has not checked that it is signed in.' });
  }

  function apiFacet(ch) {
    if (ch.platform !== 'x' || ch.capability !== 'direct' || ch.preview || !ch.publish) return null;
    const p = ch.publish, cred = ch.credentials || {};
    const f = { id: 'api', route: 'x-api', profile: String(cred.oauth_profile || '') };
    if (p.ready) return Object.assign(f, { key: 'ok', word: 'API signed in', detail: p.unattended_ok === false ? 'Scheduled posts will be held: the saved sign-in is not allowed for unattended use.' : '' });
    if (p.vault_locked) return Object.assign(f, { key: 'reauth', word: 'Vault locked', detail: 'Your sign-in is still saved. Unlock the vault to use it.' });
    return Object.assign(f, { key: 'off', word: 'API not signed in', detail: p.reason && !/vault/i.test(p.reason) ? p.reason : '' });
  }

  function facets(ch, cov) { return { browser: browserFacet(ch, cov), api: apiFacet(ch) }; }

  // The tile's word. {key, word, action}. Publishing readiness is NOT an input except through the API route.
  function accountStatus(ch, cov) {
    if (ch.preview) return { key: 'preview', word: 'Preview · not connected', action: null };
    const { browser, api } = facets(ch, cov);
    const list = [browser, api].filter(Boolean);
    const need = list.find((f) => f.key === 'reauth');
    if (need) return { key: 'reauth', word: need.word, action: null };
    if (ch.capability === 'none') return { key: 'ok', word: 'Read only', action: null };       // a read-only site: the browser is its only route
    if (ch.publish && ch.publish.ready) return { key: 'ok', word: 'Connected', action: null };        // an API sign-in, or a site published by hand
    if (browser) return { key: browser.key, word: browser.word, action: null };
    return { key: 'off', word: 'Not connected', action: null };
  }

  function _routeRow(f, label, lines) {
    return `<div class="desk-v1-cs-row" data-cs-facet="${esc(f.id)}" data-state="${esc(f.key)}">
        <span class="desk-v1-how-field-label">${esc(label)}</span>
        <span class="desk-v1-cs-word" data-cs-word>${esc(f.word)}</span>
        ${lines.filter(Boolean).map((l) => `<div class="desk-v1-cs-line">${l}</div>`).join('')}
        ${f.detail ? `<div class="desk-v1-rules-hint" data-cs-detail>${esc(f.detail)}${window.VaultUnlockUI ? window.VaultUnlockUI.buttonHTML(f.word) : ''}</div>` : ''}
      </div>`;
  }

  // The routes block for one live account. Browser and API are separate rows so an account holding both shows both.
  function accountHTML(ch, cov) {
    const { browser, api } = facets(ch, cov);
    const rows = [];
    if (browser) rows.push(_routeRow(browser, 'Browser', [
      `Profile <code>${esc(browser.profile)}</code>${browser.login ? `, login <code>${esc(browser.login)}</code>` : ''}`,
      browser.route ? `Route <code>${esc(browser.route)}</code>` : '']));
    if (api) rows.push(_routeRow(api, 'API', [api.profile ? `Sign-in profile <code>${esc(api.profile)}</code>` : '', `Route <code>${esc(api.route)}</code>`]));
    if (!rows.length) rows.push('<div class="desk-v1-rules-hint" data-cs-none>No browser or API route is set up for this account.</div>');
    const via = replacesReadVia() && ch.platform && ch.capability !== 'manual'
      ? `<div class="desk-v1-cs-line" data-cs-readvia><span class="desk-v1-how-field-label">Read via</span> ${ch.read_via === 'api' ? 'X API (paid, ~$0.005 per read)' : 'Browser pane (no charge)'}</div>` : '';
    const change = replacesReadVia() ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-reopen="${esc(ch.id)}">Change how this is connected</button>` : '';
    return `<div class="desk-v1-cs" data-cs-account="${esc(ch.id)}" aria-label="How this account is connected">${rows.join('')}${via}${change}</div>`;
  }

  // While the wizard is off its Read via controls are the only editor, so they stay. Once it is on it is the editor.
  function replacesReadVia() { const W = window.DeskV1ConnectWizard; return !!(W && typeof W.enabled === 'function' && W.enabled()); }

  // ── coverage: the free per-platform read state, no network ──────────────
  const _cov = {};            // project id -> [coverage rows]; set once per id until invalidate()
  const _covAsked = new Set();
  function coverage(pid) { return pid && _cov[pid] ? _cov[pid] : null; }
  function coverageRow(pid, platform) { const rows = coverage(pid); return rows ? rows.find((x) => x.platform === platform) || null : null; }
  function loadCoverage(pids, repaint) {
    const want = Array.from(new Set((pids || []).filter(Boolean))).filter((p) => !_covAsked.has(p));
    want.forEach((pid) => {
      _covAsked.add(pid);
      fetch(`/api/desk/engagement/coverage/${encodeURIComponent(pid)}`).then((r) => r.json()).then((d) => { _cov[pid] = (d && d.coverage) || []; })
        .catch(() => { _cov[pid] = []; }).then(() => { if (repaint) repaint(); });
    });
  }

  // ── MCP records ─────────────────────────────────────────────────────────
  const MCP = {
    registered: ['ok', 'Registered'], pending_runtime: ['saved', 'Saved, cannot run yet'], setup_failed: ['reauth', 'Saved, setup failed'],
    changed: ['reauth', 'Changed since approved'], package_missing: ['reauth', 'Package missing'], credential_missing: ['reauth', 'Credential missing'],
    missing: ['off', 'Not configured'], unknown: ['off', 'Cannot be read'],
  };
  const OBSERVED = { never_checked: 'Not checked yet.', baseline_recorded: 'Reached once. That first look is the baseline.', unchanged: 'Reached. It matches the last look.', adopted: 'Accepted as the new baseline.', changed: 'Changed since the last look.' };

  let _mcp = null, _mcpError = '', _mcpLoading = null;
  const _check = {};          // key -> { busy, out, error, adopting }

  function mcpRows() { return _mcp; }
  function mcpKey(r) { return `mcp:${r.scope}:${r.project_id || ''}:${r.server_name}`; }
  function mcpByKey(key) { return (_mcp || []).find((r) => mcpKey(r) === key) || null; }
  function _observation(r) { return r.ecosystem === 'remote' && r.observation && typeof r.observation === 'object' ? r.observation : null; }

  // Local reads only: the saved approvals, the config on disk, the package files, the Secrets entry NAMES.
  function loadMcp(force) {
    if (!window.DeskV1Store.live()) return Promise.resolve([]);
    if (_mcp && !force) return Promise.resolve(_mcp);
    if (_mcpLoading) return _mcpLoading;
    _mcpLoading = _api('POST', '/api/desk/connect/custom/connections', {}).then((o) => { _mcp = (o && o.connections) || []; _mcpError = ''; })
      .catch((e) => { _mcp = _mcp || []; _mcpError = e && e.message ? e.message : String(e); })
      .then(() => { _mcpLoading = null; return _mcp; });
    return _mcpLoading;
  }

  function mcpStatus(r) {
    const o = _observation(r);
    if (r.state === 'registered' && o && o.review_needed) return { key: 'reauth', word: 'Server changed' };
    const w = MCP[r.state] || ['off', r.state ? String(r.state).replace(/_/g, ' ') : 'Unknown'];
    return { key: w[0], word: w[1] };
  }

  function mcpItems() {
    return (_mcp || []).map((r) => ({ key: mcpKey(r), kind: 'mcp', mark: 'M', name: r.server_name,
      kindLabel: r.ecosystem === 'remote' ? 'Remote MCP server' : 'MCP server', status: mcpStatus(r) }));
  }

  function _projectName(id) {
    try { const p = (window.DeskV1Store.state().projects || []).find((x) => x.id === id); return p ? (p.name || p.id) : id; } catch (_) { return id; }
  }

  function _drift(f) {
    const rows = [].concat((f.changed || []).map((p) => ['changed', p]), (f.added || []).map((p) => ['added', p]), (f.removed || []).map((p) => ['removed', p]));
    return `<ul class="desk-v1-cu-changes" data-cs-drift>${rows.map(([k, p]) => `<li data-cs-drift-path="${esc(k)}">${esc(k)}: <code>${esc(p)}</code></li>`).join('')}</ul>${f.more ? `<div class="desk-v1-rules-hint">and ${esc(f.more)} more</div>` : ''}`;
  }

  function _creds(r) {
    const list = Array.isArray(r.credentials) ? r.credentials : [];
    if (!list.length) return 'None';
    return list.map((c) => `<div data-cs-credential>Vault entry <code>${esc(c.vault)}</code>${c.env ? ` as <code>${esc(c.env)}</code>` : ''}${c.header ? ` in the <code>${esc(c.header)}</code> header` : ''}</div>`).join('');
  }

  function _observedHTML(r, key) {
    const k = _check[key] || {};
    if (k.busy) return '<div class="desk-v1-rules-hint" data-cs-check="busy" role="status">Contacting the server…</div>';
    const o = (k.out && k.out.status) ? k.out : _observation(r);
    const err = k.error ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cs-check="error" role="alert">${esc(k.error)}</div>` : '';
    if (!o) return err;
    const diff = (o.diff || []).length ? `<ul class="desk-v1-cu-changes" data-cs-diff>${o.diff.map((d) => `<li data-cs-diff-item="${esc(d.what)}">${esc(d.what)}: ${esc(d.detail)}</li>`).join('')}</ul>` : '';
    const when = o.checked_at ? ` Last look ${esc(String(o.checked_at).replace('T', ' ').slice(0, 16))} UTC.` : '';
    const tools = typeof o.tool_count === 'number' ? `<div class="desk-v1-cs-line" data-cs-tools>Tools offered: ${esc(o.tool_count)}${o.truncated ? ' or more' : ''}</div>` : '';
    const accept = o.status === 'changed' && o.observed
      ? `<div class="desk-v1-cf-actions desk-v1-cf-actions-flat"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-adopt ${k.adopting ? 'disabled' : ''}>${k.adopting ? 'Accepting…' : 'Accept these changes (asks for your passcode)'}</button></div>` : '';
    return `${err}<div class="desk-v1-cs-obs" data-cs-observed="${esc(o.status)}" role="status"><strong>${esc(OBSERVED[o.status] || o.status)}</strong>${when}
        ${o.status === 'changed' ? ' Agents have not been told to trust what moved until you accept it.' : ''}</div>${tools}${diff}
        <div class="desk-v1-rules-hint">${esc(k.out && k.out.meaning ? k.out.meaning : 'A look shows that the server answers and what tools it offers. It does not show that it does what you want it for.')}</div>${accept}`;
  }

  function mcpDetailHTML(r) {
    const key = mcpKey(r), st = mcpStatus(r), remote = r.ecosystem === 'remote';
    const files = r.package_files || {};
    const drift = r.code === 'package_files_changed' ? _drift(files) : '';
    const note = files.status === 'not_recorded' ? '<div class="desk-v1-rules-hint" data-cs-files-note="not_recorded">The package files were not recorded when this was approved, so they are not checked. Approving it again records them.</div>' : '';
    const recover = ['changed', 'package_missing', 'setup_failed', 'missing'].indexOf(r.state) >= 0;
    const missing = r.state === 'credential_missing' && Array.isArray(r.missing) && r.missing.length ? `<div data-cs-missing>Missing from the Vault: ${r.missing.map((n) => `<code>${esc(n)}</code>`).join(', ')}.</div>` : '';
    const facts = [
      ['Kind', remote ? `Remote server, ${esc(r.protocol || '')}` : 'Package that runs on this computer'],
      [remote ? 'Address' : 'Package', `<code class="desk-v1-cf-wrap">${esc(r.package || '')}</code>${r.version ? ` ${esc(r.version)}` : ''}`],
      ['Who can use it', r.scope === 'global' ? 'All projects' : `One project: ${esc(_projectName(r.project_id))}`],
      ['Credentials', _creds(r)],
      ['Approved', esc(String(r.approved_at || '').replace('T', ' ').slice(0, 16) || 'not recorded')],
    ];
    return `
      <div class="desk-v1-conn-row" data-conn-mcp="${esc(key)}" data-conn-state="${esc(st.key)}">
        <div class="desk-v1-conn-head">
          <span class="desk-v1-channel-badge">${esc(r.server_name)}</span>
          <span class="desk-v1-conn-status" data-conn-status data-state="${esc(st.key)}">${esc(st.word)}</span>
        </div>
        <div class="desk-v1-cf-msg" data-cf-msg="${st.key === 'ok' ? 'ok' : 'warn'}" data-cs-message role="status">${esc(r.message || '')}</div>
        ${missing}${drift}${note}
        <dl class="desk-v1-cf-facts" data-cs-facts>${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>
        ${remote ? _observedHTML(r, key) : ''}
        <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
          ${recover ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-reopen="${esc(key)}">Review again</button>` : ''}
          ${remote && r.state === 'registered' ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-check-run ${(_check[key] || {}).busy ? 'disabled' : ''}>Check the connection</button>` : ''}
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-refresh>Refresh status</button>
        </div>
        <div class="desk-v1-rules-hint" data-cs-refresh-note>Refresh status reads what is saved here. It starts nothing and contacts no server.${remote ? ' Check the connection contacts only the approved address, and only when you press it.' : ''}</div>
      </div>`;
  }

  function _target(r) { const t = { server_name: r.server_name, scope: r.scope }; if (r.scope === 'project') t.project_id = r.project_id; return t; }

  async function _runCheck(r, repaint) {
    const key = mcpKey(r);
    if ((_check[key] || {}).busy) return;
    _check[key] = { busy: true }; repaint();
    try { _check[key] = { out: await _api('POST', '/api/desk/connect/custom/remote/check', _target(r)) }; }
    catch (e) { _check[key] = { error: (e && e.message) || 'The check failed.' }; }
    await loadMcp(true);
    repaint();
  }

  async function _adopt(r, repaint) {
    const key = mcpKey(r), k = _check[key] || {};
    const o = (k.out && k.out.status) ? k.out : _observation(r);
    if (!o || !o.observed || k.adopting) return;
    _check[key] = Object.assign({}, k, { adopting: true }); repaint();
    let res;
    try {
      res = await window.humanProofFetch('/api/desk/connect/custom/remote/adopt', { method: 'POST', body: JSON.stringify({ ..._target(r), observed: o.observed }) },
        { title: 'Accept remote MCP changes', description: 'Re-enter your dashboard passcode to accept what this remote MCP server now offers. Agents will use its new tools.' });
    } catch (e) { res = { ok: false, status: 0, body: { error: e && e.message ? e.message : 'could not reach the server' } }; }
    if (res === null) { _check[key] = Object.assign({}, k, { adopting: false }); repaint(); return; }      // cancelled at the passcode: nothing was sent
    if (!res.ok) { _check[key] = { error: (res.body && (res.body.error || res.body.message)) || `Accepting failed (HTTP ${res.status}).` }; repaint(); return; }
    _check[key] = {};
    await loadMcp(true);
    repaint();
  }

  // ── the editor hand-off ─────────────────────────────────────────────────
  let _reopen = null;
  function setReopen(fn) { _reopen = typeof fn === 'function' ? fn : null; }
  function _openEditor(target, repaint) {
    if (_reopen) { _reopen(target, repaint); return; }
    const T = window.DeskV1ConnTiles, A = window.DeskV1AddService;       // until the wizard is wired in: the Add service panel
    if (T) T.select('add');
    if (A && A.reset) A.reset();
    repaint();
  }

  function bindAccount(row, ch, repaint) {
    const b = row.querySelector('[data-cs-reopen]');
    if (b) b.addEventListener('click', () => _openEditor({ kind: 'account', id: ch.id, record: ch }, repaint));
  }

  function bindMcp(root, r, repaint) {
    const el = root.querySelector(`[data-conn-mcp="${CSS.escape(mcpKey(r))}"]`);
    if (!el) return;
    const on = (sel, fn) => { const n = el.querySelector(sel); if (n) n.addEventListener('click', fn); };
    on('[data-cs-reopen]', () => _openEditor({ kind: 'mcp', id: mcpKey(r), record: r }, repaint));
    on('[data-cs-check-run]', () => _runCheck(r, repaint));
    on('[data-cs-adopt]', () => _adopt(r, repaint));
    on('[data-cs-refresh]', () => { loadMcp(true).then(repaint); });
  }

  // A fresh read of everything this module keeps: the next paint asks again.
  function invalidate() { _covAsked.clear(); Object.keys(_cov).forEach((k) => { delete _cov[k]; }); }
  function refresh() { invalidate(); return loadMcp(true); }

  window.DeskV1ConnectionStatus = {
    facets, accountStatus, accountHTML, bindAccount, replacesReadVia,
    coverage, coverageRow, loadCoverage, invalidate, refresh,
    mcpRows, mcpKey, mcpByKey, mcpStatus, mcpItems, mcpDetailHTML, bindMcp, loadMcp, mcpError: () => _mcpError,
    setReopen,
  };
})();
