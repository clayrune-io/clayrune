// ── Secrets vault ────────────────────────────────────────────────────────────
//
// The UI half of mc/secrets_store.py. This panel exists for one reason: a
// credential must reach the server WITHOUT passing through the agent. Typing a
// password into the chat composer puts it in the transcript — and from there in
// MEMORY.md and possibly a distilled skill, permanently. This form posts
// browser → server directly, so the agent never sees the value.
//
// There is deliberately no way to read a value back: the API has no
// plaintext-returning route, so "reveal" is not implementable here by design.
// Editing a secret's metadata leaves the value untouched (PATCH with no value).

let _secretsAuditOpen = false;

function _secTimeAgo(iso) {
  if (!iso) return 'never';
  const ms = Date.now() - new Date(iso).getTime();
  if (isNaN(ms)) return 'never';
  const s = Math.floor(ms / 1000);
  if (s < 60) return 'just now';
  const m = Math.floor(s / 60);
  if (m < 60) return m + 'm ago';
  const h = Math.floor(m / 60);
  if (h < 24) return h + 'h ago';
  return Math.floor(h / 24) + 'd ago';
}

async function openSecretsVault() {
  const modalId = '__secrets';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    refreshSecretsList();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 820);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F510; Secrets</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px">
      <div style="font-size:12px;color:var(--text-faint);line-height:1.55;margin-bottom:12px">
        Passwords and tokens agents can <em>use</em> without ever seeing them.
        Type credentials here — never in the chat, where they'd be saved to the
        transcript forever.
      </div>
      <div id="secrets-keywarn"></div>
      <div id="secrets-lockbar"></div>
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px;flex-wrap:wrap">
        <span id="secrets-count" class="memory-hint" style="margin:0">Loading…</span>
        <div style="display:flex;gap:6px;flex-wrap:wrap">
          <button class="btn-header-action" style="padding:5px 12px;font-size:11px"
                  onclick="toggleSecretsAudit()" id="secrets-audit-btn">Access log</button>
          <button class="btn-add" style="padding:5px 12px;font-size:11px" id="secrets-add-btn"
                  onclick="openSecretEditor(null)">Add secret</button>
        </div>
      </div>
      <div id="secrets-list"></div>
      <div id="secrets-audit" style="display:none;margin-top:16px"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);

  await refreshSecretsList();
}

