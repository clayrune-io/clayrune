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
const ctx = await browser.newContext({ hasTouch: true, viewport: { width: 900, height: 700 } });
const page = await ctx.newPage();
page.on('pageerror', e => { check('no page error', false, e.message); });

await page.route('**/*', route => {
  const p = new URL(route.request().url()).pathname;
  if (p === '/') return route.fulfill({ contentType: 'text/html', body: PAGE });
  if (p === '/static/css/app.css') return route.fulfill({ contentType: 'text/css', body: APP_CSS });
  if (p === '/static/js/mermaid.js') return route.fulfill({ contentType: 'text/javascript', body: MERMAID_JS });
  return route.abort();
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

await browser.close();
if (fails.length) {
  console.error(`\n${fails.length} gesture check(s) failed: ${fails.join(', ')}`);
  process.exit(1);
}
console.log('\nAll viewer gestures OK.');
