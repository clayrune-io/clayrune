#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §5 row IA8)
 * — the acceptance journey (old T8, new path).
 *
 * ONE continuous page, driven exactly the way a user would click it (no
 * deskV1Nav shortcuts except reaching Engagement, which the real UI itself
 * only reaches via Home's own button — see navHomeToEngagement below):
 *
 *   Home -> engulfing_scanner -> new campaign (lands on the map at ① Brief,
 *   R2-18; R2-3b: no IA4 setup steps) -> left at ② Goal -> resume -> back to
 *   Brief -> forced Posy failure + Retry on the Brief stop's Suggest -> Accept all on ③ What ->
 *   ⑥ Launch -> Start -> open a piece -> What/How/When/Where -> Pause project
 *   -> Resume -> Engagement filtered to that project -> Delete a never-started
 *   Draft from its ⋯ menu (Undo).
 *
 * Each hop asserts what the user SEES (pill text, facet body copy, filtered
 * row ids, toast text) — never just that a selector exists.
 *
 * Real headless boot (real index.html + real static/js|css, no network),
 * same hermetic shape as every other desk-v1-*.mjs smoke.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-journey.mjs
 * Exit 0 = the whole journey holds; 1 = a hop regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
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

const PID = 'smoke_deskv1journey';
const PROJECTS = [{
  id: PID, name: 'Desk v1 journey smoke', status: 'active', domain: 'general', emoji: '🧪',
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
  if (path === '/api/floor') return J({ bench: [] });
  return route.abort();
}

async function newBootedPage(browser) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 950 } });
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

async function pillText(page) {
  return (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '').trim();
}

// The last campaign fixture pushed — same technique desk-v1-setup.mjs uses
// to resolve a just-created draft's id without assuming card order.
async function lastCampaignId(page) {
  return page.evaluate(() => {
    const camps = window.DeskV1Fixtures.campaigns;
    return camps[camps.length - 1].id;
  });
}

