#!/usr/bin/env node
/**
 * Secrets vault — dashboard-passcode field on set/unlock/change.
 *
 * WHY THIS EXISTS
 * ----------------
 * Found 2026-09-24 reviewing the idle-auto-lock/Lock-now change: the three
 * pre-existing vault-lock forms (set passphrase, unlock, change passphrase)
 * never sent a `passcode` field, but `_require_human_passcode`
 * (secrets_routes.py) requires one on every set/change/unlock/lock POST once
 * a dashboard passcode is configured. With one configured, Ron could not set
 * a vault passphrase, unlock, or change it at all — those three routes 403'd
 * unconditionally. Fixed by adding a "Dashboard passcode" input to each form
 * and sending it as `passcode` in the request body.
 *
 * This smoke pins the fix at the wire level (request interception, not just
 * that a field renders): each of the three requests must carry the typed
 * passcode, and a rejected passcode's server error text must reach the
 * form's status line — a silent failure here is indistinguishable from the
 * bug being back.
 *
 * Hermetic, like boot-smoke.mjs / settings-update-row.mjs: page + static
 * assets served from THIS checkout, /api/secrets* canned, everything else
 * aborted. No running MC server, no real vault.
 *
 * RUN: node tools/smoke/secrets-vault-passcode.mjs
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

let lockState = 'unconfigured';          // 'unconfigured' | 'locked' | 'unlocked'
let secretsLocked = false;
const calls = { set: [], unlock: [], change: [] };
let nextSetResponse = { ok: true, recovery_key: 'ABCD-1234-EFGH-5678' };
let nextUnlockResponse = { ok: true };
let nextChangeResponse = { ok: true };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
page.on('dialog', (d) => d.dismiss());   // Lock now uses prompt() — not under test here
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', (route) => {
  const url = new URL(route.request().url());
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
  if (path === '/api/config') return json({});

  if (path === '/api/secrets') return json({ secrets: [], locked: secretsLocked });
  if (path === '/api/secrets/vault-lock' && route.request().method() === 'GET')
    return json({ state: lockState });

  if (path === '/api/secrets/vault-lock/set') {
    calls.set.push(JSON.parse(route.request().postData() || '{}'));
    const r = nextSetResponse;
    return json(r, r.ok === false ? 400 : 200);
  }
  if (path === '/api/secrets/vault-lock/unlock') {
    calls.unlock.push(JSON.parse(route.request().postData() || '{}'));
    const r = nextUnlockResponse;
    return json(r, r.ok === false ? 400 : 200);
  }
  if (path === '/api/secrets/vault-lock/change') {
    calls.change.push(JSON.parse(route.request().postData() || '{}'));
    const r = nextChangeResponse;
    return json(r, r.ok === false ? 400 : 200);
  }
  return route.abort();
});

const openSecrets = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__secrets'));
  await page.evaluate(() => window.openSecretsVault());
  await page.waitForSelector('#secrets-lockbar', { state: 'attached', timeout: 10000 });
};

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSecretsVault === 'function', { timeout: 20000 });

  // ── 1. Unconfigured -> "Set a passphrase" sends passcode ────────────────
  lockState = 'unconfigured';
  await openSecrets();
  await page.waitForSelector('button:has-text("Set a passphrase")', { timeout: 5000 });
  await page.click('button:has-text("Set a passphrase")');
  await page.fill('#vsp-pass', 'correct-horse-battery');
  await page.fill('#vsp-pass2', 'correct-horse-battery');
  await page.fill('#vsp-passcode', 'my-dash-pass');
  await page.click('#vsp-status ~ div button.btn-add');
  await page.waitForFunction(() => document.querySelectorAll('[data-modal-id="__vault-set-passphrase"]').length === 0
    || document.querySelector('[data-modal-id="__vault-set-passphrase"]').style.display === 'none'
    || !document.body.contains(document.getElementById('vsp-pass')), { timeout: 5000 })
    .catch(() => {});
  calls.set.length === 1 && calls.set[0].passcode === 'my-dash-pass'
    ? ok('set-passphrase POSTs /vault-lock/set with passcode')
    : fail(`set-passphrase call missing/wrong passcode: ${JSON.stringify(calls.set)}`);

  // Error surfacing: passcode rejected -> server text shown in vsp-status.
  await page.evaluate(() => window.closeModalById('__vault-recovery-key'));
  nextSetResponse = { ok: false, error: 'passcode_required', message: 'Dashboard passcode required.' };
  await openSecrets();
  await page.click('button:has-text("Set a passphrase")');
  await page.fill('#vsp-pass', 'correct-horse-battery');
  await page.fill('#vsp-pass2', 'correct-horse-battery');
  await page.fill('#vsp-passcode', 'wrong-one');
  await page.click('#vsp-status ~ div button.btn-add');
  await page.waitForFunction(
    () => (document.getElementById('vsp-status')?.textContent || '').includes('Dashboard passcode required.'),
    { timeout: 5000 });
  ok('set-passphrase surfaces the server error text in the status line');
  await page.evaluate(() => window.closeModalById('__vault-set-passphrase'));

  // ── 2. Locked -> unlock sends passcode ───────────────────────────────────
  lockState = 'locked';
  await openSecrets();
  await page.waitForSelector('#vault-unlock-input', { timeout: 5000 });
  await page.fill('#vault-unlock-input', 'correct-horse-battery');
  await page.fill('#vault-unlock-passcode', 'my-dash-pass');
  await page.click('button:has-text("Unlock")');
  calls.unlock.length === 1 && calls.unlock[0].passcode === 'my-dash-pass'
    ? ok('unlock POSTs /vault-lock/unlock with passcode')
    : fail(`unlock call missing/wrong passcode: ${JSON.stringify(calls.unlock)}`);

  // Error surfacing: wrong passcode -> server text in vault-unlock-status.
  nextUnlockResponse = { ok: false, error: 'too_many_attempts', message: 'Too many attempts, try again in 5 minutes.' };
  await openSecrets();
  await page.fill('#vault-unlock-input', 'correct-horse-battery');
  await page.fill('#vault-unlock-passcode', 'my-dash-pass');
  await page.click('button:has-text("Unlock")');
  await page.waitForFunction(
    () => (document.getElementById('vault-unlock-status')?.textContent || '').includes('Too many attempts'),
    { timeout: 5000 });
  ok('unlock surfaces the server error text (too_many_attempts) in the status line');

  // ── 3. Unlocked -> "Change passphrase" sends passcode ────────────────────
  lockState = 'unlocked';
  await openSecrets();
  await page.waitForSelector('button:has-text("Change passphrase")', { timeout: 5000 });
  await page.click('button:has-text("Change passphrase")');
  await page.fill('#vcp-old', 'correct-horse-battery');
  await page.fill('#vcp-new', 'new-correct-horse-battery');
  await page.fill('#vcp-passcode', 'my-dash-pass');
  await page.click('#vcp-status ~ div button.btn-add');
  calls.change.length === 1 && calls.change[0].passcode === 'my-dash-pass'
    ? ok('change-passphrase POSTs /vault-lock/change with passcode')
    : fail(`change-passphrase call missing/wrong passcode: ${JSON.stringify(calls.change)}`);

  // Error surfacing: bad passcode -> mapped "Wrong dashboard passcode." text.
  nextChangeResponse = { ok: false, error: 'bad_passcode' };
  await openSecrets();
  await page.click('button:has-text("Change passphrase")');
  await page.fill('#vcp-old', 'correct-horse-battery');
  await page.fill('#vcp-new', 'new-correct-horse-battery');
  await page.fill('#vcp-passcode', 'wrong-one');
  await page.click('#vcp-status ~ div button.btn-add');
  await page.waitForFunction(
    () => (document.getElementById('vcp-status')?.textContent || '').includes('Wrong dashboard passcode.'),
    { timeout: 5000 });
  ok('change-passphrase maps bad_passcode (no server message) to a readable status line');

  pageErrors.length === 0 ? ok('no uncaught page errors throughout')
    : pageErrors.forEach(e => fail('uncaught: ' + e));
} finally {
  await ctx.close();
  await browser.close();
}

console.log(bad ? `\nFAILED (${bad})` : '\nALL PASS');
process.exit(bad ? 1 : 0);
