// Desk v1 — Connections: the channel tile grid (Ron 2026-10-03, from phone: "show
// the channels at the top as boxes instead of lines ... right now it is too
// cluttered"). One compact tile per account, a coloured status pill each, and ONE
// selection: the account whose detail panel (Read via, profile, Reconnect, the
// Publishing line, Remove) the screen shows below the grid. Nothing selected = no
// panel. Window-bridged module, no `import` (ground rule 1).
//
// This file owns only the grid and the selection. What a detail panel contains,
// and what its buttons do, stay in static/js/desk-v1-connections.js, which hands
// this module its status function and the placeholder list.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // One short mark per platform: a glyph, never a logo asset (nothing to load).
  const MARKS = { x: 'X', linkedin: 'in', youtube: '▶', discord: 'D', reddit: 'r', blog: 'B' };
  function _mark(platform) { return MARKS[platform] || String(platform || '?').charAt(0).toUpperCase(); }

  // The selected account id. Survives a repaint (every action repaints the screen);
  // the caller drops it when that account is gone.
  let _selected = null;
  function selected() { return _selected; }
  function select(id) { _selected = id || null; }

  function tileHTML(ch, st) {
    const on = _selected === ch.id;
    return `
        <button type="button" class="desk-v1-conn-tile" data-conn-tile="${esc(ch.id)}" data-platform="${esc(ch.platform)}"
          data-conn-state="${st.key}" aria-pressed="${on}" aria-controls="desk-v1-conn-detail" title="${esc(ch.label || ch.identity || '')}">
          <span class="desk-v1-conn-tile-top">
            <span class="desk-v1-conn-tile-mark" aria-hidden="true">${esc(_mark(ch.platform))}</span>
            <span class="desk-v1-conn-tile-name">${esc(ch.label || ch.identity || '')}</span>
          </span>
          <span class="desk-v1-conn-pill" data-conn-tile-status data-state="${st.key}">${esc(st.word)}</span>
        </button>`;
  }

  // A channel that is not built yet: shown so the user sees what is coming, but it
  // is not a button: there is nothing to select.
  function placeholderHTML(p) {
    return `
        <div class="desk-v1-conn-tile desk-v1-conn-tile-static" data-conn-placeholder="${esc(p.id)}">
          <span class="desk-v1-conn-tile-top">
            <span class="desk-v1-conn-tile-mark" aria-hidden="true">${esc(_mark(p.id))}</span>
            <span class="desk-v1-conn-tile-name">${esc(p.label)}</span>
          </span>
          <span class="desk-v1-conn-pill" data-state="off">Not available yet</span>
        </div>`;
  }

  // opts: { accounts, statusOf(ch), placeholders[] }. The one that needs a human
  // (re-auth / not connected) gets the amber pill and edge from CSS; no extra rows.
  function gridHTML(opts) {
    const tiles = (opts.accounts || []).map((ch) => tileHTML(ch, opts.statusOf(ch)))
      .concat((opts.placeholders || []).map(placeholderHTML));
    return `<div class="desk-v1-conn-tiles" data-conn-tiles role="group" aria-label="Social accounts">${tiles.join('')}</div>`;
  }

  // The panel below the grid. `bodyHTML` is the account's own detail (built by
  // the Connections screen); empty string = nothing selected = no panel.
  function detailHTML(ch, bodyHTML) {
    if (!ch) return '';
    return `
        <div class="desk-v1-conn-detail" id="desk-v1-conn-detail" data-conn-detail="${esc(ch.id)}" role="region" aria-label="${esc(`${ch.label || ch.identity || 'Account'} details`)}">
          <button type="button" class="desk-v1-conn-detail-close" data-conn-detail-close aria-label="Close details">✕</button>
          ${bodyHTML}
        </div>`;
  }

  // Clicking a tile selects it, clicking the selected one (or ✕) closes the panel.
  // `repaint` is the screen's own; the panel is brought into view afterwards so on
  // a phone, where the grid fills the screen, the click visibly does something.
  function bind(el, repaint) {
    el.querySelectorAll('[data-conn-tile]').forEach((t) => t.addEventListener('click', () => {
      const id = t.dataset.connTile;
      _selected = _selected === id ? null : id;
      repaint();
      const panel = el.querySelector('[data-conn-detail]');
      if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: 'nearest' });
    }));
    const close = el.querySelector('[data-conn-detail-close]');
    if (close) close.addEventListener('click', () => { _selected = null; repaint(); });
  }

  window.DeskV1ConnTiles = { selected, select, gridHTML, detailHTML, bind };
})();
