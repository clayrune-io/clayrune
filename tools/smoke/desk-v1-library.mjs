#!/usr/bin/env node
/**
 * Desk v1 — the Material library is usable (Ron 2026-10-06: "nothing can be done with"
 * the thumbnails). static/js/desk-v1-library-viewer.js, desk-v1-library-picker.js,
 * desk-v1-library-refs.js, desk-v1-article-pictures.js.
 *
 * LIVE (fake server), at 1440 and 390:
 *   - a library picture opens in the picture viewer; next/previous walk the folder
 *     (the app's /api/serve-image/siblings), Esc and the X close it
 *   - a library video opens in a player (the `play` URL), Esc and the X close it
 *   - ONE "Pick from library" chooser: it lists every folder (Storyboards included),
 *     searches, and View opens the viewer without choosing
 *   - a storyboard scene's picture: picking REFERENCES the library file (no upload POST)
 *   - an article: picking puts a picture line in the body, the strip shows it, and the
 *     draft that is PUT carries the library path
 *   - New image: a start/reference picture rides the generate request as {path}; a model
 *     that takes none says so
 * DEMO: the chooser reads the fixture library and calls no /api/desk route.
 *
 * RUN   cd tools/smoke && node desk-v1-library.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const GIF = Buffer.from('R0lGODlhAQABAAAAACwAAAAAAQABAAA=', 'base64');
const ABS = '/smoke/uploads/';
const pic = (folder, name) => ({ id: `desk/library/image/${folder}/${name}`, kind: 'image', title: name, path: `desk/library/image/${folder}/${name}`, src: '/api/serve-image?path=' + encodeURIComponent(`${ABS}desk/library/image/${folder}/${name}`) });
const FOLDERS = {
  image: [
    { id: 'f-sb', title: 'Storyboards', files: 3, thumb: null, items: ['scene-a.png', 'scene-b.png', 'scene-c.png'].map((n) => pic('Storyboards', n)) },
    { id: 'f-studio', title: 'Studio', files: 2, thumb: null, items: ['hero.png', 'logo.png'].map((n) => pic('Studio', n)) },
  ],
  video: [
    { id: 'f-clips', title: 'Clips', files: 2, thumb: null, items: ['intro.mp4', 'outro.mp4'].map((n) => ({
      id: `desk/library/video/Clips/${n}`, kind: 'video', title: n, path: `desk/library/video/Clips/${n}`, src: null,
      play: '/api/serve-file?inline=1&path=' + encodeURIComponent(`${ABS}desk/library/video/Clips/${n}`) })) },
  ],
};
const IMG_ENGINE = (max) => [{ id: 'google', label: 'Google', auth: { kind: 'api_key', vault_entry: 'gemini-api' }, job_limit_usd: 5,
  connected: { ready: true, vault_entry: 'gemini-api', reason: null },
  models: [{ model_id: 'gemini-img', kind: 'image', label: 'Gemini image', status: 'stable', aspect_ratios: ['1:1', '16:9'], inputs: { reference_images_max: max } }] }];

const PROJECT = (p) => ({
  id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
  next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
  last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
});

function makeServer({ refsMax = 2 } = {}) {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const srv = { log: [], fx, boards: {}, articles: {}, refsMax };
  const sbOut = (key) => {
    const b = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '' };
    return { rev: b.rev, title: b.title, pending_edits: b.pending_edits,
      scenes: b.scenes.map((s) => ({ ...s, picture: s.picture ? { ...s.picture, src: '/api/serve-image?path=' + encodeURIComponent(ABS + s.picture.path) } : null })) };
  };
  const artOut = (a) => ({ ...a, attached: false, campaign_title: '' });
  srv.handle = ({ method, path, body, J }) => {
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J({ ...workspaceFromFixtures(fx), projects, pieces: fx.families.map((f) => JSON.parse(JSON.stringify(f))) });
    if (path === '/api/desk/materials') return J({ library: FOLDERS, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/usage') return J({ rendering: [], files: {} });
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: IMG_ENGINE(srv.refsMax) });
    if (path === '/api/desk/engines/estimate') return J({ estimate: { usd: 0.04, approximate: false }, job_limit_usd: 5, refusal: null });
    if (path === '/api/desk/engines/jobs' && method === 'POST') {
      return J({ job: { job_id: 'job-1', status: 'ready', kind: 'image', engine_id: 'google', model_id: 'gemini-img', cost_usd: 0.04,
        outputs: [{ path: 'desk/library/image/Generated/job-1-0.png', mime: 'image/png', src: '/api/serve-image?path=x' }] }, replay: false }, 201);
    }
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    if (path === '/api/desk/studio/articles' && method === 'GET') return J({ articles: Object.values(srv.articles).map(artOut) });
    let m = path.match(/^\/api\/desk\/studio\/articles\/([^/]+)$/);
    if (m && method === 'PUT') {
      const cur = srv.articles[m[1]];
      if ((cur ? cur.rev : 0) !== body.rev) return J({ error: 'stale', current: cur }, 409);
      srv.articles[m[1]] = { id: m[1], topic: body.topic, project_id: body.project_id, campaign_id: body.campaign_id, tabs: body.tabs, piece_id: null, rev: body.rev + 1 };
      return J(artOut(srv.articles[m[1]]));
    }
    m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
    if (m) {
      const key = (m[1] === 'pieces' ? 'piece:' : 'studio:') + m[2];
      if (method === 'GET') return J(sbOut(key));
      if (method === 'PUT') {
        const cur = srv.boards[key] || { rev: 0 };
        if (body.rev !== cur.rev) return J({ error: 'stale' }, 409);
        srv.boards[key] = { rev: cur.rev + 1, title: body.title || '', scenes: body.scenes.map((s) => ({ ...s })), pending_edits: body.pending_edits || [] };
        return J(sbOut(key));
      }
    }
    return J({ error: 'unhandled ' + method + ' ' + path }, 404);
  };
  return srv;
}

async function newPage(browser, { live, srv, viewport }) {
  const fx = loadFixtures();
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const calls = [];
  const siblings = [];
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (STATIC[path]) return route.fulfill({ status: 200, contentType: STATIC[path][0], body: STATIC[path][1] });
    if (path === '/api/projects') return J(fx.projects.map(PROJECT));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: !!live, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/serve-image') return route.fulfill({ status: 200, contentType: 'image/gif', body: GIF });
    if (path === '/api/serve-image/siblings') {
      const p = url.searchParams.get('path') || '';
      siblings.push(p);
      const folder = FOLDERS.image.find((f) => p.includes(`/image/${f.title}/`));
      if (!folder) return J({ files: [], index: 0 });
      const files = folder.items.map((i) => ABS + i.path);
      return J({ files, index: Math.max(0, files.indexOf(p)) });
    }
    if (path === '/api/serve-file') return route.fulfill({ status: 200, contentType: 'video/mp4', body: Buffer.alloc(8) });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postData() ? JSON.parse(req.postData()) : null; } catch (e) { body = null; }
    calls.push({ method, path });
    if (!live || !srv) return route.abort();
    return srv.handle({ method, path, query: url.searchParams, body, J });
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.evaluate(() => {
    window.humanProofFetch = async (url, init) => { const r = await fetch(url, init); let b = null; try { b = await r.json(); } catch (_) { /* none */ } return { ok: r.ok, status: r.status, body: b }; };
  });
  return { ctx, page, pageErrors, calls, siblings };
}

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const shot = (page, name) => page.waitForTimeout(200).then(() => page.screenshot({ path: resolve(SHOT_DIR, name) }));
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
const openStudio = async (page) => {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
};
const chooserFolders = (page) => page.$$eval('[data-lp-folder]', (b) => b.map((x) => x.textContent.replace(/\s+/g, ' ').trim()));

