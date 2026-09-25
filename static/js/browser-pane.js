// ── Browser pane (Part B) ────────────────────────────────────────────────────
// A visible, interactive browser inside Clayrune. Backend (mc/blueprints/
// browser_routes.py) runs a headless Chromium and screencasts JPEG frames over
// CDP; this pane renders them into an <img> and forwards mouse/keyboard/scroll
// back to /api/browser/input (coords scaled from the displayed image to the
// fixed VIEW_W×VIEW_H render viewport). ES module → everything shared via
// window.* (see discovery_es_module_cross_boundary_globals).

// Human-opened panes default to this persistent profile instead of a
// throwaway one — before this, every login typed into a hand-opened pane was
// gone on the next open (Ron, 2026-09-24). Agent launches never go through
// this file (they POST /api/browser/launch directly), so they are untouched
// and stay throwaway unless an agent names a profile itself. Deliberately a
// constant here rather than `browser_default_profile` in config.json: that
// config key feeds the BACKEND's unnamed-launch default
// (browser_routes._default_profile), which — if set — would apply to every
// unnamed launch including agent ones. Keeping config empty and defaulting
// only in this human-facing file is what keeps the two paths separate.
const BP_DEFAULT_PROFILE = 'main';

const BP_VIEW_W = 1280, BP_VIEW_H = 800;
// The page coordinate space clicks are mapped into. NOT a constant: CDP's
// Emulation.setDeviceMetricsOverride does not take effect, so a 1280x800
// window actually renders a 1264x649 content viewport once browser chrome and
// the scrollbar come out. Mapping into a hardcoded 1280x800 put a click at the
// bottom edge ~150px below where the user aimed, which is what made the pane
// feel misaligned on first load. The server now ships the frame's real
// deviceWidth/deviceHeight and we scale to that; the constants are only a
// first-frame fallback.
let _bpViewW = BP_VIEW_W, _bpViewH = BP_VIEW_H;
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

// Copy the page's current selection to the HOST clipboard via
// /api/browser/selection — shared by the Ctrl/Cmd+C keydown handler and the
// toolbar copy button so both paths behave identically.
async function _bpCopySelection(cut) {
  try {
    const r = await fetch((window.API_BASE || '') + '/api/browser/selection', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: _bpSession }),
    });
    const d = await r.json();
    if (d && d.text) {
      await navigator.clipboard.writeText(d.text);
      if (cut) _bpSend({ type: 'key', key: 'Delete', code: 'Delete', keyCode: 46 });
      if (typeof showToast === 'function') showToast(cut ? 'Cut to clipboard' : 'Copied to clipboard');
    } else if (typeof showToast === 'function') {
      showToast('Nothing selected in the page');
    }
  } catch (err) {
    if (typeof showToast === 'function') showToast('Copy failed: ' + (err && err.message || err));
  }
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

// The <img> is width:100%;height:100% (fills the pane) with object-fit:contain
// (letterboxes the frame inside it) -- so img.getBoundingClientRect() is the
// WHOLE pane, not the picture. Every coordinate/delta must map through the
// CONTENT rect (the letterboxed picture itself), not the element box, or a
// click on the frame's edge lands in a letterbox bar instead.
function _bpContentRect(img) {
  const r = img.getBoundingClientRect();
  const scale = Math.min(r.width / _bpViewW, r.height / _bpViewH) || 1;
  const w = _bpViewW * scale, h = _bpViewH * scale;
  return { left: r.left + (r.width - w) / 2, top: r.top + (r.height - h) / 2, width: w, height: h, scale };
}

