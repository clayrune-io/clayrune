// ── Workflow Builder (MC-871, R2-D7 — "the canvas") ──────────────────────────
//
// The authoring surface on top of the already-merged runner + CRUD + DAG store
// (mc/workflows.py + mc/blueprints/workflow_routes.py, docs/WORKFLOW_BUILDER_SPEC.md,
// Revision 2).
//
// REHOSTED INLINE (Q7 re-reversed, Ron on seeing the shipped tab: "instead of
// opening yet another window, can we open the canvas on the same page we use
// for the overall workflow menu? If there's more than one, present the
// selection tabs at the top"). There is no floating modal any more — the ONE
// canvas implementation below mounts into whichever project's Workflows tab
// last claimed it (`_wfState`, a singleton exactly like the old modal was:
// only one workflow can be under edit at a time). agent-console.js's
// `loadWorkflows`/`_wfRenderClayruneSection`-successor builds a tabs row (one
// per workflow touching that project, plus "+ New Workflow") into
// `#wfb-clayrune-section-<projectId>` and an empty `#wfb-inline-host-<projectId>`
// div; `openWorkflowBuilder(workflowId, projectId)` is the one function that
// still loads a workflow and mounts the canvas into that host — called by a
// tab click, by "+ New Workflow", and (still) directly, e.g. from a test.
// `window._wfSyncTabsForProject(projectId, list)` is the entry point that
// decides whether the tabs row (and, on a real change, the mounted canvas)
// need rebuilding at all — see its own header for why that has to be
// carefully idempotent (a 3s CC-fan-out poll calls into this same path).
//
// A second load-bearing reason the modal had to go: `refreshModalById`
// (static/index.html) rebuilds the ENTIRE project modal's innerHTML on every
// SSE turn event for that project. A canvas embedded in that DOM would be
// wiped mid-edit exactly like agent-output streaming text would be, so
// `refreshModalById` now detaches and reattaches the whole
// `#workflows-body-<pid>` subtree around that rebuild — see its own comment
// there. That preservation, not anything in this file, is what makes "live"
// hosting safe.
//
// The definition is now `nodes` + `edges` (R2-D1),
// not a nested tree, and this file is the free-canvas editor for that shape:
// a palette docked left (a bottom sheet under the mobile breakpoint), drag a
// block onto the canvas to place a node, drag from an output port to an
// input port to connect two nodes. Node `x`/`y` persist per node; pan/zoom
// (`_wfCanvasWheel`, `_wfCanvasTouchStart/Move/End`) is a CSS transform on
// `#wfb-world` and is NOT persisted to the record (open item, R2-D7 — the
// store has no viewport field; adding one is a backend change out of this
// pass's scope, so a reopen starts centred rather than where you left it).
//
// NODE TYPES (R2-D6 — Paths is retired): `agent`, `approval`, `action`.
// Branching is a property of the EDGE (`when`), not a node — an agent step's
// `outcomes` array and an approval gate's `options` array are the declared
// vocabulary; each label gets its own output port, plus a mandatory
// `otherwise` port whenever that vocabulary is non-empty (R2-D6: "otherwise
// survives as a PORT"). An agent with no declared outcomes, and every action
// node, gets exactly one plain (unconditional, `when: null`) output port —
// matching what `mc/workflows.py::validate_workflow` actually accepts.
//
// PHASE 2'S CARD EDITORS ARE REUSED VERBATIM (brief's explicit instruction):
// `_wfRenderAgentOwn`'s project-select/persona-picker/prompt-textarea and
// `_wfRenderActionOwn`/`_wfActionFieldsHTML`/`_wfRerenderActionFields` are
// copied over unchanged from the spine build — only the surrounding layout
// (free canvas position instead of a list item) and the branching editor
// (outcomes/options as a flat array instead of nested branch lists) changed.
//
// THE SLOT-BREAK GUARD (R2-D5, carried forward from the spine's slot-ORDER
// guard, now graph-shaped): `{{steps.X.output}}` is valid in node N only if X
// DOMINATES N — X is on every path from the trigger to N, so X can never be
// skipped while N runs. `_wfFindBrokenSlotRefs` computes this with a ported
// version of the backend's toposort + dominator fixpoint
// (`mc/workflows.py::_toposort`/`_compute_dominators`) and every mutation
// that can change dominance — add/remove an edge, delete a node, remove a
// declared outcome/option (which implicitly drops the edge wired to it) —
// diffs the violation set before/after and refuses on any NEW violation,
// exactly the spine's "refuse, don't warn-and-allow, don't silently fix"
// pattern, with the reference named in the toast. NOT guarded: a rename
// (spine behaviour carried forward unchanged — this file only widens the
// guard to cover edges, not new mutation classes) and `{{prev.output}}`
// (valid only with exactly one parent — the backend's own rule, checked at
// save; by design its meaning changes on any parent change, so guarding it
// would refuse valid edits).
//
// CYCLE REFUSAL AT CONNECT (R2-D3 layer 1 — the layer that lives in the
// canvas): before an edge is added, `_wfHasPath(edges, to, from)` checks
// whether the target can already reach the source; if so the new edge would
// close a loop and the drop is refused with a toast naming both nodes. Save
// and run-start re-check server-side (`validate_workflow`/`compile_workflow`)
// — this is defence-in-depth, not the only guard.
//
// TOUCH IS FIRST-CLASS (brief, "Ron uses this from his phone"): every drag —
// place-from-palette, move-a-node, connect-a-port, pan-the-canvas — is one
// pointer-event gesture family, mirroring `floor.js`'s drag-to-hire shape
// (long-press activation on touch, 8px slop on mouse/pen). THE TRAP THAT BIT
// drag-to-hire TWICE (`floor.js` comments, `.fl-hire-dragging`): a draggable
// element's `touch-action` must default to something scrollable
// (`pan-y`/`manipulation`) and switch to `none` ONLY via a class added at
// drag ACTIVATION, never permanently — see `.wfb-node-head`/
// `.wfb-palette-block` in app.css. Ports are small dedicated controls, not
// scrollable list rows, so they carry `touch-action: none` unconditionally
// (same posture as an ordinary button) and get a real 40x40px hit box
// (`.wfb-port`) around a smaller visible dot, per the brief's "ports need
// >= 40px hit targets". Pinch-zoom is native touchstart/touchmove tracking
// (mirrors `mermaid.js`'s viewer-gesture pattern) — Pointer Events don't
// aggregate multi-touch, so a 2-finger pinch cancels any single-pointer pan
// in flight and takes over.
//
// NO BACKGROUND REFRESH while this modal is open (same as the spine build) —
// the trap of a poll rewriting the DOM mid-drag (floor.js's Floor re-render)
// does not apply here; every re-render (`_wfRender`) is the direct result of
// a user action on this same modal, never an ambient timer.
//
// FIELD SYNC: node fields (name/prompt/project/persona/outcomes/options/
// action config) are read from the DOM into the in-memory `def` only at the
// moment of a structural action (`_wfSyncDomToModel`), not on every
// keystroke — typing in one node's prompt is never clobbered by placing a
// new block or dragging an edge elsewhere on the canvas.

// The one mounted canvas -- singleton, same discipline the old modal had
// (only one workflow can be under edit at a time). `{ projectId, _wf }` where
// `_wf` is exactly the state shape `_wfFreshState` always returned; kept
// nested under `_wf` (rather than flattened) so every existing call site
// below that read `entry._wf.*` needed no further change once `_wfEntry()`
// stood in for `openModals.get(WF_MODAL_ID)`.
let _wfState = null;
function _wfEntry() { return _wfState; }
let _wfNameSeq = 0;
const _wfCharCache = new Map();

function _wfNewName(prefix) {
  _wfNameSeq += 1;
  return `${prefix}-${_wfNameSeq}`;
}

// HTML-safe (`esc`) is for literal markup. These two are for the OTHER two
// contexts a node name flows through: a dynamically-built CSS attribute
// selector (`_wfAttrEsc`, used when matching `[data-node="…"]` from JS) and a
// single-quoted inline-JS string literal inside an onclick/onpointerdown
// attribute (`_wfJsStrEsc`). Conflating either with `esc()` would leave an
// attacker- or just apostrophe-carrying node name able to break out of the
// selector or the attribute.
function _wfAttrEsc(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}
function _wfJsStrEsc(s) {
  return String(s == null ? '' : s).replace(/\\/g, '\\\\').replace(/'/g, "\\'");
}

function _wfBlankDef() {
  return { name: '', description: '', enabled: true, trigger: { type: 'manual' }, nodes: [], edges: [] };
}

function _wfTypeLabel(t) {
  return { agent: 'AGENT STEP', approval: 'APPROVAL GATE', action: 'CLAYRUNE ACTION' }[t] || String(t || '').toUpperCase();
}

function _wfBlankNode(type, x, y) {
  const name = _wfNewName(type);
  const base = { type, name, x: Math.round(x), y: Math.round(y) };
  if (type === 'agent') return { ...base, project_id: '', character: '', prompt: '', outcomes: [] };
  if (type === 'approval') return { ...base, options: ['approve', 'reject'] };
  if (type === 'action') return { ...base, action: 'backlog_create', config: {} };
  return base;
}

// The declared branching vocabulary a node's outgoing edges may name in
// `when` (mc/workflows.py `_declared_vocab`, mirrored here).
function _wfVocab(node) {
  if (node.type === 'agent') return node.outcomes || [];
  if (node.type === 'approval') return node.options || [];
  return [];
}

// Ports actually drawn on the node. R2-D6: a non-empty vocabulary always
// gets a trailing `otherwise` port; an empty vocabulary (a plain agent step,
// or any action node — action steps cannot have conditional edges at all,
// `validate_workflow`) gets exactly one plain unconditional port.
function _wfOutPorts(node) {
  const vocab = _wfVocab(node);
  if (node.type === 'action' || !vocab.length) return [{ when: null, label: '' }];
  return vocab.map(w => ({ when: w, label: w })).concat([{ when: 'otherwise', label: 'otherwise' }]);
}

// ── The Bench, as the palette (UI brief §2) ─────────────────────────────────
//
// "Drag people, not primitives." A palette row IS a hired agent; dropping one
// creates an agent step with that persona already chosen, so there is no
// "add an Agent step, then pick a persona" detour. Rows come from the SAME
// endpoint the Floor's bench reads (`/api/floor` → `bench`), so a type hired
// on the Floor is draggable here without a second roster concept.

// FACES: one resolution path, the Floor's. `avatarHTML` (render-core.js) is
// the only implementation — `fig:<name>` becomes the real figure image, a
// renderable emoji becomes a glyph. This is the guarded call shape
// `_floorAvatarHTML` uses: window.avatarHTML is a cross-module global and a
// module-load-order race would otherwise throw mid-render (floor.js, ws001
// Finding 1).
//
// TEST THE CLEANED VALUE, NOT THE RAW ONE. `avatarIsFigure`/`avatarIsRenderable`
// normalise (trim, then `fig:` prefix or "at least one codepoint above U+007F")
// exactly as `characters.clean_avatar` does server-side, and they are the same
// predicates `avatarHTML` itself branches on — so the usability verdict and the
// render can never disagree. That disagreement is the bug `_figure_avatar`
// documents: a `??` (an emoji flattened by a Windows console codepage) tested
// as present, rendered as nothing, and outranked the real face underneath it.
// An avatar that is neither a known figure nor a real emoji therefore falls
// THROUGH to the initial mark below — it is never echoed into the DOM as text.
function _wfAvatarHTML(person, size) {
  const v = (person && person.avatar) || '';
  const usable = (typeof window.avatarIsFigure === 'function' && window.avatarIsFigure(v))
    || (typeof window.avatarIsRenderable === 'function' && window.avatarIsRenderable(v));
  if (usable && typeof window.avatarHTML === 'function') return window.avatarHTML(v, size);
  // Genuinely faceless: the name's own initial, never a random face and never
  // the raw unusable string (Ron, 2026-09-11 — the mock's letter bubbles are
  // the FALLBACK here, not the default).
  return `<span class="wfb-face-initial" style="width:${size}px;height:${size}px;font-size:${Math.round(size * 0.44)}px"
    >${esc(_wfInitial(person))}</span>`;
}

function _wfInitial(person) {
  const s = String((person && (person.display || person.name)) || '').trim();
  return s ? s[0].toUpperCase() : '·';
}

function _wfBenchLookup(st, scope, name) {
  return (st.bench || []).find(b => (b.scope || 'global') === (scope || 'global') && b.name === name) || null;
}

// A node stores its persona as the persona picker's own value shape,
// `<scope>:<name>` (see `_wfReloadCharacters`), so the card header can find
// the bench row that owns the face.
function _wfPersonFromCharacter(st, character) {
  const s = String(character || '');
  const i = s.indexOf(':');
  if (i < 0) return null;
  return _wfBenchLookup(st, s.slice(0, i), s.slice(i + 1));
}

function _wfBenchFiltered(st, search) {
  const q = String(search || '').trim().toLowerCase();
  const list = st.bench || [];
  if (!q) return list;
  return list.filter(b => String(b.display || '').toLowerCase().includes(q)
    || String(b.name || '').toLowerCase().includes(q));
}

function _wfSlug(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '') || 'step';
}

