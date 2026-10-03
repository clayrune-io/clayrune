#!/usr/bin/env node
/**
 * MC-998 follow-up (Ron, 2026-09-28): the low-pane usage bar no longer
 * navigates to the full Usage Breakdown screen -- hover (or focus, or a tap)
 * shows a small ~280px summary popup anchored to the bar instead
 * (static/js/system-status.js `_ubOpenBarPopup` / `_ubRenderBarPopup`).
 * usage-bar.mjs already asserts the click-no-longer-navigates behaviour;
 * this file owns the POPUP'S CONTENT across every state the bar's provider
 * can be in: calibrated, uncalibrated, incomplete coverage, no data yet, and
 * a fetch error -- plus Esc / outside-click dismissal and that the
 * Unattributed line is never hidden.
 *
 * Real headless boot (real index.html + real static/js/*.js served
 * verbatim, no server, no network) -- same hermetic pattern as
 * usage-bar.mjs / usage-breakdown.mjs. The popup fires two parallel
 * `/api/system/usage/breakdown` fetches (dimension=character,
 * dimension=model) at provider=claude&window_kind=7d&window_scope=all --
 * fixtures below are keyed by dimension.
 *
 * RUN
 *   cd tools/smoke && node usage-bar-popup.mjs
 * Exit 0 = all pass; 1 = a case regressed / harness error.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

// Same weekly-% source the bar strip itself reads (mc/blueprints/system_routes.py).
const USAGE_FIXTURE = {
  available: true,
  top_models: [],
  provider_weekly_usage: {
    claude: { utilization: 41, resets_at: '2026-10-03T19:20:26+00:00' },
  },
};

// Character-dimension rows shared by the calibrated/uncalibrated fixtures --
// "Unknown" is the server's label for the unattributed bucket (compute_rankings,
// mc/usage_breakdown_aggregate.py) and must survive into the popup's
// "Unattributed" line even when it isn't one of the top-3 named consumers.
const CHAR_ROWS = [
  { label: 'Dave', input_processed_total: 400000, output_tokens: 100000, added: 200, session_count: 4 },
  { label: 'Tobin', input_processed_total: 200000, output_tokens: 50000, added: 90, session_count: 2 },
  { label: 'Fenn', input_processed_total: 100000, output_tokens: 20000, added: 30, session_count: 1 },
  { label: 'Vector', input_processed_total: 50000, output_tokens: 10000, added: 10, session_count: 1 },
  { label: 'Unknown', input_processed_total: 30000, output_tokens: 5000, added: 0, session_count: 1 },
];
const MODEL_ROWS = [
  { label: 'claude-sonnet-5', input_processed_total: 600000, output_tokens: 150000, added: 250, session_count: 6 },
  { label: 'claude-opus-5', input_processed_total: 180000, output_tokens: 35000, added: 80, session_count: 3 },
];

const CALIBRATED_CHAR = {
  provider: 'claude', window_kind: '7d', window_scope: 'all', empty_state: null,
  coverage_begins: '2026-09-01T00:00:00Z',
  totals: { session_count: 9, incomplete_coverage_session_count: 0 },
  tokens_per_point: { status: 'ok', median: 50000, p10: 40000, p90: 60000, sample_count: 12 },
  rankings: { dimension: 'character', rows: CHAR_ROWS, unknown_count: 1, missing_data_count: 0 },
};
const UNCALIBRATED_CHAR = {
  ...CALIBRATED_CHAR,
  tokens_per_point: { status: 'insufficient_calibration', sample_count: 2 },
};
const INCOMPLETE_CHAR = {
  provider: 'claude', window_kind: '7d', window_scope: 'all', empty_state: 'incomplete_coverage',
  coverage_begins: '2026-09-28T01:00:00Z',
  totals: { session_count: 0, incomplete_coverage_session_count: 3 },
  tokens_per_point: { status: 'insufficient_calibration', sample_count: 0 },
  rankings: { dimension: 'character', rows: [], unknown_count: 0, missing_data_count: 0 },
};
const NO_DATA_CHAR = {
  provider: 'claude', window_kind: '7d', window_scope: 'all', empty_state: 'sampling_not_begun',
  coverage_begins: null,
  totals: { session_count: 0, incomplete_coverage_session_count: 0 },
  tokens_per_point: { status: 'insufficient_calibration', sample_count: 0 },
  rankings: { dimension: 'character', rows: [], unknown_count: 0, missing_data_count: 0 },
};
const MODEL_PAYLOAD = { provider: 'claude', window_kind: '7d', window_scope: 'all', empty_state: null, rankings: { dimension: 'model', rows: MODEL_ROWS } };

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// `charBody`/`modelBody` are the per-dimension breakdown fixtures; `mode`
// picks how `/api/system/usage/breakdown` itself behaves ('ok' | '500').
function routeHandler(charBody, modelBody, mode = 'ok') {
  return (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/system/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/system/usage') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(USAGE_FIXTURE) });
    if (path === '/api/system/usage/windows') return route.fulfill({ status: 200, contentType: 'application/json', body: '{"windows":[]}' });
    if (path === '/api/system/usage/breakdown') {
      if (mode === '500') return route.fulfill({ status: 500, contentType: 'application/json', body: '{}' });
      const dim = url.searchParams.get('dimension');
      const body = dim === 'model' ? modelBody : charBody;
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    }
    return route.abort();
  };
}

async function openPopup(browser, charBody, modelBody, mode = 'ok') {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', routeHandler(charBody, modelBody, mode));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
  await page.waitForSelector('#usage-bar-strip .usage-bar-item', { state: 'attached', timeout: 5000 });
  await page.hover('#usage-bar-strip .usage-bar-item');
  await page.waitForSelector('#usage-bar-popup.open', { timeout: 3000 });
  await page.waitForTimeout(250); // let the two dimension fetches resolve + re-render
  const text = await page.$eval('#usage-bar-popup', (el) => el.textContent);
  return { ctx, page, pageErrors, text };
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1. Calibrated: estimated % of allowance per line, top-3 consumers,
  // top-2 models, Other + Unattributed always shown, session avg ──────────
  {
    const { ctx, text } = await openPopup(browser, CALIBRATED_CHAR, MODEL_PAYLOAD);
    /Dave/.test(text) && /Tobin/.test(text) && /Fenn/.test(text)
      ? ok('calibrated: top-3 consumers by character shown (Dave, Tobin, Fenn)')
      : fail(`calibrated: top-3 consumers missing: ${text}`);
    /Vector/.test(text)
      ? fail('calibrated: 4th-ranked consumer (Vector) should fold into "Other", not list separately')
      : ok('calibrated: only top-3 consumers listed by name, rest folds into Other');
    /Other/.test(text)
      ? ok('calibrated: "Other" line present')
      : fail('calibrated: "Other" line missing');
    /Unattributed/.test(text)
      ? ok('calibrated: "Unattributed" line present')
      : fail('calibrated: "Unattributed" line missing');
    /claude-sonnet-5/.test(text) && /claude-opus-5/.test(text)
      ? ok('calibrated: top-2 models shown')
      : fail(`calibrated: top-2 models missing: ${text}`);
    /% of allowance/.test(text)
      ? ok('calibrated: lines show estimated % of allowance (gate passed)')
      : fail(`calibrated: expected "% of allowance" wording, got: ${text}`);
    /Sessions/.test(text) && /9/.test(text)
      ? ok('calibrated: session count shown')
      : fail(`calibrated: session count missing: ${text}`);
    /Full report/.test(text)
      ? ok('calibrated: "Full report" link present')
      : fail('calibrated: "Full report" link missing');
    await ctx.close();
  }

  // ── 2. Uncalibrated: token shares only, plus the muted gate-not-passed line
  {
    const { ctx, text } = await openPopup(browser, UNCALIBRATED_CHAR, MODEL_PAYLOAD);
    /% of measured/.test(text)
      ? ok('uncalibrated: lines show token share of measured usage, not an allowance estimate')
      : fail(`uncalibrated: expected "% of measured" wording, got: ${text}`);
    /% of allowance/.test(text)
      ? fail('uncalibrated: must NOT show an allowance-% estimate before the gate passes')
      : ok('uncalibrated: no allowance-% estimate shown');
    /more clean samples/.test(text)
      ? ok('uncalibrated: muted "estimates start after N more clean samples" line present')
      : fail(`uncalibrated: calibration-gate hint missing: ${text}`);
    /Unattributed/.test(text)
      ? ok('uncalibrated: "Unattributed" line still present')
      : fail('uncalibrated: "Unattributed" line missing');
    await ctx.close();
  }

  // ── 3. Incomplete coverage: one muted line with the count, no fabricated rankings
  {
    const { ctx, text } = await openPopup(browser, INCOMPLETE_CHAR, MODEL_PAYLOAD);
    /3/.test(text) && /session/i.test(text)
      ? ok('incomplete coverage: muted line names the session count (3)')
      : fail(`incomplete coverage: count not surfaced: ${text}`);
    /Dave|Tobin|Fenn/.test(text)
      ? fail('incomplete coverage: must not show ranking rows for an unmeasurable window')
      : ok('incomplete coverage: no fabricated consumer ranking shown');
    await ctx.close();
  }

  // ── 4. No data yet: honest "collecting since" message, no rankings ────────
  {
    const { ctx, text } = await openPopup(browser, NO_DATA_CHAR, MODEL_PAYLOAD);
    /Collecting since|Numbers appear after the first chat turn completes/i.test(text)
      ? ok('no data yet: honest "collecting since / numbers appear after first turn" message shown')
      : fail(`no data yet: expected the empty-state message, got: ${text}`);
    /Dave|Tobin|Fenn/.test(text)
      ? fail('no data yet: must not show ranking rows with zero real data')
      : ok('no data yet: no fabricated consumer ranking shown');
    await ctx.close();
  }

  // ── 5. Fetch error: one-line error, never stale data shown as fresh ──────
  {
    const { ctx, text } = await openPopup(browser, CALIBRATED_CHAR, MODEL_PAYLOAD, '500');
    /couldn.t load|try again/i.test(text)
      ? ok('fetch error: one-line error message shown')
      : fail(`fetch error: expected an error line, got: ${text}`);
    /Dave|Tobin|Fenn|% of allowance|% of measured/.test(text)
      ? fail('fetch error: must never render stale/fixture ranking data as if it were fresh')
      : ok('fetch error: no ranking data rendered alongside the error');
    await ctx.close();
  }

  // ── 6. Esc closes the popup ────────────────────────────────────────────────
  {
    const { ctx, page } = await openPopup(browser, CALIBRATED_CHAR, MODEL_PAYLOAD);
    await page.keyboard.press('Escape');
    await page.waitForTimeout(100);
    const open = await page.$eval('#usage-bar-popup', (el) => el.classList.contains('open'));
    !open
      ? ok('Esc closes the popup')
      : fail('popup still open after Esc');
    await ctx.close();
  }

  // ── 7. Outside click closes the popup (click toggled it open/pinned first)
  {
    const { ctx, page } = await openPopup(browser, CALIBRATED_CHAR, MODEL_PAYLOAD);
    await page.click('#usage-bar-strip .usage-bar-item'); // pin it via click (touch has no hover)
    await page.waitForTimeout(100);
    await page.mouse.move(5, 5);
    await page.mouse.click(5, 5);
    await page.waitForTimeout(100);
    const open = await page.$eval('#usage-bar-popup', (el) => el.classList.contains('open'));
    !open
      ? ok('outside click closes a pinned (click-toggled) popup')
      : fail('popup still open after an outside click');
    await ctx.close();
  }

  // ── 8. Click no longer navigates to the full Breakdown/report screen ─────
  {
    const { ctx, page } = await openPopup(browser, CALIBRATED_CHAR, MODEL_PAYLOAD);
    const reportOpenBefore = await page.$eval('body', () => !!document.querySelector('[data-modal-id="__usage_report"]'));
    !reportOpenBefore
      ? ok('hovering the bar never opens the Usage report modal on its own')
      : fail('Usage report modal should not auto-open from a hover');
    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — usage bar popup: calibrated/uncalibrated/incomplete/empty/error content, Unattributed always present, Esc + outside-click dismissal.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
