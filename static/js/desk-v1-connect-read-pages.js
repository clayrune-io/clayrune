// Desk v1 — Connections: the activity page of a browser-pane account, typed only when needed.
//
// The Desk finds an account's activity page itself (`mc/desk_engagement_pane_digest.py` discovery,
// then the platform's default addresses). When that fails the read coverage says
// `pages.status === 'pages_needed'`; only then does this block ask for an address, once, never
// first (Ron 2026-10-06). A saved address stays listed so it can be removed or replaced.
//
// The write is the account's own PATCH (`read_pages`), so it is human-only on the server like the
// profile choice. `patch(body)` is the Connections screen's own save helper (it files the account
// under its project); it resolves the updated account or false after saying why in place.
// Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _saved(ch) { return Array.isArray(ch.read_pages) ? ch.read_pages : []; }

  function html(ch) {
    return `<div class="desk-v1-readpages" data-readpages-for="${esc(ch.id)}" hidden></div>`;
  }

  function _paint(host, ch, pages, patch) {
    const saved = _saved(ch);
    const needed = !!pages && pages.status === 'pages_needed';
    if (!needed && !saved.length) { host.hidden = true; host.innerHTML = ''; return; }
    const list = saved.length ? `<ul class="desk-v1-readpages-list">${saved.map((p, i) => `
        <li data-readpages-page="${i}"><span>${esc(p.url)}</span>
          <button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-readpages-remove="${i}" aria-label="${esc('Stop reading ' + p.url)}">Remove</button></li>`).join('')}</ul>` : '';
    const why = needed
      ? `<div class="desk-v1-rules-hint" data-readpages-why>Clayrune could not find the page that lists activity on this account${pages.reason ? ` (${esc(String(pages.reason).slice(0, 200))})` : ''}. Paste its address.</div>`
      : '';
    host.hidden = false;
    host.innerHTML = `
        <span class="desk-v1-how-field-label">Activity page</span>
        ${why}${list}
        <div class="desk-v1-readpages-add">
          <input type="url" class="desk-v1-rules-textinput" data-readpages-input maxlength="2048" placeholder="https://" aria-label="Address of the page that lists activity on this account">
          <button type="button" class="desk-v1-conn-btn" data-readpages-add>Save address</button>
        </div>
        <div class="desk-v1-rules-hint" data-readpages-status></div>`;
    const status = host.querySelector('[data-readpages-status]');
    const input = host.querySelector('[data-readpages-input]');
    const save = (next) => patch({ read_pages: next }).then((acc) => {
      if (!acc) return;
      ch.read_pages = acc.read_pages || [];
      _paint(host, ch, pages, patch);
    });
    const add = () => {
      const v = (input.value || '').trim();
      if (!v) return;
      if (!/^https:\/\//i.test(v)) { status.textContent = 'The address must start with https://'; return; }
      save(saved.concat([{ role: 'activity', url: v }]));
    };
    host.querySelector('[data-readpages-add]').addEventListener('click', add);
    input.addEventListener('keydown', (ev) => { if (ev.key === 'Enter') { ev.preventDefault(); add(); } });
    host.querySelectorAll('[data-readpages-remove]').forEach((b) => b.addEventListener('click', () => {
      save(saved.filter((_p, i) => String(i) !== b.dataset.readpagesRemove));
    }));
  }

  // `pages` is the coverage entry's `pages` (or null before it loads). Never throws.
  function show(row, ch, pages, patch) {
    const host = row && row.querySelector(`[data-readpages-for="${CSS.escape(ch.id)}"]`);
    if (host) _paint(host, ch, pages, patch);
  }

  window.DeskV1ReadPages = { html, show };
})();
