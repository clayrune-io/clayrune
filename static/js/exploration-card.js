// ── Brainstorm exploration card (`[clayrune:exploration-ready]`) ───────────
// MC-990 (docs/BRAINSTORM_HANDOFF_SPEC.md §2-3). A Brainstorm session (running
// as an ordinary agent chat, dispatched into the Ideas workspace or an
// existing project) emits a single terminal `[clayrune:exploration-ready]`
// line after a complete -- or explicitly provisional -- Exploration brief.
// This renders a persistent proposal card bound to that exact message, same
// shape as the `mc:team` card (static/js/team-card.js): nothing is written
// until the human opens the review form and clicks the final button, which
// POSTs the ONE transfer route (mc/blueprints/guide_routes.py::brainstorm_transfer).
//
// Versioning is DOM-derived, not counter-state that could desync across a
// refreshModal rebuild (see discovery_askuserquestion_dom_dedup.md — the
// established rule here is "dedupe/number against the DOM, never a JS
// variable that outlives a rebuild"): the live per-line path numbers a new
// card by counting `.exploration-card-mount` nodes already in its own
// `#agent-output-<sid>` container; the cold-render string-builder in
// conversation.js numbers by a local loop counter over the full replayed
// buffer. Both land on the same number the backend computes (1-based,
// chronological occurrence of the marker among assistant messages).

const _explState = new Map();          // key (sessionId|version) -> card state
let _explProjectsPromise = null;       // shared /api/projects fetch for the existing-project picker

function explorationCardPlaceholderHTML(sessionId, version, projectId, claudeSessionId, hasBrief) {
  return `<div class="exploration-card-mount" data-session-id="${esc(sessionId)}" data-version="${version}" `
    + `data-project-id="${esc(projectId || '')}" data-claude-session-id="${esc(claudeSessionId || '')}" `
    + `data-has-brief="${hasBrief === false ? '0' : '1'}"></div>`;
}

function _explKey(sessionId, version) { return sessionId + '|' + version; }

function _explDoneKey(claudeSessionId, version) { return 'mc_exploration_done_' + claudeSessionId + '_v' + version; }

function _explLoadDone(claudeSessionId, version) {
  try {
    const raw = localStorage.getItem(_explDoneKey(claudeSessionId, version));
    const list = raw ? JSON.parse(raw) : [];
    return Array.isArray(list) ? list : [];
  } catch (e) { return []; }
}

function _explSaveDone(claudeSessionId, version, list) {
  try { localStorage.setItem(_explDoneKey(claudeSessionId, version), JSON.stringify(list)); } catch (e) {}
}

async function _explEnsureProjects() {
  if (!_explProjectsPromise) {
    _explProjectsPromise = fetch(API_BASE + '/api/projects').then((r) => r.json()).then((list) => {
      const all = Array.isArray(list) ? list : [];
      return all.filter((p) => p && p.id && !p._is_ideas_workspace && !p._is_incognito_project && !p._is_steward_workspace);
    }).catch(() => []);
  }
  return _explProjectsPromise;
}

function _explSlug(name) {
  return String(name || '').trim().toLowerCase().replace(/[^a-z0-9_-]+/g, '_').replace(/^_+|_+$/g, '');
}

function _explFormCreateHTML(st) {
  const d = st.draft;
  return `<div class="exploration-grid">
    <label class="team-f wide"><span>Name</span><input data-f="name" value="${esc(d.name)}" placeholder="A name for the new project"></label>
    <label class="team-f"><span>Project ID</span><input data-f="id" value="${esc(d.id)}" spellcheck="false" placeholder="kebab-case"></label>
    <label class="team-f"><span>Domain</span><input data-f="domain" value="${esc(d.domain)}" placeholder="general"></label>
    <label class="team-f"><span>Folder (optional)</span><input data-f="folder" value="${esc(d.folder)}" placeholder="default workspace location"></label>
    <label class="team-f wide"><span>Short description</span><textarea data-f="description" rows="2" placeholder="A short hypothesis, not the whole brief">${esc(d.description)}</textarea></label>
    <label class="team-f wide"><span>Backlog task (editable)</span><textarea data-f="backlog_text" rows="2" placeholder="Left blank, the next one-day experiment is derived from the brief">${esc(d.backlog_text)}</textarea></label>
  </div>`;
}

function _explFormExistingHTML(st, projects) {
  const d = st.draft;
  const opts = (projects || []).map((p) => `<option value="${esc(p.id)}"${p.id === d.project_id ? ' selected' : ''}>${esc(p.name || p.id)}</option>`);
  return `<div class="exploration-grid">
    <label class="team-f wide"><span>Destination project</span>
      <select data-f="project_id">${projects ? '' : '<option>loading…</option>'}${opts.join('')}</select></label>
    <label class="team-f wide"><span>Backlog task (editable)</span><textarea data-f="backlog_text" rows="2" placeholder="Left blank, the next one-day experiment is derived from the brief">${esc(d.backlog_text)}</textarea></label>
  </div>`;
}

