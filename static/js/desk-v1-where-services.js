// Saved browser destinations on Where. The catalogue identifies destination
// platforms only; it never creates a tile without a saved Connections record.
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));
  let catalog = null;
  let saved = null;
  let loading = null;
  let error = '';
  let why = null; // the saved sign-in the user just tried to place; its notice stays until they leave Where
  function destination(s) {
    const bc = s.browser_connection;
    if (!bc) return null;
    // Legacy records have service:null. Match their saved URL, never the
    // display name or logo ("Instagram" could be a note about another site).
    const named = catalog.find((c) => c.id === bc.service);
    if (named) return named;
    let host;
    try { host = new URL(s.link).hostname.replace(/^www\./, ''); }
    catch { return null; }
    return catalog.find((c) => c.host.replace(/^www\./, '') === host) || null;
  }

  function html(channels) {
    if (!window.DeskV1Store.live()) return '';
    if (error || saved === null) return `<div role="status">${error ? esc(error) + ' <button type="button" class="desk-v1-where-connect" data-where-services-retry>Retry</button>' : 'Loading saved connections…'}</div>`;
    return saved.filter((s) => destination(s) &&
      !(s.account_id && channels.some((c) => c.id === s.account_id))).map((s) => {
      const reason = `Link the ${destination(s).label} account it is signed in as.`;
      return `<div class="desk-v1-where-source desk-v1-where-source-off" role="button" tabindex="0" data-where-service="${esc(s.id)}" data-service-name="${esc(s.name)}" data-platform="${esc(destination(s).id)}"
        aria-label="${esc(`${s.name}: a saved sign-in, not an account yet. Press Enter to see why it cannot be placed`)}">
        <div class="desk-v1-where-tile-head">${window.DeskV1ServiceIcons.markHTML(s, false)}<div class="desk-v1-where-tile-id"><div class="desk-v1-where-source-handle" title="${esc(s.name)}">${esc(s.name)}</div></div></div>
        <div class="desk-v1-where-tile-status"><span class="desk-v1-where-chip" data-tone="warn">Needs account</span></div>
        <div class="desk-v1-where-source-reason" title="${esc(reason)}">${esc(reason)}</div>
        <div class="desk-v1-where-tile-foot"><button type="button" class="desk-v1-where-connect" data-where-service-open="${esc(s.id)}">Link account ›</button></div>
      </div>`;
    }).join('');
  }

  // Dropping (or pressing Enter on) a saved sign-in: it holds a login, not an
  // account, so there is no column to add. Say that and offer the one next step.
  // The drop happens at the bottom of the page (SOURCES), the notice sits at the top: bring it into view.
  function explain(id, repaint) {
    why = id; repaint();
    const n = document.querySelector('[data-where-service-why]');
    if (n) n.scrollIntoView({ block: 'nearest' });
  }
  function noticeHTML() {
    const s = why && saved && saved.find((x) => x.id === why);
    const dest = s && destination(s);
    if (!dest) return '';
    return `<div class="desk-v1-where-notice" data-where-service-why role="status">${esc(dest.label)} can't be a channel yet: <strong>${esc(s.name)}</strong> is a saved sign-in, not an account. Link the ${esc(dest.label)} account it is signed in as, then drag that account up.
      <button type="button" class="desk-v1-where-notice-link" data-where-service-open="${esc(s.id)}">Link account ›</button></div>`;
  }

  function load(repaint) {
    if (loading || !window.DeskV1Store.live()) return;
    loading = Promise.all([
      catalog ? Promise.resolve({ services: catalog }) : window.DeskV1Store.api('GET', '/api/desk/connect/services'),
      window.DeskV1Store.api('GET', '/api/desk/services'),
    ]).then(([c, rows]) => {
      if (!Array.isArray(c.services) || !Array.isArray(rows)) throw new Error('Invalid saved connections.');
      catalog = c.services; saved = rows; error = '';
    }).catch((e) => { error = 'Could not load saved connections: ' + (e.message || String(e)); })
      .finally(() => { loading = null; repaint(); });
  }

  // Refresh on entry, not on paint: attachment/removal in Connections is visible
  // on return, without an endless load/repaint loop or a stale private cache.
  function refresh(repaint) { saved = null; error = ''; why = null; load(repaint); }
  function bind(el, repaint) {
    el.querySelector('[data-where-services-retry]')?.addEventListener('click', () => { error = ''; load(repaint); });
    el.querySelectorAll('[data-where-service-open]').forEach((btn) => {
      btn.onclick = () => window.deskV1Nav('connections', { account: 'service:' + btn.dataset.whereServiceOpen, attachSite: btn.dataset.whereServiceOpen });
    });
  }
  window.DeskV1WhereServices = { html, bind, refresh, explain, noticeHTML };
})();
