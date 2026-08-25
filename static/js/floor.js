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
  // Wider than the default: this is a cross-project board, not a form. At 980
  // the bench fitted three 72px-figure cards and wrapped the fourth onto a row
  // of its own, which reads as a mistake rather than as a grid.
  _clampModalSize(content, 1120);
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
    <div class="fl-body" id="floor-body"></div>`;
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

// The figure list, fetched once. Small, and it only changes when someone drops
// a new file into assets/avatars/.
let _floorFigCache = null;
async function _floorFigures() {
  if (_floorFigCache) return _floorFigCache;
  try {
    const r = await fetch(API_BASE + '/api/avatars');
    _floorFigCache = (await r.json()).figures || [];
  } catch (e) {
    _floorFigCache = [];
  }
  return _floorFigCache;
}

// A stable hue per name. Not random and not configured: the same type is the
// same colour on every machine and after every restart, which is the only
// property that matters — it is an identity cue, not a palette.
function _floorHue(name) {
  let h = 0;
  for (let i = 0; i < (name || '').length; i++) {
    h = ((h << 5) - h + name.charCodeAt(i)) | 0;
  }
  return Math.abs(h) % 360;
}

// Ron: "on the floor area we can show them in bigger size", then "still too
// small". A figure is a character, not a glyph — the whole reason to have
// artwork is being able to tell who is in the room at a glance.
//
// The size was never the binding constraint: the face sat INLINE in the name
// row, so growing it grew that row's line-height and shoved the name sideways.
// It is a left COLUMN now, which is the shape every chat list uses and the
// reason a 49px WhatsApp avatar never feels cramped.
const FLOOR_FACE_PX = 72;

function _floorAvatar(f) {
  const has = !!(f.avatar || '').trim();
  return `<span class="fl-face" title="${has ? 'click to change the face' : 'click to give this figure a face'}"
      onclick="event.stopPropagation();floorSetAvatar('${esc(f.session_id)}','${esc(f.avatar || '')}')"
    >${window.avatarHTML(f.avatar, FLOOR_FACE_PX)}</span>`;
}

async function floorSetAvatar(sessionId, current) {
  const figs = await _floorFigures();
  const next = window.prompt(
    'A face for this figure — one emoji, or one of:\n\n  '
    + figs.map((n) => 'fig:' + n).join('   ')
    + '\n\n(blank clears it)', current || '');
  if (next === null) return;
  try {
    const res = await fetch(API_BASE + '/api/floor/figure/' + encodeURIComponent(sessionId) + '/name', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      // `avatar` only — the server leaves an absent field alone, so setting a
      // face must not clear a name.
      body: JSON.stringify({ avatar: next })
    });
    if (!res.ok) throw new Error(await res.text());
    await refreshFloor();
  } catch (e) {
    alert('Could not set the face: ' + e.message);
  }
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
  // The pencil rides with the TYPE, wherever the type appears. It used to live
  // only on bench cards, and a busy type was not on the bench — so the moment
  // an agent started working you lost the only way to edit it.
  const type = f.character
    ? `<span class="fl-type">${esc(f.character.display)}</span><button class="fl-edit"
        title="Edit this persona — face, description, instructions, engine"
        onclick="event.stopPropagation();floorEditType('${esc(f.character.scope || 'global')}','${esc(f.character.name)}')"
        >&#9998;</button>`
    : `<span class="fl-type fl-untyped">no type</span>`;
  const nameTitle = chosen
    ? (f.name_from === 'self' ? 'named itself — click to change' : 'you named this — click to change')
    : 'click to name this figure';
  // The state is the most important thing on this card, and it was carried by
  // a single 13px dot — a figure mid-turn looked identical to one that had been
  // idle for twenty hours. It owns the edge, the tint and a word now.
  const stateWord = { asking: 'needs you', working: 'working', idle: 'idle' }[f.state] || f.state;
  return `<div class="fl-fig fl-${esc(f.state)}"
      onclick="floorOpenFigure('${esc(pid)}','${esc(f.claude_session_id)}','${esc(f.session_id)}')"
      title="${esc(f.task || '')}">
    ${_floorAvatar(f)}
    <div class="fl-fig-body">
      <div class="fl-fig-top">${_floorDot(f.state)}<span class="${nameCls}"
          title="${esc(nameTitle)}"
          onclick="event.stopPropagation();floorRename('${esc(f.session_id)}','${esc(f.name || '')}')"
        >${esc(f.name || 'unnamed')}</span>${type}
        <span class="fl-state">${esc(stateWord)}</span>
        <span class="fl-age">${esc(f.age || '')}</span></div>
      <div class="fl-engine">${esc(engine)}</div>
      <div class="fl-act">${esc(_floorLine(f))}</div>
      <div class="fl-task">${esc(f.task || '')}</div>
      <div class="fl-cta">Open this chat &#8594;</div>
    </div>
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
  // The project's own colour on the left edge. `modal_color` is the identity
  // cue the project modal already uses, the endpoint has always sent it, and
  // the board was throwing it away — which is most of why twelve rooms read as
  // one grey list.
  const tint = r.color ? ` style="border-left-color:${esc(r.color)}"` : '';
  const needs = r.figures.some(f => f.state === 'asking');
  return `<div class="fl-room${needs ? ' fl-room-needs' : ''}"${tint}>
    <div class="fl-room-head">
      <span class="fl-room-swatch"${r.color ? ` style="background:${esc(r.color)}"` : ''}></span>
      <span class="fl-room-name" onclick="openProjectModal('${esc(r.id)}')"
        >${r.emoji ? esc(r.emoji) + ' ' : ''}${esc(r.name)}</span>
      <span class="fl-room-n">${r.figures.length} here</span>
      ${needs ? '<span class="fl-room-flag">needs you</span>' : ''}
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
      ${floorQuietOpen ? '&#9662;' : '&#9656;'} ${quiet.length} project${quiet.length === 1 ? '' : 's'}
      with nobody in ${quiet.length === 1 ? 'it' : 'them'}
    </div>
    <div class="fl-quiet-list${floorQuietOpen ? '' : ' fl-collapsed'}">${names}</div>
  </div>`;
}

// Which bench card has its room picker open, by character name. Only ever one:
// two open pickers is two half-made decisions on screen.
let floorPickerFor = null;

function _floorBench(bench, rooms, quiet) {
  const hire = `<button class="fl-hire" onclick="floorHire()"
      title="Describe a new agent type with Claydo, then save it">&#43; Hire someone new</button>`;
  const head = `<div class="fl-section-head">Bench
      <span class="dave-sub">everyone you have hired &mdash; click one to put it in a room</span>
      ${hire}</div>`;
  if (!bench.length) {
    return `<div class="fl-bench">${head}
      <div class="dave-empty">You have not hired anyone yet.</div></div>`;
  }
  const cards = bench.map(b => {
    const open = floorPickerFor === b.name;
    const eng = [b.provider, b.model, b.effort].filter(Boolean).join(' · ');
    const hue = _floorHue(b.name || b.display || '');
    const tint = open ? '' : ` style="border-left-color:hsl(${hue} 55% 62%)"`;
    return `<div class="fl-bench-card${open ? ' fl-bench-open' : ''}"${tint}>
      <div class="fl-bench-main" onclick="floorTogglePicker('${esc(b.name)}')">
        <span class="fl-face fl-face-bench">${window.avatarHTML(b.avatar, FLOOR_FACE_PX)}</span>
        <span class="fl-bench-top"><span class="fl-who">${esc(b.display)}</span>
          ${b.display === b.name ? '' : `<span class="fl-type">${esc(b.name)}</span>`}
          <button class="fl-edit" title="Edit this persona — face, description, instructions, engine"
            onclick="event.stopPropagation();floorEditType('${esc(b.scope || 'global')}','${esc(b.name)}')"
            >&#9998;</button></span>
        <span class="fl-bench-desc">${esc(b.description || 'no description — nothing tells an agent when to use this one')}</span>
        ${(b.rooms || []).length
          ? `<span class="fl-busy" title="Already working there. You can still put it in another room — one type runs in as many projects as you like.">already in ${
              b.rooms.map(esc).join(', ')}</span>`
          : ''}
        ${(b.skills || []).length
          ? `<span class="fl-skills">${(b.skills || []).slice(0, 4)
              .map((k) => `<span class="fl-skill">${esc(k)}</span>`).join('')}${
              (b.skills || []).length > 4 ? `<span class="fl-skill fl-skill-more">+${b.skills.length - 4}</span>` : ''}</span>`
          : ''}
        <span class="fl-bench-foot">
          <span class="fl-engine">${esc(eng) || 'follows the project default'}</span>
          <span class="fl-cta">${open ? 'Pick a room &#8595;'
            : ((b.rooms || []).length ? 'Put in another room &#8594;' : 'Put in a room &#8594;')}</span>
        </span>
      </div>
      ${open ? _floorRoomPicker(b, rooms, quiet) : ''}
    </div>`;
  }).join('');
  return `<div class="fl-bench">${head}
    <div class="fl-bench-list">${cards}</div>
  </div>`;
}

