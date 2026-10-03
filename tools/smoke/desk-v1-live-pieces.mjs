#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S4) — the ③ What stop and the piece page against a
 * fake server, `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * What is under test is which requests the browser makes and what it paints from
 * the answers (the routes themselves are pinned by tests/test_desk_pieces.py):
 *   1. Create   -> a tray click POSTs M14 with the client's id; a title edit PATCHes M15;
 *                  ✕ DELETEs M16; a refused create/delete rolls back and says why.
 *   2. Assets   -> an upload is multipart M21 and the thumbnail becomes the SERVER's
 *                  `src`; a library file attaches by `path`; a SECOND asset on the same
 *                  piece is kept; two pieces of one kind coexist; a refused attach is
 *                  rolled back; Undo DELETEs the asset.
 *   3. Material -> the library lists the server's FILES; a failed M22 read says so.
 *   4. Article  -> picking an existing article PATCHes title, word_count and source.
 *   5. Piece pg -> `＋ Add media` on the piece page is the same M21 call; the excerpt is
 *                  the piece's own `body`.
 *   6. Writer   -> Save to What moves the versions it changed through M18 (never
 *                  `approved`) and the draft status through M15; Undo reverses both.
 *   7. Flag OFF -> every one of those makes 0 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-live-pieces.mjs
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

// 1x1 gifs, one per "stored file", so a thumbnail can only come from the server's `src`.
const SRC_UPLOAD = 'data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA=#server-upload';
const SRC_LIB = (n) => `data:image/gif;base64,R0lGODlhAQABAAAAACwAAAAAAQABAAA=#server-lib-${n}`;

const LIBRARY = {
  image: [{
    id: 'image:Launch shots', title: 'Launch shots', files: 2, thumb: SRC_LIB('a'),
    items: [
      { id: 'desk/library/image/Launch shots/a.png', kind: 'image', title: 'a.png', path: 'desk/library/image/Launch shots/a.png', src: SRC_LIB('a') },
      { id: 'desk/library/image/Launch shots/b.png', kind: 'image', title: 'b.png', path: 'desk/library/image/Launch shots/b.png', src: SRC_LIB('b') },
    ],
  }],
  video: [{
    id: 'video:Demos', title: 'Demos', files: 1, thumb: null,
    items: [{ id: 'desk/library/video/Demos/run.mp4', kind: 'video', title: 'run.mp4', path: 'desk/library/video/Demos/run.mp4', src: null }],
  }],
};

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const pieces = fx.families.map((f) => JSON.parse(JSON.stringify(f)));
  const srv = { log: [], next: {}, pieces, fx, materialsFail: false };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, pieces: pieces.map((p) => JSON.parse(JSON.stringify(p))) });
  return srv;
}

