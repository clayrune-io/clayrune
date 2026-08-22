#!/usr/bin/env node
/**
 * Image/diagram viewer — gesture smoke test.
 *
 * WHY THIS EXISTS
 * ---------------
 * The viewer shipped zoomable ONLY from its toolbar: the wheel handler bailed
 * unless Ctrl/Cmd was held, and there was no touch handling at all — so on a
 * phone pinching did nothing and on a desktop the wheel did nothing. Neither is
 * visible to `node --check` or to the boot smoke test, because the viewer boots
 * fine; it just ignores you.
 *
 * This loads the REAL static/js/mermaid.js + app.css in headless Chromium,
 * opens the image viewer on a generated PNG, and asserts each input path
 * actually changes the zoom: wheel, two-finger pinch, double-tap, drag-pan,
 * toolbar, keyboard. It also asserts the anchoring property — the pixel under
 * the pointer must not run away while you zoom.
 *
 * RUN   node tools/smoke/image-viewer-gestures.mjs
 * Exit 0 = every gesture works; 1 = one of them regressed.
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(__dirname, '..', '..');
const APP_CSS = readFileSync(resolve(ROOT, 'static', 'css', 'app.css'), 'utf8');
const MERMAID_JS = readFileSync(resolve(ROOT, 'static', 'js', 'mermaid.js'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/css/app.css"></head>
<body><script>
  // Globals mermaid.js resolves at call time (it is not an import graph).
  window.esc = s => String(s);
  window.API_BASE = '';
</script>
<script type="module" src="/static/js/mermaid.js"></script></body></html>`;

const fails = [];
const check = (name, ok, detail) => {
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
  if (!ok) fails.push(name);
};

const browser = await chromium.launch();
// 1280 wide on purpose: the viewer keeps its full-screen modal treatment at or
// below the app's 960px mobile breakpoint, so a narrower viewport would test
// the mobile path while claiming to test the desktop window.
const ctx = await browser.newContext({ hasTouch: true, viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
page.on('pageerror', e => { check('no page error', false, e.message); });

await page.route('**/*', route => {
  const p = new URL(route.request().url()).pathname;
  if (p === '/') return route.fulfill({ contentType: 'text/html', body: PAGE });
  if (p === '/static/css/app.css') return route.fulfill({ contentType: 'text/css', body: APP_CSS });
  if (p === '/static/js/mermaid.js') return route.fulfill({ contentType: 'text/javascript', body: MERMAID_JS });
  return route.abort();
});
await page.addInitScript(() => {
  // Balance-sheet for document-level listeners: the viewers hang drag/pan
  // handlers on `document` (a drag has to survive the cursor leaving the
  // window), so closing one by removing its overlay does NOT unbind them.
  window.__lsn = {};
  const add = document.addEventListener.bind(document);
  const rm = document.removeEventListener.bind(document);
  document.addEventListener = (t, f, o) => { window.__lsn[t] = (window.__lsn[t] || 0) + 1; return add(t, f, o); };
  document.removeEventListener = (t, f, o) => { window.__lsn[t] = (window.__lsn[t] || 0) - 1; return rm(t, f, o); };
});
await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => typeof window._openImageViewer === 'function', null, { timeout: 10000 });

// A 500x500 PNG, generated in-page so the harness stays hermetic. It overflows
// the canvas once zoomed, giving the pan/anchor assertions room to work.
await page.evaluate(() => {
  const c = document.createElement('canvas');
  c.width = c.height = 500;
  const x = c.getContext('2d');
  for (let i = 0; i < 25; i++) {
    x.fillStyle = i % 2 ? '#333' : '#ccc';
    x.fillRect((i % 5) * 100, Math.floor(i / 5) * 100, 100, 100);
  }
  window._openImageViewer(c.toDataURL('image/png'));
});
await page.waitForSelector('.mermaid-viewer-overlay img');
await page.waitForFunction(() => document.querySelector('.mermaid-viewer-overlay img').complete);

const zoom = () => page.$eval('.mermaid-viewer-zoom-label', el => parseInt(el.textContent, 10));
const reset = async () => { await page.click('._iv-zr'); await page.waitForTimeout(200); };
const box = await page.$eval('.mermaid-viewer-scroll', el => {
  const r = el.getBoundingClientRect();
  return { cx: r.left + r.width / 2, cy: r.top + r.height / 2, left: r.left, top: r.top };
});

check('opens at 100%', (await zoom()) === 100, (await zoom()) + '%');

