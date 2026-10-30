#!/usr/bin/env node
/**
 * Desk v1 — a piece's media strip: play/view a thumbnail, remove one from the post
 * (Ron 2026-10-29: "no option to view the video by pressing the triangle", "no way to
 * remove them from the post"). static/js/desk-v1-piece-media.js, drawn on BOTH the
 * ③ What rows and the piece page's Media section.
 *
 * Live (desk_v1_live ON) against a fake server, so the requests are asserted too:
 *   1. Tiles    -> a video is a real <video #t=0.1> frame with the ▶ overlay, every tile has
 *                  an open button and a ✕ with an aria-label; both surfaces draw the same.
 *   2. Open     -> a video opens the library player on its `play` URL and steps through the
 *                  post's videos; a picture opens the app image viewer; Esc closes and
 *                  focus returns to the tile; Enter on the focused tile opens it.
 *   3. Remove   -> the ✕ DELETEs .../assets/<id> and nothing else (the file stays), the tile
 *                  goes, focus lands on a neighbour; the header Undo POSTs the same id/path
 *                  back and the tile returns in its place.
 *   4. Refusal  -> a 409 keeps the asset and says why; an archived piece shows no ✕ at all.
 *   5. Demo     -> flag OFF: remove + Undo make 0 /api/desk/* requests.
 * Screenshots at 1440 and 390 go to _scratch/piece_media/.
 *
 * RUN   cd tools/smoke && node desk-v1-piece-media.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const SHOTS = resolve(REPO_ROOT, '_scratch', 'piece_media');
mkdirSync(SHOTS, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);
const PNG = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', 'base64');

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, pass, why) => (cond ? ok(pass) : fail(why || pass));

const PIECE = 'fam-30-testers';
const play = (n) => `/api/serve-file?inline=1&path=${encodeURIComponent('C:/uploads/desk/library/video/Clips/' + n)}`;
const pic = (n) => `/api/serve-image?path=${encodeURIComponent('C:/uploads/desk/library/image/Shots/' + n)}`;
const ASSETS = () => [
  { id: 'a-img1', kind: 'image', title: 'shot1.png', path: 'desk/library/image/Shots/shot1.png', src: pic('shot1.png'), play: null },
  { id: 'a-vid1', kind: 'video', title: 'intro.mp4', path: 'desk/library/video/Clips/intro.mp4', src: null, play: play('intro.mp4') },
  { id: 'a-vid2', kind: 'video', title: 'outro.mp4', path: 'desk/library/video/Clips/outro.mp4', src: null, play: play('outro.mp4') },
  { id: 'a-img2', kind: 'image', title: 'shot2.png', path: 'desk/library/image/Shots/shot2.png', src: pic('shot2.png'), play: null },
];

function makeServer() {
  const fx = loadFixtures();
  const pieces = fx.families.map((f) => JSON.parse(JSON.stringify(f)));
  pieces.find((p) => p.id === PIECE).assets = ASSETS();
  // Composer media editing is intentionally gated on approved/sent posts.
  // This suite exercises editable drafts; C2 separately asserts the lock.
  pieces.find((p) => p.id === PIECE).versions.forEach(v => { v.state = 'drafting'; v.approved = null; });
  const srv = { log: [], next: {}, pieces, fx };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx),
    projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })),
    pieces: pieces.map((p) => JSON.parse(JSON.stringify(p))) });
  return srv;
}

async function newPage(browser, { live, srv, width = 1440 }) {
  const ctx = await browser.newContext({ viewport: { width, height: width < 600 ? 844 : 950 }, hasTouch: width < 600 });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);
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
    if (path === '/api/serve-image') return route.fulfill({ status: 200, contentType: 'image/png', body: PNG });
    if (path === '/api/serve-file') return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(8) });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    let m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/assets$/);
    if (m && method === 'POST') {
      const p = srv.pieces.find((x) => x.id === m[1]);
      const was = ASSETS().find((a) => a.id === body.id);
      p.assets = (p.assets || []).concat([{ ...was, id: body.id, path: body.path, title: body.title }]);
      return J(p, 201);
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)\/assets\/([^/]+)$/);
    if (m && method === 'DELETE') {
      const p = srv.pieces.find((x) => x.id === m[1]);
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
const calls = (srv, method, re) => srv.log.filter((r) => r.method === method && re.test(r.path));
const shot = (page, name) => page.waitForTimeout(250).then(() => page.screenshot({ path: resolve(SHOTS, name) }));
const ids = (page, root) => page.$$eval(`${root} [data-pmed-open]`, (els) => els.map((e) => e.dataset.pmedOpen));

async function toWhat(page) {
  await page.evaluate(() => window.deskV1Nav('campaign', { campaignId: 'camp-1' }));
  await page.waitForSelector(`[data-what-row][data-family-id="${PIECE}"]`, { timeout: 8000 });
}
async function toPiece(page) {
  await toWhat(page);
  await page.click(`[data-family-id="${PIECE}"] [data-primary-action]`);
  await page.waitForSelector('[data-composer]');
  await page.waitForSelector('[data-composer-media-assets]');
}

// surface = '[data-what-row][data-family-id=…]' (What) or '.desk-v1-piece' (piece page)
async function exerciseSurface(page, srv, surface, tag) {
  const strip = `${surface} [data-pmed-fam="${PIECE}"]`;
  check((await ids(page, surface)).join() === 'a-img1,a-vid1,a-vid2,a-img2', `${tag} four tiles, in the post's order`);

  // 1. tiles
  const v = await page.$eval(`${strip} [data-pmed-open="a-vid1"]`, (b) => ({
    video: !!b.querySelector('video.desk-v1-what-thumb'), src: (b.querySelector('video') || {}).src || '',
    tri: !!b.querySelector('.desk-v1-pmed-play'), label: b.getAttribute('aria-label') }));
  check(v.video && /#t=0\.1$/.test(v.src) && /serve-file/.test(v.src), `${tag} a video tile is a real <video> frame (#t=0.1)`, `${tag} video tile: ${JSON.stringify(v)}`);
  check(v.tri && v.label === 'Play intro.mp4', `${tag} the ▶ overlay stays and the tile is labelled "Play intro.mp4"`, `${tag} overlay/label: ${JSON.stringify(v)}`);
  const xs = await page.$$eval(`${surface} [data-pmed-remove]`, (els) => els.map((e) => e.getAttribute('aria-label')));
  check(xs.length === 4 && xs.every((l) => /^Remove .+ from this post$/.test(l)), `${tag} every tile has a ✕ with an aria-label`, `${tag} ✕ labels: ${JSON.stringify(xs)}`);

  // 2. open: video player, stepping, Esc, focus back
  await page.click(`${strip} [data-pmed-open="a-vid1"]`);
  await page.waitForSelector('[data-lv-player] [data-lv-video]', { timeout: 4000 });
  const s1 = await page.$eval('[data-lv-video]', (e) => decodeURIComponent(e.getAttribute('src')));
  check(/intro\.mp4/.test(s1) && /serve-file/.test(s1), `${tag} the triangle opens the player on that video's play URL`, `${tag} player src ${s1}`);
  check((await page.textContent('[data-lv-count]')).trim() === '1 / 2', `${tag} the player counts the post's videos`);
  await page.click('[data-lv-next]');
  check(/outro\.mp4/.test(decodeURIComponent(await page.$eval('[data-lv-video]', (e) => e.getAttribute('src')))), `${tag} next steps to the post's other video`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-lv-player]'));
  const back = await page.evaluate(() => document.activeElement && document.activeElement.dataset.pmedOpen);
  check(back === 'a-vid1', `${tag} Esc closes the player and focus returns to the tile`, `${tag} focus after Esc: ${back}`);

  // picture opens the app image viewer
  await page.click(`${strip} [data-pmed-open="a-img2"]`);
  await page.waitForSelector('.image-viewer-overlay', { timeout: 4000 });
  ok(`${tag} a picture opens the app image viewer`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('.image-viewer-overlay'));

  // What card: the big preview opens its lead asset in the same viewer
  if (tag === '[what]') {
    await page.click(`${surface} [data-wc-open="a-img1"]`);
    await page.waitForSelector('.image-viewer-overlay', { timeout: 4000 });
    ok(`${tag} the card's preview opens its lead picture in the viewer`);
    await page.keyboard.press('Escape');
    await settle(page, () => !document.querySelector('.image-viewer-overlay'));
  }

  // keyboard: Enter on the focused tile opens it
  await page.focus(`${strip} [data-pmed-open="a-vid2"]`);
  await page.keyboard.press('Enter');
  await page.waitForSelector('[data-lv-player]', { timeout: 4000 });
  check(/outro\.mp4/.test(decodeURIComponent(await page.$eval('[data-lv-video]', (e) => e.getAttribute('src')))), `${tag} Enter on a focused tile opens the viewer at that asset`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-lv-player]'));
}

async function removeAndUndo(page, srv, surface, tag) {
  const strip = `${surface} [data-pmed-fam="${PIECE}"]`;
  srv.log.length = 0;
  // reach the ✕ by keyboard: Tab from the tile's open button
  await page.focus(`${strip} [data-pmed-open="a-vid1"]`);
  await page.keyboard.press('Tab');
  const onX = await page.evaluate(() => document.activeElement && document.activeElement.dataset.pmedRemove);
  check(onX === 'a-vid1', `${tag} Tab from a tile lands on its own ✕`, `${tag} Tab landed on ${onX}`);
  await page.keyboard.press('Enter');
  await settle(page, (s) => document.querySelectorAll(`${s} [data-pmed-open]`).length === 3, surface);
  const del = calls(srv, 'DELETE', /\/assets\//);
  check(del.length === 1 && del[0].path === `/api/desk/pieces/${PIECE}/assets/a-vid1`, `${tag} ✕ DELETEs that one asset`, `${tag} DELETEs: ${JSON.stringify(del)}`);
  check(srv.log.filter((r) => r.method !== 'DELETE').length === 0, `${tag} and sends nothing else (no file delete)`, `${tag} extra requests: ${JSON.stringify(srv.log)}`);
  check((await ids(page, surface)).join() === 'a-img1,a-vid2,a-img2', `${tag} the tile is gone, the others stay`);
  const f = await page.evaluate(() => document.activeElement && document.activeElement.dataset.pmedOpen);
  check(f === 'a-vid2', `${tag} focus moves to the neighbouring tile`, `${tag} focus after remove: ${f}`);

  srv.log.length = 0;
  await page.click('#desk-v1-undo');
  await settle(page, (s) => document.querySelectorAll(`${s} [data-pmed-open]`).length === 4, surface);
  const post = calls(srv, 'POST', /\/assets$/);
  check(post.length === 1 && post[0].body.id === 'a-vid1' && post[0].body.path === 'desk/library/video/Clips/intro.mp4', `${tag} Undo re-attaches the same file under the same id`, `${tag} Undo POSTs: ${JSON.stringify(post)}`);
  check((await ids(page, surface)).join() === 'a-img1,a-vid1,a-vid2,a-img2', `${tag} the tile returns in its place`);
}

async function refusalAndArchived(page, srv, surface, tag) {
  srv.next[`DELETE /api/desk/pieces/${PIECE}/assets`] = 'restore this archived post before editing it';
  await page.click(`${surface} [data-pmed-remove="a-img1"]`);
  await page.waitForSelector('.desk-v1-toast, [data-toast], .toast', { timeout: 4000 }).catch(() => {});
  await settle(page, (s) => document.querySelectorAll(`${s} [data-pmed-open]`).length === 4, surface);
  const body = await page.evaluate(() => document.body.innerText);
  check(/restore this archived post before editing it/.test(body), `${tag} a refusal is shown with the server's own words`, `${tag} refusal text missing`);
  check((await ids(page, surface)).join() === 'a-img1,a-vid1,a-vid2,a-img2', `${tag} and the asset is still on the post (rolled back)`);
}

async function live(browser) {
  console.log('live: What rows');
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  await toWhat(page);
  const what = `[data-what-row][data-family-id="${PIECE}"]`;
  await exerciseSurface(page, srv, what, '[what]');
  await shot(page, 'what_1440.png');
  await removeAndUndo(page, srv, what, '[what]');
  await refusalAndArchived(page, srv, what, '[what]');

  console.log('live: piece page');
  await toPiece(page);
  await exerciseSurface(page, srv, '[data-composer]', '[composer]');
  await shot(page, 'piece_1440.png');
  await removeAndUndo(page, srv, '[data-composer]', '[composer]');
  await refusalAndArchived(page, srv, '[data-composer]', '[composer]');

  // archived: no ✕ at all (the server refuses the write)
  await page.evaluate((id) => { window.DeskV1Store.state().families.find((f) => f.id === id).archivedAt = '2026-10-29T00:00:00Z'; window.deskV1RenderComposer(document.querySelector('[data-composer]').parentElement, { campaignId: 'camp-1', familyId: id }); }, PIECE);
  await page.waitForTimeout(100);
  const open = await page.$$eval('[data-composer] [data-pmed-open]', (e) => e.length);
  const xs = await page.$$eval('[data-composer] [data-pmed-remove]', (e) => e.length);
  check(open === 4 && xs === 0, '[piece] an archived piece still opens its media but shows no ✕', `[piece] archived: ${open} tiles, ${xs} ✕`);
  const real = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  check(!real.length, 'no uncaught page error (live)', 'page errors: ' + real.join(' | '));
  await ctx.close();

  // phone width
  const srv2 = makeServer();
  const m = await newPage(browser, { live: true, srv: srv2, width: 390 });
  await m.page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  await toPiece(m.page);
  await shot(m.page, 'piece_390.png');
  const box = await m.page.$eval(`[data-composer] [data-pmed-remove="a-vid1"]`, (b) => { const r = b.getBoundingClientRect(); return { w: r.width, h: r.height }; });
  check(box.w === 24 && box.h === 24, `[390] the ✕ is a ${box.w}x${box.h}px circle with a larger tap area`, `[390] ✕ too small: ${box.w}x${box.h}`);
  await m.page.click('[data-composer] [data-pmed-open="a-vid1"]');
  await m.page.waitForSelector('[data-lv-player] [data-lv-video]', { timeout: 4000 });
  const over = await m.page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  check(over <= 0, '[390] the player has no horizontal overflow', `[390] overflow ${over}px`);
  await shot(m.page, 'player_390.png');
  await m.ctx.close();
}

async function demo(browser) {
  console.log('demo: flag off');
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await page.waitForFunction(() => window.DeskV1Store.state().campaigns.length > 0, null, { timeout: 8000 });
  await toWhat(page);
  const before = await ids(page, `[data-what-row][data-family-id="${PIECE}"]`);
  srv.log.length = 0;
  await page.click(`[data-what-row][data-family-id="${PIECE}"] [data-pmed-remove="${before[0]}"]`);
  const count = `[data-what-row][data-family-id="${PIECE}"] [data-pmed-open]`;
  await settle(page, ([sel, n]) => document.querySelectorAll(sel).length === n - 1, [count, before.length]);
  await page.click('#desk-v1-undo');
  await settle(page, ([sel, n]) => document.querySelectorAll(sel).length === n, [count, before.length]);
  check(srv.log.length === 0, 'demo mode: remove + Undo make 0 /api/desk/* requests', 'demo requests: ' + JSON.stringify(srv.log));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await live(browser);
  await demo(browser);
} finally {
  await browser.close();
}
if (bad) { console.error(`\n${bad} check(s) failed`); process.exit(1); }
console.log('\nall piece-media checks hold');
