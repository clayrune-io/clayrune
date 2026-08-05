// ── Browser pane (Part B) ────────────────────────────────────────────────────
// A visible, interactive browser inside Clayrune. Backend (mc/blueprints/
// browser_routes.py) runs a headless Chromium and screencasts JPEG frames over
// CDP; this pane renders them into an <img> and forwards mouse/keyboard/scroll
// back to /api/browser/input (coords scaled from the displayed image to the
// fixed VIEW_W×VIEW_H render viewport). ES module → everything shared via
// window.* (see discovery_es_module_cross_boundary_globals).

const BP_VIEW_W = 1280, BP_VIEW_H = 800;
let _bpSession = null, _bpES = null, _bpMoveTs = 0, _bpPressed = false;
// The mouseup handler lives on `window` (a drag can end off the image), so it
// must be tracked and removed on teardown — otherwise every re-open stacks
// another listener bound to a now-detached <img>. The oldest stale one wins the
// shared _bpPressed flag and reports the release at a detached-rect corner, so
// clicks land in the corner instead of where the user clicked (feels dead).
let _bpUpHandler = null;
// Set when a Ctrl/Cmd+V is let through to the browser, cleared by the `paste`
// event it should produce. Still set after the grace period => no paste event
// arrived, so fall back to the clipboard API. See the keydown handler.
let _bpPasteAt = 0;

