#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision 2, docs/THE_DESK_V1_IA_REVISION_2.md §8 R2-3
 * row) — Campaign map smoke: the ①-⑥ stepper that replaced the Content ·
 * Conversations · Results tab strip (desk-v1-campaign.js §R2-3a comment).
 *
 * Closes the §8 R2-3 row (frame half only — R2-3b retires IA4 setup steps
 * and the rules popover, out of this ticket's scope):
 *   - New campaign opens the map at ① Goal.
 *   - Next carries a draft through to ⑥ Launch, marking each stop done.
 *   - Leaving at ④ When: the project page's draft card reads "Draft · at
 *     When", and clicking the card (Continue) resumes there.
 *   - ⑥ Launch: Start is disabled with the plan's missing bounds listed,
 *     each one's link landing on the stop that fixes it.
 *   - The old `results`/`content`/`calendar` deep links still resolve, now
 *     onto ①/③/④ (desk-v1-shell.js PANEL_ALIASES).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-setup.mjs / desk-v1-campaign.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-map.mjs
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

const PID = 'smoke_deskv1map';
const PROJECTS = [{
  id: PID, name: 'Desk v1 map smoke', status: 'active', domain: 'general', emoji: '🧪',
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

async function openProject(page, projectId) {
  await page.click(`.desk-v1-home-block-name[data-project-id="${projectId}"]`);
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
}

async function newCampaign(page, projectId) {
  await openProject(page, projectId);
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  return page.evaluate(() => {
    const camps = window.DeskV1Fixtures.campaigns;
    return camps[camps.length - 1].id;
  });
}

async function stopState(page, stop) {
  return page.$eval(`.desk-v1-map-stop[data-stop="${stop}"]`, (el) => el.dataset.state).catch(() => null);
}

const MAP_STOPS = ['goal', 'how', 'what', 'when', 'where', 'launch'];
const MAP_STOP_WORDS = { goal: 'Goal', how: 'How', what: 'What', when: 'When', where: 'Where', launch: 'Launch' };

// ── New campaign opens at ① Goal; `Next ›` carries a Draft through every
// stop to ⑥ Launch, marking each one done as it moves past it; `‹ Back`
// retreats one stop. §3 table's "guided, never locked" movement. ──────────
async function runNewCampaignStepperFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await newCampaign(page, 'engulfing_scanner');

  let goalState = await stopState(page, 'goal');
  goalState === 'here'
    ? ok(`new campaign opens the map at ① Goal (data-state="${goalState}")`)
    : fail(`new campaign did not open at Goal: data-state=${JSON.stringify(goalState)}`);

  const stopWords = await page.$$eval('.desk-v1-map-stop .desk-v1-map-stop-word', (els) => els.map((e) => e.textContent.trim()));
  JSON.stringify(stopWords) === JSON.stringify(MAP_STOPS.map((s) => MAP_STOP_WORDS[s]))
    ? ok(`map shows all six stops in order: ${JSON.stringify(stopWords)}`)
    : fail(`map stop order wrong: ${JSON.stringify(stopWords)}`);

  // Next through every stop with no data entry (§4.1: ②⑤ are placeholders
  // this ticket, ①③④ show their absorbed panel) — pure movement mechanics.
  for (let i = 0; i < MAP_STOPS.length - 1; i++) {
    const from = MAP_STOPS[i];
    const to = MAP_STOPS[i + 1];
    await page.click('[data-map-next]');
    await page.waitForTimeout(30);
    const fromState = await stopState(page, from);
    const toState = await stopState(page, to);
    fromState === 'done' && toState === 'here'
      ? ok(`Next: ${from} -> ${to} (${from}=done, ${to}=here)`)
      : fail(`Next: ${from} -> ${to} wrong states: ${from}=${JSON.stringify(fromState)}, ${to}=${JSON.stringify(toState)}`);
  }

  // At ⑥ Launch, no Next (last stop) — Back retreats to ⑤ Where.
  const noNext = await page.$('[data-map-next]');
  !noNext
    ? ok('⑥ Launch has no "Next" button (last stop)')
    : fail('⑥ Launch still shows a "Next" button');
  await page.click('[data-map-back]');
  await page.waitForTimeout(30);
  const whereState = await stopState(page, 'where');
  whereState === 'here'
    ? ok('‹ Back from ⑥ Launch returns to ⑤ Where')
    : fail(`Back from Launch landed wrong: where=${JSON.stringify(whereState)}`);

  reportUncaught(pageErrors, '[stepper-flow]');
  await ctx.close();
}

// ── Leaving a draft mid-map (at ④ When) — the project page's own card names
// the stop, and clicking the card (Continue) resumes exactly there, not back
// at ① or forward past it. §3 table: "Draft · at <stop>" / "Continue lands
// on that stop". ────────────────────────────────────────────────────────
async function runLeaveAtWhenDraftCard(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  const campaignId = await newCampaign(page, 'engulfing_scanner');
  // goal -> how -> what -> when: 3 Next clicks.
  for (let i = 0; i < 3; i++) {
    await page.click('[data-map-next]');
    await page.waitForTimeout(30);
  }
  const whenHere = await stopState(page, 'when');
  whenHere === 'here'
    ? ok('reached ④ When via 3 Next clicks')
    : fail(`did not reach When: data-state=${JSON.stringify(whenHere)}`);

  await page.click('.desk-v1-back');
  await page.waitForSelector('.desk-v1-project', { timeout: 4000 });
  const cardSel = `.desk-v1-project-camp-card[data-campaign-id="${campaignId}"]`;
  const cardLabel = (await page.textContent(`${cardSel} .desk-v1-state-word`).catch(() => '') || '');
  /Draft · at When/.test(cardLabel)
    ? ok(`left at ④ When: project card reads "${cardLabel.trim()}"`)
    : fail(`project card label wrong after leaving at When: ${JSON.stringify(cardLabel)}`);

  await page.click(cardSel);
  await page.waitForSelector('.desk-v1-campaign', { timeout: 4000 });
  const resumedWhen = await stopState(page, 'when');
  resumedWhen === 'here'
    ? ok('Continue (card click) resumes at ④ When')
    : fail(`Continue did not resume at When: data-state=${JSON.stringify(resumedWhen)}`);

  reportUncaught(pageErrors, '[leave-at-when]');
  await ctx.close();
}

// ── ⑥ Launch: Start is gated on `DeskV1Kit.validatePlan`, each missing
// bound listed with a link to the stop that fixes it (kit.js R2-1's
// `missing[].stop`). Engineer exactly 2 missing (accounts, end date) by
// filling cadence only, so the two links exercise two different stops. ────
async function runLaunchMissingLinks(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  const campaignId = await newCampaign(page, 'engulfing_scanner');
  await page.evaluate((id) => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === id);
    camp.plan.cadence.per_week = 3; // leaves accounts + end date missing
    window.deskV1GotoCampaignPanel('launch', { campaignId: id });
  }, campaignId);
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });

  const startBtn = await page.$('[data-map-start-btn]');
  const startDisabled = startBtn ? await startBtn.evaluate((b) => b.disabled) : null;
  startDisabled === true
    ? ok('⑥ Launch: Start campaign is disabled with unmet bounds')
    : fail(`Start campaign disabled-state wrong: ${JSON.stringify(startDisabled)}`);

  const missingLinks = await page.$$eval('[data-missing-stop]', (els) => els.map((e) => ({ stop: e.dataset.missingStop, text: e.textContent.trim() })));
  missingLinks.length === 2 && missingLinks.some((m) => m.stop === 'where') && missingLinks.some((m) => m.stop === 'when')
    ? ok(`⑥ Launch lists 2 missing items linking to their stops: ${JSON.stringify(missingLinks)}`)
    : fail(`⑥ Launch missing list wrong: ${JSON.stringify(missingLinks)}`);

  for (const { stop } of missingLinks) {
    await page.evaluate((id) => window.deskV1GotoCampaignPanel('launch', { campaignId: id }), campaignId);
    await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
    await page.click(`[data-missing-stop="${stop}"]`);
    await page.waitForTimeout(30);
    const landed = await stopState(page, stop);
    landed === 'here'
      ? ok(`missing-item link for "${stop}" lands on that stop`)
      : fail(`missing-item link for "${stop}" landed wrong: data-state=${JSON.stringify(landed)}`);
  }

  reportUncaught(pageErrors, '[launch-missing-links]');
  await ctx.close();
}

