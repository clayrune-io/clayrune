// Missing Sources come from Connect's registry, never a second vendor list.
// These tiles only open Connect: they carry no account or drag payload.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  let services = null;
  let loading = null;
  let error = '';

  function html(channels) {
    if (services === null) return `<div role="status">${error ? esc(error) + ' <button type="button" class="desk-v1-where-connect" data-where-services-retry>Retry</button>' : 'Loading services…'}</div>`;
    const present = new Set(channels.map((c) => window.DeskV1ServiceIcons.vendor(c) || c.platform));
    return services.filter((s) => !present.has(window.DeskV1ServiceIcons.vendor(s) || s.id)).map((s) => `
      <div class="desk-v1-where-source desk-v1-where-source-off" data-where-service="${esc(s.id)}" data-platform="${esc(s.id)}">
        <div class="desk-v1-where-tile-head">${window.DeskV1ServiceIcons.markHTML(s, false)}<div class="desk-v1-where-tile-id"><div class="desk-v1-where-source-handle" title="${esc(s.label)}">${esc(s.label)}</div></div></div>
        <div class="desk-v1-where-tile-status"><span class="desk-v1-where-chip" data-tone="warn">Not connected</span></div>
        <div class="desk-v1-where-tile-foot"><button type="button" class="desk-v1-where-connect" data-where-service-connect="${esc(s.id)}">Connect ${esc(s.label)} ›</button></div>
      </div>`).join('');
  }

  function load(repaint) {
    if (loading || services !== null || error) return;
    loading = window.DeskV1Store.api('GET', '/api/desk/connect/services').then((r) => {
      if (!Array.isArray(r.services)) throw new Error('Invalid service catalogue.');
      services = r.services;
    }).catch((e) => { error = 'Could not load services: ' + (e.message || String(e)); })
      .finally(() => { loading = null; repaint(); });
  }

  function bind(el, repaint) {
    load(repaint);
    el.querySelector('[data-where-services-retry]')?.addEventListener('click', () => { error = ''; load(repaint); repaint(); });
    el.querySelectorAll('[data-where-service-connect]').forEach((btn) => {
      btn.onclick = async () => {
        const svc = services.find((s) => s.id === btn.dataset.whereServiceConnect);
        btn.disabled = true;
        try {
          const info = await window.DeskV1Store.api('POST', '/api/desk/connect/types', { input: svc.url });
          if (!btn.isConnected) return;
          window.DeskV1ConnectWizard.resume(info, { service: svc.id }, 'connection');
          window.deskV1Nav('connections', { account: 'add' });
        } catch (e) {
          window.DeskV1Kit.toast('Could not open Connect: ' + (e.message || String(e)));
        } finally { btn.disabled = false; }
      };
    });
  }

  window.DeskV1WhereServices = { html, bind };
})();
