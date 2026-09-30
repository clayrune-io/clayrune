// ── Mermaid diagram interception ────────────────────────────────────────────
// Three code paths produce mermaid blocks: live streaming (appendAgentLine),
// modal-rebuild (outputLines builder in tile click / tab switch), and the
// popout (openPlanViewer). All converge on the same shape:
//
//   1. Insert `<div class="mermaid-block" data-source="<escaped>">
//        <div class="mermaid-pending">Building diagram…</div></div>`
//   2. Call `_renderAllMermaidPlaceholders(rootEl)` after insertion. It
//      finds un-rendered placeholders, runs mermaid.render() async, swaps
//      in the SVG (with explicit width/height stripped so CSS can scale),
//      wires the click-to-enlarge handler.
//
// Diagrams now survive tab switches (rebuild re-emits placeholder; render
// runs again) and render in the popout window.

const _mermaidBuffers = {};   // sessionId -> { placeholder, lines }

// The viewers stay open while the chat composer keeps focus, so their
// document-level key handlers must leave keys typed into a text field alone
// (Ron 2026-09-30: ArrowLeft/Right in the composer switched pictures).
function _isTypingTarget(t) {
  return !!t && (t.tagName === 'TEXTAREA' || t.tagName === 'INPUT'
                 || t.tagName === 'SELECT' || t.isContentEditable);
}

function _mermaidPlaceholderHTML(source) {
  // For HTML-string builders (outputLines, openPlanViewer).
  return `<div class="mermaid-block" data-source="${esc(source)}">` +
         `<div class="mermaid-pending">Building diagram…</div></div>`;
}

function _resizeSvgForFit(svg) {
  // Mermaid SVGs ship with explicit width/height matching the natural size.
  // Strip them so CSS max-width:100% actually scales the diagram.
  return svg.replace(/<svg([^>]*)\swidth="[^"]*"/, '<svg$1')
            .replace(/<svg([^>]*)\sheight="[^"]*"/, '<svg$1')
            .replace(/<svg /, '<svg style="max-width:100%;height:auto" ');
}

// Render via Excalidraw bridge: Mermaid source -> Excalidraw elements -> SVG.
// Returns a Promise<svgString>. Throws on unsupported diagram types so the
// caller can fall back to Mermaid's own renderer.
// Mermaid v11 (and parseMermaidToExcalidraw, which uses it under the hood)
// injects an orphan "Syntax error in text" SVG into <body> when its parser
// fails, and never cleans it up — they accumulate on the page over the
// lifetime of the tab. Sweep them before/after every render attempt.
//
// Important: Mermaid v11 *also* keeps its own working sandbox node on
// <body> (id starts with "dmermaid-" / "mermaid-") that it reuses across
// renders. Removing that crashes the next render with
// "Cannot read properties of null (reading 'firstChild')". So we only
// remove nodes that actually contain the error text — that signature is
// unique to the failure SVGs.
function _sweepOrphanMermaidNodes() {
  // Match both the bare error SVG and the wrapper div Mermaid sometimes
  // leaves on body. Mermaid's *working* sandbox div uses the same id
  // prefix, so we gate every removal on the literal "Syntax error" text
  // — the working sandbox is empty between renders and never matches.
  document.querySelectorAll(
    'body > svg[id^="mermaid-"], body > svg[id^="dmermaid-"], ' +
    'body > div[id^="mermaid-"], body > div[id^="dmermaid-"]'
  ).forEach(n => {
    if (n.closest('.mermaid-block')) return;
    const txt = (n.textContent || '');
    if (/Syntax error|mermaid version/i.test(txt)) n.remove();
  });
}

async function _renderViaExcalidraw(source) {
  const api = window._excalidrawAPI;
  if (!api) throw new Error('excalidraw-not-loaded');
  const { parseMermaidToExcalidraw, convertToExcalidrawElements, exportToSvg } = api;
  _sweepOrphanMermaidNodes();
  const { elements: skeleton, files } = await parseMermaidToExcalidraw(source);
  const elements = convertToExcalidrawElements(skeleton);
  // Strip Roughjs sketchiness AND swap the default Virgil "hand-drawn"
  // font for Helvetica. Without the font swap, diagrams still look like
  // Excalidraw whiteboard scribbles even when the strokes are clean.
  // fontFamily: 1 = Virgil (hand-drawn), 2 = Helvetica, 3 = Cascadia.
  // Applies to text elements AND to the label of arrows/lines (which
  // store the same fontFamily field on the element itself).
  for (const el of elements) {
    if (el.roughness != null) el.roughness = 0;
    if (el.fillStyle != null) el.fillStyle = 'solid';
    if (el.strokeStyle === 'dashed' || el.strokeStyle === 'dotted') {
      // keep dashed/dotted intent
    } else if (el.strokeStyle != null) {
      el.strokeStyle = 'solid';
    }
    if (el.fontFamily != null) el.fontFamily = 2;
  }
  const svgEl = await exportToSvg({
    elements,
    files: files || {},
    appState: {
      exportBackground: false,
      viewBackgroundColor: '#fdfbf6',
      exportEmbedScene: false,
    },
  });
  // exportToSvg returns an SVGElement; we want a string for innerHTML.
  return svgEl.outerHTML;
}

// Excalidraw is the preferred renderer, but it now loads on demand alongside
// mermaid instead of during boot — so the FIRST diagram of a session can arrive
// before the bridge does. Give it a bounded wait rather than silently
// downgrading that one diagram to plain Mermaid; the placeholder already reads
// "Building diagram…". Resolves immediately if the bridge is up, or if it has
// already failed (offline / filtered network).
let _excalidrawFailed = false;
window.addEventListener('excalidraw-failed', () => { _excalidrawFailed = true; }, { once: true });
function _waitForExcalidraw(ms = 8000) {
  if (window._excalidrawAPI) return Promise.resolve(true);
  if (_excalidrawFailed) return Promise.resolve(false);
  return new Promise(resolve => {
    const done = ok => { clearTimeout(t); resolve(ok); };
    const t = setTimeout(() => resolve(false), ms);
    window.addEventListener('excalidraw-ready', () => done(true), { once: true });
    window.addEventListener('excalidraw-failed', () => done(false), { once: true });
  });
}

async function _renderViaMermaid(source) {
  const id = 'mermaid-' + Math.random().toString(36).slice(2, 9);
  _sweepOrphanMermaidNodes();
  try {
    const result = await window.mermaid.render(id, source);
    return _resizeSvgForFit(result.svg);
  } finally {
    _sweepOrphanMermaidNodes();
  }
}