function _explDoneListHTML(done) {
  if (!done.length) return '';
  return `<div class="exploration-done-list">${done.map((r) =>
    `<button type="button" class="exploration-open" data-act="open-done" data-pid="${esc(r.destination_project_id)}">Open ${esc(r.destination_project_id)}</button>`
  ).join(' ')}</div>`;
}

function _explRender(mount, st) {
  const busy = st.status === 'busy';
  const done = _explLoadDone(st.claudeSessionId, st.version);
  if (!st.hasBrief) {
    mount.innerHTML = `<div class="exploration-card invalid">
      <div class="exploration-card-head">
        <span class="exploration-card-kicker">Exploration v${st.version}</span>
        <span class="exploration-card-title">No usable brief</span>
      </div>
      <div class="exploration-card-msg err">This marker did not follow a written brief, so there is nothing to hand off.</div>
    </div>`;
    return;
  }
  const newer = st.newer ? `<div class="exploration-newer">A newer exploration (v${st.newer}) exists in this conversation.</div>` : '';
  let body = '';
  if (st.mode === 'create') body = _explFormCreateHTML(st);
  else if (st.mode === 'existing') body = _explFormExistingHTML(st, st.projects);
  const actions = st.mode
    ? `<div class="exploration-card-foot">
        <button type="button" class="team-add" data-act="cancel"${busy ? ' disabled' : ''}>Cancel</button>
        <span class="exploration-card-msg${st.status === 'error' ? ' err' : ''}">${esc(st.message || '')}</span>
        <button type="button" class="team-create" data-act="submit"${busy ? ' disabled' : ''}>${busy ? 'Sending…' : (st.mode === 'create' ? 'Create project and send' : 'Add to project')}</button>
      </div>`
    : `<div class="exploration-card-foot">
        <button type="button" class="team-create" data-act="open-create">Create a new project from this</button>
        <button type="button" class="team-add" data-act="open-existing">Add to an existing project</button>
      </div>`;
  mount.innerHTML = `<div class="exploration-card">
    <div class="exploration-card-head">
      <span class="exploration-card-kicker">Exploration v${st.version}</span>
      <span class="exploration-card-title">Use this exploration</span>
    </div>
    ${newer}
    ${_explDoneListHTML(done)}
    ${body}
    ${actions}
  </div>`;
}

function _explRerenderAll(sessionId, version) {
  const key = version != null ? _explKey(sessionId, version) : null;
  document.querySelectorAll('.exploration-card-mount').forEach((m) => {
    const st = _explState.get(m.dataset.explKey);
    if (st && (!key || m.dataset.explKey === key)) _explRender(m, st);
  });
}

function _explStateFor(mount) { return _explState.get(mount.dataset.explKey || ''); }

function _explBind(mount) {
  if (mount.__explBound) return;
  mount.__explBound = true;
  mount.addEventListener('input', (e) => {
    const f = e.target.dataset && e.target.dataset.f;
    const st = _explStateFor(mount);
    if (!f || !st) return;
    if (st.mode === 'create') st.draft[f === 'name' ? 'name' : f] = e.target.value;
    else st.draft[f] = e.target.value;
    if (f === 'name' && !st.idEdited) st.draft.id = _explSlug(e.target.value);
    if (f === 'id') st.idEdited = true;
  });
  mount.addEventListener('change', (e) => {
    const f = e.target.dataset && e.target.dataset.f;
    const st = _explStateFor(mount);
    if (f === 'project_id' && st) st.draft.project_id = e.target.value;
  });
  mount.addEventListener('click', async (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const st = _explStateFor(mount);
    if (!st) return;
    const act = b.dataset.act;
    if (act === 'open-create') {
      st.mode = 'create'; st.status = 'draft'; st.message = '';
      _explRender(mount, st);
    } else if (act === 'open-existing') {
      st.mode = 'existing'; st.status = 'draft'; st.message = '';
      _explRender(mount, st);
      if (!st.projects) {
        st.projects = await _explEnsureProjects();
        if (!st.draft.project_id && st.projects[0]) st.draft.project_id = st.projects[0].id;
        _explRender(mount, st);
      }
    } else if (act === 'cancel') {
      st.mode = null; st.status = 'draft'; st.message = '';
      _explRender(mount, st);
    } else if (act === 'open-done') {
      if (typeof openProjectModal === 'function') openProjectModal(b.dataset.pid);
    } else if (act === 'submit') {
      await _explSubmit(mount, st);
    }
  });
}

