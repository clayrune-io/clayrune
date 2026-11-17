// Desk loader (MC-1205). The only Desk code that is part of dashboard boot: a
// small module that loads the rest of the Desk, once, in the background, and
// lets openDesk() wait for that same load.
//
//   - Once the dashboard has finished booting and the browser is idle, import
//     desk-v1-entry.js, which pulls in the Desk's scripts and stylesheets.
//   - openDesk() (desk-open.js) calls open(). If the Desk is already loaded this
//     is a plain deskV1Open(). If not, it paints a Desk window with the home
//     page's loading frame IN THE SAME FRAME as the click, then swaps in the real
//     Desk when the load settles. It never starts a second load: every caller
//     shares one promise.
//   - Code outside the Desk that needs Desk code (the vault popup, the secrets
//     panel, browser accounts) awaits DeskLoad.ready() instead of loading a Desk
//     file itself.
//
// The version token: see desk-v1-entry.js. This module's own `?v=` (stamped by
// the server on this tag) is what it hands on to the entry.
(function () {
  const ENTRY = '/static/js/desk-v1-entry.js';
  const MODAL_ID = '__desk';
  // After the dashboard says it has booted, wait for an idle moment, but never
  // longer than this: the Desk is background work, not something to starve.
  const IDLE_TIMEOUT_MS = 4000;
  // If the boot never reports completion (it threw part-way), start anyway.
  const BOOT_GIVE_UP_MS = 30000;

  let _promise = null;
  let _loaded = false;

  function _token() {
    try { return new URL(import.meta.url).searchParams.get('v') || ''; } catch (_) { return ''; }
  }

  // The one load. A failure clears the promise so Try again can start over. The
  // retry asks for the entry under a new URL (`&try=N`) because a browser keeps a
  // FAILED module fetch per URL; desk-v1-entry.js knows what already ran and only
  // requests what failed.
  let _attempt = 0;
  function ready() {
    if (_promise) return _promise;
    const token = _token();
    const q = [];
    if (token) q.push('v=' + encodeURIComponent(token));
    if (_attempt) q.push('try=' + _attempt);
    _promise = import(ENTRY + (q.length ? '?' + q.join('&') : ''))
      .then((m) => m.loadDesk())
      .then(() => { _loaded = true; })
      .catch((e) => { _promise = null; _attempt++; throw e; });
    return _promise;
  }

  function _modalEntry() { return (typeof openModals !== 'undefined') ? openModals.get(MODAL_ID) : null; }

  // The Desk window with nothing in it but the home page's loading frame. It is
  // registered as the `__desk` modal so closing, minimizing and stacking work,
  // but it has no `.desk-v1-shell`: anything waiting for the real Desk keeps
  // waiting until the swap.
  let _placeholder = null;
  function _paintPlaceholder() {
    const existing = _modalEntry();
    if (existing && existing.element === _placeholder) {
      if (existing.minimized && typeof restoreModal === 'function') restoreModal(MODAL_ID);
      focusModal(MODAL_ID);
      return;
    }
    const win = document.createElement('div');
    win.className = 'modal-window';
    win.dataset.modalId = MODAL_ID;
    const content = document.createElement('div');
    content.className = 'modal-content';
    _clampModalSize(content, 1080);
    const max = (typeof window._maxBtnInner === 'function') ? window._maxBtnInner(false) : '&#9633;';
    content.innerHTML = `
      <div class="modal-header" style="display:flex;align-items:center;justify-content:space-between;padding:16px 24px 12px 28px">
        <span style="font-size:16px;font-weight:700;color:var(--text)">The Desk</span>
        <div class="modal-window-controls" style="position:static;display:flex;gap:4px">
          <button class="modal-minimize" onclick="minimizeModal('${MODAL_ID}')" title="Minimize">&#x2015;</button>
          <button class="modal-maximize" onclick="toggleModalMaximize('${MODAL_ID}')" title="Maximize" aria-label="Maximize">${max}</button>
          <button class="modal-close" onclick="closeModalById('${MODAL_ID}')" title="Close">&#10005;</button>
        </div>
      </div>
      <div class="desk-v1-preload" id="desk-v1-preload"></div>`;
    win.appendChild(content);
    document.getElementById('modal-layer').appendChild(win);
    const z = nextModalZ++;
    win.style.zIndex = z;
    openModals.set(MODAL_ID, { projectId: null, element: win, minimized: false, zIndex: z });
    centerModalElement(win);
    focusModal(MODAL_ID);
    _placeholder = win;
    _paintBody({ kind: 'loading', message: 'Opening the Desk…' });
  }

  function _paintBody(gate) {
    const host = document.getElementById('desk-v1-preload');
    if (!host) return;
    const L = window.DeskV1Loading;
    host.innerHTML = L ? L.preloadHTML(gate) : '';
    const retry = host.querySelector('[data-store-retry]');
    if (retry) retry.addEventListener('click', () => { _paintBody({ kind: 'loading', message: 'Opening the Desk…' }); _openWhenReady(); });
  }

  function _stillOurs() {
    const e = _modalEntry();
    return !!(_placeholder && e && e.element === _placeholder);
  }

  // Swap the placeholder for the real Desk, unless the person closed it first.
  function _openWhenReady() {
    return ready().then(() => {
      if (!_stillOurs()) return;
      const old = _placeholder;
      _placeholder = null;
      openModals.delete(MODAL_ID);
      old.remove();
      window.deskV1Open();
    }, (e) => {
      console.error('[desk] could not load the Desk', e);
      if (_stillOurs()) _paintBody({ kind: 'error', message: 'The Desk could not be loaded: ' + ((e && e.message) || e) });
    });
  }

  // openDesk()'s body. Resolves once the real Desk is open (or the person closed
  // the window, or the load failed and the error is showing).
  let _opening = null;
  function open() {
    if (_loaded && typeof window.deskV1Open === 'function') { window.deskV1Open(); return Promise.resolve(); }
    _paintPlaceholder();
    if (!_opening) _opening = _openWhenReady().then(() => { _opening = null; });
    return _opening;
  }

  // Background load: after the dashboard has booted, at an idle moment.
  let _scheduled = false;
  function _startInBackground() {
    if (_scheduled) return;
    _scheduled = true;
    const go = () => { ready().catch((e) => console.warn('[desk] background load failed; it will retry when the Desk is opened', e)); };
    if (typeof window.requestIdleCallback === 'function') window.requestIdleCallback(go, { timeout: IDLE_TIMEOUT_MS });
    else setTimeout(go, 1500);
  }
  function _scheduleBackground() {
    const done = Array.isArray(window.__mcBootTimeline) && window.__mcBootTimeline.some((m) => m.stage === 'boot continuation done');
    if (done) { _startInBackground(); return; }
    window.addEventListener('mc-boot-complete', _startInBackground, { once: true });
    setTimeout(_startInBackground, BOOT_GIVE_UP_MS);
  }

  window.DeskLoad = { ready, open, isLoaded: () => _loaded };
  if (document.readyState === 'complete') _scheduleBackground();
  else window.addEventListener('load', _scheduleBackground, { once: true });
})();
