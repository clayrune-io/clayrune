// Studio's host picker. Host files are read only; the final action copies media.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const api = (...args) => window.DeskV1Store.api(...args);
  let active = null;
  const client = Array.from(crypto.getRandomValues(new Uint8Array(16)), (n) => n.toString(16).padStart(2, '0')).join('');
  let requestId = 0;

  function stop(st) {
    clearTimeout(st.timer);
    ++st.seq;
    if (!st.request) return;
    st.request.controller.abort();
    // Target the old request: a delayed cancellation must not cancel its successor.
    api('DELETE', '/api/desk/explorer/search?' + new URLSearchParams({ client, search: st.request.id }))
      .catch((e) => console.warn('Could not cancel file search:', e.message));
    st.request = null;
  }

  function close() {
    if (!active || active.busy) return;
    const old = active;
    stop(old);
    active = null;
    document.removeEventListener('keydown', old.keys, true);
    old.el.remove();
    if (old.focus && old.focus.isConnected) old.focus.focus();
    if (old.changed && old.onChanged) old.onChanged();
  }

  function open(folder, { onChanged } = {}) {
    if (active && active.busy) return;
    close();
    const el = document.createElement('div');
    el.className = 'desk-fe-overlay';
    el.setAttribute('data-file-explorer', '');
    el.innerHTML = `<section class="desk-fe" role="dialog" aria-modal="true" aria-label="Add files to ${esc(folder.title)}">
      <header class="desk-fe-head" data-studio-folderview="${esc(folder.id)}">
        <div><button class="desk-fe-back" data-studio-lib-back>‹ Material library</button><h2>${esc(folder.title)}</h2>
        <span class="desk-v1-studio-folderbar-name">${esc(folder.title)} · ${folder.files} files</span></div>
        <button data-fe-close aria-label="Close file explorer">✕</button>
      </header>
      <details class="desk-fe-saved"${folder.items.length ? ' open' : ''}><summary>In this library folder <span data-fe-saved-count>${folder.files}</span></summary><div class="desk-v1-studio-files" data-fe-saved></div></details>
      <div class="desk-fe-workspace"><nav class="desk-fe-rail" aria-label="Locations" data-fe-shortcuts></nav>
        <div class="desk-fe-main"><nav class="desk-fe-crumbs" aria-label="Current folder" data-fe-crumbs></nav>
          <div class="desk-fe-tools"><input type="search" data-fe-search aria-label="Search this folder" placeholder="Search this folder" maxlength="200">
            <select data-fe-filter aria-label="Show files"><option value="media">Media only</option></select></div>
          <p class="desk-fe-note">Browse your computer. Pictures and videos are copied here; originals stay where they are.</p>
          <div data-fe-results class="desk-fe-results" aria-live="polite"></div>
        </div>
      </div>
      <footer class="desk-fe-footer"><div><span data-fe-count>0 selected</span><button data-fe-clear>Clear selection</button></div><button class="btn-add" data-fe-copy disabled>Copy to folder</button></footer>
    </section>`;
    const st = active = { el, folder, onChanged, focus: document.activeElement, busy: false, changed: false,
      root: 'home', path: '', query: '', filter: 'media', selected: new Map(), seq: 0, keys: null, rows: [], request: null, timer: null };
    const results = el.querySelector('[data-fe-results]');
    const copy = el.querySelector('[data-fe-copy]');
    const paintSelection = () => {
      el.querySelector('[data-fe-count]').textContent = `${st.selected.size} selected`;
      copy.disabled = st.busy || !st.selected.size;
      copy.textContent = st.busy ? 'Copying…' : `Copy to folder${st.selected.size ? ` (${st.selected.size})` : ''}`;
      el.querySelector('[data-fe-clear]').disabled = st.busy || !st.selected.size;
      results.querySelectorAll('[data-fe-pick]').forEach((b) => b.setAttribute('aria-pressed', String(st.selected.has(b.dataset.fePick))));
    };
    const saved = () => {
      el.querySelector('[data-fe-saved-count]').textContent = st.folder.items.length;
      el.querySelector('[data-fe-saved]').innerHTML = st.folder.items.map((it) => `<button class="desk-v1-studio-file" data-studio-file="${esc(it.id)}"><span class="desk-v1-studio-file-thumb">${it.kind === 'image' && it.src ? `<img src="${esc(it.src)}" alt="" loading="lazy">` : '▶'}</span><span class="desk-v1-studio-file-name">${esc(it.title)}</span></button>`).join('');
      window.DeskV1LibraryViewer.wireShelf(el, st.folder.items);
    };
    const load = async () => {
      stop(st);
      const seq = ++st.seq;
      const id = String(++requestId), controller = new AbortController();
      st.request = { id, controller };
      results.innerHTML = '<p>Reading this folder…</p>';
      try {
        const qs = new URLSearchParams({ root: st.root, path: st.path, q: st.query, filter: st.filter, client, search: id });
        const response = await fetch('/api/desk/explorer?' + qs, { signal: controller.signal });
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
        if (active !== st || seq !== st.seq) return;
        const filter = el.querySelector('[data-fe-filter]');
        filter.innerHTML = '<option value="media">Media only</option>' + (data.allow_all_files === true ? '<option value="all">All files</option>' : '');
        if (data.allow_all_files !== true) st.filter = 'media';
        filter.value = st.filter;
        st.rows = data.items;
        el.querySelector('[data-fe-shortcuts]').innerHTML = data.shortcuts.map((it, i) => `<button data-fe-location="${i}" aria-current="${it.root === st.root && it.path === st.path ? 'location' : 'false'}">${esc(it.title)}</button>`).join('');
        el.querySelectorAll('[data-fe-location]').forEach((b) => b.onclick = () => {
          const loc = data.shortcuts[Number(b.dataset.feLocation)]; navigate(loc.root, loc.path);
        });
        el.querySelector('[data-fe-crumbs]').innerHTML = data.breadcrumbs.map((it, i) => `<button data-fe-crumb="${i}">${esc(it.title)}</button>`).join('<span aria-hidden="true">›</span>');
        el.querySelectorAll('[data-fe-crumb]').forEach((b) => b.onclick = () => navigate(st.root, data.breadcrumbs[Number(b.dataset.feCrumb)].path));
        results.innerHTML = `${data.truncated || data.limited ? '<p class="desk-fe-limit">Search truncated. Some files are not shown. Open a smaller folder or narrow your search.</p>' : ''}
          ${st.filter === 'all' ? '<p class="desk-fe-note">All files are visible. Only supported pictures and videos can be copied.</p>' : ''}
          ${data.items.length ? `<div class="desk-fe-grid">${data.items.map((it, i) => {
            const key = JSON.stringify([st.root, it.path]);
            return `<button class="desk-fe-entry" ${it.directory ? `data-fe-dir="${i}"` : `data-fe-pick="${esc(key)}" data-fe-index="${i}" aria-pressed="${st.selected.has(key)}"`}${!it.directory && !it.selectable ? ' disabled' : ''}>
              <span class="desk-fe-thumb">${it.src ? `<img src="${esc(it.src)}" alt="" loading="lazy">` : it.play ? `<video src="${esc(it.play)}" preload="metadata" muted playsinline></video><span class="desk-fe-play">▶</span>` : `<span>${it.directory ? '📁' : '▤'}</span>`}</span>
              <span class="desk-fe-name">${esc(it.title)}</span>${st.query ? `<small>${esc(it.path.split('/').slice(0, -1).join(' › '))}</small>` : ''}</button>`;
          }).join('')}</div>` : '<p>No files match. Try another folder or search.</p>'}`;
        results.querySelectorAll('[data-fe-dir]').forEach((b) => b.onclick = () => navigate(st.root, data.items[Number(b.dataset.feDir)].path));
        results.querySelectorAll('[data-fe-pick]').forEach((b) => b.onclick = () => {
          if (st.busy) return;
          const key = b.dataset.fePick, item = st.rows[Number(b.dataset.feIndex)];
          if (st.selected.has(key)) st.selected.delete(key);
          else if (st.selected.size < 50) st.selected.set(key, { root: st.root, path: item.path });
          else window.DeskV1Kit.toast('Choose up to 50 files at a time.');
          paintSelection();
        });
      } catch (e) {
        if (e.name === 'AbortError') return;
        if (active !== st || seq !== st.seq) return;
        results.innerHTML = `<p role="alert">${esc(e.message || 'Could not read this folder.')}</p><button data-fe-retry>Try again</button>`;
        results.querySelector('[data-fe-retry]').onclick = load;
      } finally {
        if (st.request && st.request.id === id) st.request = null;
      }
    };
    function navigate(root, path) {
      if (st.busy) return;
      st.root = root; st.path = path; st.query = '';
      el.querySelector('[data-fe-search]').value = '';
      load();
    }
    el.querySelector('[data-fe-search]').oninput = (e) => {
      st.query = e.target.value; stop(st); st.timer = setTimeout(load, 250);
    };
    el.querySelector('[data-fe-filter]').onchange = (e) => { st.filter = e.target.value; load(); };
    el.querySelector('[data-fe-clear]').onclick = () => { st.selected.clear(); paintSelection(); };
    copy.onclick = async () => {
      if (st.busy || !st.selected.size) return;
      st.busy = true; paintSelection();
      try {
        const out = await api('POST', '/api/desk/library/folders/copy', { id: folder.id, files: [...st.selected.values()] });
        st.folder.items.push(...out.items); st.changed = st.changed || out.items.length > 0; saved();
        // Clear successes only; failed files remain selected for a retry.
        const failed = new Set(out.errors.map((e) => JSON.stringify([e.root, e.path])));
        for (const key of st.selected.keys()) if (!failed.has(key)) st.selected.delete(key);
        const status = document.createElement('p'); status.setAttribute('role', 'status');
        status.textContent = `${out.items.length} file${out.items.length === 1 ? '' : 's'} copied. ${out.errors.map((e) => `${e.title}: ${e.error}`).join(' ')}`;
        results.prepend(status);
      } catch (e) { window.DeskV1Kit.toast(e.message || 'Could not copy these files.'); }
      finally { st.busy = false; paintSelection(); }
    };
    el.querySelector('[data-fe-close]').onclick = close;
    el.querySelector('[data-studio-lib-back]').onclick = close;
    st.keys = (e) => {
      if (document.querySelector('.image-viewer-overlay:not([hidden]), .desk-v1-lv-overlay')) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
      if (e.key !== 'Tab') return;
      const nodes = [...el.querySelectorAll('button:not(:disabled), input, select, summary')].filter((n) => n.offsetParent !== null);
      const first = nodes[0], last = nodes[nodes.length - 1];
      if (!el.contains(document.activeElement) || (!e.shiftKey && document.activeElement === last)) { e.preventDefault(); first.focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    };
    document.body.appendChild(el); document.addEventListener('keydown', st.keys, true);
    el.querySelector('[data-fe-close]').focus(); saved(); load();
  }
  window.DeskV1FileExplorer = { open, close };
})();
