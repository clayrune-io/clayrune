#!/usr/bin/env node
/**
 * Settings > Agent Providers > Claude: the API credits card, and its key field.
 *
 * Ron, 2026-10-30: there was no way to enter an API key under Settings. Item 2
 * of the card is now a paste field that saves into the vault through the SAME
 * human-only path as Settings > Secrets (POST /api/secrets behind the
 * dashboard-passcode prompt), under 'anthropic-api-key' by default, then
 * selects it. It also keeps "pick an existing vault secret".
 *
 * Pins:
 *   1. The card renders directly under the Claude row, not inside any
 *      .prov-row (every vendor row keeps one shape).
 *   2. The paste field is masked; the picker lists the vault's global secrets;
 *      the default vault name is 'anthropic-api-key'.
 *   3. Save key -> passcode prompt first (nothing sent yet) -> POST
 *      /api/secrets carries the name, the pasted value, scope global and the
 *      retyped passcode; the field is emptied; the name is selected.
 *   4. The pasted value is never in the DOM, an input, web storage or the
 *      settings PUT body after saving.
 *   5. Cancelling the prompt saves nothing and says so.
 *   6. Save sends the secret NAME to PUT /api/api-credits/settings.
 *   7. 390px: no horizontal overflow.
 *
 * Hermetic like settings-providers.mjs: page + static assets served from THIS
 * checkout, /api/* canned, everything else aborted.
 *
 * RUN: node tools/smoke/api-credits-settings.mjs
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

const KEY = 'sk-ant-api03-SMOKE-NEVER-ECHO-0123456789';
const providers = [
  { name: 'claude', display_name: 'Claude Code', installed: true, version: '2.1.0', auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: true, in_use: true },
  { name: 'codex', display_name: 'Codex CLI', installed: true, version: '2.1.0', auth_status: 'ok',
    remote_login: true, install_hint: '', capabilities: {}, default: false, in_use: false },
];
let settings = { mode: 'off', secret_name: '', plan_credit_usd: null, budget_usd: null, stop_pct: 90, reset_day: 1 };
const plan = { status: 'detected', label: 'Max 20x', credit_usd: 200, subscription_type: 'max' };
const vaultSecrets = [{ name: 'other-key', scope: 'global', allow_unattended: true }];
const calls = { secrets: [], put: [] };

const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1000, height: 900 } });
const page = await ctx.newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('console', (m) => { if (process.env.MC_SMOKE_DEBUG && m.type() === 'error') console.error('console:', m.text(), m.location().url); });
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
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/config') return json({ default_provider: 'claude' });
  if (path === '/api/agent/providers') return json({ providers, default: 'claude' });
  if (path === '/api/api-credits/status') return json({ settings, plan, cycle: {}, ledger: {} });
  if (path === '/api/api-credits/settings' && req.method() === 'PUT') {
    const body = JSON.parse(req.postData() || '{}');
    calls.put.push(body);
    settings = { ...settings, ...body };
    return json({ settings, plan, cycle: {}, ledger: {} });
  }
  if (path === '/api/secrets' && req.method() === 'POST') {
    const body = JSON.parse(req.postData() || '{}');
    calls.secrets.push(body);
    vaultSecrets.push({ name: body.name, scope: body.scope, allow_unattended: body.allow_unattended });
    return json({ ok: true, name: body.name });
  }
  if (path === '/api/secrets') return json({ secrets: vaultSecrets, locked: false });
  return route.abort();
});

const openProviders = async () => {
  await page.evaluate(() => window.closeModalById && window.closeModalById('__settings'));
  await page.evaluate(() => window.openSettings());
  await page.waitForSelector('#settings-providers-section', { state: 'attached', timeout: 10000 });
  await page.evaluate(() => {
    window.drillSettings('providers');
    // Providers opens as a sub-list; choose the section that holds the vendor rows.
    const pane = document.querySelector('#settings-detail .settings-detail-pane[data-cat="providers"]');
    const i = [...pane.children].filter((el) => el.classList.contains('settings-section')).findIndex((s) => s.id === 'settings-providers-section');
    if (i >= 0) window.drillSettingsSub(i);
  });
  await page.waitForFunction(() => { const s = document.getElementById('settings-providers-section');
    return s && s.offsetParent !== null; }, { timeout: 5000 });
};
const answerPasscode = async (pc) => {
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 10000 });
  await page.fill('input[id^="hp-passcode-"]', pc);
  await page.click('.modal-layer button.btn-add:has-text("Confirm")');
};
const keyMsg = () => page.evaluate(() => (document.querySelector('#api-credits-card .apc-msg') || {}).textContent || '');

try {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => typeof window.openSettings === 'function'
    && typeof window._renderProviderRow === 'function' && !!window.ApiCredits, { timeout: 20000 });
  await openProviders();
  await page.waitForSelector('#apc-key-value', { timeout: 10000 });

  const place = await page.evaluate(() => {
    const card = document.getElementById('api-credits-card');
    const claude = document.querySelector('#settings-providers-section .prov-row[data-provider="claude"]');
    return { cards: document.querySelectorAll('#api-credits-card').length,
      insideRow: !!card.closest('.prov-row'), follows: !!claude && claude.nextElementSibling === card };
  });
  check(place.cards === 1 && !place.insideRow && place.follows,
    'one card, directly after the Claude row, not inside any .prov-row', `placement wrong: ${JSON.stringify(place)}`);

  const field = await page.evaluate(() => {
    const i = document.getElementById('apc-key-value');
    const sel = document.getElementById('apc-secret');
    return { type: i.type, sec: getComputedStyle(i).webkitTextSecurity || '', opts: [...sel.options].map((o) => o.value) };
  });
  check(field.type === 'password' || /disc|circle|square/.test(field.sec),
    'paste field is masked', `paste field not masked: ${JSON.stringify(field)}`);
  check(field.opts.includes('other-key'), 'picker lists the existing vault secret', `picker options: ${field.opts}`);
  check((await page.inputValue('#apc-key-name')) === 'anthropic-api-key',
    "default vault name is 'anthropic-api-key'", 'default name wrong');

  // cancel first: nothing saved
  await page.fill('#apc-key-value', KEY);
  await page.click('#apc-key-save');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 10000 });
  check(calls.secrets.length === 0, 'Save key raises the passcode prompt first (nothing sent yet)',
    `secret POSTs before the prompt was answered: ${calls.secrets.length}`);
  await page.click('button.btn-secondary:has-text("Cancel")');
  await page.waitForFunction(() => /Cancelled/.test((document.querySelector('#api-credits-card .apc-msg') || {}).textContent || ''), { timeout: 5000 });
  check(calls.secrets.length === 0, 'cancelling the prompt saves nothing and says so', `POSTs after cancel: ${calls.secrets.length}`);

  // real save
  await page.fill('#apc-key-value', KEY);
  await page.click('#apc-key-save');
  await answerPasscode('smoke-pass-1');
  await page.waitForFunction(() => /Saved to the vault/.test((document.querySelector('#api-credits-card .apc-msg') || {}).textContent || ''), { timeout: 10000 });
  const sent = calls.secrets[0] || {};
  check(calls.secrets.length === 1 && sent.name === 'anthropic-api-key' && sent.value === KEY
    && sent.scope === 'global' && sent.passcode === 'smoke-pass-1',
    'POST /api/secrets carries default name, the pasted value, global scope and the retyped passcode',
    `secret POST wrong: ${JSON.stringify({ ...sent, value: sent.value === KEY ? '<key>' : sent.value })}`);
  check((await page.inputValue('#apc-key-value')) === '', 'paste field emptied after saving', 'paste field still holds the key');
  check((await page.inputValue('#apc-secret')) === 'anthropic-api-key', 'saved secret is selected in the picker',
    `picker value: ${await page.inputValue('#apc-secret')}`);

  // settings Save sends the name only (mode stays off: no passcode prompt)
  await page.click('#apc-save');
  await page.waitForFunction(() => /Saved\./.test((document.getElementById('apc-msg') || {}).textContent || ''), { timeout: 10000 });
  check(calls.put.length === 1 && calls.put[0].secret_name === 'anthropic-api-key',
    'Save sends the secret NAME to PUT /api/api-credits/settings', `PUT bodies: ${JSON.stringify(calls.put)}`);

  const leaks = await page.evaluate((k) => ({
    html: document.documentElement.outerHTML.includes(k),
    storage: JSON.stringify(localStorage).includes(k) || JSON.stringify(sessionStorage).includes(k),
    values: [...document.querySelectorAll('input,textarea')].some((i) => i.value.includes(k)),
  }), KEY);
  check(!leaks.html && !leaks.storage && !leaks.values, 'key is not in the DOM, an input, or web storage after saving',
    `key leaked: ${JSON.stringify(leaks)}`);
  check(!JSON.stringify(calls.put).includes(KEY), 'key never sent to the settings route', 'key was sent in a settings PUT');

  await page.setViewportSize({ width: 390, height: 800 });
  await openProviders();
  await page.waitForSelector('#apc-key-value', { timeout: 10000 });
  await page.waitForTimeout(200);
  const geo = await page.evaluate(() => {
    const r = document.getElementById('api-credits-card').getBoundingClientRect();
    return { over: document.documentElement.scrollWidth - document.documentElement.clientWidth, right: r.right, vw: innerWidth };
  });
  check(geo.over <= 1 && geo.right <= geo.vw + 1, '390px: no horizontal overflow, card inside the viewport',
    `overflow: ${JSON.stringify(geo)}`);
  if (process.env.MC_SMOKE_DEBUG) console.error(await page.evaluate(() => [...document.querySelectorAll('#api-credits-card *')].filter((e) => e.getBoundingClientRect().right > innerWidth + 1).slice(0, 8).map((e) => e.tagName + '#' + e.id + '.' + e.className + ' ' + Math.round(e.getBoundingClientRect().right)).join(' ; ')));
  if (process.env.MC_SMOKE_SHOT) await page.screenshot({ path: process.env.MC_SMOKE_SHOT });

  check(pageErrors.length === 0, 'no uncaught page errors', `page errors: ${pageErrors.join(' | ')}`);
} catch (e) {
  if (process.env.MC_SMOKE_DEBUG) console.error(await page.evaluate(() => JSON.stringify({ os: typeof window.openSettings, pr: typeof window._renderProviderRow, ac: typeof window.ApiCredits })).catch(() => 'eval failed'));
  fail('smoke aborted: ' + (e && e.message ? e.message : e) + (pageErrors.length ? ' | page errors: ' + pageErrors.join(' | ') : ''));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
