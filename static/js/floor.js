// ── The Floor ────────────────────────────────────────────────────────────────
// MC-897 phase 1. docs/AGENT_FLOOR_DESIGN.md.
//
// Rooms are projects, a figure is a SESSION. That distinction is the whole
// point: one character can be working in three projects at once, and the chat
// header pill — which shows one persona for one chat — structurally cannot say
// so. Today the only way to learn what is running across twenty projects is to
// open twenty modals, and a session idle for twenty hours is invisible until
// you happen to look at it.
//
// Read-only, one poll of /api/floor. It starts nothing and stops nothing;
// clicking a figure opens that project's chat on that session.

const FLOOR_MODAL = '__floor';
let floorTimer = null;
let floorQuietOpen = false;

async function openFloor() {
  if (openModals.has(FLOOR_MODAL)) {
    const entry = openModals.get(FLOOR_MODAL);
    if (entry.minimized) restoreModal(FLOOR_MODAL);
    focusModal(FLOOR_MODAL);
    refreshFloor();
    return;
  }

  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = FLOOR_MODAL;
  const content = document.createElement('div');
  content.className = 'modal-content';
  _clampModalSize(content, 980);
  content.innerHTML = `
    <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
      <span style="font-size:16px;font-weight:700;color:var(--text)">The Floor
        <span class="dave-sub" id="floor-counts">loading…</span></span>
      <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
        <button class="btn-header-action" style="padding:5px 12px;font-size:11px;margin-right:6px"
          onclick="refreshFloor()">Refresh</button>
        <button class="modal-minimize" onclick="minimizeModal('${FLOOR_MODAL}')" title="Minimize">&#x2015;</button>
        <button class="modal-close" onclick="closeFloor()" title="Close">&#10005;</button>
      </div>
    </div>
    <div style="padding:4px 24px 20px 28px" id="floor-body"></div>`;
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);

  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(FLOOR_MODAL, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(FLOOR_MODAL);

  await refreshFloor();
}

function closeFloor() {
  // The poll is tied to the window, not to the page. A board nobody is looking
  // at should not keep waking the server every 30 seconds.
  if (floorTimer) { clearInterval(floorTimer); floorTimer = null; }
  closeModalById(FLOOR_MODAL);
}

function _floorDot(state) {
  // Filled = a turn is running, hollow = alive but between turns. `asking`
  // gets its own mark because it is the only state that needs a human, and
  // burying that in a colour would make the board decorative.
  if (state === 'asking') return `<span class="fl-dot fl-asking">!</span>`;
  if (state === 'working') return `<span class="fl-dot fl-working">&#9679;</span>`;
  return `<span class="fl-dot fl-idle">&#9675;</span>`;
}

function _floorLine(f) {
  if (f.state === 'asking') {
    return f.reason === 'plan' ? 'waiting on your plan approval'
                               : 'waiting on your answer';
  }
  if (f.state === 'idle') return 'idle — between turns';
  // `activity` is only non-empty when the server streams partial messages.
  // Falling back to the plain word keeps the row from going blank when the
  // flag is off, rather than implying the session stalled.
  return { thinking: 'thinking…', writing: 'writing…', tool: 'running a tool…' }[f.activity]
         || 'working…';
}

function _floorFigure(pid, f) {
  // NAME and TYPE are two facts, not one. The card used to print "no type"
  // where the name goes, which put the board at odds with that session's own
  // prompt — it says "Your name is Vector" to a figure the board called
  // untyped. The role still shows as "no type"; it just stops standing in for
  // a name it never was.
  const engine = [f.provider, f.model].filter(Boolean).join(' · ');
  const chosen = f.name_from === 'user' || f.name_from === 'self';
  const nameCls = 'fl-who' + (chosen ? ' fl-named' : '');
  const type = f.character
    ? `<span class="fl-type">${esc(f.character.display)}</span>`
    : `<span class="fl-type fl-untyped">no type</span>`;
  const nameTitle = chosen
    ? (f.name_from === 'self' ? 'named itself — click to change' : 'you named this — click to change')
    : 'click to name this figure';
  return `<div class="fl-fig fl-${esc(f.state)}"
      onclick="floorOpenFigure('${esc(pid)}','${esc(f.claude_session_id)}','${esc(f.session_id)}')"
      title="${esc(f.task || '')}">
    <div class="fl-fig-top">${_floorDot(f.state)}<span class="${nameCls}"
        title="${esc(nameTitle)}"
        onclick="event.stopPropagation();floorRename('${esc(f.session_id)}','${esc(f.name || '')}')"
      >${esc(f.name || 'unnamed')}</span>${type}
      <span class="fl-age">${esc(f.age || '')}</span></div>
    <div class="fl-engine">${esc(engine)}</div>
    <div class="fl-act">${esc(_floorLine(f))}</div>
    <div class="fl-task">${esc(f.task || '')}</div>
  </div>`;
}