function _renderAllMermaidPlaceholders(rootEl) {
  // Block-rendering pipeline:
  //   1. If neither lib is ready: wait for the first to land, retry.
  //   2. Prefer Excalidraw (clean shapes, polished typography).
  //   3. Fall back to Mermaid for diagram types Excalidraw can't parse
  //      (state, ER, gantt, journey, pie, mindmap, timeline).
  //   4. If both fail: show the error + raw source so the user/agent can
  //      diagnose.
  if (!window.mermaid) {
    // Diagram libs are no longer in the boot path (they were 353 of a cold
    // boot's 420 requests). First placeholder that needs one pulls them in;
    // `mermaid-ready` then re-enters here exactly as it always did.
    window.addEventListener('mermaid-ready',
      () => _renderAllMermaidPlaceholders(rootEl), { once: true });
    if (typeof window.ensureDiagramLibs === 'function') window.ensureDiagramLibs();
    return;
  }
  const root = rootEl || document;
  const blocks = root.querySelectorAll('.mermaid-block[data-source]:not([data-rendered])');
  blocks.forEach(async block => {
    const source = block.dataset.source;
    block.dataset.rendered = '1';
    let svg = '';
    let renderer = 'mermaid';
    try {
      await _waitForExcalidraw();
      svg = await _renderViaExcalidraw(source);
      renderer = 'excalidraw';
    } catch (e) {
      // Excalidraw failed (not loaded yet, or unsupported diagram type).
      // Try Mermaid as a fallback.
      try {
        svg = await _renderViaMermaid(source);
      } catch (e2) {
        const msg = (e2 && (e2.message || e2.str || String(e2))) || 'render failed';
        block.innerHTML =
          `<div class="mermaid-error">Diagram error: ${esc(msg)}</div>` +
          `<pre class="mermaid-source">${esc(source)}</pre>`;
        block.style.cursor = 'default';
        return;
      }
    }
    block.innerHTML = svg;
    block.dataset.svg = svg;
    block.dataset.renderer = renderer;
    block.title = 'Click to enlarge';
    block.addEventListener('click', () => _openMermaidViewer(source, svg));
  });
}

function _handleMermaidLine(sessionId, text, el) {
  const buf = _mermaidBuffers[sessionId];
  const isOpening = /^\s*```\s*mermaid\b/.test(text);
  const isClosing = /^\s*```\s*$/.test(text);

  if (!buf && isOpening) {
    const ph = document.createElement('div');
    ph.className = 'mermaid-block';
    ph.innerHTML = '<div class="mermaid-pending">Building diagram…</div>';
    el.appendChild(ph);
    _mermaidBuffers[sessionId] = { placeholder: ph, lines: [] };
    return true;
  }
  if (buf && isClosing) {
    const source = buf.lines.join('\n');
    buf.placeholder.dataset.source = source;
    delete _mermaidBuffers[sessionId];
    _renderAllMermaidPlaceholders(buf.placeholder.parentElement || document);
    return true;
  }
  if (buf) {
    buf.lines.push(text);
    const pending = buf.placeholder.querySelector('.mermaid-pending');
    if (pending) pending.textContent = `Building diagram… (${buf.lines.length} lines)`;
    return true;
  }
  return false;
}

