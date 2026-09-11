// ── Workflow Builder (MC-871, Q7 — "a spine, not a graph") ──────────────────
//
// The authoring surface on top of the already-merged runner + CRUD
// (mc/workflows.py + mc/blueprints/workflow_routes.py, docs/WORKFLOW_BUILDER_SPEC.md).
// Opens as its own modal (`openWorkflowBuilder`), same shape as `openScheduler`
// — workflows are global cross-project objects, not owned by a project tab.
//
// SPINE, NOT GRAPH (spec Q7, verbatim): a vertical list of ordinary-DOM cards
// with CSS connectors, a Paths/Approval node splitting into side-by-side
// CSS-grid columns (a tree of lists — v1 has no rejoin, so no graph library
// is needed). No drag-to-connect edges, no free node positioning, no pan/zoom.
//
// REORDER SCOPE (Ron, 2026-09-11 — refines Q7, does not reverse it): cards
// drag to reorder WITHIN one list only. A drag's pointermove only ever
// compares against its OWN `.wfb-list` container's children (`_wfDragMove`),
// so a card structurally cannot cross into a different branch's list — moving
// a step to another branch means delete-and-re-add there, not a drag target.
// That is a deliberate v1 boundary, not a missing feature: v1 has no rejoin
// (spec "Scope"), so a step dragged across branches would need to invent
// what "next" means in a tree that was never designed to reconnect.
//
// THE SLOT-ORDER GUARD (Ron's explicit ask: "think about what reordering
// MEANS... do not silently produce a broken definition"): a step's prompt or
// action config can name an earlier step by `{{steps.<name>.output}}` (spec
// Q3). The backend does NOT validate this at authoring time — an unresolved
// slot only fails LOUDLY at RUN time (`mc.workflows.render_template`). Ron
// asked for a decision, made here: REFUSE the move/delete, don't warn-and-
// allow and don't silently fix it. `_wfFindBrokenSlotRefs` walks the tree in
// execution order threading forward the set of step names that have already
// run; `_wfGuardedListMutate`-style callers (`_wfDeleteStep`, `_wfDragCommit`)
// diff the violation set before/after a proposed structural change and revert
// on any NEW violation, with a toast naming exactly which step's slot broke.
// Chosen over "warn and let it through" because a silently-saved broken
// workflow only surfaces its break hours later, mid-run, as a failed run a
// human has to diagnose from a stack of JSON — refusing at the moment of the
// drag is the cheapest possible place to catch it. NOT guarded: `{{prev.output}}`
// — that shorthand is deliberately relative-to-whatever-ran-immediately-before
// (spec Q3), so by design its meaning changes on ANY reorder, not just a
// broken one; guarding it would mean refusing reorders that are perfectly
// valid. Left as a known, documented limitation.
//
// TOUCH: drag handles use Pointer Events with a long-press activation and
// `touch-action: pan-y` until a drag activates, mirroring floor.js's
// drag-to-hire gesture shape exactly (`_wfHandleDown`/`_wfDragMove`) per the
// brief's explicit precedent pointer. Unlike that gesture there is no ambient
// poll to survive mid-drag here — this modal has no background refresh while
// open, so the "poll rewrote the DOM mid-drag" trap floor.js hit does not
// apply, and is not reproduced by anything in this file.
//
// FIELD SYNC: card fields (name/prompt/project/persona/branch labels/action
// config) are NOT written into the in-memory `def` on every keystroke — like
// `scheduler.js`'s form, they live in the DOM and are read back only at the
// moment of a structural action (add/delete/reorder/save) via
// `_wfSyncDomToModel`, so typing in one card is never clobbered by adding a
// step in another. `.wfb-card-own` is the sync boundary: it wraps a card's
// OWN fields and never contains a nested `.wfb-card`, so
// `own.querySelector('.wfb-prompt')` can never reach into a Paths/Approval
// node's branch children by accident.

const WF_MODAL_ID = '__workflow_builder';
let _wfNameSeq = 0;
const _wfCharCache = new Map();
let _wfDrag = null; // one drag in flight at a time, same shape as floor.js's _hireDrag

function _wfNewName(prefix) {
  _wfNameSeq += 1;
  return `${prefix}-${_wfNameSeq}`;
}

function _wfPathAttr(path) {
  return esc(JSON.stringify(path));
}

function _wfBlankDef() {
  return { name: '', description: '', enabled: true, trigger: { type: 'manual' }, steps: [] };
}

// ── Modal open / load ────────────────────────────────────────────────────────

