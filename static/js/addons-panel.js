// ── Settings > Add-ons (MC-1022, docs/ADDON_INSTALLS_SPEC.md §4-5) ───────────
// Software an agent may ASK for and only a human may install. Three lists:
// waiting for you (the approval cards), installed (with Remove), available.
//
// Every state-changing click goes through humanProofFetch -> the dashboard
// passcode, retyped each time; the server enforces it (addon_routes.py), this
// module only asks. The one ungated write it makes is POST /api/addons/requests
// (filing a card to adopt a system copy), which installs nothing.
//
// Everything in a card's facts (name, licence, sizes, source host, adoption
// path/version/hash) comes from the catalogue or the server's own hashing. The
// agent's own words appear only as quoted text under "The agent says".

const _ADDON_POLL_MS = 1500;
let _addonState = null;            // last GET /api/addons
let _addonTimer = null;
let _addonBusy = '';               // a button-level message while a request is in flight
let _addonStyled = false;

function _addonStyle() {
  if (_addonStyled) return;
  _addonStyled = true;
  const s = document.createElement('style');
  s.textContent = `
    .addon-card { background: var(--surface3); border: 1px solid var(--border); border-radius: 8px;
      padding: 12px 14px; margin: 8px 0; display: flex; flex-direction: column; gap: 8px; }
    .addon-card.waiting { border-color: var(--accent); }
    .addon-head { display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; }
    .addon-name { font-size: 13px; font-weight: 600; color: var(--text); }
    .addon-kicker { font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; color: var(--accent); }
    .addon-meta { font-size: 12px; color: var(--text-dim); line-height: 1.5; }
    .addon-meta code { font-size: 11px; word-break: break-all; }
    .addon-flag { font-size: 12px; color: var(--red, #c0392b); font-weight: 600; }
    .addon-say { font-size: 12px; color: var(--text-dim); border-left: 2px solid var(--border); padding-left: 8px; }
    .addon-err { font-size: 12px; color: var(--red, #c0392b); }
    .addon-foot { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .addon-bar { height: 6px; border-radius: 3px; background: var(--border); overflow: hidden; }
    .addon-bar > span { display: block; height: 100%; background: var(--accent); }
    .addon-status-ok { color: var(--green, #2e8b57); }
    .addon-status-bad { color: var(--red, #c0392b); font-weight: 600; }
    .addon-empty { font-size: 12px; color: var(--text-dim); padding: 6px 0; }
  `;
  document.head.appendChild(s);
}

function _addonBytes(n) {
  if (n == null) return '';
  const mb = n / (1024 * 1024);
  return mb >= 1024 ? (mb / 1024).toFixed(1) + ' GB' : Math.round(mb) + ' MB';
}

