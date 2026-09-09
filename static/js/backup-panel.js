// ── Backup / Restore / Import (docs/BACKUP_EXPORT_SPEC.md) ──────────────────
//
// Full-install backup+restore (Phase 1) and per-project export/import
// (Phase 2) already shipped as API/CLI-only surfaces — mc/backup.py +
// mc/blueprints/backup_routes.py — with zero frontend wiring (grep static/
// for 'backup' returned nothing before this file). This is that UI.
//
// Restore points (spec §4.3, Phase 3a) are being built server-side in a
// parallel worktree and have no route yet — intentionally absent here.
//
// State lives on the modal's openModals entry (._backup / ._export), same
// convention as openMCPEditor's ._mcpEditing — this file is an ES module, so
// top-level state would not survive multiple concurrent modals cleanly, and
// per-project export modals can be open for more than one project at once.

const DEFAULT_BACKUP_CATEGORIES = ['records', 'artifacts', 'media', 'transcripts', 'unprotected'];

const BACKUP_CATEGORY_LABELS = {
  records: 'Records + memory',
  artifacts: 'Agent artifacts',
  media: 'Media / uploads',
  transcripts: 'Transcripts',
  unprotected: 'Unprotected work files',
};

function _fmtBackupBytes(n) {
  if (n == null) return '';
  if (n < 1024) return n + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = n, i = -1;
  do { v /= 1024; i++; } while (v >= 1024 && i < units.length - 1);
  return v.toFixed(v < 10 ? 2 : 1) + ' ' + units[i];
}

function _newBackupChecklist() {
  return { records: true, artifacts: true, media: true, transcripts: true, unprotected: true };
}

// ── Main Backup & Restore surface (sidebar → openBackupSurface) ─────────────

function openBackupSurface(tab) {
  const modalId = '__backup';
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    if (tab) { entry._backup.tab = tab; _backupRender(modalId); }
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 820);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;gap:12px;padding:14px 24px 0 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">
        <svg class="menu-svg" style="display:inline-block;vertical-align:-3px"><use href="#ic-backup"/></svg> Backup &amp; Restore
      </span>
      <div class="ext-tabs">
        <button class="ext-tab" data-tab="backup" onclick="switchBackupTab('backup')">Backup</button>
        <button class="ext-tab" data-tab="restore" onclick="switchBackupTab('restore')">Restore</button>
        <button class="ext-tab" data-tab="import" onclick="switchBackupTab('import')">Import project</button>
      </div>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px;margin-left:auto">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="backup-body" style="padding:4px 24px 20px 28px;max-height:70vh;overflow-y:auto"></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, {
    projectId: null, element: win, minimized: false, zIndex: z,
    _backup: {
      tab: tab || 'backup',
      checklist: _newBackupChecklist(),
      sizePreview: null, sizeLoading: false,
      // Per-category disclosure. COLLAPSED by default and deliberately not
      // persisted: 'unprotected' alone is dozens of rows on a real install,
      // and auto-expanding it pushed the Create button off-screen.
      expanded: { records: false, artifacts: false, media: false, transcripts: false, unprotected: false },
      label: '', creating: false, createResult: null, createError: null,
      createNotice: null,
      // Async create (POST /api/backup/create {"async":true}) — a ~48GB
      // archive is minutes of writing, so the job is polled for real byte
      // progress instead of parking on a disabled button.
      job: null,
      // Reattach (GET /api/backup/jobs). The job_id used to live ONLY here,
      // so closing the panel — or reloading the page — orphaned a running
      // 48GB write: idle form on screen, no way to watch or cancel it. The
      // server is the only thing that outlives both, so we ask it on open.
      // Deliberately not sessionStorage: that dies with the tab too.
      jobsProbe: { done: false },
      // Most recent archive in the DEFAULT destination, so the panel opens
      // saying when you last backed up rather than nothing at all.
      lastBackup: { loaded: false, loading: false, item: null },
      // Destination override (MC-945 follow-up): `override` is per-action —
      // it's sent as `dest_dir` on the next create/list call, never persisted.
      // The persisted default lives in Settings (backup_dest_dir); this is
      // just where the effective value (for the placeholder) comes from.
      destDir: { loaded: false, configured: null, effective: null, override: '' },
      restoreList: { loaded: false, loading: false, items: [] },
      restoreResult: null, restoreError: null,
      importState: {
        stage: 'input', path: '', busy: false, error: null, dryRun: null,
        projectResolution: 'skip', scheduleResolution: 'skip',
        newProjectPath: '', vaultPassphrase: '',
        localArchives: { loaded: false, loading: false, items: [] },
        result: null,
      },
    },
  });
  centerModalElement(win);
  focusModal(modalId);
  _backupRender(modalId);
}

function switchBackupTab(tab) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.tab = tab;
  _backupRender('__backup');
}

function _backupRender(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  entry.element.querySelectorAll('.ext-tab').forEach(t =>
    t.classList.toggle('active', t.dataset.tab === entry._backup.tab));
  const body = entry.element.querySelector('#backup-body');
  if (!body) return;
  const st = entry._backup;
  if (st.tab === 'restore') {
    body.innerHTML = _backupRestoreTabHTML(st);
    _backupLoadList(modalId);
  } else if (st.tab === 'import') {
    body.innerHTML = _backupImportTabHTML(st);
    if (st.importState.stage === 'input') _backupImportLoadLocal(modalId);
  } else {
    body.innerHTML = _backupCreateTabHTML(st);
    _backupLoadSizePreview(modalId);
    _backupLoadLastBackup(modalId);
  }
  _backupLoadDestDir(modalId);
  _backupProbeJobs(modalId);
}

// ── Reattach to a create that is already running ────────────────────────────
//
// Runs once per opened panel. If the server still has a live job we drop
// straight into the progress + Cancel state; if the only thing it has is a
// job that finished while the panel was closed, we show that result rather
// than a blank form. Either way the source of truth is the SERVER — this
// has to survive a full page reload, not just a modal close.
async function _backupProbeJobs(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || entry._backup.jobsProbe.done) return;
  entry._backup.jobsProbe.done = true;
  let data;
  try {
    const res = await fetch(API_BASE + '/api/backup/jobs');
    if (!res.ok) return;
    data = await res.json();
  } catch (e) {
    return;   // no reattach is a degraded panel, never a broken one
  }
  const st = entry._backup;
  if (st.job || st.creating) return;             // this panel already started one
  const live = (data.active || [])[0];
  if (live) {
    st.creating = true;
    st.job = live;
    st.createResult = null; st.createError = null; st.createNotice = null;
    _backupRender(modalId);
    _backupPollJob(modalId, live.job_id);
    return;
  }
  const last = (data.recent || [])[0];
  if (last && last.status === 'done' && last.result) {
    st.createResult = last.result;
    st.createNotice = 'This finished while the panel was closed.';
    _backupRender(modalId);
  }
}