async function openWorkflowBuilder(workflowId) {
  const modalId = WF_MODAL_ID;
  if (openModals.has(modalId)) {
    const entry = openModals.get(modalId);
    if (entry.minimized) restoreModal(modalId);
    focusModal(modalId);
    await _wfLoadInto(entry, workflowId || null);
    _wfRender();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 900);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Workflow Builder</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${modalId}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeModalById('${modalId}')" title="Close">&#10005;</button>
      </div>
    </div>
    <div id="wfb-body" class="wfb-modal-body"></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z, _wf: null });
  centerModalElement(win);
  focusModal(modalId);

  const entry = openModals.get(modalId);
  await _wfLoadInto(entry, workflowId || null);
  _wfRender();
}

async function _wfLoadInto(entry, workflowId) {
  if (!workflowId) {
    entry._wf = { def: _wfBlankDef(), workflowId: null, saving: false, error: null, _cardSeq: 0, _charLoads: [] };
    return;
  }
  try {
    const res = await fetch(API_BASE + '/api/workflows');
    const list = await res.json();
    const found = (list || []).find(w => w.id === workflowId);
    entry._wf = {
      def: found ? JSON.parse(JSON.stringify(found)) : _wfBlankDef(),
      workflowId: found ? found.id : null,
      saving: false, error: found ? null : 'Workflow not found', _cardSeq: 0, _charLoads: [],
    };
  } catch (e) {
    entry._wf = { def: _wfBlankDef(), workflowId: null, saving: false, error: 'Failed to load workflow', _cardSeq: 0, _charLoads: [] };
  }
}

// ── Tree helpers ─────────────────────────────────────────────────────────────
// A `path` is an array of keys walking from `def.steps` down to either a LIST
// (e.g. `[]` for top-level, `[2,'branches','worth_drafting']`, `[2,'otherwise']`)
// or a NODE (a list-path with one more numeric index appended). Both array
// indices and object keys resolve through the same `cur[k]` bracket lookup, so
// one walker serves both.
function _wfResolve(def, path) {
  let cur = def.steps;
  for (const k of path) cur = cur[k];
  return cur;
}

function _wfBlankNode(type) {
  const name = _wfNewName(type);
  if (type === 'agent') return { type: 'agent', name, project_id: '', character: '', prompt: '' };
  if (type === 'approval') return { type: 'approval', name, options: ['approve', 'reject'], branches: { approve: [], reject: [] } };
  if (type === 'action') return { type: 'action', name, action: 'backlog_create', config: {} };
  return { type, name };
}

function _wfTypeLabel(t) {
  return { agent: 'AGENT STEP', paths: 'PATHS', approval: 'APPROVAL GATE', action: 'CLAYRUNE ACTION' }[t] || String(t || '').toUpperCase();
}

// ── The slot-order guard ──────────────────────────────────────────────────────

const _WF_SLOT_STEP_RE = /\{\{\s*steps\.([a-zA-Z0-9_]+)\.[a-zA-Z0-9_.]+\s*\}\}/g;

function _wfCollectStepText(node) {
  const parts = [];
  if (node.type === 'agent') parts.push(node.prompt || '');
  if (node.type === 'action') Object.values(node.config || {}).forEach(v => { if (typeof v === 'string') parts.push(v); });
  return parts;
}

// Walks in execution order, threading forward the set of step names that have
// already run by the time each node is reached. Paths node names are NOT
// addable to `available` — the backend never gives a Paths node its own
// nodes_by_name entry (mc/workflows.py `_compile_list`: a paths node is
// absorbed into the preceding agent's `_next`, never independently
// referenceable), so a `{{steps.<pathsName>...}}` reference would already be
// dead on arrival regardless of order.
function _wfFindBrokenSlotRefs(steps, availableIn) {
  const available = new Set(availableIn || []);
  const problems = [];
  for (const node of steps || []) {
    if (!node || !node.type) continue;
    for (const text of _wfCollectStepText(node)) {
      _WF_SLOT_STEP_RE.lastIndex = 0;
      let m;
      while ((m = _WF_SLOT_STEP_RE.exec(text))) {
        if (!available.has(m[1])) problems.push({ step: node.name || '(unnamed)', ref: m[1] });
      }
    }
    if (node.type !== 'paths' && node.name) available.add(node.name);
    if (node.type === 'paths') {
      const branches = node.branches || {};
      for (const label of Object.keys(branches)) problems.push(..._wfFindBrokenSlotRefs(branches[label], available));
      problems.push(..._wfFindBrokenSlotRefs(node.otherwise || [], available));
    } else if (node.type === 'approval') {
      const branches = node.branches || {};
      for (const label of Object.keys(branches)) problems.push(..._wfFindBrokenSlotRefs(branches[label], available));
    }
  }
  return problems;
}

