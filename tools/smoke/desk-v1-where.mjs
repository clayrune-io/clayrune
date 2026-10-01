#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision 2, docs/THE_DESK_V1_IA_REVISION_2.md §8 R2-10
 * row) — the ⑤ Where board (frames 7a/7b, §11.6 Q1), static/js/desk-v1-where.js.
 *
 * Checks:
 *   - Render, all 3 tones: Messages column lists every piece of the campaign
 *     with an `on N channels` pill; one column per account the campaign uses
 *     (avatar + platform badge + handle + platform + ✕); version cards read
 *     `<kind> → <platform format>` and NO time (R2-19: times are When's); the rule line sits under the
 *     board; SOURCES lists one card per connected account (the 2 X accounts
 *     stay 2 cards with distinct handles), YouTube/Discord carry the
 *     `Preview · not connected` label, Reddit is `not connected` + `Connect ›`.
 *   - The retired campaign Add tray's Channels shelf is gone.
 *   - Drag 1: a message onto @ron ADDS a version (message stays listed, pill
 *     +1, card `Article → X thread`); toast + Undo restores.
 *   - Drag 2: that card @ron -> Clayrune Page MOVES it (total unchanged).
 *   - Drag 3: a source dragged up adds its column. On an Active campaign that
 *     widens the bounds -> Launch reads `Awaiting approval`; ✕ on an
 *     approved account's column keeps approval; a preview-only source
 *     (YouTube) drags and drops like the rest.
 *   - Keyboard equivalents for all three drags (focus + Enter -> menu).
 *   - A new campaign's Where shows Messages + 0 account columns; `Connect ›`
 *     lands on the project's Presence page.
 *   - Phone (390 px): the board scrolls inside itself, the page does not;
 *     ✕ and Connect are ≥ 44 px tall.
 *
 * R2-7 shipped the 6-piece fixture (camp-1) these counts follow — frame 7b's:
 * Install 3, Agent 1, Home 2, Where board 1, Retro 2 (the restore-points
 * article), Post 2; @ron 5, Clayrune Page 4, Clayrune blog 2. The EXPECT block
 * below is the ONE place to re-point if the fixture moves. Pills read
 * `DeskV1Kit.pieceChannelsText`, the same helper What's rows use.
 *
 * Real headless boot (real index.html + real static/js|css, no network).
 *
 * RUN   cd tools/smoke && node desk-v1-where.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

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
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1where';
const PROJECTS = [{
  id: PID, name: 'Desk v1 where smoke', status: 'active', domain: 'general', emoji: '🧪',
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

const TONES = [
  { name: 'default/dark', ls: {} },
  { name: 'tone-warm', ls: { mc_tone: 'warm' } },
  { name: 'tone-editorial', ls: { mc_tone: 'editorial' } },
];

// The one place that knows the current fixture's numbers (see header).
const RETRO = 'Undo anything: restore points in Clayrune 2.1'; // frame 7b's `Retro` piece
const POST = '30 Windows testers wanted'; // frame 7b's `Post` piece
const EXPECT = {
  messages: { 'Undo anything: restore points in Clayrune 2.1': 2, 'Install in two minutes': 3, '30 Windows testers wanted': 2, 'Agent live run': 1, 'Home status table': 2, 'Where board': 1 },
  columns: { 'ch-x-ron': 5, 'ch-li-page': 4, 'ch-blog': 2 },
  sources: ['ch-x-ron', 'ch-li-page', 'ch-blog', 'ch-x-clayrune', 'ch-yt-clayrune', 'ch-discord-community'],
  offSources: ['ch-reddit'],
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

async function fulfillOrAbort(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: 'UTC' });
  if (path === '/api/characters') return J([]);
  if (path === '/api/floor') return J({ bench: [] });
  return route.abort();
}

async function newBootedPage(browser, tone, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  await page.addInitScript((ls) => {
    try { for (const k of Object.keys(ls)) localStorage.setItem(k, ls[k]); } catch (e) {}
  }, (tone && tone.ls) || {});
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function gotoWhere(page, campaignId) {
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id, panel: 'where' }), campaignId || 'camp-1');
  await page.waitForSelector('.desk-v1-where[data-where]', { timeout: 6000 });
}