function _wfUniqueNodeName(st, base) {
  const taken = new Set(((st.def && st.def.nodes) || []).map(n => n.name));
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}-${i}`)) i += 1;
  return `${base}-${i}`;
}

// The palette's two tool tiles make a bare node; a PERSON makes an agent step
// with its persona and project already resolved. Brief §8c rule 1: a dropped
// person defaults its project to that persona's home room (`project_id` on the
// bench card — the "only in <project>" pin), else the project the builder was
// opened from, so nothing else needs picking.
function _wfMakeNode(st, type, scope, name, x, y) {
  if (type !== 'person') return _wfBlankNode(type, x, y);
  const person = _wfBenchLookup(st, scope, name)
    || { scope: scope || 'global', name, display: name, avatar: '', project_id: '' };
  return {
    type: 'agent',
    name: _wfUniqueNodeName(st, _wfSlug(person.name || person.display)),
    x: Math.round(x), y: Math.round(y),
    project_id: person.project_id || st.hintProjectId || '',
    character: (person.scope || 'global') + ':' + person.name,
    prompt: '', outcomes: [],
  };
}

// ── Inline host: mount / load ────────────────────────────────────────────────
//
// `#wfb-inline-host-<projectId>` is built by agent-console.js's `loadWorkflows`
// (via `_wfSyncTabsForProject` below) the moment a project's Workflows tab is
// opened. This function loads a workflow (or starts a blank one) and mounts
// the SAME canvas markup `_wfRender` has always produced into that host --
// there is no other render path.

// True if it's safe to discard whatever is currently mounted -- not dirty, or
// the user confirmed discarding it. The inline equivalent of the old modal's
// close confirm (`_wfRequestClose`), now also the gate every tab switch and
// re-mount goes through (UI brief: "switching tabs with unsaved changes must
// hit the same dirty-state warning that closing did").
function _wfConfirmDiscardIfDirty() {
  if (!_wfState || !_wfState._wf || !_wfState._wf.dirty) return true;
  return confirm('Discard unsaved changes to this workflow?');
}

// Another project's Workflows tab just stole the singleton canvas. Its host
// div is still live DOM in a still-open modal -- leaving it wired to
// event handlers that now resolve against a DIFFERENT project's `_wfState`
// would let a stray click there corrupt that other workflow. Reset it to an
// inert placeholder instead of leaving stale interactive markup behind.
function _wfRenderIdleHost(projectId) {
  const host = document.getElementById('wfb-inline-host-' + projectId);
  if (host) host.innerHTML = '<div class="wfb-canvas-idle">Editing moved to another project — pick a tab to resume here.</div>';
}

async function openWorkflowBuilder(workflowId, hintProjectId) {
  const projectId = hintProjectId || (_wfState && _wfState.projectId) || '';
  const host = document.getElementById('wfb-inline-host-' + projectId);
  if (!host) { console.warn('[workflow-builder] no inline host mounted for project', projectId); return; }
  if (!_wfConfirmDiscardIfDirty()) return;
  if (_wfState && _wfState.projectId && _wfState.projectId !== projectId) _wfRenderIdleHost(_wfState.projectId);

  host.innerHTML = '<div id="wfb-body" class="wfb-modal-body"></div>';
  // Dirty-state watcher (UI brief §6/build-order step 6): delegated so it
  // survives every _wfRender() rebuild of #wfb-body's children -- attached
  // once, to the body element itself, not to anything _wfRender() replaces.
  const wfBody = host.querySelector('#wfb-body');
  wfBody.addEventListener('input', _wfMarkDirty);
  wfBody.addEventListener('change', _wfMarkDirty);

  _wfState = { projectId, _wf: null };
  await _wfLoadInto(_wfState, workflowId || null, projectId);
  _wfRender();
}

// Re-mount the ALREADY-LOADED state into a freshly (re)built host div -- no
// network refetch. Used when `_wfSyncTabsForProject` has to rebuild the tabs
// row (the workflow set changed) but the workflow already mounted for this
// project is still in that set: the in-memory edit survives, only the DOM
// wrapper around it is rebuilt.
function _wfRemountDom(projectId) {
  const host = document.getElementById('wfb-inline-host-' + projectId);
  if (!host || !_wfState || _wfState.projectId !== projectId) return;
  host.innerHTML = '<div id="wfb-body" class="wfb-modal-body"></div>';
  const wfBody = host.querySelector('#wfb-body');
  wfBody.addEventListener('input', _wfMarkDirty);
  wfBody.addEventListener('change', _wfMarkDirty);
  _wfRender();
}

function _wfFreshState(def, workflowId, error, hintProjectId) {
  // linkedSchedule: the schedule record whose workflow_id points at this
  // workflow (MC-871 Q4 -- "one store, two views"). null until loaded/created;
  // the CADENCE lives there, never duplicated onto def.trigger, which stays
  // a bare `{type: 'schedule'}` marker.
  // hintProjectId: the project the builder was opened FROM (the tab's own
  // project, when known). Fallback target for a dropped person with no home
  // room of their own (UI brief §2: "if none, the current project").
  // bench/benchLoaded/paletteSearch: the Bench roster backing the palette
  // (UI brief §2 — "the palette is the Bench, drag people not primitives"),
  // loaded once per modal open from the SAME endpoint the Floor/Bench reads.
  // dirty/savedAt (build step 6): tracked session-side only -- the store has
  // no updated_at/version field (confirmed against mc/workflows.py), so the
  // stamp is "since this modal opened", not a durable last-modified. runErrors/
  // scrollToNode carry a failed Save/Run-now's per-card messages (step 6,
  // "inline on the offending card, not only a toast") to the next render.
  return { def, workflowId, saving: false, error: error || null, _cardSeq: 0,
           _charLoads: [], viewport: { x: 60, y: 40, scale: 1 }, linkedSchedule: null,
           hintProjectId: hintProjectId || '', bench: [], benchLoaded: false, paletteSearch: '',
           dirty: false, savedAt: null, runErrors: null, scrollToNode: null };
}

async function _wfLoadInto(entry, workflowId, hintProjectId) {
  if (!workflowId) {
    entry._wf = _wfFreshState(_wfBlankDef(), null, null, hintProjectId);
    await _wfLoadBench(entry._wf);
    return;
  }
  try {
    const res = await fetch(API_BASE + '/api/workflows');
    const list = await res.json();
    const found = (list || []).find(w => w.id === workflowId);
    entry._wf = _wfFreshState(
      found ? JSON.parse(JSON.stringify(found)) : _wfBlankDef(),
      found ? found.id : null,
      found ? null : 'Workflow not found',
      hintProjectId);
    if (found) await _wfLoadLinkedSchedule(entry._wf);
  } catch (e) {
    entry._wf = _wfFreshState(_wfBlankDef(), null, 'Failed to load workflow', hintProjectId);
  }
  await _wfLoadBench(entry._wf);
}

// Same data source as the Floor/Bench (UI brief §2, "Palette data source: the
// same roster the Floor/Bench reads"). Best-effort: a failed fetch leaves the
// palette showing an empty-bench message rather than blocking the modal —
// the builder is still usable with the Approval gate / Action tools.
async function _wfLoadBench(st) {
  try {
    const res = await fetch(API_BASE + '/api/floor');
    const data = await res.json();
    st.bench = data.bench || [];
  } catch (e) {
    console.warn('[workflow-builder] bench unavailable:', e);
    st.bench = [];
  }
  st.benchLoaded = true;
}

// ── Tabs row: "one per workflow that involves this project" ─────────────────
//
// Called by agent-console.js's `loadWorkflows` every time it re-fetches
// `/api/workflows` -- including the 3s poll that exists only to track an
// unrelated Claude Code fan-out. `_wfTabsSig` makes that safe: the tabs row
// (and, more importantly, the mounted canvas) are only touched when the SET
// of workflow ids touching this project actually changed, never on a poll
// that reconfirms the same set.
const _wfTabsSig = {}; // projectId -> last-seen "id,id,id" signature

function _wfTabsRowHTML(projectId, list, selectedId) {
  const tabs = list.map(w => `<button type="button" class="wfb-tab${w.id === selectedId ? ' active' : ''}"
      onclick="_wfTabClick('${_wfJsStrEsc(projectId)}','${_wfJsStrEsc(w.id)}')">${esc(w.name || 'Untitled workflow')}</button>`).join('');
  return `<div class="wfb-tabs-row">${tabs}
    <button type="button" class="wfb-tab wfb-tab-new" onclick="_wfNewWorkflowClick('${_wfJsStrEsc(projectId)}')">+ New Workflow</button>
  </div>`;
}

function _wfEmptyStateHTML() {
  return '<div style="color:var(--text-faint);font-style:italic;font-size:12px;padding:4px 0 2px">No workflows involve this project yet.</div>';
}

function _wfTabClick(projectId, workflowId) {
  if (_wfState && _wfState.projectId === projectId && _wfState._wf && _wfState._wf.workflowId === workflowId) return;
  if (!_wfConfirmDiscardIfDirty()) return;
  openWorkflowBuilder(workflowId, projectId);
}
window._wfTabClick = _wfTabClick;

function _wfNewWorkflowClick(projectId) {
  if (!_wfConfirmDiscardIfDirty()) return;
  openWorkflowBuilder(null, projectId);
}
window._wfNewWorkflowClick = _wfNewWorkflowClick;

function _wfSyncTabsForProject(projectId, list) {
  const section = document.getElementById('wfb-clayrune-section-' + projectId);
  if (!section) return;
  const sig = list.map(w => w.id).sort().join(',');
  if (section.dataset.wfBuilt === '1' && _wfTabsSig[projectId] === sig) return; // nothing changed -- leave the canvas alone
  _wfTabsSig[projectId] = sig;
  section.dataset.wfBuilt = '1';

  const mountedId = (_wfState && _wfState.projectId === projectId && _wfState._wf) ? _wfState._wf.workflowId : undefined;
  const stillMounted = list.some(w => w.id === mountedId);
  const selected = stillMounted ? mountedId : (list[0] ? list[0].id : null);

  // "+ New Workflow" is always present, even with zero workflows (UI brief:
  // "zero = the canvas area shows the existing empty-state line plus the
  // new-workflow action") -- `_wfTabsRowHTML` renders it unconditionally and
  // simply has no per-workflow tabs alongside it when `list` is empty.
  section.innerHTML = _wfTabsRowHTML(projectId, list, selected)
    + (list.length ? '' : _wfEmptyStateHTML())
    + `<div id="wfb-inline-host-${esc(projectId)}"></div>`;

  if (selected == null) return;
  if (stillMounted) _wfRemountDom(projectId);      // already loaded in memory -- just rebuild the DOM around it
  else openWorkflowBuilder(selected, projectId);   // a different/new workflow -- load it for real
}
window._wfSyncTabsForProject = _wfSyncTabsForProject;
window._wfConfirmDiscardIfDirty = _wfConfirmDiscardIfDirty;

// The schedule store is the single source of truth for cadence (spec Q4);
// this just finds the one row (if any) pointing back at this workflow.
async function _wfLoadLinkedSchedule(st) {
  if (!st.workflowId) return;
  try {
    const res = await fetch(API_BASE + '/api/schedules');
    const list = await res.json();
    st.linkedSchedule = (list || []).find(s => s.workflow_id === st.workflowId) || null;
  } catch (e) {
    st.linkedSchedule = null;
  }
}

function _wfDraftSchedule() {
  return { schedule_type: 'daily', time: '09:00', days: [1, 2, 3, 4, 5],
           interval_minutes: 60, run_at: '', cron_expr: '' };
}

// ── Graph helpers, ported from mc/workflows.py so the builder can enforce
//    the same rules the store re-validates on save ───────────────────────────

// Kahn's algorithm. Ties broken by `names`' own order (mirrors the backend).
// Returns a partial order if the graph is cyclic (shorter than `names`) —
// callers that care about cycles use `_wfHasPath` instead, which answers the
// question directly rather than inferring it from a short toposort.
function _wfToposort(names, edges) {
  const indeg = {}; const children = {};
  names.forEach(n => { indeg[n] = 0; children[n] = []; });
  edges.forEach(e => { if (children[e.from] && indeg[e.to] !== undefined) { children[e.from].push(e.to); indeg[e.to] += 1; } });
  const queue = names.filter(n => indeg[n] === 0);
  const order = [];
  for (let i = 0; i < queue.length; i++) {
    const n = queue[i];
    order.push(n);
    for (const c of children[n]) { indeg[c] -= 1; if (indeg[c] === 0) queue.push(c); }
  }
  return order;
}

function _wfParentsMap(names, edges) {
  const m = {};
  names.forEach(n => { m[n] = []; });
  edges.forEach(e => { if (m[e.to]) m[e.to].push(e.from); });
  return m;
}

function _wfSetEq(a, b) {
  if (a.size !== b.size) return false;
  for (const x of a) if (!b.has(x)) return false;
  return true;
}

// Standard iterative dominator fixpoint (mc/workflows.py `_compute_dominators`).
function _wfDominators(order, parentsMap) {
  const allNames = new Set(order);
  const roots = new Set(order.filter(n => !(parentsMap[n] || []).length));
  const dom = {};
  order.forEach(n => { dom[n] = roots.has(n) ? new Set([n]) : new Set(allNames); });
  let changed = true;
  while (changed) {
    changed = false;
    for (const n of order) {
      if (roots.has(n)) continue;
      const preds = parentsMap[n] || [];
      if (!preds.length) continue;
      let inter = null;
      for (const p of preds) {
        const dp = dom[p] || new Set();
        inter = inter === null ? new Set(dp) : new Set([...inter].filter(x => dp.has(x)));
      }
      inter = inter || new Set();
      inter.add(n);
      if (!_wfSetEq(inter, dom[n])) { dom[n] = inter; changed = true; }
    }
  }
  return dom;
}

