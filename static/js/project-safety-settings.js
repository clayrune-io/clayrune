// Project "Safety" dialog (backlog ecc5a845, MC-1179 pass B14 / BX4).
//
// mc/project_safety_settings.py names the project keys an agent may never write.
// Seven of them are the merge gate, the cold reviewer, the landing check and the
// worktree controls; they had no control in the product, so a human could only
// switch them on by editing data/projects/<id>.json. This dialog is that control.
//
// It saves through the EXISTING POST /api/project/<id>, which already refuses an
// agent caller (refuse_agent_safety_write). Nothing here adds a route or loosens
// a gate. Only keys the person changed are sent.
//
// Effective values are read from the code that consumes each key:
//   merge_requires_review / cold_review_required  strict `is True`  (mc/merge_review_gate.py, mc/cold_review.py)
//   verify_command            string or list of shell commands      (mc/landing_gate.py settings())
//   verify_timeout_seconds    positive number, default 1200         (mc/landing_gate.py DEFAULT_TIMEOUT_SECONDS)
//   worktree_isolation        only `false` opts out; the global
//                             worktree_isolation_enabled must also be on (mc/blueprints/agent_routes.py)
//   worktree_base_branch      falls back to code_sync_branch, then master (mc/agent_worktree.py _base_ref)
//   worktree_shared_runtime   list; empty means the default paths  (mc/agent_worktree.py DEFAULT_SHARED_RUNTIME)
// The two defaults below are copied from that code; tests/test_project_safety_settings_ui.py
// fails if either drifts.

const OVERLAY_ID = 'project-safety-overlay';
const DEFAULT_TIMEOUT_SECONDS = 1200;
const DEFAULT_SHARED_RUNTIME = ['.venv', 'venv', 'node_modules', 'data/projects', 'data/uploads',
  'config.json', 'data/settings.json', '.mcp.json'];
const FALLBACK_BASE_BRANCH = 'master';

let _pid = null;
let _globalWorktree = null;   // true / false from GET /api/config; null = could not read it

const _esc = (s) => (typeof esc === 'function' ? esc(s) : String(s ?? ''));

function _project(pid) {
  return (typeof allProjects === 'undefined' ? [] : allProjects).find(x => x.id === pid) || null;
}

// A stored verify_command is one string or a list of strings; the dialog shows
// one command per line either way.
function _verifyLines(v) {
  if (typeof v === 'string') return v.trim() ? [v.trim()] : [];
  if (Array.isArray(v)) return v.filter(c => typeof c === 'string' && c.trim()).map(c => c.trim());
  return [];
}

function _lines(text) {
  return String(text || '').split(/\r?\n/).map(l => l.trim()).filter(Boolean);
}

function _timeoutOf(p) {
  const t = p.verify_timeout_seconds;
  if (t === undefined || t === null) return { value: DEFAULT_TIMEOUT_SECONDS, invalid: false };
  if (typeof t === 'number' && Number.isFinite(t) && t > 0) return { value: t, invalid: false };
  return { value: '', invalid: true };   // hand-edited garbage: the gate rejects it, say so
}

// What the dialog shows for a project record, in one place so Save can diff against it.
function _current(p) {
  const to = _timeoutOf(p);
  return {
    merge_requires_review: p.merge_requires_review === true,
    cold_review_required: p.cold_review_required === true,
    verify_command: _verifyLines(p.verify_command),
    verify_timeout_seconds: to.value,
    timeoutInvalid: to.invalid,
    worktree_isolation: p.worktree_isolation !== false,
    worktree_base_branch: typeof p.worktree_base_branch === 'string' ? p.worktree_base_branch.trim() : '',
    worktree_shared_runtime: Array.isArray(p.worktree_shared_runtime)
      ? p.worktree_shared_runtime.map(String).filter(s => s.trim()) : [],
  };
}

function _row(id, label, hint, control, stack) {
  return `<div class="settings-row"${stack ? ' style="align-items:flex-start;flex-direction:column;gap:6px"' : ''}>
    <div><label class="settings-label" for="${id}">${label}</label><div class="settings-hint">${hint}</div></div>${control}</div>`;
}