function _bpCoords(img, e) {
  const c = _bpContentRect(img);
  return {
    x: Math.max(0, Math.min(_bpViewW, (e.clientX - c.left) / c.scale)),
    y: Math.max(0, Math.min(_bpViewH, (e.clientY - c.top) / c.scale)),
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
  let curProfile = null;
  if (sessionId) {
    // Attach mode: adopt the existing session, read its current URL for the bar.
    _bpSession = sessionId;
    try {
      const st = await fetch((window.API_BASE || '') +
        `/api/project/${encodeURIComponent(pid)}/browser/status`).then(r => r.json());
      const s = (st.sessions || []).find(x => x.session_id === sessionId);
      if (s) { curProfile = s.profile || null; if (!url) url = s.url; }
    } catch (e) {}
  } else {
    let data;
    try {
      const res = await fetch((window.API_BASE || '') + '/api/browser/launch', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ project_id: pid, url: url || 'about:blank',
                               profile: profile || BP_DEFAULT_PROFILE,
                               dpr: window.devicePixelRatio || 1 }),
      });
      data = await res.json();
      if (!res.ok) throw new Error(data.error || 'launch failed');
    } catch (e) {
      alert('Browser pane unavailable: ' + e.message);
      return;
    }
    _bpSession = data.session_id;
    curProfile = data.profile || null;
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
      <button data-bp="copy"   title="Copy page selection to clipboard" style="background:none;border:none;color:#ddd;font-size:13px;cursor:pointer;padding:2px 6px">&#128196;</button>
      <button data-bp="sessions" title="Browser sessions" style="background:none;border:none;color:#ddd;font-size:15px;cursor:pointer;padding:2px 6px;position:relative">&#9776;<span data-bp="sesscount" style="position:absolute;top:-3px;right:-3px;background:#4caf50;color:#000;font-size:9px;font-weight:700;border-radius:8px;padding:0 4px;line-height:14px;display:none"></span></button>
      <input data-bp="url" type="text" spellcheck="false" value="${(url||'').replace(/"/g,'&quot;')}"
        placeholder="Enter URL and press Enter"
        style="flex:1;min-width:60px;padding:6px 10px;font-size:13px;background:#111;border:1px solid #444;border-radius:6px;color:#eee;outline:none">
      <span data-bp="profile" title="" style="font-size:10px;padding:0 6px;border:1px solid #4a4a4a;border-radius:99px;color:#9ecb9e;flex:0 0 auto;cursor:pointer;display:none"></span>
      <span data-bp="spin" style="color:#888;font-size:12px;width:14px">&#9679;</span>
      <button data-bp="minimize" title="Minimize" style="background:none;border:none;color:#ddd;font-size:16px;cursor:pointer;padding:2px 8px">&#8211;</button>
      <button data-bp="close" title="Close" style="background:none;border:none;color:#ddd;font-size:16px;cursor:pointer;padding:2px 8px">&#10005;</button>
    </div>
    <div data-bp="tabstrip" style="display:none;flex:0 0 auto;gap:2px;padding:4px 8px 0;background:#242424;overflow-x:auto"></div>
    <div style="flex:1;position:relative;background:#000;display:flex;align-items:center;justify-content:center;overflow:hidden">
      <img data-bp="screen" tabindex="0"
        style="width:100%;height:100%;object-fit:contain;aspect-ratio:${BP_VIEW_W}/${BP_VIEW_H};outline:none;cursor:default;user-select:none" draggable="false">
      <!-- Real, editable keyboard-focus target (gap #7, IME). A plain
           tabindex <img> can receive keydown but browsers only ever engage an
           OS IME (Pinyin/Japanese/Korean input) over an editable element — a
           bare img never gets a compositionstart no matter how it's focused.
           This input takes over keyboard focus in its place; kept 1x1 and
           positioned at the last click so its native candidate window still
           tracks the caret roughly where the user is looking. -->
      <input data-bp="ime-shadow" type="text" autocomplete="off" autocapitalize="off" spellcheck="false"
        style="position:absolute;left:0;top:0;width:1px;height:1px;padding:0;border:0;opacity:0;pointer-events:none">
      <div data-bp="downloads" style="position:absolute;right:8px;bottom:8px;display:flex;flex-direction:column;gap:4px;max-width:280px;pointer-events:none"></div>
      <div data-bp="dialog-overlay" style="display:none;position:absolute;inset:0;background:rgba(0,0,0,.55);align-items:center;justify-content:center;z-index:5">
        <div data-bp="dialog-box" style="background:#2a2a2a;border:1px solid #4a4a4a;border-radius:8px;padding:16px;width:320px;max-width:90%;color:#eee;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.5)">
          <div data-bp="dialog-msg" style="white-space:pre-wrap;word-break:break-word;margin-bottom:10px;max-height:160px;overflow:auto"></div>
          <input data-bp="dialog-input" type="text" style="display:none;width:100%;box-sizing:border-box;padding:6px 8px;margin-bottom:10px;background:#111;border:1px solid #444;border-radius:6px;color:#eee">
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button data-bp="dialog-cancel" style="background:none;border:1px solid #555;color:#ddd;border-radius:6px;padding:5px 12px;cursor:pointer">Cancel</button>
            <button data-bp="dialog-ok" style="background:#3a7ae0;border:none;color:#fff;border-radius:6px;padding:5px 14px;cursor:pointer">OK</button>
          </div>
        </div>
      </div>
      <div data-bp="filechooser-overlay" style="display:none;position:absolute;inset:0;background:rgba(0,0,0,.55);align-items:center;justify-content:center;z-index:5">
        <div data-bp="filechooser-box" style="background:#2a2a2a;border:1px solid #4a4a4a;border-radius:8px;padding:16px;width:320px;max-width:90%;color:#eee;font-size:13px;box-shadow:0 8px 24px rgba(0,0,0,.5)">
          <div style="margin-bottom:10px">The page wants a file<span data-bp="filechooser-multi" style="display:none">s</span>.</div>
          <input data-bp="filechooser-input" type="file" style="width:100%;margin-bottom:10px;color:#eee">
          <div style="display:flex;justify-content:flex-end;gap:8px">
            <button data-bp="filechooser-cancel" style="background:none;border:1px solid #555;color:#ddd;border-radius:6px;padding:5px 12px;cursor:pointer">Cancel</button>
            <button data-bp="filechooser-ok" style="background:#3a7ae0;border:none;color:#fff;border-radius:6px;padding:5px 14px;cursor:pointer">Upload</button>
          </div>
        </div>
      </div>
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
  const imeShadow = $('ime-shadow');

  // Profile indicator — the sessions menu already lets you SWITCH profile,
  // but gave no ambient sign of which one a pane is currently on. A signed-in
  // ('main' or a named saved login) badge is green; a throwaway session (only
  // reachable today via an agent launch with no profile) is dim so a
  // just-typed login is visibly at risk of being lost on close.
  const profileBadge = $('profile');
  const _bpSetProfileBadge = (name) => {
    if (name) {
      profileBadge.textContent = name;
      profileBadge.title = `Signed-in profile: ${name} — click for sessions/profiles`;
      profileBadge.style.display = '';
      profileBadge.style.color = '#9ecb9e'; profileBadge.style.borderColor = '#4a4a4a';
    } else {
      profileBadge.textContent = 'temp';
      profileBadge.title = 'Throwaway session — closing it loses any login (click for sessions/profiles)';
      profileBadge.style.display = '';
      profileBadge.style.color = '#e0b366'; profileBadge.style.borderColor = '#5a4a30';
    }
  };
  _bpSetProfileBadge(curProfile);
  profileBadge.onclick = (e) => { e.stopPropagation(); _bpToggleSessionMenu(win, pid); };

  $('close').onclick = closeBrowserPane;
  $('minimize').onclick = () => _bpMinimizePane(win, pid);
  $('back').onclick = () => _bpSend({ type: 'back' });
  $('fwd').onclick = () => _bpSend({ type: 'forward' });
  $('reload').onclick = () => _bpSend({ type: 'reload' });
  // Touch has no Ctrl+V, so the keyboard path can't be the only way in. The
  // prompt box is itself a native paste target, which is what makes this work
  // on a phone (long-press → Paste) and wherever readText() is unavailable.
  $('paste').onclick = async () => {
    imeShadow.focus();
    if (await _bpPasteViaApi()) return;
    const t = prompt('Paste here and press OK — this types it into the page:');
    if (t) _bpSend({ type: 'text', text: t });
  };
  $('copy').onclick = async () => { imeShadow.focus(); await _bpCopySelection(false); };
  $('sessions').onclick = (e) => { e.stopPropagation(); _bpToggleSessionMenu(win, pid); };
  urlInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { _bpSend({ type: 'navigate', url: urlInput.value.trim() }); imeShadow.focus(); }
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
    e.preventDefault(); _bpPressed = true;
    // Keyboard focus goes to imeShadow, not img (see its declaration) — parked
    // at the click point so an OS IME's candidate window follows the caret.
    imeShadow.style.left = e.offsetX + 'px'; imeShadow.style.top = e.offsetY + 'px';
    imeShadow.focus();
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
  // Right-click: forward to the page as a real right button click instead of
  // popping Clayrune's own context menu over the frame. Without preventDefault
  // here the HOST page's menu ("Inspect", "Save image as…" for the <img>
  // itself) appeared instead of anything the target page could ever show.
  img.addEventListener('contextmenu', e => {
    e.preventDefault();
    const c = _bpCoords(img, e);
    _bpSend({ type: 'mouse', action: 'click', button: 'right', clickCount: 1, ...c });
  });
  img.addEventListener('wheel', e => {
    e.preventDefault(); const c = _bpCoords(img, e);
    _bpSend({ type: 'wheel', deltaX: e.deltaX, deltaY: e.deltaY, ...c });
  }, { passive: false });
  imeShadow.addEventListener('keydown', async e => {
    // While an IME composition is in progress, the browser also fires keydown
    // for each keystroke that builds it (e.g. every Latin letter typed toward
    // a Pinyin candidate) with e.isComposing true. Forwarding those as text
    // too would double-send: the composed characters arrive again, correctly,
    // on compositionend below.
    if (e.isComposing) return;
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
      await _bpCopySelection(k === 'x');
      return;
    }
    if ((e.ctrlKey || e.metaKey || e.altKey) &&
        !['Control', 'Alt', 'Meta', 'Shift'].includes(e.key)) {
      // Forward every other modified combo (Ctrl+F, Ctrl+A, Alt+ArrowDown, …)
      // to the PAGE instead of letting it through to the host (gap #7) —
      // before this, `return` here left the keydown unhandled and Clayrune's
      // own browser chrome caught it (Ctrl+F opened the host's Find, not the
      // pane's). _key_event_params (browser_routes.py) already parses this
      // "Ctrl+f"-style combo string, same convention _bpKeyCodes/{v,c,x} above
      // don't need since those are pane-local shortcuts, not page ones.
      // The e.key exclusion matters: pressing Ctrl alone fires its OWN keydown
      // (key:'Control', ctrlKey:true) before the letter's — without it every
      // Ctrl-anything chord sent a bogus leading "Ctrl+Control" first.
      e.preventDefault();
      let combo = '';
      if (e.ctrlKey) combo += 'Ctrl+';
      if (e.altKey) combo += 'Alt+';
      if (e.metaKey) combo += 'Meta+';
      combo += e.key;
      _bpSend({ type: 'key', key: combo, code: e.code, keyCode: e.keyCode });
      return;
    }
    // A bare modifier press (Ctrl/Alt/Meta/Shift alone, no other key yet) is
    // not a shortcut and forwards nothing — but IS the host's own chord in
    // progress, so let it through rather than preventDefault-ing it for no
    // reason.
    if (['Control', 'Alt', 'Meta', 'Shift'].includes(e.key)) return;
    e.preventDefault();
    if (e.key.length === 1) _bpSend({ type: 'text', text: e.key });
    else if (_bpKeyCodes[e.key] != null)
      _bpSend({ type: 'key', key: e.key, code: e.code, keyCode: _bpKeyCodes[e.key] });
  });

  // ── IME composition (gap #7) — CJK and other composed input. imeShadow is a
  // real editable <input> (see its declaration) because Chromium only ever
  // engages an OS IME over an editable element; a bare tabindex <img> never
  // gets a compositionstart no matter how it's focused. 'update' mirrors the
  // in-progress (underlined, uncommitted) text so the PAGE's own composition
  // UI tracks it; 'end' commits the final text via insertText. imeShadow's
  // own value is cleared after commit so it never accumulates text the pane
  // has already forwarded.
  imeShadow.addEventListener('compositionupdate', e => {
    _bpSend({ type: 'ime', phase: 'update', text: e.data || '' });
  });
  imeShadow.addEventListener('compositionend', e => {
    _bpSend({ type: 'ime', phase: 'end', text: e.data || '' });
    imeShadow.value = '';
  });

  // The primary paste path. Fires on the focused imeShadow input and carries
  // the text directly, so it needs no clipboard permission, works over plain
  // http on the LAN, and works where readText() does not exist.
  imeShadow.addEventListener('paste', e => {
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
    imeShadow.focus();
  }, { passive: true });
  img.addEventListener('touchmove', e => {
    if (!touch || e.touches.length !== 1) return;
    e.preventDefault();
    const t = e.touches[0], c = _bpContentRect(img);
    if (Math.abs(t.clientX - touch.sx) > 6 || Math.abs(t.clientY - touch.sy) > 6) touch.moved = true;
    // finger up → content scrolls down: deltaY = (prev - current), scaled to page px.
    // One uniform scale (not separate w/h ratios) -- the content rect never
    // distorts the frame's aspect, so x and y scale by the same factor.
    _bpSend({
      type: 'wheel', ..._bpCoords(img, t),
      deltaX: (touch.x - t.clientX) / c.scale,
      deltaY: (touch.y - t.clientY) / c.scale,
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
  // Fallback for the frame's true size when the server is older than this
  // file and sends no w/h: the decoded JPEG IS the viewport. CDP only scales a
  // frame down when the viewport exceeds maxWidth/maxHeight, and the window is
  // launched at exactly that size, so naturalWidth/Height is the page's own
  // coordinate space. Without this, clicks map into a 1280x800 space that does
  // not exist and land up to ~150px low.
  img.addEventListener('load', () => {
    const w = img.naturalWidth, h = img.naturalHeight;
    if (w && h && (w !== _bpViewW || h !== _bpViewH)) {
      _bpViewW = w; _bpViewH = h;
      img.style.aspectRatio = w + '/' + h;
    }
  });
  _bpES.onmessage = ev => {
    let d; try { d = JSON.parse(ev.data); } catch { return; }
    if (d.w && d.h && (d.w !== _bpViewW || d.h !== _bpViewH)) {
      // Adopt the frame's real viewport for BOTH hit-testing and layout. Setting
      // aspect-ratio from the frame also kills the vertical stretch the fixed
      // 1280/800 ratio caused (an <img> defaults to object-fit:fill).
      _bpViewW = d.w; _bpViewH = d.h;
      img.style.aspectRatio = d.w + '/' + d.h;
    }
    if (d.img) { img.src = 'data:image/jpeg;base64,' + d.img; spin.style.color = '#4caf50'; }
    if (d.url && document.activeElement !== urlInput) urlInput.value = d.url;
    if (d.status && d.status !== 'running') { spin.textContent = '×'; spin.style.color = '#e57373'; }
    // A download never repaints the page (Chromium generates no screencast
    // frame for it — see the root-cause note on Browser.downloadWillBegin in
    // browser_routes.py), so this SSE message is the ONLY signal a download
    // happened at all. Without it the pane just sits there looking frozen.
    if (d.downloads) _bpRenderDownloads(win, d.downloads);
    // 'tabs'/'dialog' membership (not truthiness) matters: an empty tabs array
    // and a cleared (null) dialog are both real, intentional states the SSE
    // stream sends deliberately (see _stream_gen) — a falsy check would treat
    // "dialog just closed" as "no update" and leave the answer box stuck up.
    if ('tabs' in d) _bpRenderTabs(win, d.tabs, d.active_target_id);
    if ('dialog' in d) _bpRenderDialog(win, d.dialog);
    if ('file_chooser' in d) _bpRenderFileChooser(win, d.file_chooser);
  };
  _bpES.onerror = () => { if (spin) spin.style.color = '#e57373'; };
  setTimeout(() => imeShadow.focus(), 100);
  // Remember the open session so a page refresh (which wipes the SPA DOM but
  // leaves the backend Chromium running) can re-attach instead of orphaning it.
  try { localStorage.setItem('mc_browser_pane_open', JSON.stringify({ sid: _bpSession, pid })); } catch (e) {}
}

// ── minimize/restore ────────────────────────────────────────────────────────
// Reuses the SAME dock (#minimized-tray) and chip styling (.minimized-chip)
// every other modal minimizes into (modal-manager.js: minimizeModal /
// restoreModal) for visual consistency, but the pane isn't registered in
// modal-manager's `openModals` map — it's a standalone element, not a
// project modal — so this is a parallel, self-contained implementation
// rather than a call into minimizeModal/restoreModal.
//
// What "costs nothing hidden" means here: the backend Chromium session and
// the SSE connection both stay alive (so a download finishing while
// minimized still reaches the pane and shows a toast — see the `downloads`
// branch in the SSE handler above), but Page.startScreencast is explicitly
// stopped, which is the one part of this pipeline with a real per-frame
// cost (JPEG capture + base64 + transfer at up to 30fps). Restoring re-arms
// it, same as a fresh navigation does server-side.
let _bpMinimizedChip = null;

function _bpMinimizePane(win, pid) {
  if (!win || !win.isConnected) return;
  const tray = document.getElementById('minimized-tray');
  if (!tray) return;  // no dock on this surface — nothing to minimize into
  win.style.display = 'none';
  _bpSend({ type: 'screencast', action: 'stop' });
  if (_bpMinimizedChip) { try { _bpMinimizedChip.remove(); } catch (e) {} }
  const chip = document.createElement('div');
  chip.className = 'minimized-chip';
  chip.id = 'chip-bp-' + _bpSession;
  const urlInput = win.querySelector('[data-bp="url"]');
  const label = ((urlInput && urlInput.value) || 'Browser').replace(/^https?:\/\//, '');
  chip.innerHTML = `
    <span class="chip-status" style="background:#4caf50"></span>
    <span>&#127760; ${_bpEsc(label)}</span>
    <span class="chip-close" title="Close">&#10005;</span>`;
  chip.addEventListener('click', (e) => {
    if (e.target.closest('.chip-close')) { closeBrowserPane(); return; }
    _bpRestorePane(win);
  });
  tray.appendChild(chip);
  _bpMinimizedChip = chip;
}

function _bpRestorePane(win) {
  if (!win) return;
  win.style.display = 'flex';
  _bpSend({ type: 'screencast', action: 'start' });
  if (_bpMinimizedChip) { try { _bpMinimizedChip.remove(); } catch (e) {} _bpMinimizedChip = null; }
  try { win.style.zIndex = nextModalZ++; } catch (e) {}
  const imeShadow = win.querySelector('[data-bp="ime-shadow"]');
  if (imeShadow) imeShadow.focus();
}

let _bpDoneToasted = new Set();

function _bpRenderDownloads(win, downloads) {
  const box = win && win.querySelector('[data-bp="downloads"]');
  if (!box) return;
  // 'canceled' used to be filtered out here — but a cap-cancel or a
  // denied-in-a-throwaway-session download IS the outcome, not noise, and
  // hiding it left the pane looking exactly as frozen as the no-signal-at-all
  // bug this whole downloads UI exists to fix. Show it with its reason.
  box.innerHTML = (downloads || []).map(d => {
    const pct = d.total_bytes ? Math.round(100 * d.received_bytes / d.total_bytes) : null;
    const label = _bpEsc(d.filename || d.guid);
    if (d.state === 'completed') {
      const href = d.serve_url ? (window.API_BASE || '') + d.serve_url : null;
      return `<div style="pointer-events:auto;background:#242424;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:11px;color:#eee">
        <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">&#10003; ${label}</div>
        ${href ? `<a href="${href}" target="_blank" style="color:#8ab4f8">Open / download</a>`
               : `<span style="color:#e57373">${_bpEsc(d.error || 'could not be saved')}</span>`}
      </div>`;
    }
    if (d.state === 'canceled') {
      return `<div style="pointer-events:auto;background:#242424;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:11px;color:#eee">
        <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">&#10005; ${label}</div>
        <span style="color:#e57373">${_bpEsc(d.error || 'canceled')}</span>
      </div>`;
    }
    return `<div style="pointer-events:none;background:#242424;border:1px solid #444;border-radius:6px;padding:6px 8px;font-size:11px;color:#eee">
      <div style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">&#8681; ${label}</div>
      <div style="height:3px;background:#111;border-radius:2px;margin-top:4px;overflow:hidden">
        <div style="height:100%;background:#4caf50;width:${pct != null ? pct : 30}%"></div>
      </div>
    </div>`;
  }).join('');
  for (const d of (downloads || [])) {
    if (d.state === 'completed' && !_bpDoneToasted.has(d.guid)) {
      _bpDoneToasted.add(d.guid);
      if (typeof showToast === 'function') {
        showToast(d.serve_url ? `\u{1F4E5} Downloaded: ${d.filename}`
                               : `Download finished but couldn't be saved: ${d.error || d.filename}`);
      }
    }
    if (d.state === 'canceled' && !_bpDoneToasted.has(d.guid)) {
      _bpDoneToasted.add(d.guid);
      if (typeof showToast === 'function') showToast(`Download canceled: ${d.error || d.filename}`);
    }
  }
}

// ── tab strip — window.open()/target=_blank/OAuth popups surfaced as tabs ──
// `tabs` is the array the `tabs` SSE payload carries (see _stream_gen);
// `activeId` is session['active_target_id']. Hidden entirely with one tab
// (the common case) so a plain page doesn't grow a strip nobody needs.
function _bpRenderTabs(win, tabs, activeId) {
  const strip = win && win.querySelector('[data-bp="tabstrip"]');
  if (!strip) return;
  tabs = tabs || [];
  if (tabs.length < 2) { strip.style.display = 'none'; strip.innerHTML = ''; return; }
  strip.style.display = 'flex';
  strip.innerHTML = tabs.map(t => {
    const active = t.target_id === activeId;
    const label = _bpEsc(t.title || t.url || 'New tab').slice(0, 40);
    return `<div data-bp-tab="${_bpEsc(t.target_id)}" title="${_bpEsc(t.url || '')}"
      style="display:flex;align-items:center;gap:6px;max-width:180px;padding:5px 8px;border-radius:6px 6px 0 0;
      cursor:pointer;font-size:11px;color:${active ? '#fff' : '#aaa'};background:${active ? '#111' : '#2f2f2f'};
      white-space:nowrap;overflow:hidden">
      <span style="overflow:hidden;text-overflow:ellipsis">${label}</span>
      <span data-bp-tab-close="${_bpEsc(t.target_id)}" style="opacity:.7;padding:0 2px">&#10005;</span>
    </div>`;
  }).join('');
  strip.querySelectorAll('[data-bp-tab]').forEach(el => {
    el.addEventListener('click', (e) => {
      if (e.target.closest('[data-bp-tab-close]')) return;
      _bpSendTabAction('activate', el.getAttribute('data-bp-tab'));
    });
  });
  strip.querySelectorAll('[data-bp-tab-close]').forEach(el => {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      _bpSendTabAction('close', el.getAttribute('data-bp-tab-close'));
    });
  });
}

function _bpSendTabAction(action, targetId) {
  if (!_bpSession) return;
  fetch((window.API_BASE || '') + '/api/browser/tab', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: _bpSession, target_id: targetId, action }),
  }).catch(() => {});
}

