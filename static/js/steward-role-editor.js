// ── Steward role editor (BUILTINS-3 SR-5) ─────────────────────────────────
// A steward role is a schedule bound to one backlog item (its charter) and a
// journal file. This module owns the human surface for it:
//   • the "Steward roles" cards in the Automation window (state, blockers,
//     start / stop / edit);
//   • the "Run as a steward role" section inside the existing schedule form
//     (charter pick, journal review, read-only preview, save).
// Backend: mc/blueprints/steward_role_routes.py (read-only support) plus the
// existing human-only schedule and steward writes (mc/steward_role_writes.py).
// Every write here goes through humanProofFetch, so the dashboard passcode is
// asked each time and an agent session can never reach it.
//
// Module (type=module): entry points hang off window; scheduler.js and
// steward.js call them at runtime, never at parse time.

const SR = { assignments: [], blockers: [], error: '', form: null };

const _E = (v) => (typeof esc === 'function' ? esc(v) : String(v == null ? '' : v)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));
const _el = (id) => document.getElementById(id);

// Short names for the install-level reasons a role cannot fire. The server
// states each one separately; the editor never merges or hides them.
const BLOCKER_LABELS = {
  core_adapter_unapplied: 'Cycle composer patch not applied',
  core_adapter_unknown: 'Cycle composer could not be inspected',
  directive_missing: 'Cycle directive missing',
  lifecycle_adapter_unavailable: 'Decision-channel adapter not installed',
  unattended_guard_off: 'Unattended guard is off',
};

async function _json(path, opts) {
  try {
    const res = await fetch(API_BASE + path, opts);
    return { ok: res.ok, status: res.status, body: await res.json().catch(() => ({})) };
  } catch (e) {
    return { ok: false, status: 0, body: { error: 'Could not reach the server: ' + e.message } };
  }
}

function _blockersHTML(blockers) {
  if (!blockers || !blockers.length) return '';
  return `<div class="srole-blockers" role="alert" data-srole-blockers>
    <div class="srole-blockers-title">Roles cannot run on this install yet</div>
    <ul>${blockers.map(b => `<li data-code="${_E(b.code)}"><b>${_E(BLOCKER_LABELS[b.code] || b.code)}.</b>
      ${_E(b.message)}</li>`).join('')}</ul></div>`;
}

function _cadence(a) {
  const m = a.interval_minutes;
  if (!m) return '';
  return m >= 60 && m % 60 === 0 ? `every ${m / 60}h` : `every ${m}m`;
}

function _stateOf(a, blocked) {
  if (a.role_error) return ['error', 'Role error'];
  if (a.active) return blocked ? ['blocked', 'Active, cannot run'] : ['active', 'Active'];
  if (a.enabled && a.steward_mode !== 'on') return ['stopped', 'Stopped'];
  return ['disabled', 'Disabled'];
}

// ── Cards ──────────────────────────────────────────────────────────────────