// ── Old `results`/`content`/`calendar` deep links still resolve — they now
// alias onto ①/③/④ instead of their retired tab-strip buttons
// (desk-v1-shell.js PANEL_ALIASES). ─────────────────────────────────────
async function runOldDeepLinksLandOnStops(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-map-stop', { timeout: 4000 });

  const CASES = [['results', 'goal'], ['content', 'what'], ['calendar', 'when']];
  for (const [route, stop] of CASES) {
    await page.evaluate((r) => window.deskV1Nav(r, { campaignId: 'camp-1' }), route);
    await page.waitForTimeout(30);
    const state = await stopState(page, stop);
    state === 'here'
      ? ok(`old "${route}" deep link lands on ${MAP_STOP_WORDS[stop]}`)
      : fail(`old "${route}" deep link landed wrong: ${stop}=${JSON.stringify(state)}`);
  }

  reportUncaught(pageErrors, '[old-deep-links]');
  await ctx.close();
}

async function main() {
  const browser = await chromium.launch();
  try {
    console.log('desk-v1-map: New campaign -> stepper mechanics');
    await runNewCampaignStepperFlow(browser);
    console.log('desk-v1-map: leave at When -> draft card -> Continue');
    await runLeaveAtWhenDraftCard(browser);
    console.log('desk-v1-map: Launch missing bounds -> links land on their stop');
    await runLaunchMissingLinks(browser);
    console.log('desk-v1-map: old results/content/calendar deep links');
    await runOldDeepLinksLandOnStops(browser);
  } finally {
    await browser.close();
  }
  if (bad) {
    console.error(`\n${bad} check(s) failed.`);
    process.exit(1);
  }
  console.log('\nALL_OK');
}

main().catch((e) => { console.error(e); process.exit(1); });