// Does a path already exist from `from` to `to` over ALL edges, regardless of
// `when`? (Cycle detection ignores branch labels — same as the backend's
// toposort, which builds adjacency from every edge unconditionally.)
function _wfHasPath(edges, from, to) {
  if (from === to) return true;
  const children = {};
  edges.forEach(e => { (children[e.from] = children[e.from] || []).push(e.to); });
  const seen = new Set([from]);
  const stack = [from];
  while (stack.length) {
    const n = stack.pop();
    for (const c of (children[n] || [])) {
      if (c === to) return true;
      if (!seen.has(c)) { seen.add(c); stack.push(c); }
    }
  }
  return false;
}

const _WF_SLOT_STEP_RE = /\{\{\s*steps\.([a-zA-Z0-9_]+)\.[a-zA-Z0-9_.]+\s*\}\}/g;

function _wfCollectStepText(node) {
  const parts = [];
  if (node.type === 'agent') parts.push(node.prompt || '');
  if (node.type === 'action') Object.values(node.config || {}).forEach(v => { if (typeof v === 'string') parts.push(v); });
  return parts;
}

// R2-D5: a `{{steps.X.*}}` reference in node N is broken unless X exists AND
// X dominates N (X is on every trigger→N path, so X can never be skipped
// while N runs). Cyclic input (should not reach here — connect-time refuses
// cycles outright) falls back to treating every node as a root so this never
// throws; it is defence-in-depth, not the primary cycle guard.
function _wfFindBrokenSlotRefs(nodes, edges) {
  const names = nodes.map(n => n.name);
  const order = _wfToposort(names, edges);
  const parentsMap = _wfParentsMap(names, edges);
  const dom = _wfDominators(order.length === names.length ? order : names, parentsMap);
  const problems = [];
  for (const node of nodes) {
    for (const text of _wfCollectStepText(node)) {
      _WF_SLOT_STEP_RE.lastIndex = 0;
      let m;
      while ((m = _WF_SLOT_STEP_RE.exec(text))) {
        const ref = m[1];
        if (ref === node.name) continue; // backend flags self-reference separately at save
        if (!names.includes(ref)) { problems.push({ step: node.name, ref }); continue; }
        const domSet = dom[node.name];
        if (!domSet || !domSet.has(ref)) problems.push({ step: node.name, ref });
      }
    }
  }
  return problems;
}

function _wfNewViolations(before, after) {
  return after.filter(a => !before.some(b => b.step === a.step && b.ref === a.ref));
}

// ── Step 5b: legal-slot Insert control + a read-only "chip" legend over the
//    reused plain <textarea>/<input> fields. Ground rule 3 (reuse the Phase-2
//    editors verbatim) rules out swapping them for a contenteditable so a
//    real in-text chip could render; this is the "at minimum visually
//    distinguishable" fallback the brief allows instead. Both the Insert
//    dropdown and the chip legend's broken/valid read reuse the EXACT
//    dominator fixpoint _wfFindBrokenSlotRefs already computes for the save
//    guard (R2-D5), so what the dropdown offers and what the legend flags as
//    broken can never disagree with each other or with the validator. ───────

function _wfLegalInsertSlots(def, nodeName) {
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const names = nodes.map(n => n.name);
  const order = _wfToposort(names, edges);
  const parentsMap = _wfParentsMap(names, edges);
  const dom = _wfDominators(order.length === names.length ? order : names, parentsMap);
  const domSet = dom[nodeName] || new Set([nodeName]);
  // Definition order, not dominator-fixpoint order -- a stable, scannable
  // dropdown that doesn't reshuffle as the author wires more of the graph.
  const ancestors = names.filter(n => n !== nodeName && domSet.has(n));
  const parents = parentsMap[nodeName] || [];
  return { ancestors, prevLegal: parents.length === 1 ? parents[0] : null };
}

function _wfInsertControlHTML(def, nodeName, fieldSelector) {
  const { ancestors, prevLegal } = _wfLegalInsertSlots(def, nodeName);
  const opts = [];
  if (prevLegal) opts.push('{{prev.output}}');
  ancestors.forEach(a => opts.push(`{{steps.${a}.output}}`));
  opts.push('{{trigger.fired_at}}', '{{run.id}}');
  return `<select class="wfb-insert-select" title="Insert a slot at the cursor"
      onchange="_wfInsertSlot(event,'${_wfJsStrEsc(nodeName)}','${_wfJsStrEsc(fieldSelector)}')">
    <option value="">Insert &#9662;</option>
    ${opts.map(v => `<option value="${esc(v)}">${esc(v)}</option>`).join('')}
  </select>`;
}

const _WF_SLOT_ANY_RE = /\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}/g;

function _wfSlotChipsHTML(def, nodeName, text) {
  const refs = [];
  _WF_SLOT_ANY_RE.lastIndex = 0;
  let m;
  while ((m = _WF_SLOT_ANY_RE.exec(text || ''))) refs.push(m[1]);
  if (!refs.length) return '';
  const { ancestors, prevLegal } = _wfLegalInsertSlots(def, nodeName);
  const legalSteps = new Set(ancestors);
  const chip = (ref) => {
    let broken;
    if (ref === 'prev.output') broken = !prevLegal;
    else if (ref === 'trigger.fired_at' || ref === 'run.id') broken = false;
    else {
      const stepM = /^steps\.([a-zA-Z0-9_]+)\./.exec(ref);
      broken = !stepM || !legalSteps.has(stepM[1]);
    }
    return `<span class="wfb-slot-chip${broken ? ' wfb-slot-chip-broken' : ''}"
      title="${broken ? 'Not reachable on every path into this step' : 'Valid here'}">{{${esc(ref)}}}</span>`;
  };
  return `<div class="wfb-slot-chips">${refs.map(chip).join('')}</div>`;
}

// Writes at the field's caret, same "sync first" discipline every other
// structural mutation follows (file header, FIELD SYNC): other cards' unsynced
// edits are captured into the model before this one mutation is applied, so a
// re-render never clobbers a prompt someone else is mid-typing.
function _wfInsertSlot(e, nodeName, fieldSelector) {
  const value = e.target.value;
  e.target.value = '';
  if (!value) return;
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  nodeName = renameMap[nodeName] || nodeName;
  const node = (entry._wf.def.nodes || []).find(n => n.name === nodeName);
  if (!node) return;
  const cardEl = document.querySelector(`.wfb-node[data-name="${_wfAttrEsc(nodeName)}"]`);
  const field = cardEl && cardEl.querySelector(fieldSelector);
  if (!field) return;
  const start = field.selectionStart != null ? field.selectionStart : field.value.length;
  const end = field.selectionEnd != null ? field.selectionEnd : field.value.length;
  const newText = field.value.slice(0, start) + value + field.value.slice(end);
  const caret = start + value.length;
  if (node.type === 'agent' && fieldSelector === '.wfb-prompt') {
    node.prompt = newText;
  } else {
    const cfgMatch = /\[data-cfg-key="([^"]+)"\]/.exec(fieldSelector);
    if (!cfgMatch) return;
    node.config = node.config || {};
    node.config[cfgMatch[1]] = newText;
  }
  _wfMarkDirty();
  _wfRender();
  const freshField = document.querySelector(`.wfb-node[data-name="${_wfAttrEsc(nodeName)}"] ${fieldSelector}`);
  if (freshField) {
    freshField.focus({ preventScroll: true });
    try { freshField.setSelectionRange(caret, caret); } catch (err) { /* not all input types support it */ }
  }
}

// ── Field sync: DOM → in-memory def, at the moment of a structural action ────

// Returns a `{oldName: newName}` map for every node renamed by this sync.
// EVERY caller that identifies a node/edge by a name baked into an onclick/
// onpointerdown attribute at the LAST render must remap through this before
// using that name — sync can rename the very node the caller is about to
// act on (the user typed a new name, then clicked "+ Add outcome" or a
// port on that same still-unrendered card), and a name baked into markup
// two renders ago is not the same string as the model's name right now.
function _wfSyncDomToModel(entry) {
  const def = entry._wf.def;
  const nameEl = document.getElementById('wfb-name');
  if (nameEl) def.name = nameEl.value;
  const descEl = document.getElementById('wfb-desc');
  if (descEl) def.description = descEl.value;
  const enabledEl = document.getElementById('wfb-enabled');
  if (enabledEl) def.enabled = !!enabledEl.checked;
  if (def.trigger && def.trigger.type === 'schedule') _wfSyncScheduleFormToState(entry._wf);
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const renameMap = {};
  document.querySelectorAll('.wfb-node').forEach((nodeEl) => {
    const oldName = nodeEl.dataset.name;
    const node = nodes.find(n => n.name === oldName);
    const own = nodeEl.querySelector('.wfb-node-own');
    if (!node || !own) return;
    _wfSyncNodeOwn(node, own);
    if (node.name && node.name !== oldName) {
      // Renames aren't guarded against breaking a TEXT slot reference
      // elsewhere (spine behaviour, carried forward — see file header), but
      // a rename WOULD silently orphan this node's own edges if they
      // weren't repointed, which is new breakage this file would be
      // introducing, not inheriting. Repoint them.
      edges.forEach(e => { if (e.from === oldName) e.from = node.name; if (e.to === oldName) e.to = node.name; });
      renameMap[oldName] = node.name;
    }
  });
  return renameMap;
}