function _render(p) {
  const cur = _current(p);
  const box = document.getElementById('project-safety-box');
  const syncBranch = (typeof p.code_sync_branch === 'string' && p.code_sync_branch.trim()) || '';
  const branchFallback = syncBranch || FALLBACK_BASE_BRANCH;
  const taStyle = 'width:100%;box-sizing:border-box;background:var(--bg);border:1px solid var(--border);'
    + 'color:var(--text);border-radius:6px;padding:8px;font-size:12px;font-family:\'JetBrains Mono\',monospace;resize:vertical';
  const globalNote = _globalWorktree === false
    ? ' <strong>The global worktree switch is off, so agents share the project folder whatever this says.</strong>'
    : _globalWorktree === null ? ' (Could not read the global worktree switch.)' : '';
  box.innerHTML = `
    <div class="mc-dialog-header">
      <span>Safety &mdash; ${_esc(p.name || p.id)}</span>
      <button class="mc-dialog-close" id="pss-close" title="Close">&#10005;</button>
    </div>
    <div class="settings-hint" style="margin-bottom:6px">Human-only: agents cannot change these. They apply to this project and take effect on the next landing or dispatch.</div>
    <div class="settings-section-title" style="margin-top:8px">Landing agent work</div>
    ${_row('pss-merge_requires_review', 'Require review before merge',
      'Agent work is held at &ldquo;awaiting review&rdquo; until a review of its exact commit passes; nothing lands on the base branch before that.',
      `<input type="checkbox" id="pss-merge_requires_review"${cur.merge_requires_review ? ' checked' : ''}>`)}
    ${_row('pss-cold_review_required', 'Require a cold review',
      'The pass must come from the server&rsquo;s cold reviewer, which sees only the backlog ask and the diff; a hand-recorded pass does not count.',
      `<input type="checkbox" id="pss-cold_review_required"${cur.cold_review_required ? ' checked' : ''}>`)}
    ${_row('pss-verify_command', 'Verify before landing',
      'Shell commands run, in order, on the exact merged result before it lands; any failure blocks the landing. One per line, blank for none.',
      `<textarea id="pss-verify_command" rows="3" spellcheck="false" placeholder="python -m pytest -q" style="${taStyle}">${_esc(cur.verify_command.join('\n'))}</textarea>`, true)}
    ${_row('pss-verify_timeout_seconds', 'Verify timeout (seconds)',
      `How long each verify command may run before it is stopped. Default ${DEFAULT_TIMEOUT_SECONDS}.${cur.timeoutInvalid ? ' <strong>The stored value is not a positive number, so the landing check rejects it until you save a valid one.</strong>' : ''}`,
      `<input class="settings-input" id="pss-verify_timeout_seconds" type="number" min="1" step="any" value="${_esc(cur.verify_timeout_seconds)}" style="width:90px">`)}
    <div class="settings-section-title" style="margin-top:12px">Worktrees</div>
    ${_row('pss-worktree_isolation', 'Isolate agents in their own worktree',
      `Each agent works in a separate git worktree so parallel agents do not overwrite each other. Unticking opts this project out.${globalNote}`,
      `<input type="checkbox" id="pss-worktree_isolation"${cur.worktree_isolation ? ' checked' : ''}>`)}
    ${_row('pss-worktree_base_branch', 'Merge-back branch',
      `The branch agent work merges back into. Blank uses ${syncBranch ? 'the sync branch' : 'the default'} (${_esc(branchFallback)}).`,
      `<input class="settings-input" id="pss-worktree_base_branch" type="text" spellcheck="false" value="${_esc(cur.worktree_base_branch)}" placeholder="${_esc(branchFallback)}" style="width:150px;text-align:left">`)}
    ${_row('pss-worktree_shared_runtime', 'Shared runtime paths',
      'Gitignored paths linked or copied into each worktree, one per line. Blank uses the default list shown as the placeholder.',
      `<textarea id="pss-worktree_shared_runtime" rows="3" spellcheck="false" placeholder="${_esc(DEFAULT_SHARED_RUNTIME.join('\n'))}" style="${taStyle}">${_esc(cur.worktree_shared_runtime.join('\n'))}</textarea>`, true)}
    <div id="pss-error" role="alert" style="color:var(--red-text);font-size:12px;margin-top:8px;min-height:16px"></div>
    <div class="mc-dialog-footer">
      <button class="btn-dispatch" id="pss-cancel" style="background:var(--surface3);border-color:var(--border2);color:var(--text)">Cancel</button>
      <button class="btn-add" id="pss-save">Save</button>
    </div>`;
  box.querySelector('#pss-close').addEventListener('click', close);
  box.querySelector('#pss-cancel').addEventListener('click', close);
  box.querySelector('#pss-save').addEventListener('click', save);
}