async function renderStewardRoles() {
  const list = _el('steward-list');
  if (!list) return;
  let area = _el('steward-role-area');
  if (!area) {
    area = document.createElement('div');
    area.id = 'steward-role-area';
    area.className = 'srole-area';
    list.insertAdjacentElement('afterend', area);
  }
  const res = await _json('/api/steward-role/assignments');
  SR.assignments = res.ok ? (res.body.assignments || []) : [];
  SR.blockers = res.body.blockers || [];
  SR.error = res.ok ? '' : (res.body.error || `HTTP ${res.status}`);
  const head = `<div class="srole-head">Steward roles
    <span class="memory-hint" style="margin:0;font-weight:normal;display:block">A schedule that carries a
    backlog item as its mandate. Create one with “Run as a steward role” in Add Schedule.</span></div>`;
  if (SR.error) {
    area.innerHTML = head + `<div class="srole-error" role="alert">Could not load steward roles: ${_E(SR.error)}</div>`;
    return;
  }
  if (!SR.assignments.length) {
    area.innerHTML = head + '<div class="schedule-empty">No steward roles yet.</div>';
    return;
  }
  const blocked = SR.blockers.length > 0;
  area.innerHTML = head + _blockersHTML(SR.blockers) + SR.assignments.map(a => {
    const [state, label] = _stateOf(a, blocked);
    const title = (a.charter_text || '').split('\n').find(l => l.trim()) || '(charter unavailable)';
    const startOff = blocked || !!a.role_error;
    const startWhy = a.role_error ? a.role_error
      : blocked ? 'Cannot start: ' + SR.blockers.map(b => BLOCKER_LABELS[b.code] || b.code).join('; ') : 'Start this role';
    const pid = _E(a.project_id);
    return `<div class="schedule-card-wrap" data-srole-card="${_E(a.schedule_id)}"><div class="schedule-card srole-card">
      <div class="schedule-card-body">
        <div class="schedule-card-project">${_E(a.project_name)}
          <span class="steward-badge srole-state" data-state="${state}">${_E(label)}</span></div>
        <div class="schedule-card-task" title="${_E(a.charter_text || '')}">${_E(title)}</div>
        ${a.role_error ? `<div class="schedule-card-error" role="alert">&#9888; ${_E(a.role_error)}</div>` : ''}
        ${a.last_error ? `<div class="schedule-card-error" title="${_E(a.last_error)}">&#9888; Last fire failed: ${_E(a.last_error)}</div>` : ''}
        <div class="schedule-card-meta">
          <span>${_E(_cadence(a))}</span>
          <span>${_E(a.character || 'inherits project default')}</span>
          <span>Next: ${a.active && a.next_run ? _E(window.formatScheduleTime ? window.formatScheduleTime(a.next_run) : a.next_run) : '—'}</span>
          <span title="Journal file the role appends to">${_E(a.journal_path || '')}</span>
        </div>
      </div>
      <div class="schedule-card-actions">
        ${a.active
          ? `<button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--red-text);border-color:var(--red)"
               data-srole-act="stop" onclick="StewardRoleEditor.stop('${pid}')" title="Stop this role (project kill switch)">Stop</button>`
          : `<button class="btn-header-action" style="padding:3px 8px;font-size:11px;color:var(--accent);border-color:var(--accent)"
               data-srole-act="start" ${startOff ? 'disabled' : ''} onclick="StewardRoleEditor.start('${pid}')"
               title="${_E(startWhy)}">Start</button>`}
        <button class="btn-header-action" style="padding:3px 8px;font-size:11px" data-srole-act="edit"
          onclick="editSchedule('${_E(a.schedule_id)}')" title="Edit charter, journal, owner and cadence">Edit</button>
      </div>
    </div></div>`;
  }).join('');
}

async function _control(pid, action) {
  const start = action === 'enable';
  const result = await window.humanProofFetch(
    `${API_BASE}/api/project/${encodeURIComponent(pid)}/steward/${action}`,
    { method: 'POST', body: '{}' },
    { title: start ? 'Start steward role' : 'Stop steward role',
      description: start
        ? 'Re-enter your dashboard passcode to start this role. Its cycles then run unattended.'
        : 'Re-enter your dashboard passcode to stop this role. Its journal, charter and history are kept.' });
  if (result === null) return;
  if (!result.ok) {
    if (window.showToast) window.showToast((start ? 'Could not start: ' : 'Could not stop: ')
      + (result.body.error || 'HTTP ' + result.status), 7000);
    return;
  }
  if (window.showToast) window.showToast(start ? 'Steward role started.' : 'Steward role stopped.', 3000);
  await renderStewardRoles();
  if (window.renderStewards) window.renderStewards();
}

// ── Schedule form section ──────────────────────────────────────────────────

function _formShell() {
  return `<label class="srole-toggle">
      <input type="checkbox" id="sched-role-on" style="margin:0;width:auto">
      <span style="font-weight:600">Run as a steward role</span>
      <span class="memory-hint" style="margin:0;font-weight:normal">(an unattended mandate from a backlog item instead of a fixed prompt)</span>
    </label>
    <div id="sched-role-fields" style="display:none"></div>
    <div id="sched-role-error" class="srole-error" role="alert" style="display:none"></div>`;
}

async function mount(existing) {
  SR.form = null;
  const host = _el('sched-role-area');
  if (!host) return;
  const isRole = !!(existing && Object.prototype.hasOwnProperty.call(existing, 'steward_role'));
  const role = isRole && existing.steward_role && typeof existing.steward_role === 'object' ? existing.steward_role : {};
  SR.form = { editingRole: isRole, scheduleId: (existing && existing.id) || '',
              enabled: !!(isRole && existing.enabled), role, options: null, optionsFor: '', journalTouched: isRole };
  host.innerHTML = _formShell();
  const box = _el('sched-role-on');
  box.addEventListener('change', () => _setOn(box.checked));
  const proj = _el('sched-project');
  if (proj) proj.addEventListener('change', () => { if (box.checked) _loadOptions(true); });
  if (isRole) { box.checked = true; await _setOn(true); }
}

async function _setOn(on) {
  const task = _el('sched-task-block');
  if (task) task.style.display = on ? 'none' : '';
  const fields = _el('sched-role-fields');
  if (!fields) return;
  _showError('');
  if (!on) {
    fields.style.display = SR.form && SR.form.editingRole ? 'block' : 'none';
    fields.innerHTML = SR.form && SR.form.editingRole
      ? `<div class="srole-note" data-srole-detach>Saving will <b>detach</b> this role. The charter, journal and run
         history stay. The schedule becomes an ordinary task schedule that runs the task below, and it is saved
         <b>disabled</b> until you enable it.</div>` : '';
    return;
  }
  fields.style.display = 'block';
  fields.innerHTML = '<div class="schedule-empty">Loading charter items…</div>';
  await _loadOptions(true);
}

async function _loadOptions(rerender) {
  const f = SR.form;
  const pid = (_el('sched-project') || {}).value || '';
  if (!f || !pid) return;
  const res = await _json('/api/steward-role/options?project_id=' + encodeURIComponent(pid));
  if (!SR.form || SR.form !== f) return;  // form was closed or rebuilt while loading
  f.options = res.ok ? res.body : null;
  f.optionsFor = pid;
  f.optionsError = res.ok ? '' : (res.body.error || 'HTTP ' + res.status);
  if (rerender) _renderFields();
}

function _renderFields() {
  const f = SR.form, fields = _el('sched-role-fields');
  if (!f || !fields) return;
  if (f.optionsError) {
    fields.innerHTML = `<div class="srole-error" role="alert">Could not load charter items: ${_E(f.optionsError)}</div>`;
    return;
  }
  const o = f.options || { charters: [], blockers: [] };
  const wanted = f.role.charter_item_id || '';
  const own = f.scheduleId;
  const known = o.charters.some(c => c.id === wanted);
  const opts = o.charters.map(c => {
    const taken = c.bound_schedule_id && c.bound_schedule_id !== own;
    return `<option value="${_E(c.id)}" data-journal="${_E(c.suggested_journal)}"${taken ? ' disabled' : ''}${c.id === wanted ? ' selected' : ''}>`
      + `${_E(c.title)}${taken ? ' — already another role’s charter' : ''}</option>`;
  });
  // A saved charter that is gone or closed stays visible and selected, so the
  // problem is read, not silently replaced by whatever sorts first.
  const missing = wanted && !known
    ? `<option value="${_E(wanted)}" selected>(missing or closed charter ${_E(wanted)})</option>` : '';
  const journal = f.role.journal_path || '';
  fields.innerHTML = `
    ${_blockersHTML(o.blockers)}
    <label>Charter <span class="memory-hint" style="margin:0;font-weight:normal">(the backlog item that is this role’s mandate; its text is authoritative)</span></label>
    <select id="sched-role-charter">
      <option value=""${wanted ? '' : ' selected'}>Pick a charter item…</option>${missing}${opts.join('')}
    </select>
    <div id="sched-role-charter-text" class="srole-charter" style="display:none"></div>
    <label>Journal file <span class="memory-hint" style="margin:0;font-weight:normal">(under docs/_journal/; the role appends here, never to backlog notes)</span></label>
    <input type="text" id="sched-role-journal" value="${_E(journal)}" spellcheck="false" style="font-family:var(--mono)"
      placeholder="docs/_journal/<item-id>-<slug>.md">
    <div class="memory-hint" style="margin:6px 0 0;font-size:11px">Owner is the Agent chosen above. Leaving it on the project default
      pins that persona when you save. Starting the role is a separate step, and the role is saved <b>disabled</b>
      unless it already ran.</div>
    <div class="sched-actions" style="margin-top:8px">
      <button class="btn-sched-cancel" id="sched-role-preview-btn" type="button">Preview</button>
    </div>
    <div id="sched-role-preview" class="srole-preview" style="display:none" aria-live="polite"></div>`;
  const sel = _el('sched-role-charter'), inp = _el('sched-role-journal');
  inp.addEventListener('input', () => { f.journalTouched = true; });
  sel.addEventListener('change', () => {
    const opt = sel.selectedOptions[0];
    if (!f.journalTouched || !inp.value.trim()) { inp.value = (opt && opt.dataset.journal) || ''; f.journalTouched = false; }
    _paintCharter();
  });
  _el('sched-role-preview-btn').addEventListener('click', preview);
  _paintCharter();
}

function _paintCharter() {
  const sel = _el('sched-role-charter'), box = _el('sched-role-charter-text');
  if (!sel || !box) return;
  const item = (SR.form.options.charters || []).find(c => c.id === sel.value);
  box.style.display = item ? 'block' : 'none';
  box.textContent = item ? `${item.title}  [${item.status || 'open'}]` : '';
}

function _collect() {
  const charter = (_el('sched-role-charter') || {}).value || '';
  const journal = ((_el('sched-role-journal') || {}).value || '').trim();
  return { version: 1, charter_item_id: charter, journal_path: journal };
}

function _showError(msg) {
  const box = _el('sched-role-error');
  if (!box) return;
  box.style.display = msg ? 'block' : 'none';
  box.textContent = msg || '';
}

async function preview() {
  const f = SR.form, out = _el('sched-role-preview');
  if (!f || !out) return;
  const role = _collect();
  out.style.display = 'block';
  if (!role.charter_item_id) { out.innerHTML = '<div class="srole-error">Pick a charter item first.</div>'; return; }
  out.innerHTML = '<div class="schedule-empty">Checking…</div>';
  const res = await _json('/api/steward-role/preview', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ project_id: (_el('sched-project') || {}).value, schedule_id: f.scheduleId || undefined,
      steward_role: role, character: (_el('sched-agent') || {}).value || '', enabled: f.enabled }),
  });
  const b = res.body || {};
  if (!res.ok && !b.checks) { out.innerHTML = `<div class="srole-error" role="alert">${_E(b.error || 'Preview failed (HTTP ' + res.status + ')')}</div>`; return; }
  const checks = (b.checks || []).map(c => `<li data-ok="${c.ok}">${c.ok ? '&#10003;' : '&#10007;'} <b>${_E(c.name)}</b> ${_E(c.detail)}</li>`).join('');
  out.innerHTML = `<ul class="srole-checks">${checks}</ul>
    ${b.charter ? `<div class="srole-charter-full"><b>Charter text the role will read</b><pre>${_E(b.charter.text)}</pre></div>` : ''}
    ${_blockersHTML(b.blockers)}
    <div class="srole-verdict" data-runnable="${!!b.runnable_now}">${b.ok
      ? (b.runnable_now ? 'Valid, and this install could run it.' : 'Valid, but it cannot run on this install yet (see above).')
      : 'Not valid yet. Fix the items marked ✗.'}</div>
    <div class="memory-hint" style="margin:4px 0 0;font-size:11px">${_E(b.note || '')}</div>`;
}

// scheduler.js asks these two at save time.
function active() { return !!(_el('sched-role-on') && _el('sched-role-on').checked); }
function handles() { return !!SR.form && !!_el('sched-role-on') && (active() || SR.form.editingRole); }

// Takes the body saveSchedule already built (cadence, character, condition,
// continuation) and sends it with the role. Returns true when saved.
async function save(body, editId, hooks) {
  const f = SR.form;
  _showError('');
  let title, description;
  if (active()) {
    const role = _collect();
    if (!role.charter_item_id) { _showError('Pick the backlog item that is this role’s charter.'); return false; }
    if (!role.journal_path) { _showError('Give the journal file the role will append to.'); return false; }
    body.steward_role = role;
    body.task = '';
    // A new role is saved disabled; starting it is its own step. An existing
    // role keeps whatever state it is in.
    body.enabled = editId ? f.enabled : false;
    title = editId ? 'Save steward role' : 'Create steward role';
    description = 'Re-enter your dashboard passcode to save this role. Saving does not start it.';
  } else {
    if (!window.confirm('Detach this steward role? The charter, journal and history stay; the schedule becomes an ordinary task and is saved disabled.')) return false;
    body.steward_role = null;
    body.enabled = false;
    title = 'Detach steward role';
    description = 'Re-enter your dashboard passcode to detach this role.';
  }
  const url = editId ? `${API_BASE}/api/schedules/${encodeURIComponent(editId)}` : `${API_BASE}/api/schedules`;
  const result = await window.humanProofFetch(url, { method: editId ? 'PUT' : 'POST', body: JSON.stringify(body) },
    { title, description });
  if (result === null) return false;           // cancelled: nothing was sent
  if (!result.ok) {
    _showError(result.body.error || ('Save failed (HTTP ' + result.status + ')'));
    return false;
  }
  if (window.showToast) window.showToast(active() ? 'Steward role saved (not started).' : 'Role detached.', 3500);
  if (hooks && hooks.done) await hooks.done();
  return true;
}

const StewardRoleEditor = {
  mount, active, handles, save, preview,
  start: (pid) => _control(pid, 'enable'),
  stop: (pid) => _control(pid, 'disable'),
  hasRole: (pid) => SR.assignments.some(a => a.project_id === pid),
  _state: SR,
};

window.StewardRoleEditor = StewardRoleEditor;
window.renderStewardRoles = renderStewardRoles;