// ── reads ────────────────────────────────────────────────────────────────
async function readBoard(page) {
  return page.evaluate(() => {
    const txt = (el) => (el ? el.textContent.replace(/\s+/g, ' ').trim() : '');
    const messages = [...document.querySelectorAll('[data-where-message]')].map((m) => ({
      familyId: m.dataset.familyId,
      title: txt(m.querySelector('.desk-v1-where-msg-title')),
      pill: txt(m.querySelector('[data-where-pill]')),
    }));
    const columns = [...document.querySelectorAll('.desk-v1-where-col')].map((c) => ({
      channelId: c.dataset.channelId,
      handle: txt(c.querySelector('.desk-v1-where-handle')),
      platform: txt(c.querySelector('.desk-v1-where-plat')),
      hasBadge: !!c.querySelector('.desk-v1-where-avatar-badge'),
      hasRemove: !!c.querySelector('[data-where-remove]'),
      preview: !!c.querySelector('[data-preview]'),
      empty: txt(c.querySelector('[data-where-colempty]')),
      versions: [...c.querySelectorAll('[data-where-version]')].map((v) => ({
        versionId: v.dataset.versionId,
        familyId: v.dataset.familyId,
        title: txt(v.querySelector('.desk-v1-where-vtitle')),
        format: txt(v.querySelector('.desk-v1-where-vformat')),
        text: txt(v),
        timeEls: v.querySelectorAll('.desk-v1-where-vtime, .desk-v1-where-vplat, .desk-v1-where-notime').length,
      })),
    }));
    const sources = [...document.querySelectorAll('[data-where-source]')].map((s) => ({
      channelId: s.dataset.channelId,
      handle: txt(s.querySelector('.desk-v1-where-source-handle')),
      preview: !!s.querySelector('[data-preview]'),
    }));
    const off = [...document.querySelectorAll('[data-where-source-off]')].map((s) => ({
      channelId: s.dataset.channelId,
      text: txt(s),
      connect: !!s.querySelector('[data-where-connect]'),
      draggable: s.hasAttribute('tabindex'),
    }));
    return { messages, columns, sources, off, rule: txt(document.querySelector('[data-where-rule]')), nocols: !!document.querySelector('[data-where-nocols]') };
  });
}

async function versionTotal(page, campaignId) {
  return page.evaluate((id) => window.DeskV1Fixtures.families
    .filter((f) => f.campaignId === id)
    .reduce((n, f) => n + f.versions.filter((v) => v.channelId && v.state !== 'archived' && v.state !== 'skipped').length, 0), campaignId);
}

async function launchStatus(page) {
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  // R2-11: a Live campaign's Launch reads its Live head instead of the old
  // status line; the Awaiting-approval branch keeps the status line.
  await page.waitForSelector('.desk-v1-map-launch-status, [data-launch-live]', { timeout: 4000 });
  return (await page.textContent('.desk-v1-map-launch-status, .desk-v1-launch-live-head').catch(() => '') || '').trim();
}

async function backToWhere(page) {
  await page.click('.desk-v1-map-stop[data-stop="where"]');
  await page.waitForSelector('.desk-v1-where[data-where]', { timeout: 4000 });
}

