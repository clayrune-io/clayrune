// ── Browser pane (Part B) ────────────────────────────────────────────────────
// A visible, interactive browser inside Clayrune. Backend (mc/blueprints/
// browser_routes.py) runs a headless Chromium and screencasts JPEG frames over
// CDP; this pane renders them into an <img> and forwards mouse/keyboard/scroll
// back to /api/browser/input (coords scaled from the displayed image to the
// fixed VIEW_W×VIEW_H render viewport). ES module → everything shared via
// window.* (see discovery_es_module_cross_boundary_globals).

const BP_VIEW_W = 1280, BP_VIEW_H = 800;
let _bpSession = null, _bpES = null, _bpMoveTs = 0, _bpPressed = false;

const _bpKeyCodes = {
  Enter: 13, Backspace: 8, Tab: 9, Escape: 27, Delete: 46,
  ArrowUp: 38, ArrowDown: 40, ArrowLeft: 37, ArrowRight: 39,
  Home: 36, End: 35, PageUp: 33, PageDown: 34,
};

function _bpSend(body) {
  if (!_bpSession) return;
  fetch((window.API_BASE || '') + '/api/browser/input', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: _bpSession, ...body }),
  }).catch(() => {});
}

function _bpCoords(img, e) {
  const r = img.getBoundingClientRect();
  return {
    x: Math.max(0, Math.min(BP_VIEW_W, (e.clientX - r.left) / r.width * BP_VIEW_W)),
    y: Math.max(0, Math.min(BP_VIEW_H, (e.clientY - r.top) / r.height * BP_VIEW_H)),
  };
}

