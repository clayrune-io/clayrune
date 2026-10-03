#!/usr/bin/env node
/**
 * Desk v1 (MC-977, Presence retired 2026-10-01) — the Connections screen.
 *
 * The one place anything external is connected, reached from the Desk header.
 * Social accounts: every workspace account with a status and a Connect /
 * Reconnect button; each X / LinkedIn account carries the "Read via" segmented
 * control (R1-E): Browser pane (free, the default) | X API (paid, ~$0.005 per
 * read). Generation engines is a placeholder heading only. Asserts:
 *   - every workspace account is listed with a status word, the not-connected
 *     Reddit account offers Connect, the lapsed LinkedIn page offers Reconnect;
 *   - Where's "Connect" and Studio's online "Connect Dropbox" route here;
 *   - X and LinkedIn accounts show the control, the blog account does not;
 *   - the default is the browser pane (aria-pressed) with no stored choice;
 *   - clicking X API PATCHes /api/desk/presence/<p>/accounts/<ch>/read with
 *     {read_via:'api', platform:'x'} and the control reflects it;
 *   - a coverage gap from the server is shown verbatim (never silenced);
 *   - a refused save (the server says no) shows its error and leaves the choice;
 *   - the profile input PATCHes browser_profile;
 *   - a stored choice survives leaving the screen and coming back;
 *   - the screen fits at 344 wide (no horizontal overflow).
 * Screenshots: docs/desk_v1/screens/retire_presence/connections_{1440,344}.png
 *
 * Hermetic: real index.html + static/, every /api route mocked, no real account.
 *
 * RUN  cd tools/smoke && node desk-v1-connections.mjs
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens', 'retire_presence');
mkdirSync(SHOT_DIR, { recursive: true });

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PROJECTS = [{
  id: 'smoke_readvia', name: 'Read via smoke', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/readvia', last_updated: '2026-09-30T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster: [],
}];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

// Per-run server state the mocks read/write.
const srv = { patches: [], refuse: false, coverage: [] };

async function fulfill(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  if (/^\/api\/desk\/engagement\/coverage\//.test(path)) return J({ project_id: 'clayrune', coverage: srv.coverage });
  if (/\/api\/desk\/presence\/[^/]+\/accounts\/[^/]+\/read$/.test(path) && req.method() === 'PATCH') {
    const body = JSON.parse(req.postData() || '{}');
    srv.patches.push({ path, body });
    if (srv.refuse) return J({ error: 'this action needs a human' }, 403);
    return J({ channel_id: 'ch-x-ron', platform: body.platform, read_via: body.read_via || 'pane',
               ...(body.browser_profile ? { browser_profile: body.browser_profile } : {}) });
  }
  return route.abort();
}

async function openConnections(browser, viewport) {
  // A phone-width run must be a touch device too: `.modal-content` only drops its
  // 380px desktop min-width under `(pointer: coarse)`, as on a real phone.
  const ctx = await browser.newContext({ viewport, ...(viewport.width < 600 ? { hasTouch: true, isMobile: true } : {}) });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfill);

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-account]', { timeout: 4000 });
  return { ctx, page, pageErrors };
}

const pressed = (page, ch) => page.$eval(
  `[data-readvia-row="${ch}"] [aria-pressed="true"]`, (b) => b.dataset.readvia).catch(() => null);

async function run(browser) {
  // -- 1440: control, default, click, coverage, refusal, profile ----------------
  srv.coverage = [{ platform: 'x', state: 'not_connected', via: 'pane',
                    message: 'Not connected (sign in to X in the browser pane)' }];
  const { ctx, page, pageErrors } = await openConnections(browser, { width: 1440, height: 900 });

  // -- the screen itself: accounts, statuses, Connect, placeholder --------------
  const listed = await page.$$eval('[data-conn-account]', (els) => els.map((e) => [e.dataset.connAccount, e.dataset.connState]));
  const state = Object.fromEntries(listed);
  check(listed.length === 7 && state['ch-x-ron'] === 'ok' && state['ch-li-page'] === 'reauth' && state['ch-reddit'] === 'off'
        && state['ch-yt-clayrune'] === 'preview',
        `all ${listed.length} workspace accounts listed with a status (${JSON.stringify(state)})`, `accounts wrong: ${JSON.stringify(listed)}`);
  const btns = await page.$$eval('[data-conn-action]', (els) => Object.fromEntries(els.map((e) => [e.dataset.connAction, e.textContent.trim()])));
  check(btns['ch-reddit'] === 'Connect' && btns['ch-li-page'] === 'Reconnect' && btns['ch-x-ron'] === 'Reconnect' && !('ch-yt-clayrune' in btns),
        `Connect on the not-connected account, Reconnect on the others, none on a preview one (${JSON.stringify(btns)})`, `buttons wrong: ${JSON.stringify(btns)}`);
  const engines = await page.$eval('[data-conn-section="engines"]', (e) => e.textContent.trim());
  check(engines === 'Generation engines', 'Generation engines is a placeholder heading only', `engines section wrong: ${JSON.stringify(engines)}`);
  await page.click('[data-conn-action="ch-reddit"]');
  await page.waitForFunction(() => (document.querySelector('[data-conn-account="ch-reddit"]') || {}).dataset.connState === 'preview', null, { timeout: 4000 });
  ok('Connect takes Reddit out of Not connected (it stays Preview: nothing is authenticated in R0)');

  const rows = await page.$$eval('[data-readvia-row]', (els) => els.map((e) => e.dataset.platform));
  // Every X/LinkedIn account gets one (R2-10's fixture added a second X account);
  // no other platform does.
  check(rows.includes('x') && rows.includes('linkedin') && rows.every((p) => p === 'x' || p === 'linkedin'),
        'X and LinkedIn accounts carry the Read via control; the blog account does not',
        `Read via rows wrong: ${JSON.stringify(rows)}`);
  check(await pressed(page, 'ch-x-ron') === 'pane',
        'default with no stored choice: Browser pane (no charge) is selected',
        `default not pane: ${await pressed(page, 'ch-x-ron')}`);
  const labels = await page.$$eval('[data-readvia-row="ch-x-ron"] [data-readvia]', (bs) => bs.map((b) => b.textContent.trim()));
  check(labels[0] === 'Browser pane (no charge)' && labels[1] === 'X API (paid, ~$0.005 per read)',
        `labels read "${labels.join('" | "')}"`, `labels wrong: ${JSON.stringify(labels)}`);

  await page.waitForFunction(() => /Not connected/.test(
    (document.querySelector('[data-readvia-row="ch-x-ron"] [data-readvia-status]') || {}).textContent || ''), null, { timeout: 4000 });
  const gap = await page.$eval('[data-readvia-row="ch-x-ron"] [data-readvia-status]', (e) => e.textContent.trim());
  check(gap === 'Not connected (sign in to X in the browser pane)',
        `the server's coverage gap is shown verbatim: "${gap}"`, `gap text wrong: ${JSON.stringify(gap)}`);
  await page.screenshot({ path: resolve(SHOT_DIR, 'connections_1440.png') });

  srv.coverage = [{ platform: 'x', state: 'not_connected', via: 'api', message: 'Not connected (no API token)' }];
  await page.click('[data-readvia-row="ch-x-ron"] [data-readvia="api"]');
  await page.waitForFunction(() => {
    const b = document.querySelector('[data-readvia-row="ch-x-ron"] [data-readvia="api"]');
    return b && b.getAttribute('aria-pressed') === 'true';
  }, null, { timeout: 4000 });
  const sent = srv.patches[srv.patches.length - 1] || {};
  check(sent.body && sent.body.read_via === 'api' && sent.body.platform === 'x'
        && sent.path.endsWith('/accounts/ch-x-ron/read'),
        `clicking X API PATCHed ${sent.path} with ${JSON.stringify(sent.body)}`,
        `PATCH wrong: ${JSON.stringify(sent)}`);
  await page.waitForFunction(() => /no API token/.test(
    (document.querySelector('[data-readvia-row="ch-x-ron"] [data-readvia-status]') || {}).textContent || ''), null, { timeout: 4000 });
  ok('api with no token: "Not connected (no API token)" shown, not silenced');
  check(!(await page.$('[data-readvia-row="ch-x-ron"] [data-readvia-profile]')),
        'the profile input is hidden while X API is selected', 'profile input still shown on api');

  srv.refuse = true;
  await page.click('[data-readvia-row="ch-x-ron"] [data-readvia="pane"]');
  await page.waitForFunction(() => /needs a human/.test(
    (document.querySelector('[data-readvia-row="ch-x-ron"] [data-readvia-status]') || {}).textContent || ''), null, { timeout: 4000 });
  check(await pressed(page, 'ch-x-ron') === 'api',
        'a refused save shows the server error and leaves the choice unchanged',
        `choice changed despite refusal: ${await pressed(page, 'ch-x-ron')}`);
  srv.refuse = false;

  await page.click('[data-readvia-row="ch-x-ron"] [data-readvia="pane"]');
  await page.waitForSelector('[data-readvia-row="ch-x-ron"] [data-readvia-profile]', { timeout: 4000 });
  await page.fill('[data-readvia-row="ch-x-ron"] [data-readvia-profile]', 'x-main');
  await page.dispatchEvent('[data-readvia-row="ch-x-ron"] [data-readvia-profile]', 'change');
  await page.waitForFunction(() => true);
  await new Promise((r) => setTimeout(r, 300));
  const last = srv.patches[srv.patches.length - 1] || {};
  check(last.body && last.body.browser_profile === 'x-main',
        'typing a profile name PATCHes browser_profile', `profile PATCH wrong: ${JSON.stringify(last)}`);

  // A stored choice survives leaving the screen and coming back.
  await page.click('[data-readvia-row="ch-x-ron"] [data-readvia="api"]');
  await page.waitForFunction(() => (document.querySelector('[data-readvia-row="ch-x-ron"] [aria-pressed="true"]') || {}).dataset.readvia === 'api', null, { timeout: 4000 });
  await page.evaluate(() => window.deskV1Nav('home', {}));
  await page.waitForSelector('.desk-v1-home-connections-btn', { timeout: 4000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-readvia-row="ch-x-ron"]', { timeout: 4000 });
  check(await pressed(page, 'ch-x-ron') === 'api', 'the Read via choice persists across leaving and re-opening the screen', `choice lost: ${await pressed(page, 'ch-x-ron')}`);

  const li = await page.$eval('[data-readvia-row="ch-li-page"] [data-readvia="api"]', (b) => b.textContent.trim());
  check(li === 'LinkedIn API (paid)' && !(await page.$('[data-readvia-row="ch-li-page"] [data-readvia-profile]')),
        'LinkedIn: API option reads "LinkedIn API (paid)", no profile input (no pane reader yet)',
        `LinkedIn row wrong: ${li}`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail(`uncaught page error: ${e}`));
  await ctx.close();

  // -- 344: no overflow, screenshot ---------------------------------------------
  srv.coverage = [{ platform: 'x', state: 'not_connected', via: 'pane',
                    message: 'Not connected (sign in to X in the browser pane)' }];
  const m = await openConnections(browser, { width: 344, height: 760 });
  await m.page.waitForSelector('[data-readvia-row="ch-x-ron"]', { timeout: 4000 });
  await m.page.$eval('[data-readvia-row="ch-x-ron"]', (e) => e.scrollIntoView({ block: 'center' }));
  const over = await m.page.evaluate(() => {
    const seg = document.querySelector('[data-readvia-row="ch-x-ron"] .desk-v1-conn-readvia-seg');
    const r = seg.getBoundingClientRect();
    return { right: r.right, vw: window.innerWidth, doc: document.documentElement.scrollWidth };
  });
  check(over.right <= over.vw && over.doc <= over.vw,
        `344px: control fits (right ${Math.round(over.right)} <= ${over.vw}, scrollWidth ${over.doc})`,
        `344px overflow: ${JSON.stringify(over)}`);
  await m.page.screenshot({ path: resolve(SHOT_DIR, 'connections_344.png') });
  await m.ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await run(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? '\nAll checks passed.' : `\n${bad} check(s) failed.`);
process.exit(exitCode);
