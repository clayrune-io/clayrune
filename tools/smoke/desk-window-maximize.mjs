#!/usr/bin/env node
// Desk window Maximize button (Ron 2026-10-31): the Desk header carries
// minimize / maximize / close like every other window. Maximize fills the
// viewport and the Desk body grows with it; a second click restores the
// floating window to its earlier size and position.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const files = loadStaticJsCss(root);
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const shots = resolve(root, '_scratch/desk_window_maximize');
mkdirSync(shots, { recursive: true });

const W = 1440, H = 900;
const browser = await chromium.launch({ headless: true });
try {
  const fx = loadFixtures();
  const ctx = await browser.newContext({ viewport: { width: W, height: H } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(7000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', async (route) => {
    const path = new URL(route.request().url()).pathname;
    const json = (v) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(v) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path.startsWith('/static/desk-icons/')) return route.fulfill({ contentType: path.endsWith('.svg') ? 'image/svg+xml' : 'image/png', body: readFileSync(resolve(root, path.slice(1))) });
    if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return json(fx.projects.map((p) => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [] })));
    if (path === '/api/characters') return json([]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
    if (path === '/api/desk/materials') return json({ library: {}, projects: [], articles: [] });
    if (path.startsWith('/api/desk/engagement/coverage/')) return json({ coverage: [] });
    if (path === '/api/desk/services') return json([]);
    if (path === '/api/desk/connect/services') return json({ services: [] });
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  const win = page.locator('.modal-window[data-modal-id="__desk"]');
  await win.waitFor();

  // All three window controls, in Windows order.
  const order = await win.locator('.modal-window-controls button').evaluateAll((bs) => bs.map((b) => b.className));
  assert.deepEqual(order, ['desk-v1-undo-btn', 'modal-minimize', 'modal-maximize', 'modal-close'], 'Desk header shows undo, minimize, maximize, close');
  const btn = win.locator('.modal-maximize');
  assert.equal(await btn.getAttribute('title'), 'Maximize');

  const rect = () => win.locator('.modal-content').evaluate((el) => { const r = el.getBoundingClientRect(); return { l: r.left, t: r.top, w: r.width, h: r.height }; });
  const bodyH = () => page.locator('#desk-v1-body').evaluate((el) => el.getBoundingClientRect().height);
  const before = await rect();
  const bodyBefore = await bodyH();
  assert(before.w < W && before.h < H, `floating Desk is smaller than the viewport (${JSON.stringify(before)})`);

  await btn.click();
  await page.waitForFunction(() => document.querySelector('.modal-window[data-modal-id="__desk"]').classList.contains('is-maximized'));
  const max = await rect();
  assert(Math.abs(max.l) <= 1 && Math.abs(max.t) <= 1 && Math.abs(max.w - W) <= 1 && Math.abs(max.h - H) <= 1, `maximized Desk fills the viewport (${JSON.stringify(max)})`);
  assert.equal(await btn.getAttribute('title'), 'Restore down');
  // The body must grow past the floating window's height and reach the bottom.
  const bodyMax = await bodyH();
  const bodyBottom = await page.locator('#desk-v1-body').evaluate((el) => el.getBoundingClientRect().bottom);
  assert(bodyMax > bodyBefore + 50, `Desk body grows when maximized (${bodyBefore} -> ${bodyMax})`);
  assert(bodyBottom <= H + 1 && bodyBottom > H - 60, `Desk body reaches the bottom without overflowing (${bodyBottom})`);
  await page.screenshot({ path: resolve(shots, 'maximized_1440.png') });

  await btn.click();
  await page.waitForFunction(() => !document.querySelector('.modal-window[data-modal-id="__desk"]').classList.contains('is-snapped'));
  const after = await rect();
  for (const k of ['l', 't', 'w', 'h']) assert(Math.abs(after[k] - before[k]) <= 2, `restore returns ${k} (${before[k]} -> ${after[k]})`);
  assert.equal(await btn.getAttribute('title'), 'Maximize');
  assert(Math.abs((await bodyH()) - bodyBefore) <= 2, 'Desk body returns to its floating height');
  await page.screenshot({ path: resolve(shots, 'restored_1440.png') });

  assert.deepEqual(errors, [], `no page errors: ${errors.join(' | ')}`);
  console.log('PASS Desk window: minimize/maximize/close controls, maximize fills viewport, body grows, restore returns geometry');
  await ctx.close();
} finally { await browser.close(); }