// The newest archive sitting in the DEFAULT destination. Same endpoint the
// Restore tab lists from, minus any per-action override — "where your
// backups normally land" is the honest thing to report on the Backup tab.
async function _backupLoadLastBackup(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || entry._backup.lastBackup.loaded || entry._backup.lastBackup.loading) return;
  entry._backup.lastBackup.loading = true;
  try {
    const res = await fetch(API_BASE + '/api/backup/list');
    const data = await res.json();
    const items = (res.ok && data.backups) ? data.backups : [];
    entry._backup.lastBackup.item = items.find(b => b.kind === 'full' && !b.error) || null;
  } catch (e) {
    entry._backup.lastBackup.item = null;
  } finally {
    entry._backup.lastBackup.loaded = true;
    entry._backup.lastBackup.loading = false;
    _backupRender(modalId);
  }
}

// ── Destination override (create + restore/list tabs) ───────────────────────

async function _backupLoadDestDir(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || entry._backup.destDir.loaded) return;
  try {
    const res = await fetch(API_BASE + '/api/backup/dest-dir');
    const data = await res.json();
    entry._backup.destDir.configured = data.configured || null;
    entry._backup.destDir.effective = data.effective || null;
  } catch (e) {
    // Leave effective/configured null — the field still works as a plain
    // override input, just without a placeholder to show what "default" means.
  } finally {
    entry._backup.destDir.loaded = true;
    _backupRender(modalId);
  }
}

function _backupDestDirFieldHTML(st, opts = {}) {
  const dd = st.destDir;
  const placeholder = dd.effective || (dd.loaded ? '' : 'loading…');
  return `
    <div style="margin-bottom:12px">
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">
        Destination ${opts.label || ''} <span style="opacity:.7">(override for this action only — set the default in Settings &rarr; System)</span>
      </label>
      <div style="display:flex;gap:6px;align-items:center">
        <input class="path-input" type="text" value="${esc(dd.override)}" placeholder="${esc(placeholder)}"
          oninput="_backupSetDestDirOverride(this.value)"
          style="flex:1;box-sizing:border-box;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
        <button class="btn-browse" onclick="_backupBrowseDestDir()" title="Browse for folder">Browse&hellip;</button>
      </div>
    </div>`;
}

// The folder picker from project-forms.js in callback mode — same dialog and
// same GET /api/browse/folders the project path field uses. Deliberately NOT
// the File System Access API or a native dialog: the browser is often not on
// the machine the backup is written to (phone, remote access).
function _backupBrowseDestDir() {
  const entry = openModals.get('__backup');
  if (!entry) return;
  const dd = entry._backup.destDir;
  openFolderPicker(null, {
    startPath: (dd.override || dd.effective || ''),
    title: 'Choose backup destination',
    onSelect: (path) => { _backupSetDestDirOverride(path); _backupRender('__backup'); },
  });
}

function _backupSetDestDirOverride(v) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.destDir.override = v;
  // A changed override invalidates the currently-loaded restore list so
  // switching tabs (or hitting "Refresh list") re-fetches from the new dir.
  entry._backup.restoreList.loaded = false;
}

// ── Backup tab (create) ──────────────────────────────────────────────────────

// One row per category. Every category carries the same disclosure — the
// backend returns `directories` for all of them now (mc/backup.py
// size_preview), not just 'unprotected' — so there is exactly one breakdown
// pattern in this panel, not two.
function _backupCategoryRowHTML(cat, state) {
  const sp = state.sizePreview;
  const c = sp && sp.categories && sp.categories[cat];
  const bytes = c ? _fmtBackupBytes(c.bytes) : (state.sizeLoading ? '…' : '');
  const dirs = (c && c.directories) || [];
  const expanded = !!state.expanded[cat];
  // preventDefault/stopPropagation: the row is a <label> wrapping the
  // checkbox, so without them clicking the caret would also tick the box.
  const caret = dirs.length
    ? `<span onclick="event.preventDefault();event.stopPropagation();_backupToggleExpand('${cat}')"
         title="${expanded ? 'Collapse' : 'Expand'}"
         style="display:inline-block;width:12px;text-align:center;cursor:pointer;color:var(--text-faint);transform:rotate(${expanded ? 90 : 0}deg);transition:transform .12s">&#9656;</span>`
    : '<span style="display:inline-block;width:12px"></span>';
  const dirLines = (expanded && dirs.length) ? `
    <div style="margin:2px 0 6px 30px;font-size:10px;color:var(--text-faint)">
      ${dirs.map(d => `
        <div style="display:flex;justify-content:space-between;gap:8px;${d.bytes > 1073741824 ? 'color:var(--amber)' : ''}">
          <span style="word-break:break-all">${esc(d.path)}${d.bytes > 1073741824 ? ' &#x26A0;' : ''}</span>
          <span style="white-space:nowrap">${_fmtBackupBytes(d.bytes)}</span>
        </div>`).join('')}
    </div>` : '';
  return `<label style="display:flex;align-items:center;gap:6px;padding:6px 4px;cursor:pointer;border-bottom:1px solid var(--border)">
      <input type="checkbox" ${state.checklist[cat] ? 'checked' : ''} onchange="_backupToggleCategory('${cat}',this.checked)">
      ${caret}
      <span style="flex:1;font-size:13px;color:var(--text)">${BACKUP_CATEGORY_LABELS[cat]}${
        dirs.length ? ` <span style="color:var(--text-faint);font-size:10px">(${dirs.length})</span>` : ''}</span>
      <span style="font-size:11px;color:var(--text-faint);font-family:var(--mono)">${bytes}</span>
    </label>${dirLines}`;
}

function _backupToggleExpand(cat) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.expanded[cat] = !entry._backup.expanded[cat];
  _backupRender('__backup');
}

