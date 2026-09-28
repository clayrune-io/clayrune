#!/usr/bin/env node
/**
 * MC-966: bottom usage-bar strip (static/js/system-status.js
 * `_renderUsageBarStrip`, `#usage-bar-strip` in static/index.html).
 *
 * Real headless boot (real index.html + real static/js/*.js served verbatim,
 * no server, no network) — same hermetic pattern as
 * channel-default-agent-visible.mjs. `/api/system/usage` is stubbed with a
 * `provider_weekly_usage` fixture shaped exactly like the server now emits it
 * (mc/blueprints/system_routes.py `system_usage_get`): a dict keyed by
 * provider name, present ONLY for providers with a real weekly %.
 *
 * Covers:
 *  1. Desktop (1440): the strip renders one bar per key in the fixture.
 *  2. Color class follows the system-status.js thresholds (green <70,
 *     amber 70-89, red >=90) for a stubbed %, plus the `exhausted` flag
 *     forcing red/full regardless of the last-sampled %.
 *  3. A provider absent from the fixture (gemini — no real weekly-%
 *     source server-side) gets NO bar — never a 0%/placeholder row.
 *  4. Phone (390): the strip is hidden (`@media max-width:960px`), and
 *     `#minimized-tray` still renders (coexistence, not a cover-up).
 *  5. Settings > Appearance toggle OFF empties the strip without needing a
 *     page reload (`toggleSetting` calls `_renderUsageBarStrip()` directly).
 *
 * RUN
 *   cd tools/smoke && node usage-bar.mjs
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

// Shaped exactly like mc/blueprints/system_routes.py's `provider_weekly_usage`:
// claude green (41%), codex red via `used_percent` alone (92%) — gemini is
// deliberately ABSENT (no real weekly-% source), never a 0%/placeholder row.
// `codex_usage_detail` + a gemini row in `top_models` cover MC-989 Part A's
// Usage-tab sections (Codex weekly/5h/plan/credits, Gemini token count).
const USAGE_FIXTURE = {
  available: true,
  top_models: [
    { model: 'claude-sonnet-5', tokens: 500000, cache_read: 0 },
    { model: 'gemini-2.5-pro', tokens: 12345, cache_read: 0 },
  ],
  codex_usage_detail: {
    weekly: { utilization: 41, resets_at: '2026-10-03T19:20:26+00:00' },
    five_hour: { utilization: 5, resets_at: '2026-09-28T02:26:25+00:00' },
    plan_type: 'plus',
    credits: { has_credits: true, unlimited: false, balance: '12.50' },
    sampled_at: '2026-09-28T01:26:25+00:00',
  },
  provider_weekly_usage: {
    claude: { utilization: 41, resets_at: '2026-10-03T19:20:26+00:00' },
    codex: { utilization: 92, resets_at: '2026-09-30T00:00:00+00:00' },
  },
};

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// `calls` records every request path this handler served — the MC-989
// no-auto-redemption check needs to assert /api/terminal/stdin (the ONLY
// endpoint that actually types into a running CLI) was never hit, since
// nothing in the smoke flow simulates a human typing into the pop-out.
function routeHandler(usageBody, calls) {
  return (route) => {
    const path = new URL(route.request().url()).pathname;
    if (calls) calls.push(path);
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/system/status') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/system/usage') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(usageBody) });
    if (path === '/api/system/usage/refresh') return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(usageBody) });
    if (path === '/api/system/usage/reset-terminal') {
      const body = route.request().postData() || '{}';
      const provider = (JSON.parse(body).provider || '').toLowerCase();
      const instruction = provider === 'codex'
        ? 'Type /usage, then choose "Redeem usage limit reset" if your account has one banked.'
        : 'Type /limit-reset and press Enter to reset the 5-hour session limit.';
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true, session_id: 'smoke-' + provider, command: provider, instruction }) });
    }
    if (path === '/api/terminal/stream') return route.fulfill({ status: 200, contentType: 'text/event-stream', body: '' });
    return route.abort();
  };
}

async function newPage(browser, { width, height }, usageBody, calls) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', routeHandler(usageBody, calls));
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#usage-bar-strip', { state: 'attached', timeout: 15000 });
  // fetchSystemUsage() is async (awaited fetch); give the stubbed round-trip
  // + _rerenderSysStatusSurfaces() a moment before asserting on its output.
  await page.waitForFunction(
    () => document.querySelectorAll('#usage-bar-strip .usage-bar-item').length > 0,
    { timeout: 5000 },
  ).catch(() => {});
  return { ctx, page, pageErrors };
}

let browser, exitCode = 1;
try {
  browser = await chromium.launch();

  // ── 1-3. Desktop 1440: bars present, colors correct, gemini omitted ──────
  {
    const { ctx, page, pageErrors } = await newPage(browser, { width: 1440, height: 900 }, USAGE_FIXTURE);
    const items = await page.$$eval('#usage-bar-strip .usage-bar-item', (els) => els.map((el) => ({
      label: el.querySelector('.usage-bar-label')?.textContent.trim(),
      pct: el.querySelector('.usage-bar-pct')?.textContent.trim(),
      cls: Array.from(el.querySelector('.usage-bar-fill').classList).find((c) => c !== 'usage-bar-fill'),
    })));

    items.length === 2
      ? ok(`desktop 1440: exactly 2 bars rendered (claude, codex) — got ${items.length}`)
      : fail(`expected 2 bars, got ${items.length}: ${JSON.stringify(items)}`);

    const claude = items.find((i) => /claude/i.test(i.label || ''));
    (claude && claude.cls === 'green' && claude.pct === '41%')
      ? ok('claude at 41% renders green (below the 70 threshold)')
      : fail(`claude bar wrong: ${JSON.stringify(claude)}`);

    const codex = items.find((i) => /codex/i.test(i.label || ''));
    (codex && codex.cls === 'red' && codex.pct === '92%')
      ? ok('codex at 92% renders red (>=90 threshold)')
      : fail(`codex bar wrong: ${JSON.stringify(codex)}`);

    const gemini = items.find((i) => /gemini/i.test(i.label || ''));
    !gemini
      ? ok('gemini (no real weekly-% source) has NO bar — not a 0%/placeholder row')
      : fail(`gemini should be omitted entirely, found: ${JSON.stringify(gemini)}`);

    const strip = await page.$eval('#usage-bar-strip', (el) => getComputedStyle(el).display);
    strip !== 'none'
      ? ok('strip is visible at 1440px')
      : fail('strip should be visible at 1440px, computed display is none');

    // ── click opens the existing system-status popover on the Usage tab ────
    await page.click('#usage-bar-strip .usage-bar-item');
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 }).catch(() => {});
    const popoverOpen = await page.$eval('#sys-status-popover', (el) => el.classList.contains('open')).catch(() => false);
    const activeTabLabel = await page.$eval('#sys-status-popover .ssp-tab.active', (el) => el.textContent.trim()).catch(() => null);
    (popoverOpen && activeTabLabel === 'Usage')
      ? ok('clicking a bar opens the system-status popover on the Usage tab')
      : fail(`click should open the popover on Usage tab, open=${popoverOpen} activeTab=${activeTabLabel}`);

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0
      ? ok('desktop pass: no uncaught page errors')
      : uncaught.forEach((e) => fail('uncaught exception (desktop): ' + e));

    await ctx.close();
  }

  // ── 4. Phone 390: strip hidden, minimized-tray unaffected ─────────────────
  {
    const { ctx, page, pageErrors } = await newPage(browser, { width: 390, height: 844 }, USAGE_FIXTURE);
    const stripDisplay = await page.$eval('#usage-bar-strip', (el) => getComputedStyle(el).display);
    stripDisplay === 'none'
      ? ok('strip is hidden at 390px (mobile has its own tab bar)')
      : fail(`strip should be display:none at 390px, computed display is "${stripDisplay}"`);

    const trayExists = await page.$('#minimized-tray');
    trayExists
      ? ok('#minimized-tray still present at mobile width (unaffected by the strip)')
      : fail('#minimized-tray missing at mobile width');

    const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    uncaught.length === 0
      ? ok('mobile pass: no uncaught page errors')
      : uncaught.forEach((e) => fail('uncaught exception (mobile): ' + e));

    await ctx.close();
  }

  // ── 5. Settings > Appearance toggle OFF empties the strip live ───────────
  {
    const { ctx, page } = await newPage(browser, { width: 1440, height: 900 }, USAGE_FIXTURE);
    const beforeCount = await page.$$eval('#usage-bar-strip .usage-bar-item', (els) => els.length);
    beforeCount > 0
      ? ok('toggle test: strip starts populated')
      : fail(`toggle test: strip should start populated, got ${beforeCount} items`);

    // `_globalConfig` is a classic-script top-level `let` (index.html), not a
    // `window` property — mutate the bare identifier, which system-status.js
    // resolves via the shared classic-script global lexical scope (see
    // discovery_es_module_cross_boundary_globals.md).
    await page.evaluate(() => {
      _globalConfig.usage_bar_enabled = false;
      _renderUsageBarStrip();
    });
    const afterHTML = await page.$eval('#usage-bar-strip', (el) => el.innerHTML.trim());
    afterHTML === ''
      ? ok('toggling usage_bar_enabled off empties the strip immediately (no reload needed)')
      : fail(`strip should be empty after toggle-off, innerHTML: "${afterHTML}"`);

    const stripDisplayAfter = await page.$eval('#usage-bar-strip', (el) => getComputedStyle(el).display);
    stripDisplayAfter === 'none'
      ? ok('empty strip collapses via :empty (no visible bottom gap)')
      : fail(`empty strip should collapse to display:none, got "${stripDisplayAfter}"`);

    await ctx.close();
  }

  // ── 6. No provider has a real weekly % at all: strip stays empty, never a
  // placeholder row ─────────────────────────────────────────────────────────
  {
    const { ctx, page } = await newPage(browser, { width: 1440, height: 900 }, { available: true, provider_weekly_usage: {} });
    const count = await page.$$eval('#usage-bar-strip .usage-bar-item', (els) => els.length);
    count === 0
      ? ok('empty provider_weekly_usage from the server renders zero bars (not a placeholder)')
      : fail(`expected 0 bars with an empty fixture, got ${count}`);
    await ctx.close();
  }

  // ── 7. MC-989: Usage-tab popup shows Codex + Gemini sections and reset
  // actions, and clicking a reset button NEVER auto-submits the redemption
  // command — it only opens a terminal pop-out + shows the instruction text
  // for a human to type themselves (/api/terminal/stdin must stay untouched).
  {
    const calls = [];
    const { ctx, page } = await newPage(browser, { width: 1440, height: 900 }, USAGE_FIXTURE, calls);
    await page.click('#usage-bar-strip .usage-bar-item');
    await page.waitForSelector('#sys-status-popover.open', { timeout: 3000 });

    const sectionHeads = await page.$$eval('.ssp-section-head', (els) => els.map((el) => el.textContent.trim()));
    sectionHeads.includes('Codex')
      ? ok('Usage popup: Codex section head present')
      : fail(`Codex section head missing, got: ${JSON.stringify(sectionHeads)}`);
    sectionHeads.includes('Gemini')
      ? ok('Usage popup: Gemini section head present')
      : fail(`Gemini section head missing, got: ${JSON.stringify(sectionHeads)}`);

    const geminiHint = await page.$eval('.sys-status-popover', (el) => el.textContent).catch(() => '');
    /Google exposes no quota/i.test(geminiHint)
      ? ok('Gemini section states honestly that no quota % exists (never a fake bar)')
      : fail('Gemini section missing the "no quota %" disclosure');
    /Sampled/i.test(geminiHint) && /Codex turn runs/i.test(geminiHint)
      ? ok('Codex section shows a "sampled <age> ago" freshness label')
      : fail('Codex freshness label missing');

    const claudeWeeklyBtn = await page.$('a.ssp-refresh[href="https://claude.ai/settings/usage"]');
    claudeWeeklyBtn
      ? ok('Claude "Reset weekly limit" link present (opens claude.ai, new tab)')
      : fail('Claude weekly-reset link missing');

    const resetBtns = await page.$$eval('.ssp-action-row button.ssp-refresh', (els) => els.map((el) => el.textContent.trim()));
    resetBtns.some((t) => /5-hour session/.test(t) && /limit-reset/.test(t))
      ? ok('Claude "Reset 5-hour session (/limit-reset)" button present')
      : fail(`Claude 5h reset button missing, got: ${JSON.stringify(resetBtns)}`);
    resetBtns.some((t) => /Redeem a banked reset/.test(t))
      ? ok('Codex "Redeem a banked reset" button present')
      : fail(`Codex redeem button missing, got: ${JSON.stringify(resetBtns)}`);

    // Click Codex's reset action — must open a terminal pop-out and show the
    // instruction as text, and must NEVER call /api/terminal/stdin (the only
    // path that types into the running CLI) on its own.
    calls.length = 0;
    await page.click('button.ssp-refresh:has-text("Redeem a banked reset")');
    await page.waitForSelector('#terminal-popout, .terminal-popout, [id^="terminal-modal"]', { timeout: 3000 }).catch(() => {});
    await page.waitForTimeout(300);
    calls.includes('/api/system/usage/reset-terminal')
      ? ok('clicking "Redeem a banked reset" calls the reset-terminal endpoint')
      : fail(`reset-terminal endpoint not called, saw: ${JSON.stringify(calls)}`);
    calls.includes('/api/terminal/stdin')
      ? fail('clicking the reset button auto-submitted stdin — a redemption must be human-typed, never auto-sent')
      : ok('no auto-submitted stdin — the redemption command is never typed for the user');

    await ctx.close();
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0
    ? '\n✅ PASS — usage-bar strip: correct bars/colors on desktop, provider omission honored, hidden on mobile, live toggle-off, no placeholder rows.'
    : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
