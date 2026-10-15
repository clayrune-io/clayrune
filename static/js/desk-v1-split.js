// Desk v1: a draggable edge between a screen's form column and its agent chat
// column (Ron 2026-10-15: widen the chat and shrink the campaign view, or the
// reverse; the pop-out stays as it is). Window-bridged module, no `import`
// (ground rule 1). One unit, one file (AGENT_RULES.md): it knows nothing about
// campaigns or storyboards, only about a container with a form on one side and
// a `side` element on the other, so any two-column screen adopts it with one call.
//
//   window.DeskV1Split.attach({
//     container, side,            the two-column host and its chat column (the side that resizes)
//     key,                        localStorage suffix, one saved ratio per screen type
//     layout: 'flex' | 'grid',    how the host lays the side out: flex-basis on `side`,
//                                 or the host's last grid column
//     minSide, minMain,           px floors: the chat column (composer + Send) and the form
//     label,                      aria-label for the separator
//   })                            safe to call again on the same container
//
// The separator is positioned over the gap between the columns, not inserted into
// the flow, so it changes neither layout. The width is applied as an inline style on
// top of the screen's own CSS and removed again (double click, or a phone-width
// window) so the stylesheet's default and its stacking rules are what is left.
// The saved value is the chat's share of the host's width, so a narrower window
// re-clamps it instead of keeping a pixel width that no longer fits.
(function () {
  const PHONE = '(max-width: 960px)';           // the Desk stacks its columns at and below this
  const KEY_PREFIX = 'clayrune.desk.split.';
  const STEP = 24, STEP_BIG = 96;
  const _all = new Set();                        // live splitters; pruned as their hosts leave the DOM
  let _winBound = false;
  let _drag = null;

  const _isPhone = () => !!(window.matchMedia && window.matchMedia(PHONE).matches);
  const _num = (v, d) => { const n = parseFloat(v); return Number.isFinite(n) ? n : d; };

  // CSS px per client px: 1 unless an ancestor is scaled (zoom/transform), so a pointer
  // distance and the px written into the style speak the same unit.
  function _k(el) {
    const w = el.offsetWidth;
    const r = el.getBoundingClientRect().width;
    return w > 0 && r > 0 ? r / w : 1;
  }

  function _load(key) {
    try {
      const v = parseFloat(localStorage.getItem(KEY_PREFIX + key));
      return Number.isFinite(v) && v > 0 && v < 1 ? v : null;
    } catch (_) { return null; /* storage blocked: the width lasts for this page only */ }
  }
  function _store(key, ratio) {
    try {
      if (ratio == null) localStorage.removeItem(KEY_PREFIX + key);
      else localStorage.setItem(KEY_PREFIX + key, String(Math.round(ratio * 10000) / 10000));
    } catch (_) { /* storage blocked: the width lasts for this page only */ }
  }

  function _bounds(s) {
    const W = s.container.clientWidth;
    const gap = _num(getComputedStyle(s.container).columnGap, 16);
    const min = s.minSide;
    // Both floors cannot always hold (a very narrow host): the chat's floor wins, so the
    // composer and Send stay reachable and the form just scrolls.
    const max = Math.max(min, W - gap - s.minMain);
    return { W, gap, min, max };
  }
  const _clamp = (b, px) => Math.min(b.max, Math.max(b.min, px));

  function _apply(s, px) {
    if (s.layout === 'grid') s.container.style.gridTemplateColumns = `minmax(0, 1fr) ${Math.round(px)}px`;
    else s.side.style.flex = `0 0 ${Math.round(px)}px`;
  }
  function _clearInline(s) {
    if (s.layout === 'grid') s.container.style.removeProperty('grid-template-columns');
    else s.side.style.removeProperty('flex');
  }

  // Put the separator over the gap and describe it to assistive tech. The value is the
  // FORM's share (the primary pane, per the ARIA window-splitter pattern), so Left lowers it.
  function _place(s) {
    const cr = s.container.getBoundingClientRect();
    const sr = s.side.getBoundingClientRect();
    const k = _k(s.container);
    const b = _bounds(s);
    s.sideRight = (sr.left + sr.width / 2) > (cr.left + cr.width / 2);
    const edge = s.sideRight ? sr.left - cr.left : sr.right - cr.left;
    const off = (s.sideRight ? -1 : 1) * b.gap / 2;
    s.sep.style.left = Math.round(edge / k - s.container.clientLeft + off) + 'px';
    const side = sr.width / k;
    const pct = (px) => Math.round(((b.W - b.gap - px) / b.W) * 100);
    s.sep.setAttribute('aria-valuenow', String(pct(side)));
    s.sep.setAttribute('aria-valuemin', String(pct(b.max)));
    s.sep.setAttribute('aria-valuemax', String(pct(b.min)));
    s.sep.setAttribute('aria-valuetext', `Form ${pct(side)} percent, chat ${100 - pct(side)} percent`);
  }

  // Re-read the layout: hide on a phone or while the side column is not shown (the Where
  // stop hides it), re-clamp a saved width to the host's current size, then reposition.
  function _sync(s) {
    if (!s.container.isConnected) return;
    const hidden = _isPhone() || !s.side.getClientRects().length || s.container.clientWidth <= 0;
    s.sep.hidden = hidden;
    if (_isPhone()) { _clearInline(s); return; }
    if (hidden) return;
    if (s.ratio != null && !_drag) {
      const b = _bounds(s);
      _apply(s, _clamp(b, s.ratio * b.W));
    }
    _place(s);
  }

  function _syncAll() {
    for (const s of _all) {
      if (!s.container.isConnected) { s.ro.disconnect(); _all.delete(s); continue; }
      _sync(s);
    }
  }

  // Set the chat's width in px (clamped), applied now; `persist` also saves the share.
  function _setSide(s, px, persist) {
    const b = _bounds(s);
    const v = _clamp(b, px);
    _apply(s, v);
    if (persist && b.W > 0) { s.ratio = v / b.W; _store(s.key, s.ratio); }
    _place(s);
  }

  function _reset(s) {
    s.ratio = null;
    _store(s.key, null);
    _clearInline(s);
    _place(s);
  }

  function _sideNow(s) { return s.side.getBoundingClientRect().width / _k(s.container); }

  function _onDown(s, e) {
    if ((e.pointerType === 'mouse' && e.button !== 0) || _drag) return;
    e.preventDefault();
    _drag = { s, id: e.pointerId, grab: 0 };
    const sepC = s.sep.getBoundingClientRect();
    _drag.grab = e.clientX - (sepC.left + sepC.width / 2);
    try { s.sep.setPointerCapture(e.pointerId); } catch (_) { /* a synthetic pointer cannot be captured: the move still reaches us on the document */ }
    s.sep.classList.add('is-dragging');
    document.documentElement.classList.add('desk-v1-split-dragging');
  }
  function _onMove(e) {
    if (!_drag || e.pointerId !== _drag.id) return;
    const s = _drag.s;
    const sr = s.side.getBoundingClientRect();
    const k = _k(s.container);
    const b = _bounds(s);
    // The separator sits mid-gap, so the pointer is half a gap from the chat's near edge.
    const x = e.clientX - _drag.grab;
    const px = s.sideRight ? (sr.right - x) / k - b.gap / 2 : (x - sr.left) / k - b.gap / 2;
    _setSide(s, px, false);
  }
  function _onUp(e) {
    if (!_drag || e.pointerId !== _drag.id) return;
    const s = _drag.s;
    _drag = null;
    s.sep.classList.remove('is-dragging');
    document.documentElement.classList.remove('desk-v1-split-dragging');
    try { s.sep.releasePointerCapture(e.pointerId); } catch (_) { /* already released */ }
    _setSide(s, _sideNow(s), true);
  }

  function _onKey(s, e) {
    const b = _bounds(s);
    const dir = s.sideRight ? 1 : -1;               // Left moves the separator left: the chat grows when it is on the right
    const step = e.shiftKey ? STEP_BIG : STEP;
    let px;
    if (e.key === 'ArrowLeft') px = _sideNow(s) + dir * step;
    else if (e.key === 'ArrowRight') px = _sideNow(s) - dir * step;
    else if (e.key === 'Home') px = b.max;           // the form at its smallest
    else if (e.key === 'End') px = b.min;            // the form at its largest
    else return;
    e.preventDefault();
    _setSide(s, px, true);
  }

  function attach(opts) {
    const o = opts || {};
    const container = o.container, side = o.side;
    if (!container || !side || !o.key) return null;
    if (container._deskV1Split) { _sync(container._deskV1Split); return container._deskV1Split; }
    const sep = document.createElement('div');
    sep.className = 'desk-v1-split-sep';
    sep.setAttribute('role', 'separator');
    sep.setAttribute('aria-orientation', 'vertical');
    sep.setAttribute('aria-label', o.label || 'Resize the columns');
    sep.setAttribute('tabindex', '0');
    sep.setAttribute('data-split-sep', o.key);
    sep.title = 'Drag to resize. Double-click to reset.';
    sep.innerHTML = '<span class="desk-v1-split-grip" aria-hidden="true"></span>';
    container.classList.add('desk-v1-split-host');
    container.appendChild(sep);
    const s = {
      container, side, sep, key: o.key, layout: o.layout === 'grid' ? 'grid' : 'flex',
      minSide: o.minSide || 260, minMain: o.minMain || 360, ratio: _load(o.key), sideRight: true, ro: null,
    };
    container._deskV1Split = s;
    sep.addEventListener('pointerdown', (e) => _onDown(s, e));
    sep.addEventListener('pointermove', _onMove);
    sep.addEventListener('pointerup', _onUp);
    sep.addEventListener('pointercancel', _onUp);
    sep.addEventListener('keydown', (e) => _onKey(s, e));
    sep.addEventListener('dblclick', (e) => { e.preventDefault(); _reset(s); });
    // The host resizing (window, Desk modal) or the side appearing/disappearing (a stop that
    // hides the chat) both change the box sizes this watches.
    s.ro = new ResizeObserver(() => _sync(s));
    s.ro.observe(container);
    s.ro.observe(side);
    _all.add(s);
    if (!_winBound) {
      _winBound = true;
      window.addEventListener('resize', _syncAll);
      if (window.matchMedia) {
        const mq = window.matchMedia(PHONE);
        if (mq.addEventListener) mq.addEventListener('change', _syncAll);
      }
    }
    _sync(s);
    return s;
  }

  window.DeskV1Split = { attach };
})();
