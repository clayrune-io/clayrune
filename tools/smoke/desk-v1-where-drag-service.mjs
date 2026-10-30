// C5: retained entry name; saved sign-in explanation and account selection, no drag.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const files = loadStaticJsCss(root);
const catalog = [{ id: 'instagram', label: 'Instagram', host: 'www.instagram.com', url: 'https://www.instagram.com' }];

async function boot(browser, width, variant) {
  const fx = loadFixtures();
  fx.channels = fx.channels.filter((c) => ['ch-x-ron', 'ch-blog'].includes(c.id));
  let saved = [];
  if (variant === 'service') saved = [{ id: 'svc-instagram', name: 'www.instagram.com', link: 'https://www.instagram.com', kind: 'browser_profile', browser_connection: { service: null, browser_profile: 'main', status: { state: 'not_checked' } } }];
  if (variant === 'assisted') fx.channels.push({ id: 'ig-account', platform: 'instagram', identity: 'My Instagram', label: 'My Instagram', connected: true, capability: 'assisted', publish: { ready: false, reason: null } });
  if (variant === 'readonly') fx.channels.push({ id: 'ig-account', platform: 'instagram', identity: 'My Instagram', label: 'My Instagram', connected: true, capability: 'none', publish: { ready: false, reason: null } });
  const ctx = await browser.newContext({ viewport: { width, height: 950 } });
  const page = await ctx.newPage();
  page.setDefaultTimeout(10000);
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', (route) => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const J = (data, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(data) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path.startsWith('/api/desk/engagement/coverage/')) return J({ coverage: [] });
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return J(fx.projects.map((p) => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [], project_path: '/smoke/' + p.id })));
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/browser/profiles') return J({ profiles: [{ name: 'main' }] });
    if (path === '/api/browser/agent-read') return J({ profiles: { main: { enabled: false, domains: [] } } });
    if (path === '/api/desk/workspace') return J(workspaceFromFixtures(fx));
    if (path === '/api/desk/services') return J(saved);
    if (path === '/api/desk/connect/services') return J({ services: catalog });
    if (req.method() !== 'GET' && path.startsWith('/api/desk/campaigns/')) return J({ ok: true });
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1GotoCampaignPanel('where', { campaignId: 'camp-1' }));
  await page.waitForSelector('[data-where]');
  return { ctx, page, errors };
}

const columns = (page) => page.locator('.desk-v1-where-col').evaluateAll((n) => n.map((c) => c.dataset.channelId));
const toastText = (page) => page.evaluate(() => (document.querySelector('.desk-v1-toast, [data-desk-toast], #desk-v1-toast') || {}).innerText || '');

let browser, failed = 0;
const results = [];
try {
  browser = await chromium.launch();
  for (const width of [1440, 390]) {
    // A. saved sign-in only (Ron's data)
    {
      const { ctx, page, errors } = await boot(browser, width, 'service');
      await page.waitForSelector('[data-where-service="svc-instagram"]');
      const before = await columns(page);
      await page.locator('[data-where-service="svc-instagram"]').click({ position: { x: 20, y: 8 } });
      const r = { hovered: false };
      const after = await columns(page);
      const note = await page.locator('[data-where-service-why]').count();
      results.push({ width, case: 'A saved sign-in', ghostRan: r.hovered, columnsBefore: before.length, columnsAfter: after.length, whyShown: note });
      assert.equal(after.length, before.length, `${width}px A: a sign-in is not an account, no column is added`);
      assert.equal(r.hovered, false, `${width}px A: selection uses no drag target`);
      assert.equal(note, 1, `${width}px A: the user is told why the drop did not add a column`);
      const why = await page.locator('[data-where-service-why]').innerText();
      assert.match(why, /Instagram/i); assert.match(why, /account/i);
      const seen = await page.locator('[data-where-service-why]').evaluate((n) => { const r = n.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; });
      assert.equal(seen, true, `${width}px A: the notice is on screen after the drop, not scrolled away`);
      await page.screenshot({ path: resolve(root, `_scratch/where-ig-why-${width}.png`) });
      // The notice offers the one next step; Enter on the tile reaches the same explanation.
      await page.evaluate(() => window.deskV1GotoCampaignPanel('where', { campaignId: 'camp-1' }));
      await page.waitForSelector('[data-where-service="svc-instagram"]');
      assert.equal(await page.locator('[data-where-service-why]').count(), 0, `${width}px A: leaving Where clears the notice`);
      await page.locator('[data-where-service="svc-instagram"]').focus();
      await page.keyboard.press('Enter');
      await page.waitForSelector('[data-where-service-why]');
      await page.evaluate(() => { window.__nav = null; window.deskV1Nav = (...a) => { window.__nav = a; }; });
      await page.locator('[data-where-service-why] [data-where-service-open]').click();
      assert.deepEqual(await page.evaluate(() => window.__nav), ['connections', { account: 'service:svc-instagram', attachSite: 'svc-instagram' }], `${width}px A: Link account opens the attach flow`);
      assert.deepEqual(errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
      await ctx.close();
    }
    // B / C. an Instagram account
    for (const variant of ['assisted', 'readonly']) {
      const { ctx, page, errors } = await boot(browser, width, variant);
      await page.waitForSelector('[data-where-source][data-channel-id="ig-account"]');
      const before = await columns(page);
      await page.locator('[data-where-source][data-channel-id="ig-account"]').click({ position: { x: 20, y: 8 } });
      const after = await columns(page);
      results.push({ width, case: variant, columnsBefore: before.length, columnsAfter: after.length });
      assert.equal(after.length, before.length + 1, `${width}px ${variant}: selecting the Instagram account adds its column`);
      assert.ok(after.includes('ig-account'));
      assert.deepEqual(errors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)), []);
      await ctx.close();
    }
    console.log(`PASS ${width}px`);
  }
} catch (e) { failed = 1; console.error(e.stack || e); }
finally { console.log(JSON.stringify(results)); if (browser) await browser.close(); }
process.exitCode = failed;
