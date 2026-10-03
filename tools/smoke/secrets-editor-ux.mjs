#!/usr/bin/env node
/**
 * Secrets vault — list + editor scroll, visible save outcome, client-side name rule.
 *
 * WHY THIS EXISTS
 * ----------------
 * 2026-10-03: Ron tried to store a key named "IIElevenlabs key" twice; the server
 * 400'd both (name must match ^[a-z0-9][a-z0-9._-]{0,63}$) and he saw nothing —
 * the error went to #sec-status, an 11px line at the foot of an editor modal that
 * could not scroll, so it sat below the fold. The vault list could not scroll
 * either, so keys past the fold were unreachable. Pins, at 1440 and 390 wide:
 *   1. the list and the editor body are real scroll containers (.modal-scroll-body)
 *   2. a failed save (client name check, and a server error) puts its message in
 *      the viewport beside Save, and toasts
 *   3. a bad name is caught BEFORE any request/passcode prompt, the rule is shown
 *      under the field, and the lowercase slug is offered ("Use iielevenlabs-key")
 *
 * Hermetic: static assets from THIS checkout, /api/secrets canned, rest aborted.
 * RUN: node tools/smoke/secrets-editor-ux.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = resolve(__dirname, '..', '..', 'static');
const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const check = (cond, m, detail = '') => { if (cond) ok(m); else { console.error('  ✗ ' + m + (detail ? ' — ' + detail : '')); bad++; } };

const SECRETS = Array.from({ length: 16 }, (_, i) => ({
  name: 'secret-' + String(i).padStart(2, '0'), username: 'u' + i, entry_type: 'login',
  scope: 'global', allow_unattended: true, description: 'fixture ' + i, use_count: 0,
}));

const browser = await chromium.launch();
for (const [w, h, mobile] of [[1440, 900, false], [390, 780, true]]) {
  console.log(`\n@ ${w}px`);
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, isMobile: mobile, hasTouch: mobile });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  const pageErrors = []; page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const writes = []; page.on('request', (r) => { if (r.method() !== 'GET' && /\/api\/secrets/.test(r.url())) writes.push(r.url()); });
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (b) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(b) });
    if (path === '/' || path === '/index.html')
      return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
    if (path.startsWith('/static/')) {
      const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
      if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
        const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
        return route.fulfill({ status: 200, contentType: type, body: readFileSync(file) });
      }
      return route.abort();
    }
    if (path === '/api/projects') return json([]);
    if (path === '/api/config') return json({});
    if (path === '/api/secrets') return json({ secrets: SECRETS, locked: false });
    if (path === '/api/secrets/vault-lock') return json({ state: 'unlocked' });
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSecretsVault === 'function', null, { timeout: 15000 });

  // 1. list scrolls
  await page.evaluate(() => window.openSecretsVault());
  await page.waitForFunction(() => document.querySelectorAll('#secrets-list > *').length >= 16, null, { timeout: 8000 });
  const list = await page.evaluate(async () => {
    const sb = document.querySelector('[data-modal-id="__secrets"] .modal-scroll-body');
    const before = sb.scrollTop; sb.scrollTop = sb.scrollHeight; await new Promise((r) => setTimeout(r, 100));
    const rows = [...document.querySelectorAll('#secrets-list > *')];
    return { overflows: sb.scrollHeight > sb.clientHeight + 2, moved: sb.scrollTop > before,
      lastInView: rows[rows.length - 1].getBoundingClientRect().bottom <= sb.getBoundingClientRect().bottom + 1 };
  });
  check(list.overflows && list.moved, 'the vault list is a scroll container and scrolls');
  check(list.lastInView, 'the last key is reachable after scrolling');

  // 2. editor scrolls, footer pinned in view
  await page.evaluate(() => window.openSecretEditor(null));
  await page.waitForSelector('#sec-name');
  const ed = await page.evaluate(() => {
    const sb = document.querySelector('[data-modal-id="__secret-edit"] .modal-scroll-body');
    const save = document.getElementById('sec-save').getBoundingClientRect();
    return { overflows: sb.scrollHeight > sb.clientHeight + 2, saveInView: save.bottom <= innerHeight && save.top >= 0 };
  });
  check(ed.overflows, 'the editor body is a scroll container');
  check(ed.saveInView, 'Save stays on screen without scrolling');

  // 3. bad name: caught client-side, rule + slug shown, no request
  await page.fill('#sec-name', 'IIElevenlabs key');
  await page.fill('#sec-value', 'not-a-real-key');
  check(await page.isVisible('#sec-name-fix'), 'the name rule shows under the field as you type');
  check(((await page.textContent('#sec-name-fix button')) || '').trim() === 'Use iielevenlabs-key', 'the lowercase slug is offered');
  await page.click('#sec-save');
  const bad1 = await page.evaluate(() => {
    const s = document.getElementById('sec-status').getBoundingClientRect();
    return { text: document.getElementById('sec-status').textContent, inView: s.top >= 0 && s.bottom <= innerHeight,
      toast: (document.getElementById('toast-container') || {}).textContent || '', open: !!document.querySelector('[data-modal-id="__secret-edit"]') };
  });
  check(/capital letters/.test(bad1.text) && bad1.inView, 'a bad name shows its error beside Save, in the viewport', JSON.stringify(bad1));
  check(/Not saved/.test(bad1.toast), 'a bad name also toasts');
  check(bad1.open && writes.length === 0, 'the modal stays open and nothing was sent (no passcode prompt)');

  // 4. take the suggestion, then a SERVER failure is just as visible
  await page.click('#sec-name-fix button');
  check((await page.inputValue('#sec-name')) === 'iielevenlabs-key', 'the suggestion fills the name field');
  check(!(await page.isVisible('#sec-name-fix')), 'the warning clears once the name is valid');
  await page.evaluate(() => { window.humanProofFetch = async () => ({ ok: false, status: 400, body: { error: 'server said no' } }); });
  await page.click('#sec-save');
  await page.waitForFunction(() => /server said no/.test(document.getElementById('sec-status')?.textContent || ''), null, { timeout: 5000 });
  const srv = await page.evaluate(() => { const s = document.getElementById('sec-status').getBoundingClientRect(); return s.top >= 0 && s.bottom <= innerHeight; });
  check(srv, 'a server error is shown in the viewport beside Save');

  // 5. success closes the modal and toasts
  await page.evaluate(() => { window.humanProofFetch = async () => ({ ok: true, status: 200, body: { name: 'iielevenlabs-key', entry_type: 'login', username: '' } }); });
  await page.click('#sec-save');
  await page.waitForFunction(() => !document.querySelector('[data-modal-id="__secret-edit"]'), null, { timeout: 5000 });
  check(/Saved/.test(await page.evaluate(() => (document.getElementById('toast-container') || {}).textContent || '')), 'success closes the editor and toasts');
  check(pageErrors.length === 0, 'no uncaught page errors', pageErrors.join(' | '));
  await ctx.close();
}
await browser.close();
if (bad) { console.error(`\nFAIL — ${bad} check(s)`); process.exit(1); }
console.log('\nALL PASS');
