// Desk v1 (MC-977) — IA5: piece page (docs/THE_DESK_V1_IA_REVISION.md §2.4,
// §5 row IA5). Window-bridged module, no `import` (ground rule 1).
//
// A piece is today's content family (fam-*, §2.4: "A piece is today's
// content family"). This file fills the four `deskV1FillPiece*` slots
// desk-v1-shell.js's `_renderPieceSkeleton`/`_renderPieceFacet` own — the
// skeleton itself, and the in-place facet switch, live in shell.js (the
// same split as desk-v1-campaign.js's five `deskV1FillCampaign*` hooks).
//
// Reuse, don't fork (§2.4 table, ticket note "Reuse existing review
// (desk-v1-review.js) and video director (desk-v1-video.js) rendering; do
// not fork them"): the What facet's Review action and the How facet's video
// director action both `deskV1Nav()` straight into the existing `review`/
// `video` routes — this file never re-renders review or video body markup
// of its own.
//
// Fixtures only (ground rule 3): nothing here calls a backend route.
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  // ── data resolution (same _fx() convention as desk-v1-campaign.js/-review.js) ─
  function _fx() { return window.DeskV1Fixtures || {}; }
  function _families() { return _fx().families || []; }
  function _campaigns() { return _fx().campaigns || []; }
  function _channels() { return _fx().channels || []; }
  function _channel(id) { return _channels().find((c) => c.id === id); }
  function _campaign(id) { return _campaigns().find((c) => c.id === id) || null; }
  function _project(id) { return (_fx().projects || []).find((p) => p.id === id) || null; }

  // Resolves the piece by `familyId` first (every real piece-page nav now
  // carries one — see desk-v1-campaign.js's `_runPrimaryAction`), falling
  // back to a `versionId` lookup for the IA1-era deep link
  // (desk-v1-home.js's `deskV1HomeGotoReview`) that only carries one — same
  // fallback shape as shell.js's own `_pieceLabel`.
  function _resolveFamily(params) {
    const p = params || {};
    if (p.familyId) { const f = _families().find((x) => x.id === p.familyId); if (f) return f; }
    if (p.versionId) return _families().find((f) => (f.versions || []).some((v) => v.id === p.versionId)) || null;
    return null;
  }

  function _fmtWhen(iso) {
    if (!iso) return null;
    try {
      const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
      return new Intl.DateTimeFormat(undefined, { timeZone: cfg.user_timezone || undefined, weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' }).format(new Date(iso));
    } catch (e) { return iso; }
  }

  const FACETS = [
    { id: 'what', label: 'What' },
    { id: 'how', label: 'How' },
    { id: 'when', label: 'When' },
    { id: 'where', label: 'Where' },
  ];

  // ── header (full mount only, §5 acceptance: same DOM node across facets) ──
  function deskV1FillPieceHeader(el, params) {
    const fam = _resolveFamily(params);
    if (!fam) { el.innerHTML = '<div class="desk-v1-stub-inline">Piece not found.</div>'; return; }
    const needsReview = (fam.versions || []).filter((v) => v.state === 'needs_review').length;
    const kindGlyph = fam.kind === 'video' ? '🎥' : fam.kind === 'article' ? '📄' : '✎';
    const kindWord = fam.kind === 'video' ? 'Video' : fam.kind === 'article' ? 'Article' : 'Post';
    el.innerHTML = `
      <div class="desk-v1-piece-header-top">
        <span class="desk-v1-piece-kind">${kindGlyph} ${esc(kindWord)}</span>
        ${needsReview ? `<span class="desk-v1-camp-tab-badge">${esc(needsReview)} to review</span>` : ''}
      </div>
      <h1 class="desk-v1-piece-title">${esc(fam.title)}</h1>`;
  }
  window.deskV1FillPieceHeader = deskV1FillPieceHeader;

  // ── facet strip ────────────────────────────────────────────────────────
  function deskV1FillPieceFacetStrip(el, params) {
    const fam = _resolveFamily(params);
    const facet = params.facet || 'what';
    el.innerHTML = `
      <div class="desk-v1-camp-tabs" role="tablist">
        ${FACETS.map((f) => `<button type="button" class="desk-v1-camp-tab" aria-selected="${facet === f.id}" data-facet="${f.id}">${esc(f.label)}</button>`).join('')}
      </div>`;
    if (!fam) return;
    el.querySelectorAll('[data-facet]').forEach((b) => b.onclick = () => {
      window.deskV1GotoPieceFacet(b.dataset.facet, {
        familyId: fam.id, campaignId: fam.campaignId, projectId: params.projectId, versionId: params.versionId,
      });
    });
  }
  window.deskV1FillPieceFacetStrip = deskV1FillPieceFacetStrip;

  // ── facet body ─────────────────────────────────────────────────────────
  function deskV1FillPieceBody(el, params) {
    const fam = _resolveFamily(params);
    if (!fam) { el.innerHTML = '<div class="desk-v1-stub-inline">Piece not found.</div>'; return; }
    const facet = params.facet || 'what';
    if (facet === 'what') _fillWhat(el, fam, params);
    else if (facet === 'how') _fillHow(el, fam, params);
    else if (facet === 'when') _fillWhen(el, fam, params);
    else _fillWhere(el, fam, params);
  }
  window.deskV1FillPieceBody = deskV1FillPieceBody;

  // What (§2.4: "message, angle for this piece, copy per version, claims +
  // sources, `? Assumed` notes... absorbs review 12b text"). R0 has no
  // separate per-piece "angle" field yet (T1's `proposedExtras.assumptions`
  // is per-campaign, not per-piece) — this facet's own content is the
  // needs-review list plus a hand-off into the real review surface (ticket:
  // "review 12b... become piece children"), never a second copy of 12b's
  // article body.
  function _fillWhat(el, fam, params) {
    const needsReview = (fam.versions || []).filter((v) => v.state === 'needs_review');
    const rowsHTML = needsReview.length
      ? needsReview.map((v) => {
          const ch = _channel(v.channelId);
          return `<div class="desk-v1-piece-facetrow">
            <span>${ch ? esc(ch.label) : 'No channel'}</span>
            <button type="button" class="desk-v1-camp-card-primary" data-review-btn="${esc(v.id)}">${fam.kind === 'video' ? 'Watch & review' : 'Review'}</button>
          </div>`;
        }).join('')
      : '<div class="desk-v1-piece-empty">Nothing needs review right now.</div>';
    el.innerHTML = `<div class="desk-v1-piece-facetbody">${rowsHTML}</div>`;
    el.querySelectorAll('[data-review-btn]').forEach((b) => b.onclick = () => {
      window.deskV1Nav('review', { campaignId: fam.campaignId, versionId: b.dataset.reviewBtn, projectId: params.projectId });
    });
  }

  // How (§2.4: "production: article writing, video (director 12d, render
  // card, budget), images/screenshots, material attached"). The director
  // itself is T5's own surface (ticket: "video 12d become piece children");
  // this facet only hands off to it, exactly like What hands off to review.
  function _fillHow(el, fam, params) {
    let bodyHTML;
    if (fam.kind === 'video') {
      const rendered = fam.render ? `${esc(fam.render.status)} · r${esc(fam.render.revision)}` : 'not started';
      bodyHTML = `<div class="desk-v1-piece-facetrow">
          <span>Render: ${rendered}</span>
          <button type="button" class="desk-v1-camp-card-primary" data-video-btn>Open video director</button>
        </div>`;
    } else if (fam.kind === 'article') {
      bodyHTML = `<div class="desk-v1-piece-empty">📄 Article · ${esc(fam.wordCount || 0)} words. Material attaches from the piece's card (⋯ → Add a channel version, or drop from the Material shelf).</div>`;
    } else {
      bodyHTML = '<div class="desk-v1-piece-empty">No production job for this piece yet.</div>';
    }
    const attached = fam.attachedAssets || [];
    if (attached.length) bodyHTML += `<div class="desk-v1-piece-empty">Attached: ${attached.map(esc).join(', ')}</div>`;
    el.innerHTML = `<div class="desk-v1-piece-facetbody">${bodyHTML}</div>`;
    const videoBtn = el.querySelector('[data-video-btn]');
    if (videoBtn) videoBtn.onclick = () => window.deskV1Nav('video', { campaignId: fam.campaignId, familyId: fam.id, projectId: params.projectId });
  }

  // When (§2.4: "publish time per version, calendar position, hold
  // window... absorbs the calendar chip, `publishAt` on versions").
  function _fillWhen(el, fam) {
    const rowsHTML = (fam.versions || []).map((v) => {
      const ch = _channel(v.channelId);
      const when = v.publishedAt ? `Published ${esc(_fmtWhen(v.publishedAt))}`
        : v.publishAt ? `Scheduled ${esc(_fmtWhen(v.publishAt))}`
        : 'Not scheduled';
      return `<div class="desk-v1-piece-facetrow"><span>${ch ? esc(ch.label) : 'No channel'}</span><span>${when}</span></div>`;
    }).join('') || '<div class="desk-v1-piece-empty">No destinations yet.</div>';
    el.innerHTML = `<div class="desk-v1-piece-facetbody">${rowsHTML}</div>`;
  }

  // Where (§2.4: "one row per destination version: account + voice + format
  // + state... absorbs the versions list on the content card").
  function _fillWhere(el, fam) {
    const rowsHTML = (fam.versions || []).map((v) => {
      const ch = _channel(v.channelId);
      const badge = ch ? (window.DeskV1Kit ? window.DeskV1Kit.channelBadge(ch, {}) : esc(ch.label)) : '<span class="desk-v1-camp-nochannel">No channel</span>';
      const fmt = v.format ? ` · ${esc(v.format)}` : '';
      const stateHTML = window.DeskV1Kit ? window.DeskV1Kit.stateLabelHTML(v.state) : esc(v.state);
      return `<div class="desk-v1-piece-facetrow"><span>${badge}${fmt}</span><span>${stateHTML}</span></div>`;
    }).join('') || '<div class="desk-v1-piece-empty">No destinations yet.</div>';
    el.innerHTML = `<div class="desk-v1-piece-facetbody">${rowsHTML}</div>`;
  }

  // ── Posy box (full mount only — §5 acceptance: same DOM node across
  // facets). Scope label is the piece title; draftKey follows the T3
  // project-prefixed convention (§3 T3 row), keyed by familyId (stable,
  // never a label — same reasoning desk-v1-campaign.js's own comment gives
  // for its card-selection draft key). ───────────────────────────────────
  function deskV1FillPieceRightColumn(el, params) {
    const fam = _resolveFamily(params);
    if (!fam) { el.innerHTML = ''; return; }
    const projectId = params.projectId || ((_campaign(fam.campaignId) || {}).projectId);
    el.innerHTML = `<div class="desk-v1-camp-posy">${window.DeskV1Kit ? window.DeskV1Kit.posyBoxHTML({
      inputId: 'desk-v1-piece-posy-input', scopeLabel: fam.title,
    }) : ''}</div>`;
    if (!window.DeskV1Kit) return;
    window.DeskV1Kit.bindPosyBox(el.querySelector('.desk-v1-camp-posy'), 'desk-v1-piece-posy-input', (text) => {
      window.DeskV1Kit.toast('Sent to Posy: “' + text + '”');
      window.DeskV1Kit.paintPosyReadyNoDiff(el.querySelector('.desk-v1-camp-posy'));
    }, {
      draftKey: `project:${projectId}:piece:${fam.id}:`,
      taskLifecycle: true,
    });
  }
  window.deskV1FillPieceRightColumn = deskV1FillPieceRightColumn;
})();
