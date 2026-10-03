// Desk v1 — Connections: ONE tile grid for everything the user has connected
// (Ron 2026-10-03, twice: "show the channels as boxes instead of lines", then "we
// now have a mix of tiles and rows ... it should all be organized in a clear way").
// Social accounts, content sources, generation engines and saved services are all
// tiles in the same grid; the last tile is always "Add service". ONE selection: the
// item whose detail panel (or the Add service panel) the screen shows below the grid.
// Nothing selected = no panel. Window-bridged module, no `import` (ground rule 1).
//
// This file owns only the grid and the selection. What a detail panel contains, and
// what its buttons do, stay with the module that owns that kind of item (accounts:
// desk-v1-connections.js, engines: desk-v1-engines.js, saved services:
// desk-v1-services.js, the Add service panel: desk-v1-add-service.js).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // One short mark per platform: a glyph, never a logo asset (nothing to load).
  const MARKS = { x: 'X', linkedin: 'in', youtube: '▶', discord: 'D', reddit: 'r', blog: 'B' };
  function mark(platform) { return MARKS[platform] || String(platform || '?').charAt(0).toUpperCase(); }

  // An account's label can carry its platform glyph ("𝕏 · @ron", "in · Clayrune page",
  // and the server's own "X · @ron"). The tile already draws that mark, so the name
  // drops the prefix instead of saying it twice.
  const _GLYPH_PREFIX = /^(?:\u{1D54F}|X|in|▶|D|r|B)\s*[·•]\s*/u;
  function plainName(label) { return String(label || '').replace(_GLYPH_PREFIX, ''); }

  // The selected key ('add' for the Add service tile). Survives a repaint (every
  // action repaints the screen); the caller drops it when that item is gone.
  let _selected = null;
  function selected() { return _selected; }
  function select(key) { _selected = key || null; }

  // item: { key, kind, mark, name, kindLabel, status: {key, word} }
  function tileHTML(it) {
    const on = _selected === it.key;
    return `
        <button type="button" class="desk-v1-conn-tile" data-conn-tile="${esc(it.key)}" data-conn-kind="${esc(it.kind)}"${it.platform ? ` data-platform="${esc(it.platform)}"` : ''}
          data-conn-state="${esc(it.status.key)}" aria-pressed="${on}" aria-controls="desk-v1-conn-detail" title="${esc(it.name)}">
          <span class="desk-v1-conn-tile-top">
            <span class="desk-v1-conn-tile-mark" aria-hidden="true">${esc(it.mark)}</span>
            <span class="desk-v1-conn-tile-name">${esc(it.name)}</span>
          </span>
          <span class="desk-v1-conn-tile-kind">${esc(it.kindLabel)}</span>
          <span class="desk-v1-conn-pill" data-conn-tile-status data-state="${esc(it.status.key)}">${esc(it.status.word)}</span>
        </button>`;
  }

  // The last tile, always. It opens the one generic add flow and has no status:
  // it is a way to add something, not a thing that is connected.
  function addTileHTML() {
    const on = _selected === 'add';
    return `
        <button type="button" class="desk-v1-conn-tile desk-v1-conn-tile-add" data-conn-add-tile aria-pressed="${on}"
          aria-controls="desk-v1-conn-detail" title="Add service">
          <span class="desk-v1-conn-tile-top">
            <span class="desk-v1-conn-tile-mark" aria-hidden="true">+</span>
            <span class="desk-v1-conn-tile-name">Add service</span>
          </span>
        </button>`;
  }

  // opts: { items[] }. A tile that needs a human (re-auth / not connected) gets the
  // amber pill and edge from CSS; no extra rows.
  function gridHTML(opts) {
    const tiles = (opts.items || []).map(tileHTML).concat([addTileHTML()]);
    return `<div class="desk-v1-conn-tiles" data-conn-tiles role="group" aria-label="Connected services">${tiles.join('')}</div>`;
  }

  // The panel below the grid. `bodyHTML` is the item's own detail (built by the
  // module that owns it); an empty key = nothing selected = no panel.
  function detailHTML(key, label, bodyHTML) {
    if (!key) return '';
    return `
        <div class="desk-v1-conn-detail" id="desk-v1-conn-detail" data-conn-detail="${esc(key)}" role="region" aria-label="${esc(`${label || 'Service'} details`)}">
          <button type="button" class="desk-v1-conn-detail-close" data-conn-detail-close aria-label="Close details">✕</button>
          ${bodyHTML}
        </div>`;
  }

  // Clicking a tile selects it, clicking the selected one (or ✕) closes the panel.
  // `repaint` is the screen's own; the panel is brought into view afterwards so on
  // a phone, where the grid fills the screen, the click visibly does something.
  // `onSelect(key)` runs before the repaint so a panel can reset its own state.
  function bind(el, repaint, onSelect) {
    const pick = (key) => {
      const next = _selected === key ? null : key;
      if (onSelect) onSelect(next, _selected);
      _selected = next;
      repaint();
      const panel = el.querySelector('[data-conn-detail]');
      if (panel && panel.scrollIntoView) panel.scrollIntoView({ block: 'nearest' });
    };
    el.querySelectorAll('[data-conn-tile]').forEach((t) => t.addEventListener('click', () => pick(t.dataset.connTile)));
    const add = el.querySelector('[data-conn-add-tile]');
    if (add) add.addEventListener('click', () => pick('add'));
    const close = el.querySelector('[data-conn-detail-close]');
    if (close) close.addEventListener('click', () => { if (onSelect) onSelect(null, _selected); _selected = null; repaint(); });
  }

  window.DeskV1ConnTiles = { selected, select, mark, plainName, gridHTML, detailHTML, bind };
})();