function _wfNewViolations(before, after) {
  return after.filter(a => !before.some(b => b.step === a.step && b.ref === a.ref));
}

// ── Field sync: DOM → in-memory def, at the moment of a structural action ────

function _wfSyncDomToModel(entry) {
  const def = entry._wf.def;
  const nameEl = document.getElementById('wfb-name');
  if (nameEl) def.name = nameEl.value;
  const descEl = document.getElementById('wfb-desc');
  if (descEl) def.description = descEl.value;
  const enabledEl = document.getElementById('wfb-enabled');
  if (enabledEl) def.enabled = !!enabledEl.checked;
  document.querySelectorAll('.wfb-card').forEach((cardEl) => {
    let nodePath;
    try { nodePath = JSON.parse(cardEl.dataset.nodepath); } catch (e) { return; }
    const node = _wfResolve(def, nodePath);
    const own = cardEl.querySelector(':scope > .wfb-card-own');
    if (!node || !own) return;
    _wfSyncOwn(node, own);
  });
}

function _wfSyncOwn(node, own) {
  const nameEl = own.querySelector('.wfb-name');
  if (nameEl) node.name = nameEl.value.trim();
  if (node.type === 'agent') {
    const projEl = own.querySelector('.wfb-project');
    const persEl = own.querySelector('.wfb-persona');
    const promptEl = own.querySelector('.wfb-prompt');
    if (projEl) node.project_id = projEl.value;
    if (persEl) node.character = persEl.value;
    if (promptEl) node.prompt = promptEl.value;
  } else if (node.type === 'paths') {
    const newBranches = {};
    own.querySelectorAll('.wfb-branch-label-input').forEach((inp) => {
      const oldKey = inp.dataset.branchKey;
      const newKey = inp.value.trim() || oldKey;
      newBranches[newKey] = (node.branches && node.branches[oldKey]) || [];
    });
    node.branches = newBranches;
  } else if (node.type === 'approval') {
    const newOptions = [];
    const newBranches = {};
    own.querySelectorAll('.wfb-approval-option-input').forEach((inp) => {
      const oldKey = inp.dataset.optionKey;
      const newKey = inp.value.trim() || oldKey;
      newOptions.push(newKey);
      newBranches[newKey] = (node.branches && node.branches[oldKey]) || [];
    });
    node.options = newOptions;
    node.branches = newBranches;
  } else if (node.type === 'action') {
    const selEl = own.querySelector('.wfb-action-select');
    if (selEl) node.action = selEl.value;
    const config = {};
    own.querySelectorAll('[data-cfg-key]').forEach((el) => {
      const key = el.dataset.cfgKey;
      const required = el.dataset.cfgRequired === '1';
      if (el.value.trim() !== '' || required) config[key] = el.value;
    });
    node.config = config;
  }
}

// ── Rendering ─────────────────────────────────────────────────────────────────

function _wfRender() {
  const entry = openModals.get(WF_MODAL_ID);
  if (!entry || !entry._wf) return;
  const body = document.getElementById('wfb-body');
  if (!body) return;
  const st = entry._wf;
  st._cardSeq = 0;
  st._charLoads = [];
  body.innerHTML = _wfRenderBody(st);
  st._charLoads.forEach(({ seq, want }) => _wfReloadCharacters(seq, want));
}

function _wfRenderBody(st) {
  const def = st.def;
  const triggerType = (def.trigger && def.trigger.type) || 'manual';
  return `
    <div class="wfb-meta">
      <label>Name</label>
      <input id="wfb-name" value="${esc(def.name || '')}" placeholder="Untitled workflow">
      <label>Description</label>
      <textarea id="wfb-desc" rows="2" placeholder="What this pipeline is for">${esc(def.description || '')}</textarea>
      <label style="display:flex;align-items:center;gap:8px;text-transform:none;font-size:12px;color:var(--text)">
        <input type="checkbox" id="wfb-enabled" style="margin:0;width:auto" ${def.enabled !== false ? 'checked' : ''}>
        <span>Enabled</span>
      </label>
    </div>
    <div class="wfb-trigger-card">
      <div class="wfb-trigger-title">TRIGGER</div>
      <div class="wfb-trigger-row">
        <label class="wfb-trigger-opt">
          <input type="radio" name="wfb-trigger" value="manual" ${triggerType !== 'schedule' ? 'checked' : ''} onchange="_wfSetTriggerType('manual')">
          Manual &mdash; Run Now or the API
        </label>
        <label class="wfb-trigger-opt wfb-trigger-disabled" title="Scheduler linkage (a schedule record's workflow_id) is a later build-order pass, not wired yet">
          <input type="radio" name="wfb-trigger" value="schedule" disabled>
          On a schedule <span class="wfb-soon">(coming soon)</span>
        </label>
      </div>
    </div>
    <div class="wfb-spine">
      ${_wfRenderList(st, def.steps || [], [])}
    </div>
    ${st.error ? `<div class="wfb-error">${esc(st.error)}</div>` : ''}
    <div class="wfb-actions">
      <button class="btn-sched-save" onclick="_wfSave()" ${st.saving ? 'disabled' : ''}>${st.saving ? 'Saving…' : (st.workflowId ? 'Update' : 'Create')}</button>
      <button class="btn-sched-cancel" onclick="closeModalById('${WF_MODAL_ID}')">Close</button>
    </div>`;
}