function _backupCreateTabHTML(state) {
  const sp = state.sizePreview;
  const total = DEFAULT_BACKUP_CATEGORIES.reduce((sum, cat) => {
    if (!state.checklist[cat]) return sum;
    const c = sp && sp.categories && sp.categories[cat];
    return sum + (c ? c.bytes || 0 : 0);
  }, 0);
  const rows = DEFAULT_BACKUP_CATEGORIES.map(cat => _backupCategoryRowHTML(cat, state)).join('');
  const noneChecked = DEFAULT_BACKUP_CATEGORIES.every(c => !state.checklist[c]);

  const running = !!(state.job && state.job.status !== 'done' && state.job.status !== 'error'
    && state.job.status !== 'cancelled');

  return `
    ${_backupLastBackupHTML(state)}
    <div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;line-height:1.5">
      Everything is included by default — untick what you don't want. Sizes are measured live before anything
      is written. You choose the folder when you hit Create.
    </div>
    <div style="font-size:11px;font-weight:700;color:var(--text);margin-bottom:2px">1. What to include</div>
    <div>${rows}</div>
    <div style="display:flex;justify-content:space-between;align-items:center;margin-top:10px;padding-top:8px;border-top:2px solid var(--border)">
      <span style="font-size:12px;color:var(--text-faint)">Total</span>
      <span style="font-size:13px;font-weight:700;color:var(--text);font-family:var(--mono)">${state.sizeLoading ? 'measuring…' : _fmtBackupBytes(total)}</span>
    </div>
    <div style="margin-top:12px">
      <label style="display:block;font-size:11px;font-weight:700;color:var(--text);margin-bottom:4px">2. Label (optional)</label>
      <input type="text" value="${esc(state.label)}" placeholder="before-migration"
        oninput="_backupSetLabel(this.value)"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
    </div>
    ${noneChecked ? '<div style="margin-top:8px;font-size:11px;color:var(--red-text)">Untick everything and there is nothing to back up — pick at least one category.</div>' : ''}
    ${state.createError ? `<div style="margin-top:10px;font-size:12px;color:var(--red-text)">${esc(state.createError)}</div>` : ''}
    ${state.createNotice ? `<div style="margin-top:10px;font-size:12px;color:var(--text-faint)">${esc(state.createNotice)}</div>` : ''}
    ${state.createResult ? _backupCreateResultHTML(state.createResult) : ''}
    <div id="backup-job-progress">${state.job ? _backupJobProgressHTML(state.job) : ''}</div>
    ${running ? '' : `<div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button class="btn-add" ${(noneChecked || state.creating) ? 'disabled' : ''} onclick="_backupCreate()">${state.creating ? 'Creating…' : 'Create backup…'}</button>
    </div>`}`;
}

// Most-recent-backup summary. Everything here comes from the archive's own
// manifest via list_backups(), so it describes what is actually on disk —
// not what this panel happens to remember doing.
function _backupLastBackupHTML(state) {
  const lb = state.lastBackup;
  if (!lb.loaded) {
    return `<div style="margin-bottom:12px;font-size:11px;color:var(--text-faint)">Checking for previous backups…</div>`;
  }
  if (!lb.item) {
    return `<div style="margin-bottom:12px;padding:8px 12px;border:1px dashed var(--border);border-radius:6px;font-size:11px;color:var(--text-faint)">
      No backup has been made yet.</div>`;
  }
  const b = lb.item;
  const cats = Object.entries(b.categories || {}).filter(([k, v]) => k !== 'vault' && v).map(([k]) => k);
  const when = b.created_at ? new Date(b.created_at).toLocaleString() : 'unknown date';
  return `<div style="margin-bottom:12px;padding:9px 12px;background:var(--surface2);border:1px solid var(--border);border-radius:6px;font-size:11px">
    <div style="display:flex;gap:8px;align-items:baseline;flex-wrap:wrap">
      <span style="font-weight:700;color:var(--text)">Last backup</span>
      <span style="color:var(--text-faint)">${esc(when)}</span>
      <span style="margin-left:auto;font-family:var(--mono);color:var(--text)">${_fmtBackupBytes(b.bytes)}</span>
    </div>
    <div style="margin-top:3px;color:var(--text-faint);font-family:var(--mono);word-break:break-all">${esc(b.path)}</div>
    <div style="margin-top:3px;color:var(--text-faint)">${esc(cats.join(', ') || 'no categories')}${
      b.file_count ? ` &middot; ${b.file_count} files` : ''}${
      b.warning_count ? ` &middot; ${b.warning_count} warning${b.warning_count === 1 ? '' : 's'}` : ''}</div>
  </div>`;
}