// Clipboard-API paste. This is the FALLBACK, not the primary path: readText()
// needs a secure context (so it is undefined over plain-http on the LAN), needs
// the clipboard-read permission (permanently dead once "Block" is clicked), and
// does not exist for web content in Firefox at all. Returns true if it pasted.
async function _bpPasteViaApi() {
  try {
    const t = navigator.clipboard && await navigator.clipboard.readText();
    if (t) { _bpSend({ type: 'text', text: t }); return true; }
  } catch (err) {}
  return false;
}

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
// profile    — OPTIONAL. Name of a persistent, signed-in Chromium profile
//              (`~/.clayrune/browser_profiles_named/<name>`). Omit for the
//              throwaway default; pass one for a site you keep logging into.
//              Naming a profile that is already open adopts that session.
async function openBrowserPane(url, projectId, sessionId, profile) {
  // Detach the current VIEW without stopping its backend session — opening or
  // switching must not kill a session another agent (or you) may still want.
  // Only the × button / per-session stop actually ends a session.
  _bpDetachView();
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
        body: JSON.stringify({ project_id: pid, url: url || 'about:blank',
                               profile: profile || undefined }),
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
      <button data-bp="paste"  title="Paste clipboard into the page" style="background:none;border:none;color:#ddd;font-size:13px;cursor:pointer;padding:2px 6px">&#128203;</button>
      <button data-bp="sessions" title="Browser sessions" style="background:none;border:none;color:#ddd;font-size:15px;cursor:pointer;padding:2px 6px;position:relative">&#9776;<span data-bp="sesscount" style="position:absolute;top:-3px;right:-3px;background:#4caf50;color:#000;font-size:9px;font-weight:700;border-radius:8px;padding:0 4px;line-height:14px;display:none"></span></button>
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
  // Touch has no Ctrl+V, so the keyboard path can't be the only way in. The
  // prompt box is itself a native paste target, which is what makes this work
  // on a phone (long-press → Paste) and wherever readText() is unavailable.
  $('paste').onclick = async () => {
    img.focus();
    if (await _bpPasteViaApi()) return;
    const t = prompt('Paste here and press OK — this types it into the page:');
    if (t) _bpSend({ type: 'text', text: t });
  };
  $('sessions').onclick = (e) => { e.stopPropagation(); _bpToggleSessionMenu(win, pid); };
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
  // Replace any prior handler so exactly one mouseup listener is ever live.
  if (_bpUpHandler) window.removeEventListener('mouseup', _bpUpHandler);
  _bpUpHandler = function _bpUp(e) {
    if (!_bpPressed) return; _bpPressed = false;
    const c = _bpCoords(img, e);
    _bpSend({ type: 'mouse', action: 'mouseReleased', button: 'left', buttons: 0, clickCount: 1, ...c });
  };
  window.addEventListener('mouseup', _bpUpHandler);
  img.addEventListener('mousemove', e => {
    const now = Date.now(); if (now - _bpMoveTs < 55) return; _bpMoveTs = now;
    const c = _bpCoords(img, e);
    _bpSend({ type: 'mouse', action: 'mouseMoved', buttons: _bpPressed ? 1 : 0, ...c });
  });
  img.addEventListener('wheel', e => {
    e.preventDefault(); const c = _bpCoords(img, e);
    _bpSend({ type: 'wheel', deltaX: e.deltaX, deltaY: e.deltaY, ...c });
  }, { passive: false });
  img.addEventListener('keydown', async e => {
    const mod = (e.ctrlKey || e.metaKey) && !e.altKey;
    const k = (e.key || '').toLowerCase();
    // ── clipboard bridge (host clipboard <-> the page in the pane) ──
    if (mod && k === 'v') {                       // paste: host clipboard → page
      // Deliberately NO preventDefault here. Cancelling the Ctrl+V keydown also
      // cancels the native `paste` event — which was the whole bug: the pane
      // suppressed the one clipboard path that always works and then relied on
      // clipboard.readText(), which needs a secure context AND a permission the
      // user may have blocked. Measured in Chromium: with preventDefault only
      // the keydown fires; without it, a `paste` event follows carrying the
      // text. The listener below consumes it and clears _bpPasteAt.
      _bpPasteAt = Date.now();
      setTimeout(async () => {
        if (!_bpPasteAt) return;                  // the paste event handled it
        _bpPasteAt = 0;
        if (await _bpPasteViaApi()) return;
        if (typeof showToast === 'function')
          showToast('Paste blocked by the browser — use the 📋 button in the pane toolbar');
      }, 200);
      return;
    }
    if (mod && (k === 'c' || k === 'x')) {          // copy / cut: page selection → host
      e.preventDefault();
      try {
        const r = await fetch((window.API_BASE || '') + '/api/browser/selection', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ session_id: _bpSession }),
        });
        const d = await r.json();
        if (d && d.text) {
          await navigator.clipboard.writeText(d.text);
          if (k === 'x') _bpSend({ type: 'key', key: 'Delete', code: 'Delete', keyCode: 46 });
        }
      } catch (err) {
        if (typeof showToast === 'function') showToast('Copy failed: ' + (err && err.message || err));
      }
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;  // let other shortcuts through
    e.preventDefault();
    if (e.key.length === 1) _bpSend({ type: 'text', text: e.key });
    else if (_bpKeyCodes[e.key] != null)
      _bpSend({ type: 'key', key: e.key, code: e.code, keyCode: _bpKeyCodes[e.key] });
  });

  // The primary paste path. Fires on the focused <img> (verified in Chromium)
  // and carries the text directly, so it needs no clipboard permission, works
  // over plain http on the LAN, and works where readText() does not exist.
  img.addEventListener('paste', e => {
    e.preventDefault();
    _bpPasteAt = 0;                   // handled — cancel the keydown fallback
    const cd = e.clipboardData || window.clipboardData;
    const t = cd ? cd.getData('text/plain') : '';
    if (t) _bpSend({ type: 'text', text: t });
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
  if (_bpUpHandler) { window.removeEventListener('mouseup', _bpUpHandler); _bpUpHandler = null; }
  _bpPressed = false;
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

// ── Multi-session switcher ──────────────────────────────────────────────────
// One pane, many sessions. The pane is a VIEWER: switching just re-binds it to
// another running session (never stops one); a badge + poll surface sessions
// that agents launch via the API so they are all discoverable and viewable.
let _bpKnownSids = null;   // sids seen by the poller (null until first poll)
let _bpPollTimer = null;
let _bpPollPid = null;     // project the poller last looked at (re-baseline on change)

function _bpEsc(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g,
    c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}
function _bpActivePid() {
  return window.currentProjectId ||
    (typeof activeProjectId !== 'undefined' ? activeProjectId : null) || 'mission_control';
}
async function _bpFetchSessions(pid) {
  try {
    const st = await fetch((window.API_BASE || '') +
      `/api/project/${encodeURIComponent(pid || _bpActivePid())}/browser/status`).then(r => r.json());
    return (st.sessions || []).filter(s => s.status === 'running');
  } catch (e) { return []; }
}

// Detach the pane VIEW (close the stream, remove the window) WITHOUT stopping
// the backend session or clearing the restore flag. Used on switch/re-open.
function _bpDetachView() {
  if (_bpES) { try { _bpES.close(); } catch (e) {} _bpES = null; }
  if (_bpUpHandler) { window.removeEventListener('mouseup', _bpUpHandler); _bpUpHandler = null; }
  _bpPressed = false;
  const w = document.getElementById('mc-browser-pane');
  if (w) w.remove();
}

// Re-bind the pane to another running session (view-only; nothing is stopped).
function switchBrowserSession(sid, pid) {
  if (!sid) return;
  if (sid === _bpSession && document.getElementById('mc-browser-pane')) return;
  openBrowserPane(null, pid || _bpActivePid(), sid);
}

// Stop a specific backend session. If it is the one being viewed, close the view too.
function stopBrowserSession(sid, pid) {
  fetch((window.API_BASE || '') + '/api/browser/stop', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid }),
  }).catch(() => {});
  if (sid === _bpSession) {
    if (_bpES) { try { _bpES.close(); } catch (e) {} _bpES = null; }
    try { localStorage.removeItem('mc_browser_pane_open'); } catch (e) {}
    _bpSession = null;
    const w = document.getElementById('mc-browser-pane'); if (w) w.remove();
  }
  setTimeout(_bpPollStatus, 250);
}

