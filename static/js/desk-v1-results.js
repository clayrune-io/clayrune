// Desk v1 (MC-977) — Results STUB (frame: none drawn, gap map C6). Built out
// in T7 (docs/desk_v1_r0_plan.md). T0a only wires the route.
(function () {
  function deskV1RenderResults(el, params) {
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">Results</div>
        <div class="desk-v1-stub-body">Goal, forecast, per-version outcomes and costs land in T7.</div>
      </div>`;
  }

  window.deskV1RenderResults = deskV1RenderResults;
})();
