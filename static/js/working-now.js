// The "N working" chip: who is working right now, without opening the Floor.
//
// Reads GET /api/floor at the server's poll_seconds while someone is working,
// and every 15s while nobody is. The Floor window polls the
// same route while it is open and hands each payload to window.workingNowFeed
// (floor.js), so this module only fetches when nobody else has just done so.
// Desktop: first item of the bottom status row, left of the usage bars. Always
// there, so its place never moves; green when someone is working, quiet
// "0 working" otherwise (also when the Floor cannot be read, so no stale count
// survives). Mobile hides that row, so the app bar has its own pill, hidden at
// zero because the bar has no room.

const _WN_ACTIVITY = { thinking: 'thinking…', writing: 'writing…', tool: 'running a tool…' };
const _WN_MOUNTS = [
  // Desktop bottom row, and the mobile app bar for 960px and narrower.
  { host: '#bottom-status-row', before: host => host.firstElementChild, atZero: true },
  { host: '#mobile-app-bar', before: host => host.querySelector('#beacon-badge-btn'), atZero: false },
];

let _wnWorking = [];
let _wnPollMs = 5000;
// With nobody working it reads the Floor less often: the chip may turn live up
// to this late, and an idle dashboard costs a third as much.
const _WN_IDLE_POLL_MS = 15000;
let _wnSharedAt = 0;   // last payload handed over by the Floor window
let _wnPopover = null;
let _wnAnchor = null;

function _wnPills() {
  return _WN_MOUNTS.map(({ host, before, atZero }) => {
    const parent = document.querySelector(host);
    if (!parent) return null;
    let pill = parent.querySelector(':scope > .wn-pill');
    if (!pill) {
      pill = document.createElement('button');
      pill.type = 'button';
      pill.className = 'wn-pill';
      pill.hidden = !atZero;
      pill._atZero = atZero;
      pill.setAttribute('aria-haspopup', 'dialog');
      pill.setAttribute('aria-expanded', 'false');
      pill.innerHTML = '<span class="wn-dot" aria-hidden="true"></span><span class="wn-count"></span>';
      pill.addEventListener('click', () => (_wnPopover ? _wnClose() : _wnOpen(pill)));
      parent.insertBefore(pill, before(parent));
    }
    return pill;
  }).filter(Boolean);
}

function _wnRender() {
  const n = _wnWorking.length;
  for (const pill of _wnPills()) {
    pill.hidden = !n && !pill._atZero;
    pill.classList.toggle('wn-live', n > 0);
    pill.querySelector('.wn-count').textContent = `${n} working`;
  }
  if (!_wnPopover) return;
  if (_wnAnchor.hidden) _wnClose();   // the mobile pill left at zero
  else _wnFill();
}

const _WN_NONE_HTML = '<p class="wn-none">No one is working right now</p>' +
  '<button type="button" class="wn-open-floor">Open the Floor</button>';

function _wnRowHTML(f) {
  const avatar = typeof window.avatarHTML === 'function' ? window.avatarHTML(f.avatar || '', 28) : '';
  const meta = [_WN_ACTIVITY[f.activity] || 'working…', f.age].filter(Boolean).join(' · ');
  return `<button type="button" class="wn-row" data-sid="${esc(f.session_id)}">` +
    `<span class="wn-avatar" aria-hidden="true">${avatar || '&#9679;'}</span><span class="wn-body">` +
    `<span class="wn-who">${esc(f.name || 'Agent')} <span class="wn-project">· ${esc(f.projectName)}</span></span>` +
    `<span class="wn-meta">${esc(meta)}</span>` +
    `<span class="wn-task">${esc(f.task || '')}</span></span></button>`;
}

function _wnFill() {
  const html = _wnWorking.map(_wnRowHTML).join('') || _WN_NONE_HTML;
  // Compared against what was written, not read back: an unchanged board must
  // not rebuild the list under a finger or drop keyboard focus.
  if (_wnPopover._html === html) return;
  const hadFocus = _wnPopover.contains(document.activeElement);
  const focused = hadFocus ? document.activeElement.dataset.sid : '';
  _wnPopover._html = html;
  _wnPopover.innerHTML = html;
  if (hadFocus) {
    const buttons = [..._wnPopover.querySelectorAll('button')];
    (buttons.find(b => b.dataset.sid === focused) || buttons[0])?.focus({ preventScroll: true });
  }
}

