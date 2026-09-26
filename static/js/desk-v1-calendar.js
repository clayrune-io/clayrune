// Desk v1 (MC-977) — Calendar view STUB (frame 12f). Built out in T4
// (docs/desk_v1_r0_plan.md); reuses helpers from schedule-calendar.js by
// window-bridging, not its hour-row grid. T0a only wires the route.
(function () {
  function deskV1RenderCalendar(el, params) {
    el.innerHTML = `
      <div class="desk-v1-stub">
        <div class="desk-v1-stub-title">Calendar</div>
        <div class="desk-v1-stub-body">Channel rows x day columns, drag-to-reschedule, lands in T4.</div>
      </div>`;
  }

  window.deskV1RenderCalendar = deskV1RenderCalendar;
})();
