#!/usr/bin/env node
/**
 * Desk v1 (44712cf4 follow-up) - any site's account reads through the browser pane.
 * Connections against a fake server, `desk_v1_live` ON, at 1440 and 390.
 *
 * What is under test is what the browser shows and sends (the routes are pinned by
 * tests/test_desk_account_read_pages.py):
 *   1. A YouTube / Instagram account carries Read via with the browser pane as its ONLY
 *      route (no API button), and takes a signed-in profile (PATCH browser_profile).
 *   2. X and LinkedIn keep their pane / API choice; a blog carries no Read via at all.
 *   3. The activity-page address is never asked first: no block while discovery is fine.
 *      When the coverage says `pages_needed` the block appears with the reason, takes one
 *      https address (PATCH read_pages [{role:'activity', url}]), lists it, and Remove
 *      sends the list without it. A non-https address is refused in the page, no request.
 *   4. A refused save (server error) shows the server's words and keeps the list as it was.
 *   5. At 390 nothing overflows sideways and the input and its button are on screen.
 *
 * RUN   cd tools/smoke && node desk-v1-live-read-pages.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const NOT_READY = { ready: false, reason: 'no publisher for this site', secret: null, unattended_ok: null };
const ADDR = 'https://studio.youtube.com/channel/UC123/comments';

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const srv = { log: [], refuse: null, coverage: [], fx };
  srv.accounts = fx.channels.filter((c) => ['x', 'linkedin', 'blog'].includes(c.platform))
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.accounts.push(
    { id: 'ch-yt', platform: 'youtube', identity: 'UC123', label: 'YouTube · UC123', capability: 'manual', voice: '', connected: false, publish: NOT_READY },
    { id: 'ch-ig', platform: 'instagram', identity: 'clayrune', label: 'Instagram · clayrune', capability: 'manual', voice: '', connected: false, publish: NOT_READY });
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, accounts: srv.accounts, pieces: [] });
  return srv;
}

async function open(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
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
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/browser/agent-read') return J({ profiles: {} });
    if (path === '/api/browser/profiles') return J({ profiles: [] });
    if (/^\/api\/desk\/engagement\/coverage\//.test(path)) return J({ project_id: 'p', coverage: srv.coverage });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines') return J({ engines: [] });
    if (path === '/api/desk/services') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    const m = path.match(/^\/api\/desk\/accounts\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const acc = srv.accounts.find((x) => x.id === m[1]);
      if (!acc) return J({ error: 'account not found' }, 404);
      if (srv.refuse) return J({ error: srv.refuse }, 400);
      const { project_id: _p, ...rest } = body;
      Object.assign(acc, rest);
      if (Array.isArray(acc.read_pages) && !acc.read_pages.length) delete acc.read_pages;
      return J(acc);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const selectTile = async (page, id) => {
  if ((await page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed')) !== 'true') await page.click(`[data-conn-tile="${id}"]`);
  await page.waitForSelector(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`, { timeout: 6000 });
};
const patches = (srv, id) => srv.log.filter((r) => r.method === 'PATCH' && r.path === `/api/desk/accounts/${id}`);
const realErrors = (errs) => errs.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));

async function run(browser, viewport, label) {
  console.log(label);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await open(browser, srv, viewport);
  const R = '[data-conn-account="ch-yt"]';

  // 1-2: which accounts carry Read via, and which routes they offer
  await selectTile(page, 'ch-yt');
  check(await page.$(`${R} [data-readvia-row][data-platform="youtube"] [data-readvia-fixed]`)
        && !(await page.$(`${R} [data-readvia]`)) && !!(await page.$(`${R} [data-readvia-profile]`)),
        'YouTube: Read via is the browser pane alone (no API button) and takes a profile', 'YouTube Read via row wrong');
  await selectTile(page, 'ch-ig');
  check(!!(await page.$('[data-conn-account="ch-ig"] [data-readvia-fixed]')) && !(await page.$('[data-conn-account="ch-ig"] [data-readvia]')),
        'Instagram: the browser pane alone', 'Instagram Read via row wrong');
  await selectTile(page, 'ch-x-ron');
  const xl = await page.$$eval('[data-conn-account="ch-x-ron"] [data-readvia]', (bs) => bs.map((b) => b.textContent.trim()));
  check(xl.length === 2 && xl[0] === 'Browser pane (no charge)' && /^X API/.test(xl[1]),
        'X keeps its pane / API choice', `X buttons: ${JSON.stringify(xl)}`);
  await selectTile(page, 'ch-blog');
  check(!(await page.$('[data-conn-account="ch-blog"] [data-readvia-row]')), 'a blog carries no Read via', 'blog shows Read via');

  // profile
  await selectTile(page, 'ch-yt');
  check(!(await page.$(`${R} [data-readpages-for]:not([hidden])`)),
        'no address field while discovery has not asked for one', 'the address block showed before pages_needed');
  await page.fill(`${R} [data-readvia-profile]`, 'yt-main');
  await page.dispatchEvent(`${R} [data-readvia-profile]`, 'change');
  await page.waitForFunction(() => true);
  await new Promise((r) => setTimeout(r, 300));
  const pp = patches(srv, 'ch-yt');
  check(pp.length === 1 && pp[0].body.browser_profile === 'yt-main' && typeof pp[0].body.project_id === 'string' && !('read_pages' in pp[0].body),
        'typing a profile PATCHes browser_profile only, filed under a project', `profile PATCH: ${JSON.stringify(pp.map((p) => p.body))}`);

  // 3: pages_needed asks once
  srv.coverage = [{ platform: 'youtube', state: 'not_read_yet', via: 'pane', message: 'Connected, not read yet on YouTube',
    pages: { source: 'none', status: 'pages_needed', reason: 'studio.youtube.com start page offered no usable links', pages: [] } }];
  await page.fill(`${R} [data-readvia-profile]`, 'yt-main2');
  await page.dispatchEvent(`${R} [data-readvia-profile]`, 'change');
  await page.press(`${R} [data-readvia-profile]`, 'Tab');   // the blur fires the browser's own change: let that save and repaint finish
  await page.waitForSelector(`${R} [data-readpages-for]:not([hidden]) [data-readpages-input]`, { timeout: 6000 });
  await new Promise((r) => setTimeout(r, 400));
  const why = await page.$eval(`${R} [data-readpages-why]`, (e) => e.textContent);
  check(/could not find the page/.test(why) && /start page offered no usable links/.test(why),
        'pages_needed shows the block with the reader\'s reason', `why text: ${why}`);
  const before = patches(srv, 'ch-yt').length;
  await page.fill(`${R} [data-readpages-input]`, 'http://studio.youtube.com/x');
  await page.click(`${R} [data-readpages-add]`);
  check((await page.$eval(`${R} [data-readpages-status]`, (e) => e.textContent)).includes('https://') && patches(srv, 'ch-yt').length === before,
        'a non-https address is refused in the page, nothing is sent', 'http address was sent or not refused');
  await page.fill(`${R} [data-readpages-input]`, ADDR);
  await page.click(`${R} [data-readpages-add]`);
  await page.waitForSelector(`${R} [data-readpages-page="0"]`, { timeout: 6000 });
  const sent = patches(srv, 'ch-yt').pop();
  check(JSON.stringify(sent.body.read_pages) === JSON.stringify([{ role: 'activity', url: ADDR }]),
        'one https address PATCHes read_pages [{role: activity, url}]', `read_pages PATCH: ${JSON.stringify(sent.body)}`);
  check((await page.$eval(`${R} [data-readpages-page="0"]`, (e) => e.textContent)).includes(ADDR),
        'the saved address is listed', 'saved address not listed');

  // 4: refusal keeps the list
  srv.refuse = 'this action needs a human: an unattended agent session cannot choose how an account is read';
  await page.fill(`${R} [data-readpages-input]`, 'https://studio.youtube.com/second');
  await page.click(`${R} [data-readpages-add]`);
  await page.waitForFunction((sel) => /needs a human/.test((document.querySelector(sel) || {}).textContent || ''), `${R} [data-readvia-status]`, { timeout: 6000 });
  check((await page.$$(`${R} [data-readpages-page]`)).length === 1, 'a refused save shows the server\'s words and keeps the list as it was', 'list changed on refusal');
  srv.refuse = null;

  // 5: layout
  const box = await page.$eval(`${R} [data-readpages-for]`, (host) => {
    const q = (s) => host.querySelector(s).getBoundingClientRect();
    const i = q('[data-readpages-input]'); const b = q('[data-readpages-add]');
    return { inputL: i.left, inputW: i.width, btnR: b.right, vw: window.innerWidth, scroll: document.documentElement.scrollWidth };
  });
  check(box.inputL >= 0 && box.btnR <= box.vw && box.inputW >= 100 && box.scroll <= box.vw,
        `input and Save fit at ${viewport.width}px (input ${Math.round(box.inputW)}px wide, no sideways scroll)`, `layout: ${JSON.stringify(box)}`);
  await page.screenshot({ path: resolve(REPO_ROOT, '_scratch', `read_pages_${viewport.width}.png`) });

  // remove sends the list without it
  await page.click(`${R} [data-readpages-remove="0"]`);
  await page.waitForFunction((sel) => !document.querySelector(sel), `${R} [data-readpages-page]`, { timeout: 6000 });
  const rm = patches(srv, 'ch-yt').pop();
  check(Array.isArray(rm.body.read_pages) && rm.body.read_pages.length === 0, 'Remove PATCHes the list without it', `remove PATCH: ${JSON.stringify(rm.body)}`);

  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await run(browser, { width: 1440, height: 900 }, 'live ON, 1440');
  await run(browser, { width: 390, height: 844 }, 'live ON, 390');
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — a non-X site reads through the pane alone, takes a profile, and is asked for an activity page only once discovery needs it.');