function _backupJobProgressHTML(job) {
  const pct = job.total_bytes ? Math.min(100, Math.floor((job.bytes_written / job.total_bytes) * 100)) : 0;
  // Cancel lives INSIDE #backup-job-progress on purpose: the poll patches
  // only that subtree every 700ms (a full re-render would eat the caret in
  // the Label field), so a button anywhere else would never track the state.
  const cancelling = job.status === 'cancelling' || job.cancel_requested;
  return `<div style="margin-top:12px">
    <div style="display:flex;justify-content:space-between;gap:8px;font-size:11px;color:var(--text-faint);margin-bottom:4px">
      <span>${_fmtBackupBytes(job.bytes_written || 0)} of ${_fmtBackupBytes(job.total_bytes || 0)}
        &middot; ${job.files_written || 0}/${job.total_files || 0} files${
        job.warnings_count ? ` &middot; ${job.warnings_count} warning${job.warnings_count === 1 ? '' : 's'}` : ''}</span>
      <span style="font-family:var(--mono)">${pct}%</span>
    </div>
    <div style="height:6px;background:var(--surface2);border-radius:3px;overflow:hidden">
      <div style="height:100%;width:${pct}%;background:var(--accent);transition:width .3s"></div>
    </div>
    ${job.current_file ? `<div style="margin-top:4px;font-size:10px;color:var(--text-faint);font-family:var(--mono);white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${esc(job.current_file)}</div>` : ''}
    <div style="display:flex;justify-content:flex-end;margin-top:8px">
      <button class="btn-browse" id="backup-cancel-btn" ${cancelling ? 'disabled' : ''}
        style="border-color:var(--red-text,#ef4444);color:var(--red-text,#ef4444)"
        onclick="_backupCancelJob()">${cancelling ? 'Cancelling…' : 'Cancel backup'}</button>
    </div>
  </div>`;
}

// Cooperative: the server flags the job and its write loop stops between
// entries, deleting its own .partial. Nothing is killed, so there is no
// half-written archive and no multi-GB orphan left behind.
async function _backupCancelJob() {
  const modalId = '__backup';
  const entry = openModals.get(modalId);
  if (!entry || !entry._backup.job) return;
  const st = entry._backup;
  const jobId = st.job.job_id;
  st.job = { ...st.job, status: 'cancelling' };   // immediate feedback; the poll confirms it
  const el = entry.element && entry.element.querySelector('#backup-job-progress');
  if (el) el.innerHTML = _backupJobProgressHTML(st.job);
  try {
    const res = await fetch(API_BASE + '/api/backup/create/cancel/' + encodeURIComponent(jobId),
      { method: 'POST' });
    const data = await res.json();
    if (!res.ok) {
      st.createError = data.error || `Cancel failed (HTTP ${res.status})`;
      _backupRender(modalId);
    }
  } catch (e) {
    st.createError = 'Cancel failed: ' + e.message;
    _backupRender(modalId);
  }
}

function _backupCreateResultHTML(r) {
  return `<div style="margin-top:10px;padding:10px 12px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.4);border-radius:6px;font-size:12px">
    <div style="font-weight:700;color:#22c55e;margin-bottom:4px">&#x2713; Created</div>
    <div style="color:var(--text);font-family:var(--mono);word-break:break-all">${esc(r.path)}</div>
    <div style="color:var(--text-faint);margin-top:4px">${r.files_written} files${r.warnings && r.warnings.length ? `, ${r.warnings.length} warning${r.warnings.length === 1 ? '' : 's'}` : ''}</div>
    ${r.warnings && r.warnings.length ? `<details style="margin-top:6px"><summary style="cursor:pointer;color:var(--text-faint);font-size:11px">Show warnings</summary><pre style="margin:6px 0 0;font-size:10px;white-space:pre-wrap">${esc(JSON.stringify(r.warnings, null, 2))}</pre></details>` : ''}
  </div>`;
}

function _backupToggleCategory(cat, checked) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.checklist[cat] = checked;
  _backupRender('__backup');
}

function _backupSetLabel(v) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.label = v;
}

async function _backupLoadSizePreview(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || entry._backup.sizePreview || entry._backup.sizeLoading) return;
  entry._backup.sizeLoading = true;
  try {
    const res = await fetch(API_BASE + '/api/backup/size-preview');
    entry._backup.sizePreview = await res.json();
  } catch (e) {
    entry._backup.sizePreview = null;
  } finally {
    entry._backup.sizeLoading = false;
    _backupRender(modalId);
  }
}

// Create is a two-step flow now: the folder picker opens HERE, seeded at the
// default from Settings → System, and the write starts only once a folder is
// confirmed. Ron's call — a Destination field parked at the top of the form
// is a question asked before the user has decided to do anything.
function _backupCreate() {
  const entry = openModals.get('__backup');
  if (!entry) return;
  const dd = entry._backup.destDir;
  openFolderPicker(null, {
    startPath: (dd.override || dd.effective || ''),
    title: 'Choose backup destination',
    onSelect: (path) => { _backupSetDestDirOverride(path); _backupStartCreate(path); },
  });
}

async function _backupStartCreate(destDir) {
  const modalId = '__backup';
  const entry = openModals.get(modalId);
  if (!entry) return;
  const st = entry._backup;
  st.creating = true; st.createError = null; st.createResult = null;
  st.createNotice = null; st.job = null;
  _backupRender(modalId);
  try {
    const res = await fetch(API_BASE + '/api/backup/create', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        categories: st.checklist, label: st.label || undefined,
        dest_dir: (destDir || '').trim() || undefined,
        async: true,   // => 202 {job_id}; the write runs on a worker thread
      }),
    });
    const data = await res.json();
    if (!res.ok) {
      st.createError = data.error || `Create failed (HTTP ${res.status})`;
      st.creating = false; _backupRender(modalId);
      return;
    }
    st.job = { job_id: data.job_id, status: 'running', files_written: 0, total_files: 0,
               bytes_written: 0, total_bytes: 0, current_file: null, warnings_count: 0 };
    _backupRender(modalId);
    _backupPollJob(modalId, data.job_id);
  } catch (e) {
    st.createError = 'Create failed: ' + e.message;
    st.creating = false;
    _backupRender(modalId);
  }
}

async function _backupPollJob(modalId, jobId) {
  const entry = openModals.get(modalId);
  if (!entry) return;                                   // modal closed — job keeps running server-side
  const st = entry._backup;
  if (!st.job || st.job.job_id !== jobId) return;       // superseded by a newer run
  let data;
  try {
    const res = await fetch(API_BASE + '/api/backup/create/status/' + encodeURIComponent(jobId));
    data = await res.json();
    if (!res.ok) {
      st.createError = data.error || `Lost track of the backup job (HTTP ${res.status})`;
      st.creating = false; st.job = null;
      _backupRender(modalId);
      return;
    }
  } catch (e) {
    setTimeout(() => _backupPollJob(modalId, jobId), 2000);  // transient fetch failure — keep watching
    return;
  }
  st.job = data;
  if (data.status === 'done') {
    st.creating = false;
    st.createResult = data.result;
    st.job = null;
    st.restoreList.loaded = false;
    st.lastBackup.loaded = false;
    _backupRender(modalId);
    return;
  }
  if (data.status === 'cancelled') {
    // The user asked for this — report it as an outcome, not a failure, and
    // say plainly that nothing was left behind.
    st.creating = false;
    st.job = null;
    st.createError = null;
    st.createNotice = 'Backup cancelled — the partial archive was deleted, nothing was written.';
    _backupRender(modalId);
    return;
  }
  if (data.status === 'error') {
    st.creating = false;
    st.createError = data.error || 'Backup failed';
    st.job = null;
    _backupRender(modalId);
    return;
  }
  // Still running: patch ONLY the progress subtree. A full _backupRender here
  // would rebuild the body every 700ms and blow away focus/caret in the Label
  // field while the user is typing.
  const el = entry.element && entry.element.querySelector('#backup-job-progress');
  if (el) el.innerHTML = _backupJobProgressHTML(data);
  else if (st.tab === 'backup') _backupRender(modalId);
  // Any other tab: keep polling silently. Re-rendering the Restore tab every
  // tick because the progress node isn't on screen would throw away its list
  // and its scroll position twice a second.
  setTimeout(() => _backupPollJob(modalId, jobId), 700);
}

// ── Restore tab ───────────────────────────────────────────────────────────────

function _backupRestoreTabHTML(state) {
  const rl = state.restoreList;
  const destField = _backupDestDirFieldHTML(state, { label: 'to list from' }) + `
    <div style="display:flex;justify-content:flex-end;margin:-6px 0 12px">
      <button class="btn-tiny" onclick="_backupRefreshList()">Refresh list</button>
    </div>`;
  if (!rl.loaded) {
    return destField + `<div style="padding:30px 12px;text-align:center;color:var(--text-faint);font-size:12px">Loading backups…</div>`;
  }
  const fullBackups = rl.items.filter(i => i.kind === 'full' && !i.error);
  const listHtml = fullBackups.length
    ? fullBackups.map(b => _backupRestoreRowHTML(b)).join('')
    : `<div style="padding:30px 12px;text-align:center;color:var(--text-faint);font-size:12px">No full-install backups found in this destination.</div>`;
  return destField + `
    <div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;line-height:1.5">
      Restore is per-category and additive — an absent category is left untouched, never deleted to match the
      archive. Restoring does not touch your code, your git history, or anything an agent already did in the
      outside world.
    </div>
    ${listHtml}
    ${state.restoreError ? `<div style="margin-top:10px;font-size:12px;color:var(--red-text)">${esc(state.restoreError)}</div>` : ''}
    ${state.restoreResult ? _backupRestoreResultHTML(state.restoreResult) : ''}`;
}

function _backupRefreshList() {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.restoreList.loaded = false;
  _backupRender('__backup');
}

function _backupRestoreRowHTML(b) {
  const cats = Object.entries(b.categories || {}).filter(([k, v]) => k !== 'vault' && v).map(([k]) => k);
  const date = b.created_at ? new Date(b.created_at).toLocaleString() : '';
  const fname = String(b.path || '').split(/[\\/]/).pop();
  return `<div style="padding:10px 12px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:4px">
    <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap">
      <span style="font-family:var(--mono);font-size:12px;color:var(--text);word-break:break-all">${esc(fname)}</span>
      <span style="margin-left:auto;font-size:11px;color:var(--text-faint)">${_fmtBackupBytes(b.bytes)}</span>
      <button class="btn-tiny" onclick='_backupRestoreConfirm(${JSON.stringify(b.path)})'>Restore</button>
    </div>
    <div style="font-size:10px;color:var(--text-faint)">${esc(date)} &middot; ${esc(cats.join(', ') || 'no categories')}${b.warning_count ? ` &middot; ${b.warning_count} warning${b.warning_count === 1 ? '' : 's'}` : ''}</div>
  </div>`;
}

function _backupRestoreResultHTML(r) {
  const perCat = Object.entries(r.per_category || {}).map(([cat, v]) =>
    `${cat}: ${v.restored} restored${v.conflicts ? `, ${v.conflicts} conflicts` : ''}${v.refused ? `, ${v.refused} refused` : ''}`
  ).join(' · ');
  return `<div style="margin-top:10px;padding:10px 12px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.4);border-radius:6px;font-size:12px">
    <div style="font-weight:700;color:#22c55e;margin-bottom:4px">&#x2713; Restored</div>
    <div style="color:var(--text)">${esc(r.announcement || '')}</div>
    ${perCat ? `<div style="color:var(--text-faint);margin-top:4px">${esc(perCat)}</div>` : ''}
  </div>`;
}

async function _backupLoadList(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || entry._backup.restoreList.loaded || entry._backup.restoreList.loading) return;
  entry._backup.restoreList.loading = true;
  const override = (entry._backup.destDir.override || '').trim();
  const url = API_BASE + '/api/backup/list' + (override ? `?dest_dir=${encodeURIComponent(override)}` : '');
  try {
    const res = await fetch(url);
    const data = await res.json();
    if (!res.ok) {
      entry._backup.restoreList = { loaded: true, loading: false, items: [] };
      entry._backup.restoreError = data.error || `List failed (HTTP ${res.status})`;
    } else {
      entry._backup.restoreList = { loaded: true, loading: false, items: data.backups || [] };
      entry._backup.restoreError = null;
    }
  } catch (e) {
    entry._backup.restoreList = { loaded: true, loading: false, items: [] };
    entry._backup.restoreError = 'List failed: ' + e.message;
  }
  _backupRender(modalId);
}

async function _backupRestoreConfirm(path) {
  const ok = confirm(
    "Restore Clayrune's records and other categories present in this archive.\n\n" +
    'Restore is additive: an absent category is left untouched, never deleted to match the archive. ' +
    "This does not touch your code, your git history, or anything an agent already did in the outside world.\n\n" +
    'Proceed with:\n' + path);
  if (!ok) return;
  const modalId = '__backup';
  const entry = openModals.get(modalId);
  if (!entry) return;
  const st = entry._backup;
  st.restoreError = null; st.restoreResult = null;
  _backupRender(modalId);
  try {
    const res = await fetch(API_BASE + '/api/backup/restore', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    const data = await res.json();
    if (!res.ok) st.restoreError = data.error || `Restore failed (HTTP ${res.status})`;
    else st.restoreResult = data;
  } catch (e) {
    st.restoreError = 'Restore failed: ' + e.message;
  } finally {
    _backupRender(modalId);
  }
}

// ── Import tab (per-project archive, dry-run → collision report → apply) ────

function _backupImportTabHTML(state) {
  const st = state.importState;
  if (st.stage === 'report' && st.dryRun) return _backupImportReportHTML(st);
  if (st.stage === 'done' && st.result) return _backupImportDoneHTML(st);
  return _backupImportInputHTML(st);
}

function _backupImportInputHTML(st) {
  const la = st.localArchives;
  const localOptions = (la.items || []).map(b =>
    `<option value="${esc(b.path)}">${esc(String(b.path).split(/[\\/]/).pop())} (${_fmtBackupBytes(b.bytes)})</option>`).join('');
  return `
    <div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;line-height:1.5">
      Import a project archive created by Export (a project's three-dot menu &rarr; Export). Nothing is written
      until you review the collision report below and confirm.
    </div>
    ${la.loaded && la.items.length ? `
      <div style="margin-bottom:10px">
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Pick a local archive, or type a path below</label>
        <select onchange="_backupImportSetPath(this.value)" style="width:100%;padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">Choose…</option>
          ${localOptions}
        </select>
      </div>` : ''}
    <div>
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Archive path (.crbackup)</label>
      <input type="text" value="${esc(st.path)}" placeholder="C:\\Users\\...\\clayrune-project-....crbackup"
        oninput="_backupImportSetPath(this.value)"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
    </div>
    ${st.error ? `<div style="margin-top:8px;font-size:12px;color:var(--red-text)">${esc(st.error)}</div>` : ''}
    <div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button class="btn-add" ${(!st.path || st.busy) ? 'disabled' : ''} onclick="_backupImportDryRun()">${st.busy ? 'Checking…' : 'Check for collisions →'}</button>
    </div>`;
}

function _backupImportSetPath(v) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.importState.path = v;
}

async function _backupImportLoadLocal(modalId) {
  const entry = openModals.get(modalId);
  if (!entry) return;
  const la = entry._backup.importState.localArchives;
  if (la.loaded || la.loading) return;
  la.loading = true;
  try {
    const res = await fetch(API_BASE + '/api/backup/list');
    const data = await res.json();
    la.items = (data.backups || []).filter(b => b.kind === 'project' && !b.error);
  } catch (e) {
    la.items = [];
  }
  la.loaded = true; la.loading = false;
  _backupRender(modalId);
}

async function _backupImportDryRun() {
  const modalId = '__backup';
  const entry = openModals.get(modalId);
  if (!entry) return;
  const st = entry._backup.importState;
  st.busy = true; st.error = null;
  _backupRender(modalId);
  try {
    const res = await fetch(API_BASE + '/api/backup/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: st.path }),
    });
    const data = await res.json();
    if (!res.ok) {
      st.error = data.error || `Dry run failed (HTTP ${res.status})`;
    } else {
      st.dryRun = data;
      const pidCollision = (data.collisions.project_id || [])[0];
      st.projectResolution = pidCollision ? pidCollision.default : 'skip';
      const schedCollision = (data.collisions.schedule_id || [])[0];
      st.scheduleResolution = schedCollision ? schedCollision.default : 'skip';
      st.stage = 'report';
    }
  } catch (e) {
    st.error = 'Dry run failed: ' + e.message;
  } finally {
    st.busy = false;
    _backupRender(modalId);
  }
}

function _backupImportReportHTML(st) {
  const dr = st.dryRun;
  const pidC = (dr.collisions.project_id || [])[0];
  const remapNeeded = !!(pidC && dr.path_remap_required && dr.path_remap_required[pidC.id]);
  const needsNewPath = st.projectResolution === 'import-as-copy' || remapNeeded;
  const charC = dr.collisions.character_name || [];
  const schedC = dr.collisions.schedule_id || [];
  const canSubmit = !st.busy && !(needsNewPath && !st.newProjectPath.trim()) &&
    !(dr.contains_secrets && !st.vaultPassphrase.trim());

  return `
    <div style="padding:10px 12px;background:var(--surface2);border-radius:6px;font-size:12px;color:var(--text);margin-bottom:12px">
      ${esc(dr.announcement || '')}
    </div>
    ${pidC ? `
      <div style="margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:4px">Project <code>${esc(pidC.id)}</code>${pidC.exists_locally ? ' already exists on this machine' : ''}</div>
        ${pidC.exists_locally ? `
          <select onchange="_backupImportSetField('projectResolution',this.value)" style="padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
            ${pidC.options.map(o => `<option value="${esc(o)}" ${st.projectResolution === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}
          </select>
          ${st.projectResolution === 'replace' ? '<div style="font-size:10px;color:var(--text-faint);margin-top:4px">The existing project\'s record + sidecars are safety-copied first.</div>' : ''}
        ` : '<div style="font-size:11px;color:var(--text-faint)">No local project with this id — will import as-is.</div>'}
      </div>` : ''}
    ${needsNewPath ? `
      <div style="margin-bottom:12px">
        <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">
          ${remapNeeded ? 'Original project path is missing on this machine — where should it live?' : 'New project path (import-as-copy)'}
        </label>
        <input type="text" value="${esc(st.newProjectPath)}" oninput="_backupImportSetField('newProjectPath',this.value)"
          placeholder="C:\\path\\to\\project"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text);font-family:var(--mono)">
      </div>` : ''}
    ${charC.length ? `
      <div style="margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:4px">Characters (${charC.length})</div>
        <div style="font-size:11px;color:var(--text-faint)">${charC.map(c => `${esc(c.name)}${c.exists_locally ? ' (exists — byte-identical files skip automatically)' : ''}`).join(', ')}</div>
      </div>` : ''}
    ${schedC.length ? `
      <div style="margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:4px">Schedules (${schedC.length})</div>
        <select onchange="_backupImportSetField('scheduleResolution',this.value)" style="padding:6px 10px;font-size:12px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          ${schedC[0].options.map(o => `<option value="${esc(o)}" ${st.scheduleResolution === o ? 'selected' : ''}>${esc(o)}</option>`).join('')}
        </select>
        <div style="font-size:10px;color:var(--text-faint);margin-top:4px">Imported schedules always arrive disabled, whichever option is picked.</div>
      </div>` : ''}
    ${dr.contains_secrets ? `
      <div style="margin-bottom:12px">
        <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:4px">This archive contains the secrets vault</div>
        <input type="password" value="${esc(st.vaultPassphrase)}" oninput="_backupImportSetField('vaultPassphrase',this.value)"
          placeholder="Passphrase used at export time"
          style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
      </div>` : ''}
    ${dr.repo_checklist && dr.repo_checklist.length ? `
      <div style="margin-bottom:12px;padding:8px 12px;background:var(--surface2);border-radius:4px;font-size:11px;color:var(--text-faint)">
        <div style="font-weight:600;color:var(--text);margin-bottom:2px">Repo — not in this archive</div>
        ${dr.repo_checklist.map(l => `<div>&bull; ${esc(l)}</div>`).join('')}
      </div>` : ''}
    ${st.error ? `<div style="margin-bottom:10px;font-size:12px;color:var(--red-text)">${esc(st.error)}</div>` : ''}
    <div style="display:flex;justify-content:space-between;margin-top:14px">
      <button class="btn-secondary" onclick="_backupImportBack()">&larr; Back</button>
      <button class="btn-add" ${canSubmit ? '' : 'disabled'} onclick="_backupImportApply()">${st.busy ? 'Importing…' : 'Import'}</button>
    </div>`;
}

function _backupImportSetField(key, value) {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.importState[key] = value;
  _backupRender('__backup');
}

function _backupImportBack() {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.importState.stage = 'input';
  entry._backup.importState.dryRun = null;
  _backupRender('__backup');
}

async function _backupImportApply() {
  const modalId = '__backup';
  const entry = openModals.get(modalId);
  if (!entry) return;
  const st = entry._backup.importState;
  st.busy = true; st.error = null;
  _backupRender(modalId);
  try {
    const res = await fetch(API_BASE + '/api/backup/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: st.path, apply: true,
        project_resolution: st.projectResolution,
        schedule_resolution: st.scheduleResolution,
        new_project_path: st.newProjectPath || undefined,
        vault_passphrase: st.vaultPassphrase || undefined,
      }),
    });
    const data = await res.json();
    if (!res.ok) { st.error = data.error || `Import failed (HTTP ${res.status})`; }
    else { st.result = data; st.stage = 'done'; }
  } catch (e) {
    st.error = 'Import failed: ' + e.message;
  } finally {
    st.busy = false;
    _backupRender(modalId);
  }
}

