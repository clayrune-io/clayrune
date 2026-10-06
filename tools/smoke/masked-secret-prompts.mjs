#!/usr/bin/env node
/**
 * Passcode / passphrase prompts must be MASKED but must NOT be password fields.
 *
 * Defect (Ron, 2026-10-06): every dashboard-passcode entry made Chrome offer
 * "Save password?" and pair it with an unrelated nearby text field as the
 * username. Browsers key that off <input type="password">, so the prompts now
 * use the shared helper static/js/masked-input.js: type=text + CSS
 * -webkit-text-security: disc, autocomplete off, the password-manager opt-out
 * attributes, no <form> around them.
 *
 * Pins, for every surface that collects the dashboard passcode or the vault
 * passphrase (human-proof modal in both modes, Allow-once, the vault panel's
 * unlock / set / change / lock-now / retire forms, the Desk vault gate, the
 * local-access passcode form):
 *   1. the input is not type=password, and nothing else in its container is
 *   2. it is masked (computed -webkit-text-security is disc) and the value
 *      still reads back for the submit that uses it
 *   3. autocomplete=off + the lastpass / 1password / bitwarden ignore attributes
 *   4. no <form> ancestor, no username-looking text sibling
 *   5. copy / cut are refused, as they are on a real password field
 * and the fallback: where the engine cannot mask text inputs (simulated by
 * hiding CSS.supports for text-security) the helper keeps type=password, so a
 * passcode is never shown in clear.
 *
 * What this cannot show: the browser's own "Save password?" bubble is browser
 * UI, outside the page. See the 2026-10-06 journal / commit for the real-Chrome
 * check of that.
 *
 * Hermetic like human-proof-guard.mjs: page + static served from THIS checkout,
 * /api/* canned, everything else aborted.
 *
 * RUN: node tools/smoke/masked-secret-prompts.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import * as pw from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const ORIGIN = 'http://mc.smoke.test';

// SMOKE_BROWSER=chromium|firefox|webkit (default chromium). The checks follow the
// engine: where it can mask text inputs the helper must use type=text + disc,
// where it cannot the helper must fall back to type=password.
const ENGINE = process.env.SMOKE_BROWSER || 'chromium';
const chromium = pw[ENGINE];

let bad = 0;
let MODE = 'css';
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

// The browser-side facts about one input. `expectMasked` is 'css' (type=text +
// disc) or 'password' (the fallback). Returns the problems found, [] when clean.
const PROBE = ({ sel, expectMasked }) => {
  const el = document.querySelector(sel);
  if (!el) return ['input ' + sel + ' not found'];
  const out = [];
  const cs = getComputedStyle(el);
  if (expectMasked === 'css') {
    if (el.type !== 'text') out.push('type is "' + el.type + '", want text');
    if (cs.webkitTextSecurity !== 'disc') out.push('computed -webkit-text-security is "' + cs.webkitTextSecurity + '", want disc');
  } else if (el.type !== 'password') {
    out.push('fallback type is "' + el.type + '", want password');
  }
  if (el.getAttribute('autocomplete') !== 'off') out.push('autocomplete is "' + el.getAttribute('autocomplete') + '", want off');
  for (const a of ['data-lpignore', 'data-1p-ignore', 'data-bwignore']) {
    if (el.getAttribute(a) !== 'true') out.push('missing ' + a);
  }
  if (!el.hasAttribute('data-mc-masked')) out.push('missing data-mc-masked');
  if (el.closest('form')) out.push('sits inside a <form>');
  if (expectMasked === 'css') {
    const scope = el.closest('.modal-content, [data-vault-gate], #local-auth-form-row, #secrets-lockbar') || el.parentElement;
    if (scope && scope.querySelector('input[type=password]')) out.push('a type=password input is in the same container');
  }
  // copy / cut are refused (a real password field refuses them too)
  for (const kind of ['copy', 'cut']) {
    const ev = new ClipboardEvent(kind, { bubbles: true, cancelable: true });
    el.dispatchEvent(ev);
    if (!ev.defaultPrevented) out.push(kind + ' is not refused');
  }
  return out;
};

async function probe(page, sel, label, expectMasked) {
  const problems = await page.evaluate(PROBE, { sel, expectMasked: expectMasked || MODE });
  check(problems.length === 0, `${label} (${sel})`, `${label} (${sel}): ${problems.join('; ')}`);
  // Masking must not change what the field holds: the submit path reads .value.
  await page.fill(sel, 'sample-secret-1');
  const v = await page.$eval(sel, (e) => e.value);
  check(v === 'sample-secret-1', `${label}: value still reads back`, `${label}: value reads "${v}"`);
  await page.fill(sel, '');
}

async function newPage(browser, { noTextSecurity = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: 1000, height: 800 } });
  const page = await ctx.newPage();
  await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
  if (noTextSecurity) {
    // An engine that cannot mask text-type inputs answers false to the
    // text-security feature test; every other property is answered as normal.
    await page.addInitScript(() => {
      const real = CSS.supports.bind(CSS);
      CSS.supports = (...a) => (/text-security/.test(String(a[0])) ? false : real(...a));
    });
  }
  page.on('dialog', (d) => { fail('unexpected native dialog: ' + d.message()); d.dismiss(); });
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const state = { lock: 'locked', passcodeConfigured: true };
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return json([]);
    if (path === '/api/config') return json({});
    if (path === '/api/local-auth/status') return json({ configured: state.passcodeConfigured });
    if (path === '/api/secrets') return json({ secrets: [], locked: state.lock === 'locked' });
    if (path === '/api/secrets/vault-lock') return json({ state: state.lock, configured: true, legacy_key_copies_present: true });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSecretsVault === 'function' && !!window.MaskedInput, { timeout: 20000 });
  return { ctx, page, pageErrors, state };
}

const browser = await chromium.launch();
try {
  // ── Chromium, the engine that masks text inputs ─────────────────────────
  {
    const { ctx, page, pageErrors, state } = await newPage(browser);
    const canMask = await page.evaluate(() => window.MaskedInput.canMask);
    console.log(`${ENGINE}: engine ${canMask ? 'masks text inputs (type=text + text-security)' : 'cannot mask text inputs (type=password fallback)'}`);
    if (ENGINE === 'chromium') check(canMask, 'helper detects -webkit-text-security support', 'helper does not detect text-security support in Chromium');
    MODE = canMask ? 'css' : 'password';

    // 1. human-proof modal, "enter your passcode"
    state.passcodeConfigured = true;
    await page.evaluate(() => { window.__hp = window.humanProofFetch('/api/x', { method: 'POST', body: '{}' }, { title: 'Confirm smoke' }); });
    await page.waitForSelector('[id^="hp-passcode-"]', { timeout: 5000 });
    const hpSel = await page.$eval('[id^="hp-passcode-"]', (e) => '#' + e.id);
    await probe(page, hpSel, 'human-proof passcode');
    await page.evaluate(() => window._hpCancel(document.querySelector('[id^="hp-passcode-"]').id.replace('hp-passcode-', '')));

    // 2. human-proof modal, "set a passcode" (fresh install)
    state.passcodeConfigured = false;
    await page.evaluate(() => { window.__hp = window.humanProofFetch('/api/x', { method: 'POST', body: '{}' }, { title: 'Set smoke' }); });
    await page.waitForSelector('[id^="hp-new-"]', { timeout: 5000 });
    const hnSel = await page.$eval('[id^="hp-new-"]', (e) => '#' + e.id);
    await probe(page, hnSel, 'human-proof new passcode');
    await page.evaluate(() => window._hpCancel(document.querySelector('[id^="hp-new-"]').id.replace('hp-new-', '')));
    state.passcodeConfigured = true;

    // 3. Allow-once
    await page.evaluate(() => { agentStatusCache['smoke-sid'] = { projectId: 'p1' }; window.attendOnceSession(null, 'smoke-sid'); });
    await page.waitForSelector('#ao-passcode-smoke-sid', { timeout: 5000 });
    await probe(page, '#ao-passcode-smoke-sid', 'Allow-once passcode');
    await page.evaluate(() => window.closeModalById('__attend-once_smoke-sid'));

    // 4. vault panel, locked: unlock passphrase + passcode
    state.lock = 'locked';
    await page.evaluate(() => window.openSecretsVault());
    await page.waitForSelector('#vault-unlock-input', { timeout: 10000 });
    await probe(page, '#vault-unlock-input', 'vault unlock passphrase');
    await probe(page, '#vault-unlock-passcode', 'vault unlock passcode');
    await page.evaluate(() => window.closeModalById('__secrets'));

    // 5. vault forms opened from their own modals
    for (const [fn, ids] of [
      ['openVaultSetPassphrase', ['#vsp-pass', '#vsp-pass2', '#vsp-passcode']],
      ['openVaultChangePassphrase', ['#vcp-old', '#vcp-new', '#vcp-passcode']],
      ['openVaultLockNow', ['#vln-passcode']],
      ['openVaultRetireLegacy', ['#vrl-passcode']],
    ]) {
      await page.evaluate((f) => window[f](), fn);
      await page.waitForSelector(ids[0], { timeout: 5000 });
      for (const id of ids) await probe(page, id, fn + ' ' + id);
      await page.evaluate(() => document.querySelectorAll('.modal-window').forEach((w) => window.closeModalById(w.dataset.modalId)));
    }

    // 6. Desk vault gate
    state.lock = 'locked';
    await page.evaluate(() => {
      const host = document.createElement('div');
      host.id = 'smoke-gate-host';
      document.body.appendChild(host);
      return window.DeskV1VaultGate.attach(host);
    });
    await page.waitForSelector('[data-vg-pass]', { timeout: 5000 });
    await probe(page, '[data-vg-pass]', 'Desk vault gate passphrase');
    await probe(page, '[data-vg-passcode]', 'Desk vault gate passcode');

    // 7. local-access passcode form (Settings)
    await page.evaluate(() => {
      const row = document.createElement('div');
      row.id = 'local-auth-form-row';
      document.body.appendChild(row);
      window._localAuthState = { configured: true };
      window.showLocalAuthForm('change');
    });
    for (const id of ['#la-cur', '#la-p1', '#la-p2']) await probe(page, id, 'local-access passcode ' + id);

    // Typing through the real keyboard still works and the screen shows dots:
    // the text is in the DOM as plain text, so the screenshot-level truth is
    // the computed style already checked; here, check the keyboard path.
    await page.click('#la-p1');
    await page.keyboard.type('typed-by-keys');
    check(await page.$eval('#la-p1', (e) => e.value === 'typed-by-keys'),
      'real keystrokes land in a masked field', 'keystrokes did not reach the masked field');

    const real = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
    check(real.length === 0, 'no page errors', 'page errors: ' + real.join(' | '));
    await ctx.close();
  }

  // ── an engine that cannot mask text inputs: fall back to type=password ──
  {
    const { ctx, page } = await newPage(browser, { noTextSecurity: true });
    console.log(`${ENGINE}: simulated engine with no text-security`);
    check(await page.evaluate(() => window.MaskedInput.canMask === false),
      'helper reports canMask=false', 'helper still reports canMask');
    await page.evaluate(() => { window.__hp = window.humanProofFetch('/api/x', { method: 'POST', body: '{}' }, { title: 'Fallback' }); });
    await page.waitForSelector('[id^="hp-passcode-"]', { timeout: 5000 });
    const sel = await page.$eval('[id^="hp-passcode-"]', (e) => '#' + e.id);
    await probe(page, sel, 'fallback human-proof passcode', 'password');
    await ctx.close();
  }
} finally {
  await browser.close();
}

if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall masked-secret-prompt checks passed');
