#!/usr/bin/env node
// C5: read-only placement overview; account selection, bounds, Undo and previews stay.
// Retired positive message-add/version-move/source-drag assertions are replaced
// by no-drag/no-menu invariants plus click/keyboard account-bound checks.
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', process.env.COMPOSER_SHOTS || 'composer_c5');
mkdirSync(SHOT_DIR, { recursive: true });

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
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
  sources: ['ch-x-ron', 'ch-li-page', 'ch-blog', 'ch-x-clayrune', 'ch-reddit'],
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

  await seedDeskV1Fixtures(page);
  await page.addInitScript(() => { const ch = window.DeskV1Fixtures.channels.find(c => c.id === 'ch-reddit'); ch.preview = false; ch.userAdded = true; });
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
      hasBadge: !!c.querySelector('[data-svc-icon]'),
      hasRemove: !!c.querySelector('[data-where-remove]'),
      preview: !!c.querySelector('[data-preview]'),
      empty: txt(c.querySelector('[data-where-colempty]')),
      posts: Number(c.querySelector('[data-where-post-count]')?.dataset.wherePostCount),
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
    `${tag} every column header has platform mark + handle + platform + ✕`,
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
  check(/edited in the composer/.test(b.rule), `${tag} composer owns assignments`, `${tag} stale placement guidance`);
  check(b.columns.every(c => c.posts === new Set(c.versions.map(v => v.familyId)).size), `${tag} counts distinct live posts per account`, `${tag} incorrect post count`);


  check(JSON.stringify(b.sources.map((s) => s.channelId)) === JSON.stringify(EXPECT.sources),
    `${tag} SOURCES: one card per connected account (${b.sources.length})`, `${tag} sources wrong: ${JSON.stringify(b.sources.map((s) => s.channelId))}`);
  const xs = b.sources.filter((s) => s.channelId === 'ch-x-ron' || s.channelId === 'ch-x-clayrune');
  check(xs.length === 2 && new Set(xs.map((s) => s.handle)).size === 2,
    `${tag} 2 X accounts render as 2 source cards with distinct handles: ${xs.map((s) => s.handle).join(', ')}`,
    `${tag} X source cards wrong: ${JSON.stringify(xs)}`);
  const prev = b.sources.filter((s) => s.preview).map((s) => s.channelId).sort();
  check(JSON.stringify(prev) === JSON.stringify([]),
    `${tag} preview-only services are absent from Sources`, `${tag} preview labels wrong: ${JSON.stringify(prev)}`);
  check(b.off.length === 1 && /not connected/i.test(b.off[0].text) && b.off[0].connect && b.off[0].draggable,
    `${tag} the not-connected card reads "${b.off[0] && b.off[0].text}" and is keyboard selectable`, `${tag} off card wrong: ${JSON.stringify(b.off)}`);

  // Ron 2026-10-16: equal-size tiles, a vendor mark on each, nothing running out of its card.
  const geo = await page.evaluate(() => {
    const tiles = [...document.querySelectorAll('.desk-v1-where-source')];
    const sizes = new Set(tiles.map((t) => { const r = t.getBoundingClientRect(); return Math.round(r.width) + 'x' + Math.round(r.height); }));
    const spill = [];
    document.querySelectorAll('.desk-v1-where-source, .desk-v1-where-colhead').forEach((t) => {
      const r = t.getBoundingClientRect();
      t.querySelectorAll('*').forEach((c) => { const q = c.getBoundingClientRect(); if (q.width && (q.right > r.right + 0.5 || q.left < r.left - 0.5)) spill.push((t.dataset.channelId || 'col') + ':' + c.className); });
    });
    return { n: tiles.length, sizes: [...sizes], spill, marks: tiles.filter((t) => t.querySelector('[data-svc-icon]')).length };
  });
  check(geo.n > 0 && geo.sizes.length === 1 && geo.spill.length === 0 && geo.marks === geo.n,
    `${tag} ${geo.n} source tiles are one size (${geo.sizes[0]}), each wears a mark, nothing spills`, `${tag} tile geometry wrong: ${JSON.stringify(geo)}`);

  const tray = await page.$$eval('#desk-v1-camp-addtray-channels', (els) => els.length);
  check(tray === 0, `${tag} the campaign Add tray has no Channels shelf`, `${tag} Channels shelf still rendered`);

  reportUncaught(pageErrors, tag);
  await ctx.close();
}

