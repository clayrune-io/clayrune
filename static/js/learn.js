// ── Learn: lesson engine + hub ───────────────────────────────────────────────
// docs/TUTORIALS_SPEC.md (Dave's DECISIONS win over the body). A lesson engine
// BESIDE walkthrough.js: it shares the visual language of the tour but none of
// its state (no step index, no demo factories, no `walkthrough_done` flag), so
// the tour and first-run setup are untouched.
//
// What makes this different from the tour: a step advances only on EVIDENCE.
//   - the user did the action while the step was active (a notify() hook that
//     floor.js calls, or a committed practice-store write), AND
//   - the authoritative state says so: the rendered practice transcript, the
//     opened Bench card, or the practice roster in learn-practice.js.
// A click, a timer, a Next button or an optimistic DOM change proves nothing.
//
// Practice is browser-only (learn-practice.js): while a run is active, fetch is
// routed to an in-tab store, so nothing practice-related exists on the server.
// Progress lives in localStorage under `learn.*`.

const FLOOR_ID = 'floor-v1';
const WORKFLOWS_ID = 'workflows-v1';
const LESSON_ID = FLOOR_ID;     // the lesson the Floor's own offer and header link to
const FIXTURE_VERSION = 1;
const LS_PROGRESS = 'learn.progress';
const LS_EVENTS = 'learn.events';
const LS_QUIET = 'learn.quiet';
const HUB_MODAL = '__learn';
const TICK_MS = 250;
const ACK_MS = 1600;            // how long an acknowledgment is readable before the next task
const UNRESOLVED_PAUSE_MS = 5000;
const MOBILE_MAX = 960;

const _q = (sel, root) => (root || document).querySelector(sel);
const _qa = (sel, root) => Array.from((root || document).querySelectorAll(sel));
const _esc = (s) => (typeof window.esc === 'function'
  ? window.esc(s)
  : String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])));
const _isMobile = () => window.innerWidth <= MOBILE_MAX;

// ── Lesson registry ──────────────────────────────────────────────────────────
// Declared as data. Copy is verbatim from the spec; no em-dashes in any of it.
// `resolve` returns the one element a step points at (or {error}); `verify`
// returns {ok, evidence} from authoritative state; `prepare` may navigate but
// never performs the taught action.
// A lesson lives in one window (the Floor, or the practice project's Workflows
// tab); the bubble offers `backLabel` when the user leaves it on a step marked
// `needsSurface`. A step's `fallback` is the explicit control for a gesture that
// needs a pointer (the Floor's hire has its own, `hire`): it makes the same real
// call the gesture ends in.
const LESSONS = {
  [FLOOR_ID]: {
    id: FLOOR_ID, version: 1,
    title: 'The Floor',
    subtitle: 'Find a figure, open a chat, hire a type.',
    meta: '3 actions · Practice only',
    chip: 'Start Floor practice',
    offer: 'Meet the Floor in three actions. Want to try?',
    done: 'You found a figure, opened its chat, and hired a type. Your real projects are untouched.',
    backLabel: 'Back to Floor', returnLabel: 'Return to Floor', surfaceKey: 'floor',
    leftMsg: 'You left the Floor. Your progress is saved.',
    steps: [
      {
        id: 'open-chat', short: 'Open Pip’s chat',
        copy: 'Find Pip in Learn practice. Open this chat to see what this figure is doing.',
        ack: 'That’s Pip’s session. A figure opens a conversation.',
        nudge: true,
      },
      {
        id: 'open-type', short: 'Open the Guide card',
        copy: 'Back on the Floor, find Guide on the Bench. Open its card. Types are who you can hire.',
        ack: 'Guide is a type. Now give it a project.',
        emphasis: true, needsSurface: true,
      },
      {
        id: 'hire', short: 'Hire Guide',
        copy: 'Give Guide a place to work. Drag it onto Learn practice, or use Hire to practice. Hiring adds the type. It does not start a task.',
        ack: '',
        nudge: true, hire: true, needsSurface: true,
      },
    ],
  },
  [WORKFLOWS_ID]: {
    id: WORKFLOWS_ID, version: 1,
    title: 'Workflows',
    subtitle: 'Place a step, connect the trigger, save it.',
    meta: '4 actions · Practice only',
    chip: 'Start Workflows practice',
    offer: 'Build a workflow in four actions. Want to try?',
    done: 'You placed a step, connected the trigger to it, and saved the workflow. Saving did not run it or schedule it. Your real workflows are untouched.',
    backLabel: 'Back to canvas', returnLabel: 'Return to canvas', surfaceKey: 'canvas',
    leftMsg: 'You left the canvas. Your progress is saved.',
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
  },
};
const LESSON_ORDER = [FLOOR_ID, WORKFLOWS_ID];