async function newPage(browser, { live, srv }) {
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 950 } });
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
    srv.log.push({ method, path, search: url.search, body, multipart, raw: multipart ? raw : '' });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') {
      if (srv.materialsFail) return J({ error: 'the uploads directory is not wired' }, 503);
      return J({
        library: LIBRARY,
        articles: [{ id: 'art-server', title: 'Server article', words: 777, projectId: 'p1' }],
        online: { video: [], image: [] }, recent: [],
      });
    }
    if (path === '/api/desk/pieces' && method === 'POST') {
      const p = { id: body.id, campaignId: body.campaign_id, kind: body.kind, title: body.title, assets: [], versions: [] };
      srv.pieces.push(p);
      return J(p, 201);
    }
    let m = path.match(/^\/api\/desk\/pieces\/([^/]+)$/);
    if (m) {
      const i = srv.pieces.findIndex((x) => x.id === m[1]);
      if (i < 0) return J({ error: 'piece not found' }, 404);
      if (method === 'DELETE') { srv.pieces.splice(i, 1); return J({ ok: true }); }
      if (method === 'PATCH') {
        const p = srv.pieces[i];
        if ('title' in body) p.title = body.title;
        if ('word_count' in body) p.wordCount = body.word_count;
        if ('source' in body) p.source = body.source;
        if ('draft_status' in body) p.draft = body.draft_status ? { status: body.draft_status } : undefined;
        return J(p);
      }
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/versions\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      const v = p && (p.versions || []).find((x) => x.id === m[2]);
      if (!v) return J({ error: 'version not found' }, 404);
      if (['approved', 'scheduled', 'sending', 'submitted', 'verified_published'].includes(body.state)) return J({ error: 'approval is its own human action' }, 400);
      Object.assign(v, body);
      return J(p);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/assets$/);
    if (m && method === 'POST') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      if (!p) return J({ error: 'piece not found' }, 404);
      let asset;
      if (multipart) {
        const id = (raw.match(/name="id"\r\n\r\n([^\r]+)/) || [])[1];
        const file = (raw.match(/name="file"; filename="([^"]+)"/) || [])[1];
        asset = { id, kind: 'image', title: file, path: 'desk/library/image/Uploads/' + file, src: SRC_UPLOAD };
      } else {
        const it = [...LIBRARY.image, ...LIBRARY.video].flatMap((f) => f.items).find((x) => x.path === body.path);
        asset = { id: body.id, kind: it.kind, title: body.title || it.title, path: it.path, src: it.src };
      }
      p.assets = (p.assets || []).concat([asset]);
      return J(p, 201);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/assets\/([^/]+)$/);
    if (m && method === 'DELETE') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      if (!p) return J({ error: 'piece not found' }, 404);
      p.assets = (p.assets || []).filter((a) => a.id !== m[2]);
      return J(p);
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
const calls = (srv, method, pathRe) => srv.log.filter((r) => r.method === method && pathRe.test(r.path));
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const openWhat = async (page, campaignId = 'camp-1') => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), campaignId);
  await page.waitForSelector('[data-what]', { timeout: 8000 });
};
const famOf = (page, id) => page.evaluate((i) => JSON.parse(JSON.stringify(window.DeskV1Store.state().families.find((f) => f.id === i) || null)), id);
const newFamilyIds = (page, before) => page.evaluate((b) => window.DeskV1Store.state().families.filter((f) => !b.includes(f.id)).map((f) => f.id), before);
const allIds = (page) => page.evaluate(() => window.DeskV1Store.state().families.map((f) => f.id));

// Tray click -> a create-card for a new piece of `type`; resolves the new piece's id.
async function addPiece(page, type) {
  const before = await allIds(page);
  await page.click(`[data-what-type="${type}"]`);
  await settle(page, (b) => window.DeskV1Store.state().families.some((f) => !b.includes(f.id)), before);
  return (await newFamilyIds(page, before))[0];
}