async function floorRename(sessionId, current) {
  // `prompt` rather than an inline editor: renaming is rare, and a text input
  // living inside a card that repaints every 30s loses what you typed.
  const next = window.prompt('Name this figure (blank clears it):', current || '');
  if (next === null) return;
  try {
    const res = await fetch(API_BASE + '/api/floor/figure/' + encodeURIComponent(sessionId) + '/name', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: next })
    });
    if (!res.ok) throw new Error(await res.text());
    await refreshFloor();
  } catch (e) {
    alert('Could not rename: ' + e.message);
  }
}

function _floorRoom(r) {
  return `<div class="fl-room">
    <div class="fl-room-head" onclick="openProjectModal('${esc(r.id)}')">
      ${r.emoji ? esc(r.emoji) + ' ' : ''}${esc(r.name)}
      <span class="fl-room-n">${r.figures.length}</span>
    </div>
    <div class="fl-figs">${r.figures.map(f => _floorFigure(r.id, f)).join('')}</div>
  </div>`;
}

function _floorQuiet(quiet) {
  if (!quiet.length) return '';
  const names = quiet.map(q =>
    `<span class="fl-quiet-item" onclick="openProjectModal('${esc(q.id)}')">${esc(q.name)}</span>`
  ).join('');
  return `<div class="fl-quiet">
    <div class="fl-quiet-head" onclick="floorToggleQuiet()">
      ${floorQuietOpen ? '&#9662;' : '&#9656;'} ${quiet.length} quiet project${quiet.length === 1 ? '' : 's'}
    </div>
    <div class="fl-quiet-list" ${floorQuietOpen ? '' : 'hidden'}>${names}</div>
  </div>`;
}

function _floorBench(bench) {
  if (!bench.length) return '';
  const cards = bench.map(b => `<div class="fl-bench-card">
      <span class="fl-who">${esc(b.display)}</span>
      <span class="fl-engine">${esc([b.provider, b.model, b.effort].filter(Boolean).join(' · ')) || '&mdash;'}</span>
      <span class="fl-bench-desc">${esc(b.description || '')}</span>
    </div>`).join('');
  return `<div class="fl-bench">
    <div class="fl-section-head">Bench <span class="dave-sub">hired types with nothing running</span></div>
    <div class="fl-bench-list">${cards}</div>
  </div>`;
}

function floorToggleQuiet() {
  floorQuietOpen = !floorQuietOpen;
  refreshFloor();
}

function floorOpenFigure(pid, csid, mcSessionId) {
  // Hierarchy is for delegation, not for inspection (DAVE_DESIGN §8): a figure
  // is always directly reachable, never only through whoever spawned it.
  openProjectModal(pid);
  setTimeout(() => {
    if (window.openConversation) window.openConversation(pid, csid, mcSessionId, true);
  }, 500);
}

async function refreshFloor() {
  const body = document.getElementById('floor-body');
  if (!body) return;
  let d;
  try {
    const res = await fetch(API_BASE + '/api/floor');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    d = await res.json();
  } catch (e) {
    body.innerHTML = `<div class="dave-empty">Could not read the floor: ${esc(e.message)}</div>`;
    return;
  }

  const c = d.counts || {};
  const counts = document.getElementById('floor-counts');
  if (counts) {
    counts.textContent = `${c.figures || 0} live · ${c.rooms || 0} room${c.rooms === 1 ? '' : 's'}`
      + ` · ${c.quiet || 0} quiet`;
  }

  const rooms = (d.rooms || []).map(_floorRoom).join('');
  const empty = !(d.rooms || []).length
    ? `<div class="dave-empty">Nothing is running right now. Every project is quiet.</div>` : '';
  // Said once, not guessed per card: with partial-message streaming off there
  // is no thinking/writing signal at all, and a row reading "working…" for a
  // whole turn should be explained rather than look broken.
  const note = d.activity_states === false
    ? `<div class="memory-hint" style="margin-top:10px">Live thinking/writing states are off
       (<code>activity_states_enabled</code>), so a running figure just reads "working".</div>` : '';

  body.innerHTML = `<div class="fl-rooms">${rooms}</div>${empty}
    ${_floorQuiet(d.quiet || [])}${_floorBench(d.bench || [])}${note}`;

  if (!floorTimer) {
    const secs = Math.max(10, parseInt(d.poll_seconds, 10) || 30);
    floorTimer = setInterval(() => {
      // Stop polling if the window went away by any route (Escape, the modal
      // manager's own close) rather than only through closeFloor().
      if (!openModals.has(FLOOR_MODAL)) {
        clearInterval(floorTimer); floorTimer = null; return;
      }
      const e = openModals.get(FLOOR_MODAL);
      if (e && e.minimized) return;   // minimized: alive, but not worth a poll
      refreshFloor();
    }, secs * 1000);
  }
}

// ── Interop: re-expose for inline / generated-on*= callers. Runtime-only.
//    `openFloor` ← sidebarNav('floor'). The rest ← generated on*= handlers
//    inside the board (refresh button, quiet toggle, figure and room clicks).
window.openFloor = openFloor;
window.closeFloor = closeFloor;
window.refreshFloor = refreshFloor;
window.floorToggleQuiet = floorToggleQuiet;
window.floorOpenFigure = floorOpenFigure;
window.floorRename = floorRename;