// Real pointer drag: mouse down on the source, cross the slop, release over
// the target's centre. Both ends are scrolled into view first (the drag
// engine has no auto-scroll), and the target is re-measured after the first
// move because the ghost/hover classes can shift layout.
async function drag(page, fromSel, toSel, opts) {
  const from = page.locator(fromSel).first();
  const to = page.locator(toSel).first();
  await to.scrollIntoViewIfNeeded(); // the board scrolls sideways past 3 accounts; Messages is sticky
  await from.scrollIntoViewIfNeeded();
  const fb = await from.boundingBox();
  if (!fb) throw new Error(`drag source not visible: ${fromSel}`);
  await page.mouse.move(fb.x + fb.width / 2, fb.y + fb.height / 2);
  await page.mouse.down();
  await page.mouse.move(fb.x + fb.width / 2 + 14, fb.y + fb.height / 2 + 14, { steps: 4 });
  const tb = await to.boundingBox();
  if (!tb) throw new Error(`drag target not visible: ${toSel}`);
  // Aim at the part of the target the tab body actually shows: the body is its
  // own scroll region, so a target scrolled half under its top edge is only
  // hittable where it overlaps the body's box.
  const body = await page.locator('#desk-v1-camp-tabbody').boundingBox();
  const top = Math.max(tb.y, body ? body.y : tb.y);
  const bottom = Math.min(tb.y + tb.height, body ? body.y + body.height : tb.y + tb.height);
  const tx = tb.x + tb.width / 2;
  const ty = top + Math.min((bottom - top) / 2, (opts && opts.maxY) || 120);
  await page.mouse.move(tx, ty, { steps: 10 });
  await page.waitForTimeout(40);
  await page.mouse.up();
  await page.waitForTimeout(120);
}

const col = (b, id) => b.columns.find((c) => c.channelId === id);
const msg = (b, title) => b.messages.find((m) => m.title === title);

