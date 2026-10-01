/**
 * Desk v1 (MC-977, IA5 -> R2-7) — piece page smoke (docs/THE_DESK_V1_IA_REVISION_2.md
 * §4.3, §8 row R2-7).
 *
 * R2-7 retired IA5's four facets: the piece is one page, Copy · Media ·
 * Versions. This checks the sections, a multi-asset piece (2 thumbnails +
 * ＋ Add media, with Undo), header + Posy staying the SAME DOM node across
 * repaints, the publish time being the calendar's own value, Back reading
 * `‹ <campaign> · What`, What -> Review -> Back reading '‹ <piece title>',
 * and the video director hand-off returning to the same piece.
 *
 * Real headless boot (real index.html + real static/js|css, no network),
 * same hermetic shape as desk-v1-review.mjs / desk-v1-video.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-piece.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const ASSETS_DIR = resolve(REPO_ROOT, 'assets');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1piece';
const PROJECTS = [{
  id: PID, name: 'Desk v1 piece smoke', status: 'active', domain: 'general', emoji: '🧪',
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

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function fulfillOrAbort(route) {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  const hit = STATIC[path];
  if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({ desk_v1: true, user_timezone: '' });
  if (path === '/api/characters') return J([]);
  return route.abort();
}

async function newBootedPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', fulfillOrAbort);

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function navToCampaign(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
}

// Through the campaign card's own primary action (ticket: "the content
// card's primary action opens the piece"), not a direct deskV1Nav — this is
// the real path a user takes, matching desk-v1-review.mjs's own comment on
// why it goes through the campaign page first.
async function navToPieceViaCard(page, familyId) {
  await navToCampaign(page);
  await page.waitForSelector(`[data-family-id="${familyId}"] [data-primary-action]`, { timeout: 8000 });
  await page.click(`[data-family-id="${familyId}"] [data-primary-action]`);
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
}

// ── card -> piece: the primary action on a content card opens the piece
// page, header shows the family's own title. ────────────────────────────────
async function runCardOpensPiece(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-restore-points');

  const title = (await page.textContent('.desk-v1-piece-title').catch(() => '') || '').trim();
  if (title === 'Undo anything: restore points in Clayrune 2.1') {
    ok(`card -> piece: header title "${title}"`);
  } else {
    fail(`card -> piece: header title wrong: ${JSON.stringify(title)}`);
  }
  const crumbTitle = (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim();
  if (crumbTitle === title) ok(`card -> piece: crumb title matches ("${crumbTitle}")`);
  else fail(`card -> piece: crumb title wrong: ${JSON.stringify(crumbTitle)}`);

  reportUncaught(pageErrors, '[card-opens-piece]');
  await ctx.close();
}

// ── R2-7: the piece is ONE page (Copy · Media · Versions). Header + Posy box
// survive every in-page repaint as the SAME DOM node (identity, not text) —
// same marker-property technique as desk-v1-campaign.mjs's tab-switch check. ─
async function runSectionsAndIdentity(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-30-testers');

  const sections = await page.$$eval('[data-piece-section]', (els) => els.map((e) => e.dataset.pieceSection));
  if (sections.join() === 'copy,media,versions') ok('piece page: one scroll with Copy · Media · Versions');
  else fail(`piece page: sections wrong: ${JSON.stringify(sections)}`);
  const facetTabs = await page.$$eval('[data-facet]', (els) => els.length);
  if (facetTabs === 0) ok('piece page: the four IA5 facet tabs are gone');
  else fail(`piece page: ${facetTabs} [data-facet] tabs still render`);
  const on = (await page.textContent('.desk-v1-piece-on') || '').trim();
  if (on === 'on 2 channels') ok(`piece header: "${on}" (same helper as What and Where)`);
  else fail(`piece header: expected "on 2 channels", got ${JSON.stringify(on)}`);

  // Multi-asset piece (frame 5a): two thumbnails + `＋ Add media`.
  const thumbs = await page.$$eval('[data-piece-media] .desk-v1-what-thumb', (els) => els.length);
  const hasAdd = await page.$('[data-piece-media] [data-add-media]');
  if (thumbs === 2 && hasAdd) ok('multi-asset piece: 2 thumbnails + ＋ Add media on the Media section');
  else fail(`multi-asset piece: thumbs=${thumbs}, add=${!!hasAdd}`);

  await page.evaluate(() => {
    document.getElementById('desk-v1-piece-header')._deskv1SmokeMarker = 'same-header';
    document.getElementById('desk-v1-piece-posy-input')._deskv1SmokeMarker = 'same-posy';
  });

  // ＋ Add media picks a Material library folder, through the command bus (Undo).
  await page.click('[data-piece-media] [data-add-media]');
  await page.waitForSelector('.desk-v1-addto-menu', { timeout: 2000 });
  await page.locator('.desk-v1-addto-menu button').first().click();
  await page.waitForFunction(() => document.querySelectorAll('[data-piece-media] .desk-v1-what-thumb').length === 3, null, { timeout: 2000 }).catch(() => {});
  const after = await page.$$eval('[data-piece-media] .desk-v1-what-thumb', (els) => els.length);
  if (after === 3) ok('＋ Add media: a third asset lands on the piece');
  else fail(`＋ Add media: expected 3 assets, got ${after}`);
  await page.click('#desk-v1-undo'); // quiet Undo (Ron 2026-10-01)
  await page.waitForFunction(() => document.querySelectorAll('[data-piece-media] .desk-v1-what-thumb').length === 2, null, { timeout: 2000 }).catch(() => {});
  const undone = await page.$$eval('[data-piece-media] .desk-v1-what-thumb', (els) => els.length);
  if (undone === 2) ok('＋ Add media: Undo removes it again');
  else fail(`＋ Add media: Undo left ${undone} assets`);

  const identity = await page.evaluate(() => ({
    header: document.getElementById('desk-v1-piece-header')?._deskv1SmokeMarker,
    posy: document.getElementById('desk-v1-piece-posy-input')?._deskv1SmokeMarker,
  }));
  if (identity.header === 'same-header' && identity.posy === 'same-posy') ok('repaint: header + Posy box are the same DOM nodes');
  else fail(`repaint: node identity lost: ${JSON.stringify(identity)}`);

  reportUncaught(pageErrors, '[sections-identity]');
  await ctx.close();
}

// ── Versions: the publish time is ONE value with the calendar. Editing it
// here goes through the calendar's own reschedule command (Undo toast). ────
async function runVersionTime(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-30-testers');
  const sel = '[data-version-time="v-testers-li"]';
  await page.waitForSelector(sel, { timeout: 4000 });
  const before = await page.$eval(sel, (i) => i.value);
  const next = await page.evaluate((v) => {
    const d = new Date(v); d.setHours(d.getHours() + 2);
    const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  }, before);
  await page.fill(sel, next);
  await page.$eval(sel, (i) => i.dispatchEvent(new Event('change', { bubbles: true })));
  await page.waitForTimeout(150);
  const calWhen = await page.evaluate(() => {
    const v = window.DeskV1Fixtures.families.find((f) => f.id === 'fam-30-testers').versions.find((x) => x.id === 'v-testers-li');
    const d = window.deskV1CalendarVersionWhen(v); const p = (n) => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  });
  if (calWhen === next) ok(`Versions: editing the publish time moves the calendar value (${before} -> ${calWhen})`);
  else fail(`Versions: calendar value ${calWhen}, expected ${next}`);
  const undo = page.locator('#desk-v1-undo:not([disabled])'); // quiet Undo (Ron 2026-10-01)
  if (await undo.count()) {
    await undo.first().click();
    await page.waitForTimeout(150);
    // fill() already fires `change`, and the line above fires it again: two moves to the same time are in the history.
    if ((await page.$eval(sel, (i) => i.value)) !== before && await undo.count()) { await undo.first().click(); await page.waitForTimeout(150); }
    const restored = await page.$eval(sel, (i) => i.value);
    if (restored === before) ok('Versions: Undo restores the publish time');
    else fail(`Versions: Undo left ${restored}, expected ${before}`);
  } else fail('Versions: no header Undo after a time edit');
  reportUncaught(pageErrors, '[version-time]');
  await ctx.close();
}

// ── Back from a piece reads `‹ <campaign> · What`; What -> Review -> Back
// reads '‹ <piece title>' (review 12b is a piece child). ──────────────────
async function runBackLabels(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-restore-points');
  const campTitle = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1').plan.title);
  const pieceBack = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
  if (pieceBack.includes(campTitle) && /·\s*What$/.test(pieceBack)) ok(`piece: Back reads "${pieceBack}"`);
  else fail(`piece: Back wrong: ${JSON.stringify(pieceBack)} (campaign "${campTitle}")`);

  await page.click('[data-review-btn]');
  await page.waitForSelector('.desk-v1-review', { timeout: 8000 });
  const back = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
  if (back.includes('Undo anything: restore points in Clayrune 2.1')) ok(`What -> Review: Back reads "${back}"`);
  else fail(`What -> Review: Back wrong: ${JSON.stringify(back)}`);

  // Back from the piece lands on the campaign's ③ What list.
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
  await page.click('.desk-v1-back');
  await page.waitForSelector('[data-what]', { timeout: 8000 });
  ok('piece -> Back: lands on the campaign What list');

  reportUncaught(pageErrors, '[back-labels]');
  await ctx.close();
}

// ── Media -> video director -> Back returns to the same piece. ───────────
async function runMediaToVideoBack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToPieceViaCard(page, 'fam-install-video');
  await page.waitForSelector('[data-video-btn]', { timeout: 8000 });

  await page.click('[data-video-btn]');
  await page.waitForSelector('.desk-v1-video-director', { timeout: 8000 });
  const videoBack = (await page.textContent('.desk-v1-back').catch(() => '') || '').trim();
  if (videoBack.includes('Install in two minutes')) ok(`Media -> video director: Back reads "${videoBack}"`);
  else fail(`Media -> video director: Back wrong: ${JSON.stringify(videoBack)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
  const title = (await page.textContent('.desk-v1-piece-title').catch(() => '') || '').trim();
  if (title === 'Install in two minutes') ok('Media -> video director -> Back: returns to the same piece');
  else fail(`Media -> video director -> Back: landed on ${JSON.stringify(title)}`);

  reportUncaught(pageErrors, '[media-video-back]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await runCardOpensPiece(browser);
  await runSectionsAndIdentity(browser);
  await runVersionTime(browser);
  await runBackLabels(browser);
  await runMediaToVideoBack(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error('❌ FAIL — smoke harness error: ' + (e && e.message ? e.message : e));
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}

console.log(bad
  ? `\n❌ FAIL — ${bad} Desk v1 piece check(s) regressed.`
  : '\n✅ PASS — Desk v1 R2-7 piece page: card -> piece, Copy · Media · Versions, multi-asset + Add media, publish time via the calendar command, Back labels, Media -> video -> Back all hold.');
process.exit(exitCode);