function _backupImportDoneHTML(st) {
  const r = st.result;
  const skipped = r.status === 'skipped';
  return `
    <div style="padding:14px;background:${skipped ? 'var(--surface2)' : 'rgba(34,197,94,0.12)'};border:1px solid ${skipped ? 'var(--border)' : 'rgba(34,197,94,0.4)'};border-radius:6px;font-size:12px">
      <div style="font-weight:700;${skipped ? 'color:var(--text)' : 'color:#22c55e'};margin-bottom:6px">${skipped ? 'Skipped' : '\u2713 Imported'}</div>
      ${skipped ? `<div style="color:var(--text)">${esc(r.reason || '')}</div>` : `
        <div style="color:var(--text)">Project id: <code>${esc(r.final_project_id)}</code>${r.path_remapped ? ` &middot; path remapped to <code>${esc(r.new_project_path)}</code>` : ''}</div>
        <div style="color:var(--text-faint);margin-top:4px">Restored: ${esc(JSON.stringify(r.restored))}</div>
        ${(r.schedules_imported || r.schedules_skipped) ? `<div style="color:var(--text-faint)">Schedules: ${r.schedules_imported} imported, ${r.schedules_skipped} skipped</div>` : ''}
        ${(r.vault && r.vault.imported != null) ? `<div style="color:var(--text-faint)">Vault: ${r.vault.imported} imported, ${r.vault.skipped || 0} skipped</div>` : ''}
        ${r.pre_replace_copy ? `<div style="color:var(--text-faint);margin-top:4px">Previous project safety-copied to <code style="word-break:break-all">${esc(r.pre_replace_copy)}</code></div>` : ''}
        ${(r.warnings && r.warnings.length) ? `<details style="margin-top:6px"><summary style="cursor:pointer;color:var(--text-faint);font-size:11px">Show ${r.warnings.length} warning(s)</summary><pre style="margin:6px 0 0;font-size:10px;white-space:pre-wrap">${esc(JSON.stringify(r.warnings, null, 2))}</pre></details>` : ''}
      `}
    </div>
    <div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button class="btn-secondary" onclick="_backupImportReset()">Import another</button>
    </div>`;
}

