// ── Team proposal card (```mc:team```) ─────────────────────────────────────
// An agent (hired Claydo, Ask Claydo, anyone) answers "who do I need for X?"
// with a fenced mc:team block. This renders it as an editable card; the human's
// "Create team" click is the ONLY thing that creates anything, through
// POST /api/characters/team, which checks every member before the first write
// and keeps all of it or none of it. Each row either REUSES an agent that
// already exists (hired into the project as-is; the card never edits its
// persona or engine) or CREATES a new one from the editable fields.
//
// State lives in `_teamState`, keyed by the block's text + project, so a
// refreshModal rebuild re-mounts the card with the user's edits intact.

const _teamState = new Map();
const _teamCatalogs = {};      // pid -> {chars: [], promise}
let _teamShared = null;        // {figures: [], providers: []}
let _teamFocus = null;         // {key, idx, f, start, end} — survives a rebuild
const _teamBuffers = {};       // sessionId -> {ph, lines} while a block streams
const _TEAM_EFFORTS = ['', 'low', 'medium', 'high', 'xhigh', 'max'];

function _teamHash(s) {
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0;
  return (h >>> 0).toString(36);
}

function teamCardPlaceholderHTML(source, projectId) {
  return `<div class="team-card-mount" data-source="${esc(source)}" data-project-id="${esc(projectId || '')}"></div>`;
}

function _teamNormMember(m) {
  const eng = (m.engine && typeof m.engine === 'object') ? m.engine : m;
  const ref = String(m.reuse || m.ref || '').trim();
  return {
    mode: ref ? 'reuse' : 'new',
    ref,
    reason: String(m.reason || ''),
    note: String(m.note || m.engine_note || ''),
    draft: {
      name: String(m.name || '').trim().toLowerCase(),
      agent_name: String(m.agent_name || m.goes_by || ''),
      description: String(m.description || m.role || ''),
      body: String(m.body || m.persona || ''),
      avatar: String(m.avatar || ''),
      provider: String(eng.provider || ''),
      model: String(eng.model || ''),
      effort: String(eng.effort || ''),
      scope: m.scope === 'global' ? 'global' : 'project',
    },
  };
}

// Returns {title, members} or null when the block is not a usable proposal.
function parseTeamProposal(source) {
  let data;
  try { data = JSON.parse(String(source || '').trim()); } catch (e) { return null; }
  const list = Array.isArray(data) ? data : (data && Array.isArray(data.members) ? data.members : null);
  if (!list) return null;
  const members = list.filter((m) => m && typeof m === 'object').map(_teamNormMember);
  if (!members.length) return null;
  const title = (!Array.isArray(data) && (data.title || data.team)) || '';
  return { title: String(title), members };
}

// A reuse ref may arrive as "global:name", a bare file name, or the name the
// agent goes by. Resolve against the real list; project scope wins, the same
// shadowing order list_characters uses.
function _teamResolve(ref, chars) {
  const r = String(ref || '').trim();
  if (!r || !chars) return null;
  const exact = chars.find((c) => `${c.scope}:${c.name}` === r);
  if (exact) return exact;
  const bare = r.includes(':') ? r.split(':').slice(1).join(':') : r;
  const low = bare.toLowerCase();
  return chars.find((c) => c.scope === 'project' && c.name === low)
    || chars.find((c) => c.name === low)
    || chars.find((c) => (c.agent_name || '').toLowerCase() === low)
    || null;
}

function _teamEngineText(c) {
  const e = (c && c.engine) || {};
  const parts = [e.provider, e.model, e.effort && `effort ${e.effort}`].filter(Boolean);
  return parts.length ? parts.join(' · ') : 'project default engine';
}

async function _teamEnsureCatalog(pid) {
  if (!_teamShared) {
    _teamShared = { figures: [], providers: [] };
    try {
      const [a, p] = await Promise.all([
        fetch(API_BASE + '/api/avatars').then((r) => r.json()).catch(() => ({})),
        fetch(API_BASE + '/api/agent/providers').then((r) => r.json()).catch(() => []),
      ]);
      _teamShared.figures = a.figures || [];
      _teamShared.providers = (Array.isArray(p) ? p : (p.providers || [])).filter((x) => x && x.name);
    } catch (e) { /* the selects just stay short; typing still works */ }
  }
  const k = pid || '';
  if (!_teamCatalogs[k]) {
    _teamCatalogs[k] = { chars: null };
    _teamCatalogs[k].promise = fetch(API_BASE + '/api/characters' + (pid ? '?project_id=' + encodeURIComponent(pid) : ''))
      .then((r) => r.json()).then((list) => { _teamCatalogs[k].chars = Array.isArray(list) ? list : []; })
      .catch(() => { _teamCatalogs[k].chars = []; });
  }
  await _teamCatalogs[k].promise;
  return _teamCatalogs[k].chars;
}