function _wfSyncNodeOwn(node, own) {
  const nameEl = own.querySelector('.wfb-name');
  if (nameEl) node.name = nameEl.value.trim() || node.name;
  if (node.type === 'agent') {
    const projEl = own.querySelector('.wfb-project');
    const persEl = own.querySelector('.wfb-persona');
    const promptEl = own.querySelector('.wfb-prompt');
    if (projEl) node.project_id = projEl.value;
    if (persEl) node.character = persEl.value;
    if (promptEl) node.prompt = promptEl.value;
    node.outcomes = [...own.querySelectorAll('.wfb-outcome-input')].map(i => i.value.trim()).filter(Boolean);
  } else if (node.type === 'approval') {
    node.options = [...own.querySelectorAll('.wfb-option-input')].map(i => i.value.trim()).filter(Boolean);
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
  const entry = _wfEntry();
  if (!entry || !entry._wf) return;
  _wfClosePortPopover(); // its anchor port is about to be replaced
  const body = document.getElementById('wfb-body');
  if (!body) return;
  const st = entry._wf;
  body.innerHTML = _wfRenderBody(st);
  st._charLoads.forEach(({ seq, want }) => _wfReloadCharacters(seq, want));
  // Run-now/Save set scrollToNode to the first offending card (step 6); pan
  // it into view here, once, then clear the flag so it doesn't re-pan on
  // every unrelated re-render that follows.
  if (st.scrollToNode) {
    const target = st.scrollToNode;
    st.scrollToNode = null;
    _wfPanToNode(st, target);
  }
  _wfApplyViewport(st);
  _wfAttachCanvasGestures();
}

// Cards live on a pan/zoom CSS-transformed canvas, not inside a scrolling
// container, so scrollIntoView() does nothing here -- centre the viewport on
// the node instead. 260/... below are the node width/an estimated header+
// first-field height (`.wfb-node` in app.css); exact centring isn't the
// point, getting the flagged card on-screen is.
function _wfPanToNode(st, nodeName) {
  const node = (st.def.nodes || []).find(n => n.name === nodeName);
  const vp = document.getElementById('wfb-canvas-viewport');
  if (!node || !vp) return;
  const rect = vp.getBoundingClientRect();
  const cx = (node.x || 0) + 130;
  const cy = (node.y || 0) + 80;
  st.viewport.x = rect.width / 2 - cx * st.viewport.scale;
  st.viewport.y = rect.height / 2 - cy * st.viewport.scale;
}

// ── The Trigger box (MC-871 Change 2, canvas) ────────────────────────────────
//
// Ron: "Needs the Trigger box to exist on the canvas as first point" -- chosen
// deliberately as its OWN shape, not a real node: it is never a member of
// `def.nodes`, never in NODE_TYPES, has no card editor, and the actual
// manual/schedule form above (`.wfb-trigger-card`) is unchanged and still owns
// `def.trigger.type`. This box is a positioned, draggable marker for it on
// the free canvas -- `trigger.x`/`trigger.y` are additive keys on the same
// dict (verified round-tripping through mc/workflows.py: `doc.get('trigger')`
// is stored and returned verbatim, no key allowlist).
//
// NO input port (nothing can feed a trigger) and NO output port either: every
// output port on a real node is wired through `_wfTryAddEdge`, which the
// dominator/broken-slot fixpoint (`_wfToposort`/`_wfDominators`) walks by
// iterating `def.nodes` -- the trigger isn't in that array, so an edge
// touching it would need those functions (and the stored edge shape) to learn
// a node that isn't a node. Out of scope per the brief ("if wiring would mean
// touching the edge model or stored format, DON'T"), so it stays visually
// unconnected.
// Above the root node, not to its left: a root's `x` is wherever the user
// actually dropped it (usually near the canvas's default viewport origin),
// so subtracting a further fixed offset from that can walk the trigger off
// the LEFT edge of the default {x:60,y:40,scale:1} viewport on the very
// first node placed -- exactly the case a brand-new workflow hits. Sitting
// above keeps its x anchored to a point already known to be visible.
function _wfTriggerDefaultPos(def) {
  const nodes = def.nodes || [];
  if (!nodes.length) return { x: 40, y: 40 };
  const edges = def.edges || [];
  const targets = new Set(edges.map(e => e.to));
  const root = nodes.find(n => !targets.has(n.name)) || nodes[0];
  return { x: root.x || 0, y: (root.y || 0) - 110 };
}

function _wfRenderTriggerBox(st) {
  const def = st.def;
  const trigger = def.trigger || (def.trigger = { type: 'manual' });
  const hasPos = typeof trigger.x === 'number' && typeof trigger.y === 'number';
  const pos = hasPos ? { x: trigger.x, y: trigger.y } : _wfTriggerDefaultPos(def);
  const label = trigger.type === 'schedule' ? 'On a schedule' : 'Manual';
  return `<div class="wfb-trigger-box" data-name="__trigger__" style="left:${pos.x}px;top:${pos.y}px">
    <div class="wfb-trigger-box-head" onpointerdown="_wfNodeDragDown(event)">
      <span class="wfb-trigger-box-icon">&#9654;</span>
      <span class="wfb-trigger-box-title">Trigger</span>
    </div>
    <div class="wfb-trigger-box-sub">${esc(label)}</div>
  </div>`;
}

function _wfRenderBody(st) {
  const def = st.def;
  st._cardSeq = 0;
  st._charLoads = [];
  const triggerType = (def.trigger && def.trigger.type) || 'manual';
  const nodes = def.nodes || [];
  const nodesHtml = nodes.map(n => _wfRenderNode(st, n)).join('');
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
        <label class="wfb-trigger-opt">
          <input type="radio" name="wfb-trigger" value="schedule" ${triggerType === 'schedule' ? 'checked' : ''} onchange="_wfSetTriggerType('schedule')">
          On a schedule
        </label>
      </div>
      ${triggerType === 'schedule' ? _wfRenderScheduleCadence(st) : ''}
    </div>
    <div class="wfb-builder">
      <div class="wfb-palette" id="wfb-palette">${_wfRenderPalette(st)}</div>
      <div id="wfb-canvas-viewport" class="wfb-canvas-viewport" onpointerdown="_wfViewportDown(event)">
        <svg id="wfb-canvas-svg" class="wfb-canvas-svg"></svg>
        <div id="wfb-world" class="wfb-canvas-world">${_wfRenderTriggerBox(st)}${nodesHtml}</div>
        ${nodes.length ? '' : '<div class="wfb-canvas-empty">drop anyone anywhere &middot; drag a port to connect &middot; + on a port adds &amp; wires the next step</div>'}
      </div>
    </div>
    ${st.error ? `<div class="wfb-error">${esc(st.error)}</div>` : ''}
    <div class="wfb-actions">
      <button class="btn-sched-save" onclick="_wfSave()" ${st.saving ? 'disabled' : ''}>${st.saving ? 'Saving…' : (st.workflowId ? 'Update' : 'Create')}</button>
      <button class="btn-sched-cancel" style="color:var(--accent);border-color:var(--accent)" onclick="_wfRunNow()"
        title="${st.workflowId ? 'Validate and run this workflow now' : 'Save the workflow first'}">&#x25B6; Run now</button>
      <span id="wfb-save-stamp" class="wfb-save-stamp${st.dirty ? ' wfb-save-stamp-dirty' : ''}">${st.dirty ? 'Unsaved changes' : esc(_wfRelativeSavedLabel(st.savedAt))}</span>
    </div>`;
}

// The palette is the Bench plus exactly two tools (UI brief §2). People are
// listed first because they are the common case; the tools sit under a rule so
// the eye lands on a face, not on a primitive.
const WFB_PALETTE_PEOPLE_CAP = 8;

function _wfRenderPalette(st) {
  const bench = _wfBenchFiltered(st, st.paletteSearch);
  // A search has already narrowed the list, so it shows every match; the
  // unfiltered list caps and offers the rest behind "+ N more".
  const shown = st.paletteSearch ? bench : bench.slice(0, WFB_PALETTE_PEOPLE_CAP);
  const hidden = bench.length - shown.length;
  const rows = shown.map(b => `<div class="wfb-palette-person"
      onpointerdown="_wfPaletteDown(event,'person','${_wfJsStrEsc(b.scope || 'global')}','${_wfJsStrEsc(b.name)}')"
      title="${esc(b.description || b.name)}">
      <span class="wfb-palette-avatar">${_wfAvatarHTML(b, 28)}</span>
      <span class="wfb-palette-person-info">
        <span class="wfb-palette-person-name">${esc(b.display || b.name)}</span>
        <span class="wfb-palette-person-role">${esc(b.name)}</span>
      </span>
    </div>`).join('');
  const empty = st.benchLoaded
    ? (st.paletteSearch ? 'No one matches.' : 'You have not hired anyone yet.')
    : 'Loading the bench&hellip;';
  return `
    <div class="wfb-palette-title">People &middot; drag onto canvas</div>
    <input class="wfb-palette-search" id="wfb-palette-search" placeholder="Search bench&hellip;"
      value="${esc(st.paletteSearch || '')}" oninput="_wfPaletteSearch(this.value)">
    <div class="wfb-palette-people">${rows || `<div class="wfb-palette-empty">${empty}</div>`}</div>
    <button type="button" class="wfb-palette-more" onclick="_wfHireSomeone()"
      >${hidden > 0 ? `+ ${hidden} more &middot; ` : ''}Hire someone new</button>
    <div class="wfb-palette-divider"></div>
    <div class="wfb-palette-tools-title">Tools</div>
    <div class="wfb-palette-block" onpointerdown="_wfPaletteDown(event,'approval')">
      <span class="wfb-palette-icon">&#9995;</span>
      <span class="wfb-palette-block-info"><span>Approval gate</span>
        <span class="wfb-palette-block-sub">a human decides</span></span>
    </div>
    <div class="wfb-palette-block" onpointerdown="_wfPaletteDown(event,'action')">
      <span class="wfb-palette-icon">&#9881;</span>
      <span class="wfb-palette-block-info"><span>Action</span>
        <span class="wfb-palette-block-sub">${Object.keys(_WF_ACTION_LABELS).length} verbs &middot; no agent</span></span>
    </div>
    <div class="wfb-palette-hint">Drop a person on the canvas, or on a card to run after it. Every port's + adds and wires the next step.</div>`;
}

// Re-renders ONLY the palette: a keystroke in the search box must not rebuild
// the canvas (that would blow away an unsynced prompt the user is typing in a
// card, and reset the viewport mid-search).
function _wfPaletteSearch(value) {
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  entry._wf.paletteSearch = value;
  const box = document.getElementById('wfb-palette');
  if (!box) return;
  box.innerHTML = _wfRenderPalette(entry._wf);
  const input = document.getElementById('wfb-palette-search');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
}

// Claydo's character mode, the one creation flow (floor.js `floorHire`) — not
// a second one. Guarded the same way: it is a cross-module global.
function _wfHireSomeone() {
  if (typeof window.floorHire === 'function') window.floorHire();
}

function _wfRenderNode(st, node) {
  const nameAttr = esc(node.name || '');
  let own = '';
  if (node.type === 'agent') own = _wfRenderAgentOwn(st, node);
  else if (node.type === 'approval') own = _wfRenderApprovalOwn(node);
  else if (node.type === 'action') own = _wfRenderActionOwn(st, node);
  else own = 'Unknown node type.';
  const edges = st.def.edges || [];
  const outPorts = _wfOutPorts(node);
  const portsHtml = outPorts.map((p) => {
    const connected = edges.some(e => e.from === node.name && (e.when || null) === (p.when || null));
    return `<div class="wfb-port-row${p.when === 'otherwise' ? ' wfb-port-otherwise' : ''}${connected ? '' : ' wfb-port-unconnected'}">
      ${p.label ? `<span class="wfb-port-label">${esc(p.label)}</span>` : ''}
      <span class="wfb-port wfb-port-out" data-node="${nameAttr}" data-when="${esc(p.when || '')}" onpointerdown="_wfPortDown(event)"><span class="wfb-port-dot"></span></span>
      <button type="button" class="wfb-port-plus" title="After &ldquo;${esc(p.label || 'this step')}&rdquo;, run&hellip;"
        onclick="_wfPortPlusClick(event,'${_wfJsStrEsc(node.name)}','${_wfJsStrEsc(p.when || '')}')">&#43;</button>
      ${connected ? '' : '<span class="wfb-port-stub"></span>'}
    </div>`;
  }).join('');
  // An agent card leads with WHO, not with a type label — the persona was
  // chosen by the drag itself (UI brief §3), so the face is the identity and
  // the step name is the subtitle it is referenced by in slots.
  const person = node.type === 'agent' ? _wfPersonFromCharacter(st, node.character) : null;
  const headHtml = node.type === 'agent'
    ? `<span class="wfb-node-avatar">${_wfAvatarHTML(person, 22)}</span>
       <span class="wfb-node-title">
         <span class="wfb-node-persona">${esc(person ? (person.display || person.name) : 'No persona yet')}</span>
         <span class="wfb-node-step-sep">&middot;</span>
         <span class="wfb-node-step-name">${esc(node.name || '')}</span>
       </span>`
    : `<span class="wfb-node-type">${_wfTypeLabel(node.type)}</span>`;
  // Step 6: Run-now (and a failed Save) surface INLINE on the offending card,
  // not only as a toast (brief §7) -- st.runErrors is a {nodeName: message}
  // map a validate attempt populates; _wfRender's caller pans the canvas to
  // st.scrollToNode so the red-outlined card is the one already in view.
  const runError = st.runErrors && st.runErrors[node.name];
  return `<div class="wfb-node${runError ? ' wfb-node-error' : ''}" data-name="${nameAttr}" style="left:${node.x || 0}px;top:${node.y || 0}px">
    <div class="wfb-node-head" onpointerdown="_wfNodeDragDown(event)">
      ${headHtml}
      <button class="wfb-node-del" title="Delete step" onclick="_wfDeleteNode('${_wfJsStrEsc(node.name)}')">&#10005;</button>
    </div>
    <div class="wfb-node-own">
      ${runError ? `<div class="wfb-node-inline-error">${esc(runError)}</div>` : ''}
      ${own}
    </div>
    <span class="wfb-port wfb-port-in" data-node="${nameAttr}"><span class="wfb-port-dot"></span></span>
    <div class="wfb-ports-out">${portsHtml}</div>
  </div>`;
}

// Verbatim from the Phase 2 spine (static/js/workflow-builder.js pre-canvas):
// project select, persona picker with its face + caching, prompt textarea.
// Only the branching editor below the prompt changed (a flat `outcomes`
// array instead of nested branch lists, since branching is now edge `when`
// labels, not a child node — R2-D6).
function _wfRenderAgentOwn(st, node) {
  const seq = ++st._cardSeq;
  const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter(p => p.project_path);
  const pid = node.project_id || (projects[0] && projects[0].id) || '';
  st._charLoads.push({ seq, want: node.character || '' });
  const outcomes = node.outcomes || [];
  return `
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
    ${_wfInsertControlHTML(st.def, node.name, '.wfb-prompt')}
    ${_wfSlotChipsHTML(st.def, node.name, node.prompt || '')}
    <div class="wfb-branch-labels">
      <label>Outcomes <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(this step must end its reply naming one &mdash; each gets its own port below to wire up)</span></label>
      ${outcomes.map((label, oi) => `<div class="wfb-branch-label-row">
        <input class="wfb-outcome-input" value="${esc(label)}">
        <button class="wfb-branch-del" title="Remove outcome" onclick="_wfRemoveOutcome('${_wfJsStrEsc(node.name)}',${oi})">&#10005;</button>
      </div>`).join('')}
      <button class="wfb-add-btn" onclick="_wfAddOutcome('${_wfJsStrEsc(node.name)}')">+ Add outcome</button>
    </div>`;
}

function _wfRenderApprovalOwn(node) {
  const options = node.options || [];
  return `
    <label>Name</label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="approval-name">
    <div class="wfb-branch-labels">
      <label>Options <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(what a human can choose &mdash; delivered over the question channel; each gets its own port)</span></label>
      ${options.map((label, oi) => `<div class="wfb-branch-label-row">
        <input class="wfb-option-input" value="${esc(label)}">
        <button class="wfb-branch-del" title="Remove option" onclick="_wfRemoveOption('${_wfJsStrEsc(node.name)}',${oi})">&#10005;</button>
      </div>`).join('')}
      <button class="wfb-add-btn" onclick="_wfAddOption('${_wfJsStrEsc(node.name)}')">+ Add option</button>
    </div>`;
}

const _WF_ACTION_LABELS = { backlog_create: 'Create a backlog item', backlog_patch: 'Patch a backlog item', desk_harvest: 'Run a Desk harvest' };

function _wfRenderActionOwn(st, node) {
  const action = node.action || 'backlog_create';
  return `
    <label>Name</label>
    <input class="wfb-name" value="${esc(node.name || '')}" placeholder="action-name">
    <label>Action</label>
    <select class="wfb-action-select" onchange="_wfRerenderActionFields(this)">
      ${Object.keys(_WF_ACTION_LABELS).map(a => `<option value="${a}"${a === action ? ' selected' : ''}>${esc(_WF_ACTION_LABELS[a])}</option>`).join('')}
    </select>
    <div class="wfb-action-fields">${_wfActionFieldsHTML(action, node.config || {}, st.def, node.name)}</div>`;
}

// def/nodeName (step 5b): "anywhere a slot is legal, e.g. the backlog
// action's item-id field, offer an Insert control" (brief) -- every
// data-cfg-key text input/textarea below gets its own Insert dropdown +
// chip legend, keyed to that one field by its own `[data-cfg-key="…"]`
// selector so each field's insert lands in the right place.
function _wfActionFieldsHTML(action, cfg, def, nodeName) {
  const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter(p => p.project_path);
  const projOpts = (includeAny) => (includeAny ? '<option value="">(any project)</option>' : '')
    + projects.map(p => `<option value="${esc(p.id)}"${p.id === cfg.project_id ? ' selected' : ''}>${esc(p.name)}</option>`).join('');
  const slotField = (selector, text) => def && nodeName
    ? `${_wfInsertControlHTML(def, nodeName, selector)}${_wfSlotChipsHTML(def, nodeName, text)}` : '';
  if (action === 'backlog_patch') {
    return `
      <label>Project</label>
      <select data-cfg-key="project_id" data-cfg-required="1">${projOpts(false)}</select>
      <label>Backlog item ID <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(can be a slot, e.g. <code>{{steps.triage.result.item_id}}</code>)</span></label>
      <input data-cfg-key="item_id" data-cfg-required="1" value="${esc(cfg.item_id || '')}">
      ${slotField('[data-cfg-key="item_id"]', cfg.item_id || '')}
      <label>New status <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = leave unchanged)</span></label>
      <select data-cfg-key="status">
        <option value=""${!cfg.status ? ' selected' : ''}>(unchanged)</option>
        ${['open', 'in_progress', 'blocked', 'done'].map(s => `<option value="${s}"${cfg.status === s ? ' selected' : ''}>${s}</option>`).join('')}
      </select>
      <label>New text <span class="memory-hint" style="margin:0;font-weight:normal;text-transform:none">(blank = leave unchanged)</span></label>
      <textarea data-cfg-key="text" rows="2">${esc(cfg.text || '')}</textarea>
      ${slotField('[data-cfg-key="text"]', cfg.text || '')}`;
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
    ${slotField('[data-cfg-key="text"]', cfg.text || '')}
    <label>Priority</label>
    <select data-cfg-key="priority">
      ${['low', 'normal', 'high'].map(p => `<option value="${p}"${(cfg.priority || 'normal') === p ? ' selected' : ''}>${p}</option>`).join('')}
    </select>`;
}

function _wfRerenderActionFields(selectEl) {
  const own = selectEl.closest('.wfb-node-own');
  const box = own && own.querySelector('.wfb-action-fields');
  if (!box) return;
  const entry = _wfEntry();
  const nodeEl = selectEl.closest('.wfb-node');
  const nodeName = nodeEl ? nodeEl.dataset.name : '';
  const def = entry && entry._wf ? entry._wf.def : null;
  box.innerHTML = _wfActionFieldsHTML(selectEl.value, {}, def, nodeName);
}

// ── Persona picker (mirrors scheduler.js's reloadSchedCharacters, per-node) ──

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
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  // Keep x/y (MC-871 Change 2): they're the canvas box's dragged position,
  // unrelated to which radio is picked -- a fresh `{type: t}` here would snap
  // the box back to its default spot every time the trigger type changes.
  const { x, y } = st.def.trigger || {};
  st.def.trigger = (typeof x === 'number' && typeof y === 'number') ? { type: t, x, y } : { type: t };
  if (t === 'schedule' && !st.linkedSchedule) st.linkedSchedule = _wfDraftSchedule();
  _wfMarkDirty();
  _wfRender();
}

// ── Trigger card: schedule cadence sub-form ──────────────────────────────────
// The cadence itself lives on the linked SCHEDULE record (spec Q4 -- "one
// store, two views"), never duplicated onto def.trigger. This mirrors
// scheduler.js's own type-fields form but under a distinct `wfb-sched-*`
// namespace so the two forms can never collide if both modals are open.

function _wfRenderScheduleCadence(st) {
  const s = st.linkedSchedule || _wfDraftSchedule();
  const missing = !!(st.def.trigger && st.def.trigger.type === 'schedule' && st.linkedSchedule === null && st.workflowId);
  return `
    <div class="wfb-sched-cadence">
      ${missing ? '<div class="memory-hint" style="margin:0 0 8px">No schedule found for this trigger yet &mdash; save to create one.</div>' : ''}
      <div class="sched-type-row">
        ${['daily', 'weekly', 'interval', 'once', 'cron'].map(t => `<button type="button" class="sched-type-btn${s.schedule_type === t ? ' active' : ''}" onclick="_wfSetSchedType('${t}')">${t[0].toUpperCase()}${t.slice(1)}</button>`).join('')}
      </div>
      <div id="wfb-sched-type-fields">${_wfSchedTypeFieldsHTML(s.schedule_type, s)}</div>
      ${st.linkedSchedule && st.linkedSchedule.id ? `
      <label style="display:flex;align-items:center;gap:8px;cursor:pointer;font-weight:normal;margin-top:8px">
        <span class="schedule-toggle ${st.linkedSchedule.enabled !== false ? 'on' : ''}" onclick="_wfToggleSchedEnabled()"></span>
        <span>${st.linkedSchedule.enabled !== false ? 'Enabled' : 'Disabled'}</span>
      </label>` : ''}
    </div>`;
}

// Deliberately separate from scheduler.js's renderSchedTypeFields (different
// element ids under wfb-sched-*) rather than shared, to avoid two forms
// fighting over one #sched-type-fields if the Scheduler modal is ALSO open.
// <input type="datetime-local"> speaks local wall time with no zone attached
// -- inlined rather than imported, since scheduler.js's own `_schedLocalInputValue`
// is module-private (ES modules don't leak top-level bindings; see this
// file's header on window accessors).
function _wfLocalInputValue(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
}

function _wfSchedTypeFieldsHTML(type, s) {
  const time = s.time || '09:00';
  const days = s.days || [1, 2, 3, 4, 5];
  const interval = s.interval_minutes || 60;
  const runAt = _wfLocalInputValue(s.run_at);
  const cronExpr = s.cron_expr || '';
  if (type === 'daily') {
    return `
      <label>Time</label>
      <input type="time" id="wfb-sched-time" value="${esc(time)}">`;
  }
  if (type === 'weekly') {
    const dayLabels = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
    return `
      <label>Time</label>
      <input type="time" id="wfb-sched-time" value="${esc(time)}">
      <label>Days</label>
      <div class="sched-days">
        ${dayLabels.map((label, i) => { const d = i + 1; return `<button type="button" class="sched-day-btn${days.includes(d) ? ' active' : ''}" data-day="${d}" onclick="this.classList.toggle('active');_wfMarkDirty()">${label}</button>`; }).join('')}
      </div>`;
  }
  if (type === 'interval') {
    return `<label>Interval (minutes)</label><input type="number" id="wfb-sched-interval" value="${interval}" min="1" step="1">`;
  }
  if (type === 'once') {
    return `<label>Run At</label><input type="datetime-local" id="wfb-sched-runat" value="${esc(runAt)}">`;
  }
  if (type === 'cron') {
    return `<label>Cron Expression</label><input type="text" id="wfb-sched-cron" value="${esc(cronExpr)}" placeholder="*/15 * * * *" spellcheck="false" style="font-family:var(--mono)">`;
  }
  return '';
}

function _wfSetSchedType(type) {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  _wfSyncScheduleFormToState(st);
  st.linkedSchedule.schedule_type = type;
  const box = document.getElementById('wfb-sched-type-fields');
  if (box) box.innerHTML = _wfSchedTypeFieldsHTML(type, st.linkedSchedule);
  document.querySelectorAll('.wfb-sched-cadence .sched-type-btn').forEach(b => b.classList.toggle('active', b.textContent.toLowerCase() === type));
  _wfMarkDirty();
}

function _wfToggleSchedEnabled() {
  const entry = _wfEntry(); if (!entry) return;
  const st = entry._wf;
  if (!st.linkedSchedule) return;
  st.linkedSchedule.enabled = !(st.linkedSchedule.enabled !== false);
  _wfMarkDirty();
  _wfRender();
}

// Reads the cadence sub-form's live DOM values back into st.linkedSchedule.
// Called before anything that might re-render the card (switching type) or
// save, mirroring _wfSyncDomToModel's "sync at the moment of a structural
// action" rule -- typing in the cron field is never clobbered by that.
function _wfSyncScheduleFormToState(st) {
  if (!st.linkedSchedule) st.linkedSchedule = _wfDraftSchedule();
  const s = st.linkedSchedule;
  const timeEl = document.getElementById('wfb-sched-time');
  if (timeEl) s.time = timeEl.value;
  const daysEls = document.querySelectorAll('.wfb-sched-cadence .sched-day-btn.active');
  if (daysEls.length || document.getElementById('wfb-sched-time')) {
    s.days = [...daysEls].map(b => parseInt(b.dataset.day, 10));
  }
  const intervalEl = document.getElementById('wfb-sched-interval');
  if (intervalEl) s.interval_minutes = parseInt(intervalEl.value, 10) || 60;
  const runAtEl = document.getElementById('wfb-sched-runat');
  if (runAtEl && runAtEl.value) {
    // <input type="datetime-local"> is local wall time with no zone attached
    // -- convert through Date the same way scheduler.js's saveSchedule does,
    // never a raw string round-trip (that shifted a one-shot's fire time by
    // the host's UTC offset).
    const d = new Date(runAtEl.value);
    if (!isNaN(d.getTime())) s.run_at = d.toISOString();
  }
  const cronEl = document.getElementById('wfb-sched-cron');
  if (cronEl) s.cron_expr = cronEl.value;
}

function _wfAddOutcome(name) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const node = (entry._wf.def.nodes || []).find(n => n.name === name); if (!node) return;
  node.outcomes = node.outcomes || [];
  node.outcomes.push('outcome-' + (node.outcomes.length + 1));
  _wfMarkDirty();
  _wfRender();
}

function _wfRemoveOutcome(name, idx) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const def = entry._wf.def;
  const node = (def.nodes || []).find(n => n.name === name); if (!node) return;
  const label = (node.outcomes || [])[idx];
  if (label === undefined) return;
  const before = _wfFindBrokenSlotRefs(def.nodes, def.edges || []);
  const savedOutcomes = node.outcomes;
  node.outcomes = node.outcomes.filter((_, i) => i !== idx);
  const newEdges = (def.edges || []).filter(e => !(e.from === name && e.when === label));
  const after = _wfFindBrokenSlotRefs(def.nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    node.outcomes = savedOutcomes;
    showToast(`Can't remove outcome "${label}" — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs the edge on that port.`, 6000);
    return;
  }
  def.edges = newEdges;
  _wfMarkDirty();
  _wfRender();
}

function _wfAddOption(name) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const node = (entry._wf.def.nodes || []).find(n => n.name === name); if (!node) return;
  const n = (node.options || []).length + 1;
  node.options = (node.options || []).concat(['option-' + n]);
  _wfMarkDirty();
  _wfRender();
}