function _bpToggleSessionMenu(win, pid) {
  const existing = win.querySelector('[data-bp="sessmenu"]');
  if (existing) { existing.remove(); return; }
  const menu = document.createElement('div');
  menu.setAttribute('data-bp', 'sessmenu');
  menu.style.cssText =
    'position:absolute;top:42px;left:8px;min-width:280px;max-width:92%;background:#242424;' +
    'border:1px solid #444;border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,.5);z-index:5;' +
    'padding:6px;font-size:12px;color:#eee';
  win.appendChild(menu);
  _bpRenderSessionMenu(menu, pid);
  setTimeout(() => {
    const off = (ev) => {
      if (!menu.contains(ev.target) && !ev.target.closest('[data-bp="sessions"]')) {
        menu.remove(); document.removeEventListener('mousedown', off, true);
      }
    };
    document.addEventListener('mousedown', off, true);
  }, 0);
}

async function _bpFetchProfiles() {
  try {
    const r = await fetch((window.API_BASE || '') + '/api/browser/profiles');
    return (await r.json()).profiles || [];
  } catch (e) { return []; }
}

// Forget a saved profile — this is the sign-out, so it asks.
async function forgetBrowserProfile(name, menu, pid) {
  if (!confirm(`Forget "${name}"?\n\nThis deletes its cookies — anything signed in there is signed out.`)) return;
  try {
    const r = await fetch((window.API_BASE || '') + '/api/browser/profiles/' +
      encodeURIComponent(name), { method: 'DELETE' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.error || r.statusText);
  } catch (e) { alert('Could not forget it: ' + e.message); }
  if (menu && menu.isConnected) _bpRenderSessionMenu(menu, pid);
}

async function _bpRenderSessionMenu(menu, pid) {
  const [sessions, profiles] = await Promise.all([
    _bpFetchSessions(pid), _bpFetchProfiles(),
  ]);
  if (!menu.isConnected) return;
  const rows = sessions.map(s => {
    const active = s.session_id === _bpSession;
    const label = (s.url && s.url !== 'about:blank') ? s.url : 'about:blank';
    // The profile badge is what tells a signed-in tab from a throwaway one —
    // without it two rows on the same site look identical.
    const badge = s.profile ? `<span title="Signed-in profile: ${_bpEsc(s.profile)}"
      style="font-size:10px;padding:0 5px;border:1px solid #4a4a4a;border-radius:99px;color:#9ecb9e;flex:0 0 auto">${_bpEsc(s.profile)}</span>` : '';
    return `<div data-sid="${_bpEsc(s.session_id)}" style="display:flex;align-items:center;gap:6px;padding:6px;border-radius:6px;cursor:pointer;${active ? 'background:#2f3a2f' : ''}">
      <span style="width:8px;height:8px;border-radius:50%;background:${active ? '#4caf50' : '#666'};flex:0 0 auto"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="${_bpEsc(label)}">${_bpEsc(label)}</span>
      ${badge}
      <span data-stop="${_bpEsc(s.session_id)}" title="Stop this session" style="color:#e57373;cursor:pointer;padding:0 4px;flex:0 0 auto">&#10005;</span>
    </div>`;
  }).join('') || '<div style="padding:8px;color:#999">No running sessions</div>';

  // Only profiles that are NOT already open — an open one is in the list above,
  // and "Open" on it would just re-adopt the same session.
  const idle = profiles.filter(p => !p.in_use_by);
  const saved = idle.length ? `
    <div style="margin-top:4px;padding:6px 6px 2px;border-top:1px solid #3a3a3a;color:#999;font-size:10px">
      SAVED LOGINS — these keep their cookies
    </div>` + idle.map(p => `
    <div data-prof="${_bpEsc(p.name)}" style="display:flex;align-items:center;gap:6px;padding:6px;border-radius:6px;cursor:pointer">
      <span style="width:8px;height:8px;border-radius:50%;background:#8ab4f8;flex:0 0 auto"></span>
      <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${_bpEsc(p.name)}</span>
      <span style="color:#777;font-size:10px;flex:0 0 auto">${_bpEsc(p.size_mb)} MB</span>
      <span data-forget="${_bpEsc(p.name)}" title="Forget it — signs this profile out" style="color:#e57373;cursor:pointer;padding:0 4px;flex:0 0 auto">&#10005;</span>
    </div>`).join('') : '';

  menu.innerHTML = rows + saved +
    `<div data-newbp="1" style="margin-top:4px;padding:6px;border-top:1px solid #3a3a3a;color:#8ab4f8;cursor:pointer">&#43; New browser</div>
     <div data-newprof="1" style="padding:6px;color:#8ab4f8;cursor:pointer">&#43; New signed-in browser&hellip;</div>`;
  menu.querySelectorAll('[data-sid]').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('[data-stop]')) return;
      switchBrowserSession(row.dataset.sid, pid);
    });
  });
  menu.querySelectorAll('[data-stop]').forEach(x => {
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      stopBrowserSession(x.dataset.stop, pid);
      _bpRenderSessionMenu(menu, pid);
    });
  });
  menu.querySelectorAll('[data-prof]').forEach(row => {
    row.addEventListener('click', (e) => {
      if (e.target.closest('[data-forget]')) return;
      menu.remove();
      openBrowserPane('about:blank', pid, null, row.dataset.prof);
    });
  });
  menu.querySelectorAll('[data-forget]').forEach(x => {
    x.addEventListener('click', (e) => {
      e.stopPropagation();
      forgetBrowserProfile(x.dataset.forget, menu, pid);
    });
  });
  const nb = menu.querySelector('[data-newbp]');
  if (nb) nb.addEventListener('click', () => { menu.remove(); openBrowserPane('about:blank', pid); });
  const np = menu.querySelector('[data-newprof]');
  if (np) np.addEventListener('click', () => {
    const name = (prompt('Name this profile — logins in it are remembered under this name.\n\nLowercase letters, digits, . - _ (e.g. reddit)') || '').trim().toLowerCase();
    if (!name) return;
    menu.remove();
    openBrowserPane('about:blank', pid, null, name);
  });
}

