// MC-1183 / DV10: real campaign menu + store, hermetic HTTP wire assertions.
// Every request is intercepted; this cannot change a live workspace.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright';
import { loadStaticJsCss, deskLoaded } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const assets = loadStaticJsCss(root), origin = 'http://mc.smoke.test';
const fx = loadFixtures(), camp = fx.campaigns[0];
let family, calls = [], refuse = new Set(), pending, release;
let live = true, checks = 0;
const errors = [];
const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
page.setDefaultTimeout(5000);
page.on('pageerror', e => { errors.push(e.message); console.error('PAGE ERROR:', e.message); });
await installDemoFixtures(page);
await page.addInitScript(() => localStorage.setItem('walkthrough_done', '1'));
await page.route('**/*', async route => {
  const req = route.request(), path = new URL(req.url()).pathname;
  const json = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  if (path === '/') return route.fulfill({ contentType: 'text/html', body: readFileSync(resolve(root, 'static/index.html'), 'utf8') });
  if (assets[path]) return route.fulfill({ contentType: assets[path][0], body: assets[path][1] });
  if (path === '/api/projects' || path === '/api/characters') return json([]);
  if (path === '/api/config') return json({ desk_v1: true, desk_v1_live: live });
  if (path === '/api/desk/workspace') return json({ ...workspaceFromFixtures(fx), pieces: [family] });
  if (path.startsWith('/api/desk/') && req.method() !== 'GET') {
    const data = req.postDataJSON();
    calls.push({ method: req.method(), path, data });
    if (pending) { const wait = pending; pending = null; await wait; }
    const vid = decodeURIComponent(path.split('/').at(-1));
    if (refuse.has(vid)) return json({ error: 'this version is sending; it cannot be changed' }, 409);
    if (req.method() === 'POST' && /^\/api\/desk\/pieces\/[^/]+\/archive$/.test(path)) {
      // The whole-post archive route owned by DeskV1PieceLifecycle.
      family.archivedAt = '2026-11-16T00:00:00Z';
      family.versions.forEach(v => { if (!['verified_published', 'you_reported', 'failed'].includes(v.state)) v.state = 'archived'; });
      return json(family);
    }
    assert.equal(req.method(), 'PATCH', 'only per-version PATCH is permitted');
    assert.match(path, /^\/api\/desk\/pieces\/[^/]+\/versions\/[^/]+$/);
    assert(['skipped', 'archived', 'drafting', 'planned', 'needs_review'].includes(data.state), 'no approval or scheduling write');
    family.versions.find(v => v.id === vid).state = data.state;
    return json(family);
  }
  return route.abort();
});
const pass = message => { checks++; console.log('PASS ' + message); };
async function setup(states, kind = 'post', isLive = true) {
  live = isLive; calls = []; refuse = new Set();
  family = { id: 'dv10', campaignId: camp.id, kind, title: 'DV10 family',
    versions: states.map((state, i) => ({ id: 'v' + i, channelId: fx.channels[i % fx.channels.length].id, state, revision: 0 })) };
  await page.goto(origin, { waitUntil: 'domcontentloaded' });
  await deskLoaded(page);
  await page.waitForFunction(() => window.deskV1CampaignWhatBridge && window.DeskV1Store && window.DeskV1Kit);
  await page.evaluate(async ({ family, camp, live }) => {
    if (live) await window.DeskV1Store.load();
    else window.DeskV1Fixtures.families = [family];
    window.dv10family = window.DeskV1Store.state().families.find(f => f.id === family.id);
    window.dv10camp = camp;
    window.dv10toasts = [];
    const toast = window.DeskV1Kit.toast;
    window.DeskV1Kit.toast = (msg, ...args) => { window.dv10toasts.push(msg); return toast(msg, ...args); };
    const host = document.createElement('div'); host.id = 'dv10-host';
    host.style = 'position:fixed;top:120px;left:200px;z-index:999999';
    host.innerHTML = '<button id="dv10-trigger">Menu</button>'; document.body.appendChild(host);
  }, { family, camp, live });
}
async function menu(action) {
  await page.evaluate(() => window.deskV1CampaignWhatBridge.openCardMenu(document.querySelector('#dv10-trigger'), window.dv10family, window.dv10camp));
  if (action) await page.locator('[data-menu-' + action + ']').click();
}
const states = () => page.evaluate(() => window.dv10family.versions.map(v => v.state));
async function settled(expected) { await page.waitForFunction(expected => JSON.stringify(window.dv10family.versions.map(v => v.state)) === JSON.stringify(expected), expected); }
async function undo() { await page.evaluate(() => window.DeskV1Kit.commandBus.undoLast()); }
try {
  await setup(['scheduled', 'approved', 'needs_review', 'verified_published', 'failed', 'skipped', 'archived']);
  await menu('skip');
  await settled(['skipped', 'skipped', 'skipped', 'verified_published', 'failed', 'skipped', 'archived']);
  assert.equal(calls.length, 3, 'Skip must PATCH each non-terminal version on the server');
  assert(calls.every(c => c.data.state === 'skipped'));
  pass('Skip persists every non-terminal version and preserves terminal versions');
  await setup(['scheduled', 'approved']); await menu('skip'); await settled(['skipped', 'skipped']);
  assert.equal(await page.evaluate(() => window.DeskV1Kit.commandBus.history.length), 0);
  await page.waitForFunction(() => window.dv10toasts.some(t => t.includes('Undo')));
  pass('approved/scheduled disposal has no fake Undo or route to restore publishing');
  await page.reload(); await deskLoaded(page); await page.waitForFunction(() => window.DeskV1Store); await page.evaluate(() => window.DeskV1Store.load());
  assert.deepEqual(await page.evaluate(() => window.DeskV1Store.state().families[0].versions.map(v => v.state)), ['skipped', 'skipped']);
  pass('Skip survives workspace reload');

  await setup(['planned']);
  pending = new Promise(resolve => { release = resolve; });
  await menu('skip');
  assert.deepEqual(await states(), ['planned']);
  release(); await settled(['skipped']); await undo(); await settled(['planned']);
  assert.deepEqual(calls.map(c => c.data.state), ['skipped', 'planned']);
  pass('no local change before server success; Undo restores through PATCH');

  await setup(['planned']);
  pending = new Promise(resolve => { release = resolve; });
  await menu('skip'); await menu('skip');
  assert.equal(calls.length, 1);
  assert.deepEqual(await states(), ['planned']);
  release(); await settled(['skipped']);
  pass('repeated Skip while pending sends one request');

  await setup(['sending', 'planned']); refuse.add('v0');
  await menu('skip'); await settled(['sending', 'skipped']);
  assert.equal(calls.length, 2);
  assert(await page.evaluate(() => window.dv10toasts.some(t => t.includes('cannot be changed'))));
  pass('refused version stays unchanged with server error; remaining versions attempted');

  await setup(['drafting']); await menu('skip'); await settled(['skipped']);
  refuse.add('v0'); await undo();
  await page.waitForFunction(() => window.dv10toasts.some(t => t.includes('Could not undo')));
  assert.deepEqual(await states(), ['skipped']);
  pass('refused Undo preserves server and local skipped state');

  await setup(['scheduled', 'planned', 'verified_published']); await menu('archive');
  await settled(['archived', 'archived', 'verified_published']);
  await page.waitForFunction(() => !!window.dv10family.archivedAt);
  assert.equal(calls.length, 1, 'Archive is one whole-post request, not per-version PATCHes');
  assert.equal(calls[0].method, 'POST'); assert.match(calls[0].path, /\/pieces\/dv10\/archive$/);
  pass('Archive stays on the whole-post route, sets archivedAt, preserves published history');

  await setup(['planned'], 'article'); await menu();
  for (const action of ['move', 'dup']) {
    assert.equal(await page.locator('[data-menu-' + action + ']').isDisabled(), true);
    assert.match(await page.locator('[data-menu-' + action + ']').getAttribute('title'), /not connected/);
  }
  assert.equal(calls.length, 0); pass('live Move and non-post Duplicate disabled with not-connected reason');
  await setup(['planned']); await menu();
  assert.equal(await page.locator('[data-menu-dup]').isDisabled(), false);
  assert.equal(await page.locator('[data-menu-dup-campaign]').count(), 1);
  pass('connected post Duplicate retained');

  await setup(['planned'], 'article', false); await menu();
  assert.equal(await page.locator('[data-menu-move]').isDisabled(), false);
  assert.equal(await page.locator('[data-menu-dup]').isDisabled(), false);
  await page.locator('[data-menu-skip]').click(); await settled(['skipped']); await undo(); await settled(['planned']);
  assert.equal(calls.length, 0); pass('demo keeps local actions and Undo with no writes');
  assert.deepEqual(errors, []); pass('no browser runtime errors');
  console.log(`${checks} passed, 0 failed`);
} catch (e) {
  console.error(e); console.log(`${checks} passed, 1 failed`); process.exitCode = 1;
} finally { await browser.close(); }
