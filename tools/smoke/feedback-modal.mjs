#!/usr/bin/env node
/**
 * Feedback modal smoke (backlog f638e8d9, MC-904 1/3).
 *
 * REWORK (2026-09-29): Send no longer calls the server. It builds a
 * mailto:hello@clayrune.io link from exactly the textarea text and hands it
 * to the OS mail client — /api/feedback (POST) is gone; only the read-only
 * /api/feedback/context (version/OS for the prefill) remains.
 *
 * Hermetic: real index.html + every real static/js/*.js + static/css/*.css
 * served verbatim (same technique as claydo-mobile.mjs), /api/feedback/context
 * stubbed at the network layer. The client sets `window.location.href` to
 * the mailto: link (static/js/feedback-modal.js) — Chromium still emits a
 * Network `request` for it before failing to find a handler (confirmed:
 * neither `page.route` nor a target="_blank" popup sees it, but
 * `page.waitForRequest` does), which this test reads for the exact href and
 * lets fail. No running server, no vault, no mailer, no real mail client.
 *
 * navigator.clipboard needs a secure context, so this file uses a localhost
 * origin (Chromium's localhost secure-context exception) rather than the
 * arbitrary mc.smoke.test host most other smoke files use — precedent:
 * tools/smoke/browser-paste.mjs, tools/smoke/browser-pane-tabs-dialog.mjs.
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
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://localhost:9821'; // localhost secure-context exception, for navigator.clipboard
const VW_MOBILE = 390, VH_MOBILE = 844; // brief's 390px phone width

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

function parseMailto(url) {
  // url is "mailto:hello@clayrune.io?subject=...&body=..." — decode by hand,
  // URL()'s mailto support doesn't parse the query the way we need.
  const [addrPart, query] = url.slice('mailto:'.length).split('?');
  const params = new URLSearchParams(query || '');
  return { address: addrPart, subject: params.get('subject'), body: params.get('body') };
}

async function routeCommon(page) {
  await page.route('**/*', (route) => {
    const req = route.request();
    const url = req.url();
    const path = new URL(url).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/feedback/context') {
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ version: 'v9.9.9-smoke', os: 'SmokeOS 1.0' }) });
    }
    if (path === '/api/feedback') {
      // The POST send path is gone — a route still being hit here would be
      // the regression this test exists to catch.
      return route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
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

// Clicks #fb-send-btn and captures the mailto: href the page tried to
// navigate to. The would-be navigation never actually leaves the page (no
// handler for the protocol in headless Chromium), so page.url() stays put —
// waitForRequest is what observes the attempt.
async function clickSendAndCaptureMailto(ctx, page) {
  const reqPromise = page.waitForRequest((r) => r.url().startsWith('mailto:'), { timeout: 3000 }).catch(() => null);
  await page.click('#fb-send-btn');
  const req = await reqPromise;
  return req ? req.url() : null;
}

// Grant clipboard-write in this context so navigator.clipboard.writeText
// doesn't reject with a permission error in headless Chromium.
async function grantClipboard(ctx) {
  await ctx.grantPermissions(['clipboard-write', 'clipboard-read'], { origin: ORIGIN });
}

async function testDesktop(browser) {
  console.log('\n[1] Desktop: sidebar footer entry -> modal -> prefill -> send -> mailto');
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  await grantClipboard(ctx);
  const page = await ctx.newPage();
  await routeCommon(page);
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

  // No reply-to field left — the user's own From address is the reply-to
  // in a mailto (brief item 1).
  const replyTo = await page.$('#fb-reply-to');
  replyTo ? fail('reply-to field still present — should have been removed') : ok('reply-to field is gone (mailto From IS the reply-to)');

  // Editable: user can clear it and type their own message (privacy constraint).
  const testMessage = 'a completely different user-typed message';
  await page.fill('#fb-message', testMessage);
  const afterEdit = await page.$eval('#fb-message', (el) => el.value);
  (afterEdit === testMessage)
    ? ok('textarea is editable — user can delete the prefilled line entirely')
    : fail(`textarea did not accept the edit (got ${JSON.stringify(afterEdit)})`);

  const href = await clickSendAndCaptureMailto(ctx, page);
  if (href) {
    const parsed = parseMailto(href);
    (parsed.address === 'hello@clayrune.io')
      ? ok('mailto address is hello@clayrune.io')
      : fail(`mailto address wrong (got ${parsed.address})`);
    (parsed.subject === 'Clayrune feedback')
      ? ok('mailto subject is "Clayrune feedback"')
      : fail(`mailto subject wrong (got ${JSON.stringify(parsed.subject)})`);
    (parsed.body === testMessage)
      ? ok('decoded mailto body equals exactly the textarea text, no silent extras')
      : fail(`mailto body did not match textarea (got ${JSON.stringify(parsed.body)})`);
  } else {
    fail('Send never opened a mailto: link');
  }

  await page.waitForFunction(() => (document.getElementById('fb-status') || {}).textContent.includes('mail app should open'), { timeout: 3000 })
    .then(() => ok('status shows "Your mail app should open..." with the nothing-sent disclosure'))
    .catch(() => fail('mail-app status message never appeared after send'));

  const copyBtn = await page.$('#fb-copy-btn');
  const copyVisible = copyBtn ? await copyBtn.isVisible() : false;
  copyVisible ? ok('Copy message button appears as the fallback after send') : fail('Copy message button did not appear after send');

  await page.click('#fb-copy-btn');
  const clipboardText = await page.evaluate(() => navigator.clipboard.readText());
  (clipboardText === testMessage)
    ? ok('Copy message copies exactly the textarea text to the clipboard')
    : fail(`clipboard did not match textarea (got ${JSON.stringify(clipboardText)})`);

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

async function testLongMessage(browser) {
  console.log('\n[2] Long message (>1800 char encoded mailto): no silent truncation');
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  await routeCommon(page);

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.click('.sidebar-footer [data-nav="feedback"]');
  await openAndInspectModal(page);

  const longMessage = 'x'.repeat(2500);
  await page.fill('#fb-message', longMessage);
  const href = await clickSendAndCaptureMailto(ctx, page);

  if (href) {
    const parsed = parseMailto(href);
    (parsed.body === null)
      ? ok('over-long message opens mail with no body param (address + subject only), not a truncated body')
      : fail(`expected no body param on the long-message path, got ${JSON.stringify(parsed.body)}`);
    (parsed.address === 'hello@clayrune.io' && parsed.subject === 'Clayrune feedback')
      ? ok('long-message mailto still carries the correct address + subject')
      : fail(`long-message mailto address/subject wrong (got ${JSON.stringify(parsed)})`);
  } else {
    fail('Send never opened a mailto: link on the long-message path');
  }

  const status = await page.$eval('#fb-status', (el) => el.textContent);
  /too long/i.test(status)
    ? ok(`status names the message as too long for a mail link ("${status}")`)
    : fail(`status did not explain the too-long condition (got ${JSON.stringify(status)})`);

  const copyBtn = await page.$('#fb-copy-btn');
  const copyVisible = copyBtn ? await copyBtn.isVisible() : false;
  copyVisible ? ok('Copy message button is kept (not removed) on the long-message path') : fail('Copy message button missing on the long-message path');

  await ctx.close();
}

async function testMobile(browser) {
  console.log('\n[3] Mobile (390px): drawer footer entry -> modal -> prefill -> send -> mailto');
  const ctx = await browser.newContext({ viewport: { width: VW_MOBILE, height: VH_MOBILE }, hasTouch: true });
  const page = await ctx.newPage();
  await routeCommon(page);
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

  const mobileMessage = 'mobile bug report';
  await page.fill('#fb-message', mobileMessage);
  const href = await clickSendAndCaptureMailto(ctx, page);

  if (href) {
    const parsed = parseMailto(href);
    (parsed.body === mobileMessage)
      ? ok('mobile mailto body equals exactly the textarea text')
      : fail(`mobile mailto body did not match (got ${JSON.stringify(parsed.body)})`);
  } else {
    fail('Send never opened a mailto: link on mobile');
  }

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();
  await testDesktop(browser);
  await testLongMessage(browser);
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
