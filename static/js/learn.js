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

const LESSON_ID = 'floor-v1';
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
const LESSONS = {
  [LESSON_ID]: {
    id: LESSON_ID, version: 1,
    title: 'The Floor',
    subtitle: 'Find a figure, open a chat, hire a type.',
    meta: '3 actions · Practice only',
    chip: 'Start Floor practice',
    offer: 'Meet the Floor in three actions. Want to try?',
    done: 'You found a figure, opened its chat, and hired a type. Your real projects are untouched.',
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
        emphasis: true,
      },
      {
        id: 'hire', short: 'Hire Guide',
        copy: 'Give Guide a place to work. Drag it onto Learn practice, or use Hire to practice. Hiring adds the type. It does not start a task.',
        ack: '',
        nudge: true, hire: true,
      },
    ],
  },
};

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
    list.push({ t: Date.now(), event, lesson: LESSON_ID, ...(detail || {}) });
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
function _floorFront() {
  const e = (typeof openModals !== 'undefined') ? openModals.get('__floor') : null;
  return !!(e && !e.minimized && e.element.classList.contains('focused'));
}

function _resolveStep() {
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
  const dest = _q(destSel);
  return { el: card, cue: card, dest: dest && _measurable(dest) && _unobscured(dest) ? dest : null };
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
    actions = `<button class="lrn-btn" data-lrn="return-floor">Return to Floor</button>
      <button class="lrn-btn" data-lrn="replay">Replay</button>
      <button class="lrn-btn" data-lrn="hub">Learn</button>`;
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
    if (S.hint) copy += `\n${S.hintLabel}`;
    if (!S.floorWasFrontNow && st.id !== 'open-chat') {
      actions += `<button class="lrn-btn lrn-primary" data-lrn="back-floor">Back to Floor</button>`;
    }
    if (S.retrySave) {
      actions += `<button class="lrn-btn lrn-primary" data-lrn="retry-save">Retry</button>`;
    } else if (st.hire && S.floorWasFrontNow) {
      actions += `<button class="lrn-btn lrn-primary" data-lrn="hire" ${S.hirePending ? 'disabled' : ''}>${S.hireFailed ? 'Retry hire' : 'Hire to practice'}</button>`;
    }
  }
  const foot = (ph === 'completed' || ph === 'paused' || ph === 'restart' || ph === 'unavailable') ? '' : `
    <div class="lrn-foot">
      <button class="lrn-link" data-lrn="pause">Pause</button>
      <button class="lrn-link" data-lrn="hint">Hint</button>
      <button class="lrn-link" data-lrn="leave">Leave practice</button>
      <button class="lrn-link lrn-quiet-toggle" data-lrn="quiet" aria-pressed="${_quiet() ? 'true' : 'false'}">Quiet effects</button>
    </div>`;
  const leaveOnly = (ph === 'paused' || ph === 'restart' || ph === 'unavailable')
    ? `<div class="lrn-foot"><button class="lrn-link" data-lrn="leave">Leave practice</button></div>` : '';
  const head = ph === 'completed'
    ? `<span class="lrn-seal" aria-hidden="true">✓</span>`
    : '';
  return `
    <div class="lrn-head">
      <img class="lrn-claydo" src="/assets/claydo-idle.webp" alt="" draggable="false">
      <span class="lrn-title">${_esc(L.title)}</span>
      <span class="lrn-progress" role="status" aria-live="polite">${done}/${total}</span>
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
    S.floorWasFrontNow, S.tickNow, _quiet(), S.pauseMsg, S.restartMsg].join('|');
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
}

async function _enterStep(idx) {
  if (!S) return;
  const gen = ++_gen;
  _clearTimers();
  S.idx = idx;
  S.phase = 'preparing';
  S.error = ''; S.hint = false; S.hireFailed = false; S.hirePending = false;
  S.stepStartedMs = Date.now();
  S.unresolvedSince = null; S.awaySince = null; S.floorWasFront = false; S.scrolled = false;
  S.nudgesStopped = false; S.tickNow = false; S.pendingEvidence = null;
  _patch(S.lessonId, (r) => { r.step = idx; r.stepStartedAt = S.stepStartedMs; r.status = 'active'; });
  _ev('step-start', { step: _step().id });
  _renderBubble(true);
  try { await _prepare(); } catch (e) { console.warn('[learn] step preparation failed:', e); }
  if (!S || gen !== _gen) return;
  S.phase = 'active';
  _renderBubble(true);
  _tick();
  _tickTimer = setInterval(_tick, TICK_MS);
}

// Preparation may navigate and load fixtures. It never performs the action.
async function _prepare() {
  const st = _step();
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

// Re-resolve, reposition, verify. Runs on a timer so it survives the Floor's
// own poll replacing #floor-body: every pass reacquires the nodes by identity.
function _tick() {
  if (!S || (S.phase !== 'active' && S.phase !== 'unavailable')) return;
  _applyQuietClass();
  const dragging = document.body.classList.contains('pd-drag-active') || document.body.classList.contains('hire-active');
  const st = _step();
  const front = _floorFront();
  S.floorWasFrontNow = front;
  if (front) { S.floorWasFront = true; S.awaySince = null; }

  // Authoritative verification first: a step is earned by state, not by looks.
  // A save that failed waits for the Retry control instead of looping.
  if (S.retrySave) { _renderBubble(); return; }
  const v = _verify(st);
  if (v) { _commit(v); return; }

  if (st.hire) _watchHireFailure();

  if (dragging) { _renderBubble(); return; }   // freeze highlights during a drag

  const res = front ? _resolveStep() : { error: 'away' };
  if (res.el && _measurable(res.el) && _unobscured(res.el)) {
    S.unresolvedSince = null;
    if (S.phase === 'unavailable') { S.phase = 'active'; }
    _scrollOnce(res.el);
    _showOutline(_isMobile() && st.id === 'hire' ? res.el : res.el);
    _showArrow(res.cue || res.el, res.dest || null, false);
    _renderBubble();
    _placeBubble([_rectOf(res.el), res.dest ? _rectOf(res.dest) : null]);
    return;
  }

  _hideDecor();
  _renderBubble();
  _placeBubble([]);
  if (!front) {
    // Away from the Floor: steps 2 and 3 show Back to Floor. Once the user had
    // the Floor in this step and then leaves it, that is a navigation away.
    if (S.floorWasFront) {
      if (!S.awaySince) S.awaySince = Date.now();
      if (Date.now() - S.awaySince > UNRESOLVED_PAUSE_MS) _pause('You left the Floor. Your progress is saved.');
    }
    return;
  }
  // The Floor is in front but the target will not resolve (missing, duplicate,
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
    // One active lesson at a time. (Only one lesson exists in the pilot; this
    // keeps the rule true when more land.)
    if (!window.confirm('Leave the current practice and switch? Your progress is saved.')) return false;
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
  S = null;
  _bubbleSig = '';
  if (_root) { _root.remove(); _root = null; }
  document.body.classList.remove('lrn-practicing');
  if (typeof openModals !== 'undefined' && openModals.has(P.PID) && typeof closeModalById === 'function') closeModalById(P.PID);
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
  else if (act === 'back-floor') { _openFloorSurface(); }
  else if (act === 'hire') _hirePressed();
  else if (act === 'retry-target') { _ev('retry'); _enterStep(S.idx); }
  else if (act === 'retry-save') { const v = S.pendingEvidence; S.retrySave = false; if (v) _commit(v); }
  else if (act === 'restart') _restartPractice();
  else if (act === 'return-floor') { _openFloorSurface(); }
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
document.addEventListener('pointerdown', () => {
  if (!S || !_root) return;
  S.nudgesStopped = true;
  const a = _q('#lrn-arrow', _root);
  if (a) a.classList.remove('lrn-nudge');
}, true);

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

function _hubHTML() {
  const L = LESSONS[LESSON_ID], st = _hubState(LESSON_ID);
  return `
    <div class="lrn-hub">
      <div class="lrn-hub-lede">Practice Clayrune with Claydo. Practice never touches your real projects.</div>
      <div class="lrn-card" data-lesson="${_esc(L.id)}">
        <div class="lrn-card-top"><span class="lrn-card-title">${_esc(L.title)}</span>${st.note ? `<span class="lrn-card-note">${_esc(st.note)}</span>` : ''}</div>
        <div class="lrn-card-sub">${_esc(L.subtitle)}</div>
        <div class="lrn-card-meta">${_esc(L.meta)}</div>
        <button class="btn-add lrn-card-go" onclick="LearnEngine.startFromHub('${_esc(L.id)}')">${_esc(st.label)}</button>
      </div>
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
  hasLesson: (id) => Object.prototype.hasOwnProperty.call(LESSONS, id),
  lessonChip: (id) => (LESSONS[id] ? LESSONS[id].chip : ''),
  get state() { return S ? { lesson: S.lessonId, phase: S.phase, step: S.idx, run: S.runId } : null; },
  progress: () => _rec(LESSON_ID),
};
window.openLearn = openLearn;
window.closeHub = closeHub;
