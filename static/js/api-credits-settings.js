// ── API credits card (Settings > Agent Providers > Claude) ───────────────────
// docs/API_CREDITS_BUILD_SPEC.md. Lets a Max/Ultra/Team subscriber point
// Clayrune's background toolless calls at the monthly API credits, with a
// budget, a stop point and a measure-first mode.
//
// The key is a vault entry. Item 2 either picks an existing vault secret or
// takes a pasted key and saves it through the SAME human-only path as
// Settings > Secrets (`POST /api/secrets`, dashboard passcode re-entered by
// humanProofFetch). Clayrune stores only the secret's NAME in
// data/api_credits.json. The pasted value lives in one input, is emptied before
// the request goes out, is never copied into the draft, and nothing here ever
// reads a value back: no route returns one.
//
// Rendered directly under the Claude row by provider-settings.js (a sibling,
// not inside .prov-row, so every vendor row keeps one shape); the card repaints only its own container so a settings
// re-render never eats a half-typed draft. Window-bridged, no `import`.
(function () {
  const DEFAULT_KEY_NAME = 'anthropic-api-key';
  const API = () => (typeof API_BASE === 'string' ? API_BASE : '');
  const esc = (s) => (window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));

  let status = null;       // GET /api/api-credits/status
  let vault = null;        // GET /api/secrets -> { secrets, locked }
  let loading = false;
  let loadError = '';
  let draft = null;        // editable copy of status.settings
  let busy = '';           // '' | 'save' | 'key' | 'test'
  let message = null;      // { text, kind: ok|bad|pending } shown under the buttons
  let keyMessage = null;   // same, under the key field

  function toast(t) { if (typeof showToast === 'function') showToast(t); }
  function el(id) { return document.getElementById(id); }
  function root() { return el('api-credits-card'); }

  function seedDraft(s, plan) {
    const credit = s.plan_credit_usd != null ? s.plan_credit_usd
      : (plan && plan.credit_usd != null ? plan.credit_usd : null);
    draft = Object.assign({}, s, {
      plan_credit_usd: credit,
      budget_usd: s.budget_usd != null ? s.budget_usd : credit,
    });
  }

  async function load(force, keepDraft) {
    if (loading || (status && !force)) return;
    loading = true; loadError = '';
    try {
      const [st, sec] = await Promise.all([
        fetch(API() + '/api/api-credits/status').then((r) => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); }),
        fetch(API() + '/api/secrets').then((r) => (r.ok ? r.json() : { secrets: [] })).catch(() => ({ secrets: [] })),
      ]);
      status = st; vault = sec;
      if (!keepDraft || !draft) seedDraft(st.settings, st.plan);
    } catch (e) {
      loadError = (e && e.message) || 'could not load';
    }
    loading = false;
    repaint();
  }

  // Everything the card shows besides its own inputs.
  function planLine() {
    const p = status && status.plan;
    if (!p) return '';
    if (p.status === 'detected') return `Detected: <b>${esc(p.label)}</b>${p.credit_usd != null ? ` ($${p.credit_usd}/month)` : ''}.`;
    if (p.status === 'not_eligible') return 'Your plan does not include API credits (detected: ' + esc(p.subscription_type || 'unknown') + '). You can still enter an amount if that is wrong.';
    return 'Could not read your plan from this computer. Enter the credit amount yourself.';
  }

  function secretOptions() {
    const list = ((vault && vault.secrets) || []).filter((s) => s.scope === 'global');
    const opts = ['<option value="">(none selected)</option>'];
    let found = false;
    for (const s of list) {
      const sel = s.name === draft.secret_name;
      found = found || sel;
      const attended = s.allow_unattended === false;
      opts.push(`<option value="${esc(s.name)}"${sel ? ' selected' : ''}${attended ? ' disabled' : ''}>${esc(s.name)}${attended ? ' (attended-only, cannot be used by background calls)' : ''}</option>`);
    }
    if (draft.secret_name && !found) {
      opts.push(`<option value="${esc(draft.secret_name)}" selected>${esc(draft.secret_name)} (not in vault)</option>`);
    }
    return opts.join('');
  }

  function msgHtml(m) {
    if (!m) return '';
    const color = m.kind === 'ok' ? 'var(--green)' : m.kind === 'bad' ? 'var(--red)' : 'var(--text-faint)';
    return `<div class="apc-msg" data-kind="${esc(m.kind)}" style="font-size:11px;color:${color};margin-top:4px">${esc(m.text)}</div>`;
  }

  function numField(id, label, hint, value, attrs) {
    return `<div class="settings-row">
        <div><div class="settings-label">${label}</div><div class="settings-hint">${hint}</div></div>
        <input type="number" class="settings-input" id="${id}" value="${value == null ? '' : esc(value)}" ${attrs || ''}
          oninput="ApiCredits.edit('${id}', this.value)" style="width:110px">
      </div>`;
  }

  function cardHtml() {
    if (loadError) return `<div class="settings-hint">API credits: ${esc(loadError)} <button type="button" class="btn-add" style="background:var(--surface3);color:var(--text)" onclick="ApiCredits.reload()">Retry</button></div>`;
    if (!status || !draft) return '<div class="settings-hint">Loading API credits…</div>';
    const locked = !!(vault && vault.locked);
    const saved = status.settings;
    const dirtyKey = draft.secret_name !== saved.secret_name;
    const hasSaved = !!saved.secret_name;
    const modeOpt = (v, label, hint) => `<label style="display:flex;gap:6px;align-items:flex-start;font-size:12px;color:var(--text);padding:2px 0">
        <input type="radio" name="apc-mode" value="${v}" ${draft.mode === v ? 'checked' : ''} onchange="ApiCredits.edit('apc-mode', '${v}')">
        <span><b>${label}</b> <span style="color:var(--text-faint)">${hint}</span></span></label>`;
    return `
      <div class="apc-title" style="font-weight:600;color:var(--text);font-size:13px">API credits for background work</div>
      <div class="settings-hint">Send Clayrune's small background calls (memory, Distiller, mail and page digests, the router) to your monthly API credits instead of your plan. Anything that fails goes back to your plan for that same call.</div>

      <div class="settings-row"><div>
          <div class="settings-label">1. Plan and monthly credit</div>
          <div class="settings-hint" id="apc-plan-line">${planLine()}</div>
        </div>
        <span style="display:flex;align-items:center;gap:4px;font-size:12px">$<input type="number" min="0" step="1" class="settings-input" id="apc-credit" style="width:90px" value="${draft.plan_credit_usd == null ? '' : esc(draft.plan_credit_usd)}" oninput="ApiCredits.edit('apc-credit', this.value)"></span>
      </div>

      <div class="settings-row" style="align-items:flex-start"><div style="min-width:0;flex:1">
          <div class="settings-label">2. API key</div>
          <div class="settings-hint">Create a key in the Anthropic Console, in the workspace you capped. It is saved in this computer's vault; Clayrune keeps only its name and never shows the key again.</div>
          ${locked ? '<div class="settings-hint" style="color:var(--amber)">The vault is locked. Unlock it in Settings &gt; Secrets before saving or testing a key.</div>' : ''}
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px">
            <select class="settings-select" id="apc-secret" onchange="ApiCredits.edit('apc-secret', this.value)" aria-label="Pick a key already in the vault">${secretOptions()}</select>
          </div>
          <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:6px">
            <input ${window.MaskedInput ? window.MaskedInput.attrs() : 'type="password" autocomplete="off"'} class="settings-input" id="apc-key-value" style="flex:1;min-width:160px;text-align:left" placeholder="${hasSaved ? 'paste a new key to replace it' : 'or paste a key (sk-ant-…)'}" aria-label="Paste an API key">
            <input type="text" class="settings-input" id="apc-key-name" style="width:170px;text-align:left" value="${esc(draft.secret_name || DEFAULT_KEY_NAME)}" aria-label="Vault name for the key" oninput="ApiCredits.edit('apc-key-name', this.value)">
            <button type="button" class="btn-add" style="background:var(--surface3);color:var(--text)" id="apc-key-save" onclick="ApiCredits.saveKey(this)" ${busy ? 'disabled' : ''}>${busy === 'key' ? 'Saving…' : 'Save key to vault'}</button>
          </div>
          ${msgHtml(keyMessage)}
        </div>
      </div>

      ${numField('apc-budget', '3. Budget for Clayrune ($)', 'How much of the credit Clayrune may use per cycle. Default: the whole credit.', draft.budget_usd, 'min="0" step="1"')}
      ${numField('apc-stop', '4. Stop at (% of budget)', 'Past this point background calls go back to your plan.', draft.stop_pct, 'min="1" max="100" step="1"')}
      ${numField('apc-reset', '5. Cycle reset day (1-31)', 'The day your credits refresh. Read it off the Console Billing page.', draft.reset_day, 'min="1" max="31" step="1"')}

      <div class="settings-row" style="align-items:flex-start"><div>
          <div class="settings-label">6. Mode</div>
          <div class="settings-hint">Start with Measure first to see what a cycle would cost before moving anything.</div>
        </div>
        <div style="display:flex;flex-direction:column;max-width:330px">
          ${modeOpt('off', 'Off', 'Background calls use your plan (today).')}
          ${modeOpt('measure', 'Measure first', 'Still uses your plan; records an estimated cost per call.')}
          ${modeOpt('on', 'On', 'Use API credits, within the budget.')}
        </div>
      </div>

      <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:6px">
        <button type="button" class="btn-add" style="background:var(--surface3);color:var(--text)" id="apc-test" onclick="ApiCredits.test(this)" ${(busy || !hasSaved || dirtyKey) ? 'disabled' : ''} title="${dirtyKey ? 'Save first, then test' : 'One tiny call to check the key and the credit'}">${busy === 'test' ? 'Testing…' : 'Test key'}</button>
        <button type="button" class="btn-add" id="apc-save" onclick="ApiCredits.save(this)" ${busy ? 'disabled' : ''}>${busy === 'save' ? 'Saving…' : 'Save'}</button>
      </div>
      <div id="apc-msg">${msgHtml(message)}</div>
      <div class="settings-hint" style="margin-top:8px">Claim your credits at claude.ai &gt; Settings &gt; Billing &gt; API credits &gt; Link organization. Then set a workspace spend limit in the Anthropic Console as the backstop: it is the only limit Anthropic enforces. Clayrune's own count is an estimate from token counts and a price table.</div>`;
  }

  function render() {
    // Called inside the Claude row on every settings render.
    setTimeout(() => { if (!status && !loading) load(); else repaint(); }, 0);
    return `<div id="api-credits-card" class="apc-card" style="padding:10px 12px 8px;border:1px solid var(--border);border-radius:8px;margin:-4px 0 10px">${cardHtml()}</div>`;
  }

  function repaint() {
    const r = root();
    if (!r) return;
    // Keep what the user is typing: the key box is never part of the draft.
    const pending = el('apc-key-value') ? el('apc-key-value').value : '';
    const focusId = document.activeElement && r.contains(document.activeElement) ? document.activeElement.id : '';
    r.innerHTML = cardHtml();
    if (pending && el('apc-key-value')) el('apc-key-value').value = pending;
    if (focusId && el(focusId)) el(focusId).focus();
  }

  function edit(id, value) {
    if (!draft) return;
    const num = (v) => (v === '' || v == null ? null : Number(v));
    if (id === 'apc-credit') draft.plan_credit_usd = num(value);
    else if (id === 'apc-budget') draft.budget_usd = num(value);
    else if (id === 'apc-stop') draft.stop_pct = num(value);
    else if (id === 'apc-reset') draft.reset_day = num(value);
    else if (id === 'apc-mode') draft.mode = value;
    else if (id === 'apc-secret') { draft.secret_name = value; repaint(); }
    else if (id === 'apc-key-name') { /* name only; applied when the key is saved */ }
  }

  function settingsBody() {
    const b = {};
    for (const k of ['mode', 'secret_name', 'plan_credit_usd', 'budget_usd', 'stop_pct', 'reset_day']) b[k] = draft[k];
    return b;
  }

  // Turning offload on (or widening what it may spend) needs the passcode: the
  // server enforces it; this only decides whether to open the prompt first.
  function widens() {
    const s = status.settings;
    return draft.mode === 'on' && ['mode', 'secret_name', 'budget_usd', 'stop_pct'].some((k) => draft[k] !== s[k]);
  }

  async function putSettings(body) {
    const opts = { method: 'PUT', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json' } };
    if (widens() && window.humanProofFetch) {
      return window.humanProofFetch(API() + '/api/api-credits/settings', opts, {
        title: 'Turn on API credits',
        description: 'Re-enter your dashboard passcode to let background work spend your API credits.',
      });
    }
    const r = await fetch(API() + '/api/api-credits/settings', opts);
    return { ok: r.ok, status: r.status, body: await r.json().catch(() => ({})) };
  }

  async function save(btn) {
    if (!draft || busy) return;
    busy = 'save'; message = { text: 'Saving…', kind: 'pending' }; repaint();
    try {
      const res = await putSettings(settingsBody());
      if (res === null) { message = null; }
      else if (!res.ok) { message = { text: (res.body && (res.body.error || res.body.message)) || ('Could not save (HTTP ' + res.status + ')'), kind: 'bad' }; }
      else {
        status = res.body; seedDraft(status.settings, status.plan);
        message = { text: 'Saved.', kind: 'ok' };
        if (typeof window._rerenderSysStatusSurfaces === 'function') window._rerenderSysStatusSurfaces();
      }
    } catch (e) { message = { text: (e && e.message) || 'Could not save', kind: 'bad' }; }
    busy = ''; repaint();
  }

  // Paste -> vault. The value leaves the input before the request starts; on a
  // cancelled or failed prompt the user pastes again rather than us holding it.
  async function saveKey(btn) {
    const valueEl = el('apc-key-value');
    const name = (el('apc-key-name') ? el('apc-key-name').value : '').trim() || DEFAULT_KEY_NAME;
    const value = valueEl ? valueEl.value : '';
    if (busy) return;
    if (!value.trim()) { keyMessage = { text: 'Paste the API key first.', kind: 'bad' }; repaint(); return; }
    if (window.SecretForm && window.SecretForm.nameProblem) {
      const bad = window.SecretForm.nameProblem(name);
      if (bad) { keyMessage = { text: bad, kind: 'bad' }; repaint(); return; }
    }
    valueEl.value = '';
    busy = 'key'; keyMessage = { text: 'Waiting for your passcode…', kind: 'pending' }; repaint();
    try {
      const res = await window.humanProofFetch(API() + '/api/secrets', {
        method: 'POST',
        body: JSON.stringify({ name, value, scope: 'global', allow_unattended: true, entry_type: 'api_key',
          description: 'Anthropic API key for the API-credits offload (background calls)' }),
      }, { title: 'Save API key', description: `Re-enter your dashboard passcode to store "${name}" in the vault.`, busyText: 'Saving…' });
      if (res === null) keyMessage = { text: 'Cancelled. Nothing was saved; paste the key again.', kind: 'bad' };
      else if (!res.ok) keyMessage = { text: (res.body && (res.body.error || res.body.message)) || ('Could not save (HTTP ' + res.status + ')'), kind: 'bad' };
      else {
        draft.secret_name = name;
        keyMessage = { text: `Saved to the vault as "${name}" and selected. Press Save to use it.`, kind: 'ok' };
        try { vault = await (await fetch(API() + '/api/secrets')).json(); } catch (_) { /* list refreshes on reload */ }
        toast('API key saved to the vault');
      }
    } catch (e) { keyMessage = { text: (e && e.message) || 'Could not save the key', kind: 'bad' }; }
    busy = ''; repaint();
  }

  async function test(btn) {
    if (busy) return;
    busy = 'test'; message = { text: 'Testing…', kind: 'pending' }; repaint();
    try {
      const r = await fetch(API() + '/api/api-credits/test', { method: 'POST' });
      const out = await r.json().catch(() => ({}));
      const kind = out.result === 'ok' ? 'ok' : 'bad';
      message = { text: out.message || out.error || ('HTTP ' + r.status), kind };
      await load(true, true);
    } catch (e) { message = { text: (e && e.message) || 'Test failed', kind: 'bad' }; }
    busy = ''; repaint();
  }

  window.ApiCredits = { render, edit, save, saveKey, test, reload: () => { loadError = ''; load(true); } };
})();
