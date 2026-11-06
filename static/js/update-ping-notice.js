// Daily update-check ping: the disclosure text, the one-time notice for installs
// that predate it, and the "update available" notice (mc/update_ping.py).
//
// The ping is ON by default but sends nothing until the user has SEEN this text
// (server flag update_ping_disclosed). Two places show it: the first-run step
// (first-run.js imports UPDATE_PING_DISCLOSURE and posts /choice when the user
// leaves the step) and the one-time notice below for installs that already
// finished setup. Dismissing the notice keeps the ping ON; the toggle on it
// turns it off. Both write through POST /api/update-ping/*, not PUT /api/config:
// a fresh install has no passcode for a yes/no to hang off (see
// mc/blueprints/update_ping_routes.py).

export const UPDATE_PING_DISCLOSURE =
  'Once a day Clayrune sends one small message to clayrune.io: a random install ID, ' +
  'your Clayrune version, and your operating system (windows, darwin or linux). ' +
  'Nothing else. No name, no computer name, no projects, no files. ' +
  'It is how we count installs and tell you when an update is out.';

export const UPDATE_PING_HOWTO_OFF =
  'Turn it off any time in Settings → System, or set DO_NOT_TRACK=1 (or CLAYRUNE_NO_PING=1) in the environment.';

async function _pingStatus() {
  try {
    const r = await fetch(API_BASE + '/api/update-ping/status');
    return r.ok ? await r.json() : null;
  } catch (_) { return null; }
}

export async function updatePingChoose(enabled) {
  try {
    const r = await fetch(API_BASE + '/api/update-ping/choice', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: !!enabled }),
    });
    if (r.ok && typeof _globalConfig === 'object' && _globalConfig) {
      _globalConfig.update_ping_enabled = !!enabled;
    }
    return r.ok;
  } catch (_) { return false; }
}

function _closeNotice() {
  const el = document.getElementById('upn-card');
  if (el) el.remove();
}

async function _showDisclosureNotice(st) {
  if (document.getElementById('upn-card')) return;
  const card = document.createElement('div');
  card.id = 'upn-card';
  card.className = 'upn-card';
  card.setAttribute('role', 'dialog');
  card.setAttribute('aria-label', 'Update check');
  card.innerHTML = `
    <div class="upn-title">Update check</div>
    <div class="upn-body">${esc(UPDATE_PING_DISCLOSURE)}</div>
    <div class="upn-row">
      <span class="upn-label">Send it</span>
      <div class="settings-toggle ${st.enabled ? 'on' : ''}" id="upn-toggle" role="switch"
           aria-checked="${st.enabled ? 'true' : 'false'}" tabindex="0"></div>
    </div>
    <div class="upn-body upn-faint">${esc(UPDATE_PING_HOWTO_OFF)}</div>
    <div class="upn-actions"><button class="toast-btn primary" id="upn-dismiss">Got it</button></div>`;
  document.body.appendChild(card);
  const toggle = card.querySelector('#upn-toggle');
  const flip = async () => {
    const on = !toggle.classList.contains('on');
    toggle.classList.toggle('on', on);
    toggle.setAttribute('aria-checked', on ? 'true' : 'false');
    if (!(await updatePingChoose(on))) {
      toggle.classList.toggle('on', !on);
      toggle.setAttribute('aria-checked', !on ? 'true' : 'false');
      if (typeof showToast === 'function') showToast('Could not save that choice. Try Settings → System.', 5000);
    }
  };
  toggle.addEventListener('click', flip);
  toggle.addEventListener('keydown', e => { if (e.key === ' ' || e.key === 'Enter') { e.preventDefault(); flip(); } });
  card.querySelector('#upn-dismiss').addEventListener('click', _closeNotice);
  // "First ping only after it was shown": record the display now. Dismissing
  // changes nothing (the ping stays ON); the toggle above is the way to opt out.
  try { await fetch(API_BASE + '/api/update-ping/seen', { method: 'POST' }); } catch (_) {}
}

function _showUpdateAvailable(st) {
  if (typeof showActionToast !== 'function') return;
  if (localStorage.getItem('mc_ping_update_dismissed_for') === st.latest_version) return;
  showActionToast(
    `<strong>Clayrune ${esc(st.latest_version)} is available</strong><br>` +
    `<span style="color:var(--text-faint);font-size:12px">You are on ${esc(st.current_version || 'an older build')}.</span>`,
    [
      { label: 'Dismiss', onclick: () => localStorage.setItem('mc_ping_update_dismissed_for', st.latest_version) },
      { label: 'Update', primary: true, onclick: () => { try { sidebarNav('settings'); } catch (_) {} } },
    ],
    { key: 'update-ping-latest', dismissOnAction: true });
}

// Boot hook (index.html). Waits for the first-run wizard to have its chance to
// open, so a brand-new install sees the disclosure as a setup step, not twice.
export async function checkUpdatePingNotice() {
  const st = await _pingStatus();
  if (!st || st.env_disabled) return;       // env hard-off: nothing is ever sent, nothing to disclose
  if (!st.disclosed) {
    if (document.getElementById('setup-overlay')) return;   // first-run owns the disclosure
    await _showDisclosureNotice(st);
    return;
  }
  if (st.enabled && st.update_available && st.latest_version) {
    // The git/Mac update toast covers the same release; do not stack two.
    try {
      const u = await (await fetch(API_BASE + '/api/system/update/cached')).json();
      if (u && u.update_available) return;
    } catch (_) {}
    _showUpdateAvailable(st);
  }
}

// Settings -> System row: a note when the environment forces the ping off, so a
// toggle that reads ON is not a lie.
export async function refreshUpdatePingSection() {
  const note = document.getElementById('update-ping-env-note');
  const toggle = document.getElementById('update-ping-toggle');
  if (!note) return;
  const st = await _pingStatus();
  if (st && st.env_disabled) {
    note.style.display = '';
    note.textContent = 'Off: DO_NOT_TRACK or CLAYRUNE_NO_PING is set in the environment, which overrides this switch.';
    if (toggle) toggle.classList.remove('on');
  }
}

window.checkUpdatePingNotice = checkUpdatePingNotice;     // interop: boot continuation (index.html)
window.refreshUpdatePingSection = refreshUpdatePingSection; // interop: Settings render (settings-drill.js)
