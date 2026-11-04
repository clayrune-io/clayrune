#!/usr/bin/env node
// C3 browser contract: real shared skins, typing, account tabs and mobile layout.
// Real markup, modules, events, workspace reload and both viewport widths.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const files = loadStaticJsCss(root);
const index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const shots = resolve(root, '_scratch/composer_c3');
mkdirSync(shots, { recursive: true });

async function boot(browser, width, live = true) {
  const fx = loadFixtures();
  const campaign = fx.campaigns.find((c) => c.id === 'camp-1');
  // Use fixture identities instead of assuming names in the connected workspace.
  const x = fx.channels.find((c) => c.platform === 'x');
  const li = fx.channels.find((c) => c.platform === 'linkedin');
  campaign.plan.accounts = [x.id, li.id];
  const ctx = await browser.newContext({ viewport: { width, height: 950 }, hasTouch: width === 390, isMobile: width === 390 });
  const page = await ctx.newPage();
  page.setDefaultTimeout(7000);
  const errors = [], writes = [];
  const control = { fail: null, delay: 0 };
  page.on('pageerror', (e) => errors.push(e.message));
  if (!live) await installDemoFixtures(page);
  await page.route('**/*', async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const json = (v, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(v) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path.startsWith('/static/desk-icons/')) return route.fulfill({ contentType: path.endsWith('.svg') ? 'image/svg+xml' : 'image/png', body: readFileSync(resolve(root, path.slice(1))) });
    if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: live });
    if (path === '/api/projects') return json(fx.projects.map((p) => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [] })));
    if (path === '/api/characters') return json([]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
    if (path === '/api/desk/materials') return json({ library: {}, projects: [], articles: [] });
    if (path.startsWith('/api/desk/engagement/coverage/')) return json({ coverage: [] });
    if (path === '/api/desk/services') return json([]);
    if (path === '/api/desk/connect/services') return json({ services: [] });
    if (path.startsWith('/api/desk/') && method !== 'GET') {
      const body = req.postDataJSON();
      writes.push({ method, path, body });
      if (control.delay) await new Promise((r) => setTimeout(r, control.delay));
      if (control.fail && control.fail(path, body)) { control.fail = null; return json({ error: 'injected refusal' }, 409); }
      if (path === '/api/desk/pieces' && method === 'POST') {
        const f = { id: body.id, campaignId: body.campaign_id, kind: body.kind, title: body.title, body: body.body, assets: [], versions: [] };
        assert(!fx.families.some((p) => p.id === f.id), 'create id is unique');
        fx.families.push(f); return json(f);
      }
      const m = path.match(/^\/api\/desk\/pieces\/([^/]+)(?:\/versions(?:\/([^/]+))?)?$/);
      if (m) {
        const fam = fx.families.find((f) => f.id === m[1]);
        if (!fam) return json({ error: 'piece not found' }, 404);
        if (!path.includes('/versions')) { Object.assign(fam, body); return json(fam); }
        if (method === 'POST') {
          assert(!fam.versions.some((v) => v.channelId === body.account_id && !['archived', 'skipped'].includes(v.state)));
          fam.versions.push({ id: body.id, channelId: body.account_id, state: 'drafting', revision: 0 });
        } else {
          const v = fam.versions.find((v) => v.id === m[2]);
          if (body.scheduled_at) v.publishAt = body.scheduled_at;
          if (body.state) { v.state = body.state; if (body.state === 'needs_review') v.approved = null; }
        }
        return json(fam);
      }
      return json({ ok: true });
    }
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length);
  await page.evaluate(() => window.deskV1GotoCampaignPanel('what', { campaignId: 'camp-1' }));
  await page.waitForSelector('[data-what-type="post"]');
  return { ctx, page, fx, campaign, x, li, writes, errors, control };
}
const created = (s) => s.writes.filter((w) => w.method === 'POST' && w.path === '/api/desk/pieces');
const saved = (page) => page.waitForFunction(() => /^Saved/.test(document.querySelector('[data-composer-status]')?.textContent || ''));
const what = async (page) => { await page.evaluate(() => window.deskV1GotoCampaignPanel('what', { campaignId: 'camp-1' })); };
const open = async (page) => { await page.click('[data-what-type="post"]'); await page.waitForSelector('[data-composer]'); };

