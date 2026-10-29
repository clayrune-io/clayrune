#!/usr/bin/env node
// Real SPA and Connect screens; hermetic workspace and registry projections.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const shots = resolve(root, '_scratch/where-services-copy');
mkdirSync(shots, { recursive: true });
// Read the actual registry and actual connection choices, without a live server.
const catalog = JSON.parse(execFileSync('python', ['-c',
  'import json; from mc.desk_connect import registry, type_view; print(json.dumps([{**registry.brief(s), "types": type_view.project_service(s["id"])} for s in registry.registry()["services"]]))',
], { cwd: root, encoding: 'utf8' }));

async function boot(browser, width, failCatalog = false) {
  const fx = loadFixtures();
  fx.channels = fx.channels.filter(c => ['ch-x-ron', 'ch-li-page', 'ch-blog'].includes(c.id));
  fx.families.push({ id: 'p-empty', campaignId: 'camp-1', kind: 'post', title: 'Empty post', body: '', assets: [], versions: [] });
  const ctx = await browser.newContext({ viewport: { width, height: 950 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(10000);
  const errors = [], calls = [];
  let failTypes = false;
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const J = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return J(fx.projects.map(p => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [], project_path: '/smoke/' + p.id })));
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/desk/workspace') return J(workspaceFromFixtures(fx));
    if (path === '/api/desk/connect/services') {
      if (failCatalog) { failCatalog = false; return J({ error: 'Catalogue offline' }, 503); }
      return J({ services: catalog.map(({ types, ...s }) => s) });
    }
    if (path === '/api/desk/connect/types') {
      calls.push({ method: req.method(), body: req.postDataJSON() });
      if (failTypes) { failTypes = false; return J({ error: 'Options temporarily unavailable' }, 503); }
      const svc = catalog.find(s => s.url === req.postDataJSON().input);
      return J({ ...svc.types, host: svc.host, url: svc.url, input: svc.url });
    }
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().families.some(f => f.id === 'p-empty'));
  const where = async () => {
    await page.evaluate(() => window.deskV1GotoCampaignPanel('where', { campaignId: 'camp-1' }));
    await page.waitForSelector('[data-where]');
  };
  const piece = async () => {
    await page.evaluate(() => window.deskV1Nav('piece', { familyId: 'p-empty', campaignId: 'camp-1' }));
    await page.waitForSelector('[data-piece-copy-where], [data-piece-copy-campaign]');
  };
  return { ctx, page, errors, calls, where, piece, failNextTypes: () => { failTypes = true; } };
}

let browser;
try {
  browser = await chromium.launch();
  for (const width of [1440, 390]) {
    const { ctx, page, errors, calls, where, piece, failNextTypes } = await boot(browser, width, true);
    await where();
    await page.locator('[data-where-services-retry]').click();
    await page.waitForSelector('[data-where-service="youtube"]');
    const ids = await page.locator('[data-where-service]').evaluateAll(nodes => nodes.map(n => n.dataset.whereService));
    assert.deepEqual(ids.sort(), catalog.filter(s => !['x', 'linkedin'].includes(s.id)).map(s => s.id).sort());
    assert.equal(await page.locator('[data-where-source]').count(), 3);
    assert.equal(await page.locator('[data-where-service][data-where-source]').count(), 0);
    assert.equal(await page.locator('[data-where-service]').evaluateAll(nodes => nodes.some(n => n.draggable || n.hasAttribute('tabindex') || n.hasAttribute('data-channel-id'))), false);
    await page.locator('[data-where-sources]').scrollIntoViewIfNeeded();
    const overflow = await page.locator('[data-where-service-connect]').evaluateAll(nodes => nodes.filter(n => n.scrollWidth > n.clientWidth + 1).map(n => n.textContent));
    assert.deepEqual(overflow, []);
    await page.screenshot({ path: resolve(shots, `sources-${width}.png`) });
    await page.locator('[data-where-service-connect="github"]').scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(shots, `sources-bottom-${width}.png`) });
    failNextTypes();
    await page.locator('[data-where-service-connect="youtube"]').click();
    await page.waitForFunction(() => document.querySelector('[data-where-service-connect="youtube"]')?.disabled === false);
    assert.equal(await page.locator('[data-where]').count(), 1, 'options failure stays on board');
    assert.match(await page.locator('body').innerText(), /Options temporarily unavailable/);
    assert.equal(await page.locator('[data-where-service-connect="youtube"]').evaluate(n => n.getBoundingClientRect().right <= innerWidth), true);
    await page.locator('[data-where-service-connect="youtube"]').focus();
    await page.keyboard.press('Enter');
    await page.waitForSelector('[data-cfw-step="connection"]');
    assert.equal(await page.evaluate(() => window.DeskV1ConnectWizard.state().sel.service), 'youtube');
    assert.equal(calls.at(-1).body.input, catalog.find(s => s.id === 'youtube').url);
    await page.screenshot({ path: resolve(shots, `connect-youtube-${width}.png`) });
    await where();
    await page.locator('[data-where-service-connect="instagram"]').click();
    await page.waitForFunction(() => window.DeskV1ConnectWizard.state().sel.service === 'instagram');
    await page.waitForSelector('[data-cfw-step="connection"]');

    // A connected account replaces its placeholder; existing accounts stay separate.
    await page.evaluate(() => window.DeskV1Store.state().channels.push({ id: 'yt-new', platform: 'youtube', identity: 'My channel', connected: true, capability: 'none' }));
    await where();
    assert.equal(await page.locator('[data-where-service="youtube"]').count(), 0);
    assert.equal(await page.locator('[data-channel-id="yt-new"][data-where-source-readonly]').count(), 1);

    for (const kind of ['post', 'article', 'video']) {
      await page.evaluate(kind => { window.DeskV1Store.state().families.find(f => f.id === 'p-empty').kind = kind; }, kind);
      await piece();
      if (kind === 'post') await page.screenshot({ path: resolve(shots, `copy-${width}.png`) });
      await page.locator('[data-piece-copy-where]').click();
      await page.waitForSelector('[data-where][data-campaign-id="camp-1"]');
    }
    await page.evaluate(() => { const p = window.DeskV1Store.state().families.find(f => f.id === 'p-empty'); p.campaignId = null; p.kind = 'post'; });
    await piece();
    assert.match(await page.locator('[data-piece-section="copy"]').innerText(), /Add this piece to a campaign in What/);
    await page.screenshot({ path: resolve(shots, `copy-no-campaign-${width}.png`) });
    await page.locator('[data-piece-copy-campaign]').click();
    await page.waitForSelector('.desk-v1-home');
    assert.deepEqual(errors.filter(e => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
    await ctx.close();
    console.log(`PASS ${width}px: complete catalogue, retry, no drag, keyboard Connect, real wizard selection, connected replacement, Copy routes including articles/orphans`);
  }
} catch (e) { console.error(e.stack || e); process.exitCode = 1; }
finally { if (browser) await browser.close(); }
