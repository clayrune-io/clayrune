// Desk v1 (MC-977) — Proposed state, Start sheet, rules popover, Posy
// instructions STUB (frame: none drawn, gap map C6). Built out in T2b
// (docs/desk_v1_r0_plan.md). T0a only wires the route.
(function () {
  function deskV1RenderRules(el, params) {
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">Rules</div>
        <div class="desk-v1-stub-body">Proposed state, Start sheet and the rules popover land in T2b.</div>
      </div>`;
  }

  window.deskV1RenderRules = deskV1RenderRules;
})();
