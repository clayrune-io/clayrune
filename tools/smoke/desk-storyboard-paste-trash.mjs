#!/usr/bin/env node
/**
 * Desk storyboard (Ron, 2026-10-02): paste pictures, drag a timeline block to the bin,
 * and no Undo popup (the Undo lives on the header and beside the bin). Live mode, fake server.
 *
 *   1. Paste     -> a ClipboardEvent with an image puts it in the SELECTED scene (same multipart
 *                   upload + PUT as Add picture); no selection = a new scene; two images = one scene
 *                   each; pasted text and a paste into a text field are left alone.
 *   2. Button    -> a scene's Paste button reads navigator.clipboard; a refusal says so in one line.
 *   3. Trash     -> dragging a Timeline block onto the bin deletes the scene (mouse and a touch
 *                   long-press); the page raises NO toast, the Undo beside the bin is enabled with
 *                   'Undo: <label>', and a click on it restores the scene. The header Undo pulses.
 *   4. Reorder   -> a block dragged onto another block still reorders.
 *
 * RUN   cd tools/smoke && node desk-storyboard-paste-trash.mjs
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


// ── helpers for this smoke ─────────────────────────────────────────────────
const SHOT_DIR = process.env.SHOT_DIR || '';
const labels = (page) => sceneRows(page);
const tileSel = (id) => `[data-sb-timeline] .desk-v1-video-scene[data-scene-id="${id}"]`;
const idOf = (page, label) => page.$eval(`.desk-v1-sb-scene[data-scene-label="${label}"]`, (li) => li.dataset.sceneId);
const selectedLabels = (page) => page.$$eval('.desk-v1-sb-scene.desk-v1-sb-selected', (els) => els.map((e) => e.dataset.sceneLabel));
const uploads = (srv) => srv.log.filter((r) => r.method === 'POST' && /storyboard\/pictures$/.test(r.path));
const clearToasts = (page) => page.evaluate(() => document.querySelectorAll('.toast').forEach((e) => e.remove()));

// A paste the way a browser makes one: a ClipboardEvent on the focused node, carrying files and/or text.
function paste(page, { names = [], text = '', type = 'image/png', onSel = '' }) {
  return page.evaluate(({ names, text, type, onSel }) => {
    const dt = new DataTransfer();
    names.forEach((n) => dt.items.add(new File([new Uint8Array([137, 80, 78, 71])], n, { type })));
    if (text) dt.setData('text/plain', text);
    const target = onSel ? document.querySelector(onSel) : document.body;
    // returns true when the page took the paste over (preventDefault)
    return !target.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
  }, { names, text, type, onSel });
}

async function mouseDrag(page, fromSel, toSel) {
  await page.$eval(toSel, (e) => e.scrollIntoView({ block: 'nearest' }));
  const a = await (await page.$(fromSel)).boundingBox();
  const b = await (await page.$(toSel)).boundingBox();
  const ax = a.x + a.width / 2, ay = a.y + a.height / 2, bx = b.x + b.width / 2, by = b.y + b.height / 2;
  await page.mouse.move(ax, ay);
  await page.mouse.down();
  await page.mouse.move(ax + 14, ay + 14, { steps: 3 });
  await page.mouse.move(bx, by, { steps: 12 });
  await page.mouse.up();
}

// A real touch long-press drag: CDP touch events, so the page sees pointerType 'touch'.
async function touchDrag(ctx, page, fromSel, toSel) {
  const cdp = await ctx.newCDPSession(page);
  const a = await (await page.$(fromSel)).boundingBox();
  const b = await (await page.$(toSel)).boundingBox();
  const ax = a.x + a.width / 2, ay = a.y + a.height / 2, bx = b.x + b.width / 2, by = b.y + b.height / 2;
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: ax, y: ay }] });
  await page.waitForTimeout(600);           // past the 400ms long-press
  for (let i = 1; i <= 10; i++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: ax + (bx - ax) * i / 10, y: ay + (by - ay) * i / 10 }] });
    await page.waitForTimeout(16);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}

async function openStudio(browser, opts) {
  const srv = makeServer();
  const ctx = await browser.newContext(opts);
  const h = await newPage(browser, { live: true, srv, ctx });
  await settle(h.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await studioNewVideo(h.page);
  return { srv, ...h };
}

// ── 1-2, 4: paste + button + reorder + bin (desktop) ───────────────────────
async function desktop(browser) {
  const { srv, ctx, page, pageErrors } = await openStudio(browser, { viewport: { width: 1440, height: 900 } });
  for (const l of ['Opening', 'Middle', 'Closing']) await addScene(page, l);
  await page.waitForTimeout(250);
  const key = () => Object.keys(srv.boards)[0];

  const sel0 = await selectedLabels(page);
  const tileSel0 = await page.$$eval('[data-sb-timeline] .desk-v1-sb-selected', (n) => n.length);
  (sel0.length === 1 && sel0[0] === 'Closing' && tileSel0 === 1) ? ok('the scene just added is selected, on its row and its timeline block') : fail('selection after add: ' + JSON.stringify({ sel0, tileSel0 }));

  // 1a. select Opening by clicking its title; paste -> it takes the picture.
  await page.click('.desk-v1-sb-scene[data-scene-label="Opening"] [data-scene-title]');
  const sel1 = await selectedLabels(page);
  sel1.join() === 'Opening' ? ok('clicking a scene selects it (and only it)') : fail('click-select: ' + sel1);
  srv.log.length = 0;
  const took = await paste(page, { names: ['image.png'] });
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Opening"] .desk-v1-sb-thumb img'));
  await page.waitForTimeout(250);
  const up1 = uploads(srv);
  const put1 = puts(srv, /storyboard$/).pop();
  (took && up1.length === 1 && up1[0].multipart && /filename="pasted-picture-\d+\.png"/.test(up1[0].raw) && put1 && put1.body.scenes[0].label === 'Opening' && /pasted-picture-\d+/.test(put1.body.scenes[0].picture.path)
    && !put1.body.scenes[1].picture && !put1.body.scenes[2].picture)
    ? ok('Ctrl+V with a screenshot: one multipart upload (the Add picture call), then a PUT putting it in the selected scene only')
    : fail('paste into selected: ' + JSON.stringify({ took, up: up1.length, put: put1 && put1.body.scenes.map((s) => [s.label, s.picture && s.picture.path]) }));
  const openingId = await idOf(page, 'Opening');
  (await page.$(tileSel(openingId) + ' img')) ? ok('the timeline block shows the pasted picture') : fail('timeline block has no picture');

  // 1b. let go of the selection; paste -> a NEW scene at the end.
  await page.click('.desk-v1-sb-scene[data-scene-label="Opening"] [data-scene-title]');
  (await selectedLabels(page)).length === 0 ? ok('a second click on the row lets go of the selection') : fail('did not deselect');
  await paste(page, { names: ['image.png'] });
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 4);
  await page.waitForTimeout(250);
  const b1 = srv.boards[key()].scenes;
  (b1.length === 4 && b1[3].picture && b1[3].label === 'New scene' && b1[1].picture === null)
    ? ok('with no scene selected a paste adds a new scene holding the picture') : fail('no-selection paste: ' + JSON.stringify(b1.map((s) => [s.label, !!s.picture])));

  // 1c. two pictures with Middle selected -> Middle + one new scene right after it.
  await page.click('.desk-v1-sb-scene[data-scene-label="Middle"] [data-scene-title]');
  srv.log.length = 0;
  await paste(page, { names: ['image.png', 'image.png'] });
  await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 5);
  await page.waitForTimeout(250);
  const b2 = srv.boards[key()].scenes;
  (uploads(srv).length === 2 && b2.map((s) => s.label).join() === 'Opening,Middle,New scene,Closing,New scene' && b2[1].picture && b2[2].picture && !b2[3].picture)
    ? ok('two pictures = one scene each: the first fills the selected scene, the next is a new scene right after it')
    : fail('two-picture paste: ' + JSON.stringify({ up: uploads(srv).length, board: b2.map((s) => [s.label, !!s.picture]) }));

  // 1d. text, and a paste into a text field, are left alone.
  srv.log.length = 0;
  const textTook = await paste(page, { text: 'hello', names: [] });
  await page.click('.desk-v1-sb-scene[data-scene-label="Closing"] [data-scene-edit]');
  await page.waitForSelector('[data-scene-edit-label]');
  const fieldTook = await paste(page, { names: ['image.png'], onSel: '[data-scene-edit-label]' });
  await page.waitForTimeout(300);
  (!textTook && !fieldTook && uploads(srv).length === 0 && puts(srv, /storyboard$/).length === 0)
    ? ok('pasted text is ignored, and a paste inside a text field is never intercepted (0 uploads, 0 writes)')
    : fail('paste leaks: ' + JSON.stringify({ textTook, fieldTook, up: uploads(srv).length }));
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-label]) [data-scene-edit]');

  // 2. The Paste button reads navigator.clipboard.
  await page.evaluate(() => {
    const png = new Blob([new Uint8Array([137, 80, 78, 71])], { type: 'image/png' });
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { read: async () => [{ types: ['image/png'], getType: async () => png }] } });
  });
  srv.log.length = 0;
  await page.click('.desk-v1-sb-scene[data-scene-label="Closing"] [data-scene-paste]');
  await settle(page, () => !!document.querySelector('.desk-v1-sb-scene[data-scene-label="Closing"] .desk-v1-sb-thumb img'));
  const sel2 = await selectedLabels(page);
  (uploads(srv).length === 1 && sel2.join() === 'Closing')
    ? ok('the Paste button reads the clipboard into that scene (and selects it)') : fail('paste button: ' + JSON.stringify({ up: uploads(srv).length, sel2 }));
  await page.evaluate(() => {
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { read: async () => { throw new DOMException('denied', 'NotAllowedError'); } } });
  });
  await clearToasts(page);
  srv.log.length = 0;
  await page.click('.desk-v1-sb-scene[data-scene-label="Opening"] [data-scene-paste]');
  await page.waitForSelector('.toast .toast-msg', { timeout: 4000 });
  const msg = await page.textContent('.toast .toast-msg');
  (/Add picture/.test(msg) && !/\n/.test(msg) && uploads(srv).length === 0) ? ok(`a refused clipboard says so in one line and points to Add picture ("${msg}")`) : fail('refusal message: ' + msg);

  // 4. Dragging a block onto another block still reorders.
  const idO = await idOf(page, 'Opening'), idM = await idOf(page, 'Middle');
  await mouseDrag(page, tileSel(idO), tileSel(idM));
  await settle(page, () => document.querySelector('[data-storyboard] .desk-v1-sb-scene').dataset.sceneLabel !== 'Opening');
  ok('dragging a timeline block onto another block still reorders (Opening moved off the top)');

  // 3. Trash: drag a block onto the bin.
  await clearToasts(page);
  const before = await labels(page);
  const idC = await idOf(page, 'Closing');
  srv.log.length = 0;
  await mouseDrag(page, tileSel(idC), '[data-sb-trash]');
  await settle(page, (n) => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === n - 1, before.length);
  await page.waitForTimeout(400);
  const after = await labels(page);
  after.length === before.length - 1 && !after.includes('Closing') ? ok('dragging a timeline block onto the bin deletes that scene') : fail('trash drag: ' + JSON.stringify({ before, after }));
  const lastPut = puts(srv, /storyboard$/).pop();
  (lastPut && !lastPut.body.scenes.some((s) => s.label === 'Closing')) ? ok('the delete is saved (a PUT without the scene)') : fail('delete not saved');
  (await page.$('.toast')) === null ? ok('no toast in the DOM after the delete (the Undo popup is gone)') : fail('a toast appeared after the delete: ' + await page.textContent('.toast'));
  const undo = await page.$eval('[data-sb-undo]', (b) => ({ disabled: b.disabled, title: b.title, aria: b.getAttribute('aria-label') }));
  (!undo.disabled && undo.title === 'Undo: Deleted scene “Closing”' && undo.aria === undo.title) ? ok(`the Undo beside the bin is enabled: "${undo.title}"`) : fail('bin undo: ' + JSON.stringify(undo));
  const head = await page.$eval('#desk-v1-undo', (b) => ({ title: b.title, pulse: b.classList.contains('desk-v1-undo-pulse') }));
  (head.title.startsWith('Undo: Deleted scene “Closing”') && head.pulse) ? ok('the header Undo shows the same label and pulses') : fail('header undo: ' + JSON.stringify(head));
  if (SHOT_DIR) { await page.$eval('[data-sb-timeline-wrap]', (e) => e.scrollIntoView({ block: 'start' })); await page.screenshot({ path: SHOT_DIR + '/storyboard-paste-trash-1440.png' }); }
  await page.waitForTimeout(1400);
  (await page.$eval('#desk-v1-undo', (b) => b.classList.contains('desk-v1-undo-pulse'))) ? fail('pulse class never cleared') : ok('the pulse clears itself');

  srv.log.length = 0;
  await page.click('[data-sb-undo]');
  await settle(page, (n) => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === n, before.length);
  await page.waitForTimeout(300);
  const restored = await labels(page);
  (JSON.stringify(restored) === JSON.stringify(before) && puts(srv, /storyboard$/).pop().body.scenes.some((s) => s.label === 'Closing'))
    ? ok('clicking the bin-side Undo restores the scene in place, and saves it') : fail('undo restore: ' + JSON.stringify({ before, restored }));
  (await page.$eval('[data-sb-undo]', (b) => b.title)) !== 'Undo: Deleted scene “Closing”' ? ok('the Undo button now names the command before it') : fail('undo label stale');

  // the row's own delete: same path, still no toast
  await clearToasts(page);
  await page.click('.desk-v1-sb-scene[data-scene-label="Middle"] [data-scene-delete]');
  await page.waitForTimeout(400);
  (await page.$('.toast')) === null ? ok('the scene-row delete raises no toast either') : fail('row delete raised a toast');

  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 3: the same drag by touch, on a phone ──────────────────────────────────
async function phone(browser) {
  const { ctx, page, pageErrors } = await openStudio(browser, { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
  for (const l of ['Opening', 'Middle']) await addScene(page, l);
  await page.waitForTimeout(250);
  const idM = await idOf(page, 'Middle');
  await clearToasts(page);
  await page.$eval(tileSel(idM), (e) => e.scrollIntoView({ block: 'center' }));
  const tile = await (await page.$(tileSel(idM))).boundingBox();
  const bin = await (await page.$('[data-sb-trash]')).boundingBox();
  const inView = tile.y >= 0 && tile.y + tile.height <= 844 && bin.y >= 0 && bin.y + bin.height <= 844;
  if (SHOT_DIR) await page.screenshot({ path: SHOT_DIR + '/storyboard-paste-trash-390.png' });
  await touchDrag(ctx, page, tileSel(idM), '[data-sb-trash]');
  await page.waitForTimeout(500);
  const after = await labels(page);
  (after.join() === 'Opening') ? ok('390px, touch long-press: a timeline block dragged onto the bin deletes the scene') : fail('touch trash: ' + JSON.stringify({ after, inView, tile, bin }));
  (await page.$('.toast')) === null ? ok('no toast on the phone either') : fail('toast on phone');
  const u = await page.$eval('[data-sb-undo]', (b) => ({ d: b.disabled, t: b.title }));
  (!u.d && u.t === 'Undo: Deleted scene “Middle”') ? ok('the bin-side Undo is enabled on the phone') : fail('phone undo: ' + JSON.stringify(u));
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('Desktop: paste, Paste button, reorder, drag to bin, Undo');
  await desktop(browser);
  console.log('Phone: touch long-press drag to bin');
  await phone(browser);
} catch (e) {
  fail('smoke crashed: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-storyboard-paste-trash checks passed');
process.exit(bad ? 1 : 0);