async function live(browser, vp, name) {
  const tag = `[live ${name}]`;
  console.log(tag);
  const srv = makeServer();
  const { ctx, page, pageErrors, siblings } = await newPage(browser, { live: true, srv, viewport: vp });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(page);
  await settle(page, () => document.querySelectorAll('[data-studio-folder]').length >= 3);

  // 1. picture viewer
  await page.click('[data-studio-folder="f-sb"]');
  await page.waitForSelector('[data-studio-file]');
  await page.locator('[data-studio-file="desk/library/image/Storyboards/scene-b.png"]').click();
  await page.waitForSelector('.mermaid-viewer-overlay img');
  const src0 = await page.$eval('.mermaid-viewer-overlay img', (i) => i.getAttribute('src'));
  check(/scene-b\.png/.test(decodeURIComponent(src0)), `${tag} clicking a library picture opens it full size`, `${tag} viewer src ${src0}`);
  await settle(page, () => /2 \/ 3/.test((document.querySelector('.iv-counter') || {}).textContent || ''));
  ok(`${tag} the viewer knows its place in the folder: ${await page.textContent('.iv-counter')}`);
  check(siblings.length > 0, `${tag} the folder's pictures come from /api/serve-image/siblings`, `${tag} siblings never asked`);
  await page.keyboard.press('ArrowRight');
  await settle(page, () => /scene-c/.test(decodeURIComponent(document.querySelector('.mermaid-viewer-overlay img').getAttribute('src'))));
  ok(`${tag} Next steps to the next picture in the folder`);
  await page.click('.iv-nav-prev');
  await settle(page, () => /scene-b/.test(decodeURIComponent(document.querySelector('.mermaid-viewer-overlay img').getAttribute('src'))));
  ok(`${tag} Previous steps back`);
  if (name === '390') {
    const box = await page.$eval('.mermaid-viewer-content', (e) => { const r = e.getBoundingClientRect(); return { l: r.left, r: r.right, w: innerWidth }; });
    check(box.l >= 0 && box.r <= box.w, `${tag} the viewer fits the phone (${Math.round(box.l)}..${Math.round(box.r)} of ${box.w})`, `${tag} viewer overflows ${JSON.stringify(box)}`);
  }
  await shot(page, `library_viewer_${name}.png`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('.mermaid-viewer-overlay'));
  ok(`${tag} Esc closes the viewer`);
  await page.locator('[data-studio-file="desk/library/image/Storyboards/scene-a.png"]').click();
  await page.waitForSelector('.mermaid-viewer-overlay ._iv-close');
  await page.click('.mermaid-viewer-overlay ._iv-close');
  await settle(page, () => !document.querySelector('.mermaid-viewer-overlay'));
  ok(`${tag} the X closes the viewer`);

  // 2. video player
  await page.click('[data-studio-lib-back]');
  await page.click('[data-studio-folder="f-clips"]');
  await page.locator('[data-studio-file="desk/library/video/Clips/outro.mp4"]').click();
  await page.waitForSelector('[data-lv-player] video');
  const vsrc = await page.$eval('[data-lv-video]', (v) => v.getAttribute('src'));
  check(/outro\.mp4/.test(decodeURIComponent(vsrc)) && /serve-file/.test(vsrc), `${tag} a library video opens in a player on its play URL`, `${tag} video src ${vsrc}`);
  check(/2 \/ 2/.test(await page.textContent('[data-lv-count]')), `${tag} the player counts the folder's videos`, `${tag} count ${await page.textContent('[data-lv-count]')}`);
  await page.click('[data-lv-prev]');
  check(/intro\.mp4/.test(decodeURIComponent(await page.$eval('[data-lv-video]', (v) => v.getAttribute('src')))), `${tag} the player steps to the previous video`, `${tag} prev did not step`);
  if (name === '390') {
    const over = await overflow(page);
    check(over <= 0, `${tag} the player has no horizontal overflow`, `${tag} player overflow ${over}px`);
  }
  await shot(page, `library_player_${name}.png`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-lv-player]'));
  ok(`${tag} Esc closes the player`);
  await page.locator('[data-studio-file="desk/library/video/Clips/intro.mp4"]').click();
  await page.waitForSelector('[data-lv-close]');
  await page.click('[data-lv-close]');
  await settle(page, () => !document.querySelector('[data-lv-player]'));
  ok(`${tag} the X closes the player`);

  // 3. a storyboard scene's picture
  await openStudio(page);
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-scene-add]', { timeout: 8000 });
  await page.click('[data-scene-add]');
  await page.waitForSelector('.desk-v1-sb-scene [data-scene-library]');
  const btnLabel = (await page.textContent('[data-scene-library]')).trim();
  check(btnLabel === 'Pick from library', `${tag} the scene's button reads "${btnLabel}"`, `${tag} scene button: ${btnLabel}`);
  srv.log.length = 0;
  await page.click('[data-scene-library]');
  await page.waitForSelector('[data-lp] [data-lp-tile]');
  const folders = await chooserFolders(page);
  check(folders.some((f) => /^Storyboards/.test(f)) && folders.some((f) => /^Studio/.test(f)) && /^All folders/.test(folders[0]),
    `${tag} the chooser lists every image folder, Storyboards included: ${JSON.stringify(folders)}`, `${tag} folders: ${JSON.stringify(folders)}`);
  check(!folders.some((f) => /^Clips/.test(f)), `${tag} a picture target does not offer videos`, `${tag} videos offered: ${JSON.stringify(folders)}`);
  check((await page.$$('[data-lp-tile]')).length === 5, `${tag} All folders shows all 5 pictures`, `${tag} tiles ${(await page.$$('[data-lp-tile]')).length}`);
  await page.fill('[data-lp-search]', 'logo');
  check((await page.$$('[data-lp-tile]')).length === 1, `${tag} search narrows to 1 picture`, `${tag} search tiles ${(await page.$$('[data-lp-tile]')).length}`);
  await page.fill('[data-lp-search]', 'zzz');
  check(!!(await page.$('[data-lp-empty]')), `${tag} a search with no match says so`, `${tag} no empty state`);
  await page.fill('[data-lp-search]', '');
  await page.click('[data-lp-folder="image:Storyboards"]');
  check((await page.$$('[data-lp-tile]')).length === 3, `${tag} the Storyboards chip shows its 3 pictures`, `${tag} chip tiles ${(await page.$$('[data-lp-tile]')).length}`);
  if (name === '390') {
    const over = await overflow(page);
    check(over <= 0, `${tag} the chooser has no horizontal overflow`, `${tag} chooser overflow ${over}px`);
    const hs = await page.$$eval('[data-lp-pick], [data-lp-view], [data-lp-close]', (b) => b.map((x) => Math.round(x.getBoundingClientRect().height)));
    check(Math.min(...hs) >= 40, `${tag} chooser controls are tappable (min ${Math.min(...hs)}px)`, `${tag} small controls: ${hs}`);
  }
  await shot(page, `library_picker_${name}.png`);
  await page.click('[data-lp-view="1"]');
  await page.waitForSelector('.mermaid-viewer-overlay img');
  check(!!(await page.$('[data-lp]')), `${tag} View opens the picture without choosing it (the chooser stays)`, `${tag} chooser gone after View`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('.mermaid-viewer-overlay'));
  check(!!(await page.$('[data-lp]')), `${tag} Esc closes only the viewer`, `${tag} Esc closed the chooser too`);
  await page.locator('[data-lp-pick]').nth(2).click();
  await settle(page, () => !document.querySelector('[data-lp]'));
  await settle(page, () => { const i = document.querySelector('.desk-v1-sb-scene img'); return !!i && /scene-c/.test(decodeURIComponent(i.getAttribute('src'))); });
  const put = srv.log.filter((r) => r.method === 'PUT' && /storyboard$/.test(r.path)).pop();
  check(put && put.body.scenes[0].picture && put.body.scenes[0].picture.path === 'desk/library/image/Storyboards/scene-c.png',
    `${tag} the scene is saved pointing at the library path, ${put && put.body.scenes[0].picture && put.body.scenes[0].picture.path}`, `${tag} board PUT: ${JSON.stringify(put && put.body)}`);
  check(!srv.log.some((r) => r.method === 'POST' && /pictures$/.test(r.path)), `${tag} nothing was uploaded: the library file is referenced, not copied`, `${tag} an upload POST was sent`);
  check(/nothing was copied/.test(await page.textContent('.toast')), `${tag} the page says it did not copy`, `${tag} toast: ${await page.textContent('.toast')}`);
  await shot(page, `library_scene_${name}.png`);

  // 4. an article
  await openStudio(page);
  await page.click('[data-studio-new="article"]');
  await page.waitForSelector('[data-sa-topic]');
  await page.fill('[data-sa-topic]', 'Library pictures');
  await page.click('[data-sa-start]');
  await page.waitForSelector('[data-view="writer"] [data-writer-body]');
  await page.click('[data-writer-body]');
  await page.keyboard.type('Intro line.');
  check((await page.textContent('[data-sa-library]')).trim() === 'Pick from library', `${tag} the article button reads "Pick from library"`, `${tag} article button text`);
  await page.click('[data-sa-library]');
  await page.waitForSelector('[data-lp] [data-lp-tile]');
  await page.fill('[data-lp-search]', 'hero');
  await page.locator('[data-lp-pick]').first().click();
  await settle(page, () => /!\[hero\.png\]\(desk\/library\/image\/Studio\/hero\.png\)/.test(document.querySelector('[data-writer-body]').innerText));
  ok(`${tag} the picture is a line in the article body`);
  await settle(page, () => !!document.querySelector('[data-sa-pic] img'));
  ok(`${tag} the strip under the writer shows the picture`);
  await settle(page, () => /Saved/.test((document.querySelector('[data-sa-status]') || {}).textContent || ''));
  const aput = srv.log.filter((r) => r.method === 'PUT' && /studio\/articles\//.test(r.path)).pop();
  check(aput && /desk\/library\/image\/Studio\/hero\.png/.test(aput.body.tabs[0].body), `${tag} the saved draft carries the library path`, `${tag} article PUT: ${JSON.stringify(aput && aput.body.tabs)}`);
  check(/nothing was copied/.test(await page.textContent('.toast')), `${tag} the page says it did not copy`, `${tag} toast: ${await page.textContent('.toast')}`);
  await page.click('[data-sa-pic-open]');
  await page.waitForSelector('.mermaid-viewer-overlay img');
  ok(`${tag} a picture in the strip opens in the viewer`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('.mermaid-viewer-overlay'));
  if (name === '390') {
    const over = await overflow(page);
    check(over <= 0, `${tag} the article page has no horizontal overflow`, `${tag} article overflow ${over}px`);
  }
  await shot(page, `library_article_${name}.png`);

  // 5. New image
  await openStudio(page);
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'image' }));
  await page.waitForSelector('[data-sc-source="engine"]', { timeout: 8000 });
  await page.click('[data-sc-source="engine"]');
  await page.waitForSelector('[data-eng-panel][data-kind="image"] [data-eng-refs]', { timeout: 8000 });
  await page.fill('[data-eng-prompt]', 'A calm gradient');
  await page.click('[data-eng-refs] [data-lr-pick]');
  await page.waitForSelector('[data-lp] [data-lp-tile]');
  await page.locator('[data-lp-pick]').first().click();
  await settle(page, () => !!document.querySelector('[data-eng-refs] [data-lr-item]'));
  ok(`${tag} a picked library picture shows on the New image page`);
  check(/nothing was copied/.test(await page.textContent('.toast')), `${tag} the page says it did not copy`, `${tag} toast: ${await page.textContent('.toast')}`);
  await page.click('[data-eng-refs] [data-lr-pick]');
  await page.waitForSelector('[data-lp] [data-lp-tile]');
  await page.locator('[data-lp-pick]').nth(1).click();
  await settle(page, () => document.querySelectorAll('[data-eng-refs] [data-lr-item]').length === 2);
  check(!!(await page.$('[data-eng-refs] [data-lr-pick][disabled]')) && !!(await page.$('[data-lr-full]')), `${tag} a full strip disables the button and says why`, `${tag} full strip not signalled`);
  await shot(page, `library_image_${name}.png`);
  await page.fill('[data-eng-prompt]', 'A calm gradient, again');
  await page.press('[data-eng-prompt]', 'Tab');
  await settle(page, () => !document.querySelector('[data-eng-estimate][data-state="pricing"]'));
  srv.log.length = 0;
  await page.click('[data-eng-render-btn]');
  await settle(page, () => !!document.querySelector('[data-eng-output] img'));
  const job = srv.log.filter((r) => r.method === 'POST' && /engines\/jobs$/.test(r.path)).pop();
  check(job && JSON.stringify(job.body.reference_images) === JSON.stringify([{ path: 'desk/library/image/Storyboards/scene-a.png' }, { path: 'desk/library/image/Storyboards/scene-b.png' }]),
    `${tag} Generate sends the library paths as reference_images`, `${tag} job body: ${JSON.stringify(job && job.body)}`);
  await page.click('[data-eng-refs] [data-lr-remove="0"]');
  await settle(page, () => document.querySelectorAll('[data-eng-refs] [data-lr-item]').length === 1);
  ok(`${tag} a picture can be taken off again`);
  if (name === '390') {
    const over = await overflow(page);
    check(over <= 0, `${tag} New image has no horizontal overflow`, `${tag} image overflow ${over}px`);
  }
  check(realErrors(pageErrors).length === 0, `${tag} no uncaught page errors`, `${tag} page errors: ${pageErrors.join(' | ')}`);
  await ctx.close();

  // A model that takes no start picture says so and sends none.
  const srv0 = makeServer({ refsMax: 0 });
  const b0 = await newPage(browser, { live: true, srv: srv0, viewport: vp });
  await settle(b0.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(b0.page);
  await b0.page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'image' }));
  await b0.page.waitForSelector('[data-sc-source="engine"]', { timeout: 8000 });
  await b0.page.click('[data-sc-source="engine"]');
  await b0.page.waitForSelector('[data-eng-refs]', { timeout: 8000 });
  check(!!(await b0.page.$('[data-eng-refs] [data-lr-blocked]')) && !(await b0.page.$('[data-eng-refs] [data-lr-pick]')),
    `${tag} a model with no reference-image input says so and offers no button`, `${tag} blocked strip missing`);
  await b0.ctx.close();
}

