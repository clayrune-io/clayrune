#!/usr/bin/env node
// Fit mode must follow the frame: drag the resize corner, Maximize/Restore, and
// a browser window resize each leave the picture inside the stage and re-fit it.
// A manual zoom must survive the same resizes.
import assert from 'node:assert/strict';
import { mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = resolve(ROOT, '_scratch/iv-resize');
mkdirSync(OUT, { recursive: true });
const assets = loadStaticJsCss(ROOT);
const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/css/app.css"><link rel="stylesheet" href="/static/css/image-viewer.css"></head>
<body style="margin:0;background:#e9e6de"><div id="minimized-tray"></div>
<script type="module" src="/static/js/image-viewer.js"></script></body></html>`;
let checks = 0;
function check(condition, label) { assert.ok(condition, label); checks++; console.log('PASS ' + label); }
const browser = await chromium.launch();
try {
  const page = await (await browser.newContext({ viewport: { width: 1600, height: 1000 } })).newPage();
  page.on('pageerror', e => console.error('PAGE ERROR', e.message));
  let png = '';
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (assets[url.pathname]) { const [contentType, body] = assets[url.pathname]; return route.fulfill({ contentType, body }); }
    if (url.pathname === '/api/serve-image') return route.fulfill({ contentType: 'image/png', body: Buffer.from(png, 'base64') });
    return route.abort();
  });
  await page.goto('https://image.smoke.test');
  await page.waitForFunction(() => window._openImageViewer);
  // Tall page, like the resume in the report (1190 x 1540).
  png = await page.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 1190; c.height = 1540;
    const x = c.getContext('2d'); x.fillStyle = '#fff'; x.fillRect(0, 0, 1190, 1540);
    x.fillStyle = '#c33'; x.fillRect(0, 0, 1190, 60); x.fillRect(0, 1480, 1190, 60);
    x.fillStyle = '#336'; x.font = '90px sans-serif'; x.fillText('TOP', 40, 200); x.fillText('BOTTOM', 40, 1440);
    x.fillStyle = '#bbb'; for (let y = 260; y < 1380; y += 70) x.fillRect(40, y, 1110, 22);
    x.strokeStyle = '#336'; x.lineWidth = 8; x.strokeRect(4, 4, 1182, 1532);
    return c.toDataURL('image/png').split(',')[1];
  });
  const open = async () => {
    await page.evaluate(() => window._openImageViewer('/api/serve-image?path=a.png'));
    await page.waitForFunction(() => !document.querySelector('.iv-image').hidden);
  };
  const state = () => page.evaluate(() => {
    const s = document.querySelector('.iv-stage').getBoundingClientRect(), i = document.querySelector('.iv-image').getBoundingClientRect();
    return { s: { l: s.left, t: s.top, r: s.right, b: s.bottom, w: s.width, h: s.height },
      i: { l: i.left, t: i.top, r: i.right, b: i.bottom, w: i.width, h: i.height },
      zoom: document.querySelector('[data-iv=zoom] [value=current]').textContent,
      fitPressed: document.querySelector('[data-iv=fit]').getAttribute('aria-pressed') };
  });
  const inside = ({ s, i }) => i.l >= s.l - 1 && i.r <= s.r + 1 && i.t >= s.t - 1 && i.b <= s.b + 1;
  const report = (tag, st) => console.log(tag, 'stage', Math.round(st.s.w) + 'x' + Math.round(st.s.h), 'img', Math.round(st.i.w) + 'x' + Math.round(st.i.h), 'zoom', st.zoom, 'inside', inside(st));
  const shot = name => page.screenshot({ path: resolve(OUT, `${name}.png`) });
  const grip = async () => { const b = await page.locator('.image-viewer').boundingBox(); return { x: b.x + b.width - 6, y: b.y + b.height - 6 }; };
  const dragCorner = async (dx, dy) => {
    const g = await grip();
    await page.mouse.move(g.x, g.y); await page.mouse.down();
    await page.mouse.move(g.x + dx / 2, g.y + dy / 2, { steps: 5 }); await page.mouse.move(g.x + dx, g.y + dy, { steps: 5 });
    await page.mouse.up(); await page.waitForTimeout(150);
  };

  // Route 1: drag the native resize corner, smaller then larger.
  await open(); await page.waitForTimeout(100);
  let st = await state(); report('open      ', st); await shot('large-before');
  check(inside(st) && st.fitPressed === 'true', 'opens fitted inside the stage');
  const openZoom = st.zoom;
  const w0 = (await page.locator('.image-viewer').boundingBox()).width;
  await dragCorner(-700, -450);
  const w1 = (await page.locator('.image-viewer').boundingBox()).width;
  st = await state(); report('drag small', st); await shot('small');
  check(w1 < w0 - 300, 'dragging the corner really shrank the frame (' + w0 + ' -> ' + w1 + ')');
  check(inside(st), 'drag smaller: picture sits inside the stage');
  check(st.zoom !== openZoom, 'drag smaller: zoom label changed (' + openZoom + ' -> ' + st.zoom + ')');
  const smallZoom = st.zoom;
  await dragCorner(600, 400);
  st = await state(); report('drag large', st); await shot('large-after');
  check(inside(st) && st.zoom !== smallZoom, 'drag larger: picture grows with the frame (' + smallZoom + ' -> ' + st.zoom + ')');
  await page.locator('[data-iv=close]').click();

  // Route 2: Maximize, then Restore down.
  await open(); await page.locator('.image-viewer').evaluate(el => { el.style.width = '800px'; el.style.height = '560px'; });
  await page.waitForTimeout(100); st = await state(); report('restored  ', st); const restoredZoom = st.zoom;
  await page.locator('[data-iv=maximize]').click(); await page.waitForTimeout(150);
  st = await state(); report('maximized ', st);
  check(inside(st) && st.zoom !== restoredZoom, 'maximize: picture grows (' + restoredZoom + ' -> ' + st.zoom + ')');
  const maxZoom = st.zoom;
  await page.locator('[data-iv=maximize]').click(); await page.waitForTimeout(150);
  st = await state(); report('restored  ', st);
  check(inside(st) && st.zoom !== maxZoom, 'restore down: picture shrinks back (' + maxZoom + ' -> ' + st.zoom + ')');
  await page.locator('[data-iv=close]').click();

  // Route 3: browser window resize (frame is 94vw x 90vh).
  await open(); st = await state(); report('window big', st); const bigZoom = st.zoom;
  await page.setViewportSize({ width: 900, height: 600 }); await page.waitForTimeout(150);
  st = await state(); report('window sml', st);
  check(inside(st) && st.zoom !== bigZoom, 'browser window shrink: picture refits (' + bigZoom + ' -> ' + st.zoom + ')');
  await page.setViewportSize({ width: 1600, height: 1000 }); await page.waitForTimeout(150);
  st = await state(); check(inside(st) && st.zoom === bigZoom, 'browser window grow: picture returns to ' + bigZoom);
  await page.locator('[data-iv=close]').click();

  // Ron's report: a stray wheel-down over the picture at Fit (a ~4% nudge) used
  // to drop Fit mode, leaving "69%" frozen while the frame shrank under it.
  await open(); await page.locator('.image-viewer').evaluate(el => { el.style.width = '1500px'; el.style.height = '900px'; });
  await page.waitForTimeout(100); st = await state(); const fitBefore = st.zoom;
  const sb = await page.locator('.iv-stage').boundingBox();
  await page.mouse.move(sb.x + sb.width / 2, sb.y + sb.height / 2); await page.mouse.wheel(0, 20); await page.waitForTimeout(100);
  st = await state(); report('nudged    ', st);
  check(st.fitPressed === 'true' && st.zoom === fitBefore, 'wheel-out at Fit stays in Fit (' + fitBefore + ')');
  await page.locator('.image-viewer').evaluate(el => { el.style.width = '800px'; el.style.height = '500px'; });
  await page.waitForTimeout(150); st = await state(); report('nudge+small', st); await shot('after-nudge-small');
  check(inside(st) && st.zoom !== fitBefore, 'after a wheel nudge, shrinking the frame still refits (' + fitBefore + ' -> ' + st.zoom + ')');
  // Zooming IN is deliberate: leave Fit, and keep that zoom across the resize.
  const sb2 = await page.locator('.iv-stage').boundingBox();
  await page.mouse.move(sb2.x + sb2.width / 2, sb2.y + sb2.height / 2); await page.mouse.wheel(0, -200); await page.waitForTimeout(100);
  st = await state(); check(st.fitPressed === 'false', 'wheel-in leaves Fit');
  await page.locator('[data-iv=close]').click();

  // Manual zoom is kept across a resize (Windows Photos behaviour).
  await open(); await page.locator('[data-iv=plus]').click(); await page.locator('[data-iv=plus]').click();
  st = await state(); const manual = st.zoom;
  await page.locator('.image-viewer').evaluate(el => { el.style.width = '700px'; el.style.height = '500px'; });
  await page.waitForTimeout(150); st = await state();
  check(st.zoom === manual && st.fitPressed === 'false', 'manual zoom survives a resize (' + manual + ')');
  await page.locator('[data-iv=fit]').click(); await page.waitForTimeout(100);
  await page.locator('.image-viewer').evaluate(el => { el.style.width = '1200px'; el.style.height = '800px'; });
  await page.waitForTimeout(150); st = await state();
  check(inside(st) && st.fitPressed === 'true', 'Fit button re-arms following the frame');
  console.log(`\n${checks} checks passed`);
} finally { await browser.close(); }
