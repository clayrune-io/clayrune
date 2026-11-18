#!/usr/bin/env node
/**
 * MC-1179 DV6 — live "Edit text": edits are keyed by paragraph position, and the
 * saved body drops an emptied paragraph, so after one emptied paragraph every
 * later save used the stale keys against the shorter list and deleted another
 * paragraph while the screen still showed it.
 *
 * Body A,B,C,D,E. Empty C (autosave stores A,B,D,E), then fix a typo in E.
 * Server must end with A,B,D,E-fixed. Then leave Edit mode: the screen must show
 * what the server holds. NO real network: every /api/desk/* call is answered here.
 *
 * RUN   cd tools/smoke && node desk-v1-review-edit-text.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss, deskLoaded } from './_static.mjs';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const STATIC = loadStaticJsCss(REPO_ROOT);
const ORIGIN = 'http://mc.smoke.test';

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const BODY = ['Alpha one.', 'Bravo two.', 'Charlie three.', 'Delta four.', 'Echo five.'].join('\n\n');

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const accounts = [{
    id: 'ch-x', platform: 'x', identity: '@ch-x', label: 'CH-X', capability: 'direct', voice: '', connected: true,
    publish: { ready: true, reason: null, secret: 'x.oauth-token', unattended_ok: true },
  }];
  const pieces = [{
    id: 'p-1', campaignId: 'camp-1', kind: 'post', title: 'Five paragraphs', body: BODY, assets: [],
    versions: [{ id: 'v-x', channelId: 'ch-x', state: 'needs_review', revision: 0, body: '', claimsState: {}, approved: null, failure: null, claims: [] }],
  }];
  const ws = workspaceFromFixtures(fx);
  const camp = JSON.parse(JSON.stringify(ws.campaigns.find((c) => c.id === 'camp-1')));
  const srv = { log: [], fx, pieces, projects };
  srv.workspace = () => ({ ...ws, projects, accounts, campaigns: [JSON.parse(JSON.stringify(camp))], pieces: JSON.parse(JSON.stringify(pieces)) });
  return srv;
}

async function newPage(browser, srv) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1400 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskV1ReviewPollMs = 60000; });
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/pieces' && method === 'GET') return J(JSON.parse(JSON.stringify(srv.pieces)));
    const m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      const v = p.versions.find((x) => x.id === m[2]);
      if ('body' in body) v.body = body.body;
      return J(p);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await deskLoaded(page);
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const patches = (srv) => srv.log.filter((r) => r.method === 'PATCH' && /\/versions\/v-x$/.test(r.path));
async function until(pred, ms = 6000) {
  const t0 = Date.now();
  while (!pred()) { if (Date.now() - t0 > ms) return false; await new Promise((r) => setTimeout(r, 25)); }
  return true;
}
const serverBody = (srv) => srv.pieces[0].versions[0].body;

const browser = await chromium.launch();
try {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('review', { campaignId: 'camp-1', versionId: 'v-x' }));
  await page.waitForSelector('.desk-v1-review');
  await page.click('[data-mode-btn="edit"]');

  console.log('DV6 edit text: empty a paragraph, then edit a later one');
  await page.locator('[data-edit-para="p2"]').fill('');
  (await until(() => patches(srv).length === 1)) ? ok('first autosave sent') : fail('first autosave never sent');
  serverBody(srv) === 'Alpha one.\n\nBravo two.\n\nDelta four.\n\nEcho five.'
    ? ok('emptying the 3rd paragraph removed only it') : fail('after emptying: ' + JSON.stringify(serverBody(srv)));
  await settle(page, () => (document.getElementById('desk-v1-review-savestatus') || {}).textContent === 'Saved');

  await page.locator('[data-edit-para="p4"]').fill('Echo five, fixed.');
  (await until(() => patches(srv).length === 2)) ? ok('second autosave sent') : fail('second autosave never sent');
  const want = 'Alpha one.\n\nBravo two.\n\nDelta four.\n\nEcho five, fixed.';
  serverBody(srv) === want
    ? ok('the typo fix landed and no other paragraph was lost') : fail('after the typo fix the server holds: ' + JSON.stringify(serverBody(srv)));

  // one more keystroke anywhere (the report's third state): must still be the same text.
  await page.locator('[data-edit-para="p4"]').fill('Echo five, fixed twice.');
  await until(() => patches(srv).length === 3);
  serverBody(srv) === 'Alpha one.\n\nBravo two.\n\nDelta four.\n\nEcho five, fixed twice.'
    ? ok('a further edit still touches only that paragraph') : fail('after a third edit the server holds: ' + JSON.stringify(serverBody(srv)));

  console.log('DV6 leaving Edit text shows what the server holds');
  await settle(page, () => (document.getElementById('desk-v1-review-savestatus') || {}).textContent === 'Saved');
  await page.click('[data-mode-btn="review"]');
  await page.waitForSelector('#desk-v1-review-article[data-mode="review"]');
  const shown = (await page.locator('#desk-v1-review-article p.desk-v1-review-p').allInnerTexts()).map((t) => t.trim());
  JSON.stringify(shown) === JSON.stringify(serverBody(srv).split('\n\n'))
    ? ok('the article shows the same paragraphs the server holds') : fail('screen: ' + JSON.stringify(shown) + ' server: ' + JSON.stringify(serverBody(srv)));

  pageErrors.length === 0 ? ok('no page errors') : fail('page errors: ' + pageErrors.join(' | '));
  await ctx.close();
} finally {
  await browser.close();
}
if (bad) { console.error(`\n✗ ${bad} check(s) failed`); process.exit(1); }
console.log('\nall checks passed');
