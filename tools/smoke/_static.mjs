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
// was loaded before DOMContentLoaded (they `goto`, then read `window.DeskV1*`), so
// by default the loader they are served ends with a top-level await on the same
// single load; a module script holds DOMContentLoaded until that settles. The two
// smokes that test the real loading (desk-v1-lazy-load) passes
// `{ lazyDesk: true }` and get the file as shipped.
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

// A smoke that opens the Desk right after the dashboard's first card appears must
// wait for the background Desk load first (a top-level await does not hold
// DOMContentLoaded). The smokes that test the loading itself do not call this.
export function deskLoaded(page) {
  return page.waitForFunction(() => !!(window.DeskLoad && window.DeskLoad.isLoaded()), null, { timeout: 30000 });
}
