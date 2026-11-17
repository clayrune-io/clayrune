// Opens the Desk. This is the one entry point everything else calls:
// the sidebar "Desk" target, the dashboard's Open Desk button, and the
// server-side screenshot capture (mc/desk_capture.py).
//
// static/js/*.js are ES modules, so a top-level function is not global;
// anything called from an inline handler or another file has to be on
// `window` explicitly — same rule as every other module here.
//
// Desk v1 is the only Desk: this hands straight to the v1 shell
// (desk-v1-shell.js), which owns the `__desk` modal. It does not read the
// stored `desk_v1` config value; an old `desk_v1: false` is inert.
(function () {
  function openDesk() {
    if (typeof window.deskV1Open === 'function') return window.deskV1Open();
  }

  window.openDesk = openDesk;
})();
