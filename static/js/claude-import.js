// ── Bring in existing Claude Code projects (backlog ba3b73f9) ────────────────
// A new install has an empty grid although ~/.claude/projects already records
// every folder the user has run `claude` in. GET /api/claude-import/scan lists
// them (dry run); the ticked ones are created through POST /api/project/<id>,
// the SAME endpoint the "Create Project" form uses, so the folder-in-use check,
// the install-dir refusal and the fence install all apply unchanged.
//
// One renderer, two hosts: the first-run setup step (first-run.js mounts it into
// its card) and a standalone modal (openClaudeImport, linked from the Create
// Project form). State lives here, so a re-render of the host keeps the ticks.

const CI_DEFAULT_TICKED = 5;   // "nothing pre-ticked beyond the 5 most recent"
const CI_SKIP_LABEL = {
  missing: 'folders that no longer exist',
  registered: 'already in Clayrune',
  worktree: 'Clayrune agent worktrees',
  install_dir: 'the Clayrune install itself',
  home_or_root: 'your home folder',
  no_cwd: 'history folders it could not read',
};

let _ci = _ciFresh();
let _ciRoot = null;       // the element currently showing the importer
let _ciVariant = 'modal'; // 'setup' (inside the first-run card) | 'modal'

function _ciFresh() {
  return { phase: 'idle', data: null, ticked: new Set(), results: [], error: '' };
}

function _ciEsc(s) { return (typeof esc === 'function') ? esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

function _ciAgo(iso) {
  const t = Date.parse(iso);
  if (!isFinite(t)) return '';
  const d = Math.max(0, (Date.now() - t) / 1000);
  if (d < 3600) return Math.max(1, Math.round(d / 60)) + ' min ago';
  if (d < 86400) return Math.round(d / 3600) + ' h ago';
  if (d < 86400 * 60) return Math.round(d / 86400) + ' d ago';
  return new Date(t).toISOString().slice(0, 10);
}

// Start (or restart) the scan. `force` drops what a previous run left behind —
// the first-run wizard calls it on every start so a re-run sees today's disk.
async function claudeImportPrefetch(opts) {
  if (!(opts && opts.force) && (_ci.phase === 'loading' || _ci.phase === 'ready')) return;
  _ci = _ciFresh();
  _ci.phase = 'loading';
  _ciRender();
  try {
    const res = await fetch(API_BASE + '/api/claude-import/scan');
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || `HTTP ${res.status}`);
    _ci.data = data;
    _ci.ticked = new Set(data.candidates.slice(0, CI_DEFAULT_TICKED).map(c => c.path));
    _ci.phase = 'ready';
  } catch (e) {
    _ci.error = String((e && e.message) || e);
    _ci.phase = 'error';
  }
  _ciRender();
}

// True when there is nothing to offer, so the setup wizard can leave the step
// out. A failed scan counts: a first run must not be held up by it.
function claudeImportKnownEmpty() {
  if (_ci.results.length) return false;
  if (_ci.phase === 'error') return true;
  return _ci.phase === 'ready' && !(_ci.data && _ci.data.candidates.length);
}

function claudeImportMount(el, opts) {
  _ciRoot = el || null;
  _ciVariant = (opts && opts.variant) || 'modal';
  if (_ci.phase === 'idle') claudeImportPrefetch();
  else _ciRender();
}

function _ciSkippedLine() {
  const sk = (_ci.data && _ci.data.skipped) || {};
  const parts = Object.keys(CI_SKIP_LABEL).filter(k => sk[k] > 0).map(k => `${sk[k]} ${CI_SKIP_LABEL[k]}`);
  return parts.length ? `<div class="ci-skipped">Left out: ${_ciEsc(parts.join(', '))}.</div>` : '';
}

