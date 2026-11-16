// Desk v1 Studio: re-check a browser-pane engine (Gemini on your plan) until it is ready.
// Ron 2026-11-15: the image panel said "No browser pane is open on the 'main' profile"
// with the pane open and signed in, because the engine list was read once, when the
// panel opened, and the pane was opened after. One unit, one file (AGENT_RULES.md).
//
//   watchPaneEngine(host, { alive, getEngines, loadEngines, selected, onChange })
//
// While the SELECTED engine is a browser-pane engine that is not ready and the panel is
// on screen, it asks the server again every few seconds, and on window focus. It
// repaints only when readiness actually changed, so an open panel does not flicker and a
// half-typed prompt or a held estimate is left alone. It stops by itself once the panel
// is gone (alive() false). The server check is one dict lookup when no pane is open.
const POLL_MS = 4000;
const watched = new Map();   // host -> opts
let timer = null;

function _waiting(opts) {
  const e = opts.selected(opts.getEngines());
  return !!(e && e.auth && e.auth.kind === 'browser_pane' && !(e.connected && e.connected.ready));
}

async function _check(host, opts) {
  if (opts.busy || !opts.alive() || !host.getClientRects().length || !opts.getEngines() || !_waiting(opts)) return;
  opts.busy = true;
  try {
    const before = JSON.stringify((opts.selected(opts.getEngines()) || {}).connected || {});
    const engines = await opts.loadEngines();
    if (!opts.alive()) return;
    const after = JSON.stringify((opts.selected(engines) || {}).connected || {});
    if (after !== before) opts.onChange(engines);
  } catch (_) {
    // the panel's own error line covers a server that cannot be reached; try again next tick
  } finally {
    opts.busy = false;
  }
}

function _tick() {
  for (const [host, opts] of watched) {
    if (!opts.alive()) { watched.delete(host); continue; }
    _check(host, opts);
  }
  if (!watched.size) { clearInterval(timer); timer = null; }
}

export function watchPaneEngine(host, opts) {
  watched.set(host, { ...opts, busy: false });
  if (!timer) timer = setInterval(_tick, POLL_MS);
}

window.addEventListener('focus', () => { if (watched.size) _tick(); });