// url        — page to open (launch mode) or the label to seed the bar with.
// projectId  — owning project (defaults to the active one).
// sessionId  — OPTIONAL. When given, ATTACH the pane to that already-running
//              session (e.g. one an agent launched via /api/browser/launch)
//              instead of spawning a new one. This is how an agent surfaces a
//              server-side session into the user's UI — see the
//              `[browser-attach:<sid>]` marker in resume-preview.js.
async function openBrowserPane(url, projectId, sessionId) {
  if (_bpSession) closeBrowserPane();
  const pid = projectId || window.currentProjectId ||
    (typeof activeProjectId !== 'undefined' ? activeProjectId : null) || 'mission_control';
  if (sessionId) {
    // Attach mode: adopt the existing session, read its current URL for the bar.
    _bpSession = sessionId;
    if (!url) {
      try {
        const st = await fetch((window.API_BASE || '') +
          `/api/project/${encodeURIComponent(pid)}/browser/status`).then(r => r.json());
        const s = (st.sessions || []).find(x => x.session_id === sessionId);
        if (s) url = s.url;
      } catch (e) {}
    }
  } else {
    let data;
    try {
      const res = await fetch((window.API_BASE || '') + '/api/browser/launch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: pid, url: url || 'about:blank' }),
      });
      data = await res.json();
      if (!res.ok) throw new Error(data.error || 'launch failed');
    } catch (e) {
      alert('Browser pane unavailable: ' + e.message);
      return;
    }
    _bpSession = data.session_id;
  }

  // ── DOM ──
  const win = document.createElement('div');
  win.id = 'mc-browser-pane';
  const W = Math.min(window.innerWidth * 0.96, 1120);
  const H = Math.min(window.innerHeight * 0.92, 760);
  win.style.cssText =
    `position:fixed;left:${Math.max(4, (window.innerWidth - W) / 2)}px;` +
    `top:${Math.max(4, (window.innerHeight - H) / 2)}px;width:${W}px;height:${H}px;` +
    // No hard-coded z-index: the pane joins the shared modal stacking order
    // (nextModalZ) below, so it behaves like every other pop-up instead of
    // pinning itself above everything. pointer-events:auto is required because
    // the #modal-layer host is pointer-events:none.
    'background:#1e1e1e;border:1px solid var(--border,#444);border-radius:10px;pointer-events:auto;' +
    'display:flex;flex-direction:column;box-shadow:0 12px 48px rgba(0,0,0,.5);overflow:hidden';
  win.innerHTML = `
    <div data-bp="bar" style="display:flex;align-items:center;gap:6px;padding:8px 10px;background:#2a2a2a;flex:0 0 auto;cursor:move;touch-action:none;user-select:none">
      <button data-bp="back"   title="Back"    style="background:none;border:none;color:#ddd;font-size:16px;cursor:pointer;padding:2px 6px">&#8592;</button>
      <button data-bp="fwd"    title="Forward" style="background:none;border:none;color:#ddd;font-size:16px;cursor:pointer;padding:2px 6px">&#8594;</button>
      <button data-bp="reload" title="Reload"  style="background:none;border:none;color:#ddd;font-size:15px;cursor:pointer;padding:2px 6px">&#8635;</button>
      <input data-bp="url" type="text" spellcheck="false" value="${(url||'').replace(/"/g,'&quot;')}"
        placeholder="Enter URL and press Enter"
        style="flex:1;min-width:60px;padding:6px 10px;font-size:13px;background:#111;border:1px solid #444;border-radius:6px;color:#eee;outline:none">
      <span data-bp="spin" style="color:#888;font-size:12px;width:14px">&#9679;</span>
      <button data-bp="close" title="Close" style="background:none;border:none;color:#ddd;font-size:16px;cursor:pointer;padding:2px 8px">&#10005;</button>
    </div>
    <div style="flex:1;background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden">
      <img data-bp="screen" tabindex="0"
        style="max-width:100%;max-height:100%;aspect-ratio:${BP_VIEW_W}/${BP_VIEW_H};outline:none;cursor:default;user-select:none" draggable="false">
    </div>
    <div data-bp="grip" title="Drag to resize"
      style="position:absolute;right:1px;bottom:1px;width:22px;height:22px;cursor:nwse-resize;touch-action:none;background:linear-gradient(135deg,transparent 45%,#777 45%,#777 55%,transparent 55%,transparent 70%,#777 70%,#777 80%,transparent 80%);border-radius:0 0 9px 0"></div>`;
  // Live inside #modal-layer (z-300 stacking context) alongside terminals and
  // other pop-ups, and take the next slot in the shared modal z-order so it
  // opens on top but does NOT stay pinned above later-focused windows.
  (document.getElementById('modal-layer') || document.body).appendChild(win);
  try { win.style.zIndex = nextModalZ++; } catch (e) { win.style.zIndex = 1000; }
  // Raise-to-front on interaction, exactly like focusModal does for modals, so
  // clicking another window brings it forward and this stops being always-on-top.
  win.addEventListener('mousedown', () => {
    try { win.style.zIndex = nextModalZ++; } catch (e) {}
  }, true);

  // Restore the last size/position the user left the pane at (clamped so a
  // resized-down window can't strand it off-screen).
  try {
    const g = JSON.parse(localStorage.getItem('mc_browser_pane_geom') || 'null');
    if (g && g.w > 240 && g.h > 180) {
      win.style.width = Math.min(g.w, window.innerWidth) + 'px';
      win.style.height = Math.min(g.h, window.innerHeight) + 'px';
      win.style.left = Math.max(0, Math.min(g.l, window.innerWidth - 60)) + 'px';
      win.style.top = Math.max(0, Math.min(g.t, window.innerHeight - 40)) + 'px';
    }
  } catch (e) {}
  const _bpSaveGeom = () => {
    try {
      localStorage.setItem('mc_browser_pane_geom', JSON.stringify(
        { l: win.offsetLeft, t: win.offsetTop, w: win.offsetWidth, h: win.offsetHeight }));
    } catch (e) {}
  };

  const $ = sel => win.querySelector(`[data-bp="${sel}"]`);
  const img = $('screen'), urlInput = $('url'), spin = $('spin');

  $('close').onclick = closeBrowserPane;
  $('back').onclick = () => _bpSend({ type: 'back' });
  $('fwd').onclick = () => _bpSend({ type: 'forward' });
  $('reload').onclick = () => _bpSend({ type: 'reload' });
  urlInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { _bpSend({ type: 'navigate', url: urlInput.value.trim() }); img.focus(); }
  });

  // ── move (drag the toolbar) + resize (corner grip) — pointer events cover
  //    mouse and touch alike; setPointerCapture keeps the gesture even off-element.
  const bar = $('bar'), grip = $('grip');
  let drag = null, rz = null;
  bar.addEventListener('pointerdown', e => {
    if (e.target.closest('button') || e.target.tagName === 'INPUT') return;  // let controls work
    drag = { sx: e.clientX, sy: e.clientY, l: win.offsetLeft, t: win.offsetTop };
    bar.setPointerCapture(e.pointerId);
  });
  bar.addEventListener('pointermove', e => {
    if (!drag) return;
    const nl = Math.max(80 - win.offsetWidth, Math.min(window.innerWidth - 60, drag.l + e.clientX - drag.sx));
    const nt = Math.max(0, Math.min(window.innerHeight - 40, drag.t + e.clientY - drag.sy));
    win.style.left = nl + 'px'; win.style.top = nt + 'px';
  });
  const _endDrag = () => { if (drag) { drag = null; _bpSaveGeom(); } };
  bar.addEventListener('pointerup', _endDrag);
  bar.addEventListener('pointercancel', _endDrag);
  grip.addEventListener('pointerdown', e => {
    e.preventDefault();
    rz = { sx: e.clientX, sy: e.clientY, w: win.offsetWidth, h: win.offsetHeight };
    grip.setPointerCapture(e.pointerId);
  });
  grip.addEventListener('pointermove', e => {
    if (!rz) return;
    win.style.width = Math.max(320, Math.min(window.innerWidth, rz.w + e.clientX - rz.sx)) + 'px';
    win.style.height = Math.max(240, Math.min(window.innerHeight, rz.h + e.clientY - rz.sy)) + 'px';
  });
  const _endRz = () => { if (rz) { rz = null; _bpSaveGeom(); } };
  grip.addEventListener('pointerup', _endRz);
  grip.addEventListener('pointercancel', _endRz);

  // ── input forwarding ──
  img.addEventListener('mousedown', e => {
    e.preventDefault(); img.focus(); _bpPressed = true;
    const c = _bpCoords(img, e);
    _bpSend({ type: 'mouse', action: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, ...c });
  });
  window.addEventListener('mouseup', _bpUp);
  function _bpUp(e) {
    if (!_bpPressed) return; _bpPressed = false;
    const c = _bpCoords(img, e);
    _bpSend({ type: 'mouse', action: 'mouseReleased', button: 'left', buttons: 0, clickCount: 1, ...c });
  }
  img.addEventListener('mousemove', e => {
    const now = Date.now(); if (now - _bpMoveTs < 55) return; _bpMoveTs = now;
    const c = _bpCoords(img, e);
    _bpSend({ type: 'mouse', action: 'mouseMoved', buttons: _bpPressed ? 1 : 0, ...c });
  });
  img.addEventListener('wheel', e => {
    e.preventDefault(); const c = _bpCoords(img, e);
    _bpSend({ type: 'wheel', deltaX: e.deltaX, deltaY: e.deltaY, ...c });
  }, { passive: false });
  img.addEventListener('keydown', e => {
    if (e.ctrlKey || e.metaKey || e.altKey) return;  // let shortcuts through
    e.preventDefault();
    if (e.key.length === 1) _bpSend({ type: 'text', text: e.key });
    else if (_bpKeyCodes[e.key] != null)
      _bpSend({ type: 'key', key: e.key, code: e.code, keyCode: _bpKeyCodes[e.key] });
  });

  // ── touch: drag-to-scroll, tap-to-click (mouse events don't map on phones) ──
  // preventDefault on move/end also suppresses the synthetic mouse events so a
  // scroll isn't mis-fired as a click.
  let touch = null;
  img.addEventListener('touchstart', e => {
    if (e.touches.length !== 1) { touch = null; return; }
    const t = e.touches[0];
    touch = { x: t.clientX, y: t.clientY, sx: t.clientX, sy: t.clientY, moved: false };
    img.focus();
  }, { passive: true });
  img.addEventListener('touchmove', e => {
    if (!touch || e.touches.length !== 1) return;
    e.preventDefault();
    const t = e.touches[0], r = img.getBoundingClientRect();
    if (Math.abs(t.clientX - touch.sx) > 6 || Math.abs(t.clientY - touch.sy) > 6) touch.moved = true;
    // finger up → content scrolls down: deltaY = (prev - current), scaled to page px
    _bpSend({
      type: 'wheel', ..._bpCoords(img, t),
      deltaX: (touch.x - t.clientX) * (BP_VIEW_W / r.width),
      deltaY: (touch.y - t.clientY) * (BP_VIEW_H / r.height),
    });
    touch.x = t.clientX; touch.y = t.clientY;
  }, { passive: false });
  img.addEventListener('touchend', e => {
    if (touch && !touch.moved) {  // a tap → click at the start point
      const c = _bpCoords(img, { clientX: touch.sx, clientY: touch.sy });
      _bpSend({ type: 'mouse', action: 'mousePressed', button: 'left', buttons: 1, clickCount: 1, ...c });
      _bpSend({ type: 'mouse', action: 'mouseReleased', button: 'left', buttons: 0, clickCount: 1, ...c });
    }
    touch = null;
  });

  // ── frame stream ──
  _bpES = new EventSource((window.API_BASE || '') + '/api/browser/stream?session_id=' + _bpSession);
  _bpES.onmessage = ev => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.img) { img.src = 'data:image/jpeg;base64,' + d.img; spin.style.color = '#4caf50'; }
    if (d.url && document.activeElement !== urlInput) urlInput.value = d.url;
    if (d.status && d.status !== 'running') { spin.textContent = '×'; spin.style.color = '#e57373'; }
  };
  _bpES.onerror = () => { if (spin) spin.style.color = '#e57373'; };
  setTimeout(() => img.focus(), 100);
  // Remember the open session so a page refresh (which wipes the SPA DOM but
  // leaves the backend Chromium running) can re-attach instead of orphaning it.
  try { localStorage.setItem('mc_browser_pane_open', JSON.stringify({ sid: _bpSession, pid })); } catch (e) {}
}

