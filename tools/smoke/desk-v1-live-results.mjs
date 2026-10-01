#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S3) — the ① Goal stop against a fake server,
 * `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * What is under test is which requests the browser makes and what it paints from
 * the answers. The numbers are the SERVER's (M11, GET /api/desk/campaigns/<id>/
 * results), so every figure below is one the fixtures do not hold:
 *   1. Read      -> opening the stop GETs .../results and paints the server's
 *                   current/target, not the fixture goal's own `current`.
 *   2. No data   -> a null `current` reads "No data yet", never "0 of 30"; a null
 *                   term reading reads "no data".
 *   3. Loading / failure -> the panel says "Loading results…" while the read is
 *                   out, and "Could not load results" (with Try again) when it
 *                   fails; it never falls back to the goal object or the fixtures.
 *   4. Manual entry -> PATCH {goal} (whole goal, entries included) lands BEFORE the
 *                   re-read, and the panel shows the server's new number. A refused
 *                   save rolls the entry back and says why.
 *   5. Field edit -> a target change PATCHes the goal and re-reads.
 *   6. Flag OFF  -> adding an entry makes 0 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-live-results.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// The fake server's own M11: current is the newest entry's value, null with none.
// (The real derivation is mc.desk.campaign_results, pinned by tests/test_desk_results.py;
// this stand-in only has to be a function of what the browser PATCHed.) The `+100`
// marks every figure as server-made, so "the panel painted the server's number"
// cannot be satisfied by the fixture's own `current`.
function resultsOf(c) {
  const g = c.goal || {};
  const entries = (g.source === 'manual' && g.entries) || [];
  const last = entries.reduce((a, b) => (!a || new Date(b.at) > new Date(a.at) ? b : a), null);
  const terms = (c.terms || []).map((t) => ({ index: t.index, current: t.index === 1 ? null : 7, target: 15 }));
  return {
    campaign_id: c.id,
    goal: { current: last ? last.value + 100 : null, target: g.target == null ? null : g.target, deadline: g.deadline || null, source: g.source || null, freshness: last ? last.at : null },
    forecast: null, terms, versions: [], costs: { ledger: null },
  };
}

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  // camp-1 is the active campaign under test. Its goal reads 11 in the fixture; the
  // server's own reading of the same entries is 111, so the page can only show 111
  // by asking the server.
  const srv = { log: [], next: {}, campaigns, fx, holdResults: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

async function newPage(browser, { live, srv }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    srv.log.push({ method, path, search: url.search, body });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    let m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/results$/);
    if (m && method === 'GET') {
      const c = srv.campaigns.find((x) => x.id === m[1]);
      if (!c) return J({ error: 'campaign not found' }, 404);
      if (srv.holdResults) await srv.holdResults;
      return J(resultsOf(c));
    }
    m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const c = srv.campaigns.find((x) => x.id === m[1]);
      if (!c) return J({ error: 'campaign not found' }, 404);
      Object.assign(c, body);
      return J(c);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const calls = (srv, method, pathRe) => srv.log.filter((r) => r.method === method && pathRe.test(r.path));
const text = (page, sel) => page.textContent(sel).then((t) => (t || '').trim()).catch(() => '');
const openGoal = async (page, id) => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate((cid) => window.deskV1Nav('results', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-goal'));
};
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

// ── 1: read ────────────────────────────────────────────────────────────────
async function read(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openGoal(page, 'camp-1');
  await settle(page, () => !!document.querySelector('.desk-v1-goal-progress-number'));
  const num = await text(page, '.desk-v1-goal-progress-number');
  const tgt = await text(page, '.desk-v1-goal-progress-target');
  (num === '111' && tgt === 'of 30 tester signups')
    ? ok(`the panel paints the SERVER's reading: "${num}" ${tgt} (the fixture goal says 11)`) : fail(`progress: ${JSON.stringify({ num, tgt })}`);
  const gets = calls(srv, 'GET', /^\/api\/desk\/campaigns\/camp-1\/results$/);
  gets.length >= 1 ? ok(`opening the stop GETs the results route (${gets.length}x)`) : fail('no results GET');
  const demoBanner = await page.evaluate(() => /Demo data/.test(document.body.innerText));
  !demoBanner ? ok('no demo banner in live mode') : fail('demo banner shown in live mode');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 2: no data is not 0 ────────────────────────────────────────────────────
