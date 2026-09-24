#!/usr/bin/env node
/**
 * Claydo mobile smoke (2026-09-24 report: Ron's son on the Android app).
 *
 * WHY THIS EXISTS
 * ----------------
 * Three mobile-only Claydo defects, all in static/js/claydo.js:
 *
 *  1. Pressing Enter dismisses the soft keyboard but keeps focus on the
 *     field (down-button dismiss), so nothing told mobile.js's viewport
 *     watchdog the keyboard was gone — the panel sat shrunk. submitClaydo()
 *     never had the blur + window.mcRestoreFullHeight() + frame-yield fix
 *     conversation.js's sendFollowup() got on 2026-09-14 (see
 *     mobile-send-height-restore.mjs) for the exact same bug class.
 *  2. Ron: the thinking dots must appear immediately on send — optimistic,
 *     not gated on the network/CLI cold-start. submitClaydo() already builds
 *     the dots before the fetch() call; this asserts that stays true and
 *     that BOTH the height restore and the dots land within ~1 frame of
 *     Enter, independent of the (held-open, never-resolving) stream request.
 *  3. The header button next to X minimized on mobile, where a minimized
 *     Claydo has no reopen affordance — Ron wants it to mirror the Android
 *     hardware-back behaviour (closeModalById) instead. Desktop keeps
 *     minimize.
 *
 * Hermetic: real index.html + every real static/js/*.js + static/css/*.css
 * served verbatim (same auto-serve technique as mobile-send-height-restore.mjs),
 * a hand-driven fake visualViewport, and /api/guide/stream held open forever
 * so the dots/height assertions can't be passing "by luck" because the fetch
 * happened to resolve fast. No server, no network, no MC state.
 *
 * RUN   node tools/smoke/claydo-mobile.mjs
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
const VW_MOBILE = 412, VH_MOBILE = 883;   // a typical Android phone, CSS px
const KB = 380;                           // a typical soft keyboard

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const INSTALL_FAKE_VV = `
  (() => {
    const t = new EventTarget();
    t.height = window.innerHeight;
    t.width = window.innerWidth;
    t.offsetTop = 0;
    t.scale = 1;
    Object.defineProperty(window, 'visualViewport', { value: t, configurable: true });
    window.__vv = t;
  })();
`;

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

async function routeCommon(page, held) {
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    if (path === '/api/characters') return route.fulfill({ status: 200, contentType: 'application/json', body: '[]' });
    // Held open forever — proves the dots/height restore can't be relying on
    // the network resolving fast.
    if (path === '/api/guide/stream') {
      held.posted = true;
      return new Promise(() => {});
    }
    return route.abort();
  });
}

async function testMobileSendTiming(browser) {
  console.log('\n[1] Mobile: Enter -> full height + dots, both before the (held-open) network call');
  const ctx = await browser.newContext({ viewport: { width: VW_MOBILE, height: VH_MOBILE }, hasTouch: true });
  const page = await ctx.newPage();
  await page.addInitScript(INSTALL_FAKE_VV);
  const held = { posted: false };
  await routeCommon(page, held);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  // Mobile hides #claydo-fab (app.css:7533) and moves the trigger into the
  // bottom tab bar — same selector split as walkthrough.js's spotlight target.
  await page.waitForSelector('#bottom-tab-bar', { timeout: 15000 });
  await page.evaluate(() => openClaydo());
  await page.waitForSelector('#claydo-input', { timeout: 5000 });

  const layout = await page.evaluate(() => document.documentElement.clientHeight);
  const appVh = () => page.evaluate(() => parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0);

  // Open the keyboard (focus + shrink vv), type a message.
  await page.evaluate((kb) => {
    const ta = document.getElementById('claydo-input');
    ta.focus();
    ta.value = 'how do I start a hivemind?';
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, KB);
  await page.waitForTimeout(900);
  const shrunk = await appVh();
  (Math.abs(shrunk - (layout - KB)) <= 4)
    ? ok(`keyboard-open baseline: Claydo shrank to keyboard height (${shrunk} ~= ${layout - KB})`)
    : fail(`keyboard-open baseline never shrank (got ${shrunk}, want ~${layout - KB})`);

  // Press Enter (down-button dismiss model: keyboard closes, focus stays put
  // until submitClaydo's own blur runs) — same trigger as the textarea's
  // real onkeydown handler.
  const t0 = await page.evaluate(() => performance.now());
  await page.evaluate(() => {
    const ta = document.getElementById('claydo-input');
    ta.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
  });

  // Poll every frame for the dots + full height, both independent of the
  // held-open /api/guide/stream request.
  const result = await page.evaluate(() => new Promise((res) => {
    const t0 = performance.now();
    let dotsAt = null, heightAt = null;
    const layout = document.documentElement.clientHeight;
    function tick() {
      const vh = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0;
      if (dotsAt === null && document.querySelector('.claydo-thinking')) dotsAt = performance.now() - t0;
      if (heightAt === null && Math.abs(vh - layout) <= 4) heightAt = performance.now() - t0;
      if ((dotsAt !== null && heightAt !== null) || performance.now() - t0 > 3000) {
        return res({ dotsAt, heightAt });
      }
      requestAnimationFrame(tick);
    }
    requestAnimationFrame(tick);
  }));

  (result.dotsAt !== null && result.dotsAt < 200)
    ? ok(`thinking dots appeared ${result.dotsAt.toFixed(1)}ms after Enter (optimistic, no network wait)`)
    : fail(`thinking dots took ${result.dotsAt === null ? 'never' : result.dotsAt.toFixed(1) + 'ms'} to appear (want < 200ms, network is held open)`);
  (result.heightAt !== null && result.heightAt < 200)
    ? ok(`full height restored ${result.heightAt.toFixed(1)}ms after Enter (want < 200ms)`)
    : fail(`full height took ${result.heightAt === null ? 'never' : result.heightAt.toFixed(1) + 'ms'} to restore (want < 200ms)`);

  held.posted
    ? ok('the /api/guide/stream POST fired (submit path ran to completion, just held open by the harness)')
    : fail('/api/guide/stream was never called — submitClaydo did not run');

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

async function testBackButton(browser, mobile) {
  const label = mobile ? 'mobile' : 'desktop';
  console.log(`\n[${mobile ? 2 : 3}] ${label}: Claydo header second button`);
  const vp = mobile ? { width: VW_MOBILE, height: VH_MOBILE } : { width: 1280, height: 800 };
  const ctx = await browser.newContext({ viewport: vp, hasTouch: mobile });
  const page = await ctx.newPage();
  if (mobile) await page.addInitScript(INSTALL_FAKE_VV);
  const held = { posted: false };
  await routeCommon(page, held);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));

  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector(mobile ? '#bottom-tab-bar' : '#claydo-fab', { timeout: 15000 });
  await page.evaluate(() => openClaydo());
  await page.waitForSelector('#claydo-input', { timeout: 5000 });

  const btn = await page.evaluate(() => {
    const b = document.querySelector('#modal-layer .modal-header .modal-minimize');
    return b ? { title: b.title, text: b.textContent.trim() } : null;
  });

  if (mobile) {
    (btn && btn.title === 'Back' && btn.text === '←')
      ? ok(`mobile header shows a Back button (title="${btn?.title}", icon="${btn?.text}")`)
      : fail(`mobile header did not show Back (got ${JSON.stringify(btn)})`);

    await page.evaluate(() => document.querySelector('#modal-layer .modal-header .modal-minimize').click());
    const stillOpen = await page.evaluate(() => typeof openModals !== 'undefined' && openModals.has('__claydo'));
    (!stillOpen)
      ? ok('clicking Back closed Claydo (same effect as Android hardware back)')
      : fail('clicking Back did not close Claydo — openModals still has __claydo');
  } else {
    (btn && btn.title === 'Minimize' && btn.text === '―')
      ? ok(`desktop header still shows Minimize (title="${btn?.title}", icon="${btn?.text}")`)
      : fail(`desktop header lost Minimize (got ${JSON.stringify(btn)})`);

    await page.evaluate(() => document.querySelector('#modal-layer .modal-header .modal-minimize').click());
    const entry = await page.evaluate(() => {
      const e = typeof openModals !== 'undefined' && openModals.get('__claydo');
      return e ? { minimized: !!e.minimized } : null;
    });
    (entry && entry.minimized)
      ? ok('clicking Minimize on desktop still minimizes (does not close)')
      : fail(`clicking the desktop button did not minimize (got ${JSON.stringify(entry)})`);
  }

  const uncaught = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  uncaught.forEach((e) => fail('uncaught page error: ' + e));

  await ctx.close();
}

let browser;
let exitCode = 1;
try {
  browser = await chromium.launch();
  await testMobileSendTiming(browser);
  await testBackButton(browser, true);
  await testBackButton(browser, false);
  exitCode = bad ? 1 : 0;
} catch (e) {
  console.error(e);
  exitCode = 1;
} finally {
  if (browser) await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(exitCode);
