#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §5 row IA1)
 * — Project page smoke.
 *
 * Closes IA1's acceptance: Home shows project cards; Home -> project card
 * -> campaign -> Back reads '‹ Clayrune' then '‹ Desk'; a Needs-you deep
 * link into a review builds the 5-deep stack (home, project, campaign,
 * piece, review); the presence/engagement/piece stub routes render an
 * honest placeholder instead of a blank pane.
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-campaign.mjs / desk-v1-home.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-project.mjs
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

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];
for (const f of readdirSync(ASSETS_DIR)) {
  const ext = f.slice(f.lastIndexOf('.'));
  if (MIME[ext]) STATIC[`/assets/${f}`] = [MIME[ext], readFileSync(resolve(ASSETS_DIR, f))];
}

const PID = 'smoke_deskv1project';
const PROJECTS = [{
  id: PID, name: 'Desk v1 project smoke', status: 'active', domain: 'general', emoji: '🧪',
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

async function newBootedPage(browser, viewport) {
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
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
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

async function crumb(page) {
  return {
    title: (await page.textContent('.desk-v1-crumb-title').catch(() => '') || '').trim(),
    back: (await page.textContent('.desk-v1-back').catch(() => null) || '').trim() || null,
  };
}

// ── Home shows project cards (§5 row IA1: "Home shows project cards"). ─────
async function runHomeProjectCards(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  const cards = await page.$$eval('#desk-v1-home-projects .desk-v1-home-project-card', (els) =>
    els.map((e) => ({
      id: e.dataset.projectId,
      name: e.querySelector('.desk-v1-home-project-name').textContent.trim(),
      meta: e.querySelector('.desk-v1-home-project-meta').textContent.trim(),
    })));
  cards.length === 2 && cards.some((c) => c.id === 'clayrune') && cards.some((c) => c.id === 'engulfing_scanner')
    ? ok(`Home shows both fixture projects as cards: ${JSON.stringify(cards)}`)
    : fail(`Home project cards wrong: ${JSON.stringify(cards)}`);

  reportUncaught(pageErrors, '[home-projects]');
  await ctx.close();
}

// ── Home -> project card -> campaign -> Back reads '‹ Clayrune' then
// '‹ Desk' (§5 row IA1's own acceptance wording, verbatim). ────────────────
async function runProjectToCampaignAndBack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-project-card[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  let c = await crumb(page);
  c.title === 'Clayrune' && c.back === '‹ Desk'
    ? ok(`Home -> project: crumb reads "Clayrune", back reads "${c.back}"`)
    : fail(`project crumb wrong: ${JSON.stringify(c)}`);

  // camp-1 (audience subject) and camp-2 (feature subject) both belong to
  // clayrune and are not archived — both render as cards with the right
  // subject glyph (§1's "◉ project · ▣ product · ✦ feature · ◎ audience").
  const camps = await page.$$eval('#desk-v1-project-camps .desk-v1-project-camp-card', (els) =>
    els.map((e) => ({
      id: e.dataset.campaignId,
      name: e.querySelector('.desk-v1-project-camp-name').textContent.trim(),
      glyph: e.querySelector('.desk-v1-project-camp-subject').textContent.trim(),
      subjectLabel: (e.querySelector('.desk-v1-project-camp-subject-label') || {}).textContent || '',
    })));
  const camp1 = camps.find((c2) => c2.id === 'camp-1');
  const camp2 = camps.find((c2) => c2.id === 'camp-2');
  camp1 && camp1.glyph === '◎' && /Windows users trying Claude Code/.test(camp1.subjectLabel)
    ? ok(`camp-1 card shows the audience glyph + subject label: ${JSON.stringify(camp1)}`)
    : fail(`camp-1 card wrong: ${JSON.stringify(camp1)}`);
  camp2 && camp2.glyph === '✦' && /Restore points/.test(camp2.subjectLabel)
    ? ok(`camp-2 card shows the feature glyph + subject label: ${JSON.stringify(camp2)}`)
    : fail(`camp-2 card wrong: ${JSON.stringify(camp2)}`);

  await page.click('.desk-v1-project-camp-card[data-campaign-id="camp-1"]');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Windows beta testers' && c.back === '‹ Clayrune'
    ? ok(`project -> campaign: crumb reads "Windows beta testers", back reads "${c.back}"`)
    : fail(`campaign crumb wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Clayrune' && c.back === '‹ Desk'
    ? ok(`campaign -> Back: lands on project, crumb "Clayrune", back reads "${c.back}" (the ticket's own worked example)`)
    : fail(`back-to-project crumb wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home', { timeout: 4000 });
  ok('project -> Back: lands on Home');

  reportUncaught(pageErrors, '[project-campaign-back]');
  await ctx.close();
}

// ── engulfing_scanner: 1 Active campaign + 1 Needs-you item (§5 row IA1's
// own fixture ask), scoped separately from clayrune's. ─────────────────────
async function runSecondProject(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-project-card[data-project-id="engulfing_scanner"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  const c = await crumb(page);
  c.title === 'Engulfing scanner'
    ? ok('Home -> engulfing_scanner project: crumb reads "Engulfing scanner"')
    : fail(`engulfing_scanner crumb wrong: ${JSON.stringify(c)}`);

  const camps = await page.$$eval('#desk-v1-project-camps .desk-v1-project-camp-card', (els) =>
    els.map((e) => e.querySelector('.desk-v1-project-camp-name').textContent.trim()));
  camps.length === 1 && camps[0] === 'Signal alerts for day traders'
    ? ok(`engulfing_scanner shows its one Active campaign: ${JSON.stringify(camps)}`)
    : fail(`engulfing_scanner campaign list wrong: ${JSON.stringify(camps)}`);

  const needsYouText = (await page.textContent('#desk-v1-project-needsyou').catch(() => '') || '');
  /futures/.test(needsYouText)
    ? ok('engulfing_scanner\'s own Needs-you list shows conv-7 (not clayrune\'s items)')
    : fail(`engulfing_scanner Needs-you wrong: ${JSON.stringify(needsYouText)}`);

  reportUncaught(pageErrors, '[second-project]');
  await ctx.close();
}

// ── A Needs-you deep link into a review builds the 5-deep stack (§5 row
// IA1's own acceptance wording): home, project, campaign, piece, review —
// verified by walking Back up through every label in turn. ────────────────
async function runNeedsYouDeepStack(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('#desk-v1-home-needsyou .desk-v1-home-needsyou-row:has-text("piece to approve")');
  await page.waitForSelector('.desk-v1-review', { timeout: 4000 });
  let c = await crumb(page);
  c.back === '‹ Undo anything: restore points in Clayrune 2.1'
    ? ok(`review's Back label is the piece entry's title: "${c.back}"`)
    : fail(`review Back label wrong (piece not on the stack?): ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForTimeout(50);
  c = await crumb(page);
  c.back === '‹ Windows beta testers'
    ? ok(`piece's Back label is the campaign entry's title: "${c.back}"`)
    : fail(`piece Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Windows beta testers' && c.back === '‹ Clayrune'
    ? ok(`campaign's Back label is the project entry's title: "${c.back}"`)
    : fail(`campaign Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  c = await crumb(page);
  c.title === 'Clayrune' && c.back === '‹ Desk'
    ? ok(`project's Back label is Home ("Desk"): "${c.back}"`)
    : fail(`project Back label wrong: ${JSON.stringify(c)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-home', { timeout: 4000 });
  const backGone = await page.$('.desk-v1-back');
  !backGone
    ? ok('5-deep stack (home, project, campaign, piece, review) exhausted in exactly 4 Backs, landing on root Home with no Back button')
    : fail('Home unexpectedly still shows a Back button — stack deeper than 5');

  reportUncaught(pageErrors, '[needsyou-deep-stack]');
  await ctx.close();
}

// ── presence/engagement/piece stub routes (§5 row IA1: "presence (stub),
// engagement (stub), piece (stub)") — each renders an honest placeholder,
// never a blank pane, when reached directly rather than via the review
// chain above. ──────────────────────────────────────────────────────────
async function runStubRoutes(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await page.click('.desk-v1-home-project-card[data-project-id="clayrune"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });

  await page.click('.desk-v1-project-presence-btn');
  await page.waitForSelector('.desk-v1-stub', { timeout: 4000 });
  let stubText = (await page.textContent('.desk-v1-stub-body').catch(() => '') || '');
  /Presence is not built yet/.test(stubText)
    ? ok(`presence route: honest stub placeholder — "${stubText.trim()}"`)
    : fail(`presence stub wrong: ${JSON.stringify(stubText)}`);

  await page.evaluate(() => window.deskV1Nav('engagement', {}));
  await page.waitForSelector('.desk-v1-stub', { timeout: 4000 });
  stubText = (await page.textContent('.desk-v1-stub-body').catch(() => '') || '');
  /Engagement is not built yet/.test(stubText)
    ? ok(`engagement route: honest stub placeholder — "${stubText.trim()}"`)
    : fail(`engagement stub wrong: ${JSON.stringify(stubText)}`);

  // Reached directly (not via deskV1HomeGotoReview's chain), so _pieceLabel
  // falls back to a real family's title when given its versionId.
  await page.evaluate(() => window.deskV1Nav('piece', { versionId: 'v-restore-blog' }));
  await page.waitForSelector('.desk-v1-stub', { timeout: 4000 });
  stubText = (await page.textContent('.desk-v1-stub-body').catch(() => '') || '');
  /Undo anything: restore points in Clayrune 2\.1 is not built yet/.test(stubText)
    ? ok(`piece route: honest stub placeholder, titled from the fixture family — "${stubText.trim()}"`)
    : fail(`piece stub wrong: ${JSON.stringify(stubText)}`);

  reportUncaught(pageErrors, '[stub-routes]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await runHomeProjectCards(browser);
  await runProjectToCampaignAndBack(browser);
  await runSecondProject(browser);
  await runNeedsYouDeepStack(browser);
  await runStubRoutes(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);
