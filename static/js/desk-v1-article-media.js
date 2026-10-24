// Body media uses the existing library chooser. Persisted Markdown is portable;
// editor widgets serialize back to that same text rather than raw HTML.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const line = /^(!?)\[([^\]]*)\]\(([^)]+)\)$/;
  function parse(text) {
    const match = line.exec(String(text || '').trim());
    if (!match) return null;
    let path; try { path = decodeURI(match[3]); } catch (_) { return null; }
    if (!path.startsWith('desk/library/') || path.split(/[\\/]/).some((s) => s === '..' || s === '.') || /[\x00-\x1f<>]/.test(path)) return null;
    return { title: match[2], path, image: !!match[1] };
  }
  function pathsIn(text) {
    const seen = new Set();
    return String(text || '').split('\n').map(parse).filter((it) => it && !seen.has(it.path) && seen.add(it.path));
  }
  function marker(it) {
    const title = String(it.title || 'File').replace(/[\[\]\r\n]/g, '');
    return `${it.kind === 'image' ? '!' : ''}[${title}](${encodeURI(it.path).replace(/\(/g, '%28').replace(/\)/g, '%29')})`;
  }
  function html(text, editable = true) {
    const it = parse(text);
    if (!it) return null;
    return `<figure class="desk-v1-article-embed" contenteditable="false" data-article-marker="${esc(text)}" data-article-path="${esc(it.path)}">
      ${editable ? '<button type="button" data-article-remove aria-label="Remove embedded media">✕</button>' : ''}
      <span data-article-preview>Loading ${esc(it.title)}…</span><figcaption>${esc(it.title)}</figcaption></figure>`;
  }
  async function hydrate(root) {
    const nodes = [...root.querySelectorAll('[data-article-path]')];
    nodes.forEach((node) => {
      const remove = node.querySelector('[data-article-remove]');
      if (remove) remove.onclick = () => { node.remove(); root.dispatchEvent(new Event('input', { bubbles: true })); };
    });
    if (!nodes.length || !window.DeskV1LibraryPicker) return;
    try {
      const files = await window.DeskV1LibraryPicker.files(['image', 'video', 'file', 'audio']);
      if (!root.isConnected) return;
      nodes.forEach((node) => {
        const it = files.find((f) => f.path === node.dataset.articlePath);
        const preview = node.querySelector('[data-article-preview]');
        if (!preview) return;
        if (!it) { preview.textContent = 'File is no longer in the library.'; return; }
        if (it.kind === 'image' && it.src) preview.innerHTML = `<button type="button" data-article-open aria-label="View ${esc(it.title)} full size"><img src="${esc(it.src)}" alt="${esc(it.title)}"></button>`;
        else if (it.kind === 'video' && it.play) preview.innerHTML = `<video controls playsinline preload="metadata" src="${esc(it.play)}"></video>`;
        else if (it.kind === 'audio' && it.play) preview.innerHTML = `<audio controls preload="metadata" src="${esc(it.play)}"></audio>`;
        else if (it.play || it.download) preview.innerHTML = `<a href="${esc(it.download || it.play)}" target="_blank" rel="noopener">Open ${esc(it.title)}</a>`;
        else preview.textContent = it.title;
        if (!['image', 'video'].includes(it.kind)) {
          const full = node.querySelector('[data-sa-pic-open]'); if (full) full.remove();
        }
        node.querySelectorAll('[data-article-open], [data-sa-pic-open]').forEach((button) => {
          button.onclick = () => { if (window.DeskV1LibraryViewer) window.DeskV1LibraryViewer.open([it], 0); };
        });
      });
    } catch (e) {
      nodes.forEach((n) => { const p = n.querySelector('[data-article-preview]'); if (p) p.textContent = 'Could not read the library: ' + e.message; });
    }
  }
  function pick(body, button) {
    const selection = window.getSelection();
    let block = selection && body.contains(selection.anchorNode) ? selection.anchorNode : null;
    while (block && block.parentNode !== body) block = block.parentNode;
    window.DeskV1LibraryPicker.open({ kinds: ['image', 'video', 'file', 'audio'], returnFocus: button, onPick: (it) => {
      const text = marker(it);
      if (!parse(text)) { window.DeskV1Kit.toast('This file cannot be embedded.'); return; }
      const holder = document.createElement('div'); holder.innerHTML = html(text);
      const embed = holder.firstElementChild;
      if (block && block.isConnected) block.after(embed); else body.appendChild(embed);
      body.dispatchEvent(new Event('input', { bubbles: true }));
      hydrate(body);
      window.DeskV1Kit.toast(it.provenance ? `Added “${it.title}” to the article. A copy is saved in the library.` : `Added “${it.title}” from the library; nothing was copied.`);
    } });
  }
  function wire(root, tab) {
    if (!root || root.dataset.articleMediaWired) return;
    root.dataset.articleMediaWired = '1';
    const body = root.querySelector('[data-writer-body]');
    const actions = root.querySelector('.desk-v1-writer-actions');
    if (!root.querySelector('[data-sa-library]')) {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'btn-secondary';
      button.textContent = 'Pick from library'; button.dataset.articleLibrary = '';
      button.onclick = () => pick(body, button); actions.appendChild(button);
    }
    hydrate(body);
  }
  function mount(host, getText) {
    const refresh = () => {
      host.innerHTML = pathsIn(getText()).map((it) => html(marker({ ...it, kind: it.image ? 'image' : 'file' }), false)).join('');
      host.querySelectorAll('[data-article-path]').forEach((node) => {
        node.dataset.saPic = '';
        const button = document.createElement('button'); button.type = 'button'; button.dataset.saPicOpen = '';
        button.className = 'btn-secondary'; button.textContent = 'View full size'; node.appendChild(button);
      });
      hydrate(host);
    };
    refresh(); return { refresh };
  }
  window.DeskV1ArticleMedia = { parse, pathsIn, marker, html, hydrate, pick, mount, wire };
})();
