#!/usr/bin/env node
/**
 * MC-975 Part A -- the Settings toggle for codex_unattended_sandbox
 * (Settings > Agent > Advanced). The human-only gate is server-side
 * (tests/test_codex_sandbox_setting.py); this pins the UI half:
 *   1. the row renders in Advanced, reflecting the server value
 *      (absent key = the server default ON, so it reads ON, not OFF),
 *   2. clicking it goes through the passcode modal (humanProofFetch) and
 *      PUTs exactly {codex_unattended_sandbox: <bool>} -- nothing else,
 *   3. cancelling the passcode prompt leaves the saved value unchanged and
 *      re-renders the switch from the server's real config.
 *
 * Hermetic like engine-fallback-settings.mjs: page + static assets from THIS
 * checkout, /api/* canned, everything else aborted.
 *
 * RUN: node tools/smoke/codex-sandbox-setting.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = resolve(__dirname, '..', '..', 'static');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

let config = { default_provider: 'claude' };   // codex_unattended_sandbox ABSENT -> server default ON
const puts = [];

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
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
  if (path === '/api/config' && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    puts.push(body);
    const { passcode, ...rest } = body;
    config = { ...config, ...rest };
    return json({ ok: true });
  }
  if (path === '/api/config') return json(config);
  if (path === '/api/agent/providers') return json({ providers: [], default: 'claude' });
  return route.abort();
});

const humanProof = async (action) => {   // 'submit' | 'cancel'
  await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 });
  await page.evaluate((a) => {
    const win = document.querySelector('[data-modal-id^="__human-proof-"]');
    const id = win.dataset.modalId;
    if (a === 'submit') {
      document.getElementById(`hp-passcode-${id}`).value = 'smoke-dash-passcode';
      window._hpSubmit(id);
    } else {
      (window._hpCancel || window.closeModalById)(id);
    }
  }, action);
};

const openAdvanced = async () => {
  await page.evaluate(() => { window.closeModalById && window.closeModalById('__settings'); });
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#codex-sandbox-row', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => window.drillSettings('agent'));
  await page.evaluate(() => {
    const idx = [...document.querySelectorAll('[data-cat="agent"] .settings-section-title')]
      .findIndex(t => t.textContent.trim() === 'Advanced');
    window.drillSettingsSub(idx);
  });
  await page.waitForFunction(() => {
    const r = document.getElementById('codex-sandbox-row');
    return r && r.offsetParent !== null;
  }, { timeout: 5000 });
};
const toggleOn = () => page.evaluate(() =>
  document.querySelector('#codex-sandbox-row .settings-toggle').classList.contains('on'));

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function', { timeout: 20000 });

  await openAdvanced();
  check(await toggleOn() === true, 'absent key renders ON (the server default)', 'absent key rendered OFF');
  const hint = await page.evaluate(() => document.querySelector('#codex-sandbox-row .settings-hint').textContent);
  check(/passcode/i.test(hint) && /agent cannot/i.test(hint),
    'hint says the change needs the passcode and an agent cannot make it', `hint: ${hint}`);

  puts.length = 0;
  await page.evaluate(() => document.querySelector('#codex-sandbox-row .settings-toggle').click());
  await humanProof('submit');
  await page.waitForFunction(() => true);
  await page.waitForTimeout(300);
  check(puts.length === 1 && puts[0].codex_unattended_sandbox === false
      && Object.keys(puts[0]).sort().join() === 'codex_unattended_sandbox,passcode',
    'click -> passcode modal -> PUT carries only codex_unattended_sandbox=false (+ passcode)',
    `PUT bodies: ${JSON.stringify(puts)}`);
  check(await toggleOn() === false, 'switch shows OFF after the saved change', 'switch not OFF after save');

  puts.length = 0;
  await page.evaluate(() => document.querySelector('#codex-sandbox-row .settings-toggle').click());
  await humanProof('cancel');
  await page.waitForTimeout(500);
  await openAdvanced();
  check(puts.length === 0 && config.codex_unattended_sandbox === false && await toggleOn() === false,
    'cancelling the passcode prompt sends no PUT and the switch re-renders from the saved value',
    `puts=${JSON.stringify(puts)} saved=${config.codex_unattended_sandbox} on=${await toggleOn()}`);

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
