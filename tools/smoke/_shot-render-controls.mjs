#!/usr/bin/env node
// Per-scene render controls (Select + Render this scene) on a Studio scene row (two scenes, one with a long title)
// and on the video director's scene strip, at 1440 and 390. Mocked APIs only.
// RUN   cd tools/smoke && node _shot-render-controls.mjs <before|after>   -> docs/desk_v1/screens/render_controls_<tag>_{studio,director}_{1440,390}.png
// `before` serves static/css/desk-v1-render-scenes.css as committed at HEAD (git show), so the working tree is never rewound.
// Also prints, per size, whether any control's box intersects a sibling control's box on the same card (the defect).
import { readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
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

const tag = process.argv[2] || 'after';
let INDEX = INDEX_HTML;
if (tag === 'before') {
  const head = (p) => execFileSync('git', ['show', 'HEAD:' + p], { cwd: REPO_ROOT, encoding: 'utf8', maxBuffer: 1 << 26 });
  STATIC['/static/css/desk-v1-render-scenes.css'][1] = head('static/css/desk-v1-render-scenes.css');
}

const GIF = 'R0lGODlhAQABAAAAACwAAAAAAQABAAA=';
const SRC_PIC = (name) => `data:image/gif;base64,${GIF}#${name}`;
let bad = 0;

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const pieces = fx.families.map((f) => JSON.parse(JSON.stringify(f)));
  const srv = { log: [], boards: {}, fx, pieces };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, pieces: pieces.map((p) => JSON.parse(JSON.stringify(p))) });
  srv.out = (key) => {
    const b = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '' };
    return { rev: b.rev, title: b.title, pending_edits: b.pending_edits, scenes: b.scenes.map((s) => ({ ...s, picture: s.picture ? { ...s.picture, src: SRC_PIC(s.picture.path) } : null })) };
  };
  return srv;
}

const quote = (ids) => ({ estimate: { usd: 0, credits: (ids || []).length * 6 }, plan: { clips: (ids || []).length }, refusal: null, board_fingerprint: JSON.stringify(ids || []) });

async function newPage(browser, { srv, viewport }) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  await installDemoFixtures(page);
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
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/desk/engines') return J({ engines: [{ id: 'higgsfield_mcp', label: 'Higgsfield', currency: 'credits', connected: { ready: true }, job_limit_credits: 100,
      models: [{ model_id: 'seedance', label: 'Seedance', kind: 'video', aspect_ratios: ['16:9'] }] }] });
    if (path === '/api/desk/engines/render/estimate') return J(quote(req.postDataJSON().scene_ids));
    if (path === '/api/desk/engines/renders' && method === 'GET') return J({ render: null });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    const m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
    if (m) {
      const key = (m[1] === 'pieces' ? 'piece:' : 'studio:') + m[2];
      if (method === 'GET') return J(srv.out(key));
      if (method === 'PUT') {
        const cur = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '' };
        if (body.rev !== cur.rev) return J({ error: 'rev', problems: [] }, 409);
        srv.boards[key] = { rev: cur.rev + 1, title: m[1] === 'studio' ? (body.title || cur.title || '') : '', scenes: body.scenes.map((s) => ({ ...s })), pending_edits: body.pending_edits || [] };
        return J(srv.out(key));
      }
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
const SIZES = [[1440, 900], [390, 844]];

