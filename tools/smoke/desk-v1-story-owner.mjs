#!/usr/bin/env node
/**
 * MC-1179 DV1 — the story autosave must write to the storyboard it was typed in,
 * not to whichever storyboard is open when its timer fires.
 *
 * A save of video A's story is still in flight when the person types more and
 * opens video B. When the first save returns, the follow-up save is armed and
 * used to resolve its owner then: B's id and B's rev, with A's scenes and story.
 * Fake server, `desk_v1_live` ON; every other request is aborted.
 *
 * RUN   cd tools/smoke && node desk-v1-story-owner.mjs
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

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const srv = { log: [], boards: {}, fx, slowFirstPut: true };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, pieces: [] });
  srv.out = (key) => {
    const b = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '', story: '' };
    return { rev: b.rev, title: b.title, story: b.story, pending_edits: b.pending_edits, scenes: b.scenes };
  };
  return srv;
}

async function newPage(browser, srv) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
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
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    const m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
    if (m) {
      const key = (m[1] === 'pieces' ? 'piece:' : 'studio:') + m[2];
      if (method === 'GET') return J(srv.out(key));
      if (method === 'PUT') {
        // The first save is slow: the person types more and leaves while it is in flight.
        if (srv.slowFirstPut) { srv.slowFirstPut = false; await new Promise((r) => setTimeout(r, 1800)); }
        const cur = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '', story: '' };
        if (body.rev !== cur.rev) return J({ error: `changed since you loaded it (at revision ${cur.rev}, you had ${body.rev})` }, 409);
        srv.boards[key] = { rev: cur.rev + 1, title: body.title || cur.title || '', story: body.story || '', scenes: body.scenes, pending_edits: body.pending_edits || [] };
        return J(srv.out(key));
      }
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
const storyPuts = (srv) => srv.log.filter((r) => r.method === 'PUT' && /storyboard$/.test(r.path));
const ownerOf = (r) => r.path.match(/studio\/([^/]+)\/storyboard$/)[1];

async function newVideo(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  await page.waitForSelector('[data-sb-story]:not([disabled])', { timeout: 8000 });
}

const browser = await chromium.launch();
try {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv);
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);

  console.log('DV1 story autosave owner');
  await newVideo(page);                                  // video A
  await page.fill('[data-sb-story]', 'Story for A.');
  const t0 = Date.now();
  while (storyPuts(srv).length < 1 && Date.now() - t0 < 5000) await page.waitForTimeout(25);
  storyPuts(srv).length === 1 ? ok('the first autosave of A is in flight (the server is slow to answer)') : fail('first autosave never sent');
  const idA = storyPuts(srv).length ? ownerOf(storyPuts(srv)[0]) : null;

  await page.fill('[data-sb-story]', 'Story for A, with more typed while that save was in flight.');
  await page.evaluate(() => document.querySelector('[data-sb-story]').blur());
  await newVideo(page);                                  // the person opens video B

  await page.waitForTimeout(3500);                       // the first save returns, the follow-up is armed and fires
  const puts = storyPuts(srv);
  const owners = puts.map(ownerOf);
  const wrong = puts.filter((r) => ownerOf(r) !== idA);
  wrong.length === 0
    ? ok('every autosave of A\'s story went to A') : fail('A\'s story was saved to another storyboard: ' + JSON.stringify(wrong.map((r) => [ownerOf(r), r.body.story])));
  const lastA = [...puts].reverse().find((r) => ownerOf(r) === idA);
  (lastA && /with more typed/.test(lastA.body.story || ''))
    ? ok('A\'s later text reached A') : fail('A\'s later text never reached A: ' + JSON.stringify(owners));
  Object.keys(srv.boards).filter((k) => k !== 'studio:' + idA).length === 0
    ? ok('no other storyboard exists on the server') : fail('server boards: ' + JSON.stringify(Object.entries(srv.boards).map(([k, b]) => [k, b.story])));

  pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e)).forEach((e) => fail('page error: ' + e));
  await ctx.close();
} finally {
  await browser.close();
}
if (bad) { console.error(`\n✗ ${bad} check(s) failed`); process.exit(1); }
console.log('\nall checks passed');
