// C2 owns Post media ingestion and its single picker. Piece writes share the
// shell's queue; uploads use M21, whose saved files already live in the library.
(function () {
  const store = () => window.DeskV1Store;
  const esc = (s) => window.esc(String(s ?? ''));
  const uid = () => 'asset-' + Date.now().toString(36) + Math.random().toString(36).slice(2);
  const url = (d) => '/api/desk/pieces/' + encodeURIComponent(d.id) + '/assets';
  const chains = new Map();
  const renders = new Map();

  function mount(el, camp, d, hooks) {
    const root = el.querySelector('[data-composer]');
    const host = document.createElement('div');
    host.className = 'desk-v1-composer-media';
    host.innerHTML = `<div class="desk-v1-cm-heading"><h2>Media</h2><button type="button" class="btn-secondary" data-composer-add-media>Add media</button></div>
      <p class="desk-v1-composer-hint">Drop or paste pictures and videos anywhere on this post.</p>
      <div class="desk-v1-cm-assets" data-composer-media-assets></div><p role="status" aria-live="polite" data-composer-media-status></p>
      <dialog class="desk-v1-cm-picker" data-composer-media-picker aria-labelledby="desk-v1-cm-title">
        <div class="desk-v1-cm-heading"><h2 id="desk-v1-cm-title">Add media</h2><button type="button" class="btn-secondary" data-cm-close aria-label="Close media picker">Close</button></div>
        <div class="desk-v1-cm-tabs" role="tablist" aria-label="Media source"><button type="button" role="tab" id="cm-upload-tab" aria-controls="cm-upload-panel" aria-selected="true" data-cm-tab="upload">Upload</button><button type="button" role="tab" id="cm-library-tab" aria-controls="cm-library-panel" aria-selected="false" tabindex="-1" data-cm-tab="library">Library</button></div>
        <div role="tabpanel" id="cm-upload-panel" aria-labelledby="cm-upload-tab" data-cm-panel="upload"><p>Choose pictures or videos from this device, or drop or paste them here.</p><label class="desk-v1-cm-file">Choose files<input type="file" multiple data-cm-files aria-label="Upload pictures or videos" accept="image/png,image/jpeg,image/gif,image/webp,${esc(window.DeskV1MediaFormats.videoAccept)}"></label><p class="desk-v1-composer-hint">Images up to 25 MB. Videos up to 500 MB; playback depends on this browser.</p></div>
        <div role="tabpanel" id="cm-library-panel" aria-labelledby="cm-library-tab" data-cm-panel="library" hidden><label>Search the library<input type="search" data-cm-search></label><div class="desk-v1-cm-library" data-cm-library></div></div>
      </dialog>`;
    root.querySelector('.desk-v1-composer-sheet').appendChild(host);
    const add = host.querySelector('[data-composer-add-media]');
    const dialog = host.querySelector('dialog');
    const status = host.querySelector('[data-composer-media-status]');
    const library = host.querySelector('[data-cm-library]');
    const search = host.querySelector('[data-cm-search]');
    let items = [], read = 0;
    const editable = () => !d.locked && !d.fam?.archivedAt && !(d.fam?.versions || []).some((v) => ['approved', 'scheduled'].includes(v.state) || window.DeskV1ComposerChannels.sent(v));
    const message = (s, failed = false) => { d.mediaStatus = s; d.mediaFailed = failed; paint(); };
    const localPaint = () => {
      status.textContent = d.mediaStatus || ''; status.toggleAttribute('data-failed', !!d.mediaFailed);
      const strip = host.querySelector('[data-composer-media-assets]');
      strip.innerHTML = d.fam ? window.DeskV1PieceMedia.thumbsHTML(d.fam) : '';
      if (d.fam) window.DeskV1PieceMedia.wire(strip, d.fam, () => { paint(); hooks.update(); });
      if (!editable() || d.mediaBusy) strip.querySelectorAll('[data-pmed-remove]').forEach((b) => b.remove());
      add.disabled = !editable() || !!d.mediaBusy;
      host.querySelector('[data-cm-files]').disabled = !editable() || !!d.mediaBusy;
      hooks.update();
    };
    const paint = () => (renders.get(d.id) || localPaint)();
    renders.set(d.id, localPaint);
    const close = () => { dialog.close(); add.focus({ preventScroll: true }); };
    const paintLibrary = () => {
      const q = search.value.toLowerCase().trim();
      const shown = items.filter((it) => `${it.title} ${it.folder}`.toLowerCase().includes(q));
      library.innerHTML = shown.length ? shown.map((it) => `<button type="button" class="desk-v1-cm-item" data-cm-pick="${esc(it.path || it.id)}" ${d.mediaBusy || !editable() ? 'disabled' : ''}>
        ${it.kind === 'video' && it.play ? `<video src="${esc(it.play)}#t=0.1" muted playsinline preload="metadata" aria-hidden="true"></video>` : it.src ? `<img src="${esc(it.src)}" alt="">` : '<span aria-hidden="true">▧</span>'}
        <span>${it.kind === 'video' ? '▶ ' : ''}${esc(it.title)}</span><small>${esc(it.folder || '')}</small></button>`).join('') : '<p>No media found in the library.</p>';
      library.querySelectorAll('[data-cm-pick]').forEach((b) => {
        b.onclick = () => { const it = items.find((i) => (i.path || i.id) === b.dataset.cmPick); if (it) { close(); enqueue([{ item: it }]); } };
      });
    };
    async function loadLibrary() {
      const mine = ++read;
      library.textContent = 'Loading the library…';
      try {
        // Read fresh on every visit: uploads anywhere in the product must appear.
        items = (await window.DeskV1LibraryPicker.files(['image', 'video'])).sort((a, b) => (b.mtime || 0) - (a.mtime || 0));
        if (mine === read && host.isConnected) paintLibrary();
      } catch (e) {
        if (mine !== read || !host.isConnected) return;
        library.textContent = `Could not load the library: ${e.message || e}`;
        const retry = document.createElement('button'); retry.type = 'button'; retry.textContent = 'Retry'; retry.onclick = loadLibrary; library.appendChild(retry);
      }
    }
    function tab(name) {
      host.querySelectorAll('[data-cm-tab]').forEach((b) => { const on = b.dataset.cmTab === name; b.setAttribute('aria-selected', String(on)); b.tabIndex = on ? 0 : -1; });
      host.querySelectorAll('[data-cm-panel]').forEach((p) => { p.hidden = p.dataset.cmPanel !== name; });
      if (name === 'library') loadLibrary();
    }
    host.querySelectorAll('[data-cm-tab]').forEach((b) => {
      b.onclick = () => tab(b.dataset.cmTab);
      b.onkeydown = (e) => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
        e.preventDefault(); const name = e.key === 'Home' ? 'upload' : e.key === 'End' ? 'library' : b.dataset.cmTab === 'upload' ? 'library' : 'upload';
        tab(name); host.querySelector(`[data-cm-tab="${name}"]`).focus();
      };
    });
    add.onclick = () => { tab('upload'); dialog.showModal(); };
    host.querySelector('[data-cm-close]').onclick = close;
    dialog.oncancel = (e) => { e.preventDefault(); close(); };
    // The dashboard's global Esc closes the whole Desk unless the dialog owns it.
    dialog.addEventListener('keydown', (e) => {
      e.stopPropagation();
      if (e.key === 'Escape') { e.preventDefault(); close(); return; }
      if (e.key !== 'Tab') return;
      const all = [...dialog.querySelectorAll('button:not([disabled]), input:not([disabled])')].filter(n => n.getClientRects().length);
      const first = all[0], last = all[all.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    });
    search.oninput = paintLibrary;

    async function attach(entry) {
      if (!editable()) throw new Error('Return this post to review before changing its media.');
      const file = entry.file, it = entry.item;
      // Wait for text's first-content create before asking the shell to save.
      await window.deskV1AfterPieceSaved(d.id);
      await hooks.save();
      if (!d.fam || d.failed) throw new Error(d.status || 'Could not create the post draft.');
      const id = uid(), kind = file ? (window.DeskV1MediaFormats.isVideo(file) ? 'video' : 'image') : it.kind;
      const blob = file ? URL.createObjectURL(file) : null;
      const asset = { id, kind, title: file ? file.name : it.title, path: it?.path, src: kind === 'image' ? blob || it.src : it?.src, play: kind === 'video' ? blob || it.play : null };
      const fam = d.fam;
      let kept = false;
      try {
        const result = await store().write({
          label: `Added ${asset.title} to this post`,
          apply: () => { fam.assets = (fam.assets || []).concat(asset); paint(); },
          unapply: () => { fam.assets = (fam.assets || []).filter((a) => a.id !== id); },
          repaint: paint,
          request: () => window.deskV1QueuePiece(d.id, async () => {
            if (!editable()) throw new Error('Return this post to review before changing its media.');
            let body;
            if (file) { body = new FormData(); body.append('file', file, file.name); body.append('id', id); body.append('title', file.name); }
            else body = { id, path: it.path, title: it.title };
            const out = await store().api('POST', url(d), body);
            const saved = out?.assets?.find((a) => a.id === id);
            if (!saved?.path) throw new Error('The upload reply did not include the saved media. Reload the post to check its attachments.');
            Object.assign(asset, saved);
            window.deskV1WhatInvalidateMaterials?.();
            paint(); return out;
          }),
          undoRequest: () => window.deskV1QueuePiece(d.id, () => store().api('DELETE', url(d) + '/' + encodeURIComponent(id))),
        });
        if (!result.ok) throw new Error(result.error);
        kept = true;
        if (file && !store().live()) {
          const lib = store().state().materialLibrary ||= {};
          const folders = lib[kind] ||= [];
          let folder = folders.find(f => f.title === 'Uploads');
          if (!folder) { folder = { id: kind + ':Uploads', title: 'Uploads', items: [] }; folders.push(folder); }
          folder.items.push({ ...asset, mtime: Date.now() / 1000 });
        }
      } finally {
        if (blob && (store().live() || !kept)) URL.revokeObjectURL(blob);
      }
    }
    function enqueue(entries) {
      if (!entries.length) return;
      if (!editable()) { message('Return this post to review before changing its media.', true); return; }
      d.mediaPending = (d.mediaPending || 0) + entries.length;
      d.mediaBusy = true; paint(); message('Adding media…');
      const task = (chains.get(d.id) || Promise.resolve()).then(async () => {
        let failed = false;
        for (const entry of entries) {
          try { await attach(entry); }
          catch (e) { failed = true; message(`Could not add ${entry.file?.name || entry.item?.title || 'media'}: ${e.message || e}`, true); }
          finally { d.mediaPending--; }
        }
        if (!failed) message(store().live() ? 'Media saved in the library and attached.' : 'Media added in demo only.');
      }).finally(() => { d.mediaBusy = d.mediaPending > 0; paint(); });
      chains.set(d.id, task);
      task.finally(() => { if (chains.get(d.id) === task) chains.delete(d.id); });
    }
    function files(list) {
      const entries = [], errors = [];
      for (const file of list) {
        const kind = window.DeskV1MediaFormats.isVideo(file) ? 'video' : 'image';
        const error = window.DeskV1MediaFormats.error(file, kind);
        if (error) errors.push(`${file.name}: ${error}`); else entries.push({ file });
      }
      enqueue(entries);
      if (errors.length) { message(errors.join(' '), true); window.DeskV1Kit.toast(errors.join(' ')); }
    }
    host.querySelector('[data-cm-files]').onchange = (e) => { const list = [...e.target.files]; e.target.value = ''; if (list.length) { close(); files(list); } };
    root.addEventListener('paste', (e) => {
      const list = [...(e.clipboardData?.files || [])];
      if (!list.length) return; // Text paste remains native, including inside Write.
      e.preventDefault(); e.stopPropagation(); files(list);
    });
    root.addEventListener('dragover', (e) => {
      if (!Array.from(e.dataTransfer?.types || []).includes('Files')) return;
      e.preventDefault(); e.stopPropagation(); root.classList.add('desk-v1-cm-dragging');
    });
    root.addEventListener('dragleave', (e) => { if (!root.contains(e.relatedTarget)) root.classList.remove('desk-v1-cm-dragging'); });
    root.addEventListener('drop', (e) => {
      const list = [...(e.dataTransfer?.files || [])];
      if (!list.length) return;
      e.preventDefault(); e.stopPropagation(); root.classList.remove('desk-v1-cm-dragging'); files(list);
    });
    if (d.mediaStatus) message(d.mediaStatus, d.mediaFailed);
    paint();
  }
  window.DeskV1ComposerMedia = { mount };
})();
