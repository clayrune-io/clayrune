// Pointer-drag (MC-977 T0c) — the generic state machine extracted from
// floor.js's drag-to-hire (docs/DRAG_TO_HIRE_SPEC.md), the only pointer-drag
// gesture in the app when this was pulled out. Window-bridged module, no
// `import` (ground rule 1) — `window.PointerDrag`.
//
// This module owns the MECHANICS only: activation threshold (8px mouse
// travel, a touch long-press), the ghost that follows the pointer,
// `touch-action:none` applied only once a drag actually activates, Esc
// cancel, and teardown (including an optional focus-return for keyboard/
// screen-reader users, off by default so an existing caller doesn't gain a
// visible behaviour change it never asked for). It does NOT know what a
// "target" means to any given caller — drag-to-hire's project tiles, or a
// future Desk drop zone, are the caller's own domain and stay in the
// caller's file; `markTargets()` below is a thin, optional helper for the
// common "classify every candidate element as ok/refused" shape, not a
// requirement.
//
// Every rule here has a caller-visible reason baked into floor.js's own
// comments (poll-tick survival, capture timing, blur/pointerId filtering,
// touch-action ordering) — this file keeps the mechanism, not the WHY;
// read floor.js's drag-to-hire section for the incidents that pinned it.
(function () {
  const DEFAULT_SLOP_PX = 8;
  const DEFAULT_LONG_PRESS_MS = 400;
  // Published on <body> for every activated drag regardless of caller —
  // `activeBodyClass` above is the caller's OWN visual hook (floor.js's
  // `hire-active` drives its dimming CSS) and callers won't all share one.
  // The global Escape-closes-modal handler (index.html) needs a single,
  // caller-agnostic "a drag is in flight" signal so Desk's own drags (once
  // it grows one) get the same Esc-cancels-drag-not-modal exemption Floor's
  // hire drag already had — checking a Floor-specific class there meant any
  // OTHER caller's drag closed the modal underneath it on Escape.
  const DRAG_ACTIVE_BODY_CLASS = 'pd-drag-active';

  // `opts` contract (all but the state-slot accessors are optional):
  //   isDragActive()          -> true if the caller already has a drag running
  //   getDragState()          -> the caller's current state object (or null)
  //   setDragState(st|null)   -> caller stores/clears its own state slot
  //   slopPx, longPressMs     -> activation tuning (defaults above)
  //   draggingClass           -> class added to `el` only once active
  //   activeBodyClass         -> class added to <body> only once active
  //   ghostClass              -> class on the ghost node (caller styles it)
  //   ghostHTML(state)        -> innerHTML for the ghost, built once at activation
  //   ghostRotationDeg        -> exposed as the `--pd-rotate` CSS var on the ghost
  //   ghostOffsetX/Y          -> px offset from the raw pointer position
  //   returnFocus             -> focus `el` back after teardown (default false)
  //   data                    -> caller payload, copied onto `state.data`
  //   onActivate(state, x, y) -> fires once, right after the ghost is mounted
  //   onMove(state, x, y)     -> fires on every move once active
  //   onDrop(state, x, y)     -> fires on pointerup while active; return value
  //                              is handed to afterDrop after teardown runs
  //   afterDrop(state, result)
  //   onTeardown(state, wasDrag) -> fires BEFORE the ghost/listeners are torn
  //                                 down, so callers can read final DOM state
  //   onEnd(state, wasDrag)      -> fires AFTER teardown is fully complete
  function begin(el, e, opts) {
    if (typeof e.button === 'number' && e.button !== 0) return null;   // left/primary only
    if (opts.isDragActive && opts.isDragActive()) return null;

    const slop = opts.slopPx || DEFAULT_SLOP_PX;
    const longPressMs = opts.longPressMs || DEFAULT_LONG_PRESS_MS;

    const st = {
      el, opts,
      pointerId: e.pointerId, pointerType: e.pointerType || 'mouse',
      startX: e.clientX, startY: e.clientY,
      active: false, ghost: null, longPressTimer: null,
      data: opts.data || {},
    };
    opts.setDragState(st);

    if (st.pointerType === 'touch') {
      st.longPressTimer = setTimeout(() => {
        if (opts.getDragState() === st && !st.active) activate(st.startX, st.startY);
      }, longPressMs);
    }

    function activate(x, y) {
      st.active = true;
      clearTimeout(st.longPressTimer);
      // Capture belongs here, not at the initial pointerdown — a plain click
      // must never risk being retargeted away from whatever it actually hit.
      try { st.el.setPointerCapture(st.pointerId); } catch (err) { /* best-effort */ }
      if (navigator.vibrate) { try { navigator.vibrate(15); } catch (err) { /* not every device */ } }
      document.body.classList.add(DRAG_ACTIVE_BODY_CLASS);
      if (opts.activeBodyClass) document.body.classList.add(opts.activeBodyClass);
      if (opts.draggingClass) st.el.classList.add(opts.draggingClass);
      const ghost = document.createElement('div');
      if (opts.ghostClass) ghost.className = opts.ghostClass;
      if (opts.ghostHTML) ghost.innerHTML = opts.ghostHTML(st);
      if (opts.ghostRotationDeg) ghost.style.setProperty('--pd-rotate', opts.ghostRotationDeg + 'deg');
      positionGhost(ghost, x, y);
      document.body.appendChild(ghost);
      st.ghost = ghost;
      if (opts.onActivate) opts.onActivate(st, x, y);
    }

    function positionGhost(ghost, x, y) {
      ghost.style.left = (x + (opts.ghostOffsetX || 0)) + 'px';
      ghost.style.top = (y + (opts.ghostOffsetY || 0)) + 'px';
    }

    function onMove(ev) {
      if (opts.getDragState() !== st || ev.pointerId !== st.pointerId) return;
      const dx = ev.clientX - st.startX, dy = ev.clientY - st.startY;
      if (!st.active) {
        // Mouse/pen: crossing the slop is itself the activation gesture.
        // Touch waits for the long-press timer instead — real finger movement
        // before it fires reads as a scroll attempt, so it cancels the timer
        // rather than activating (no accidental entry).
        if (st.pointerType !== 'touch' && Math.hypot(dx, dy) > slop) {
          activate(ev.clientX, ev.clientY);
        } else if (st.pointerType === 'touch' && Math.hypot(dx, dy) > slop * 1.5) {
          clearTimeout(st.longPressTimer);
          teardown(false);
        }
        return;
      }
      ev.preventDefault();
      if (st.ghost) positionGhost(st.ghost, ev.clientX, ev.clientY);
      if (opts.onMove) opts.onMove(st, ev.clientX, ev.clientY);
    }

    function onUp(ev) {
      if (opts.getDragState() !== st || ev.pointerId !== st.pointerId) return;
      clearTimeout(st.longPressTimer);
      if (!st.active) { teardown(false); return; }
      const result = opts.onDrop ? opts.onDrop(st, ev.clientX, ev.clientY) : null;
      teardown(true);
      if (opts.afterDrop) opts.afterDrop(st, result);
    }

    function onCancel(ev) {
      if (opts.getDragState() !== st) return;
      // `blur` carries no pointerId, so only a real PointerEvent gets filtered
      // by it — comparing unconditionally would make the blur cancel a no-op.
      if (ev && ev.pointerId !== undefined && ev.pointerId !== st.pointerId) return;
      clearTimeout(st.longPressTimer);
      teardown(st.active);
    }

    function onKeydown(ev) {
      if (ev.key === 'Escape' && opts.getDragState() === st) teardown(st.active);
    }

    function teardown(wasDrag) {
      if (opts.onTeardown) opts.onTeardown(st, wasDrag);
      document.body.classList.remove(DRAG_ACTIVE_BODY_CLASS);
      if (opts.activeBodyClass) document.body.classList.remove(opts.activeBodyClass);
      if (opts.draggingClass) st.el.classList.remove(opts.draggingClass);
      if (st.ghost) { st.ghost.remove(); st.ghost = null; }
      // Belt as well as braces: the ghost lives on <body>, so a stale one from
      // any path that skipped this teardown would sit on the board until a
      // reload. Sweep by class, not only by handle.
      if (opts.ghostClass) document.querySelectorAll('.' + opts.ghostClass).forEach((g) => g.remove());
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup', onUp);
      window.removeEventListener('pointercancel', onCancel);
      window.removeEventListener('blur', onCancel);
      document.removeEventListener('keydown', onKeydown);
      try { st.el.releasePointerCapture(st.pointerId); } catch (err) { /* already released, or the node is gone */ }
      opts.setDragState(null);
      if (opts.returnFocus && wasDrag && typeof st.el.focus === 'function') {
        // Best-effort: only elements that were already focusable receive it;
        // anything else is a silent no-op, never a thrown error.
        try { st.el.focus({ preventScroll: true }); } catch (err) { /* not focusable */ }
      }
      if (opts.onEnd) opts.onEnd(st, wasDrag);
    }

    // On WINDOW, not on `el` — the gesture must outlive the node. A view can
    // re-render its whole body mid-drag; a listener bound to the card dies
    // with the card, taking the pointerup that would have dropped (or
    // cleaned up) with it. Window sees the release no matter what happened to
    // the thing being dragged. Releasing over another app never sends a
    // pointerup at all, so `blur` is covered too.
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', onUp);
    window.addEventListener('pointercancel', onCancel);
    window.addEventListener('blur', onCancel);
    document.addEventListener('keydown', onKeydown);

    return st;
  }

  // Optional convenience for the common "mark every candidate element as a
  // valid target, a dimmed-refused one, or neither" shape (advertise/dim-
  // with-reason). `classify(el)` returns `true`/`false`, or `{ok, reason}` to
  // also carry a refusal reason as a `title` attribute. It changes no CSS —
  // the class names are the caller's own.
  // `selector` must be a SINGLE simple selector, not a comma-joined compound
  // one — `selector + '.' + cls` only lands the suffix on the last branch of
  // a compound list (this bit drag-to-hire's own `HIRE_TILE_SEL` once; see
  // floor.js's `_hireTileSel`). A caller with more than one target shape
  // calls this once per branch, or builds its own per-branch selector first.
  function markTargets(selector, classify, classes) {
    const okClass = classes && classes.ok;
    const refusedClass = classes && classes.refused;
    document.querySelectorAll(selector).forEach((el) => {
      const verdict = classify(el);
      const ok = typeof verdict === 'object' ? !!verdict.ok : !!verdict;
      const reason = typeof verdict === 'object' ? verdict.reason : null;
      if (okClass) el.classList.toggle(okClass, ok);
      if (refusedClass) el.classList.toggle(refusedClass, !ok);
      if (!ok && reason) el.title = reason; else el.removeAttribute('title');
    });
  }

  function clearTargetMarks(selector, classes) {
    const sel = [classes && classes.ok, classes && classes.refused, classes && classes.hover]
      .filter(Boolean).map((c) => selector + '.' + c).join(',');
    if (!sel) return;
    document.querySelectorAll(sel).forEach((el) => {
      if (classes.ok) el.classList.remove(classes.ok);
      if (classes.refused) el.classList.remove(classes.refused);
      if (classes.hover) el.classList.remove(classes.hover);
      el.removeAttribute('title');
    });
  }

  window.PointerDrag = { begin, markTargets, clearTargetMarks };
})();
