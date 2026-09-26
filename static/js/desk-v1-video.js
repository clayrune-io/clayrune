// Desk v1 (MC-977) — video intake + director STUB (frames: 11d not in repo,
// 12d). Built out in T5 (docs/desk_v1_r0_plan.md). T0a only wires the route.
(function () {
  function deskV1RenderVideo(el, params) {
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">Video</div>
        <div class="desk-v1-stub-body">Intake sheet, director, scene strip and the render card land in T5.</div>
      </div>`;
  }

  window.deskV1RenderVideo = deskV1RenderVideo;
})();
