// Desk v1 — Connections: the Service step's name suggestions (docs/DESK_CONNECT_BY_URL_SPEC.md,
// slice 2; Ron 2026-10-03: "add service only from url or name"). As a NAME is typed, the
// registry's closest services are listed under the box (GET /api/desk/connect/suggest);
// choosing one fills the box and goes on. Anything that looks like an address is not
// suggested for. Suggestions are registry data only: no web lookup of any kind happens here.
// Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const DELAY_MS = 120;
  let _items = [];
  let _seq = 0;                    // an answer for an older keystroke is dropped
  let _timer = null;

  function reset() { _items = []; _seq++; clearTimeout(_timer); }
  function items() { return _items.slice(); }

  function _looksLikeAddress(t) { return t.indexOf('/') >= 0 || t.indexOf('.') >= 0 || t.indexOf(':') >= 0; }

  function listHTML() {
    return '<ul class="desk-v1-cf-suggest" data-cf-suggest role="listbox" aria-label="Matching services" hidden></ul>';
  }

  function _paint(root) {
    const ul = root.querySelector('[data-cf-suggest]');
    if (!ul) return;
    ul.innerHTML = _items.map((s) => `<li role="presentation"><button type="button" role="option" class="desk-v1-cf-suggest-item" data-cf-suggest-pick="${esc(s.id)}">
        <span class="desk-v1-cf-suggest-name">${esc(s.label)}</span><span class="desk-v1-cf-suggest-host">${esc(s.host)}</span></button></li>`).join('');
    ul.hidden = _items.length === 0;
  }

  // ctx: { input, api(method, url), onPick(item) }
  function bind(root, ctx) {
    const input = ctx.input;
    const ul = root.querySelector('[data-cf-suggest]');
    if (!input || !ul) return;
    const ask = () => {
      const q = input.value.trim();
      clearTimeout(_timer);
      if (!q || _looksLikeAddress(q)) { _items = []; _seq++; _paint(root); return; }
      const n = ++_seq;
      _timer = setTimeout(async () => {
        let got = [];
        try { const r = await ctx.api('GET', `/api/desk/connect/suggest?q=${encodeURIComponent(q)}`); got = (r && r.suggestions) || []; } catch (_) { got = []; }
        if (n !== _seq) return;
        _items = got;
        _paint(root);
      }, DELAY_MS);
    };
    input.addEventListener('input', ask);
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { const b = ul.querySelector('button'); if (b) { e.preventDefault(); b.focus(); } }
      else if (e.key === 'Escape' && _items.length) { _items = []; _paint(root); }
    });
    ul.addEventListener('keydown', (e) => {
      const btns = Array.from(ul.querySelectorAll('button'));
      const at = btns.indexOf(document.activeElement);
      if (e.key === 'ArrowDown' && at < btns.length - 1) { e.preventDefault(); btns[at + 1].focus(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); (at > 0 ? btns[at - 1] : input).focus(); }
      else if (e.key === 'Escape') { input.focus(); }
    });
    ul.addEventListener('click', (e) => {
      const b = e.target.closest('[data-cf-suggest-pick]');
      if (!b) return;
      const item = _items.find((s) => s.id === b.dataset.cfSuggestPick);
      if (item) { _items = []; _paint(root); ctx.onPick(item); }
    });
    _paint(root);                  // a repaint of the screen keeps what was listed
  }

  window.DeskV1ConnectSuggest = { listHTML, bind, reset, items };
})();
