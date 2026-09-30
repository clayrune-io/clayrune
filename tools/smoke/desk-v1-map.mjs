#!/usr/bin/env node
/**
 * Desk v1 (MC-977 IA revision 2, docs/THE_DESK_V1_IA_REVISION_2.md §8 R2-3
 * row) — Campaign map smoke: the ①-⑥ stepper that replaced the Content ·
 * Conversations · Results tab strip (desk-v1-campaign.js §R2-3a comment).
 *
 * Closes the §8 R2-3 row (frame half) and the R2-3b row (the retirements —
 * last sections below; they also re-home the still-valid checks of the
 * retired desk-v1-setup.mjs / desk-v1-rules.mjs):
 *   - New campaign opens the map at ① Brief (R2-18).
 *   - Next carries a draft through to ⑥ Launch, marking each stop done.
 *   - Leaving at ④ When: the project page's draft card reads "Draft · at
 *     When", and clicking the card (Continue) resumes there.
 *   - ⑥ Launch: Start is disabled with the plan's missing bounds listed,
 *     each one's link landing on the stop that fixes it.
 *   - The old `results`/`content`/`calendar` deep links still resolve, now
 *     onto ①/③/④ (desk-v1-shell.js PANEL_ALIASES).
 *   - R2-3b: New campaign renders the map stepper first (no "Setup n of 3",
 *     no setup-step DOM); no rules-popover trigger or rule chips; no
 *     Conversations tab; the old `conversations` deep link lands on
 *     Engagement filtered to that campaign; at 390 px the current stop sits
 *     inside the stepper viewport on Goal and on Launch; 6 is one click away
 *     on a fresh Draft and shows the Project select with "Project" first in
 *     its missing list; a Draft's more-menu deletes it and returns Home.
 *
 * Re-homed from the retired desk-v1-setup.mjs / desk-v1-rules.mjs (R2-3b row:
 * "none dropped unnamed"). Live checks moved here unchanged in substance;
 * these were retired WITH their UI and are NOT re-homed:
 *   setup.mjs  step-1/2 pills ("Setup n of 3"), "Draft the plan" -> Proposed +
 *              3 Planned pieces, leave-at-step-2 resume (resume now covered by
 *              the leave-at-When check above), all-manual accounts checkbox
 *              picking (the account picker moved to Where), "from <project>"
 *              inherited labels and "Change for the project ›" (both hang off
 *              plan._inheritedFields, which only the wizard ever set).
 *   rules.mjs  rules-popover checks: cadence clamp chip + effective hint,
 *              per-job budget cap, raise-budget crumb, decline-widening radio,
 *              Channels included/excluded, durable-instruction rule chip + Undo,
 *              popover phone docking, the "Ends ..." / Rules chips + Edit hook,
 *              and the t2b_* screenshot capture.
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

const MAP_STOPS = ['how', 'goal', 'what', 'when', 'where', 'launch'];
const MAP_STOP_WORDS = { goal: 'Goal', how: 'Brief', what: 'What', when: 'When', where: 'Where', launch: 'Launch' };

// ── New campaign opens at ① Brief (R2-18; route key `how`); `Next ›` carries a Draft through every
// stop to ⑥ Launch, marking each one done as it moves past it; `‹ Back`
// retreats one stop. §3 table's "guided, never locked" movement. ──────────
async function runNewCampaignStepperFlow(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  await newCampaign(page, 'engulfing_scanner');

  const briefState = await stopState(page, 'how');
  briefState === 'here'
    ? ok(`new campaign opens the map at ① Brief (data-state="${briefState}")`)
    : fail(`new campaign did not open at Brief: data-state=${JSON.stringify(briefState)}`);

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
  // how (Brief) -> goal -> what -> when: 3 Next clicks.
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

// ── R2-3b: what the retirements left behind ─────────────────────────────
async function runR23bRetirements(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);

  // Project-less Draft from Home's page-level + New campaign (R2-2f).
  await page.click('.desk-v1-home-newcamp-page-btn');
  await page.waitForSelector('.desk-v1-campaign .desk-v1-map-stop[data-stop="goal"]', { timeout: 6000 });
  const campId = await page.evaluate(() => {
    const camps = window.DeskV1Fixtures.campaigns;
    return camps[camps.length - 1].id;
  });

  // 1. No setup-step DOM, no "Setup n of 3".
  const setupDom = await page.$$('[data-setup-continue], [data-setup-title], [data-setup-outcome], [data-setup-brief], [data-setup-account], [data-setup-draftplan], .desk-v1-setup');
  const bodyText = await page.evaluate(() => document.body.innerText);
  const setupText = /Setup\s+\d\s+of\s+3/.test(bodyText);
  setupDom.length === 0 && !setupText
    ? ok('New campaign renders no setup-step DOM and no "Setup n of 3"')
    : fail(`setup steps still render: ${setupDom.length} node(s), text match=${setupText}`);

  // 2. The stepper is the first thing under the crumb, above every form field.
  const tops = await page.evaluate(() => {
    const stops = document.querySelector('.desk-v1-map-stops');
    const fields = [...document.querySelectorAll('.desk-v1-campaign input, .desk-v1-campaign select, .desk-v1-campaign textarea, .desk-v1-campaign [contenteditable="true"]')]
      .filter((e) => e.offsetParent !== null);
    return { stops: stops ? stops.getBoundingClientRect().top : null, fields: fields.map((e) => e.getBoundingClientRect().top) };
  });
  tops.stops != null && tops.fields.every((t) => tops.stops < t)
    ? ok(`map stepper sits above every form field (stepper top ${Math.round(tops.stops)}px; ${tops.fields.length} field(s) below)`)
    : fail(`stepper is not above the form fields: ${JSON.stringify(tops)}`);

  // 3. No rules-popover trigger / rule chips on any stop.
  let rulesHits = 0;
  for (const stop of MAP_STOPS) {
    await page.click(`.desk-v1-map-stop[data-stop="${stop}"]`);
    await page.waitForTimeout(30);
    rulesHits += (await page.$$('[data-rules-edit], .desk-v1-camp-rule-chip, .desk-v1-rules-pop, [data-rules-popover]')).length;
  }
  rulesHits === 0
    ? ok('no rules-popover trigger or rule chips on any of the six stops')
    : fail(`${rulesHits} rules-popover node(s) still render`);

  // 4. No Conversations tab/stop.
  await page.click('.desk-v1-map-stop[data-stop="goal"]');
  const tabWords = await page.$$eval('.desk-v1-campaign button, .desk-v1-campaign [role="tab"]', (els) => els.map((e) => e.textContent.trim()).filter((t) => /^conversations?$/i.test(t)));
  const convStop = await page.$('[data-stop="conversations"], [data-panel="conversations"], .desk-v1-camp-tab[data-tab="conversations"]');
  tabWords.length === 0 && !convStop
    ? ok('campaign page has no Conversations tab or stop')
    : fail(`Conversations tab still present: ${JSON.stringify(tabWords)} stop=${!!convStop}`);

  // 5. Launch is one click from the top stepper on a fresh Draft; Project
  // select shown, "Project" first in the missing list.
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
  const launch = await page.evaluate(() => ({
    hasSelect: !!document.querySelector('.desk-v1-map-launch [data-setup-project]'),
    firstMissing: ((document.querySelector('.desk-v1-map-launch-missing li') || {}).textContent || '').trim(),
    startDisabled: !!(document.querySelector('[data-map-start-btn]') || {}).disabled,
  }));
  launch.hasSelect && /^Project/.test(launch.firstMissing) && launch.startDisabled
    ? ok(`Launch one click from the stepper: Project select shown, first missing "${launch.firstMissing}", Start disabled`)
    : fail(`Launch on a fresh Draft wrong: ${JSON.stringify(launch)}`);

  // 6. A Draft's more-menu offers Delete draft; it returns Home without the row.
  await page.click('[data-camp-more-btn]');
  await page.waitForSelector('.desk-v1-camp-cardmenu [data-menu-delete-draft]', { timeout: 4000 });
  await page.click('.desk-v1-camp-cardmenu [data-menu-delete-draft]');
  await page.waitForSelector('.desk-v1-home-board-blocks, .desk-v1-home-empty', { timeout: 6000 });
  const rowGone = (await page.$(`.desk-v1-home-row[data-campaign-id="${campId}"]`)) === null;
  const inFixtures = await page.evaluate((id) => window.DeskV1Fixtures.campaigns.some((c) => c.id === id), campId);
  rowGone && !inFixtures
    ? ok('more-menu Delete draft pops back to Home; the row is gone')
    : fail(`more-menu Delete draft: rowGone=${rowGone} stillInFixtures=${inFixtures}`);

  reportUncaught(pageErrors, '[r2-3b-retirements]');
  await ctx.close();
}

// Old `conversations` deep link -> Engagement filtered to that campaign.
async function runConversationsDeepLink(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  await page.evaluate(() => window.deskV1Nav('conversations', { campaignId: 'camp-1' }));
  await page.waitForTimeout(80);
  const landed = await page.evaluate(() => {
    const crumb = (document.querySelector('.desk-v1-crumb') || {}).textContent || '';
    return {
      crumb: crumb.replace(/\s+/g, ' ').trim(), hasMap: !!document.querySelector('.desk-v1-map-stop'),
      // the campaign-scoped embed: a source strip, and no per-row campaign label (that only shows at scope "all")
      embed: !!document.querySelector('.desk-v1-conv-source'), allScopeLabels: document.querySelectorAll('.desk-v1-conv-row-camp').length,
    };
  });
  /Engagement/.test(landed.crumb) && !landed.hasMap && landed.embed && landed.allScopeLabels === 0
    ? ok(`old "conversations" deep link lands on Engagement filtered to the campaign (crumb: "${landed.crumb}")`)
    : fail(`old "conversations" deep link landed wrong: ${JSON.stringify(landed)}`);

  reportUncaught(pageErrors, '[conversations-deep-link]');
  await ctx.close();
}

// <=960px: the stepper scrolls horizontally and keeps the current stop in view.
async function runMobileStepper(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { width: 390, height: 844 });
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('.desk-v1-map-stop', { timeout: 4000 });
  const inView = () => page.evaluate(() => {
    const stops = document.querySelector('.desk-v1-map-stops');
    const here = document.querySelector('.desk-v1-map-stop[data-state="here"]');
    if (!stops || !here) return null;
    const s = stops.getBoundingClientRect(), b = here.getBoundingClientRect();
    return { stop: here.dataset.stop, inside: b.left >= s.left - 0.5 && b.right <= s.right + 0.5, scrolls: stops.scrollWidth > stops.clientWidth + 1 };
  });
  for (const stop of ['goal', 'launch']) {
    await page.evaluate(([id, st]) => window.deskV1GotoCampaignPanel(st, { campaignId: id }), ['camp-1', stop]);
    await page.waitForTimeout(120);
    const v = await inView();
    v && v.stop === stop && v.inside
      ? ok(`390px: current stop "${stop}" sits fully inside the stepper viewport (stepper scrolls: ${v.scrolls})`)
      : fail(`390px: current stop "${stop}" not fully in view: ${JSON.stringify(v)}`);
  }
  reportUncaught(pageErrors, '[mobile-stepper]');
  await ctx.close();
}

// ════════════════════════════════════════════════════════════════════════
// R2-3b re-homed checks. desk-v1-setup.mjs and desk-v1-rules.mjs are retired
// (their IA4 wizard / rules popover no longer exist); every check that still
// describes live UI moved here, same assertions, reached by the R2 path. See
// the "re-homed from" line on each runner; retired-with-the-UI checks are
// listed in the header comment.
// ════════════════════════════════════════════════════════════════════════

const TONES = [
  { name: 'default/dark', ls: {} },
  { name: 'tone-warm', ls: { mc_tone: 'warm' } },
  { name: 'tone-editorial', ls: { mc_tone: 'editorial' } },
];

async function newTonePage(browser, tone, viewport) {
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

async function navToProposedCampaign(page, campaignId) {
  await page.evaluate((id) => window.deskV1Nav('campaign', { campaignId: id }), campaignId);
  await page.waitForSelector('.desk-v1-camp-summary', { timeout: 8000 });
}

async function waitForWideningSheet(page, timeout = 2000) {
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { timeout });
  return (await page.textContent('.desk-v1-rules-confirmtext').catch(() => '') || '').trim();
}

async function respondToWideningSheet(page, accept) {
  await page.click(accept ? '[data-confirm-accept]' : '[data-confirm-decline]');
  await page.waitForSelector('.desk-v1-rules-confirm-overlay', { state: 'detached', timeout: 2000 });
}

// re-homed from desk-v1-rules.mjs runProposedRenderChecks (minus the Rules
// chips + Edit hook assertion, retired with the popover).
async function runProposedRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newTonePage(browser, tone);
  await navToProposedCampaign(page, 'camp-2');

  const stateWord = (await page.textContent('.desk-v1-camp-state-pill .desk-v1-state-word').catch(() => '') || '').trim();
  stateWord === 'Proposed'
    ? ok(`[${tone.name}] state pill reads "Proposed"`)
    : fail(`[${tone.name}] state pill wrong: ${JSON.stringify(stateWord)}`);

  const startBtn = await page.$('[data-start-campaign]');
  startBtn ? ok(`[${tone.name}] "Start campaign" primary button renders`) : fail(`[${tone.name}] Start campaign button missing`);

  const dashedFields = await page.$$eval('[data-goal-field]', (els) => els.map((e) => e.dataset.goalField));
  ['target', 'dateLabel', 'audience'].every((f) => dashedFields.includes(f))
    ? ok(`[${tone.name}] goal sentence has 3 editable dashed fields: ${JSON.stringify(dashedFields)}`)
    : fail(`[${tone.name}] goal sentence fields wrong: ${JSON.stringify(dashedFields)}`);

  const warning = await page.$('.desk-v1-rules-goal-warning');
  warning ? ok(`[${tone.name}] untracked goal shows "⚠ not tracked yet" (CMP-03), not a blocker`) : fail(`[${tone.name}] untracked-goal warning missing`);

  const badgeTitle = await page.$eval('.desk-v1-camp-summary-badges .desk-v1-channel-badge', (el) => el.title).catch(() => '');
  /Publishes after approval/.test(badgeTitle)
    ? ok(`[${tone.name}] A12: each-piece review mode → channel badge reads "Publishes after approval": ${JSON.stringify(badgeTitle)}`)
    : fail(`[${tone.name}] channel badge copy wrong for each_piece: ${JSON.stringify(badgeTitle)}`);

  const blockerQ = (await page.textContent('.desk-v1-rules-blocker-q').catch(() => '') || '').trim();
  /X post or the LinkedIn post/i.test(blockerQ)
    ? ok(`[${tone.name}] "⛔ Posy's one question" blocker card renders: "${blockerQ}"`)
    : fail(`[${tone.name}] blocker card missing/wrong: ${JSON.stringify(blockerQ)}`);
  const answerCount = await page.$$eval('.desk-v1-rules-blocker-answer', (els) => els.length);
  answerCount === 2
    ? ok(`[${tone.name}] blocker card has 2 answer buttons`)
    : fail(`[${tone.name}] expected 2 blocker answers, got ${answerCount}`);

  const cardTitles = await page.$$eval('.desk-v1-rules-proposed-card .desk-v1-camp-card-title', (els) => els.map((e) => e.textContent.trim()));
  cardTitles.length === 2
    ? ok(`[${tone.name}] Posy's 2 proposed pieces render: ${JSON.stringify(cardTitles)}`)
    : fail(`[${tone.name}] expected 2 proposed cards, got ${JSON.stringify(cardTitles)}`);

  const assumedCount = await page.$$eval('.desk-v1-rules-assumed', (els) => els.length);
  assumedCount === 2
    ? ok(`[${tone.name}] both proposed cards carry a "? Assumed" popover`)
    : fail(`[${tone.name}] expected 2 "? Assumed" popovers, got ${assumedCount}`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runGoalEdit.
async function runGoalEdit(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} });
  await navToProposedCampaign(page, 'camp-2');

  await page.click('[data-goal-field="target"]');
  await page.keyboard.press('Control+A');
  await page.keyboard.type('75');
  await page.click('.desk-v1-rules-goal-sentence'); // blur

  const target = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').plan.goal.target);
  target === 75
    ? ok('editing the goal sentence\'s target field commits to camp.plan.goal.target on blur (T1 §4: one canonical plan object)')
    : fail(`goal target did not commit: ${JSON.stringify(target)}`);

  reportUncaught(pageErrors, '[goal-edit]');
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runGoalDateEdit + runGoalDateEditYearless
// (minus the "Ends ..." rule-chip assertions, retired with the chips): the
// summary bar's deadline and plan.end.date move off one write.
async function runGoalDateEdit(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} });
  await navToProposedCampaign(page, 'camp-2');

  const before = await page.$eval('[data-goal-field="dateLabel"]', (e) => e.textContent.trim());
  await page.click('[data-goal-field="dateLabel"]');
  await page.keyboard.press('Control+A');
  await page.keyboard.type('2026-12-25');
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.waitForTimeout(50);

  const after = await page.$eval('[data-goal-field="dateLabel"]', (e) => e.textContent.trim());
  after !== before
    ? ok(`editing the goal date field moves the summary bar's own deadline: "${before}" → "${after}"`)
    : fail(`summary bar deadline did not update: stayed "${before}"`);

  const plan = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    return { deadline: camp.plan.goal.deadline, end: camp.plan.end.date };
  });
  plan.deadline === '2026-12-25' && plan.end === '2026-12-25'
    ? ok('camp.plan.goal.deadline and camp.plan.end.date both hold the new date (single write, both readers)')
    : fail(`plan not updated consistently: ${JSON.stringify(plan)}`);

  // Yearless "Dec 25" resolves against the plan's own (2026) year.
  await page.evaluate(() => { const c = window.DeskV1Fixtures.campaigns.find((x) => x.id === 'camp-2'); c.plan.goal.deadline = '2026-06-01'; c.plan.end.date = '2026-06-01'; });
  await navToProposedCampaign(page, 'camp-2');
  await page.click('[data-goal-field="dateLabel"]');
  await page.keyboard.press('Control+A');
  await page.keyboard.type('Dec 25');
  await page.evaluate(() => document.activeElement && document.activeElement.blur());
  await page.waitForTimeout(50);
  const deadline = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').plan.goal.deadline);
  deadline === '2026-12-25'
    ? ok(`typing the yearless "Dec 25" resolves against the plan's own year: camp.plan.goal.deadline = "${deadline}"`)
    : fail(`yearless date did not resolve to 2026-12-25: camp.plan.goal.deadline = "${deadline}"`);

  reportUncaught(pageErrors, '[goal-date-edit]');
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runValidatePlanMissingEnd.
async function runValidatePlanMissingEnd(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} });
  await navToProposedCampaign(page, 'camp-2');

  const result = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    const plan = Object.assign({}, camp.plan, { end: { date: null, post_cap: null } });
    return window.DeskV1Kit.validatePlan(plan);
  });
  !result.ok && result.missing.some((m) => m.bound === 'end' && m.stop === 'when')
    ? ok(`validatePlan() names the missing end date with its stop (When): ${JSON.stringify(result.missing)}`)
    : fail(`validatePlan() did not flag the missing end date at When: ${JSON.stringify(result)}`);

  const fullResult = await page.evaluate(() => window.DeskV1Kit.validatePlan(window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').plan));
  fullResult.ok
    ? ok('the unmodified camp-2 fixture plan validates clean (all bounds present)')
    : fail(`camp-2's own plan fixture unexpectedly fails validatePlan(): ${JSON.stringify(fullResult)}`);

  reportUncaught(pageErrors, '[validate-plan-missing-end]');
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runBlockerAnswer.
async function runBlockerAnswer(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} });
  await navToProposedCampaign(page, 'camp-2');
  await page.waitForSelector('.desk-v1-rules-blocker', { timeout: 4000 });

  await page.click('[data-answer-id="a-x-first"]');
  const goneCard = await page.$('.desk-v1-rules-blocker');
  !goneCard ? ok('answering the blocker removes its card') : fail('blocker card still present after answering');

  await page.waitForSelector('.toast .toast-btn.primary', { timeout: 2000 }).catch(() => {});
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const backCard = await page.$('.desk-v1-rules-blocker');
  backCard ? ok('Undo restores the blocker card') : fail('Undo did not restore the blocker card');

  reportUncaught(pageErrors, '[blocker-answer]');
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runStartSheet (Proposed page's Start).
async function runStartSheet(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} });
  await navToProposedCampaign(page, 'camp-2');

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  ok('"Start campaign" opens the review sheet');

  const title = (await page.textContent('.desk-v1-rules-sheet-title').catch(() => '') || '');
  /Restore points launch/.test(title)
    ? ok(`sheet title names the campaign: "${title.trim()}"`)
    : fail(`sheet title wrong: ${JSON.stringify(title)}`);

  const rowLabels = await page.$$eval('.desk-v1-rules-authrow-label', (els) => els.map((e) => e.textContent.trim()));
  ['Accounts', 'Frequency ceiling', 'Dates', 'Replies', 'Paid', 'Generation limits', 'Stop conditions']
    .every((l) => rowLabels.includes(l)) && !rowLabels.includes('Review mode')
    ? ok(`all 7 CMP-05 authority rows render, Review mode retired: ${JSON.stringify(rowLabels)}`)
    : fail(`authority rows missing/wrong: ${JSON.stringify(rowLabels)}`);

  const note = (await page.textContent('.desk-v1-rules-sheet-note').catch(() => '') || '');
  /Starting doesn.t approve any piece/.test(note)
    ? ok('sheet states "Starting doesn\'t approve any piece" verbatim')
    : fail(`sheet note wrong: ${JSON.stringify(note)}`);

  await page.keyboard.press('Escape');
  const closedByEsc = !(await page.$('.desk-v1-rules-sheet'));
  closedByEsc ? ok('Escape closes the sheet') : fail('Escape did not close the sheet');

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  await page.click('[data-sheet-cancel]');
  const closedByCancel = !(await page.$('.desk-v1-rules-sheet'));
  const stateAfterCancel = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').state);
  closedByCancel && stateAfterCancel === 'proposed'
    ? ok('Cancel closes the sheet and leaves the campaign Proposed')
    : fail(`Cancel misbehaved: closed=${closedByCancel}, state=${JSON.stringify(stateAfterCancel)}`);

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  await page.click('[data-sheet-confirm]');
  await page.waitForTimeout(80);
  const afterConfirm = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    return { state: camp.state, hasPolicy: !!camp.policyRecord };
  });
  afterConfirm.state === 'active' && afterConfirm.hasPolicy
    ? ok('Confirm sets the campaign Active and creates a policy record')
    : fail(`Confirm did not start the campaign correctly: ${JSON.stringify(afterConfirm)}`);

  await page.waitForSelector('.toast .toast-btn.primary', { timeout: 2000 }).catch(() => {});
  await page.click('.toast .toast-btn.primary');
  await page.waitForTimeout(50);
  const afterUndo = await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2');
    return { state: camp.state, hasPolicy: !!camp.policyRecord };
  });
  afterUndo.state === 'proposed' && !afterUndo.hasPolicy
    ? ok('Undo reverts the campaign back to Proposed and drops the policy record')
    : fail(`Undo did not revert correctly: ${JSON.stringify(afterUndo)}`);

  reportUncaught(pageErrors, '[start-sheet]');
  await ctx.close();
}

// A Draft filled in on the stops (fixture shortcut, same one the journey
// takes), then ⑥ Launch -> Start campaign -> the Start sheet. Re-homes the
// four desk-v1-setup.mjs "step 3" checks that still describe live UI:
//   runAcceptDefaultsToActive (0 '—' rows, Confirm -> Active),
//   runZeroConnectedAccountsManualCopy (all-manual Replies row + Confirm still
//   completes), runStep3GateMissingEndCannotStart (Confirm disabled, missing
//   bound named).
// The wizard-only checks (step pills, "Draft the plan" -> Proposed + 3
// Planned pieces, leave-at-step-2 resume) retired with the wizard; resume is
// covered by runLeaveAtWhenDraftCard above. The "from <project>" and "Change
// for the project" checks are retired as unreachable (see the note inside).
async function draftInProject(page, projectId, plan) {
  await openProject(page, projectId);
  await page.click('.desk-v1-project-newcamp-btn');
  await page.waitForSelector('.desk-v1-campaign .desk-v1-map-stop[data-stop="goal"]', { timeout: 4000 });
  const id = await page.evaluate((p) => {
    const camps = window.DeskV1Fixtures.campaigns;
    const c = camps[camps.length - 1];
    Object.assign(c.plan, p);
    return c.id;
  }, plan);
  await page.click('.desk-v1-map-stop[data-stop="launch"]');
  await page.waitForSelector('.desk-v1-map-launch', { timeout: 4000 });
  return id;
}

async function runDraftStartSheetFromLaunch(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  const plan = { accounts: ['ch-x-ron'], cadence: { per_week: 2 }, end: { date: null, post_cap: 12 } };
  const id = await draftInProject(page, 'engulfing_scanner', plan);
  await page.waitForSelector('[data-map-start-btn]:not([disabled])', { timeout: 4000 });
  await page.click('[data-map-start-btn]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });

  const rowVals = await page.$$eval('.desk-v1-rules-authrow-val', (els) => els.map((e) => e.textContent.trim()));
  const dashCount = rowVals.filter((v) => v === '—').length;
  dashCount === 0
    ? ok(`Start sheet from ⑥ shows 0 '—' rows: ${JSON.stringify(rowVals)}`)
    : fail(`Start sheet still shows ${dashCount} '—' row(s): ${JSON.stringify(rowVals)}`);
  // RETIRED, not re-homed: setup.mjs's 'inherited rows labelled "from <project>"'
  // and '"Change for the project >" lands on Presence' checks. Both hung off
  // `plan._inheritedFields`, which only the retired wizard's "Draft the plan"
  // step ever stamped; nothing on the R2 path sets it (a clamped cadence is
  // a Launch missing item, not a Start-sheet label), so the UI is unreachable.
  await page.click('[data-sheet-confirm]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  const activeText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Active/.test(activeText)
    ? ok(`Confirm — Start campaign takes it Active: "${activeText.trim()}"`)
    : fail(`campaign not Active after Start confirm: ${JSON.stringify(activeText)}`);

  reportUncaught(pageErrors, '[draft-start-sheet]');
  await ctx.close();
}

async function runAllManualAccountsReplies(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  // clayrune's ch-blog is manual capability (never API-connected).
  await draftInProject(page, 'clayrune', { accounts: ['ch-blog'], cadence: { per_week: 1 }, end: { date: null, post_cap: 8 } });
  await page.waitForSelector('[data-map-start-btn]:not([disabled])', { timeout: 4000 });
  await page.click('[data-map-start-btn]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });

  const repliesVal = await page.evaluate(() => {
    const row = Array.from(document.querySelectorAll('.desk-v1-rules-authrow'))
      .find((r) => r.querySelector('.desk-v1-rules-authrow-label').textContent.trim() === 'Replies');
    return row ? row.querySelector('.desk-v1-rules-authrow-val').textContent.trim() : null;
  });
  repliesVal === '✋ You publish it'
    ? ok(`zero connected (all-manual) accounts: Replies row reads "${repliesVal}"`)
    : fail(`Replies row wrong for all-manual accounts: ${JSON.stringify(repliesVal)}`);

  await page.click('[data-sheet-confirm]');
  await page.waitForSelector('.desk-v1-camp-state-pill', { timeout: 4000 });
  const activeText = (await page.textContent('.desk-v1-camp-state-pill').catch(() => '') || '');
  /Active/.test(activeText)
    ? ok(`zero connected (all-manual) accounts: Confirm — Start campaign still completes to Active: "${activeText.trim()}"`)
    : fail(`all-manual accounts: campaign not Active after Start confirm: ${JSON.stringify(activeText)}`);

  reportUncaught(pageErrors, '[all-manual-accounts]');
  await ctx.close();
}

async function runStartSheetGateMissingEnd(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser);
  const id = await draftInProject(page, 'engulfing_scanner', { accounts: ['ch-x-ron'], cadence: { per_week: 2 }, end: { date: null, post_cap: null } });
  // ⑥'s own Start is already disabled for a missing end (runLaunchMissingLinks);
  // the sheet is the second gate — open it directly.
  await page.evaluate((cid) => window.deskV1OpenStartSheet(cid), id);
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 4000 });

  const confirmDisabled = await page.$eval('[data-sheet-confirm]', (b) => b.disabled);
  confirmDisabled
    ? ok('Start sheet Confirm is disabled when validatePlan is not ok (missing end)')
    : fail('Start sheet Confirm should be disabled when the plan is missing its end bound');

  const noteText = (await page.textContent('.desk-v1-rules-sheet-note').catch(() => '') || '');
  /end date \(When\)/.test(noteText) && !/step \d/.test(noteText)
    ? ok(`Start sheet names the missing bound with its map stop: "${noteText.trim()}"`)
    : fail(`Start sheet note wrong: ${JSON.stringify(noteText)}`);

  reportUncaught(pageErrors, '[start-sheet-gate]');
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runPhoneLayout (Proposed summary collapse +
// Start sheet docking; the popover-docking assertion retired with it).
async function runProposedPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToProposedCampaign(page, 'camp-2');

  const channelsHidden = await page.$eval('.desk-v1-camp-summary-group[data-summary-group="channels"]', (el) => getComputedStyle(el).display).catch(() => '');
  channelsHidden === 'none'
    ? ok('§11: phone collapses the Proposed summary\'s Channels group')
    : fail(`§11: Channels group should be hidden on phone, got ${JSON.stringify(channelsHidden)}`);

  await page.click('[data-start-campaign]');
  await page.waitForSelector('.desk-v1-rules-sheet', { timeout: 2000 });
  const sheetBox = await page.$eval('.desk-v1-rules-sheet', (el) => el.getBoundingClientRect());
  Math.abs(sheetBox.width - 390) < 2
    ? ok(`§11: Start sheet docks full-width on phone (${sheetBox.width.toFixed(0)}px)`)
    : fail(`§11: Start sheet not full-width on phone: ${sheetBox.width}px`);
  const confirmHeight = await page.$eval('[data-sheet-confirm]', (el) => el.getBoundingClientRect().height);
  confirmHeight >= 44
    ? ok(`§11: Confirm button is touch-sized (${confirmHeight.toFixed(0)}px)`)
    : fail(`§11: Confirm button too short for touch: ${confirmHeight}px`);
  await page.keyboard.press('Escape');

  reportUncaught(pageErrors, '[proposed-phone]');
  await ctx.close();
}

// re-homed from desk-v1-rules.mjs runPosyInstructions (INS-01/03: Before/After
// on a plain instruction, a widening one confirms first). INS-02 (a durable
// instruction becomes a rule chip + Undo removes it) retired with the chips.
async function runPosyInstructions(browser) {
  const { ctx, page, pageErrors } = await newTonePage(browser, { ls: {} });
  await navToProposedCampaign(page, 'camp-1');

  const input = '#desk-v1-camp-posy-input';
  await page.waitForSelector(input, { timeout: 4000 });

  // Send starts the §5 task lifecycle (real wall-clock, up to ~4.1s to Ready).
  await page.fill(input, 'Focus this week on the video piece');
  await page.keyboard.press('Enter');
  await page.waitForSelector('.desk-v1-rules-posyreply', { timeout: 6500 });
  const reply = (await page.textContent('.desk-v1-rules-posyreply').catch(() => '') || '');
  /Before/.test(reply) && /After/.test(reply)
    ? ok(`plain instruction renders Before/After: "${reply.replace(/\s+/g, ' ').trim().slice(0, 90)}..."`)
    : fail(`Before/After reply missing: ${JSON.stringify(reply)}`);

  await page.fill(input, 'Turn on paid promotion for this');
  await page.keyboard.press('Enter');
  const posyConfirmText = await waitForWideningSheet(page, 6500).catch(() => null);
  posyConfirmText
    ? ok('a widening Posy instruction opens the confirm sheet before applying')
    : fail('widening instruction should have opened the confirm sheet');
  await respondToWideningSheet(page, true);

  reportUncaught(pageErrors, '[posy-instructions]');
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
    console.log('desk-v1-map: R2-3b retirements (setup steps, rules popover, Conversations tab)');
    await runR23bRetirements(browser);
    console.log('desk-v1-map: old conversations deep link -> Engagement');
    await runConversationsDeepLink(browser);
    console.log('desk-v1-map: 390px stepper keeps the current stop in view');
    await runMobileStepper(browser);
    console.log('desk-v1-map: re-homed - Proposed page (from desk-v1-rules.mjs)');
    for (const tone of TONES) await runProposedRenderChecks(browser, tone);
    await runGoalEdit(browser);
    await runGoalDateEdit(browser);
    await runValidatePlanMissingEnd(browser);
    await runBlockerAnswer(browser);
    await runStartSheet(browser);
    await runPosyInstructions(browser);
    await runProposedPhoneLayout(browser);
    console.log('desk-v1-map: re-homed - Start sheet from Launch (from desk-v1-setup.mjs)');
    await runDraftStartSheetFromLaunch(browser);
    await runAllManualAccountsReplies(browser);
    await runStartSheetGateMissingEnd(browser);
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
