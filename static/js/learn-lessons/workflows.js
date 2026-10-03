// ── Learn lesson: Workflows (workflows-v1) ───────────────────────────────────
// One lesson, one file: its copy, the elements each step points at, the
// evidence each step is verified against, and how its window is opened. The
// engine (static/js/learn.js) owns progress, cues and navigation and calls the
// hooks below; the contract is documented in learn-lessons/registry.js.
// Copy is verbatim from docs/TUTORIALS_SPEC.md; no em-dashes in any of it.

export const WORKFLOWS_ID = 'workflows-v1';

// ── What the canvas holds, and where its targets are ─────────────────────────
// The nodes and the trigger's wiring live in the builder's definition (`def`),
// not in the DOM, so step 2 and 3 evidence is read from that model through
// window._wfLearn. Only Save commits anything to the practice store.
function _wfDef(P) {
  const m = window._wfLearn && window._wfLearn.state();
  return m && m.projectId === P.PID && m.wf ? m.wf.def : null;
}
function _wfEntryNames(def) {
  return (def && def.trigger && Array.isArray(def.trigger.entry)) ? def.trigger.entry : [];
}
// The step the Connect action wires up: placed, but nothing runs into it yet.
function _wfLooseNode(def) {
  const entry = _wfEntryNames(def), edges = def.edges || [];
  return (def.nodes || []).find((n) => !entry.includes(n.name) && !edges.some((e) => e.to === n.name)) || null;
}
// The stand-in for the empty canvas area a dragged tool lands on: right of the
// Trigger when there is room, else below it. Re-measured every call.
function _canvasDropTarget(vp) {
  const here = () => {
    if (!vp.isConnected) return null;
    const v = vp.getBoundingClientRect();
    const trig = vp.querySelector('.wfb-trigger-box');
    const t = trig ? trig.getBoundingClientRect() : null;
    const w = 150, h = 90;
    let x = t ? t.right + 70 : v.left + v.width / 2 - w / 2;
    let y = t ? t.top : v.top + v.height / 2 - h / 2;
    if (x + w > v.right - 8) { x = t ? t.left : v.left + 24; y = (t ? t.bottom : v.top) + 60; }
    x = Math.max(v.left + 8, Math.min(x, v.right - w - 8));
    y = Math.max(v.top + 8, Math.min(y, v.bottom - h - 8));
    if (v.width < w + 16 || v.height < h + 16) return null;
    return { left: x, top: y, right: x + w, bottom: y + h, width: w, height: h };
  };
  const none = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
  return {
    virtual: true, vp,
    get isConnected() { return !!here(); },
    getBoundingClientRect() { return here() || none; },
    get point() { const r = here(); return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null; },
  };
}

// ── Hooks ────────────────────────────────────────────────────────────────────
// The window this lesson lives in: the practice project's modal, on its
// Workflows tab.
function surfaceFront({ P, dom }) { return dom.modalFront(P.PID); }

// Opening it is navigation, never the taught action (that is pressing
// "+ New Workflow").
async function openSurface({ P }) {
  modalActiveTab[P.PID] = 'workflows';
  if (typeof window.openProjectModal === 'function') window.openProjectModal(P.PID);
  if (!document.getElementById('wfb-clayrune-section-' + P.PID) && typeof window.switchModalTab === 'function') {
    window.switchModalTab(P.PID, 'workflows');
  }
  // The section exists as soon as the tab renders, but its tabs row and canvas
  // host are built when the tab's workflow list lands (`data-wf-built`). A canvas
  // mounted before that is wiped by the build.
  const built = () => { const s = document.getElementById('wfb-clayrune-section-' + P.PID); return !!s && s.dataset.wfBuilt === '1'; };
  for (let i = 0; i < 30 && !built(); i++) await new Promise((r) => setTimeout(r, 100));
}

