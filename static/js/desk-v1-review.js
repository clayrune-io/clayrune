// Desk v1 (MC-977) — full-width review STUB (frame 12b). Built out in T3
// (docs/desk_v1_r0_plan.md). T0a only wires the route.
(function () {
  function deskV1RenderReview(el, params) {
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">Review</div>
        <div class="desk-v1-stub-body">The full-width article/video review with claims and Show changes lands in T3.</div>
      </div>`;
  }

  window.deskV1RenderReview = deskV1RenderReview;
})();