// Poll so agent-launched sessions are discoverable even when the pane is shut:
// keep the Browser-button badge current and toast genuinely new sessions.
async function _bpPollStatus() {
  const pid = _bpActivePid();
  // Switching projects re-baselines silently so we don't toast the new
  // project's pre-existing sessions as if they just opened.
  if (pid !== _bpPollPid) { _bpKnownSids = null; _bpPollPid = pid; }
  const sessions = await _bpFetchSessions(pid);
  const badge = document.getElementById('bp-badge');
  if (badge) {
    if (sessions.length) { badge.textContent = sessions.length; badge.style.display = ''; }
    else badge.style.display = 'none';
  }
  const win = document.getElementById('mc-browser-pane');
  if (win) {
    const c = win.querySelector('[data-bp="sesscount"]');
    if (c) { if (sessions.length > 1) { c.textContent = sessions.length; c.style.display = ''; } else c.style.display = 'none'; }
  }
  const cur = new Set(sessions.map(s => s.session_id));
  if (_bpKnownSids !== null) {
    const fresh = sessions.filter(s => !_bpKnownSids.has(s.session_id) && s.session_id !== _bpSession);
    if (fresh.length) _bpNotifyNewSessions(fresh, pid);
  }
  _bpKnownSids = cur;
}

// An agent opens browsers in bursts (a login flow, then a submit flow), and one
// toast per session buried a phone screen under six of them. They coalesce into
// a single keyed toast that counts up instead.
let _bpPending = [];       // sessions announced but not yet viewed/dismissed
let _bpPendingPid = null;