async function noData(browser) {
  const srv = makeServer();
  const c1 = srv.campaigns.find((c) => c.id === 'camp-1');
  c1.goal.entries = [];
  c1.goal.current = 0;               // a stale stored zero must not reach the screen either
  c1.goal.horizon = 'long';
  c1.terms = [{ index: 1, starts: '2026-07-01', ends: '2026-08-30' }, { index: 2, starts: '2026-09-01', ends: '2026-10-20' }];
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openGoal(page, 'camp-1');
  await settle(page, () => !!document.querySelector('.desk-v1-goal-nodata'));
  const msg = await text(page, '.desk-v1-goal-nodata');
  /^No data yet/.test(msg) ? ok(`no entries reads "${msg}"`) : fail('no-data copy: ' + msg);
  const number = await page.$('.desk-v1-goal-progress-number');
  !number ? ok('no progress number renders') : fail('a progress number rendered with no data');
  const body = await text(page, '.desk-v1-goal-effectiveness');
  /\b0 of\b/.test(body) ? fail('panel reads "0 of": ' + body) : ok('the panel never reads "0 of"');
  const rows = await page.$$eval('.desk-v1-goal-term-row', (els) => els.map((e) => e.textContent.trim()));
  (rows.length === 2 && rows[0] === 'Term 1: no data (target 15)' && rows[1] === 'Term 2: 7 of 15')
    ? ok(`per-term rows come from the server, a null reading says "no data": ${JSON.stringify(rows)}`) : fail('term rows: ' + JSON.stringify(rows));
  await ctx.close();
}

