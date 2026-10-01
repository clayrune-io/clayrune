#!/usr/bin/env node
/**
 * MC-998 phase 4c: Usage Breakdown section (static/js/system-status.js
 * `_renderUsageBreakdownSection`, backed by GET /api/system/usage/breakdown
 * and GET /api/system/usage/windows — mc/blueprints/system_routes.py).
 *
 * Real headless boot (real index.html + real static/js/*.js + static/css/*.css
 * served verbatim, no server, no network) — same hermetic pattern as
 * usage-bar.mjs. The two new endpoints are stubbed with fixtures shaped
 * exactly like mc/usage_breakdown_aggregate.build_breakdown()'s payload.
 *
 * Covers:
 *  1. Desktop (1440): Breakdown section renders totals/tokens-per-point/
 *     segmented bar/ranking table from a populated fixture; `.ub-table-wrap`
 *     visible, `.ub-cards` hidden.
 *  2. Phone (390): same fixture via the SAME renderer (openSystemUsage's
 *     modal path) — `.ub-table-wrap` hidden, `.ub-cards` visible with the
 *     same row data (acceptance check 4: desktop and mobile reach the same
 *     breakdown).
 *  3. Changing the dimension/sort controls re-fetches
 *     /api/system/usage/breakdown with the new query params.
 *  4. All 5 spec empty states render distinguishably: No runs, No vendor
 *     percentage, Sampling has not begun, Insufficient calibration,
 *     Telemetry unavailable.
 *
 * RUN
 *   cd tools/smoke && node usage-breakdown.mjs
 * Exit 0 = all pass; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

// codex-only so the strip renders exactly one bar (review finding #6's
// entry point) without needing a second click target in the test.
const USAGE_FIXTURE = {
  available: true, top_models: [],
  provider_weekly_usage: { codex: { utilization: 55, resets_at: '2026-09-30T00:00:00+00:00' } },
};

const WINDOWS_FIXTURE = {
  windows: [{ resets_at: '2026-09-25T00:00:00+00:00', range_start: '2026-09-24T19:00:00+00:00', range_end: '2026-09-25T00:00:00+00:00', completed: true }],
  coverage_begins: '2026-08-01T00:00:00+00:00',
};

// Shaped exactly like mc/usage_breakdown_aggregate.build_breakdown()'s payload.
const POPULATED_FIXTURE = {
  provider: 'claude', window_kind: '5h', window_scope: 'all',
  range_start: '2026-09-28T15:00:00+00:00', range_end: '2026-09-28T20:00:00+00:00',
  coverage_begins: '2026-08-01T00:00:00+00:00',
  empty_state: null,
  totals: {
    session_count: 3,
    tokens: { input_fresh: 1000, input_cache_write: 0, input_cache_read: 0, input_processed_total: 12000, output_tokens: 4000 },
    loc: { added: 120, deleted: 30 },
    loc_unavailable_count: 1,
    token_coverage_unavailable_count: 0,
  },
  rankings: {
    rows: [
      { label: 'proj-a', input_processed_total: 8000, output_tokens: 2500, added: 100, session_count: 2 },
      { label: 'proj-b', input_processed_total: 4000, output_tokens: 1500, added: 20, session_count: 1 },
    ],
    unknown_count: 0, missing_data_count: 0,
  },
  dimension: 'project', sort_by: 'input',
  tokens_per_point: { status: 'ok', median: 50000, p10: 40000, p90: 60000, sample_count: 8, note: 'Indicative: account-wide bar' },
  bar_change: { status: 'ok', delta_pp: 10, earliest: '2026-09-28T15:00:00+00:00', latest: '2026-09-28T19:55:00+00:00' },
  segmented_bar: { status: 'ok', bar_change_pp: 10, estimated_pp: 6, unattributed_pp: 4, range_pp: [5, 7] },
};

const NO_RUNS_FIXTURE = {
  ...POPULATED_FIXTURE, empty_state: 'no_runs',
  totals: { session_count: 0, tokens: { input_fresh: null, input_cache_write: null, input_cache_read: null, input_processed_total: null, output_tokens: null }, loc: { added: null, deleted: null }, loc_unavailable_count: 0, token_coverage_unavailable_count: 0 },
  rankings: { rows: [], unknown_count: 0, missing_data_count: 0 },
  bar_change: { status: 'insufficient_samples', delta_pp: null },
  segmented_bar: { status: 'insufficient_samples', estimated_pp: null, unattributed_pp: null, range_pp: null },
  tokens_per_point: { status: 'insufficient_samples', median: null, p10: null, p90: null, sample_count: 0, note: 'Indicative: account-wide bar' },
};

// Review finding #4 ("2026-09-28 re-review"): a session overlaps this
// window but couldn't be isolated to it (still mid-turn across the
// boundary) -- distinct from "No runs", and the excluded-session count must
// be visible, not silently dropped.
const INCOMPLETE_COVERAGE_FIXTURE = {
  ...POPULATED_FIXTURE, empty_state: 'incomplete_coverage',
  totals: { session_count: 0, tokens: { input_fresh: null, input_cache_write: null, input_cache_read: null, input_processed_total: null, output_tokens: null }, loc: { added: null, deleted: null }, loc_unavailable_count: 0, token_coverage_unavailable_count: 0, incomplete_coverage_session_count: 1 },
  rankings: { rows: [], unknown_count: 0, missing_data_count: 0 },
  bar_change: { status: 'insufficient_samples', delta_pp: null },
  segmented_bar: { status: 'insufficient_samples', estimated_pp: null, unattributed_pp: null, range_pp: null },
  tokens_per_point: { status: 'insufficient_samples', median: null, p10: null, p90: null, sample_count: 0, note: 'Indicative: account-wide bar' },
};

const NO_VENDOR_PCT_FIXTURE = {
  ...POPULATED_FIXTURE, empty_state: 'no_vendor_percentage',
  bar_change: { status: 'insufficient_samples', delta_pp: null },
  segmented_bar: { status: 'insufficient_samples', estimated_pp: null, unattributed_pp: null, range_pp: null },
};

const SAMPLING_NOT_BEGUN_FIXTURE = {
  ...POPULATED_FIXTURE, empty_state: 'sampling_not_begun', coverage_begins: null,
  bar_change: { status: 'insufficient_samples', delta_pp: null },
  segmented_bar: { status: 'insufficient_samples', estimated_pp: null, unattributed_pp: null, range_pp: null },
};

const INSUFFICIENT_CALIBRATION_FIXTURE = {
  ...POPULATED_FIXTURE,
  tokens_per_point: { status: 'insufficient_calibration', median: null, p10: null, p90: null, sample_count: 1, note: 'Indicative: account-wide bar' },
  segmented_bar: { status: 'insufficient_calibration', estimated_pp: null, unattributed_pp: 10, range_pp: null, bar_change_pp: 10 },
};

const TELEMETRY_UNAVAILABLE_FIXTURE = {
  ...POPULATED_FIXTURE,
  totals: { session_count: 2, tokens: { input_fresh: null, input_cache_write: null, input_cache_read: null, input_processed_total: null, output_tokens: null }, loc: { added: null, deleted: null }, loc_unavailable_count: 0, token_coverage_unavailable_count: 2 },
};

// Review finding #9: estimate (12) exceeds the observed vendor change (10) --
// unattributed_pp is negative (-2) and must render as a separate excess
// error, never as a literal negative "Unattributed" amount.
const ESTIMATE_EXCEEDS_OBSERVED_FIXTURE = {
  ...POPULATED_FIXTURE,
  segmented_bar: { status: 'estimate_exceeds_observed', bar_change_pp: 10, estimated_pp: 12, unattributed_pp: -2, range_pp: [9, 13] },
};

// Ron 2026-09-30 screenshot: vendor meter 48%, estimated Clayrune 30.83pp,
// unattributed 16.17pp, bar_change 47pp. The bar must read on the SAME 0-100
// scale as the meter (30.83% + 16.17%), not as a share of bar_change (66%).
const SEVEN_DAY_FIXTURE = {
  ...POPULATED_FIXTURE, window_kind: '7d',
  segmented_bar: { status: 'ok', bar_change_pp: 47, estimated_pp: 30.83, unattributed_pp: 16.17, range_pp: [28, 33] },
};

// Review finding #7's own reproduction: a Claude request in flight when
// Codex is selected before it resolves. Distinct session_count per provider
// (111 vs 222) is the tell for which one actually ended up on screen.
const CLAUDE_RACE_FIXTURE = { ...POPULATED_FIXTURE, provider: 'claude', totals: { ...POPULATED_FIXTURE.totals, session_count: 111 } };
const CODEX_RACE_FIXTURE = { ...POPULATED_FIXTURE, provider: 'codex', totals: { ...POPULATED_FIXTURE.totals, session_count: 222 } };

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

function routeHandler(breakdownBody, windowsBody, calls) {
  return (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (calls) calls.push(path + url.search);
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/system/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/system/usage') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(USAGE_FIXTURE) });
    if (path === '/api/system/usage/breakdown') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(breakdownBody) });
    if (path === '/api/system/usage/windows') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(windowsBody) });
    return route.abort();
  };
}

// MC-998 follow-up (Dave, Ron 2026-09-28): the Breakdown section moved OUT of
// the Usage modal/popover into its own Advanced-sidebar page (`usage-report`,
// `openUsageReport()`, `#usage-report-surface`) — same content/renderer,
// reached identically from desktop sidebar and mobile drawer. Both helpers
// below now open THAT surface; names kept as-is to avoid touching every call
// site in this file.
async function openDesktopPopover(browser, breakdownBody, windowsBody, calls) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', routeHandler(breakdownBody, windowsBody, calls));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
  await page.evaluate(() => { sidebarNav('usage-report'); });
  await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
  return { ctx, page, pageErrors };
}

async function openMobileModal(browser, breakdownBody, windowsBody, calls) {
  const ctx = await browser.newContext({ viewport: { width: 390, height: 844 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', routeHandler(breakdownBody, windowsBody, calls));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
  await page.evaluate(() => { mobileDrawerNav('usage-report'); });
  await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
  return { ctx, page, pageErrors };
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Desktop 1440: populated fixture renders totals/tpp/segbar/table ───
  {
    const { ctx, page, pageErrors } = await openDesktopPopover(browser, POPULATED_FIXTURE, WINDOWS_FIXTURE);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Breakdown/.test(text) ? ok('Breakdown section head present') : fail('Breakdown section head missing');
    /12\.0k/.test(text) ? ok('input tokens total (12.0k) rendered') : fail(`input total missing from: ${text.slice(0, 800)}`);
    /50\.0k/.test(text) ? ok('tokens-per-1% median (50.0k) rendered') : fail('tokens-per-point median missing');
    /range 5\.00%.{0,3}7\.00%/.test(text) ? ok('segmented bar renders its estimate range (5.00%-7.00%)') : fail(`range caveat missing from: ${text.slice(0, 800)}`);

    const segW = await page.$$eval('.ub-segbar > div', (els) => els.map((e) => [e.className, e.style.width]));
    JSON.stringify(segW) === JSON.stringify([['ub-segbar-estimated', '6%'], ['ub-segbar-unattributed', '4%']])
      ? ok('segbar on the absolute 0-100 scale: Clayrune 6%, Unattributed 4% (not 60% of bar_change)')
      : fail(`segbar widths wrong: ${JSON.stringify(segW)}`);
    /Change in this range: 10\.00% of 100%/.test(text)
      ? ok('segbar caption states the change in this range (10.00% of 100%)')
      : fail(`segbar caption missing from: ${text.slice(0, 800)}`);

    const tableVisible = await page.$eval('.ub-table-wrap', (el) => getComputedStyle(el).display !== 'none');
    tableVisible ? ok('desktop: .ub-table-wrap visible') : fail('.ub-table-wrap should be visible at 1440px');
    const cardsHidden = await page.$eval('.ub-cards', (el) => getComputedStyle(el).display === 'none');
    cardsHidden ? ok('desktop: .ub-cards hidden') : fail('.ub-cards should be display:none at 1440px');

    const rowLabels = await page.$$eval('.ub-table tbody tr td:first-child', (els) => els.map((e) => e.textContent.trim()));
    JSON.stringify(rowLabels) === JSON.stringify(['proj-a', 'proj-b'])
      ? ok('ranking table rows in fixture order (proj-a, proj-b)')
      : fail(`ranking rows wrong: ${JSON.stringify(rowLabels)}`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0 ? ok('desktop pass: no uncaught page errors') : uncaught.forEach((e) => fail('uncaught (desktop): ' + e));
    await ctx.close();
  }

  // ── 2. Phone 390: same fixture, same renderer, cards visible ─────────────
  {
    const { ctx, page, pageErrors } = await openMobileModal(browser, POPULATED_FIXTURE, WINDOWS_FIXTURE);
    const tableHidden = await page.$eval('.ub-table-wrap', (el) => getComputedStyle(el).display === 'none');
    tableHidden ? ok('phone: .ub-table-wrap hidden') : fail('.ub-table-wrap should be display:none at 390px');
    const cardsVisible = await page.$eval('.ub-cards', (el) => getComputedStyle(el).display !== 'none');
    cardsVisible ? ok('phone: .ub-cards visible') : fail('.ub-cards should be visible at 390px');

    const cardLabels = await page.$$eval('.ub-card .ub-card-label', (els) => els.map((e) => e.textContent.trim()));
    JSON.stringify(cardLabels) === JSON.stringify(['proj-a', 'proj-b'])
      ? ok('phone stacked cards carry the SAME rows as desktop (proj-a, proj-b)')
      : fail(`phone card rows wrong: ${JSON.stringify(cardLabels)}`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0 ? ok('phone pass: no uncaught page errors') : uncaught.forEach((e) => fail('uncaught (phone): ' + e));
    await ctx.close();
  }

  // ── 3. Changing dimension/sort re-fetches the breakdown with new params ──
  {
    const calls = [];
    const { ctx, page } = await openDesktopPopover(browser, POPULATED_FIXTURE, WINDOWS_FIXTURE, calls);
    calls.length = 0;
    await page.selectOption('.ub-select:has(option[value="character"])', 'character');
    await page.waitForTimeout(200);
    calls.some((c) => c.includes('/api/system/usage/breakdown') && c.includes('dimension=character'))
      ? ok('selecting dimension=character re-fetches breakdown with dimension=character')
      : fail(`dimension change did not re-fetch correctly, saw: ${JSON.stringify(calls)}`);

    calls.length = 0;
    await page.selectOption('.ub-select:has(option[value="output"])', 'output');
    await page.waitForTimeout(200);
    calls.some((c) => c.includes('/api/system/usage/breakdown') && c.includes('sort=output'))
      ? ok('selecting sort=output re-fetches breakdown with sort=output')
      : fail(`sort change did not re-fetch correctly, saw: ${JSON.stringify(calls)}`);
    await ctx.close();
  }

  // ── 4. Five spec empty states render distinguishably ──────────────────────
  {
    const cases = [
      { fixture: NO_RUNS_FIXTURE, pattern: /No runs/i, name: 'No runs' },
      { fixture: NO_VENDOR_PCT_FIXTURE, pattern: /No vendor percentage/i, name: 'No vendor percentage' },
      { fixture: SAMPLING_NOT_BEGUN_FIXTURE, pattern: /Sampling has not begun/i, name: 'Sampling has not begun' },
      { fixture: INSUFFICIENT_CALIBRATION_FIXTURE, pattern: /Insufficient calibration/i, name: 'Insufficient calibration' },
      { fixture: TELEMETRY_UNAVAILABLE_FIXTURE, pattern: /Telemetry unavailable/i, name: 'Telemetry unavailable' },
    ];
    for (const c of cases) {
      const { ctx, page } = await openDesktopPopover(browser, c.fixture, WINDOWS_FIXTURE);
      const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
      c.pattern.test(text) ? ok(`empty state "${c.name}" renders distinguishably`) : fail(`empty state "${c.name}" not found in: ${text.slice(0, 500)}`);
      await ctx.close();
    }
  }

  // ── 4b. Finding #4: "incomplete coverage" is NOT the same message as
  //        "No runs", and the excluded-session count is visible ───────────
  {
    const { ctx, page } = await openDesktopPopover(browser, INCOMPLETE_COVERAGE_FIXTURE, WINDOWS_FIXTURE);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    (!/No runs/i.test(text) && /not yet measurable/i.test(text))
      ? ok('incomplete-coverage empty state is distinct from "No runs"')
      : fail(`expected "Not yet measurable", not "No runs", in: ${text.slice(0, 500)}`);
    /1 session\(s\) overlap this window/i.test(text)
      ? ok('incomplete_coverage_session_count is surfaced, not silently dropped')
      : fail(`expected the excluded-session hint in: ${text.slice(0, 500)}`);
    await ctx.close();
  }

  // ── 5. Finding #9: unattributed bucket stays visible pre-calibration,
  //      and an over-estimate renders as an excess error, never a negative
  //      "Unattributed" amount ─────────────────────────────────────────────
  {
    const { ctx, page } = await openDesktopPopover(browser, INSUFFICIENT_CALIBRATION_FIXTURE, WINDOWS_FIXTURE);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Unattributed[\s\S]{0,40}10\.00%/.test(text)
      ? ok('pre-calibration: unattributed bucket (10.00%) stays visible, not hidden')
      : fail(`pre-calibration unattributed value missing from: ${text.slice(0, 800)}`);
    const segW = await page.$$eval('.ub-segbar > div', (els) => els.map((e) => [e.className, e.style.width]));
    JSON.stringify(segW) === JSON.stringify([['ub-segbar-unattributed', '10%']])
      ? ok('pre-calibration: a single Unattributed segment of width 10% (not the full-width unknown bar)')
      : fail(`pre-calibration segbar wrong: ${JSON.stringify(segW)}`);
    await ctx.close();
  }
  {
    const { ctx, page } = await openDesktopPopover(browser, ESTIMATE_EXCEEDS_OBSERVED_FIXTURE, WINDOWS_FIXTURE);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /exceeds[\s\S]{0,60}2\.00%/i.test(text)
      ? ok('estimate-exceeds-observed: excess (2.00%) rendered as a separate error')
      : fail(`excess-error text missing from: ${text.slice(0, 800)}`);
    const segW = await page.$$eval('.ub-segbar > div', (els) => els.map((e) => [e.className, e.style.width]));
    JSON.stringify(segW) === JSON.stringify([['ub-segbar-estimated', '12%']])
      ? ok('estimate-exceeds-observed: Clayrune segment is estimated_pp (12%) on the absolute scale, no Unattributed segment')
      : fail(`exceeds segbar wrong: ${JSON.stringify(segW)}`);
    /Unattributed.{0,10}-2\.00%/.test(text)
      ? fail('estimate-exceeds-observed: rendered a literal negative "Unattributed" amount')
      : ok('estimate-exceeds-observed: no literal negative Unattributed amount rendered');
    await ctx.close();
  }

  // ── 5b. Absolute-scale segbar: Ron's 7d screenshot (meter 48%) ──────────
  {
    const { ctx, page } = await openDesktopPopover(browser, SEVEN_DAY_FIXTURE, WINDOWS_FIXTURE);
    // Dark theme is the :root base; the default tone adds tone-warm (light).
    await page.evaluate(() => { document.body.classList.remove('tone-warm', 'tone-editorial'); });
    const segW = await page.$$eval('.ub-segbar > div', (els) => els.map((e) => [e.className, e.style.width]));
    JSON.stringify(segW) === JSON.stringify([['ub-segbar-estimated', '30.83%'], ['ub-segbar-unattributed', '16.17%']])
      ? ok('7d fixture: Clayrune 30.83% + Unattributed 16.17% (sum 47%, never 66%)')
      : fail(`7d segbar widths wrong: ${JSON.stringify(segW)}`);
    const px = await page.$eval('.ub-segbar', (bar) => {
      const w = bar.getBoundingClientRect().width;
      return [...bar.children].map((c) => c.getBoundingClientRect().width / w * 100);
    });
    (Math.abs(px[0] - 30.83) < 0.5 && Math.abs(px[1] - 16.17) < 0.5)
      ? ok(`7d fixture: rendered pixel shares ${px[0].toFixed(1)}% / ${px[1].toFixed(1)}% of the track`)
      : fail(`7d rendered shares wrong: ${JSON.stringify(px)}`);
    const hatched = await page.$eval('.ub-segbar-unattributed', (e) => getComputedStyle(e).backgroundImage.includes('repeating-linear-gradient'));
    hatched ? ok('Unattributed segment is hatched (distinct from Clayrune)') : fail('Unattributed segment lost its hatch style');
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Change in this range: 47\.00% of 100%/.test(text) ? ok('7d caption: "Change in this range: 47.00% of 100%"') : fail(`7d caption missing from: ${text.slice(0, 800)}`);
    const shot = process.env.SEGBAR_SCREENSHOT;
    if (shot) {
      const el = await page.$('.ub-segbar');
      const box = await el.boundingBox();
      await page.screenshot({ path: shot, clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 120), width: Math.min(1440, box.width + 40), height: 260 } });
    }
    await ctx.close();
  }
  {
    // Clamp: never wider than the track even on an implausible fixture.
    const { ctx, page } = await openDesktopPopover(browser, { ...POPULATED_FIXTURE, segmented_bar: { status: 'ok', bar_change_pp: 100, estimated_pp: 80, unattributed_pp: 60, range_pp: null } }, WINDOWS_FIXTURE);
    const segW = await page.$$eval('.ub-segbar > div', (els) => els.map((e) => e.style.width));
    JSON.stringify(segW) === JSON.stringify(['80%', '20%']) ? ok('total clamped to 100%: 80% + 60pp renders 80% + 20%') : fail(`clamp wrong: ${JSON.stringify(segW)}`);
    await ctx.close();
  }

  // ── 6. Finding #6 SUPERSEDED (MC-998 follow-up, Ron 2026-09-28): the strip
  //      no longer opens this report at all -- it shows its own small anchored
  //      popup with its OWN independent fetch (own dimension queries, own
  //      cache; see usage-bar-popup.mjs). What the original finding protected
  //      against (a click filling the report with the wrong provider) is now
  //      structural: clicking a bar cannot touch the report's `_ubProvider`
  //      selection at all. Assert exactly that decoupling. ─────────────────
  {
    const calls = [];
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.route('**/*', routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE, calls));
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip .usage-bar-item', { state: 'attached', timeout: 15000 });

    // Click the codex bar (opens the small popup, its own fetch pipeline)...
    await page.click('#usage-bar-strip .usage-bar-item');
    await page.waitForTimeout(200);
    const reportOpenedByBarClick = await page.$eval('#usage-report-surface', () => true).catch(() => false);
    !reportOpenedByBarClick
      ? ok('clicking the codex bar never opens the Usage report at all')
      : fail('clicking a bar should not open the Usage report');

    // ...then open the report separately: it must still default to claude/5h,
    // completely unaffected by which bar was just clicked.
    calls.length = 0;
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForTimeout(200);
    calls.some((c) => c.includes('/api/system/usage/breakdown') && c.includes('provider=claude') && c.includes('window_kind=5h'))
      ? ok('the report opens on its own claude/5h default, unaffected by the earlier codex bar click')
      : fail(`report should fetch claude/5h on its own, saw: ${JSON.stringify(calls)}`);
    const providerSelected = await page.$eval('.ub-select:has(option[value="claude"])', (el) => el.value).catch(() => null);
    providerSelected === 'claude'
      ? ok('report provider control stays on its own default (claude), not the bar’s codex')
      : fail(`provider control should still show claude, saw "${providerSelected}"`);

    await ctx.close();
  }

  // ── 7. Finding #7: an in-flight fetch never leaks a stale/superseded
  //      response into a newer selection, and a failed fetch surfaces an
  //      error instead of silently relabelling old data as current ────────
  {
    // 7a. Race: switch provider before the FIRST (slow) request resolves --
    // the newer request must win even though the older one settles later.
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    let breakdownCalls = 0;
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/system/usage/breakdown') {
        breakdownCalls++;
        const isFirst = breakdownCalls === 1;
        const provider = url.searchParams.get('provider');
        const body = provider === 'codex' ? CODEX_RACE_FIXTURE : CLAUDE_RACE_FIXTURE;
        if (isFirst) await new Promise((r) => setTimeout(r, 400));
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      }
      return routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE)(route);
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    // The initial claude/5h fetch (delayed 400ms) is now in flight; switch
    // to codex before it resolves.
    await page.selectOption('.ub-select:has(option[value="claude"])', 'codex');
    await page.waitForTimeout(600);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    (/222/.test(text) && !/111/.test(text))
      ? ok('race: newer selection (codex/222) wins even though claude/111 resolves later')
      : fail(`race: stale/superseded data leaked into the current selection: ${text.slice(0, 400)}`);
    const providerSelected = await page.$eval('.ub-select:has(option[value="claude"])', (el) => el.value);
    providerSelected === 'codex'
      ? ok('race: provider control still reflects codex after the late response')
      : fail(`race: provider control drifted to "${providerSelected}"`);
    await ctx.close();
  }
  {
    // 7b. A failed refresh keeps the last good render but surfaces an error
    // hint -- it must not silently pretend the stale data is current.
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    let fail500 = false;
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/system/usage/breakdown') {
        if (fail500) return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(POPULATED_FIXTURE) });
      }
      return routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE)(route);
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForSelector('#usage-report-surface .ub-table-wrap', { state: 'attached', timeout: 5000 });

    fail500 = true;
    await page.click('#ssp-usage-refresh-btn');
    await page.waitForTimeout(300);
    const text2 = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Last refresh failed/i.test(text2)
      ? ok('failed refresh surfaces an error hint')
      : fail(`failed refresh did not surface an error hint: ${text2.slice(0, 500)}`);
    /proj-a/.test(text2)
      ? ok('failed refresh keeps showing the last good data, not a blank/failed state')
      : fail('failed refresh should keep the last good render visible');
    await ctx.close();
  }
  {
    // 7c. A failed FIRST load (no prior cache) shows a distinct failed
    // message -- not the generic "not loaded yet", which reads as nothing
    // happened rather than an actual failure.
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/system/usage/breakdown') {
        return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
      }
      return routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE)(route);
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForTimeout(300);
    const text3 = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /failed to load/i.test(text3)
      ? ok('a failed first load shows a distinct failed message, not "not loaded yet"')
      : fail(`first-load failure should show a distinct error, saw: ${text3.slice(0, 500)}`);
    await ctx.close();
  }
  {
    // 7d. Finding #7's remaining P2 (windows-picker cache key): a failed
    // windows fetch after switching provider must NOT leave the PRIOR
    // provider's completed-window option selectable under the new one --
    // picking it would send that stale range to the new provider's endpoint.
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    let windowsProvider = null;
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/system/usage/windows') {
        windowsProvider = url.searchParams.get('provider');
        if (windowsProvider === 'codex') return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(WINDOWS_FIXTURE) });
      }
      return routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE)(route);
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForTimeout(200);
    await page.selectOption('.ub-select:has(option[value="claude"])', 'codex');
    await page.waitForTimeout(300);
    const rangeSelect = await page.$('select.ub-select[onchange*="\'range\'"]');
    const rangeOptCountAfter = rangeSelect ? await rangeSelect.$$eval('option', (els) => els.length) : null;
    const text4 = await page.$eval('.sys-status-popover', (el) => el.textContent);
    (rangeOptCountAfter === 1)
      ? ok('windows cache: a failed provider-switch fetch clears the range picker, no stale option')
      : fail(`stale window option still selectable after switching provider+failed fetch (${rangeOptCountAfter} option(s)): ${text4.slice(0, 400)}`);
    /completed windows unavailable/i.test(text4)
      ? ok('windows cache: failure state surfaced for the range picker')
      : fail(`expected a windows-unavailable hint in: ${text4.slice(0, 400)}`);
    await ctx.close();
  }

  // ── 8. MC-998: vendor reports NO window of this kind (windows: [] after a
  //      SUCCESSFUL load) -> say so, instead of "Insufficient samples"/"No
  //      runs" that read like a Clayrune data bug. Totals/rankings remain. ──
  {
    const NO_WINDOW_FIXTURE = {
      ...POPULATED_FIXTURE, provider: 'codex', empty_state: 'no_vendor_percentage',
      bar_change: { status: 'insufficient_samples', delta_pp: null },
      segmented_bar: { status: 'insufficient_samples', estimated_pp: null, unattributed_pp: null, range_pp: null },
      tokens_per_point: { status: 'insufficient_samples', median: null, p10: null, p90: null, sample_count: 0, note: 'Indicative: account-wide bar' },
    };
    const EMPTY_WINDOWS = { windows: [], coverage_begins: null };
    const { ctx, page } = await openDesktopPopover(browser, NO_WINDOW_FIXTURE, EMPTY_WINDOWS);
    await page.selectOption('.ub-select:has(option[value="claude"])', 'codex');
    await page.waitForTimeout(300);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Codex has not reported a 5-hour limit for this account in the last 90 days/.test(text)
      ? ok('empty windows list: "<Vendor> has not reported a 5-hour limit ... last 90 days" shown')
      : fail(`no-window message missing from: ${text.slice(0, 600)}`);
    (!/Insufficient samples/i.test(text) && !/No vendor percentage/i.test(text) && !/Tokens per 1%/.test(text))
      ? ok('empty windows list: no "Insufficient samples"/calibration states alongside it')
      : fail(`calibration state leaked next to the no-window message: ${text.slice(0, 600)}`);
    /proj-a/.test(text) ? ok('empty windows list: rankings still render') : fail('rankings should still render');
    const windowKindOpts = await page.$$eval('.ub-select option[value="5h"], .ub-select option[value="7d"]', (els) => els.map((e) => e.value));
    JSON.stringify(windowKindOpts) === JSON.stringify(['5h', '7d'])
      ? ok('empty windows list: the 5-hour option stays selectable')
      : fail(`window-kind options changed: ${JSON.stringify(windowKindOpts)}`);
    const shot = resolve(REPO_ROOT, '_scratch', 'mc998-no-vendor-window.png');
    await page.screenshot({ path: shot, fullPage: false });
    console.log('  screenshot: ' + shot);
    await ctx.close();
  }
  {
    // 8b. A FAILED windows fetch must not produce the message (error
    // behaviour unchanged), and neither may a windows cache for a prior
    // selection: claude -> [] (message), then codex -> 500 (no message).
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/system/usage/windows') {
        if (url.searchParams.get('provider') === 'codex') return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
        return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ windows: [], coverage_begins: null }) });
      }
      return routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE)(route);
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForTimeout(300);
    let text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Claude has not reported a 5-hour limit/.test(text)
      ? ok('windows [] for claude/5h shows the message (control for 8b)')
      : fail(`expected the no-window message for claude/5h in: ${text.slice(0, 500)}`);
    await page.selectOption('.ub-select:has(option[value="claude"])', 'codex');
    await page.waitForTimeout(300);
    text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    (!/has not reported a/i.test(text) && /completed windows unavailable/i.test(text))
      ? ok('failed windows fetch (after a prior empty one) does NOT show the no-window message')
      : fail(`no-window message shown for a failed windows fetch: ${text.slice(0, 500)}`);
    await ctx.close();
  }
  {
    // 8c. First-ever windows fetch fails: no message either.
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.route('**/*', async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname === '/api/system/usage/windows') return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
      return routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE)(route);
    });
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
    await page.evaluate(() => { sidebarNav('usage-report'); });
    await page.waitForSelector('#usage-report-surface .ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForTimeout(300);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    (!/has not reported a/i.test(text) && /Tokens per 1%/.test(text))
      ? ok('failed first windows fetch: no no-window message, normal calibration rows stay')
      : fail(`failed windows fetch misrendered: ${text.slice(0, 500)}`);
    await ctx.close();
  }

  // ── 9. MC-998 follow-up 6: input breakdown (Fresh / Cache reads / Cache
  //      writes with their share of input, writes split by TTL), the reworded
  //      incomplete-coverage hint, calibration evidence, the range label, and
  //      the model-unknown hint. Desktop totals + phone cards both. ─────────
  {
    const TOKEN_FIXTURE = {
      ...POPULATED_FIXTURE,
      totals: {
        ...POPULATED_FIXTURE.totals,
        tokens: { input_fresh: 40000000, input_cache_read: 1208000000, input_cache_write: 12000000,
                  input_processed_total: 1260000000, output_tokens: 9000000,
                  cache_write_5m: 2000000, cache_write_1h: 6000000, cache_write_ttl_unknown: 4000000 },
        incomplete_coverage_session_count: 2, model_unknown_session_count: 3,
      },
      rankings: { ...POPULATED_FIXTURE.rankings, rows: [
        { label: 'opus', input_processed_total: 1000, input_fresh: 100, input_cache_read: 800, input_cache_write: 100, output_tokens: 50, added: 1, session_count: 1 },
      ], mixed_session_count: 2, whole_session_attribution_count: 1 },
      tokens_per_point: { status: 'ok', median: 50000, pooled_rate: 50000, p10: 40000, p90: 60000, sample_count: 8, total_delta_pp: 41.5, run_count: 6, note: 'Indicative: account-wide bar',
        composition: { cache_read: { per_point: 48500, share: 0.971 }, cache_write: { per_point: 1200, share: 0.024 }, output: { per_point: 250, share: 0.005 }, fresh: { per_point: 80, share: 0.0000156 } } },
      segmented_bar: { status: 'ok', bar_change_pp: 10, estimated_pp: 6, unattributed_pp: 4, range_pp: [5, 7] },
    };
    const { ctx, page, pageErrors } = await openDesktopPopover(browser, TOKEN_FIXTURE, WINDOWS_FIXTURE);
    await page.evaluate(() => { document.body.classList.remove('tone-warm', 'tone-editorial'); });
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /Cache reads\s*1\.21B \(95\.9%\)/.test(text) || /Cache reads\s*1\.208B \(95\.9%\)/.test(text)
      ? ok('input breakdown: "Cache reads" shows tokens and its share of input')
      : fail(`cache-reads share missing from: ${text.slice(0, 900)}`);
    (/Fresh\s*\S+ \(3\.2%\)/.test(text) && /Cache writes\s*\S+ \(1\.0%\)/.test(text))
      ? ok('input breakdown: Fresh and Cache writes rows carry their shares')
      : fail(`fresh/writes shares missing from: ${text.slice(0, 900)}`);
    (/5-minute/.test(text) && /1-hour/.test(text) && /TTL unknown/.test(text))
      ? ok('cache writes split into 5-minute / 1-hour / TTL unknown')
      : fail(`TTL split rows missing from: ${text.slice(0, 900)}`);
    /their unmeasured portions are excluded/.test(text) && !/aren't isolated to it yet/.test(text)
      ? ok('incomplete-coverage hint reworded: unmeasured portions excluded, not whole sessions')
      : fail(`old/absent incomplete-coverage hint: ${text.slice(0, 900)}`);
    /41\.5pp over 6 stretches/.test(text) && !/n=8/.test(text) && !/6 runs/.test(text)
      ? ok('Tokens per 1%: calibration evidence "41.5pp over 6 stretches" replaces n= (and "runs")')
      : fail(`calibration evidence missing from: ${text.slice(0, 900)}`);
    /Tokens per 1% \(pooled rate\)/.test(text)
      ? ok('Tokens per 1% is labelled "pooled rate"')
      : fail(`pooled-rate label missing from: ${text.slice(0, 900)}`);
    (/Per point: cache reads 48\.5k \(97\.1%\) · cache writes 1\.2k \(2\.4%\) · output <1K \(0\.5%\) · fresh <1K \(0\.0%\)/.test(text)
      && /not how the vendor weights each type/.test(text))
      ? ok('per-point composition line + "volume, not vendor weighting" caption render')
      : fail(`composition line/caption missing from: ${text.slice(Math.max(0, text.indexOf("Per point")), text.indexOf("Per point") + 400)}`);
    (await page.$eval('.sys-status-popover', (el) =>
      [...el.querySelectorAll('span[title]')].some((n) => /^6 stretches$/.test(n.textContent) && /not an agent run/.test(n.title))))
      ? ok('"stretches" carries a tooltip saying it is not an agent run')
      : fail('stretches tooltip missing');
    /calibration-rate range/.test(text) && /not from bounds on attribution/.test(text)
      ? ok('estimate range labelled as calibration-rate variation, not attribution bounds')
      : fail(`range label missing from: ${text.slice(0, 900)}`);
    /3 session\(s\) have no recorded model/.test(text)
      ? ok('model-unknown sessions surfaced in a visible hint')
      : fail(`model-unknown hint missing from: ${text.slice(0, 900)}`);
    /2 session\(s\) ran on more than one model/.test(text)
      ? ok('mixed-model sessions noted under the model ranking')
      : fail(`mixed-session hint missing from: ${text.slice(0, 900)}`);
    const shot = resolve(REPO_ROOT, '_scratch', 'mc998-token-accounting-report-dark.png');
    await page.screenshot({ path: shot, fullPage: true });
    console.log('  screenshot: ' + shot);
    pageErrors.length === 0 ? ok('no page errors on the token-accounting render') : fail(`page errors: ${pageErrors.join(' | ')}`);
    await ctx.close();
  }
  {
    const TOKEN_FIXTURE_ROWS = {
      ...POPULATED_FIXTURE,
      rankings: { ...POPULATED_FIXTURE.rankings, rows: [
        { label: 'opus', input_processed_total: 1000, input_fresh: 100, input_cache_read: 800, input_cache_write: 100,
          cache_write_5m: null, cache_write_1h: null, cache_write_ttl_unknown: 100, output_tokens: 50, added: 1, session_count: 1 },
      ] },
    };
    const { ctx, page } = await openMobileModal(browser, TOKEN_FIXTURE_ROWS, WINDOWS_FIXTURE);
    const cardText = await page.$eval('.ub-cards', (el) => el.textContent);
    /Cache reads\s*800 \(80\.0%\)/.test(cardText) && /Fresh\s*100 \(10\.0%\)/.test(cardText) && /Cache writes\s*100 \(10\.0%\)/.test(cardText)
      ? ok('phone cards: Fresh / Cache reads / Cache writes with shares')
      : fail(`phone card breakdown wrong: ${cardText.slice(0, 500)}`);
    /TTL unknown/.test(cardText) ? ok('phone cards: older-session writes shown as TTL unknown') : fail(`phone card TTL-unknown row missing: ${cardText.slice(0, 500)}`);
    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — usage breakdown: desktop table + phone cards from the same data, control re-fetch, all 5 empty states.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
