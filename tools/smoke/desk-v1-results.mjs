#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R0 plan T7) — Results smoke (no frame drawn, gap map C6;
 * built from THE_DESK_V1_UI.md §7 text alone).
 *
 * Closes: A10 (forecasts labelled as estimates, never "on pace"; missing
 * data never renders as a fake 0).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-conversations.mjs / desk-v1-video.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-results.mjs
 * Exit 0 = every case holds (render checks in all three tones, interaction
 * checks once, phone layout once); 1 = a case regressed / harness error.
 * Also writes docs/desk_v1/screens/t7_results_{desktop_1440,phone_390}.png
 * (default tone only).
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

const PID = 'smoke_deskv1results';
const PROJECTS = [{
  id: PID, name: 'Desk v1 results smoke', status: 'active', domain: 'general', emoji: '🧪',
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

// Through the campaign page first (matches the real navigation path), then
// straight to results via the shell route T0a already registers — same
// two-step precedent T4/T6's smokes use.
async function navToResults(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('results', { campaignId: 'camp-1' }));
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: goal + forecast wording, per-version
// outcomes with honest delayed/n/a (never a fake 0), costs with the
// cost-per-outcome line withheld while ads is unmeasured, diagnostics last. ──
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToResults(page);
  await page.waitForSelector('.desk-v1-results', { timeout: 8000 });

  const goalNumber = (await page.textContent('.desk-v1-results-goal-number').catch(() => '') || '').trim();
  const goalTarget = (await page.textContent('.desk-v1-results-goal-target').catch(() => '') || '').trim();
  goalNumber === '11' && goalTarget === 'of 30 tester signups'
    ? ok(`[${tone.name}] goal reads "${goalNumber}" / "${goalTarget}"`)
    : fail(`[${tone.name}] goal wrong: number=${JSON.stringify(goalNumber)} target=${JSON.stringify(goalTarget)}`);

  const forecast = (await page.textContent('.desk-v1-results-forecast').catch(() => '') || '').replace(/\s+/g, ' ').trim();
  /Projected 26 of 30/.test(forecast) && /below target/.test(forecast) && /estimate/.test(forecast)
    ? ok(`[${tone.name}] forecast reads: "${forecast}"`)
    : fail(`[${tone.name}] forecast wrong: ${JSON.stringify(forecast)}`);
  /on pace/i.test(forecast)
    ? fail(`[${tone.name}] forecast must never say "on pace": ${JSON.stringify(forecast)}`)
    : ok(`[${tone.name}] forecast never says "on pace"`);

  // A10 + MET-01: per-version rows — verified/measured shows a real number,
  // the two unmeasured rows show their honest label, never "0".
  const rows = await page.$$eval('.desk-v1-results-version-row', (els) => els.map((e) => ({
    outcome: (e.querySelector('.desk-v1-results-outcome-word') || {}).textContent,
    metric: (e.querySelector('.desk-v1-results-version-metric') || {}).textContent,
    contrib: (e.querySelector('.desk-v1-results-version-contrib') || {}).textContent,
  })));
  rows.length === 3
    ? ok(`[${tone.name}] "What went out" lists 3 version rows`)
    : fail(`[${tone.name}] expected 3 version rows, got ${rows.length}`);
  const published = rows.find((r) => /Verified published/.test(r.outcome || ''));
  published && published.metric.trim() === '8 signups' && /\+8/.test(published.contrib)
    ? ok(`[${tone.name}] the verified row shows a real measured metric: "${published.metric.trim()}"`)
    : fail(`[${tone.name}] verified row wrong: ${JSON.stringify(published)}`);
  const unknownRows = rows.filter((r) => /Unknown outcome/.test(r.outcome || ''));
  unknownRows.length === 2
    ? ok(`[${tone.name}] the 2 unreported rows both read "? Unknown outcome"`)
    : fail(`[${tone.name}] expected 2 unknown-outcome rows, got ${unknownRows.length}`);
  const metricTexts = unknownRows.map((r) => r.metric.trim());
  metricTexts.includes('delayed') && metricTexts.includes('n/a')
    ? ok(`[${tone.name}] missing outcomes read "delayed"/"n/a", never a fake 0: ${JSON.stringify(metricTexts)}`)
    : fail(`[${tone.name}] missing-outcome labels wrong: ${JSON.stringify(metricTexts)}`);
  metricTexts.some((t) => t === '0')
    ? fail(`[${tone.name}] a missing outcome rendered as a bare "0"`)
    : ok(`[${tone.name}] no missing outcome rendered as a bare "0"`);

  // MET-02: ads is unmeasured (null) in the fixture — cost-per-outcome must
  // be withheld, with an explanatory note instead.
  const costRows = await page.$$eval('.desk-v1-results-cost-row', (els) => els.map((e) => {
    const spans = e.querySelectorAll('span');
    return { label: (spans[0] || {}).textContent, value: (spans[1] || {}).textContent };
  }));
  const adsRow = costRows.find((r) => r.label === 'Ads');
  adsRow && adsRow.value === 'n/a'
    ? ok(`[${tone.name}] unmeasured Ads cost reads "n/a", not $0: "${adsRow.label} ${adsRow.value}"`)
    : fail(`[${tone.name}] Ads cost row wrong: ${JSON.stringify(adsRow)}`);
  const perOutcomeRow = await page.$('.desk-v1-results-cost-row-total');
  perOutcomeRow
    ? fail(`[${tone.name}] cost-per-outcome should be hidden while Ads is unmeasured`)
    : ok(`[${tone.name}] cost-per-outcome correctly withheld (not every cost category is measured)`);
  const costNote = (await page.textContent('.desk-v1-results-cost-note').catch(() => '') || '');
  /hidden until/.test(costNote)
    ? ok(`[${tone.name}] cost note explains the withheld line: "${costNote.trim()}"`)
    : fail(`[${tone.name}] cost note missing/wrong: ${JSON.stringify(costNote)}`);

  // §7: diagnostics last, small, never a gate — exact fixture numbers.
  const diag = (await page.textContent('.desk-v1-results-diagnostics').catch(() => '') || '').trim();
  diag === 'You edited 5 of 7 before approval.'
    ? ok(`[${tone.name}] diagnostics reads the exact fixture line: "${diag}"`)
    : fail(`[${tone.name}] diagnostics wrong: ${JSON.stringify(diag)}`);

  // Posy's read: exactly one proposed experiment, with both actions present.
  const posyText = (await page.textContent('.desk-v1-results-posy-text').catch(() => '') || '').trim();
  posyText.length > 0
    ? ok(`[${tone.name}] Posy's read renders: "${posyText.slice(0, 60)}..."`)
    : fail(`[${tone.name}] Posy's read is empty`);
  const hasAccept = !!(await page.$('[data-results-experiment-accept]'));
  const hasDismiss = !!(await page.$('[data-results-experiment-dismiss]'));
  hasAccept && hasDismiss
    ? ok(`[${tone.name}] "Set up experiment" / "Not now" both present`)
    : fail(`[${tone.name}] experiment actions missing: accept=${hasAccept} dismiss=${hasDismiss}`);

  const bodyText = (await page.textContent('.desk-v1-results').catch(() => '') || '');
  /\bfree\b/i.test(bodyText)
    ? fail(`[${tone.name}] copy lint: page must never say "free"`)
    : ok(`[${tone.name}] copy lint: no "free" on the page`);
  /posts automatically/i.test(bodyText)
    ? fail(`[${tone.name}] copy lint: page must never say "posts automatically"`)
    : ok(`[${tone.name}] copy lint: no "posts automatically" on the page`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── Accepting the experiment creates a real client-side family (Content
// list, once T2a lands) via the command bus, with Undo removing it — same
// "fixtures only, every drop a command with an inverse" contract every
// other R0 surface follows (§10; ground rule 3). ────────────────────────────
async function runAcceptExperiment(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  const requests = [];
  page.on('request', (r) => { if (r.url().includes('/api/')) requests.push(r.url()); });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-results', { timeout: 8000 });

  const before = await page.evaluate(() => (window.DeskV1Fixtures.families || []).length);
  await page.click('[data-results-experiment-accept]');
  await page.waitForSelector('.toast', { timeout: 2000 }).catch(() => {});
  const toastText = (await page.textContent('.toast').catch(() => '') || '');
  /Added/.test(toastText) && /Content/.test(toastText)
    ? ok(`accepting fires the commandBus toast: "${toastText.trim()}"`)
    : fail(`accept toast missing/wrong: ${JSON.stringify(toastText)}`);

  const after = await page.evaluate(() => (window.DeskV1Fixtures.families || []).length);
  after === before + 1
    ? ok(`accepting pushes exactly one new planned family onto DeskV1Fixtures.families (${before} -> ${after})`)
    : fail(`expected families to grow by 1, got ${before} -> ${after}`);

  const confirm = (await page.textContent('.desk-v1-results-posy-confirm').catch(() => '') || '');
  /Added/.test(confirm)
    ? ok(`the Posy card swaps to a confirmation line: "${confirm.trim()}"`)
    : fail(`confirmation line missing/wrong: ${JSON.stringify(confirm)}`);

  const apiCalls = requests.filter((u) => !/\/api\/(projects|config|characters)$/.test(u));
  apiCalls.length === 0
    ? ok('accepting the experiment made no backend API call — fixtures only, per R0 ground rules')
    : fail(`accept should not hit the network, but called: ${JSON.stringify(apiCalls)}`);

  // Undo removes the family again (§10: every command carries its inverse).
  await page.click('.toast .toast-btn');
  await page.waitForTimeout(30);
  const afterUndo = await page.evaluate(() => (window.DeskV1Fixtures.families || []).length);
  afterUndo === before
    ? ok(`Undo removes the planned family again (back to ${afterUndo})`)
    : fail(`Undo did not restore family count: expected ${before}, got ${afterUndo}`);

  reportUncaught(pageErrors, '[accept-experiment]');
  await ctx.close();
}

// ── "Not now" declines without mutating fixtures. ───────────────────────────
async function runDismissExperiment(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-results', { timeout: 8000 });

  const before = await page.evaluate(() => (window.DeskV1Fixtures.families || []).length);
  await page.click('[data-results-experiment-dismiss]');
  await page.waitForTimeout(30);
  const posyGone = await page.$('.desk-v1-results-posy');
  !posyGone
    ? ok('"Not now" clears the Posy card')
    : fail('"Not now" should clear the Posy card');
  const after = await page.evaluate(() => (window.DeskV1Fixtures.families || []).length);
  after === before
    ? ok('"Not now" does not create a family')
    : fail(`"Not now" should not mutate fixtures: ${before} -> ${after}`);

  reportUncaught(pageErrors, '[dismiss-experiment]');
  await ctx.close();
}

// ── §11 phone: hit targets ≥44px on the experiment actions. ─────────────────
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-results', { timeout: 8000 });

  const acceptHeight = await page.$eval('[data-results-experiment-accept]', (el) => el.getBoundingClientRect().height).catch(() => 0);
  acceptHeight >= 40
    ? ok(`§11: "Set up experiment" is touch-sized (${acceptHeight.toFixed(0)}px)`)
    : fail(`§11: "Set up experiment" too short for touch: ${acceptHeight}px`);

  const overflowX = await page.evaluate(() => document.querySelector('.desk-v1-results').scrollWidth > document.querySelector('.desk-v1-body').clientWidth + 2);
  !overflowX
    ? ok('§11: no horizontal overflow at 390px')
    : fail('§11: results page overflows the phone viewport width');

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

// ── screenshots (default tone only, per the ticket brief). ─────────────────
async function captureScreenshots(browser) {
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 1440, height: 950 });
    await navToResults(page);
    await page.waitForSelector('.desk-v1-results', { timeout: 8000 });
    await page.screenshot({ path: resolve(SHOT_DIR, 't7_results_desktop_1440.png') });
    ok('desktop screenshot saved: t7_results_desktop_1440.png');
    await ctx.close();
  }
  {
    const { ctx, page } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
    await navToResults(page);
    await page.waitForSelector('.desk-v1-results', { timeout: 8000 });
    await page.screenshot({ path: resolve(SHOT_DIR, 't7_results_phone_390.png') });
    ok('phone screenshot saved: t7_results_phone_390.png');
    await ctx.close();
  }
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runAcceptExperiment(browser);
  await runDismissExperiment(browser);
  await runPhoneLayout(browser);
  await captureScreenshots(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);