// ── 3: loading, then a failure that does not fall back ─────────────────────
async function loadingAndFailure(browser) {
  const srv = makeServer();
  let release;
  srv.holdResults = new Promise((r) => { release = r; });
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openGoal(page, 'camp-1');
  await settle(page, () => !!document.querySelector('.desk-v1-goal-loading'));
  const loadingText = await text(page, '.desk-v1-goal-loading');
  const during = await page.$('.desk-v1-goal-progress-number');
  (/Loading results/.test(loadingText) && !during)
    ? ok('while the read is out the panel says "Loading results…" and shows no number') : fail('loading state: ' + JSON.stringify({ loadingText, during: !!during }));
  srv.holdResults = null; release();
  await settle(page, () => !!document.querySelector('.desk-v1-goal-progress-number'));

  // A failing read: explicit error, no number from anywhere else, Try again recovers.
  const srv2 = makeServer();
  srv2.next['GET /api/desk/campaigns/camp-1/results'] = 'results are unavailable';
  const second = await newPage(browser, { live: true, srv: srv2 });
  await settle(second.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openGoal(second.page, 'camp-1');
  await settle(second.page, () => !!document.querySelector('.desk-v1-goal-loaderror'));
  const err = await text(second.page, '.desk-v1-goal-loaderror');
  const leaked = await second.page.$('.desk-v1-goal-progress-number');
  (/Could not load results: results are unavailable/.test(err) && !leaked)
    ? ok(`a failed read says why and paints no number: "${err}"`) : fail('error state: ' + JSON.stringify({ err, leaked: !!leaked }));
  await second.page.click('[data-goal-retry]');
  await settle(second.page, () => !!document.querySelector('.desk-v1-goal-progress-number'));
  ok('Try again reads the route again and paints the answer');
  await second.ctx.close();
  await ctx.close();
}

// ── 4: manual entry ────────────────────────────────────────────────────────
async function manualEntry(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openGoal(page, 'camp-1');
  await settle(page, () => !!document.querySelector('.desk-v1-goal-progress-number'));

  srv.log.length = 0;
  await page.fill('[data-manual-entry-date]', '2026-09-28');
  await page.fill('[data-manual-entry-value]', '14');
  await page.click('[data-manual-entry-add]');
  await settle(page, () => document.querySelector('.desk-v1-goal-progress-number')?.textContent.trim() === '114');
  const patch = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  const get = calls(srv, 'GET', /^\/api\/desk\/campaigns\/camp-1\/results$/);
  (patch.length === 1 && patch[0].search === '?shape=v1' && Object.keys(patch[0].body).join() === 'goal'
    && patch[0].body.goal.entries.length === 2 && patch[0].body.goal.entries[1].value === 14 && patch[0].body.goal.entries[1].at === '2026-09-28')
    ? ok('the entry is one PATCH {goal} carrying the whole entry list') : fail('entry PATCH: ' + JSON.stringify(patch));
  (get.length >= 1 && srv.log.indexOf(patch[0]) < srv.log.indexOf(get[0]))
    ? ok("results are read again AFTER the PATCH, and the panel shows the server's new number (114)") : fail('order: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));

  // A refused save puts the entry back, says why, and the panel keeps the old reading.
  srv.next['PATCH /api/desk/campaigns/camp-1'] = 'goal.entries must be a list of at most 2000';
  await page.fill('[data-manual-entry-value]', '99');
  await page.click('[data-manual-entry-add]');
  await settle(page, () => /was not saved: goal.entries must be a list of at most 2000/.test(document.body.innerText));
  const entries = await page.evaluate(() => window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1').goal.entries.map((e) => e.value));
  const shown = await text(page, '.desk-v1-goal-progress-number');
  (JSON.stringify(entries) === '[11,14]' && shown === '114')
    ? ok('a refused entry is rolled back (entries [11,14]) and the panel still reads 114') : fail('after refusal: ' + JSON.stringify({ entries, shown }));
  await ctx.close();
}

// ── 5: field edit ──────────────────────────────────────────────────────────
async function fieldEdit(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openGoal(page, 'camp-1');
  await settle(page, () => !!document.querySelector('.desk-v1-goal-progress-number'));
  srv.log.length = 0;
  await page.fill('[data-goal-field="target"]', '40');
  await page.press('[data-goal-field="target"]', 'Tab');
  await settle(page, () => /of 40 tester signups/.test(document.querySelector('.desk-v1-goal-progress-target')?.textContent || ''));
  const patch = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  (patch.length === 1 && patch[0].body.goal.target === 40 && patch[0].body.goal.metric === 'tester signups')
    ? ok('a target edit PATCHes the goal and the panel re-reads (of 40)') : fail('target PATCH: ' + JSON.stringify(patch));
  await ctx.close();
}

// ── 6: flag OFF ────────────────────────────────────────────────────────────
async function demoCallsNothing(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => window.DeskV1Store.demo());
  await openGoal(page, 'camp-1');
  const num = await text(page, '.desk-v1-goal-progress-number');
  srv.log.length = 0;
  await page.fill('[data-manual-entry-date]', '2026-09-28');
  await page.fill('[data-manual-entry-value]', '14');
  await page.click('[data-manual-entry-add]');
  await page.fill('[data-goal-field="target"]', '40');
  await page.press('[data-goal-field="target"]', 'Tab');
  await page.waitForTimeout(300);
  const after = await text(page, '.desk-v1-goal-progress-number');
  (num === '11' && after === '14') ? ok('demo mode paints the fixture goal (11) and a new entry moves it locally (14)') : fail('demo numbers: ' + JSON.stringify({ num, after }));
  srv.log.length === 0 ? ok('flag OFF: a manual entry and a target edit made 0 /api/desk/* requests') : fail('demo mode called the server: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: read'); await read(browser);
  console.log('live ON: no data'); await noData(browser);
  console.log('live ON: loading + failure'); await loadingAndFailure(browser);
  console.log('live ON: manual entry'); await manualEntry(browser);
  console.log('live ON: field edit'); await fieldEdit(browser);
  console.log('live OFF: demo'); await demoCallsNothing(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log("\n✅ PASS — S3 the Goal stop reads the server's results, shows no data as no data, PATCHes goal edits and re-reads, rolls back refusals, and demo mode calls nothing.");