// ── 1. render, every tone ────────────────────────────────────────────────
async function runRender(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await gotoWhere(page);
  const b = await readBoard(page);
  const tag = `[${tone.name}]`;

  check(JSON.stringify(Object.fromEntries(b.messages.map((m) => [m.title, 1]))) === JSON.stringify(Object.fromEntries(Object.keys(EXPECT.messages).map((t) => [t, 1]))),
    `${tag} Messages lists all ${b.messages.length} pieces of the campaign`,
    `${tag} Messages wrong: ${JSON.stringify(b.messages.map((m) => m.title))}`);
  const pillBad = Object.entries(EXPECT.messages).filter(([t, n]) => (msg(b, t) || {}).pill !== `on ${n} channel${n === 1 ? '' : 's'}`);
  check(pillBad.length === 0, `${tag} every Messages pill equals its live version count (${Object.values(EXPECT.messages).join(',')})`,
    `${tag} pills wrong: ${JSON.stringify(pillBad.map(([t]) => [t, (msg(b, t) || {}).pill]))}`);
  check(JSON.stringify(b.columns.map((c) => c.channelId)) === JSON.stringify(Object.keys(EXPECT.columns)),
    `${tag} one column per account the campaign uses: ${b.columns.map((c) => c.handle).join(' · ')}`,
    `${tag} columns wrong: ${JSON.stringify(b.columns.map((c) => c.channelId))}`);
  const cntBad = Object.entries(EXPECT.columns).filter(([id, n]) => (col(b, id) || { versions: [] }).versions.length !== n);
  check(cntBad.length === 0, `${tag} column card counts ${Object.values(EXPECT.columns).join(' / ')}`, `${tag} column counts wrong: ${JSON.stringify(cntBad)}`);
  check(b.columns.every((c) => c.hasBadge && c.hasRemove && c.handle && c.platform),
    `${tag} every column header has avatar + platform badge + handle + platform + ✕`,
    `${tag} a column header is missing a part: ${JSON.stringify(b.columns)}`);
  const ron = col(b, 'ch-x-ron');
  const installX = ron.versions.find((v) => v.title === 'Install in two minutes');
  check(installX && installX.format === 'Video → X clip',
    `${tag} version card reads "${installX && installX.format}"`,
    `${tag} version card wrong: ${JSON.stringify(installX)}`);
  // R2-19: Where owns channel placement only. No card in any column carries a
  // time element, a `Wed 10:00` / `No time yet` text, or an hh:mm anywhere.
  const allCards = b.columns.flatMap((c) => c.versions);
  const timed = allCards.filter((v) => v.timeEls > 0 || /\b\d{1,2}:\d{2}\b|\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b|No time yet/.test(v.text));
  check(allCards.length > 0 && timed.length === 0,
    `${tag} R2-19: none of the ${allCards.length} Where version cards carries a time`,
    `${tag} R2-19: Where cards still show a time: ${JSON.stringify(timed)}`);
  check(/Dragging from Messages always ADDS a version/.test(b.rule) && /MOVES it/.test(b.rule),
    `${tag} rule line under the board: "${b.rule}"`, `${tag} rule line wrong: ${JSON.stringify(b.rule)}`);

  check(JSON.stringify(b.sources.map((s) => s.channelId)) === JSON.stringify(EXPECT.sources),
    `${tag} SOURCES: one card per connected account (${b.sources.length})`, `${tag} sources wrong: ${JSON.stringify(b.sources.map((s) => s.channelId))}`);
  const xs = b.sources.filter((s) => s.channelId === 'ch-x-ron' || s.channelId === 'ch-x-clayrune');
  check(xs.length === 2 && new Set(xs.map((s) => s.handle)).size === 2,
    `${tag} 2 X accounts render as 2 source cards with distinct handles: ${xs.map((s) => s.handle).join(', ')}`,
    `${tag} X source cards wrong: ${JSON.stringify(xs)}`);
  const prev = b.sources.filter((s) => s.preview).map((s) => s.channelId).sort();
  check(JSON.stringify(prev) === JSON.stringify(['ch-discord-community', 'ch-yt-clayrune']),
    `${tag} YouTube + Discord carry "Preview · not connected"; X/LinkedIn/blog do not`, `${tag} preview labels wrong: ${JSON.stringify(prev)}`);
  check(b.off.length === 1 && /not connected/.test(b.off[0].text) && b.off[0].connect && !b.off[0].draggable,
    `${tag} the not-connected card reads "${b.off[0] && b.off[0].text}" and is not draggable`, `${tag} off card wrong: ${JSON.stringify(b.off)}`);

  const tray = await page.$$eval('#desk-v1-camp-addtray-channels', (els) => els.length);
  check(tray === 0, `${tag} the campaign Add tray has no Channels shelf`, `${tag} Channels shelf still rendered`);

  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// ── 2. the three drags (pointer), Active campaign ─────────────────────────
async function runDrags(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await gotoWhere(page);
  const total0 = await versionTotal(page, 'camp-1');

  // Drag 1: message -> @ron ADDS a version; the message stays listed.
  await drag(page, '[data-where-message][data-family-id="fam-restore-points"]', '.desk-v1-where-col[data-channel-id="ch-x-ron"]');
  let b = await readBoard(page);
  const ron = col(b, 'ch-x-ron');
  const armInRon = ron.versions.find((v) => v.familyId === 'fam-restore-points');
  check(!!armInRon && armInRon.format === 'Article → X thread',
    `drag 1: Messages → @ron adds a card reading "${armInRon && armInRon.format}"`, `drag 1: no X-thread card on @ron: ${JSON.stringify(ron.versions)}`);
  check(!!msg(b, RETRO) && msg(b, RETRO).pill === 'on 3 channels',
    'drag 1: the message is still in Messages, pill now "on 3 channels"', `drag 1: message/pill wrong: ${JSON.stringify(msg(b, RETRO))}`);
  check(ron.versions.length === EXPECT.columns['ch-x-ron'] + 1 && (await versionTotal(page, 'camp-1')) === total0 + 1,
    `drag 1: @ron ${ron.versions.length} cards, campaign total ${total0} → ${total0 + 1}`, 'drag 1: counts wrong');
  const undoVisible = await page.$$eval('.toast', (els) => els.some((e) => /Undo/.test(e.textContent) && /Undo anything/.test(e.textContent)));
  check(undoVisible, 'drag 1: a toast with Undo names the piece', 'drag 1: no Undo toast');

  // Drag 2: that card @ron -> Clayrune Page MOVES it; total unchanged.
  const cardSel = `[data-where-version][data-version-id="${armInRon.versionId}"]`;
  await drag(page, cardSel, '.desk-v1-where-col[data-channel-id="ch-li-page"]');
  b = await readBoard(page);
  const moved = col(b, 'ch-li-page').versions.find((v) => v.versionId === armInRon.versionId);
  check(!!moved && moved.format === 'Article → LinkedIn native article' && !col(b, 'ch-x-ron').versions.some((v) => v.versionId === armInRon.versionId),
    `drag 2: @ron → Clayrune Page moves it ("${moved && moved.format}"), gone from @ron`, `drag 2: move wrong: ${JSON.stringify(moved)}`);
  check((await versionTotal(page, 'camp-1')) === total0 + 1 && msg(b, RETRO).pill === 'on 3 channels',
    'drag 2: total unchanged by a move; the pill still says "on 3 channels"', 'drag 2: a move changed the total');

  // A published version cannot be moved.
  const pubBefore = col(b, 'ch-x-ron').versions.filter((v) => v.title === 'Install in two minutes').length;
  await drag(page, '[data-where-version][data-version-id="v-install-x"]', '.desk-v1-where-col[data-channel-id="ch-li-page"]');
  b = await readBoard(page);
  check(col(b, 'ch-x-ron').versions.some((v) => v.versionId === 'v-install-x') && pubBefore === 1,
    'drag 2b: an already-published version stays put (refused with a toast)', 'drag 2b: a published version moved');

  // Drag 3: source -> board on an ACTIVE campaign: new column + Awaiting approval.
  const status0 = await launchStatus(page);
  check(!/Awaiting approval/.test(status0), `drag 3: before the drag Launch reads "${status0}"`, `drag 3: already awaiting before any change: ${status0}`);
  await backToWhere(page);
  await drag(page, '[data-where-source][data-channel-id="ch-x-clayrune"]', '[data-where-messages]');
  b = await readBoard(page);
  const added = col(b, 'ch-x-clayrune');
  check(!!added && added.versions.length === 0 && /Drag a message here to add its X version\./.test(added.empty),
    `drag 3: @clayrune source → board adds an EMPTY column: "${added && added.empty}"`, `drag 3: column wrong: ${JSON.stringify(added)}`);
  const status1 = await launchStatus(page);
  check(/Awaiting approval/.test(status1), `drag 3: on an Active campaign Launch reads "${status1}"`, `drag 3: Launch not awaiting: ${JSON.stringify(status1)}`);
  await backToWhere(page);
  const aw = await page.$('[data-where-awaiting]');
  check(!!aw, 'drag 3: the Where stop carries the same Awaiting-approval notice', 'drag 3: no awaiting notice on Where');

  // Preview-only account behaves like any other: drag up, drop a message, remove.
  await drag(page, '[data-where-source][data-channel-id="ch-yt-clayrune"]', '[data-where-messages]');
  await drag(page, '[data-where-message][data-family-id="fam-install-video"]', '.desk-v1-where-col[data-channel-id="ch-yt-clayrune"]');
  b = await readBoard(page);
  const yt = col(b, 'ch-yt-clayrune');
  check(!!yt && yt.preview && yt.versions.length === 1 && yt.versions[0].format === 'Video → YouTube upload',
    `preview-only account: column tagged "Preview · not connected", took a drop ("${yt && yt.versions[0] && yt.versions[0].format}")`, `preview-only account wrong: ${JSON.stringify(yt)}`);

  reportUncaught(pageErrors, '[drags]');
  await ctx.close();
}

// ── 3. ✕ narrows and keeps approval ───────────────────────────────────────
async function runRemove(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await gotoWhere(page);
  const pendingBefore = await page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.campaignId === 'camp-1')
    .flatMap((f) => f.versions).filter((v) => v.channelId === 'ch-blog' && v.state !== 'archived').length);
  await page.click('[data-where-remove][data-channel-id="ch-blog"]');
  await page.waitForTimeout(80);
  let b = await readBoard(page);
  check(!col(b, 'ch-blog') && b.columns.length === 2,
    '✕ removes the Clayrune blog column (2 left); the source card stays in SOURCES', `✕ did not remove the column: ${JSON.stringify(b.columns.map((c) => c.channelId))}`);
  check(b.sources.some((s) => s.channelId === 'ch-blog'), '✕: the account is still offered in SOURCES', '✕: the source card vanished');
  const status = await launchStatus(page);
  check(!/Awaiting approval/.test(status), `✕ on an Active campaign is narrowing — approval kept, Launch reads "${status}"`, `✕ wrongly reopened approval: ${JSON.stringify(status)}`);
  await backToWhere(page);
  // Undo (the toast's own) restores the column and every archived version.
  const undoBtn = page.locator('.toast button', { hasText: 'Undo' }).first();
  await undoBtn.click();
  await page.waitForTimeout(100);
  b = await readBoard(page);
  const pendingAfter = await page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.campaignId === 'camp-1')
    .flatMap((f) => f.versions).filter((v) => v.channelId === 'ch-blog' && v.state !== 'archived').length);
  check(!!col(b, 'ch-blog') && pendingAfter === pendingBefore,
    `Undo brings the column back with its ${pendingAfter} version(s)`, `Undo wrong: column=${!!col(b, 'ch-blog')} versions ${pendingBefore} → ${pendingAfter}`);
  reportUncaught(pageErrors, '[remove]');
  await ctx.close();
}

