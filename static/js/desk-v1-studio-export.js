// Local MP4 export, independent of the paid render panel and preview clock.
(function () {
  const mounted = new WeakMap();
  function html() {
    return `<button type="button" class="desk-v1-sb-editbtn" data-export-start>Export</button>
      <div class="desk-v1-sb-export" data-sb-export>
        <progress data-export-progress max="100" value="0" hidden aria-label="MP4 export progress"></progress>
        <span data-export-status role="status"></span>
        <a data-export-download hidden download>Download MP4</a>
        <a data-export-open hidden target="_blank" rel="noopener">Open MP4</a>
      </div>`;
  }
  async function json(url, options = {}) {
    const response = await fetch(url, options);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `Export request failed (${response.status})`);
    return data;
  }
  function mount(root, api) {
    mounted.get(root)?.destroy();
    const button = root.querySelector('[data-export-start]');
    if (!button) return;
    const find = name => root.querySelector(`[data-export-${name}]`);
    let timer = 0, destroyed = false, busy = false, serial = 0;
    const owner = api.owner();
    const query = new URLSearchParams({ kind: owner.kind, id: owner.id });
    const live = window.DeskV1Store.live();
    function refresh() {
      button.disabled = !live || busy || !api.scenes().some(s => !s.placeholder);
      button.title = live ? 'Export this timeline as a local MP4' : 'Export is available in the live Desk';
    }
    function show(job) {
      if (destroyed) return;
      busy = ['queued', 'running'].includes(job.status);
      refresh();
      find('status').textContent = job.message || '';
      find('progress').hidden = !busy;
      find('progress').value = job.progress || 0;
      for (const name of ['download', 'open']) {
        const link = find(name);
        link.hidden = job.status !== 'done';
        if (job.status === 'done') link.href = job.download + (name === 'open' ? '?open=1' : '');
        else link.removeAttribute('href');
      }
      if (busy) timer = setTimeout(() => poll(job.id), 800);
    }
    async function poll(id) {
      if (destroyed) return;
      try { show(await json(`/api/desk/exports/${encodeURIComponent(id)}`)); }
      catch (error) { show({ status: 'failed', message: `${error.message} Click Export to retry.` }); }
    }
    button.onclick = async () => {
      if (busy || (api.beforeExport && api.beforeExport() === false)) return;
      const mine = ++serial;
      clearTimeout(timer);
      busy = true; refresh();
      find('status').textContent = 'Starting local export…';
      find('download').hidden = find('open').hidden = true;
      // Exactly Preview's snapshot: current order, right-edge trim, no examples.
      const scenes = api.scenes().filter(s => !s.placeholder).map(s => ({
        id: s.id, label: s.label || 'Scene', line: s.line || '',
        duration_sec: Number.isFinite(Number(s.durationSec)) && Number(s.durationSec) > 0 ? Number(s.durationSec) : 3,
        picture: s.picture ? { path: s.picture.path, kind: s.picture.kind || 'image', title: s.picture.title } : null,
      }));
      try {
        const job = await json('/api/desk/exports', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ owner, scenes }) });
        if (!destroyed && mine === serial) show(job);
      } catch (error) { if (!destroyed && mine === serial) show({ status: 'failed', message: `${error.message} Click Export to retry.` }); }
    };
    function destroy() { destroyed = true; clearTimeout(timer); observer.disconnect(); mounted.delete(root); }
    const observer = new MutationObserver(() => { if (!button.isConnected) destroy(); });
    observer.observe(document.body, { childList: true, subtree: true });
    mounted.set(root, { refresh, destroy });
    refresh();
    const initial = serial;
    // Reopen resumes polling and recovers the download link without a second job.
    if (live) json(`/api/desk/exports?${query}`).then(({ job }) => { if (job && initial === serial) show(job); })
      .catch(error => {
        if (!destroyed && initial === serial) find('status').textContent = `Could not restore export status: ${error.message} Click Export to start a new job.`;
      });
  }
  window.DeskV1StudioExport = { html, mount, refresh: root => mounted.get(root)?.refresh() };
})();
