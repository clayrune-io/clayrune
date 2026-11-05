#!/usr/bin/env node
/**
 * Focused check for Settings → Background "Fit & framing" (zoom + position).
 *
 * Verifies that applyDashboardBackground() sizes the body background from the
 * cover baseline × zoom and positions it at the chosen focal point, that the
 * choice persists to localStorage, that it recomputes on viewport resize, and
 * that the Settings live-preview <img> mirrors the crop. Hermetic: the page +
 * /api/projects + /api/config are fulfilled from fixtures, everything else is
 * aborted (same approach as boot-smoke.mjs). No server, no network, no data.
 *
 *   cd tools/smoke && node bg-framing-check.mjs
 */
import { chromium } from 'playwright';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
// Every real static/js/**/*.js and static/css/**/*.css, walked from disk
// (_static.mjs) — no per-module const / route line to forget when a module is
// added, or imported by an existing one (an unserved module is aborted and the
// SPA boots without it).
const STATIC = loadStaticJsCss(REPO_ROOT);
const PROJECTS_JSON = readFileSync(resolve(__dirname, 'fixtures', 'projects.json'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const ok = (m) => console.log('  ✓ ' + m);
let bad = 0;
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

let browser, exitCode = 1;
try {
  browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await ctx.newPage();
  page.on('pageerror', (e) => fail('pageerror: ' + (e.message || e)));
  await page.route('**/*', (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/api/projects') return route.fulfill({ status: 200, contentType: 'application/json', body: PROJECTS_JSON });
    if (path === '/api/config') return route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    const hit = STATIC[path];
    if (hit) return route.fulfill({ status: 200, contentType: hit[0], body: hit[1] });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card', { timeout: 15000 });

  // Inject a 400×400 image and drive the public setters, exactly as the UI does.
  const applied = await page.evaluate(() => {
    const c = document.createElement('canvas'); c.width = 400; c.height = 400;
    const g = c.getContext('2d'); g.fillStyle = '#3a6bb5'; g.fillRect(0, 0, 400, 400);
    const url = c.toDataURL('image/png');
    localStorage.setItem('mc_bg_image', url);
    localStorage.setItem('mc_bg_imgw', '400');
    localStorage.setItem('mc_bg_imgh', '400');
    // Framing is driven by the crop-box editor since 3380a39 (it replaced the
    // setBgZoom/setBgPosX/setBgPosY sliders, which this smoke went on calling
    // and so failed on every run until 2026-09-20). Write the same three
    // globals a drag writes, then persist + apply exactly as _bgCropDragEnd does.
    setBgMode('image');     // globals defined by the SPA
    bgZoom = 150;
    bgPosX = 20;
    bgPosY = 80;
    _bgSaveFraming();
    applyDashboardBackground();
    return { size: document.body.style.backgroundSize, pos: document.body.style.backgroundPosition };
  });

  // cover for 400×400 at 1280×800 = max(1280/400, 800/400) = 3.2; ×1.5 = 4.8 → 1920px.
  applied.size === 'cover, 1920px 1920px' ? ok('body background-size = cover baseline × zoom (' + applied.size + ')')
    : fail('unexpected background-size: ' + applied.size);
  applied.pos === 'center center, 20% 80%' ? ok('body background-position = focal point (' + applied.pos + ')')
    : fail('unexpected background-position: ' + applied.pos);

  const persisted = await page.evaluate(() => ({
    zoom: localStorage.getItem('mc_bg_zoom'), x: localStorage.getItem('mc_bg_posx'), y: localStorage.getItem('mc_bg_posy'),
  }));
  (persisted.zoom === '150' && persisted.x === '20' && persisted.y === '80')
    ? ok('framing persisted to localStorage (zoom=150 x=20 y=80)')
    : fail('framing not persisted: ' + JSON.stringify(persisted));

  // Resize → recompute. At 800×800, cover = max(2, 2) = 2; ×1.5 = 3.0 → 1200px.
  await page.setViewportSize({ width: 800, height: 800 });
  await page.waitForTimeout(220); // debounced 120ms
  const resized = await page.evaluate(() => document.body.style.backgroundSize);
  resized === 'cover, 1200px 1200px' ? ok('background-size recomputed on resize (' + resized + ')')
    : fail('resize did not recompute: ' + resized);

  // The crop box mirrors zoom + focal point (Settings -> Appearance -> Background).
  // 3380a39 replaced the old #mc-bg-preview-img with the drag-box editor, so the
  // visible region is now the BOX: at viewport 800x800 on a 400x400 image with
  // zoom 150, cover = 2, scale = 3, so the box spans 800/1200 = 66.7% of the
  // displayed image and sits at (1 - 0.667) * 20% = 6.7% from its left edge.
  await page.evaluate(async () => {
    if (typeof openSettings === 'function') await openSettings();  // async: fetches config
  });
  await page.waitForSelector('#settings-body', { timeout: 8000 }).catch(() => {});
  await page.evaluate(() => {
    if (typeof drillSettings === 'function') drillSettings('appearance');
    // The Background pane renders on the search view, which flattens every pane.
    if (typeof filterSettings === 'function') filterSettings('framing');
  });
  await page.waitForSelector('#mc-crop-box', { timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(150);  // the box renders on the crop <img>'s onload
  const box = await page.evaluate(() => {
    const b = document.getElementById('mc-crop-box');
    const i = document.getElementById('mc-crop-img');
    if (!b || !i || !i.clientWidth) return null;
    return { wFrac: parseFloat(b.style.width) / i.clientWidth,
             leftFrac: parseFloat(b.style.left) / i.clientWidth };
  });
  if (!box) {
    fail('crop box (#mc-crop-box) did not render');
  } else {
    Math.abs(box.wFrac - 0.667) < 0.03
      ? ok('crop box width mirrors zoom (' + box.wFrac.toFixed(3) + ' of the image)')
      : fail('crop box width wrong: ' + box.wFrac);
    Math.abs(box.leftFrac - 0.067) < 0.02
      ? ok('crop box offset mirrors focal point (' + box.leftFrac.toFixed(3) + ')')
      : fail('crop box offset wrong: ' + box.leftFrac);
  }

  exitCode = bad === 0 ? 0 : 1;
  console.log(bad === 0 ? '\n✅ PASS — background framing works.' : `\n❌ FAIL — ${bad} check(s) failed.`);
} catch (err) {
  console.error('❌ harness error:', err && err.stack ? err.stack : err);
  exitCode = 1;
} finally {
  if (browser) await browser.close().catch(() => {});
  process.exit(exitCode);
}