async function refreshSecretsList() {
  const list = document.getElementById('secrets-list');
  const countEl = document.getElementById('secrets-count');
  const warnEl = document.getElementById('secrets-keywarn');
  if (!list) return;
  let data, lockData;
  try {
    const [res, lockRes] = await Promise.all([
      fetch(API_BASE + '/api/secrets'),
      fetch(API_BASE + '/api/secrets/vault-lock'),
    ]);
    data = await res.json();
    lockData = await lockRes.json();
    if (data.error) throw new Error(data.error);
  } catch (e) {
    list.innerHTML = `<div class="process-empty">Could not read the vault: ${esc(e.message)}</div>`;
    return;
  }

  _renderVaultLockbar(lockData && lockData.state, lockData && lockData.legacy_key_copies_present);
  const addBtn = document.getElementById('secrets-add-btn');
  if (addBtn) addBtn.style.display = data.locked ? 'none' : '';

  // A file-backed master key is readable by anything running as this user,
  // whereas the OS keyring is at least gated by the login session. Say so.
  if (warnEl) {
    warnEl.innerHTML = data.key_at_rest_warning ? `
      <div style="padding:8px 12px;margin-bottom:12px;border:1px solid var(--border);
                  border-left:3px solid var(--warn,#c9852a);border-radius:4px;
                  font-size:11px;color:var(--text-faint);line-height:1.5">
        ${esc(data.key_at_rest_warning)}
      </div>` : '';
  }

  const secrets = data.secrets || [];
  if (countEl) countEl.textContent = secrets.length
    ? `${secrets.length} secret${secrets.length === 1 ? '' : 's'}${data.locked ? ' (locked)' : ''}`
    : 'No secrets yet';

  if (data.locked) {
    // Names only, no actions — a locked vault can't decrypt to edit/delete/
    // copy-ref safely, and this keeps that visibly true rather than showing
    // buttons that would just 403/lock-error when clicked.
    list.innerHTML = secrets.length ? secrets.map(s => `
      <div style="display:flex;align-items:center;gap:8px;padding:6px 0;
                  border-bottom:1px solid var(--border);opacity:0.6">
        <code style="font-size:13px;color:var(--text);font-family:var(--mono)">${esc(s.name)}</code>
      </div>`).join('') : `<div class="process-empty">Vault is locked.</div>`;
    return;
  }

  if (!secrets.length) {
    list.innerHTML = `<div class="process-empty">
      Nothing stored. Add one, then reference it in a task as
      <code>{{secret:name}}</code> — the server fills it in at the moment of use.
    </div>`;
    return;
  }

  const presets = await _secLoadPresets();
  list.innerHTML = secrets.map(s => {
    const scopeBadge = s.scope === 'global'
      ? '<span style="font-size:10px;color:var(--text-faint)">all projects</span>'
      : `<span style="font-size:10px;color:var(--text-faint)">only ${esc(s.scope)}</span>`;
    const totpBadge = s.kind === 'totp' ? `
      <span title="Two-factor code generator${s.issuer ? ' — ' + esc(s.issuer) : ''}"
            style="font-size:10px;padding:1px 6px;border:1px solid var(--border);
                   border-radius:99px;color:var(--text-faint)">2FA code</span>` : '';
    // A 2FA seed already carries its own tag. An entry whose type was only
    // inferred takes the engine's type when it is one an engine owns (Higgsfield
    // saved before types existed has a username, so it infers as a Login).
    const eType = (s.entry_type_inferred && presets[s.name] && presets[s.name].entry_type) || s.entry_type;
    const typeTag = (s.kind === 'totp' || !window.SecretForm.TYPES[eType]) ? '' : `
      <span data-sec-type="${esc(eType)}" style="font-size:10px;padding:1px 6px;border:1px solid var(--border);
                   border-radius:99px;color:var(--text-faint)">${esc(window.SecretForm.TYPES[eType].label)}</span>`;
    const attended = s.allow_unattended ? '' : `
      <span title="Steward and scheduled runs are refused this credential"
            style="font-size:10px;padding:1px 6px;border:1px solid var(--border);
                   border-radius:99px;color:var(--text-faint)">attended only</span>`;
    const used = s.use_count
      ? `used ${s.use_count}&times;, last ${esc(_secTimeAgo(s.last_used_at))}`
      : 'never used';
    return `
      <div style="display:flex;align-items:flex-start;gap:12px;padding:10px 0;
                  border-bottom:1px solid var(--border)">
        <div style="flex:1;min-width:0">
          <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
            <code style="font-size:13px;color:var(--text);font-family:var(--mono)">${esc(s.name)}</code>
            ${s.username ? `<span style="font-size:11px;color:var(--text-faint);font-family:var(--mono)">${esc(s.username)}</span>` : ''}
            ${typeTag}${scopeBadge}${totpBadge}${attended}
          </div>
          ${s.description ? `<div style="font-size:11px;color:var(--text-faint);margin-top:3px">${esc(s.description)}</div>` : ''}
          <div style="font-size:10px;color:var(--text-faint);margin-top:3px">${used}</div>
        </div>
        <div style="display:flex;gap:6px;flex-shrink:0">
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  title="Copy the placeholder to paste into a task"
                  onclick="copySecretPlaceholder('${esc(s.name)}')">Copy ref</button>
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  onclick="openSecretEditor('${esc(s.name)}')">Edit</button>
          <button class="btn-header-action" style="padding:4px 10px;font-size:11px"
                  onclick="deleteSecret('${esc(s.name)}')">Delete</button>
        </div>
      </div>`;
  }).join('');
}

// ── Vault passphrase lock (MC backlog 503edfe4) ─────────────────────────────
//
// Three states from GET /api/secrets/vault-lock: 'unconfigured' (no
// passphrase ever set — an inline offer, easy to ignore), 'locked' (a human
// must unlock before anything below can be read), 'unlocked' (configured and
// open — an inline "Change passphrase" link). The unlock form itself is
// rendered inline in the lockbar rather than a nested modal, since it's one
// or two fields and needs to work above a phone keyboard.

let _secUseRecoveryKey = false;

function _secToggleUseRecoveryKey() {
  _secUseRecoveryKey = !_secUseRecoveryKey;
  refreshSecretsList();
}

// `_require_human_passcode` (secrets_routes.py) reads this field on every
// set/change/unlock/lock POST — re-entered here rather than trusted from a
// cookie/session, for the forged-Origin reason documented there. `message`
// carries the friendlier server text for passcode_required/too_many_attempts;
// bad_passcode has none, so it gets one here.
function _vaultLockErrorText(out) {
  if (!out) return 'Request failed.';
  if (out.error === 'bad_passcode') return 'Wrong dashboard passcode.';
  return out.message || out.error || 'Request failed.';
}

// A pre-passphrase-lock copy of the master key (DPAPI mirror, OS keyring
// entry) can survive the initial set-passphrase quarantine attempt — see
// _quarantine_legacy_key_material's retry in secrets_store.py (MC 503edfe4
// follow-up, clayrune.log 2026-09-24T16:18Z). The server now retries on
// every unlock too, but a vault left locked never unlocks to trigger that,
// so this line + button is the fallback path a human can always reach.
function _vaultLegacyWarningHtml(legacyPresent) {
  if (!legacyPresent) return '';
  return `
    <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;
                flex-wrap:wrap;margin-top:8px;padding:8px 10px;border:1px solid var(--border);
                border-left:3px solid var(--warn,#c9852a);border-radius:4px">
      <span style="font-size:10px;color:var(--text-faint);flex:1;min-width:160px">
        A legacy copy of the master key is still on this machine outside the
        passphrase lock.
      </span>
      <button class="btn-header-action" style="padding:3px 8px;font-size:10px;flex-shrink:0"
              onclick="openVaultRetireLegacy()">Retire legacy key copies</button>
    </div>`;
}