function _teamOpt(value, label, current) {
  return `<option value="${esc(value)}"${value === current ? ' selected' : ''}>${esc(label)}</option>`;
}

function _teamMemberHTML(st, m, i, chars) {
  const done = st.status === 'created' || st.status === 'busy';
  const dis = done ? ' disabled' : '';
  const face = (typeof window.avatarHTML === 'function');
  const err = st.memberErrors[i] ? `<div class="team-member-err">${esc(st.memberErrors[i])}</div>` : '';
  const toggle = `<div class="team-mode" role="group">
      <button type="button" data-act="mode" data-mode="reuse" class="${m.mode === 'reuse' ? 'sel' : ''}"${dis}${st.projectId ? '' : ' disabled title="Reusing hires into a project; open this in a project"'}>Reuse existing</button>
      <button type="button" data-act="mode" data-mode="new" class="${m.mode === 'new' ? 'sel' : ''}"${dis}>Create new</button>
    </div>`;
  const remove = `<button type="button" class="team-member-remove" data-act="remove" title="Remove from the team"${dis}>&#10005;</button>`;

  if (m.mode === 'reuse') {
    const c = chars ? _teamResolve(m.ref, chars) : null;
    const opts = (chars || []).map((x) => _teamOpt(`${x.scope}:${x.name}`,
      `${x.agent_name ? x.agent_name + ' (' + x.name + ')' : x.name} · ${x.scope}`, c ? `${c.scope}:${c.name}` : ''));
    const missing = chars && !c
      ? `<div class="team-member-err">No existing agent matches "${esc(m.ref)}". Pick one, or switch to Create new.</div>` : '';
    return `<div class="team-member reuse" data-idx="${i}">
      <div class="team-member-top">
        ${face ? window.avatarHTML(c ? (c.avatar || '') : '', 28) : ''}
        <span class="team-member-label">REUSE <strong>${esc(c ? (c.agent_name || c.name) : m.ref)}</strong></span>
        ${toggle}${remove}
      </div>
      <label class="team-f wide"><span>Existing agent</span>
        <select data-f="ref"${dis}>${chars ? '' : '<option>loading…</option>'}${opts.join('')}${chars && !c ? _teamOpt('', '(pick one)', '') : ''}</select></label>
      ${m.reason ? `<div class="team-member-reason">Why it fits: ${esc(m.reason)}</div>` : ''}
      <div class="team-member-engine">Engine (not changed here): ${esc(c ? _teamEngineText(c) : '—')}</div>
      ${m.note ? `<div class="team-member-note">${esc(m.note)}</div>` : ''}
      ${missing}${err}
    </div>`;
  }

  const d = m.draft;
  const figs = (_teamShared && _teamShared.figures) || [];
  const faceOpts = [_teamOpt('', 'no face', d.avatar)]
    .concat(d.avatar && !d.avatar.startsWith('fig:') ? [_teamOpt(d.avatar, d.avatar, d.avatar)] : [])
    .concat(figs.map((f) => _teamOpt('fig:' + f, f, d.avatar)));
  if (d.avatar.startsWith('fig:') && !figs.includes(d.avatar.slice(4))) faceOpts.push(_teamOpt(d.avatar, d.avatar + ' (not on this install)', d.avatar));
  const provs = (_teamShared && _teamShared.providers) || [];
  const provOpts = [_teamOpt('', 'project default', d.provider)].concat(provs.map((p) => _teamOpt(p.name, p.display_name || p.name, d.provider)));
  if (d.provider && !provs.some((p) => p.name === d.provider)) provOpts.push(_teamOpt(d.provider, d.provider, d.provider));
  const prov = provs.find((p) => p.name === d.provider);
  const listId = `team-models-${st.key}-${i}`;
  const models = prov && prov.models ? prov.models.map((x) => `<option value="${esc(x.id)}">${esc(x.label || x.id)}</option>`).join('') : '';
  const conflict = st.conflicts[i]
    ? `<label class="team-conflict"><input type="checkbox" data-act="overwrite" data-key="${esc(st.conflicts[i])}"${st.overwrite.has(st.conflicts[i]) ? ' checked' : ''}${dis}> <strong>${esc(st.conflicts[i])}</strong> already exists. Tick to overwrite it, or rename this member.</label>` : '';
  return `<div class="team-member new" data-idx="${i}">
    <div class="team-member-top">
      ${face ? window.avatarHTML(d.avatar, 28) : ''}
      <span class="team-member-label">CREATE NEW</span>
      ${toggle}${remove}
    </div>
    <div class="team-grid">
      <label class="team-f"><span>Name</span><input data-f="name" value="${esc(d.name)}" spellcheck="false" placeholder="kebab-case"${dis}></label>
      <label class="team-f"><span>Goes by</span><input data-f="agent_name" value="${esc(d.agent_name)}" maxlength="32"${dis}></label>
      <label class="team-f wide"><span>Role</span><input data-f="description" value="${esc(d.description)}"${dis}></label>
      <label class="team-f"><span>Face</span><select data-f="avatar"${dis}>${faceOpts.join('')}</select></label>
      <label class="team-f"><span>Where</span><select data-f="scope"${dis}>${_teamOpt('project', 'this project', d.scope)}${_teamOpt('global', 'all projects', d.scope)}</select></label>
      <label class="team-f"><span>Provider</span><select data-f="provider"${dis}>${provOpts.join('')}</select></label>
      <label class="team-f"><span>Model</span><input data-f="model" value="${esc(d.model)}" list="${esc(listId)}" placeholder="default"${dis}><datalist id="${esc(listId)}">${models}</datalist></label>
      <label class="team-f"><span>Effort</span><select data-f="effort"${dis}>${_TEAM_EFFORTS.map((e) => _teamOpt(e, e || 'default', d.effort)).join('')}</select></label>
      <label class="team-f wide"><span>Persona</span><textarea data-f="body" rows="4"${dis}>${esc(d.body)}</textarea></label>
    </div>
    ${conflict}${err}
  </div>`;
}

