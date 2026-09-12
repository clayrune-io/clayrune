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

// `window.avatarHTML` is a cross-module global (render-core.js) — the same
// exposure as `esc`, `openModals`, `_clampModalSize`. mention-autocomplete.js
// already guards this call with a `typeof` check; the Floor didn't, so a
// module-load-order race (or a future rename) throws mid-render and freezes
// the whole board (ws001 Finding 1). Guard here the way floor.js's own
// _floorEngine() guards window._providerModelChoices.
function _floorAvatarHTML(value, size) {
  return (typeof window.avatarHTML === 'function')
    ? window.avatarHTML(value, size) : '';
}

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

function _floorLine(f, helperActive) {
  if (f.state === 'asking') {
    return f.reason === 'plan' ? 'waiting on your plan approval'
                               : 'waiting on your answer';
  }
  // Ron: a figure between turns with a live helper reads as "nothing is
  // happening on the project's floor". It IS between its own turns — that
  // part of `idle` is true — but the room is not quiet, so the second line
  // has to say what actually is: the helper, not the parent, is running.
  if (f.state === 'idle') return helperActive ? 'its helper is working' : 'idle — between turns';
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

// Model-provider mark, pinned to the face's own corner (Ron, 2026-09-09: "the
// logo of their model provider next to them ... easy to identify which is
// what"). Deliberately NOT render-core.js's `_providerBadge` — that one hides
// claude as the assumed default for a usage-breakdown table; here every
// figure's engine is the point, claude included. `ic-prov-<name>` symbols
// live in index.html; a provider with no symbol renders no badge at all
// rather than a guessed or blank one.
const FLOOR_PROV_ICONS = new Set(['claude', 'gemini', 'codex', 'opencode', 'goose', 'aider', 'kiro']);
function _floorProviderBadge(provider) {
  const p = (provider || '').toLowerCase();
  if (!FLOOR_PROV_ICONS.has(p)) return '';
  return `<span class="fl-prov-badge prov-${p}" title="${esc(p)}"><svg><use href="#ic-prov-${p}"/></svg></span>`;
}

function _floorAvatar(f) {
  const has = !!(f.avatar || '').trim();
  return `<span class="fl-face" title="${has ? 'click to change the face' : 'click to give this figure a face'}"
      onclick="event.stopPropagation();floorSetAvatar('${esc(f.session_id)}','${esc(f.avatar || '')}')"
    >${_floorAvatarHTML(f.avatar, FLOOR_FACE_PX)}${_floorProviderBadge(f.provider)}</span>`;
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

// Engine label for a card. Reuses the model picker's friendly names
// (the live provider catalog) so the Floor stops being the one
// surface that prints raw ids — "claude · claude-opus-5" where the chat header
// and the persona picker both say "Opus 5" (Ron, 2026-09-01).
//
// `from` is the server's model_from: 'own' when the session was dispatched
// with this model, 'project' when it is only inheriting the project default.
// Those are different facts and the board used to render them identically.
function _floorEngine(provider, model, effort, from) {
  // _providerModelChoices covers every provider (it reads the live provider
  // catalog), where _engShortLabel only knows the Claude list — a gemini
  // figure would otherwise still print its raw id here.
  let label = model;
  if (model && typeof window._providerModelChoices === 'function') {
    const hit = (window._providerModelChoices(provider || 'claude') || [])
      .find((x) => x[0] === model);
    if (hit) label = hit[1];
  }
  const bits = [];
  if (provider) bits.push(provider);
  if (label) bits.push(label);
  if (effort) bits.push('effort ' + effort);
  if (!bits.length) return '';
  return bits.join(' · ') + (from === 'project' ? ' (project default)' : '');
}

function _floorFigure(pid, f) {
  // NAME and TYPE are two facts, not one. The card used to print "no type"
  // where the name goes, which put the board at odds with that session's own
  // prompt — it says "Your name is Vector" to a figure the board called
  // untyped. The role still shows as "no type"; it just stops standing in for
  // a name it never was.
  const engine = _floorEngine(f.provider, f.model, '', f.model_from);
  const chosen = f.name_from === 'user' || f.name_from === 'self';
  const nameCls = 'fl-who' + (chosen ? ' fl-named' : '');
  // The pencil rides with the TYPE, wherever the type appears. It used to live
  // only on bench cards, and a busy type was not on the bench — so the moment
  // an agent started working you lost the only way to edit it.
  const type = f.character
    ? `<span class="fl-type">${esc(f.character.display)}</span><button class="fl-edit"
        title="Edit this persona — face, description, instructions, engine"
        onclick="event.stopPropagation();floorEditType('${esc(f.character.scope || 'global')}','${esc(f.character.name)}','${esc(pid)}')"
        >&#9998;</button><button class="fl-hire-to"
        title="Hire ${esc(f.character.display)} onto a project — the no-drag path (docs/DRAG_TO_HIRE_SPEC.md §8)"
        onclick="event.stopPropagation();floorHireMenu('${esc(pid)}','${esc(f.character.scope || 'global')}','${esc(f.character.name)}','${esc(f.character.display)}')"
        >&#8981;</button>`
    : `<span class="fl-type fl-untyped">no type</span>`;
  const nameTitle = chosen
    ? (f.name_from === 'self' ? 'named itself — click to change' : 'you named this — click to change')
    : 'click to name this figure';
  // "Is anyone working right now?" was asked six times on a day helpers WERE
  // out (2026-09-02) because the Floor carried f.subagents (6a5650a) but no
  // card ever read it — the same "+N" convention the Channel roster row
  // already uses for the identical fact, reused here so the two surfaces
  // agree rather than inventing a second visual language for one concept.
  const runningHelpers = (f.subagents || []).filter(s => s.running).length;
  // Ron, 2026-09-08 (screenshot agent_b817326e56.png): Dave's card read IDLE
  // with the grey inactive treatment while a helper was mid-run under him —
  // "seems like nothing happening on the project's floor". `idle` is true
  // about the parent's own turn and false about the room. ONLY a genuinely
  // idle parent (no running helper) gets remapped — a figure with zero
  // helpers must never light up, or the board stops being honest.
  const helperActive = f.state === 'idle' && runningHelpers > 0;
  // The visual state — dot, edge colour, tint, pill — reuses the `working`
  // treatment wholesale rather than inventing a third visual language. The
  // WORD stays distinct ("helper working", not "working") because it is a
  // different, still-true fact: the parent is between turns, its helper isn't.
  const visualState = helperActive ? 'working' : f.state;
  const stateWord = helperActive
    ? 'helper working'
    : ({ asking: 'needs you', working: 'working', idle: 'idle' }[f.state] || f.state);
  const helpers = runningHelpers > 0
    ? `<span class="conv-helpers" title="${runningHelpers} helper${runningHelpers !== 1 ? 's' : ''} working">+${runningHelpers}</span>`
    : '';
  // Drag-to-hire (docs/DRAG_TO_HIRE_SPEC.md §7/§8) only starts from a figure
  // that carries a character — a characterless session has nothing to hire
  // (spec §11). No pointerdown handler at all for those, so a plain Vector
  // run behaves exactly as before: click opens the chat, nothing else.
  const dragAttrs = f.character
    ? ` onpointerdown="floorFigDown(event,'${esc(pid)}','${esc(f.character.scope || 'global')}','${esc(f.character.name)}','${esc(f.character.display)}','${esc(f.avatar || '')}')"`
    : '';
  return `<div class="fl-fig fl-${esc(visualState)}${f.character ? ' fl-draggable' : ''}"${dragAttrs}
      onclick="floorOpenFigure('${esc(pid)}','${esc(f.claude_session_id)}','${esc(f.session_id)}')"
      title="${esc(f.task || '')}">
    ${_floorAvatar(f)}
    <div class="fl-fig-body">
      <div class="fl-fig-top">${_floorDot(visualState)}<span class="${nameCls}"
          title="${esc(nameTitle)}"
          onclick="event.stopPropagation();floorRename('${esc(f.session_id)}','${esc(f.name || '')}')"
        >${esc(f.name || 'unnamed')}</span>${type}
        <span class="fl-state">${esc(stateWord)}</span>${helpers}
        <span class="fl-age">${esc(f.age || '')}</span></div>
      <div class="fl-engine">${esc(engine)}</div>
      <div class="fl-act">${esc(_floorLine(f, helperActive))}</div>
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

// Which bench card has its room picker open, keyed "scope:project:name". Not
// the name alone — two projects may each hire a `researcher`, and one click
// would open both cards' pickers. Only ever one open: two is two half-made
// decisions on screen.
let floorPickerFor = null;

function _floorBench(bench, rooms, quiet) {
  const hire = `<button class="fl-hire" onclick="floorHire()"
      title="Describe a new agent type with Claydo, then save it">&#43; Hire someone new</button>`;
  const head = `<div class="fl-section-head">Bench
      <span class="dave-sub">everyone you have hired, global and per-project &mdash; click one to put it in a room</span>
      ${hire}</div>`;
  if (!bench.length) {
    return `<div class="fl-bench">${head}
      <div class="dave-empty">You have not hired anyone yet.</div></div>`;
  }
  const cards = bench.map(b => {
    const key = (b.scope || 'global') + ':' + (b.project_id || '') + ':' + b.name;
    const open = floorPickerFor === key;
    const eng = _floorEngine(b.provider, b.model, b.effort, '');
    const hue = _floorHue(b.name || b.display || '');
    const tint = open ? '' : ` style="border-left-color:hsl(${hue} 55% 62%)"`;
    // The Bench is the natural drag SOURCE for hiring — it is the list of
    // people you could put to work, whereas a room figure is already working.
    // Shipping drag on figures only (spec §7/§8 reads "figure") left the real
    // Floor with nothing useful to drag: the sole figure with a character is
    // usually the project's own agent, already hired here (Ron, 2026-09-09).
    // `pid` is '' — a bench card belongs to no project until it is dropped on
    // one; floorFigDown only uses pid for the already-hired-here check.
    const bDrag = ` onpointerdown="floorFigDown(event,'','${esc(b.scope || 'global')}','${esc(b.name)}','${esc(b.display)}','${esc(b.avatar || '')}')"`;
    return `<div class="fl-bench-card fl-draggable${open ? ' fl-bench-open' : ''}"${tint}${bDrag}>
      <div class="fl-bench-main" onclick="floorTogglePicker('${esc(key)}')">
        <span class="fl-face fl-face-bench">${_floorAvatarHTML(b.avatar, FLOOR_FACE_PX)}${_floorProviderBadge(b.provider)}</span>
        <span class="fl-bench-top"><span class="fl-who">${esc(b.display)}</span>
          ${b.display === b.name ? '' : `<span class="fl-type">${esc(b.name)}</span>`}
          <button class="fl-edit" title="Edit this persona — face, description, instructions, engine"
            onclick="event.stopPropagation();floorEditType('${esc(b.scope || 'global')}','${esc(b.name)}','${esc(b.project_id || '')}')"
            >&#9998;</button></span>
        <span class="fl-bench-desc">${esc(b.description || 'no description — nothing tells an agent when to use this one')}</span>
        ${b.project_name
          ? `<span class="fl-owned" title="A project persona. It lives in this project and can only work here — the global ones go anywhere.">only in ${esc(b.project_name)}</span>`
          : ''}
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
  let all = (rooms || []).concat(quiet || []);
  // A project persona is only dispatchable in its own project. Offering the
  // other nineteen rooms would be nineteen clicks that cannot be honoured.
  if (b.project_id) all = all.filter((r) => r.id === b.project_id);
  if (!all.length) {
    return `<div class="fl-pick-empty">${b.project_name
      ? esc(b.project_name) + ' is not on the board right now.'
      : 'No projects.'}</div>`;
  }
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

function floorTogglePicker(key) {
  floorPickerFor = floorPickerFor === key ? null : key;
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

function floorEditType(scope, name, projectId) {
  // The same editor the composer's pencil opens — one persona editor, not a
  // Floor-flavoured second one.
  //
  // The project id is NOT optional for a project-scoped persona: the editor
  // fetches /api/characters/project/<name>, and that route 400s without a
  // project_id because a project persona's identity is (project, name), not
  // name. This used to pass a hard `null` on the reasoning that "a global type
  // belongs to no room" — true, and irrelevant the day project personas
  // reached the bench: every pencil on a project card opened an alert instead
  // of an editor. A global type still passes nothing, which is correct.
  if (typeof window.openPersonaEditor !== 'function') return;
  window.openPersonaEditor(projectId || null, scope, name, () => refreshFloor());
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
  // A mouse click still fires on the fl-fig div after a real drag-release —
  // preventDefault on pointermove does not suppress it. `_lastHireDragEnd` is
  // stamped the moment a drag crosses the activation threshold (§9.3's 8px/
  // long-press gate), so "grabbed and dropped" never also reopens the chat.
  if (Date.now() - _lastHireDragEnd < 300) return;
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
    _floorSchedulePoll(5);   // keep asking; one bad fetch must not freeze the board
    return;
  }

  // Everything below THROWS on a bad payload or a broken cross-module global
  // (window.avatarHTML, per figure/bench card). refreshFloor is async and
  // nothing catches its rejection (openFloor:57 awaits it inside an
  // uncaught async fn; the interval callback below calls it bare) — so
  // without this try/finally, one throw here left `floorTimer` set from a
  // PRIOR successful tick but nothing ever scheduling the next one: the
  // board froze permanently (ws001 Finding 1). The invariant this encodes is
  // "refreshFloor always schedules its next poll", not "catch this one
  // throw" — same shape as the fetch-catch above, for the render half.
  try {
    // Build the WHOLE board — header text included — before touching the
    // DOM at all. Previously the counts header was written first and the
    // body last; a throw in between left a TRUTHFUL header ("2 live · 2
    // rooms") over a SILENTLY EMPTY body, indistinguishable from "nobody is
    // working". Computing both first means either the whole render lands or
    // none of it does — a stale-but-honest board, never a fabricated one.
    const c = d.counts || {};
    const countsText = `${c.figures || 0} live · ${c.rooms || 0} room${c.rooms === 1 ? '' : 's'}`
      + ` · ${c.quiet || 0} quiet`;

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
    const bodyHTML = `<div class="fl-rooms">${rooms}</div>${empty}
      ${_floorBench(d.bench || [], d.rooms || [], d.quiet || [])}
      ${_floorQuiet(d.quiet || [])}${note}`;

    const counts = document.getElementById('floor-counts');
    if (counts) counts.textContent = countsText;
    body.innerHTML = bodyHTML;
  } finally {
    _floorSchedulePoll(parseInt(d.poll_seconds, 10) || 5);
  }
}

// Start the board's poll if it is not already running. Split out of
// refreshFloor so the FETCH-ERROR path can reach it too: that path used to
// `return` before this code, so a single failed fetch — a restart, a slow
// load — left the board frozen until it was closed and reopened. That is the
// "sometimes it updates, sometimes it doesn't" behaviour; the payload was
// fine, the board had simply stopped asking.
function _floorSchedulePoll(pollSeconds) {
  if (floorTimer) return;
  // Floor is a live board; the client floor was 10s, which silently overrode
  // any faster server value. Keep a sane lower bound, but let the server ask
  // for a fast tick.
  const secs = Math.max(3, pollSeconds || 5);
  floorTimer = setInterval(() => {
    // Stop polling if the window went away by any route (Escape, the modal
    // manager's own close) rather than only through closeFloor().
    if (!openModals.has(FLOOR_MODAL)) {
      clearInterval(floorTimer); floorTimer = null; return;
    }
    const e = openModals.get(FLOOR_MODAL);
    if (e && e.minimized) return;   // minimized: alive, but not worth a poll
    // A poll mid-drag DESTROYS the drag. refreshFloor rewrites #floor-body
    // wholesale, so the card the pointer is holding is replaced by a new
    // node: its listeners die with it, the capture is lost, the ghost
    // freezes where it stood, and pointerup reaches nothing — so
    // `hire-active` and the ghost stay on screen forever and `_hireDrag`
    // never clears, which makes every LATER drag return early at the
    // `if (_hireDrag)` guard. That is the "agent stuck on the board, nothing
    // else works" failure: it took a drag lasting longer than one 5s tick,
    // which is any drag where Ron pauses to aim. The window-level listeners
    // below now survive the node going away; this skip means it doesn't.
    if (_hireDrag) return;
    // refreshFloor's own try/finally already guarantees the NEXT poll gets
    // scheduled even if this render throws; .catch here only stops that
    // throw from surfacing as an unhandled promise rejection in the console.
    refreshFloor().catch(() => {});
  }, secs * 1000);
}

// ── Drag-to-hire (docs/DRAG_TO_HIRE_SPEC.md) ────────────────────────────────
// Grab a Floor figure that carries a character, drop it on a project tile
// (`#projects-col .card[data-id]`, render-core.js's tileHTML), and that
// character joins the project's `roster` — permanent membership, not just a
// chat. Pointer Events throughout (§8), never HTML5 drag-and-drop: the
// existing OS-file-drop precedents (attDrop/createDrop, render-core.js) are
// the wrong tool for an intra-app gesture — they never fire on touch, which
// would fail the phone requirement (Ron uses Clayrune from his phone)
// structurally rather than just clumsily.
//
// One state object, not per-figure state: only one drag can be in flight at
// a time, and keeping it in a single `_hireDrag` makes "is a drag active"
// and "cancel whatever is active" both a single null-check, no scanning.
let _hireDrag = null;
// Stamped the instant a drag crosses the activation threshold (8px travel
// for mouse, a long-press for touch — §9.3's "no accidental entry" rule).
// floorOpenFigure reads this to swallow the click a mouse-up still fires on
// the same element; see the comment there.
let _lastHireDragEnd = 0;

const HIRE_LONG_PRESS_MS = 400;   // spec §8: "long-press (~400ms)"
const HIRE_DRAG_SLOP_PX = 8;      // spec §9.3: "a real drag (8px pointer travel)"

// The drop target markup differs by layout: desktop renders `.card` tiles
// into #projects-col, mobile (isMobileChatList, <=960px) replaces the whole
// grid with `.mc-chat-row` rows (mobile.js renderMobileChatList) — same
// container, disjoint child class, never both at once. Every hit-test/marker
// query below has to reach whichever one is actually on screen, or a mobile
// drag finds no tile at all: it activates, drags, and every release reads as
// "off every tile" (Ron, mobile, 2026-09-10 — the drop silently did nothing).
const HIRE_TILE_SEL = '#projects-col .card, #projects-col .mc-chat-row';
// Same idea, with an extra class/state appended to EACH branch — string-
// concatenating a suffix onto HIRE_TILE_SEL as a whole would only land it on
// the second selector in the comma list.
function _hireTileSel(suffix) {
  return '#projects-col .card' + suffix + ', #projects-col .mc-chat-row' + suffix;
}

function floorFigDown(e, pid, scope, name, display, avatar) {
  if (typeof e.button === 'number' && e.button !== 0) return;   // left/primary only
  // A second pointer going down mid-drag (a stray second finger) must not
  // start a SECOND drag on top of the first — only one figure can be "picked
  // up" at once, and the first one wins.
  if (_hireDrag) return;
  const el = e.currentTarget;
  const st = {
    pid, scope, name, display, avatar,
    pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
    startX: e.clientX, startY: e.clientY,
    active: false, el, ghost: null, longPressTimer: null,
  };
  _hireDrag = st;
  if (st.pointerType === 'touch') {
    st.longPressTimer = setTimeout(() => {
      if (_hireDrag === st && !st.active) _floorHireActivate(st, st.startX, st.startY);
    }, HIRE_LONG_PRESS_MS);
  }
  // Capture is NOT taken here. Chromium retargets `click` to whichever element
  // holds pointer capture — so grabbing it on every pointerdown silently ate
  // every click on a card's children (the edit pencil, "put in a room", the
  // figure itself) even for a plain click that was never a drag, because the
  // click landed on the capturing card instead of the button underneath it.
  // Capture only starts in _floorHireActivate(), the same moment
  // `.fl-hire-dragging` is added — a plain click never reaches either.
  // On WINDOW, not on `el`. The gesture outlives the node: the Floor re-renders
  // its whole body on every poll, the modal can close, a room can empty — and a
  // listener bound to the card dies with the card, taking the pointerup that
  // would have dropped (or cleaned up) with it. Window sees the release no
  // matter what happened to the thing being dragged.
  window.addEventListener('pointermove', _floorHireMove);
  window.addEventListener('pointerup', _floorHireUp);
  window.addEventListener('pointercancel', _floorHireCancel);
  // Releasing over another app never sends us a pointerup at all.
  window.addEventListener('blur', _floorHireCancel);
}

function _floorHireMove(e) {
  const st = _hireDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  const dx = e.clientX - st.startX, dy = e.clientY - st.startY;
  if (!st.active) {
    // Mouse/pen: 8px of travel is itself the activation gesture. Touch waits
    // for the long-press timer instead — real finger movement before it
    // fires reads as an attempt to scroll the figure list, not a drag, so it
    // cancels the timer rather than activating (§9.3: no accidental entry).
    if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > HIRE_DRAG_SLOP_PX) {
      _floorHireActivate(st, e.clientX, e.clientY);
    } else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > HIRE_DRAG_SLOP_PX * 1.5) {
      clearTimeout(st.longPressTimer);
      _floorHireTeardown(st, false);
    }
    return;
  }
  e.preventDefault();
  if (st.ghost) { st.ghost.style.left = e.clientX + 'px'; st.ghost.style.top = e.clientY + 'px'; }
  _floorHireHoverAt(e.clientX, e.clientY);
}

function _floorHireActivate(st, x, y) {
  st.active = true;
  clearTimeout(st.longPressTimer);
  // Capture belongs here, not at pointerdown (see floorFigDown) — only a real
  // drag needs pointerup/pointermove to keep targeting `el` once the pointer
  // leaves it; a plain click must never be at risk of being retargeted away
  // from the element the user actually clicked.
  try { st.el.setPointerCapture(st.pointerId); } catch (err) { /* best-effort */ }
  if (navigator.vibrate) { try { navigator.vibrate(15); } catch (e) { /* not every device */ } }
  document.body.classList.add('hire-active');
  // Only NOW does the card stop panning (app.css :7715) — before activation
  // the card must stay a plain scrollable list item so a touch that isn't a
  // long-press still scrolls the bench (Ron, mobile, 2026-09-10).
  st.el.classList.add('fl-hire-dragging');
  const ghost = document.createElement('div');
  ghost.className = 'hire-ghost';
  ghost.innerHTML = _floorAvatarHTML(st.avatar, FLOOR_FACE_PX);
  ghost.style.left = x + 'px';
  ghost.style.top = y + 'px';
  document.body.appendChild(ghost);
  st.ghost = ghost;
  // Mark every project tile as a valid target or a visibly dead one, up
  // front — "dead targets look dead before the drop, not after" (§7). A
  // project-scoped character can only ever hire into its own project; a
  // global one can hire into any of them.
  document.querySelectorAll(HIRE_TILE_SEL).forEach((card) => {
    const ok = st.scope !== 'project' || card.dataset.id === st.pid;
    card.classList.toggle('hire-target', ok);
    card.classList.toggle('hire-refused', !ok);
  });
}

function _floorHireHoverAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const card = el && el.closest && el.closest(_hireTileSel('.hire-target'));
  document.querySelectorAll(_hireTileSel('.hire-hover')).forEach((c) => {
    if (c !== card) c.classList.remove('hire-hover');
  });
  if (card) card.classList.add('hire-hover');
}

function _floorHireUp(e) {
  const st = _hireDrag;
  if (!st || e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  if (!st.active) { _floorHireTeardown(st, false); return; }
  const el = document.elementFromPoint(e.clientX, e.clientY);
  const card = el && el.closest && el.closest(HIRE_TILE_SEL);
  const allowed = !!(card && (st.scope !== 'project' || card.dataset.id === st.pid));
  _floorHireTeardown(st, true);
  if (allowed) _hireDrop(card.dataset.id, st);
  // A refused or off-target release writes nothing and opens nothing — Esc
  // and "release outside any tile" are the same cancel per spec §7.
}

function _floorHireCancel(e) {
  const st = _hireDrag;
  if (!st) return;
  // `blur` carries no pointerId, so only a real PointerEvent gets filtered by
  // it — comparing unconditionally made the blur cancel a silent no-op.
  if (e && e.pointerId !== undefined && e.pointerId !== st.pointerId) return;
  clearTimeout(st.longPressTimer);
  _floorHireTeardown(st, st.active);
}

function _floorHireTeardown(st, wasDrag) {
  document.body.classList.remove('hire-active');
  st.el.classList.remove('fl-hire-dragging');
  document.querySelectorAll(_hireTileSel('.hire-target') + ',' + _hireTileSel('.hire-refused') + ',' + _hireTileSel('.hire-hover'))
    .forEach((c) => c.classList.remove('hire-target', 'hire-refused', 'hire-hover'));
  if (st.ghost) { st.ghost.remove(); st.ghost = null; }
  // Belt as well as braces: the ghost is appended to <body>, so a stale one
  // from any path that somehow skipped this teardown would sit on the board
  // until a reload. Sweep by class, not only by handle.
  document.querySelectorAll('.hire-ghost').forEach((g) => g.remove());
  window.removeEventListener('pointermove', _floorHireMove);
  window.removeEventListener('pointerup', _floorHireUp);
  window.removeEventListener('pointercancel', _floorHireCancel);
  window.removeEventListener('blur', _floorHireCancel);
  try { st.el.releasePointerCapture(st.pointerId); } catch (e) { /* already released, or the node is gone */ }
  if (wasDrag) _lastHireDragEnd = Date.now();
  _hireDrag = null;
}

// Esc cancels a drag in progress (§7) — writes nothing, opens nothing,
// restores everything by tearing down the one class + the marker classes.
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape' && _hireDrag) {
    _floorHireTeardown(_hireDrag, _hireDrag.active);
  }
});

