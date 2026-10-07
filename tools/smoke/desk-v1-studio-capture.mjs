#!/usr/bin/env node
// Live Studio capture UI contract, full SPA, hermetic API. Backend Chromium
// and network confinement are exercised by tests/test_desk_capture.py.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const INDEX = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const SHOTS = resolve(ROOT, '_scratch/capture');
mkdirSync(SHOTS, { recursive: true });
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aP1kAAAAASUVORK5CYII=', 'base64');
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const fx = loadFixtures();
    const pid = fx.projects[0].id;
    const context = await browser.newContext({ viewport: { width, height: 950 } });
    const page = await context.newPage();
    const errors = [], calls = [], items = [];
    let appAddress = '', failCapture = true;
    page.on('pageerror', e => errors.push(e.message));
    const projects = fx.projects.map(p => ({ ...p, state: 'active', status: 'active', domain: 'general',
      activity_log: [], backlog: [], roster: [], last_updated_relative: 'today',
      presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
    await page.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url()), path = url.pathname, method = req.method();
      const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ contentType: 'text/html', body: INDEX });
      if (STATIC[path]) return route.fulfill({ contentType: STATIC[path][0], body: STATIC[path][1] });
      if (path === '/api/serve-image') return route.fulfill({ contentType: 'image/png', body: PNG });
      if (path === '/api/projects') return J(projects);
      if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/characters') return J([]);
      if (path === '/api/local-auth/status') return J({ configured: true });
      if (!path.startsWith('/api/desk/')) return route.abort();
      const body = req.postData() ? JSON.parse(req.postData()) : null;
      calls.push({ path, method, body });
      if (path === '/api/desk/workspace') return J({ ...workspaceFromFixtures(fx), projects, pieces: fx.families });
      if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
      if (path === '/api/desk/studio/articles') return J({ articles: [] });
      if (path === '/api/desk/studio/usage') return J({ rendering: [], files: {} });
      if (path === '/api/desk/materials') {
        assert.equal(method, 'GET', 'capture must not upload a second copy');
        return J({ library: { video: [], image: items.length ? [{ id: 'Studio', title: 'Studio', files: items.length, items }] : [] }, recent: items, articles: [], online: { video: [], image: [] } });
      }
      if (path.startsWith('/api/desk/capture/projects/')) {
        if (method === 'PUT') appAddress = body.app_address;
        return J({ project_id: pid, name: 'Test product', app_address: appAddress, clayrune: false, pages: [] });
      }
      if (path === '/api/desk/capture') {
        assert.equal(body.project_id, pid);
        assert.equal(body.page, '/pricing');
        if (failCapture) { failCapture = false; return J({ error: 'The app is not running.' }, 502); }
        const item = { id: 'desk/library/image/Studio/product.png', kind: 'image', title: 'Product · Pricing', path: 'desk/library/image/Studio/product.png', src: '/api/serve-image?path=capture.png' };
        items.push(item);
        return J({ item });
      }
      return J({ error: 'unhandled ' + path }, 404);
    });
    await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-home-studio-btn');
    await page.click('.desk-v1-home-studio-btn');
    await page.click('[data-studio-new="image"]');
    await page.selectOption('[data-sc-product]', pid);
    await page.click('[data-sc-source="capture"]');
    await page.waitForSelector('[data-capture-address]');
    assert.match(await page.textContent('[data-capture-status]'), /address where you open/);
    assert.equal(await page.locator('[data-capture-take]').count(), 0);
    await page.screenshot({ path: resolve(SHOTS, `capture-address-${width}.png`) });
    await page.fill('[data-capture-address]', 'http://localhost:3000');
    await page.click('[data-capture-save]');
    await page.waitForSelector('[data-capture-page]');
    await page.fill('[data-capture-page]', '/pricing');
    await page.screenshot({ path: resolve(SHOTS, `capture-ready-${width}.png`) });
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
    await page.click('[data-capture-take]');
    await page.waitForFunction(() => document.querySelector('[data-capture-status]').textContent.includes('not running'));
    assert.equal(await page.isEnabled('[data-capture-take]'), true);
    await page.click('[data-capture-take]');
    await page.waitForSelector('[data-sc-saved]');
    assert.equal(await page.isEnabled('[data-sc-use]'), true);
    assert.equal(items.length, 1);
    assert.equal(calls.filter(c => c.path === '/api/desk/materials' && c.method === 'POST').length, 0);
    await page.screenshot({ path: resolve(SHOTS, `capture-saved-${width}.png`) });
    await page.evaluate(() => window.deskV1Nav('studio'));
    await page.waitForSelector('[data-studio-recent-row]');
    assert.match(await page.textContent('[data-studio-recent-row]'), /Product/);
    // The same source body in campaign What needs an asset id, not the library
    // path that identifies the FILE. Inspect the bridge without mutating a campaign.
    const bridged = await page.evaluate(async pid => {
      const host = document.createElement('div'); document.body.appendChild(host);
      const ctx = { card: { id: 'campaign-capture' }, fam: { kind: 'image' }, camp: { projectId: pid } };
      host.innerHTML = window.DeskV1StudioCapture.bodyHTML(ctx);
      let chosen;
      await window.DeskV1StudioCapture.wire(host, ctx, { attach: item => { chosen = item; } });
      host.querySelector('[data-capture-page]').value = '/pricing';
      await host.querySelector('[data-capture-take]').onclick();
      host.remove(); return chosen;
    }, pid);
    assert.match(bridged.id, /^[A-Za-z0-9_-]{1,80}$/);
    assert.equal(bridged.path, 'desk/library/image/Studio/product.png');
    assert.deepEqual(errors, []);
    console.log(`PASS capture ${width}: address, page, retry, saved path, Recent, no duplicate upload or overflow`);
    await context.close();
  }
} finally { await browser.close(); }