// ── JS dialogs (alert/confirm/prompt) — Page.javascriptDialogOpening ──
// `dlg` is null when there is nothing to answer (including "just closed");
// an unanswered dialog only pauses the PAGE's own JS (see browser_dialog's
// docstring) so leaving this overlay up costs nothing but does need to be
// torn down the moment the backend reports the dialog gone.
function _bpRenderDialog(win, dlg) {
  const overlay = win && win.querySelector('[data-bp="dialog-overlay"]');
  if (!overlay) return;
  if (!dlg) { overlay.style.display = 'none'; return; }
  const msgEl = win.querySelector('[data-bp="dialog-msg"]');
  const inputEl = win.querySelector('[data-bp="dialog-input"]');
  const cancelBtn = win.querySelector('[data-bp="dialog-cancel"]');
  const okBtn = win.querySelector('[data-bp="dialog-ok"]');
  const isPrompt = dlg.type === 'prompt';
  msgEl.textContent = dlg.message || '';
  inputEl.style.display = isPrompt ? '' : 'none';
  inputEl.value = dlg.default_prompt || '';
  // beforeunload has no meaningful Cancel/OK distinction here — the pane isn't
  // actually navigating away underneath the user, so treat it like a confirm.
  cancelBtn.style.display = dlg.type === 'alert' ? 'none' : '';
  overlay.style.display = 'flex';
  const answer = (accept) => {
    if (!_bpSession) return;
    fetch((window.API_BASE || '') + '/api/browser/dialog', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: _bpSession, accept, text: isPrompt ? inputEl.value : '' }),
    }).catch(() => {});
    overlay.style.display = 'none';
  };
  okBtn.onclick = () => answer(true);
  cancelBtn.onclick = () => answer(false);
  if (isPrompt) setTimeout(() => inputEl.focus(), 0);
}