function _wfRemoveOption(name, idx) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const def = entry._wf.def;
  const node = (def.nodes || []).find(n => n.name === name); if (!node) return;
  const label = (node.options || [])[idx];
  if (label === undefined) return;
  if ((node.options || []).length <= 1) { showToast('An approval gate needs at least one option.', 4000); return; }
  const before = _wfFindBrokenSlotRefs(def.nodes, def.edges || []);
  const savedOptions = node.options;
  node.options = node.options.filter((_, i) => i !== idx);
  const newEdges = (def.edges || []).filter(e => !(e.from === name && e.when === label));
  const after = _wfFindBrokenSlotRefs(def.nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    node.options = savedOptions;
    showToast(`Can't remove option "${label}" — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs the edge on that port.`, 6000);
    return;
  }
  def.edges = newEdges;
  _wfMarkDirty();
  _wfRender();
}

function _wfDeleteNode(name) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  name = renameMap[name] || name;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const newNodes = nodes.filter(n => n.name !== name);
  const newEdges = edges.filter(e => e.from !== name && e.to !== name);
  const after = _wfFindBrokenSlotRefs(newNodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't delete "${name}" — "${newOnes[0].step}" still reads {{steps.${newOnes[0].ref}.…}}.`, 6000);
    return;
  }
  def.nodes = newNodes;
  def.edges = newEdges;
  if (_wfSelectedEdge && (_wfSelectedEdge.from === name || _wfSelectedEdge.to === name)) _wfSelectedEdge = null;
  _wfMarkDirty();
  _wfRender();
}

let _wfSelectedEdge = null; // { from, to, when } | null

function _wfEdgeClick(e, from, to, when) {
  e.stopPropagation();
  _wfSelectedEdge = { from, to, when: when || null };
  _wfRedrawEdges();
}

function _wfDeleteEdge(from, to, when) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  from = renameMap[from] || from;
  to = renameMap[to] || to;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const newEdges = edges.filter(e => !(e.from === from && e.to === to && (e.when || null) === (when || null)));
  const after = _wfFindBrokenSlotRefs(nodes, newEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't remove that connection — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which needs it to stay reachable.`, 6000);
    return;
  }
  def.edges = newEdges;
  _wfSelectedEdge = null;
  _wfMarkDirty();
  _wfRender();
}

document.addEventListener('keydown', (e) => {
  if (!_wfState || !_wfSelectedEdge) return;
  const tag = document.activeElement && document.activeElement.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA') return;
  if (e.key === 'Delete' || e.key === 'Backspace') {
    _wfDeleteEdge(_wfSelectedEdge.from, _wfSelectedEdge.to, _wfSelectedEdge.when);
  } else if (e.key === 'Escape') {
    _wfSelectedEdge = null;
    _wfRedrawEdges();
  }
});