function _teamRender(mount, st) {
  const chars = (_teamCatalogs[st.projectId || ''] || {}).chars || null;
  const reuse = st.members.filter((m) => m.mode === 'reuse').length;
  const created = st.status === 'created';
  const busy = st.status === 'busy';
  const hireRow = st.projectId
    ? `<label class="team-hire"><input type="checkbox" data-f="hireNew"${st.hireNew ? ' checked' : ''}${created || busy ? ' disabled' : ''}> Also hire the new ones into this project</label>`
    : `<span class="team-hire muted">Open this in a project to reuse or hire agents.</span>`;
  mount.innerHTML = `<div class="team-card${created ? ' created' : ''}">
    <div class="team-card-head">
      <span class="team-card-kicker">Team proposal</span>
      ${st.title ? `<span class="team-card-title">${esc(st.title)}</span>` : ''}
      <span class="team-card-count">${st.members.length} member${st.members.length === 1 ? '' : 's'} · ${reuse} reuse · ${st.members.length - reuse} new</span>
    </div>
    ${st.members.map((m, i) => _teamMemberHTML(st, m, i, chars)).join('')}
    <div class="team-card-foot">
      <button type="button" class="team-add" data-act="add"${created || busy ? ' disabled' : ''}>+ Add member</button>
      ${hireRow}
      <span class="team-card-msg${st.status === 'error' ? ' err' : ''}">${esc(st.message || '')}</span>
      <button type="button" class="team-create" data-act="create"${created || busy || !st.members.length ? ' disabled' : ''}>${created ? 'Team created' : busy ? 'Creating…' : 'Create team'}</button>
    </div>
  </div>`;
  if (_teamFocus && _teamFocus.key === st.key) {
    const el = mount.querySelector(`.team-member[data-idx="${_teamFocus.idx}"] [data-f="${_teamFocus.f}"]`);
    if (el && document.activeElement !== el) {
      el.focus();
      try { if (_teamFocus.start != null) el.setSelectionRange(_teamFocus.start, _teamFocus.end); } catch (e) {}
    }
  }
}

function _teamRerenderAll(key) {
  document.querySelectorAll('.team-card-mount[data-team-key]').forEach((m) => {
    const st = _teamState.get(m.dataset.teamKey);
    if (st && (!key || st.key === key)) _teamRender(m, st);
  });
}