// ── Shared viewer gestures: wheel, pinch, drag-pan, double-tap ─────────────
// Both viewers used to be zoomable ONLY from the toolbar: the wheel handler
// required Ctrl/Cmd and there was no touch handling at all, so on a phone
// pinching did nothing and on a desktop the wheel did nothing. This installs
// pointer-anchored zoom on the canvas and hands the toolbar buttons and the
// keyboard shortcuts the SAME anchored path, so every control agrees on what
// "150%" means and on which pixel stays still while it happens.
const _IV_MIN = 0.2, _IV_MAX = 5;
function _ivGestures(scrollEl, wrap, zoomLabel) {
  let scale = 1;
  // `wrap` used to be `width:100%` of `scrollEl` (CSS-relative), so growing
  // the frame grew the rendered diagram by the SAME factor — the visible
  // fraction (clientWidth / scrollWidth) stayed constant no matter how much
  // bigger the frame got. `natural` + `fitScale` give the wrap an explicit
  // PIXEL size instead: fixed until something re-fits it, so enlarging the
  // frame actually reveals more canvas at the current zoom.
  const natural = { w: 0, h: 0 };
  let fitScale = 1;
  // Set once the user has deliberately zoomed/panned/pinched, so an
  // automatic resize-triggered re-fit doesn't clobber a view they chose.
  let userAdjusted = false;
  let programmatic = false;
  const paint = () => {
    wrap.style.transform = `scale(${scale})`;
    wrap.style.transformOrigin = 'top left';
    if (zoomLabel) zoomLabel.textContent = Math.round(fitScale * scale * 100) + '%';
  };
  // The 0.12s CSS transition is right for a button press and wrong for a
  // continuous gesture — it lags a pinch by a frame and fights every wheel
  // tick. Off for the duration of a gesture, back on after.
  const live = on => { wrap.style.transition = on ? 'none' : ''; };
  const center = () => {
    const r = scrollEl.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };
  // Zoom about a viewport point: the pixel under the cursor/fingers must not
  // move. transform-origin is top-left, so the wrap's own screen position is
  // unaffected by the scale change — only the scroll offset needs correcting.
  const zoomTo = (next, cx, cy) => {
    next = Math.max(_IV_MIN, Math.min(_IV_MAX, next));
    if (Math.abs(next - scale) < 0.0005) return;
    if (cx == null) { const c = center(); cx = c.x; cy = c.y; }
    const r = wrap.getBoundingClientRect();
    const ux = (cx - r.left) / scale, uy = (cy - r.top) / scale;
    scale = next;
    paint();
    scrollEl.scrollLeft += (r.left + ux * scale) - cx;
    scrollEl.scrollTop += (r.top + uy * scale) - cy;
  };
  const pannable = () =>
    scrollEl.scrollWidth > scrollEl.clientWidth + 1 ||
    scrollEl.scrollHeight > scrollEl.clientHeight + 1;
  const setCursor = () => { scrollEl.style.cursor = pannable() ? 'grab' : ''; };

  // Recompute the wrap's PIXEL base size from the frame's current available
  // space. This is what "Fit to view", double-tap, and a resize/rotate all
  // converge on — so they agree on what "fit" means. Narrow frames fit WIDTH
  // only and leave height to scroll: fitting both on a phone shrinks a tall
  // diagram past legibility just to make its bottom edge visible up front.
  const fit = () => {
    if (!natural.w || !natural.h) return;
    const cs = getComputedStyle(scrollEl);
    const padW = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
    const padH = (parseFloat(cs.paddingTop) || 0) + (parseFloat(cs.paddingBottom) || 0);
    const availW = Math.max(1, scrollEl.clientWidth - padW);
    const availH = Math.max(1, scrollEl.clientHeight - padH);
    const kw = availW / natural.w, kh = availH / natural.h;
    fitScale = _ivMobile() ? kw : Math.min(kw, kh);
    wrap.style.width = Math.round(natural.w * fitScale) + 'px';
    wrap.style.height = Math.round(natural.h * fitScale) + 'px';
    scale = 1;
    userAdjusted = false;
    paint();
    programmatic = true;
    scrollEl.scrollLeft = 0;
    scrollEl.scrollTop = 0;
    setTimeout(() => { programmatic = false; }, 50);
    setCursor();
  };
  const setNatural = (w, h) => { natural.w = w; natural.h = h; };

  // A native one-finger scroll (no JS zoomTo involved) still counts as the
  // user choosing a view — don't let a later resize snap it back to fit.
  scrollEl.addEventListener('scroll', () => { if (!programmatic) userAdjusted = true; });

  // Re-run fit on any frame resize (drag-resize, window resize, rotate) —
  // unless the user has already zoomed/panned away from fit, in which case a
  // bigger frame should simply reveal more of their current view, not yank
  // them back to 100%.
  let ro = null;
  if (typeof ResizeObserver !== 'undefined') {
    ro = new ResizeObserver(() => {
      if (!natural.w || !natural.h) return;
      if (!userAdjusted) fit(); else setCursor();
    });
    ro.observe(scrollEl);
  }

  // Wheel = zoom, no modifier needed. Panning a zoomed-in picture is what the
  // drag below is for; a wheel that scrolls a picture by a few pixels is the
  // less useful of the two bindings.
  scrollEl.addEventListener('wheel', e => {
    e.preventDefault();
    userAdjusted = true;
    live(true);
    const step = e.deltaMode === 1 ? 1.12 : 1.0022;   // line-mode vs pixel-mode
    zoomTo(scale * Math.pow(step, -e.deltaY), e.clientX, e.clientY);
    live(false);
    setCursor();
  }, { passive: false });

  // ── Mouse drag-pan ──
  let drag = null;
  scrollEl.addEventListener('mousedown', e => {
    if (e.button !== 0 || !pannable()) return;
    userAdjusted = true;
    drag = { x: e.clientX, y: e.clientY, l: scrollEl.scrollLeft, t: scrollEl.scrollTop };
    scrollEl.style.cursor = 'grabbing';
    e.preventDefault();          // also kills the browser's native image-drag
  });
  const onMove = e => {
    if (!drag) return;
    scrollEl.scrollLeft = drag.l - (e.clientX - drag.x);
    scrollEl.scrollTop = drag.t - (e.clientY - drag.y);
  };
  const onUp = () => { if (drag) { drag = null; setCursor(); } };
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);

  // ── Touch: two-finger pinch, double-tap to toggle ──
  // One finger is left to the browser's native scrolling (CSS touch-action
  // keeps pan-x/pan-y and takes only pinch-zoom away from the page).
  const dist = t => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
  const mid = t => ({ x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 });
  let pinch = null, lastTap = 0;
  scrollEl.addEventListener('touchstart', e => {
    if (e.touches.length === 2) {
      pinch = { d: dist(e.touches) || 1, s: scale };
      userAdjusted = true;
      live(true);
      e.preventDefault();
    }
  }, { passive: false });
  scrollEl.addEventListener('touchmove', e => {
    if (e.touches.length !== 2 || !pinch) return;
    e.preventDefault();
    const c = mid(e.touches);
    zoomTo(pinch.s * (dist(e.touches) / pinch.d), c.x, c.y);
  }, { passive: false });
  scrollEl.addEventListener('touchend', e => {
    if (pinch && e.touches.length < 2) { pinch = null; live(false); setCursor(); return; }
    if (e.touches.length || e.changedTouches.length !== 1) return;
    const now = Date.now(), t = e.changedTouches[0];
    if (now - lastTap < 300) {
      lastTap = 0;
      // Double-tap toggles the SAME fit the button/resize converge on: back to
      // fit from anywhere zoomed in, or a fixed zoom-in step from fit itself.
      if (scale > 1.05) { fit(); }
      else { userAdjusted = true; zoomTo(2.5, t.clientX, t.clientY); setCursor(); }
    } else lastTap = now;
  });

  paint();
  setTimeout(setCursor, 0);
  return {
    zoomBy: f => { userAdjusted = true; zoomTo(scale * f); setCursor(); },
    fit, setNatural,
    // The pan listeners live on `document` so a drag survives the cursor
    // leaving the canvas — which means closing the viewer by removing its
    // overlay does NOT unbind them. Callers must call this from their close
    // path or every picture opened leaks a pair.
    destroy: () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      if (ro) ro.disconnect();
    },
  };
}

// ── Viewer windows ───────────────────────────────────────────────────────
// The viewers were modal dialogs: a full-screen dim backdrop that swallowed
// every click, so opening a thumbnail took the whole dashboard hostage. They
// are now ordinary floating windows — drag by the toolbar, resize from any
// edge (makeResizable), click to raise, and the app behind stays live. The
// backdrop and the modal sizing survive on mobile only, where a floating
// window on a 400px screen would be worse than what it replaces.
//
// Stacking: each viewer has its OWN overlay, and an overlay's z-index makes it
// a stacking context — so raising the *content* cannot lift one viewer above
// another. The overlay is what has to move.
let _IV_Z = 10000;
const _IV_STACK = [];
const _ivMobile = () => window.matchMedia('(max-width: 960px)').matches;

// The toolbar's own width requirement, measured from its children rather than
// hardcoded — a narrow image (~330px in the report that motivated this) used
// to leave the viewer window sized to the PICTURE while the toolbar (fixed
// content: 7-8 buttons) needed ~400px, so buttons past "save" rendered outside
// the window with no wrap/scroll/overflow-menu to reach them. Call this BEFORE
// anything has constrained the toolbar's width (i.e. right after the overlay
// is appended, while the content box still has its roomy CSS default) so each
// button reports its true unclamped size instead of an already-shrunk one.
// Live chrome measurement (toolbar height + the windowed content box's own
// border) so _ivFitBox reserves exactly as much room as the frame actually
// takes. A hardcoded guess here left the content area a couple of px short
// of the requested size, which was enough for gest.fit() — which measures
// the REAL space — to round an exactly-fitting picture down to 99% instead
// of 100%. Call AFTER _ivWindowify (so the border class is already applied)
// and before anything has constrained the toolbar's own layout.
function _ivChrome(content, toolbar) {
  const cs = getComputedStyle(content);
  const borderW = (parseFloat(cs.borderLeftWidth) || 0) + (parseFloat(cs.borderRightWidth) || 0);
  const borderH = (parseFloat(cs.borderTopWidth) || 0) + (parseFloat(cs.borderBottomWidth) || 0);
  const tbH = Math.ceil(toolbar.getBoundingClientRect().height);
  return {
    w: 48 + borderW,             // 24px .mermaid-viewer-scroll padding each side
    h: tbH + 48 + borderH,       // toolbar + the same 24px padding each side
  };
}