// ── 2. the three drags (pointer), Active campaign ─────────────────────────
async function runDrags(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await gotoWhere(page);
  const snapshot = () => page.evaluate(() => JSON.stringify(window.DeskV1Fixtures.campaigns) + JSON.stringify(window.DeskV1Fixtures.families));
  const before = await snapshot();
  for (const sel of ['[data-where-message]', '[data-where-version]', '[data-where-source]']) {
    const node = page.locator(sel).first();
    await node.scrollIntoViewIfNeeded(); const r = await node.boundingBox();
    await page.mouse.move(r.x + 20, r.y + 8); await page.mouse.down();
    await page.mouse.move(r.x + 70, r.y + 30, { steps: 5 });
    check(await page.locator('.pd-ghost, .pd-drop-target').count() === 0, `no drag starts from ${sel}`, `drag still active for ${sel}`);
    await page.mouse.move(2, 2); await page.mouse.up();
  }
  check(await snapshot() === before, 'pointer drags change no assignments or account bounds', 'drag mutated state');
  for (const sel of ['[data-where-message]', '[data-where-version]']) {
    await page.locator(sel).first().click({ position: { x: 20, y: 8 } });
    await page.locator(sel).first().dispatchEvent('keydown', { key: 'Enter' });
  }
  check(await page.locator('.desk-v1-addto-menu, [data-where-remove-version]').count() === 0 && await snapshot() === before,
    'no individual assignment menus or removal controls', 'Where still edits assignments');
  reportUncaught(pageErrors, '[read-only]'); await ctx.close();
}

async function runCounts(browser) {
  const { ctx, page } = await newBootedPage(browser, { ls: {} });
  await page.evaluate(() => {
    const rows = window.DeskV1Fixtures.families.filter(f => f.campaignId === 'camp-1');
    const fam = rows.find(f => f.versions.some(v => v.channelId === 'ch-x-ron'));
    fam.versions.push({ id: 'duplicate-live', channelId: 'ch-x-ron', state: 'drafting' });
    rows.push({ id: 'not-live', campaignId: 'camp-1', title: 'Inactive assignments', versions: [
      { id: 'archived-version', channelId: 'ch-x-ron', state: 'archived' },
      { id: 'skipped-version', channelId: 'ch-x-ron', state: 'skipped' },
    ] });
    rows.push({ id: 'archived-piece', campaignId: 'camp-1', title: 'Archived piece', archivedAt: '2026-10-30', versions: [
      { id: 'archived-piece-version', channelId: 'ch-x-ron', state: 'drafting' },
    ] });
    window.DeskV1Fixtures.families.push(...rows.filter(f => ['not-live', 'archived-piece'].includes(f.id)));
  });
  await gotoWhere(page);
  check((await readBoard(page)).columns.find(c => c.channelId === 'ch-x-ron').posts === EXPECT.columns['ch-x-ron'],
    'post count ignores duplicate destinations, inactive versions and archived pieces', 'live post count inflated');
  await ctx.close();
}

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
  // Undo (the header's; Ron 2026-10-02 removed the popup) restores the column and every archived version.
  await page.click('#desk-v1-undo');
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

  check(await page.locator('[data-where-post-count="0"]').count() === 1, 'new account starts with zero posts', 'new account prefilled');
  check(!!await page.$('[data-where-awaiting]'), 'account expansion still awaits approval', 'widening lost approval gate');
  const status = await launchStatus(page);
  check(/Awaiting approval/.test(status), 'Launch refuses expanded bounds without approval', 'Launch accepted widening');
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
  await page.click('[data-where-source][data-channel-id="ch-x-ron"]');
  const b2 = await readBoard(page);
  check(b2.columns.length === 1 && b2.columns[0].channelId === 'ch-x-ron' && !(await page.$('[data-where-awaiting]')),
    'draft: a source selected adds a column and never shows Awaiting approval', `draft: ${JSON.stringify(b2.columns.map((c) => c.channelId))}`);

  // Connect › → the Connections screen (the one place accounts are connected).
  await page.click('[data-where-connect]');
  await page.waitForSelector('[data-connections] [data-conn-tile="ch-reddit"]', { timeout: 4000 });
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
  await runCounts(browser);
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