function _backupImportReset() {
  const entry = openModals.get('__backup');
  if (!entry) return;
  entry._backup.importState = {
    stage: 'input', path: '', busy: false, error: null, dryRun: null,
    projectResolution: 'skip', scheduleResolution: 'skip', newProjectPath: '', vaultPassphrase: '',
    localArchives: { loaded: false, loading: false, items: [] }, result: null,
  };
  _backupRender('__backup');
}

// ── Per-project Export (project three-dot menu → Export) ────────────────────

function openProjectExport(projectId) {
  const modalId = `__export_${projectId}`;
  if (openModals.has(modalId)) { focusModal(modalId); return; }
  const project = (allProjects || []).find(p => p.id === projectId);
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 560);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">
        <svg class="menu-svg" style="display:inline-block;vertical-align:-3px"><use href="#ic-backup"/></svg>
        Export: ${esc(project ? (project.name || project.id) : projectId)}
      </span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div data-role="export-body" style="padding:4px 24px 20px 28px"></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, {
    projectId, element: win, minimized: false, zIndex: z,
    _export: {
      checklist: _newBackupChecklist(),
      vault: null, vaultPassphrase: '', label: '',
      exporting: false, error: null, result: null,
    },
  });
  centerModalElement(win);
  focusModal(modalId);
  _exportRender(modalId);
}

