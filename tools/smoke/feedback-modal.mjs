#!/usr/bin/env node
/**
 * Feedback modal smoke (backlog f638e8d9, MC-904 1/3).
 *
 * Hermetic: real index.html + every real static/js/*.js + static/css/*.css
 * served verbatim (same technique as claydo-mobile.mjs), /api/feedback and
 * /api/feedback/context stubbed at the network layer — no real server, no
 * vault, no mailer.
 *
 * Covers the privacy constraint (backlog item (c)): the prefilled version/OS
 * line must be VISIBLE and EDITABLE text inside the textarea, never a hidden
 * field appended by the client or the server.
 *
 * RUN   node tools/smoke/feedback-modal.mjs
 * Exit  0 = all checks pass; 1 = a regression.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const VW_MOBILE = 390, VH_MOBILE = 844; // brief's 390px phone width

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function routeCommon(page, sent) {
  await page.route('**/*', (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/feedback/context') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: 'v9.9.9-smoke', os: 'SmokeOS 1.0' }) });
    }
    if (path === '/api/feedback' && req.method() === 'POST') {
      sent.body = JSON.parse(req.postData() || '{}');
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ ok: true }) });
    }
    return route.abort();
  });
}

async function openAndInspectModal(page) {
  await page.waitForSelector('[data-modal-id="__feedback"]', { timeout: 5000 });
  const ta = await page.$('#fb-message');
  const prefill = ta ? await ta.inputValue() : null;
  return prefill;
}

async function testDesktop(browser) {
  console.log('\n[1] Desktop: sidebar footer entry -> modal -> prefill -> send');
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  const sent = {};
  await routeCommon(page, sent);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  const entry = await page.waitForSelector('.sidebar-footer [data-nav="feedback"]', { timeout: 15000 });
  entry ? ok('sidebar footer "Send feedback" entry is present and reachable') : fail('sidebar footer entry not found');

  await entry.click();
  const prefill = await openAndInspectModal(page);
  (prefill && prefill.startsWith('Clayrune v9.9.9-smoke on SmokeOS 1.0'))
    ? ok(`prefilled line is visible in the textarea ("${prefill.split('\\n')[0]}")`)
    : fail(`prefill missing or wrong (got ${JSON.stringify(prefill)})`);

  // Editable: user can clear it and type their own message (privacy constraint).
  await page.fill('#fb-message', 'a completely different user-typed message');
  const afterEdit = await page.$eval('#fb-message', (el) => el.value);
  (afterEdit === 'a completely different user-typed message')
    ? ok('textarea is editable — user can delete the prefilled line entirely')
    : fail(`textarea did not accept the edit (got ${JSON.stringify(afterEdit)})`);

  // Honeypot is deliberately off-screen (left:-9999px), not display:none —
  // some bots skip fields hidden that obvious way. Playwright's isVisible()
  // only checks display/visibility, not screen position, so assert the
  // bounding box sits off-canvas instead.
  const hpBox = await page.$eval('#fb-topic', (el) => el.getBoundingClientRect().right);
  (hpBox < 0)
    ? ok(`honeypot field (#fb-topic) exists but sits off-screen (right edge at ${hpBox}px)`)
    : fail(`honeypot is on-screen and could be seen by a human (right edge at ${hpBox}px)`);

  await page.fill('#fb-reply-to', 'ron@example.com');
  await page.click('#fb-send-btn');
  await page.waitForFunction(() => (document.getElementById('fb-status') || {}).textContent === 'Sent. Thank you.', { timeout: 3000 })
    .then(() => ok('send shows the success message ("Sent. Thank you.")'))
    .catch(() => fail('success message never appeared after send'));

  (sent.body && sent.body.message === 'a completely different user-typed message' && sent.body.reply_to === 'ron@example.com' && sent.body.hp_topic === '')
    ? ok('payload sent equals exactly the visible textarea + email fields, no silent extras')
    : fail(`payload did not match visible fields (got ${JSON.stringify(sent.body)})`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

async function testMobile(browser) {
  console.log('\n[2] Mobile (390px): drawer footer entry -> modal -> prefill -> send');
  const ctx = await browser.newContext({ viewport: { width: VW_MOBILE, height: VH_MOBILE }, hasTouch: true });
  const page = await ctx.newPage();
  const sent = {};
  await routeCommon(page, sent);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#mc-hamburger-btn', { timeout: 15000 });
  await page.click('#mc-hamburger-btn');
  const entry = await page.waitForSelector('.mobile-drawer-footer .mobile-drawer-item', { timeout: 5000 });
  entry ? ok('mobile drawer footer "Send feedback" entry is present and reachable') : fail('mobile drawer footer entry not found');

  await entry.click();
  const prefill = await openAndInspectModal(page);
  (prefill && prefill.startsWith('Clayrune v9.9.9-smoke on SmokeOS 1.0'))
    ? ok(`prefilled line is visible in the textarea on mobile ("${prefill.split('\\n')[0]}")`)
    : fail(`mobile prefill missing or wrong (got ${JSON.stringify(prefill)})`);

  const drawerClosed = await page.evaluate(() => document.getElementById('mobile-drawer').getAttribute('aria-hidden') === 'true');
  drawerClosed ? ok('opening the modal closed the mobile drawer behind it') : fail('mobile drawer stayed open behind the modal');

  await page.fill('#fb-message', 'mobile bug report');
  await page.click('#fb-send-btn');
  await page.waitForFunction(() => (document.getElementById('fb-status') || {}).textContent === 'Sent. Thank you.', { timeout: 3000 })
    .then(() => ok('mobile send shows the success message'))
    .catch(() => fail('mobile success message never appeared after send'));

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();
  await testDesktop(browser);
  await testMobile(browser);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);
