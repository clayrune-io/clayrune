#!/usr/bin/env node
/**
 * mcViewportHeightSync — soft-keyboard height sync probe.
 *
 * WHY THIS EXISTS
 * ---------------
 * The recurring mobile bug: the soft keyboard closes but the app stays pinned
 * at keyboard height, leaving half the screen dead ("split screen"). It had
 * been patched three times off reasoning alone, each time against a different
 * guess about which signal went wrong, and it kept coming back — because the
 * failure modes are properties of the *device's* viewport reporting, which no
 * amount of reading the code reveals.
 *
 * So they are simulated here instead. `window.visualViewport` is replaced with
 * a fake we drive by hand, which lets each real-world misbehaviour be
 * reproduced deterministically in headless Chromium:
 *
 *   1. clean keyboard show/hide (`resizes-visual`, the Chrome/Safari default)
 *   2. keyboard show/hide where the WHOLE window resizes (`resizes-content` /
 *      Android WebView adjustResize)
 *   3. STALE visualViewport: the keyboard is gone, focus is still on the field,
 *      and vv.height never updates and never fires 'resize' — the down-button
 *      dismiss case that the old vv-only watchdog could not see, because
 *      vv.height stayed equal to what we had allocated
 *   4. a small viewport delta (a collapsing URL bar) must NOT be mistaken for
 *      a keyboard
 *   5. a short vv with nothing focused must never shrink the app
 *
 * Hermetic: a synthetic fixture page + the real static/js/mobile.js. No server,
 * no network, no MC state.
 *
 * RUN   node tools/smoke/mobile-keyboard-viewport.mjs
 * Exit  0 = all scenarios pass; 1 = a regression.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const MOBILE_JS = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'mobile.js'), 'utf8');

const VW = 412, VH = 883;          // a typical Android phone, CSS px
const KB = 380;                    // a typical soft keyboard

const FIXTURE = `<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  html, body { margin: 0; padding: 0; height: 100vh; overflow: hidden; }
  #sheet { height: var(--mc-app-vh, 100dvh); }
  #content { height: 400px; }
</style></head><body>
<div id="sheet"><div id="content">transcript</div><textarea id="composer"></textarea></div>
</body></html>`;

// The fake visual viewport. `height`/`offsetTop` are plain writable fields so a
// scenario can leave them STALE (mutate nothing, fire nothing) — the whole
// point of case 3.
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

let failures = 0;
function check(name, actual, expected, tol = 2) {
  const ok = Math.abs(actual - expected) <= tol;
  if (!ok) failures++;
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}\n        got ${actual}, want ${expected} (±${tol})`);
}

const appVh = page => page.evaluate(() =>
  parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh')) || 0);

// A tap: touchstart + touchend at the same point, on plain content.
const tapContent = page => page.evaluate(() => {
  const el = document.getElementById('content');
  const pt = t => [new Touch({ identifier: 1, target: t, clientX: 50, clientY: 50 })];
  el.dispatchEvent(new TouchEvent('touchstart', { bubbles: true, touches: pt(el), changedTouches: pt(el) }));
  el.dispatchEvent(new TouchEvent('touchend',   { bubbles: true, touches: [],     changedTouches: pt(el) }));
});

const main = async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, hasTouch: true });
  const page = await ctx.newPage();
  await page.addInitScript(INSTALL_FAKE_VV);
  await page.route('**/*', route => {
    const url = route.request().url();
    if (url.endsWith('/fixture.html')) return route.fulfill({ contentType: 'text/html', body: FIXTURE });
    if (url.endsWith('/mobile.js'))    return route.fulfill({ contentType: 'text/javascript', body: MOBILE_JS });
    return route.abort();
  });
  page.on('pageerror', e => { console.log(`FAIL  page threw: ${e.message}`); failures++; });
  await page.goto('http://mc.test/fixture.html');
  await page.addScriptTag({ url: '/mobile.js', type: 'module' });
  await page.waitForFunction(() =>
    getComputedStyle(document.documentElement).getPropertyValue('--mc-app-vh').trim() !== '');

  const layout = await page.evaluate(() => document.documentElement.clientHeight);
  check('idle: full layout viewport', await appVh(page), layout);

  // ── 1. resizes-visual: focus + vv shrinks, then a clean hide ───────────────
  await page.evaluate(kb => {
    document.getElementById('composer').focus();
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, KB);
  await page.waitForTimeout(900);
  check('keyboard up (resizes-visual): app sits above it', await appVh(page), layout - KB);

  await page.evaluate(() => {
    window.__vv.height = window.innerHeight;
    window.__vv.dispatchEvent(new Event('resize'));
  });
  await page.waitForTimeout(900);
  check('keyboard down (resizes-visual): app back to full', await appVh(page), layout);

  // ── 2. STALE vv — the down-button dismiss that kept coming back ────────────
  // Keyboard "up", then the user dismisses it with the down button: focus stays
  // on the field, vv.height stays at its keyboard-open value forever, no event
  // ever fires. Every keyboard-open signal we have is still true, so only the
  // user's next tap can break the deadlock.
  await page.evaluate(kb => {
    document.getElementById('composer').focus();
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, KB);
  await page.waitForTimeout(900);
  check('stale-vv setup: pinned at keyboard height', await appVh(page), layout - KB);

  await page.waitForTimeout(1200);   // both watchdogs get several ticks
  check('stale-vv: watchdogs correctly do NOT fight a live keyboard',
        await appVh(page), layout - KB);

  await tapContent(page);
  await page.waitForTimeout(900);
  check('stale-vv: tap on content recovers full height', await appVh(page), layout);
  check('stale-vv: the tap blurred the field',
        await page.evaluate(() => document.activeElement === document.getElementById('composer') ? 1 : 0), 0, 0);

  // ── 3. resizes-content / adjustResize: the whole window resizes ────────────
  // Nothing to infer here — innerHeight itself shrinks, so the inset is zero
  // and the app simply follows the window.
  await ctx.setDefaultTimeout(5000);
  await page.setViewportSize({ width: VW, height: VH - KB });
  await page.evaluate(() => {
    document.getElementById('composer').focus();
    window.__vv.height = window.innerHeight;
    window.__vv.dispatchEvent(new Event('resize'));
  });
  await page.waitForTimeout(900);
  check('keyboard up (resizes-content): follows the resized window',
        await appVh(page), await page.evaluate(() => document.documentElement.clientHeight));

  await page.setViewportSize({ width: VW, height: VH });
  await page.evaluate(() => {
    window.__vv.height = window.innerHeight;
    window.__vv.dispatchEvent(new Event('resize'));
  });
  await page.waitForTimeout(900);
  check('keyboard down (resizes-content): back to full', await appVh(page), layout);

  // ── 4. a collapsing URL bar is not a keyboard ─────────────────────────────
  await page.evaluate(() => {
    document.getElementById('composer').focus();
    window.__vv.height = window.innerHeight - 56;
    window.__vv.dispatchEvent(new Event('resize'));
  });
  await page.waitForTimeout(600);
  check('56px delta ignored (URL bar, not a keyboard)', await appVh(page), layout);

  // ── 5. short vv with nothing focused can never shrink the app ─────────────
  await page.evaluate(kb => {
    document.getElementById('composer').blur();
    window.__vv.height = window.innerHeight - kb;
    window.__vv.dispatchEvent(new Event('resize'));
  }, KB);
  await page.waitForTimeout(900);
  check('short vv with no focus: app stays full', await appVh(page), layout);

  await browser.close();
  console.log(failures ? `\n${failures} check(s) failed` : '\nall checks passed');
  process.exit(failures ? 1 : 0);
};

main().catch(e => { console.error(e); process.exit(1); });
