#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S9a = MC-1020) — storyboards persisted server-side, against
 * a fake server, `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * What is under test is which requests the browser makes and what it paints from
 * the answers (the store itself is pinned by tests/test_desk_storyboard.py):
 *   1. Create   -> Studio's New video: Add scene PUTs {rev, scenes, pending_edits, title}
 *                  and the next PUT carries the rev the server answered with.
 *   2. Picture  -> a scene's picture is a multipart POST to the library, then a PUT whose
 *                  scene carries {path,title}; the thumbnail is the SERVER's src.
 *   3. Reorder  -> Arrow Down on a scene's handle PUTs the list in its new order; Undo is
 *                  another PUT of the earlier order; Delete is a PUT without the scene.
 *   4. Reload   -> a fresh page finds the draft in Studio's Recent (GET list), opens it,
 *                  and shows the same scenes, order, picture and title.
 *   5. 409      -> a write against a stale rev is refused: the user is told the storyboard
 *                  changed elsewhere and the page shows the SERVER's list.
 *   6. Director -> the video piece's director loads the piece's board, inserts a scene with
 *                  a picture, reloads with it; the campaign storyboard reads the same board.
 *   7. Flag OFF -> Studio's storyboard and the director make 0 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-live-storyboard.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

const GIF = 'R0lGODlhAQABAAAAACwAAAAAAQABAAA=';
const SRC_PIC = (name) => `data:image/gif;base64,${GIF}#${name}`;
const PNG = { name: 'hero.png', mimeType: 'image/png', buffer: Buffer.from('not-really-a-png') };

// The fake server keeps ONE board per owner key, with the same rev rule as the real store.
function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const pieces = fx.families.map((f) => JSON.parse(JSON.stringify(f)));
  const srv = { log: [], boards: {}, fx, pieces, pics: {} };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, pieces: pieces.map((p) => JSON.parse(JSON.stringify(p))) });
  srv.out = (key) => {
    const b = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '' };
    return {
      rev: b.rev, title: b.title, pending_edits: b.pending_edits,
      scenes: b.scenes.map((s) => ({ ...s, picture: s.picture ? { ...s.picture, src: SRC_PIC(s.picture.path) } : null })),
    };
  };
  return srv;
}

async function newPage(browser, { live, srv, ctx: sharedCtx }) {
  const ctx = sharedCtx || await browser.newContext({ viewport: { width: 1400, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);   // demo mode is the harness's: the page ships no fixtures (S10)
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
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    const raw = req.postData() || '';
    try { body = req.postDataJSON(); } catch (_) { /* multipart or none */ }
    const multipart = /multipart\/form-data/.test(req.headers()['content-type'] || '');
    srv.log.push({ method, path, body, multipart, raw: multipart ? raw : '' });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') {
      return J({ storyboards: Object.entries(srv.boards).filter(([k]) => k.startsWith('studio:')).map(([k, b]) => ({ id: k.slice(7), title: b.title, scenes: b.scenes.length, updated_at: '2026-10-01T10:00:00Z' })) });
    }
    let m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
    if (m) {
      const key = (m[1] === 'pieces' ? 'piece:' : 'studio:') + m[2];
      if (method === 'GET') return J(srv.out(key));
      if (method === 'PUT') {
        const cur = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '' };
        if (body.rev !== cur.rev) return J({ error: `this storyboard changed since you loaded it (it is at revision ${cur.rev}, you had ${body.rev}): reload it, then make the change again`, problems: [`current_rev=${cur.rev}`] }, 409);
        srv.boards[key] = {
          rev: cur.rev + 1, title: m[1] === 'studio' ? (body.title || cur.title || '') : '',
          scenes: body.scenes.map((s) => ({ ...s })), pending_edits: body.pending_edits || [],
        };
        return J(srv.out(key));
      }
    }
    m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard\/pictures$/);
    if (m && method === 'POST') {
      const file = (raw.match(/name="file"; filename="([^"]+)"/) || [])[1] || 'x.png';
      const rel = 'desk/library/image/Storyboards/' + file;
      return J({ path: rel, kind: 'image', title: file, src: SRC_PIC(rel) }, 201);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const puts = (srv, re) => srv.log.filter((r) => r.method === 'PUT' && re.test(r.path));
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const sceneRows = (page) => page.$$eval('[data-storyboard] .desk-v1-sb-scene', (els) => els.map((e) => e.dataset.sceneLabel));
const thumbOf = (page, label) => page.$eval(`.desk-v1-sb-scene[data-scene-label="${label}"] .desk-v1-sb-thumb img`, (i) => i.getAttribute('src')).catch(() => null);
const toastText = (page) => page.textContent('.toast').catch(() => '');

async function studioNewVideo(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
}

async function addScene(page, label) {
  const before = (await sceneRows(page)).length;
  await page.click('[data-scene-add]');
  await settle(page, (n) => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === n + 1, before);
  const input = page.locator('[data-scene-edit-label]');
  await input.fill(label);
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-label]) [data-scene-edit]');
  await settle(page, (l) => !!document.querySelector(`.desk-v1-sb-scene[data-scene-label="${l}"]`), label);
}