// POST the hire, or report why not. Shared by the drag drop and the no-drag
// "Hire to project…" menu (floorHireMenu) — both outcomes in §6 route
// through this one call so they can never disagree about what "hired" means.
async function _hireCharacter(scope, name, projectId, hiredBy) {
  try {
    const res = await fetch(API_BASE + '/api/project/' + encodeURIComponent(projectId) + '/roster/hire', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ character: scope + ':' + name, hired_by: hiredBy || 'drag' }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || ('HTTP ' + res.status));
    // Write the server's roster straight into the local cache instead of
    // waiting on the next Floor poll — _hireOpenChannel fires 500ms later and
    // expands the hired character's Channel row immediately, which needs a
    // matching roster entry to exist (_channelRoster) or the accordion has no
    // row to expand under (it fell back to the "no one's on this channel yet"
    // empty state, the exact gap channel-mode's drag-to-hire smoke test caught
    // once the rail stopped rendering a filtered pane unconditionally).
    if (data.roster) {
      const p = (typeof allProjects !== 'undefined' ? allProjects : []).find((x) => x.id === projectId);
      if (p) p.roster = data.roster;
    }
    return data;
  } catch (e) {
    if (window.showToast) showToast('Could not hire: ' + e.message, 4000);
    return null;
  }
}

