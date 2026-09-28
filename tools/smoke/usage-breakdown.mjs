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

const USAGE_FIXTURE = { available: true, top_models: [], provider_weekly_usage: {} };

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

async function openDesktopPopover(browser, breakdownBody, windowsBody, calls) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', routeHandler(breakdownBody, windowsBody, calls));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
  await page.evaluate(() => { toggleSysStatusPopover(); _sysStatusSwitchTab('usage'); });
  await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 });
  await page.waitForSelector('.ub-controls', { state: 'attached', timeout: 5000 });
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
  await page.evaluate(() => { openSystemUsage(); });
  await page.waitForSelector('.ub-controls', { state: 'attached', timeout: 5000 });
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