function _wfTryAddEdge(from, to, when) {
  const entry = _wfEntry(); if (!entry) return;
  const renameMap = _wfSyncDomToModel(entry);
  from = renameMap[from] || from;
  to = renameMap[to] || to;
  const def = entry._wf.def;
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  if (edges.some(e => e.from === from && e.to === to && (e.when || null) === (when || null))) return;
  // R2-D3 layer 1: the drag that would close a cycle is refused at drop.
  if (_wfHasPath(edges, to, from)) {
    showToast(`${from} → ${to} would create a loop (${to} already reaches ${from}).`, 5000);
    return;
  }
  const before = _wfFindBrokenSlotRefs(nodes, edges);
  const candidateEdges = edges.concat([{ from, to, when: when || undefined }]);
  const after = _wfFindBrokenSlotRefs(nodes, candidateEdges);
  const newOnes = _wfNewViolations(before, after);
  if (newOnes.length) {
    showToast(`Can't connect ${from} → ${to} — "${newOnes[0].step}" reads {{steps.${newOnes[0].ref}.…}}, which this would make skippable.`, 6000);
    return;
  }
  def.edges = candidateEdges;
  _wfMarkDirty();
  _wfRender();
}

// ── Canvas: pan + zoom (viewport is session-only, see file header) ──────────

function _wfApplyViewport(st) {
  const world = document.getElementById('wfb-world');
  if (world) world.style.transform = `translate(${st.viewport.x}px, ${st.viewport.y}px) scale(${st.viewport.scale})`;
  _wfRedrawEdges();
}

function _wfZoomAt(st, screenX, screenY, newScaleRaw) {
  const newScale = Math.min(2.5, Math.max(0.25, newScaleRaw));
  const v = st.viewport;
  const wx = (screenX - v.x) / v.scale;
  const wy = (screenY - v.y) / v.scale;
  v.x = screenX - wx * newScale;
  v.y = screenY - wy * newScale;
  v.scale = newScale;
  _wfApplyViewport(st);
}

function _wfCanvasWheel(e) {
  e.preventDefault();
  const entry = _wfEntry(); if (!entry) return;
  const rect = e.currentTarget.getBoundingClientRect();
  const cx = e.clientX - rect.left, cy = e.clientY - rect.top;
  const factor = Math.exp(-e.deltaY * 0.0015);
  _wfZoomAt(entry._wf, cx, cy, entry._wf.viewport.scale * factor);
}

let _wfPan = null;

function _wfViewportDown(e) {
  if (e.target.closest('.wfb-node, .wfb-port')) return;
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfPan || _wfNodeDrag || _wfPlaceDrag || _wfConnectDrag) return;
  const entry = _wfEntry(); if (!entry) return;
  const vp = e.currentTarget;
  _wfPan = { pointerId: e.pointerId, startX: e.clientX, startY: e.clientY, vx: entry._wf.viewport.x, vy: entry._wf.viewport.y, vp };
  vp.classList.add('wfb-panning');
  try { vp.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  vp.addEventListener('pointermove', _wfViewportMove);
  vp.addEventListener('pointerup', _wfViewportUp);
  vp.addEventListener('pointercancel', _wfViewportUp);
}

function _wfViewportMove(e) {
  if (!_wfPan || e.pointerId !== _wfPan.pointerId) return;
  const entry = _wfEntry(); if (!entry) return;
  entry._wf.viewport.x = _wfPan.vx + (e.clientX - _wfPan.startX);
  entry._wf.viewport.y = _wfPan.vy + (e.clientY - _wfPan.startY);
  _wfApplyViewport(entry._wf);
}

function _wfViewportUp(e) {
  if (!_wfPan || (e.pointerId !== undefined && e.pointerId !== _wfPan.pointerId)) return;
  const st = _wfPan;
  st.vp.classList.remove('wfb-panning');
  try { st.vp.releasePointerCapture(st.pointerId); } catch (err) { /* already released */ }
  st.vp.removeEventListener('pointermove', _wfViewportMove);
  st.vp.removeEventListener('pointerup', _wfViewportUp);
  st.vp.removeEventListener('pointercancel', _wfViewportUp);
  _wfPan = null;
}

// Native touch listeners for 2-finger pinch — Pointer Events don't aggregate
// multi-touch, so this mirrors mermaid.js's viewer-gesture pinch handling
// rather than extending the pointer-based pan above.
let _wfPinch = null;

function _wfTouchDist(t) { return Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY); }
function _wfTouchMid(t) { return { x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 }; }

function _wfCanvasTouchStart(e) {
  if (e.touches.length !== 2) return;
  if (_wfPan) _wfViewportUp({ pointerId: _wfPan.pointerId });
  const entry = _wfEntry(); if (!entry) return;
  _wfPinch = { d: _wfTouchDist(e.touches) || 1, scale: entry._wf.viewport.scale, vp: e.currentTarget };
  e.preventDefault();
}

function _wfCanvasTouchMove(e) {
  if (!_wfPinch || e.touches.length !== 2) return;
  e.preventDefault();
  const entry = _wfEntry(); if (!entry) return;
  const rect = _wfPinch.vp.getBoundingClientRect();
  const mid = _wfTouchMid(e.touches);
  _wfZoomAt(entry._wf, mid.x - rect.left, mid.y - rect.top, _wfPinch.scale * (_wfTouchDist(e.touches) / _wfPinch.d));
}

function _wfCanvasTouchEnd(e) {
  if (_wfPinch && e.touches.length < 2) _wfPinch = null;
}

function _wfAttachCanvasGestures() {
  const vp = document.getElementById('wfb-canvas-viewport');
  if (!vp) return;
  vp.addEventListener('wheel', _wfCanvasWheel, { passive: false });
  vp.addEventListener('touchstart', _wfCanvasTouchStart, { passive: false });
  vp.addEventListener('touchmove', _wfCanvasTouchMove, { passive: false });
  vp.addEventListener('touchend', _wfCanvasTouchEnd);
  vp.addEventListener('touchcancel', _wfCanvasTouchEnd);
}

// ── Drag-to-place: palette block → new node on the canvas ────────────────────
// Same pointer-event gesture shape as floor.js's drag-to-hire: long-press
// activation on touch, 8px slop on mouse/pen, a ghost that follows the
// pointer, dropped only if released over the canvas viewport.

const WFB_LONG_PRESS_MS = 400;
const WFB_DRAG_SLOP_PX = 8;

let _wfPlaceDrag = null;

function _wfPointInRect(x, y, r) { return x >= r.left && x <= r.right && y >= r.top && y <= r.bottom; }

// `type` is 'person' | 'approval' | 'action'. A person carries its bench
// identity (scope + name) through the drag so the drop can build an agent step
// with that persona already set — the whole point of the palette being the
// Bench (UI brief §2).
function _wfPaletteDown(e, type, scope, name) {
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfPlaceDrag || _wfNodeDrag || _wfPan || _wfConnectDrag) return;
  const st = {
    pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
    startX: e.clientX, startY: e.clientY, active: false, type,
    scope: scope || '', name: name || '',
    el: e.currentTarget, ghost: null, longPressTimer: null,
  };
  _wfPlaceDrag = st;
  if (st.pointerType === 'touch') {
    st.longPressTimer = setTimeout(() => { if (_wfPlaceDrag === st && !st.active) _wfPlaceActivate(st, st.startX, st.startY); }, WFB_LONG_PRESS_MS);
  }
  try { st.el.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfPlaceMove);
  window.addEventListener('pointerup', _wfPlaceUp);
  window.addEventListener('pointercancel', _wfPlaceCancel);
  window.addEventListener('blur', _wfPlaceCancel);
}

function _wfPlaceActivate(st, x, y) {
  st.active = true;
  clearTimeout(st.longPressTimer);
  st.el.classList.add('wfb-palette-dragging');
  const ghost = document.createElement('div');
  ghost.className = 'wfb-place-ghost';
  ghost.textContent = st.type === 'person'
    ? (((_wfEntry() || {})._wf
        && (_wfBenchLookup(_wfEntry()._wf, st.scope, st.name) || {}).display) || st.name)
    : _wfTypeLabel(st.type);
  ghost.style.left = x + 'px';
  ghost.style.top = y + 'px';
  document.body.appendChild(ghost);
  st.ghost = ghost;
}

function _wfPlaceMove(e) {
  const st = _wfPlaceDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const dx = e.clientX - st.startX, dy = e.clientY - st.startY;
  if (!st.active) {
    if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX) _wfPlaceActivate(st, e.clientX, e.clientY);
    else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX * 1.5) { clearTimeout(st.longPressTimer); _wfPlaceTeardown(st); }
    return;
  }
  e.preventDefault();
  if (st.ghost) { st.ghost.style.left = e.clientX + 'px'; st.ghost.style.top = e.clientY + 'px'; }
  const vp = document.getElementById('wfb-canvas-viewport');
  if (vp) vp.classList.toggle('wfb-drop-target', _wfPointInRect(e.clientX, e.clientY, vp.getBoundingClientRect()));
}

function _wfPlaceUp(e) {
  const st = _wfPlaceDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) {
    const vp = document.getElementById('wfb-canvas-viewport');
    if (vp && _wfPointInRect(e.clientX, e.clientY, vp.getBoundingClientRect())) {
      _wfPlaceNodeAt(st.type, e.clientX, e.clientY, vp, st.scope, st.name);
    }
  }
  _wfPlaceTeardown(st);
}

function _wfPlaceCancel(e) {
  const st = _wfPlaceDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  _wfPlaceTeardown(st);
}

function _wfPlaceTeardown(st) {
  st.el.classList.remove('wfb-palette-dragging');
  const vp = document.getElementById('wfb-canvas-viewport');
  if (vp) vp.classList.remove('wfb-drop-target');
  if (st.ghost) { st.ghost.remove(); st.ghost = null; }
  document.querySelectorAll('.wfb-place-ghost').forEach(g => g.remove());
  window.removeEventListener('pointermove', _wfPlaceMove);
  window.removeEventListener('pointerup', _wfPlaceUp);
  window.removeEventListener('pointercancel', _wfPlaceCancel);
  window.removeEventListener('blur', _wfPlaceCancel);
  try { st.el.releasePointerCapture(st.pointerId); } catch (e) { /* already released */ }
  _wfPlaceDrag = null;
}

function _wfPlaceNodeAt(type, clientX, clientY, vp, scope, name) {
  const entry = _wfEntry(); if (!entry) return;
  // Sync first: a drop re-renders, and the sync can RENAME the very card being
  // dropped onto, so resolve the target's name only after it has run.
  const renameMap = _wfSyncDomToModel(entry);
  const st = entry._wf;

  // Dropped ONTO an existing card = the same thing as that card's `+`: place
  // after it and wire the edge (UI brief §4). Which port: the one actually
  // under the pointer if there is one, else the card's first output port.
  const under = document.elementFromPoint(clientX, clientY);
  const cardEl = under && under.closest ? under.closest('.wfb-node') : null;
  if (cardEl && cardEl.dataset.name) {
    const fromName = renameMap[cardEl.dataset.name] || cardEl.dataset.name;
    const portEl = under.closest('.wfb-port-out');
    const fromNode = (st.def.nodes || []).find(n => n.name === fromName);
    if (fromNode) {
      const when = portEl ? (portEl.dataset.when || null) : ((_wfOutPorts(fromNode)[0] || {}).when || null);
      _wfInsertAfter(entry, fromName, when, type, scope, name, { synced: true });
      return;
    }
  }

  const rect = vp.getBoundingClientRect();
  const v = st.viewport;
  const wx = (clientX - rect.left - v.x) / v.scale;
  const wy = (clientY - rect.top - v.y) / v.scale;
  const node = _wfMakeNode(st, type, scope, name, wx - 130, wy - 24);
  st.def.nodes = (st.def.nodes || []).concat([node]);
  _wfMarkDirty();
  _wfRender();
  if (node.type === 'agent') _wfFocusPrompt(node.name);
}

// Place a new node after `fromName`'s `when` port and wire the edge. The one
// path every auto-place uses — the port `+` popover and drop-onto-card both
// land here, so "added and wired" means one thing.
//
// No cycle check is needed: the node is brand new, so nothing can already
// reach it. No slot-break check either — this only ADDS an edge and a node,
// and `_wfFindBrokenSlotRefs` violations come from removing reachability.
function _wfInsertAfter(entry, fromName, when, type, scope, name, opts) {
  if (!(opts && opts.synced)) {
    const renameMap = _wfSyncDomToModel(entry);
    fromName = renameMap[fromName] || fromName;
  }
  const st = entry._wf;
  const def = st.def;
  const fromNode = (def.nodes || []).find(n => n.name === fromName);
  if (!fromNode) return;
  // Desktop places the new card to the right of its parent and stacks
  // branches downward, one row per port (UI brief §5).
  const idx = Math.max(0, _wfOutPorts(fromNode).findIndex(p => (p.when || null) === (when || null)));
  const node = _wfMakeNode(st, type, scope, name, (fromNode.x || 0) + 320, (fromNode.y || 0) + idx * 150);
  def.nodes = (def.nodes || []).concat([node]);
  def.edges = (def.edges || []).concat([{ from: fromName, to: node.name, when: when || undefined }]);
  _wfMarkDirty();
  _wfRender();
  if (node.type === 'agent') _wfFocusPrompt(node.name);
}

// The prompt is the one place the author actually types (UI brief §2 — "prompt
// focuses"), so a freshly-dropped person hands them the caret.
function _wfFocusPrompt(nodeName) {
  const el = document.querySelector(`.wfb-node[data-name="${_wfAttrEsc(nodeName)}"] .wfb-prompt`);
  // preventScroll is load-bearing, not a nicety: the modal body is an
  // overflow:auto scroller, so a plain focus() scrolls the freshly-dropped
  // card into view and drags the whole canvas out from under the pointer —
  // the card you just placed jumps, and the next drop lands somewhere else.
  if (el) el.focus({ preventScroll: true });
}