async function demo(browser) {
  const tag = '[demo]';
  console.log(tag);
  const { ctx, page, pageErrors, calls } = await newPage(browser, { live: false });
  await page.waitForFunction(() => !!window.DeskV1Fixtures, null, { timeout: 8000 });
  await openStudio(page);
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-storyboard]', { timeout: 8000 });
  check(!(await page.$('[data-scene-library]')), `${tag} the demo storyboard keeps its fixture controls (no live-only Pick from library)`, `${tag} demo grew a library button`);
  await page.evaluate(() => window.DeskV1LibraryPicker.open({ kinds: ['image'] }));
  await page.waitForSelector('[data-lp] [data-lp-empty]', { timeout: 6000 });
  const folders = await chooserFolders(page);
  check(folders.length > 1, `${tag} the chooser reads the fixture library's folders (it has no files to show): ${JSON.stringify(folders)}`, `${tag} folders: ${JSON.stringify(folders)}`);
  await page.keyboard.press('Escape');
  await settle(page, () => !document.querySelector('[data-lp]'));
  ok(`${tag} Esc closes the chooser`);
  check(calls.length === 0, `${tag} nothing called /api/desk`, `${tag} called: ${JSON.stringify(calls)}`);
  check(realErrors(pageErrors).length === 0, `${tag} no uncaught page errors`, `${tag} page errors: ${pageErrors.join(' | ')}`);
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await live(browser, { width: 1440, height: 950 }, '1440');
  await live(browser, { width: 390, height: 844 }, '390');
  await demo(browser);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-v1-library checks passed');
process.exit(bad ? 1 : 0);
