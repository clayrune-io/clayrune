#!/usr/bin/env node
// MC-968: real Results/retro rendering against recorded-outcome projections.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const INDEX = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const shots = resolve(ROOT, '_scratch/step9');
mkdirSync(shots, { recursive: true });
const receipts = [
  { id: 'low', published_at: '2026-10-20T00:00:00Z', platform: 'x', body: 'A release accepted two days ago', impressions: 4, replies: 0, possibly_suppressed: true },
  { id: 'missing', published_at: '2026-10-20T00:00:00Z', platform: 'linkedin', body: 'No measurements returned', impressions: null, replies: null, possibly_suppressed: null },
  { id: 'normal', published_at: '2026-10-20T00:00:00Z', platform: 'x', body: 'A measured receipt', impressions: 18, replies: 2, possibly_suppressed: false },
  { id: 'young', published_at: '2026-10-22T23:00:00Z', platform: 'x', body: '<img src=x onerror=alert(1)>', impressions: 1, replies: null, possibly_suppressed: null },
];
const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 1100 } });
    const page = await context.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    const fx = loadFixtures();
    const workspace = workspaceFromFixtures(fx);
    await page.route('**/*', async (route) => {
      const path = new URL(route.request().url()).pathname;
      const J = (body) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
      if (path === '/') return route.fulfill({ status: 200, contentType: 'text/html', body: INDEX });
      if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
      if (path === '/api/projects') return J(fx.projects.map((p) => ({ ...p, status: 'active', roster: [], backlog: [], activity_log: [], provider: 'claude', domain: 'general' })));
      if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
      if (path === '/api/characters') return J([]);
      if (path === '/api/local-auth/status') return J({ configured: true });
      if (path === '/api/desk/workspace') return J(workspace);
      if (/\/campaigns\/[^/]+\/results$/.test(path)) return J({ goal: { current: null, target: 30 }, terms: [], versions: [], costs: {} });
      if (/\/campaigns\/[^/]+\/retro$/.test(path)) return J({ term: 1, status: 'interim', goal: {}, spend: {}, dimensions: [], findings: [], receipt_outcomes: receipts });
      return route.abort();
    });
    await page.goto(ORIGIN, { waitUntil: 'domcontentloaded' });
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForSelector('.desk-v1-shell');
    await page.evaluate(() => window.deskV1Nav('results', { campaignId: 'camp-1' }));
    await page.waitForSelector('[data-receipt-outcome="low"]');
    const low = page.locator('[data-receipt-outcome="low"]');
    assert.match(await low.textContent(), /Impressions: 4[\s\S]*Replies: 0[\s\S]*possibly suppressed/);
    assert.match(await page.locator('[data-receipt-outcome="missing"]').textContent(), /Impressions: unknown[\s\S]*Suppression: unknown/);
    assert.match(await page.locator('[data-receipt-outcome="normal"]').textContent(), /Reach meets threshold/);
    assert.match(await page.locator('[data-receipt-outcome="young"]').textContent(), /Suppression: unknown/);
    assert.equal(await page.locator('[data-receipt-outcomes] img').count(), 0);
    const cells = page.locator('[data-receipt-outcomes]');
    await cells.scrollIntoViewIfNeeded();
    const bounds = await cells.boundingBox();
    assert.ok(bounds.x >= 0 && bounds.x + bounds.width <= width + 1, 'receipt cells fit viewport');
    await cells.screenshot({ path: resolve(shots, `results-${width}.png`) });
    // Outcomes remain available before and after expanding an interim retro.
    await page.locator('[data-retro-run]').click();
    assert.equal(await page.locator('[data-receipt-outcome]').count(), receipts.length);
    assert.deepEqual(errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
    await context.close();
    console.log(`PASS ${width}: live receipt outcomes, unknowns, escaping, viewport, interim expansion`);
  }
} finally {
  await browser.close();
}