// ── Drag to move an existing node ─────────────────────────────────────────────

let _wfNodeDrag = null;

function _wfNodeDragDown(e) {
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfNodeDrag || _wfPan || _wfPlaceDrag || _wfConnectDrag) return;
  // The Trigger box (MC-871 Change 2) is deliberately NOT a node -- not in
  // `nodes[]`, no card markup -- but Ron wants it draggable on the same
  // canvas via the same gesture, so it shares this one drag path rather than
  // getting a second implementation. `_wfNodeDragUp` below is the only place
  // that branches on which kind of element this turned out to be.
  const nodeEl = e.currentTarget.closest('.wfb-node, .wfb-trigger-box');
  const entry = _wfEntry();
  if (!nodeEl || !entry) return;
  const st = {
    pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
    startX: e.clientX, startY: e.clientY, active: false, nodeEl,
    scale: entry._wf.viewport.scale,
    origX: parseFloat(nodeEl.style.left) || 0, origY: parseFloat(nodeEl.style.top) || 0,
    longPressTimer: null,
  };
  _wfNodeDrag = st;
  if (st.pointerType === 'touch') {
    st.longPressTimer = setTimeout(() => { if (_wfNodeDrag === st && !st.active) _wfNodeDragActivate(st); }, WFB_LONG_PRESS_MS);
  }
  try { e.currentTarget.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfNodeDragMove);
  window.addEventListener('pointerup', _wfNodeDragUp);
  window.addEventListener('pointercancel', _wfNodeDragCancel);
  window.addEventListener('blur', _wfNodeDragCancel);
}

function _wfNodeDragActivate(st) {
  st.active = true;
  clearTimeout(st.longPressTimer);
  st.nodeEl.classList.add('wfb-node-dragging');
}

function _wfNodeDragMove(e) {
  const st = _wfNodeDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const dx = e.clientX - st.startX, dy = e.clientY - st.startY;
  if (!st.active) {
    if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX) _wfNodeDragActivate(st);
    else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > WFB_DRAG_SLOP_PX * 1.5) { clearTimeout(st.longPressTimer); _wfNodeDragTeardown(st); }
    return;
  }
  e.preventDefault();
  st.nodeEl.style.left = (st.origX + dx / st.scale) + 'px';
  st.nodeEl.style.top = (st.origY + dy / st.scale) + 'px';
  _wfRedrawEdges();
}

function _wfNodeDragUp(e) {
  const st = _wfNodeDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) {
    const entry = _wfEntry();
    if (entry && st.nodeEl.dataset.name === '__trigger__') {
      entry._wf.def.trigger.x = parseFloat(st.nodeEl.style.left) || 0;
      entry._wf.def.trigger.y = parseFloat(st.nodeEl.style.top) || 0;
      _wfMarkDirty();
    } else {
      const node = entry && (entry._wf.def.nodes || []).find(n => n.name === st.nodeEl.dataset.name);
      if (node) {
        node.x = parseFloat(st.nodeEl.style.left) || 0;
        node.y = parseFloat(st.nodeEl.style.top) || 0;
        _wfMarkDirty();
      }
    }
  }
  _wfNodeDragTeardown(st);
}

function _wfNodeDragCancel(e) {
  const st = _wfNodeDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (st.active) { st.nodeEl.style.left = st.origX + 'px'; st.nodeEl.style.top = st.origY + 'px'; _wfRedrawEdges(); }
  _wfNodeDragTeardown(st);
}

function _wfNodeDragTeardown(st) {
  st.nodeEl.classList.remove('wfb-node-dragging');
  window.removeEventListener('pointermove', _wfNodeDragMove);
  window.removeEventListener('pointerup', _wfNodeDragUp);
  window.removeEventListener('pointercancel', _wfNodeDragCancel);
  window.removeEventListener('blur', _wfNodeDragCancel);
  try { st.nodeEl.releasePointerCapture(st.pointerId); } catch (e) { /* already released */ }
  _wfNodeDrag = null;
}

// ── Drag to connect: output port → input port ────────────────────────────────

let _wfConnectDrag = null;

function _wfPortDown(e) {
  e.stopPropagation();
  if (typeof e.button === 'number' && e.button !== 0) return;
  if (_wfConnectDrag || _wfNodeDrag || _wfPan || _wfPlaceDrag) return;
  const portEl = e.currentTarget;
  const st = { pointerId: e.pointerId, fromNode: portEl.dataset.node, when: portEl.dataset.when || null, portEl, curX: e.clientX, curY: e.clientY };
  _wfConnectDrag = st;
  try { portEl.setPointerCapture(e.pointerId); } catch (err) { /* best-effort */ }
  window.addEventListener('pointermove', _wfConnectMove);
  window.addEventListener('pointerup', _wfConnectUp);
  window.addEventListener('pointercancel', _wfConnectCancel);
  window.addEventListener('blur', _wfConnectCancel);
  _wfRedrawEdges();
}

function _wfConnectMove(e) {
  const st = _wfConnectDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  e.preventDefault();
  st.curX = e.clientX; st.curY = e.clientY;
  const target = document.elementFromPoint(e.clientX, e.clientY);
  const portIn = target && target.closest && target.closest('.wfb-port-in');
  document.querySelectorAll('.wfb-port-in.wfb-port-target').forEach(p => { if (p !== portIn) p.classList.remove('wfb-port-target'); });
  if (portIn && portIn.dataset.node !== st.fromNode) portIn.classList.add('wfb-port-target');
  _wfRedrawEdges();
}

function _wfConnectUp(e) {
  const st = _wfConnectDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const target = document.elementFromPoint(e.clientX, e.clientY);
  const portIn = target && target.closest && target.closest('.wfb-port-in');
  _wfConnectTeardown(st);
  if (portIn && portIn.dataset.node && portIn.dataset.node !== st.fromNode) {
    _wfTryAddEdge(st.fromNode, portIn.dataset.node, st.when);
  }
}

function _wfConnectCancel(e) {
  const st = _wfConnectDrag;
  if (!st) return;
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  _wfConnectTeardown(st);
}

function _wfConnectTeardown(st) {
  document.querySelectorAll('.wfb-port-in.wfb-port-target').forEach(p => p.classList.remove('wfb-port-target'));
  window.removeEventListener('pointermove', _wfConnectMove);
  window.removeEventListener('pointerup', _wfConnectUp);
  window.removeEventListener('pointercancel', _wfConnectCancel);
  window.removeEventListener('blur', _wfConnectCancel);
  try { st.portEl.releasePointerCapture(st.pointerId); } catch (e) { /* already released */ }
  _wfConnectDrag = null;
  _wfRedrawEdges();
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _wfConnectDrag) _wfConnectCancel(null);
});

// ── Edge overlay: one absolutely-positioned SVG, cubic paths between the
//    real port DOM elements' current screen positions (R2-D7: "an
//    absolutely-positioned SVG overlay drawing cubic paths between port
//    coordinates"). Recomputed on every pan/zoom tick and node-drag frame,
//    plus whenever the node/edge list changes shape via `_wfRender`. ────────

function _wfRedrawEdges() {
  const svg = document.getElementById('wfb-canvas-svg');
  const vp = document.getElementById('wfb-canvas-viewport');
  const entry = _wfEntry();
  if (!svg || !vp || !entry || !entry._wf) return;
  const rect = vp.getBoundingClientRect();
  const ptOf = (el) => { const r = el.getBoundingClientRect(); return { x: r.left + r.width / 2 - rect.left, y: r.top + r.height / 2 - rect.top }; };
  const edges = entry._wf.def.edges || [];
  const bezier = (p1, p2) => {
    const dx = Math.max(50, Math.abs(p2.x - p1.x) * 0.5);
    return `M ${p1.x},${p1.y} C ${p1.x + dx},${p1.y} ${p2.x - dx},${p2.y} ${p2.x},${p2.y}`;
  };
  let html = '';
  for (const e of edges) {
    const fromEl = vp.querySelector(`.wfb-port-out[data-node="${_wfAttrEsc(e.from)}"][data-when="${_wfAttrEsc(e.when || '')}"]`);
    const toEl = vp.querySelector(`.wfb-port-in[data-node="${_wfAttrEsc(e.to)}"]`);
    if (!fromEl || !toEl) continue;
    const selected = _wfSelectedEdge && _wfSelectedEdge.from === e.from && _wfSelectedEdge.to === e.to && (_wfSelectedEdge.when || null) === (e.when || null);
    html += `<path class="wfb-edge-path${selected ? ' wfb-edge-selected' : ''}" d="${bezier(ptOf(fromEl), ptOf(toEl))}"
      onpointerdown="_wfEdgeClick(event,'${_wfJsStrEsc(e.from)}','${_wfJsStrEsc(e.to)}','${_wfJsStrEsc(e.when || '')}')"></path>`;
  }
  if (_wfConnectDrag) {
    const fromEl = vp.querySelector(`.wfb-port-out[data-node="${_wfAttrEsc(_wfConnectDrag.fromNode)}"][data-when="${_wfAttrEsc(_wfConnectDrag.when || '')}"]`);
    if (fromEl) {
      const p2 = { x: _wfConnectDrag.curX - rect.left, y: _wfConnectDrag.curY - rect.top };
      html += `<path class="wfb-edge-temp" d="${bezier(ptOf(fromEl), p2)}"></path>`;
    }
  }
  svg.innerHTML = html;
}

// ── The port `+` popover: "After <label>, run…" ─────────────────────────────
//
// The 80% path (UI brief §4): every output port carries a `+` that offers what
// can come next — an Action, an Approval gate, or a PERSON — and auto-places
// and wires the pick. Drawing an arrow by hand is for REWIRING, never a
// requirement, so a whole pipeline can be built without one.
//
// Lives on <body>, not inside the modal: the modal body is an overflow:auto
// scroller and a popover anchored to a port near its edge would be clipped by
// it. Position is therefore fixed/screen-space, clamped into the viewport.

let _wfPortPopover = null; // { fromNode, when, search, anchorX, anchorY }

function _wfPortPlusClick(e, fromNode, when) {
  e.stopPropagation();
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const already = _wfPortPopover
    && _wfPortPopover.fromNode === fromNode
    && (_wfPortPopover.when || '') === (when || '');
  const rect = e.currentTarget.getBoundingClientRect();
  _wfClosePortPopover();
  if (already) return; // a second click on the same + closes it
  _wfPortPopover = { fromNode, when: when || null, search: '', anchorX: rect.right, anchorY: rect.top };
  _wfRenderPortPopover();
}

function _wfPopoverPeopleHTML(st, search) {
  const people = _wfBenchFiltered(st, search);
  if (!people.length) return '<div class="wfb-popover-empty">No one matches.</div>';
  return people.map(b => `<div class="wfb-popover-person"
    onclick="_wfPopoverPickPerson('${_wfJsStrEsc(b.scope || 'global')}','${_wfJsStrEsc(b.name)}')">
    <span class="wfb-popover-avatar">${_wfAvatarHTML(b, 20)}</span>
    <span class="wfb-popover-person-name">${esc(b.display || b.name)}</span>
  </div>`).join('');
}

function _wfRenderPortPopover() {
  const p = _wfPortPopover; if (!p) return;
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const box = document.createElement('div');
  box.className = 'wfb-port-popover';
  box.id = 'wfb-port-popover';
  box.innerHTML = `
    <div class="wfb-popover-title">After <em>${esc(p.when || 'this step')}</em>, run&hellip;</div>
    <div class="wfb-popover-row" onclick="_wfPopoverPick('action')">
      <span class="wfb-popover-icon">&#9881;</span> Action <span class="wfb-popover-caret">&#9662;</span></div>
    <div class="wfb-popover-row" onclick="_wfPopoverPick('approval')">
      <span class="wfb-popover-icon">&#9995;</span> Approval gate</div>
    <div class="wfb-popover-people-title">People</div>
    <input class="wfb-popover-search" placeholder="Search bench&hellip;" value="${esc(p.search || '')}"
      oninput="_wfPopoverSearch(this.value)">
    <div class="wfb-popover-people">${_wfPopoverPeopleHTML(entry._wf, p.search)}</div>`;
  document.body.appendChild(box);
  // Clamp into the viewport AFTER layout, so the measured size is real.
  let left = p.anchorX + 8;
  let top = p.anchorY - 8;
  if (left + box.offsetWidth > window.innerWidth - 8) left = Math.max(8, window.innerWidth - box.offsetWidth - 8);
  if (top + box.offsetHeight > window.innerHeight - 8) top = Math.max(8, window.innerHeight - box.offsetHeight - 8);
  box.style.left = left + 'px';
  box.style.top = top + 'px';
  // Deferred: this handler is installed during a click that is still
  // propagating, and would otherwise close the popover it just opened.
  setTimeout(() => document.addEventListener('pointerdown', _wfPopoverOutsideDown, true), 0);
  const input = box.querySelector('.wfb-popover-search');
  if (input) input.focus();
}

function _wfClosePortPopover() {
  const el = document.getElementById('wfb-port-popover');
  if (el) el.remove();
  document.removeEventListener('pointerdown', _wfPopoverOutsideDown, true);
  _wfPortPopover = null;
}