const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const s = await boot(browser, width);
    const { page, x, li } = s;
    await open(page);
    const tab = (id) => page.locator('[data-composer-preview-tab="' + id + '"]');
    const copy = page.locator('[data-composer-preview-stage] .desk-v1-pv-copy');
    const panel = page.locator('.desk-v1-composer-preview');
    assert.equal(await page.locator('[data-composer-preview-tab]').count(), 2);
    assert.equal(s.writes.length, 0, 'preview mounting creates no draft');
    const editorWidth = (await page.locator('[data-composer-text]').boundingBox()).width;
    if (width === 390) {
      assert.equal(await panel.isVisible(), false, 'mobile preview collapsed by default');
      await page.click('.desk-v1-composer-preview-toggle');
      assert.equal(await panel.isVisible(), true);
      assert.equal((await page.locator('[data-composer-text]').boundingBox()).width, editorWidth, 'preview never squeezes mobile editor');
      assert(editorWidth > 280);
    } else {
      const editor = await page.locator('[data-composer-text]').boundingBox();
      const preview = await panel.boundingBox();
      assert(preview.x > editor.x + editor.width, 'desktop side panel');
    }
    await page.fill('[data-composer-text]', 'Live before Save draft');
    await saved(page);
    assert.equal(await copy.textContent(), 'Live before Save draft');
    await page.fill('[data-composer-text]', '<b>literal copy</b> & \"quotes\"');
    assert.equal(await copy.textContent(), '<b>literal copy</b> & \"quotes\"', 'typed markup stays literal');
    assert.equal(await copy.locator('b').count(), 0, 'no HTML injection');
    // Two unsaved changes must update immediately, without waiting for a save.
    const many = Array.from({ length: 14 }, (_, i) => 'Line ' + i + ': a launch story with real copy.').join('\n');
    await page.fill('[data-composer-text]', many);
    assert.equal(await copy.textContent(), many);
    await tab(li.id).click();
    assert.equal(await page.locator('[data-pv-platform="linkedin"]').count(), 1);
    assert.equal(await page.locator('[data-pv-cut]').isVisible(), true, 'long copy shows feed cut line');
    await page.fill('[data-composer-text]', 'Short copy');
    assert.equal(await page.locator('[data-pv-cut]').isVisible(), false, 'short copy has no cut line');
    await page.fill('[data-composer-text]', many);
    await tab(x.id).click();
    await page.fill('[data-composer-text]', 'a'.repeat(280));
    assert.equal(await page.locator('.desk-v1-composer-preview-length[data-failed]').count(), 0);
    await page.fill('[data-composer-text]', 'a'.repeat(281));
    assert.match(await page.locator('.desk-v1-composer-preview-length[data-failed]').textContent(), /X 281\/280.*1 over limit/);
    await page.fill('[data-composer-text]', String.fromCodePoint(0x1f600).repeat(280));
    assert.match(await page.locator('.desk-v1-composer-preview-length').textContent(), /280\/280/);
    assert.equal(await page.locator('.desk-v1-composer-preview-length[data-failed]').count(), 0, 'Unicode counter agrees with editor');
    // A sibling media module updates fam assets and repaints its own host.
    await page.evaluate(() => {
      const fam = window.DeskV1Store.state().families.find((f) => f.body === 'Live before Save draft');
      fam.assets = [{ kind: 'image', src: 'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII=', title: 'Attached picture' }];
      document.querySelector('[data-composer-count]').append(document.createTextNode(' '));
    });
    await page.waitForSelector('[data-composer-preview-stage] .desk-v1-pv-media img');
    assert.equal(await page.locator('[data-composer-preview-stage] .desk-v1-pv-media img').getAttribute('alt'), 'Attached picture');
    await page.click('[data-composer-save]'); await saved(page);
    // Keyboard tab navigation uses one roving tab stop.
    await tab(x.id).focus(); await page.keyboard.press('ArrowRight');
    assert.equal(await tab(li.id).getAttribute('aria-selected'), 'true');
    assert.equal(await tab(li.id).getAttribute('tabindex'), '0');
    assert.equal(await tab(x.id).getAttribute('tabindex'), '-1');
    await page.evaluate(() => {
      window.DeskV1Store.state().families.find((f) => f.body?.startsWith(String.fromCodePoint(0x1f600))).assets = [];
      document.querySelector('[data-composer-count]').append(document.createTextNode(' '));
    });
    await page.waitForFunction(() => !document.querySelector('[data-composer-preview-stage] .desk-v1-pv-media'));
    await page.fill('[data-composer-text]', many);
    await page.locator('[data-composer-text]').evaluate((el) => { el.scrollTop = 0; el.blur(); });
    await page.locator('[data-composer]').evaluate((el) => el.parentElement.scrollTo(0, 0));
    if (width === 390) await panel.scrollIntoViewIfNeeded();
    await page.screenshot({ path: resolve(shots, 'composer-' + width + '.png'), fullPage: true });
    if (width === 390) {
      await page.click('.desk-v1-composer-preview-toggle');
      assert.equal(await panel.isVisible(), false);
      assert.equal(await page.inputValue('[data-composer-text]'), many, 'toggle keeps editor text');
      await page.locator('.desk-v1-composer-top').scrollIntoViewIfNeeded();
      await page.screenshot({ path: resolve(shots, 'composer-390-editor.png'), fullPage: true });
    }
    await page.click('[data-composer-channel="' + li.id + '"]');
    await saved(page);
    assert.equal(await tab(li.id).count(), 0, 'unticked account removed');
    assert.equal(await tab(x.id).getAttribute('aria-selected'), 'true', 'active tab falls back to remaining account');
    await page.click('[data-composer-channel="' + x.id + '"]');
    await saved(page);
    assert.equal(await page.locator('[data-composer-preview-tab]').count(), 0);
    assert.match(await page.locator('[data-composer-preview-stage]').textContent(), /Tick a campaign account/);
    await page.click('[data-composer-channel="' + li.id + '"]'); await saved(page);
    assert.equal(await tab(li.id).getAttribute('aria-selected'), 'true');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'no page overflow');
    assert.equal(s.writes.filter((w) => /publish|approve/.test(w.path)).length, 0);
    assert.deepEqual(s.errors, []);
    console.log('PASS composer preview ' + width + ': shared skins, unsaved typing, literal copy, tabs/keyboard, cut line, X limit, media, empty selection, layout');
    await s.ctx.close();
  }
} finally { await browser.close(); }