function _ivToolbarMinWidth(toolbar) {
  const cs = getComputedStyle(toolbar);
  const gap = parseFloat(cs.columnGap || cs.gap) || 0;
  const kids = [...toolbar.children];
  const sum = kids.reduce((s, el) => s + el.getBoundingClientRect().width, 0);
  const pad = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
  return Math.ceil(sum + gap * Math.max(0, kids.length - 1) + pad);
}

// Same glyph pair the project modal's maximize button draws (interactions.js
// _maxBtnInner, exposed on window). Inline fallback for the one call site
// that renders the button's initial HTML synchronously during this module's
// own load — before interactions.js (which loads after mermaid.js in
// index.html) has necessarily finished — mirroring browser-pane.js's
// _bpMaxIcon, which hits the exact same ordering gap for the same reason.
function _ivMaxIcon(isFull) {
  if (typeof window._maxBtnInner === 'function') return window._maxBtnInner(isFull);
  return isFull
    ? '<svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">' +
      '<rect x="1.5" y="4.5" width="8" height="8" rx="1.5" stroke="currentColor" stroke-width="1.3"/>' +
      '<path d="M4.7 4.3V2.8a1.3 1.3 0 0 1 1.3-1.3h5.2a1.3 1.3 0 0 1 1.3 1.3V8a1.3 1.3 0 0 1-1.3 1.3H9.8" ' +
      'stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>'
    : '<svg width="12" height="12" viewBox="0 0 14 14" fill="none" aria-hidden="true">' +
      '<rect x="2" y="2" width="10" height="10" rx="1.5" stroke="currentColor" stroke-width="1.3"/></svg>';
}

// Minimize/Maximize/Close, styled off the project modal header's own button
// classes (render-core.js .modal-minimize/.modal-maximize/.modal-close) so
// they look native rather than inventing a second button style just for this
// overlay. `.modal-window-controls` normally floats absolute top-right over
// a modal's content; here it rides inline at the end of the toolbar's own
// flex row instead (same position:static override Desk's header already
// applies for the same reason). Shared by both viewers that call
// _ivWindowify — the '_iv-min'/'_iv-max'/'_iv-close' hooks below are what it
// wires up.
function _ivWinControlsHTML() {
  return `<div class="mermaid-viewer-wincontrols modal-window-controls" style="position:static;display:flex;gap:4px;margin-left:auto">
      <button class="modal-minimize _iv-min" title="Minimize">&#x2015;</button>
      <button class="modal-maximize _iv-max" title="Maximize" aria-label="Maximize">${_ivMaxIcon(false)}</button>
      <button class="modal-close _iv-close" title="Close (Esc)">&#10005;</button>
    </div>`;
}