function _floorRoomPicker(b, rooms, quiet) {
  // Busy rooms first: putting a second agent somewhere already active is the
  // more common intent than waking a project that has been quiet for a week.
  const all = (rooms || []).concat(quiet || []);
  if (!all.length) return '<div class="fl-pick-empty">No projects.</div>';
  const inAlready = new Set(b.rooms || []);
  const items = all.map(r =>
    `<span class="fl-pick${inAlready.has(r.name) ? ' fl-pick-again' : ''}"
      title="${inAlready.has(r.name)
        ? esc(b.display) + ' is already working here — this starts a SECOND one'
        : 'Start ' + esc(b.display) + ' here'}"
      onclick="floorPlace('${esc(b.scope || 'global')}','${esc(b.name)}','${esc(b.display)}','${esc(r.id)}')"
      >${esc(r.name)}${inAlready.has(r.name) ? ' &#183; again' : ''}</span>`).join('');
  return `<div class="fl-pick-row"><span class="fl-pick-label">into&hellip;</span>${items}</div>`;
}

function floorTogglePicker(name) {
  floorPickerFor = floorPickerFor === name ? null : name;
  refreshFloor();
}

function floorPlace(scope, name, display, projectId) {
  // Lands on the +NEW CHAT screen, not on whatever chat happened to be open.
  // `setComposerCharacter` sets the persona for the next dispatch, so pointing
  // at an existing conversation showed a toast about a screen Ron was not
  // looking at — he could not tell whether his open chat had just changed
  // personality. Now the persona row is on screen with the choice in it.
  //
  // Still does NOT dispatch: a bench click knows WHO but not WHAT, and
  // inventing a task to make the button feel decisive is how an agent ends up
  // doing something nobody asked for.
  floorPickerFor = null;
  openProjectModal(projectId);
  setTimeout(() => {
    if (typeof window.newAgentTab === 'function') window.newAgentTab(projectId);
    if (typeof window.setComposerCharacter === 'function') {
      window.setComposerCharacter(projectId, scope + ':' + name);
    }
    if (window.showToast) {
      showToast('New chat with ' + display + ' — type what you want done.', 4000);
    }
  }, 500);
}

