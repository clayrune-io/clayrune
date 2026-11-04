// Real SPA + atomic duplicate route + temporary filesystem. No live Desk writes.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { installDemoFixtures } from './desk-v1-fixture-api.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const SHOTS = resolve(ROOT, '_scratch/composer_c4');
mkdirSync(SHOTS, { recursive: true });
const register = async (pid, name, command) => {
  const r = await fetch('http://localhost:5199/api/processes/register', { method: 'POST',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pid, name, command, project_id: 'mission_control' }) });
  assert(r.ok, 'process registration failed: ' + await r.text());
};
await register(process.pid, 'Composer C4 smoke', 'node tools/smoke/desk-v1-composer-duplicate.mjs');
const python = process.env.MC_PYTHON || 'python';
const fixture = spawn(python, [resolve(ROOT, 'tools/smoke/_desk_composer_duplicate_server.py')], { cwd: ROOT, stdio: ['ignore', 'pipe', 'pipe'] });
await register(fixture.pid, 'Isolated composer duplicate fixture', python + ' tools/smoke/_desk_composer_duplicate_server.py');
let browser, origin, stderr = '';
fixture.stderr.on('data', (data) => { stderr += data; });
const state = async (page) => (await page.request.get(origin + '/__state')).json();
async function boot(page, campaignId = 'source') {
  await page.goto(origin);
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.desk-v1-shell');
  await what(page, campaignId);
}
async function what(page, campaignId = 'source') {
  await page.evaluate((campaignId) => window.deskV1GotoCampaignPanel('what', { campaignId }), campaignId);
  await page.waitForSelector('[data-what-type="post"]');
}
async function menu(page, id = 'original') {
  await page.locator(`[data-what-row][data-family-id="${id}"] [data-more-btn]`).click();
  await page.waitForSelector('[data-menu-dup-campaign]');
}
async function opened(page) {
  await page.waitForSelector('[data-composer]');
  return page.evaluate(() => window.DeskV1Store.state().families.find((f) =>
    f.id === document.querySelector('[data-composer]')?.closest('[data-family-id]')?.dataset.familyId)
    || window.DeskV1Store.state().families.at(-1));
}
function fresh(post, source, campaign, channels) {
  assert.equal(post.campaign_id, campaign);
  assert.equal(post.body, source.body);
  assert.equal(post.draft_status, 'drafting');
  assert.deepEqual(post.assets.map((a) => a.path), source.assets.map((a) => a.path));
  assert(!post.assets.some((a) => source.assets.some((b) => a.id === b.id)));
  assert.deepEqual(post.versions.map((v) => v.account_id), channels);
  assert(post.versions.every((v) => v.state === 'drafting' && !v.approved && !v.scheduled_at && !v.receipt && v.revision === 0));
}
try {
  origin = await new Promise((done, reject) => {
    let output = '';
    fixture.stdout.on('data', (data) => { output += data; const m = output.match(/\{"port":\s*(\d+)\}/); if (m) done('http://127.0.0.1:' + m[1]); });
    fixture.once('exit', (code) => reject(new Error('fixture exited ' + code + ': ' + stderr)));
    setTimeout(() => reject(new Error('fixture timeout: ' + stderr)), 15000).unref();
  });
  browser = await chromium.launch({ headless: true });
  for (const width of [1440, 390]) {
    const context = await browser.newContext({ viewport: { width, height: 950 }, isMobile: width === 390, hasTouch: width === 390 });
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const errors = [], mutations = [];
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('request', (r) => { if (r.method() !== 'GET' && new URL(r.url()).pathname.startsWith('/api/desk/')) mutations.push(new URL(r.url()).pathname); });
    await boot(page);
    const initial = await state(page), source = initial.pieces.original;
    await menu(page);
    await page.screenshot({ path: resolve(SHOTS, `menu-${width}.png`), fullPage: true });
    await page.evaluate(() => { const b = document.querySelector('[data-menu-dup]'); b.click(); b.click(); });
    const copy = await opened(page);
    const after = await state(page);
    assert.equal(Object.keys(after.pieces).length, Object.keys(initial.pieces).length + 1, 'double click creates one copy');
    fresh(after.pieces[copy.id], source, 'source', ['x', 'li']);
    assert.equal(await page.inputValue('[data-composer-text]'), source.body);
    assert.equal(await page.locator('[data-composer-text]').getAttribute('readonly'), null);
    assert.deepEqual(after.pieces.original, source);
    assert.deepEqual(after.campaigns, initial.campaigns);
    await page.screenshot({ path: resolve(SHOTS, `composer-${width}.png`), fullPage: true });
    assert(await page.locator('[data-composer]').evaluate((e) => e.scrollWidth <= e.clientWidth), 'composer fits');
    await boot(page);
    await page.locator(`[data-family-id="${copy.id}"] [data-primary-action]`).click();
    await page.waitForSelector('[data-composer]');
    assert.equal(await page.inputValue('[data-composer-text]'), source.body, 'reopens persisted text');
    await page.fill('[data-composer-text]', 'Independent copy ' + width);
    await page.click('[data-composer-save]');
    await page.waitForFunction(() => document.querySelector('[data-composer-status]')?.textContent === 'Saved');
    assert.equal((await state(page)).pieces.original.body, source.body);
    await what(page); await menu(page);
    const anchor = page.locator('[data-family-id="original"] [data-more-btn]');
    const beforePicker = await anchor.boundingBox();
    await page.click('[data-menu-dup-campaign]');
    const picker = page.locator('.desk-v1-addto-menu');
    await picker.waitFor();
    await page.screenshot({ path: resolve(SHOTS, `campaign-picker-${width}.png`), fullPage: true });
    const rect = await picker.boundingBox();
    assert(rect.x >= 0 && rect.x + rect.width <= width + 1, 'campaign picker fits');
    assert.deepEqual(await anchor.boundingBox(), beforePicker, 'picker does not move the card menu trigger');
    const beforeCancel = Object.keys((await state(page)).pieces).length;
    await page.keyboard.press('Escape');
    assert.equal(await picker.count(), 0, 'Escape dismisses campaign picker');
    assert.equal(Object.keys((await state(page)).pieces).length, beforeCancel);
    await menu(page); await page.click('[data-menu-dup-campaign]');
    await picker.getByRole('menuitem', { name: "Ron's other campaign · Target project", exact: true }).click();
    const cross = await opened(page);
    fresh((await state(page)).pieces[cross.id], source, 'target', ['li']);
    assert.equal(await page.locator('[data-composer-channel]').count(), 1);
    assert.equal(await page.locator('[data-composer-channel][aria-pressed="true"]').count(), 1);
    await page.click('#desk-v1-undo');
    await page.waitForFunction(async (id) => !(await (await fetch('/__state')).json()).pieces[id], cross.id);
    assert.equal((await state(page)).pieces.original.body, source.body, 'Undo only deletes copy');
    await what(page); await menu(page); await page.click('[data-menu-dup-campaign]');
    await page.locator('.desk-v1-addto-menu').getByRole('menuitem', { name: 'No channels yet · Target project', exact: true }).click();
    const empty = await opened(page);
    fresh((await state(page)).pieces[empty.id], source, 'empty', []);
    assert(await page.locator('[data-composer-review]').isDisabled(), 'no implicit destination outside empty bound');
    await what(page);
    const count = Object.keys((await state(page)).pieces).length;
    await page.route('**/api/desk/pieces/original/duplicate', (route) => route.fulfill({ status: 409, contentType: 'application/json', body: '{"error":"duplicate refused"}' }));
    await menu(page); await page.click('[data-menu-dup]');
    await page.waitForFunction(() => document.body.textContent.includes('duplicate refused'));
    assert.equal(Object.keys((await state(page)).pieces).length, count);
    assert.equal(await page.locator('[data-composer]').count(), 0, 'refusal does not open a fake copy');
    assert.equal(await page.evaluate(() => window.DeskV1Store.state().families.length), count, 'refusal rolls back local copy');
    await page.unroute('**/api/desk/pieces/original/duplicate');
    await menu(page); await page.click('[data-menu-dup]'); await opened(page);
    assert(!mutations.some((p) => /approve|publish/.test(p)), 'no release routes');
    assert.deepEqual(errors, []);
    console.log(`PASS C4 ${width}: real persisted copy/media, double click, reload/edit, cross campaign bounds, Undo, empty bound, refusal/retry, layout`);
    await context.close();
  }
  const context = await browser.newContext({ viewport: { width: 390, height: 950 } });
  const page = await context.newPage();
  await installDemoFixtures(page);
  await page.route('**/api/config', (route) => route.fulfill({ contentType: 'application/json', body: '{"desk_v1":true,"desk_v1_live":false}' }));
  await page.addInitScript(() => {
    const fx = window.DeskV1Fixtures, camp = fx.campaigns[0], ch = fx.channels[0];
    camp.plan.accounts = [ch.id];
    fx.families.push({ id: 'demo-original', kind: 'post', campaignId: camp.id, title: 'Demo source', body: 'Demo body',
      assets: [{ id: 'old-asset', kind: 'image', path: 'demo.png' }], versions: [{ id: 'old-v', channelId: ch.id, state: 'scheduled', approved: {}, publishAt: '2030-01-01', revision: 4 }] });
  });
  let demoWrites = 0;
  page.on('request', (r) => { if (r.method() !== 'GET' && new URL(r.url()).pathname.startsWith('/api/desk/')) demoWrites++; });
  await page.goto(origin); await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social')); await page.waitForSelector('.desk-v1-shell');
  await what(page, await page.evaluate(() => window.DeskV1Fixtures.campaigns[0].id));
  await menu(page, 'demo-original'); await page.click('[data-menu-dup]');
  const demo = await opened(page);
  assert.equal(demo.body, 'Demo body'); assert.equal(demo.assets.length, 1);
  assert.equal(demo.versions[0].state, 'drafting'); assert(!demo.versions[0].publishAt && !demo.versions[0].approved);
  assert.equal(demoWrites, 0, 'demo never mutates API');
  console.log('PASS C4 demo local-only content copy and fresh state');
  await context.close();
} finally {
  if (browser) await browser.close();
  if (origin) await fetch(origin + '/__shutdown', { method: 'POST' });
  if (fixture.exitCode === null) await new Promise((done) => fixture.once('exit', done));
}
