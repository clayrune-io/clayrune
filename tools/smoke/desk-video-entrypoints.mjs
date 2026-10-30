// Every New video entry must use VideoOpen -> storyboard -> VideoEditor.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { seedDeskV1Fixtures } from './desk-v1-fixture-api.mjs';
const root = fileURLToPath(new URL('../..', import.meta.url));
const files = loadStaticJsCss(root);
const browser = await chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1440, height: 950 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  await page.route('**/*', route => {
    const path = new URL(route.request().url()).pathname;
    const json = body => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path === '/api/config') return json({ desk_v1: true });
    if (path === '/api/projects') return json([{ id: 'clayrune', name: 'Clayrune', status: 'active', domain: 'general', display_order: 0, roster: [] }]);
    if (path === '/api/characters') return json([]);
    return route.abort();
  });
  await seedDeskV1Fixtures(page);
  await page.goto('http://mc.smoke.test/');
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await page.evaluate(() => {
    window.__routes = []; window.__renders = []; window.__opens = [];
    const nav = window.deskV1Nav, render = window.deskV1RenderVideoEditor;
    window.deskV1Nav = (route, params) => { window.__routes.push(route); return nav(route, params); };
    window.deskV1RenderVideoEditor = (el, params) => { window.__renders.push(params); return render(el, params); };
    for (const name of ['open', 'openStudio', 'openFromDeepLink']) {
      const fn = window.DeskV1VideoOpen[name];
      window.DeskV1VideoOpen[name] = (...args) => { window.__opens.push(name); return fn(...args); };
    }
  });
  async function editor(label, standalone, returning = false) {
    await page.waitForSelector('[data-sb-timeline]');
    assert.equal(await page.evaluate(returning => window.__routes.at(returning ? -2 : -1), returning), 'storyboard', label + ' route');
    assert.equal(await page.evaluate(() => !!window.__renders.at(-1).standalone), standalone, label + ' renderer ownership');
    assert.equal(await page.locator('[data-storyboard]').count(), 1);
    assert.equal(await page.locator('[data-vo-details], .desk-v1-video-layout').count(), 0);
    console.log('PASS shared route + renderer: ' + label);
  }
  await page.evaluate(() => window.deskV1Nav('studio'));
  await page.click('[data-studio-new="video"]');
  await editor('Studio New video', true);
  assert.equal(await page.locator('[data-sb-standalone]').count(), 1);
  assert.equal(await page.evaluate(() => window.__opens.at(-1)), 'openStudio');
  // Register the standalone draft, then reopen it from Recent.
  await page.fill('[data-sc-title]', 'Standalone draft'); await page.press('[data-sc-title]', 'Tab');
  const itemId = await page.locator('[data-storyboard]').getAttribute('data-family-id');
  await page.click('.desk-v1-back');
  await page.click(`[data-studio-recent-row="${itemId}"]`);
  await editor('Studio Recent draft', true);
  assert.equal(await page.inputValue('[data-sc-title]'), 'Standalone draft');
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await editor('legacy standalone video deep link', true);
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1', panel: 'what' }));
  await page.click('[data-what-type="video"]');
  await page.click('[data-what-source="create"]');
  await editor('campaign What New video / Create new', false);
  assert.equal(await page.evaluate(() => window.__opens.at(-1)), 'open');
  await page.click('.desk-v1-back');
  await page.click('[data-family-id="fam-install-video"] [data-primary-action]');
  await editor('existing campaign video card', false);
  await page.evaluate(() => {
    const fx = window.DeskV1Store.state();
    fx.channels.find(c => c.id === 'ch-li-page').health = 'ok';
    fx.families.find(f => f.id === 'fam-restore-points').versions.forEach(v => { v.state = 'approved'; });
    window.deskV1Nav('home');
  });
  await page.click('[data-needsyou-kind="video"][data-campaign-id="camp-1"]');
  await editor('Home video needs-you deep link', false);
  await page.evaluate(() => window.deskV1HomeGotoReview('camp-1', 'v-install-li'));
  await page.waitForSelector('.desk-v1-review'); await page.click('.desk-v1-back');
  await editor('Home review deep link / Back', false, true);
  assert.deepEqual(errors, []);
} finally { await browser.close(); }