// ── 1-5: Studio, standalone ────────────────────────────────────────────────
async function studio(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await studioNewVideo(page);

  const note = await page.$('[data-sb-no-scenes]');
  note ? ok('a live Studio storyboard starts empty and says so (no demo sample rows)') : fail('no empty-state note');
  (await sceneRows(page)).length === 0 ? ok('no example scenes are painted live') : fail('example scenes leaked into live mode');

  // 1. Add scene -> PUT with the rev 0 the page read.
  srv.log.length = 0;
  await page.click('[data-scene-add]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 1);
  await page.waitForTimeout(250);
  const first = puts(srv, /storyboard$/);
  (first.length === 1 && /^\/api\/desk\/studio\/studio-/.test(first[0].path) && first[0].body.rev === 0 && first[0].body.scenes.length === 1
    && first[0].body.scenes[0].duration_sec === 3 && first[0].body.scenes[0].picture === null && Array.isArray(first[0].body.pending_edits) && first[0].body.title === 'New video')
    ? ok('Add scene PUTs the whole list to the Studio item at rev 0 {rev, scenes, pending_edits, title}') : fail('first PUT: ' + JSON.stringify(first.map((r) => r.body)));
  const itemKey = Object.keys(srv.boards)[0];

  // Name it (edit) -> second PUT carries rev 1 (the one the server answered with).
  await page.fill('[data-scene-edit-label]', 'Opening');
  await page.click('[data-scene-edit]');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Opening"]'));
  await page.waitForTimeout(250);
  const second = puts(srv, /storyboard$/);
  (second.length === 2 && second[1].body.rev === 1 && second[1].body.scenes[0].label === 'Opening' && srv.boards[itemKey].rev === 2)
    ? ok('the next write carries the rev the server answered with (1 -> 2)') : fail('second PUT: ' + JSON.stringify(second.map((r) => [r.body.rev, r.body.scenes.map((s) => s.label)])));

  await addScene(page, 'Middle');
  await addScene(page, 'Closing');
  await page.waitForTimeout(250);
  JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)) === '["Opening","Middle","Closing"]'
    ? ok('three scenes persisted in array order') : fail('server order: ' + JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)));

  // 2. Picture: multipart POST to the library, then the PUT carries {path,title}.
  srv.log.length = 0;
  const chooserP = page.waitForEvent('filechooser');
  await page.click('.desk-v1-sb-scene[data-scene-label="Opening"] [data-scene-picture]');
  await (await chooserP).setFiles(PNG);
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Opening"] .desk-v1-sb-thumb img'));
  await page.waitForTimeout(250);
  const upl = srv.log.filter((r) => r.method === 'POST' && /storyboard\/pictures$/.test(r.path));
  const picPut = puts(srv, /storyboard$/).pop();
  const stored = srv.boards[itemKey].scenes[0].picture;
  (upl.length === 1 && upl[0].multipart && /filename="hero\.png"/.test(upl[0].raw) && picPut && picPut.body.scenes[0].picture
    && picPut.body.scenes[0].picture.path === 'desk/library/image/Storyboards/hero.png' && !('src' in picPut.body.scenes[0].picture) && stored && stored.path === picPut.body.scenes[0].picture.path)
    ? ok('a picture is one multipart upload, then a PUT with {path,title} only (src is never sent)') : fail('picture calls: ' + JSON.stringify({ upl: upl.length, put: picPut && picPut.body.scenes[0] }));
  (await thumbOf(page, 'Opening')) === SRC_PIC('desk/library/image/Storyboards/hero.png')
    ? ok("the scene's thumbnail is the SERVER's src") : fail('thumb: ' + (await thumbOf(page, 'Opening')));

  // 3. Reorder with Arrow Down on the handle; Undo re-PUTs the earlier order.
  srv.log.length = 0;
  await page.focus('.desk-v1-sb-scene[data-scene-label="Opening"] [data-scene-handle]');
  await page.keyboard.press('ArrowDown');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene')[1].dataset.sceneLabel === 'Opening');
  await page.waitForTimeout(250);
  JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)) === '["Middle","Opening","Closing"]'
    ? ok('Arrow Down on a handle PUTs the new order') : fail('after reorder: ' + JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)));
  // Quiet Undo (Ron 2026-10-01): a saved reorder raises no toast; the header Undo names it.
  const moveUndo = (await page.getAttribute('#desk-v1-undo', 'title')) || '';
  (!(await page.$('.toast')) && /Moved scene/.test(moveUndo)) ? ok(`a saved reorder raises no toast; the header Undo names it: "${moveUndo}"`) : fail(`reorder should be quiet with a header Undo: ${moveUndo}`);
  await page.click('#desk-v1-undo');
  await page.waitForTimeout(300);
  JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)) === '["Opening","Middle","Closing"]'
    ? ok('Undo is another PUT: the server has the earlier order again') : fail('after undo: ' + JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)));

  // Delete is a PUT without the scene.
  await page.click('.desk-v1-sb-scene[data-scene-label="Closing"] [data-scene-delete]');
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 2);
  await page.waitForTimeout(250);
  JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)) === '["Opening","Middle"]'
    ? ok('Delete PUTs the list without the scene') : fail('after delete: ' + JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)));

  // Title (Studio items keep one; Recent lists it).
  await page.fill('[data-sc-title]', 'Launch teaser');
  await page.press('[data-sc-title]', 'Tab');
  await page.waitForTimeout(300);
  srv.boards[itemKey].title === 'Launch teaser' ? ok('the title is saved with the storyboard') : fail('title: ' + srv.boards[itemKey].title);

  // 4. Reload: a FRESH page reads it back from the server.
  const page2 = await newPage(browser, { live: true, srv });
  await settle(page2.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page2.page.evaluate(() => window.deskV1Nav('studio', {}));
  await page2.page.waitForSelector(`[data-studio-recent-row="${itemKey.slice(7)}"]`, { timeout: 8000 });
  const rowText = await page2.page.textContent(`[data-studio-recent-row="${itemKey.slice(7)}"]`);
  /Launch teaser/.test(rowText) && /2 scenes/.test(rowText) ? ok(`a fresh page lists the draft in Studio's Recent: "${rowText.replace(/\s+/g, ' ').trim()}"`) : fail('recent row: ' + rowText);
  await page2.page.click(`[data-studio-recent-row="${itemKey.slice(7)}"]`);
  await page2.page.waitForSelector('[data-studio-create] [data-storyboard] .desk-v1-sb-scene', { timeout: 8000 });
  await page2.page.waitForFunction(() => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 2, null, { timeout: 8000 });
  const rows2 = await sceneRows(page2.page);
  (JSON.stringify(rows2) === '["Opening","Middle"]' && (await thumbOf(page2.page, 'Opening')) === SRC_PIC('desk/library/image/Storyboards/hero.png') && (await page2.page.inputValue('[data-sc-title]')) === 'Launch teaser')
    ? ok('after a reload the same scenes, order, picture and title are shown') : fail('reloaded: ' + JSON.stringify({ rows2, thumb: await thumbOf(page2.page, 'Opening') }));

  // 5. 409: the board moves on behind this page's back, then it saves.
  srv.boards[itemKey].rev += 5;
  srv.boards[itemKey].scenes.push({ id: 'sc-elsewhere', label: 'Added elsewhere', line: '', duration_sec: 3, picture: null, edited: false });
  await page2.page.click('.desk-v1-sb-scene[data-scene-label="Middle"] [data-scene-delete]');
  await page2.page.waitForFunction(() => /changed somewhere else/.test(document.body.innerText), null, { timeout: 8000 });
  await page2.page.waitForFunction(() => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Added elsewhere"]'), null, { timeout: 8000 });
  const rows3 = await sceneRows(page2.page);
  (JSON.stringify(rows3) === '["Opening","Middle","Added elsewhere"]' && srv.boards[itemKey].scenes.length === 3)
    ? ok('a stale rev is a 409: the user is told it changed elsewhere, the page shows the SERVER list, nothing was overwritten') : fail('after 409: ' + JSON.stringify(rows3) + ' server=' + srv.boards[itemKey].scenes.length);
  // ...and the next change works against the fresh rev.
  await page2.page.click('.desk-v1-sb-scene[data-scene-label="Middle"] [data-scene-delete]');
  await page2.page.waitForTimeout(400);
  JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)) === '["Opening","Added elsewhere"]'
    ? ok('after the refresh the same change succeeds') : fail('retry: ' + JSON.stringify(srv.boards[itemKey].scenes.map((s) => s.label)));

  realErrors(pageErrors).concat(realErrors(page2.pageErrors)).forEach((e) => fail('page error: ' + e));
  await ctx.close();
  await page2.ctx.close();
}

