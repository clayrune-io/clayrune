// ── Settings > Connectivity > Passkeys (docs/PASSKEYS_SPEC.md, slices 1 and 2a) ─
// Enrol, list and revoke a passkey. DISABLED FOR ACTIONS: an enrolled passkey
// manages the registry and nothing else. No gate reads it yet, so every
// human-only action still asks for the dashboard passcode exactly as before.
//
// Enrollment and revocation work only from the host's own browser at
// http://localhost:<port>; the server refuses LAN, tunnel and forged-header
// callers (mc/passkeys/host_check.py) and this panel just reports what the
// server said. The retyped passcode goes through humanProofFetch, like every
// other human-only click; this file never sees or stores it.
//
// Once one passkey is active (`needs_passkey_to_change`), adding or revoking one
// takes a passkey assertion instead of the passcode (passkeys-webauthn.js), and a
// refused assertion is shown as refused: the passcode is never offered as a way
// round it. The passcode keeps two jobs, lost-all recovery and resetting a
// refused registry, which live in passkeys-recovery.js with the copy for every
// state in which passkeys are unavailable.

let _pkState = null;      // last GET /api/passkeys
let _pkMsg = '';          // one status line under the heading
let _pkBusy = false;
let _pkStyled = false;

function _pkStyle() {
  if (_pkStyled) return;
  _pkStyled = true;
  const s = document.createElement('style');
  s.textContent = `
    .pk-row { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; padding: 8px 0;
      border-top: 1px solid var(--border); }
    .pk-row:first-child { border-top: 0; }
    .pk-name { font-size: 13px; font-weight: 600; color: var(--text); }
    .pk-meta { font-size: 12px; color: var(--text-dim); line-height: 1.5; }
    .pk-revoked .pk-name { text-decoration: line-through; color: var(--text-dim); }
    .pk-err { font-size: 12px; color: var(--red, #c0392b); }
    .pk-foot { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin-top: 8px; }
    .pk-secondary { margin-top: 12px; padding-top: 8px; border-top: 1px solid var(--border); }
    .pk-foot input[type=text] { padding: 6px 8px; font-size: 12px; background: var(--surface2);
      border: 1px solid var(--border); border-radius: 4px; color: var(--text); min-width: 180px; }
  `;
  document.head.appendChild(s);
}

function passkeysSettingsHTML() {
  return `
    <div class="settings-section" id="passkeys-section">
      <div class="settings-section-title">Passkeys</div>
      <div class="settings-hint" style="margin-bottom:10px;line-height:1.45">
        Register a passkey from this computer's own browser. Once you have one, adding or
        revoking a passkey asks for a passkey; every other human-only action still asks
        for your dashboard passcode.
      </div>
      <div id="passkeys-host"><div class="settings-hint">Loading…</div></div>
    </div>`;
}

