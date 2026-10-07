#!/usr/bin/env node
/**
 * Desk v1 — "Sign in with the saved login" button (static/js/desk-v1-connect-signin.js, slice P2b),
 * the browser half, against a fake server. The server half is tests/test_desk_signin_fill.py.
 *
 * After the 2026-10-05 audit POST /api/desk/connect/signin/fill needs the dashboard passcode on every
 * call (the one proof an agent cannot forge), so the button goes through the shared passcode prompt:
 *
 *   - clicking it opens the prompt and sends NOTHING until the passcode is typed;
 *   - cancelling the prompt sends nothing and leaves no message;
 *   - a wrong passcode reaches the server once and shows the server's refusal;
 *   - the right passcode sends the request ONCE with `passcode`, a login NAME (never a value) and no
 *     project_id, and shows the state word's message.
 *
 * RUN   cd tools/smoke && node desk-v1-connect-signin-fill.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const PROJECTS = JSON.parse(readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8'));
const STATIC = loadStaticJsCss(REPO_ROOT, { isolateConnectScreens: true });
const ORIGIN = 'http://mc.smoke.test';
const PASSCODE = 'right-passcode';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const fills = [];
const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1200, height: 800 } })).newPage();
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
await page.route('**/*', async (route) => {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
  if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
  if (path === '/api/projects') return J(PROJECTS);
  if (path === '/api/config') return J({});
  if (path === '/api/local-auth/status') return J({ configured: true });
  if (path === '/api/desk/connect/signin/fill') {
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    fills.push(body);
    if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
    return J({ ok: true, state: 'submitted', message: 'The login was typed and submitted. Check the pane for the result.' });
  }
  return route.abort();
});
await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
await page.waitForFunction(() => !!window.DeskV1ConnectSignin && typeof window.humanProofFetch === 'function', null, { timeout: 8000 });

// the owner's side, reduced to what the button needs: a root, a repaint, an api() it must NOT use
await page.evaluate(() => {
  const root = document.createElement('div');
  root.id = 'fill-root';
  document.body.appendChild(root);
  const S = window.DeskV1ConnectSignin;
  const spec = { url: null, login: 'linkedin.login', profile: 'li-ron' };
  const paint = () => { root.innerHTML = S.fillHTML('k', spec); S.bindFill(root, 'k', spec, ctx, () => ({ service: 'linkedin', route_id: 'linkedin-browser', account_id: 'ch-1', project_id: 'ignored-by-server' })); };
  const ctx = { api: () => { throw new Error('the fill must not go through the plain api()'); }, repaint: paint };
  paint();
});

const msg = () => page.evaluate(() => { const e = document.querySelector('#fill-root [data-cs-fillmsg]'); return e ? e.textContent.trim() : ''; });
const prompt = (code) => page.fill('input[id^="hp-passcode-"]', code).then(() => page.click('.modal-content .btn-add'));

console.log('Sign in with the saved login: the passcode prompt');
await page.click('[data-cs-fillbtn="k"]');
await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
check(fills.length === 0, 'clicking the button opens the passcode prompt and sends nothing yet', 'a request was sent before the passcode: ' + JSON.stringify(fills));

await page.click('.modal-content .btn-secondary');                       // Cancel
await page.waitForFunction(() => !document.querySelector('input[id^="hp-passcode-"]'), null, { timeout: 4000 });
check(fills.length === 0 && (await msg()) === '', 'cancelling the prompt sends nothing and leaves no message', `fills ${fills.length} msg "${await msg()}"`);
check(!(await page.$eval('[data-cs-fillbtn="k"]', (b) => b.disabled)), 'the button is usable again after a cancel', 'the button stayed busy');

await page.click('[data-cs-fillbtn="k"]');
await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
await prompt('wrong-passcode');
await page.waitForFunction(() => /Wrong dashboard passcode/.test(document.body.innerText), null, { timeout: 4000 });
check(fills.length === 1 && fills[0].passcode === 'wrong-passcode', 'a wrong passcode reaches the server once and the prompt says so', JSON.stringify(fills));

await prompt(PASSCODE);
await page.waitForFunction(() => /typed and submitted/.test((document.querySelector('#fill-root [data-cs-fillmsg]') || {}).textContent || ''), null, { timeout: 4000 });
const last = fills[fills.length - 1];
check(fills.length === 2 && last.passcode === PASSCODE, 'the right passcode sends the request once more, with the passcode', JSON.stringify(fills));
check(last.login === undefined && last.account_id === 'ch-1' && last.service === 'linkedin' && last.route_id === 'linkedin-browser',
  'the request names the account and route, never a value', JSON.stringify(last));
check(/typed and submitted/.test(await msg()), 'the server\'s message is shown', await msg());
check(realErrors(pageErrors).length === 0, 'no page error', realErrors(pageErrors).join(' | '));

function realErrors(e) { return e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m)); }
await browser.close();
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall checks passed');
