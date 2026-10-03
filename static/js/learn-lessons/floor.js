// ── Learn lesson: the Floor (floor-v1) ───────────────────────────────────────
// One lesson, one file: its copy, the elements each step points at, the
// evidence each step is verified against, and how its window is opened. The
// engine (static/js/learn.js) owns progress, cues and navigation and calls the
// hooks below; the contract is documented in learn-lessons/registry.js.
// Copy is verbatim from docs/TUTORIALS_SPEC.md; no em-dashes in any of it.

export const FLOOR_ID = 'floor-v1';

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

// ── Hooks ────────────────────────────────────────────────────────────────────
function surfaceFront({ dom }) { return dom.modalFront('__floor'); }

async function openSurface() {
  if (typeof window.openFloor === 'function') await window.openFloor();
}

function resolveStep({ P, step: st, dom }) {
  if (st.id === 'open-chat') {
    const fig = dom.only('#floor-body .fl-room .fl-fig');
    if (!fig) return { error: 'missing' };
    if (fig.dup) return { error: 'duplicate' };
    if (fig.dataset.flSession !== P.SID) return { error: 'missing' };
    return { el: fig, cue: fig.querySelector('.fl-cta') || fig };
  }
  if (st.id === 'open-type') {
    const main = dom.only(`#floor-body .fl-bench-card[data-fl-type="${P.GUIDE_REF}"] .fl-bench-main`);
    if (!main) return { error: 'missing' };
    if (main.dup) return { error: 'duplicate' };
    return { el: main.closest('.fl-bench-card') || main, cue: main };
  }
  const card = dom.only(`#floor-body .fl-bench-card.fl-draggable[data-fl-type="${P.GUIDE_REF}"]`);
  if (!card) return { error: 'missing' };
  if (card.dup) return { error: 'duplicate' };
  const destSel = dom.isMobile()
    ? `#projects-col .mc-chat-row[data-id="${P.PID}"]`
    : `#projects-col .card[data-id="${P.PID}"]`;
  const tile = dom.q(destSel);
  // Desktop: the tile is usually half under the Floor window; the drag lands on the
  // part that is showing. Phone: the row is behind the full-screen Floor, so null.
  const ex = tile && dom.measurable(tile) ? _exposedOf(tile) : null;
  return { el: card, cue: card, dest: ex ? _exposedTarget(tile, ex) : null };
}

// Preparation may navigate and load fixtures. It never performs the action.
async function prepare(ctx) {
  const { P, step: st, dom } = ctx;
  if (st.id === 'open-chat') {
    // The chat must be opened by the user DURING this step, so a leftover open
    // practice chat from an earlier pass is closed first.
    if (typeof openModals !== 'undefined' && openModals.has(P.PID) && typeof closeModalById === 'function') closeModalById(P.PID);
    await openSurface();
  } else if (typeof window.floorClosePicker === 'function') {
    window.floorClosePicker();
  }
  if (st.id === 'hire') {
    if (!surfaceFront(ctx)) await openSurface();
    _exposeDestination(P, dom);
  }
}

// Spec, step 3 desktop: put the real Floor and the dashboard where the practice
// tile is visible BEFORE the user grabs anything (never while a pointer is
// held, and only the Floor window moves). When the viewport cannot show both,
// the window stays put and the "Hire to practice" control is the path.
function _exposeDestination(P, dom) {
  if (dom.isMobile() || document.body.classList.contains('hire-active')) return;
  const tile = dom.q(`#projects-col .card[data-id="${P.PID}"]`);
  const e = (typeof openModals !== 'undefined') ? openModals.get('__floor') : null;
  if (!tile || !e || e.minimized) return;
  const win = e.element, content = win.querySelector('.modal-content') || win;
  const t = tile.getBoundingClientRect(), w = content.getBoundingClientRect();
  if (t.right <= w.left || t.left >= w.right || t.bottom <= w.top || t.top >= w.bottom) return;  // already clear
  const left = Math.round(t.right + 16);
  if (left + w.width > window.innerWidth - 8) return;                                              // no room: fallback control
  win.style.left = left + 'px';
}

// Authoritative evidence for the active step, or null. The engine has already
// checked that practice is active and belongs to this run.
function verify({ S, P, step: st, dom }) {
  const acted = (kind) => S.actions.some((a) => a.kind === kind && a.at >= S.stepStartedMs);
  if (st.id === 'open-chat') {
    if (!acted('open-figure')) return null;
    const out = document.getElementById('agent-output-' + P.SID);
    const win = typeof openModals !== 'undefined' ? openModals.get(P.PID) : null;
    if (!out || !win || win.minimized || !dom.measurable(out)) return null;
    if (!(out.textContent || '').includes(P.TRANSCRIPT)) return null;
    return { ok: true, evidence: { project: P.PID, session: P.SID } };
  }
  if (st.id === 'open-type') {
    if (!acted('toggle-type')) return null;
    const card = dom.q(`#floor-body .fl-bench-card[data-fl-type="${P.GUIDE_REF}"]`);
    if (!card || !card.classList.contains('fl-bench-open')) return null;
    const row = card.querySelector('.fl-pick-row');
    if (!row || !dom.measurable(row)) return null;
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

// A failed practice hire keeps the step and shows the real error with Retry.
function watchTick({ S, step: st, renderBubble }) {
  if (!st.hire) return;
  const at = window._floorLastHireErrorAt || 0;
  if (at >= S.stepStartedMs && window._floorLastHireError && !S.hirePending) {
    if (!S.hireFailed || S.error !== window._floorLastHireError) {
      S.hireFailed = true; S.error = window._floorLastHireError;
      renderBubble(true);
    }
  }
}

// The explicit control for step 3: the same real call the Bench's drag ends in,
// reached without a pointer. It performs the hire; it does not mark the step.
// (The engine owns the pending/failed flags around this call.)
async function hire({ P }) {
  window._floorLastHireError = ''; window._floorLastHireErrorAt = 0;
  if (typeof window.floorHireMenu === 'function') await window.floorHireMenu(P.PID, 'project', 'guide', 'Guide');
}

// Runs on every leave, whichever lesson was active.
function cleanup() {
  window._floorLastHireError = ''; window._floorLastHireErrorAt = 0;
}

// A lesson lives in one window (the Floor); the bubble offers `backLabel` when
// the user leaves it on a step marked `needsSurface`. A step's `fallback` is the
// explicit control for a gesture that needs a pointer (the Floor's hire has its
// own, `hire`): it makes the same real call the gesture ends in.
export const floorLesson = {
  id: FLOOR_ID, version: 1,
  title: 'The Floor',
  subtitle: 'Find a figure, open a chat, hire a type.',
  meta: '3 actions · Practice only',
  chip: 'Start Floor practice',
  offer: 'Meet the Floor in three actions. Want to try?',
  offerOn: 'floor-opened',
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
  surfaceFront, openSurface, resolveStep, prepare, verify, watchTick, hire, cleanup,
};