function _bpShortUrl(u, max = 72) {
  const s = String(u || '');
  return s.length > max ? s.slice(0, max - 1) + '…' : s;
}

function _bpNotifyNewSessions(fresh, pid) {
  // The toast IS the way in. Telling the user to "click Browser" only works on
  // desktop — mobile (≤960px) hides .btn-popout entirely.
  const container = document.getElementById('toast-container');
  const showing = container && Array.from(container.children).some(
    c => c.dataset.toastKey === 'bp-new-session' && !c.classList.contains('toast-out'));
  // Once the previous toast is gone (viewed, swiped, or timed out) the count
  // starts over — otherwise a later single session would announce itself as #7.
  if (!showing || _bpPendingPid !== pid) { _bpPending = []; _bpPendingPid = pid; }

  for (const s of fresh) {
    if (_bpPending.some(p => p.sid === s.session_id)) continue;
    _bpPending.push({
      sid: s.session_id,
      url: (s.url && s.url !== 'about:blank') ? s.url : 'a new browser',
    });
  }
  const n = _bpPending.length;
  if (!n) return;
  const newest = _bpPending[n - 1];

  if (typeof showActionToast !== 'function') {
    if (typeof showToast === 'function') {
      showToast(`\u{1F310} ${n} browser session${n > 1 ? 's' : ''} opened`);
    }
    return;
  }
  const head = n === 1 ? 'A browser session opened' : `${n} browser sessions opened`;
  const sub = n === 1 ? newest.url : `Newest: ${newest.url}`;
  showActionToast(
    `\u{1F310} ${head}<br><span style="opacity:.7;font-size:11px;word-break:break-all">${_bpEsc(_bpShortUrl(sub))}</span>`,
    [{
      label: n === 1 ? 'View' : 'View newest', primary: true,
      onclick: () => { _bpPending = []; openBrowserPane(null, pid, newest.sid); },
    }],
    { key: 'bp-new-session', autoDismissMs: 12000 });
}
function _bpStartPoll() {
  if (_bpPollTimer) return;
  _bpPollStatus();
  _bpPollTimer = setInterval(_bpPollStatus, 5000);
}

// The 🌐 Browser button: attach to the most-recent running session if any
// (so you don't spawn duplicates), otherwise launch a fresh one.
async function browserButtonClick(pid) {
  const p = pid || _bpActivePid();
  const sessions = await _bpFetchSessions(p);
  if (sessions.length) {
    const byRecent = sessions.slice().sort((a, b) => (b.started_at || '').localeCompare(a.started_at || ''));
    // Prefer the most-recent session that's actually on a page, so clicking
    // Browser doesn't land you on a stray about:blank when a real one exists.
    const pick = byRecent.find(s => s.url && s.url !== 'about:blank') || byRecent[0];
    openBrowserPane(null, p, pick.session_id);
  } else {
    openBrowserPane('about:blank', p);
  }
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
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => { _bpRestoreOnLoad(); _bpStartPoll(); });
} else {
  setTimeout(() => { _bpRestoreOnLoad(); _bpStartPoll(); }, 0);
}

window.openBrowserPane = openBrowserPane;
window.closeBrowserPane = closeBrowserPane;
// Attach the UI pane to an already-running session (agent-launched via API).
window.attachBrowserPane = (sessionId, projectId) => openBrowserPane(null, projectId, sessionId);
window.browserButtonClick = browserButtonClick;
window.switchBrowserSession = switchBrowserSession;
window.stopBrowserSession = stopBrowserSession;
