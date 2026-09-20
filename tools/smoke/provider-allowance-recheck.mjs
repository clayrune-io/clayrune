#!/usr/bin/env node
/**
 * Providers row: an out-of-allowance vendor shows the record AND the way out.
 *
 * WHY THIS EXISTS
 * ----------------
 * 2026-09-19: Codex stayed refused for five days after the user bought more
 * credits. The allowance record is cleared only by a reset time or a
 * successful run, and dispatch refuses before running, so nothing could clear
 * it. The row now carries "Re-check allowance" (POST
 * /api/agent/<p>/allowance/recheck).
 *
 * Pins:
 *   1. Only the exhausted vendor's row shows the record + the button.
 *   2. Click -> exactly ONE POST to that vendor's recheck route.
 *   3. After the server stops reporting the record, the row repaints without
 *      the banner or the button.
 *   4. The composer's shared button builder is exposed and names the vendor.
 *
 * Hermetic like settings-providers.mjs: page + static assets served from THIS
 * checkout, /api/* canned, everything else aborted.
 *
 * RUN: node tools/smoke/provider-allowance-recheck.mjs
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
    remote_login: true, install_hint: '', capabilities: {}, default: false, in_use: false,
    allowance_exhausted: 'Out of allowance, resets Sep 24, 2026 7:58 AM' },
];
const rechecks = [];

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
  if (path === '/api/config') return json({ default_provider: 'claude' });
  if (path === '/api/agent/providers')
    return json({ providers: providers.map(p => ({ ...p })), default: 'claude' });
  let m = path.match(/^\/api\/agent\/([^/]+)\/allowance\/recheck$/);
  if (m && req.method() === 'POST') {
    rechecks.push(m[1]);
    const rec = providers.find(p => p.name === m[1]);
    if (rec) delete rec.allowance_exhausted;   // the server cleared the record
    return json({ ok: true, provider: m[1], was_exhausted: true, probe: 'usable' });
  }
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

await page.goto(ORIGIN + '/');
await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 15000 });
await openProviders();

const rowState = () => page.evaluate(() => {
  const out = {};
  for (const row of document.querySelectorAll('.prov-row')) {
    out[row.dataset.provider] = {
      banner: (row.querySelector('.prov-row-allowance') || {}).textContent || '',
      button: !!row.querySelector('.prov-allowance-recheck'),
    };
  }
  return out;
});

let rows = await rowState();
check(rows.codex && rows.codex.button && /Out of allowance/.test(rows.codex.banner),
  'exhausted vendor row shows the record and Re-check allowance',
  'exhausted vendor row missing record/button: ' + JSON.stringify(rows.codex));
check(rows.claude && !rows.claude.button && !rows.claude.banner,
  'healthy vendor row shows neither', 'healthy vendor row has allowance UI: ' + JSON.stringify(rows.claude));

await page.click('.prov-row[data-provider="codex"] .prov-allowance-recheck');
await page.waitForFunction(
  () => !document.querySelector('.prov-row[data-provider="codex"] .prov-allowance-recheck'),
  { timeout: 5000 }).catch(() => {});
check(rechecks.length === 1 && rechecks[0] === 'codex',
  'click POSTed exactly once to /api/agent/codex/allowance/recheck',
  'recheck calls: ' + JSON.stringify(rechecks));
rows = await rowState();
check(rows.codex && !rows.codex.button && !rows.codex.banner,
  'row repainted without the record after the server cleared it',
  'row still shows allowance UI: ' + JSON.stringify(rows.codex));

const composerBtn = await page.evaluate(() => window._allowanceRecheckBtn && window._allowanceRecheckBtn('codex'));
check(!!composerBtn && composerBtn.includes("providerAllowanceRecheck('codex'"),
  'composer button builder is exposed and names the vendor', 'composer builder: ' + composerBtn);

check(pageErrors.length === 0, 'no page errors', 'page errors: ' + pageErrors.join(' | '));
await browser.close();
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nprovider-allowance-recheck: all checks passed');