function _teamStateFor(mount) {
  return _teamState.get(mount.dataset.teamKey || '');
}

function _teamBind(mount) {
  if (mount.__teamBound) return;
  mount.__teamBound = true;
  const fieldEvent = (e) => {
    const t = e.target;
    const f = t.dataset && t.dataset.f;
    const st = _teamStateFor(mount);
    if (!f || !st || st.status === 'created') return;
    if (f === 'hireNew') { st.hireNew = t.checked; return; }
    const row = t.closest('.team-member');
    const m = row && st.members[+row.dataset.idx];
    if (!m) return;
    if (f === 'ref') m.ref = t.value;
    else m.draft[f] = f === 'name' ? t.value.toLowerCase() : t.value;
    delete st.memberErrors[+row.dataset.idx];
    if (e.type === 'input') {
      _teamFocus = { key: st.key, idx: +row.dataset.idx, f, start: t.selectionStart, end: t.selectionEnd };
    } else if (t.tagName === 'SELECT') {
      _teamRender(mount, st);
    }
  };
  mount.addEventListener('input', fieldEvent);
  mount.addEventListener('change', fieldEvent);
  mount.addEventListener('focusin', (e) => {
    const t = e.target, st = _teamStateFor(mount), row = t.closest && t.closest('.team-member');
    if (st && row && t.dataset.f) _teamFocus = { key: st.key, idx: +row.dataset.idx, f: t.dataset.f, start: t.selectionStart, end: t.selectionEnd };
  });
  mount.addEventListener('focusout', (e) => { if (e.relatedTarget) _teamFocus = null; });
  mount.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    const st = _teamStateFor(mount);
    if (!b || !st || b.disabled) return;
    const row = b.closest('.team-member');
    const i = row ? +row.dataset.idx : -1;
    const act = b.dataset.act;
    if (act === 'add') {
      st.members.push(_teamNormMember({ scope: st.projectId ? 'project' : 'global' }));
    } else if (act === 'remove' && i >= 0) {
      st.members.splice(i, 1);
      st.memberErrors = {}; st.conflicts = {};
    } else if (act === 'mode' && i >= 0) {
      _teamFlip(st, st.members[i], b.dataset.mode);
      delete st.memberErrors[i]; delete st.conflicts[i];
    } else if (act === 'overwrite') {
      if (b.checked) st.overwrite.add(b.dataset.key); else st.overwrite.delete(b.dataset.key);
      return;
    } else if (act === 'create') {
      _teamCreate(st);
      return;
    } else {
      return;
    }
    _teamRender(mount, st);
  });
}

// Flipping never loses what either side held: a reuse row remembers its ref,
// a new row its draft, so flipping back and forth is lossless.
function _teamFlip(st, m, mode) {
  if (mode === m.mode) return;
  const chars = (_teamCatalogs[st.projectId || ''] || {}).chars || [];
  if (mode === 'new') {
    const c = _teamResolve(m.ref, chars);
    if (c && !m.draft.name) {
      m.draft.name = c.name + '-new';
      m.draft.description = m.draft.description || c.description || '';
      m.draft.avatar = m.draft.avatar || c.avatar || '';
    }
  } else if (!m.ref) {
    const c = _teamResolve(m.draft.name, chars) || _teamResolve(m.draft.agent_name, chars);
    m.ref = c ? `${c.scope}:${c.name}` : (chars[0] ? `${chars[0].scope}:${chars[0].name}` : '');
  }
  m.mode = mode;
}