function _overlay() {
  let ov = document.getElementById(OVERLAY_ID);
  if (ov) return ov;
  ov = document.createElement('div');
  ov.id = OVERLAY_ID;
  ov.className = 'mc-dialog-overlay';
  ov.innerHTML = '<div class="mc-dialog" id="project-safety-box" role="dialog" aria-label="Project safety settings"></div>';
  ov.addEventListener('click', (e) => { if (e.target === ov) close(); });
  document.body.appendChild(ov);
  return ov;
}

async function open(projectId) {
  const p = _project(projectId);
  if (!p) return;
  _pid = projectId;
  _globalWorktree = null;
  try {
    const r = await fetch(API_BASE + '/api/config');
    if (r.ok) _globalWorktree = !!(await r.json()).worktree_isolation_enabled;
  } catch (e) { /* the dialog says it could not read the switch */ }
  const ov = _overlay();
  _render(p);
  ov.classList.add('visible');
}

function close() {
  const ov = document.getElementById(OVERLAY_ID);
  if (ov) ov.classList.remove('visible');
  _pid = null;
}

function _showError(msg) {
  const el = document.getElementById('pss-error');
  if (el) el.textContent = msg;
}

// Read the form, diff against the stored record, return {body} or {error}.
function _collect(p) {
  const cur = _current(p);
  const $ = (id) => document.getElementById('pss-' + id);
  const body = {};
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

  const merge = $('merge_requires_review').checked;
  if (merge !== cur.merge_requires_review) body.merge_requires_review = merge;
  const cold = $('cold_review_required').checked;
  if (cold !== cur.cold_review_required) body.cold_review_required = cold;

  const cmds = _lines($('verify_command').value);
  if (cmds.length > 128) return { error: 'Verify: at most 128 commands.' };
  if (cmds.some(c => c.includes('\0'))) return { error: 'Verify: a command contains a NUL character.' };
  if (!same(cmds, cur.verify_command)) body.verify_command = cmds.length === 0 ? null : cmds.length === 1 ? cmds[0] : cmds;

  const rawTimeout = $('verify_timeout_seconds').value.trim();
  const timeout = Number(rawTimeout);
  // Never blank: the landing check treats a null timeout as invalid, and there
  // is no way to delete a key through the project route, so a number is saved.
  if (rawTimeout === '' || !Number.isFinite(timeout) || timeout <= 0) {
    return { error: `Verify timeout: enter a number of seconds greater than 0 (default ${DEFAULT_TIMEOUT_SECONDS}).` };
  }
  if (timeout !== cur.verify_timeout_seconds) body.verify_timeout_seconds = timeout;

  const iso = $('worktree_isolation').checked;
  if (iso !== cur.worktree_isolation) body.worktree_isolation = iso;

  const branch = $('worktree_base_branch').value.trim();
  if (/\s/.test(branch) || branch.startsWith('-')) {
    return { error: 'Merge-back branch: a branch name has no spaces and does not start with "-".' };
  }
  if (branch !== cur.worktree_base_branch) body.worktree_base_branch = branch;

  const runtime = _lines($('worktree_shared_runtime').value);
  if (!same(runtime, cur.worktree_shared_runtime)) body.worktree_shared_runtime = runtime;
  return { body };
}

async function save() {
  const pid = _pid;
  const p = _project(pid);
  if (!p) { close(); return; }
  const { body, error } = _collect(p);
  if (error) { _showError(error); return; }
  if (!Object.keys(body).length) { close(); return; }   // nothing changed: not a write
  const btn = document.getElementById('pss-save');
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(API_BASE + `/api/project/${encodeURIComponent(pid)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
    if (!r.ok) {
      const d = await r.json().catch(() => ({}));
      _showError(d.error || `Save failed (${r.status})`);
      return;
    }
  } catch (e) {
    _showError('Save failed: ' + (e && e.message ? e.message : e));
    return;
  } finally {
    if (btn) btn.disabled = false;
  }
  close();
  if (typeof showToast === 'function') showToast('Safety settings saved', 3000);
  if (typeof refreshSilent === 'function') await refreshSilent();
}

window.ProjectSafetySettings = { open, close };
window.openProjectSafetyDialog = open;