function _wnPlace() {
  const bar = _wnAnchor.closest('.mc-app-bar');
  if (bar) {   // hangs under the bar, full width (working-now.css)
    _wnPopover.style.top = `${Math.round(bar.getBoundingClientRect().bottom)}px`;
    return;
  }
  // The bottom row: opens upward, like the usage bars' popup beside it.
  // Anchored by its bottom edge, so a longer list grows up, not off-screen.
  const pill = _wnAnchor.getBoundingClientRect();
  const left = Math.min(pill.left, window.innerWidth - _wnPopover.offsetWidth - 8);
  _wnPopover.style.left = `${Math.round(Math.max(8, left))}px`;
  _wnPopover.style.bottom = `${Math.round(window.innerHeight - pill.top + 8)}px`;
}

function _wnOutside(event) {
  if (_wnPopover?.contains(event.target) || event.target.closest?.('.wn-pill')) return;
  _wnClose();
}

function _wnKeydown(event) {
  if (event.key !== 'Escape') return;
  // This Escape closes the popover only, not the window behind it.
  event.preventDefault();
  event.stopImmediatePropagation();
  const anchor = _wnAnchor;
  _wnClose();
  anchor?.focus({ preventScroll: true });
}

function _wnOpen(anchor) {
  _wnAnchor = anchor;
  _wnPopover = document.createElement('div');
  _wnPopover.className = 'wn-popover';
  _wnPopover.id = 'working-now-popover';
  _wnPopover.setAttribute('role', 'dialog');
  _wnPopover.setAttribute('aria-label', 'Agents working now');
  _wnPopover.addEventListener('click', event => {
    if (event.target.closest('.wn-open-floor')) {
      _wnClose();
      window.openFloor?.();
      return;
    }
    const row = event.target.closest('.wn-row');
    const figure = row && _wnWorking.find(f => f.session_id === row.dataset.sid);
    if (!figure) return;
    _wnClose();
    // The same route as clicking the figure on the Floor.
    window.floorOpenFigure?.(figure.projectId, figure.claude_session_id, figure.session_id);
  });
  document.body.appendChild(_wnPopover);
  _wnFill();
  _wnPlace();
  anchor.setAttribute('aria-expanded', 'true');
  document.addEventListener('pointerdown', _wnOutside, true);
  window.addEventListener('keydown', _wnKeydown, true);
  window.addEventListener('resize', _wnClose);
}

function _wnClose() {
  if (!_wnPopover) return;
  _wnPopover.remove();
  _wnPopover = null;
  _wnAnchor?.setAttribute('aria-expanded', 'false');
  _wnAnchor = null;
  document.removeEventListener('pointerdown', _wnOutside, true);
  window.removeEventListener('keydown', _wnKeydown, true);
  window.removeEventListener('resize', _wnClose);
}

function _wnApply(payload) {
  // Read first, assign after: a payload that throws changes nothing.
  const working = (payload?.rooms || []).flatMap(room => (room.figures || [])
    .filter(f => f.state === 'working')
    .map(f => ({ ...f, projectId: room.id, projectName: room.name })));
  _wnPollMs = Math.max(3, parseInt(payload?.poll_seconds, 10) || 5) * 1000;
  _wnWorking = working;
  _wnRender();
}

let _wnTimer = null;
let _wnBusy = false;

async function _wnTick() {
  clearTimeout(_wnTimer);
  const fedElsewhere = Date.now() - _wnSharedAt < _wnPollMs * 1.5;
  if (!_wnBusy && !document.hidden && !fedElsewhere && !window._clayruneDuplicateTab) {
    _wnBusy = true;
    try {
      const res = await fetch(API_BASE + '/api/floor');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      _wnApply(await res.json());
    } catch (e) {
      // An unreadable board is unknown, not "still N working".
      _wnWorking = [];
      _wnRender();
    } finally {
      _wnBusy = false;
    }
  }
  clearTimeout(_wnTimer);
  _wnTimer = setTimeout(_wnTick, _wnWorking.length ? _wnPollMs : Math.max(_wnPollMs, _WN_IDLE_POLL_MS));
}

// ES-module cross-boundary rule: floor.js reaches this via window.
window.workingNowFeed = payload => {
  // Called before refreshFloor's try/finally: a throw here would stop the
  // Floor's own polling.
  try {
    _wnApply(payload);
    _wnSharedAt = Date.now();
  } catch (e) {
    console.warn('[working-now] could not read the Floor payload:', e);
  }
};
// Take the slot before the first read, so nothing in the bar shifts when it lands.
_wnRender();
document.addEventListener('visibilitychange', () => { if (!document.hidden) _wnTick(); });
// Wait for load: avatarHTML and floorOpenFigure come from later modules.
if (document.readyState === 'complete') _wnTick();
else window.addEventListener('load', _wnTick);
