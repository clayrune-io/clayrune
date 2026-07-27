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

async function openBrowserPane(url, projectId) {
  if (_bpSession) closeBrowserPane();
  const pid = projectId || window.currentProjectId ||
    (typeof activeProjectId !== 'undefined' ? activeProjectId : null) || 'mission_control';
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

  // ── DOM ──
  const win = document.createElement('div');
  win.id = 'mc-browser-pane';
  win.style.cssText =
    'position:fixed;inset:0;margin:auto;width:min(96vw,1120px);height:min(92vh,760px);' +
    'background:#1e1e1e;border:1px solid var(--border,#444);border-radius:10px;z-index:100000;' +
    'display:flex;flex-direction:column;box-shadow:0 12px 48px rgba(0,0,0,.5);overflow:hidden';
  win.innerHTML = `
    <div style="display:flex;align-items:center;gap:6px;padding:8px 10px;background:#2a2a2a;flex:0 0 auto">
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
    </div>`;
  document.body.appendChild(win);

  const $ = sel => win.querySelector(`[data-bp="${sel}"]`);
  const img = $('screen'), urlInput = $('url'), spin = $('spin');

  $('close').onclick = closeBrowserPane;
  $('back').onclick = () => _bpSend({ type: 'back' });
  $('fwd').onclick = () => _bpSend({ type: 'forward' });
  $('reload').onclick = () => _bpSend({ type: 'reload' });
  urlInput.addEventListener('keydown', e => {
    if (e.key === 'Enter') { _bpSend({ type: 'navigate', url: urlInput.value.trim() }); img.focus(); }
  });

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
}

function closeBrowserPane() {
  if (_bpES) { try { _bpES.close(); } catch (e) {} _bpES = null; }
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

window.openBrowserPane = openBrowserPane;
window.closeBrowserPane = closeBrowserPane;
