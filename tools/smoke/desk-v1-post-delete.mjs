#!/usr/bin/env node
// MC-1126: real piece page + confirm sheet + humanProofFetch, fake network only.
import assert from 'node:assert/strict';
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadStaticJsCss } from './_static.mjs';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const STATIC = loadStaticJsCss(ROOT);
const HTML = readFileSync(resolve(ROOT, 'static/index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const PROOF = 'fake-smoke-passcode';
const browser = await chromium.launch({headless: true});
const shots = resolve(ROOT, '_scratch/post-delete');
mkdirSync(shots, {recursive: true});

async function run(width) {
  const context = await browser.newContext({viewport: {width, height: 950}});
  const page = await context.newPage();
  const errors = [], calls = [];
  page.on('pageerror', e => errors.push(e.message));
  const ws = workspaceFromFixtures(loadFixtures());
  const receipt = {platform: 'x', post_id: '123', permalink: 'https://x.com/test/status/123', posted_at: '2026-10-01T01:00:00Z'};
  const piece = {id: 'p-delete', campaignId: 'camp-1', kind: 'post', title: 'Published example', body: 'A test post', assets: [], versions: [
    {id: 'v-x', channelId: 'ch-x', state: 'verified_published', receipt: {...receipt}, publishedAt: receipt.posted_at},
    {id: 'v-li', channelId: 'ch-li', state: 'verified_published', receipt: {...receipt, platform: 'linkedin'}},
    {id: 'v-submitted', channelId: 'ch-x', state: 'submitted', receipt: {...receipt}},
    {id: 'v-draft', channelId: 'ch-x', state: 'needs_review'},
  ]};
  ws.pieces = [piece];
  ws.accounts = [
    {id: 'ch-x', platform: 'x', label: 'Example X', identity: '@example', capability: 'direct'},
    {id: 'ch-li', platform: 'linkedin', label: 'Example LinkedIn', identity: 'Example', capability: 'direct'},
  ];
  let refusal = true;
  await page.route('**/*', async route => {
    const req = route.request(), path = new URL(req.url()).pathname;
    const J = (body, status = 200) => route.fulfill({status, contentType: 'application/json', body: JSON.stringify(body)});
    if (path === '/') return route.fulfill({status: 200, contentType: 'text/html', body: HTML});
    if (STATIC[path]) return route.fulfill({status: 200, contentType: STATIC[path][0], body: STATIC[path][1]});
    if (path === '/api/config') return J({desk_v1: true, desk_v1_live: true});
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({configured: true});
    if (path === '/api/projects') return J(ws.projects.map(p => ({...p, status: 'active', domain: 'general', emoji: 'T',
      description: '', summary: '', current_task: '', next_action: '', blocked: false, activity_log: [], backlog: [],
      project_path: '/smoke/' + p.id, last_updated: '2026-10-01T00:00:00Z', display_order: 0, roster: []})));
    if (path === '/api/desk/workspace') return J(ws);
    if (path === '/api/desk/pieces') return J(ws.pieces);
    if (path.endsWith('/delete-post')) {
      assert.equal(req.method(), 'POST');
      const body = req.postDataJSON();
      calls.push({path, body});
      assert.deepEqual(Object.keys(body), ['passcode']); // no client target or "by" overrides
      if (body.passcode !== PROOF) return J({error: 'bad_passcode'}, 403);
      if (path.includes('/v-li/')) return J({error: 'LinkedIn deletion is unsupported: no scoped refreshing adapter.', code: 'unsupported'}, 422);
      if (refusal) return J({error: 'X API HTTP 403: You cannot delete this post.'}, 502);
      piece.versions[0].deleted = {at: '2026-10-29T20:00:00Z', by: 'human'};
      return J(piece);
    }
    return route.abort(); // includes every actual platform host
  });
  await page.goto(ORIGIN + '/', {waitUntil: 'domcontentloaded'});
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row');
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForFunction(() => window.DeskV1Store.state().families.length === 1);
  await page.evaluate(() => window.deskV1Nav('piece', {familyId: 'p-delete', campaignId: 'camp-1'}));
  const button = page.locator('[data-post-delete="v-x"]');
  await button.waitFor();
  assert.equal(await page.locator('[data-post-delete]').count(), 2);
  assert.equal(await button.innerText(), 'Delete from X');

  await button.click();
  await page.locator('[data-confirm-decline]').click();
  assert.equal(calls.length, 0);
  assert.equal(await button.isEnabled(), true);

  const confirm = async (id = 'v-x') => {
    await page.locator(`[data-post-delete="${id}"]`).click();
    await page.locator('[data-confirm-accept]').click();
    await page.locator('input[id^="hp-passcode-"]').waitFor();
  };
  const submit = async value => {
    await page.locator('input[id^="hp-passcode-"]').fill(value);
    await page.locator('input[id^="hp-passcode-"]').press('Enter');
  };
  await confirm();
  await page.locator('[data-modal-id^="__human-proof-"]').getByRole('button', {name: 'Cancel', exact: true}).click();
  assert.equal(calls.length, 0);
  await page.waitForFunction(() => !document.querySelector('[data-post-delete="v-x"]').disabled);

  await confirm();
  await submit('wrong-proof');
  await page.getByText('Wrong dashboard passcode.', {exact: true}).waitFor();
  assert.equal(await page.locator('input[id^="hp-passcode-"]').inputValue(), '');
  await submit(PROOF);
  const status = page.locator('[data-post-delete-status="v-x"]');
  await status.filter({hasText: 'You cannot delete this post.'}).waitFor();
  assert.equal(await status.getAttribute('role'), 'alert');
  assert.equal(piece.versions[0].deleted, undefined);
  assert.equal(await button.isEnabled(), true);

  await confirm('v-li');
  await submit(PROOF);
  await page.locator('[data-post-delete-status="v-li"]').filter({hasText: 'unsupported'}).waitFor();

  refusal = false;
  await confirm();
  assert.equal(await page.locator('input[id^="hp-passcode-"]').inputValue(), '');
  await submit(PROOF);
  await page.waitForFunction(() => !document.querySelector('[data-post-delete="v-x"]'));
  assert.match(await page.locator('[data-version-row="v-x"]').innerText(), /Deleted from X.*by human/s);
  assert.deepEqual(piece.versions[0].receipt, receipt);
  assert.equal(piece.versions[0].state, 'verified_published');
  assert.equal(calls.filter(c => c.path.includes('/v-x/')).length, 3); // wrong proof + refusal + success
  assert.equal(await page.evaluate(() => {
    const original = window.DeskV1Store.live;
    window.DeskV1Store.live = () => false;
    const html = window.DeskV1PostDelete.html({state: 'verified_published', receipt: {platform: 'x'}}, {});
    window.DeskV1Store.live = original;
    return html;
  }), '');
  await page.screenshot({path: resolve(shots, `deleted-${width}.png`), fullPage: true});
  // Refresh from the server answer, not a local-only mutation.
  await page.evaluate(async () => {
    await window.DeskV1Store.load({force: true});
    window.deskV1PieceRepaint();
  });
  assert.equal(await page.locator('[data-post-delete="v-x"]').count(), 0);
  assert.deepEqual(errors, []);
  console.log(`PASS ${width}px: confirm/cancel, passcode/cancel/retype, refusal reason, LinkedIn unsupported, receipt retained, deletion survives reload, demo disabled`);
  await context.close();
}

try {
  await run(1440);
  await run(390);
} finally { await browser.close(); }