function _ivWindowify(overlay, content, toolbar) {
  _IV_STACK.push(overlay);
  const raise = () => {
    if (_IV_STACK[_IV_STACK.length - 1] !== overlay) {
      const i = _IV_STACK.indexOf(overlay);
      if (i >= 0) _IV_STACK.splice(i, 1);
      _IV_STACK.push(overlay);
    }
    overlay.style.zIndex = ++_IV_Z;
  };
  const untrack = () => {
    const i = _IV_STACK.indexOf(overlay);
    if (i >= 0) _IV_STACK.splice(i, 1);
  };
  // Only the TOP window answers the keyboard. Two open pictures both reacting
  // to `-` (and both closing on Esc) is the bug this prevents.
  const isTop = () => _IV_STACK[_IV_STACK.length - 1] === overlay;
  if (_ivMobile()) return { raise, untrack, isTop, destroy: untrack };

  overlay.classList.add('iv-windowed');
  content.addEventListener('mousedown', raise);
  raise();

  // Flex centering cannot be dragged, so the first grab converts the window to
  // explicit fixed geometry. Deferred rather than done up front because
  // sizeToImage resizes the box after the picture decodes, and staying centred
  // until the user actually moves it is the nicer default.
  const pin = () => {
    if (content.style.position === 'fixed') return;
    const r = content.getBoundingClientRect();
    content.style.position = 'fixed';
    content.style.margin = '0';
    content.style.left = Math.round(r.left) + 'px';
    content.style.top = Math.round(r.top) + 'px';
  };
  let drag = null;
  const startDrag = (x, y, target) => {
    if (target.closest && target.closest('button, a, input, select, textarea')) return false;
    pin();
    const r = content.getBoundingClientRect();
    drag = { x, y, l: r.left, t: r.top };
    toolbar.classList.add('dragging');
    return true;
  };
  const moveDrag = (x, y) => {
    if (!drag) return;
    // Never let the window be dragged somewhere it can't be grabbed back from:
    // keep 40px of it on screen horizontally and its toolbar always reachable.
    const w = content.offsetWidth;
    const l = Math.min(Math.max(drag.l + x - drag.x, 40 - w), window.innerWidth - 40);
    const t = Math.min(Math.max(drag.t + y - drag.y, 0), window.innerHeight - 40);
    content.style.left = Math.round(l) + 'px';
    content.style.top = Math.round(t) + 'px';
  };
  const endDrag = () => { if (drag) { drag = null; toolbar.classList.remove('dragging'); } };
  const onTbDown = e => { if (e.button === 0 && startDrag(e.clientX, e.clientY, e.target)) e.preventDefault(); };
  const onTbTouch = e => {
    if (e.touches.length === 1 && startDrag(e.touches[0].clientX, e.touches[0].clientY, e.target)) e.preventDefault();
  };
  const onMouseMove = e => moveDrag(e.clientX, e.clientY);
  const onTouchMove = e => { if (drag && e.touches.length === 1) moveDrag(e.touches[0].clientX, e.touches[0].clientY); };
  toolbar.addEventListener('mousedown', onTbDown);
  toolbar.addEventListener('touchstart', onTbTouch, { passive: false });
  document.addEventListener('mousemove', onMouseMove);
  document.addEventListener('mouseup', endDrag);
  document.addEventListener('touchmove', onTouchMove, { passive: false });
  document.addEventListener('touchend', endDrag);

  // ── Minimize → chip in #minimized-tray, same dock/style every other
  // window minimizes into (modal-manager.js minimizeModal/restoreModal,
  // browser-pane.js _bpMinimizePane) — but this overlay isn't in
  // modal-manager's openModals map, so like browser-pane it reimplements the
  // dock-and-chip contract standalone rather than calling into it. The chip's
  // own close (×) forwards to the toolbar's REAL close button rather than
  // duplicating closeIt's cleanup — closeIt is defined by the call site
  // after this returns, and its listener is already attached by the time a
  // user could ever click a minimized chip's close.
  let chip = null;
  const minimize = () => {
    const tray = document.getElementById('minimized-tray');
    if (!tray) return;         // no dock on this surface — nothing to minimize into
    untrack();
    overlay.style.display = 'none';
    const img = content.querySelector('.mermaid-viewer-svg img');
    const label = (img && img.alt) || 'Image';
    chip = document.createElement('div');
    chip.className = 'minimized-chip';
    chip.innerHTML = `<span class="chip-status" style="background:#4caf50"></span>` +
      `<span>&#128247; ${esc(label)}</span>` +
      `<span class="chip-close" title="Close">&#10005;</span>`;
    chip.addEventListener('click', e => {
      if (e.target.closest('.chip-close')) {
        const closeBtn = toolbar.querySelector('._iv-close');
        if (closeBtn) closeBtn.click();
        return;
      }
      restore();
    });
    tray.appendChild(chip);
  };
  const restore = () => {
    overlay.style.display = '';
    if (chip) { chip.remove(); chip = null; }
    raise();
  };
  const minBtn = toolbar.querySelector('._iv-min');
  if (minBtn) minBtn.addEventListener('click', e => { e.stopPropagation(); minimize(); });

  // ── Maximize/restore — fills the whole viewport, matching
  // .modal-window.is-maximized .modal-content (render-core.js) and
  // browser-pane.js's _bpApplyMaximizedRect for the same reason: border/
  // radius are cleared too, or the box would be 2px wider/taller than the
  // viewport it's meant to exactly fill. `pin()` converts the still-flex-
  // centered window to fixed geometry first if it's never been dragged —
  // maximize has to work as the very first click, not just after a drag.
  let maxState = null;
  const maxBtn = toolbar.querySelector('._iv-max');
  const toggleMaximize = () => {
    if (maxState) {
      const g = maxState; maxState = null;
      content.style.left = g.left; content.style.top = g.top;
      content.style.width = g.width; content.style.height = g.height;
      content.style.border = g.border; content.style.borderRadius = g.borderRadius;
      content.style.maxWidth = g.maxWidth; content.style.maxHeight = g.maxHeight;
    } else {
      pin();
      maxState = {
        left: content.style.left, top: content.style.top,
        width: content.style.width, height: content.style.height,
        border: content.style.border, borderRadius: content.style.borderRadius,
        maxWidth: content.style.maxWidth, maxHeight: content.style.maxHeight,
      };
      content.style.left = '0px';
      content.style.top = '0px';
      // The CSS class sets max-width:95vw/max-height:92vh as a resize-drag
      // ceiling (_ivFitBox) — that caps `width` too, not just clamps overflow,
      // so without clearing it here the window "maximizes" to only 95%/92%
      // of the viewport instead of filling it.
      content.style.maxWidth = 'none';
      content.style.maxHeight = 'none';
      content.style.width = window.innerWidth + 'px';
      content.style.height = window.innerHeight + 'px';
      content.style.border = 'none';
      content.style.borderRadius = '0';
    }
    if (maxBtn) {
      maxBtn.innerHTML = _ivMaxIcon(!!maxState);
      maxBtn.title = maxState ? 'Restore down' : 'Maximize';
      maxBtn.setAttribute('aria-label', maxBtn.title);
    }
  };
  if (maxBtn) maxBtn.addEventListener('click', e => { e.stopPropagation(); toggleMaximize(); });

  return {
    raise, untrack, isTop, minimize, restore,
    isMinimized: () => overlay.style.display === 'none',
    destroy: () => {
      untrack();
      if (chip) { chip.remove(); chip = null; }
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', endDrag);
      document.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('touchend', endDrag);
    },
  };
}

// Fit a window to content of natural size nw x nh: shrink by aspect until the
// WHOLE thing fits inside a fraction of the viewport, never enlarge past 1:1.
// The old code clamped width and height independently at 95vw/92vh, which for
// anything bigger than the screen — i.e. every screenshot — meant "maximised".
//
// DESKTOP ONLY — both call sites skip this on mobile and keep the CSS
// 95vw/92vh "modal treatment" frame instead, unconditionally, regardless of
// the content's own natural size: a diagram whose natural units are tiny
// (common for mermaid viewBoxes) must still open legible on a phone, not
// pinned to 1:1. `_ivGestures.fit()` is what actually scales the content to
// fill whichever frame this (or the mobile CSS default) leaves it with.
//
// `toolbarMinW` (from _ivToolbarMinWidth) raises the width floor past the
// generic 320px so the window is never narrower than its OWN toolbar needs —
// a floor, not a fixed size: it only bites when the picture is narrower than
// its controls.
function _ivFitBox(nw, nh, toolbarMinW, chrome) {
  const CHROME_W = chrome ? chrome.w : 48;
  const CHROME_H = chrome ? chrome.h : 93;      // fallback: ~45px toolbar + 48px padding
  const k = Math.min(1, (window.innerWidth * 0.8 - CHROME_W) / nw,
                        (window.innerHeight * 0.8 - CHROME_H) / nh);
  const wFloor = Math.max(320, (toolbarMinW || 0) + 2);
  return {
    w: Math.max(wFloor, Math.round(nw * k) + CHROME_W),
    h: Math.max(220, Math.round(nh * k) + CHROME_H),
  };
}