function _ciResultsHTML() {
  if (!_ci.results.length) return '';
  const ok = _ci.results.filter(r => r.ok), bad = _ci.results.filter(r => !r.ok);
  let h = `<div class="ci-results" role="status">`;
  if (ok.length) h += `<div class="ci-result-ok">&#x2713; Added ${ok.length} project${ok.length === 1 ? '' : 's'}: ${_ciEsc(ok.map(r => r.name).join(', '))}.</div>`;
  bad.forEach(r => { h += `<div class="ci-result-bad">&#x2717; ${_ciEsc(r.name)} not added: ${_ciEsc(r.error)}</div>`; });
  return h + `</div>`;
}

function _ciBodyHTML() {
  if (_ci.phase === 'idle' || _ci.phase === 'loading') {
    return `<div class="ci-status">Looking through your Claude Code history&hellip;</div>`;
  }
  if (_ci.phase === 'error') {
    return `<div class="ci-status ci-result-bad">Could not read your Claude Code history: ${_ciEsc(_ci.error)}</div>
      <button type="button" class="ci-btn" onclick="claudeImportRetry()">Try again</button>`;
  }
  const list = _ci.data.candidates;
  const adding = _ci.phase === 'adding';
  if (!list.length) {
    return _ciResultsHTML() + `<div class="ci-status">${_ci.results.length ? 'Nothing else to bring in.' : 'No Claude Code projects to bring in.'}</div>` + _ciSkippedLine();
  }
  const n = list.filter(c => _ci.ticked.has(c.path)).length;
  const rows = list.map((c, i) => `
    <label class="ci-row${_ci.ticked.has(c.path) ? ' on' : ''}">
      <input type="checkbox" ${_ci.ticked.has(c.path) ? 'checked' : ''} ${adding ? 'disabled' : ''} onchange="claudeImportToggle(${i},this.checked)">
      <span class="ci-main">
        <span class="ci-name">${_ciEsc(c.name)}</span>
        <span class="ci-path" title="${_ciEsc(c.path)}">${_ciEsc(c.path)}</span>
      </span>
      <span class="ci-meta">${c.session_count} session${c.session_count === 1 ? '' : 's'} &middot; ${_ciEsc(_ciAgo(c.last_activity))}</span>
    </label>`).join('');
  const addCls = _ciVariant === 'setup' ? 'ci-btn' : 'btn-add ci-add';
  return _ciResultsHTML() + `
    <div class="ci-bar">
      <span class="ci-count">${n} of ${list.length} selected</span>
      <span class="ci-bar-links">
        <button type="button" class="ci-link" onclick="claudeImportAll(true)" ${adding ? 'disabled' : ''}>Select all</button>
        <button type="button" class="ci-link" onclick="claudeImportAll(false)" ${adding ? 'disabled' : ''}>Select none</button>
      </span>
    </div>
    <div class="ci-list" id="ci-list">${rows}</div>
    ${_ci.data.truncated ? `<div class="ci-skipped">Showing the ${list.length} most recent of ${_ci.data.total}.</div>` : ''}
    ${_ciSkippedLine()}
    <div class="ci-actions">
      <button type="button" class="${addCls}" onclick="claudeImportAdd()" ${(n === 0 || adding) ? 'disabled' : ''}>${adding ? 'Adding&hellip;' : `Add ${n} project${n === 1 ? '' : 's'}`}</button>
    </div>`;
}

function _ciRender() {
  if (!_ciRoot || !_ciRoot.isConnected) return;
  const list = _ciRoot.querySelector('#ci-list');
  const keep = list ? list.scrollTop : 0;
  _ciRoot.innerHTML = `<div class="ci-root ci-${_ciVariant}">${_ciBodyHTML()}
    <div class="ci-note">Clayrune only reads your Claude Code history. Nothing in <code>~/.claude</code> is changed.</div></div>`;
  const again = _ciRoot.querySelector('#ci-list');
  if (again && keep) again.scrollTop = keep;
}