// ── 1: create / rename / remove ────────────────────────────────────────────
async function createRenameRemove(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(page);
  srv.log.length = 0;

  const id1 = await addPiece(page, 'post');
  await page.waitForFunction(() => document.querySelectorAll('[data-what-create]').length === 1);
  const post = calls(srv, 'POST', /^\/api\/desk\/pieces$/);
  (post.length === 1 && post[0].body.id === id1 && post[0].body.campaign_id === 'camp-1' && post[0].body.kind === 'post' && post[0].body.title === 'New post')
    ? ok(`a tray click POSTs M14 with the client's id, campaign, kind and title (${id1})`) : fail('create POST: ' + JSON.stringify(post.map((r) => r.body)));

  // Several of one kind are allowed (Ron 2026-09-29): a second post is a second piece.
  const id2 = await addPiece(page, 'post');
  const postsOnServer = srv.pieces.filter((p) => p.campaignId === 'camp-1' && p.kind === 'post' && [id1, id2].includes(p.id));
  (id1 !== id2 && postsOnServer.length === 2)
    ? ok('two pieces of the same kind in one campaign are two server pieces') : fail('same-kind pieces: ' + JSON.stringify(postsOnServer.map((p) => p.id)));

  // Rename through the card's title field.
  const input = page.locator(`[data-what-create][data-family-id="${id1}"] [data-what-title]`);
  await input.fill('Launch thread');
  await input.press('Tab');
  await settle(page, (i) => (window.DeskV1Store.state().families.find((f) => f.id === i) || {}).title === 'Launch thread', id1);
  const patch = calls(srv, 'PATCH', new RegExp(`^/api/desk/pieces/${id1}$`));
  (patch.length === 1 && patch[0].body.title === 'Launch thread' && srv.pieces.find((p) => p.id === id1).title === 'Launch thread')
    ? ok('a title edit PATCHes M15 {title} and the server holds it') : fail('rename PATCH: ' + JSON.stringify(patch.map((r) => r.body)));

  // ✕ removes the piece on the server; Undo puts it back.
  srv.log.length = 0;
  await page.click(`[data-what-create][data-family-id="${id2}"] [data-what-remove]`);
  await settle(page, (i) => !window.DeskV1Store.state().families.some((f) => f.id === i), id2);
  const del = calls(srv, 'DELETE', new RegExp(`^/api/desk/pieces/${id2}$`));
  (del.length === 1 && !srv.pieces.some((p) => p.id === id2)) ? ok('✕ DELETEs M16 and the server no longer has the piece') : fail('delete: ' + JSON.stringify(srv.log));

  // A refused delete rolls the card back and says why.
  srv.next[`DELETE /api/desk/pieces/${id1}`] = 'this piece has a version that is sending or already sent';
  await page.click(`[data-what-create][data-family-id="${id1}"] [data-what-remove]`);
  await page.waitForFunction(() => /was not saved: this piece has a version that is sending or already sent/.test(document.body.innerText), null, { timeout: 8000 });
  const back = await page.$(`[data-what-create][data-family-id="${id1}"]`);
  const still = await famOf(page, id1);
  (back && still) ? ok("a refused delete puts the card back and shows the server's reason") : fail('refused delete not rolled back');

  // A refused create never leaves a phantom piece on screen.
  const before = await allIds(page);
  srv.next['POST /api/desk/pieces'] = 'campaign not found';
  await page.click('[data-what-type="image"]');
  await page.waitForFunction(() => /was not saved: campaign not found/.test(document.body.innerText), null, { timeout: 8000 });
  const after = await allIds(page);
  (JSON.stringify(before) === JSON.stringify(after)) ? ok('a refused create leaves no piece behind') : fail('phantom piece after refused create');
  realErrors(pageErrors).length ? realErrors(pageErrors).forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 2: assets (several per piece), upload and library ──────────────────────
async function assets(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(page);

  // Upload: tray -> Image -> Upload -> a real file through the hidden input.
  const id = await addPiece(page, 'image');
  await page.click(`[data-what-create][data-family-id="${id}"] [data-what-source="upload"]`);
  await page.waitForSelector(`[data-what-create][data-family-id="${id}"] [data-what-libfile]`, { timeout: 8000 });
  const files = await page.$$eval(`[data-what-create][data-family-id="${id}"] [data-what-libfile]`, (els) => els.map((e) => e.dataset.whatLibfile));
  (files.length === 2 && files[0].endsWith('a.png'))
    ? ok(`the Upload body lists the server library's FILES (${files.length}), not the fixture folders`) : fail('library tiles: ' + JSON.stringify(files));
  const noFixtureFolders = await page.$$('[data-what-folder]');
  !noFixtureFolders.length ? ok('no fixture folder tile (data-what-folder) appears live') : fail('fixture folders leaked into live mode');

  srv.log.length = 0;
  await page.setInputFiles(`[data-what-create][data-family-id="${id}"] [data-what-file]`, { name: 'shot.png', mimeType: 'image/png', buffer: Buffer.from('not-really-a-png') });
  await settle(page, (i) => ((window.DeskV1Store.state().families.find((f) => f.id === i) || {}).assets || []).some((a) => a.path), id);
  const up = calls(srv, 'POST', new RegExp(`^/api/desk/pieces/${id}/assets$`));
  const fam1 = await famOf(page, id);
  (up.length === 1 && up[0].multipart && /filename="shot\.png"/.test(up[0].raw))
    ? ok('a file from this computer is one multipart M21 POST') : fail('upload POST: ' + JSON.stringify(up.map((r) => ({ m: r.multipart, raw: r.raw.slice(0, 80) }))));
  (fam1.assets.length === 1 && fam1.assets[0].src === SRC_UPLOAD && fam1.assets[0].path === 'desk/library/image/Uploads/shot.png' && /^asset-/.test(fam1.assets[0].id) && srv.pieces.find((p) => p.id === id).assets[0].id === fam1.assets[0].id)
    ? ok("the asset keeps the client's id and takes the SERVER's stored path and thumbnail URL") : fail('uploaded asset: ' + JSON.stringify(fam1.assets));
  const thumb = await page.$eval(`[data-what-row][data-family-id="${id}"] [data-asset-id]`, (e) => e.getAttribute('src')).catch(() => null);
  thumb === SRC_UPLOAD ? ok('the row paints that server src as its thumbnail') : fail('thumbnail src: ' + thumb);

  // A SECOND asset on the same piece, from the library, through ＋ Add media.
  srv.log.length = 0;
  await page.click(`[data-what-row][data-family-id="${id}"] [data-add-media]`);
  await page.waitForSelector('.desk-v1-addto-menu button', { timeout: 8000 });
  const labels = await page.$$eval('.desk-v1-addto-menu button', (els) => els.map((e) => e.textContent));
  (labels.length === 2 && labels[0] === 'Launch shots / a.png') ? ok(`＋ Add media lists library files: ${JSON.stringify(labels)}`) : fail('add-media menu: ' + JSON.stringify(labels));
  await page.click('.desk-v1-addto-menu button:nth-of-type(2)');
  await settle(page, (i) => ((window.DeskV1Store.state().families.find((f) => f.id === i) || {}).assets || []).length === 2, id);
  const lib = calls(srv, 'POST', new RegExp(`^/api/desk/pieces/${id}/assets$`));
  (lib.length === 1 && !lib[0].multipart && lib[0].body.path === 'desk/library/image/Launch shots/b.png')
    ? ok('a library file attaches as JSON {path} (M21), no copy of the file') : fail('library POST: ' + JSON.stringify(lib.map((r) => r.body)));
  const serverAssets = srv.pieces.find((p) => p.id === id).assets.map((a) => a.title);
  serverAssets.length === 2 ? ok(`one piece carries two assets on the server: ${JSON.stringify(serverAssets)}`) : fail('server assets: ' + JSON.stringify(serverAssets));

  // A refused attach is rolled back and says why; the first asset is untouched.
  srv.next[`POST /api/desk/pieces/${id}/assets`] = 'a piece can carry at most 50 assets';
  await page.click(`[data-what-row][data-family-id="${id}"] [data-add-media]`);
  await page.waitForSelector('.desk-v1-addto-menu button', { timeout: 8000 });
  await page.click('.desk-v1-addto-menu button:nth-of-type(1)');
  await page.waitForFunction(() => /was not saved: a piece can carry at most 50 assets/.test(document.body.innerText), null, { timeout: 8000 });
  const afterRefuse = await famOf(page, id);
  afterRefuse.assets.length === 2 ? ok('a refused attach is rolled back (2 assets remain) with the server text') : fail('after refusal: ' + afterRefuse.assets.length);

  // A source with no file behind it (a Studio capture/online/generate) is refused, not faked.
  const id3 = await addPiece(page, 'image');
  await page.click(`[data-what-create][data-family-id="${id3}"] [data-what-source="generate"]`);
  await page.waitForSelector(`[data-what-create][data-family-id="${id3}"] [data-what-change-source]`, { timeout: 8000 });
  srv.log.length = 0;
  await page.click(`[data-what-create][data-family-id="${id3}"] [data-gen-go]`);
  await page.waitForFunction(() => /was not saved: this source has not produced a file/.test(document.body.innerText), null, { timeout: 8000 });
  const tried = calls(srv, 'POST', new RegExp(`^/api/desk/pieces/${id3}/assets$`));
  const f3 = await famOf(page, id3);
  (tried.length === 0 && f3.assets.length === 0)
    ? ok('a generated image has no file: attaching it is refused with a toast, no request, nothing kept') : fail('generated asset: ' + JSON.stringify({ tried: tried.length, assets: f3.assets.length }));
  await ctx.close();
}

// ── 3: material read failure ───────────────────────────────────────────────
async function materialsFailure(browser) {
  const srv = makeServer();
  srv.materialsFail = true;
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(page);
  const id = await addPiece(page, 'video');
  await page.click(`[data-what-create][data-family-id="${id}"] [data-what-source="upload"]`);
  await page.waitForSelector('[data-what-lib-error]', { timeout: 8000 });
  const msg = await page.textContent('[data-what-lib-error]');
  /Could not load the material library: the uploads directory is not wired/.test(msg)
    ? ok(`a failed M22 read says so: "${msg.trim()}"`) : fail('materials error: ' + msg);
  const empty = await page.$('[data-what-lib-empty]');
  !empty ? ok('and it is not painted as an empty library') : fail('failure shown as empty');
  await ctx.close();
}

// ── 4: pick an existing article ────────────────────────────────────────────
async function pickArticle(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(page);
  const id = await addPiece(page, 'article');
  await page.click(`[data-what-create][data-family-id="${id}"] [data-what-source="browse"]`);
  await page.waitForSelector(`[data-what-create][data-family-id="${id}"] [data-what-existing]`, { timeout: 8000 });
  const listed = await page.$$eval(`[data-what-create][data-family-id="${id}"] [data-what-existing]`, (els) => els.map((e) => e.dataset.whatExisting));
  (listed.length === 1 && listed[0] === 'art-server') ? ok('Browse existing lists the server\'s articles, not the fixture\'s') : fail('existing articles: ' + JSON.stringify(listed));
  srv.log.length = 0;
  await page.click(`[data-what-create][data-family-id="${id}"] [data-what-existing="art-server"]`);
  await settle(page, (i) => (window.DeskV1Store.state().families.find((f) => f.id === i) || {}).title === 'Server article', id);
  const p = calls(srv, 'PATCH', new RegExp(`^/api/desk/pieces/${id}$`));
  (p.length === 1 && p[0].body.title === 'Server article' && p[0].body.word_count === 777 && p[0].body.source.kind === 'article' && p[0].body.source.ref === 'art-server')
    ? ok('picking an article PATCHes title, word_count and source {kind, ref}') : fail('pick PATCH: ' + JSON.stringify(p.map((r) => r.body)));
  await ctx.close();
}

// ── 5: the piece page ──────────────────────────────────────────────────────
async function piecePage(browser) {
  const srv = makeServer();
  srv.pieces.find((p) => p.id === 'fam-restore-points').body = 'The server-held text of this piece, shown as its excerpt.';
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(page);
  await page.click('[data-family-id="fam-restore-points"] [data-primary-action]');
  await page.waitForSelector('.desk-v1-piece', { timeout: 8000 });
  const ex = await page.textContent('[data-piece-excerpt]').catch(() => '');
  /server-held text/.test(ex) ? ok('the Copy excerpt is the piece\'s own body from M1') : fail('excerpt: ' + ex);

  // fam-restore-points is an article: no Add media there. Use the image piece.
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector('[data-what]', { timeout: 8000 });
  await page.click('[data-family-id="fam-where-board"] [data-primary-action]');
  await page.waitForSelector('.desk-v1-piece [data-add-media]', { timeout: 8000 });
  srv.log.length = 0;
  await page.click('.desk-v1-piece [data-add-media]');
  await page.waitForSelector('.desk-v1-addto-menu button', { timeout: 8000 });
  await page.click('.desk-v1-addto-menu button:nth-of-type(1)');
  await settle(page, () => (window.DeskV1Store.state().families.find((f) => f.id === 'fam-where-board').assets || []).length === 2);
  const lib = calls(srv, 'POST', /^\/api\/desk\/pieces\/fam-where-board\/assets$/);
  (lib.length === 1 && lib[0].body.path === 'desk/library/image/Launch shots/a.png')
    ? ok('＋ Add media on the piece page is the same M21 attach') : fail('piece-page attach: ' + JSON.stringify(lib.map((r) => r.body)));
  const thumbs = await page.$$eval('.desk-v1-piece [data-piece-media] [data-asset-id]', (els) => els.length);
  thumbs === 2 ? ok('the page now shows both assets (a piece carries more than one)') : fail('thumbs on page: ' + thumbs);
  await ctx.close();
}

// ── 6: writer Save to What -> in review ────────────────────────────────────
// Where (slice S5) places versions; the helper stands in for it on both sides.
async function writerPiece(page, srv) {
  const id = await addPiece(page, 'article');
  const ver = { id: 'v-writer-x', channelId: 'ch-x-ron', state: 'drafting', revision: 0 };
  srv.pieces.find((p) => p.id === id).versions = [{ ...ver }];
  await page.evaluate(([i, v]) => { window.DeskV1Store.state().families.find((f) => f.id === i).versions.push(v); }, [id, ver]);
  await page.click(`[data-what-create][data-family-id="${id}"] [data-what-source="write"]`);
  await page.waitForSelector('[data-writer-save]', { timeout: 8000 });
  return id;
}

async function writerSave(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(page);
  const id = await writerPiece(page, srv);
  srv.log.length = 0;
  await page.click('[data-writer-save]');
  await settle(page, () => !document.querySelector('[data-writer]'));
  await page.waitForTimeout(300);
  const vp = calls(srv, 'PATCH', new RegExp(`^/api/desk/pieces/${id}/versions/v-writer-x$`));
  const pp = calls(srv, 'PATCH', new RegExp(`^/api/desk/pieces/${id}$`));
  (vp.length === 1 && vp[0].body.state === 'needs_review' && Object.keys(vp[0].body).join() === 'state')
    ? ok('Save to What moves the placed version through M18 as {state: needs_review} and sends no body') : fail('version PATCH: ' + JSON.stringify(vp.map((r) => r.body)));
  (pp.length === 1 && pp[0].body.draft_status === 'in_review')
    ? ok('and the piece draft status through M15 {draft_status: in_review}') : fail('piece PATCH: ' + JSON.stringify(pp.map((r) => r.body)));
  const sp = srv.pieces.find((p) => p.id === id);
  (sp.versions[0].state === 'needs_review' && sp.draft && sp.draft.status === 'in_review') ? ok('the server holds both') : fail('server state: ' + JSON.stringify(sp));
  const approvals = srv.log.filter((r) => /approved|scheduled/.test(JSON.stringify(r.body || {})));
  !approvals.length ? ok('no request carried an approval state') : fail('an approval state was sent: ' + JSON.stringify(approvals));

  // Undo reverses both on the server.
  await page.click('#desk-v1-undo'); // quiet Undo (Ron 2026-10-01): the header button, not a toast
  await page.waitForTimeout(500);
  const sp2 = srv.pieces.find((p) => p.id === id);
  (sp2.versions[0].state === 'drafting' && (!sp2.draft || sp2.draft.status === 'drafting'))
    ? ok('Undo PATCHes the version back to drafting and the draft status back') : fail('after undo: ' + JSON.stringify(sp2));
  await ctx.close();

  // A refused save puts the local states back and says why.
  const srv2 = makeServer();
  const second = await newPage(browser, { live: true, srv: srv2 });
  await settle(second.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openWhat(second.page);
  const id2 = await writerPiece(second.page, srv2);
  srv2.next[`PATCH /api/desk/pieces/${id2}/versions/v-writer-x`] = 'this version is sending; it cannot be changed';
  await second.page.click('[data-writer-save]');
  await second.page.waitForFunction(() => /was not saved: this version is sending; it cannot be changed/.test(document.body.innerText), null, { timeout: 8000 });
  const local = await famOf(second.page, id2);
  (local.versions[0].state === 'drafting' && local.draft.status === 'drafting')
    ? ok("a refused save rolls the version and draft status back and shows the server's reason") : fail('after refused save: ' + JSON.stringify({ v: local.versions[0].state, d: local.draft }));
  await second.ctx.close();
}

// ── 7: flag OFF ────────────────────────────────────────────────────────────
async function demoCallsNothing(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => window.DeskV1Store.demo());
  await openWhat(page);
  srv.log.length = 0;
  const id = await addPiece(page, 'image');
  await page.click(`[data-what-create][data-family-id="${id}"] [data-what-source="upload"]`);
  await page.waitForSelector(`[data-what-create][data-family-id="${id}"] [data-what-folder]`, { timeout: 8000 });
  const folders = await page.$$eval('[data-what-folder]', (els) => els.length);
  folders === 3 ? ok('demo mode shows the fixture\'s 3 material folders') : fail('demo folders: ' + folders);
  await page.setInputFiles(`[data-what-create][data-family-id="${id}"] [data-what-file]`, { name: 'shot.png', mimeType: 'image/png', buffer: Buffer.from('x') });
  await settle(page, (i) => ((window.DeskV1Store.state().families.find((f) => f.id === i) || {}).assets || []).length === 1, id);
  const id2 = await addPiece(page, 'post');
  await page.fill(`[data-what-create][data-family-id="${id2}"] [data-what-title]`, 'Local only');
  await page.press(`[data-what-create][data-family-id="${id2}"] [data-what-title]`, 'Tab');
  await page.click(`[data-what-create][data-family-id="${id2}"] [data-what-remove]`);
  await page.waitForTimeout(300);
  srv.log.length === 0
    ? ok('flag OFF: create, upload, rename and remove made 0 /api/desk/* requests') : fail('demo mode called the server: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: create / rename / remove'); await createRenameRemove(browser);
  console.log('live ON: assets'); await assets(browser);
  console.log('live ON: materials failure'); await materialsFailure(browser);
  console.log('live ON: pick an article'); await pickArticle(browser);
  console.log('live ON: piece page'); await piecePage(browser);
  console.log('live ON: writer save'); await writerSave(browser);
  console.log('live OFF: demo'); await demoCallsNothing(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — S4 What + piece page call the piece routes live (create/rename/delete, multipart + library assets, article pick, writer save to review), read the server library, roll back refusals, never send an approval, and demo mode calls nothing.');