// ── file chooser (Page.fileChooserOpened) — a picker on the USER's device ──
// Before this, <input type=file> inside the pane opened Chromium's native
// picker on the SERVER's desktop (invisible to whoever is looking at the
// pane, and blocking the page until someone at the server dismissed it — see
// browser_file_chooser's docstring). The backend intercepts it and reports
// `mode` here instead; this overlay is what actually lets the human pick a
// file from THEIR OWN device (works over the phone/remote console too,
// same as the rest of the pane) and uploads it through the multipart route,
// which is the only path that ever reaches DOM.setFileInputFiles — there is
// no field anywhere a caller can put a server path in its place.
function _bpRenderFileChooser(win, fc) {
  const overlay = win && win.querySelector('[data-bp="filechooser-overlay"]');
  if (!overlay) return;
  if (!fc) { overlay.style.display = 'none'; return; }
  const inputEl = win.querySelector('[data-bp="filechooser-input"]');
  const multiEl = win.querySelector('[data-bp="filechooser-multi"]');
  const cancelBtn = win.querySelector('[data-bp="filechooser-cancel"]');
  const okBtn = win.querySelector('[data-bp="filechooser-ok"]');
  const multi = fc.mode === 'selectMultiple';
  inputEl.multiple = multi;
  inputEl.value = '';
  multiEl.style.display = multi ? '' : 'none';
  overlay.style.display = 'flex';
  const cancel = () => {
    if (!_bpSession) return;
    const fd = new FormData();
    fd.append('session_id', _bpSession);
    fd.append('action', 'cancel');
    fetch((window.API_BASE || '') + '/api/browser/file-chooser', { method: 'POST', body: fd }).catch(() => {});
    overlay.style.display = 'none';
  };
  cancelBtn.onclick = cancel;
  okBtn.onclick = () => {
    if (!_bpSession || !inputEl.files.length) return;
    const fd = new FormData();
    fd.append('session_id', _bpSession);
    for (const f of inputEl.files) fd.append('file', f);
    fetch((window.API_BASE || '') + '/api/browser/file-chooser', { method: 'POST', body: fd })
      .then(r => r.json()).then(d => {
        if (d && d.error && typeof showToast === 'function') showToast('Upload failed: ' + d.error);
      }).catch(() => {});
    overlay.style.display = 'none';
  };
}

function closeBrowserPane() {
  if (_bpMinimizedChip) { try { _bpMinimizedChip.remove(); } catch (e) {} _bpMinimizedChip = null; }
  if (_bpES) { try { _bpES.close(); } catch (e) {} _bpES = null; }
  if (_bpUpHandler) { window.removeEventListener('mouseup', _bpUpHandler); _bpUpHandler = null; }
  _bpPressed = false;
  // Reset the viewport guess: the next session may render at a different
  // size, and a stale value would mis-map every click before its first frame.
  _bpViewW = BP_VIEW_W; _bpViewH = BP_VIEW_H;
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
  if (_bpMinimizedChip) { try { _bpMinimizedChip.remove(); } catch (e) {} _bpMinimizedChip = null; }
  if (_bpES) { try { _bpES.close(); } catch (e) {} _bpES = null; }
  if (_bpUpHandler) { window.removeEventListener('mouseup', _bpUpHandler); _bpUpHandler = null; }
  _bpPressed = false;
  // Reset the viewport guess: the next session may render at a different
  // size, and a stale value would mis-map every click before its first frame.
  _bpViewW = BP_VIEW_W; _bpViewH = BP_VIEW_H;
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
