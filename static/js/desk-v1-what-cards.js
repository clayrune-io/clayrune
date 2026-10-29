// Desk v1 ③ What: the campaign's pieces as media CARDS (Ron 2026-10-29: "the
// what screen needs some sort of graphic work ... to better emphasize that these
// are the actual media pieces the user has available for this campaign").
//
// Pure markup, no state and no handlers: desk-v1-what.js owns every behaviour
// and the data-* hooks it wires (data-what-row, data-primary-action,
// data-more-btn, data-add-media). This file owns only what a piece LOOKS like
// in the list, the header above it, the empty state and the tray's heading, so
// the look can change without touching the What stop's logic (one unit, one file).
//
//   preview   first image, or a video's poster with a play badge, or the first
//             lines of copy on a paper tile, or a kind glyph that invites media
//   body      title, `on N channels`, per-version status, extra thumbs + Add media
//
// Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  const GLYPHS = { post: '💬', article: '📄', video: '🎬', image: '🖼' };
  const PLACEHOLDER_TEXT = {
    video: 'No video yet', image: 'No image yet', post: 'No media yet', article: 'No media yet',
  };

  function _glyph(kind) { return GLYPHS[kind] || '▣'; }

  // What the piece shows at the top of its card. Returns the preview's inner
  // HTML and which of the four looks it is (the card carries it as data-preview).
  function _preview(fam, assets, copy) {
    const lead = assets[0];
    if (lead) {
      const isVideo = lead.kind === 'video' || (fam.kind === 'video' && !lead.src);
      const media = lead.src
        ? `<img class="desk-v1-wc-img" data-asset-id="${esc(lead.id)}" src="${esc(lead.src)}" alt="${esc(lead.title || fam.title || 'Piece media')}">`
        : `<span class="desk-v1-wc-nosrc" aria-hidden="true">${esc(isVideo ? '' : '▣')}</span>`;
      // The preview opens the lead tile on the strip below (desk-v1-what.js forwards
      // the click), so the big ▶ plays the video in the same player. It is not a
      // [data-pmed-open] itself: the strip stays the one list of the piece's media.
      const name = lead.title || (isVideo ? 'video' : 'picture');
      const label = esc(`${isVideo ? 'Play' : 'View'} ${name}`);
      return {
        look: isVideo ? 'video' : 'image',
        html: `<span class="desk-v1-wc-openwrap"><button type="button" class="desk-v1-wc-open" data-wc-open="${esc(lead.id)}" aria-label="${label}" title="${label}">` +
          media + (isVideo ? '<span class="desk-v1-wc-play" aria-hidden="true">▶</span>' : '') + '</button></span>',
      };
    }
    if ((fam.kind === 'post' || fam.kind === 'article') && copy) {
      return { look: 'paper', html: `<div class="desk-v1-wc-paper" data-wc-copy>${esc(copy)}</div>` };
    }
    return {
      look: 'empty',
      html: `<button type="button" class="desk-v1-wc-empty" data-add-media="${esc(fam.id)}" aria-haspopup="menu">` +
        `<span class="desk-v1-wc-empty-glyph" aria-hidden="true">${esc(_glyph(fam.kind))}</span>` +
        `<span class="desk-v1-wc-empty-text">${esc(PLACEHOLDER_TEXT[fam.kind] || 'No media yet')}</span>` +
        '<span class="desk-v1-wc-empty-add">＋ Add media</span></button>',
    };
  }

  // `ctx`: { kindWord, channelsText, statusHTML, copy }, all prepared by the
  // caller from the Kit so What and Where keep reading the same words.
  function cardHTML(fam, assets, ctx) {
    const pv = _preview(fam, assets, ctx.copy || '');
    const extra = assets.length ? window.DeskV1PieceMedia.thumbsHTML(fam) : ''; // play / remove each one
    const addBtn = pv.look === 'empty' ? ''
      : `<button type="button" class="desk-v1-what-addmedia desk-v1-wc-addmedia" data-add-media="${esc(fam.id)}" aria-haspopup="menu">＋ Add media</button>`;
    const strip = extra || addBtn ? `<div class="desk-v1-what-media desk-v1-wc-strip" data-what-media>${extra}${addBtn}</div>` : '';
    return `<div class="desk-v1-camp-card desk-v1-wc-card" data-what-row data-family-id="${esc(fam.id)}" data-kind="${esc(fam.kind || '')}" data-preview="${pv.look}">
      <div class="desk-v1-wc-preview" data-wc-preview>
        <span class="desk-v1-what-kind desk-v1-wc-badge" data-glyph="${esc(_glyph(fam.kind))}">${esc(String(ctx.kindWord || '').toUpperCase())}</span>
        ${pv.html}
      </div>
      <div class="desk-v1-wc-body">
        <div class="desk-v1-what-main">
          <button type="button" class="desk-v1-what-title" data-primary-action>${esc(fam.title)}</button>
          <div class="desk-v1-what-on" data-what-on>${esc(ctx.channelsText)}</div>
        </div>
        <div class="desk-v1-what-status" data-what-status>${ctx.statusHTML}</div>
        ${strip}
        <div class="desk-v1-camp-card-result" aria-live="polite"></div>
      </div>
      <div class="desk-v1-camp-card-more desk-v1-wc-more">
        <button type="button" class="desk-v1-camp-card-morebtn" data-more-btn aria-haspopup="menu" aria-label="More actions">⋯</button>
      </div>
    </div>`;
  }

  // The header over the list: it names the list as the campaign's own media.
  // `shown` differs from `total` only while a filter is on.
  function headerHTML(total, shown, filtered) {
    const n = (k) => `${k} piece${k === 1 ? '' : 's'}`;
    return `<div class="desk-v1-wc-head" data-wc-head>
      <h3 class="desk-v1-wc-head-title">In this campaign <span class="desk-v1-wc-count" data-wc-count>${esc(n(total))}${filtered ? ` · ${esc(shown)} shown` : ''}</span></h3>
      <p class="desk-v1-wc-head-sub">These are the posts, articles, videos and images this campaign will publish.</p>
    </div>`;
  }

  function emptyHTML() {
    return `<div class="desk-v1-camp-empty desk-v1-wc-nopieces" data-wc-empty>
      <span class="desk-v1-wc-nopieces-glyph" aria-hidden="true">🗂</span>
      <strong>No pieces yet</strong>
      <span>Pick a type under Add a piece below to make the first one. Nothing is posted until you approve it.</span>
    </div>`;
  }

  // The tray's heading: it is a palette for NEW pieces, never part of the list.
  function trayHeadHTML() {
    return '<div class="desk-v1-what-tray-title desk-v1-wc-tray-head"><span class="desk-v1-wc-tray-name">Add a piece</span>' +
      '<span class="desk-v1-what-tray-drag"> Pick a type, or drag one into the list above.</span></div>';
  }

  window.DeskV1WhatCards = { cardHTML, headerHTML, emptyHTML, trayHeadHTML };
})();