function _wfRenderList(st, list, listPath) {
  const cards = (list || []).map((node, i) => _wfRenderCard(st, node, listPath, i)).join('');
  const lastIsAgent = list && list.length && list[list.length - 1].type === 'agent';
  const pathAttr = _wfPathAttr(listPath);
  return `<div class="wfb-list" data-listpath="${pathAttr}">
    ${cards || '<div class="wfb-list-empty">No steps yet.</div>'}
    <div class="wfb-list-footer">
      <button class="wfb-add-btn" onclick="_wfAddStep('${pathAttr}','agent')">+ Agent step</button>
      <button class="wfb-add-btn" onclick="_wfAddStep('${pathAttr}','approval')">+ Approval gate</button>
      <button class="wfb-add-btn" onclick="_wfAddStep('${pathAttr}','action')">+ Clayrune action</button>
      ${lastIsAgent ? `<button class="wfb-add-btn wfb-add-branch" onclick="_wfAddBranching('${pathAttr}')">+ Split into paths</button>` : ''}
    </div>
  </div>`;
}

function _wfRenderCard(st, node, listPath, i) {
  const nodePath = listPath.concat([i]);
  const nodePathAttr = _wfPathAttr(nodePath);
  let own = '';
  let children = '';
  if (node.type === 'agent') own = _wfRenderAgentOwn(st, node);
  else if (node.type === 'paths') { own = _wfRenderPathsOwn(node, nodePath); children = _wfRenderPathsChildren(st, node, nodePath); }
  else if (node.type === 'approval') { own = _wfRenderApprovalOwn(node, nodePath); children = _wfRenderApprovalChildren(st, node, nodePath); }
  else if (node.type === 'action') own = _wfRenderActionOwn(node);
  else own = '<div class="wfb-card-own">Unknown node type.</div>';

  return `<div class="wfb-card-wrap">
    <div class="wfb-card" data-nodepath="${nodePathAttr}" data-type="${esc(node.type)}">
      <div class="wfb-card-head">
        <span class="wfb-card-num">${i + 1}</span>
        <span class="wfb-drag-handle" title="Drag to reorder within this list"
              onpointerdown="_wfHandleDown(event,'${nodePathAttr}')">&#9776;</span>
        <span class="wfb-card-type">${_wfTypeLabel(node.type)}</span>
        <button class="wfb-card-del" title="Delete step" onclick="_wfDeleteStep('${nodePathAttr}')">&#10005;</button>
      </div>
      ${own}
      ${children}
    </div>
  </div>`;
}

function _wfRenderAgentOwn(st, node) {
  const seq = ++st._cardSeq;
  const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter(p => p.project_path);
  const pid = node.project_id || (projects[0] && projects[0].id) || '';
  st._charLoads.push({ seq, want: node.character || '' });
  return `<div class="wfb-card-own">
    <label>Name <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(referenced as <code>{{steps.NAME.output}}</code>)</span></label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="step-name">
    <label>Project</label>
    <select id="wfb-proj-${seq}" class="wfb-project" onchange="_wfReloadCharacters(${seq})">
      ${projects.map(p => `<option value="${esc(p.id)}"${p.id === pid ? ' selected' : ''}>${esc(p.name)}</option>`).join('')}
    </select>
    <label>Agent</label>
    <div class="sched-agent-row">
      <span id="wfb-face-${seq}" class="sched-agent-face"></span>
      <select id="wfb-persona-${seq}" class="wfb-persona"><option value="">Loading…</option></select>
    </div>
    <label>Prompt <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(<code>{{steps.NAME.output}}</code> / <code>{{prev.output}}</code> pull an earlier step's result forward)</span></label>
    <textarea class="wfb-prompt" rows="3" placeholder="What should this step do?">${esc(node.prompt || '')}</textarea>
  </div>`;
}