async function _teamCreate(st) {
  const chars = (_teamCatalogs[st.projectId || ''] || {}).chars || [];
  const members = st.members.map((m) => {
    if (m.mode === 'reuse') {
      const c = _teamResolve(m.ref, chars);
      return { mode: 'reuse', ref: c ? `${c.scope}:${c.name}` : m.ref };
    }
    return Object.assign({ mode: 'new' }, m.draft);
  });
  st.status = 'busy'; st.message = 'Checking every member…'; st.memberErrors = {};
  _teamRerenderAll(st.key);
  let res, data = {};
  try {
    res = await fetch(API_BASE + '/api/characters/team', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ members, project_id: st.projectId || null,
        overwrite: [...st.overwrite], hire_new: !!(st.projectId && st.hireNew) }),
    });
    data = await res.json().catch(() => ({}));
  } catch (e) {
    st.status = 'error'; st.message = 'Network error: ' + (e.message || e);
    _teamRerenderAll(st.key);
    return;
  }
  if (res.status === 201) {
    const c = (data.created || []).length, h = (data.hired || []).length, a = (data.already_hired || []).length;
    st.status = 'created';
    st.message = `Created ${c}` + (h ? `, hired ${h} into this project` : '') + (a ? ` (${a} already on the roster)` : '') + '.';
    st.conflicts = {};
    try { localStorage.setItem('mc_team_done_' + st.key, st.message); } catch (e) {}
    for (const rec of data.created || []) {
      try { window.dispatchEvent(new CustomEvent('clayrune:characters-changed', { detail: { scope: rec.scope, name: rec.name, action: 'create' } })); } catch (e) {}
    }
    delete _teamCatalogs[st.projectId || ''];
    if (h && typeof window.refreshSilent === 'function') { try { window.refreshSilent(); } catch (e) {} }
  } else {
    st.status = 'error';
    st.message = data.error || `Create failed (${res.status})`;
    (data.member_errors || []).forEach((e) => { st.memberErrors[e.index] = e.error; });
    st.conflicts = {};
    (data.conflicts || []).forEach((c) => { st.conflicts[c.index] = c.key; });
  }
  _teamRerenderAll(st.key);
}

function mountTeamCards(root, opts) {
  const scope = root || document;
  const pids = new Set();
  scope.querySelectorAll('.team-card-mount:not(.team-card-pending)').forEach((mount) => {
    const source = mount.dataset.source || '';
    const pid = mount.dataset.projectId || (opts && opts.projectId) || '';
    const key = _teamHash(source + '|' + pid);
    let st = _teamState.get(key);
    if (!st) {
      const prop = parseTeamProposal(source);
      if (!prop) {
        mount.innerHTML = `<div class="team-card-invalid">This team proposal is not valid JSON, so there is nothing to create.</div><pre class="hl-codeblock">${esc(source)}</pre>`;
        return;
      }
      let doneMsg = null;
      try { doneMsg = localStorage.getItem('mc_team_done_' + key); } catch (e) {}
      st = { key, projectId: pid, title: prop.title, members: prop.members,
        hireNew: !!pid, overwrite: new Set(), memberErrors: {}, conflicts: {},
        status: doneMsg ? 'created' : 'draft', message: doneMsg || '' };
      _teamState.set(key, st);
    }
    mount.dataset.teamKey = key;
    _teamBind(mount);
    _teamRender(mount, st);
    pids.add(pid);
  });
  pids.forEach((pid) => {
    const known = _teamCatalogs[pid] && _teamCatalogs[pid].chars;
    if (!known || !_teamShared) _teamEnsureCatalog(pid).then(() => _teamRerenderAll());
  });
}

// Streaming path, mirroring _handleMermaidLine: buffer the fence's lines, then
// mount the card once the closing fence arrives.
function _handleTeamLine(sessionId, text, el) {
  const buf = _teamBuffers[sessionId];
  if (!buf && /^\s*```\s*mc:team\b/.test(text)) {
    const ph = document.createElement('div');
    ph.className = 'team-card-mount team-card-pending';
    ph.textContent = 'Reading team proposal…';
    el.appendChild(ph);
    _teamBuffers[sessionId] = { ph, lines: [] };
    return true;
  }
  if (buf && /^\s*```\s*$/.test(text)) {
    delete _teamBuffers[sessionId];
    let pid = '';
    try { pid = (agentStatusCache[sessionId] || {}).projectId || ''; } catch (e) {}
    if (!pid) {
      const modal = el.closest('[data-modal-id]');
      const mid = modal ? modal.dataset.modalId : '';
      pid = mid && !mid.startsWith('__') ? mid : '';
    }
    buf.ph.classList.remove('team-card-pending');
    buf.ph.textContent = '';
    buf.ph.dataset.source = buf.lines.join('\n');
    buf.ph.dataset.projectId = pid;
    mountTeamCards(buf.ph.parentElement);
    return true;
  }
  if (buf) { buf.lines.push(text); return true; }
  return false;
}

// ── ES-module interop ───────────────────────────────────────────────────────
// conversation.js (the rerender loop + appendAgentLine), claydo.js, and
// index.html's refreshModal reach these across the module boundary.
window.teamCardPlaceholderHTML = teamCardPlaceholderHTML;
window.parseTeamProposal = parseTeamProposal;
window.mountTeamCards = mountTeamCards;
window._handleTeamLine = _handleTeamLine;
