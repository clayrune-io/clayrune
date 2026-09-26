// Desk v1 (MC-977) — Campaign page STUB (frame 12a). Built out in T2a/T2b
// (docs/desk_v1_r0_plan.md). `desk-v1-shell.js` owns the campaign-page
// skeleton (summary / tab strip / tab body / right column / add tray) and
// mounts each slot by calling the five `deskV1FillCampaign*` hooks below —
// this file owns their content so the skeleton never has to know whether a
// slot's real ticket has landed. T0a's versions are placeholders; T2a/T2b
// replace their bodies without touching the skeleton or the router.
(function () {
  function _campaign(campaignId) {
    const camps = (window.DeskV1Fixtures && window.DeskV1Fixtures.campaigns) || [];
    return camps.find(c => c.id === campaignId) || null;
  }

  function deskV1FillCampaignSummary(el, params) {
    const c = _campaign(params.campaignId);
    el.innerHTML = c
      ? `<div class="desk-v1-stub-inline">${esc(c.name)} &middot; goal ${c.goal.current}/${c.goal.target} ${esc(c.goal.metric)} &mdash; summary bar lands in T2a</div>`
      : '<div class="desk-v1-stub-inline">Summary — T2a</div>';
  }

  function deskV1FillCampaignTabStrip(el) {
    el.innerHTML = '<div class="desk-v1-stub-inline">Content &middot; Conversations &middot; Results — tabs land in T2a</div>';
  }

  function deskV1FillCampaignTabBody(el, params) {
    const c = _campaign(params.campaignId);
    const families = c
      ? ((window.DeskV1Fixtures && window.DeskV1Fixtures.families) || []).filter(f => f.campaignId === c.id)
      : [];
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-body">Content list (grouped, per-version states) lands in T2a.</div>
        <div class="desk-v1-stub-list">
          ${families.map(f => `<div class="desk-v1-stub-row">${esc(f.title)} &middot; ${f.versions.length} version${f.versions.length === 1 ? '' : 's'}</div>`).join('')}
        </div>
      </div>`;
  }

  function deskV1FillCampaignRightColumn(el) {
    el.innerHTML = '<div class="desk-v1-stub-inline">Posy box — T0b kit, scoped in T2a</div>';
  }

  function deskV1FillCampaignAddTray(el) {
    el.innerHTML = '<div class="desk-v1-stub-inline">Add tray (Channels &middot; Material) — T2a, reusing T1\'s shelves</div>';
  }

  window.deskV1FillCampaignSummary = deskV1FillCampaignSummary;
  window.deskV1FillCampaignTabStrip = deskV1FillCampaignTabStrip;
  window.deskV1FillCampaignTabBody = deskV1FillCampaignTabBody;
  window.deskV1FillCampaignRightColumn = deskV1FillCampaignRightColumn;
  window.deskV1FillCampaignAddTray = deskV1FillCampaignAddTray;
})();