function _openMermaidViewer(source, svg) {
  // Make the viewer SVG fill the modal — strip any inline width/height/style
  // and apply our own. Dimensions controlled via .mermaid-viewer-svg CSS.
  const big = svg.replace(/<svg([^>]*?)\sstyle="[^"]*"/, '<svg$1')
                 .replace(/<svg([^>]*?)\swidth="[^"]*"/, '<svg$1')
                 .replace(/<svg([^>]*?)\sheight="[^"]*"/, '<svg$1')
                 .replace(/<svg /, '<svg style="width:100%;height:auto;display:block" ');
  const overlay = document.createElement('div');
  overlay.className = 'mermaid-viewer-overlay';
  overlay.innerHTML = `
    <div class="mermaid-viewer-content">
      <div class="mermaid-viewer-toolbar">
        <button class="mermaid-viewer-btn mermaid-viewer-zoom-out" title="Zoom out">&minus;</button>
        <span class="mermaid-viewer-zoom-label">100%</span>
        <button class="mermaid-viewer-btn mermaid-viewer-zoom-in" title="Zoom in">+</button>
        <button class="mermaid-viewer-btn mermaid-viewer-zoom-reset" title="Fit to view">&#8634;</button>
        <button class="mermaid-viewer-btn mermaid-viewer-source-toggle" title="Toggle source">&lt;/&gt; source</button>
        <button class="mermaid-viewer-btn mermaid-viewer-dl" title="Download as PNG">&#8681; save</button>
        ${_ivWinControlsHTML()}
      </div>
      <div class="mermaid-viewer-scroll">
        <div class="mermaid-viewer-svg">${big}</div>
      </div>
      <pre class="mermaid-source" style="display:none">${esc(source)}</pre>
    </div>`;
  document.body.appendChild(overlay);
  if (typeof makeResizable === 'function') makeResizable(overlay.querySelector('.mermaid-viewer-content'));
  const svgWrap = overlay.querySelector('.mermaid-viewer-svg');
  const zoomLabel = overlay.querySelector('.mermaid-viewer-zoom-label');
  const gest = _ivGestures(overlay.querySelector('.mermaid-viewer-scroll'), svgWrap, zoomLabel);
  const content = overlay.querySelector('.mermaid-viewer-content');
  const toolbarEl = overlay.querySelector('.mermaid-viewer-toolbar');
  const win = _ivWindowify(overlay, content, toolbarEl);
  // Measured BEFORE content's width is touched, while the toolbar still has
  // its roomy CSS-default box to lay out in unclamped (see _ivToolbarMinWidth).
  const toolbarMinW = _ivToolbarMinWidth(toolbarEl);
  // Size to the diagram the way the image viewer sizes to the picture. The
  // rendered <svg> had its width/height stripped for the fill-the-box layout,
  // so its intrinsic size comes from the viewBox; a missing or degenerate one
  // just leaves the CSS default alone.
  const vb = (svgWrap.querySelector('svg')?.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
  if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
    gest.setNatural(vb[2], vb[3]);
    // Desktop: size the WINDOW to the diagram's natural pixels (never bigger —
    // magnifying on open looks soft). Mobile keeps the CSS 95vw/92vh "modal
    // treatment" frame regardless of the diagram's own natural size — a small
    // viewBox must still open legible on a phone, not pinned to 1:1. Either
    // way, gest.fit() below is what actually scales the diagram to fill
    // whatever frame this leaves it with.
    if (!_ivMobile()) {
      const box = _ivFitBox(vb[2], vb[3], toolbarMinW, _ivChrome(content, toolbarEl));
      content.style.width = box.w + 'px';
      // Drag-resize (makeResizable) reads computed min-width as ITS floor, so
      // this has to move too or a manual resize could shrink the window back
      // down past the toolbar's own requirement.
      content.style.minWidth = Math.max(320, toolbarMinW + 2) + 'px';
      content.style.height = box.h + 'px';
    } else {
      content.style.minWidth = '320px';
    }
    gest.fit();
  }
  const closeIt = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey);
    gest.destroy();
    win.destroy();
  };
  // Close on backdrop click — but ONLY when the gesture also STARTED on the
  // backdrop. A `click` fires on the nearest common ancestor of mousedown and
  // mouseup, so dragging the resize corner (inside the content) and releasing
  // over the backdrop reported e.target === overlay and slammed the viewer shut
  // mid-resize. Same for a text selection dragged past the edge.
  let _downOnBackdrop = false;
  overlay.addEventListener('mousedown', e => { _downOnBackdrop = (e.target === overlay); });
  overlay.addEventListener('click', e => {
    if (e.target === overlay && _downOnBackdrop) closeIt();
  });
  overlay.querySelector('._iv-close').addEventListener('click', closeIt);
  overlay.querySelector('.mermaid-viewer-source-toggle').addEventListener('click', e => {
    e.stopPropagation();
    const pre = overlay.querySelector('.mermaid-source');
    pre.style.display = pre.style.display === 'none' ? 'block' : 'none';
  });
  overlay.querySelector('.mermaid-viewer-dl').addEventListener('click', e => {
    e.stopPropagation();
    _downloadMermaid(overlay);
  });
  overlay.querySelector('.mermaid-viewer-zoom-in').addEventListener('click', e => {
    e.stopPropagation(); gest.zoomBy(1.25);
  });
  overlay.querySelector('.mermaid-viewer-zoom-out').addEventListener('click', e => {
    e.stopPropagation(); gest.zoomBy(1 / 1.25);
  });
  overlay.querySelector('.mermaid-viewer-zoom-reset').addEventListener('click', e => {
    e.stopPropagation(); gest.fit();
  });
  const onKey = e => {
    if (!win.isTop()) return;                     // only the front window listens
    if (_isTypingTarget(e.target)) return;        // caret keys + '-'/'0' belong to the field
    if (e.key === 'Escape') closeIt();
    else if (e.key === '+' || e.key === '=') gest.zoomBy(1.25);
    else if (e.key === '-') gest.zoomBy(1 / 1.25);
    else if (e.key === '0') gest.fit();
  };
  document.addEventListener('keydown', onKey);
}