async function _explSubmit(mount, st) {
  const destination = st.mode === 'create'
    ? { mode: 'create', id: st.draft.id, name: st.draft.name, domain: st.draft.domain, folder: st.draft.folder, description: st.draft.description }
    : { mode: 'existing', project_id: st.draft.project_id };
  if (st.mode === 'create' && !st.draft.id) {
    st.status = 'error'; st.message = 'A project ID is required.'; _explRender(mount, st); return;
  }
  if (st.mode === 'existing' && !st.draft.project_id) {
    st.status = 'error'; st.message = 'Pick a destination project.'; _explRender(mount, st); return;
  }
  st.status = 'busy'; st.message = ''; _explRender(mount, st);
  let res, data = {};
  try {
    // MC-995: promoting a brainstorm into a doc + backlog item is human-only-gated.
    const result = await humanProofFetch(API_BASE + `/api/project/${encodeURIComponent(st.projectId)}/brainstorm/transfer`, {
      method: 'POST',
      body: JSON.stringify({
        claude_session_id: st.claudeSessionId, version: st.version,
        destination, backlog_text: st.draft.backlog_text,
      }),
    }, {
      title: 'Transfer exploration',
      description: 'Re-enter your dashboard passcode to promote this exploration.',
    });
    if (result === null) { st.status = 'draft'; _explRender(mount, st); return; }
    res = { status: result.status };
    data = result.body;
  } catch (e) {
    st.status = 'error'; st.message = 'Network error: ' + (e.message || e);
    _explRender(mount, st);
    return;
  }
  if (res.status === 201 || res.status === 200) {
    const done = _explLoadDone(st.claudeSessionId, st.version);
    done.push({ destination_project_id: data.destination_project_id, doc_path: data.doc_path, backlog_item_id: data.backlog_item_id });
    _explSaveDone(st.claudeSessionId, st.version, done);
    st.mode = null; st.status = 'draft'; st.message = '';
    _explRender(mount, st);
    if (typeof openProjectModal === 'function') openProjectModal(data.destination_project_id);
  } else {
    st.status = 'error';
    st.message = data.error || `Transfer failed (${res.status})`;
    _explRender(mount, st);
  }
}

// Scans every mounted card for a given sessionId and marks all but the
// highest version as "a newer exploration exists" — matches on rebuild too,
// since mounts are DOM-derived each render pass (see module docblock).
function _explMarkSuperseded(sessionId) {
  const mounts = [...document.querySelectorAll(`.exploration-card-mount[data-session-id="${CSS.escape(sessionId)}"]`)];
  if (mounts.length < 2) return;
  const versions = mounts.map((m) => +m.dataset.version);
  const max = Math.max(...versions);
  mounts.forEach((m) => {
    const st = _explState.get(m.dataset.explKey);
    if (!st) return;
    const v = +m.dataset.version;
    const wantNewer = v < max ? max : null;
    if (st.newer !== wantNewer) { st.newer = wantNewer; _explRender(m, st); }
  });
}

function mountExplorationCards(root) {
  const scope = root || document;
  const sids = new Set();
  scope.querySelectorAll('.exploration-card-mount').forEach((mount) => {
    const sessionId = mount.dataset.sessionId || '';
    const version = +mount.dataset.version || 1;
    const key = _explKey(sessionId, version);
    mount.dataset.explKey = key;
    sids.add(sessionId);
    if (_explState.has(key)) { _explBind(mount); _explRender(mount, _explState.get(key)); return; }
    const st = {
      key, sessionId, version, newer: null,
      projectId: mount.dataset.projectId || '',
      claudeSessionId: mount.dataset.claudeSessionId || '',
      hasBrief: mount.dataset.hasBrief !== '0',
      mode: null, status: 'draft', message: '', idEdited: false, projects: null,
      draft: { name: '', id: '', domain: 'general', folder: '', description: '', backlog_text: '', project_id: '' },
    };
    _explState.set(key, st);
    _explBind(mount);
    _explRender(mount, st);
  });
  sids.forEach((sid) => _explMarkSuperseded(sid));
}

// Live per-line path, mirroring _handleTeamLine's contract in appendAgentLine
// (conversation.js): a single terminal line, no start/end buffering needed.
// Returns true when the line was the marker and has been intercepted.
function _handleExplorationLine(sessionId, text, el) {
  if (!/^\s*\[clayrune:exploration-ready\]\s*$/.test(text)) return false;
  const version = el.querySelectorAll('.exploration-card-mount').length + 1;
  let projectId = '';
  try { projectId = (agentStatusCache[sessionId] || {}).projectId || ''; } catch (e) {}
  let claudeSessionId = '';
  try { claudeSessionId = (agentStatusCache[sessionId] || {}).claudeSessionId || ''; } catch (e) {}
  // No-brief heuristic: walk back from the last child collecting narration
  // text since the most recent user prompt bubble; a marker with no real
  // brief above it renders no write action (spec §2).
  let briefChars = 0;
  for (let n = el.lastElementChild; n; n = n.previousElementSibling) {
    if (n.classList.contains('agent-line-prompt')) break;
    if (n.classList.contains('agent-line')) briefChars += (n.textContent || '').trim().length;
  }
  const ph = document.createElement('div');
  ph.className = 'exploration-card-mount';
  ph.dataset.sessionId = sessionId;
  ph.dataset.version = String(version);
  ph.dataset.projectId = projectId;
  ph.dataset.claudeSessionId = claudeSessionId;
  ph.dataset.hasBrief = briefChars >= 20 ? '1' : '0';
  el.appendChild(ph);
  mountExplorationCards(ph.parentElement);
  return true;
}

// ── ES-module interop (same contract as team-card.js) ───────────────────────
window.explorationCardPlaceholderHTML = explorationCardPlaceholderHTML;
window.mountExplorationCards = mountExplorationCards;
window._handleExplorationLine = _handleExplorationLine;