function claudeImportToggle(i, on) {
  const c = _ci.data && _ci.data.candidates[i];
  if (!c) return;
  if (on) _ci.ticked.add(c.path); else _ci.ticked.delete(c.path);
  _ciRender();
}

function claudeImportAll(on) {
  if (!_ci.data) return;
  _ci.ticked = on ? new Set(_ci.data.candidates.map(c => c.path)) : new Set();
  _ciRender();
}

function claudeImportRetry() { claudeImportPrefetch({ force: true }); }

// Creates each ticked project one at a time through the normal add-project
// endpoint. A project that fails (folder already used, install dir, ...) is
// reported by name with the server's own reason and left in the list.
async function claudeImportAdd() {
  if (_ci.phase !== 'ready' || !_ci.data) return;
  const picks = _ci.data.candidates.filter(c => _ci.ticked.has(c.path));
  if (!picks.length) return;
  _ci.phase = 'adding';
  _ciRender();
  const results = [];
  const added = new Set();
  const used = new Set((typeof allProjects !== 'undefined' && Array.isArray(allProjects) ? allProjects : []).map(p => p.id));
  for (const c of picks) {
    let id = c.id, n = 2;
    // POST /api/project/<id> on an id that exists UPDATES that project, so an id
    // taken since the scan (or by an earlier pick) must never be reused.
    while (used.has(id)) id = `${c.id}_${n++}`;
    try {
      const res = await fetch(API_BASE + `/api/project/${encodeURIComponent(id)}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: c.name, domain: 'general', status: 'active', project_path: c.path }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.ok) { used.add(id); added.add(c.path); results.push({ ok: true, name: c.name, id }); }
      else results.push({ ok: false, name: c.name, error: data.error || `HTTP ${res.status}` });
    } catch (e) {
      results.push({ ok: false, name: c.name, error: String((e && e.message) || e) });
    }
  }
  _ci.data.candidates = _ci.data.candidates.filter(c => !added.has(c.path));
  _ci.ticked = new Set([..._ci.ticked].filter(p => !added.has(p)));
  _ci.results = results;
  _ci.phase = 'ready';
  _ciRender();
  try { if (typeof refreshSilent === 'function') await refreshSilent(); } catch (_) { /* grid refresh is cosmetic */ }
}

// Standalone surface, reachable any time (Create Project form links here).
function openClaudeImport() {
  const modalId = '__claude_import';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  content.innerHTML = `
    <div class="modal-header" style="padding:16px 24px 12px 28px;border-radius:6px 6px 0 0">
      <div class="modal-window-controls">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
      <h2 style="margin:0;font-size:18px;font-weight:700;color:var(--text)">Bring in your Claude Code projects</h2>
    </div>
    <div class="ci-modal-body">
      <div class="ci-lead">These are folders you have already used with Claude Code. Tick the ones you want on your dashboard.</div>
      <div id="claude-import-root"></div>
    </div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  if (typeof mcPushSurfaceHistory === 'function') mcPushSurfaceHistory();
  centerModalElement(win);
  focusModal(modalId);
  // Always rescan: what is on disk, and what is registered, may have changed.
  _ciRoot = win.querySelector('#claude-import-root');
  _ciVariant = 'modal';
  claudeImportPrefetch({ force: true });
}

// ── interop: window re-exposure for inline/generated/cross-module callers ──
window.claudeImportPrefetch = claudeImportPrefetch;   // interop: first-run.js startFirstRun
window.claudeImportKnownEmpty = claudeImportKnownEmpty; // interop: first-run.js step skip()
window.claudeImportMount = claudeImportMount;         // interop: first-run.js step onEnter
window.openClaudeImport = openClaudeImport;           // interop: Create Project form link
window.claudeImportToggle = claudeImportToggle;       // interop: generated onchange
window.claudeImportAll = claudeImportAll;             // interop: generated onclick
window.claudeImportAdd = claudeImportAdd;             // interop: generated onclick
window.claudeImportRetry = claudeImportRetry;         // interop: generated onclick
