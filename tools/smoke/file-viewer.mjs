#!/usr/bin/env node
// Real SPA + real formatter/modal manager, hermetic API fixtures. No live writes.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const OUT = resolve(ROOT, '_scratch/file_viewer');
mkdirSync(OUT, { recursive: true });
const assets = loadStaticJsCss(ROOT);
const html = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const projects = readFileSync(resolve(ROOT, 'tools/smoke/fixtures/projects.json'), 'utf8');
const path = 'C:/Projects/Quarterly reports/notes & "review".md';
const markdown = '# Review notes\n\n**Ready to read** on PC and phone.\n\n| File | Result |\n| --- | --- |\n| Report | Open in place |\n\n<script>window.filePreviewInjected = true</script>\n';
let checks = 0;
function check(condition, label) { assert.ok(condition, label); checks++; console.log('PASS ' + label); }
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 }, isMobile: width === 390, hasTouch: width === 390 });
    const errors = [];
    const requests = [];
    await ctx.route('**/*', route => {
      const u = new URL(route.request().url());
      if (u.pathname === '/') return route.fulfill({ contentType: 'text/html', body: html });
      if (assets[u.pathname]) { const [contentType, body] = assets[u.pathname]; return route.fulfill({ contentType, body }); }
      if (u.pathname === '/api/projects') return route.fulfill({ contentType: 'application/json', body: projects });
      if (u.pathname === '/api/config') return route.fulfill({ contentType: 'application/json', body: '{}' });
      if (u.pathname.endsWith('/agent/plan-file')) return route.fulfill({ json: { content: markdown, path, filename: 'notes.md' } });
      if (u.pathname === '/api/serve-file') {
        const p = u.searchParams.get('path');
        requests.push({ path: p, inline: u.searchParams.get('inline') });
        if (p.endsWith('missing.md')) return route.fulfill({ status: 404, body: 'Not found' });
        const body = p === path ? markdown : p.endsWith('large.txt') ? 'x'.repeat(2 * 1024 * 1024 + 1)
          : p.endsWith('/README') ? 'Small extensionless text\nsecond line'
          : p.endsWith('/binary') || p.endsWith('.bin') ? Buffer.from([0, 1, 2, 255])
          : p.endsWith('.py') ? '<script>alert("escaped")</script>\nprint("hello")'
          : p.endsWith('.txt') ? Buffer.from([0xff, 0xfe, 0x48, 0, 0x69, 0]) : 'native media fixture';
        return route.fulfill({ contentType: 'application/octet-stream', headers: { 'Content-Disposition': 'attachment; filename="fixture.bin"' }, body });
      }
      return route.abort();
    });
    const page = await ctx.newPage();
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('https://file.smoke.test');
    await page.waitForFunction(() => typeof allProjects !== 'undefined' && allProjects.length > 0);
    await page.waitForFunction(() => window.openFileViewer && window.renderMarkdownDocument);
    const renderLink = async (p, label = 'Read file') => {
      await page.evaluate(({ p, label }) => {
        let host = document.getElementById('file-link-smoke');
        if (!host) {
          host = document.createElement('div'); host.id = 'file-link-smoke'; host.className = 'agent-output';
          Object.assign(host.style, { position: 'fixed', left: '10px', top: '80px', zIndex: '199', maxWidth: '90vw' });
          document.body.appendChild(host);
        }
        host.innerHTML = window.formatAgentText(`[file:${p}|${label}]`);
      }, { p, label });
      return page.locator('#file-link-smoke .hl-file-link');
    };
    const close = async () => { await page.locator('.file-viewer-content .modal-close').click(); };
    const open = async p => { const link = await renderLink(p); await link.click(); return page.locator('.file-viewer-body'); };
    const link = await renderLink(path);
    check(new URL(await link.getAttribute('href'), page.url()).searchParams.get('inline') === '1', `${width}: text href supports open in new tab`);
    check(await page.locator('#file-link-smoke .hl-file-path').count() === 0, `${width}: no trailing visible path span`);
    check(await link.getAttribute('title') === path, `${width}: title carries exact absolute path`);
    check(await link.locator('.hl-file-ic').count() === 1 && (await link.textContent()).endsWith('Read file') && !(await link.textContent()).includes(path), `${width}: labeled chip keeps icon and label only`);
    const unlabeled = await page.evaluate(p => {
      const host = document.createElement('div'); host.innerHTML = window.formatAgentText(`[file:${p}]`);
      const a = host.querySelector('a.hl-file-link');
      return { title: a.title, text: a.textContent, paths: host.querySelectorAll('.hl-file-path').length };
    }, path);
    check(unlabeled.title === path && unlabeled.text.endsWith(path) && unlabeled.paths === 0, `${width}: unlabeled chip retains path text and title without extra span`);
    await link.click();
    await page.waitForSelector('.file-viewer-body .hl-table td');
    check(await page.locator('.file-viewer-body .hl-bold').textContent() === 'Ready to read', `${width}: markdown renders`);
    check(await page.locator('.file-viewer-body td').last().textContent() === 'Open in place', `${width}: pipe table renders`);
    check(await page.locator('.file-viewer-path').textContent() === path, `${width}: modal retains exact path with quotes, spaces and ampersand`);
    check(await page.locator('.file-viewer-path').evaluate(el => getComputedStyle(el).userSelect === 'text'), `${width}: modal path stays selectable on touch`);
    check(await page.evaluate(() => !window.filePreviewInjected), `${width}: document HTML is escaped`);
    const fits = await page.locator('.file-viewer-content').evaluate(el => {
      const b = el.getBoundingClientRect(), p = el.querySelector('.file-viewer-path');
      const r = p.getBoundingClientRect();
      return b.left >= 0 && b.right <= innerWidth + 1 && b.bottom <= innerHeight + 1 && r.right <= b.right && p.scrollWidth <= p.clientWidth + 1;
    });
    check(fits, `${width}: modal and full path fit viewport`);
    check(requests.some(r => r.path === path && r.inline === '1'), `${width}: preview fetch uses inline URL`);
    await page.locator('#file-link-smoke').evaluate(el => el.style.visibility = 'hidden');
    await page.screenshot({ path: resolve(OUT, `${width}.png`) });
    const downloadPromise = page.waitForEvent('download');
    await page.locator('.file-viewer-download').click();
    const download = await downloadPromise;
    check(download.suggestedFilename() === 'fixture.bin' && requests.at(-1).inline === null, `${width}: explicit Download uses attachment URL`);
    await close();
    await page.locator('#file-link-smoke').evaluate(el => el.style.visibility = 'visible');

    for (const ext of ['png', 'svg', 'pdf', 'mp3', 'mp4']) {
      const media = await renderLink('C:/Projects/picture.' + ext);
      check((await media.getAttribute('href')).includes('inline=1&') && await media.getAttribute('data-file-preview') === null, `${width}: ${ext} native link is intact`);
      check(await media.getAttribute('target') === '_blank', `${width}: ${ext} opens new tab`);
      check(await media.getAttribute('title') === 'C:/Projects/picture.' + ext, `${width}: ${ext} title carries absolute path`);
    }
    const binary = await renderLink('C:/Projects/archive.bin');
    check(!(await binary.getAttribute('href')).includes('inline=') && await binary.getAttribute('data-file-preview') === null, `${width}: unknown binary keeps download URL`);
    check(await binary.getAttribute('title') === 'C:/Projects/archive.bin', `${width}: binary title carries path without Download prefix`);
    const binaryDownload = page.waitForEvent('download'); await binary.click(); await binaryDownload;
    check(await page.locator('.file-viewer-content').count() === 0, `${width}: binary downloads without modal`);

    let body = await open('C:/Projects/README');
    await body.locator('pre').waitFor();
    check((await body.textContent()).includes('Small extensionless text'), `${width}: small extensionless text opens`); await close();
    body = await open('C:/Projects/binary');
    await page.waitForFunction(() => document.querySelector('.file-viewer-body').textContent.includes('Download instead'));
    check((await body.textContent()).includes('text') || (await body.textContent()).includes('binary'), `${width}: extensionless binary is refused`); await close();
    body = await open('C:/Projects/code.py'); await body.locator('pre').waitFor();
    check((await body.textContent()).startsWith('<script>') && await body.locator('script').count() === 0, `${width}: code is escaped and preserved`); await close();
    body = await open('C:/Projects/windows.txt'); await body.locator('pre').waitFor();
    check(await body.textContent() === 'Hi', `${width}: UTF-16 BOM text decodes`); await close();
    body = await open('C:/Projects/large.txt');
    await page.waitForFunction(() => document.querySelector('.file-viewer-body').textContent.includes('too large'));
    check((await body.textContent()).includes('2 MiB'), `${width}: Content-Length cap offers download`); await close();
    body = await open('C:/Projects/missing.md');
    await page.waitForFunction(() => document.querySelector('.file-viewer-body').textContent.includes('HTTP 404'));
    check((await body.textContent()).includes('404'), `${width}: errors remain visible`); await close();

    // No Content-Length: stop consuming a stream as soon as it crosses the cap.
    await page.evaluate(() => {
      const realFetch = window.fetch;
      window.fetch = (...args) => String(args[0]).includes('streamed.txt') ? Promise.resolve(new Response(new ReadableStream({
        start(controller) { controller.enqueue(new Uint8Array(2 * 1024 * 1024 + 1)); },
        cancel() { window.filePreviewStreamCancelled = true; },
      }))) : realFetch(...args);
    });
    body = await open('C:/Projects/streamed.txt');
    await page.waitForFunction(() => document.querySelector('.file-viewer-body').textContent.includes('too large'));
    check(await page.evaluate(() => window.filePreviewStreamCancelled), `${width}: stream cap cancels without buffering the rest`); await close();

    await page.evaluate(() => window.openPlanFileViewer('fixture', 'file-viewer-smoke'));
    await page.waitForSelector('[data-modal-id="__planfile_file-viewer-smoke"] td');
    check(await page.locator('[data-modal-id="__planfile_file-viewer-smoke"] .hl-bold').textContent() === 'Ready to read', `${width}: plan viewer uses shared renderer`);
    await page.evaluate(() => window.closeModalById('__planfile_file-viewer-smoke'));
    // Check the actual browser default on a modified text-link click.
    const modified = await renderLink(path);
    const popupPromise = ctx.waitForEvent('page');
    await modified.click({ modifiers: ['Control'] });
    const popup = await popupPromise; await popup.close();
    check(await page.locator('.file-viewer-content').count() === 0, `${width}: modified click bypasses modal`);
    check(errors.length === 0, `${width}: no uncaught exceptions (${errors.join('; ')})`);
    await ctx.close();
  }
  console.log(`PASS file viewer: ${checks} checks; screenshots ${OUT}`);
} finally { await browser.close(); }
