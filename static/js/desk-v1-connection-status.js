// Saved connection readiness, evidence and editor handoff. Public words are shared; route identifiers stay in collapsed Details.
import { ConnectionWords as W } from './desk-v1-connection-words.js';
import { accountHTML as socialHTML, bindAccount as bindSocial, invalidatePermissions } from './desk-v1-connection-account.js';
import { ConnectionRemoval as Remove } from './desk-v1-connection-remove.js';
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
    const off = window.DeskV1ReadsOff?.facet(c, f);                  // reading not allowed yet: its own state, ahead of a sign-in check and never "Needs sign-in"
    if (off) return off;
    const checked = window.DeskV1BrowserAccount?.facet(ch, f);
    if (checked) return checked;
    if (c && c.state === 'not_connected') return Object.assign(f, { key: 'reauth', word: W.status.signin, detail: c.reason || c.message || 'The last read hit the sign-in page.' });
    if (c && c.state === 'ok') return Object.assign(f, { key: 'ok', word: W.status.ready, detail: c.last_ok_at ? `Last read ${String(c.last_ok_at).replace('T', ' ').slice(0, 16)} UTC.` : '' });
    if (c && c.state === 'not_read_yet') return Object.assign(f, { key: 'saved', word: W.status.unchecked, detail: c.reason || 'Nothing has been read through it yet.' });
    return Object.assign(f, { key: 'saved', word: W.status.unchecked, detail: f.via === 'api' ? 'This account is read through the X API. The profile is kept for signing in.' : 'Clayrune has not checked that it is signed in.' });
  }

  function apiFacet(ch) {
    if (ch.platform !== 'x' || ch.capability !== 'direct' || ch.preview || !ch.publish) return null;
    const p = ch.publish, cred = ch.credentials || {};
    if (!p.ready && !p.secret && !cred.oauth_profile && !cred.oauth_vault) return null;
    const f = { id: 'api', route: 'x-api', profile: String(cred.oauth_profile || '') };
    if (p.ready) return Object.assign(f, { key: 'ok', word: W.status.ready, detail: p.unattended_ok === false ? 'Scheduled posts will be held: the saved sign-in is not allowed for unattended use.' : '' });
    if (p.vault_locked) return Object.assign(f, { key: 'reauth', word: W.status.broken, locked:true, detail: 'Your sign-in is still saved. Unlock the vault to use it.' });
    return Object.assign(f, { key: 'reauth', word: W.status.signin, detail: p.reason && !/vault/i.test(p.reason) ? p.reason : '' });
  }

  function facets(ch, cov) { return { browser: browserFacet(ch, cov), api: apiFacet(ch) }; }

  // Readiness and permission are separate. A read grant being off is not a
  // failed sign-in; the saved browser check remains evidence of readiness.
  function accountStatus(ch, cov) {
    if (ch.preview) return W.state('unchecked');
    const {browser,api}=facets(ch,cov);
    const list=[browser,api].filter(Boolean);
    if (list.some(f=>f.locked)) return W.state('broken');
    const checked=ch.browser_setup?.status?.state;
    if (checked==='sign_in_required') return W.state('signin');
    if (checked==='check_failed') return W.state('broken');
    if (list.some(f=>f.key==='reauth' && !f.locked)) return W.state('signin');
    if (checked==='verified' || list.some(f=>f.key==='ok') || ch.publish?.ready) return W.state('ready');
    if (/review pending/i.test(ch.publish?.reason || '')) return W.state('waiting', W.serviceName(ch.platform));
    return W.state('unchecked');
  }

  function _routeRow(f, label, lines, ch) {
    return `<div class="desk-v1-cs-row" data-cs-facet="${esc(f.id)}" data-state="${esc(f.key)}">
        <span class="desk-v1-how-field-label">${esc(label)}</span>
        ${window.DeskV1StatusSignin?.routeWord(ch, f) || `<span class="desk-v1-cs-word" data-cs-word>${esc(f.word)}</span>`}
        ${lines.filter(Boolean).map((l) => `<div class="desk-v1-cs-line">${l}</div>`).join('')}
        ${f.detail ? `<div class="desk-v1-rules-hint" data-cs-detail>${esc(f.detail)}${window.VaultUnlockUI ? window.VaultUnlockUI.buttonHTML(f.locked ? {vault_locked:true} : f.detail) : ''}</div>` : ''}

      </div>`;
  }

  // The routes block for one live account. Browser and API are separate rows so an account holding both shows both.
  function accountHTML(ch, cov, status) {
    const { browser, api } = facets(ch, cov);
    const rows=[];
    if(browser) rows.push(_routeRow(browser, W.labels.browser, [
      `Profile <code>${esc(browser.profile)}</code>${browser.login ? `, login <code>${esc(browser.login)}</code>` : ''}`,
      browser.route ? `Route <code>${esc(browser.route)}</code>` : ''], ch));
    if(api) rows.push(_routeRow(api,W.labels.api,[`Profile <code>${esc(api.profile)}</code>`, `Route <code>${esc(api.route)}</code>`],ch));
    const method=ch.read_via==='api'?'X API (paid, ~$0.005 per read)':'Browser pane (no charge)';
    rows.push(`<p data-cs-readvia>${esc(method)}</p>`);
    if(ch.publish?.reason) rows.push(`<p data-conn-publish>${esc(ch.publish.reason)}</p>`);
    const assisted=window.DeskV1AssistedPost?.accountHTML(ch);
    if(assisted) rows.push(assisted);
    return socialHTML(ch,cov,{details:rows.join(''),status});
  }

  // Retained for callers that need to know whether the shared wizard is enabled.
  function replacesReadVia() { const W = window.DeskV1ConnectWizard; return !!(W && typeof W.enabled === 'function' && W.enabled()); }

  // ── coverage: the free per-platform read state, no network ──────────────
  const _cov = {};            // project id -> [coverage rows]; set once per id until invalidate()
  const _covAsked = new Set();
  function coverage(pid) { return pid && _cov[pid] ? _cov[pid] : null; }
  // One row per read target (an account): with `accountId`, that account's row; a row with no account (a platform the project only published to) is the fallback.
  function coverageRow(pid, platform, accountId) {
    const rows = coverage(pid); if (!rows) return null;
    const on = rows.filter((x) => x.platform === platform);
    return (accountId && on.find((x) => x.account_id === accountId)) || on.find((x) => !x.account_id) || (accountId ? null : on[0]) || null;
  }
  function loadCoverage(pids, repaint) {
    const want = Array.from(new Set((pids || []).filter(Boolean))).filter((p) => !_covAsked.has(p));
    want.forEach((pid) => {
      _covAsked.add(pid);
      fetch(`/api/desk/engagement/coverage/${encodeURIComponent(pid)}`).then((r) => r.json()).then((d) => { _cov[pid] = (d && d.coverage) || []; })
        .catch(() => { _cov[pid] = []; }).then(() => { if (repaint) repaint(); });
    });
  }

  // ── MCP records ─────────────────────────────────────────────────────────
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
    const k=_check[mcpKey(r)] || {}, o=k.out || _observation(r);
    if(k.error) return W.state('broken');
    if(o?.review_needed) return W.state('broken');
    if(r.state==='registered' && r.check) return W.from(r.check.status);
    if(r.state==='registered' && o && ['unchanged','adopted','baseline_recorded'].includes(o.status)) return W.state('ready');
    return W.from(r.state);
  }

  function mcpItems() {
    return (_mcp || []).map((r) => ({ key: mcpKey(r), kind: 'mcp', name: r.server_name,
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
    const key = mcpKey(r), st = mcpStatus(r), remote = r.ecosystem === 'remote', repo = r.ecosystem === 'github';
    const files = r.package_files || {};
    const drift = r.code === 'package_files_changed' ? _drift(files) : '';
    const note = files.status === 'not_recorded' ? '<div class="desk-v1-rules-hint" data-cs-files-note="not_recorded">The package files were not recorded when this was approved, so they are not checked. Approving it again records them.</div>' : '';
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
        <p data-connection-next>${esc(st.kind==='unchecked' && !remote && !repo?W.reasons.noCheck:W.next[st.kind])}</p>
        <details data-connection-details><summary>${W.labels.details}</summary><p data-cs-message>${esc(r.message || '')}</p>${missing}${drift}${note}
        <dl class="desk-v1-cf-facts" data-cs-facts>${facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd>${v}</dd></div>`).join('')}</dl>
        ${remote ? _observedHTML(r, key) : ''}
        ${repo && (_check[key] || {}).busy ? '<div class="desk-v1-rules-hint" data-cs-check="busy" role="status">Starting the server to check it…</div>' : ''}
        ${repo && (_check[key] || {}).error ? `<div class="desk-v1-cf-msg" data-cf-msg="error" data-cs-check="error" role="alert">${esc(_check[key].error)}</div>` : ''}
        </details>
        <div class="desk-v1-cf-actions desk-v1-cf-actions-flat">
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-check-run ${!(remote || repo) || r.state!=='registered' || (_check[key] || {}).busy ? 'disabled' : ''}>${W.buttons.check}</button>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-cs-reopen="${esc(key)}">${W.buttons.edit}</button>
          ${Remove.html(r.server_name)}
        </div>
        <div class="desk-v1-rules-hint" data-cs-refresh-note>${W.buttons.check} ${remote ? 'contacts only the approved address, and only when you press it.' : repo ? 'starts the approved server once, asks for its tools, then stops it.' : 'is unavailable for this connection.'}</div>
      </div>`;
  }

  function _target(r) { const t = { server_name: r.server_name, scope: r.scope }; if (r.scope === 'project') t.project_id = r.project_id; return t; }

  async function _runCheck(r, repaint) {
    const key = mcpKey(r);
    if ((_check[key] || {}).busy) return;
    _check[key] = { busy: true }; repaint();
    const path = r.ecosystem === 'github' ? '/api/desk/connect/custom/github/check' : '/api/desk/connect/custom/remote/check';
    try { _check[key] = { out: await _api('POST', path, _target(r)) }; }
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
    bindSocial(row,ch,repaint);
    window.DeskV1BrowserAccount?.bind(row,ch,repaint);
    row.querySelectorAll('[data-cs-facet]').forEach((r) => window.DeskV1AccountSignin?.bind(r, ch, repaint));
    row.querySelectorAll('[data-cs-facet]').forEach((r) => window.DeskV1ReadsOff?.bind(r, ch, repaint));
    window.DeskV1StatusSignin?.bind(row, ch, repaint);
    const b = row.querySelector('[data-cs-reopen]');
    if (b) b.addEventListener('click', () => _openEditor({ kind: 'account', id: ch.id, record: ch }, repaint));
  }

  function bindMcp(root, r, repaint) {
    const el = root.querySelector(`[data-conn-mcp="${CSS.escape(mcpKey(r))}"]`);
    if (!el) return;
    Remove.bind(el, {name:r.server_name, key:mcpKey(r), url:'/api/desk/connect/custom/connections', body:_target(r), passcode:true,
      onRemoved:async () => { _mcp = (_mcp || []).filter(row => mcpKey(row) !== mcpKey(r)); await loadMcp(true); window.DeskV1ConnTiles.select(null); repaint(); }});
    const on = (sel, fn) => { const n = el.querySelector(sel); if (n) n.addEventListener('click', fn); };
    on('[data-cs-reopen]', () => _openEditor({ kind: 'mcp', id: mcpKey(r), record: r }, repaint));
    on('[data-cs-check-run]', () => _runCheck(r, repaint));
    on('[data-cs-adopt]', () => _adopt(r, repaint));
    on('[data-cs-refresh]', () => { loadMcp(true).then(repaint); });
  }

  // A fresh read of everything this module keeps: the next paint asks again.
  function invalidate() { invalidatePermissions(); _covAsked.clear(); Object.keys(_cov).forEach((k) => { delete _cov[k]; }); }
  function refresh() { invalidate(); return loadMcp(true); }

  window.DeskV1ConnectionStatus = {
    facets, accountStatus, accountHTML, bindAccount, replacesReadVia,
    coverage, coverageRow, loadCoverage, invalidate, refresh,
    mcpRows, mcpKey, mcpByKey, mcpStatus, mcpItems, mcpDetailHTML, bindMcp, loadMcp, mcpError: () => _mcpError,
    setReopen, reopen: _openEditor,
  };
})();
