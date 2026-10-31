#!/usr/bin/env node
// Real calendar/composer modules + stateful M18/version-create fake. No live data.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';
const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const files = loadStaticJsCss(root), index = readFileSync(resolve(root, 'static/index.html'), 'utf8');
const shots = resolve(root, '_scratch/when_calendar'); mkdirSync(shots, { recursive: true });

async function boot(browser, width) {
  const fx = loadFixtures(), camp = fx.campaigns.find((c) => c.id === 'camp-1');
  const x = fx.channels.find((c) => c.platform === 'x');
  const li = fx.channels.find((c) => c.platform === 'linkedin');
  x.health = li.health = 'ok';
  const held = { ...li, id: 'held', health: 'held', holdReason: 'Sign-in expired' };
  const yt = { ...li, id: 'yt', platform: 'youtube', label: 'YouTube', health: 'ok' };
  const outside = { ...x, id: 'outside', label: 'Other campaign account' };
  fx.channels = [x, li, held, yt, outside]; camp.plan.accounts = [x.id, li.id, held.id, yt.id];
  camp.when = { slots: [] };
  const make = (id, channelId, state = 'drafting', extra = {}) => ({ id, channelId, state, revision: 0, ...extra });
  const body = 'A calendar post with real copy. '.repeat(9);
  fx.families = [
    { id: 'multi', campaignId: camp.id, title: 'Two destinations, one post', kind: 'post', body, assets: [{ kind: 'image', src: '/assets/claydo-idle.webp', title: 'Attached picture' }], versions: [make('vx', x.id), make('vli', li.id)] },
    { id: 'add', campaignId: camp.id, title: 'Add a destination', kind: 'post', body: 'Short copy for this post', assets: [], versions: [make('add-x', x.id)] },
    { id: 'scheduled', campaignId: camp.id, title: 'Scheduled post', kind: 'post', body: 'This is already scheduled', assets: [], versions: [make('scheduled-x', x.id, 'scheduled', { publishAt: '2026-09-30T15:00:00-07:00', approved: { at: '2026-09-29T10:00:00Z' } })] },
    { id: 'review', campaignId: camp.id, title: 'Review me', kind: 'post', body: 'Ready to review', assets: [], versions: [make('review-x', x.id, 'needs_review', { publishAt: '2026-09-30T16:00:00-07:00' })] },
    { id: 'unplaced', campaignId: camp.id, title: 'No destinations yet', kind: 'post', body: 'Choose a channel', assets: [], versions: [] },
  ];
  const ctx = await browser.newContext({ viewport: { width, height: 1200 }, timezoneId: 'America/Los_Angeles', hasTouch: width === 390, isMobile: width === 390 });
  await ctx.clock.install({ time: new Date('2026-09-30T12:00:00-07:00') });
  const page = await ctx.newPage(); page.setDefaultTimeout(7000);
  const errors = [], writes = [], control = { fail: null };
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', async (route) => {
    const req = route.request(), path = new URL(req.url()).pathname, method = req.method();
    const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/') return route.fulfill({ contentType: 'text/html; charset=utf-8', body: index });
    if (files[path]) return route.fulfill({ contentType: files[path][0], body: files[path][1] });
    if (path.startsWith('/static/desk-icons/') || path.startsWith('/assets/')) {
      try { return route.fulfill({ contentType: path.endsWith('.svg') ? 'image/svg+xml' : 'image/webp', body: readFileSync(resolve(root, path.slice(1))) }); } catch { return route.abort(); }
    }
    if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: true });
    if (path === '/api/projects') return json(fx.projects.map((p) => ({ ...p, status: 'active', activity_log: [], backlog: [], roster: [] })));
    if (path === '/api/characters') return json([]);
    if (path === '/api/local-auth/status') return json({ configured: true });
    if (path === '/api/desk/workspace') return json(workspaceFromFixtures(fx));
    if (path === '/api/desk/materials') return json({ library: {}, projects: [], articles: [] });
    if (path.startsWith('/api/desk/engagement/coverage/')) return json({ coverage: [] });
    if (path === '/api/desk/services') return json([]);
    if (path === '/api/desk/connect/services') return json({ services: [] });
    if (path.startsWith('/api/desk/') && method !== 'GET') {
      const body = req.postDataJSON(); writes.push({ method, path, body });
      if (control.fail?.(path, body)) { control.fail = null; return json({ error: 'injected refusal' }, 409); }
      if (path === '/api/desk/pieces') {
        fx.families.push({ id: body.id, campaignId: body.campaign_id, title: body.title, body: body.body, kind: body.kind, assets: [], versions: [] });
        return json(fx.families.at(-1));
      }
      const m = path.match(/^\/api\/desk\/pieces\/([^/]+)(?:\/versions(?:\/([^/]+))?)?$/);
      if (m) {
        const fam = fx.families.find((f) => f.id === m[1]);
        if (!m[0].includes('/versions')) { Object.assign(fam, body); return json(fam); }
        if (method === 'POST') {
          assert(camp.plan.accounts.includes(body.account_id), 'destination stays in campaign');
          assert(!fam.versions.some((v) => v.channelId === body.account_id && !['archived', 'skipped'].includes(v.state)), 'no duplicate destinations');
          fam.versions.push(make(body.id, body.account_id));
        } else {
          const v = fam.versions.find((v) => v.id === m[2]);
          if ('scheduled_at' in body) { if (body.scheduled_at) v.publishAt = body.scheduled_at; else delete v.publishAt; }
          if ('state' in body) { assert(!['scheduled', 'approved'].includes(body.state)); v.state = body.state; v.approved = null; }
        }
        return json(fam);
      }
    }
    return route.abort();
  });
  await page.goto('http://mc.smoke.test/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length);
  const calendar = async () => page.evaluate(() => window.deskV1Nav('calendar', { campaignId: 'camp-1' }));
  await calendar();
  return { ctx, page, fx, camp, x, li, writes, errors, control, calendar };
}
async function drag(page, source, target, check) {
  await page.evaluate(() => document.querySelectorAll('.toast').forEach((e) => e.remove()));
  await page.locator(source).first().scrollIntoViewIfNeeded();
  const a = await page.locator(source).first().boundingBox(), b = await page.locator(target).first().boundingBox();
  assert(a && b, 'drag endpoints visible');
  await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2); await page.mouse.down();
  await page.mouse.move(a.x + a.width / 2 + 15, a.y + a.height / 2, { steps: 3 });
  await page.mouse.move(b.x + b.width / 2, b.y + b.height / 2, { steps: 8 });
  if (check) await check();
  await page.mouse.up();
}
const chip = (id) => `[data-chip-version="${id}"]:visible`;
const card = (id) => `[data-unsched-family="${id}"]`;
const row = (id) => `.desk-v1-cal-cell[data-channel-id="${id}"][data-day-key="2026-09-30"]`;
const until = async (fn) => { for (let i = 0; !fn(); i++) { assert(i < 160, 'write settled'); await new Promise((r) => setTimeout(r, 25)); } };
const browser = await chromium.launch({ headless: true });
try {
  for (const width of [1440, 390]) {
    const s = await boot(browser, width), { page, fx, x, li, writes } = s;
    assert.equal(await page.locator(card('multi')).count(), 1, 'one card for two versions');
    assert.equal(await page.locator(`${card('multi')} .desk-v1-channel-badge`).count(), 2, 'both platform badges');
    if (width === 1440) {
      await drag(page, card('multi'), row('held'), async () => {
        assert.equal(await page.locator(`${row('held')}.desk-v1-cal-target-refused`).count(), 1);
        assert.match(await page.locator(`${row('held')} .desk-v1-cal-cell-preview`).innerText(), /Sign-in expired/);
        await page.screenshot({ path: resolve(shots, 'refused_1440.png') });
      });
      await page.waitForTimeout(80); assert.equal(writes.length, 0, 'held row refuses drop');
      await drag(page, card('multi'), row('yt'), async () => assert.match(await page.locator(`${row('yt')} .desk-v1-cal-cell-preview`).innerText(), /exactly one video/));
      await page.waitForTimeout(80); assert.equal(writes.length, 0, 'incompatible media refuses drop');
      await drag(page, card('multi'), row(x.id));
      await until(() => writes.some((w) => w.path.endsWith('/vx')));
      await page.waitForSelector(chip('vx')); assert.equal(await page.locator(card('multi')).count(), 1, 'remaining channel still unscheduled');
      assert.equal(writes.filter((w) => w.method === 'POST').length, 0, 'existing target only reschedules');
      await page.click('#desk-v1-undo'); await page.waitForSelector(card('multi'));
      await until(() => writes.at(-1)?.body.scheduled_at === null);
      await drag(page, card('add'), row(li.id));
      await until(() => fx.families.find((f) => f.id === 'add').versions.some((v) => v.channelId === li.id && v.publishAt));
      const added = fx.families.find((f) => f.id === 'add').versions.find((v) => v.channelId === li.id);
      const post = writes.findIndex((w) => w.method === 'POST' && w.body.account_id === li.id);
      assert(post >= 0 && writes[post + 1].body.scheduled_at, 'same destination POST then scheduling PATCH');
      await page.click('#desk-v1-undo'); await until(() => !added.publishAt);
      await page.click('#desk-v1-undo'); await until(() => added.state === 'archived');
      // A failed destination POST rolls back and must never send a time PATCH.
      const before = writes.length;
      s.control.fail = (path) => path === '/api/desk/pieces/add/versions';
      await drag(page, card('add'), row(li.id));
      await until(() => writes.length > before);
      await page.waitForTimeout(80);
      assert.equal(writes.length, before + 1);
      assert.equal(await page.locator(`${card('add')} .desk-v1-channel-badge`).count(), 1);
      await page.click(chip('scheduled-x')); await page.waitForSelector('[data-cal-detail]');
      assert.equal(await page.locator('[data-cal-open-review]').count(), 0, 'scheduled detail has no Review');
      await page.screenshot({ path: resolve(shots, `detail_${width}.png`) });
      await page.fill('[data-kbd-when]', '2026-09-30T17:45'); await page.click('[data-kbd-move]');
      await until(() => fx.families.find((f) => f.id === 'scheduled').versions[0].publishAt === '2026-10-01T00:45:00.000Z');
      assert.equal(fx.families.find((f) => f.id === 'scheduled').versions[0].state, 'scheduled');
      s.control.fail = (path) => path.endsWith('/scheduled-x');
      await page.click(chip('scheduled-x')); await page.fill('[data-kbd-when]', '2026-09-30T18:00'); await page.click('[data-kbd-move]');
      await page.waitForFunction(() => document.querySelector('[data-cal-detail-status]')?.textContent.includes('injected refusal'));
      await page.click('[data-cal-detail-close]');
      await drag(page, chip('scheduled-x'), '[data-cal-unschedule-tray]');
      await until(() => !fx.families.find((f) => f.id === 'scheduled').versions[0].publishAt);
      await page.waitForSelector(card('scheduled'));
      await page.click('#desk-v1-undo'); await page.waitForSelector(chip('scheduled-x'));
      assert(writes.at(-1).body.scheduled_at, 'unschedule Undo restores persisted time');
      await page.screenshot({ path: resolve(shots, `calendar_${width}.png`) });
    }
    await page.screenshot({ path: resolve(shots, `calendar_${width}.png`) });
    const info = '[data-cal-preview-family="multi"] [data-cal-info]';
    if (width === 1440) { await page.hover(card('multi')); await page.waitForSelector('[data-cal-preview]'); }
    else { await page.tap(info); await page.waitForSelector('[data-cal-preview]'); }
    assert.match(await page.locator('[data-cal-preview]').innerText(), /Two destinations, one post/);
    assert.equal(await page.locator('[data-cal-preview] .desk-v1-pv-card').count(), 1, 'reuses post renderer');
    assert.equal(await page.locator('[data-cal-preview] img').count(), 1, 'attached media thumbnail');
    assert.equal(await page.locator('[data-cal-preview] .desk-v1-pv-copy').innerText(), bodyExcerpt(), '140-character copy excerpt');
    await page.screenshot({ path: resolve(shots, `preview_${width}.png`) });
    await page.click('[data-cal-preview-close]');
    await page.click(chip('review-x')); await page.waitForSelector('[data-cal-detail]');
    assert.equal(await page.locator('[data-cal-open-review]').count(), 1, 'needs-review gets Review link');
    await page.click('[data-cal-detail-close]');
    await page.locator(chip('scheduled-x')).focus(); await page.keyboard.press('Enter');
    await page.waitForSelector('[data-cal-detail]');
    await page.screenshot({ path: resolve(shots, `detail_keyboard_${width}.png`) });
    s.control.fail = (path) => path.endsWith('/scheduled-x');
    await page.click('[data-cal-unschedule]');
    await page.waitForFunction(() => document.querySelector('[data-cal-detail-status]')?.textContent.includes('injected refusal'));
    assert.equal(await page.locator(chip('scheduled-x')).count(), 1, 'refused unschedule restores the chip');
    await page.click('[data-cal-unschedule]'); await page.waitForSelector(card('scheduled'));
    await page.click(card('scheduled')); await page.waitForSelector('[data-cal-tray-picker]');
    await page.selectOption('[data-cal-tray-channel]', x.id); await page.fill('[data-cal-tray-time]', '2026-09-30T18:30');
    page.once('dialog', (d) => d.accept());
    await page.click('[data-cal-tray-save]'); await page.waitForSelector(chip('scheduled-x'));
    assert.equal(fx.families.find((f) => f.id === 'scheduled').versions[0].state, 'needs_review', 'new date after unschedule withdraws approval');
    // Phone keyboard/tap path can also create a destination for an unplaced post.
    await page.click(card('unplaced')); await page.waitForSelector('[data-cal-tray-picker]');
    await page.selectOption('[data-cal-tray-channel]', x.id); await page.fill('[data-cal-tray-time]', '2026-09-30T19:00');
    await page.click('[data-cal-tray-save]');
    await until(() => fx.families.find((f) => f.id === 'unplaced').versions.some((v) => v.publishAt));
    // Force a workspace reload: the new destination and timing came from saved responses.
    await page.evaluate(() => window.DeskV1Store.load({ force: true })); await s.calendar();
    assert.equal(await page.locator(card('unplaced')).count(), 0);
    // No account outside the family's campaign can be offered or accepted.
    assert.match(await page.evaluate(() => window.DeskV1CalendarTray.reason(window.DeskV1Store.state().families.find((f) => f.id === 'add'), 'outside')), /Outside/);
    await page.click(chip('scheduled-x')); await page.click('[data-cal-edit-post]');
    await page.waitForSelector('[data-composer-name] [data-piece-title-edit]');
    await page.click('[data-piece-title-edit]'); await page.fill('[data-piece-name-input]', 'Named from composer'); await page.keyboard.press('Enter');
    await until(() => fx.families.find((f) => f.id === 'scheduled').title === 'Named from composer');
    await page.waitForFunction(() => document.querySelector('[data-piece-title-edit]')?.textContent === 'Named from composer');
    await page.screenshot({ path: resolve(shots, `composer_${width}.png`) });
    await page.evaluate(() => window.DeskV1Composer.open(window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1')));
    assert.equal(await page.locator('[data-piece-title-edit]').count(), 0, 'no name editor before piece exists');
    await page.fill('[data-composer-text]', 'Brand new content');
    await page.waitForSelector('[data-composer-name] [data-piece-title-edit]');
    await page.click('[data-piece-title-edit]'); await page.fill('[data-piece-name-input]', 'Brand new named draft'); await page.keyboard.press('Enter');
    await until(() => fx.families.some((f) => f.title === 'Brand new named draft'));
    // Empty review retains the shell back control only.
    await page.evaluate(() => { window.DeskV1Store.state().families.forEach((f) => f.versions.forEach((v) => { v.state = 'drafting'; })); window.deskV1Nav('review', { campaignId: 'camp-1' }); });
    await page.waitForSelector('.desk-v1-review-empty');
    assert.equal(await page.locator('.desk-v1-review-empty button').count(), 0);
    assert.equal(await page.locator('.desk-v1-back').count(), 1);
    assert.deepEqual(s.errors, [], 'no runtime errors');
    console.log(`PASS ${width}: family cards/badges, info preview, details/keyboard, unschedule/Undo, bounded destination writes, composer Name, single Review back`);
    await s.ctx.close();
  }
} finally { await browser.close(); }

function bodyExcerpt() { return ('A calendar post with real copy. '.repeat(9)).slice(0, 140); }
