#!/usr/bin/env node
/**
 * Desk v1, `desk_v1_live` ON: deleting a draft from Home sticks across a reload.
 *
 * Ron 2026-10-30 ('FL3 V2'): a draft deleted from the Desk main screen came back
 * after reopening the Desk. The delete spliced the browser's own arrays and never
 * called the server, so the next GET /api/desk/workspace brought it back. Pinned
 * here against a fake server that keeps state between page loads:
 *
 *   1. Home ⋯ › Delete draft sends DELETE /api/desk/campaigns/<id>, and after a
 *      full page reload the draft is not on Home.
 *   2. The header Undo sends POST .../restore and the draft is back after a reload.
 *   3. A refused DELETE (500) puts the row back and says why on screen; a reload
 *      still shows it, because the server still holds it.
 *   4. Flag OFF (demo): the delete makes 0 /api/desk/* writes.
 *
 * Server half (DELETE, trash, restore): tests/test_desk_campaign_delete.py.
 *
 * RUN   cd tools/smoke && node desk-v1-delete-draft-live.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);
const DRAFT_ID = 'camp-draft-fl3v2';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

function makeServer() {
  const fx = loadFixtures();
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const base = campaigns.find((c) => c.projectId) || campaigns[0];
  const draft = JSON.parse(JSON.stringify(base));
  Object.assign(draft, { id: DRAFT_ID, state: 'draft', plan: { ...draft.plan, title: 'FL3 V2 campaign' } });
  campaigns.push(draft);
  // trash: what the real server keeps for Undo (mc/desk_campaign_trash.py).
  const srv = { log: [], next: {}, campaigns, trash: {}, fx };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), campaigns: srv.campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

async function route(page, srv, live) {
  await page.route('**/*', async (r) => {
    const req = r.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => r.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return r.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return r.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return r.abort();
    srv.log.push({ method, path });
    const key = `${method} ${path}`;
    if (srv.next[key]) { const [status, msg] = srv.next[key]; delete srv.next[key]; return J({ error: msg }, status); }
    if (path === '/api/desk/workspace' && method === 'GET') return J(srv.workspace());
    let m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'DELETE') {
      const i = srv.campaigns.findIndex((c) => c.id === m[1]);
      if (i < 0) return J({ error: 'campaign not found' }, 404);
      srv.trash[m[1]] = srv.campaigns.splice(i, 1)[0];
      return J({ ok: true });
    }
    m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/restore$/);
    if (m && method === 'POST') {
      const c = srv.trash[m[1]];
      if (!c) return J({ error: 'that campaign is not in the trash' }, 404);
      delete srv.trash[m[1]];
      srv.campaigns.push(c);
      return J(c);
    }
    if (method === 'GET') return J({});
    return r.abort();
  });
}

async function openHome(page) {
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-home-board', { timeout: 10000 });
}

const draftRow = (page) => page.evaluate((id) => !!document.querySelector(`.desk-v1-home-row[data-campaign-id="${id}"]`), DRAFT_ID);
const writes = (srv) => srv.log.filter((x) => x.method !== 'GET');

async function deleteFromHome(page) {
  await page.waitForSelector(`[data-draft-more="${DRAFT_ID}"]`, { timeout: 8000 });
  await page.click(`[data-draft-more="${DRAFT_ID}"]`);
  await page.waitForSelector('.desk-v1-camp-cardmenu [data-menu-delete-draft]', { timeout: 4000 });
  await page.click('.desk-v1-camp-cardmenu [data-menu-delete-draft]');
}

async function live(browser) {
  const srv = makeServer();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message || String(e)));
  await route(page, srv, true);
  await openHome(page);
  (await draftRow(page)) ? ok('the FL3 V2 draft is on Home') : fail('the draft row never rendered');

  // 1: delete, then reload.
  srv.log.length = 0;
  await deleteFromHome(page);
  await page.waitForFunction(() => !document.querySelector('.desk-v1-camp-cardmenu'), null, { timeout: 4000 });
  await page.waitForTimeout(300);
  const del = writes(srv).filter((x) => x.method === 'DELETE' && x.path === `/api/desk/campaigns/${DRAFT_ID}`);
  del.length === 1 ? ok('Delete draft sends DELETE /api/desk/campaigns/<id> once') : fail(`DELETE calls: ${JSON.stringify(writes(srv))}`);
  await openHome(page);
  !(await draftRow(page)) ? ok('after a full reload the deleted draft stays deleted') : fail('the deleted draft came back after a reload (FL3 V2 bug)');

  // 2: Undo restores on the server too. Delete again in this page, then Undo.
  if (srv.trash[DRAFT_ID]) { srv.campaigns.push(srv.trash[DRAFT_ID]); delete srv.trash[DRAFT_ID]; }
  await openHome(page);
  await deleteFromHome(page);
  await page.waitForFunction(() => /Deleted/.test(document.querySelector('#desk-v1-undo')?.title || ''), null, { timeout: 4000 });
  srv.log.length = 0;
  await page.click('#desk-v1-undo');
  await page.waitForSelector(`.desk-v1-home-row[data-campaign-id="${DRAFT_ID}"]`, { timeout: 4000 });
  await page.waitForTimeout(300);
  writes(srv).some((x) => x.method === 'POST' && x.path === `/api/desk/campaigns/${DRAFT_ID}/restore`)
    ? ok('Undo sends POST .../restore') : fail(`Undo writes: ${JSON.stringify(writes(srv))}`);
  await openHome(page);
  (await draftRow(page)) ? ok('after Undo and a reload the draft is back') : fail('Undo restored the row locally only');

  // 3: a refused DELETE is never silent.
  srv.next[`DELETE /api/desk/campaigns/${DRAFT_ID}`] = [500, 'disk full'];
  await deleteFromHome(page);
  await page.waitForFunction(() => /was not saved: disk full/.test(document.body.innerText), null, { timeout: 4000 })
    .then(() => ok('a refused DELETE says why on screen'), () => fail('a refused DELETE showed no error'));
  (await draftRow(page)) ? ok('a refused DELETE puts the row back') : fail('a refused DELETE left the row hidden');
  await openHome(page);
  (await draftRow(page)) ? ok('after the refusal and a reload the draft is still there (server held it)') : fail('draft missing after refused delete');

  errors.length ? fail('page errors: ' + errors.join(' | ')) : ok('no uncaught page errors');
  await ctx.close();
}

async function demo(browser) {
  const srv = makeServer();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  await installDemoFixtures(page);
  await page.addInitScript((id) => {
    const fx = window.DeskV1Fixtures;
    const base = fx.campaigns.find((c) => c.projectId) || fx.campaigns[0];
    fx.campaigns.push({ ...JSON.parse(JSON.stringify(base)), id, state: 'draft', plan: { ...base.plan, title: 'FL3 V2 campaign' } });
  }, DRAFT_ID);
  await route(page, srv, false);
  await openHome(page);
  srv.log.length = 0;
  await deleteFromHome(page);
  await page.waitForTimeout(300);
  const gone = !(await draftRow(page));
  (gone && writes(srv).length === 0) ? ok('flag OFF: the draft is deleted locally with 0 /api/desk writes')
    : fail(`flag OFF: gone=${gone} writes=${JSON.stringify(writes(srv))}`);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('[live]'); await live(browser);
  console.log('[demo]'); await demo(browser);
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