function _wfRenderPathsOwn(node, nodePath) {
  const nodePathAttr = _wfPathAttr(nodePath);
  const branches = node.branches || {};
  const labels = Object.keys(branches);
  return `<div class="wfb-card-own">
    <label>Name <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(label only &mdash; not slot-referenceable)</span></label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="branch point">
    <div class="wfb-branch-labels">
      <label>Branches <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(the agent step above must end its reply naming one of these as its outcome)</span></label>
      ${labels.map((label, bi) => `<div class="wfb-branch-label-row">
        <input class="wfb-branch-label-input" value="${esc(label)}" data-branch-key="${esc(label)}">
        <button class="wfb-branch-del" title="Remove branch" onclick="_wfRemoveBranch('${nodePathAttr}',${bi})">&#10005;</button>
      </div>`).join('')}
      <button class="wfb-add-btn" onclick="_wfAddBranch('${nodePathAttr}')">+ Add branch</button>
    </div>
  </div>`;
}

function _wfRenderPathsChildren(st, node, nodePath) {
  const branches = node.branches || {};
  const labels = Object.keys(branches);
  return `<div class="wfb-branch-grid">
    ${labels.map(label => `<div class="wfb-branch-col">
      <div class="wfb-branch-col-title">${esc(label)}</div>
      ${_wfRenderList(st, branches[label] || [], nodePath.concat(['branches', label]))}
    </div>`).join('')}
    <div class="wfb-branch-col wfb-otherwise-col">
      <div class="wfb-branch-col-title">otherwise <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(no / unparseable outcome &mdash; mandatory, fail-closed)</span></div>
      ${_wfRenderList(st, node.otherwise || [], nodePath.concat(['otherwise']))}
    </div>
  </div>`;
}

function _wfRenderApprovalOwn(node, nodePath) {
  const nodePathAttr = _wfPathAttr(nodePath);
  const options = node.options || [];
  return `<div class="wfb-card-own">
    <label>Name</label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="approval-name">
    <div class="wfb-branch-labels">
      <label>Options <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(what a human can choose &mdash; delivered over the question channel)</span></label>
      ${options.map((label, oi) => `<div class="wfb-branch-label-row">
        <input class="wfb-approval-option-input" value="${esc(label)}" data-option-key="${esc(label)}">
        <button class="wfb-branch-del" title="Remove option" onclick="_wfRemoveOption('${nodePathAttr}',${oi})">&#10005;</button>
      </div>`).join('')}
      <button class="wfb-add-btn" onclick="_wfAddOption('${nodePathAttr}')">+ Add option</button>
    </div>
  </div>`;
}

function _wfRenderApprovalChildren(st, node, nodePath) {
  const options = node.options || [];
  return `<div class="wfb-branch-grid">
    ${options.map(label => `<div class="wfb-branch-col">
      <div class="wfb-branch-col-title">${esc(label)}</div>
      ${_wfRenderList(st, (node.branches || {})[label] || [], nodePath.concat(['branches', label]))}
    </div>`).join('')}
  </div>`;
}

const _WF_ACTION_LABELS = { backlog_create: 'Create a backlog item', backlog_patch: 'Patch a backlog item', desk_harvest: 'Run a Desk harvest' };

function _wfRenderActionOwn(node) {
  const action = node.action || 'backlog_create';
  return `<div class="wfb-card-own">
    <label>Name</label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="action-name">
    <label>Action</label>
    <select class="wfb-action-select" onchange="_wfRerenderActionFields(this)">
      ${Object.keys(_WF_ACTION_LABELS).map(a => `<option value="${a}"${a === action ? ' selected' : ''}>${esc(_WF_ACTION_LABELS[a])}</option>`).join('')}
    </select>
    <div class="wfb-action-fields">${_wfActionFieldsHTML(action, node.config || {})}</div>
  </div>`;
}