function _exportRender(modalId) {
  const entry = openModals.get(modalId);
  if (!entry || !entry.element) return;
  const body = entry.element.querySelector('[data-role="export-body"]');
  if (!body) return;
  body.innerHTML = _exportBodyHTML(entry._export, entry.projectId);
}

function _exportBodyHTML(state, projectId) {
  const rows = DEFAULT_BACKUP_CATEGORIES.map(cat => `
    <label style="display:flex;align-items:center;gap:8px;padding:6px 4px;cursor:pointer;border-bottom:1px solid var(--border)">
      <input type="checkbox" ${state.checklist[cat] ? 'checked' : ''} onchange="_exportToggleCategory('${esc(projectId)}','${cat}',this.checked)">
      <span style="font-size:13px;color:var(--text)">${BACKUP_CATEGORY_LABELS[cat]}</span>
    </label>`).join('');
  const noneChecked = DEFAULT_BACKUP_CATEGORIES.every(c => !state.checklist[c]);
  const canSubmit = !noneChecked && state.vault !== null &&
    !(state.vault && !state.vaultPassphrase.trim()) && !state.exporting;

  return `
    <div style="font-size:11px;color:var(--text-faint);margin-bottom:10px;line-height:1.5">
      Exports this project's own record, sidecars, memory vault, rules and schedules into one archive — not
      the repo, which travels via its own git remote. Everything is included by default; untick what you don't want.
    </div>
    <div>${rows}</div>
    <div style="margin-top:14px;padding:10px 12px;background:var(--surface2);border-radius:6px">
      <div style="font-size:12px;font-weight:600;color:var(--text);margin-bottom:6px">Include the secrets vault?</div>
      <div style="display:flex;gap:14px;font-size:12px">
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="radio" name="export-vault-${esc(projectId)}" ${state.vault === true ? 'checked' : ''} onchange="_exportSetVault('${esc(projectId)}',true)"> Include (needs a passphrase)
        </label>
        <label style="display:flex;align-items:center;gap:6px;cursor:pointer">
          <input type="radio" name="export-vault-${esc(projectId)}" ${state.vault === false ? 'checked' : ''} onchange="_exportSetVault('${esc(projectId)}',false)"> Omit
        </label>
      </div>
      ${state.vault === null ? '<div style="font-size:11px;color:var(--amber);margin-top:6px">Unanswered — export refuses to proceed until you pick one.</div>' : ''}
      ${state.vault === true ? `
        <input type="password" value="${esc(state.vaultPassphrase)}" oninput="_exportSetVaultPassphrase('${esc(projectId)}',this.value)"
          placeholder="Passphrase to re-encrypt the vault under"
          style="width:100%;margin-top:8px;padding:6px 10px;font-size:13px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
        <div style="font-size:10px;color:var(--text-faint);margin-top:4px">A leaked archive is a credential bundle — this passphrase is the only lock.</div>` : ''}
    </div>
    <div style="margin-top:12px">
      <label style="display:block;font-size:11px;color:var(--text-faint);margin-bottom:4px">Label (optional)</label>
      <input type="text" value="${esc(state.label)}" oninput="_exportSetLabel('${esc(projectId)}',this.value)"
        style="width:100%;padding:6px 10px;font-size:13px;background:var(--surface2);border:1px solid var(--border);border-radius:4px;color:var(--text)">
    </div>
    ${noneChecked ? '<div style="margin-top:8px;font-size:11px;color:var(--red-text)">Untick everything and there is nothing to export — pick at least one category.</div>' : ''}
    ${state.error ? `<div style="margin-top:10px;font-size:12px;color:var(--red-text)">${esc(state.error)}</div>` : ''}
    ${state.result ? _exportResultHTML(state.result) : ''}
    <div style="display:flex;justify-content:flex-end;margin-top:14px">
      <button class="btn-add" ${canSubmit ? '' : 'disabled'} onclick="_exportRun('${esc(projectId)}')">${state.exporting ? 'Exporting…' : 'Export'}</button>
    </div>`;
}

