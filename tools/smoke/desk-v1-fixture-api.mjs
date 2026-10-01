/**
 * Desk v1 (MC-1021 R1-W S0) — the smoke harness's stand-in for the live Desk
 * store. Not a smoke itself: a module every desk-v1 smoke imports.
 *
 * WHY. The v1 surfaces read `DeskV1Store.state()` (static/js/desk-v1-store.js),
 * which with `desk_v1_live` off hands them `window.DeskV1Fixtures` itself
 * (demo mode; the page loads static/js/desk-v1-fixtures.js), so a smoke that
 * mutates it (and 19 do) sees its own writes, exactly as before the store
 * existed. This module gives a smoke the one thing the page can't:
 *
 *   SERVE M1. `GET /api/desk/workspace` is answered from the fixtures in the
 *      server's own response shape, so a smoke can turn `desk_v1_live` on and
 *      exercise the real load path. Each request evaluates the fixture file
 *      afresh, so one test's mutations never leak into the next.
 *
 * Production never imports this file; it is not under static/.
 *
 * Call `seedDeskV1Fixtures(page)` AFTER the smoke's own `page.route('**\/*', ...)`:
 * Playwright runs the most recently registered matching route first, so the
 * M1 handler below wins for its one URL and everything else falls through.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import vm from 'node:vm';

const __dirname = dirname(fileURLToPath(import.meta.url));
export const FIXTURES_PATH = resolve(__dirname, '..', '..', 'static', 'js', 'desk-v1-fixtures.js');
export const FIXTURES_SOURCE = readFileSync(FIXTURES_PATH, 'utf8');

/** The fixture object, freshly evaluated (the file is a window-bridged IIFE). */
export function loadFixtures() {
  const win = {};
  vm.runInNewContext(FIXTURES_SOURCE, { window: win, console });
  return win.DeskV1Fixtures;
}

/** The fixtures in M1's response shape (`mc.desk.v1_workspace`). */
export function workspaceFromFixtures(fx = loadFixtures()) {
  return {
    projects: fx.projects,
    campaigns: fx.campaigns,
    accounts: fx.channels,
    pieces: fx.families,
  };
}

export async function seedDeskV1Fixtures(page) {
  await page.route('**/api/desk/workspace', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(workspaceFromFixtures()),
    });
  });
}