function _wfActionFieldsHTML(action, cfg) {
  const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter(p => p.project_path);
  const projOpts = (includeAny) => (includeAny ? '<option value="">(any project)</option>' : '')
    + projects.map(p => `<option value="${esc(p.id)}"${p.id === cfg.project_id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
  if (action === 'backlog_patch') {
    return `
      <label>Project</label>
      <select data-cfg-key="project_id" data-cfg-required="1">${projOpts(false)}</select>
      <label>Backlog item ID <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(can be a slot, e.g. <code>{{steps.triage.result.item_id}}</code>)</span></label>
      <input data-cfg-key="item_id" data-cfg-required="1" value="${esc(cfg.item_id || '')}">
      <label>New status <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = leave unchanged)</span></label>
      <select data-cfg-key="status">
        <option value=""${!cfg.status ? ' selected' : ''}>(unchanged)</option>
        ${['open', 'in_progress', 'blocked', 'done'].map(s => `<option value="${s}"${cfg.status === s ? ' selected' : ''}>${s}</option>`).join('')}
      </select>
      <label>New text <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = leave unchanged)</span></label>
      <textarea data-cfg-key="text" rows="2">${esc(cfg.text || '')}</textarea>`;
  }
  if (action === 'desk_harvest') {
    return `
      <label>Project <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = every project)</span></label>
      <select data-cfg-key="project_id">${projOpts(true)}</select>`;
  }
  // backlog_create (default)
  return `
    <label>Project</label>
    <select data-cfg-key="project_id" data-cfg-required="1">${projOpts(false)}</select>
    <label>Text</label>
    <textarea data-cfg-key="text" data-cfg-required="1" rows="2" placeholder="What the item says">${esc(cfg.text || '')}</textarea>
    <label>Priority</label>
    <select data-cfg-key="priority">
      ${['low', 'normal', 'high'].map(p => `<option value="${p}"${(cfg.priority || 'normal') === p ? ' selected' : ''}>${p}</option>`).join('')}
    </select>`;
}

function _wfRerenderActionFields(selectEl) {
  const own = selectEl.closest('.wfb-card-own');
  const box = own && own.querySelector('.wfb-action-fields');
  if (!box) return;
  box.innerHTML = _wfActionFieldsHTML(selectEl.value, {});
}

// ── Persona picker (mirrors scheduler.js's reloadSchedCharacters, per-card) ──

async function _wfCharactersFor(pid) {
  if (_wfCharCache.has(pid)) return _wfCharCache.get(pid);
  const res = await fetch(API_BASE + '/api/characters?project_id=' + encodeURIComponent(pid));
  if (!res.ok) throw new Error('characters ' + res.status);
  const list = await res.json();
  _wfCharCache.set(pid, list);
  return list;
}

function _wfProjectDefault(pid, list) {
  const p = (typeof allProjects !== 'undefined' ? allProjects : []).find(x => x.id === pid);
  const d = p && p.default_character;
  if (!d) return null;
  const [scope, name] = String(d).split(':');
  return list.find(c => c.scope === scope && c.name === name) || null;
}

function _wfPaintFace(seq, list) {
  const box = document.getElementById('wfb-face-' + seq);
  const sel = document.getElementById('wfb-persona-' + seq);
  if (!box || !sel) return;
  const pid = document.getElementById('wfb-proj-' + seq)?.value || '';
  let rec = null;
  if (list) {
    const v = sel.value;
    rec = v ? list.find(c => (c.scope + ':' + c.name) === v) : _wfProjectDefault(pid, list);
  }
  box.innerHTML = window.avatarHTML(rec && rec.avatar, 26);
}

async function _wfReloadCharacters(seq, want) {
  const sel = document.getElementById('wfb-persona-' + seq);
  const pid = document.getElementById('wfb-proj-' + seq)?.value || '';
  if (!sel || !pid) return;
  const target = (want === undefined) ? sel.value : (want || '');
  sel.innerHTML = '<option value="">Loading…</option>';
  let list;
  try {
    list = await _wfCharactersFor(pid);
  } catch (e) {
    sel.innerHTML = '<option value="">Project default (persona list unavailable)</option>';
    _wfPaintFace(seq, null);
    return;
  }
  const proj = list.filter(c => c.scope === 'project');
  const glob = list.filter(c => c.scope !== 'project' && !c.shadowed_by_project);
  const label = (c) => (c.agent_name ? `${c.agent_name} — ${c.display_name || c.name}` : (c.display_name || c.name));
  const opt = (c) => { const v = c.scope + ':' + c.name; return `<option value="${esc(v)}"${v === target ? ' selected' : ''}>${esc(label(c))}</option>`; };
  const dflt = _wfProjectDefault(pid, list);
  const group = (name, rows) => rows.length ? `<optgroup label="${esc(name)}">${rows.map(opt).join('')}</optgroup>` : '';
  sel.innerHTML =
    `<option value=""${target ? '' : ' selected'}>${esc(dflt ? `Project default — ${label(dflt)}` : 'Project default (no persona)')}</option>`
    + group('This project', proj) + group('Global', glob);
  if (target && sel.value !== target) sel.value = '';
  sel.onchange = () => _wfPaintFace(seq, _wfCharCache.get(pid));
  _wfPaintFace(seq, list);
}

// ── Structural mutations ──────────────────────────────────────────────────────

function _wfSetTriggerType(t) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  entry._wf.def.trigger = { type: t };
  _wfRender();
}

function _wfAddStep(listPathAttr, type) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const listPath = JSON.parse(listPathAttr);
  const list = _wfResolve(entry._wf.def, listPath);
  list.push(_wfBlankNode(type));
  _wfRender();
}

function _wfAddBranching(listPathAttr) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const listPath = JSON.parse(listPathAttr);
  const list = _wfResolve(entry._wf.def, listPath);
  if (!list.length || list[list.length - 1].type !== 'agent') {
    showToast('A Paths node must directly follow an agent step.', 4000);
    return;
  }
  list.push({ type: 'paths', name: '', branches: { 'branch-1': [] }, otherwise: [] });
  _wfRender();
}

function _wfAddBranch(nodePathAttr) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const node = _wfResolve(entry._wf.def, JSON.parse(nodePathAttr));
  node.branches = node.branches || {};
  const n = Object.keys(node.branches).length + 1;
  node.branches['branch-' + n] = [];
  _wfRender();
}

function _wfRemoveBranch(nodePathAttr, branchIdx) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const node = _wfResolve(entry._wf.def, JSON.parse(nodePathAttr));
  const labels = Object.keys(node.branches || {});
  const label = labels[branchIdx];
  if (label === undefined) return;
  if (labels.length <= 1) { showToast('A Paths node needs at least one branch besides otherwise.', 4000); return; }
  delete node.branches[label];
  _wfRender();
}

function _wfAddOption(nodePathAttr) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const node = _wfResolve(entry._wf.def, JSON.parse(nodePathAttr));
  const n = (node.options || []).length + 1;
  const label = 'option-' + n;
  node.options = (node.options || []).concat([label]);
  node.branches = node.branches || {};
  node.branches[label] = [];
  _wfRender();
}

function _wfRemoveOption(nodePathAttr, idx) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const node = _wfResolve(entry._wf.def, JSON.parse(nodePathAttr));
  const label = (node.options || [])[idx];
  if (label === undefined) return;
  if ((node.options || []).length <= 1) { showToast('An approval gate needs at least one option.', 4000); return; }
  node.options = node.options.filter((_, i) => i !== idx);
  if (node.branches) delete node.branches[label];
  _wfRender();
}

function _wfDeleteStep(nodePathAttr) {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const def = entry._wf.def;
  const nodePath = JSON.parse(nodePathAttr);
  const listPath = nodePath.slice(0, -1);
  const idx = nodePath[nodePath.length - 1];
  const list = _wfResolve(def, listPath);
  const before = _wfFindBrokenSlotRefs(def.steps, []);
  const removed = list.splice(idx, 1);
  const after = _wfFindBrokenSlotRefs(def.steps, []);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    list.splice(idx, 0, removed[0]);
    showToast(`Can't delete "${removed[0].name || 'this step'}" — step "${newOnes[0].step}" still reads {{steps.${newOnes[0].ref}.…}}.`, 6000);
    return;
  }
  _wfRender();
}

