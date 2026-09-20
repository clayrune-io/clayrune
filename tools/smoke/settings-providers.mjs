#!/usr/bin/env node
/**
 * Settings -> Providers: every vendor is ONE row component, with the full
 * action set (F1 + the "unify all vendor UIs" scope addition, 2026-09-18).
 *
 * WHY THIS EXISTS
 * ----------------
 * Vendor setup used to live only in the first-run tour: once the default
 * vendor was installed and signed in, the tour step was skipped and Settings
 * could not install, sign in or switch to another vendor. Settings also
 * special-cased Claude (its own buttons + status line) so no two vendors
 * looked alike. Both surfaces now render rows through
 * walkthrough.js _renderProviderRow.
 *
 * Pins, at desktop AND 390px:
 *   1. Settings -> Providers lists every provider, incl. an uninstalled one.
 *   2. Every row has the same skeleton (head/name/state pill), every installed
 *      row the same action set (Default, Sign in, Check status), and the
 *      Claude row is byte-identical to another vendor in the same state once
 *      name/label are normalised — the per-vendor difference is only what a
 *      button DOES.
 *   3. "Sign in remotely" appears exactly where the server says remote_login.
 *   4. The buttons are wired: Install -> install-launch, Install selected ->
 *      the batch route (ONE request), Set default -> PUT default_provider,
 *      Check status -> that vendor's auth-probe, Check setup status ->
 *      /api/agent/providers?refresh=1.
 *   5. No horizontal overflow; every button sits inside the viewport.
 *   6. The tour step renders the SAME row shape and pre-ticks vendors that
 *      are already installed and signed in.
 *
 * Hermetic like settings-update-row.mjs: page + static assets served from
 * THIS checkout, /api/* canned, everything else aborted.
 *
 * RUN: node tools/smoke/settings-providers.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const providers = [
  { name: 'claude', display_name: 'Claude Code', installed: true, version: '2.1.0', auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: true, in_use: true },
  { name: 'codex', display_name: 'Codex CLI', installed: true, version: '2.1.0', auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: false, in_use: false },
  { name: 'gemini', display_name: 'Gemini CLI', installed: true, version: '0.9.0', auth_status: 'not_logged_in',
    remote_login: false, install_hint: '', capabilities: { auth_probe_spends_quota: true }, default: false, in_use: false },
  { name: 'qwen', display_name: 'Qwen Code', installed: false, version: null, auth_status: 'unknown',
    remote_login: false, install_hint: 'npm install -g @qwen-code/qwen-code', capabilities: {}, default: false, in_use: false },
];
let config = { default_provider: 'claude' };
const calls = { install: [], batch: [], probe: [], put: [], refreshList: 0, login: [] };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const req = route.request();
  const url = new URL(req.url());
  const path = url.pathname;
  const json = (body, status = 200) => route.fulfill({
    status, contentType: 'application/json', body: JSON.stringify(body) });

  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
      body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
  if (path.startsWith('/static/')) {
    const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
    if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
      const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8'
        : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
      return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
    }
    return route.abort();
  }
  if (path === '/api/projects') return json([]);
  if (path === '/api/config' && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    calls.put.push(body);
    config = { ...config, ...body };
    return json({ ok: true });
  }
  if (path === '/api/config') return json(config);
  if (path === '/api/agent/providers') {
    if (url.searchParams.get('refresh') === '1') calls.refreshList++;
    return json({ providers: providers.map(p => ({ ...p, default: p.name === config.default_provider })),
                  default: config.default_provider });
  }
  if (path === '/api/agent/providers/install-launch') {
    calls.batch.push(JSON.parse(req.postData() || '{}').names);
    return json({ ok: true, installed: JSON.parse(req.postData() || '{}').names });
  }
  let m = path.match(/^\/api\/agent\/provider\/([^/]+)\/install-launch$/);
  if (m) { calls.install.push(m[1]); return json({ ok: true }); }
  m = path.match(/^\/api\/agent\/([^/]+)\/auth-probe$/);
  if (m) {
    calls.probe.push(m[1]);
    return json({ ok: true, status: 'ok', last_checked: 'now' });
  }
  m = path.match(/^\/api\/agent\/provider\/([^/]+)\/login-launch$/);
  if (m) { calls.login.push(m[1]); return json({ ok: true }); }
  return route.abort();
});

const openProviders = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#settings-providers-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('providers'));
  await page.waitForFunction(
    () => { const s = document.getElementById('settings-providers-section');
            return s && s.offsetParent !== null; }, { timeout: 5000 });
};

// Everything that can differ between two vendors in the same state, blanked.
// The API-key field (.prov-row-extra) and the checked Default radio are
// per-vendor DATA (does this vendor take a key; is it the default), not
// layout, so they are stripped before comparing.
const normalise = (html, p) => html
  .replace(/<div class="prov-row-extra"[\s\S]*?<\/button>\s*<\/div>/, '')
  .replace(/ checked=""/g, '')
  .split(p.name).join('@N').split(p.display_name).join('@D');

const readRows = () => page.evaluate(() => {
  const norm = (row) => ({
    name: row.dataset.provider,
    html: row.outerHTML,
    head: !!row.querySelector('label.prov-row-head'),
    nameEl: !!row.querySelector('.prov-row-name'),
    state: (row.querySelector('.prov-row-state') || {}).textContent || '',
    actions: !!row.querySelector('.prov-row-actions'),
    def: !!row.querySelector('.prov-row-actions input[type=radio]'),
    signIn: !!row.querySelector('.prov-sign-in'),
    remote: !!row.querySelector('.prov-sign-in-remote'),
    check: !!row.querySelector('.prov-check'),
    install: !!row.querySelector('.prov-install'),
    select: !!row.querySelector('.prov-row-select'),
  });
  return [...document.querySelectorAll('#settings-providers-section .prov-row')].map(norm);
});

const assertLayout = async (label) => {
  const rows = await readRows();
  const by = Object.fromEntries(rows.map(r => [r.name, r]));
  check(rows.length === providers.length,
    `[${label}] one row per provider (${rows.length})`,
    `[${label}] expected ${providers.length} rows, got ${rows.length}`);
  check(rows.every(r => r.head && r.nameEl && r.state),
    `[${label}] every row has head + name + state pill`,
    `[${label}] a row is missing its head/name/state: ${JSON.stringify(rows.map(r => [r.name, r.head, r.nameEl, r.state]))}`);
  const installed = rows.filter(r => providers.find(p => p.name === r.name).installed);
  check(installed.every(r => r.actions && r.def && r.signIn && r.check && !r.install),
    `[${label}] every installed row has Default + Sign in + Check status (and no Install)`,
    `[${label}] installed rows differ in action set: ${JSON.stringify(installed.map(r => [r.name, r.def, r.signIn, r.check, r.install]))}`);
  check(by.qwen.install && by.qwen.select && !by.qwen.check && by.qwen.state === 'not installed',
    `[${label}] uninstalled row: Install + batch tick-box, no Sign in/Check`,
    `[${label}] uninstalled row wrong: ${JSON.stringify(by.qwen)}`);
  check(by.claude.remote && by.codex.remote && !by.gemini.remote,
    `[${label}] Sign in remotely appears exactly where remote_login is true`,
    `[${label}] remote-login buttons wrong: ${JSON.stringify(rows.map(r => [r.name, r.remote]))}`);
  check(!by.claude.html.includes('prov-row-extra') && by.codex.html.includes('prov-row-extra')
        && by.gemini.html.includes('prov-row-extra'),
    `[${label}] the only per-vendor addition is the API-key field, on vendors that take a key (Claude signs in via OAuth)`,
    `[${label}] API-key field placement wrong`);
  const c = normalise(by.claude.html, providers[0]);
  const x = normalise(by.codex.html, providers[1]);
  check(c === x,
    `[${label}] Claude row is identical to Codex row (same state) once name/label are blanked`,
    `[${label}] Claude row differs structurally from Codex row:\n${c}\n---\n${x}`);
  const geo = await page.evaluate(() => {
    const body = document.getElementById('settings-body');
    const win = document.getElementById('settings-body').closest('.modal-window') || body;
    const wr = win.getBoundingClientRect();
    const out = [];
    document.querySelectorAll('#settings-providers-section button, #settings-providers-section input').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width && (r.right > wr.right + 1 || r.left < wr.left - 1)) out.push(el.className || el.tagName);
    });
    return { overflowX: document.documentElement.scrollWidth - document.documentElement.clientWidth,
             bodyOverflow: body.scrollWidth - body.clientWidth, clipped: out };
  });
  check(geo.overflowX <= 1 && geo.bodyOverflow <= 1 && geo.clipped.length === 0,
    `[${label}] no horizontal overflow, no control outside the window`,
    `[${label}] overflow: ${JSON.stringify(geo)}`);
};

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function'
    && typeof window._renderProviderRow === 'function', { timeout: 20000 });

  // ── desktop ────────────────────────────────────────────────────────────
  await openProviders();
  await assertLayout('desktop');
  if (process.env.MC_SMOKE_SHOT) await page.waitForTimeout(900);
  if (process.env.MC_SMOKE_SHOT) await page.screenshot({ path: process.env.MC_SMOKE_SHOT.replace(/(\.\w+)?$/, '-desktop$1') });

  // ── wiring ─────────────────────────────────────────────────────────────
  await page.click('#settings-providers-section .prov-row[data-provider="qwen"] .prov-install');
  await page.waitForFunction(() => document.getElementById('wt-install-msg-qwen')?.textContent.includes('terminal opened'));
  check(calls.install.join() === 'qwen', 'Install -> POST provider/qwen/install-launch',
    `install calls: ${calls.install}`);

  await page.check('#settings-providers-section .prov-row[data-provider="qwen"] .settings-prov-install-sel');
  await page.click('#settings-prov-install-selected');
  await page.waitForFunction(() => document.getElementById('wt-install-msg-qwen')?.textContent.includes('terminal opened'));
  check(calls.batch.length === 1 && calls.batch[0].join() === 'qwen',
    'Install selected -> ONE batch request naming the ticked vendor',
    `batch calls: ${JSON.stringify(calls.batch)}`);

  await page.click('#settings-providers-section .prov-row[data-provider="gemini"] .prov-sign-in');
  await page.waitForFunction(() => true);
  await page.waitForTimeout(100);
  check(calls.login.join() === 'gemini', 'Sign in -> POST provider/gemini/login-launch',
    `login calls: ${calls.login}`);

  await page.check('#settings-providers-section .prov-row[data-provider="codex"] input[type=radio]');
  await page.waitForFunction(() => document.querySelector(
    '#settings-providers-section .prov-row[data-provider="codex"] input[type=radio]')?.checked);
  check(calls.put.some(b => b.default_provider === 'codex'), 'Default radio -> PUT default_provider=codex',
    `puts: ${JSON.stringify(calls.put)}`);

  const geminiTitle = await page.getAttribute(
    '#settings-providers-section .prov-row[data-provider="gemini"] .prov-check', 'title');
  check(/quota/i.test(geminiTitle || ''), 'Gemini Check status discloses the quota cost',
    `gemini Check status title: ${geminiTitle}`);

  providers.find(p => p.name === 'gemini').auth_status = 'ok';
  await page.click('#settings-providers-section .prov-row[data-provider="gemini"] .prov-check');
  await page.waitForFunction(() => document.querySelector(
    '#settings-providers-section .prov-row[data-provider="gemini"] .prov-row-state')?.textContent === 'signed in');
  check(calls.probe.join() === 'gemini', 'Check status -> POST gemini/auth-probe, pill flips to signed in',
    `probe calls: ${calls.probe}`);

  await page.click('#settings-prov-check-status');
  await page.waitForFunction(() => true);
  await page.waitForTimeout(150);
  check(calls.refreshList === 1, 'Check setup status -> GET /api/agent/providers?refresh=1',
    `refresh fetches: ${calls.refreshList}`);

  // ── mobile ─────────────────────────────────────────────────────────────
  providers.find(p => p.name === 'gemini').auth_status = 'not_logged_in';
  await page.setViewportSize({ width: 390, height: 844 });
  await openProviders();
  await assertLayout('390px');
  if (process.env.MC_SMOKE_SHOT) await page.waitForTimeout(900);
  if (process.env.MC_SMOKE_SHOT) await page.screenshot({ path: process.env.MC_SMOKE_SHOT.replace(/(\.\w+)?$/, '-390$1') });

  // ── tour step: same row shape + pre-ticked ready vendors ───────────────
  await page.setViewportSize({ width: 1000, height: 900 });
  await page.evaluate(() => window.closeModalById('__settings'));
  // No saved default (a first run) so the step is not skipped, then past Welcome.
  await page.evaluate(async () => { _globalConfig.default_provider = ''; await _ensureAgentProviders(true); window.startWalkthrough(); });
  await page.waitForSelector('#wt-overlay', { timeout: 5000 });
  await page.evaluate(() => window.wtNext());
  await page.waitForSelector('#wt-overlay input[name="wt-provider"]', { timeout: 5000 });
  const tour = await page.evaluate(() => [...document.querySelectorAll('#wt-overlay .prov-row')].map(r => ({
    name: r.dataset.provider,
    ticked: r.querySelector('input[name="wt-provider"]').checked,
    head: !!r.querySelector('label.prov-row-head'),
    state: r.querySelector('.prov-row-state').textContent,
  })));
  check(tour.length === providers.length && tour.every(r => r.head),
    'tour step renders the shared row (same head/name/state skeleton) for every vendor',
    `tour rows: ${JSON.stringify(tour)}`);
  const ticked = tour.filter(r => r.ticked).map(r => r.name).sort().join();
  check(ticked === 'claude,codex',
    'tour pre-ticks vendors that are installed AND signed in (claude, codex), not the rest',
    `pre-ticked: ${ticked}`);

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
