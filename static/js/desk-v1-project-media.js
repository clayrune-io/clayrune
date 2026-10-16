// Project files share one browser in Studio, the library picker and What.
// Only use() crosses the copy boundary; consumers always receive a library path.
(function () {
  const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const api = (method, path, body) => window.DeskV1Store.api(method, path, body);
  const html = () => '<section class="desk-v1-project-media" data-project-media></section>';

  async function use(item) {
    if (!item.project_id) return item;
    const copy = await api('POST', '/api/desk/project-media/copy', { id: item.id });
    if (typeof window.deskV1WhatInvalidateMaterials === 'function') window.deskV1WhatInvalidateMaterials();
    return copy;
  }

  function mount(host, opts) {
    if (!host || !window.DeskV1Store.live()) return;
    opts = opts || {};
    let projects = opts.projects || [];
    const view = opts.state || {};
    let query = view.query || '', selected = view.selected || '', busy = false;
    const kinds = opts.kinds || ['image', 'video'];
    host.innerHTML = `<h3 class="desk-v1-studio-sub">From your projects</h3>
      <div class="desk-v1-pm-controls">
        <select class="desk-v1-sb-edit-input" data-pm-project aria-label="Filter by project"></select>
        <input type="search" class="desk-v1-sb-edit-input" data-pm-search placeholder="Search project files" aria-label="Search project files">
        <button type="button" class="btn-secondary" data-pm-refresh>Refresh files</button>
      </div><p class="desk-v1-pm-status" data-pm-status role="status" aria-live="polite"></p><div data-pm-results></div>`;
    const filter = host.querySelector('[data-pm-project]');
    const results = host.querySelector('[data-pm-results]');
    const status = host.querySelector('[data-pm-status]');
    const refresh = host.querySelector('[data-pm-refresh]');
    const setProjects = (value) => {
      projects = value || [];
      filter.innerHTML = '<option value="">All projects</option>' + projects.map((p) => `<option value="${esc(p.project_id)}">${esc(p.title)}</option>`).join('');
      filter.value = selected;
      paint();
    };
    const tile = (item, index) => `<div class="desk-v1-lp-tile">
      <button type="button" class="desk-v1-lp-pick" data-pm-use="${index}" aria-label="${opts.onPick ? 'Use' : 'Save to library:'} ${esc(item.title)}">
        <span class="desk-v1-lp-thumb">${item.kind === 'image' ? `<img src="${esc(item.src)}" alt="" loading="lazy">` : `<video src="${esc(item.play)}#t=0.1" preload="metadata" muted playsinline aria-hidden="true"></video>`}</span>
        <span class="desk-v1-lp-name" title="${esc(item.title)}">${esc(item.title)}</span>
        <span class="desk-v1-lp-folder">${esc(item.agent_name || item.kind)}</span>
      </button><button type="button" class="desk-v1-lp-view" data-pm-view="${index}" aria-label="View ${esc(item.title)}">⤢</button></div>`;
    let shown = [];
    function paint() {
      shown = [];
      results.innerHTML = projects.filter((p) => !selected || p.project_id === selected).map((p) => {
        const items = (p.items || []).filter((i) => kinds.includes(i.kind) && (!query || `${p.title} ${i.title} ${i.folder} ${i.agent_name || ''}`.toLowerCase().includes(query)));
        if (!items.length) return '';
        const folders = [...new Set(items.map((i) => i.folder))];
        return `<details class="desk-v1-pm-project" data-pm-pid="${esc(p.project_id)}" ${selected || query ? 'open' : ''}><summary>${esc(p.title)} · ${items.length} ${items.length === 1 ? 'file' : 'files'}</summary>
          <div data-pm-project-files>${folders.map((folder) => `<details class="desk-v1-pm-folder" ${query ? 'open' : ''}><summary>${esc(folder === '.' ? 'Project folder' : folder)}</summary><div class="desk-v1-lp-grid" data-pm-folder-files></div></details>`).join('')}</div></details>`;
      }).join('') || '<div class="desk-v1-camp-empty">No project files match. Refresh files to find recent work.</div>';
      // Mount thumbnails only after opening a folder; a library may contain thousands of files.
      results.querySelectorAll('.desk-v1-pm-project').forEach((projectEl) => {
        const project = projects.find((p) => p.project_id === projectEl.dataset.pmPid);
        const candidates = (project.items || []).filter((i) => kinds.includes(i.kind) && (!query || `${project.title} ${i.title} ${i.folder} ${i.agent_name || ''}`.toLowerCase().includes(query)));
        const folders = [...new Set(candidates.map((i) => i.folder))];
        projectEl.querySelectorAll('.desk-v1-pm-folder').forEach((folderEl, n) => {
          const fill = () => {
            const grid = folderEl.querySelector('[data-pm-folder-files]');
            if (!folderEl.open || grid.childElementCount) return;
            grid.innerHTML = candidates.filter((i) => i.folder === folders[n]).map((i) => { const index = shown.push(i) - 1; return tile(i, index); }).join('');
          };
          folderEl.ontoggle = fill;
          fill();
        });
      });
    }
    results.onclick = async (event) => {
      const view = event.target.closest('[data-pm-view]');
      if (view) { if (window.DeskV1LibraryViewer) window.DeskV1LibraryViewer.open(shown, Number(view.dataset.pmView)); return; }
      const button = event.target.closest('[data-pm-use]');
      if (!button || busy) return;
      busy = true; button.disabled = true; status.textContent = 'Saving a copy to the library…';
      try {
        const copied = await use(shown[Number(button.dataset.pmUse)]);
        status.textContent = 'Saved to the library.';
        if (opts.onPick) opts.onPick(copied);
      } catch (e) { status.textContent = `Could not use that file: ${e.message || e}`; }
      finally { busy = false; button.disabled = false; }
    };
    filter.onchange = () => { view.selected = selected = filter.value; paint(); };
    host.querySelector('[data-pm-search]').value = query;
    host.querySelector('[data-pm-search]').oninput = (e) => { view.query = query = e.target.value.trim().toLowerCase(); paint(); };
    refresh.onclick = async () => {
      refresh.disabled = true; status.textContent = 'Finding project files…';
      try {
        await api('POST', '/api/desk/project-media/refresh', {});
        for (let i = 0; i < 60 && host.isConnected; i++) {
          await new Promise((resolve) => setTimeout(resolve, 1000));
          const state = await api('GET', '/api/desk/project-media');
          if (!state.refreshing && state.error) throw new Error(state.error);
          if (!state.refreshing) { setProjects(state.projects); status.textContent = 'Project files refreshed.'; return; }
        }
        status.textContent = 'Still finding files. Open the library again in a moment.';
      } catch (e) { status.textContent = `Could not refresh files: ${e.message || e}`; }
      finally { refresh.disabled = false; }
    };
    setProjects(projects);
  }
  window.DeskV1ProjectMedia = { html, mount, use };
})();