// ── Drag-to-reorder (pointer events, floor.js drag-to-hire's gesture shape,
//    scoped to reordering within ONE `.wfb-list` container — see file header) ─

const WFB_LONG_PRESS_MS = 400;
const WFB_DRAG_SLOP_PX = 8;

function _wfHandleDown(e, nodePathAttr) {
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfDrag) return;
  const wrap = e.currentTarget.closest('.wfb-card-wrap');
  const list = wrap && wrap.parentElement;
  if (!wrap || !list || !list.classList.contains('wfb-list')) return;
  const st = {
    pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
    startX: e.clientX, startY: e.clientY,
    active: false, handle: e.currentTarget, wrap, list, nodePathAttr, longPressTimer: null,
  };
  _wfDrag = st;
  if (st.pointerType === 'touch') {
    st.longPressTimer = setTimeout(() => { if (_wfDrag === st && !st.active) _wfDragActivate(st); }, WFB_LONG_PRESS_MS);
  }
  try { st.handle.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfDragMove);
  window.addEventListener('pointerup', _wfDragUp);
  window.addEventListener('pointercancel', _wfDragCancel);
  window.addEventListener('blur', _wfDragCancel);
}

function _wfDragActivate(st) {
  st.active = true;
  clearTimeout(st.longPressTimer);
  st.wrap.classList.add('wfb-dragging');
}

