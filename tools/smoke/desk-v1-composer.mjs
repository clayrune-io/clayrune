#!/usr/bin/env node
// C1 browser contract against a stateful fake of the existing piece routes.
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
const shots = resolve(root, '_scratch/composer_c1');
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
    assert.equal(created(s).length, 0, 'tile click creates no empty piece');
    assert.equal(await page.locator('[data-what-add-modal]').count(), 0);
    assert.equal(await page.locator('[data-composer-channel][aria-pressed="true"]').count(), 2);
    assert.equal(await page.locator('[data-composer-review]').isDisabled(), true);
    await what(page); await open(page);
    assert.equal(created(s).length, 0, 'leaving empty composer creates no piece');
    await page.fill('[data-composer-text]', '   ');
    assert.equal(created(s).length, 0, 'whitespace creates no piece');
    s.control.delay = 40;
    await page.fill('[data-composer-text]', 'First content');
    await page.fill('[data-composer-text]', "Draft updated while creation runs: Ron's post <script>");
    await saved(page);
    assert.equal(created(s).length, 1, 'only one create during typing');
    const id = created(s)[0].body.id;
    const fam = s.fx.families.find((f) => f.id === id);
    assert.equal(fam.versions.length, 2);
    assert.equal(fam.body, "Draft updated while creation runs: Ron's post <script>");
    s.control.delay = 0;
    await page.fill('[data-composer-text]', 'A'.repeat(281));
    assert.match(await page.locator('[data-composer-count]').innerText(), /281\/280.*over limit/);
    await page.click('[data-composer-save]'); await saved(page);
    await page.fill('[data-composer-text]', 'A small release, one thoughtful update at a time.');
    s.control.fail = (path, body) => path === `/api/desk/pieces/${id}` && !!body.body;
    await page.click('[data-composer-save]');
    await page.waitForFunction(() => document.querySelector('[data-composer-status]').textContent.includes('injected refusal'));
    assert.equal(await page.inputValue('[data-composer-text]'), 'A small release, one thoughtful update at a time.');
    assert.equal(fam.body.length, 281, 'failed text write leaves saved copy intact');
    await page.click('[data-composer-save]'); await saved(page);
    // Ticks mutate only versions; deselect/reselect doesn't duplicate live ones.
    await page.click(`[data-composer-channel="${li.id}"]`); await saved(page);
    assert.equal(fam.versions.filter((v) => v.state !== 'archived').length, 1);
    assert.deepEqual(s.campaign.plan.accounts, [x.id, li.id]);
    await page.click(`[data-composer-channel="${li.id}"]`); await saved(page);
    assert.equal(fam.versions.filter((v) => v.state !== 'archived').length, 2);
    assert.equal(await page.locator('[data-composer-channel]').count(), 2, 'outside-bound accounts unavailable');
    await page.click('[data-composer-when]');
    const v = fam.versions.find((v) => v.channelId === x.id && v.state !== 'archived');
    await page.fill(`[data-composer-time="${v.id}"]`, '2026-11-02T10:15');
    await page.click(`[data-composer-time-save="${v.id}"]`);
    await page.waitForFunction(() => document.querySelector('[data-composer-time-status]').textContent === 'Time saved');
    assert.equal(s.writes.findLast((w) => w.body.scheduled_at)?.body.scheduled_at, v.publishAt);
    const firstTime = v.publishAt;
    s.control.fail = (path, body) => !!body.scheduled_at;
    await page.fill(`[data-composer-time="${v.id}"]`, '2026-11-02T11:15');
    await page.click(`[data-composer-time-save="${v.id}"]`);
    await page.waitForFunction(() => document.querySelector('[data-composer-time-status]').textContent.includes('injected refusal'));
    assert.equal(v.publishAt, firstTime, 'schedule refusal rolls back the calendar field');
    await page.evaluate((vid) => {
      const fam = window.DeskV1Store.state().families.find((f) => f.versions.some((v) => v.id === vid));
      const v = fam.versions.find((v) => v.id === vid);
      v.state = 'scheduled'; v.approved = { at: v.publishAt }; // exercise the existing calendar confirmation
    }, v.id);
    page.once('dialog', (dialog) => dialog.dismiss());
    await page.fill(`[data-composer-time="${v.id}"]`, '2026-11-03T10:15');
    await page.click(`[data-composer-time-save="${v.id}"]`);
    await page.waitForFunction(() => document.querySelector('[data-composer-time-status]').textContent === 'Time unchanged');
    page.once('dialog', (dialog) => dialog.accept());
    await page.fill(`[data-composer-time="${v.id}"]`, '2026-11-03T10:15');
    await page.click(`[data-composer-time-save="${v.id}"]`);
    await page.waitForFunction(() => document.querySelector('[data-composer-time-status]').textContent === 'Time saved');
    assert.equal(v.state, 'needs_review', 'outside approved day withdraws approval');
    await page.click('[data-composer-when]');
    await page.screenshot({ path: resolve(shots, `composer-${width}.png`), fullPage: true });
    const overflow = await page.locator('[data-composer]').evaluate((e) => e.scrollWidth > e.clientWidth);
    assert(!overflow, `${width}: no composer horizontal overflow`);
    await page.click('[data-composer-review]');
    await page.waitForFunction(() => !document.querySelector('[data-composer]'));
    assert(fam.versions.filter((v) => v.state !== 'archived').every((v) => v.state === 'needs_review'));
    assert.equal(fam.draft_status, 'in_review');
    assert(!s.writes.some((w) => /approve|publish/.test(w.path)), 'human gates never called');
    await what(page);
    await page.click(`[data-what-row][data-family-id="${id}"] [data-primary-action]`);
    await page.waitForSelector('[data-composer]');
    assert.equal(await page.inputValue('[data-composer-text]'), fam.body, 'post card opens saved copy');
    await page.reload();
    await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
    await page.evaluate(() => window.sidebarNav('social'));
    await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length);
    await what(page);
    await page.click(`[data-what-row][data-family-id="${id}"] [data-primary-action]`);
    await page.waitForSelector('[data-composer]');
    assert.equal(await page.inputValue('[data-composer-text]'), fam.body, 'fresh workspace reopens same text');
    assert.deepEqual(s.errors, []);
    console.log(`PASS composer C1 ${width}: empty/create/race, text retry, bounded ticks, schedule gate, Review, reload, layout`);
    await s.ctx.close();
  }
  const s = await boot(browser, 1440);
  s.control.fail = (path) => path === '/api/desk/pieces';
  await open(s.page); await s.page.fill('[data-composer-text]', 'Retain this failed first draft');
  await s.page.waitForFunction(() => document.querySelector('[data-composer-status]').textContent.includes('injected refusal'));
  assert.equal(s.fx.families.filter((f) => f.body === 'Retain this failed first draft').length, 0);
  await s.page.click('[data-composer-save]'); await saved(s.page);
  assert.equal(s.fx.families.filter((f) => f.body === 'Retain this failed first draft').length, 1);
  console.log('PASS failed first-content create retains text and retries');
  await what(s.page); await open(s.page);
  s.control.fail = (path, body) => path.endsWith('/versions') && body.account_id === s.li.id;
  await s.page.fill('[data-composer-text]', 'Partial destination write');
  await s.page.waitForFunction(() => document.querySelector('[data-composer-status]').textContent.includes('injected refusal'));
  const partial = s.fx.families.find((f) => f.body === 'Partial destination write');
  assert.equal(partial.versions.length, 1, 'first destination remains saved on second refusal');
  await s.page.click('[data-composer-save]'); await saved(s.page);
  assert.equal(partial.versions.length, 2, 'retry creates only the missing destination');
  console.log('PASS partial destination failure retries without duplicating successful destinations');
  await s.ctx.close();
  const demo = await boot(browser, 390, false);
  await open(demo.page); await demo.page.fill('[data-composer-text]', 'Demo text only'); await saved(demo.page);
  assert.equal(demo.writes.length, 0, 'demo writes no API mutations');
  console.log('PASS demo draft remains local');
  await demo.ctx.close();
} finally { await browser.close(); }