function _hireProjectName(projectId) {
  const p = (typeof allProjects !== 'undefined' ? allProjects : []).find((x) => x.id === projectId);
  return p ? (p.name || p.id) : projectId;
}

async function _hireDrop(projectId, st) {
  const data = await _hireCharacter(st.scope, st.name, projectId, 'drag');
  if (!data) return;
  _hireToast(data, st.display, projectId);
  _hireOpenChannel(projectId, st.scope + ':' + st.name);
  if (window.refreshFloor) refreshFloor();
}

// The toast text carries both mitigations from spec §9: symmetry (un-hire is
// one click away, said implicitly by "hired" never reading as irreversible)
// and the surfaced-not-silent threshold — 5+ hired agents on one project
// names the position's own reopen trigger instead of crossing it quietly.
function _hireToast(data, display, projectId) {
  if (!window.showToast) return;
  const projName = _hireProjectName(projectId);
  if (data.already_hired) {
    showToast(display + ' is already on ' + projName + '.', 3000);
    return;
  }
  const n = (data.roster || []).filter((r) => !r.removed_at).length;
  showToast(n >= 5
    ? `Hired ${display} onto ${projName} — that's ${n} agents here, the rail is getting crowded.`
    : `Hired ${display} onto ${projName}.`, 3500);
}

