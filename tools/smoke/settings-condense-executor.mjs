#!/usr/bin/env node
/**
 * Settings -> Auto-condense -> "Executor" row (MC-1179 ML16).
 *
 * WHY THIS EXISTS
 * ----------------
 * The shipped default for `condense_mode` moved from 'agent' (launches a
 * tool-capable agent) to 'structured' (one toolless model call applied by the
 * server). The page has its own fallback for a config that carries no key:
 * `cfg.condense_mode || '<default>'`. If that literal disagrees with the
 * server's default (mc/condense_mode.py), the page shows one executor while
 * the server runs the other.
 *
 * This smoke pins what a person sees (saving is behind the human passcode
 * gate and is not exercised here):
 *   1. A config with no `condense_mode` shows Structured selected.
 *   2. A config that says 'agent' shows Agent selected (a saved choice is
 *      shown as saved, never replaced by the default).
 *
 * Hermetic, like settings-update-row.mjs: page + static assets served from
 * THIS checkout, /api/config canned, everything else aborted. No running
 * server.
 *
 * RUN: node tools/smoke/settings-condense-executor.mjs
 * Screenshot of the row: set MC_SMOKE_SHOT=<path>.
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
const ORIGIN = 'http://mc.smoke.test';
const SELECT = `select[onchange*="'condense_mode'"]`;

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let configBody = {};

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const req = route.request();
  const path = new URL(req.url()).pathname;
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
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config' && req.method() === 'GET') return json(configBody);
  return route.abort();
});

const openSettings = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  // _renderSettings() builds every category pane in one shot; the row is
  // attached before any drilling makes it visible.
  await page.waitForSelector(SELECT, { state: 'attached', timeout: 10000 });
};

const shown = () => page.evaluate((sel) => {
  const el = document.querySelector(sel);
  return {
    value: el.value,
    first: el.options[0].value,
    options: [...el.options].map((o) => o.value),
    selectedLabel: el.options[el.selectedIndex].textContent.trim(),
    hint: el.closest('.settings-row').querySelector('.settings-hint').textContent.trim(),
  };
}, SELECT);

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });

  // ── 1. No key in the config: Structured ─────────────────────────────────
  configBody = {};
  await openSettings();
  let s = await shown();
  if (s.value === 'structured') ok(`no condense_mode in config -> "${s.selectedLabel}" selected`);
  else fail(`no condense_mode in config -> expected structured, page shows ${s.value}`);
  if (s.first === 'structured' && s.options.includes('agent') && s.options.length === 2)
    ok('both executors offered, Structured first');
  else fail(`options are ${JSON.stringify(s.options)}`);
  if (/default/i.test(s.selectedLabel) && /Structured \(default\)/.test(s.hint))
    ok('the row says which executor is the default');
  else fail(`default not named: label "${s.selectedLabel}", hint "${s.hint}"`);

  if (process.env.MC_SMOKE_SHOT) {
    // Drill to wherever the row lives (category and section order are UI
    // content, not a contract), then shoot it.
    await page.evaluate((sel) => {
      const el = document.querySelector(sel);
      const pane = el.closest('.settings-detail-pane');
      window.drillSettings(pane.dataset.cat);
      const secs = [...pane.querySelectorAll('.settings-section')];
      const idx = secs.indexOf(el.closest('.settings-section'));
      if (idx >= 0 && window.drillSettingsSub) window.drillSettingsSub(idx);
    }, SELECT);
    await page.waitForTimeout(400);
    await page.screenshot({ path: process.env.MC_SMOKE_SHOT });
    ok('screenshot written');
  }

  // ── 2. A saved 'agent' is shown as saved ────────────────────────────────
  configBody = { condense_mode: 'agent' };
  await openSettings();
  s = await shown();
  if (s.value === 'agent') ok(`config says agent -> "${s.selectedLabel}" selected`);
  else fail(`config says agent -> page shows ${s.value}`);

  if (pageErrors.length) fail('page errors: ' + pageErrors.join(' | '));
  else ok('no page errors');
} catch (e) {
  fail('harness error: ' + (e && e.stack || e));
} finally {
  await browser.close();
}

if (bad) { console.error(`\nFAIL: ${bad} check(s) failed`); process.exit(1); }
console.log('\nPASS: settings condense executor row');
