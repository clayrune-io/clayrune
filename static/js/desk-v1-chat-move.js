// Move the shared Desk chat's large view by its chrome strip. Geometry uses
// the same properties as makeResizable and DeskV1ChatChrome's open/close reset.
(function () {
  let _drag = null;

  function cancel(el) {
    if (!_drag || (el && _drag.el !== el)) return;
    _drag.bar.classList.remove('is-moving');
    _drag.listeners.abort();
    _drag = null;
  }

  function move(x, y) {
    const d = _drag;
    if (!d) return;
    if (!d.el.isConnected || !d.el.classList.contains('is-popped') || innerWidth <= 960) { cancel(); return; }
    // Match modalDragMove: leave enough of the header on screen to grab again.
    d.el.style.left = Math.max(0, Math.min(innerWidth - 100, d.left + x - d.x)) + 'px';
    d.el.style.top = Math.max(0, Math.min(innerHeight - 50, d.top + y - d.y)) + 'px';
  }

  function start(el, bar, e, touch) {
    if (!el.classList.contains('is-popped') || innerWidth <= 960) return;
    if (e.target.closest('button, input, textarea, select, a, [contenteditable], .mc-resize-handle')) return;
    if (touch ? e.touches.length !== 1 : e.button !== 0) return;
    cancel();
    const p = touch ? e.touches[0] : e;
    const r = el.getBoundingClientRect();
    Object.assign(el.style, { position: 'fixed', margin: '0', left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px' });
    const listeners = new AbortController();
    _drag = { el, bar, listeners, x: p.clientX, y: p.clientY, left: r.left, top: r.top };
    bar.classList.add('is-moving');
    const opts = { signal: listeners.signal };
    if (touch) {
      document.addEventListener('touchmove', (ev) => {
        if (ev.touches.length !== 1) { cancel(); return; }
        ev.preventDefault();
        move(ev.touches[0].clientX, ev.touches[0].clientY);
      }, { ...opts, passive: false });
      document.addEventListener('touchend', () => cancel(), opts);
      document.addEventListener('touchcancel', () => cancel(), opts);
    } else {
      document.addEventListener('mousemove', (ev) => move(ev.clientX, ev.clientY), opts);
      document.addEventListener('mouseup', () => cancel(), opts);
    }
    window.addEventListener('blur', () => cancel(), opts);
    e.preventDefault();
  }

  function attach(el, bar) {
    bar.addEventListener('mousedown', (e) => start(el, bar, e, false));
    bar.addEventListener('touchstart', (e) => start(el, bar, e, true), { passive: false });
  }

  window.DeskV1ChatMove = { attach, cancel };
})();