// ── 1. Plain wheel (no modifier) zooms ──
await page.mouse.move(box.cx, box.cy);
await page.mouse.wheel(0, -400);
await page.waitForTimeout(150);
const wheelIn = await zoom();
check('wheel up zooms IN without Ctrl', wheelIn > 100, wheelIn + '%');
await page.mouse.wheel(0, 800);
await page.waitForTimeout(150);
const wheelOut = await zoom();
check('wheel down zooms OUT', wheelOut < wheelIn, `${wheelIn}% -> ${wheelOut}%`);

// ── 2. Anchoring: the pixel under the cursor stays under the cursor ──
await reset();
const PX = box.left + 120, PY = box.top + 120;
const anchor = await page.evaluate(({ x, y }) => {
  const r = document.querySelector('.mermaid-viewer-svg').getBoundingClientRect();
  return { ux: x - r.left, uy: y - r.top };          // scale is 1 right after reset
}, { x: PX, y: PY });
await page.mouse.move(PX, PY);
await page.mouse.wheel(0, -500);
await page.waitForTimeout(200);
const drift = await page.evaluate(({ ux, uy, x, y }) => {
  const wrap = document.querySelector('.mermaid-viewer-svg');
  const s = parseFloat((wrap.style.transform.match(/scale\(([\d.]+)\)/) || [0, 1])[1]);
  const r = wrap.getBoundingClientRect();
  return Math.hypot((r.left + ux * s) - x, (r.top + uy * s) - y);
}, { ...anchor, x: PX, y: PY });
check('zoom is anchored at the cursor', drift < 4, drift.toFixed(1) + 'px drift');

// ── 3. Two-finger pinch ──
await reset();
const pinched = await page.evaluate(({ cx, cy }) => {
  const el = document.querySelector('.mermaid-viewer-scroll');
  const fire = (type, pts) => {
    const touches = pts.map((p, i) => new Touch({ identifier: i, target: el, clientX: p[0], clientY: p[1] }));
    el.dispatchEvent(new TouchEvent(type, {
      touches, targetTouches: touches, changedTouches: touches, bubbles: true, cancelable: true,
    }));
  };
  fire('touchstart', [[cx - 50, cy], [cx + 50, cy]]);
  fire('touchmove', [[cx - 150, cy], [cx + 150, cy]]);
  fire('touchend', []);
  return parseInt(document.querySelector('.mermaid-viewer-zoom-label').textContent, 10);
}, box);
check('two-finger spread zooms in', pinched > 250, `${pinched}% (expected ~300%)`);

// ── 4. Double-tap toggles ──
await reset();
const tapped = await page.evaluate(({ cx, cy }) => {
  const el = document.querySelector('.mermaid-viewer-scroll');
  const tap = () => {
    const t = [new Touch({ identifier: 0, target: el, clientX: cx, clientY: cy })];
    el.dispatchEvent(new TouchEvent('touchend', {
      touches: [], targetTouches: [], changedTouches: t, bubbles: true, cancelable: true,
    }));
  };
  tap(); tap();
  return parseInt(document.querySelector('.mermaid-viewer-zoom-label').textContent, 10);
}, box);
check('double-tap zooms in', tapped > 100, tapped + '%');

// ── 5. Drag pans once the picture overflows ──
await page.click('._iv-zi');
await page.click('._iv-zi');
await page.waitForTimeout(250);
const panned = await page.evaluate(({ cx, cy }) => {
  const el = document.querySelector('.mermaid-viewer-scroll');
  el.scrollLeft = 60; el.scrollTop = 60;
  const before = el.scrollLeft;
  el.dispatchEvent(new MouseEvent('mousedown', { clientX: cx, clientY: cy, button: 0, bubbles: true, cancelable: true }));
  document.dispatchEvent(new MouseEvent('mousemove', { clientX: cx - 40, clientY: cy - 40, bubbles: true }));
  document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  return { before, after: el.scrollLeft };
}, box);
check('drag pans a zoomed picture', panned.after > panned.before, `scrollLeft ${panned.before} -> ${panned.after}`);

// ── 6. Toolbar + keyboard still work ──
await reset();
check('reset button returns to 100%', (await zoom()) === 100, (await zoom()) + '%');
await page.click('._iv-zi');
await page.waitForTimeout(200);
check('toolbar + zooms in', (await zoom()) === 125, (await zoom()) + '%');
await page.keyboard.press('0');
await page.waitForTimeout(200);
check('key 0 resets', (await zoom()) === 100, (await zoom()) + '%');