// ── 4. keyboard equivalents ───────────────────────────────────────────────
async function runKeyboard(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await gotoWhere(page);

  // Source: focus + Enter adds the column (and keeps focus on that source).
  await page.focus('[data-where-source][data-channel-id="ch-x-clayrune"]');
  await page.keyboard.press('Enter');
  await page.waitForTimeout(100);
  let b = await readBoard(page);
  check(!!col(b, 'ch-x-clayrune'), 'keyboard: Enter on a source card adds its column', 'keyboard: Enter on a source added nothing');
  const focused = await page.evaluate(() => document.activeElement && document.activeElement.dataset && document.activeElement.dataset.channelId);
  check(focused === 'ch-x-clayrune', 'keyboard: focus stays on that source card after the add', `keyboard: focus lost (${focused})`);

  // Message: Enter opens a menu of the board's accounts; pick @clayrune.
  await page.focus('[data-where-message][data-family-id="fam-30-testers"]');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  const items = await page.$$eval('.desk-v1-addto-menu button', (els) => els.map((e) => e.textContent.trim()));
  check(items.length === 4 && items.some((t) => /@clayrune/.test(t)), `keyboard: message menu lists the ${items.length} account columns`, `keyboard: message menu wrong: ${JSON.stringify(items)}`);
  await page.locator('.desk-v1-addto-menu button', { hasText: '@clayrune' }).click();
  await page.waitForTimeout(100);
  b = await readBoard(page);
  const fp = col(b, 'ch-x-clayrune').versions.find((v) => v.familyId === 'fam-30-testers');
  check(!!fp && fp.format === 'Post → X post' && msg(b, POST).pill === 'on 3 channels',
    `keyboard: message → account adds "${fp && fp.format}", message stays listed (on 3 channels)`, `keyboard: message add wrong: ${JSON.stringify(fp)}`);
  const focusMsg = await page.evaluate(() => document.activeElement && document.activeElement.dataset && document.activeElement.dataset.familyId);
  check(focusMsg === 'fam-30-testers', 'keyboard: focus returns to the message card', `keyboard: focus not restored (${focusMsg})`);

  // Version: Enter opens "Move to…" (never the account it is already on).
  await page.focus(`[data-where-version][data-version-id="${fp.versionId}"]`);
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  const mv = await page.$$eval('.desk-v1-addto-menu button', (els) => els.map((e) => e.textContent.trim()));
  check(mv.length === 3 && !mv.some((t) => /@clayrune/.test(t)), `keyboard: version menu offers the ${mv.length} OTHER accounts`, `keyboard: version menu wrong: ${JSON.stringify(mv)}`);
  await page.locator('.desk-v1-addto-menu button', { hasText: '@ron' }).click();
  await page.waitForTimeout(100);
  b = await readBoard(page);
  check(!!col(b, 'ch-x-ron').versions.find((v) => v.versionId === fp.versionId) && col(b, 'ch-x-clayrune').versions.length === 0,
    'keyboard: version → @ron moves it (old column empty again)', 'keyboard: move wrong');
  reportUncaught(pageErrors, '[keyboard]');
  await ctx.close();
}

