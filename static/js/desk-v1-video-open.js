// Desk v1: the ONE way a video piece is opened (Ron 2026-10-29: "two different
// 'video' editors exist ... Ron wants ONE: every entry point to a video piece
// opens the Studio video editor (with scenes)").
//
// The Studio video editor is the `storyboard` route (desk-v1-studio.js, scenes,
// story chat, render). Before this file a video piece reached it only from the
// What create-card; a card click went to the generic piece page, whose `Open
// video director` button went to the older `video` route. Every caller now goes
// through `DeskV1VideoOpen.open()` / `.openFromDeepLink()` instead of choosing a
// route itself, so where a video piece opens is decided here and nowhere else.
//
//   open(fam, camp, opts)       push the Studio editor for one video piece
//   openFromDeepLink(params)    campaign (③ What) + editor, for a Home deep link
//   familyFor(params)           the video piece a {familyId|versionId|campaignId} names
//   deskV1RenderVideoEditor     the `storyboard` route's renderer: the editor plus
//                               the strip above it
//
// New video makes a video. A post carrying it is made through New post and
// Add media. Only a waiting version's Review remains above the scene editor.
//
// Back from the editor reads `‹ Back to What` when it was opened from the
// campaign's What stop (desk-v1-shell.js `_backLabel`). The piece page's own
// `Open video editor` goes back to the editor it came from, rather than pushing
// a second copy of it (`fromEditor`).
//
// Window-bridged module, no `import` (ground rule 1).
(function () {
  function esc(s) { return window.esc ? window.esc(s) : String(s == null ? '' : s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function _fx() { return window.DeskV1Store.state(); }
  function _families() { return _fx().families || []; }
  function _campaign(id) { return (_fx().campaigns || []).find((c) => c.id === id) || null; }

  function isVideo(fam) { return !!fam && fam.kind === 'video'; }

  // The video piece a nav's params name: `familyId` first, else the piece that
  // holds `versionId`, else (a campaign-level link such as Home's "video to
  // watch") the campaign's first video piece. null when none is a video.
  function familyFor(params) {
    const p = params || {};
    let fam = null;
    if (p.familyId) fam = _families().find((f) => f.id === p.familyId) || null;
    if (!fam && p.versionId) fam = _families().find((f) => (f.versions || []).some((v) => v.id === p.versionId)) || null;
    if (!fam && p.campaignId && !p.familyId && !p.versionId) fam = _families().find((f) => f.campaignId === p.campaignId && isVideo(f)) || null;
    return isVideo(fam) ? fam : null;
  }

  // Opens the Studio editor for `fam`. `camp` may be null (the piece's own
  // campaign is used). `opts.fromEditor`: the caller is the piece page the editor
  // itself opened, so going back IS opening the editor. Returns true.
  function open(fam, camp, opts) {
    const o = opts || {};
    if (o.fromEditor && typeof window.deskV1Back === 'function') { window.deskV1Back(); return true; }
    const campaignId = (camp && camp.id) || fam.campaignId;
    const projectId = (camp && camp.projectId) || (_campaign(campaignId) || {}).projectId;
    window.deskV1Nav('storyboard', { campaignId, familyId: fam.id, projectId });
    return true;
  }

  // Studio has no campaign or post to create. It still uses the SAME route
  // and renderer, with the existing standalone storyboard owner.
  function openStudio(params) {
    window.deskV1Nav('storyboard', Object.assign({}, params, { kind: 'video', standalone: true }));
    return true;
  }

  // A deep link that lands inside a campaign (Home's needs-you rows): pushes the
  // campaign at ③ What, then the editor on top, so Back reads `‹ Back to What`.
  // Returns false (and pushes nothing) when the params name no video piece.
  function openFromDeepLink(params) {
    const fam = familyFor(params);
    if (!fam) return false;
    const campaignId = fam.campaignId || (params || {}).campaignId;
    const camp = _campaign(campaignId);
    const projectId = (params || {}).projectId || (camp && camp.projectId) || null;
    window.deskV1Nav('campaign', { campaignId, projectId, panel: 'what' });
    return open(fam, camp);
  }

  // ── the strip above the editor ───────────────────────────────────────────
  function _toReview(fam) { return (fam.versions || []).filter((v) => v.state === 'needs_review'); }

  function stripHTML(fam) {
    const review = _toReview(fam);
    if (!review.length) return '';
    return `<div class="desk-v1-vo-strip" data-video-open-strip data-family-id="${esc(fam.id)}">
      <span class="desk-v1-vo-actions">
        ${review.length ? `<button type="button" class="desk-v1-camp-card-primary" data-vo-review="${esc(review[0].id)}">Review${review.length > 1 ? ` (${esc(review.length)})` : ''}</button>` : ''}
      </span>
    </div>`;
  }

  function _bindStrip(host, fam, params) {
    const review = host.querySelector('[data-vo-review]');
    if (review) review.onclick = () => window.deskV1Nav('review', { campaignId: fam.campaignId, versionId: review.dataset.voReview, projectId: params.projectId });
  }

  // The `storyboard` route. The strip sits above the editor, which paints into
  // its own body element, so its repaints (a live load landing, a render) never
  // wipe the strip. A route that names no video piece (an article/image card, or
  // a stale link) is the editor alone, exactly as before.
  function deskV1RenderVideoEditor(el, params) {
    params = params || {};
    const fam = familyFor(params);
    if (!fam || typeof window.deskV1RenderStoryboard !== 'function') {
      if (typeof window.deskV1RenderStoryboard === 'function') window.deskV1RenderStoryboard(el, params);
      return;
    }
    el.innerHTML = '<div data-video-editor-strip></div><div data-video-editor-body></div>';
    const strip = el.querySelector('[data-video-editor-strip]');
    window.deskV1RenderStoryboard(el.querySelector('[data-video-editor-body]'), params);
    strip.innerHTML = stripHTML(fam);
    _bindStrip(strip, fam, params);
  }

  window.DeskV1VideoOpen = { isVideo, familyFor, open, openStudio, openFromDeepLink, stripHTML };
  window.deskV1RenderVideoEditor = deskV1RenderVideoEditor;
})();
