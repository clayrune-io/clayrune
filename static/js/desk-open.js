// Opens the Desk. This is the one entry point everything else calls:
// the sidebar "Desk" target, the dashboard's Open Desk button, and the
// server-side screenshot capture (mc/desk_capture.py).
//
// static/js/*.js are ES modules, so a top-level function is not global;
// anything called from an inline handler or another file has to be on
// `window` explicitly — same rule as every other module here.
//
// With Desk v1 on (the default) this hands straight to the v1 shell
// (desk-v1-shell.js), which owns the whole `__desk` modal. Until the legacy
// screen is deleted, `window.openLegacyDesk` (set by desk.js) is the fallback
// for an install that still has the old `desk_v1: false` setting.
(function () {
  function openDesk() {
    const cfg = (typeof _globalConfig !== 'undefined' && _globalConfig) || {};
    if (cfg.desk_v1 && typeof window.deskV1Open === 'function') {
      return window.deskV1Open();
    }
    if (typeof window.openLegacyDesk === 'function') return window.openLegacyDesk();
  }

  window.openDesk = openDesk;
})();