function floorEditType(scope, name) {
  // The same editor the composer's pencil opens — one persona editor, not a
  // Floor-flavoured second one. No project id: a global type belongs to no
  // room, and the editor guards that path.
  if (typeof window.openPersonaEditor !== 'function') return;
  window.openPersonaEditor(null, scope, name, () => refreshFloor());
}

function floorHire() {
  // Claydo's character mode, not a second builder. Two creation flows would
  // disagree about what a character is within a week.
  const toChar = () => {
    if (typeof window.setClaydoMode === 'function') window.setClaydoMode('character');
  };
  try {
    if (typeof window.openClaydo === 'function') {
      Promise.resolve(window.openClaydo()).then(toChar).catch(toChar);
    } else {
      toChar();
    }
  } catch (e) { /* the button is a shortcut, never the only route */ }
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
    ? `<div class="fl-empty">
         <div class="fl-empty-mark">&#9675;</div>
         <div class="fl-empty-head">Nobody is working right now</div>
         <div class="fl-empty-sub">Pick someone off the bench below and put them in a room.</div>
       </div>` : '';
  // Said once, not guessed per card: with partial-message streaming off there
  // is no thinking/writing signal at all, and a row reading "working…" for a
  // whole turn should be explained rather than look broken.
  const note = d.activity_states === false
    ? `<div class="memory-hint" style="margin-top:10px">Live thinking/writing states are off
       (<code>activity_states_enabled</code>), so a running figure just reads "working".</div>` : '';

  // Order is the priority order: who is working, then who you could put to
  // work, then — last — the projects with nobody in them. Quiet sat in the
  // middle and the eye hit a collapsed grey count on its way to the bench.
  body.innerHTML = `<div class="fl-rooms">${rooms}</div>${empty}
    ${_floorBench(d.bench || [], d.rooms || [], d.quiet || [])}
    ${_floorQuiet(d.quiet || [])}${note}`;

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
window.floorSetAvatar = floorSetAvatar;
window.floorTogglePicker = floorTogglePicker;
window.floorPlace = floorPlace;
window.floorHire = floorHire;
window.floorEditType = floorEditType;
