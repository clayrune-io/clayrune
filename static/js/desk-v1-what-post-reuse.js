// Copy an existing post into a NEW piece. No versions, approvals or shared files.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  const url = (fam) => '/api/desk/pieces/' + encodeURIComponent(fam.id);
  const id = () => 'asset-copy-' + (window.crypto?.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2, 12));
  const clone = (o) => JSON.parse(JSON.stringify(o));
  function html() {
    return '<div data-what-post-reuse><p>Copy a post and its media. The original stays unchanged.</p>' +
      '<div data-what-reuse-list>Loading posts…</div><p data-what-reuse-status role="status" aria-live="polite"></p></div>';
  }

  // Read only server-issued, same-origin media URLs; then upload fresh files.
  async function mediaFile(asset) {
    const path = asset.play || asset.src;
    if (!path || !asset.path) throw new Error(`“${asset.title || 'Media'}” has no saved file.`);
    const u = new URL(path, location.href);
    if (u.origin !== location.origin || !['/api/serve-file', '/api/serve-image'].includes(u.pathname)) {
      throw new Error('The saved media URL is not a local library file.');
    }
    const r = await fetch(u.href);
    if (!r.ok) throw new Error(`Could not copy “${asset.title || 'Media'}” (${r.status}).`);
    const blob = await r.blob();
    return new File([blob], asset.path.replace(/\\/g, '/').split('/').pop(), { type: blob.type });
  }

  async function copy(source, fam) {
    const store = window.DeskV1Store;
    const snapshot = clone(source);
    const before = { body: fam.body || '', assets: clone(fam.assets || []) };
    const assets = snapshot.assets || [];
    const added = [];
    const body = snapshot.body || '';
    let copiedAssets = assets.map((a) => ({ ...clone(a), id: id() }));
    const queue = (fn) => window.deskV1QueuePiece(fam.id, fn);
    return store.write({
      label: `Copied “${snapshot.title}”`,
      apply: () => { fam.body = body; fam.assets = before.assets.concat(clone(copiedAssets)); },
      unapply: () => Object.assign(fam, clone(before)),
      repaint: () => window.deskV1RepaintWhat(),
      request: () => queue(async () => {
        let patched = false;
        try {
          await store.api('PATCH', url(fam), { body });
          patched = true;
          for (let i = 0; i < assets.length; i++) {
            // One file at a time: do not retain an entire video library in RAM.
            const file = await mediaFile(assets[i]);
            const fd = new FormData();
            const assetId = id();
            fd.append('file', file); fd.append('id', assetId); fd.append('title', assets[i].title || file.name);
            const saved = await store.api('POST', url(fam) + '/assets', fd);
            added.push(saved.assets.find((a) => a.id === assetId));
          }
          copiedAssets = clone(added);
          fam.body = body; fam.assets = before.assets.concat(clone(copiedAssets));
        } catch (e) {
          const cleanup = [];
          for (const asset of added) {
            try { await store.api('DELETE', url(fam) + '/assets/' + encodeURIComponent(asset.id)); }
            catch (err) { cleanup.push(err.message || String(err)); }
          }
          if (patched) {
            try { await store.api('PATCH', url(fam), { body: before.body }); }
            catch (err) { cleanup.push(err.message || String(err)); }
          }
          if (cleanup.length) throw new Error(`${e.message || e}; rollback incomplete: ${cleanup.join('; ')}. Reload before retrying.`);
          throw e;
        }
      }),
      undoRequest: () => queue(async () => {
        for (const asset of added) await store.api('DELETE', url(fam) + '/assets/' + encodeURIComponent(asset.id));
        await store.api('PATCH', url(fam), { body: before.body });
      }),
    });
  }

  async function wire(root, { fam, camp }, done) {
    const list = root.querySelector('[data-what-reuse-list]');
    if (!list) return;
    const store = window.DeskV1Store;
    try {
      const all = store.live() ? await store.api('GET', '/api/desk/pieces') : store.state().families;
      if (!list.isConnected) return;
      const posts = all.filter((f) => f.kind === 'post' && f.id !== fam.id && !f.archivedAt);
      list.innerHTML = posts.length ? posts.map((p) => `<button type="button" class="desk-v1-what-existing" data-what-reuse="${esc(p.id)}">
        <span class="desk-v1-what-existing-title">${esc(p.title)}</span>
        <span class="desk-v1-what-existing-meta">${p.campaignId === camp.id ? 'This campaign' : 'Post library'} · ${(p.assets || []).length} media</span>
      </button>`).join('') : '<p>No existing posts yet.</p>';
      list.querySelectorAll('[data-what-reuse]').forEach((b) => b.onclick = async () => {
        const status = root.querySelector('[data-what-reuse-status]');
        window.DeskV1WhatAddModal.busy(true);
        status.textContent = 'Copying post and media…';
        try {
          const r = await copy(posts.find((p) => p.id === b.dataset.whatReuse), fam);
          if (!r.ok) throw new Error(r.error);
          done();
        } catch (e) {
          const current = document.querySelector('[data-what-reuse-status]') || status;
          current.textContent = `Copy failed: ${e.message || e}`;
        } finally { window.DeskV1WhatAddModal.busy(false); }
      });
    } catch (e) {
      list.textContent = `Could not load posts: ${e.message || e}`;
    }
  }
  window.DeskV1WhatPostReuse = { html, wire };
})();