// The defect, measured: for each card, every pair of its visible controls/text blocks must not intersect,
// and nothing may poke out of the card's own box. Returns the offending pairs (empty = clean).
const measure = (page, cardSel) => page.evaluate((sel) => {
  const out = [];
  document.querySelectorAll(sel).forEach((card) => {
    const cb = card.getBoundingClientRect();
    const parts = [...card.querySelectorAll('[data-scene-handle], .desk-v1-sb-thumb, .desk-v1-sb-scenetitle, .desk-v1-sb-meta > *, [data-scene-render-select], [data-scene-render-one], .desk-v1-video-scene-label')]
      .filter((e) => e.offsetParent !== null).map((e) => ({ n: e.getAttribute('data-scene-render-one') != null ? 'render-one' : e.getAttribute('data-scene-render-select') != null ? 'select' : (e.getAttribute('data-scene-edit') != null ? 'edit' : e.className || e.tagName), b: e.getBoundingClientRect() }));
    for (let i = 0; i < parts.length; i++) {
      const a = parts[i].b;
      if (a.left < cb.left - 1 || a.right > cb.right + 1) out.push(`${parts[i].n} outside card (${Math.round(a.left)}..${Math.round(a.right)} vs ${Math.round(cb.left)}..${Math.round(cb.right)})`);
      for (let j = i + 1; j < parts.length; j++) {
        const b = parts[j].b;
        if (a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1 && b.top < a.bottom - 1) out.push(`${parts[i].n} x ${parts[j].n}`);
      }
    }
    const bin = document.querySelector('.desk-v1-sb-bincol');
    const one = card.querySelector('[data-scene-render-one]');
    if (bin && one) { const a = one.getBoundingClientRect(), b = bin.getBoundingClientRect(); if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) out.push('render-one x undo/bin column'); }
  });
  return { overflowX: document.documentElement.scrollWidth > innerWidth, pairs: out };
}, cardSel);

async function shoot(page, name, w, sel) {
  await page.mouse.move(2, 2);
  await page.evaluate((s) => { const e = document.querySelector(s); e && e.scrollIntoView({ block: 'center' }); }, sel);
  await page.waitForTimeout(400);
  const file = `render_controls_${tag}_${name}_${w}.png`;
  await page.screenshot({ path: resolve(OUT, file) });
  console.log('  shot', file);
}

const browser = await chromium.launch();
for (const [w, h] of SIZES) {
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, { srv, viewport: { width: w, height: h } });
    await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
    await page.evaluate(() => window.deskV1Nav('studio', {}));
    await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
    await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
    await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
    await page.click('[data-scene-add]');
    await page.click('[data-scene-add]');
    await settle(page, () => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === 2);
    await page.waitForSelector('[data-storyboard] [data-scene-render-one]', { timeout: 8000 });
    // the second scene takes a long title, the stress case for the title column
    await page.evaluate(() => { const t = document.querySelectorAll('[data-scene-title]')[1]; if (t) t.lastChild.textContent = 'A long scene name that must wrap inside its own column'; });
    await page.click('[data-scene-id] [data-scene-render-select]');
    await page.waitForTimeout(600);
    await shoot(page, 'studio', w, '[data-storyboard] .desk-v1-sb-scene');
    const r = await measure(page, '[data-storyboard] .desk-v1-sb-scene');
    console.log('  studio', w, JSON.stringify(r)); if (r.pairs.length || r.overflowX) bad++;
    if (pageErrors.length) { console.error('  page errors', pageErrors); bad++; }
    await ctx.close();
  }
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, { srv, viewport: { width: w, height: h } });
    await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
    await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
    await page.waitForSelector('[data-add-scene]', { timeout: 8000 });
    page.on('dialog', (d) => d.accept());   // window.prompt: name the new scene (default)
    for (let i = 0; i < 2; i++) {   // each insert opens the picture chooser; a picture is optional, so choose none
      const chooser = page.waitForEvent('filechooser');
      await page.click('[data-add-scene]');
      await (await chooser).setFiles({ name: 'hero.png', mimeType: 'image/png', buffer: Buffer.from('x') });
      await page.waitForTimeout(500);
    }
    await page.waitForTimeout(500);
    if (process.env.DBG) console.log(await page.evaluate(() => document.getElementById('desk-v1-video-scenestrip').outerHTML.slice(0, 1500)));
    await page.waitForSelector('#desk-v1-video-scenestrip .desk-v1-video-scene', { state: 'attached', timeout: 8000 });
    await page.waitForSelector('#desk-v1-video-scenestrip [data-scene-render-one]', { timeout: 8000 });
    await page.waitForTimeout(600);
    await shoot(page, 'director', w, '#desk-v1-video-scenestrip');
    const r = await measure(page, '#desk-v1-video-scenestrip .desk-v1-video-scene');
    console.log('  director', w, JSON.stringify(r)); if (r.pairs.length || r.overflowX) bad++;
    if (pageErrors.length) { console.error('  page errors', pageErrors); bad++; }
    await ctx.close();
  }
}
await browser.close();
console.log(bad ? `FAIL (${bad})` : 'PASS');
process.exit(bad ? 1 : 0);