async function _addonApi(method, url, body) {
  const res = await fetch(API_BASE + url, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const out = await res.json().catch(() => ({}));
  if (!res.ok) { const e = new Error(out.error || out.message || ('HTTP ' + res.status)); e.status = res.status; throw e; }
  return out;
}

// A human-only POST: the passcode is retyped for each call. Rejects with the server's reason.
async function _addonHumanPost(url, body, proof) {
  if (typeof window.humanProofFetch !== 'function') throw new Error('the passcode prompt is not available');
  const res = await window.humanProofFetch(url.startsWith('http') ? url : API_BASE + url,
    { method: 'POST', body: JSON.stringify(body || {}) }, proof);
  if (res === null) { const e = new Error('the dashboard passcode was not entered, so nothing was sent'); e.cancelled = true; throw e; }
  if (!res.ok) throw new Error((res.body && (res.body.error || res.body.message)) || ('HTTP ' + res.status));
  return res.body;
}

function _addonCardHTML(c, passcodeOk) {
  const a = c.addon || {};
  const adopt = c.kind === 'adopt';
  const lines = [];
  lines.push(`<div class="addon-meta">${esc(a.description || '')}</div>`);
  lines.push(`<div class="addon-meta">Licence: <b>${esc(a.licence || '')}</b>. ${esc(a.licence_line || '')}</div>`);
  if (a.licence_flag) lines.push(`<div class="addon-flag">${esc(a.licence_flag)}</div>`);
  if (adopt) {
    const ad = c.adoption || {};
    lines.push(`<div class="addon-meta">Already on this computer: version ${esc(ad.version || '?')}. ${esc(ad.note || '')}</div>`);
    Object.entries(ad.binaries || {}).forEach(([n, b]) => {
      lines.push(`<div class="addon-meta">${esc(n)}: <code>${esc(b.path)}</code><br>SHA-256 <code>${esc(b.sha256)}</code></div>`);
    });
    lines.push('<div class="addon-meta">Clayrune will run exactly this file and ask again if it ever changes.</div>');
  } else {
    lines.push(`<div class="addon-meta">Downloads ${esc(_addonBytes(a.download_bytes))} from <b>${esc(a.source_host || '')}</b> (${esc(a.source || '')}), uses ${esc(_addonBytes(a.installed_bytes))} on disk. Checked against a fixed SHA-256 before anything is unpacked. Version ${esc(a.version || '')}, source last re-checked ${esc(a.last_verified || '')}.</div>`);
    if (a.trust_note) lines.push(`<div class="addon-meta">${esc(a.trust_note)}</div>`);
  }
  if (c.agent_says) lines.push(`<div class="addon-say">The agent says: “${esc(c.agent_says)}”</div>`);
  const who = c.requested_by || {};
  if (who.project_id) lines.push(`<div class="addon-meta">Asked for by project <b>${esc(who.project_id)}</b>${who.unattended ? ' (unattended run, waiting on you)' : ''}.</div>`);

  let foot = '';
  if (c.state === 'installing') {
    const p = c.progress || {};
    const pct = p.total ? Math.min(100, Math.round(100 * p.done / p.total)) : 0;
    foot = `<div class="addon-meta">${esc(p.phase || 'working')}…</div><div class="addon-bar"><span style="width:${pct}%"></span></div>`;
  } else if (!passcodeOk) {
    foot = `<div class="addon-meta">Set a dashboard passcode first (<a href="#" onclick="drillSettings('connect');return false">Settings &gt; Connectivity &gt; Network access</a>). Nothing can be installed without one.</div>`;
  } else {
    const label = c.state === 'failed' ? 'Retry' : (adopt ? 'Use this copy' : 'Approve and install');
    foot = `<div class="addon-foot">
      <button type="button" class="btn-add" data-addon-act="approve" data-rid="${esc(c.id)}">${label}</button>
      <button type="button" data-addon-act="decline" data-rid="${esc(c.id)}">Decline</button></div>`;
  }
  if (c.state === 'failed' && c.error) lines.push(`<div class="addon-err">${esc(c.error)}</div>`);
  return `<div class="addon-card waiting"><div class="addon-head">
      <span class="addon-kicker">${adopt ? 'Use existing' : 'Install'}</span>
      <span class="addon-name">${esc(a.name || c.addon_id)}</span></div>
    ${lines.join('')}${foot}</div>`;
}

function _addonInstalledHTML(list) {
  if (!list.length) return '<div class="addon-empty">Nothing installed yet.</div>';
  return list.map((i) => {
    const bad = i.status !== 'ok';
    const inUse = (i.in_use_by || []).length ? `In use by ${esc(i.in_use_by.join(', '))}.` : '';
    return `<div class="addon-card"><div class="addon-head"><span class="addon-name">${esc(i.name)}</span>
      <span class="addon-meta">${esc(i.version || '')} · ${i.source === 'system' ? 'adopted from this computer' : 'installed by Clayrune'}</span></div>
      <div class="addon-meta">${esc(i.description || '')}</div>
      <div class="addon-meta">${esc(i.licence || '')} · ${esc(_addonBytes(i.size_on_disk))} · added ${esc((i.installed_at || '').slice(0, 10))}${i.last_verified ? ' · verified ' + esc(String(i.last_verified).slice(0, 10)) : ''}</div>
      <div class="addon-meta">Status: <span class="${bad ? 'addon-status-bad' : 'addon-status-ok'}">${esc(i.status)}</span>${i.status_detail ? ' — ' + esc(i.status_detail) : ''} ${inUse}</div>
      ${i.note ? `<div class="addon-meta">${esc(i.note)}</div>` : ''}
      <div class="addon-foot"><button type="button" data-addon-act="remove" data-id="${esc(i.id)}" data-source="${esc(i.source)}">Remove</button></div></div>`;
  }).join('');
}

function _addonAvailableHTML(list, passcodeOk, platform) {
  if (!list.length) return '<div class="addon-empty">Everything in the catalogue is installed.</div>';
  return list.map((a) => {
    const canDownload = a.has_static_build;
    let actions = '';
    if (!passcodeOk) actions = '<div class="addon-meta">Set a dashboard passcode to install.</div>';
    else {
      if (canDownload) actions += `<button type="button" class="btn-add" data-addon-act="install" data-id="${esc(a.id)}">Install</button>`;
      if (a.can_adopt) actions += `<button type="button" data-addon-act="adopt" data-id="${esc(a.id)}">Use the copy already on this computer</button>`;
    }
    const noBuild = canDownload ? '' : `<div class="addon-meta">No download for this computer (${esc(platform)}). ${esc(a.platform_note || '')} ${a.command_for_user ? 'Run <code>' + esc(a.command_for_user) + '</code> yourself, then use the copy already on this computer.' : ''}</div>`;
    return `<div class="addon-card"><div class="addon-head"><span class="addon-name">${esc(a.name)}</span>
      <span class="addon-meta">${esc(a.version)} · ${esc(a.licence)}</span></div>
      <div class="addon-meta">${esc(a.description)}</div>
      ${a.licence_flag ? `<div class="addon-flag">${esc(a.licence_flag)}</div>` : ''}
      ${canDownload ? `<div class="addon-meta">${esc(_addonBytes(a.download_bytes))} download from ${esc(a.source_host)}.</div>` : ''}
      ${noBuild}<div class="addon-foot">${actions}</div></div>`;
  }).join('');
}

function _addonRender() {
  const host = document.getElementById('addons-host');
  if (!host) return;
  _addonStyle();
  const st = _addonState;
  if (!st) { host.innerHTML = '<div class="addon-empty">Loading…</div>'; return; }
  const ok = !!st.passcode_configured;
  host.innerHTML = `
    ${st.catalogue_ok ? '' : `<div class="addon-err">The add-on catalogue could not be read: ${esc(st.catalogue_error)}</div>`}
    ${_addonBusy ? `<div class="addon-meta">${esc(_addonBusy)}</div>` : ''}
    <div class="settings-hint">Waiting for you</div>
    ${st.pending.length ? st.pending.map((c) => _addonCardHTML(c, ok)).join('') : '<div class="addon-empty">No agent is waiting on an add-on.</div>'}
    <div class="settings-hint" style="margin-top:14px">Installed</div>
    ${_addonInstalledHTML(st.installed)}
    <div class="settings-hint" style="margin-top:14px">Available</div>
    ${_addonAvailableHTML(st.available, ok, st.platform)}`;
  host.querySelectorAll('[data-addon-act]').forEach((b) => b.addEventListener('click', () => _addonAct(b)));
}

async function _addonAct(btn) {
  const act = btn.dataset.addonAct, rid = btn.dataset.rid, id = btn.dataset.id;
  try {
    if (act === 'approve') {
      await _addonHumanPost(`/api/addons/requests/${rid}/approve`, {}, { title: 'Approve add-on', description: 'Re-enter your dashboard passcode to approve this install.' });
    } else if (act === 'decline') {
      await _addonHumanPost(`/api/addons/requests/${rid}/decline`, {}, { title: 'Decline add-on', description: 'Re-enter your dashboard passcode to decline. Agents will not be able to ask again for 30 days.' });
    } else if (act === 'install') {
      await _addonHumanPost('/api/addons/install', { addon_id: id }, { title: 'Install add-on', description: 'Re-enter your dashboard passcode to install this add-on.' });
    } else if (act === 'adopt') {
      const out = await _addonApi('POST', '/api/addons/requests', { addon_id: 'system:' + id, reason: 'You chose to use the copy already on this computer.' });
      if (out.outcome === 'declined') _addonBusy = 'This add-on was declined recently. Use Install, or wait for the 30 days to pass.';
    } else if (act === 'remove') {
      const forget = btn.dataset.source === 'system';
      if (!window.confirm(forget ? 'Forget this adopted copy? Its files are left where they are.' : 'Delete this add-on from this computer?')) return;
      await _addonHumanPost(`/api/addons/${id}/remove`, {}, { title: 'Remove add-on', description: 'Re-enter your dashboard passcode to remove this add-on.' });
    }
    if (act !== 'adopt') _addonBusy = '';
  } catch (e) {
    if (!e.cancelled) _addonBusy = e.message;
  }
  await refreshAddonsSection();
}

async function refreshAddonsSection() {
  if (!document.getElementById('addons-host')) return;
  try {
    _addonState = await _addonApi('GET', '/api/addons');
  } catch (e) {
    const host = document.getElementById('addons-host');
    if (host) host.innerHTML = `<div class="addon-err">Could not load add-ons: ${esc(e.message)}</div>`;
    return;
  }
  _addonRender();
  _addonSchedule();
  _addonNudgeInbox();
}

// While anything is installing, poll for progress; stop when the panel is gone.
function _addonSchedule() {
  clearTimeout(_addonTimer);
  const installing = _addonState && _addonState.pending.some((c) => c.state === 'installing');
  if (installing && document.getElementById('addons-host')) _addonTimer = setTimeout(refreshAddonsSection, _ADDON_POLL_MS);
}

// ── Inbox / Waiting-on-you ──────────────────────────────────────────────────
// A pending request is a blocking event. The feed and the mobile dashboard ask
// addonAttentionItems() for rows to add to their own "needs you" list; the row
// opens this page. Polled lightly so a parked unattended run shows up without
// anyone opening Settings.
let _addonPending = [];
let _addonPendingKey = '';

function addonAttentionItems() {
  return _addonPending.filter((c) => c.state !== 'installing').map((c) => ({
    projectId: '__addons__', project: 'Add-on: ' + ((c.addon && c.addon.name) || c.addon_id), domain: '',
    msg: c.state === 'failed' ? 'Install failed — retry or decline' : 'Asked for by an agent — needs your approval',
    icon: '\u{1F4E6}', kind: 'addon', action: 'Review', sessionId: null,
  }));
}

function _addonNudgeInbox() {
  if (!_addonState) return;
  _addonPending = _addonState.pending || [];
  const key = _addonPending.map((c) => c.id + ':' + c.state).join(',');
  if (key === _addonPendingKey) return;
  _addonPendingKey = key;
  try { if (typeof renderFeed === 'function') renderFeed(); } catch (_) {}
  try { if (typeof renderWaitingOnYou === 'function') renderWaitingOnYou(); } catch (_) {}
}

async function _addonPollPending() {
  try {
    _addonState = await _addonApi('GET', '/api/addons');
    _addonNudgeInbox();
    if (document.getElementById('addons-host')) _addonRender();
  } catch (_) { /* a missed poll just shows the previous list */ }
}

function openAddonsSettings() {
  if (typeof openSettings === 'function') openSettings();
  setTimeout(() => { if (typeof drillSettings === 'function') drillSettings('addons'); refreshAddonsSection(); }, 60);
}

window.refreshAddonsSection = refreshAddonsSection;
window.addonAttentionItems = addonAttentionItems;
window.openAddonsSettings = openAddonsSettings;
setTimeout(_addonPollPending, 4000);
setInterval(_addonPollPending, 30000);
