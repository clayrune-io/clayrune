// Piece page, VERSIONS section with no destinations yet: one line and one link
// into the Where board for this piece's campaign (or to Desk Home when the
// piece has no campaign to put it on a channel in).
(function () {
  const esc = (s) => window.esc(String(s == null ? '' : s));

  function html(fam) {
    const camp = (window.DeskV1Store.state().campaigns || []).find((c) => c.id === fam.campaignId);
    if (fam.archivedAt) return '<div class="desk-v1-piece-empty" data-piece-versions-empty>No destinations yet</div>';
    return camp
      ? `<div class="desk-v1-piece-empty" data-piece-versions-empty>No destinations yet
          <button type="button" class="desk-v1-piece-empty-link" data-piece-put-on-channel="${esc(fam.id)}">View campaign channels ›</button></div>`
      : `<div class="desk-v1-piece-empty" data-piece-versions-empty>No destinations yet
          <span class="desk-v1-piece-empty-hint">This piece is not in a campaign yet.</span>
          <button type="button" class="desk-v1-piece-empty-link" data-piece-choose-campaign>Choose a campaign ›</button></div>`;
  }

  function bind(el, fam) {
    el.querySelector('[data-piece-put-on-channel]')?.addEventListener('click', () =>
      window.deskV1GotoCampaignPanel('where', { campaignId: fam.campaignId, highlightFamilyId: fam.id }));
    el.querySelector('[data-piece-choose-campaign]')?.addEventListener('click', () => window.deskV1Nav('home', {}));
  }

  window.DeskV1PieceVersionsEmpty = { html, bind };
})();
