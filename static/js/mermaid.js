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
  const paint = () => {
    wrap.style.transform = `scale(${scale})`;
    wrap.style.transformOrigin = 'top left';
    if (zoomLabel) zoomLabel.textContent = Math.round(scale * 100) + '%';
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

  // Wheel = zoom, no modifier needed. Panning a zoomed-in picture is what the
  // drag below is for; a wheel that scrolls a picture by a few pixels is the
  // less useful of the two bindings.
  scrollEl.addEventListener('wheel', e => {
    e.preventDefault();
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
      zoomTo(scale > 1.05 ? 1 : 2.5, t.clientX, t.clientY);
      setCursor();
    } else lastTap = now;
  });

  paint();
  setTimeout(setCursor, 0);
  return {
    zoomBy: f => { zoomTo(scale * f); setCursor(); },
    reset: () => { zoomTo(1); scrollEl.scrollTop = scrollEl.scrollLeft = 0; setCursor(); },
    refit: setCursor,
    // The pan listeners live on `document` so a drag survives the cursor
    // leaving the canvas — which means closing the viewer by removing its
    // overlay does NOT unbind them. Callers must call this from their close
    // path or every picture opened leaks a pair.
    destroy: () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
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
  return {
    raise, untrack, isTop,
    destroy: () => {
      untrack();
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
function _ivFitBox(nw, nh) {
  const CHROME_H = 45 + 48;                     // toolbar + 24px canvas padding x2
  const CHROME_W = 48;
  const f = _ivMobile() ? 0.95 : 0.8;
  const k = Math.min(1, (window.innerWidth * f - CHROME_W) / nw,
                        (window.innerHeight * f - CHROME_H) / nh);
  return {
    w: Math.max(320, Math.round(nw * k) + CHROME_W),
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
        <button class="mermaid-viewer-btn mermaid-viewer-close" title="Close (Esc)">&times;</button>
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
  const win = _ivWindowify(overlay, content, overlay.querySelector('.mermaid-viewer-toolbar'));
  // Size to the diagram the way the image viewer sizes to the picture. The
  // rendered <svg> had its width/height stripped for the fill-the-box layout,
  // so its intrinsic size comes from the viewBox; a missing or degenerate one
  // just leaves the CSS default alone.
  const vb = (svgWrap.querySelector('svg')?.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
  if (vb.length === 4 && vb[2] > 0 && vb[3] > 0) {
    const box = _ivFitBox(vb[2], vb[3]);
    content.style.width = box.w + 'px';
    content.style.height = box.h + 'px';
    gest.refit();
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
  overlay.querySelector('.mermaid-viewer-close').addEventListener('click', closeIt);
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
    e.stopPropagation(); gest.reset();
  });
  const onKey = e => {
    if (!win.isTop()) return;                     // only the front window listens
    if (e.key === 'Escape') closeIt();
    else if (e.key === '+' || e.key === '=') gest.zoomBy(1.25);
    else if (e.key === '-') gest.zoomBy(1 / 1.25);
    else if (e.key === '0') gest.reset();
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
        <button class="mermaid-viewer-btn _iv-close" title="Close (Esc)">&times;</button>
      </div>
      <div class="mermaid-viewer-scroll">
        <div class="mermaid-viewer-svg"><img src="${src}" style="display:block;width:100%;height:auto" alt=""></div>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  if (typeof makeResizable === 'function') makeResizable(overlay.querySelector('.mermaid-viewer-content'));
  const wrap = overlay.querySelector('.mermaid-viewer-svg');
  const zoomLabel = overlay.querySelector('.mermaid-viewer-zoom-label');
  const content = overlay.querySelector('.mermaid-viewer-content');
  const scrollEl = overlay.querySelector('.mermaid-viewer-scroll');
  const imgEl = overlay.querySelector('.mermaid-viewer-svg img');

  const gest = _ivGestures(scrollEl, wrap, zoomLabel);
  const win = _ivWindowify(overlay, content, overlay.querySelector('.mermaid-viewer-toolbar'));

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
    const box = _ivFitBox(nw, nh);
    content.style.width = box.w + 'px';
    content.style.height = box.h + 'px';
    gest.refit();
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
    e.stopPropagation(); gest.reset();
  });
  const onKey = e => {
    if (!win.isTop()) return;                     // only the front window listens
    if (e.key === 'Escape') closeIt();
    else if (e.key === '+' || e.key === '=') gest.zoomBy(1.25);
    else if (e.key === '-') gest.zoomBy(1 / 1.25);
    else if (e.key === '0') gest.reset();
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