function _renderVaultLockbar(state, legacyPresent) {
  const bar = document.getElementById('secrets-lockbar');
  if (!bar) return;
  if (state === 'locked') {
    bar.innerHTML = `
      <div style="padding:12px;margin-bottom:12px;border:1px solid var(--border);
                  border-left:3px solid var(--warn,#c9852a);border-radius:4px">
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:8px">
          &#x1F512; Vault is locked
        </div>
        <div style="font-size:11px;color:var(--text-faint);margin-bottom:10px">
          Unlock it to read, add, or edit secrets. Metadata (names) is still
          visible below; values are not.
        </div>
        <div style="display:flex;gap:6px;flex-wrap:wrap;align-items:center">
          <input ${window.MaskedInput.attrs()} id="vault-unlock-input"
            placeholder="${_secUseRecoveryKey ? 'recovery key' : 'passphrase'}"
            style="flex:1;min-width:160px;padding:7px 10px;font-size:13px;
                   background:var(--surface2);border:1px solid var(--border);
                   border-radius:4px;color:var(--text);font-family:var(--mono)"
            onkeydown="if(event.key==='Enter')submitVaultUnlock()">
          <input ${window.MaskedInput.attrs()} id="vault-unlock-passcode"
            placeholder="dashboard passcode"
            style="flex:1;min-width:160px;padding:7px 10px;font-size:13px;
                   background:var(--surface2);border:1px solid var(--border);
                   border-radius:4px;color:var(--text);font-family:var(--mono)"
            onkeydown="if(event.key==='Enter')submitVaultUnlock()">
          <button class="btn-add" style="padding:6px 14px;font-size:11px"
                  onclick="submitVaultUnlock()">Unlock</button>
        </div>
        <div style="display:flex;justify-content:space-between;align-items:center;margin-top:8px">
          <button class="btn-header-action" style="padding:3px 8px;font-size:10px"
                  onclick="_secToggleUseRecoveryKey()">
            ${_secUseRecoveryKey ? 'Use passphrase instead' : 'Use recovery key instead'}
          </button>
          <span id="vault-unlock-status" style="font-size:11px;color:var(--danger,#c94a3a)"></span>
        </div>
        ${_vaultLegacyWarningHtml(legacyPresent)}
      </div>`;
    const input = document.getElementById('vault-unlock-input');
    if (input) input.focus();
  } else if (state === 'unconfigured') {
    bar.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:8px;
                  flex-wrap:wrap;padding:8px 12px;margin-bottom:12px;border:1px solid var(--border);
                  border-radius:4px">
        <div style="font-size:11px;color:var(--text-faint);line-height:1.5;flex:1;min-width:200px">
          No vault passphrase set — the server can decrypt secrets as soon as it
          starts. Set one so the vault stays locked until you unlock it by hand.
        </div>
        <button class="btn-header-action" style="padding:5px 12px;font-size:11px;flex-shrink:0"
                onclick="openVaultSetPassphrase()">Set a passphrase</button>
      </div>`;
  } else if (state === 'unlocked') {
    bar.innerHTML = `
      <div style="display:flex;align-items:center;justify-content:flex-end;gap:6px;margin-bottom:6px">
        <span style="font-size:10px;color:var(--text-faint)">&#x1F513; Vault unlocked</span>
        <button class="btn-header-action" style="padding:3px 8px;font-size:10px"
                onclick="openVaultLockNow()">Lock now</button>
        <button class="btn-header-action" style="padding:3px 8px;font-size:10px"
                onclick="openVaultChangePassphrase()">Change passphrase</button>
      </div>
      ${_vaultLegacyWarningHtml(legacyPresent)}`;
  } else {
    bar.innerHTML = '';
  }
}

function openVaultLockNow() {
  // Same human-passcode gate as set/change/unlock (_require_human_passcode) —
  // re-entered here rather than trusted from a cookie/session, for the same
  // forged-Origin reason those routes do it (see secrets_routes.py).
  const modalId = '__vault-lock-now';
  if (openModals.has(modalId)) closeModalById(modalId);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-compact';
  _clampModalSize(content, 420);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F512; Lock the vault</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        Re-enter your dashboard passcode to lock the vault now.
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Dashboard passcode</label>
        <input ${window.MaskedInput.attrs()} id="vln-passcode"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)"
          onkeydown="if(event.key==='Enter')submitVaultLockNow('${modalId}')">
      </div>
      <div id="vln-status" style="font-size:11px;color:var(--danger,#c94a3a);min-height:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="submitVaultLockNow('${modalId}')">Lock now</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  document.getElementById('vln-passcode').focus();
}

async function submitVaultLockNow(modalId) {
  const passcode = document.getElementById('vln-passcode').value;
  const statusEl = document.getElementById('vln-status');
  if (!passcode) { statusEl.textContent = 'Dashboard passcode required.'; return; }
  try {
    const res = await fetch(API_BASE + '/api/secrets/vault-lock/lock', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode }),
    });
    const out = await res.json();
    if (!res.ok) { statusEl.textContent = _vaultLockErrorText(out); return; }
    closeModalById(modalId);
    showToast('Vault locked');
    await refreshSecretsList();
  } catch (e) {
    statusEl.textContent = 'Lock failed: ' + e.message;
  }
}

function openVaultRetireLegacy() {
  // Same human-passcode gate as the other vault-lock actions — this MOVES
  // the legacy copies into quarantine, never deletes (secrets_store.py's
  // _quarantine_legacy_key_material).
  const modalId = '__vault-retire-legacy';
  if (openModals.has(modalId)) closeModalById(modalId);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-compact';
  _clampModalSize(content, 420);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Retire legacy key copies</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        Moves the legacy master-key copy (DPAPI mirror / OS keyring entry)
        into a quarantine directory outside the passphrase lock's reach. It
        is moved, not deleted. Re-enter your dashboard passcode to continue.
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Dashboard passcode</label>
        <input ${window.MaskedInput.attrs()} id="vrl-passcode"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)"
          onkeydown="if(event.key==='Enter')submitVaultRetireLegacy('${modalId}')">
      </div>
      <div id="vrl-status" style="font-size:11px;color:var(--danger,#c94a3a);min-height:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="submitVaultRetireLegacy('${modalId}')">Retire</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  document.getElementById('vrl-passcode').focus();
}

async function submitVaultRetireLegacy(modalId) {
  const passcode = document.getElementById('vrl-passcode').value;
  const statusEl = document.getElementById('vrl-status');
  if (!passcode) { statusEl.textContent = 'Dashboard passcode required.'; return; }
  try {
    const res = await fetch(API_BASE + '/api/secrets/vault-lock/retire-legacy', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passcode }),
    });
    const out = await res.json();
    if (!res.ok) { statusEl.textContent = _vaultLockErrorText(out); return; }
    closeModalById(modalId);
    showToast(out.had_legacy_copies ? 'Legacy key copies retired' : 'Nothing to retire');
    await refreshSecretsList();
  } catch (e) {
    statusEl.textContent = 'Retire failed: ' + e.message;
  }
}

async function submitVaultUnlock() {
  const input = document.getElementById('vault-unlock-input');
  const passcodeInput = document.getElementById('vault-unlock-passcode');
  const statusEl = document.getElementById('vault-unlock-status');
  const value = (input && input.value || '').trim();
  const passcode = (passcodeInput && passcodeInput.value || '').trim();
  if (!value) return;
  if (!passcode) { if (statusEl) statusEl.textContent = 'Dashboard passcode required.'; return; }
  const body = _secUseRecoveryKey ? { recovery_key: value, passcode } : { passphrase: value, passcode };
  try {
    const r = await window.VaultUnlock.unlock(body);   // vault-unlock.js: the one unlock request
    if (!r.ok) {
      if (statusEl) statusEl.textContent = _vaultLockErrorText(r.body);
      if (input) { input.value = ''; input.focus(); }
      if (passcodeInput) passcodeInput.value = '';
      return;
    }
    showToast('Vault unlocked');
    await refreshSecretsList();
  } catch (e) {
    if (statusEl) statusEl.textContent = 'Unlock failed: ' + e.message;
  }
}

function openVaultSetPassphrase() {
  const modalId = '__vault-set-passphrase';
  if (openModals.has(modalId)) closeModalById(modalId);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-compact';
  _clampModalSize(content, 480);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">&#x1F512; Set a vault passphrase</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        The server will start locked after every restart until a human unlocks
        it from this dashboard. You'll also get a one-time recovery key —
        write it down, it's the only backup if you forget the passphrase.
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">1. Passphrase</label>
        <input ${window.MaskedInput.attrs()} id="vsp-pass"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">2. Confirm</label>
        <input ${window.MaskedInput.attrs()} id="vsp-pass2"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">3. Dashboard passcode</label>
        <input ${window.MaskedInput.attrs()} id="vsp-passcode"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div id="vsp-status" style="font-size:11px;color:var(--danger,#c94a3a);min-height:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="submitVaultSetPassphrase('${modalId}')">Set passphrase</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  document.getElementById('vsp-pass').focus();
}

async function submitVaultSetPassphrase(modalId) {
  const pass = document.getElementById('vsp-pass').value;
  const pass2 = document.getElementById('vsp-pass2').value;
  const passcode = document.getElementById('vsp-passcode').value.trim();
  const statusEl = document.getElementById('vsp-status');
  if (pass.length < 8) { statusEl.textContent = 'At least 8 characters.'; return; }
  if (pass !== pass2) { statusEl.textContent = "Passphrases don't match."; return; }
  if (!passcode) { statusEl.textContent = 'Dashboard passcode required.'; return; }
  try {
    const res = await fetch(API_BASE + '/api/secrets/vault-lock/set', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ passphrase: pass, passcode }),
    });
    const out = await res.json();
    if (!res.ok) { statusEl.textContent = _vaultLockErrorText(out); return; }
    closeModalById(modalId);
    _showVaultRecoveryKey(out.recovery_key);
    await refreshSecretsList();
  } catch (e) {
    statusEl.textContent = 'Failed: ' + e.message;
  }
}

function _showVaultRecoveryKey(recoveryKey) {
  const modalId = '__vault-recovery-key';
  if (openModals.has(modalId)) closeModalById(modalId);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-compact';
  _clampModalSize(content, 480);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Save your recovery key</span>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        This is shown <strong>once</strong>. It unlocks the vault if you ever
        forget the passphrase — there is no other way in.
      </div>
      <code style="display:block;padding:12px;background:var(--surface2);border:1px solid var(--border);
                   border-radius:4px;font-size:14px;letter-spacing:1px;text-align:center;
                   word-break:break-all;color:var(--text)">${esc(recoveryKey)}</code>
      <button class="btn-header-action" style="padding:6px 12px;font-size:11px"
              onclick="navigator.clipboard.writeText('${esc(recoveryKey)}').then(()=>showToast('Copied'))">Copy</button>
      <div style="display:flex;justify-content:flex-end">
        <button class="btn-add" onclick="closeModalById('${modalId}')">I've saved it</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
}

function openVaultChangePassphrase() {
  const modalId = '__vault-change-passphrase';
  if (openModals.has(modalId)) closeModalById(modalId);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content modal-compact';
  _clampModalSize(content, 480);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Change vault passphrase</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      <div style="font-size:11px;color:var(--text-faint);line-height:1.55">
        Your recovery key from setup still works after this — only the
        passphrase leg changes.
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">1. Current passphrase</label>
        <input ${window.MaskedInput.attrs()} id="vcp-old"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">2. New passphrase</label>
        <input ${window.MaskedInput.attrs()} id="vcp-new"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div>
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">3. Dashboard passcode</label>
        <input ${window.MaskedInput.attrs()} id="vcp-passcode"
          style="width:100%;padding:7px 10px;font-size:13px;background:var(--surface2);
                 border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>
      <div id="vcp-status" style="font-size:11px;color:var(--danger,#c94a3a);min-height:14px"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" onclick="submitVaultChangePassphrase('${modalId}')">Change passphrase</button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  document.getElementById('vcp-old').focus();
}

async function submitVaultChangePassphrase(modalId) {
  const oldPass = document.getElementById('vcp-old').value;
  const newPass = document.getElementById('vcp-new').value;
  const passcode = document.getElementById('vcp-passcode').value.trim();
  const statusEl = document.getElementById('vcp-status');
  if (newPass.length < 8) { statusEl.textContent = 'New passphrase: at least 8 characters.'; return; }
  if (!passcode) { statusEl.textContent = 'Dashboard passcode required.'; return; }
  try {
    const res = await fetch(API_BASE + '/api/secrets/vault-lock/change', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ old_passphrase: oldPass, new_passphrase: newPass, passcode }),
    });
    const out = await res.json();
    if (!res.ok) { statusEl.textContent = _vaultLockErrorText(out); return; }
    closeModalById(modalId);
    showToast('Passphrase changed');
  } catch (e) {
    statusEl.textContent = 'Failed: ' + e.message;
  }
}

function copySecretPlaceholder(name) {
  const ref = '{{secret:' + name + '}}';
  navigator.clipboard.writeText(ref)
    .then(() => showToast('Copied ' + ref))
    .catch(() => showToast('Copy failed — the reference is ' + ref, 5000));
}

async function deleteSecret(name) {
  if (!confirm(`Delete "${name}"?\n\nAnything referencing {{secret:${name}}} will start failing.`)) return;
  try {
    // MC-995: deleting a secret is human-only-gated server-side.
    const result = await humanProofFetch(API_BASE + '/api/secrets/' + encodeURIComponent(name), {
      method: 'DELETE',
    }, {
      title: 'Delete secret',
      description: `Re-enter your dashboard passcode to delete "${name}".`,
    });
    if (result === null) return;
    if (!result.ok) throw new Error(result.body.error || `HTTP ${result.status}`);
    showToast('Deleted ' + name);
  } catch (e) {
    showToast('Delete failed: ' + e.message, 4000);
  }
  refreshSecretsList();
}

async function toggleSecretsAudit() {
  const box = document.getElementById('secrets-audit');
  const btn = document.getElementById('secrets-audit-btn');
  if (!box) return;
  _secretsAuditOpen = !_secretsAuditOpen;
  box.style.display = _secretsAuditOpen ? 'block' : 'none';
  if (btn) btn.textContent = _secretsAuditOpen ? 'Hide log' : 'Access log';
  if (!_secretsAuditOpen) return;

  box.innerHTML = '<div class="memory-hint">Loading…</div>';
  let records = [];
  try {
    const res = await fetch(API_BASE + '/api/secrets/audit?limit=80');
    records = (await res.json()).records || [];
  } catch (e) {
    box.innerHTML = `<div class="process-empty">Could not read the log: ${esc(e.message)}</div>`;
    return;
  }
  if (!records.length) {
    box.innerHTML = '<div class="process-empty">No access recorded yet.</div>';
    return;
  }
  box.innerHTML = `
    <div style="font-size:11px;color:var(--text-faint);margin-bottom:6px">
      Every read, change and refusal. Values are never recorded.
    </div>
    <table class="process-table">
      <thead><tr><th>When</th><th>Event</th><th>Secret</th><th>Used by</th><th>Why refused</th></tr></thead>
      <tbody>${records.map(r => `
        <tr>
          <td style="white-space:nowrap">${esc(_secTimeAgo(r.ts))}</td>
          <td>${esc(r.event || '')}${r.unattended ? ' <span style="color:var(--text-faint)">(unattended)</span>' : ''}</td>
          <td><code style="font-size:11px">${esc(r.name || '')}</code></td>
          <td>${esc(r.consumer || '—')}${r.project ? ' / ' + esc(r.project) : ''}</td>
          <td style="color:var(--text-faint)">${esc(r.reason || '')}</td>
        </tr>`).join('')}</tbody>
    </table>`;
}

// ── Editor ───────────────────────────────────────────────────────────────────

// The entry types, the name rule and the field markup live in static/js/secret-form.js
// (window.SecretForm), shared with the Connections Add service flow. This panel keeps
// the editor modal, the save logic and the vault-lock UI.
//
// A Desk generation engine's credential form spec (GET /api/desk/engines →
// `credential`): its entry_type (which locks the type), its own labels, a hint
// and the vendor URL. Metadata only; the human still types the value and
// submits through the passcode-gated save below. `preset` is that spec while the
// open editor is locked to an engine.
const _secState = { type: 'login', preset: null, typeBefore: 'login', isNew: true };
let _secPresetsByName = null;

function _secLoadPresets() {
  if (_secPresetsByName) return Promise.resolve(_secPresetsByName);
  const eng = window.DeskV1Engines;
  if (!eng || typeof eng.list !== 'function') return Promise.resolve({});
  return eng.list(null).then((engines) => {
    const m = {};
    (engines || []).forEach((e) => { if (e.credential && e.credential.vault_entry) m[e.credential.vault_entry] = e.credential; });
    return (_secPresetsByName = m);
  }).catch(() => ({}));
}

function _secRender() { window.SecretForm.render('sec', _secState); }
function _secApplyPreset(preset) { window.SecretForm.applyPreset('sec', _secState, preset); }
function _secSetType(type) { window.SecretForm.setType('sec', _secState, type); }
function _secToggleReveal() { window.SecretForm.toggleReveal('sec'); }
function _secScopeChanged() { window.SecretForm.scopeChanged('sec'); }

// `preset`: a credential spec from an engine row. `preset.create` opens an Add
// with `name` prefilled (the entry does not exist yet); without it a given
// name is an Edit, as ever. A plain Add also picks a preset up by the name typed.
async function openSecretEditor(name, preset) {
  const isNew = !name || !!(preset && preset.create);
  const modalId = '__secret-edit';
  if (openModals.has(modalId)) closeModalById(modalId);
  if (name && !preset) preset = (await _secLoadPresets())[name] || null;
  _secState.isNew = isNew;
  // A prefilled Add (Connect on an engine row) locks the name: it IS the link
  // to the engine, and a typo would store a credential nothing reads.
  const lockName = !isNew || !!(preset && preset.create);

  let existing = null;
  if (!isNew) {
    try {
      const res = await fetch(API_BASE + '/api/secrets');
      existing = ((await res.json()).secrets || []).find(s => s.name === name) || null;
    } catch (e) { /* fall through to a blank form */ }
  }
  // An entry saved before types existed arrives with the server's inference
  // (a username makes it a Login, otherwise an API key); a new one starts as a Login.
  _secState.type = (existing && window.SecretForm.TYPES[existing.entry_type]) ? existing.entry_type : 'login';
  // Preset state outlives the editor it locked; clear it so a stale preset cannot
  // hand its saved "type before" back to this entry in _secApplyPreset.
  _secState.preset = null; _secState.typeBefore = _secState.type;

  const realProjects = (allProjects || []).filter(p => !isIncognitoProject(p));

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 620);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">
        &#x1F510; ${isNew ? 'Add secret' : 'Edit: ' + esc(name)}
      </span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="modal-scroll-body" style="padding:4px 24px 20px 28px;display:flex;flex-direction:column;gap:14px">
      ${window.SecretForm.fieldsHtml({
        p: 'sec', isNew, name, lockName, existing,
        projects: realProjects.map(p => ({ id: p.id, name: p.name })),
        handlers: { type: (t) => `_secSetType('${t}')`, reveal: '_secToggleReveal()', scope: '_secScopeChanged()' },
      })}
    </div>
    <!-- Pinned OUTSIDE the scroll body: the outcome line and the Save button
         stay on screen however long the form is (a status line at the foot of
         the scrolled form sat below the fold, so a failed save looked like
         nothing happening). -->
    <div style="flex-shrink:0;padding:10px 24px 14px 28px;border-top:1px solid var(--border);
                display:flex;flex-direction:column;gap:8px">
      <div id="sec-status" role="alert" aria-live="polite" style="font-size:12px;line-height:1.45;
           color:var(--text-faint);min-height:16px;overflow-wrap:anywhere"></div>
      <div style="display:flex;gap:8px;justify-content:flex-end">
        <button class="btn-secondary" onclick="closeModalById('${modalId}')">Cancel</button>
        <button class="btn-add" id="sec-save" onclick="saveSecret('${modalId}', ${isNew})">
          ${isNew ? 'Save secret' : 'Save changes'}
        </button>
      </div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  _secApplyPreset(preset);
  if (isNew && !lockName) {
    // A plain Add: typing a known engine's vault name switches to its labels.
    const nameEl = document.getElementById('sec-name');
    const retarget = async () => {
      const found = (await _secLoadPresets())[nameEl.value.trim()] || null;
      if (found !== _secState.preset) _secApplyPreset(found); else _secRender();   // re-render: {{user:NAME}} help follows the name
    };
    nameEl.addEventListener('input', retarget);
    nameEl.addEventListener('input', () => window.SecretForm.checkName('sec'));
    _secLoadPresets();
  }
  const first = document.getElementById(isNew && !lockName ? 'sec-name' : 'sec-value');
  if (first) first.focus();
}

async function saveSecret(modalId, isNew) {
  const status = document.getElementById('sec-status');
  const btn = document.getElementById('sec-save');
  const nameEl = document.getElementById('sec-name');
  const valueEl = document.getElementById('sec-value');
  const name = (nameEl?.value || '').trim();
  const value = valueEl?.value || '';
  const scopeIsProject = document.querySelector('input[name="sec-scope"]:checked')?.value === 'project';
  const scope = scopeIsProject ? (document.getElementById('sec-project')?.value || '') : 'global';

  // A failure has to land where the eye already is: the pinned footer beside Save,
  // boxed and scrolled into view, plus a toast that outlives a closed modal.
  const fail = (msg) => {
    if (status) {
      status.textContent = msg;
      status.style.cssText += ';color:var(--danger,#c0553f);padding:6px 10px;border:1px solid var(--danger,#c0553f);border-radius:4px';
      status.scrollIntoView({ block: 'nearest' });
    }
    showToast('Not saved: ' + msg, 8000);
  };
  // A Google Authenticator export names its own accounts, so the name field is
  // not required (and would be meaningless) for that path. Only a Login has 2FA.
  const isBulkImport = _secState.type === 'login' && /^otpauth-migration:\/\//i.test(value.trim());
  if (!name && !isBulkImport) return fail('Give it a name.');
  // Checked BEFORE the passcode prompt: a bad name used to cost a passcode entry
  // and a server 400 that nobody saw.
  if (!isBulkImport && !nameEl?.readOnly) {
    const problem = window.SecretForm.checkName('sec');
    if (problem) { nameEl.focus(); return fail(problem); }
  }
  if (isNew && !value) return fail(_secState.preset ? `Paste the ${_secState.preset.secret_label}.` : 'Paste the value you want stored.');
  if (scopeIsProject && !scope) return fail('Pick a project, or choose “Every project”.');
  // A type with no username slot (API key, Token, and so the Gemini/OpenAI
  // presets) stores none, whatever a previous type left typed in the hidden input.
  const T = window.SecretForm.TYPES[_secState.type] || window.SecretForm.TYPES.login;
  const username = !T.user ? '' : (document.getElementById('sec-user')?.value || '').trim();
  if (_secState.preset && _secState.preset.username_required && !username) return fail(`Enter the ${_secState.preset.username_label}.`);
  if (!_secState.preset && T.user && T.user.required && !username) return fail(`Enter the ${T.user.label}.`);

  const body = {
    name,
    username,
    entry_type: _secState.type,
    description: document.getElementById('sec-desc')?.value || '',
    scope,
    allow_unattended: !!document.getElementById('sec-unattended')?.checked,
  };
  // On edit, an empty value means "keep the current one" — the PATCH route
  // re-seals the existing value rather than us ever holding it here.
  if (value) body.value = value;

  if (btn) { btn.disabled = true; btn.textContent = 'Saving…'; }
  if (status) { status.textContent = ''; status.style.cssText += ';color:var(--text-faint);padding:0;border:0'; }
  try {
    // A Google Authenticator export carries MANY accounts, so it can't go
    // through the single-secret path — route it to the importer instead of
    // storing the whole payload as one useless blob.
    if (isBulkImport) {
      // MC-995: bulk-importing TOTP secrets is human-only-gated server-side.
      const result = await humanProofFetch(API_BASE + '/api/secrets/import-authenticator', {
        method: 'POST',
        body: JSON.stringify({
          uri: value.trim(), commit: true, scope,
          allow_unattended: body.allow_unattended,
        }),
      }, {
        title: 'Import authenticator export',
        description: 'Re-enter your dashboard passcode to import these accounts.',
      });
      if (result === null) { if (btn) { btn.disabled = false; btn.textContent = 'Save secret'; } return; }
      const d = result.body;
      if (!result.ok) throw new Error(d.error || `HTTP ${result.status}`);
      if (valueEl) valueEl.value = '';
      closeModalById(modalId);
      showToast(`Imported ${d.imported.length} account(s) from Google Authenticator`);
      refreshSecretsList();
      return;
    }
    // MC-995: creating/editing a secret is human-only-gated server-side.
    const result = isNew
      ? await humanProofFetch(API_BASE + '/api/secrets', {
          method: 'POST',
          body: JSON.stringify({ ...body, value }),
        }, { title: 'Save secret', description: `Re-enter your dashboard passcode to store "${name}".` })
      : await humanProofFetch(API_BASE + '/api/secrets/' + encodeURIComponent(name), {
          method: 'PATCH',
          body: JSON.stringify(body),
        }, { title: 'Save secret', description: `Re-enter your dashboard passcode to update "${name}".` });
    if (result === null) { if (btn) { btn.disabled = false; btn.textContent = isNew ? 'Save secret' : 'Save changes'; } return; }
    const data = result.body;
    if (!result.ok) throw new Error(data.error || `HTTP ${result.status}`);
    // A server older than the username field accepts the POST and drops it —
    // Flask ignores unknown JSON keys, so the save "succeeds" and the credential
    // is stored half-complete. Only the echoed record can tell us, and silence
    // here is what sent an agent looking for a username that was never written.
    if (body.username && !data.username) {
      if (valueEl) valueEl.value = '';
      refreshSecretsList();   // the rest of the entry did save
      return fail('Saved, but this server build does not store usernames yet — '
                  + 'restart Clayrune, then re-enter the username.');
    }
    // Same trap for the type: an older server ignores `entry_type`, the save
    // "succeeds", and the form's choice silently never took.
    if (!data.entry_type) {
      if (valueEl) valueEl.value = '';
      refreshSecretsList();
      return fail('Saved, but this server build does not store entry types yet — '
                  + 'restart Clayrune, then re-save to set the type.');
    }
    // Drop the plaintext from the DOM the moment it is no longer needed.
    if (valueEl) valueEl.value = '';
    closeModalById(modalId);
    showToast(isNew ? `Saved — reference it as {{secret:${name}}}` : 'Saved ' + name);
    refreshSecretsList();
  } catch (e) {
    fail(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = isNew ? 'Save secret' : 'Save changes'; }
  }
}


// ── Interop: re-expose for inline / generated-on*= callers. All runtime-only.
//    `openSecretsVault` ← sidebarNav('secrets'); the rest ← the modals' own
//    generated on*= handlers. `_secTimeAgo` stays module-private.
window.openSecretsVault = openSecretsVault;
window.refreshSecretsList = refreshSecretsList;
window.openSecretEditor = openSecretEditor;
window.saveSecret = saveSecret;
window.deleteSecret = deleteSecret;
window.copySecretPlaceholder = copySecretPlaceholder;
window.toggleSecretsAudit = toggleSecretsAudit;
window._secToggleReveal = _secToggleReveal;
window._secScopeChanged = _secScopeChanged;
window._secSetType = _secSetType;
// This module's `<script type="module">` tag means none of its top-level
// functions are implicitly global — every onclick="fnName()" below needs an
// explicit window.fnName export or it ReferenceErrors and does nothing.
// These six (plus the recovery-key toggle) were missing entirely: the whole
// passphrase-lock UI (set/unlock/change/lock) was unclickable in a real
// browser. Found 2026-09-24 building the vault-lock passcode smoke test.
window.submitVaultUnlock = submitVaultUnlock;
window.openVaultSetPassphrase = openVaultSetPassphrase;
window.submitVaultSetPassphrase = submitVaultSetPassphrase;
window.openVaultChangePassphrase = openVaultChangePassphrase;
window.submitVaultChangePassphrase = submitVaultChangePassphrase;
window.openVaultLockNow = openVaultLockNow;
window.submitVaultLockNow = submitVaultLockNow;
window._secToggleUseRecoveryKey = _secToggleUseRecoveryKey;
window.openVaultRetireLegacy = openVaultRetireLegacy;
window.submitVaultRetireLegacy = submitVaultRetireLegacy;