function resolveStep({ P, step: st, dom }) {
  const modal = typeof openModals !== 'undefined' ? openModals.get(P.PID) : null;
  if (!modal) return { error: 'missing' };
  const root = modal.element;
  if (st.id === 'open-canvas') {
    const btn = dom.only(`#wfb-clayrune-section-${P.PID} .wfb-tab-new`);
    if (!btn) return { error: 'missing' };
    if (btn.dup) return { error: 'duplicate' };
    return { el: btn, cue: btn };
  }
  const vp = dom.only('#wfb-canvas-viewport');
  if (!vp) return { error: 'missing' };
  if (vp.dup) return { error: 'duplicate' };
  if (!root.contains(vp)) return { error: 'missing' };
  if (st.id === 'place-node') {
    const tool = dom.only('.wfb-toolbar-tool[data-wf-tool="approval"]');
    if (!tool) return { error: 'missing' };
    if (tool.dup) return { error: 'duplicate' };
    return { el: tool, cue: tool, dest: _canvasDropTarget(vp) };
  }
  if (st.id === 'connect') {
    const port = dom.only('.wfb-trigger-box .wfb-port-out');
    if (!port) return { error: 'missing' };
    if (port.dup) return { error: 'duplicate' };
    const def = _wfDef(P), node = def && _wfLooseNode(def);
    const card = node ? dom.only(`.wfb-node[data-name="${CSS.escape(node.name)}"]`) : null;
    if (!card) return { error: 'missing' };
    if (card.dup) return { error: 'duplicate' };
    // The wire drops anywhere on the card; the cue lands on its in-port so the
    // endpoint is plain.
    return { el: port, cue: port, dest: card.querySelector('.wfb-port-in') || card, instant: true };
  }
  // save: the name first, then Create.
  const name = dom.only('#wfb-name');
  const btn = dom.only('.wfb-toolbar .btn-sched-save');
  if (!name || !btn) return { error: 'missing' };
  if (name.dup || btn.dup) return { error: 'duplicate' };
  const target = name.value.trim() ? btn : name;
  return { el: target, cue: target };
}

// Preparation may navigate and load fixtures. It never performs the action.
async function prepare(ctx) {
  const { P, step: st } = ctx;
  if (st.id === 'open-canvas') {
    // The canvas must be opened by the user DURING this step, so a leftover
    // practice modal or canvas from an earlier pass is cleared first.
    if (typeof openModals !== 'undefined' && openModals.has(P.PID) && typeof closeModalById === 'function') closeModalById(P.PID);
    if (window._wfLearn) window._wfLearn.discard(P.PID);
  }
  await openSurface(ctx);
  // Resuming after a reload: the canvas is gone with the page. An empty one is
  // the correct start for the steps that follow, so restore it (the user has
  // already done "open the canvas"; nothing here places or connects anything).
  if (st.id !== 'open-canvas' && !_wfDef(P) && typeof window.openWorkflowBuilder === 'function') {
    await window.openWorkflowBuilder(null, P.PID, 'a new workflow');
  }
}

// What the canvas held when the step began, so "placed" and "connected" mean
// "more than before". Runs once the step is prepared and still valid.
function stepReady({ S, P }) {
  const def = _wfDef(P);
  S.baseNodes = def ? (def.nodes || []).length : 0;
  S.baseEntry = def ? _wfEntryNames(def).length : 0;
}

// Which earlier step a step needs finished, when the canvas it depends on is
// gone (a reload empties it). -1 = nothing to redo.
function prereqRewind({ P, step }) {
  const id = step.id;
  if (id === 'open-canvas') return -1;
  const def = _wfDef(P);
  if (!def) return 0;
  if (id === 'place-node') return -1;
  if (!(def.nodes || []).length) return 1;
  if (id === 'save') {
    const names = def.nodes.map((n) => n.name), edges = def.edges || [];
    return _wfEntryNames(def).some((n) => names.includes(n) && !edges.some((e) => e.to === n)) ? -1 : 2;
  }
  return -1;
}

