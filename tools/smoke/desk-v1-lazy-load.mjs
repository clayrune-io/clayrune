#!/usr/bin/env node
/**
 * Desk v1 (MC-1205, half 2 / FE21) — the Desk is off the dashboard boot path.
 *
 *   1. BOOT        dashboard boot requests NO desk-v1 script or stylesheet before
 *                  it reports itself booted (the `mc-boot-complete` event).
 *   2. BACKGROUND  after boot, once idle, the whole Desk family loads on its own:
 *                  every script and sheet, each requested exactly once.
 *   3. VERSIONED   every Desk file the entry requests carries the `?v=` token the
 *                  server stamped on the loader's tag (a dynamic import does not
 *                  get one from the server; the entry copies it).
 *   4. OPEN WARM   opening the Desk after that is the real Desk in the same JS
 *                  turn and requests no Desk file again (never a second load).
 *   5. OPEN COLD   a click BEFORE the load is done (entry held back) shows the
 *                  home loading frame in the same JS turn, never a blank window;
 *                  a second click does not stack a second window; when the load
 *                  finishes the real Desk replaces it, one request per file.
 *   6. FAILURE     the load fails -> the window shows the error and Try again; a
 *                  working Try again opens the real Desk. No endless spinner.
 *   7. CALLERS     non-Desk callers of Desk code (secrets panel presets) work
 *                  without the Desk having been opened.
 *
 * Fails on the code before MC-1205 at check 1 (132+ desk-v1 requests at boot).
 *
 * RUN   cd tools/smoke && node desk-v1-lazy-load.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const RAW_INDEX = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const TOKEN = 'smoketoken1';
// What server.py index() does to every /static/*.js|css reference.
const INDEX_HTML = RAW_INDEX.replace(/(src|href)="(\/static\/[^"?]+\.(?:js|css))"/g, `$1="$2?v=${TOKEN}"`);
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT, { lazyDesk: true });
const ENTRY_PATH = '/static/js/desk-v1-entry.js';

const PID = 'smoke_lazy_load';
const PROJECTS = [{
  id: PID, name: 'Lazy load smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster: [],
}];
const DRAFT = JSON.parse(JSON.stringify(loadFixtures().campaigns.find((c) => c.id === 'camp-1')));
Object.assign(DRAFT, { id: 'll-draft', projectId: PID, state: 'draft' });
DRAFT.plan.title = 'Lazy load draft'; delete DRAFT.map; delete DRAFT.subject;
const WORKSPACE = {
  projects: [{ id: PID, name: 'Server Project Lumen', state: 'active', roster: [],
    presence: { replies: 'drafts', desk_agent: null, state: 'active' } }],
  campaigns: [DRAFT], accounts: [], pieces: [],
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const isDeskFile = (url) => /\/static\/(js|css)\/desk-v1-/.test(url) && !/desk-v1-loading\./.test(url);

// `entry` is a handler for the entry file's request: hold it, fail it, or pass it.
async function newPage(browser, { entry } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const reqs = [];          // every Desk file request, in order
  let bootMark = null;      // how many Desk requests had happened when boot reported complete
  await page.exposeFunction('__bootMark', () => { if (bootMark === null) bootMark = reqs.length; });
  await page.addInitScript(() => window.addEventListener('mc-boot-complete', () => window.__bootMark()));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const u = new URL(req.url());
    const path = u.pathname;
    if (isDeskFile(req.url())) reqs.push(u.pathname + u.search);
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === ENTRY_PATH && entry) return entry(route, () => route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] }));
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(PROJECTS);
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/desk/workspace') return J(WORKSPACE);
    if (path === '/api/desk/engagement') return J({ conversations: [] });
    if (path === '/api/desk/engines') return J({ engines: [] });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  return { ctx, page, pageErrors, reqs, bootMark: () => bootMark };
}

const settle = (page, pred, arg, timeout = 15000) => page.waitForFunction(pred, arg, { timeout });
const clickDesk = (page) => page.evaluate(() => {
  document.querySelector('.sidebar-item[data-nav="social"]').click();
  return {
    realShell: !!document.querySelector('.desk-v1-shell'),
    windows: document.querySelectorAll('.modal-window[data-modal-id="__desk"]').length,
    rows: document.querySelectorAll('.desk-v1-loading-row').length,
    text: (document.querySelector('.modal-window[data-modal-id="__desk"]') || { innerText: '' }).innerText,
  };
});

const browser = await chromium.launch();
try {
  // ── 1-4: boot, background load, token, warm open ───────────────────────────
  {
    const { ctx, page, pageErrors, reqs, bootMark } = await newPage(browser);
    await settle(page, () => window.__bootMarkSeen !== undefined || (window.__mcBootTimeline || []).some((m) => m.stage === 'boot continuation done'), null, 20000)
      .catch(() => fail('the dashboard never reported "boot continuation done"'));
    await settle(page, () => window.DeskLoad && window.DeskLoad.isLoaded(), null, 30000).catch(() => fail('the Desk never loaded in the background'));
    const atBoot = bootMark();
    atBoot === 0 ? ok('zero desk-v1 script/stylesheet requests before the dashboard reported booted')
      : fail(`${atBoot} desk-v1 requests were made before boot completed (first: ${reqs[0]})`);

    const js = reqs.filter((r) => /\/js\//.test(r));
    const css = reqs.filter((r) => /\/css\//.test(r));
    (js.length >= 134 && css.length >= 84) ? ok(`the Desk loaded itself in the background (${js.length} scripts, ${css.length} stylesheets)`)
      : fail(`background load incomplete: ${js.length} scripts, ${css.length} stylesheets`);
    const dups = reqs.filter((r, i) => reqs.indexOf(r) !== i);
    dups.length === 0 ? ok('every Desk file was requested exactly once') : fail('requested more than once: ' + dups.slice(0, 4).join(', '));

    // Without a token: only the modules a Desk file imports by a relative path (those
    // are bare by nature) and the entry's own BARE set. Everything the entry lists
    // itself must carry the token the loader copied from its own stamped tag.
    const entrySrc = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'desk-v1-entry.js'), 'utf8');
    const listed = new Set([...entrySrc.match(/const MODULES = \[([\s\S]*?)\];/)[1].matchAll(/'([^']+)'/g)].map((m) => '/static/js/' + m[1] + '.js'));
    const bareSet = new Set([...entrySrc.match(/const BARE = new Set\(\[([\s\S]*?)\]\)/)[1].matchAll(/'([^']+)'/g)].map((m) => '/static/js/' + m[1] + '.js'));
    const unversioned = reqs.filter((r) => !r.includes('v=' + TOKEN)).map((r) => r.split('?')[0]);
    const stray = unversioned.filter((u) => /\/css\//.test(u) || (listed.has(u) && !bareSet.has(u)));
    stray.length === 0 ? ok(`every script and sheet the entry lists carries ?v=${TOKEN} (copied from the loader's tag); ${unversioned.length} bare by design (relative imports)`)
      : fail('Desk files requested without the version token: ' + stray.slice(0, 5).join(', '));

    const before = reqs.length;
    const warm = await clickDesk(page);
    (warm.realShell && warm.windows === 1) ? ok('a warm open is the real Desk in the same JS turn') : fail('warm open: ' + JSON.stringify(warm));
    await settle(page, () => /Server Project Lumen/.test(document.body.innerText));
    ok('the home page fills in with the workspace');
    reqs.length === before ? ok('opening the Desk made no further Desk file request (no second load)') : fail(`${reqs.length - before} Desk requests on a warm open`);
    pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('zero page errors');
    await ctx.close();
  }

  // ── 5: cold open, entry held back ──────────────────────────────────────────
  {
    let release; const gate = new Promise((r) => { release = r; });
    const { ctx, page, pageErrors, reqs } = await newPage(browser, { entry: async (route, pass) => { await gate; return pass(); } });
    const cold = await clickDesk(page);
    (!cold.realShell && cold.windows === 1 && cold.rows >= 3) ? ok('cold open: a Desk window with the home loading frame is up in the same JS turn')
      : fail('cold open: ' + JSON.stringify({ ...cold, text: cold.text.slice(0, 60) }));
    /CAMPAIGN/.test(cold.text) ? ok('cold open: the home column headings are there') : fail('cold open has no headings: ' + cold.text.slice(0, 80));
    const again = await clickDesk(page);
    again.windows === 1 ? ok('a second click does not stack a second window') : fail('second click made ' + again.windows + ' windows');
    release();
    await settle(page, () => !!document.querySelector('.desk-v1-shell') && /Server Project Lumen/.test(document.body.innerText));
    const after = await page.evaluate(() => ({ windows: document.querySelectorAll('.modal-window[data-modal-id="__desk"]').length, preload: !!document.getElementById('desk-v1-preload') }));
    (after.windows === 1 && !after.preload) ? ok('when the load finishes the real Desk replaces the loading window') : fail('after the swap: ' + JSON.stringify(after));
    const dups = reqs.filter((r, i) => reqs.indexOf(r) !== i);
    dups.length === 0 ? ok('a click before the load did not start a second load') : fail('requested twice: ' + dups.slice(0, 4).join(', '));
    pageErrors.length ? fail('page errors: ' + pageErrors.join(' | ')) : ok('zero page errors');
    await ctx.close();
  }

  // ── 6: the load fails, then Try again ──────────────────────────────────────
  {
    let failing = true;
    const { ctx, page, pageErrors } = await newPage(browser, {
      entry: async (route, pass) => (failing ? route.fulfill({ status: 500, contentType: 'text/plain', body: 'boom' }) : pass()),
    });
    await clickDesk(page);
    await settle(page, () => !!document.querySelector('#desk-v1-preload [data-store-gate="error"]'), null, 20000);
    const err = await page.evaluate(() => ({ text: document.getElementById('desk-v1-preload').innerText, retry: !!document.querySelector('#desk-v1-preload [data-store-retry]'), rows: document.querySelectorAll('.desk-v1-loading-row').length }));
    (/could not be loaded/i.test(err.text) && err.retry && err.rows === 0) ? ok('a failed load shows its own error with Try again, not a spinner') : fail('failure state: ' + JSON.stringify(err));
    failing = false;
    await page.click('#desk-v1-preload [data-store-retry]');
    await settle(page, () => !!document.querySelector('.desk-v1-shell') && /Server Project Lumen/.test(document.body.innerText), null, 30000);
    ok('Try again loads the Desk and opens it');
    pageErrors.filter((m) => !/Failed to fetch dynamically imported module|boom/.test(m)).length
      ? fail('page errors: ' + pageErrors.join(' | ')) : ok('no unexpected page errors');
    await ctx.close();
  }

  // ── 7: a non-Desk caller that needs Desk code ──────────────────────────────
  {
    const { ctx, page } = await newPage(browser);
    const viaLoad = await page.evaluate(async () => {
      const before = !!window.DeskV1Engines;
      await window.DeskLoad.ready();
      return { before, after: !!window.DeskV1Engines && typeof window.DeskV1Engines.list === 'function' };
    });
    viaLoad.after ? ok(`DeskLoad.ready() makes Desk code available to non-Desk callers (before: ${viaLoad.before})`) : fail('DeskLoad.ready() did not provide DeskV1Engines');
    await ctx.close();
  }
} finally {
  await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall checks passed');
