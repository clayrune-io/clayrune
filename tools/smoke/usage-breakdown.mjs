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
    /range 5\.00%.{0,3}7\.00%/.test(text) ? ok('segmented bar renders its estimate range (5.00%-7.00%)') : fail(`range caveat missing from: ${text.slice(0, 800)}`);

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
    await ctx.close();
  }
  {
    const { ctx, page } = await openDesktopPopover(browser, ESTIMATE_EXCEEDS_OBSERVED_FIXTURE, WINDOWS_FIXTURE);
    const text = await page.$eval('.sys-status-popover', (el) => el.textContent);
    /exceeds[\s\S]{0,60}2\.00%/i.test(text)
      ? ok('estimate-exceeds-observed: excess (2.00%) rendered as a separate error')
      : fail(`excess-error text missing from: ${text.slice(0, 800)}`);
    /Unattributed.{0,10}-2\.00%/.test(text)
      ? fail('estimate-exceeds-observed: rendered a literal negative "Unattributed" amount')
      : ok('estimate-exceeds-observed: no literal negative Unattributed amount rendered');
    await ctx.close();
  }

  // ── 6. Finding #6: the desktop weekly-strip entry loads Breakdown for the
  //      CLICKED provider, not the Claude/5h defaults ─────────────────────
  {
    const calls = [];
    const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    const page = await ctx.newPage();
    await page.route('**/*', routeHandler(POPULATED_FIXTURE, WINDOWS_FIXTURE, calls));
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#usage-bar-strip .usage-bar-item', { state: 'attached', timeout: 15000 });
    calls.length = 0;
    await page.click('#usage-bar-strip .usage-bar-item');
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 }).catch(() => {});

    const popoverOpen = await page.$eval('#sys-status-popover', (el) => el.classList.contains('open')).catch(() => false);
    const activeTabLabel = await page.$eval('#sys-status-popover .ssp-tab.active', (el) => el.textContent.trim()).catch(() => null);
    (popoverOpen && activeTabLabel === 'Usage')
      ? ok('strip click opens the popover on the Usage tab')
      : fail(`strip click should open Usage tab, open=${popoverOpen} activeTab=${activeTabLabel}`);

    await page.waitForTimeout(200);
    calls.some((c) => c.includes('/api/system/usage/breakdown') && c.includes('provider=codex') && c.includes('window_kind=7d'))
      ? ok('strip click fetches Breakdown for the clicked provider (codex) at 7d, not Claude/5h defaults')
      : fail(`strip click did not fetch codex/7d breakdown, saw: ${JSON.stringify(calls)}`);

    const providerSelected = await page.$eval('.ub-select:has(option[value="claude"])', (el) => el.value).catch(() => null);
    providerSelected === 'codex'
      ? ok('provider control reflects the clicked provider (codex)')
      : fail(`provider control should show codex selected, saw "${providerSelected}"`);

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
    await page.evaluate(() => { toggleSysStatusPopover(); _sysStatusSwitchTab('usage'); });
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 });
    await page.waitForSelector('.ub-controls', { state: 'attached', timeout: 5000 });
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
    await page.evaluate(() => { toggleSysStatusPopover(); _sysStatusSwitchTab('usage'); });
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 });
    await page.waitForSelector('.ub-controls', { state: 'attached', timeout: 5000 });
    await page.waitForSelector('.ub-table-wrap', { state: 'attached', timeout: 5000 });

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
    await page.evaluate(() => { toggleSysStatusPopover(); _sysStatusSwitchTab('usage'); });
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 });
    await page.waitForSelector('.ub-controls', { state: 'attached', timeout: 5000 });
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
    await page.evaluate(() => { toggleSysStatusPopover(); _sysStatusSwitchTab('usage'); });
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 });
    await page.waitForSelector('.ub-controls', { state: 'attached', timeout: 5000 });
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