// Post-hire open: the project modal, in Channel mode, with the hired
// character's row selected (spec §10 — depends on Channel mode Phase 1,
// which has shipped). setRailMode/openChannelPerson are conversation.js
// exports; the timeout mirrors floorOpenFigure/floorPlace's own wait for the
// modal to finish mounting before touching its rail.
function _hireOpenChannel(projectId, characterRef) {
  openProjectModal(projectId);
  setTimeout(() => {
    if (typeof window.setRailMode === 'function') window.setRailMode(projectId, 'channel');
    // ARM THE COMPOSER, don't just drill to the row. Landing on the agent's
    // (empty) channel with PERSONA still reading "None" meant the next thing
    // typed went to the project default — the drop looked finished and was one
    // step short. `setComposerCharacter` writes `pendingDispatchCharacter`,
    // which is the value dispatchAgent actually puts in the POST body
    // (`body.character`, resume-preview.js), so this arms the real payload and
    // not merely the select's displayed text. The starter chips only fill the
    // textarea, so they dispatch through the same armed composer.
    if (typeof window.setComposerCharacter === 'function') {
      window.setComposerCharacter(projectId, characterRef);
    }
    // Land on the +New screen so the armed persona is on screen and one keypress
    // from dispatch. openChannelPerson overrides this when the agent already has
    // history here (switchAgentTab clears the New state) — a resumed chat keeps
    // its own spawn persona, and the arming stays for the next new chat.
    if (typeof window.newAgentTab === 'function') window.newAgentTab(projectId);
    if (typeof window.openChannelPerson === 'function') window.openChannelPerson(projectId, characterRef);
  }, 500);
}

