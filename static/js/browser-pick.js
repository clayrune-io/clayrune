// ── Browser pane element picker (UI half) ────────────────────────────────────
// A human aims at an element in the pane and ONE chip lands in the chat
// composer: the element's trimmed HTML, a few computed styles, a CSS selector
// path and a clipped screenshot. Backend: mc/blueprints/browser_pick_routes.py
// (human-only; the page's HTML never enters a prompt -- the chip is a path to a
// file whose content is wrapped in /api/browser/read's untrusted-content
// envelope). ES module → everything shared via window.* (see
// discovery_es_module_cross_boundary_globals).
//
// Input is pointer events, so one code path serves mouse and touch:
//   mouse   move = aim (highlight follows), click = pick
//   touch   press + drag = aim, lift = pick   (a phone has no hover)
// Esc, the banner's Cancel, or the toolbar button again leaves pick mode.
// Works on phones: the pane's mobile sheet gets the same button in its ⋮ menu.
//
// The pane (browser-pane.js) calls bpPickInit() once per opened pane and owns
// nothing else here; the composer (composer-extras.js) calls bpPickChipHTML()
// to draw a chip whose entry carries `pick`.

const BP_PICK_HOVER_GAP_MS = 60;   // min gap between hover requests (one in flight at a time)

const _bpPickEsc = (s) => (typeof esc === 'function' ? esc(String(s)) :
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;'));

// Which composer the chip goes to: the same keys the composer reads
// (conversation.js) -- `fu_<session>` for the open chat, else the project's
// new-dispatch box.
function _bpPickKey(pid) {
  const tab = (typeof activeAgentTab !== 'undefined' && activeAgentTab) ? activeAgentTab[pid] : null;
  const wantNew = (typeof agentConvNew !== 'undefined' && agentConvNew) ? agentConvNew[pid] === true : false;
  return (tab && !wantNew) ? 'fu_' + tab : pid;
}

function _bpPickThumb(path) {
  return (window.API_BASE || '') + '/api/serve-image?path=' + encodeURIComponent(path);
}

function _bpPickToast(msg, ms) {
  if (typeof showToast === 'function') showToast(msg, ms || 4000);
}

function bpPickChipHTML(img) {
  const p = img.pick || {};
  const label = _bpPickEsc(p.label || 'element');
  const title = _bpPickEsc((p.label || 'element') + (p.selector ? ' — ' + p.selector : ''));
  if (p.thumbUrl) {
    return `<img src="${_bpPickEsc(p.thumbUrl)}" alt="picked element" title="${title}">` +
      `<div style="position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.7);color:#fff;` +
      `font-size:9px;line-height:1.3;padding:1px 3px;white-space:nowrap;overflow:hidden;` +
      `text-overflow:ellipsis" data-pick-chip="label">${label}</div>`;
  }
  return `<div class="agent-file-preview" title="${title}" data-pick-chip="label">&#127919; ${label}</div>`;
}

function _bpPickAddChip(pid, d) {
  const key = _bpPickKey(pid);
  const list = agentPendingImages[key] || [];
  list.push({
    file: null, objectUrl: null,
    // serverPath set => the composer skips its upload and sends this path
    // (uploadAgentImages). The file is already in data/uploads.
    serverPath: d.context_path,
    isDocument: true,
    fileName: 'Picked ' + (d.label || d.tag || 'element'),
    pick: {
      label: d.label || d.tag || 'element',
      selector: d.selector || '',
      thumbUrl: d.screenshot_path ? _bpPickThumb(d.screenshot_path) : '',
    },
  });
  agentPendingImages[key] = list;
  if (typeof refreshModal === 'function') refreshModal();
  return key;
}

function bpPickInit(ctx) {
  const { win, img } = ctx;
  const stage = img && img.parentElement;
  if (!win || !stage) return;
  const buttons = Array.from(win.querySelectorAll('[data-bp="pick"], [data-bp="mm-pick"]'));
  let mode = null;   // {overlay, box, tag, banner, msg, onKey, inflight, queued, busy, timer} while picking

  const api = (path, body) => fetch((window.API_BASE || '') + path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }).then(async r => {
    let d = {};
    try { d = await r.json(); } catch (e) {}
    return { ok: r.ok && d && d.ok !== false, status: r.status, d };
  });

  // Client px -> the pane's picture px (the space /api/browser/input takes);
  // null when the point is in the letterbox bars, not on the picture.
  function toView(e) {
    const g = ctx.geometry();
    const x = (e.clientX - g.left) / g.scale, y = (e.clientY - g.top) / g.scale;
    if (!(x >= 0 && y >= 0 && x <= g.viewW && y <= g.viewH)) return null;
    return { x, y };
  }

  function paint(d) {
    if (!mode) return;
    const g = ctx.geometry();
    const o = mode.overlay.getBoundingClientRect();
    const r = d.rect;
    Object.assign(mode.box.style, {
      display: 'block',
      left: (g.left - o.left + r.x * g.scale) + 'px', top: (g.top - o.top + r.y * g.scale) + 'px',
      width: (r.w * g.scale) + 'px', height: (r.h * g.scale) + 'px',
    });
    mode.tag.textContent = d.label || '';
    mode.tag.style.display = d.label ? 'block' : 'none';
    // keep the label inside the overlay: above the box unless there is no room
    mode.tag.style.top = (parseFloat(mode.box.style.top) < 22 ? mode.box.offsetHeight + 2 : -20) + 'px';
  }

  function hideBox() {
    if (mode) mode.box.style.display = 'none';
  }

  function hover(pt) {
    if (!mode) return;
    mode.queued = pt;
    if (mode.inflight) return;
    const m = mode;
    const p = m.queued; m.queued = null; m.inflight = true;
    api('/api/browser/pick/hover', { session_id: ctx.getSession(), x: p.x, y: p.y })
      .then(res => {
        if (mode !== m) return;
        if (res.ok) paint(res.d); else hideBox();
      })
      .catch(() => { if (mode === m) hideBox(); })
      .finally(() => {
        m.timer = setTimeout(() => { m.inflight = false; if (mode === m && m.queued) hover(m.queued); },
          BP_PICK_HOVER_GAP_MS);
      });
  }

  async function pick(pt) {
    const m = mode;
    if (!m || m.busy) return;
    m.busy = true;
    m.msg.textContent = 'Picking…';
    let res;
    try {
      res = await api('/api/browser/pick', { session_id: ctx.getSession(), x: pt.x, y: pt.y });
    } catch (err) {
      res = { ok: false, d: { error: 'network', detail: String(err && err.message || err) } };
    }
    if (mode !== m) return;
    m.busy = false;
    if (!res.ok) {
      m.msg.textContent = m.hint;
      const d = res.d || {};
      _bpPickToast('Pick failed: ' + (d.detail || d.error || ('HTTP ' + res.status)), 6000);
      return;
    }
    const d = res.d;
    _bpPickAddChip(ctx.getProject(), d);
    stop();
    const bits = [];
    if (d.screenshot && d.screenshot !== 'attached') bits.push('no screenshot (' + d.screenshot + ')');
    if (d.truncated) bits.push('trimmed to the size cap');
    if (d.hidden_content_flagged) bits.push('hidden text stripped');
    _bpPickToast('Attached ' + (d.label || d.tag || 'element') + ' to the chat' +
      (bits.length ? ' — ' + bits.join(', ') : ''), 5000);
  }

  function start() {
    if (mode) return;
    const hint = 'Aim at an element, then click (touch: lift) to attach it to the chat';
    const overlay = document.createElement('div');
    overlay.dataset.bp = 'pick-overlay';
    overlay.style.cssText = 'position:absolute;inset:0;z-index:4;cursor:crosshair;touch-action:none;' +
      '-webkit-user-select:none;user-select:none;-webkit-touch-callout:none';
    overlay.innerHTML =
      '<div data-bp="pick-box" style="display:none;position:absolute;box-sizing:border-box;' +
        'border:2px solid #4aa3ff;background:rgba(74,163,255,.18);pointer-events:none">' +
        '<div data-bp="pick-tag" style="position:absolute;left:-2px;top:-20px;background:#4aa3ff;color:#000;' +
        'font:600 11px/16px monospace;padding:0 5px;border-radius:3px;white-space:nowrap;max-width:260px;' +
        'overflow:hidden;text-overflow:ellipsis"></div></div>' +
      '<div data-bp="pick-banner" style="position:absolute;left:8px;right:8px;top:8px;display:flex;gap:8px;' +
        'align-items:center;background:rgba(30,30,30,.92);border:1px solid #4aa3ff;border-radius:8px;' +
        'padding:6px 8px;color:#eee;font-size:12px;cursor:default">' +
        '<span data-bp="pick-msg" style="flex:1;min-width:0"></span>' +
        '<button data-bp="pick-cancel" style="background:none;border:1px solid #666;color:#ddd;' +
        'border-radius:6px;padding:4px 10px;cursor:pointer;font-size:12px">Cancel</button></div>';
    stage.appendChild(overlay);
    const m = mode = {
      overlay, hint,
      box: overlay.querySelector('[data-bp="pick-box"]'),
      tag: overlay.querySelector('[data-bp="pick-tag"]'),
      msg: overlay.querySelector('[data-bp="pick-msg"]'),
      inflight: false, queued: null, busy: false, timer: 0, onKey: null,
    };
    m.msg.textContent = hint;
    const banner = overlay.querySelector('[data-bp="pick-banner"]');
    for (const ev of ['pointerdown', 'pointerup', 'pointermove', 'click']) {
      banner.addEventListener(ev, e => e.stopPropagation());
    }
    overlay.querySelector('[data-bp="pick-cancel"]').onclick = stop;

    let pressed = false;
    overlay.addEventListener('pointerdown', e => {
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      e.preventDefault();
      pressed = true;
      try { overlay.setPointerCapture(e.pointerId); } catch (err) {}
      const pt = toView(e);
      if (pt) hover(pt); else hideBox();
    });
    overlay.addEventListener('pointermove', e => {
      // mouse aims on plain move; touch/pen only while pressed (a hovering pen is fine too)
      if (e.pointerType === 'touch' && !pressed) return;
      const pt = toView(e);
      if (pt) hover(pt); else hideBox();
    });
    overlay.addEventListener('pointerup', e => {
      if (!pressed) return;
      pressed = false;
      e.preventDefault();
      const pt = toView(e);
      if (pt) pick(pt); else hideBox();
    });
    overlay.addEventListener('pointercancel', () => { pressed = false; hideBox(); });
    overlay.addEventListener('contextmenu', e => e.preventDefault());

    m.onKey = (e) => {
      if (!win.isConnected) { stop(); return; }   // the pane was closed under us
      if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); stop(); }
    };
    window.addEventListener('keydown', m.onKey, true);
    buttons.forEach(b => b.setAttribute('aria-pressed', 'true'));
    const desk = win.querySelector('[data-bp="pick"]');
    if (desk) desk.style.background = 'rgba(74,163,255,.35)';
  }

  function stop() {
    const m = mode;
    if (!m) return;
    mode = null;
    clearTimeout(m.timer);
    window.removeEventListener('keydown', m.onKey, true);
    m.overlay.remove();
    buttons.forEach(b => b.setAttribute('aria-pressed', 'false'));
    const desk = win.querySelector('[data-bp="pick"]');
    if (desk) desk.style.background = 'none';
  }

  buttons.forEach(b => {
    b.onclick = (e) => {
      e.stopPropagation();
      const menu = win.querySelector('[data-bp="mobmenu"]');
      if (menu) menu.style.display = 'none';
      if (mode) stop(); else start();
    };
  });
}

window.bpPickInit = bpPickInit;
window.bpPickChipHTML = bpPickChipHTML;
