#!/usr/bin/env node
/**
 * MC-713 — Settings > System > Installed App.
 *
 * WHY THIS EXISTS
 * ----------------
 * An installed Clayrune PWA silently keeps running old code after
 * manifest.json/sw.js change; before this section the only fix was a manual
 * Chrome uninstall/reinstall with no in-app guidance. This section surfaces
 * install state, service-worker state/version, manifest hash and a manual
 * "Force update" action.
 *
 * Pins:
 *   1. The section renders under Settings > System, titled "Installed App",
 *      and its live SW version + manifest hash hydrate from the real
 *      /sw.js and /manifest.json content (not hardcoded).
 *   2. When the browser has no serviceWorker support, the Force update
 *      button is disabled with a one-line reason instead of silently doing
 *      nothing.
 *   3. The live search box finds "Installed App" by title.
 *
 * Hermetic like engine-fallback-settings.mjs: page + static assets from THIS
 * checkout (including the REAL /sw.js and /manifest.json), /api/* canned,
 * everything else aborted.
 *
 * RUN: node tools/smoke/installed-app.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const STATIC_ROOT = resolve(REPO_ROOT, 'static');
// 'localhost' (not an arbitrary hostname like mc.smoke.test) so Chromium
// treats this as a secure context — the Service Worker API this section
// reads (`navigator.serviceWorker`) does not exist otherwise, which is
// exactly the false "not supported" case that broke the disabled-scenario
// below before this was pinned to localhost.
const ORIGIN = 'http://localhost:59931';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const SW_SOURCE = readFileSync(resolve(STATIC_ROOT, 'sw.js'), 'utf8');
const REAL_SW_VERSION = (SW_SOURCE.match(/SW_VERSION\s*=\s*['"]([^'"]+)['"]/) || [])[1];
if (!REAL_SW_VERSION) { console.error('could not find SW_VERSION in static/sw.js'); process.exit(1); }

function registerRoutes(page) {
  return page.route('**/*', (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const json = (body, status = 200) => route.fulfill({
      status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8',
        body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
    if (path === '/sw.js')
      return route.fulfill({ status: 200, contentType: 'application/javascript', body: SW_SOURCE });
    if (path === '/manifest.json')
      return route.fulfill({ status: 200, contentType: 'application/manifest+json',
        body: readFileSync(resolve(STATIC_ROOT, 'manifest.json'), 'utf8') });
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
    if (path === '/api/config') return json({});
    if (path === '/api/agent/providers')
      return json({ providers: [{ name: 'claude', display_name: 'Claude Code', installed: true,
        auth_status: 'ok', remote_login: true, install_hint: '', capabilities: {}, default: true, in_use: true }],
        default: 'claude' });
    if (path === '/api/local-auth/status') return json({ configured: true });
    return route.abort();
  });
}

// Drill Settings -> System -> the "Installed App" sub-section.
async function drillToInstalledApp(page) {
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('[data-cat="system"] #installed-app-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('system'));
  await page.evaluate(() => {
    const idx = [...document.querySelectorAll('[data-cat="system"] .settings-section-title')]
      .findIndex((t) => t.textContent.trim() === 'Installed App');
    window.drillSettingsSub(idx);
  });
  await page.waitForFunction(() => {
    const s = document.getElementById('installed-app-section');
    return s && s.offsetParent !== null;
  }, { timeout: 5000 });
}

async function runSupportedScenario(browser) {
  console.log('scenario: serviceWorker supported');
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await registerRoutes(page);
  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.openSettings === 'function'
      && typeof window.refreshInstalledAppSection === 'function'
      && typeof window.forceUpdateServiceWorker === 'function', { timeout: 20000 });
    ok('openSettings / refreshInstalledAppSection / forceUpdateServiceWorker reachable from onclick="" (exported on window)');

    await drillToInstalledApp(page);
    ok('the section renders under Settings > System, titled "Installed App"');

    await page.waitForFunction(() => !!window._mcSwVersion && !!window._mcManifestHash, { timeout: 10000 });
    const hint = await page.evaluate(() => document.getElementById('installed-app-section').innerText);
    check(hint.includes(REAL_SW_VERSION),
      `service-worker version hydrates from the real sw.js (${REAL_SW_VERSION})`,
      `version ${REAL_SW_VERSION} not found in section text: ${JSON.stringify(hint)}`);
    check(/hash [0-9a-f]{6,}/.test(hint),
      'manifest hash hydrates from the real manifest.json',
      `no manifest hash found in section text: ${JSON.stringify(hint)}`);

    const disabled = await page.evaluate(() =>
      document.getElementById('installed-app-update-btn').disabled);
    check(!disabled, 'Force update button is enabled when serviceWorker is supported',
      'Force update button was disabled with serviceWorker support present');

    pageErrors.length === 0 ? ok('no uncaught page errors')
      : pageErrors.forEach((e) => fail('uncaught: ' + e));
  } finally {
    await ctx.close();
  }
}

async function runUnsupportedScenario(browser) {
  console.log('scenario: serviceWorker NOT supported');
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => {
    localStorage.setItem('walkthrough_done', '1');
    // Simulate a browser with no Service Worker API before any app script runs
    // (the boot-time registration IIFE and the section's own swSupported check
    // both key off 'serviceWorker' in navigator).
    Object.defineProperty(window.navigator, 'serviceWorker', { value: undefined, configurable: true });
  });
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await registerRoutes(page);
  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });
    await drillToInstalledApp(page);

    const state = await page.evaluate(() => ({
      disabled: document.getElementById('installed-app-update-btn').disabled,
      reason: document.getElementById('installed-app-update-hint').textContent,
    }));
    check(state.disabled, 'Force update button is disabled when serviceWorker is unsupported',
      `button was not disabled: ${JSON.stringify(state)}`);
    check(/not supported/i.test(state.reason),
      `disabled button shows a one-line reason ("${state.reason}")`,
      `no reason shown: ${JSON.stringify(state)}`);

    pageErrors.length === 0 ? ok('no uncaught page errors')
      : pageErrors.forEach((e) => fail('uncaught: ' + e));
  } finally {
    await ctx.close();
  }
}

async function runSearchScenario(browser) {
  console.log('scenario: search finds "Installed App"');
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  await registerRoutes(page);
  try {
    await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => typeof window.filterSettings === 'function', { timeout: 20000 });
    await page.evaluate(() => window.openSettings());
    await page.waitForSelector('#installed-app-section', { state: 'attached', timeout: 10000 });
    await page.evaluate(() => window.filterSettings('Installed App'));
    const visible = await page.evaluate(() => {
      const sec = document.getElementById('installed-app-section');
      const pane = sec && sec.closest('.settings-detail-pane');
      return !!sec && !sec.classList.contains('settings-hidden')
        && !!pane && !pane.classList.contains('settings-hidden');
    });
    check(visible, 'searching "Installed App" surfaces the section (not hidden)',
      'the section stayed hidden after searching its own title');
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await runSupportedScenario(browser);
  await runUnsupportedScenario(browser);
  await runSearchScenario(browser);
} finally {
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
