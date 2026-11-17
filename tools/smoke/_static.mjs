// Shared static loader for the smoke harnesses' fake servers.
//
// The harnesses serve the SPA from memory (Playwright route.fulfill or a bare
// http server), so they must preload every file the page can request. They used
// to do this with a FLAT readdirSync of static/js and static/css, which 404s
// silently on any module in a subfolder (static/js/learn-lessons/). This walks
// both trees recursively so a new subfolder needs no harness edit.
//
// Usage:  Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
// Returns { '/static/js/a/b.js': [contentType, utf8Body], ... } — the same
// shape the harnesses already build by hand.
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';

const TYPES = {
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

function walk(dir, urlPrefix, ext, out) {
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    if (ent.isDirectory()) walk(resolve(dir, ent.name), `${urlPrefix}/${ent.name}`, ext, out);
    else if (ent.name.endsWith(ext)) out[`${urlPrefix}/${ent.name}`] = [TYPES[ext], readFileSync(resolve(dir, ent.name), 'utf8')];
  }
  return out;
}

// The Desk is not in index.html any more: static/js/desk-load.js imports it in the
// background after boot (MC-1205). The smokes were written for a page whose Desk
// was loaded before DOMContentLoaded, and that is no longer true of any page.
// By default the loader they are served ends with a top-level await on the same
// single load, which only makes the load START at once (as shipped it waits for
// the dashboard's boot, then an idle moment). It does NOT hold DOMContentLoaded:
// a module's top-level await never does (measured: `page.goto(..., {waitUntil:
// 'domcontentloaded'})` returns while the awaiting module is still pending). So
// every smoke that touches the Desk must `await deskLoaded(page)` after each
// goto and reload, before it reads a window.DeskV1* global: the entry imports
// its modules one after another, and one global being present says nothing about
// the rest. desk-v1-lazy-load passes `{ lazyDesk: true }` and gets the file as
// shipped, because it tests the loading itself.
export function loadStaticJsCss(repoRoot, { isolateConnectScreens = false, lazyDesk = false } = {}) {
  const out = {};
  walk(resolve(repoRoot, 'static', 'js'), '/static/js', '.js', out);
  walk(resolve(repoRoot, 'static', 'css'), '/static/css', '.css', out);
  // The marks on Connections cards (static/desk-icons/): SVG as text, the one PNG as bytes.
  const icons = resolve(repoRoot, 'static', 'desk-icons');
  if (existsSync(icons)) for (const f of readdirSync(icons)) {
    if (f.endsWith('.svg')) out[`/static/desk-icons/${f}`] = ['image/svg+xml', readFileSync(resolve(icons, f), 'utf8')];
    else if (f.endsWith('.png')) out[`/static/desk-icons/${f}`] = ['image/png', readFileSync(resolve(icons, f))];
  }
  // Unit harnesses provide their own navigation/destination fixtures. Production
  // activation belongs to the end-to-end simplify harness, which does not opt out.
  if (isolateConnectScreens) for (const name of ['account-step', 'options-step', 'integration']) {
    out[`/static/js/desk-v1-connect-${name}.js`] = [TYPES['.js'], ''];
  }
  const loader = out['/static/js/desk-load.js'];
  if (loader && !lazyDesk) loader[1] += '\nawait window.DeskLoad.ready();\n';
  return out;
}

// Resolves once the whole Desk has loaded (DeskLoad.isLoaded(): the entry has
// imported every script and stylesheet). Call it after EVERY page.goto and
// page.reload of the dashboard, before reading any window.DeskV1* global or
// opening the Desk: navigation resolves at DOMContentLoaded, which the background
// load does not hold. The smokes that test the loading itself do not call this.
export function deskLoaded(page) {
  // On a page served as shipped the load only starts after boot and an idle
  // moment (up to 4 s); starting it here costs nothing and makes the wait short.
  return page.waitForFunction(() => {
    if (!window.DeskLoad) return false;
    if (!window.__deskLoadKicked) { window.__deskLoadKicked = true; window.DeskLoad.ready().catch(() => {}); }
    return window.DeskLoad.isLoaded();
  }, null, { timeout: 30000 });
}
