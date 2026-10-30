#!/usr/bin/env node
// Capture the Add scene control in Studio (with one scene) and the video director (empty), at 1440 and 390.
// RUN   cd tools/smoke && node _shot-add-scene.mjs <before|after>   -> docs/desk_v1/screens/add_scene_<tag>_{studio,director}_{1440,390}.png
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
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

// `before` serves the three touched files exactly as committed at HEAD (git show), so the working tree is never rewound.
const tag = process.argv[2] || 'after';
let INDEX = INDEX_HTML;
if (tag === 'before') {
  const head = (p) => execFileSync('git', ['show', 'HEAD:' + p], { cwd: REPO_ROOT, encoding: 'utf8', maxBuffer: 1 << 26 });
  for (const f of ['desk-v1-studio.js', 'desk-v1-video.js']) STATIC['/static/js/' + f][1] = head('static/js/' + f);
  INDEX = head('static/index.html');
  delete STATIC['/static/css/desk-v1-add-scene.css'];
}

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

async function newPage(browser, { live, srv, viewport }) {
  const ctx = await browser.newContext({ viewport });
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
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX });
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
const OUT = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
// 195 = a 390px phone at 200% zoom
const SIZES = [[1440, 900], [390, 844], [195, 760]];

async function shoot(page, name, w, focus) {
  await page.mouse.move(2, 2);
  await page.waitForTimeout(300);
  await page.evaluate(() => { const b = document.querySelector('.desk-v1-sb-addrow'); b && b.scrollIntoView({ block: 'center' }); });
  await page.waitForTimeout(300);
  const file = `add_scene_${tag}_${name}${focus ? '_focus' : ''}_${w}.png`;
  await page.screenshot({ path: resolve(OUT, file) });
  console.log('  shot', file);
}

const browser = await chromium.launch();
for (const [w, h] of SIZES) {
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { live: true, srv, viewport: { width: w, height: h } });
    await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
    await page.evaluate(() => window.deskV1Nav('studio', {}));
    await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
    await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
    await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
    await page.click('[data-scene-add]');
    await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 1);
    await shoot(page, 'studio', w);
    await ctx.close();
  }
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { live: true, srv, viewport: { width: w, height: h } });
    await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
    await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
    await page.waitForSelector('[data-add-scene]', { timeout: 8000 });
    await shoot(page, 'director', w);
    if (tag === 'after') {
      await page.keyboard.press('Tab');
      await page.focus('[data-add-scene]');
      const box = await page.$eval('[data-add-scene]', (b) => { const r = b.getBoundingClientRect(); const cs = getComputedStyle(b); return { w: Math.round(r.width), h: Math.round(r.height), outline: cs.outlineStyle + ' ' + cs.outlineWidth, over: document.documentElement.scrollWidth > innerWidth, focusVisible: b.matches(':focus-visible') }; });
      const geo = await page.evaluate(() => { const r = (q) => { const e = document.querySelector(q); if (!e) return null; const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right)]; }; return { vw: innerWidth, modal: r('.modal-window[data-modal-id="__desk"]'), shell: r('.desk-v1-shell'), row: r('.desk-v1-sb-addrow'), btn: r('[data-add-scene]') }; });
      console.log('  ', w, JSON.stringify(box), JSON.stringify(geo));
      await shoot(page, 'director', w, true);
    }
    await ctx.close();
  }
}
await browser.close();
