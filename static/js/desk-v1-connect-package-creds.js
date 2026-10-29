// Desk v1 — Connections: the credential rows of the PACKAGE setup page (desk-v1-connect-package-step.js).
// One row per environment variable the package needs. Each row names the variable (fixed text when the repository's own
// instructions named it, an editable box when the person added the row) and gets its value from the Vault in one of two ways:
//   * choose a saved credential (metadata only: DeskV1ConnectVaultPicker.metadata over GET /api/secrets), or
//   * "Add a new credential": a masked paste box whose value is saved to the Vault through the passcode-gated
//     POST /api/secrets (the same write the Desk key guides use), when the person presses Continue.
//
// WHERE A SECRET LIVES. Only in a wizard-owned DOM node (`api.host`, as the reference step does): never in this module's
// state, a log, the connection record or a request that is not the one Vault write. The package connection that is
// reviewed and saved afterwards carries only {env, vault name}. The Review step refuses a Vault name that does not exist,
// so a new credential has to be written when Continue is pressed, before Review; it is created, never replaced: a name
// already in the Vault is skipped and a fresh listing is checked immediately before the write.
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const W = () => window.DeskV1ConnectCopy.words;
const MAX_ROWS = 8;

export function createPackageCreds() {
  let rows = [], seq = 0, vault = null, names = new Set(), busy = false, loadError = '', saving = false;
  const nodes = new Map();                                     // row id -> the kept DOM node holding its masked input

  const secretInput = (r) => { const n = nodes.get(r.id); return n ? n.querySelector('[data-pk-cred-secret]') : null; };
  const typed = (r) => { const i = secretInput(r); return i ? i.value : ''; };
  const clearSecret = (r) => { const i = secretInput(r); if (i) i.value = ''; };
  const trimmed = (r) => ({ env: r.env.trim(), vault: r.vault.trim() });
  const has = (r) => (r.mode === 'new' ? !!typed(r) : !!r.vault.trim());

  function add(env = '', fixed = false) { rows.push({ id: ++seq, env, fixed, vault: '', mode: 'saved' }); }
  function reset() { rows.forEach(clearSecret); rows = []; vault = null; names = new Set(); busy = false; loadError = ''; saving = false; nodes.clear(); }

  async function load(api) {
    if (vault !== null || busy) return;
    busy = true; loadError = ''; api.repaint();
    try {
      const r = await api.ctx.api('GET', '/api/secrets');
      vault = window.DeskV1ConnectVaultPicker.metadata(r);
      names = new Set((r.secrets || []).map((s) => s.name));
    } catch (e) { loadError = (e && e.message) || W().credentialsLoadFailed; }
    busy = false; api.repaint();
  }

  // ── markup ────────────────────────────────────────────────────────────────
  function nameHTML(r, i) {
    if (r.fixed) return `<div class="desk-v1-cfk-cred-fixed"><span class="desk-v1-cfk-cred-fixed-label">${esc(W().variableLabel)}</span><strong data-pk-cred-fixed="${i}">${esc(r.env)}</strong></div>`;
    return `<label class="desk-v1-conn-add-field">${esc(W().variableLabel)}<input type="text" class="desk-v1-rules-textinput" data-pk-cred-env="${i}" value="${esc(r.env)}" placeholder="VARIABLE_NAME" maxlength="64" autocapitalize="characters" spellcheck="false"></label>`;
  }
  function valueHTML(r, i) {
    if (r.mode === 'new') return `<div data-pk-cred-secret-slot="${r.id}"></div>`;
    return `${window.DeskV1ConnectVaultPicker.html(vault || [], r.vault, `data-pk-cred-vault="${i}" aria-describedby="pk-vault-help-${i}"`)}<span class="desk-v1-cfw-fact-text" id="pk-vault-help-${i}">${esc(W().credentialPickHelp)}</span>`;
  }
  function rowHTML(r, i) {
    const swap = r.mode === 'new'
      ? `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-cred-use-saved="${i}">${esc(W().useSaved)}</button>`
      : `<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-cred-new="${i}">${esc(W().addNew)}</button>`;
    return `<div class="desk-v1-cfk-cred" data-pk-cred="${i}">
        <div class="desk-v1-cfk-cred-col">${nameHTML(r, i)}</div>
        <div class="desk-v1-cfk-cred-col">${valueHTML(r, i)}</div>
        <div class="desk-v1-cfk-cred-actions">${swap}<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-cred-remove="${i}">Remove</button></div>
      </div>`;
  }
  function statusHTML() {
    if (busy) return `<div class="desk-v1-cfw-fact-text" data-pk-cred-status role="status">${esc(W().loading)}</div>`;
    if (loadError) return `<div class="desk-v1-cfw-msg" data-pk-cred-status role="alert">${esc(loadError)} <button type="button" class="desk-v1-cf-link" data-pk-cred-retry>${esc(W().retry)}</button></div>`;
    return '';
  }
  // `optional`: names the repository mentions as extras (an informational line, never a row).
  function html(optional) {
    const extra = (optional || []).length ? `<div class="desk-v1-cfw-fact-text" data-pk-creds-optional>${esc(W().credentialsOptional)} ${optional.map((e) => `<span data-connect-value><strong>${esc(e)}</strong></span>`).join(', ')}</div>` : '';
    return `${extra}${statusHTML()}${rows.map(rowHTML).join('')}
        <div class="desk-v1-cfk-actions"><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-pk-cred-add>Add a credential</button></div>`;
  }

  // ── events ────────────────────────────────────────────────────────────────
  function bind(root, api) {
    if (rows.length && vault === null && !busy && !loadError) load(api);
    root.querySelectorAll('[data-pk-cred-env]').forEach((n) => n.addEventListener('input', () => { rows[+n.dataset.pkCredEnv].env = n.value; }));
    root.querySelectorAll('[data-pk-cred-vault]').forEach((n) => n.addEventListener('change', () => { rows[+n.dataset.pkCredVault].vault = n.value; }));
    root.querySelectorAll('[data-pk-cred-remove]').forEach((n) => n.addEventListener('click', () => {
      const [r] = rows.splice(+n.dataset.pkCredRemove, 1); if (r) { clearSecret(r); nodes.delete(r.id); }
      api.repaint();
    }));
    root.querySelectorAll('[data-pk-cred-new]').forEach((n) => n.addEventListener('click', () => {
      const r = rows[+n.dataset.pkCredNew]; r.mode = 'new'; r.vault = ''; api.repaint();
      const i = secretInput(r); if (i) i.focus();
    }));
    root.querySelectorAll('[data-pk-cred-use-saved]').forEach((n) => n.addEventListener('click', () => {
      const r = rows[+n.dataset.pkCredUseSaved]; clearSecret(r); r.mode = 'saved'; api.repaint();
    }));
    root.querySelectorAll('[data-pk-cred-secret-slot]').forEach((slot) => {
      const r = rows.find((x) => x.id === +slot.dataset.pkCredSecretSlot); if (!r) return;
      nodes.set(r.id, api.host(slot, `cred-${r.id}`, (n) => {
        n.innerHTML = `<label class="desk-v1-conn-add-field">${esc(W().secretValue)}<input class="desk-v1-rules-textinput" type="password" data-pk-cred-secret="${r.id}" maxlength="8192" autocomplete="new-password" autocapitalize="off" spellcheck="false" aria-describedby="pk-secret-help-${r.id}"><span class="desk-v1-cfw-fact-text" id="pk-secret-help-${r.id}">${esc(W().credentialValueHelp)}</span></label>`;
      }, 'type'));
    });
    const addButton = root.querySelector('[data-pk-cred-add]');
    if (addButton) addButton.addEventListener('click', () => { if (rows.length < MAX_ROWS) add('', false); api.repaint(); });
    const retry = root.querySelector('[data-pk-cred-retry]');
    if (retry) retry.addEventListener('click', () => { vault = null; load(api); });
  }

  // ── saving a new credential ───────────────────────────────────────────────
  const nameFor = (env, taken) => {
    const base = (env.toLowerCase().replace(/[^a-z0-9._-]+/g, '-').replace(/^[^a-z0-9]+/, '') || 'credential').slice(0, 58);
    let name = base, n = 1;
    while (taken.has(name)) name = `${base}-${++n}`;
    return name;
  };
  // -> { ok: true } | { ok: false, message } (message '' = the person cancelled the passcode prompt). Writes only a NEW entry.
  async function commit(api) {
    if (saving) return { ok: false, message: '' };
    const fresh = rows.filter((r) => r.mode === 'new' && typed(r));
    if (!fresh.length) return { ok: true };
    saving = true;
    try {
      for (const r of fresh) {
        let taken;
        try { taken = new Set(((await api.ctx.api('GET', '/api/secrets')).secrets || []).map((s) => s.name)); }
        catch (e) { return { ok: false, message: (e && e.message) || W().credentialsLoadFailed }; }
        const name = nameFor(r.env.trim(), taken);
        let res;
        try {
          res = await window.humanProofFetch('/api/secrets', { method: 'POST', body: JSON.stringify({ name, value: typed(r), scope: 'global', allow_unattended: true, entry_type: 'api_key', description: `Saved from Connections for ${r.env.trim()}` }) },
            { title: W().credentialSaveTitle, description: W().credentialSaveProof, busyText: W().credentialSaving });
        } catch (e) { return { ok: false, message: (e && e.message) || W().credentialSaveFailed }; }
        if (res === null) return { ok: false, message: '' };
        if (!res.ok) { const b = res.body || {}; return { ok: false, message: b.error || b.message || `${W().credentialSaveFailed} (HTTP ${res.status})` }; }
        clearSecret(r); r.mode = 'saved'; r.vault = name;
        names.add(name); (vault || (vault = [])).push({ name, username: '', scope: 'global', allow_unattended: true });
      }
      return { ok: true };
    } finally { saving = false; api.repaint(); }
  }

  return {
    add, reset, bind, html, commit,
    count: () => rows.length,
    has: (env) => rows.some((r) => r.env === env),
    // rows named by the repository's own instructions: fixed text, not an editable box
    setDocumented(list) { reset(); (list || []).forEach((c) => add(c.env, true)); },
    // a suggestion fills a blank row first, else adds one (never past the cap)
    suggest(env) { if (rows.length >= MAX_ROWS) return; const empty = rows.find((r) => !r.fixed && !r.env.trim() && !has(r)); if (empty) empty.env = env; else add(env, false); },
    restore(list) { reset(); (list || []).forEach(c => {add(c.env);rows[rows.length-1].vault=c.vault;}); },
    envs: () => rows.map((r) => r.env.trim()).filter(Boolean),
    // {env, vault} for the Review request; a row still waiting to be saved has no vault name yet and is left out
    list: () => rows.map(trimmed).filter((c) => c.env && c.vault),
    // a row that has one half of the pair only
    half() { const r = rows.find((x) => (x.env.trim() || has(x)) && !(x.env.trim() && has(x))); return r ? { env: r.env.trim(), vault: r.vault.trim() } : null; },
  };
}
