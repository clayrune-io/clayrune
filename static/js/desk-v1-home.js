// Desk v1 (MC-977) — Desk Home STUB (frame 11a). Built out in T1
// (docs/desk_v1_r0_plan.md): the promote box, Needs you, campaign cards as
// drop targets, and the Channels/Material shelves. T0a only wires the route
// so `desk-v1-shell.js` has something real to render and Back can be proved
// to work end to end.
(function () {
  function deskV1RenderHome(el) {
    const camps = (window.DeskV1Fixtures && window.DeskV1Fixtures.campaigns) || [];
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">The Desk</div>
        <div class="desk-v1-stub-body">Home — promote box, Needs you, campaign cards and shelves land in T1.</div>
        <div class="desk-v1-stub-list">
          ${camps.map(c => `<button type="button" class="desk-v1-stub-link" onclick="deskV1Nav('campaign',{campaignId:'${c.id}'})">${esc(c.name)}</button>`).join('') || '<span class="desk-v1-stub-empty">No fixture campaigns.</span>'}
        </div>
      </div>`;
  }

  window.deskV1RenderHome = deskV1RenderHome;
})();
