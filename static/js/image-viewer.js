// Native picture window. Public entry point stays compatible with src-only
// callers; a source element or explicit list scopes navigation to its message.
import { imageGestures } from './image-viewer-gestures.js';
import { folderImages, folderIndex } from './image-viewer-folder.js';

const backgrounds = ['white', 'checker', 'dark'];
// Shared data bridge with diagram windows; whichever viewer was raised last
// owns the keyboard, including when a picture and diagram are both open.
const windowState = window._viewerWindows ||= { stack: [], z: 10000 };
const viewerStack = windowState.stack;
const icons = {
  rotate: '<path d="M4 8a7 7 0 1 1-1 7M4 3v5h5"/>',
  copy: '<rect x="8" y="8" width="12" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  print: '<path d="M6 8V3h12v5M6 17H3V8h18v9h-3M6 13h12v8H6zM17 11h1"/>',
  save: '<path d="M12 3v12m-5-5 5 5 5-5M4 17v4h16v-4"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v1"/>',
  background: '<path d="M3 3h18v18H3zM3 3l18 18M3 21 21 3"/>',
  filmstrip: '<rect x="2" y="4" width="20" height="16" rx="1"/><path d="M6 4v16M18 4v16M2 9h4m-4 6h4M18 9h4m-4 6h4"/>',
  fullscreen: '<path d="M3 9V3h6m6 0h6v6M3 15v6h6m6 0h6v-6"/>',
  fit: '<path d="M3 8V3h5m8 0h5v5M3 16v5h5m8 0h5v-5M7 7h10v10H7z"/>',
  actual: '<path d="M5 8h2v9M13 8h2v9M10 10v1m0 3v1"/>',
  prev: '<path d="m15 5-7 7 7 7"/>', next: '<path d="m9 5 7 7-7 7"/>',
  close: '<path d="m6 6 12 12M6 18 18 6"/>',
  minimize: '<path d="M5 12h14"/>', maximize: '<rect x="5" y="5" width="14" height="14"/>',
  open: '<path d="M14 3h7v7m0-7L10 14M10 3H3v18h18v-7"/>',
  minus: '<path d="M5 12h14"/>', plus: '<path d="M5 12h14m-7-7v14"/>',
};
const icon = name => `<svg viewBox="0 0 24 24" aria-hidden="true">${icons[name] || ''}</svg>`;
const button = (name, label, text = '') => `<button type="button" data-iv="${name}" title="${label}" aria-label="${label}">${icon(name)}${text ? `<span class="iv-button-label">${text}</span>` : ''}</button>`;
const absolute = src => { try { return new URL(src, location.href).href; } catch { return src; } };
function filename(src) {
  try {
    const u = new URL(src, location.href);
    if (u.protocol === 'data:' || u.protocol === 'blob:') return 'Image';
    // URLSearchParams already decodes ?path; don't decode a literal % twice.
    const path = u.searchParams.get('path') || decodeURIComponent(u.pathname);
    return path.split(/[\\/]/).pop() || 'Image';
  } catch { return 'Image'; }
}
function messageImages(source) {
  const gallery = source.closest('[data-image-gallery]');
  if (gallery) return [...gallery.querySelectorAll('img')].map(img => ({ src: img.src, name: img.alt || filename(img.src) }));
  const line = source.closest('.agent-line');
  if (!line) return [{ src: source.src }];
  // Chat narration is rendered one div per line, rather than one message div.
  // A prompt/tool/status boundary stops traversal; never scan the whole chat.
  const plain = el => el?.classList.contains('agent-line') && ![...el.classList].some(c => /^agent-line-/.test(c));
  let first = line;
  if (plain(line)) while (plain(first.previousElementSibling)) first = first.previousElementSibling;
  const list = [];
  for (let el = first; el; el = el.nextElementSibling) {
    if (el !== line && !plain(el)) break;
    list.push(...[...el.querySelectorAll('.agent-img')].map(img => ({ src: img.src })));
    if (!plain(line)) break;
  }
  return list;
}
const sizeLabel = bytes => bytes < 1024 ? `${bytes} B` : bytes < 1048576 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1048576).toFixed(1)} MB`;

export function openImageViewer(src, options = {}) {
  if (!src) return;
  const raw = options.images || (options.source ? messageImages(options.source) : [src]);
  const images = raw.map(it => typeof it === 'string' ? { src: it } : it).filter(it => it?.src);
  let index = images.findIndex(it => absolute(it.src) === absolute(src));
  if (index < 0) { images.unshift({ src }); index = 0; }
  const mac = (options.platform || window.__imageViewerPlatform || navigator.userAgentData?.platform || navigator.platform).toLowerCase().includes('mac');
  const overlay = document.createElement('div');
  overlay.className = 'image-viewer-overlay';
  const viewer = document.createElement('section');
  viewer.className = `image-viewer${mac ? ' iv-mac' : ''}`;
  viewer.setAttribute('role', 'dialog');
  viewer.setAttribute('aria-label', 'Image viewer');
  viewer.innerHTML = `
    <header class="iv-titlebar"><span class="iv-titlemark">${icon('filmstrip')}</span><h2 class="iv-title"></h2>
      <div class="iv-window-controls">${button('minimize', 'Minimize')}${button('maximize', 'Maximize')}${button('close', 'Close (Esc)')}</div></header>
    <div class="iv-toolbar" role="toolbar" aria-label="Picture tools">
      ${button('rotate', 'Rotate clockwise', 'Rotate')}${button('copy', 'Copy image', 'Copy')}${button('print', 'Print image')}
      <span class="iv-divider"></span>${button('save', 'Save image', 'Save')}
      <a data-iv="open" target="_blank" rel="noopener" title="Open original" aria-label="Open original">${icon('open')}</a>
      <span class="iv-tool-spacer"></span>${button('background', 'Background: white')}${button('info', 'Image information')}${button('fullscreen', 'Fullscreen')}</div>
    <div class="iv-workspace"><div class="iv-stage" data-background="white"><img class="iv-image" alt="" draggable="false" hidden></div>
      <aside class="iv-info" aria-label="Image information" hidden><h3>Image information</h3><dl>
        <dt>Name</dt><dd data-info="name"></dd><dt>Format</dt><dd data-info="format">Loading…</dd>
        <dt>Dimensions</dt><dd data-info="dimensions">Loading…</dd><dt>File size</dt><dd data-info="size">Loading…</dd>
      </dl></aside></div>
    <nav class="iv-filmstrip" aria-label="Pictures" hidden></nav>
    <footer class="iv-statusbar">${button('filmstrip', 'Toggle filmstrip')}<span class="iv-count" aria-live="polite"></span>
      <div class="iv-file-summary"><span data-summary="format"></span><span data-summary="dimensions"></span><span data-summary="size"></span></div>
      <div class="iv-zoom">${button('fit', 'Fit to view')}${button('actual', 'Actual size (100%)')}
        <select data-iv="zoom" aria-label="Zoom percentage"><option value="fit">Fit</option>${[10,25,50,75,100,125,150,200,400,800,1600].map(n => `<option value="${n}">${n}%</option>`).join('')}<option value="current" hidden></option></select>
        ${button('minus', 'Zoom out')}<input type="range" data-iv="slider" aria-label="Zoom" min="1" max="1600" value="100">${button('plus', 'Zoom in')}</div></footer>`;
  overlay.append(viewer); document.body.append(overlay);
  const $ = name => viewer.querySelector(`[data-iv="${name}"]`);
  const stage = viewer.querySelector('.iv-stage'), image = viewer.querySelector('.iv-image');
  const info = viewer.querySelector('.iv-info'), strip = viewer.querySelector('.iv-filmstrip');
  const title = viewer.querySelector('.iv-title');
  const returnFocus = document.activeElement;
  let closed = false, chip = null, request = null, blobPromise = null, currentName = '', rotation = 0;
  let bg = 'white';
  try { const saved = localStorage.getItem('mc_img_viewer_bg'); if (backgrounds.includes(saved)) bg = saved; } catch { /* optional preference */ }
  const notify = text => {
    viewer.querySelector('.iv-notice')?.remove();
    const notice = document.createElement('div'); notice.className = 'iv-notice'; notice.setAttribute('role', 'status'); notice.textContent = text;
    viewer.querySelector('.iv-workspace').append(notice);
  };
  const top = () => viewerStack.at(-1) === overlay;
  const untrack = () => { const i = viewerStack.indexOf(overlay); if (i >= 0) viewerStack.splice(i, 1); };
  const raise = () => { untrack(); viewerStack.push(overlay); overlay.style.zIndex = ++windowState.z; };
  viewer.addEventListener('pointerdown', raise); raise();
  const gestures = imageGestures(stage, image, state => {
    rotation = state.angle;
    const percent = Math.round(state.scale * 100);
    const custom = $('zoom').querySelector('[value=current]');
    custom.textContent = `${percent}%`; custom.hidden = false; $('zoom').value = 'current';
    $('slider').value = percent;
    $('fit').setAttribute('aria-pressed', String(state.fitted));
    $('actual').setAttribute('aria-pressed', String(Math.abs(state.scale - 1) < .001 && !state.fitted));
    viewer.dataset.rotation = state.angle;
    viewer.dataset.zoom = state.scale;
  }, delta => go(index + delta));
  const metadata = (key, value) => {
    viewer.querySelector(`[data-info="${key}"]`).textContent = value;
    const summary = viewer.querySelector(`[data-summary="${key}"]`);
    if (summary) summary.textContent = value === 'Loading…' || value === 'Unavailable' ? '' : value;
  };
  const applyBackground = () => {
    stage.dataset.background = bg;
    $('background').title = `Background: ${bg}`; $('background').setAttribute('aria-label', $('background').title);
    try { localStorage.setItem('mc_img_viewer_bg', bg); } catch { /* optional preference */ }
  };
  const go = next => {
    index = (next + images.length) % images.length;
    const item = images[index];
    request?.abort(); request = new AbortController();
    const token = request;
    viewer.querySelector('.iv-notice')?.remove();
    image.hidden = true;
    // A fresh image element is unnecessary: onload checks the active src and
    // dimensions, while abort/version checks protect asynchronous metadata.
    currentName = item.name || filename(item.src);
    title.textContent = currentName; title.title = currentName;
    viewer.setAttribute('aria-label', `Image viewer: ${currentName}`);
    metadata('name', currentName);
    for (const key of ['format', 'dimensions', 'size']) metadata(key, 'Loading…');
    image.alt = currentName;
    image.onload = () => {
      image.hidden = false; gestures.load(image.naturalWidth, image.naturalHeight);
      metadata('dimensions', `${image.naturalWidth} × ${image.naturalHeight}`);
    };
    image.onerror = () => { notify('This image could not be loaded.'); metadata('dimensions', 'Unavailable'); };
    image.src = item.src;
    $('open').href = item.src;
    viewer.querySelector('.iv-count').textContent = `${index + 1} / ${images.length}`;
    strip.querySelectorAll('button').forEach((el, i) => el.setAttribute('aria-current', String(i === index)));
    strip.querySelector('[aria-current=true]')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    // Fetch the current image only. Do not enumerate its directory or infer a
    // size from Content-Length (compressed responses may differ from the file).
    blobPromise = fetch(item.src, { signal: token.signal }).then(response => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.blob();
    }).then(blob => {
      if (closed || token !== request) return null;
      metadata('size', sizeLabel(blob.size));
      metadata('format', (blob.type.replace(/^image\//, '').replace('svg+xml', 'svg') || 'Unknown').toUpperCase());
      return blob;
    }).catch(error => {
      if (closed || token !== request || error.name === 'AbortError') return null;
      metadata('size', 'Unavailable'); metadata('format', 'Unavailable');
      console.warn('[image-viewer] Image metadata unavailable:', error.message);
      return null;
    });
  };
  // Built once, when the list first has 2+ pictures: at open, or later when the
  // folder list arrives for a source open (see the upgrade after go(index)).
  let navBuilt = false;
  const buildNav = () => {
    if (navBuilt || images.length < 2) return;
    navBuilt = true;
    for (const [i, item] of images.entries()) {
      const thumb = document.createElement('button'); thumb.type = 'button'; thumb.className = 'iv-thumb';
      thumb.setAttribute('aria-label', `Show ${item.name || filename(item.src)}`);
      const img = document.createElement('img'); img.loading = 'lazy'; img.src = item.src; img.alt = ''; thumb.append(img);
      thumb.onclick = () => go(i); strip.append(thumb);
    }
    for (const [name, delta] of [['prev', -1], ['next', 1]]) {
      const nav = document.createElement('button'); nav.type = 'button'; nav.className = `iv-nav iv-nav-${name}`;
      nav.setAttribute('aria-label', name === 'prev' ? 'Previous image' : 'Next image');
      nav.innerHTML = icon(name); nav.onclick = () => go(index + delta);
      // Siblings of the stage so swiping/panning never also triggers a button.
      viewer.querySelector('.iv-workspace').append(nav);
    }
    $('filmstrip').disabled = false;
  };
  buildNav();
  $('filmstrip').disabled = !navBuilt;
  $('filmstrip').setAttribute('aria-pressed', 'false');
  $('filmstrip').onclick = () => { strip.hidden = !strip.hidden; $('filmstrip').setAttribute('aria-pressed', String(!strip.hidden)); };
  $('info').setAttribute('aria-pressed', 'false');
  $('info').onclick = () => { info.hidden = !info.hidden; $('info').setAttribute('aria-pressed', String(!info.hidden)); };
  $('background').onclick = () => { bg = backgrounds[(backgrounds.indexOf(bg) + 1) % backgrounds.length]; applyBackground(); };
  $('rotate').onclick = () => gestures.rotate();
  $('fit').onclick = gestures.fit; $('actual').onclick = () => gestures.zoom(1);
  $('plus').onclick = () => gestures.zoomBy(1.25); $('minus').onclick = () => gestures.zoomBy(1 / 1.25);
  $('zoom').onchange = () => $('zoom').value === 'fit' ? gestures.fit() : gestures.zoom(Number($('zoom').value) / 100);
  $('slider').oninput = () => gestures.zoom(Number($('slider').value) / 100);
  const raster = async () => {
    if (image.hidden || !image.naturalWidth) throw new Error('Wait for the image to load.');
    // Capture NOW; navigating while a clipboard/print operation is awaiting a
    // blob must not replace that operation's pixels with the next image.
    const canvas = document.createElement('canvas');
    canvas.width = rotation % 180 ? image.naturalHeight : image.naturalWidth;
    canvas.height = rotation % 180 ? image.naturalWidth : image.naturalHeight;
    const context = canvas.getContext('2d');
    context.translate(canvas.width / 2, canvas.height / 2); context.rotate(rotation * Math.PI / 180);
    context.drawImage(image, -image.naturalWidth / 2, -image.naturalHeight / 2);
    return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('Could not encode this image.')), 'image/png'));
  };
  $('copy').onclick = async () => {
    try {
      if (!navigator.clipboard?.write || typeof ClipboardItem === 'undefined') throw new Error('Copy image is unavailable in this browser. Save the image instead.');
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': raster() })]);
      if (!closed) notify('Image copied.');
    } catch (error) { if (!closed) notify(`Copy failed: ${error.message}`); }
  };
  $('save').onclick = async () => {
    const name = currentName, pending = blobPromise;
    try {
      const blob = await pending;
      if (!blob) throw new Error('Could not read the image file.');
      const url = URL.createObjectURL(blob), link = document.createElement('a');
      link.href = url; link.download = name === 'Image' ? `image.${blob.type.split('/')[1]?.replace('svg+xml', 'svg') || 'png'}` : name;
      document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { if (!closed) notify(`Save failed: ${error.message}`); }
  };
  $('print').onclick = async () => {
    const name = currentName;
    try {
      const blob = await raster();
      if (closed) return;
      const url = URL.createObjectURL(blob), frame = document.createElement('iframe');
      frame.title = 'Print image'; frame.style.cssText = 'position:fixed;width:1px;height:1px;left:-10000px;border:0';
      document.body.append(frame);
      const doc = frame.contentDocument;
      const style = doc.createElement('style'); style.textContent = '@page{margin:12mm}body{margin:0;text-align:center}img{max-width:100%;max-height:95vh;object-fit:contain}'; doc.head.append(style);
      doc.title = name;
      const picture = doc.createElement('img');
      const cleanup = () => { frame.remove(); URL.revokeObjectURL(url); clearTimeout(timer); };
      const timer = setTimeout(cleanup, 60000);
      picture.onload = () => {
        if (closed) { cleanup(); return; }
        try {
          frame.contentWindow.addEventListener('afterprint', cleanup, { once: true });
          frame.contentWindow.focus(); frame.contentWindow.print();
        } catch (error) { cleanup(); notify(`Print failed: ${error.message}`); }
      };
      picture.onerror = () => { cleanup(); notify('Print failed: image could not be prepared.'); };
      picture.src = url; doc.body.append(picture);
    } catch (error) { if (!closed) notify(`Print failed: ${error.message}`); }
  };
  $('fullscreen').onclick = async () => {
    try {
      if (document.fullscreenElement === viewer) await document.exitFullscreen();
      else if (viewer.requestFullscreen) await viewer.requestFullscreen();
      else viewer.classList.toggle('is-maximized');
    } catch (error) { notify(`Fullscreen unavailable: ${error.message}`); }
  };
  $('maximize').onclick = () => {
    const maximized = viewer.classList.toggle('is-maximized');
    $('maximize').title = maximized ? 'Restore down' : 'Maximize'; $('maximize').setAttribute('aria-label', $('maximize').title);
  };
  const tray = document.getElementById('minimized-tray');
  $('minimize').disabled = !tray;
  $('minimize').onclick = () => {
    if (!tray) return;
    overlay.hidden = true; untrack();
    chip = document.createElement('button'); chip.className = 'minimized-chip'; chip.textContent = currentName;
    chip.onclick = () => { overlay.hidden = false; chip.remove(); chip = null; raise(); $('close').focus(); };
    tray.append(chip);
  };
  // Pointer capture keeps dragging inside this window; no persistent document
  // handlers are needed. The app behind a desktop picture stays usable.
  const bar = viewer.querySelector('.iv-titlebar'); let drag = null;
  bar.addEventListener('pointerdown', e => {
    if (e.button !== 0 || e.target.closest('button') || matchMedia('(max-width:960px)').matches || viewer.classList.contains('is-maximized')) return;
    const r = viewer.getBoundingClientRect(); drag = { x: e.clientX, y: e.clientY, left: r.left, top: r.top };
    viewer.style.position = 'fixed'; viewer.style.left = `${r.left}px`; viewer.style.top = `${r.top}px`;
    bar.setPointerCapture(e.pointerId);
  });
  bar.addEventListener('pointermove', e => {
    if (!drag) return;
    viewer.style.left = `${Math.max(40 - viewer.offsetWidth, Math.min(innerWidth - 40, drag.left + e.clientX - drag.x))}px`;
    viewer.style.top = `${Math.max(0, Math.min(innerHeight - 40, drag.top + e.clientY - drag.y))}px`;
  });
  bar.addEventListener('pointerup', () => { drag = null; });
  bar.addEventListener('pointercancel', () => { drag = null; });
  const listRequest = new AbortController();
  const close = () => {
    closed = true; request?.abort(); listRequest.abort(); gestures.destroy(); untrack(); chip?.remove();
    document.removeEventListener('keydown', onKey, true);
    if (document.fullscreenElement === viewer) document.exitFullscreen().catch(error => console.warn('[image-viewer] Exit fullscreen failed:', error.message));
    overlay.remove();
    if (returnFocus?.isConnected) returnFocus.focus({ preventScroll: true });
  };
  const onKey = e => {
    if (e.defaultPrevented || !top()) return;
    // Escape always closes; arrows/sliders/typing keep their native behavior.
    if (e.key === 'Escape' && (viewer.contains(e.target) || !e.target.closest('input, textarea, select, [contenteditable=true]'))) {
      e.preventDefault(); e.stopPropagation(); close(); return;
    }
    if (e.target.closest('input, textarea, select, [contenteditable=true]') || e.ctrlKey || e.metaKey || e.altKey) return;
    const action = { ArrowLeft: () => go(index - 1), ArrowRight: () => go(index + 1), '+': () => gestures.zoomBy(1.25), '=': () => gestures.zoomBy(1.25), '-': () => gestures.zoomBy(.8), '0': gestures.fit, '1': () => gestures.zoom(1) }[e.key];
    if (action) { e.preventDefault(); e.stopPropagation(); action(); }
  };
  $('close').onclick = close; document.addEventListener('keydown', onKey, true);
  applyBackground(); go(index); $('close').focus({ preventScroll: true });
  // A chat/source open scopes to its message, then widens to the picture's
  // folder (the pre-1100 behaviour) once the list arrives. The shown picture is
  // never reloaded; a late answer after close, or after navigating to a picture
  // the folder does not list, is dropped.
  if (!options.images && options.source) {
    const opened = images[index].src;
    folderImages(opened, listRequest.signal).then(folder => {
      if (closed || !folder) return;
      const at = folderIndex(folder, images[index].src, opened);
      if (at < 0) return;
      images.splice(0, images.length, ...folder.images);
      index = at;
      strip.replaceChildren(); viewer.querySelectorAll('.iv-nav').forEach(el => el.remove()); navBuilt = false;
      buildNav();
      viewer.querySelector('.iv-count').textContent = `${index + 1} / ${images.length}`;
      strip.querySelectorAll('button').forEach((el, i) => el.setAttribute('aria-current', String(i === index)));
    });
  }
  return { close, element: viewer };
}

window._openImageViewer = openImageViewer;
