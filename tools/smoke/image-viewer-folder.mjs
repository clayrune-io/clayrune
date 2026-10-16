#!/usr/bin/env node
// MC-1100 regression: a chat picture that is alone in its message still steps
// through its folder (/api/serve-image/siblings). Hermetic: stubbed routes only.
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(ROOT);
const files = ['C:/pics/a.png', 'C:/pics/b.png', 'C:/pics/c.png'];
const html = `<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/css/app.css"><link rel="stylesheet" href="/static/css/image-viewer.css"></head>
<body style="margin:0"><div id="minimized-tray"></div>
<script>window.API_BASE='';window.esc=s=>String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));</script>
<script type="module" src="/static/js/image-viewer.js"></script><script type="module" src="/static/js/rich-text.js"></script></body></html>`;
// 1x1 PNG; the route hands back the same pixels for every file.
const pngB64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
const png = Buffer.from(pngB64, 'base64');
let checks = 0;
function check(condition, label) { assert.ok(condition, label); checks++; console.log('PASS ' + label); }

const browser = await chromium.launch();
try {
  // folder = 3 files, index 1 | one = 1 file | 404 | late-* = list answers only when released
  for (const mode of ['folder', 'one', '404', 'late-after-close', 'late-after-nav']) {
    const context = await browser.newContext({ viewport: { width: 1200, height: 800 }, hasTouch: true });
    const page = await context.newPage();
    const errors = [], siblings = [];
    let release = null;
    page.on('pageerror', e => errors.push(e.message));
    await page.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[url.pathname]) { const [contentType, body] = assets[url.pathname]; return route.fulfill({ contentType, body }); }
      if (url.pathname === '/api/serve-image') return route.fulfill({ contentType: 'image/png', body: png });
      if (url.pathname === '/api/serve-image/siblings') {
        siblings.push(url.searchParams.get('path'));
        if (mode === '404') return route.fulfill({ status: 404, body: 'nope' });
        if (mode.startsWith('late-')) await new Promise(r => { release = r; });
        const list = mode === 'one' ? [files[1]] : files;
        return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ files: list, index: mode === 'one' ? 0 : 1 }) });
      }
      return route.abort();
    });
    await page.goto('https://folder.smoke.test');
    await page.waitForFunction(() => window._openImageViewer && window.formatAgentText);
    const count = () => page.locator('.iv-count').textContent();
    const shown = () => page.locator('.iv-title').textContent();
    // Real chat rendering, real click handler.
    const chat = async text => {
      await page.evaluate(({ text }) => {
        document.getElementById('chat')?.remove();
        const root = document.createElement('div'); root.id = 'chat';
        root.innerHTML = '<div class="agent-line">' + window.formatAgentText(text) + '</div><div class="agent-line agent-line-prompt">next</div>';
        document.body.append(root);
      }, { text });
      await page.locator('#chat .agent-img').first().click();
      await page.waitForFunction(() => document.querySelector('.iv-image') && !document.querySelector('.iv-image').hidden);
    };

    if (mode === 'folder') {
      await chat(files[1]);
      await page.waitForSelector('.iv-nav-next');
      check(await count() === '2 / 3' && await shown() === 'b.png', 'folder list: counter 2 / 3 on the opened picture, picture unchanged');
      check(siblings[0] === files[1], 'folder list: route asked about the opened file');
      check(await page.locator('.iv-nav').count() === 2 && await page.locator('.iv-thumb').count() === 3, 'folder list: arrows and 3 filmstrip thumbs appear');
      check(await page.locator('[data-iv=filmstrip]').isEnabled(), 'folder list: filmstrip button enabled');
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('.iv-title').textContent === 'c.png');
      check(await count() === '3 / 3', 'folder list: ArrowRight moves to the next file, 3 / 3');
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('.iv-title').textContent === 'a.png');
      check(await count() === '1 / 3', 'folder list: wraps to the first file');
      await page.locator('.iv-nav-next').click();
      check(await count() === '2 / 3', 'folder list: next arrow click');
      await page.locator('[data-iv=filmstrip]').click();
      await page.locator('.iv-thumb').nth(2).click();
      check(await count() === '3 / 3' && await page.locator('.iv-thumb').nth(2).getAttribute('aria-current') === 'true', 'folder list: filmstrip thumb selects and marks current');
      await page.keyboard.press('ArrowLeft'); await page.keyboard.press('ArrowLeft');
      check(await count() === '1 / 3', 'folder list: ArrowLeft steps back');
      await page.locator('[data-iv=fit]').click();
      await page.locator('.iv-stage').evaluate(el => {
        const r = el.getBoundingClientRect(), y = r.top + r.height / 2, x = r.left + r.width * .7;
        const t = cx => new Touch({ identifier: 0, target: el, clientX: cx, clientY: y });
        for (const [type, pts, end] of [['touchstart', [t(x)], [t(x)]], ['touchmove', [t(x - 100)], [t(x - 100)]], ['touchend', [], [t(x - 100)]]])
          el.dispatchEvent(new TouchEvent(type, { touches: pts, changedTouches: end, bubbles: true, cancelable: true }));
      });
      await page.waitForFunction(() => document.querySelector('.iv-count').textContent === '2 / 3');
      check(true, 'folder list: swipe at fit moves to the next file');
    } else if (mode === 'late-after-close') {
      await chat(files[1]);
      check(await page.locator('.iv-nav').count() === 0 && await count() === '1 / 1', 'late-after-close: single picture, no arrows before the list arrives');
      await page.locator('[data-iv=close]').click();
      release?.(); await page.waitForTimeout(250);
      check(await page.locator('.image-viewer').count() === 0, 'late-after-close: answer after close is ignored');
    } else if (mode === 'late-after-nav') {
      // Two-picture message scope; user navigates to a picture the folder does not list before the answer lands.
      await chat(files[1] + '\n' + 'D:/elsewhere/z.png');
      await page.waitForSelector('.iv-nav-next');
      await page.keyboard.press('ArrowRight');
      await page.waitForFunction(() => document.querySelector('.iv-title').textContent === 'z.png');
      release?.(); await page.waitForTimeout(250);
      check(await shown() === 'z.png' && await count() === '2 / 2', 'late-after-nav: picture outside the folder keeps message scope, shown picture untouched');
    } else {
      await chat(files[1]);
      await page.waitForTimeout(250);
      check(siblings.length === 1, `${mode}: folder lookup was attempted once`);
      check(await page.locator('.iv-nav').count() === 0 && await count() === '1 / 1' && await shown() === 'b.png', `${mode}: falls back to the message scope (single picture, no arrows)`);
      check(await page.locator('[data-iv=filmstrip]').isDisabled(), `${mode}: filmstrip stays disabled`);
    }
    check(!errors.length, `${mode}: no runtime exceptions (${errors.join('; ')})`);
    await context.close();
  }
  // data: src has no ?path=, so no lookup at all.
  const context = await browser.newContext({ viewport: { width: 1200, height: 800 } });
  const page = await context.newPage();
  const requested = [];
  await page.route('**/*', route => {
    const url = new URL(route.request().url());
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
    if (assets[url.pathname]) { const [contentType, body] = assets[url.pathname]; return route.fulfill({ contentType, body }); }
    requested.push(url.pathname); return route.abort();
  });
  await page.goto('https://folder.smoke.test');
  await page.waitForFunction(() => window._openImageViewer);
  await page.evaluate(b64 => {
    const img = document.createElement('img'); img.className = 'agent-img'; img.src = 'data:image/png;base64,' + b64;
    const line = document.createElement('div'); line.className = 'agent-line'; line.append(img); document.body.append(line);
    window._openImageViewer(img.src, { source: img });
  }, pngB64);
  await page.waitForTimeout(250);
  check(!requested.includes('/api/serve-image/siblings') && await page.locator('.iv-nav').count() === 0, 'data: src: no folder lookup, message scope');
  await context.close();
  console.log(`PASS ${checks} folder navigation checks`);
} finally { await browser.close(); }
