/**
 * Desk v1 (MC-1021 R1-W S0) — the smoke harness's stand-in for the live Desk
 * store. Not a smoke itself: a module every desk-v1 smoke imports.
 *
 * WHY. The v1 surfaces read `DeskV1Store.state()` (static/js/desk-v1-store.js),
 * which with `desk_v1_live` off hands them `window.DeskV1Fixtures` itself
 * (demo mode). Since R1-W S10 the production page no longer loads the fixture
 * file (it lives in tools/smoke/fixtures/), so this module is the ONLY thing
 * that puts them there:
 *
 *   INJECT. `installDemoFixtures(page)` runs the fixture file as an init
 *      script, so `window.DeskV1Fixtures` exists before any page script, the
 *      same object a smoke that mutates it (and 19 do) has always seen. Only
 *      the demo-mode smokes (the ones that call `seedDeskV1Fixtures`) get it:
 *      a live-mode page has no fixtures at all, so a surface that read them in
 *      live mode would fail the smoke instead of passing quietly.
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
export const FIXTURES_PATH = resolve(__dirname, 'fixtures', 'desk-v1-fixtures.js');
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

/** Put `window.DeskV1Fixtures` on every document the page loads (demo mode). */
export async function installDemoFixtures(page) {
  await page.addInitScript(FIXTURES_SOURCE);
}

export async function seedDeskV1Fixtures(page) {
  await installDemoFixtures(page);
  await page.route('**/api/desk/workspace', (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    return route.fulfill({
      status: 200, contentType: 'application/json',
      body: JSON.stringify(workspaceFromFixtures()),
    });
  });
}