function _wfPopoverOutsideDown(e) {
  const el = document.getElementById('wfb-port-popover');
  if (el && !el.contains(e.target)) _wfClosePortPopover();
}

// Only the people list is rebuilt, so the caret stays where it is as you type.
function _wfPopoverSearch(value) {
  if (!_wfPortPopover) return;
  _wfPortPopover.search = value;
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const box = document.getElementById('wfb-port-popover');
  const list = box && box.querySelector('.wfb-popover-people');
  if (list) list.innerHTML = _wfPopoverPeopleHTML(entry._wf, value);
}

function _wfPopoverPick(type) {
  const p = _wfPortPopover; if (!p) return;
  const entry = _wfEntry(); if (!entry) return;
  _wfClosePortPopover();
  _wfInsertAfter(entry, p.fromNode, p.when, type, null, null);
}

function _wfPopoverPickPerson(scope, name) {
  const p = _wfPortPopover; if (!p) return;
  const entry = _wfEntry(); if (!entry) return;
  _wfClosePortPopover();
  _wfInsertAfter(entry, p.fromNode, p.when, 'person', scope, name);
}

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _wfPortPopover) _wfClosePortPopover();
});

// ── Save ──────────────────────────────────────────────────────────────────────

async function _wfSave() {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  const def = st.def;
  if (!(def.name || '').trim()) { showToast('Name the workflow before saving.', 4000); return; }
  const nodes = def.nodes || [];
  if (!nodes.length) { showToast('Drag at least one block onto the canvas before saving.', 4000); return; }
  const names = nodes.map(n => n.name);
  const dupe = names.find((n, i) => names.indexOf(n) !== i);
  if (dupe) { showToast(`Duplicate step name "${dupe}" — names must be unique.`, 5000); return; }
  if (def.trigger && def.trigger.type === 'schedule' && st.linkedSchedule &&
      st.linkedSchedule.schedule_type === 'weekly' && !(st.linkedSchedule.days || []).length) {
    showToast('Pick at least one day for a weekly schedule.', 4000); return;
  }
  // Step 6, "the same validator that guards save": mirrors the checks
  // mc/workflows.py::validate_workflow already hard-enforces (missing
  // prompt/project, no options, cycles, broken slot scope) so a violation
  // reads inline on its card before ever reaching the network, not only as a
  // round-tripped 400. The server re-validates regardless (defence in depth).
  const problems = _wfValidateGraph(def);
  if (problems.length) {
    _wfApplyRunErrors(st, problems);
    showToast(problems[0].message, 5000);
    return;
  }

  st.saving = true; st.error = null;
  _wfRender();
  const body = { name: def.name, description: def.description, enabled: def.enabled !== false, trigger: def.trigger, nodes: def.nodes, edges: def.edges || [] };
  try {
    const url = st.workflowId ? `${API_BASE}/api/workflows/${st.workflowId}` : `${API_BASE}/api/workflows`;
    const method = st.workflowId ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) throw new Error(data.error || ('HTTP ' + res.status));
    st.workflowId = data.workflow.id;
    st.def = JSON.parse(JSON.stringify(data.workflow));
    await _wfSaveLinkedSchedule(st);
    st.saving = false;
    st.dirty = false;
    st.savedAt = Date.now();
    st.runErrors = null;
    showToast('Workflow saved', 3000);
    _wfRender();
    if (window.refreshWorkflowsList) window.refreshWorkflowsList();
  } catch (e) {
    st.saving = false;
    st.error = 'Save failed: ' + e.message;
    // mc/workflows.py's error strings quote the offending node name in single
    // quotes ("agent step 'draft' missing prompt"). Best-effort: if a node
    // name from this graph appears quoted in the message, also surface it
    // inline on that card (defence in depth for structural checks
    // _wfValidateGraph doesn't mirror, e.g. a hand-edited store file) --
    // _wfApplyRunErrors renders on its own, so skip the redundant call below.
    const names = (def.nodes || []).map(n => n.name);
    const hit = names.find(n => e.message.includes(`'${n}'`));
    if (hit) _wfApplyRunErrors(st, [{ node: hit, message: e.message }]);
    else _wfRender();
  }
}

// The authoring face of MC-871 Q4: "choosing 'on a schedule' creates or edits
// the linked schedule record through the EXISTING CRUD. One store, two
// views." Runs AFTER the workflow itself is saved, so a brand-new workflow
// has a real id to point workflow_id at. Reverting to Manual deletes the
// linked schedule outright -- a disabled-but-lingering row pointing at a
// workflow whose author no longer wants a cadence is exactly the dangling
// state the deleted-workflow guard on the OTHER side exists to avoid.
async function _wfSaveLinkedSchedule(st) {
  const isSchedule = st.def.trigger && st.def.trigger.type === 'schedule';
  if (!isSchedule) {
    if (st.linkedSchedule && st.linkedSchedule.id) {
      try { await fetch(`${API_BASE}/api/schedules/${encodeURIComponent(st.linkedSchedule.id)}`, { method: 'DELETE' }); } catch (e) {}
      st.linkedSchedule = null;
    }
    return;
  }
  const s = st.linkedSchedule || _wfDraftSchedule();
  const body = {
    workflow_id: st.workflowId,
    task: '', project_id: '',
    enabled: s.enabled !== false,
    schedule_type: s.schedule_type || 'daily',
    time: s.time, days: s.days, interval_minutes: s.interval_minutes,
    run_at: s.run_at, cron_expr: s.cron_expr,
  };
  try {
    const url = s.id ? `${API_BASE}/api/schedules/${encodeURIComponent(s.id)}` : `${API_BASE}/api/schedules`;
    const method = s.id ? 'PUT' : 'POST';
    const res = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    st.linkedSchedule = data;
  } catch (e) {
    showToast('Workflow saved, but the schedule failed: ' + e.message, 6000);
  }
}

// ── Step 6: dirty state, Run-now inline errors, saved stamp ──────────────────
//
// No autosave timer. The store (mc/workflows.py) has no updated_at/version
// field on a workflow record -- confirmed by reading create_workflow/
// update_workflow, neither stamps one -- so there is no cheap way to detect
// "another session already changed this since I loaded it" before an
// autosave overwrites it. The brief's own escape hatch: implement dirty-
// state + stamp + on-close warning and SAY the timer was left out rather
// than ship a save that can silently clobber a concurrent edit.

// Required fields per verb (mirrors validate_workflow's per-action checks,
// mc/workflows.py lines ~408-410 dispatch into per-verb requirements the
// route layer enforces on write). desk_harvest has none -- project_id is
// optional there ("blank = every project").
const _WF_ACTION_REQUIRED = {
  backlog_create: ['project_id', 'text'],
  backlog_patch: ['project_id', 'item_id'],
  desk_harvest: [],
};

// Client mirror of mc/workflows.py::validate_workflow, scoped to what the
// canvas can point a red outline at (brief §7: Run-now "selects and scrolls
// to the offending card"). NOT a full reimplementation -- name/duplicate-name/
// empty-graph/weekly-day checks stay in _wfSave as whole-form concerns with no
// single card to blame, and the server re-validates everything regardless on
// both save and run (R2-D3/D5 defence in depth). Reuses the exact toposort/
// dominator/broken-slot helpers the save guard already computes, so this can
// never disagree with what actually gets refused.
function _wfValidateGraph(def) {
  const nodes = def.nodes || [];
  const edges = def.edges || [];
  const names = nodes.map(n => n.name);
  const problems = []; // [{node, message}]
  const add = (name, message) => problems.push({ node: name, message });

  nodes.forEach((node) => {
    if (node.type === 'agent') {
      if (!node.project_id) add(node.name, 'Needs a project.');
      if (!(node.prompt || '').trim()) add(node.name, 'Needs a prompt.');
      const outcomes = node.outcomes || [];
      if (outcomes.includes('otherwise')) add(node.name, '"otherwise" is reserved, not a usable outcome label.');
      if (new Set(outcomes).size !== outcomes.length) add(node.name, 'Outcome labels must be unique.');
    } else if (node.type === 'approval') {
      const options = node.options || [];
      if (!options.length) add(node.name, 'Needs at least one option.');
      if (options.includes('otherwise')) add(node.name, '"otherwise" is reserved, not a usable option label.');
      if (new Set(options).size !== options.length) add(node.name, 'Option labels must be unique.');
    } else if (node.type === 'action') {
      const required = _WF_ACTION_REQUIRED[node.action] || [];
      const cfg = node.config || {};
      const missing = required.filter(k => !String(cfg[k] || '').trim());
      if (missing.length) add(node.name, `Needs ${missing.join(', ')}.`);
    }
  });

  const order = _wfToposort(names, edges);
  if (order.length !== names.length) {
    names.filter(n => !order.includes(n)).forEach(n => add(n, "Part of a cycle -- this step can never run."));
  }

  _wfFindBrokenSlotRefs(nodes, edges).forEach(({ step, ref }) => {
    add(step, `Uses {{steps.${ref}.*}}, but "${ref}" isn't guaranteed to have run first.`);
  });

  return problems;
}

// One message per node (first problem wins -- the card shows one banner, not
// a list); pans the canvas to the first offender so Save/Run-now never leaves
// the fix off-screen (brief §7: "never a modal alert").
function _wfApplyRunErrors(st, problems) {
  const map = {};
  problems.forEach((p) => { if (!map[p.node]) map[p.node] = p.message; });
  st.runErrors = map;
  st.scrollToNode = problems[0].node;
  _wfRender();
}

async function _wfRunNow() {
  const entry = _wfEntry(); if (!entry) return;
  _wfSyncDomToModel(entry);
  const st = entry._wf;
  if (!st.workflowId) { showToast('Save the workflow before running it.', 4000); return; }
  const problems = _wfValidateGraph(st.def);
  if (problems.length) {
    _wfApplyRunErrors(st, problems);
    showToast(problems[0].message, 5000);
    return;
  }
  // Re-render if this clears a PREVIOUS failed attempt's red outline -- a
  // validate-clean retry must not leave a stale error card on screen while
  // the fetch is in flight.
  if (st.runErrors) { st.runErrors = null; _wfRender(); }
  try {
    const res = await fetch(`${API_BASE}/api/workflows/${encodeURIComponent(st.workflowId)}/run`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      const msg = data.error || ('HTTP ' + res.status);
      const names = (st.def.nodes || []).map(n => n.name);
      const hit = names.find(n => msg.includes(`'${n}'`));
      if (hit) _wfApplyRunErrors(st, [{ node: hit, message: msg }]);
      showToast('Run failed: ' + msg, 6000);
      return;
    }
    showToast('Run started.', 3000);
  } catch (e) {
    showToast('Run failed: ' + e.message, 6000);
  }
}

// Marks the graph dirty the instant anything changes -- either the delegated
// #wfb-body input/change listener (typing, dropdowns) or an explicit call
// from a structural mutator whose action isn't a native input/change event
// (a button click, a pointer drag). Idempotent and cheap enough to call from
// both: it only touches the DOM directly (never a full _wfRender()) so a
// keystroke marking the graph dirty can never clobber the field being typed
// into, matching this file's existing FIELD SYNC discipline.
function _wfMarkDirty() {
  const entry = _wfEntry(); if (!entry || !entry._wf) return;
  const st = entry._wf;
  if (st.dirty) return;
  st.dirty = true;
  _wfPaintSaveStamp(st);
}

function _wfPaintSaveStamp(st) {
  const el = document.getElementById('wfb-save-stamp');
  if (!el) return;
  el.textContent = st.dirty ? 'Unsaved changes' : _wfRelativeSavedLabel(st.savedAt);
  el.classList.toggle('wfb-save-stamp-dirty', !!st.dirty);
}

function _wfRelativeSavedLabel(ts) {
  if (!ts) return '';
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 5) return 'saved just now';
  if (s < 60) return `saved ${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `saved ${m}m ago`;
  return `saved ${Math.round(m / 60)}h ago`;
}

// ── Interop: window accessors for onclick/onpointerdown targets + cross-
//    module entry points (static/js/scheduler.js:626-650 is the pattern this
//    file follows — every top-level declaration here is module-scoped).
window.openWorkflowBuilder = openWorkflowBuilder;
window._wfEntry = _wfEntry; // test/debug introspection of the mounted singleton
window._wfSetTriggerType = _wfSetTriggerType;
window._wfPaletteDown = _wfPaletteDown;
window._wfPaletteSearch = _wfPaletteSearch;
window._wfHireSomeone = _wfHireSomeone;
window._wfPortPlusClick = _wfPortPlusClick;
window._wfPopoverPick = _wfPopoverPick;
window._wfPopoverPickPerson = _wfPopoverPickPerson;
window._wfPopoverSearch = _wfPopoverSearch;
window._wfViewportDown = _wfViewportDown;
window._wfNodeDragDown = _wfNodeDragDown;
window._wfPortDown = _wfPortDown;
window._wfEdgeClick = _wfEdgeClick;
window._wfDeleteNode = _wfDeleteNode;
window._wfAddOutcome = _wfAddOutcome;
window._wfRemoveOutcome = _wfRemoveOutcome;
window._wfAddOption = _wfAddOption;
window._wfRemoveOption = _wfRemoveOption;
window._wfReloadCharacters = _wfReloadCharacters;
window._wfRerenderActionFields = _wfRerenderActionFields;
window._wfInsertSlot = _wfInsertSlot;
window._wfSave = _wfSave;
window._wfRunNow = _wfRunNow;
window._wfMarkDirty = _wfMarkDirty;
window._wfSetSchedType = _wfSetSchedType;
window._wfToggleSchedEnabled = _wfToggleSchedEnabled;
