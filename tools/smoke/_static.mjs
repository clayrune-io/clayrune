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
import { readFileSync, readdirSync } from 'node:fs';
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

export function loadStaticJsCss(repoRoot) {
  const out = {};
  walk(resolve(repoRoot, 'static', 'js'), '/static/js', '.js', out);
  walk(resolve(repoRoot, 'static', 'css'), '/static/css', '.css', out);
  return out;
}