// ── 6: the director + the campaign storyboard, one board per piece ─────────
async function director(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
  await page.waitForSelector('[data-add-scene]', { timeout: 8000 });
  const gets = srv.log.filter((r) => r.method === 'GET' && r.path === '/api/desk/pieces/fam-install-video/storyboard');
  gets.length === 1 ? ok("the director reads the piece's board (GET) when it opens") : fail('director GETs: ' + gets.length);
  (await page.$$('.desk-v1-video-scene')).length === 0 ? ok('no fixture scenes are painted live') : fail('fixture scenes leaked');

  page.once('dialog', (d) => d.accept('Hero shot'));
  const chooserP = page.waitForEvent('filechooser');
  srv.log.length = 0;
  await page.click('[data-add-scene]');
  await (await chooserP).setFiles(PNG);
  await settle(page, () => document.querySelectorAll('.desk-v1-video-scene').length === 1);
  await page.waitForTimeout(300);
  const board = srv.boards['piece:fam-install-video'];
  (board && board.scenes.length === 1 && board.scenes[0].label === 'Hero shot' && board.scenes[0].picture && board.scenes[0].picture.path.endsWith('hero.png')
    && board.pending_edits.length === 1 && /Inserted/.test(board.pending_edits[0].label))
    ? ok('Add scene + a picture: one upload, one PUT; the scene, its picture and the pending edit are stored') : fail('director board: ' + JSON.stringify(board));
  const thumb = await page.$eval('.desk-v1-video-scene .desk-v1-video-scene-thumb img', (i) => i.getAttribute('src')).catch(() => null);
  thumb === SRC_PIC('desk/library/image/Storyboards/hero.png') ? ok("the strip tile paints the server's picture src") : fail('tile thumb: ' + thumb);

  // Reload the director: the scene AND the pending edit come back.
  const p2 = await newPage(browser, { live: true, srv });
  await settle(p2.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await p2.page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
  await p2.page.waitForSelector('.desk-v1-video-scene', { timeout: 8000 });
  const pending = await p2.page.textContent('.desk-v1-video-pending').catch(() => '');
  /Inserted/.test(pending) ? ok('after a reload the scene and its pending edit are still there') : fail('pending after reload: ' + pending);

  // The campaign storyboard reads the SAME board (one per piece).
  await p2.page.evaluate(() => window.deskV1Nav('storyboard', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
  await p2.page.waitForSelector('[data-storyboard] .desk-v1-sb-scene', { timeout: 8000 });
  const rows = await sceneRows(p2.page);
  (rows.length === 1 && rows[0] === 'Hero shot') ? ok('the campaign storyboard shows the same scene (one board per piece)') : fail('campaign storyboard rows: ' + JSON.stringify(rows));

  realErrors(pageErrors).concat(realErrors(p2.pageErrors)).forEach((e) => fail('page error: ' + e));
  await ctx.close();
  await p2.ctx.close();
}

// ── 7: flag OFF ────────────────────────────────────────────────────────────
async function flagOff(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: false, srv });
  await page.waitForFunction(() => !!window.DeskV1Fixtures, null, { timeout: 8000 });
  srv.log.length = 0;
  await studioNewVideo(page);
  const rows = await sceneRows(page);
  rows.length === 4 ? ok('demo Studio still opens the four sample scenes') : fail('demo rows: ' + rows.length);
  (await page.$('[data-scene-add]')) ? fail('demo storyboard grew an Add scene button') : ok('demo storyboard has no live-only controls');
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
  await page.waitForSelector('.desk-v1-video-director', { timeout: 8000 });
  (await page.$$('.desk-v1-video-scene')).length > 0 ? ok('demo director still shows the fixture scenes') : fail('demo director lost its scenes');
  const reqs = srv.log.filter((r) => r.path.startsWith('/api/desk/')).length;
  reqs === 0 ? ok('flag OFF: 0 /api/desk/* requests from Studio storyboard + director') : fail(`flag OFF made ${reqs} /api/desk requests: ` + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('Studio, standalone (create / picture / reorder / undo / reload / 409)');
  await studio(browser);
  console.log('Director + campaign storyboard');
  await director(browser);
  console.log('Flag OFF');
  await flagOff(browser);
} catch (e) {
  fail('smoke crashed: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-v1-live-storyboard checks passed');
process.exit(bad ? 1 : 0);
