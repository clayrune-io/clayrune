// Desk v1 (MC-977) — piece page (docs/THE_DESK_V1_IA_REVISION_2.md §4.3, §8 row
// R2-7; first built as IA5). Window-bridged module, no `import` (ground rule 1).
//
// A piece is today's content family. R2-7 retired IA5's four facets: a piece is
// ONE drill-in page under ③ What (`‹ <campaign> · What`), a single scroll with
// three sections:
//
//   Copy      one row per destination version, its state, claims; `Review`
//             opens 12b as a child (`‹ <piece>`)
//   Media     the piece's assets (`piece.assets[]`, thumbnails + `＋ Add media`),
//             render status, the video director hand-off
//   Versions  one row per destination: account + format + PUBLISH TIME + state
//
// desk-v1-shell.js's `_renderPieceSkeleton` owns the frame and calls the three
// `deskV1FillPiece*` slots below. Reuse, don't fork: Review and the video
// director are still `deskV1Nav()` hand-offs to the existing `review`/`video`
// routes; the publish time is edited through the calendar's own reschedule
// command (`deskV1CalendarRescheduleVersion`), so "one value, two views" holds
// and the approval-window gate is the calendar's, not a second copy.
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

  // Resolves the piece by `familyId` first (every real piece-page nav carries
  // one — see desk-v1-campaign.js's `_runPrimaryAction`), falling back to a
  // `versionId` lookup for the IA1-era deep link (desk-v1-home.js's
  // `deskV1HomeGotoReview`) that only carries one — same fallback shape as
  // shell.js's own `_pieceLabel`.
  function _resolveFamily(params) {
    const p = params || {};
    if (p.familyId) { const f = _families().find((x) => x.id === p.familyId); if (f) return f; }
    if (p.versionId) return _families().find((f) => (f.versions || []).some((v) => v.id === p.versionId)) || null;
    return null;
  }

  // The page currently on screen, so a change made elsewhere (the calendar's
  // reschedule command and its Undo) can repaint the Versions section.
  let _cur = null; // { el, params }

  // `datetime-local` value <-> instant, in the browser's local time — the same
  // reading the calendar's own "Edit time" prompt uses (`new Date(value)`).
  function _toInputValue(d) {
    if (!d || isNaN(d)) return '';
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }
  function _versionWhen(v) {
    return typeof window.deskV1CalendarVersionWhen === 'function' ? window.deskV1CalendarVersionWhen(v) : null;
  }

  // ── header ─────────────────────────────────────────────────────────────
  function deskV1FillPieceHeader(el, params) {
    const fam = _resolveFamily(params);
    if (!fam) { el.innerHTML = '<div class="desk-v1-stub-inline">Piece not found.</div>'; return; }
    const needsReview = (fam.versions || []).filter((v) => v.state === 'needs_review').length;
    const kindGlyph = fam.kind === 'video' ? '🎥' : fam.kind === 'article' ? '📄' : fam.kind === 'image' ? '🖼' : '✎';
    el.innerHTML = `
      <div class="desk-v1-piece-header-top">
        <span class="desk-v1-piece-kind">${kindGlyph} ${esc(DeskV1Kit.pieceKindWord(fam.kind))}</span>
        <span class="desk-v1-piece-on">${esc(DeskV1Kit.pieceChannelsText(fam))}</span>
        ${needsReview ? `<span class="desk-v1-camp-tab-badge">${esc(needsReview)} to review</span>` : ''}
      </div>
      <h1 class="desk-v1-piece-title">${esc(fam.title)}</h1>`;
  }
  window.deskV1FillPieceHeader = deskV1FillPieceHeader;

  // ── body: Copy · Media · Versions, one scroll ──────────────────────────
  function _sectionHTML(id, title, inner) {
    return `<section class="desk-v1-piece-section" data-piece-section="${id}" aria-labelledby="desk-v1-piece-h-${id}">
      <h2 class="desk-v1-piece-section-title" id="desk-v1-piece-h-${id}">${esc(title)}</h2>
      ${inner}
    </section>`;
  }

  function _versionBadge(v) {
    const ch = _channel(v.channelId);
    return ch ? DeskV1Kit.channelBadge(ch, {}) : '<span class="desk-v1-camp-nochannel">No channel</span>';
  }

  function _copyHTML(fam) {
    const versions = fam.versions || [];
    const excerpt = (_fx().contentPreview || {})[fam.id];
    const rows = versions.map((v) => {
      const claims = (v.claims || []).length;
      const action = v.state === 'needs_review'
        ? `<button type="button" class="desk-v1-camp-card-primary" data-review-btn="${esc(v.id)}">${fam.kind === 'video' ? 'Watch & review' : 'Review'}</button>`
        : '';
      return `<div class="desk-v1-piece-facetrow" data-copy-row="${esc(v.id)}">
        <span>${_versionBadge(v)}${claims ? ` · ${esc(claims)} claim${claims === 1 ? '' : 's'}` : ''}</span>
        <span class="desk-v1-piece-rowend">${DeskV1Kit.stateLabelHTML(v.state)}${action}</span>
      </div>`;
    }).join('');
    return (excerpt ? `<div class="desk-v1-piece-excerpt" data-piece-excerpt>${esc(excerpt)}</div>` : '') +
      (rows || '<div class="desk-v1-piece-empty">No versions yet — add one from the Where board.</div>');
  }

  function _mediaHTML(fam) {
    const assets = DeskV1Kit.pieceAssets(fam);
    const thumbs = assets.map((a) => a.src
      ? `<img class="desk-v1-what-thumb" data-asset-id="${esc(a.id)}" src="${esc(a.src)}" alt="${esc(a.title || 'Asset')}" title="${esc(a.title || '')}">`
      : `<span class="desk-v1-what-thumb desk-v1-what-thumb-blank" data-asset-id="${esc(a.id)}" title="${esc(a.title || '')}">${esc(a.kind === 'video' ? '▶' : '▣')}</span>`).join('');
    const render = fam.render
      ? `<div class="desk-v1-piece-facetrow" data-piece-render><span>Render</span><span>${esc(fam.render.status)}${fam.render.progress != null && fam.render.status === 'rendering' ? ` ${esc(fam.render.progress)}%` : ''} · r${esc(fam.render.revision)}</span></div>`
      : '';
    const director = fam.kind === 'video'
      ? `<div class="desk-v1-piece-facetrow"><span>Video director</span><button type="button" class="desk-v1-camp-card-primary" data-video-btn>Open video director</button></div>`
      : '';
    const addable = fam.kind === 'video' || fam.kind === 'image' || fam.kind === 'post';
    return `<div class="desk-v1-what-media desk-v1-piece-media" data-piece-media>${thumbs}${addable
      ? `<button type="button" class="desk-v1-what-addmedia" data-add-media="${esc(fam.id)}" aria-haspopup="menu">＋ Add media</button>` : ''}</div>` +
      (assets.length || addable ? '' : '<div class="desk-v1-piece-empty">No media on this piece.</div>') + render + director;
  }

  function _versionsHTML(fam) {
    const versions = fam.versions || [];
    if (!versions.length) return '<div class="desk-v1-piece-empty">No destinations yet.</div>';
    return versions.map((v) => {
      const fmt = v.format ? ` · ${esc(v.format)}` : '';
      const when = _versionWhen(v);
      const frozen = !!v.publishedAt || ['verified_published', 'you_reported', 'archived', 'skipped'].includes(v.state);
      return `<div class="desk-v1-piece-facetrow" data-version-row="${esc(v.id)}">
        <span>${_versionBadge(v)}${fmt}</span>
        <span class="desk-v1-piece-rowend">
          <label class="desk-v1-piece-time"><span class="desk-v1-piece-time-label">Publish time</span>
            <input type="datetime-local" data-version-time="${esc(v.id)}" value="${esc(_toInputValue(when))}" ${frozen ? 'disabled' : ''} aria-label="Publish time">
          </label>
          ${DeskV1Kit.stateLabelHTML(v.state)}
        </span>
      </div>`;
    }).join('');
  }

  function deskV1FillPieceBody(el, params) {
    const fam = _resolveFamily(params);
    if (!fam) { el.innerHTML = '<div class="desk-v1-stub-inline">Piece not found.</div>'; return; }
    _cur = { el, params };
    _paintBody(el, fam, params);
  }
  window.deskV1FillPieceBody = deskV1FillPieceBody;

  function _paintBody(el, fam, params) {
    el.innerHTML = `<div class="desk-v1-piece-facetbody">
      ${_sectionHTML('copy', 'Copy', _copyHTML(fam))}
      ${_sectionHTML('media', 'Media', _mediaHTML(fam))}
      ${_sectionHTML('versions', 'Versions', _versionsHTML(fam))}
    </div>`;
    el.querySelectorAll('[data-review-btn]').forEach((b) => b.onclick = () => {
      window.deskV1Nav('review', { campaignId: fam.campaignId, versionId: b.dataset.reviewBtn, projectId: params.projectId });
    });
    const videoBtn = el.querySelector('[data-video-btn]');
    if (videoBtn) videoBtn.onclick = () => window.deskV1Nav('video', { campaignId: fam.campaignId, familyId: fam.id, projectId: params.projectId });
    const add = el.querySelector('[data-add-media]');
    if (add) add.onclick = () => {
      if (typeof window.deskV1WhatAddMedia === 'function') window.deskV1WhatAddMedia(add, fam, () => _repaint());
    };
    el.querySelectorAll('[data-version-time]').forEach((input) => {
      input.onchange = () => {
        if (!input.value) { _repaint(); return; }
        const ok = typeof window.deskV1CalendarRescheduleVersion === 'function'
          && window.deskV1CalendarRescheduleVersion(input.dataset.versionTime, new Date(input.value));
        if (!ok) DeskV1Kit.toast('Could not change that time.');
        _repaint();
      };
    });
  }

  // Repaints the mounted piece page (header + body) from the fixture.
  function _repaint() {
    if (!_cur || !_cur.el || !_cur.el.isConnected) return;
    const fam = _resolveFamily(_cur.params);
    if (!fam) return;
    const head = document.getElementById('desk-v1-piece-header');
    if (head) deskV1FillPieceHeader(head, _cur.params);
    _paintBody(_cur.el, fam, _cur.params);
  }
  window.deskV1PieceRepaint = _repaint;

  // ── Posy box (full mount only). Scope label is the piece title; draftKey
  // follows the T3 project-prefixed convention (§3 T3 row), keyed by familyId
  // (stable, never a label — same reasoning desk-v1-campaign.js's own comment
  // gives for its card-selection draft key). ──────────────────────────────
  function deskV1FillPieceRightColumn(el, params) {
    const fam = _resolveFamily(params);
    if (!fam) { el.innerHTML = ''; return; }
    const camp = _campaign(fam.campaignId);
    const projectId = params.projectId || ((camp || {}).projectId);
    const agentRef = window.DeskV1Kit ? window.DeskV1Kit.deskAgentRef({ project: _project(projectId), campaign: camp }) : null;
    el.innerHTML = `<div class="desk-v1-camp-posy">${window.DeskV1Kit ? window.DeskV1Kit.posyBoxHTML({
      inputId: 'desk-v1-piece-posy-input', scopeLabel: fam.title, agentRef,
    }) : ''}</div>`;
    if (!window.DeskV1Kit) return;
    window.DeskV1Kit.bindPosyBox(el.querySelector('.desk-v1-camp-posy'), 'desk-v1-piece-posy-input', (text) => {
      window.DeskV1Kit.toast('Sent to ' + window.DeskV1Kit.deskAgentName({ project: _project(projectId), campaign: camp }) + ': “' + text + '”');
      window.DeskV1Kit.paintPosyReadyNoDiff(el.querySelector('.desk-v1-camp-posy'));
    }, {
      draftKey: `project:${projectId}:piece:${fam.id}:`,
      taskLifecycle: true,
    });
  }
  window.deskV1FillPieceRightColumn = deskV1FillPieceRightColumn;
})();
