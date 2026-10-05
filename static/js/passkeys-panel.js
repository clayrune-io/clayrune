// ── Settings > Connectivity > Passkeys (docs/PASSKEYS_SPEC.md, slice 1) ──────
// Enrol, list and revoke a passkey. DISABLED FOR ACTIONS: an enrolled passkey
// is a stored public key and nothing more. No gate reads it yet, so every
// human-only action still asks for the dashboard passcode exactly as before.
//
// Enrollment and revocation work only from the host's own browser at
// http://localhost:<port>; the server refuses LAN, tunnel and forged-header
// callers (mc/passkeys/host_check.py) and this panel just reports what the
// server said. The retyped passcode goes through humanProofFetch, like every
// other human-only click; this file never sees or stores it.

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
        Register a passkey from this computer's own browser. Passkeys are stored but not
        used yet: every human-only action still asks for your dashboard passcode.
      </div>
      <div id="passkeys-host"><div class="settings-hint">Loading…</div></div>
    </div>`;
}

function _pkB64urlToBuf(s) {
  const b = atob(String(s).replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((String(s).length + 3) % 4));
  const out = new Uint8Array(b.length);
  for (let i = 0; i < b.length; i++) out[i] = b.charCodeAt(i);
  return out.buffer;
}

function _pkBufToB64url(buf) {
  const bytes = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

// Server options (base64url strings) -> what navigator.credentials.create wants.
function _pkCreationOptions(o) {
  const pk = Object.assign({}, o);
  pk.challenge = _pkB64urlToBuf(o.challenge);
  pk.user = Object.assign({}, o.user, { id: _pkB64urlToBuf(o.user.id) });
  pk.excludeCredentials = (o.excludeCredentials || []).map((c) => Object.assign({}, c, { id: _pkB64urlToBuf(c.id) }));
  return pk;
}

// A PublicKeyCredential -> the JSON shape the server's finish route takes.
function _pkCredentialJSON(cred) {
  const r = cred.response;
  return {
    id: cred.id,
    rawId: _pkBufToB64url(cred.rawId),
    type: cred.type,
    authenticatorAttachment: cred.authenticatorAttachment || undefined,
    clientExtensionResults: cred.getClientExtensionResults ? cred.getClientExtensionResults() : {},
    response: {
      clientDataJSON: _pkBufToB64url(r.clientDataJSON),
      attestationObject: _pkBufToB64url(r.attestationObject),
      transports: typeof r.getTransports === 'function' ? r.getTransports() : [],
    },
  };
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
  const b = (r && r.body) || {};
  return b.message || b.error || ('HTTP ' + (r ? r.status : '?'));
}

function _pkFmt(ts) {
  return ts ? String(ts).slice(0, 10) : '';
}

function _pkRender() {
  const host = document.getElementById('passkeys-host');
  if (!host) return;
  _pkStyle();
  const st = _pkState;
  if (!st) { host.innerHTML = '<div class="settings-hint">Loading…</div>'; return; }
  const parts = [];
  if (_pkMsg) parts.push(`<div class="pk-meta" id="passkeys-msg">${esc(_pkMsg)}</div>`);
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
    if (st.can_enroll_here) {
      const full = (st.active_count || 0) >= (st.max_active || 0);
      parts.push(`<div class="pk-foot">
        <input type="text" id="passkeys-label" maxlength="64" placeholder="Name (e.g. This laptop)" autocomplete="off">
        <button type="button" class="btn-add" data-pk-act="enroll" ${(_pkBusy || full) ? 'disabled' : ''}>Add a passkey</button>
        ${full ? '<span class="pk-meta">The limit of ' + esc(st.max_active) + ' active passkeys is reached.</span>' : ''}
      </div>`);
    } else {
      parts.push(`<div class="pk-meta" id="passkeys-blocked">${esc(st.enroll_blocked_message || 'Passkeys can only be added or revoked from the host computer.')}</div>`);
    }
  }
  host.innerHTML = parts.join('');
  host.querySelectorAll('[data-pk-act]').forEach((b) => b.addEventListener('click', () => _pkAct(b)));
}

async function _pkEnroll() {
  const labelEl = document.getElementById('passkeys-label');
  const label = labelEl ? labelEl.value.trim() : '';
  if (!(navigator.credentials && window.PublicKeyCredential)) {
    _pkMsg = 'This browser does not support passkeys.';
    return;
  }
  const started = await window.humanProofFetch(API_BASE + '/api/passkeys/register/options',
    { method: 'POST', body: JSON.stringify(label ? { label } : {}) },
    { title: 'Add a passkey', description: 'Re-enter your dashboard passcode to add a passkey.' });
  if (started === null) return;
  if (!started.ok) { _pkMsg = _pkWhy(started); return; }
  let cred;
  try {
    cred = await navigator.credentials.create({ publicKey: _pkCreationOptions(started.body.options) });
  } catch (e) {
    _pkMsg = e && e.name === 'NotAllowedError'
      ? 'The passkey prompt was cancelled or timed out. Nothing was saved.'
      : 'The browser could not create a passkey: ' + ((e && e.message) || e);
    return;
  }
  const fin = await _pkJson('POST', '/api/passkeys/register/finish',
    { ceremony_id: started.body.ceremony_id, credential: _pkCredentialJSON(cred) });
  _pkMsg = fin.ok ? 'Passkey added.' : 'The server refused the passkey: ' + _pkWhy(fin);
}

async function _pkRevoke(id) {
  if (!window.confirm('Revoke this passkey? It stops counting as registered right away.')) return;
  const res = await window.humanProofFetch(API_BASE + '/api/passkeys/' + encodeURIComponent(id),
    { method: 'DELETE', body: JSON.stringify({}) },
    { title: 'Revoke passkey', description: 'Re-enter your dashboard passcode to revoke this passkey.' });
  if (res === null) return;
  _pkMsg = res.ok ? 'Passkey revoked.' : _pkWhy(res);
}

async function _pkAct(btn) {
  if (_pkBusy) return;
  _pkBusy = true;
  _pkMsg = '';
  try {
    if (btn.dataset.pkAct === 'enroll') await _pkEnroll();
    else if (btn.dataset.pkAct === 'revoke') await _pkRevoke(btn.dataset.pkId);
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
    const host = document.getElementById('passkeys-host');
    if (host) host.innerHTML = `<div class="pk-err">Could not load passkeys: ${esc(_pkWhy(r))}</div>`;
    return;
  }
  _pkState = r.body;
  _pkRender();
}

window.passkeysSettingsHTML = passkeysSettingsHTML;     // interop: settings template (_renderSettings)
window.refreshPasskeysSection = refreshPasskeysSection; // interop: settings hydration (_renderSettings)