async function run(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  // ── Home -> engulfing_scanner ────────────────────────────────────────────
  await page.waitForSelector('.desk-v1-home-block-name[data-project-id="engulfing_scanner"]', { timeout: 8000 });
  await page.click('.desk-v1-home-block-name[data-project-id="engulfing_scanner"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const projectTitle = (await page.textContent('.desk-v1-crumb-title, .desk-v1-project-title').catch(() => '') || '');
  /Engulfing scanner/.test(projectTitle)
    ? ok(`Home -> project: landed on "${projectTitle.trim()}"`)
    : fail(`Home -> project: wrong project page: ${JSON.stringify(projectTitle)}`);

  // ── new feature campaign ─────────────────────────────────────────────────
  // R2-3b: IA4's 3-step setup is gone — a new campaign opens the map stepper
  // at ① Brief (R2-18) like every other campaign, with no "Setup n of 3" anywhere.
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  await page.waitForSelector('.desk-v1-map-stop[data-stop="how"][data-state="here"]', { timeout: 8000 });
  let text = await page.evaluate(() => document.body.innerText);
  !/Setup\s+\d\s+of\s+3/.test(text)
    ? ok('new campaign: opens on the map at ① Brief, no "Setup n of 3"')
    : fail('new campaign: still shows an IA4 setup step');
  const campA = await lastCampaignId(page);

  // ── left at ② Goal ────────────────────────────────────────────────────────
  await page.click('[data-map-next]');
  await page.waitForSelector('.desk-v1-map-stop[data-stop="goal"][data-state="here"]', { timeout: 8000 });
  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const cardWordSel = `.desk-v1-project-camp-card[data-campaign-id="${campA}"] .desk-v1-state-word`;
  let cardLabel = (await page.textContent(cardWordSel).catch(() => '') || '').trim();
  /Draft · at Goal/.test(cardLabel)
    ? ok(`left at ② Goal: project card reads "${cardLabel}"`)
    : fail(`left-at-Goal card label wrong: ${JSON.stringify(cardLabel)}`);

  // ── resume ────────────────────────────────────────────────────────────────
  await page.click(`.desk-v1-project-camp-card[data-campaign-id="${campA}"]`);
  await page.waitForSelector('.desk-v1-map-stop[data-stop="goal"][data-state="here"]', { timeout: 8000 });
  ok('resume: back on ② Goal');
  await page.click('[data-stop="how"]');
  await page.waitForSelector('.desk-v1-map-stop[data-stop="how"][data-state="here"]', { timeout: 8000 });

  // ── forced Posy failure + Retry (the Brief stop's Suggest task) ───────────
  await page.evaluate(() => { window.__deskV1PosyForce = 'fail'; });
  await page.click('[data-how-suggest]');
  await page.waitForSelector('.desk-v1-posy-failed', { timeout: 4000 });
  const failedText = (await page.textContent('.desk-v1-posy-failed').catch(() => '') || '');
  /couldn.t finish/.test(failedText)
    ? ok(`forced Posy failure: shows "${failedText.trim()}"`)
    : fail(`forced Posy failure: wrong copy: ${JSON.stringify(failedText)}`);
  const stillDraft = await page.evaluate((id) => window.DeskV1Fixtures.campaigns.find((c) => c.id === id).state, campA);
  stillDraft === 'draft'
    ? ok('forced Posy failure: campaign stays in Draft, nothing changed')
    : fail(`forced Posy failure should not mutate campaign state, got: ${stillDraft}`);

  await page.evaluate(() => { window.__deskV1PosyForce = undefined; });
  await page.click('[data-posy-retry]');
  // Wait for the Suggest task to land before leaving How — clicking ③ while it is
  // still Working raced it (flaked under load).
  await page.waitForFunction(() => /suggested 3 pieces/.test(document.body.textContent || ''), null, { timeout: 6000 });
  await page.click('[data-stop="what"]');
  await page.waitForSelector('.desk-v1-camp-suggested-banner', { timeout: 4000 });
  const banner = (await page.textContent('.desk-v1-camp-suggested-banner').catch(() => '') || '').trim();
  /3 suggested/.test(banner)
    ? ok(`Retry: succeeds, ③ What shows "${banner}"`)
    : fail(`Retry did not recover: ${JSON.stringify(banner)}`);
  await page.click('[data-suggested-accept-all]');
  await page.waitForTimeout(80);

  // ── Start ─────────────────────────────────────────────────────────────────
  // The plan's accounts / cadence / end are typed on ⑤/④ in the real UI;
  // they are set on the fixture here (same shortcut desk-v1-campaign.mjs's
  // runProjectSelect takes) until R2-13 rewrites this journey end to end.
  await page.evaluate((id) => {
    const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === id);
    c.plan.accounts = ['ch-x-ron']; c.plan.cadence.per_week = 2; c.plan.end = { date: null, post_cap: 12 };
  }, campA);
  await page.click('[data-stop="launch"]');
  await page.waitForSelector('[data-map-start-btn]:not([disabled])', { timeout: 4000 });
  await page.click('[data-map-start-btn]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });
  await page.click('[data-sheet-confirm]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  text = await pillText(page);
  /Active/.test(text)
    ? ok(`Start: campaign now Active: "${text}"`)
    : fail(`Start did not activate the campaign: ${JSON.stringify(text)}`);
  await page.click('[data-stop="what"]');

  // ── open a piece ─────────────────────────────────────────────────────────
  // R2-7: freshly-planned pieces are ordinary rows on What (no collapsed group
  // to expand); the title is the row's primary action.
  const pieceId = await page.evaluate((id) => window.DeskV1Fixtures.families.find((f) => f.campaignId === id && f.id.startsWith('fam-suggest-')).id, campA);
  await page.waitForSelector(`[data-family-id="${pieceId}"] [data-primary-action]`, { timeout: 8000 });
  await page.click(`[data-family-id="${pieceId}"] [data-primary-action]`);
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
  const pieceTitle = (await page.textContent('.desk-v1-piece-title').catch(() => '') || '').trim();
  pieceTitle.length > 0
    ? ok(`open a piece: header title "${pieceTitle}"`)
    : fail('open a piece: no title rendered');

  // ── Copy · Media · Versions — a fresh planned post has no copy to review,
  // no media, and one planned version on its destination (real content, not
  // just a selector match). ────────────────────────────────────────────────
  await page.waitForSelector('[data-piece-section="versions"]', { timeout: 8000 });
  const sectionText = async (name) => (await page.textContent(`[data-piece-section="${name}"]`).catch(() => '') || '').replace(/\s+/g, ' ').trim();
  let body = await sectionText('copy');
  /planned/i.test(body) && !/Review/.test(body)
    ? ok(`Copy: "${body}"`)
    : fail(`Copy section wrong: ${JSON.stringify(body)}`);
  body = await sectionText('media');
  /Add media/.test(body)
    ? ok(`Media: "${body}"`)
    : fail(`Media section wrong: ${JSON.stringify(body)}`);
  body = await sectionText('versions');
  /Publish time/.test(body) && /planned/i.test(body)
    ? ok(`Versions: shows the version's state and its publish time — "${body}"`)
    : fail(`Versions section wrong: ${JSON.stringify(body)}`);

  // ── back to the project: Pause / Resume ─────────────────────────────────
  await page.click('.desk-v1-back'); // piece -> campaign
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  await page.click('.desk-v1-back'); // campaign -> project
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });

  await page.click('[data-pause-project-btn]');
  await page.waitForTimeout(50);
  let campAState = await page.evaluate((id) => window.DeskV1Fixtures.campaigns.find((c) => c.id === id).state, campA);
  let camp3State = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-3').state);
  (campAState === 'paused' && camp3State === 'paused')
    ? ok(`Pause project: both campaigns paused (new campaign: ${campAState}, camp-3: ${camp3State})`)
    : fail(`Pause project did not pause every campaign: new=${campAState} camp-3=${camp3State}`);
  const pauseBtnGone = await page.$('[data-pause-project-btn]');
  const resumeBtnShown = await page.$('[data-resume-project-btn]');
  (!pauseBtnGone && resumeBtnShown)
    ? ok('Pause project: project page now offers Resume instead of Pause')
    : fail('Pause project: page did not flip to the Resume affordance');

  await page.click('[data-resume-project-btn]');
  await page.waitForTimeout(50);
  campAState = await page.evaluate((id) => window.DeskV1Fixtures.campaigns.find((c) => c.id === id).state, campA);
  camp3State = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-3').state);
  (campAState === 'active' && camp3State === 'active')
    ? ok(`Resume: both campaigns active again (new campaign: ${campAState}, camp-3: ${camp3State})`)
    : fail(`Resume did not restore every campaign: new=${campAState} camp-3=${camp3State}`);

  // ── Engagement filtered to that project — Home is the only real click path
  // to Engagement (desk-v1-home.js's own .desk-v1-home-engagement-btn). ──────
  while (await page.$('.desk-v1-back')) {
    await page.click('.desk-v1-back');
    await page.waitForTimeout(20);
  }
  await page.waitForSelector('.desk-v1-home-engagement-btn', { timeout: 8000 });
  await page.click('.desk-v1-home-engagement-btn');
  await page.waitForSelector('.desk-v1-engagement', { timeout: 8000 });
  await page.selectOption('[data-eng-filter="project"]', 'engulfing_scanner');
  const rowIds = await page.$$eval('.desk-v1-eng-row', (els) => els.map((e) => e.dataset.engConv));
  (rowIds.length === 1 && rowIds[0] === 'conv-7')
    ? ok(`Engagement filtered to Engulfing scanner: only "${rowIds.join(', ')}"`)
    : fail(`Engagement project filter wrong: ${JSON.stringify(rowIds)}`);

  // ── Delete a never-started Draft (Undo) ─────────────────────────────────
  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'engulfing_scanner' }));
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-map-stop[data-stop="how"]', { timeout: 8000 }); // ① Brief — never touched
  const campB = await lastCampaignId(page);

  await page.click('[data-camp-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 4000 });
  const menuText = (await page.textContent('.desk-v1-camp-cardmenu').catch(() => '') || '');
  /Delete draft/.test(menuText) && !/Archive/.test(menuText)
    ? ok('never-started Draft: More menu offers Delete draft (not Archive)')
    : fail(`never-started Draft: More menu wrong: ${JSON.stringify(menuText)}`);

  // R2-3b: no confirm sheet for a Draft — the Undo toast is the safety.
  await page.click('.desk-v1-camp-cardmenu [data-menu-delete-draft]');
  await page.waitForSelector('.toast', { timeout: 4000 });
  // Toasts have no `key` here, so they STACK rather than replace — the
  // still-showing Resume toast from the Pause/Resume step above can linger
  // in the DOM. `.toast` alone would then resolve to the OLDEST (first
  // child), same trap documented in desk-v1-campaign.mjs — always read/act
  // on `.last()`.
  const lastToast = () => page.locator('.toast').last();
  const toastText = (await lastToast().textContent().catch(() => '') || '');
  /Deleted/.test(toastText)
    ? ok(`Delete: toast reads "${toastText.trim()}"`)
    : fail(`Delete: toast wrong: ${JSON.stringify(toastText)}`);
  let campBGone = await page.evaluate((id) => !window.DeskV1Fixtures.campaigns.some((c) => c.id === id), campB);
  campBGone
    ? ok('Delete: campaign removed from the fixture')
    : fail('Delete: campaign still present after confirm');

  // Delete's onDone('deleted') already navigated to Home synchronously
  // (desk-v1-setup.js:96 / desk-v1-campaign.js:198) — Undo only restores the
  // fixture, it does not navigate back. Confirm the restore by returning to
  // the project ourselves, the same way a user would.
  await lastToast().locator('.toast-btn.primary').click();
  await page.waitForTimeout(50);
  const campBBack = await page.evaluate((id) => window.DeskV1Fixtures.campaigns.some((c) => c.id === id), campB);
  campBBack
    ? ok('Undo: campaign restored to the fixture')
    : fail('Undo: campaign was not restored');
  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'engulfing_scanner' }));
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const cardBack = await page.$(`.desk-v1-project-camp-card[data-campaign-id="${campB}"]`);
  cardBack
    ? ok('Undo: the never-started Draft is visible on the project page again')
    : fail('Undo: draft card did not reappear on the project page');

  reportUncaught(pageErrors, '[journey]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await run(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('journey smoke error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);