function _wfDragMove(e) {
  const st = _wfDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const dx = e.clientX - st.startX, dy = e.clientY - st.startY;
  if (!st.active) {
    if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX) _wfDragActivate(st);
    else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX * 1.5) {
      clearTimeout(st.longPressTimer);
      _wfDragTeardown(st);
    }
    return;
  }
  e.preventDefault();
  // Compare only against THIS list's own siblings — a card cannot cross into
  // another branch's `.wfb-list`, by construction (see file header).
  const siblings = [...st.list.children].filter(c => c.classList.contains('wfb-card-wrap') && c !== st.wrap);
  let target = null, before = true;
  for (const sib of siblings) {
    const r = sib.getBoundingClientRect();
    if (e.clientY < r.top + r.height / 2) { target = sib; before = true; break; }
    target = sib; before = false;
  }
  if (target) {
    if (before) st.list.insertBefore(st.wrap, target);
    else st.list.insertBefore(st.wrap, target.nextSibling);
  }
}

function _wfDragUp(e) {
  const st = _wfDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (!st.active) { _wfDragTeardown(st); return; }
  _wfDragCommit(st);
  _wfDragTeardown(st);
}

function _wfDragCommit(st) {
  const entry = openModals.get(WF_MODAL_ID);
  if (!entry) return;
  _wfSyncDomToModel(entry); // capture any typed edits before the structural change
  const def = entry._wf.def;
  const listPath = JSON.parse(st.list.dataset.listpath);
  const list = _wfResolve(def, listPath);
  const newOrderPaths = [...st.list.children]
    .filter(c => c.classList.contains('wfb-card-wrap'))
    .map(c => JSON.parse(c.querySelector(':scope > .wfb-card').dataset.nodepath));
  const orig = list.slice();
  const before = _wfFindBrokenSlotRefs(def.steps, []);
  const reordered = newOrderPaths.map(p => list[p[p.length - 1]]);
  list.length = 0; list.push(...reordered);
  const after = _wfFindBrokenSlotRefs(def.steps, []);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    list.length = 0; list.push(...orig);
    showToast(`Can't move that — step "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs to run first.`, 6000);
  }
  _wfRender(); // one render either way: commits the new order, or restores the old one
}

function _wfDragCancel(e) {
  const st = _wfDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) _wfRender(); // the model was never touched mid-drag — a fresh render restores DOM order
  _wfDragTeardown(st);
}

function _wfDragTeardown(st) {
  if (st.wrap) st.wrap.classList.remove('wfb-dragging');
  window.removeEventListener('pointermove', _wfDragMove);
  window.removeEventListener('pointerup', _wfDragUp);
  window.removeEventListener('pointercancel', _wfDragCancel);
  window.removeEventListener('blur', _wfDragCancel);
  try { st.handle.releasePointerCapture(st.pointerId); } catch (e) { /* already released, or gone */ }
  _wfDrag = null;
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _wfDrag) _wfDragCancel(e);
});

// ── Save ──────────────────────────────────────────────────────────────────────

async function _wfSave() {
  const entry = openModals.get(WF_MODAL_ID); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  const def = st.def;
  if (!(def.name || '').trim()) { showToast('Name the workflow before saving.', 4000); return; }
  if (!def.steps || !def.steps.length) { showToast('Add at least one step before saving.', 4000); return; }

  st.saving = true; st.error = null;
  _wfRender();
  const body = { name: def.name, description: def.description, enabled: def.enabled !== false, trigger: def.trigger, steps: def.steps };
  try {
    const url = st.workflowId ? `${API_BASE}/api/workflows/${st.workflowId}` : `${API_BASE}/api/workflows`;
    const method = st.workflowId ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
    st.workflowId = data.workflow.id;
    st.def = JSON.parse(JSON.stringify(data.workflow));
    st.saving = false;
    showToast('Workflow saved', 3000);
    _wfRender();
    if (window.refreshWorkflowsList) window.refreshWorkflowsList();
  } catch (e) {
    st.saving = false;
    st.error = 'Save failed: ' + e.message;
    _wfRender();
  }
}

// ── Interop: window accessors for onclick/onchange targets + cross-module
//    entry points (static/js/scheduler.js:626-650 is the pattern this follows).
window.openWorkflowBuilder = openWorkflowBuilder;
window._wfSetTriggerType = _wfSetTriggerType;
window._wfAddStep = _wfAddStep;
window._wfAddBranching = _wfAddBranching;
window._wfAddBranch = _wfAddBranch;
window._wfRemoveBranch = _wfRemoveBranch;
window._wfAddOption = _wfAddOption;
window._wfRemoveOption = _wfRemoveOption;
window._wfDeleteStep = _wfDeleteStep;
window._wfHandleDown = _wfHandleDown;
window._wfReloadCharacters = _wfReloadCharacters;
window._wfRerenderActionFields = _wfRerenderActionFields;
window._wfSave = _wfSave;