// Evidence. Steps 1 to 3 read the builder's own model (what the canvas holds),
// step 4 reads the practice store (what Save committed). Nothing here looks at a
// click or at what is painted. The engine has already checked that practice is
// active and belongs to this run.
function verify({ S, P, step: st, dom }) {
  const def = _wfDef(P);
  if (st.id === 'open-canvas') {
    const vp = dom.q(`#modal-layer [data-modal-id="${P.PID}"] #wfb-canvas-viewport`);
    return def && vp && dom.measurable(vp) ? { ok: true, evidence: { project: P.PID } } : null;
  }
  if (st.id === 'place-node') {
    return def && (def.nodes || []).length > S.baseNodes
      ? { ok: true, evidence: { nodes: def.nodes.length } } : null;
  }
  if (st.id === 'connect') {
    if (!def) return null;
    const names = (def.nodes || []).map((n) => n.name), edges = def.edges || [];
    const wired = _wfEntryNames(def).filter((n) => names.includes(n) && !edges.some((e) => e.to === n));
    return wired.length > S.baseEntry ? { ok: true, evidence: { entry: wired } } : null;
  }
  const hit = P.workflows(S.runId).find((w) => w.run_id === S.runId && Date.parse(w.saved_at) >= S.stepStartedMs - 1
    && (w.nodes || []).length > 0 && ((w.trigger && w.trigger.entry) || []).some((n) => (w.nodes || []).some((x) => x.name === n)));
  return hit ? { ok: true, evidence: { workflow: hit.id, seq: hit.seq } } : null;
}

// The explicit controls for the lesson's two pointer gestures: each makes the
// same real builder call the gesture ends in (a toolbar tap places the block at
// a free spot; dragging from the Trigger's dot onto a card calls _wfMakeRoot).
// They act on the canvas; the step still advances only when the verify tick
// reads the result from the model.
function fallback({ P, step: st }) {
  if (st.id === 'place-node' && typeof window._wfPlaceToolAtFreeSpot === 'function') {
    window._wfPlaceToolAtFreeSpot('approval');
  } else if (st.id === 'connect' && typeof window._wfMakeRoot === 'function') {
    const def = _wfDef(P), node = def && _wfLooseNode(def);
    if (node) window._wfMakeRoot(node.name);
  }
}

// Runs on every leave, whichever lesson was active: a practice canvas must not
// outlive its run.
function cleanup({ P }) {
  if (window._wfLearn) window._wfLearn.discard(P.PID);
}

// A lesson lives in one window (the practice project's Workflows tab); the
// bubble offers `backLabel` when the user leaves it on a step marked
// `needsSurface`. A step's `fallback` is the explicit control for a gesture that
// needs a pointer: it makes the same real call the gesture ends in.
export const workflowsLesson = {
  id: WORKFLOWS_ID, version: 1,
  title: 'Workflows',
  subtitle: 'Place a step, connect the trigger, save it.',
  meta: '4 actions · Practice only',
  chip: 'Start Workflows practice',
  offer: 'Build a workflow in four actions. Want to try?',
  done: 'You placed a step, connected the trigger to it, and saved the workflow. Saving did not run it or schedule it. Your real workflows are untouched.',
  backLabel: 'Back to canvas', returnLabel: 'Return to canvas', surfaceKey: 'canvas',
  leftMsg: 'You left the canvas. Your progress is saved.',
  rewindMsg: 'The canvas was reset, so this goes back a step. Only practice state resets.',
  steps: [
    {
      id: 'open-canvas', short: 'Open the canvas',
      copy: 'In the Workflows tab of Learn practice, press + New Workflow to open a blank canvas. A workflow is steps joined by lines.',
      ack: 'That’s the canvas. Every workflow starts at a trigger.',
      nudge: true, needsSurface: true,
    },
    {
      id: 'place-node', short: 'Place a step',
      copy: 'Drag Approval gate from the toolbar onto the canvas. It is a step where a person decides. A tap works too.',
      ack: 'That’s a step. Each step does one job.',
      nudge: true, needsSurface: true,
      fallback: { label: 'Place it for me' },
    },
    {
      id: 'connect', short: 'Connect the trigger',
      copy: 'Drag the dot on the Trigger onto your step. The trigger starts the workflow, and this line says what runs first.',
      ack: 'Connected. When the trigger fires, this step runs first.',
      nudge: true, needsSurface: true,
      fallback: { label: 'Connect it for me' },
    },
    {
      id: 'save', short: 'Save it',
      copy: 'Name your workflow, then press Create. Saving keeps it. It does not run it or put it on a schedule.',
      ack: '',
      nudge: true, needsSurface: true,
    },
  ],
  surfaceFront, openSurface, resolveStep, prepare, stepReady, prereqRewind, verify, fallback, cleanup,
};