// ── 7. It is a WINDOW, not a screen-grabbing modal ──
const winMode = await page.evaluate(() => {
  const ov = document.querySelector('.mermaid-viewer-overlay');
  const c = ov.querySelector('.mermaid-viewer-content');
  const cs = getComputedStyle(ov);
  return {
    classed: ov.classList.contains('iv-windowed'),
    bg: cs.backgroundColor,
    pe: cs.pointerEvents,
    contentPe: getComputedStyle(c).pointerEvents,
    w: c.offsetWidth, h: c.offsetHeight,
    vw: window.innerWidth, vh: window.innerHeight,
  };
});
check('opens in window mode', winMode.classed, 'iv-windowed');
check('no screen-covering backdrop', /rgba\(0, 0, 0, 0\)|transparent/.test(winMode.bg), winMode.bg);
check('clicks pass through to the app behind', winMode.pe === 'none' && winMode.contentPe === 'auto',
  `overlay ${winMode.pe} / content ${winMode.contentPe}`);
check('window is fitted, not full-screen',
  winMode.w < winMode.vw * 0.9 && winMode.h <= winMode.vh * 0.85,
  `${winMode.w}x${winMode.h} in ${winMode.vw}x${winMode.vh}`);

// The app behind really is reachable: a real click lands on the page, not the overlay.
const behind = await page.evaluate(() => {
  const ov = document.querySelector('.mermaid-viewer-overlay');
  const r = ov.querySelector('.mermaid-viewer-content').getBoundingClientRect();
  // A point inside the overlay's box but outside the window itself.
  const el = document.elementFromPoint(Math.max(2, r.left / 2), 4);
  return el ? el.tagName + '.' + (el.className || '') : 'null';
});
check('point beside the window is not the overlay', !behind.includes('mermaid-viewer'), behind);

// ── 8. Drag by the toolbar moves it ──
const moved = await page.evaluate(() => {
  const c = document.querySelector('.mermaid-viewer-content');
  const tb = c.querySelector('.mermaid-viewer-toolbar');
  const r0 = c.getBoundingClientRect();
  const x = r0.left + r0.width / 2, y = r0.top + 8;
  tb.dispatchEvent(new MouseEvent('mousedown', { clientX: x, clientY: y, button: 0, bubbles: true, cancelable: true }));
  document.dispatchEvent(new MouseEvent('mousemove', { clientX: x + 70, clientY: y + 50, bubbles: true }));
  document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
  const r1 = c.getBoundingClientRect();
  return { dx: Math.round(r1.left - r0.left), dy: Math.round(r1.top - r0.top), pos: c.style.position };
});
// +/-1px: pinning rounds the pre-drag left/top to whole pixels.
check('toolbar drag moves the window',
  Math.abs(moved.dx - 70) <= 2 && Math.abs(moved.dy - 50) <= 2 && moved.pos === 'fixed',
  `moved ${moved.dx},${moved.dy} (${moved.pos})`);

// Buttons inside the toolbar must still click, not start a drag.
await page.click('._iv-zi');
await page.waitForTimeout(200);
check('toolbar buttons still work after windowing', (await zoom()) === 125, (await zoom()) + '%');

// ── 9. A tiny picture opens tiny ──
await page.click('._iv-close');
await page.waitForTimeout(100);
await page.evaluate(() => {
  const c = document.createElement('canvas');
  c.width = 120; c.height = 80;
  const x = c.getContext('2d'); x.fillStyle = '#4a7'; x.fillRect(0, 0, 120, 80);
  window._openImageViewer(c.toDataURL('image/png'));
});
await page.waitForSelector('.mermaid-viewer-overlay img');
await page.waitForFunction(() => document.querySelector('.mermaid-viewer-overlay img').complete);
await page.waitForTimeout(200);
const small = await page.evaluate(() => {
  const c = document.querySelector('.mermaid-viewer-content');
  return { w: c.offsetWidth, h: c.offsetHeight };
});
check('a thumbnail opens at the CSS min size, not full-screen',
  small.w <= 340 && small.h <= 240, `${small.w}x${small.h}`);

// ── 10. Closing unbinds every document listener it added ──
await page.click('._iv-close');
await page.waitForTimeout(150);
const leaked = await page.evaluate(() => {
  const l = window.__lsn || {};
  return ['mousemove', 'mouseup', 'touchmove', 'touchend', 'keydown']
    .filter(t => (l[t] || 0) !== 0)
    .map(t => `${t}:${l[t]}`);
});
check('closing leaks no document listeners', leaked.length === 0, leaked.join(', ') || 'balanced');

await browser.close();
if (fails.length) {
  console.error(`\n${fails.length} gesture check(s) failed: ${fails.join(', ')}`);
  process.exit(1);
}
console.log('\nAll viewer gestures OK.');
