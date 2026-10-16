// Chat [file:...] links: bounded text previews, native media, binary downloads.
const TEXT_EXTENSIONS = new Set(('md markdown mdx txt text log json jsonl ndjson csv tsv yaml yml toml ini cfg conf env xml html htm css scss sass less py pyi js mjs cjs jsx ts tsx vue svelte rs go java kt kts c h cpp hpp cc cs sh bash zsh fish ps1 psm1 bat cmd sql rb php r swift dart lua pl ipynb rst tex diff patch properties gitignore gitattributes editorconfig dockerignore').split(' '));
// Active document formats still obey serve-file's existing inline guards.
const MEDIA_EXTENSIONS = new Set(('png jpg jpeg gif webp bmp svg ico tif tiff avif heic heif pdf mp3 wav ogg oga m4a aac flac opus mp4 webm mov m4v avi ogv').split(' '));
const MAX_BYTES = 2 * 1024 * 1024;
let fileViewerSequence = 0;

function fileLinkKind(path) {
  const name = path.split(/[\\/]/).pop().toLowerCase();
  const extension = name.includes('.') ? name.split('.').pop() : '';
  if (TEXT_EXTENSIONS.has(extension) || !extension) return 'text';
  if (MEDIA_EXTENSIONS.has(extension)) return 'media';
  return 'download';
}

function fileLinkUrl(path, inline = true) {
  return API_BASE + '/api/serve-file?' + (inline ? 'inline=1&' : '') + 'path=' + encodeURIComponent(path);
}

async function readPreview(response) {
  if (Number(response.headers.get('Content-Length')) > MAX_BYTES) {
    await response.body?.cancel();
    throw new Error('File is too large to preview (2 MiB limit). Download instead.');
  }
  // Enforce the cap while streaming too: chunked or compressed responses may
  // omit Content-Length or advertise a smaller size than their decoded body.
  const reader = response.body.getReader();
  const chunks = [];
  let size = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_BYTES) {
        await reader.cancel();
        throw new Error('File is too large to preview (2 MiB limit). Download instead.');
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const bytes = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
  const encoding = bytes[0] === 0xff && bytes[1] === 0xfe ? 'utf-16le'
    : bytes[0] === 0xfe && bytes[1] === 0xff ? 'utf-16be' : 'utf-8';
  let text;
  try { text = new TextDecoder(encoding, { fatal: true }).decode(bytes); }
  catch { throw new Error('This file is not supported text. Download instead.'); }
  if (/[\x00-\x08\x0e-\x1f\x7f]/.test(text)) {
    throw new Error('This file contains binary data. Download instead.');
  }
  return text;
}

async function openFileViewer(path) {
  const existing = [...openModals.entries()].find(([, entry]) => entry.element.dataset.filePath === path);
  if (existing) {
    if (existing[1].minimized) restoreModal(existing[0]);
    focusModal(existing[0]);
    return;
  }
  const modalId = '__file_viewer_' + ++fileViewerSequence;
  const win = document.createElement('div');
  win.className = 'modal-window';
  win.dataset.modalId = modalId;
  win.dataset.filePath = path;
  const content = document.createElement('div');
  content.className = 'modal-content plan-viewer-content file-viewer-content';
  content.setAttribute('role', 'dialog');
  content.setAttribute('aria-label', 'File viewer: ' + path);
  content.innerHTML = `
    <div class="modal-window-controls">
      <button class="modal-minimize" title="Minimize" aria-label="Minimize file viewer">&#x2015;</button>
      <button class="modal-close" title="Close" aria-label="Close file viewer">&#10005;</button>
    </div>
    <div class="plan-viewer-header modal-header file-viewer-header">
      <h3>File viewer</h3>
      <div class="file-viewer-path" dir="ltr"></div>
      <a class="file-viewer-download" target="_blank" rel="noopener">Download</a>
    </div>
    <div class="plan-viewer-body file-viewer-body" role="status">Loading file…</div>`;
  content.querySelector('.file-viewer-path').textContent = path;
  content.querySelector('.file-viewer-download').href = fileLinkUrl(path, false);
  for (const element of content.querySelectorAll('.file-viewer-path, .file-viewer-download')) {
    // The modal header normally starts a drag, including on links and text.
    // Keep path selection and the Download action usable with mouse and touch.
    element.addEventListener('mousedown', event => event.stopPropagation());
    element.addEventListener('touchstart', event => event.stopPropagation(), { passive: true });
  }
  content.querySelector('.modal-close').addEventListener('click', () => closeModalById(modalId));
  content.querySelector('.modal-minimize').addEventListener('click', () => minimizeModal(modalId));
  win.appendChild(content);
  document.getElementById('modal-layer').appendChild(win);
  const z = nextModalZ++;
  win.style.zIndex = z;
  openModals.set(modalId, { projectId: null, element: win, minimized: false, zIndex: z });
  centerModalElement(win);
  focusModal(modalId);
  content.querySelector('.modal-close').focus({ preventScroll: true });
  const body = content.querySelector('.file-viewer-body');
  try {
    const response = await fetch(fileLinkUrl(path));
    if (!response.ok) throw new Error('Could not open file (HTTP ' + response.status + ').');
    const text = await readPreview(response);
    if (!win.isConnected) return;
    body.removeAttribute('role');
    if (/\.(md|markdown|mdx)$/i.test(path)) {
      body.innerHTML = window.renderMarkdownDocument(text);
    } else {
      const pre = document.createElement('pre');
      pre.textContent = text;
      body.replaceChildren(pre);
    }
  } catch (error) {
    body.textContent = error.message;
  }
}

// Delegation also covers streaming chat, saved runs and documents opened later.
// Modified clicks, middle-clicks and long-press keep the anchor's real URL.
document.addEventListener('click', event => {
  if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const link = event.target.closest('a.hl-file-link[data-file-preview]');
  if (!link) return;
  event.preventDefault();
  openFileViewer(link.dataset.filePreview);
});

window.fileLinkKind = fileLinkKind;
window.openFileViewer = openFileViewer;