async function _pkJson(method, url, body) {
  const res = await fetch(API_BASE + url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const out = await res.json().catch(() => ({}));
  return { ok: res.ok, status: res.status, body: out };
}

function _pkWhy(r) {
  return window.PasskeysRecovery.why(r);
}

function _pkFmt(ts) {
  return ts ? String(ts).slice(0, 10) : '';
}

// The card shown instead of a passkey list while the server refuses the registry
// or the vault is not usable. `extra` is a state from PasskeysRecovery.describe.
function _pkStateHTML(extra) {
  const bits = [`<div class="pk-meta" id="passkeys-state" data-pk-state="${esc(extra.code)}">${esc(extra.text)}</div>`];
  const btns = [];
  if (extra.action) btns.push(`<button type="button" class="btn-add" data-pk-act="${esc(extra.action.act)}">${esc(extra.action.label)}</button>`);
  if (extra.resettable) btns.push('<button type="button" data-pk-act="reset">Reset passkeys</button>');
  if (btns.length) bits.push(`<div class="pk-foot">${btns.join('')}</div>`);
  if (extra.resettable) bits.push('<div class="pk-meta">Resetting works from this computer\'s own browser, with your dashboard passcode.</div>');
  return bits.join('');
}

function _pkRender() {
  const host = document.getElementById('passkeys-host');
  if (!host) return;
  _pkStyle();
  const st = _pkState;
  if (!st) { host.innerHTML = '<div class="settings-hint">Loading…</div>'; return; }
  const parts = [];
  if (_pkMsg) parts.push(`<div class="pk-meta" id="passkeys-msg">${esc(_pkMsg)}</div>`);
  if (st.refused) {
    parts.push(_pkStateHTML(st.refused));
    host.innerHTML = parts.join('');
    host.querySelectorAll('[data-pk-act]').forEach((b) => b.addEventListener('click', () => _pkAct(b)));
    return;
  }
  if (!st.available) {
    parts.push('<div class="pk-meta">The passkey library is not installed on this server, so passkeys cannot be registered.</div>');
  }
  const creds = st.credentials || [];
  if (creds.length) {
    parts.push(creds.map((c) => {
      const gone = !!c.revoked_at;
      return `<div class="pk-row ${gone ? 'pk-revoked' : ''}" data-pk-id="${esc(c.id)}">
        <span class="pk-name">${esc(c.label || 'Passkey')}</span>
        <span class="pk-meta">added ${esc(_pkFmt(c.created_at))}${c.last_used_at ? ' · last used ' + esc(_pkFmt(c.last_used_at)) : ''}${gone ? ' · revoked ' + esc(_pkFmt(c.revoked_at)) : ''}</span>
        ${gone ? '' : `<button type="button" data-pk-act="revoke" data-pk-id="${esc(c.id)}">Revoke</button>`}
      </div>`;
    }).join(''));
  } else if (st.available) {
    parts.push('<div class="pk-meta">No passkeys registered.</div>');
  }
  if (st.available) {
    const vaultState = window.PasskeysRecovery.describe(st.enroll_blocked_reason);
    if (st.can_enroll_here) {
      const full = (st.active_count || 0) >= (st.max_active || 0);
      parts.push(`<div class="pk-foot">
        <input type="text" id="passkeys-label" maxlength="64" placeholder="Name (e.g. This laptop)" autocomplete="off">
        <button type="button" class="btn-add" data-pk-act="enroll" ${(_pkBusy || full) ? 'disabled' : ''}>Add a passkey</button>
        ${full ? '<span class="pk-meta">The limit of ' + esc(st.max_active) + ' active passkeys is reached.</span>' : ''}
      </div>`);
      if (st.needs_passkey_to_change) {
        parts.push('<div class="pk-meta">Adding or revoking a passkey asks you to approve with one you already have.</div>');
      }
      if ((st.active_count || 0) > 0) {
        parts.push(`<div class="pk-secondary">
          <div class="pk-meta">Lost every passkey? Revoke them all with your dashboard passcode and start again.</div>
          <div class="pk-foot"><button type="button" data-pk-act="recover">Revoke all passkeys</button></div>
        </div>`);
      }
    } else if (vaultState) {
      parts.push(_pkStateHTML(vaultState));
    } else {
      parts.push(`<div class="pk-meta" id="passkeys-blocked">${esc(st.enroll_blocked_message || 'Passkeys can only be added or revoked from the host computer.')}</div>`);
    }
  }
  host.innerHTML = parts.join('');
  host.querySelectorAll('[data-pk-act]').forEach((b) => b.addEventListener('click', () => _pkAct(b)));
}

async function _pkEnroll() {
  const W = window.PasskeysWebauthn;
  const labelEl = document.getElementById('passkeys-label');
  const label = labelEl ? labelEl.value.trim() : '';
  if (!(navigator.credentials && window.PublicKeyCredential)) {
    _pkMsg = 'This browser does not support passkeys.';
    return;
  }
  let started;
  if (_pkState && _pkState.needs_passkey_to_change) {
    // A passkey exists: approve the add with one of them, not the passcode.
    const a = await W.assertFor('add');
    if (!a.proof) { _pkMsg = a.response ? _pkWhy(a.response) : a.error; return; }
    started = await _pkJson('POST', '/api/passkeys/register/options',
      Object.assign({ proof: a.proof }, label ? { label } : {}));
  } else {
    started = await window.humanProofFetch(API_BASE + '/api/passkeys/register/options',
      { method: 'POST', body: JSON.stringify(label ? { label } : {}) },
      { title: 'Add a passkey', description: 'Re-enter your dashboard passcode to add a passkey.' });
    if (started === null) return;
  }
  if (!started.ok) { _pkMsg = _pkWhy(started); return; }
  let cred;
  try {
    cred = await navigator.credentials.create({ publicKey: W.creationOptions(started.body.options) });
  } catch (e) {
    _pkMsg = e && e.name === 'NotAllowedError'
      ? 'The passkey prompt was cancelled or timed out. Nothing was saved.'
      : 'The browser could not create a passkey: ' + ((e && e.message) || e);
    return;
  }
  const fin = await _pkJson('POST', '/api/passkeys/register/finish',
    { ceremony_id: started.body.ceremony_id, credential: W.creationJSON(cred) });
  _pkMsg = fin.ok ? 'Passkey added.' : 'The server refused the passkey: ' + _pkWhy(fin);
}

async function _pkRevoke(id) {
  const last = _pkState && (_pkState.active_count || 0) <= 1;
  if (!window.confirm('Revoke this passkey? It stops counting as registered right away.'
    + (last ? ' It is your last one, so the dashboard passcode applies again afterwards.' : ''))) return;
  let res;
  if (_pkState && _pkState.needs_passkey_to_change) {
    const a = await window.PasskeysWebauthn.assertFor('revoke', id);
    if (!a.proof) { _pkMsg = a.response ? _pkWhy(a.response) : a.error; return; }
    res = await _pkJson('DELETE', '/api/passkeys/' + encodeURIComponent(id), { proof: a.proof });
  } else {
    res = await window.humanProofFetch(API_BASE + '/api/passkeys/' + encodeURIComponent(id),
      { method: 'DELETE', body: JSON.stringify({}) },
      { title: 'Revoke passkey', description: 'Re-enter your dashboard passcode to revoke this passkey.' });
    if (res === null) return;
  }
  _pkMsg = res.ok ? 'Passkey revoked.' : _pkWhy(res);
}

async function _pkAct(btn) {
  if (_pkBusy) return;
  const act = btn.dataset.pkAct;
  if (act === 'vault-open' || act === 'vault-set') { window.PasskeysRecovery.openVault(act); return; }
  _pkBusy = true;
  _pkMsg = '';
  try {
    if (act === 'enroll') await _pkEnroll();
    else if (act === 'revoke') await _pkRevoke(btn.dataset.pkId);
    else if (act === 'recover') _pkMsg = (await window.PasskeysRecovery.recover()) || '';
    else if (act === 'reset') _pkMsg = (await window.PasskeysRecovery.reset()) || '';
  } catch (e) {
    _pkMsg = (e && e.message) || 'Something went wrong.';
  } finally {
    _pkBusy = false;
  }
  await refreshPasskeysSection();
}

async function refreshPasskeysSection() {
  if (!document.getElementById('passkeys-host')) return;
  const r = await _pkJson('GET', '/api/passkeys').catch((e) => ({ ok: false, status: 0, body: { message: e.message } }));
  if (!r.ok) {
    const refused = window.PasskeysRecovery.describe(r.body && r.body.error);
    if (refused) {
      _pkState = { refused, available: true };
      _pkRender();
      return;
    }
    const host = document.getElementById('passkeys-host');
    if (host) host.innerHTML = `<div class="pk-err">Could not load passkeys: ${esc(_pkWhy(r))}</div>`;
    return;
  }
  _pkState = r.body;
  _pkRender();
}

window.passkeysSettingsHTML = passkeysSettingsHTML;     // interop: settings template (_renderSettings)
window.refreshPasskeysSection = refreshPasskeysSection; // interop: settings hydration (_renderSettings)
