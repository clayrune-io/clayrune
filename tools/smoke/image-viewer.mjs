#!/usr/bin/env node
/**
 * Image viewer — window controls + prev/next smoke test (Ron's ask, 2026-09-29).
 *
 * WHY THIS EXISTS
 * ---------------
 * The viewer previously had a single close button on the LEFT of its bar and
 * no way to step through a folder of images. This loads the REAL
 * static/js/mermaid.js + app.css in headless Chromium, opens the viewer on
 * a fake 3-image folder (via intercepted /api/serve-image + /api/serve-image
 * /siblings), and drives the real UI: Next/Prev + arrow keys, Minimize ->
 * chip -> restore, Maximize -> fill viewport, Close -> overlay gone.
 *
 * RUN   node tools/smoke/image-viewer.mjs
 * Exit 0 = every control works; 1 = one of them regressed.
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

// 4x4 PNG — same stand-in used by boot-smoke.mjs; content is irrelevant here,
// only the requested `path` query param (which file) matters.
const PNG_B64 = 'iVBORw0KGgoAAAANSUhEUgAAAAQAAAAECAYAAACp8Z5+AAAAEklEQVR4nGP8z8Dwn4EIwDiqEAAA//8DABjcA0/9b3pPAAAAAElFTkSuQmCC';
const PNG_BYTES = Buffer.from(PNG_B64, 'base64');

const FOLDER = 'C:/fake/desk_mockups';
const FILES = [`${FOLDER}/1.png`, `${FOLDER}/2.png`, `${FOLDER}/3.png`];

const PAGE = `<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="/static/css/app.css"></head>
<body><div id="minimized-tray"></div><script>
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
const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
const page = await ctx.newPage();
page.on('pageerror', e => { check('no page error', false, e.message); });

await page.route('**/*', route => {
  const url = new URL(route.request().url());
  const p = url.pathname;
  if (p === '/') return route.fulfill({ contentType: 'text/html', body: PAGE });
  if (p === '/static/css/app.css') return route.fulfill({ contentType: 'text/css', body: APP_CSS });
  if (p === '/static/js/mermaid.js') return route.fulfill({ contentType: 'text/javascript', body: MERMAID_JS });
  if (p === '/api/serve-image') return route.fulfill({ contentType: 'image/png', body: PNG_BYTES });
  if (p === '/api/serve-image/siblings') {
    const reqPath = url.searchParams.get('path');
    const index = Math.max(0, FILES.indexOf(reqPath));
    return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ files: FILES, index }) });
  }
  return route.abort();
});
await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
await page.waitForFunction(() => typeof window._openImageViewer === 'function', null, { timeout: 10000 });

await page.evaluate((src) => window._openImageViewer(src), '/api/serve-image?path=' + encodeURIComponent(FILES[0]));
await page.waitForSelector('.mermaid-viewer-overlay img');
await page.waitForFunction(() => document.querySelector('.mermaid-viewer-overlay img').complete);
await page.waitForFunction(() => (document.querySelector('.iv-counter') || {}).textContent === '1 / 3', null, { timeout: 5000 });

const counter = () => page.$eval('.iv-counter', el => el.textContent);
const imgSrc = () => page.$eval('.mermaid-viewer-overlay img', el => el.getAttribute('src'));

check('opens showing 1 / 3', (await counter()) === '1 / 3', await counter());

// ── Next twice -> 3rd image, "3 / 3" ──
await page.click('.iv-nav-next');
await page.waitForTimeout(150);
await page.click('.iv-nav-next');
await page.waitForTimeout(150);
check('Next x2 shows 3 / 3', (await counter()) === '3 / 3', await counter());
check('Next x2 loaded the 3rd file', (await imgSrc()).includes(encodeURIComponent(FILES[2])), await imgSrc());

// ── ArrowLeft goes back ──
await page.keyboard.press('ArrowLeft');
await page.waitForTimeout(150);
check('ArrowLeft goes back to 2 / 3', (await counter()) === '2 / 3', await counter());
check('ArrowLeft loaded the 2nd file', (await imgSrc()).includes(encodeURIComponent(FILES[1])), await imgSrc());

// ── Minimize -> chip -> restore keeps the same image ──
await page.click('._iv-min');
await page.waitForTimeout(150);
const minimized = await page.evaluate(() => {
  const ov = document.querySelector('.mermaid-viewer-overlay');
  const chip = document.querySelector('#minimized-tray .minimized-chip');
  return { overlayHidden: ov.style.display === 'none', chipPresent: !!chip };
});
check('Minimize hides the window', minimized.overlayHidden, JSON.stringify(minimized));
check('Minimize creates a restorable chip', minimized.chipPresent, JSON.stringify(minimized));

await page.click('#minimized-tray .minimized-chip');
await page.waitForTimeout(150);
const restored = await page.evaluate(() => {
  const ov = document.querySelector('.mermaid-viewer-overlay');
  const chip = document.querySelector('#minimized-tray .minimized-chip');
  return { overlayVisible: ov.style.display !== 'none', chipGone: !chip };
});
check('Restore brings the window back', restored.overlayVisible, JSON.stringify(restored));
check('Restore removes the chip', restored.chipGone, JSON.stringify(restored));
check('Restore keeps the same image (2 / 3)', (await counter()) === '2 / 3', await counter());

// ── Maximize fills the viewport ──
await page.click('._iv-max');
await page.waitForTimeout(150);
const maxed = await page.evaluate(() => {
  const c = document.querySelector('.mermaid-viewer-content');
  return { w: c.offsetWidth, h: c.offsetHeight, vw: window.innerWidth, vh: window.innerHeight };
});
check('Maximize fills the viewport', maxed.w === maxed.vw && maxed.h === maxed.vh, JSON.stringify(maxed));

// ── Close removes the viewer ──
await page.click('._iv-close');
await page.waitForTimeout(150);
const closed = await page.evaluate(() => !document.querySelector('.mermaid-viewer-overlay'));
check('Close removes the viewer', closed, closed ? 'overlay gone' : 'overlay still present');

await browser.close();
if (fails.length) {
  console.error(`\n${fails.length} check(s) failed: ${fails.join(', ')}`);
  process.exit(1);
}
console.log('\nAll image-viewer nav checks OK.');
