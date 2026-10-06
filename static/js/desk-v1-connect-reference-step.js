// MC-1062/12: reference-only Setup, Permissions, Review and Result. Registered by
// unknown-step's entry script. Secrets remain in the wizard-owned DOM until final
// humanProofFetch; only labelled, bounded parameters reach the 12b metadata store.
export function registerReferenceStep(W) {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const matches = (sel) => sel.type === 'reference';
  const uid = () => window.crypto?.randomUUID ? window.crypto.randomUUID() : 'ref-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 12);
  const fresh = () => ({ view: 'info', url: '', name: '', kind: 'api_base', source: '', address: '', auth_type: 'unknown', transport: 'http',
    credential_names: '', placements: '', scopes: '', labels: {}, mode: 'none', entry: 'api_key', vaultName: '', vault: null, vaultBusy: false, vaultError: '',
    unattended: true, detBusy: false, det: null, detError: '', rid: '', saving: false, result: null });
  let P = fresh(), gen = 0, vaultGen = 0, secretNode = null, specNode = null;
  const field = (v, provenance = 'user_input', confidence = 'stated') => ({ value: v, provenance, confidence });
  const input = (key, label, max = 300) => `<label class="desk-v1-conn-add-field">${esc(label)}<input class="desk-v1-rules-textinput" data-ref-field="${key}" value="${esc(P[key])}" maxlength="${max}" autocapitalize="off" spellcheck="false"></label>`;
  const button = (attr, text) => `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" ${attr}>${esc(text)}</button>`;
  const fact = (head, text) => `<div class="desk-v1-cfw-fact"><strong class="desk-v1-cfw-fact-head">${esc(head)}</strong><span class="desk-v1-cfw-fact-text">${esc(text)}</span></div>`;
  const select = (key, label, items) => `<label class="desk-v1-conn-add-field">${esc(label)}<select class="desk-v1-rules-textinput" data-ref-select="${key}">${items.map(([v, t]) => `<option value="${v}"${P[key] === v ? ' selected' : ''}>${esc(t)}</option>`).join('')}</select></label>`;
  function drop() { gen++; vaultGen++; P = fresh(); secretNode = null; specNode = null; }
  function ensure(api) {
    if (P.url) return;
    P.url = api.info.url; P.name = api.info.host;
    P.source ||= api.info.url; P.address ||= api.info.url;
  }
  function begin(data = {}) { P.kind = data.kind || 'api_base'; P.source = data.input || ''; P.address = data.address || ''; }
  function edited(key) { delete P.labels[key]; P.rid = ''; }
  function meta(api) {
    if (api.sel.variant === 'reference') return null;
    const fields = {}, add = (k, value) => { if (Array.isArray(value) && !value.length) return; fields[k] = P.labels[k] && JSON.stringify(P.labels[k].value) === JSON.stringify(value) ? P.labels[k] : field(value); };
    add(P.kind === 'pypi' ? 'package' : 'address', P.kind === 'pypi' ? P.source.trim().replace(/^(pypi|pip):/i, '') : P.address.trim());
    add('auth_type', P.auth_type); add('transport', P.transport);
    ['credential_names', 'placements', 'scopes'].forEach((k) => add(k, P[k].split(/[\s,]+/).filter(Boolean)));
    return { kind: P.kind, fields };
  }
  function useAlternative(alt) {
    const values = alt.fields || {};
    ['auth_type', 'transport'].forEach((k) => {
      const f = k === 'transport' ? field(alt.transport || 'unknown', 'classifier', 'inferred') : values[k];
      if (f && ['unknown', 'http', 'stdio', 'streamable_http', 'sse', 'none', 'basic', 'bearer', 'oauth', 'api_key'].includes(f.value)) {
        P[k] = f.value; P.labels[k] = field(f.value, f.provenance, f.confidence);
      }
    });
    if (values.url && typeof values.url.value === 'string') { P.address = values.url.value; P.labels.address = field(P.address, values.url.provenance, values.url.confidence); }
    if (P.kind === 'pypi' && alt.package) {
      const p = alt.package;
      P.source = p.name + (p.resolved_version ? '==' + p.resolved_version : '');
      P.labels.package = field(P.source, 'registry_metadata');
    }
    const creds = (alt.credentials || []).slice(0, 12);
    P.credential_names = creds.map((c) => c.name).filter(Boolean).join(' ');
    P.placements = creds.map((c) => c.placement).filter(Boolean).join(' ');
    P.scopes = (alt.scopes || []).slice(0, 20).join(' ');
    ['credential_names', 'placements', 'scopes'].forEach((k) => { P.labels[k] = field(P[k].split(' ').filter(Boolean), 'classifier', 'inferred'); });
    P.rid = ''; P.view = 'parameters';
  }
  async function detect(api) {
    const n = ++gen;
    P.detBusy = true; P.detError = ''; P.det = null;
    const body = { kind: P.kind, input: P.source.trim() };
    if (P.kind === 'api_spec' && specNode && specNode.querySelector('textarea').value.trim()) body.text = specNode.querySelector('textarea').value;
    api.repaint();
    try {
      const answer = await api.ctx.api('POST', '/api/desk/connect/detect', body);
      if (n !== gen) return;
      P.det = answer;
      if (!answer || !answer.ok) P.detError = (answer && answer.error) || 'The detection failed.';
    } catch (e) { if (n !== gen) return; P.detError = e.message || 'The detection failed.'; }
    if (n !== gen) return;
    P.detBusy = false; api.repaint();
  }
  async function loadVault(api) {
    const n = vaultGen;
    if (P.vault !== null || P.vaultBusy) return;
    P.vaultBusy = true; P.vaultError = ''; api.repaint();
    try { const r = await api.ctx.api('GET', '/api/secrets'); if (n !== vaultGen) return; P.vault = r.secrets || []; }
    catch (e) { if (n !== vaultGen) return; P.vaultError = e.message || 'Secrets could not be listed.'; }
    if (n !== vaultGen) return;
    P.vaultBusy = false; api.repaint();
  }
  function existing() { return (P.vault || []).find((s) => s.name === P.vaultName.trim()); }
  function problem(api) {
    if (!P.name.trim()) return 'Enter a name for this service.';
    if (api.sel.variant !== 'reference' && !(P.kind === 'pypi' ? P.source.trim() : P.address.trim())) return 'Enter the reference address or package.';
    if (P.mode !== 'none' && !P.vaultName.trim()) return 'Name the credential in Secrets.';
    if (P.mode === 'existing' && !existing()) return 'Choose an existing credential; Secrets must finish loading.';
    if (P.mode === 'new' && (!secretNode || !secretNode.querySelector('[data-ref-secret]').value)) return 'Enter the credential value.';
    if (P.mode === 'new' && P.entry === 'api_key_pair' && !secretNode.querySelector('[data-ref-user]').value.trim()) return 'Enter the Key ID.';
    return '';
  }
  function credentialForm() {
    return select('mode', 'Credential (optional)', [['none', 'No credential'], ['existing', 'Existing entry in Secrets'], ['new', 'New credential']])
      + (P.mode === 'none' ? '' : input('vaultName', 'Secrets entry name', 120))
      + (P.mode === 'existing' ? `<div data-ref-vault role="status">${esc(P.vaultBusy ? 'Loading Secrets…' : P.vaultError || (existing() ? 'Existing entry selected' : 'Enter an existing entry name'))}</div>${P.vaultError ? button('data-ref-retry-vault', 'Retry') : ''}` : '')
      + (P.mode === 'new' ? select('entry', 'Credential kind', [['api_key', 'API key'], ['token', 'Token'], ['login', 'Username/password'], ['api_key_pair', 'Key ID/secret']]) + '<div data-ref-secret-slot></div>' : '');
  }
  function detected(api) {
    if (P.detBusy) return '<div role="status">Reading public parameters…</div>' + button('data-ref-stop-detect', 'Stop waiting');
    if (P.detError) return `<div role="alert" data-ref-detect-status="failed">${esc(P.detError)}</div>`;
    if (!P.det) return '';
    const alts = P.det.alternatives || [];
    const supported = alts.filter((a) => a.route_type === 'api' || (P.kind === 'pypi' && a.package && a.package.ecosystem === 'pypi'));
    return `<div role="status" data-ref-detect-status="${esc(P.det.status)}">${esc(P.det.status === 'incomplete' ? 'Detection incomplete' : alts.length ? 'Detected suggestions' : 'Nothing detected')}</div>`
      + api.paged('detected', supported, (a) => button(`data-ref-alt="${esc(a.id)}"`, P.kind === 'pypi' ? 'Edit PyPI reference draft' : 'Edit API reference draft'));
  }
  W.registerScreen({ id: 'reference-setup', step: 'setup', match: matches,
    title: (api) => api.sel.variant === 'reference' ? 'Save a reference' : P.kind === 'pypi' ? 'PyPI details' : P.view === 'parameters' ? 'API parameters' : 'API details',
    copy: (api) => api.sel.variant === 'reference' ? 'Save the address and optional credential for agents. This does not connect the service.' : P.kind === 'pypi' ? 'Save package information for later. This version cannot run a PyPI connection.' : 'Save API information for later. This version cannot run a new API connection.',
    body: (api) => {
      ensure(api);
      if (api.sel.variant === 'reference' || P.view === 'credential') return (api.sel.variant === 'reference' ? '' : button('data-ref-prev="parameters"', 'Back to parameters')) + input('name', 'Name', 80) + credentialForm();
      if (P.view === 'parameters') return button('data-ref-prev="info"', 'Back to source') + input('name', 'Name', 80) + (P.kind === 'pypi' ? input('source', 'PyPI package') : input('address', 'API address'))
        + select('auth_type', 'Authentication type', ['unknown', 'none', 'api_key', 'bearer', 'basic', 'oauth'].map((v) => [v, v]))
        + input('credential_names', 'Parameter names (no values)', 800) + input('placements', 'Placements (header, env, query, body, cookie, basic_user, basic_password, unknown)', 300) + input('scopes', 'Scopes', 2400);
      return input('source', P.kind === 'pypi' ? 'PyPI package' : P.kind === 'api_spec' ? 'OpenAPI address' : 'API address')
        + button(`data-ref-detect ${P.detBusy ? 'disabled' : ''}`, 'Detect parameters') + `<div data-ref-detected-slot>${detected(api)}</div>`;
    },
    details: (api) => {
      if (api.sel.variant === 'reference') return fact('Address', P.url);
      if (P.view === 'info') return select('kind', 'Information source', [['api_base', 'API address'], ['api_spec', 'OpenAPI document'], ['pypi', 'PyPI package']])
        + (P.kind === 'api_spec' ? '<div data-ref-spec-slot></div>' : '') + (P.det ? fact('Untrusted evidence only; not saved', JSON.stringify({ problems: P.det.problems, alternatives: P.det.alternatives })) : '');
      return fact('Draft provenance', JSON.stringify(P.labels)) + select('transport', 'Transport (draft only)', ['http', 'unknown', 'stdio', 'streamable_http', 'sse'].map((v) => [v, v]));
    },
    primary: (api) => ({ label: 'Continue', disabled: P.detBusy, run: async () => {
      if (api.sel.variant !== 'reference' && P.view === 'info') {
        if (!P.source.trim()) { api.error('Enter an address or a package name.'); return false; }
        if (P.kind !== 'pypi') P.address = P.source.trim();
        P.view = 'parameters'; api.repaint(); return false;
      }
      if (api.sel.variant !== 'reference' && P.view === 'parameters') { P.view = 'credential'; api.repaint(); return false; }
      const why = problem(api); if (why) { api.error(why); return false; } return true;
    } }),
    bind: (root, api) => {
      root.querySelectorAll('[data-ref-field]').forEach((n) => n.addEventListener('input', () => {
        const k = n.dataset.refField; P[k] = n.value; edited(k);
        if (k === 'source') {
          gen++; P.det = null; P.detBusy = false; P.detError = '';
          root.querySelector('[data-ref-detected-slot]')?.replaceChildren();
          const det = root.querySelector('[data-ref-detect]'); if (det) det.disabled = false;
          if (P.view === 'info') { P.labels = {}; P.auth_type = 'unknown'; P.transport = P.kind === 'pypi' ? 'stdio' : 'http'; P.scopes = ''; P.credential_names = ''; P.placements = ''; }
        }
      }));
      root.querySelectorAll('[data-ref-select]').forEach((n) => n.addEventListener('change', () => {
        const k = n.dataset.refSelect;
        if (k === 'entry' || k === 'mode') { if (secretNode) secretNode.querySelectorAll('input').forEach((i) => { i.value = ''; }); }
        P[k] = n.value; edited(k);
        if (k === 'kind') { gen++; P.det = null; P.detBusy = false; P.detError = ''; P.labels = {}; P.source = ''; P.address = ''; P.auth_type = 'unknown'; P.transport = n.value === 'pypi' ? 'stdio' : 'http'; P.scopes = ''; P.credential_names = ''; P.placements = ''; if (specNode) specNode.querySelector('textarea').value = ''; }
        api.repaint(); if (k === 'mode' && P.mode === 'existing') loadVault(api);
      }));
      const slot = root.querySelector('[data-ref-secret-slot]');
      if (slot) {
        secretNode = api.host(slot, 'credential', (n) => { n.innerHTML = '<label class="desk-v1-conn-add-field" data-ref-user-label>Username / Key ID<input class="desk-v1-rules-textinput" data-ref-user maxlength="200" autocomplete="off"></label><label class="desk-v1-conn-add-field">Credential value<input class="desk-v1-rules-textinput" type="password" data-ref-secret maxlength="8192" autocomplete="new-password"></label>'; n.querySelectorAll('input').forEach((i) => i.addEventListener('input', () => { P.rid = ''; })); }, 'type');
        secretNode.querySelector('[data-ref-user-label]').hidden = !['login', 'api_key_pair'].includes(P.entry);
      }
      const specs = root.querySelector('[data-ref-spec-slot]');
      if (specs) specNode = api.host(specs, 'spec', (n) => { n.innerHTML = '<label class="desk-v1-conn-add-field">OpenAPI JSON (optional, used only for detection)<textarea class="desk-v1-rules-textinput" rows="5" maxlength="200000"></textarea></label>'; n.querySelector('textarea').addEventListener('input', () => { gen++; P.det = null; P.detBusy = false; P.detError = ''; document.querySelector('[data-ref-detected-slot]')?.replaceChildren(); const b = document.querySelector('[data-ref-detect]'); if (b) b.disabled = false; }); }, 'type');
      root.querySelector('[data-ref-detect]')?.addEventListener('click', () => detect(api));
      root.querySelectorAll('[data-ref-prev]').forEach((n) => n.addEventListener('click', () => { P.view = n.dataset.refPrev; api.repaint(); }));
      root.querySelector('[data-ref-stop-detect]')?.addEventListener('click', () => { gen++; P.detBusy = false; P.detError = 'Stopped waiting. Detection may finish on the server; its answer will be ignored.'; api.repaint(); });
      root.querySelector('[data-ref-retry-vault]')?.addEventListener('click', () => loadVault(api));
      root.querySelectorAll('[data-ref-alt]').forEach((n) => n.addEventListener('click', () => { const a = P.det.alternatives.find((v) => v.id === n.dataset.refAlt); useAlternative(a); api.repaint(); }));
    }, discard: drop });
  W.registerScreen({ id: 'reference-permissions', step: 'permissions', match: matches, title: () => 'Permissions',
    copy: () => 'Saving information grants no connection permissions. Credential access follows its existing vault policy.',
    body: () => P.mode === 'new' ? `<label class="desk-v1-cfw-option"><input type="checkbox" data-ref-unattended ${P.unattended ? 'checked' : ''}><span>Allow unattended credential use</span></label>` : P.mode === 'existing' && existing() ? fact('Existing vault policy', `Scope: ${existing().scope || 'global'}; unattended use: ${existing().allow_unattended === false ? 'not allowed' : 'allowed'}`) : fact('Connection permissions', 'None'),
    primary: () => ({ label: 'Continue' }), bind: (root) => root.querySelector('[data-ref-unattended]')?.addEventListener('change', (e) => { P.unattended = e.target.checked; P.rid = ''; }), discard: drop });
  W.registerScreen({ id: 'reference-review', step: 'review', match: matches, title: () => 'Review service',
    copy: () => 'Review what will be saved before entering your passcode.',
    body: (api) => fact('Name', P.name) + fact('Address', P.url) + fact('Status', 'Reference only; not connected or verified')
      + fact('Credential', P.mode === 'none' ? 'None' : `${P.vaultName} (${P.mode === 'new' ? 'entered, hidden' : 'existing reference'})`)
      + (meta(api) ? fact('Draft information', P.kind === 'pypi' ? P.source : P.address) : ''),
    details: (api) => meta(api) ? fact('Untrusted draft data, never approved settings', JSON.stringify(meta(api))) : '',
    primary: (api) => ({ label: P.saving ? 'Saving…' : 'Save', disabled: P.saving || !!problem(api), run: async () => {
      const why = problem(api); if (why) { api.error(why); return false; }
      P.rid ||= uid(); P.saving = true; const n = gen;
      let draft = { url: P.url, method: 'save_for_agents', name: P.name.trim(), credential: null };
      const metadata = meta(api); if (metadata) draft.reference_draft = metadata;
      if (P.mode === 'existing') draft.credential = { name: P.vaultName.trim(), existing: true };
      if (P.mode === 'new') draft.credential = { name: P.vaultName.trim(), entry_type: P.entry,
        value: secretNode.querySelector('[data-ref-secret]').value, username: ['login', 'api_key_pair'].includes(P.entry) ? secretNode.querySelector('[data-ref-user]').value : '', allow_unattended: P.unattended };
      api.repaint(); let response;
      try { response = await window.humanProofFetch('/api/desk/connect/commit', { method: 'POST', body: JSON.stringify({ request_id: P.rid, draft }) }, { title: 'Save reference', description: 'Re-enter your dashboard passcode to save reference information.' }); }
      catch (e) { response = { ok: false, body: { error: e.message || 'The save failed.' } }; }
      draft = null;
      if (n !== gen) return false;
      P.saving = false;
      if (response === null) { api.repaint(); return false; }
      if (!response.ok) { api.error(response.body?.error || 'The reference could not be saved.'); return false; }
      P.result = response.body;
      if (secretNode) secretNode.querySelectorAll('input').forEach((i) => { i.value = ''; });
      if (specNode) specNode.querySelector('textarea').value = '';
      return true;
    } }), discard: drop });
  W.registerScreen({ id: 'reference-result', step: 'result', match: matches, title: () => 'Service saved',
    copy: () => 'Your saved service and its status are shown below.',
    body: () => fact('Saved', P.result?.duplicate ? 'Already saved' : 'Reference saved') + fact('Connection', 'Reference only; not connected') + fact('Permissions', 'No connection permissions granted') + fact('Verified', 'Not checked')
      + fact('Credential', P.result?.credential ? P.result.credential.referenced ? 'Existing vault entry referenced' : 'Stored in Secrets' : 'None'),
    primary: (api) => ({ label: 'Done', run: async () => { api.finish(P.result?.service); return false; } }), discard: drop });
  window.DeskV1ConnectReferenceStep = { matches, begin };
}
