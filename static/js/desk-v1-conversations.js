// Desk v1 (MC-977) — Conversations STUB (frame 12c). Built out in T6
// (docs/desk_v1_r0_plan.md). T0a only wires the route.
(function () {
  function deskV1RenderConversations(el, params) {
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">Conversations</div>
        <div class="desk-v1-stub-body">The source switch (On our posts / Mentions / Discussions) and threads land in T6.</div>
      </div>`;
  }

  window.deskV1RenderConversations = deskV1RenderConversations;
})();