// Lightbox for inline agent-output images. Reuses the mermaid-viewer
// overlay chrome (backdrop, toolbar, Esc/click-out close) for visual
// consistency; click the image or +/- to zoom.
// ── Download helpers (shared by the image + mermaid viewers) ────────────────
function _dlBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function _dlStamp() {
  const d = new Date();
  const p = n => String(n).padStart(2, '0');
  return `${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}-${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`;
}
// Prefer the real file's name — /api/serve-image carries it in ?path= — so a
// saved attachment keeps its original name instead of a generic stamp.
function _imgFilename(src, mime) {
  try {
    const u = new URL(src, location.href);
    const p = u.searchParams.get('path') || u.pathname;
    const base = decodeURIComponent(p).split(/[/\\]/).pop() || '';
    if (base && /\.[a-z0-9]{2,5}$/i.test(base)) return base;
  } catch (_) { /* data: URL or unparseable — fall through to the stamp */ }
  const ext = ((mime || '').split('/')[1] || 'png').replace('svg+xml', 'svg');
  return `image-${_dlStamp()}.${ext}`;
}
async function _downloadImage(src) {
  try {
    // Fetch → blob so the download is forced even when the server serves the
    // image inline (Content-Disposition), and so we can read the real MIME.
    const res = await fetch(src);
    const blob = await res.blob();
    _dlBlob(blob, _imgFilename(src, blob.type));
  } catch (_) {
    // Same-origin and data: URLs still save fine via a plain anchor.
    const a = document.createElement('a');
    a.href = src;
    a.download = _imgFilename(src, '');
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}

// Export a rendered mermaid diagram as a PNG (3x, flattened onto white so the
// transparent SVG background doesn't come out black in other apps).
//
// mermaid emits <foreignObject> labels (htmlLabels: true). Chrome rasterises
// those correctly through an <img> — verified, text and all — but that is an
// engine-dependent behaviour, and a SILENTLY BLANK png would be worse than no
// png. So any failure falls back to saving the SVG, which always works and is
// vector anyway.
function _downloadMermaid(overlay) {
  const svgEl = overlay.querySelector('.mermaid-viewer-svg svg');
  if (!svgEl) return;
  const clone = svgEl.cloneNode(true);
  const vb = (svgEl.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
  const rect = svgEl.getBoundingClientRect();
  const w = Math.ceil((vb.length === 4 && vb[2]) ? vb[2] : (rect.width || 800));
  const h = Math.ceil((vb.length === 4 && vb[3]) ? vb[3] : (rect.height || 600));
  clone.removeAttribute('style');          // the viewer forces width:100% — drop it
  clone.setAttribute('width', w);
  clone.setAttribute('height', h);
  if (!clone.getAttribute('xmlns')) clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
  const svgStr = new XMLSerializer().serializeToString(clone);
  const saveSvg = () => _dlBlob(
    new Blob([svgStr], { type: 'image/svg+xml;charset=utf-8' }),
    `diagram-${_dlStamp()}.svg`);
  const img = new Image();
  img.onload = () => {
    try {
      const S = 3;                          // 3x so the text stays crisp
      const c = document.createElement('canvas');
      c.width = w * S;
      c.height = h * S;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob(blob => (blob ? _dlBlob(blob, `diagram-${_dlStamp()}.png`) : saveSvg()), 'image/png');
    } catch (_) { saveSvg(); }
  };
  img.onerror = saveSvg;
  img.src = 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(svgStr);
}

// Background modes for the viewer canvas. White first — it's the sane default
// for reading artwork; the checkerboard (transparency indicator) is opt-in.
const _IV_BGS = ['white', 'checker', 'dark'];
const _IV_BG_KEY = 'mc_img_viewer_bg';
function _ivBgGet() {
  try {
    const v = localStorage.getItem(_IV_BG_KEY);
    return _IV_BGS.includes(v) ? v : 'white';
  } catch (_) { return 'white'; }
}

function _openImageViewer(src) {
  const overlay = document.createElement('div');
  overlay.className = 'mermaid-viewer-overlay';
  overlay.innerHTML = `
    <div class="mermaid-viewer-content">
      <div class="mermaid-viewer-toolbar">
        <button class="mermaid-viewer-btn _iv-zo" title="Zoom out">&minus;</button>
        <span class="mermaid-viewer-zoom-label">100%</span>
        <button class="mermaid-viewer-btn _iv-zi" title="Zoom in">+</button>
        <button class="mermaid-viewer-btn _iv-zr" title="Fit to view">&#8634;</button>
        <button class="mermaid-viewer-btn _iv-bg" title="Background (white / checker / dark)">&#9673; bg</button>
        <button class="mermaid-viewer-btn _iv-dl" title="Download image">&#8681; save</button>
        <a class="mermaid-viewer-btn _iv-open" href="${src}" target="_blank" rel="noopener" title="Open original">open ↗</a>
        <span class="iv-counter"></span>
        ${_ivWinControlsHTML()}
      </div>
      <div class="mermaid-viewer-scroll">
        <div class="mermaid-viewer-svg"><img src="${src}" style="display:block;width:100%;height:auto" alt=""></div>
      </div>
      <button class="iv-nav-btn iv-nav-prev" title="Previous (←)" style="display:none">&#8249;</button>
      <button class="iv-nav-btn iv-nav-next" title="Next (→)" style="display:none">&#8250;</button>
    </div>`;
  document.body.appendChild(overlay);
  if (typeof makeResizable === 'function') makeResizable(overlay.querySelector('.mermaid-viewer-content'));
  const wrap = overlay.querySelector('.mermaid-viewer-svg');
  const zoomLabel = overlay.querySelector('.mermaid-viewer-zoom-label');
  const content = overlay.querySelector('.mermaid-viewer-content');
  const scrollEl = overlay.querySelector('.mermaid-viewer-scroll');
  const imgEl = overlay.querySelector('.mermaid-viewer-svg img');

  const gest = _ivGestures(scrollEl, wrap, zoomLabel);
  const toolbarEl = overlay.querySelector('.mermaid-viewer-toolbar');
  const win = _ivWindowify(overlay, content, toolbarEl);
  // Measured BEFORE content's width is touched, while the toolbar still has
  // its roomy CSS-default box to lay out in unclamped (see _ivToolbarMinWidth).
  const toolbarMinW = _ivToolbarMinWidth(toolbarEl);

  // ── Background mode (persisted) ──
  let bg = _ivBgGet();
  const applyBg = () => {
    scrollEl.classList.remove('vbg-checker', 'vbg-dark');
    if (bg !== 'white') scrollEl.classList.add('vbg-' + bg);
    try { localStorage.setItem(_IV_BG_KEY, bg); } catch (_) {}
  };
  applyBg();
  overlay.querySelector('._iv-bg').addEventListener('click', e => {
    e.stopPropagation();
    bg = _IV_BGS[(_IV_BGS.indexOf(bg) + 1) % _IV_BGS.length];
    applyBg();
  });
  overlay.querySelector('._iv-dl').addEventListener('click', e => {
    e.stopPropagation();
    _downloadImage(src);
  });

  // ── Open at the picture's size, not a hard-coded 95vw x 92vh ──
  // The CSS default filled the screen for a thumbnail AND for a screenshot.
  // _ivFitBox sizes the window to the whole picture, shrinking by aspect when
  // it has to, so it opens showing everything and leaves the dashboard visible
  // around it. Drag-resize from any edge still applies afterwards.
  const sizeToImage = () => {
    const nw = imgEl.naturalWidth, nh = imgEl.naturalHeight;
    if (!nw || !nh) return;                       // decode failed — keep CSS default
    gest.setNatural(nw, nh);
    // Desktop: size the WINDOW to the picture's natural pixels (never bigger —
    // magnifying on open looks soft). Mobile keeps the CSS 95vw/92vh "modal
    // treatment" frame regardless of the picture's own size. Either way,
    // gest.fit() below scales the picture to fill whatever frame this leaves.
    if (!_ivMobile()) {
      const box = _ivFitBox(nw, nh, toolbarMinW, _ivChrome(content, toolbarEl));
      content.style.width = box.w + 'px';
      // Drag-resize (makeResizable) reads computed min-width as ITS floor, so
      // this has to move too or a manual resize could shrink the window back
      // down past the toolbar's own requirement.
      content.style.minWidth = Math.max(320, toolbarMinW + 2) + 'px';
      content.style.height = box.h + 'px';
    } else {
      content.style.minWidth = '320px';
    }
    gest.fit();
  };
  if (imgEl.complete) sizeToImage();
  else imgEl.addEventListener('load', sizeToImage, { once: true });
  const closeIt = () => {
    overlay.remove();
    document.removeEventListener('keydown', onKey);
    gest.destroy();
    win.destroy();
  };
  // Close on backdrop click — but ONLY when the gesture also STARTED there. A
  // `click` fires on the nearest common ancestor of mousedown and mouseup, so
  // dragging the resize corner (inside the content) and releasing over the
  // backdrop reported e.target === overlay and made the image vanish mid-resize.
  let _downOnBackdrop = false;
  overlay.addEventListener('mousedown', e => { _downOnBackdrop = (e.target === overlay); });
  overlay.addEventListener('click', e => {
    if (e.target === overlay && _downOnBackdrop) closeIt();
  });
  overlay.querySelector('._iv-close').addEventListener('click', closeIt);
  overlay.querySelector('._iv-zi').addEventListener('click', e => {
    e.stopPropagation(); gest.zoomBy(1.25);
  });
  overlay.querySelector('._iv-zo').addEventListener('click', e => {
    e.stopPropagation(); gest.zoomBy(1 / 1.25);
  });
  overlay.querySelector('._iv-zr').addEventListener('click', e => {
    e.stopPropagation(); gest.fit();
  });

  // ── Prev/Next through the other image files in the same folder ──
  // Only meaningful when `src` names a real file on disk (/api/serve-image
  // ?path=...) — a mermaid diagram data: URL or anything else has no
  // "folder" to step through, so the arrows/counter just stay hidden (their
  // CSS default) rather than erroring.
  const counterEl = overlay.querySelector('.iv-counter');
  const prevBtn = overlay.querySelector('.iv-nav-prev');
  const nextBtn = overlay.querySelector('.iv-nav-next');
  let siblings = null;   // { files: [absPath, ...], } once loaded
  let ivIdx = 0;
  const srcPath = (() => {
    try { return new URL(src, location.href).searchParams.get('path'); } catch (_) { return null; }
  })();
  const updateNavUI = () => {
    const n = siblings ? siblings.files.length : 0;
    const show = n > 1;
    prevBtn.style.display = show ? '' : 'none';
    nextBtn.style.display = show ? '' : 'none';
    counterEl.textContent = show ? `${ivIdx + 1} / ${n}` : '';
  };
  const goTo = (i) => {
    if (!siblings) return;
    const n = siblings.files.length;
    ivIdx = ((i % n) + n) % n;
    const newSrc = '/api/serve-image?path=' + encodeURIComponent(siblings.files[ivIdx]);
    imgEl.src = newSrc;
    overlay.querySelector('._iv-open').href = newSrc;
    updateNavUI();
  };
  if (srcPath) {
    fetch('/api/serve-image/siblings?path=' + encodeURIComponent(srcPath))
      .then(r => (r.ok ? r.json() : null))
      .then(data => {
        if (!data || !Array.isArray(data.files) || !data.files.length) return;
        siblings = data;
        ivIdx = data.index || 0;
        updateNavUI();
      })
      .catch(() => {});
  }
  // Re-fit the zoom to whatever image is now loaded — covers both the first
  // load and every nav step (sizeToImage above only ever fires once, for the
  // window's own initial sizing; navigating deliberately leaves the window's
  // size/position alone and only re-fits the picture inside it).
  imgEl.addEventListener('load', () => {
    if (imgEl.naturalWidth && imgEl.naturalHeight) {
      gest.setNatural(imgEl.naturalWidth, imgEl.naturalHeight);
      gest.fit();
    }
  });
  prevBtn.addEventListener('click', e => { e.stopPropagation(); goTo(ivIdx - 1); });
  nextBtn.addEventListener('click', e => { e.stopPropagation(); goTo(ivIdx + 1); });

  const onKey = e => {
    if (!win.isTop()) return;                     // only the front window listens
    if (_isTypingTarget(e.target)) return;        // caret keys + '-'/'0' belong to the field
    if (e.key === 'Escape') closeIt();
    else if (e.key === '+' || e.key === '=') gest.zoomBy(1.25);
    else if (e.key === '-') gest.zoomBy(1 / 1.25);
    else if (e.key === '0') gest.fit();
    else if (e.key === 'ArrowLeft') goTo(ivIdx - 1);
    else if (e.key === 'ArrowRight') goTo(ivIdx + 1);
  };
  document.addEventListener('keydown', onKey);
}

// ── ES-module interop ───────────────────────────────────────────────────────
// Re-expose page-called functions on window. Inbound inline callers:
// refreshModal + openPlanViewer call _renderAllMermaidPlaceholders after a
// rebuild; the outputLines builder + openPlanViewer's body builder emit
// placeholders via _mermaidPlaceholderHTML; appendAgentLine's streaming path
// calls _handleMermaidLine; and the rich-text formatter generates
// onclick="_openImageViewer(this.src)" attributes, which resolve against
// the global object at click time. Everything else is module-private
// (_mermaidBuffers, _resizeSvgForFit, _sweepOrphanMermaidNodes,
// _renderViaExcalidraw, _renderViaMermaid, and _openMermaidViewer — wired
// only via region-internal addEventListener). No accessor or identity
// bridges needed: the formal scans found zero generated-handler assignments
// and zero wholesale reassignments of any region binding anywhere.
// The mermaid library loader/theming stays as the inline <head> module
// (window.mermaid + 'mermaid-ready'); this module only consumes it.
window._mermaidPlaceholderHTML = _mermaidPlaceholderHTML;
window._renderAllMermaidPlaceholders = _renderAllMermaidPlaceholders;
window._handleMermaidLine = _handleMermaidLine;
window._openImageViewer = _openImageViewer;
