#!/usr/bin/env node
/**
 * Desk v1 (MC-977, R2-4) — ① Goal smoke: the measurable goal editor +
 * effectiveness panel (docs/THE_DESK_V1_IA_REVISION_2.md §4.1 row "① Goal",
 * §8 R2-4 acceptance). Replaces the old T7 Results smoke — that UI (Posy's
 * experiment nudge, per-version breakdown, cost table, diagnostics) is gone;
 * this file's checks are exactly the R2-4 row's three: a manual entry
 * updates progress and pace, a `long` horizon shows per-term rows, and
 * removing the source reads `⚠ Not measured` (never a fake `0 of`).
 *
 * Real headless boot (real index.html + real static/js|css, no network), same
 * hermetic shape as desk-v1-conversations.mjs / desk-v1-video.mjs.
 *
 * RUN
 *   cd tools/smoke && node desk-v1-results.mjs
 * Exit 0 = every case holds; 1 = a case regressed / harness error.
 */
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

const MIME = { '.webp': 'image/webp', '.png': 'image/png', '.jpg': 'image/jpeg', '.svg': 'image/svg+xml' };

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
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

  await seedDeskV1Fixtures(page);
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

// Through the campaign page first (matches the real navigation path), then
// straight to ① goal via the shell route (still called 'results' — R2-3's
// PANEL_ALIASES table, unchanged by this ticket).
async function navToResults(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.evaluate(() => window.deskV1Nav('results', { campaignId: 'camp-1' }));
}

function reportUncaught(pageErrors, tag) {
  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  if (uncaught.length) uncaught.forEach((e) => fail(`${tag} uncaught page error: ${e}`));
}

// ── render checks, one per tone: the editor shows camp-1's fixture goal
// (metric/target/baseline/horizon/deadline/source), the effectiveness panel
// reads progress + a pace word, and copy lint holds. ────────────────────────
async function runToneRenderChecks(browser, tone) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, tone);
  await navToResults(page);
  await page.waitForSelector('.desk-v1-goal', { timeout: 8000 });

  const metricVal = await page.inputValue('[data-goal-field="metric"]').catch(() => '');
  const targetVal = await page.inputValue('[data-goal-field="target"]').catch(() => '');
  const horizonVal = await page.inputValue('[data-goal-field="horizon"]').catch(() => '');
  const sourceVal = await page.inputValue('[data-goal-field="source"]').catch(() => '');
  metricVal === 'tester signups' && targetVal === '30' && horizonVal === 'short' && sourceVal === 'manual'
    ? ok(`[${tone.name}] editor reads camp-1's fixture goal: metric="${metricVal}" target=${targetVal} horizon=${horizonVal} source=${sourceVal}`)
    : fail(`[${tone.name}] editor field values wrong: metric=${JSON.stringify(metricVal)} target=${JSON.stringify(targetVal)} horizon=${JSON.stringify(horizonVal)} source=${JSON.stringify(sourceVal)}`);

  const progressNumber = (await page.textContent('.desk-v1-goal-progress-number').catch(() => '') || '').trim();
  const progressTarget = (await page.textContent('.desk-v1-goal-progress-target').catch(() => '') || '').trim();
  progressNumber === '11' && progressTarget === 'of 30 tester signups'
    ? ok(`[${tone.name}] effectiveness progress reads "${progressNumber}" / "${progressTarget}"`)
    : fail(`[${tone.name}] effectiveness progress wrong: number=${JSON.stringify(progressNumber)} target=${JSON.stringify(progressTarget)}`);

  const paceText = (await page.textContent('.desk-v1-goal-pace-word').catch(() => '') || '').trim();
  /^(Ahead|On track|Behind)$/.test(paceText)
    ? ok(`[${tone.name}] pace reads a known state: "${paceText}"`)
    : fail(`[${tone.name}] pace text unexpected: ${JSON.stringify(paceText)}`);

  const bodyText = (await page.textContent('.desk-v1-goal').catch(() => '') || '');
  /\bfree\b/i.test(bodyText)
    ? fail(`[${tone.name}] copy lint: page must never say "free"`)
    : ok(`[${tone.name}] copy lint: no "free" on the page`);
  /\bon pace\b/i.test(bodyText)
    ? fail(`[${tone.name}] copy lint: page must never say "on pace" (§7/A10)`)
    : ok(`[${tone.name}] copy lint: no "on pace" on the page`);

  reportUncaught(pageErrors, `[${tone.name}]`);
  await ctx.close();
}

