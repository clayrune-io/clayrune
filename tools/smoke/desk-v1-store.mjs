#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S0) — the store's live path, with NO fixtures seeded.
 *
 * The other desk-v1 smokes run the store in its flag-off (demo) mode. This one
 * covers both modes as production sees them, with `desk_v1_live` set per case.
 *   1. live ON, M1 answers  -> surfaces render the SERVER's project, not demo
 *                              data, and the "Demo data" banner is absent.
 *   2. live ON, M1 fails    -> an explicit error state with the server's text and
 *                              Try again; no project card; demo data and the
 *                              banner never appear. Try again re-reads M1.
 *   3. live OFF             -> DEMO MODE: the banner "Demo data - not your
 *                              workspace" shows on every page (Home, a project,
 *                              a campaign), the fixtures render, M1 is not read.
 *   4. run(): a refused write rolls back and toasts the server's own error; an
 *                              accepted one keeps its change.
 *
 * RUN   cd tools/smoke && node desk-v1-store.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { installDemoFixtures, loadFixtures } from './desk-v1-fixture-api.mjs';

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
// Home lists only projects that have a live campaign (Ron 2026-10-01), so the
// project needs one to appear: a draft cloned from a fixture campaign, which
// carries every field the surfaces read.
const DRAFT = JSON.parse(JSON.stringify(loadFixtures().campaigns.find((c) => c.id === 'camp-1')));
Object.assign(DRAFT, { id: 'zed-draft', projectId: PID, state: 'draft' });
DRAFT.plan.title = 'Zed draft'; delete DRAFT.map; delete DRAFT.subject;
const WORKSPACE = {
  projects: [{ id: PID, name: 'Server Project Zed', state: 'active', roster: [],
    presence: { replies: 'drafts', desk_agent: null, state: 'active' } }],
  campaigns: [DRAFT], accounts: [], pieces: [],
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// `m1` is read per request so a case can flip it between Try again clicks.
async function newPage(browser, { live, m1, fixtures = !live }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  if (fixtures) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
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
  const notFx = await page.evaluate(() => window.DeskV1Store.state() !== window.DeskV1Fixtures && !window.DeskV1Store.demo());
  notFx ? ok('live ON: state() is not the fixtures and demo() is false') : fail('live ON store is handing out the fixtures');
  const bannerOn = await page.$eval('#desk-v1-demo-banner', (el) => !el.hidden);
  !bannerOn ? ok('live ON: no "Demo data" banner') : fail('demo banner shown with desk_v1_live on');
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

  // An agent box with no live route must not run the R0 simulation: no
  // "Simulated reply", no made-up answer, an explicit "not connected" line.
  await page.evaluate((projectId) => window.deskV1Nav('project', { projectId }), PID);
  await settle(page, () => !!document.querySelector('#desk-v1-project-posy-input'));
  await page.fill('#desk-v1-project-posy-input', 'Pause everything');
  await page.click('[data-posy-send="desk-v1-project-posy-input"]');
  await settle(page, () => !!document.querySelector('[data-posy-not-connected]'));
  ok('live ON: an unwired agent box says it is not connected');
  const simText = await deskText(page);
  (!/Simulated reply|answered; nothing changed|Working on it/.test(simText)) ? ok('live ON: no simulated agent reply on an unwired box') : fail('simulated agent reply shown in live mode: ' + simText.slice(0, 200));

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
  const bannerErr = await page.$eval('#desk-v1-demo-banner', (el) => !el.hidden);
  !bannerErr ? ok('no demo banner under the error (a failed live load is not demo mode)') : fail('demo banner under a live error');

  mode = 'ok';
  await page.click('[data-store-retry]');
  await settle(page, () => /Server Project Zed/.test(document.body.innerText));
  ok('Try again re-reads M1 and renders the project');
  await ctx.close();
}

async function demoMode(browser) {
  const { ctx, page, hits } = await newPage(browser, { live: false, m1: () => ({ status: 500, body: {} }) });
  const banner = (p) => p.$eval('#desk-v1-demo-banner', (el) => ({ shown: !el.hidden && el.offsetHeight > 0, text: el.textContent.trim() }));
  await settle(page, () => window.DeskV1Store.demo());
  const home = await banner(page);
  (home.shown && home.text === 'Demo data - not your workspace')
    ? ok('live OFF: Home shows the banner "Demo data - not your workspace"') : fail('Home banner: ' + JSON.stringify(home));
  const names = await page.evaluate(() => window.DeskV1Fixtures.projects.map((p) => p.name));
  const t = await deskText(page);
  names.some((n) => t.includes(n)) ? ok('demo fixtures render under the banner') : fail('no demo project on Home');
  const gate = await page.$('[data-store-gate]');
  !gate ? ok('no store gate in demo mode') : fail('gate painted in demo mode');
  hits.m1 === 0 ? ok('M1 is not requested while the flag is off') : fail('M1 read with the flag off');

  const pid = await page.evaluate(() => window.DeskV1Fixtures.projects[0].id);
  await page.evaluate((projectId) => window.deskV1Nav('project', { projectId }), pid);
  await settle(page, () => /Back|Desk/.test(document.querySelector('.desk-v1-crumb').innerText) && !!document.querySelector('.desk-v1-body').children.length);
  const proj = await banner(page);
  proj.shown ? ok('banner persists on the project page') : fail('banner gone on the project page');
  const cid = await page.evaluate(() => window.DeskV1Fixtures.campaigns[0].id);
  await page.evaluate((campaignId) => window.deskV1Nav('campaign', { campaignId }), cid);
  await settle(page, () => !!document.getElementById('desk-v1-camp-tabstrip'));
  const camp = await banner(page);
  camp.shown ? ok('banner persists on a campaign page') : fail('banner gone on the campaign page');
  await ctx.close();
}

// Production since S10: the flag is off AND the page carries no fixtures. The
// store must say so, not paint an empty Desk or reach for demo data.
async function offNoFixtures(browser) {
  const { ctx, page, hits } = await newPage(browser, { live: false, fixtures: false, m1: () => ({ status: 200, body: WORKSPACE }) });
  await settle(page, () => !!document.querySelector('[data-store-gate="off"]'));
  ok('flag off, no fixtures: the shell paints the explicit "off" state');
  const t = await deskText(page);
  /nothing to show/.test(t) ? ok('the off state says there is nothing to show') : fail('off state text: ' + t);
  const fx = await page.evaluate(() => typeof window.DeskV1Fixtures);
  fx === 'undefined' ? ok('window.DeskV1Fixtures does not exist on the page') : fail('fixtures present on a production-shaped page');
  const banner = await page.$eval('#desk-v1-demo-banner', (el) => el.hidden);
  banner ? ok('no demo banner (nothing is being demoed)') : fail('demo banner shown with no fixtures');
  hits.m1 === 0 ? ok('M1 is not requested while the flag is off') : fail('M1 read with the flag off');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON, M1 answers'); await liveOk(browser);
  console.log('live ON, M1 fails'); await liveFails(browser);
  console.log('live OFF (demo mode)'); await demoMode(browser);
  console.log('live OFF, no fixtures (production shape)'); await offNoFixtures(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — live loads M1 and never shows demo data or the banner, a failed load is an explicit error, flag-off is labelled demo mode on every page, run() rolls back refusals.');
