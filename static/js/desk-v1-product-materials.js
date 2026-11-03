// Project documents used by every toolless Desk planner/writer call.
(function () {
  const esc = (s) => String(s || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const files = (p) => (p && p.presence && p.presence.product_materials) || [];
  function readsHTML(project) {
    const paths = files(project);
    return `<div class="desk-v1-product-reads" data-product-reads data-product-project="${esc(project && project.id)}">${esc(paths.length ? 'Reads: ' + paths.join(', ') : 'No product materials: the agent only knows what you tell it')}</div>`;
  }
  function refresh(project) {
    document.querySelectorAll('[data-product-reads]').forEach((line) => {
      if (line.dataset.productProject !== project.id) return;
      const paths = files(project);
      line.textContent = paths.length ? 'Reads: ' + paths.join(', ') : 'No product materials: the agent only knows what you tell it';
    });
  }
  function mount(host, project) {
    if (!host || !project || !window.DeskV1Store.live()) return;
    const pres = project.presence || {};
    const paths = pres.product_docs == null ? files(project) : pres.product_docs;
    host.innerHTML = `<details class="desk-v1-product-materials">
      <summary>Product materials</summary>
      <p>Files from ${esc(project.name)}'s folder, shared with its planners and writers.</p>
      <ul>${paths.map((path, i) => `<li><span>${esc(path)}</span><button type="button" data-product-remove="${i}" aria-label="Remove ${esc(path)}">Remove</button></li>`).join('')}</ul>
      <form data-product-add><label>Path in project folder<input type="text" data-product-path placeholder="docs/LAUNCH_COPY.md" required></label><button type="submit">Add</button></form>
      <button type="button" data-product-default>Use README default</button>
      <p class="desk-v1-product-note" data-product-error role="status">${esc(pres.product_materials_note)}</p>
    </details>`;
    const details = host.querySelector('details');
    const error = host.querySelector('[data-product-error]');
    async function save(next) {
      host.querySelectorAll('button').forEach((b) => { b.disabled = true; });
      try {
        const result = await window.DeskV1Store.api('PATCH', `/api/desk/presence/${encodeURIComponent(project.id)}`, { product_docs: next });
        project.presence = Object.assign({}, project.presence, result);
        refresh(project);
        mount(host, project);
        host.querySelector('details').open = true;
      } catch (e) {
        error.textContent = e.message || String(e);
        host.querySelectorAll('button').forEach((b) => { b.disabled = false; });
      }
    }
    host.querySelector('[data-product-add]').onsubmit = (e) => {
      e.preventDefault();
      const path = host.querySelector('[data-product-path]').value.trim();
      if (path) save([...paths, path]);
    };
    host.querySelectorAll('[data-product-remove]').forEach((b) => {
      b.onclick = () => save(paths.filter((_, i) => i !== Number(b.dataset.productRemove)));
    });
    host.querySelector('[data-product-default]').onclick = () => save(null);
    details.open = false;
  }
  window.DeskV1ProductMaterials = { mount, readsHTML };
})();