// The no-drag path (§8, hard requirement — "the drag is an accelerator,
// never the only door"). Floor has no context-menu component yet, so this is
// a button on the figure card + a plain picker rather than a right-click
// menu; every outcome in §6 is still reachable through it. A project-scoped
// character has exactly one legal target — its own project — so it hires
// straight there with no picker at all.
async function floorHireMenu(currentPid, scope, name, display) {
  let targetPid = currentPid;
  if (scope !== 'project') {
    const projects = (typeof allProjects !== 'undefined' ? allProjects : []).filter((p) =>
      !(typeof isIncognitoProject === 'function' && isIncognitoProject(p)) &&
      !(typeof isStewardWorkspace === 'function' && isStewardWorkspace(p)));
    if (!projects.length) { if (window.showToast) showToast('No projects to hire into.', 3000); return; }
    const listing = projects.map((p, i) => `${i + 1}. ${p.name || p.id}`).join('\n');
    const pick = window.prompt(`Hire ${display} into which project? Type its number.\n\n${listing}`, '');
    if (pick === null) return;
    const idx = parseInt(pick, 10) - 1;
    const chosen = projects[idx]
      || projects.find((p) => (p.name || p.id).toLowerCase() === pick.trim().toLowerCase());
    if (!chosen) { if (window.showToast) showToast('No matching project.', 3000); return; }
    targetPid = chosen.id;
  }
  const data = await _hireCharacter(scope, name, targetPid, 'menu');
  if (!data) return;
  _hireToast(data, display, targetPid);
  _hireOpenChannel(targetPid, scope + ':' + name);
  if (window.refreshFloor) refreshFloor();
}

// ── Interop: re-expose for inline / generated-on*= callers. Runtime-only.
//    `openFloor` ← sidebarNav('floor'). The rest ← generated on*= handlers
//    inside the board (refresh button, quiet toggle, figure and room clicks).
window.openFloor = openFloor;
window.closeFloor = closeFloor;
window.refreshFloor = refreshFloor;
window.floorToggleQuiet = floorToggleQuiet;
window.floorFigDown = floorFigDown;
window.floorHireMenu = floorHireMenu;
window.floorOpenFigure = floorOpenFigure;
window.floorRename = floorRename;
window.floorSetAvatar = floorSetAvatar;
window.floorTogglePicker = floorTogglePicker;
window.floorPlace = floorPlace;
window.floorHire = floorHire;
window.floorEditType = floorEditType;
