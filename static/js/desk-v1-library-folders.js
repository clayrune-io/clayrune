// User-made mixed-media folders. Physical library paths never appear as labels.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const api = (...args) => window.DeskV1Store.api(...args);

  function mount(host, { onChanged } = {}) {
    if (!host || host.dataset.mounted) return;
    host.dataset.mounted = 'true';
    let folders = [];
    const refresh = async () => {
      host.innerHTML = '<p>Loading your folders…</p>';
      try {
        const out = await api('GET', '/api/desk/library/folders'); folders = out.folders;
        if (!host.isConnected) return;
        paint();
      } catch (e) {
        host.innerHTML = `<p role="alert">${esc(e.message || 'Could not load your folders.')}</p><button data-lf-retry>Try again</button>`;
        host.querySelector('[data-lf-retry]').onclick = refresh;
      }
    };
    const changed = () => { if (onChanged) onChanged(); else refresh(); };
    const editor = (folder) => {
      const panel = host.querySelector('[data-lf-editor]');
      panel.innerHTML = `<form class="desk-lf-form"><label>${folder ? 'Rename folder' : 'New folder'}<input data-lf-name maxlength="80" required value="${esc(folder ? folder.title : '')}" placeholder="e.g. Product photos"></label><button class="btn-add" type="submit">${folder ? 'Save name' : 'Create folder'}</button><button type="button" data-lf-cancel>Cancel</button><span role="alert" data-lf-error></span></form>`;
      panel.querySelector('[data-lf-cancel]').onclick = () => { panel.innerHTML = ''; };
      panel.querySelector('form').onsubmit = async (e) => {
        e.preventDefault();
        const submit = panel.querySelector('[type=submit]'); submit.disabled = true;
        try {
          const out = await api(folder ? 'PATCH' : 'POST', '/api/desk/library/folders', { ...(folder ? { id: folder.id } : {}), title: panel.querySelector('input').value });
          await refresh();
          if (!folder) window.DeskV1FileExplorer.open(out, { onChanged: changed });
          else changed();
        } catch (err) { if (panel.isConnected) { panel.querySelector('[data-lf-error]').textContent = err.message; submit.disabled = false; } }
      };
      panel.querySelector('input').focus();
    };
    const paint = () => {
      host.innerHTML = `<div class="desk-lf-toolbar"><p>Keep pictures and videos together in folders you name.</p><button data-lf-new>＋ New folder</button></div><div data-lf-editor></div>
        <div class="desk-lf-grid">${folders.map((f, i) => `<article class="desk-lf-card"><button class="desk-v1-studio-folder" data-studio-folder="${esc(f.id)}" data-lf-open="${i}"><span class="desk-lf-cover">${f.items.find((it) => it.kind === 'image') ? `<img src="${esc(f.items.find((it) => it.kind === 'image').src)}" alt="" loading="lazy">` : '📁'}</span><span class="desk-v1-studio-folder-name">${esc(f.title)}</span><span class="desk-v1-studio-folder-count">${f.files} files</span></button><div class="desk-lf-actions"><button data-lf-rename="${i}" aria-label="Rename ${esc(f.title)}">Rename</button><button data-lf-delete="${i}" aria-label="Delete ${esc(f.title)}">Delete</button></div></article>`).join('') || '<p>No folders yet. Create one to start collecting files.</p>'}</div>`;
      host.querySelector('[data-lf-new]').onclick = () => editor(null);
      host.querySelectorAll('[data-lf-open]').forEach((b) => b.onclick = () => window.DeskV1FileExplorer.open(folders[Number(b.dataset.lfOpen)], { onChanged: changed }));
      host.querySelectorAll('[data-lf-rename]').forEach((b) => b.onclick = () => editor(folders[Number(b.dataset.lfRename)]));
      host.querySelectorAll('[data-lf-delete]').forEach((b) => b.onclick = () => {
        const folder = folders[Number(b.dataset.lfDelete)], panel = host.querySelector('[data-lf-editor]');
        panel.innerHTML = `<div class="desk-lf-confirm"><p>Delete “${esc(folder.title)}” and its ${folder.files} library files? Files used by pieces or drafts are protected. Originals on your computer stay where they are.</p><button data-lf-confirm>Delete folder</button><button data-lf-cancel>Cancel</button><span role="alert" data-lf-error></span></div>`;
        panel.querySelector('[data-lf-cancel]').onclick = () => { panel.innerHTML = ''; };
        panel.querySelector('[data-lf-confirm]').onclick = async (e) => {
          e.target.disabled = true;
          try { await api('DELETE', '/api/desk/library/folders', { id: folder.id }); await refresh(); changed(); }
          catch (err) { panel.querySelector('[data-lf-error]').textContent = err.message; e.target.disabled = false; }
        };
        panel.querySelector('[data-lf-cancel]').focus();
      });
    };
    refresh();
  }
  window.DeskV1LibraryFolders = { mount };
})();
