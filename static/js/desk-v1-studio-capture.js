// Real screenshot controls; Studio and campaign What use the same body.
(() => {
  const esc = (v) => window.esc(String(v ?? ''));
  const api = (method, path, body) => window.DeskV1Store.api(method, path, body);

  function bodyHTML(ctx) {
    return `<div class="desk-v1-capture" data-product-capture data-project="${esc(ctx.camp?.projectId || '')}">
      <p class="desk-v1-cap-note" data-capture-status role="status">Opening the product’s pages…</p>
      <div data-capture-controls></div>
      <div class="desk-v1-cap-preview" data-capture-preview hidden></div>
    </div>`;
  }

  async function wire(host, ctx, bridge) {
    const root = host.querySelector('[data-product-capture]');
    if (!root) return false;
    const pid = ctx.camp?.projectId;
    const status = root.querySelector('[data-capture-status]');
    const controls = root.querySelector('[data-capture-controls]');
    const alive = () => root.isConnected;
    const endpoint = '/api/desk/capture/projects/' + encodeURIComponent(pid || '');
    let info;
    const fail = (e) => { if (alive()) { status.textContent = e?.message || String(e); status.setAttribute('role', 'alert'); } };
    function paint() {
      if (!alive()) return;
      status.setAttribute('role', 'status');
      status.textContent = info.app_address ? 'A real screenshot is saved in Material library › Studio.' : 'What is the address where you open this app?';
      controls.innerHTML = `${info.clayrune ? '' : `<label class="desk-v1-capture-field">App address
        <input data-capture-address type="url" value="${esc(info.app_address)}" placeholder="http://localhost:3000" autocomplete="off"></label>
        <button class="btn-secondary" type="button" data-capture-save>Save app address</button>`}
        ${info.app_address ? `<label class="desk-v1-capture-field">Page
        ${info.clayrune ? `<select data-capture-page>${info.pages.map(p => `<option value="${esc(p.id)}">${esc(p.label)}</option>`).join('')}</select>`
          : '<input data-capture-page value="/" placeholder="/pricing" autocomplete="off">'}</label>
        <button class="btn-add" type="button" data-capture-take>Capture this screen</button>` : ''}`;
      const save = controls.querySelector('[data-capture-save]');
      if (save) save.onclick = async () => {
        save.disabled = true;
        try {
          info = await api('PUT', endpoint, { app_address: controls.querySelector('[data-capture-address]').value });
          paint();
        } catch (e) { fail(e); } finally { if (alive()) save.disabled = false; }
      };
      const take = controls.querySelector('[data-capture-take]');
      if (take) take.onclick = async () => {
        // A changed address must be saved so its exact provenance is durable.
        const addr = controls.querySelector('[data-capture-address]');
        if (addr && addr.value.trim() !== info.app_address) { fail(new Error('Save the changed app address first.')); return; }
        take.disabled = true;
        if (save) save.disabled = true;
        status.textContent = 'Capturing the page…'; status.setAttribute('role', 'status');
        try {
          const result = await api('POST', '/api/desk/capture', { project_id: pid, page: controls.querySelector('[data-capture-page]').value });
          window.deskV1WhatInvalidateMaterials?.();
          // The capture stays saved even if the user changes product/source
          // while it runs; never attach it to the newly selected product.
          if (!alive()) return;
          const preview = root.querySelector('[data-capture-preview]');
          preview.hidden = false;
          preview.innerHTML = `<img src="${esc(result.item.src)}" alt="${esc(result.item.title)}">`;
          status.textContent = 'Saved in Material library › Studio.';
          const assetId = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => b.toString(16).padStart(2, '0')).join('');
          bridge.attach({ ...result.item, id: 'asset-' + assetId });
        } catch (e) { fail(e); } finally { if (alive()) { take.disabled = false; if (save) save.disabled = false; } }
      };
    }
    if (!pid) { status.textContent = 'Pick a product above to capture from it.'; return true; }
    try { info = await api('GET', endpoint); paint(); }
    catch (e) {
      fail(e);
      if (alive()) { controls.innerHTML = '<button class="btn-secondary" type="button" data-capture-retry>Try again</button>'; controls.querySelector('button').onclick = () => wire(host, ctx, bridge); }
    }
    return true;
  }
  window.DeskV1StudioCapture = { bodyHTML, wire };
})();