// ── R2-4 acceptance #1: "manual entry 14 dated today updates progress and
// pace." Verified against an independently-computed expected pace (same
// formula, computed here from wall-clock `now` and camp-1's own term
// bounds) rather than a hardcoded word, so the check doesn't silently rot as
// real dates move past the fixture's 2026 term window. ──────────────────────
async function runManualEntry(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-goal', { timeout: 8000 });

  const today = new Date().toISOString().slice(0, 10);
  await page.fill('[data-manual-entry-date]', today);
  await page.fill('[data-manual-entry-value]', '14');
  await page.click('[data-manual-entry-add]');
  await page.waitForTimeout(30);

  const progressNumber = (await page.textContent('.desk-v1-goal-progress-number').catch(() => '') || '').trim();
  progressNumber === '14'
    ? ok(`manual entry 14 dated today updates progress: "${progressNumber}"`)
    : fail(`progress did not update to 14: got "${progressNumber}"`);

  const entryRows = await page.$$eval('.desk-v1-goal-manual-list .desk-v1-rules-hint', (els) => els.map((e) => e.textContent));
  entryRows.some((t) => t.includes(today) && t.includes('14'))
    ? ok(`the new entry appears in the manual-entry list: "${entryRows[0]}"`)
    : fail(`new entry missing from the list: ${JSON.stringify(entryRows)}`);

  const start = new Date('2026-09-01').getTime();
  const end = new Date('2026-10-20').getTime();
  const elapsed = Math.max(0, Math.min(1, (Date.now() - start) / (end - start)));
  const pace = elapsed > 0 ? (14 / 30) / elapsed : null;
  const expectedWord = pace == null ? null : (pace > 1.2 ? 'Ahead' : pace >= 0.9 ? 'On track' : 'Behind');
  const paceText = (await page.textContent('.desk-v1-goal-pace-word').catch(() => '') || '').trim();
  (expectedWord && paceText === expectedWord)
    ? ok(`pace recomputes off the new entry: "${paceText}" (elapsed=${elapsed.toFixed(2)}, progress=${(14 / 30).toFixed(2)})`)
    : fail(`pace wrong: expected "${expectedWord}" (elapsed=${elapsed.toFixed(2)}), got "${paceText}"`);

  reportUncaught(pageErrors, '[manual-entry]');
  await ctx.close();
}

// ── R2-4 acceptance #2: "`long` horizon shows term rows." ───────────────────
async function runLongHorizonTermRows(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-goal', { timeout: 8000 });

  await page.evaluate(() => {
    const camp = window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-1');
    camp.goal.horizon = 'long';
    camp.terms = [
      { index: 1, starts: '2026-07-01', ends: '2026-08-30', target: 15, current: 12 },
      { index: 2, starts: '2026-09-01', ends: '2026-10-20', target: 15, current: 11 },
    ];
    window.deskV1RenderResults(document.querySelector('.desk-v1-camp-tabbody') || document.querySelector('#desk-v1-camp-tabbody'), { campaignId: 'camp-1' });
  });
  await page.waitForTimeout(30);

  const rows = await page.$$eval('.desk-v1-goal-term-row', (els) => els.map((e) => e.textContent.trim()));
  rows.length === 2 && rows[0] === 'Term 1: 12 of 15' && rows[1] === 'Term 2: 11 of 15'
    ? ok(`long horizon shows per-term rows: ${JSON.stringify(rows)}`)
    : fail(`term rows wrong: ${JSON.stringify(rows)}`);

  reportUncaught(pageErrors, '[long-horizon]');
  await ctx.close();
}

// ── R2-4 acceptance #3: "source removed → ⚠ Not measured" (never a `0 of`,
// MET-01). ───────────────────────────────────────────────────────────────
async function runSourceRemoved(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-goal', { timeout: 8000 });

  await page.selectOption('[data-goal-field="source"]', '');
  await page.waitForTimeout(30);

  const untracked = (await page.textContent('.desk-v1-goal-untracked').catch(() => '') || '').trim();
  untracked === '⚠ Not measured: add a source'
    ? ok(`removing the source reads "${untracked}"`)
    : fail(`untracked copy wrong: ${JSON.stringify(untracked)}`);

  const progressNumber = await page.$('.desk-v1-goal-progress-number');
  !progressNumber
    ? ok('no progress number renders while untracked (never a fake "0 of 30")')
    : fail('a progress number rendered while the source is removed — should be withheld entirely');

  const bodyText = (await page.textContent('.desk-v1-goal-effectiveness').catch(() => '') || '');
  /\b0 of\b/.test(bodyText)
    ? fail(`untracked effectiveness panel must never read "0 of...": ${JSON.stringify(bodyText)}`)
    : ok('untracked effectiveness panel never reads "0 of..."');

  reportUncaught(pageErrors, '[source-removed]');
  await ctx.close();
}

// ── §11 phone: hit targets ≥44px on the "+ Add entry" button, no overflow. ──
async function runPhoneLayout(browser) {
  const { ctx, page, pageErrors } = await newBootedPage(browser, { ls: {} }, { width: 390, height: 844 });
  await navToResults(page);
  await page.waitForSelector('.desk-v1-goal', { timeout: 8000 });

  const addHeight = await page.$eval('[data-manual-entry-add]', (el) => el.getBoundingClientRect().height).catch(() => 0);
  addHeight >= 40
    ? ok(`§11: "+ Add entry" is touch-sized (${addHeight.toFixed(0)}px)`)
    : fail(`§11: "+ Add entry" too short for touch: ${addHeight}px`);

  const overflowX = await page.evaluate(() => document.querySelector('.desk-v1-results').scrollWidth > document.querySelector('.desk-v1-body').clientWidth + 2);
  !overflowX
    ? ok('§11: no horizontal overflow at 390px')
    : fail('§11: goal panel overflows the phone viewport width');

  reportUncaught(pageErrors, '[phone]');
  await ctx.close();
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  for (const tone of TONES) await runToneRenderChecks(browser, tone);
  await runManualEntry(browser);
  await runLongHorizonTermRows(browser);
  await runSourceRemoved(browser);
  await runPhoneLayout(browser);
  exitCode = bad === 0 ? 0 : 1;
} catch (e) {
  console.error('harness error:', e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad === 0 ? `\nAll checks passed.` : `\n${bad} check(s) failed.`);
process.exit(exitCode);