function closeBrowserPane() {
  if (_bpES) { try { _bpES.close(); } catch (e) {} _bpES = null; }
  // Explicit close ends the backend session — so don't restore it on next load.
  try { localStorage.removeItem('mc_browser_pane_open'); } catch (e) {}
  if (_bpSession) {
    fetch((window.API_BASE || '') + '/api/browser/stop', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: _bpSession }),
    }).catch(() => {});
    _bpSession = null;
  }
  const win = document.getElementById('mc-browser-pane');
  if (win) win.remove();
}

// On SPA boot, restore the pane if its backend session is still running (i.e.
// the user refreshed rather than closing it). A dead session — e.g. after a
// server restart, which kills all panes — clears the flag so no ghost pane
// pops up. This also stops refreshes from leaking orphaned Chromiums.
function _bpRestoreOnLoad() {
  let saved;
  try { saved = JSON.parse(localStorage.getItem('mc_browser_pane_open') || 'null'); } catch (e) { saved = null; }
  if (!saved || !saved.sid) return;
  const pid = saved.pid || 'mission_control';
  fetch((window.API_BASE || '') + `/api/project/${encodeURIComponent(pid)}/browser/status`)
    .then(r => r.json())
    .then(st => {
      const s = (st.sessions || []).find(x => x.session_id === saved.sid && x.status === 'running');
      if (s) openBrowserPane(null, pid, saved.sid);
      else { try { localStorage.removeItem('mc_browser_pane_open'); } catch (e) {} }
    })
    .catch(() => {});
}
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', _bpRestoreOnLoad);
else setTimeout(_bpRestoreOnLoad, 0);

window.openBrowserPane = openBrowserPane;
window.closeBrowserPane = closeBrowserPane;
// Attach the UI pane to an already-running session (agent-launched via API).
window.attachBrowserPane = (sessionId, projectId) => openBrowserPane(null, projectId, sessionId);