// ── Progress (localStorage `learn.*`) ────────────────────────────────────────
function _readProgress() {
  try {
    const s = JSON.parse(localStorage.getItem(LS_PROGRESS) || 'null');
    if (s && typeof s === 'object' && s.lessons && typeof s.lessons === 'object') return s;
  } catch (e) { /* unreadable progress reads as none */ }
  return { v: 1, lessons: {} };
}
function _writeProgress(p) {
  try { localStorage.setItem(LS_PROGRESS, JSON.stringify(p)); return true; }
  catch (e) { console.warn('[learn] could not save progress:', e); return false; }
}
function _rec(id) { return _readProgress().lessons[id] || null; }
// Read-modify-write one lesson record. Returns the saved record, or null if the
// write failed (the caller keeps the step and offers Retry).
function _patch(id, fn) {
  const p = _readProgress();
  const cur = p.lessons[id] || { version: LESSONS[id].version, fixture: FIXTURE_VERSION, runId: '',
    status: 'idle', step: 0, verified: [], stepStartedAt: 0, offered: false, completions: 0, completedAt: 0 };
  fn(cur);
  p.lessons[id] = cur;
  return _writeProgress(p) ? cur : null;
}
function _quiet() {
  try { return localStorage.getItem(LS_QUIET) === '1'; } catch (e) { return false; }
}
// Local-only telemetry (spec "Acceptance and pilot review"): step starts,
// verified completions, pauses, target failures, hint use. No chat contents.
function _ev(event, detail) {
  try {
    const list = JSON.parse(localStorage.getItem(LS_EVENTS) || '[]');
    list.push({ t: Date.now(), event, lesson: S ? S.lessonId : FLOOR_ID, ...(detail || {}) });
    localStorage.setItem(LS_EVENTS, JSON.stringify(list.slice(-200)));
  } catch (e) { /* telemetry is best-effort */ }
}
function _newRunId() { return 'run-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

// ── Engine state ─────────────────────────────────────────────────────────────
// phase: idle | preparing | active | verifying | acknowledged | paused |
//        restart | unavailable | completed
let S = null;      // the live run, or null when no lesson is active
let _tickTimer = null;
let _ackTimer = null;
let _gen = 0;      // bumped on every enter/leave; async work from an old run drops itself
let _priorFocus = null;
let _root = null;

function _lesson() { return S ? LESSONS[S.lessonId] : null; }
function _step() { return S ? _lesson().steps[S.idx] : null; }
function _reduced() {
  return _quiet() || (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches);
}

// ── DOM resolution (identity + action hooks, never text or child order) ──────
function _only(sel) {
  const all = _qa(sel);
  return all.length === 1 ? all[0] : (all.length ? { dup: all.length } : null);
}
function _measurable(el) {
  if (!el || !el.isConnected) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 4 || r.height < 4) return false;
  const cs = getComputedStyle(el);
  if (cs.display === 'none' || cs.visibility === 'hidden') return false;
  return r.bottom > 0 && r.right > 0 && r.top < window.innerHeight && r.left < window.innerWidth;
}
// The element at the target's centre must be the target or inside it. Our own
// overlay never intercepts (pointer-events: none) except the bubble, which is
// positioned off the target.
function _unobscured(el) {
  const r = el.getBoundingClientRect();
  const x = Math.min(window.innerWidth - 1, Math.max(0, r.left + r.width / 2));
  const y = Math.min(window.innerHeight - 1, Math.max(0, r.top + r.height / 2));
  const hit = document.elementFromPoint(x, y);
  return !!hit && (el.contains(hit) || (hit.closest && hit.closest('.lrn-bubble') === null && hit.contains(el)));
}
// The part of `tile` that is actually on screen and not under another window:
// sample it on an 8px grid and keep the cells that hit-test to the tile. Returns
// the bounding box of those cells plus the hit cell nearest its centre (the
// drop point), or null when less than EXPOSED_MIN x EXPOSED_MIN is exposed.
const EXPOSED_MIN = 40, EXPOSED_STEP = 8;
function _exposedOf(tile) {
  if (!tile || !tile.isConnected) return null;
  const r = tile.getBoundingClientRect();
  const x0 = Math.max(0, r.left), y0 = Math.max(0, r.top);
  const x1 = Math.min(window.innerWidth, r.right), y1 = Math.min(window.innerHeight, r.bottom);
  if (x1 - x0 < EXPOSED_MIN || y1 - y0 < EXPOSED_MIN) return null;
  const hits = [];
  let L = Infinity, T = Infinity, R = -Infinity, B = -Infinity;
  const h = EXPOSED_STEP / 2;
  for (let y = y0 + h; y < y1; y += EXPOSED_STEP) {
    for (let x = x0 + h; x < x1; x += EXPOSED_STEP) {
      const hit = document.elementFromPoint(x, y);
      if (!hit || !tile.contains(hit)) continue;
      hits.push([x, y]);
      L = Math.min(L, x - h); T = Math.min(T, y - h); R = Math.max(R, x + h); B = Math.max(B, y + h);
    }
  }
  // Area as well as extent, so a thin L-shaped strip cannot pass for a drop zone.
  if (R - L < EXPOSED_MIN || B - T < EXPOSED_MIN || hits.length * EXPOSED_STEP * EXPOSED_STEP < EXPOSED_MIN * EXPOSED_MIN) return null;
  const cx = (L + R) / 2, cy = (T + B) / 2;
  let best = hits[0], bd = Infinity;
  for (const p of hits) { const d = Math.hypot(p[0] - cx, p[1] - cy); if (d < bd) { bd = d; best = p; } }
  return { rect: { left: L, top: T, right: R, bottom: B, width: R - L, height: B - T }, x: best[0], y: best[1] };
}
// A stand-in element for the exposed part of a tile, so the arrow, the bubble
// placement and the hand can treat it like any other destination. Re-sampled at
// most every 250ms; the caller hands over a fresh one on every lesson tick.
function _exposedTarget(tile, first) {
  let snap = first, at = performance.now();
  const get = () => {
    const n = performance.now();
    if (n - at > 250) { snap = _exposedOf(tile); at = n; }
    return snap;
  };
  const none = { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
  return {
    virtual: true, tile,
    get isConnected() { return tile.isConnected && !!get(); },
    getBoundingClientRect() { const sn = get(); return sn ? sn.rect : none; },
    get point() { const sn = get(); return sn ? { x: sn.x, y: sn.y } : null; },
  };
}
function _modalFront(id) {
  const e = (typeof openModals !== 'undefined') ? openModals.get(id) : null;
  return !!(e && !e.minimized && e.element.classList.contains('focused'));
}
function _floorFront() { return _modalFront('__floor'); }
// The window the active lesson lives in: the Floor, or the practice project's
// modal (its Workflows tab) for the Workflows lesson.
function _surfaceFront() {
  return S && S.lessonId === WORKFLOWS_ID ? _modalFront(window.LearnPractice.PID) : _floorFront();
}

function _resolveStep() {
  return S.lessonId === WORKFLOWS_ID ? _resolveWorkflowsStep() : _resolveFloorStep();
}

function _resolveFloorStep() {
  const P = window.LearnPractice;
  const st = _step();
  if (st.id === 'open-chat') {
    const fig = _only('#floor-body .fl-room .fl-fig');
    if (!fig) return { error: 'missing' };
    if (fig.dup) return { error: 'duplicate' };
    if (fig.dataset.flSession !== P.SID) return { error: 'missing' };
    return { el: fig, cue: fig.querySelector('.fl-cta') || fig };
  }
  if (st.id === 'open-type') {
    const main = _only(`#floor-body .fl-bench-card[data-fl-type="${P.GUIDE_REF}"] .fl-bench-main`);
    if (!main) return { error: 'missing' };
    if (main.dup) return { error: 'duplicate' };
    return { el: main.closest('.fl-bench-card') || main, cue: main };
  }
  const card = _only(`#floor-body .fl-bench-card.fl-draggable[data-fl-type="${P.GUIDE_REF}"]`);
  if (!card) return { error: 'missing' };
  if (card.dup) return { error: 'duplicate' };
  const destSel = _isMobile()
    ? `#projects-col .mc-chat-row[data-id="${P.PID}"]`
    : `#projects-col .card[data-id="${P.PID}"]`;
  const tile = _q(destSel);
  // Desktop: the tile is usually half under the Floor window; the drag lands on the
  // part that is showing. Phone: the row is behind the full-screen Floor, so null.
  const ex = tile && _measurable(tile) ? _exposedOf(tile) : null;
  return { el: card, cue: card, dest: ex ? _exposedTarget(tile, ex) : null };
}

// ── Workflows lesson: what the canvas holds, and where its targets are ───────
// The nodes and the trigger's wiring live in the builder's definition (`def`),
// not in the DOM, so step 2 and 3 evidence is read from that model through
// window._wfLearn. Only Save commits anything to the practice store.
function _wfDef() {
  const m = window._wfLearn && window._wfLearn.state();
  return m && m.projectId === window.LearnPractice.PID && m.wf ? m.wf.def : null;
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

function _resolveWorkflowsStep() {
  const P = window.LearnPractice;
  const st = _step();
  const modal = typeof openModals !== 'undefined' ? openModals.get(P.PID) : null;
  if (!modal) return { error: 'missing' };
  const root = modal.element;
  if (st.id === 'open-canvas') {
    const btn = _only(`#wfb-clayrune-section-${P.PID} .wfb-tab-new`);
    if (!btn) return { error: 'missing' };
    if (btn.dup) return { error: 'duplicate' };
    return { el: btn, cue: btn };
  }
  const vp = _only('#wfb-canvas-viewport');
  if (!vp) return { error: 'missing' };
  if (vp.dup) return { error: 'duplicate' };
  if (!root.contains(vp)) return { error: 'missing' };
  if (st.id === 'place-node') {
    const tool = _only('.wfb-toolbar-tool[data-wf-tool="approval"]');
    if (!tool) return { error: 'missing' };
    if (tool.dup) return { error: 'duplicate' };
    return { el: tool, cue: tool, dest: _canvasDropTarget(vp) };
  }
  if (st.id === 'connect') {
    const port = _only('.wfb-trigger-box .wfb-port-out');
    if (!port) return { error: 'missing' };
    if (port.dup) return { error: 'duplicate' };
    const def = _wfDef(), node = def && _wfLooseNode(def);
    const card = node ? _only(`.wfb-node[data-name="${CSS.escape(node.name)}"]`) : null;
    if (!card) return { error: 'missing' };
    if (card.dup) return { error: 'duplicate' };
    // The wire drops anywhere on the card; the cue lands on its in-port so the
    // endpoint is plain.
    return { el: port, cue: port, dest: card.querySelector('.wfb-port-in') || card, instant: true };
  }
  // save: the name first, then Create.
  const name = _only('#wfb-name');
  const btn = _only('.wfb-toolbar .btn-sched-save');
  if (!name || !btn) return { error: 'missing' };
  if (name.dup || btn.dup) return { error: 'duplicate' };
  const target = name.value.trim() ? btn : name;
  return { el: target, cue: target };
}

// ── Bubble, outline, arrow ───────────────────────────────────────────────────
function _ensureRoot() {
  if (_root && _root.isConnected) return _root;
  _root = document.createElement('div');
  _root.id = 'lrn-root';
  _root.className = 'lrn-root';
  _root.innerHTML = `
    <div class="lrn-banner" id="lrn-banner">Practice only. Nothing here touches your real projects.</div>
    <div class="lrn-outline" id="lrn-outline" hidden></div>
    <div class="lrn-arrow" id="lrn-arrow" hidden aria-hidden="true"><div class="lrn-arrow-in"><svg viewBox="0 0 24 24" width="28" height="28"><path d="M3 12h15M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg></div></div>
    <div class="lrn-hand-drop" id="lrn-hand-drop" hidden aria-hidden="true"></div>
    <div class="lrn-hand-ghost" id="lrn-hand-ghost" hidden aria-hidden="true"></div>
    <div class="lrn-hand-ring" id="lrn-hand-ripple" hidden aria-hidden="true"></div>
    <div class="lrn-hand-ring lrn-hand-hold" id="lrn-hand-hold" hidden aria-hidden="true"></div>
    <div class="lrn-hand" id="lrn-hand" hidden aria-hidden="true">${HAND_SVG}</div>
    <div class="lrn-bubble" id="lrn-bubble" role="dialog" aria-label="Claydo practice" tabindex="-1"></div>
    <div class="lrn-dots" id="lrn-dots" hidden aria-hidden="true"></div>`;
  document.body.appendChild(_root);
  _applyQuietClass();
  return _root;
}
function _applyQuietClass() {
  if (_root) _root.classList.toggle('lrn-quiet', _reduced());
}

function _bubbleHTML() {
  const L = _lesson(), st = _step(), rec = _rec(S.lessonId) || {};
  const done = (rec.verified || []).filter((v) => v.run === S.runId).length;
  const total = L.steps.length;
  const ph = S.phase;
  let copy = '', ack = false, actions = '', err = S.error || '';
  if (ph === 'completed') {
    copy = L.done; ack = true;
    actions = `<button class="lrn-btn" data-lrn="return-${L.surfaceKey}">${_esc(L.returnLabel)}</button>
      <button class="lrn-btn" data-lrn="replay">Replay</button>
      <button class="lrn-btn" data-lrn="hub">Learn</button>
      <button class="lrn-btn lrn-foot-leave" data-lrn="leave">Close</button>`;
  } else if (ph === 'paused') {
    copy = S.pauseMsg || 'Paused. Your progress is saved.';
    actions = `<button class="lrn-btn lrn-primary" data-lrn="resume">Resume</button>
      <button class="lrn-btn" data-lrn="hub">Learn</button>`;
  } else if (ph === 'restart') {
    copy = S.restartMsg || 'This practice needs a restart. Only practice state resets. Your real projects are untouched.';
    actions = `<button class="lrn-btn lrn-primary" data-lrn="restart">Restart practice</button>`;
  } else if (ph === 'unavailable') {
    copy = 'This lesson needs an update. Your progress is saved.';
    actions = `<button class="lrn-btn lrn-primary" data-lrn="retry-target">Retry</button>
      <button class="lrn-btn" data-lrn="hub">Learn</button>`;
  } else if (ph === 'acknowledged' || ph === 'verifying') {
    copy = st.ack || ''; ack = ph === 'acknowledged';
  } else {
    copy = st.copy;
    if (S.notice) copy = `${S.notice}\n${copy}`;
    if (S.hint) copy += `\n${S.hintLabel}`;
    if (!S.surfaceFrontNow && st.needsSurface) {
      actions += `<button class="lrn-btn lrn-primary" data-lrn="back-${L.surfaceKey}">${_esc(L.backLabel)}</button>`;
    }
    if (S.retrySave) {
      actions += `<button class="lrn-btn lrn-primary" data-lrn="retry-save">Retry</button>`;
    } else if (st.hire && S.surfaceFrontNow) {
      actions += `<button class="lrn-btn lrn-primary" data-lrn="hire" ${S.hirePending ? 'disabled' : ''}>${S.hireFailed ? 'Retry hire' : 'Hire to practice'}</button>`;
    } else if (st.fallback && S.surfaceFrontNow) {
      // The explicit control for a gesture that needs a pointer: the same real
      // builder call the drag ends in, reachable from the keyboard.
      actions += `<button class="lrn-btn lrn-primary" data-lrn="fallback">${_esc(st.fallback.label)}</button>`;
    }
  }
  const foot = (ph === 'completed' || ph === 'paused' || ph === 'restart' || ph === 'unavailable') ? '' : `
    <div class="lrn-foot">
      <button class="lrn-link" data-lrn="pause">Pause</button>
      <button class="lrn-link" data-lrn="hint">Hint</button>
      <button class="lrn-link lrn-foot-leave" data-lrn="leave">Leave practice</button>
      <button class="lrn-link lrn-quiet-toggle" data-lrn="quiet" aria-pressed="${_quiet() ? 'true' : 'false'}">Quiet effects</button>
    </div>`;
  const leaveOnly = (ph === 'paused' || ph === 'restart' || ph === 'unavailable')
    ? `<div class="lrn-foot lrn-foot-leave"><button class="lrn-link" data-lrn="leave">Leave practice</button></div>` : '';
  const head = ph === 'completed'
    ? `<span class="lrn-seal" aria-hidden="true">✓</span>`
    : '';
  return `
    <div class="lrn-head">
      <img class="lrn-claydo" src="/assets/claydo-idle.webp" alt="" draggable="false">
      <span class="lrn-title">${_esc(L.title)}</span>
      <span class="lrn-progress" role="status" aria-live="polite">${done}/${total}</span>
      <button class="lrn-leave" data-lrn="leave" aria-label="Leave practice">Leave</button>
      <button class="lrn-collapse" data-lrn="collapse" aria-label="${S.collapsed ? 'Expand' : 'Collapse'} the practice bubble" aria-expanded="${S.collapsed ? 'false' : 'true'}">${S.collapsed ? '▴' : '▾'}</button>
    </div>
    <div class="lrn-task">${ph === 'completed' ? 'Practice complete' : _esc('Step ' + (S.idx + 1) + ' of ' + total + ': ' + st.short)}</div>
    <div class="lrn-body">
      ${head}
      <div class="lrn-copy${ack ? ' lrn-ack' : ''}">${S.tickNow ? '<span class="lrn-tick" aria-hidden="true">✓</span> ' : ''}${_esc(copy).replace(/\n/g, '<br>')}</div>
      ${err ? `<div class="lrn-err" role="alert">${_esc(err)}</div>` : ''}
      ${actions ? `<div class="lrn-actions">${actions}</div>` : ''}
      ${foot}${leaveOnly}
    </div>`;
}

let _bubbleSig = '';
function _renderBubble(force) {
  if (!S) return;
  _ensureRoot();
  const b = _q('#lrn-bubble', _root);
  const sig = [S.phase, S.idx, S.hint, S.error, S.hirePending, S.hireFailed, S.collapsed,
    S.surfaceFrontNow, S.tickNow, _quiet(), S.pauseMsg, S.restartMsg, S.notice].join('|');
  if (!force && sig === _bubbleSig) return;
  _bubbleSig = sig;
  b.innerHTML = _bubbleHTML();
  b.dataset.phase = S.phase;
  b.dataset.step = String(S.idx);
  b.classList.toggle('lrn-collapsed', !!S.collapsed);
  b.classList.toggle('lrn-mobile', _isMobile());
}

function _placeBubble(rects) {
  const b = _q('#lrn-bubble', _root);
  if (!b) return;
  if (_isMobile()) {
    // Bottom-docked above the bottom tab bar and safe-area inset; the visual
    // viewport lifts it above an open keyboard.
    let lift = 0;
    const tab = _q('#bottom-tab-bar');
    if (tab && getComputedStyle(tab).display !== 'none') {
      const tr = tab.getBoundingClientRect();
      if (tr.height > 0 && tr.top < window.innerHeight) lift = Math.max(lift, window.innerHeight - tr.top);
    }
    const vv = window.visualViewport;
    if (vv) lift = Math.max(lift, window.innerHeight - (vv.height + vv.offsetTop));
    b.style.left = '8px'; b.style.right = '8px'; b.style.top = 'auto'; b.style.width = 'auto';
    b.style.bottom = `calc(${Math.round(lift)}px + env(safe-area-inset-bottom, 0px) + 8px)`;
    b.style.removeProperty('--lrn-tail-x');
    return;
  }
  const W = 330, M = 12;
  b.style.right = 'auto'; b.style.bottom = 'auto'; b.style.width = W + 'px';
  const bh = b.offsetHeight || 150;
  const vw = window.innerWidth, vh = window.innerHeight;
  const avoid = rects.filter(Boolean);
  const fits = (x, y) => x >= M && y >= M && x + W <= vw - M && y + bh <= vh - M
    && avoid.every((r) => x + W < r.left - 4 || x > r.right + 4 || y + bh < r.top - 4 || y > r.bottom + 4);
  const t = rects[0];
  let pos = null;
  if (t) {
    const cands = [
      [t.right + 16, t.top + t.height / 2 - bh / 2],
      [t.left - W - 16, t.top + t.height / 2 - bh / 2],
      [t.left + t.width / 2 - W / 2, t.bottom + 16],
      [t.left + t.width / 2 - W / 2, t.top - bh - 16],
    ];
    for (const [x, y] of cands) {
      const cy = Math.min(vh - M - bh, Math.max(M, y));
      if (fits(x, cy)) { pos = [x, cy]; break; }
    }
  }
  if (!pos) {
    // No clear side: dock bottom-right, then bottom-left, away from everything.
    for (const x of [vw - W - M, M]) {
      const y = vh - bh - M;
      if (fits(x, y)) { pos = [x, y]; break; }
    }
  }
  if (!pos) pos = [vw - W - M, vh - bh - M];
  b.style.left = Math.round(pos[0]) + 'px';
  b.style.top = Math.round(pos[1]) + 'px';
  // Tail: which edge of the bubble faces the target.
  if (t) {
    const cx = pos[0] + W / 2, cy = pos[1] + bh / 2;
    const tcx = t.left + t.width / 2, tcy = t.top + t.height / 2;
    b.dataset.tail = Math.abs(tcx - cx) > Math.abs(tcy - cy) ? (tcx > cx ? 'right' : 'left') : (tcy > cy ? 'bottom' : 'top');
  } else delete b.dataset.tail;
}

// The strip the dragged card travels across, so the bubble is not placed on it.
function _pathRect(from, to) {
  const a = from.getBoundingClientRect(), pt = to.virtual ? to.point : null, b = to.getBoundingClientRect();
  const ax = a.left + a.width / 2, ay = a.top + a.height / 2;
  const bx = pt ? pt.x : b.left + b.width / 2, by = pt ? pt.y : b.top + b.height / 2;
  const left = Math.min(ax, bx) - 30, right = Math.max(ax, bx) + 30, top = Math.min(ay, by) - 50, bottom = Math.max(ay, by) + 30;
  return { left, top, right, bottom, width: right - left, height: bottom - top };
}
function _rectOf(el) { const r = el.getBoundingClientRect(); return { left: r.left, top: r.top, right: r.right, bottom: r.bottom, width: r.width, height: r.height }; }

function _showOutline(el) {
  const o = _q('#lrn-outline', _root);
  if (!o) return;
  if (!el) { o.hidden = true; return; }
  const r = el.getBoundingClientRect(), pad = 4;
  o.style.left = (r.left - pad) + 'px'; o.style.top = (r.top - pad) + 'px';
  o.style.width = (r.width + pad * 2) + 'px'; o.style.height = (r.height + pad * 2) + 'px';
  if (o.hidden) { o.hidden = false; o.classList.remove('lrn-fade'); void o.offsetWidth; o.classList.add('lrn-fade'); }
}

// An arrow beside the cue, pointing at it (step 1) or from the source toward
// the destination (step 3). Two 6px nudges, then it holds still. Any input
// stops the nudging.
function _showArrow(cueEl, towardEl, restart) {
  const a = _q('#lrn-arrow', _root);
  if (!a) return;
  const st = _step();
  if (!cueEl || !st || !st.nudge) { a.hidden = true; return; }
  const r = cueEl.getBoundingClientRect();
  let ang, ax, ay;
  if (towardEl) {
    const d = towardEl.getBoundingClientRect();
    const dx = (d.left + d.width / 2) - (r.left + r.width / 2), dy = (d.top + d.height / 2) - (r.top + r.height / 2);
    ang = Math.atan2(dy, dx);
    ax = r.left + r.width / 2 + Math.cos(ang) * (r.width / 2 + 22) - 14;
    ay = r.top + r.height / 2 + Math.sin(ang) * (r.height / 2 + 22) - 14;
  } else {
    // Point at the cue from the right, so it reads as "this one".
    ang = Math.PI;
    ax = r.right + 8; ay = r.top + r.height / 2 - 14;
    if (ax + 40 > window.innerWidth) { ang = 0; ax = r.left - 36; }
  }
  a.style.left = Math.round(ax) + 'px'; a.style.top = Math.round(ay) + 'px';
  a.style.setProperty('--lrn-ang', (ang * 180 / Math.PI) + 'deg');
  const wasHidden = a.hidden;
  a.hidden = false;
  if (restart || wasHidden) {
    a.classList.remove('lrn-nudge'); void a.offsetWidth;
    if (!S.nudgesStopped) a.classList.add('lrn-nudge');
  }
}

// ── Hand cue ─────────────────────────────────────────────────────────────────
// A pointing hand that SHOWS the gesture instead of describing it. Two generic
// calls so any lesson can reuse it: Hand.click(target) taps the target;
// Hand.drag(from, to) presses `from`, carries a translucent ghost of it onto
// `to`, and drops. Everything lives in #lrn-root, is pointer-events: none, and
// is drawn from LIVE rects every frame, so a resize, a scroll or a Floor poll
// that replaces the nodes never leaves it pointing where the target used to be
// (the caller hands it the freshly resolved nodes on every tick).
// On a phone the real drag begins with a long press, so the hand holds first.
// Under reduced motion / Quiet effects it is a static hand on the target.
const HAND_W = 44, HAND_H = 53;
const HAND_TIP_X = 15.4, HAND_TIP_Y = 5;          // fingertip, in element px (viewBox 40x48 at 1.1x)
const HAND_HOLD_MS = 500;                         // phone long-press; the real one (pointer-drag.js) fires at 400
const HAND_RESUME_MS = 800;                       // after the user lets go, wait out the verify tick before returning
const HAND_SVG = `<svg viewBox="0 0 40 48" width="${HAND_W}" height="${HAND_H}" focusable="false" aria-hidden="true">
  <path d="M10 25V8.5A4 4 0 0 1 18 8.5V19A3 3 0 0 1 24 19.5A3 3 0 0 1 30 21A3 3 0 0 1 35 23.5V35Q35 44 26 44H19Q12 44 9.5 38L4.6 29.4A2.4 2.4 0 0 1 8.8 27L10 29Z" fill="#f6e6cc" stroke="#7a4a2a" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
  <path d="M24 19.5V25M30 21V25" fill="none" stroke="#7a4a2a" stroke-width="1.6" stroke-linecap="round"/>
</svg>`;

const _clamp01 = (x) => Math.max(0, Math.min(1, x));
const _lerp = (a, b, p) => a + (b - a) * p;
const _easeOut = (p) => 1 - Math.pow(1 - p, 3);
const _easeInOut = (p) => (p < 0.5 ? 4 * p * p * p : 1 - Math.pow(-2 * p + 2, 3) / 2);
const _segP = (t, a, b) => (b > a ? _clamp01((t - a) / (b - a)) : 1);

// One loop of the cue as consecutive named phases (ms). The phase name is
// published on #lrn-hand[data-phase] so a test can wait for a moment, not a time.
function _handPlan(kind, phone) {
  let t = 0;
  const p = {};
  const add = (name, d) => { p[name] = [t, t + d]; t += d; };
  add('glide', 700); add('press', 200);
  if (kind === 'click') {
    add('hold', 100); add('lift', 250); add('rest', 800); add('fade', 200);
  } else {
    add('hold', phone ? HAND_HOLD_MS : 100); add('carry', 1200); add('release', 250); add('fade', 250); add('gap', 1000);
  }
  p.total = t;
  return p;
}
const HAND_ORDER = ['glide', 'press', 'hold', 'lift', 'rest', 'carry', 'release', 'fade', 'gap'];

function _handCentre(el) {
  const r = el.getBoundingClientRect();
  const pt = el.virtual ? el.point : null;   // an exposed-part target lands on a point that really is the tile
  return { x: pt ? pt.x : r.left + r.width / 2, y: pt ? pt.y : r.top + r.height / 2, r };
}
function _handLive(el) { return !!el && el.isConnected && (el.virtual || _measurable(el)); }
// An arc that bulges upward so the carry reads as lifting, not sliding.
function _handPath(a, b, p) {
  const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1;
  let nx = -dy / len, ny = dx / len;
  if (ny > 0) { nx = -nx; ny = -ny; }
  const bulge = Math.sin(Math.PI * p) * Math.min(48, len * 0.12);
  return { x: a.x + dx * p + nx * bulge, y: a.y + dy * p + ny * bulge };
}

const Hand = (() => {
  let h = null;   // { mode, kind, phone, from, to, plan, t0, lap, raf, ghostScale }

  function els() {
    if (!_root || !_root.isConnected) return null;
    const g = (id) => _q('#' + id, _root);
    const E = { hand: g('lrn-hand'), ripple: g('lrn-hand-ripple'), hold: g('lrn-hand-hold'), ghost: g('lrn-hand-ghost'), drop: g('lrn-hand-drop') };
    return E.hand ? E : null;
  }
  function hideAll(E) {
    for (const k of Object.keys(E)) if (E[k]) E[k].hidden = true;
    E.hand.removeAttribute('data-kind'); E.hand.removeAttribute('data-phase');
    if (E.ghost) E.ghost.innerHTML = '';
  }
  const at = (el, x, y, s) => { el.style.transform = `translate(${x.toFixed(1)}px, ${y.toFixed(1)}px) scale(${s})`; };

  // A translucent copy of the grabbed card. Inert: no ids, handlers or data-*
  // survive the clone, so nothing document-wide can ever match it.
  function buildGhost(E, el) {
    const r = el.getBoundingClientRect();
    const c = el.cloneNode(true);
    for (const n of [c, ...c.querySelectorAll('*')]) {
      for (const a of Array.from(n.attributes)) {
        if (a.name === 'id' || a.name.startsWith('on') || a.name.startsWith('data-') || a.name === 'tabindex'
          || a.name === 'role' || a.name.startsWith('aria-')) n.removeAttribute(a.name);
      }
    }
    c.style.cssText += ';margin:0;width:100%;height:100%;box-sizing:border-box;pointer-events:none';
    E.ghost.innerHTML = '';
    E.ghost.appendChild(c);
    E.ghost.style.width = r.width + 'px';
    E.ghost.style.height = r.height + 'px';
    h.ghostScale = Math.min(1, 170 / r.width, 110 / r.height);
  }

  function drawStatic(E) {
    if (!_handLive(h.from)) { hideAll(E); return; }
    const c = _handCentre(h.from);
    for (const k of ['ripple', 'hold', 'ghost', 'drop']) E[k].hidden = true;
    E.hand.hidden = false; E.hand.dataset.kind = 'static'; E.hand.dataset.phase = 'static';
    at(E.hand, c.x - HAND_TIP_X, c.y - HAND_TIP_Y, 1);
    E.hand.style.opacity = '1';
  }

  function draw(E, t) {
    const P = h.plan, drag = h.kind === 'drag';
    if (!_handLive(h.from) || (drag && !_handLive(h.to))) { hideAll(E); return; }
    const name = HAND_ORDER.find((n) => P[n] && t >= P[n][0] && t < P[n][1]) || 'gap';
    const prog = (n) => (P[n] ? _segP(t, P[n][0], P[n][1]) : 0);
    const a = _handCentre(h.from), c0 = { x: a.x, y: a.y };
    const b = drag ? _handCentre(h.to) : null, c1 = b ? { x: b.x, y: b.y } : c0;
    const start = { x: c0.x + 72, y: c0.y + 56 };
    let tip = c0, scale = 1, opacity = 1;
    if (name === 'glide') { const p = _easeOut(prog('glide')); tip = { x: _lerp(start.x, c0.x, p), y: _lerp(start.y, c0.y, p) }; opacity = _clamp01(p * 2.2); }
    else if (name === 'press') scale = _lerp(1, 0.88, _easeOut(prog('press')));
    else if (name === 'hold') scale = 0.88;
    else if (name === 'lift') scale = _lerp(0.88, 1, _easeOut(prog('lift')));
    else if (name === 'carry') { tip = _handPath(c0, c1, _easeInOut(prog('carry'))); scale = 0.9; }
    else if (name === 'release') { tip = c1; scale = _lerp(0.9, 1, _easeOut(prog('release'))); }
    else if (name === 'fade') { tip = drag ? c1 : c0; opacity = 1 - prog('fade'); }
    else if (name === 'gap') { tip = drag ? c1 : c0; opacity = 0; }
    // 'rest': the hand stays on the target at full size and opacity.

    E.hand.hidden = opacity <= 0.001;
    E.hand.dataset.kind = h.kind;       // hideAll() clears it when a target blinks out of view mid-loop
    E.hand.dataset.phase = name;
    at(E.hand, tip.x - HAND_TIP_X, tip.y - HAND_TIP_Y, scale);
    E.hand.style.opacity = String(opacity);

    // Ripple at the fingertip when the finger lands.
    const rp = _segP(t, P.press[0] + 80, P.press[0] + 730);
    const showRipple = rp > 0 && rp < 1;
    E.ripple.hidden = !showRipple;
    if (showRipple) { at(E.ripple, c0.x, c0.y, _lerp(0.3, 1.6, _easeOut(rp))); E.ripple.style.opacity = String((1 - rp) * 0.85); }

    // Phone long-press: a ring fills while the finger holds, then the drag starts.
    const showHold = drag && h.phone && name === 'hold';
    E.hold.hidden = !showHold;
    if (showHold) { at(E.hold, c0.x, c0.y, 1); E.hold.style.setProperty('--lrn-p', prog('hold').toFixed(3)); }

    if (!drag) { E.ghost.hidden = true; E.drop.hidden = true; return; }

    // Ghost of the card: lifted at the grab, rides the fingertip, fades on drop.
    let ga = 0, gc = c0, gs = 1;
    if (name === 'hold') ga = h.phone ? 0 : 0.55 * prog('hold');
    else if (name === 'carry') { ga = 0.55 * (h.phone ? _clamp01(prog('carry') * 8) : 1); gc = tip; gs = _lerp(1, h.ghostScale, _easeOut(_clamp01(prog('carry') * 4))); }
    else if (name === 'release') { ga = 0.55 * (1 - prog('release')); gc = c1; gs = h.ghostScale * _lerp(1, 0.8, prog('release')); }
    E.ghost.hidden = ga <= 0.001;
    if (!E.ghost.hidden) {
      const gw = parseFloat(E.ghost.style.width) || a.r.width, gh = parseFloat(E.ghost.style.height) || a.r.height;
      at(E.ghost, gc.x - gw / 2, gc.y - gh / 2, gs);
      E.ghost.style.opacity = String(ga);
    }

    // Drop pulse on the destination tile.
    const dp = _segP(t, P.release[0], P.fade[1] + 150);
    const showDrop = t >= P.release[0] && dp < 1 && name !== 'gap';
    E.drop.hidden = !showDrop;
    if (showDrop) {
      const r = b.r;
      E.drop.style.left = r.left + 'px'; E.drop.style.top = r.top + 'px';
      E.drop.style.width = r.width + 'px'; E.drop.style.height = r.height + 'px';
      E.drop.style.transform = `scale(${_lerp(1, 1.04, dp)})`;
      E.drop.style.opacity = String((1 - dp) * 0.9);
    }
  }

  function loop(now) {
    if (!h) return;
    h.raf = requestAnimationFrame(loop);
    const E = els();
    if (!E) return;
    if (!h.t0) h.t0 = now;
    const el = now - h.t0, lap = Math.floor(el / h.plan.total);
    // A new loop re-reads the grabbed card (its open/closed state may have changed);
    // an empty ghost means a Floor poll swapped the card out mid-loop (hideAll cleared it).
    if (lap !== h.lap || (h.kind === 'drag' && !E.ghost.firstChild)) {
      h.lap = lap;
      if (h.kind === 'drag' && _handLive(h.from)) buildGhost(E, h.from);
    }
    draw(E, el % h.plan.total);
  }

  // opts.instant: the real gesture starts on press (a port, not a long-press card),
  // so the phone cue skips the hold.
  function set(kind, from, to, opts) {
    _ensureRoot();
    const E = els();
    if (!E || !from) { stop(); return; }
    const phone = _isMobile() && !(opts && opts.instant), mode = _reduced() ? 'static' : kind;
    if (h && h.mode === mode && h.phone === phone) {
      h.from = from; h.to = to || null;                     // fresh nodes after a Floor poll; the loop keeps its place
      if (mode === 'static') drawStatic(E);
      return;
    }
    stop();
    h = { mode, kind, phone, from, to: to || null, plan: _handPlan(kind, phone), t0: 0, lap: -1, raf: 0, ghostScale: 1 };
    E.hand.dataset.kind = mode === 'static' ? 'static' : kind;
    if (mode === 'static') drawStatic(E);
    else h.raf = requestAnimationFrame(loop);
  }
  function stop() {
    if (h && h.raf) cancelAnimationFrame(h.raf);
    h = null;
    const E = els();
    if (E) hideAll(E);
  }
  return { click: (target) => set('click', target, null), drag: (from, to, opts) => set('drag', from, to, opts), stop, get active() { return !!h; } };
})();

// While the user holds the real target (or has just let go), the hand stays away:
// it must never dance over their own drag, and a click that is about to verify
// must not flash it back. Infinity = a pointer is down on the target.
let _handHoldUntil = 0;

function _syncHand(res) {
  if (S.hirePending || Date.now() < _handHoldUntil) { Hand.stop(); return; }
  if (res.dest) { Hand.drag(res.el, res.dest, { instant: !!res.instant }); return; }
  const st = _step();
  if (st.hire || st.fallback) {
    // No usable drop zone (phone, or too little of the tile showing): tapping the
    // card hires nothing, so show the gesture the user can actually complete.
    const btn = _q(`#lrn-bubble [data-lrn="${st.hire ? 'hire' : 'fallback'}"]`, _root);
    if (btn && _measurable(btn)) Hand.click(btn); else Hand.stop();
    return;
  }
  Hand.click(res.cue || res.el);
}

// ── Engine: steps ────────────────────────────────────────────────────────────
function _clearTimers() {
  if (_tickTimer) { clearInterval(_tickTimer); _tickTimer = null; }
  if (_ackTimer) { clearTimeout(_ackTimer); _ackTimer = null; }
}

function _hideDecor() {
  if (!_root) return;
  const o = _q('#lrn-outline', _root), a = _q('#lrn-arrow', _root);
  if (o) o.hidden = true;
  if (a) { a.hidden = true; a.classList.remove('lrn-nudge'); }
  Hand.stop();
}

async function _enterStep(idx, notice) {
  if (!S) return;
  const gen = ++_gen;
  _clearTimers();
  S.idx = idx;
  S.phase = 'preparing';
  S.error = ''; S.hint = false; S.hireFailed = false; S.hirePending = false;
  S.notice = notice || '';
  S.stepStartedMs = Date.now();
  S.unresolvedSince = null; S.awaySince = null; S.surfaceWasFront = false; S.scrolled = false;
  S.nudgesStopped = false; S.tickNow = false; S.pendingEvidence = null;
  _handHoldUntil = 0; Hand.stop();
  _patch(S.lessonId, (r) => { r.step = idx; r.stepStartedAt = S.stepStartedMs; r.status = 'active'; });
  _ev('step-start', { step: _step().id });
  _renderBubble(true);
  try { await _prepare(); } catch (e) { console.warn('[learn] step preparation failed:', e); }
  if (!S || gen !== _gen) return;
  const back = _prereqRewind();
  if (back >= 0) { _ev('rewind', { step: _step().id }); return _enterStep(back, 'The canvas was reset, so this goes back a step. Only practice state resets.'); }
  if (S.lessonId === WORKFLOWS_ID) {
    const def = _wfDef();
    S.baseNodes = def ? (def.nodes || []).length : 0;
    S.baseEntry = def ? _wfEntryNames(def).length : 0;
  }
  S.phase = 'active';
  _renderBubble(true);
  _tick();
  _tickTimer = setInterval(_tick, TICK_MS);
}

// Preparation may navigate and load fixtures. It never performs the action.
async function _prepare() {
  const st = _step();
  if (S.lessonId === WORKFLOWS_ID) return _prepareWorkflows(st);
  if (st.id === 'open-chat') {
    // The chat must be opened by the user DURING this step, so a leftover open
    // practice chat from an earlier pass is closed first.
    const P = window.LearnPractice;
    if (typeof openModals !== 'undefined' && openModals.has(P.PID) && typeof closeModalById === 'function') closeModalById(P.PID);
    await _openFloorSurface();
  } else if (typeof window.floorClosePicker === 'function') {
    window.floorClosePicker();
  }
  if (st.id === 'hire') {
    if (!_floorFront()) await _openFloorSurface();
    _exposeDestination();
  }
}

// Spec, step 3 desktop: put the real Floor and the dashboard where the practice
// tile is visible BEFORE the user grabs anything (never while a pointer is
// held, and only the Floor window moves). When the viewport cannot show both,
// the window stays put and the "Hire to practice" control is the path.
function _exposeDestination() {
  if (_isMobile() || document.body.classList.contains('hire-active')) return;
  const tile = _q(`#projects-col .card[data-id="${window.LearnPractice.PID}"]`);
  const e = (typeof openModals !== 'undefined') ? openModals.get('__floor') : null;
  if (!tile || !e || e.minimized) return;
  const win = e.element, content = win.querySelector('.modal-content') || win;
  const t = tile.getBoundingClientRect(), w = content.getBoundingClientRect();
  if (t.right <= w.left || t.left >= w.right || t.bottom <= w.top || t.top >= w.bottom) return;  // already clear
  const left = Math.round(t.right + 16);
  if (left + w.width > window.innerWidth - 8) return;                                              // no room: fallback control
  win.style.left = left + 'px';
}

async function _openFloorSurface() {
  if (typeof window.openFloor === 'function') await window.openFloor();
}

// The Workflows lesson's window: the practice project's modal on its Workflows
// tab. Opening it is navigation, never the taught action (that is pressing
// "+ New Workflow").
async function _openCanvasSurface() {
  const P = window.LearnPractice;
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
function _openSurface() {
  return S && S.lessonId === WORKFLOWS_ID ? _openCanvasSurface() : _openFloorSurface();
}

async function _prepareWorkflows(st) {
  const P = window.LearnPractice;
  if (st.id === 'open-canvas') {
    // The canvas must be opened by the user DURING this step, so a leftover
    // practice modal or canvas from an earlier pass is cleared first.
    if (typeof openModals !== 'undefined' && openModals.has(P.PID) && typeof closeModalById === 'function') closeModalById(P.PID);
    if (window._wfLearn) window._wfLearn.discard(P.PID);
  }
  await _openCanvasSurface();
  // Resuming after a reload: the canvas is gone with the page. An empty one is
  // the correct start for the steps that follow, so restore it (the user has
  // already done "open the canvas"; nothing here places or connects anything).
  if (st.id !== 'open-canvas' && !_wfDef() && typeof window.openWorkflowBuilder === 'function') {
    await window.openWorkflowBuilder(null, P.PID, 'a new workflow');
  }
}

// Which earlier step a step needs finished, when the canvas it depends on is
// gone (a reload empties it). -1 = nothing to redo.
function _prereqRewind() {
  if (!S || S.lessonId !== WORKFLOWS_ID) return -1;
  const id = _step().id;
  if (id === 'open-canvas') return -1;
  const def = _wfDef();
  if (!def) return 0;
  if (id === 'place-node') return -1;
  if (!(def.nodes || []).length) return 1;
  if (id === 'save') {
    const names = def.nodes.map((n) => n.name), edges = def.edges || [];
    return _wfEntryNames(def).some((n) => names.includes(n) && !edges.some((e) => e.to === n)) ? -1 : 2;
  }
  return -1;
}

// Re-resolve, reposition, verify. Runs on a timer so it survives the Floor's
// own poll replacing #floor-body: every pass reacquires the nodes by identity.
function _tick() {
  if (!S || (S.phase !== 'active' && S.phase !== 'unavailable')) return;
  _applyQuietClass();
  const dragging = document.body.classList.contains('pd-drag-active') || document.body.classList.contains('hire-active');
  const st = _step();
  const front = _surfaceFront();
  S.surfaceFrontNow = front;
  if (front) { S.surfaceWasFront = true; S.awaySince = null; }

  // Authoritative verification first: a step is earned by state, not by looks.
  // A save that failed waits for the Retry control instead of looping.
  if (S.retrySave) { _renderBubble(); return; }
  const v = _verify(st);
  if (v) { _commit(v); return; }

  if (st.hire) _watchHireFailure();

  if (dragging) {   // freeze highlights during a drag, and keep the hand off the user's own drag
    _handHoldUntil = Math.max(_handHoldUntil, Date.now() + HAND_RESUME_MS);
    Hand.stop();
    _renderBubble();
    return;
  }

  const res = front ? _resolveStep() : { error: 'away' };
  if (res.el && _measurable(res.el) && _unobscured(res.el)) {
    S.unresolvedSince = null;
    if (S.phase === 'unavailable') { S.phase = 'active'; }
    _scrollOnce(res.el);
    _showOutline(_isMobile() && st.id === 'hire' ? res.el : res.el);
    _showArrow(res.cue || res.el, res.dest || null, false);
    _renderBubble();
    _placeBubble([_rectOf(res.el), res.dest ? _rectOf(res.dest) : null, res.dest ? _pathRect(res.el, res.dest) : null]);
    _syncHand(res);   // after the bubble is placed: the hand may point at a button inside it
    return;
  }

  _hideDecor();
  _renderBubble();
  _placeBubble([]);
  if (!front) {
    // Away from the lesson's window: steps that need it show a way back. Once
    // the user had it in this step and then leaves it, that is a navigation away.
    if (S.surfaceWasFront) {
      if (!S.awaySince) S.awaySince = Date.now();
      if (Date.now() - S.awaySince > UNRESOLVED_PAUSE_MS) _pause(_lesson().leftMsg);
    }
    return;
  }
  // The lesson's window is in front but the target will not resolve (missing, duplicate,
  // hidden, covered). Never substitute demo markup, never mark it done.
  if (!S.unresolvedSince) S.unresolvedSince = Date.now();
  if (Date.now() - S.unresolvedSince > UNRESOLVED_PAUSE_MS) {
    _ev('target-failure', { step: st.id, reason: res.error || 'unobscured' });
    _clearTimers();
    S.phase = 'unavailable';
    _renderBubble(true);
    _placeBubble([]);
  }
}

function _scrollOnce(el) {
  if (S.scrolled) return;
  S.scrolled = true;
  // Only before interaction starts, and only on a phone where the dock may
  // cover the target. Never moves a window.
  if (!_isMobile()) return;
  try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch (e) { /* older engines */ }
}

// Authoritative evidence for the active step, or null.
function _verify(st) {
  const P = window.LearnPractice;
  if (!P || !P.active || P.runId !== S.runId) return null;
  if (S.lessonId === WORKFLOWS_ID) return _verifyWorkflows(st);
  const acted = (kind) => S.actions.some((a) => a.kind === kind && a.at >= S.stepStartedMs);
  if (st.id === 'open-chat') {
    if (!acted('open-figure')) return null;
    const out = document.getElementById('agent-output-' + P.SID);
    const win = typeof openModals !== 'undefined' ? openModals.get(P.PID) : null;
    if (!out || !win || win.minimized || !_measurable(out)) return null;
    if (!(out.textContent || '').includes(P.TRANSCRIPT)) return null;
    return { ok: true, evidence: { project: P.PID, session: P.SID } };
  }
  if (st.id === 'open-type') {
    if (!acted('toggle-type')) return null;
    const card = _q(`#floor-body .fl-bench-card[data-fl-type="${P.GUIDE_REF}"]`);
    if (!card || !card.classList.contains('fl-bench-open')) return null;
    const row = card.querySelector('.fl-pick-row');
    if (!row || !_measurable(row)) return null;
    return { ok: true, evidence: { type: P.GUIDE_REF } };
  }
  if (st.id === 'hire') {
    const hit = P.roster(S.runId).find((r) => r.character === P.GUIDE_REF && !r.removed_at
      && r.run_id === S.runId && Date.parse(r.hired_at) >= S.stepStartedMs - 1
      && (r.hired_by === 'drag' || r.hired_by === 'menu'));
    return hit ? { ok: true, evidence: { type: P.GUIDE_REF, seq: hit.seq } } : null;
  }
  return null;
}

// Workflows evidence. Steps 1 to 3 read the builder's own model (what the canvas
// holds), step 4 reads the practice store (what Save committed). Nothing here
// looks at a click or at what is painted.
function _verifyWorkflows(st) {
  const P = window.LearnPractice;
  const def = _wfDef();
  if (st.id === 'open-canvas') {
    const vp = _q(`#modal-layer [data-modal-id="${P.PID}"] #wfb-canvas-viewport`);
    return def && vp && _measurable(vp) ? { ok: true, evidence: { project: P.PID } } : null;
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

// A failed practice hire keeps the step and shows the real error with Retry.
function _watchHireFailure() {
  const at = window._floorLastHireErrorAt || 0;
  if (at >= S.stepStartedMs && window._floorLastHireError && !S.hirePending) {
    if (!S.hireFailed || S.error !== window._floorLastHireError) {
      S.hireFailed = true; S.error = window._floorLastHireError;
      _renderBubble(true);
    }
  }
}

// Save the verified action BEFORE advancing. A failed save stays retryable and
// never re-performs the hire.
function _commit(v) {
  if (!S || S.phase === 'verifying' || S.phase === 'acknowledged') return;
  const st = _step(), L = _lesson();
  S.phase = 'verifying';
  _clearTimers();
  _hideDecor();
  const id = `${S.runId}:${st.id}`;
  const last = S.idx === L.steps.length - 1;
  const saved = _patch(S.lessonId, (r) => {
    if (!(r.verified || []).some((x) => x.id === id)) {
      (r.verified = r.verified || []).push({ id, run: S.runId, step: st.id, at: Date.now() });
    }
    if (last) { r.status = 'completed'; r.completedAt = Date.now(); r.completions = (r.completions || 0) + 1; }
    else r.step = S.idx + 1;
  });
  if (!saved) {
    S.pendingEvidence = v; S.phase = 'active'; S.error = 'Could not save your progress. Nothing was lost; try again.';
    S.retrySave = true;
    _renderBubble(true);
    return;
  }
  S.error = ''; S.retrySave = false;
  _ev('step-verified', { step: st.id });
  S.tickNow = true;
  if (last) {
    S.phase = 'completed';
    _renderBubble(true);
    _finishCelebration();
    _announce('3/3');
    return;
  }
  S.phase = 'acknowledged';
  _renderBubble(true);
  _announce((S.idx + 1) + '/' + L.steps.length);
  const gen = _gen;
  _ackTimer = setTimeout(() => { if (S && gen === _gen && S.phase === 'acknowledged') _enterStep(S.idx + 1); }, ACK_MS);
}

function _announce() { /* the .lrn-progress region is aria-live="polite" and re-renders with the count */ }

function _finishCelebration() {
  const dots = _q('#lrn-dots', _root), b = _q('#lrn-bubble', _root);
  if (!dots || !b) return;
  _placeBubble([]);
  if (_reduced()) { dots.hidden = true; return; }   // static seal only
  const r = b.getBoundingClientRect();
  dots.style.left = (r.left + r.width / 2) + 'px';
  dots.style.top = (r.top + 8) + 'px';
  const cols = ['var(--accent)', 'var(--green, #4caf7d)', 'var(--amber, #e0a93b)'];
  dots.innerHTML = Array.from({ length: 6 }, (_, i) => {
    const a = (i / 6) * Math.PI * 2 + 0.4;
    return `<i style="--dx:${Math.round(Math.cos(a) * 46)}px;--dy:${Math.round(Math.sin(a) * 30 - 8)}px;background:${cols[i % 3]}"></i>`;
  }).join('');
  dots.hidden = false;
  setTimeout(() => { if (dots) dots.hidden = true; }, 760);
}

function _pause(msg) {
  if (!S || S.phase === 'paused' || S.phase === 'completed') return;
  const wasAck = S.phase === 'acknowledged';
  _clearTimers();
  _hideDecor();
  S.phase = 'paused';
  S.pauseMsg = msg || 'Paused. Your progress is saved.';
  S.pausedFromAck = wasAck;
  _patch(S.lessonId, (r) => { r.status = 'paused'; });
  _ev('pause', { step: _step().id });
  _renderBubble(true);
  _placeBubble([]);
}

// Resume re-prepares the same step with a fresh evidence window: only an action
// taken after resuming counts.
async function _resume() {
  if (!S) return;
  const next = S.pausedFromAck ? S.idx + 1 : S.idx;
  S.pausedFromAck = false;
  if (next >= _lesson().steps.length) { S.phase = 'completed'; _renderBubble(true); return; }
  await _enterStep(next);
}

// ── Entry points ─────────────────────────────────────────────────────────────
function _needsRestart(rec, L) {
  return !!rec && (rec.status === 'active' || rec.status === 'paused')
    && (rec.version !== L.version || rec.fixture !== FIXTURE_VERSION);
}

// start(): the one door every entry point uses (hub, offer, header, Claydo).
//   opts.fresh  = start a new run even over saved progress (Replay).
async function startLesson(lessonId, source, opts) {
  const L = LESSONS[lessonId];
  if (!L) { console.warn('[learn] unknown lesson refused:', lessonId); return false; }
  opts = opts || {};
  if (S && S.lessonId === lessonId && S.phase !== 'completed' && !opts.fresh) {
    // Already running: just bring it back (resume if paused).
    if (S.phase === 'paused') await _resume();
    else _renderBubble(true);
    return true;
  }
  if (S && S.lessonId !== lessonId) {
    // One active lesson at a time. A finished lesson has nothing to lose, so
    // only an unfinished one asks.
    if (S.phase !== 'completed' && !window.confirm('Leave the current practice and switch? Your progress is saved.')) return false;
    leave();
  }
  if (S) leave(true);

  const rec = _rec(lessonId);
  const resumable = !opts.fresh && rec && (rec.status === 'active' || rec.status === 'paused') && !!rec.runId;
  _priorFocus = document.activeElement && document.activeElement !== document.body ? document.activeElement : null;
  const runId = resumable ? rec.runId : _newRunId();
  if (!resumable) {
    _patch(lessonId, (r) => {
      r.version = L.version; r.fixture = FIXTURE_VERSION; r.runId = runId; r.status = 'active';
      r.step = 0; r.verified = (r.verified || []).filter((x) => x.run !== runId); r.stepStartedAt = 0;
    });
  }
  S = { lessonId, runId, idx: resumable ? Math.min(rec.step || 0, L.steps.length - 1) : 0, phase: 'preparing',
    source: source || '', actions: [], error: '', collapsed: false };
  _ev(resumable ? 'resume' : 'start', { source: source || '' });
  window.LearnPractice.enter(runId, !resumable);
  _ensureRoot();
  document.body.classList.add('lrn-practicing');
  closeHub();
  _dismissOffer();
  if (resumable && _needsRestart(rec, L)) {
    S.phase = 'restart';
    S.restartMsg = 'This lesson was updated, so this practice needs a restart. Only practice state resets. Your real projects are untouched.';
    _renderBubble(true); _placeBubble([]);
    return true;
  }
  await _refreshSurfaces(true);
  _arrive();
  await _enterStep(S.idx);
  return true;
}

function _arrive() {
  const b = _q('#lrn-bubble', _root);
  if (!b) return;
  b.classList.remove('lrn-arrive'); void b.offsetWidth;
  b.classList.add('lrn-arrive');
}

// Re-draw the dashboard and the Floor from whichever context is now active.
async function _refreshSurfaces(entering) {
  const P = window.LearnPractice;
  for (let i = 0; i < 6; i++) {
    if (typeof window.fetchProjects === 'function') await window.fetchProjects();
    const has = typeof allProjects !== 'undefined' && allProjects.some((p) => p.id === P.PID);
    if (entering ? has : !has) break;
    await new Promise((r) => setTimeout(r, 250));
  }
  if (typeof openModals !== 'undefined' && openModals.has('__floor') && typeof window.refreshFloor === 'function') {
    await window.refreshFloor();
  }
}

// Leave removes overlays, listeners and routing and restores the prior surface.
// It does not reset progress. `quiet` skips the surface refresh (used when a
// new run starts immediately after).
function leave(quiet) {
  if (!S) return;
  _clearTimers();
  _gen++;
  const P = window.LearnPractice;
  if (S.phase !== 'completed') {
    _patch(S.lessonId, (r) => { if (r.status === 'active') r.status = 'paused'; });
    _ev('leave', { step: _step() ? _step().id : '' });
  }
  Hand.stop();
  _handHoldUntil = 0;
  S = null;
  _bubbleSig = '';
  if (_root) { _root.remove(); _root = null; }
  document.body.classList.remove('lrn-practicing');
  if (typeof openModals !== 'undefined' && openModals.has(P.PID) && typeof closeModalById === 'function') closeModalById(P.PID);
  if (window._wfLearn) window._wfLearn.discard(P.PID);   // a practice canvas must not outlive its run
  P.leave();
  window._floorLastHireError = ''; window._floorLastHireErrorAt = 0;
  if (!quiet) {
    _refreshSurfaces(false);
    if (_priorFocus && _priorFocus.isConnected) { try { _priorFocus.focus(); } catch (e) { /* gone */ } }
  }
  _priorFocus = null;
}

function notify(kind, detail) {
  if (!S || !window.LearnPractice.active) return;
  S.actions.push({ kind, at: Date.now(), detail: detail || {} });
}

// ── Bubble controls ──────────────────────────────────────────────────────────
document.addEventListener('click', (e) => {
  const t = e.target && e.target.closest && e.target.closest('[data-lrn]');
  if (!t || !S || !_root || !_root.contains(t)) return;
  const act = t.dataset.lrn;
  if (act === 'pause') _pause();
  else if (act === 'resume') _resume();
  else if (act === 'leave') leave();
  else if (act === 'hint') _hint();
  else if (act === 'quiet') {
    try { localStorage.setItem(LS_QUIET, _quiet() ? '0' : '1'); } catch (err) { /* ignore */ }
    _applyQuietClass(); _renderBubble(true);
  } else if (act === 'collapse') { S.collapsed = !S.collapsed; _renderBubble(true); _tick(); }
  else if (act === 'back-floor' || act === 'back-canvas') { _openSurface(); }
  else if (act === 'hire') _hirePressed();
  else if (act === 'fallback') _fallbackPressed();
  else if (act === 'retry-target') { _ev('retry'); _enterStep(S.idx); }
  else if (act === 'retry-save') { const v = S.pendingEvidence; S.retrySave = false; if (v) _commit(v); }
  else if (act === 'restart') _restartPractice();
  else if (act === 'return-floor' || act === 'return-canvas') { _openSurface(); }
  else if (act === 'replay') startLesson(S.lessonId, 'replay', { fresh: true });
  else if (act === 'hub') openLearn();
});

// Escape pauses, never completes. Only when the key lands inside the bubble:
// elsewhere Escape keeps its job of closing the focused window.
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Escape' || !S || !_root) return;
  if (!_root.contains(document.activeElement)) return;
  e.stopPropagation();
  _pause();
}, true);

// Any pointer input stops the arrow's nudging (it holds still instead).
// A press ON the target is the user starting the real action: the hand leaves
// at once and stays away until they let go (and the verify tick has run).
document.addEventListener('pointerdown', (e) => {
  if (!S || !_root) return;
  S.nudgesStopped = true;
  const a = _q('#lrn-arrow', _root);
  if (a) a.classList.remove('lrn-nudge');
  if (S.phase === 'active' && Hand.active) {
    const res = _resolveStep();
    if (res.el && res.el.contains && res.el.contains(e.target)) { _handHoldUntil = Infinity; Hand.stop(); }
  }
}, true);
const _handLetGo = () => { if (_handHoldUntil === Infinity) _handHoldUntil = Date.now() + HAND_RESUME_MS; };
document.addEventListener('pointerup', _handLetGo, true);
document.addEventListener('pointercancel', _handLetGo, true);

window.addEventListener('resize', () => { if (S) _tick(); });
if (window.visualViewport) window.visualViewport.addEventListener('resize', () => { if (S) _tick(); });
window.addEventListener('scroll', () => { if (S) _tick(); }, true);

function _hint() {
  const st = _step();
  S.hint = true;
  S.hintLabel = 'Hint: ' + st.copy.split('. ')[0].replace(/\.$/, '') + '.';
  _ev('hint', { step: st.id });
  _renderBubble(true);
  S.nudgesStopped = false;
  const res = _resolveStep();
  if (res.el) _showArrow(res.cue || res.el, res.dest || null, true);
}

// The explicit control for step 3: the same real call the Bench's drag ends in,
// reached without a pointer. It performs the hire; it does not mark the step.
async function _hirePressed() {
  if (!S || S.hirePending) return;
  const P = window.LearnPractice;
  S.hirePending = true; S.error = ''; S.hireFailed = false;
  window._floorLastHireError = ''; window._floorLastHireErrorAt = 0;
  _renderBubble(true);
  try {
    if (typeof window.floorHireMenu === 'function') await window.floorHireMenu(P.PID, 'project', 'guide', 'Guide');
  } finally {
    if (S) { S.hirePending = false; _renderBubble(true); }
  }
}

// The explicit controls for the Workflows lesson's two pointer gestures: each
// makes the same real builder call the gesture ends in (a toolbar tap places the
// block at a free spot; dragging from the Trigger's dot onto a card calls
// _wfMakeRoot). They act on the canvas; the step still advances only when the
// verify tick reads the result from the model.
function _fallbackPressed() {
  if (!S || S.lessonId !== WORKFLOWS_ID) return;
  const st = _step();
  _ev('fallback', { step: st.id });
  if (st.id === 'place-node' && typeof window._wfPlaceToolAtFreeSpot === 'function') {
    window._wfPlaceToolAtFreeSpot('approval');
  } else if (st.id === 'connect' && typeof window._wfMakeRoot === 'function') {
    const def = _wfDef(), node = def && _wfLooseNode(def);
    if (node) window._wfMakeRoot(node.name);
  }
  _tick();
}

async function _restartPractice() {
  if (!S) return;
  const L = _lesson(), id = S.lessonId;
  leave(true);
  _patch(id, (r) => { r.status = 'idle'; r.runId = ''; r.step = 0; r.version = L.version; r.fixture = FIXTURE_VERSION; });
  await startLesson(id, 'restart', { fresh: true });
}

// ── Hub ──────────────────────────────────────────────────────────────────────
function _hubState(id) {
  const rec = _rec(id);
  if (S && S.lessonId === id && S.phase !== 'completed') return { label: 'Resume', note: 'In progress' };
  if (rec && (rec.status === 'active' || rec.status === 'paused')) return { label: 'Resume', note: 'In progress' };
  if (rec && (rec.completions || 0) > 0) return { label: 'Replay', note: 'Completed' };
  return { label: 'Start', note: '' };
}

function _hubCardHTML(L) {
  const st = _hubState(L.id);
  return `
      <div class="lrn-card" data-lesson="${_esc(L.id)}">
        <div class="lrn-card-top"><span class="lrn-card-title">${_esc(L.title)}</span>${st.note ? `<span class="lrn-card-note">${_esc(st.note)}</span>` : ''}</div>
        <div class="lrn-card-sub">${_esc(L.subtitle)}</div>
        <div class="lrn-card-meta">${_esc(L.meta)}</div>
        <button class="btn-add lrn-card-go" onclick="LearnEngine.startFromHub('${_esc(L.id)}')">${_esc(st.label)}</button>
      </div>`;
}

function _hubHTML() {
  return `
    <div class="lrn-hub">
      <div class="lrn-hub-lede">Practice Clayrune with Claydo. Practice never touches your real projects.</div>${LESSON_ORDER.map((id) => _hubCardHTML(LESSONS[id])).join('')}
    </div>`;
}

function openLearn() {
  if (typeof openModals === 'undefined') return;
  if (openModals.has(HUB_MODAL)) {
    const entry = openModals.get(HUB_MODAL);
    if (entry.minimized) restoreModal(HUB_MODAL);
    focusModal(HUB_MODAL);
    _refreshHub();
    return;
  }
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = HUB_MODAL;
  const content = document.createElement('div');
  content.className = 'modal-content';
  if (typeof _clampModalSize === 'function') _clampModalSize(content, 640);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">Learn with Claydo</span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="modal-minimize" onclick="minimizeModal('${HUB_MODAL}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeHub()" title="Close">&#10005;</button>
      </div>
    </div>
    <div class="lrn-hub-body" id="lrn-hub-body">${_hubHTML()}</div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(HUB_MODAL, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(HUB_MODAL);
}

function _refreshHub() {
  const body = document.getElementById('lrn-hub-body');
  if (body) body.innerHTML = _hubHTML();
}

function closeHub() {
  if (typeof openModals !== 'undefined' && openModals.has(HUB_MODAL) && typeof closeModalById === 'function') closeModalById(HUB_MODAL);
}

function hubStart(id) {
  const st = _hubState(id);
  return startLesson(id, 'hub', { fresh: st.label === 'Replay' });
}

// ── First-open offer ─────────────────────────────────────────────────────────
let _offerEl = null;
function _dismissOffer() { if (_offerEl) { _offerEl.remove(); _offerEl = null; } }

function _blockedByUserState() {
  if (document.getElementById('wt-overlay')) return true;                      // tour running
  const setup = document.getElementById('setup-overlay');
  if (setup && getComputedStyle(setup).display !== 'none') return true;          // setup still open
  if (document.querySelector('dialog[open], #cmd-overlay.visible, .mermaid-viewer-overlay')) return true;
  if (document.body.classList.contains('pd-drag-active') || document.body.classList.contains('hire-active')) return true;
  const a = document.activeElement;
  // An editor the user can see. A closed palette's input can keep focus while
  // hidden; that is not someone typing.
  if (a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)) && _measurable(a) && _unobscured(a)) return true;
  return false;
}

// Called by openFloor() after a FRESH open (not a re-focus of an open window).
// Offers once per progress owner, never over a dialog, drag or editor.
function onFloorOpened() {
  if (S) return;
  const rec = _rec(LESSON_ID);
  if (rec && rec.offered) return;
  if (_blockedByUserState()) return;
  _patch(LESSON_ID, (r) => { r.offered = true; });
  _showOffer();
}

function _showOffer() {
  _dismissOffer();
  const L = LESSONS[LESSON_ID];
  const el = document.createElement('div');
  el.className = 'lrn-bubble lrn-offer';
  el.id = 'lrn-offer';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-label', 'Claydo offer');
  el.innerHTML = `
    <div class="lrn-head"><img class="lrn-claydo" src="/assets/claydo-idle.webp" alt="" draggable="false">
      <span class="lrn-title">Claydo</span></div>
    <div class="lrn-body">
      <div class="lrn-copy">${_esc(L.offer)}</div>
      <div class="lrn-actions">
        <button class="lrn-btn lrn-primary" data-offer="go">Start practice</button>
        <button class="lrn-btn" data-offer="no">Not now</button>
      </div>
    </div>`;
  el.addEventListener('click', (e) => {
    const t = e.target.closest('[data-offer]');
    if (!t) return;
    if (t.dataset.offer === 'go') startLesson(LESSON_ID, 'offer');
    else { _patch(LESSON_ID, (r) => { r.dismissed = true; }); _dismissOffer(); }
  });
  document.body.appendChild(el);
  _offerEl = el;
  el.classList.toggle('lrn-mobile', _isMobile());
  el.classList.toggle('lrn-quiet', _reduced());
  if (_isMobile()) {
    el.style.left = '8px'; el.style.right = '8px';
    el.style.bottom = 'calc(72px + env(safe-area-inset-bottom, 0px))';
  } else {
    el.style.right = '24px'; el.style.bottom = '24px'; el.style.width = '330px';
  }
  el.classList.add('lrn-arrive');
}

// ── Public surface ───────────────────────────────────────────────────────────
window.LearnEngine = {
  LESSON_ID,
  start: startLesson, leave, notify, onFloorOpened, startFromHub: hubStart,
  hand: { click: Hand.click, drag: Hand.drag, stop: Hand.stop },
  hasLesson: (id) => Object.prototype.hasOwnProperty.call(LESSONS, id),
  lessonChip: (id) => (LESSONS[id] ? LESSONS[id].chip : ''),
  get state() { return S ? { lesson: S.lessonId, phase: S.phase, step: S.idx, run: S.runId } : null; },
  progress: (id) => _rec(id || (S ? S.lessonId : LESSON_ID)),
};
window.openLearn = openLearn;
window.closeHub = closeHub;