// ── 5. a new campaign: Messages + 0 columns; Connect › ─────────────────────
async function runNewCampaign(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await page.click('.desk-v1-home-block-name[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  const id = await page.evaluate(() => { const c = window.DeskV1Fixtures.campaigns; return c[c.length - 1].id; });
  await page.click('.desk-v1-map-stop[data-stop="where"]');
  await page.waitForSelector('.desk-v1-where[data-where]', { timeout: 4000 });
  const b = await readBoard(page);
  check(b.columns.length === 0 && b.nocols, `new campaign (${id}): Where shows 0 account columns + the empty-board line`, `new campaign: ${b.columns.length} columns`);
  check(!!(await page.$('[data-where-messages]')), 'new campaign: the Messages column is there', 'new campaign: Messages column missing');
  check(b.sources.length === EXPECT.sources.length, `new campaign: SOURCES offers the project's ${b.sources.length} connected accounts`, `new campaign: ${b.sources.length} sources`);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_10_where_empty_1440.png') });

  // Source → board on a DRAFT adds the column without any approval state.
  await drag(page, '[data-where-source][data-channel-id="ch-x-ron"]', '[data-where-messages]');
  const b2 = await readBoard(page);
  check(b2.columns.length === 1 && b2.columns[0].channelId === 'ch-x-ron' && !(await page.$('[data-where-awaiting]')),
    'draft: a source dragged up adds a column and never shows Awaiting approval', `draft: ${JSON.stringify(b2.columns.map((c) => c.channelId))}`);

  // Connect › → the Connections screen (the one place accounts are connected).
  await page.click('[data-where-connect]');
  await page.waitForSelector('[data-connections] [data-conn-account="ch-reddit"]', { timeout: 4000 });
  ok('Connect › on the not-connected card lands on the Connections screen');
  reportUncaught(pageErrors, '[new-campaign]');
  await ctx.close();
}

// ── 6. phone ─────────────────────────────────────────────────────────────
async function runPhone(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await gotoWhere(page);
  const m = await page.evaluate(() => {
    const board = document.querySelector('[data-where-board]');
    const rm = document.querySelector('[data-where-remove]');
    const cn = document.querySelector('[data-where-connect]');
    return {
      // MC-977 WH-1: on phone the board STACKS (Messages above full-width columns)
      // instead of scrolling sideways, so nothing may overflow it horizontally.
      boardStacks: getComputedStyle(board).flexDirection === 'column' && board.scrollWidth <= board.clientWidth + 4,
      pageOverflow: document.documentElement.scrollWidth > window.innerWidth + 2,
      removeH: Math.round(rm.getBoundingClientRect().height),
      connectH: Math.round(cn.getBoundingClientRect().height),
    };
  });
  check(m.boardStacks && !m.pageOverflow, `phone: the board stacks (no sideways scroll), the page does not scroll sideways (${JSON.stringify(m)})`, `phone: overflow wrong ${JSON.stringify(m)}`);
  check(m.removeH >= 44 && m.connectH >= 44, `phone: ✕ ${m.removeH}px and Connect › ${m.connectH}px tall (≥ 44)`, `phone: tap targets too small ${JSON.stringify(m)}`);
  await page.waitForTimeout(80);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_10_where_phone_390.png') });
  ok('phone screenshot saved: r2_10_where_phone_390.png');
  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

async function captureDesktop(browser) {
  const { ctx, page } = await newBootedPage(browser, { ls: {} });
  await gotoWhere(page);
  await page.waitForTimeout(80);
  await page.screenshot({ path: resolve(SHOT_DIR, 'r2_10_where_1440.png') });
  ok('desktop screenshot saved: r2_10_where_1440.png');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runRender(browser, tone);
  await runDrags(browser);
  await runRemove(browser);
  await runKeyboard(browser);
  await runNewCampaign(browser);
  await runPhone(browser);
  await captureDesktop(browser);
  if (bad) { console.error(`\n${bad} check(s) failed.`); exitCode = 1; } else { console.log('\nAll checks passed.'); exitCode = 0; }
} catch (e) {
  console.error('HARNESS ERROR:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
process.exit(exitCode);
