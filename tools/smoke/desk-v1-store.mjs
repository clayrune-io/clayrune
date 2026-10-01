#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S0) — the store's live path, with NO fixtures seeded.
 *
 * Every other desk-v1 smoke seeds `window.DeskV1Fixtures` and runs the store
 * in its flag-off mode. This one is the production shape: nothing seeded, and
 * `desk_v1_live` on or off as each case needs.
 *   1. live ON, M1 answers  -> surfaces render the SERVER's project, not demo data.
 *   2. live ON, M1 fails    -> an explicit error state with the server's text and
 *                              Try again; no project card; fixtures never appear.
 *                              Try again re-reads M1 and renders.
 *   3. live OFF, unseeded   -> the "no live data" gate, still no demo data.
 *   4. run(): a refused write rolls back and toasts the server's own error; an
 *                              accepted one keeps its change.
 *
 * RUN   cd tools/smoke && node desk-v1-store.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const PID = 'smoke_store';
const PROJECTS = [{
  id: PID, name: 'Store smoke', status: 'active', domain: 'general', emoji: '🧪',
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
const WORKSPACE = {
  projects: [{ id: PID, name: 'Server Project Zed', state: 'active', roster: [],
    presence: { replies: 'drafts', desk_agent: null, state: 'active' } }],
  campaigns: [], accounts: [], pieces: [],
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// `m1` is read per request so a case can flip it between Try again clicks.
async function newPage(browser, { live, m1 }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const hits = { m1: 0 };
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(PROJECTS);
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/desk/workspace') { hits.m1++; const r = m1(); return J(r.body, r.status); }
    if (path === '/api/desk/nope-refused') return J({ error: 'refused by the server' }, 409);
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });
  await page.click('.sidebar-item[data-nav="social"]');
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors, hits };
}

const deskText = (page) => page.evaluate(() => document.querySelector('.modal-window[data-modal-id="__desk"]').innerText);
const settle = (page, pred) => page.waitForFunction(pred, null, { timeout: 8000 });

async function liveOk(browser) {
  const { ctx, page, pageErrors, hits } = await newPage(browser, { live: true, m1: () => ({ status: 200, body: WORKSPACE }) });
  await settle(page, () => /Server Project Zed/.test(document.body.innerText));
  ok('live ON: Home shows the project M1 returned');
  const fx = await page.evaluate(() => typeof window.DeskV1Fixtures);
  fx === 'undefined' ? ok('no DeskV1Fixtures global in the unseeded page') : fail('fixtures leaked into an unseeded page');
  const gate = await page.$('[data-store-gate]');
  !gate ? ok('no store gate once M1 has loaded') : fail('gate still painted after a good load');
  hits.m1 >= 1 ? ok(`M1 was read (${hits.m1}x)`) : fail('M1 never requested');

  const refused = await page.evaluate(async () => {
    const st = { n: 0 };
    const r = await window.DeskV1Store.run({
      label: 'Pause',
      apply: () => { st.n = 1; }, unapply: () => { st.n = 0; },
      request: () => window.DeskV1Store.api('POST', '/api/desk/nope-refused', {}),
    });
    return { r, n: st.n, text: document.body.innerText };
  });
  (refused.r.ok === false && refused.n === 0 && refused.r.error === 'refused by the server')
    ? ok('run(): refusal resolves {ok:false} with the server text and rolls the optimistic change back')
    : fail('run() refusal: ' + JSON.stringify(refused.r) + ' n=' + refused.n);
  /Pause was not saved: refused by the server/.test(refused.text) ? ok('run(): refusal toast names the change and the server error') : fail('no refusal toast text');

  const accepted = await page.evaluate(async () => {
    const st = { n: 0 };
    const r = await window.DeskV1Store.run({
      label: 'Resume',
      apply: () => { st.n = 1; }, unapply: () => { st.n = 0; },
      request: async () => ({ done: true }),
    });
    return { r, n: st.n };
  });
  (accepted.r.ok === true && accepted.n === 1) ? ok('run(): accepted write keeps its change') : fail('run() accept: ' + JSON.stringify(accepted));

  const rejected = await page.evaluate(() => window.DeskV1Store.run({}).then(() => 'resolved', () => 'rejected'));
  rejected === 'rejected' ? ok('run(): a malformed command is rejected, not run') : fail('malformed run() resolved');

  const real = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  real.length ? real.forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

async function liveFails(browser) {
  let mode = 'fail';
  const { ctx, page } = await newPage(browser, {
    live: true,
    m1: () => (mode === 'fail' ? { status: 500, body: { error: 'desk store exploded' } } : { status: 200, body: WORKSPACE }),
  });
  await settle(page, () => !!document.querySelector('[data-store-gate="error"]'));
  const t = await deskText(page);
  /desk store exploded/.test(t) ? ok("live ON, M1 500: the error state carries the server's own message") : fail('error text missing: ' + t.slice(0, 200));
  const hasRetry = await page.$('[data-store-retry]');
  hasRetry ? ok('error state offers Try again') : fail('no Try again button');
  !/Server Project Zed|Clayrune/.test(t) ? ok('no project or demo data painted under the error') : fail('data painted under an error');

  mode = 'ok';
  await page.click('[data-store-retry]');
  await settle(page, () => /Server Project Zed/.test(document.body.innerText));
  ok('Try again re-reads M1 and renders the project');
  await ctx.close();
}

async function liveOffUnseeded(browser) {
  const { ctx, page, hits } = await newPage(browser, { live: false, m1: () => ({ status: 500, body: {} }) });
  await settle(page, () => !!document.querySelector('[data-store-gate="off"]'));
  ok('live OFF, nothing seeded: the "no live data" gate shows');
  const t = await deskText(page);
  /Server Project Zed|Clayrune/.test(t) ? fail('demo or live data painted with live off') : ok('no data painted');
  hits.m1 === 0 ? ok('M1 is not requested while the flag is off') : fail('M1 read with the flag off');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON, M1 answers'); await liveOk(browser);
  console.log('live ON, M1 fails'); await liveFails(browser);
  console.log('live OFF, unseeded'); await liveOffUnseeded(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — the live store loads M1, shows an explicit error (never demo data) on failure, and run() rolls back refusals.');
