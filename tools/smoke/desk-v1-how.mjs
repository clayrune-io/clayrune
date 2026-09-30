#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R2-6) — the ② How stop of the campaign map
 * (docs/THE_DESK_V1_IA_REVISION_2.md §3 row ② How, §8 R2-6 acceptance).
 *
 * Drives camp-1 (Active, `approval.bounds` snapshot already recorded) through:
 *   How stop renders (strategy/angle/agent/budget) -> Suggest What/When/Where
 *   -> Working -> Ready -> ③ shows "3 suggested" -> Accept all creates 3
 *   Planned pieces -> ④ shows the suggested cadence -> ⑥ shows the suggested
 *   placement -> forced Posy failure -> Retry recovers -> budget own $50 on
 *   the Active campaign flips ⑥ Launch to "Awaiting approval" -> lowering to
 *   $40 does not clear it.
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as every other desk-v1-*.mjs smoke.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-how.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
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

const PID = 'smoke_deskv1how';
const PROJECTS = [{
  id: PID, name: 'Desk v1 how smoke', status: 'active', domain: 'general', emoji: '🧪',
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
// One character so desk-v1-how.js's agent picker (`/api/characters`) has a
// real entry to resolve, matching what the live app serves — an empty list
// would silently hide a broken fetch instead of exercising it.
const CHARACTERS = [{ scope: 'global', name: 'dave', agent_name: 'Dave', avatar: '🛡️' }];

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
  if (path === '/api/characters') return J(CHARACTERS);
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

async function navToCampaign(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-campaign', { timeout: 8000 });
}

async function gotoStop(page, stop) {
  await page.click(`.desk-v1-map-stop[data-stop="${stop}"]`);
}

async function familyCountFor(page, campaignId) {
  return page.evaluate((cid) => window.DeskV1Fixtures.families.filter((f) => f.campaignId === cid).length, campaignId);
}

// Toasts stack (up to 5s) before they self-remove, so a plain "does a toast
// with this text exist" check can't tell a fresh Suggest run's toast apart
// from the PREVIOUS run's — the Suggest task fires the identical message
// both times. Count toasts before the triggering click, then wait for that
// count to grow: proof a NEW one landed, immune to whether the old one has
// auto-dismissed yet. Safe here because Suggest's toast is the only kind
// this smoke ever fires.
async function nextToastText(page, triggerFn, timeout) {
  const before = await page.locator('.toast').count();
  await triggerFn();
  await page.waitForFunction((n) => document.querySelectorAll('.toast').length > n, before, { timeout: timeout || 6000 });
  return (await page.locator('.toast').last().textContent().catch(() => '') || '');
}

async function run(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await navToCampaign(page);

  // ── ② How stop renders strategy/angle/agent/budget ──────────────────────
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 8000 });
  const strategyVal = await page.inputValue('[data-how-strategy]').catch(() => '');
  /Show the beta working end to end/.test(strategyVal)
    ? ok(`How stop: strategy textarea reads the fixture: "${strategyVal.trim()}"`)
    : fail(`How stop: strategy textarea wrong: ${JSON.stringify(strategyVal)}`);
  const agentLabel = (await page.textContent('[data-how-agent-trigger]').catch(() => '') || '').trim();
  agentLabel.length > 0 && agentLabel !== 'undefined'
    ? ok(`How stop: agent picker trigger renders a label: "${agentLabel}"`)
    : fail(`How stop: agent picker trigger empty: ${JSON.stringify(agentLabel)}`);
  const budgetSource = await page.inputValue('[data-how-budget-source]').catch(() => '');
  budgetSource === 'none'
    ? ok('How stop: budget starts at "None" (fixture default)')
    : fail(`How stop: budget source should start "none", got: ${JSON.stringify(budgetSource)}`);

  // ── Suggest What/When/Where -> Working -> Ready ──────────────────────────
  const famCountBefore = await familyCountFor(page, 'camp-1');
  const suggestToast = await nextToastText(page, async () => {
    await page.click('[data-how-suggest]');
    await page.waitForSelector('.desk-v1-posy-stage', { timeout: 3000 }).catch(() => {});
  }, 6000);
  /suggested 3 pieces, a cadence and a placement/.test(suggestToast)
    ? ok(`Suggest task: reaches Ready, toast reads: "${suggestToast.trim()}"`)
    : fail(`Suggest task: toast wrong: ${JSON.stringify(suggestToast)}`);

  // ── ③ What: "3 suggested" banner + Accept all -> 3 Planned pieces ───────
  await gotoStop(page, 'what');
  await page.waitForSelector('.desk-v1-camp-suggested-banner', { timeout: 4000 });
  const bannerText = (await page.textContent('.desk-v1-camp-suggested-banner').catch(() => '') || '').trim();
  /3 suggested/.test(bannerText)
    ? ok(`③ What: suggested banner reads "${bannerText}"`)
    : fail(`③ What: suggested banner wrong: ${JSON.stringify(bannerText)}`);

  await page.click('[data-suggested-accept-all]');
  await page.waitForTimeout(50);
  const famCountAfter = await familyCountFor(page, 'camp-1');
  (famCountAfter - famCountBefore) === 3
    ? ok(`Accept all: 3 new pieces created (${famCountBefore} -> ${famCountAfter})`)
    : fail(`Accept all: expected +3 pieces, got ${famCountBefore} -> ${famCountAfter}`);
  const newIds = await page.evaluate(() => window.DeskV1Fixtures.families.filter((f) => f.id.startsWith('fam-suggest-')).map((f) => f.id));
  newIds.length === 3
    ? ok(`Accept all: all 3 new pieces have the "fam-suggest-" id prefix`)
    : fail(`Accept all: unexpected new-piece ids: ${JSON.stringify(newIds)}`);
  // The new pieces' "planned" state groups under PLANNED ("other"), which
  // COLLAPSED_BY_DEFAULT (desk-v1-campaign.js) hides behind a "show ›"
  // toggle — expand it before reading the cards' state words off the DOM.
  const showOther = await page.$('[data-group-show="other"]');
  if (showOther) await showOther.click();
  const stateWords = [];
  for (const id of newIds) {
    const w = (await page.textContent(`[data-family-id="${id}"] .desk-v1-camp-vrow-state`).catch(() => '') || '').trim();
    stateWords.push(w);
  }
  stateWords.every((w) => /◇\s*Planned/.test(w))
    ? ok(`Accept all: every new piece card reads "◇ Planned": ${JSON.stringify(stateWords)}`)
    : fail(`Accept all: a new piece card isn't "◇ Planned": ${JSON.stringify(stateWords)}`);
  const bannerGone = await page.$('.desk-v1-camp-suggested-banner');
  !bannerGone
    ? ok('Accept all: the "N suggested" banner clears once accepted')
    : fail('Accept all: banner still showing after accept');

  // ── ④ When: cadence proposal ─────────────────────────────────────────────
  await gotoStop(page, 'when');
  await page.waitForSelector('.desk-v1-camp-suggested-banner', { timeout: 4000 });
  const whenBanner = (await page.textContent('.desk-v1-camp-suggested-banner').catch(() => '') || '').trim();
  /suggested.*3x\/week/.test(whenBanner)
    ? ok(`④ When: cadence proposal banner reads "${whenBanner}"`)
    : fail(`④ When: cadence proposal banner wrong: ${JSON.stringify(whenBanner)}`);

  // ── ⑤ Where: placement suggestion ────────────────────────────────────────
  await gotoStop(page, 'where');
  await page.waitForSelector('.desk-v1-stub-inline', { timeout: 4000 });
  const whereText = (await page.textContent('.desk-v1-stub-inline').catch(() => '') || '').trim();
  /suggested/.test(whereText) && /@ron|ch-x-ron/.test(whereText)
    ? ok(`⑤ Where: placement suggestion reads "${whereText}"`)
    : fail(`⑤ Where: placement suggestion wrong: ${JSON.stringify(whereText)}`);

  // ── forced Posy failure -> Retry ─────────────────────────────────────────
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 4000 });
  await page.evaluate(() => { window.__deskV1PosyForce = 'fail'; });
  await page.click('[data-how-suggest]');
  await page.waitForSelector('.desk-v1-posy-failed', { timeout: 6000 });
  const failedText = (await page.textContent('.desk-v1-posy-failed').catch(() => '') || '');
  /couldn.t finish/.test(failedText)
    ? ok(`forced Posy failure: shows "${failedText.trim()}"`)
    : fail(`forced Posy failure: wrong copy: ${JSON.stringify(failedText)}`);

  await page.evaluate(() => { window.__deskV1PosyForce = undefined; });
  const retryToast = await nextToastText(page, () => page.click('[data-posy-retry]'), 6000);
  /suggested 3 pieces, a cadence and a placement/.test(retryToast)
    ? ok(`Retry: recovers, reaches Ready again: "${retryToast.trim()}"`)
    : fail(`Retry: did not recover: ${JSON.stringify(retryToast)}`);

  // ── ⑥ budget own $50 on an Active campaign -> Awaiting approval ─────────
  await gotoStop(page, 'how');
  await page.waitForSelector('.desk-v1-how', { timeout: 4000 });
  await page.selectOption('[data-how-budget-source]', 'own');
  await page.waitForSelector('[data-how-budget-amount]', { timeout: 4000 });
  await page.fill('[data-how-budget-amount]', '50');
  await page.keyboard.press('Tab'); // blur fires the real 'change' exactly once

  await gotoStop(page, 'launch');
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
  let launchStatus = (await page.textContent('.desk-v1-map-launch-status').catch(() => '') || '').trim();
  /Awaiting approval/.test(launchStatus)
    ? ok(`⑥ Launch: budget own $50 flips the panel to "${launchStatus}"`)
    : fail(`⑥ Launch: expected Awaiting approval after $50 own budget, got: ${JSON.stringify(launchStatus)}`);

  // ── lowering to $40 does not clear the Awaiting-approval state ──────────
  await gotoStop(page, 'how');
  await page.waitForSelector('[data-how-budget-amount]', { timeout: 4000 });
  await page.fill('[data-how-budget-amount]', '40');
  await page.keyboard.press('Tab');

  await gotoStop(page, 'launch');
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
  launchStatus = (await page.textContent('.desk-v1-map-launch-status').catch(() => '') || '').trim();
  /Awaiting approval/.test(launchStatus)
    ? ok(`⑥ Launch: lowering the budget to $40 does NOT clear "Awaiting approval": "${launchStatus}"`)
    : fail(`⑥ Launch: lowering to $40 wrongly cleared Awaiting approval: ${JSON.stringify(launchStatus)}`);

  reportUncaught(pageErrors, '[how]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  await run(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('how smoke error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);
