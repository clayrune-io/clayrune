#!/usr/bin/env node
/**
 * The shared passcode prompt (human-proof-modal.js) while a slow route works.
 * Ron 2026-10-07: a storyboard Render took ~2 minutes to answer, the prompt sat
 * unchanged, and pressing Confirm again looked like it did nothing. Pins:
 *   1. after Confirm the prompt says it is working and its controls are disabled,
 *   2. a second Confirm / Cancel while it works sends nothing and closes nothing,
 *   3. exactly one request reaches the server, and the prompt closes on its answer.
 *
 * Hermetic: page + static assets from THIS checkout, /api/* canned.
 * RUN: node tools/smoke/human-proof-busy.mjs
 */
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join, normalize } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const STATIC_ROOT = resolve(__dirname, '..', '..', 'static');

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const posts = [];
let release = null;

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1000, height: 900 } })).newPage();
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
const pageErrors = [];
page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

await page.route('**/*', async (route) => {
  const req = route.request();
  const path = new URL(req.url()).pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/' || path === '/index.html')
    return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: readFileSync(resolve(STATIC_ROOT, 'index.html'), 'utf8') });
  if (path.startsWith('/static/')) {
    const file = normalize(join(STATIC_ROOT, path.slice('/static/'.length)));
    if (file.startsWith(STATIC_ROOT) && existsSync(file)) {
      const type = file.endsWith('.js') ? 'text/javascript; charset=utf-8' : file.endsWith('.css') ? 'text/css; charset=utf-8' : 'application/octet-stream';
      return route.fulfill({ status: 200, contentType: type, body: readFileSync(file, 'utf8') });
    }
    return route.abort();
  }
  if (path === '/api/projects') return json([]);
  if (path === '/api/local-auth/status') return json({ configured: true });
  if (path === '/api/smoke/slow' && req.method() === 'POST') {
    posts.push(JSON.parse(req.postData() || '{}'));
    await new Promise((r) => { release = r; });          // held until the test lets it answer
    return json({ render: { render_id: 'rnd-slow' } }, 201);
  }
  return route.abort();
});

await page.goto('http://mc.smoke.test/');
await page.waitForFunction(() => typeof window.humanProofFetch === 'function', { timeout: 15000 });
await page.evaluate(() => {
  window.__hpResult = undefined;
  window.humanProofFetch('/api/smoke/slow', { method: 'POST', body: JSON.stringify({ idempotency_key: 'k1' }) },
    { title: 'Render this video', description: 'Re-enter your dashboard passcode.' })
    .then((r) => { window.__hpResult = r; });
});
await page.waitForSelector('[data-modal-id^="__human-proof-"]', { timeout: 5000 });
const id = await page.$eval('[data-modal-id^="__human-proof-"]', (w) => w.dataset.modalId);

await page.fill(`#hp-passcode-${id}`, 'smoke-dash-passcode');
await page.click(`#hp-body-${id} .btn-add`);
await page.waitForSelector(`#hp-body-${id} [data-hp-busy]`, { timeout: 5000 });
const busy = await page.evaluate((mid) => {
  const body = document.getElementById(`hp-body-${mid}`);
  return { note: body.querySelector('[data-hp-busy]').textContent, confirm: body.querySelector('.btn-add').textContent,
    disabled: [...body.querySelectorAll('input, button')].every((el) => el.disabled) };
}, id);
check(/Waiting for the server/.test(busy.note) && busy.confirm === 'Working…' && busy.disabled,
  'after Confirm the prompt says it is working and its controls are disabled', 'busy state: ' + JSON.stringify(busy));

await page.evaluate((mid) => { window._hpSubmit(mid); window._hpCancel(mid); }, id);
await page.waitForTimeout(300);
check(posts.length === 1, 'a second Confirm while working sends nothing', `POSTs sent: ${posts.length}`);
check(posts[0] && posts[0].passcode === 'smoke-dash-passcode' && posts[0].idempotency_key === 'k1',
  'the one request carries the passcode and the original body', 'body: ' + JSON.stringify(posts[0]));
check(!!(await page.$(`[data-modal-id="${id}"]`)) && (await page.evaluate(() => window.__hpResult)) === undefined,
  'Cancel while working neither closes the prompt nor reports "nothing was sent"', 'prompt closed or resolved early');

release();
await page.waitForFunction(() => window.__hpResult !== undefined, { timeout: 5000 });
const result = await page.evaluate(() => window.__hpResult);
check(result && result.ok && result.status === 201 && result.body.render.render_id === 'rnd-slow',
  'the caller gets the server answer', 'result: ' + JSON.stringify(result));
check(!(await page.$(`[data-modal-id="${id}"]`)), 'the prompt closes once the server answers', 'prompt still open');

pageErrors.forEach((m) => fail('page error: ' + m));
await browser.close();
if (bad) { console.error(`\nhuman-proof-busy: ${bad} failure(s)`); process.exit(1); }
console.log('\nhuman-proof-busy: all checks passed');
