#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision, docs/THE_DESK_V1_IA_REVISION.md §5 row IA8)
 * — the acceptance journey (old T8, new path).
 *
 * ONE continuous page, driven exactly the way a user would click it (no
 * deskV1Nav shortcuts except reaching Engagement, which the real UI itself
 * only reaches via Home's own button — see navHomeToEngagement below):
 *
 *   Home -> engulfing_scanner -> new campaign -> setup interrupted at step 2
 *   -> resume -> forced Posy failure + Retry -> Start -> open a piece ->
 *   What/How/When/Where -> Pause project -> Resume -> Engagement filtered to
 *   that project -> Delete a never-started Draft (Undo).
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
  await page.waitForSelector('.desk-v1-home-project-card[data-project-id="engulfing_scanner"]', { timeout: 8000 });
  await page.click('.desk-v1-home-project-card[data-project-id="engulfing_scanner"]');
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const projectTitle = (await page.textContent('.desk-v1-crumb-title, .desk-v1-project-title').catch(() => '') || '');
  /Engulfing scanner/.test(projectTitle)
    ? ok(`Home -> project: landed on "${projectTitle.trim()}"`)
    : fail(`Home -> project: wrong project page: ${JSON.stringify(projectTitle)}`);

  // ── new feature campaign ─────────────────────────────────────────────────
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
  let text = await pillText(page);
  /Setup 1 of 3/.test(text)
    ? ok(`new campaign: opens at step 1: "${text}"`)
    : fail(`new campaign: wrong step: ${JSON.stringify(text)}`);
  const campA = await lastCampaignId(page);

  // ── setup interrupted at step 2 ──────────────────────────────────────────
  await page.click('[data-setup-continue]');
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 8000 });
  text = await pillText(page);
  /Setup 2 of 3/.test(text)
    ? ok(`setup step 2 (Plan) renders: "${text}"`)
    : fail(`setup step 2 wrong: ${JSON.stringify(text)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 8000 });
  const cardWordSel = `.desk-v1-project-camp-card[data-campaign-id="${campA}"] .desk-v1-state-word`;
  // R2-3: the project card's draft label moved from "Setup N of 3" to
  // "Draft · at <stop>" (camp.map.stop) — IA4's steps never touch the map,
  // so it reads "at Goal" throughout setup regardless of the step reached.
  let cardLabel = (await page.textContent(cardWordSel).catch(() => '') || '').trim();
  /Draft · at Goal/.test(cardLabel)
    ? ok(`interrupted at step 2: project card reads "${cardLabel}"`)
    : fail(`interrupted-at-step-2 card label wrong: ${JSON.stringify(cardLabel)}`);

  // ── resume ────────────────────────────────────────────────────────────────
  await page.click(`.desk-v1-project-camp-card[data-campaign-id="${campA}"]`);
  await page.waitForSelector('[data-setup-draftplan]', { timeout: 8000 });
  text = await pillText(page);
  /Setup 2 of 3/.test(text)
    ? ok(`resume: back on step 2 (Plan): "${text}"`)
    : fail(`resume landed wrong: ${JSON.stringify(text)}`);

  // ── forced Posy failure + Retry ──────────────────────────────────────────
  await page.evaluate(() => { window.__deskV1PosyForce = 'fail'; });
  await page.click('[data-setup-draftplan]');
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
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  text = await pillText(page);
  /Proposed/.test(text)
    ? ok(`Retry: succeeds, campaign now Proposed: "${text}"`)
    : fail(`Retry did not recover: ${JSON.stringify(text)}`);

  // ── Start ─────────────────────────────────────────────────────────────────
  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });
  await page.click('[data-sheet-confirm]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  text = await pillText(page);
  /Active/.test(text)
    ? ok(`Start: campaign now Active: "${text}"`)
    : fail(`Start did not activate the campaign: ${JSON.stringify(text)}`);

  // ── open a piece ─────────────────────────────────────────────────────────
  // Freshly-planned pieces land in a collapsed group ("Planned · 3, show ›")
  // — real content, but the user has to expand it before a card is
  // reachable at all.
  const pieceId = `${campA}-piece-1`;
  await page.waitForSelector('[data-group-show]', { timeout: 8000 });
  await page.click('[data-group-show]');
  await page.waitForSelector(`[data-family-id="${pieceId}"] [data-primary-action]`, { timeout: 8000 });
  await page.click(`[data-family-id="${pieceId}"] [data-primary-action]`);
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
  const pieceTitle = (await page.textContent('.desk-v1-piece-title').catch(() => '') || '').trim();
  pieceTitle.length > 0
    ? ok(`open a piece: header title "${pieceTitle}"`)
    : fail('open a piece: no title rendered');

  // ── What/How/When/Where — each fixture piece is kind:'post', no versions
  // yet reviewed/scheduled, so every facet has its own distinct empty-state
  // copy (real content, not just a selector match). ──────────────────────────
  await page.waitForSelector('[data-facet="what"][aria-selected="true"]', { timeout: 8000 });
  let body = (await page.textContent('.desk-v1-piece-facetbody').catch(() => '') || '');
  /Nothing needs review right now/.test(body)
    ? ok(`What: "${body.trim()}"`)
    : fail(`What facet body wrong: ${JSON.stringify(body)}`);

  await page.click('[data-facet="how"]');
  await page.waitForSelector('[data-facet="how"][aria-selected="true"]', { timeout: 4000 });
  body = (await page.textContent('.desk-v1-piece-facetbody').catch(() => '') || '');
  /No production job for this piece yet/.test(body)
    ? ok(`How: "${body.trim()}"`)
    : fail(`How facet body wrong: ${JSON.stringify(body)}`);

  await page.click('[data-facet="when"]');
  await page.waitForSelector('[data-facet="when"][aria-selected="true"]', { timeout: 4000 });
  body = (await page.textContent('.desk-v1-piece-facetbody').catch(() => '') || '');
  /Not scheduled/.test(body)
    ? ok(`When: "${body.trim()}"`)
    : fail(`When facet body wrong: ${JSON.stringify(body)}`);

  await page.click('[data-facet="where"]');
  await page.waitForSelector('[data-facet="where"][aria-selected="true"]', { timeout: 4000 });
  body = (await page.textContent('.desk-v1-piece-facetbody').catch(() => '') || '');
  /Planned/.test(body)
    ? ok(`Where: shows the version's state — "${body.trim()}"`)
    : fail(`Where facet body wrong: ${JSON.stringify(body)}`);

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
  await page.waitForSelector('[data-setup-continue]', { timeout: 8000 }); // step 1 — never touched
  const campB = await lastCampaignId(page);

  await page.click('[data-camp-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu', { timeout: 4000 });
  const menuText = (await page.textContent('.desk-v1-camp-cardmenu').catch(() => '') || '');
  /Delete campaign/.test(menuText)
    ? ok('never-started Draft: More menu offers Delete (not Archive)')
    : fail(`never-started Draft: More menu wrong: ${JSON.stringify(menuText)}`);

  await page.click('.desk-v1-camp-cardmenu [data-menu-delete]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout: 4000 });
  await page.click('[data-confirm-accept]');
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