function _exportResultHTML(r) {
  return `<div style="margin-top:10px;padding:10px 12px;background:rgba(34,197,94,0.12);border:1px solid rgba(34,197,94,0.4);border-radius:6px;font-size:12px">
    <div style="font-weight:700;color:#22c55e;margin-bottom:4px">&#x2713; Exported</div>
    <div style="color:var(--text);font-family:var(--mono);word-break:break-all">${esc(r.path)}</div>
    <div style="color:var(--text-faint);margin-top:4px">${r.files_written} files${r.warnings && r.warnings.length ? `, ${r.warnings.length} warning${r.warnings.length === 1 ? '' : 's'}` : ''}</div>
    ${r.repo_checklist && r.repo_checklist.length ? `
      <div style="margin-top:8px;padding-top:8px;border-top:1px solid var(--border)">
        <div style="font-weight:600;color:var(--text);margin-bottom:2px">Repo — not in this archive</div>
        ${r.repo_checklist.map(l => `<div style="color:var(--text-faint)">&bull; ${esc(l)}</div>`).join('')}
      </div>` : ''}
  </div>`;
}

function _exportToggleCategory(projectId, cat, checked) {
  const entry = openModals.get(`__export_${projectId}`);
  if (!entry) return;
  entry._export.checklist[cat] = checked;
  _exportRender(`__export_${projectId}`);
}

function _exportSetVault(projectId, v) {
  const entry = openModals.get(`__export_${projectId}`);
  if (!entry) return;
  entry._export.vault = v;
  _exportRender(`__export_${projectId}`);
}

function _exportSetVaultPassphrase(projectId, v) {
  const entry = openModals.get(`__export_${projectId}`);
  if (!entry) return;
  entry._export.vaultPassphrase = v;
}

function _exportSetLabel(projectId, v) {
  const entry = openModals.get(`__export_${projectId}`);
  if (!entry) return;
  entry._export.label = v;
}

async function _exportRun(projectId) {
  const modalId = `__export_${projectId}`;
  const entry = openModals.get(modalId);
  if (!entry) return;
  const st = entry._export;
  st.exporting = true; st.error = null; st.result = null;
  _exportRender(modalId);
  try {
    const res = await fetch(API_BASE + `/api/backup/export-project/${encodeURIComponent(projectId)}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        categories: st.checklist, vault: st.vault,
        vault_passphrase: st.vault ? st.vaultPassphrase : undefined,
        label: st.label || undefined,
      }),
    });
    const data = await res.json();
    if (!res.ok) st.error = data.error || `Export failed (HTTP ${res.status})`;
    else st.result = data;
  } catch (e) {
    st.error = 'Export failed: ' + e.message;
  } finally {
    st.exporting = false;
    _exportRender(modalId);
  }
}

// ── Interop: sidebarNav('backup'), project three-dot menu, generated
//    on*= handlers all resolve these against window at call time. ──
window.openBackupSurface = openBackupSurface;             // sidebarNav('backup')
window.switchBackupTab = switchBackupTab;
window._backupToggleCategory = _backupToggleCategory;
window._backupSetLabel = _backupSetLabel;
window._backupSetDestDirOverride = _backupSetDestDirOverride;
window._backupRefreshList = _backupRefreshList;
window._backupCreate = _backupCreate;
window._backupStartCreate = _backupStartCreate;
window._backupCancelJob = _backupCancelJob;
window._backupToggleExpand = _backupToggleExpand;
window._backupBrowseDestDir = _backupBrowseDestDir;
window._backupRestoreConfirm = _backupRestoreConfirm;
window._backupImportSetPath = _backupImportSetPath;
window._backupImportDryRun = _backupImportDryRun;
window._backupImportSetField = _backupImportSetField;
window._backupImportBack = _backupImportBack;
window._backupImportApply = _backupImportApply;
window._backupImportReset = _backupImportReset;
window.openProjectExport = openProjectExport;              // project three-dot menu onclick
window._exportToggleCategory = _exportToggleCategory;
window._exportSetVault = _exportSetVault;
window._exportSetVaultPassphrase = _exportSetVaultPassphrase;
window._exportSetLabel = _exportSetLabel;
window._exportRun = _exportRun;
