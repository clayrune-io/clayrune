#!/usr/bin/env node
/**
 * Desk v1 (MC-977, Presence retired 2026-10-01) — the Connections screen.
 *
 * The one place anything external is connected, reached from the Desk header.
 * Social accounts: every workspace account with a status and a Connect /
 * Reconnect button; each X / LinkedIn account carries the "Read via" segmented
 * control (R1-E): Browser pane (free, the default) | X API (paid, ~$0.005 per
 * read). Generation engines is a placeholder heading only.
 * 2026-10-03 (Ron, from phone: "too cluttered"): the accounts are a grid of
 * compact TILES (one per account, status pill); selecting a tile opens that one
 * account's detail (status, Connect / Reconnect, Read via, profile) in a single
 * panel below the grid, one at a time, none selected = no panel. Asserts:
 *   - the tiles: 3-4 per row at 1440, 2 per row at 390, a re-auth / not
 *     connected tile is visibly flagged, nothing selected = no panel, selecting
 *     a second tile replaces the first, clicking the open tile closes it;
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
 * 2026-10-03 round 2 (Ron: "still too cluttered ... a mix of tiles and rows"): ONE
 * grid for everything connected (accounts, content sources, engines, saved services);
 * what is not connected is NOT shown (no Reddit / YouTube preview, no Dropbox, no
 * engine placeholder); the last tile is always "Add service", one searchable list +
 * "Something else" (saved for agents, never "Connected"). Asserts: the exact tile
 * set, no placeholders, Add service present and last, no per-kind sections or rows
 * outside the panel, Connect via a newly added account, Something else saves and
 * renders, and the tile name does not repeat the platform glyph.
 * Screenshots: docs/desk_v1/screens/connections_v2_{1440,390}.png
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
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
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
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 4000 });
  return { ctx, page, pageErrors };
}

// Open one account's detail panel (a no-op when it is already the open one).
async function selectTile(page, id) {
  if ((await page.getAttribute(`[data-conn-tile="${id}"]`, 'aria-pressed')) !== 'true') await page.click(`[data-conn-tile="${id}"]`);
  await page.waitForSelector(`[data-conn-detail="${id}"] [data-conn-account="${id}"]`, { timeout: 4000 });
}

const pressed = (page, ch) => page.$eval(
  `[data-readvia-row="${ch}"] [aria-pressed="true"]`, (b) => b.dataset.readvia).catch(() => null);

async function run(browser) {
  // -- 1440: control, default, click, coverage, refusal, profile ----------------
  srv.coverage = [{ platform: 'x', state: 'not_connected', via: 'pane',
                    message: 'Not connected (sign in to X in the browser pane)' }];
  const { ctx, page, pageErrors } = await openConnections(browser, { width: 1440, height: 900 });

  // -- the screen itself: accounts, statuses, Connect, placeholder --------------
  // The tile grid: layout, flags, one panel at a time.
  const tiles = await page.$$eval('[data-conn-tile]', (els) => els.map((e) => [e.dataset.connTile, e.dataset.connState]));
  const state = Object.fromEntries(tiles);
  const ids = tiles.map((t) => t[0]);
  check(JSON.stringify(ids) === JSON.stringify(['ch-x-ron', 'ch-li-page', 'ch-blog', 'ch-x-clayrune', 'source:yt', 'source:gdrive', 'source:gphotos'])
        && state['ch-x-ron'] === 'ok' && state['ch-li-page'] === 'reauth' && Object.values(state).every((v) => v === 'ok' || v === 'reauth'),
        `the grid is only what is connected or needs attention: ${ids.length} tiles (${JSON.stringify(state)})`, `tiles wrong: ${JSON.stringify(tiles)}`);
  const hidden = await page.evaluate(() => ['[data-conn-tile="ch-reddit"]', '[data-conn-tile="ch-yt-clayrune"]', '[data-conn-tile="source:dropbox"]', '[data-conn-tile^="engine:"]']
    .filter((sel) => document.querySelector(sel)));
  check(hidden.length === 0, 'no unconnected placeholder is rendered (Reddit, YouTube, Dropbox, engines)', `placeholders rendered: ${JSON.stringify(hidden)}`);
  const addPos = await page.$$eval('[data-conn-tiles] > *', (els) => els.map((e) => e.hasAttribute('data-conn-add-tile')));
  check(addPos.length === ids.length + 1 && addPos[addPos.length - 1] && addPos.filter(Boolean).length === 1
        && (await page.textContent('[data-conn-add-tile]')).includes('Add service'),
        'one "Add service" tile, and it is the last tile', `Add service tile missing or not last: ${JSON.stringify(addPos)}`);
  const kinds = await page.$$eval('[data-conn-tile]', (els) => els.map((e) => [e.dataset.connKind, e.querySelector('.desk-v1-conn-tile-kind').textContent.trim()]));
  check(kinds.every((k) => k[0] && k[1]) && kinds[0][1] === 'Social account' && kinds[4][1] === 'Content source',
        'every tile carries a small kind label (Social account / Content source)', `kind labels wrong: ${JSON.stringify(kinds)}`);
  const structure = await page.evaluate(() => ({
    sections: document.querySelectorAll('[data-conn-section], .desk-v1-conn-section').length,
    rowsOutsidePanel: [...document.querySelectorAll('[data-conn-account], [data-conn-source], [data-conn-engine]')].filter((e) => !e.closest('[data-conn-detail]')).length,
  }));
  check(structure.sections === 0 && structure.rowsOutsidePanel === 0, 'no per-kind sections and no rows: tiles only, until one is opened',
        `mixed layout still there: ${JSON.stringify(structure)}`);
  const names = await page.$$eval('[data-conn-tile] .desk-v1-conn-tile-name', (els) => els.map((e) => e.textContent.trim()));
  check(names.every((n) => !/^(?:\u{1D54F}|X|in)\s*[·•]/u.test(n)) && names.includes('@ron'),
        `the tile name does not repeat the platform mark (${names.slice(0, 3).join(' | ')})`, `a tile name repeats the mark: ${JSON.stringify(names)}`);
  const perRow = await page.$$eval('[data-conn-tile]', (els) => {
    const tops = els.map((e) => Math.round(e.getBoundingClientRect().top));
    return tops.filter((t) => t === tops[0]).length;
  });
  check(perRow >= 3 && perRow <= 6, `1440px: ${perRow} tiles per row`, `1440px tiles per row wrong: ${perRow}`);
  const flags = await page.$$eval('[data-conn-tile]', (els) => Object.fromEntries(els.map((e) => [e.dataset.connTile,
    [getComputedStyle(e).borderLeftWidth, getComputedStyle(e.querySelector('[data-conn-tile-status]')).color]])));
  check(flags['ch-li-page'][0] === '4px' && flags['ch-x-ron'][0] !== '4px' && flags['ch-li-page'][1] !== flags['ch-x-ron'][1],
        'a re-auth tile is flagged (heavy left edge, amber pill) and a connected one is not', `flags wrong: ${JSON.stringify(flags)}`);

  // Marks wear the vendor's brand colour behind a readable glyph (Ron 2026-10-03);
  // blog / unknown stay the neutral mark. Contrast is WCAG on the painted colours.
  const marks = await page.$$eval('[data-conn-tile]', (els) => {
    const lin = (c) => { c /= 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    const lum = (rgb) => 0.2126 * lin(rgb[0]) + 0.7152 * lin(rgb[1]) + 0.0722 * lin(rgb[2]);
    const parse = (s) => s.match(/\d+/g).slice(0, 3).map(Number);
    return Object.fromEntries(els.map((e) => {
      const m = e.querySelector('.desk-v1-conn-tile-mark'); const cs = getComputedStyle(m);
      const a = lum(parse(cs.backgroundColor)), b = lum(parse(cs.color));
      return [e.dataset.connTile, { bg: cs.backgroundColor, brand: m.dataset.brand || null, ratio: (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05) }];
    }));
  });
  check(marks['ch-x-ron'].bg === 'rgb(0, 0, 0)' && marks['ch-x-ron'].brand === 'x'
        && marks['ch-li-page'].bg === 'rgb(10, 102, 194)' && marks['ch-li-page'].brand === 'linkedin',
        'the X mark is #000000 and the LinkedIn mark is #0A66C2', `brand backgrounds wrong: ${JSON.stringify([marks['ch-x-ron'], marks['ch-li-page']])}`);
  check(marks['source:yt'].bg === 'rgb(255, 0, 0)' && marks['source:gdrive'].bg === 'rgb(26, 115, 232)' && marks['source:gphotos'].bg === 'rgb(234, 67, 53)',
        'YouTube source, Google Drive and Google Photos marks carry their brand colours', `source marks wrong: ${JSON.stringify([marks['source:yt'], marks['source:gdrive'], marks['source:gphotos']])}`);
  check(marks['ch-blog'].brand === null,
        'the blog mark stays the neutral mark (no brand colour)', `blog mark not neutral: ${JSON.stringify(marks['ch-blog'])}`);
  const weak = Object.entries(marks).filter(([, m]) => m.ratio < 4.5);
  check(weak.length === 0, `every mark's glyph reads at WCAG AA (min ${Math.min(...Object.values(marks).map((m) => m.ratio)).toFixed(2)}:1)`, `marks under 4.5:1: ${JSON.stringify(weak)}`);

  // Add service: the border is solid in every state (a dotted edge read as broken).
  const addBorder = () => page.$eval('[data-conn-add-tile]', (e) => { const s = getComputedStyle(e); return [s.borderTopStyle, s.borderRightStyle, s.borderBottomStyle, s.borderLeftStyle, s.outlineStyle].join('/'); });
  const solid = (s) => s.split('/').slice(0, 4).every((v) => v === 'solid');
  const states = { rest: await addBorder() };
  await page.hover('[data-conn-add-tile]'); states.hover = await addBorder();
  await page.mouse.move(0, 0);
  await page.keyboard.press('Tab'); await page.focus('[data-conn-add-tile]'); states.focus = await addBorder();
  await page.click('[data-conn-add-tile]'); await page.waitForSelector('[data-conn-detail="add"]', { timeout: 4000 });
  states.selected = await addBorder();
  await page.hover('[data-conn-add-tile]'); states.selectedHover = await addBorder();
  await page.mouse.move(0, 0);
  await page.screenshot({ path: resolve(SHOT_DIR, 'connections_v3_add_selected_1440.png') });
  check(Object.values(states).every(solid), 'the Add service border is solid at rest, hover, focus, selected', `Add service border not solid: ${JSON.stringify(states)}`);
  await page.click('[data-conn-add-tile]');
  await page.waitForFunction(() => !document.querySelector('[data-conn-detail]'), null, { timeout: 4000 });

  // Open each account in turn: the action it offers, and whether it carries Read via.
  const btns = {}; const readPlatforms = [];
  for (const [id] of tiles.filter((t) => !t[0].startsWith('source:'))) {
    await selectTile(page, id);
    check((await page.$$('[data-conn-detail]')).length === 1 && (await page.$$('[data-conn-account]')).length === 1,
          `selecting ${id}: exactly one detail panel is open`, `more than one detail panel open after selecting ${id}`);
    const a = await page.$('[data-conn-detail] [data-conn-action]');
    if (a) btns[id] = (await a.textContent()).trim();
    const r = await page.$('[data-conn-detail] [data-readvia-row]');
    if (r) readPlatforms.push(await r.getAttribute('data-platform'));
  }
  check(btns['ch-li-page'] === 'Reconnect' && btns['ch-x-ron'] === 'Reconnect',
        `Reconnect on the connected / lapsed accounts (${JSON.stringify(btns)})`, `buttons wrong: ${JSON.stringify(btns)}`);
  await page.click('[data-conn-tile="source:gdrive"]');
  await page.waitForSelector('[data-conn-detail="source:gdrive"] [data-conn-source="gdrive"]', { timeout: 4000 });
  ok('a content source tile opens its own detail below the grid');

  // Add service -> a social account (demo: nothing is saved): it appears flagged as
  // needing a connection, and Connect flips it (with nothing authenticated).
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-conn-detail="add"] [data-add-service] [data-add-list]', { timeout: 4000 });
  const pickList = await page.$$eval('[data-add-pick]', (els) => els.map((e) => e.dataset.addPick));
  check(pickList.includes('account:x') && pickList.includes('account:linkedin') && pickList.includes('account:blog') && pickList[pickList.length - 1] === 'other'
        && pickList.includes('account:youtube') && pickList.includes('account:instagram') && pickList.includes('account:tiktok')
        && !pickList.some((k) => /dropbox|gdrive|reddit/i.test(k)),
        `Add service lists what Clayrune can connect, then "Something else" last (${pickList.join(', ')})`, `pick list wrong: ${JSON.stringify(pickList)}`);
  await page.fill('[data-add-search]', 'link');
  const shown = await page.$$eval('[data-add-pick]', (els) => els.filter((e) => !e.parentElement.hidden).map((e) => e.dataset.addPick));
  check(shown.join() === 'account:linkedin,other', 'the search narrows the one list (Something else is always offered)', `search wrong: ${JSON.stringify(shown)}`);
  await page.fill('[data-add-search]', 'zzzz');
  check(await page.$eval('[data-add-nomatch]', (e) => !e.hidden), 'nothing matches: it says to pick Something else', 'no-match line missing');
  await page.fill('[data-add-search]', '');
  await page.click('[data-add-pick="account:x"]');
  await page.waitForSelector('[data-conn-add]', { timeout: 4000 });
  await page.fill('[data-conn-add-identity]', '@newbie');
  await page.click('[data-conn-add-submit]');
  await page.waitForSelector('[data-conn-tile][data-conn-state="off"]', { timeout: 4000 });
  const newId = await page.$eval('[data-conn-tile][data-conn-state="off"]', (e) => e.dataset.connTile);
  check((await page.getAttribute(`[data-conn-tile="${newId}"]`, 'aria-pressed')) === 'true'
        && (await page.$$eval('[data-conn-tiles] > *', (e) => e[e.length - 1].hasAttribute('data-conn-add-tile'))),
        'an added account is a flagged (not connected) tile, selected, and Add service is still last', 'added account not shown as expected');
  await page.waitForSelector(`[data-conn-detail="${newId}"] [data-conn-action="${newId}"]`, { timeout: 4000 });
  btns[newId] = (await page.textContent(`[data-conn-action="${newId}"]`)).trim();
  check(btns[newId] === 'Connect', 'a not-connected account offers Connect', `new account button: ${btns[newId]}`);
  await page.click(`[data-conn-action="${newId}"]`);
  await page.waitForFunction((id) => (document.querySelector(`[data-conn-tile="${id}"]`) || {}).dataset.connState === 'ok', newId, { timeout: 4000 });
  ok('Connect makes the tile Connected (preview: nothing is authenticated)');

  // Add service -> Something else: a name, a link, a credential NAME. Honest status, no value asked.
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-add-pick="other"]', { timeout: 4000 });
  await page.click('[data-add-pick="other"]');
  await page.waitForSelector('[data-svc-add]', { timeout: 4000 });
  check(!(await page.$('[data-svc-add] input[type="password"]')), 'Something else never asks for a secret value (a vault entry NAME only)', 'a password field is on the Something else form');
  await page.fill('[data-svc-add-name]', 'Plausible analytics');
  await page.fill('[data-svc-add-link]', 'https://plausible.io');
  await page.fill('[data-svc-add-cred]', 'plausible.key');
  await page.click('[data-svc-add-submit]');
  await page.waitForSelector('[data-conn-tile^="service:"]', { timeout: 4000 });
  const svc = await page.$eval('[data-conn-tile^="service:"]', (e) => ({ name: e.querySelector('.desk-v1-conn-tile-name').textContent.trim(),
    pill: e.querySelector('[data-conn-tile-status]').textContent.trim(), state: e.dataset.connState, kind: e.querySelector('.desk-v1-conn-tile-kind').textContent.trim() }));
  check(svc.name === 'Plausible analytics' && svc.pill === 'Saved for agents' && svc.state === 'saved',
        `Something else renders a tile that says "${svc.pill}", never Connected`, `service tile wrong: ${JSON.stringify(svc)}`);
  const honest = await page.$eval('[data-conn-detail] [data-svc-honest]', (e) => e.textContent);
  check(/does not connect to this service or post to it/.test(honest) && !(await page.$('[data-conn-detail] [data-conn-action]')),
        'its detail says Clayrune does not connect to it or post to it, and offers no Connect', `service detail wrong: ${honest}`);
  check((await page.$$eval('[data-conn-tiles] > *', (e) => e[e.length - 1].hasAttribute('data-conn-add-tile'))), 'Add service is still the last tile', 'Add service not last after saving a service');
  await page.screenshot({ path: resolve(SHOT_DIR, 'connections_v3_service_1440.png') });
  await page.click('[data-svc-remove]');
  await page.waitForFunction(() => !document.querySelector('[data-conn-tile^="service:"]'), null, { timeout: 4000 });
  ok('Remove takes the saved service off the grid');

  await selectTile(page, 'ch-x-ron');
  await page.click('[data-conn-detail-close]');
  check(!(await page.$('[data-conn-detail]')) && (await page.getAttribute('[data-conn-tile="ch-x-ron"]', 'aria-pressed')) === 'false',
        'the panel close button closes it and unpresses the tile', 'close button did nothing');
  await selectTile(page, 'ch-x-ron');
  await page.click('[data-conn-tile="ch-x-ron"]');
  check(!(await page.$('[data-conn-detail]')), 'clicking the open tile closes its detail', 'the detail stayed open after clicking its tile again');

  // Every X/LinkedIn account gets one (R2-10's fixture added a second X account);
  // no other platform does.
  check(readPlatforms.includes('x') && readPlatforms.includes('linkedin') && readPlatforms.every((p) => p === 'x' || p === 'linkedin'),
        'X and LinkedIn accounts carry the Read via control; the blog account does not',
        `Read via rows wrong: ${JSON.stringify(readPlatforms)}`);
  await selectTile(page, 'ch-x-ron');
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
  await page.screenshot({ path: resolve(SHOT_DIR, 'connections_v3_1440.png') });

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
  await page.waitForSelector('[data-conn-tile]', { timeout: 4000 });
  await selectTile(page, 'ch-x-ron');
  check(await pressed(page, 'ch-x-ron') === 'api', 'the Read via choice persists across leaving and re-opening the screen', `choice lost: ${await pressed(page, 'ch-x-ron')}`);

  await selectTile(page, 'ch-li-page');
  check(!(await page.$('[data-readvia-row="ch-li-page"] [data-readvia]')) && !!(await page.$('[data-readvia-row="ch-li-page"] [data-readvia-fixed]'))
        && !!(await page.$('[data-readvia-row="ch-li-page"] [data-readvia-profile]')),
        'LinkedIn: no API option (it has no API read), the browser pane alone, and it takes a profile',
        'LinkedIn row wrong');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail(`uncaught page error: ${e}`));
  await ctx.close();

  // -- 344: no overflow, screenshot ---------------------------------------------
  srv.coverage = [{ platform: 'x', state: 'not_connected', via: 'pane',
                    message: 'Not connected (sign in to X in the browser pane)' }];
  const m = await openConnections(browser, { width: 344, height: 760 });
  await selectTile(m.page, 'ch-x-ron');
  await m.page.$eval('[data-readvia-row="ch-x-ron"]', (e) => e.scrollIntoView({ block: 'center' }));
  const over = await m.page.evaluate(() => {
    const seg = document.querySelector('[data-readvia-row="ch-x-ron"] .desk-v1-conn-readvia-seg');
    const r = seg.getBoundingClientRect();
    const tiles = [...document.querySelectorAll('[data-conn-tile]')].map((e) => e.getBoundingClientRect());
    return { right: r.right, vw: window.innerWidth, doc: document.documentElement.scrollWidth, tileRight: Math.max(...tiles.map((t) => t.right)) };
  });
  check(over.right <= over.vw && over.tileRight <= over.vw && over.doc <= over.vw,
        `344px: control and tiles fit (right ${Math.round(over.right)}, tiles ${Math.round(over.tileRight)} <= ${over.vw}, scrollWidth ${over.doc})`,
        `344px overflow: ${JSON.stringify(over)}`);
  await m.ctx.close();

  // -- 390 (a phone): two tiles per row, selecting one shows its panel, screenshot --
  const p = await openConnections(browser, { width: 390, height: 844 });
  const perRow390 = await p.page.$$eval('[data-conn-tile]', (els) => {
    const tops = els.map((e) => Math.round(e.getBoundingClientRect().top));
    return tops.filter((t) => t === tops[0]).length;
  });
  check(perRow390 === 2, `390px: ${perRow390} tiles per row`, `390px tiles per row wrong: ${perRow390}`);
  await p.page.click('[data-conn-tile="ch-li-page"]');
  await p.page.waitForSelector('[data-conn-detail="ch-li-page"] [data-conn-action]', { timeout: 4000 });
  const vis = await p.page.$eval('[data-conn-detail]', (e) => { const r = e.getBoundingClientRect(); return r.top < window.innerHeight && r.bottom > 0; });
  check(vis, '390px: tapping a tile brings its detail panel into view', '390px: the detail panel is off screen after tapping a tile');
  await p.page.screenshot({ path: resolve(SHOT_DIR, 'connections_v3_390.png') });
  await p.ctx.close();
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
