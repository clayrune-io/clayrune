#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S9b = MC-1019) — generation engines on the page, against a fake
 * server with a MOCKED engine (no vendor is ever reached, nothing is spent), `desk_v1_live` ON.
 *
 * What is under test is which requests the browser makes and what it paints from the answers
 * (the engine layer itself is pinned by tests/test_desk_engines.py):
 *   1. Connections -> each engine shows connected or not BY VAULT NAME (no value anywhere), and
 *                     the per-job limit is saved through the passcode prompt.
 *   2. Studio video-> engine + model picker, the estimate BEFORE Render, a refusal shown with its
 *                     reason (Render disabled), Render goes through the passcode prompt, the job
 *                     is polled to ready and the saved library path shown.
 *   3. Guards      -> a price that moved since the user looked stops the render; a cancelled
 *                     passcode sends nothing.
 *   4. Studio image-> prompt + engine, estimate, Generate (passcode), the picture is drawn from
 *                     the library.
 *   5. Director    -> the video piece's director paints the engine panel (owner = the piece),
 *                     a campaign-budget refusal is visible, the fixture render card is gone.
 *   6. Flag OFF    -> no engine panel, no /api/desk/* request.
 *
 * RUN   cd tools/smoke && node desk-v1-live-render.mjs
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
  const srv = { log: [], boards: {}, fx, pieces, pics: {}, limits: { higgsfield: null, google: 2, openai: 5 }, renders: {}, jobs: {}, estCalls: 0, bump: 0, polls: {} };
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

const ENGINES = (limits) => [
  { id: 'higgsfield', label: 'Higgsfield', auth: { kind: 'key_id_secret', vault_entry: 'higgsfield' }, job_limit_usd: limits.higgsfield,
    connected: { ready: false, vault_entry: 'higgsfield', reason: "no vault entry named 'higgsfield' (add it in Secrets)" },
    models: [{ model_id: 'kling-2.5', kind: 'video', label: 'Kling 2.5 Turbo', status: 'stable', aspect_ratios: ['16:9', '9:16'] }] },
  { id: 'google', label: 'Google', auth: { kind: 'api_key', vault_entry: 'gemini-api' }, job_limit_usd: limits.google,
    connected: { ready: true, vault_entry: 'gemini-api', reason: null },
    models: [{ model_id: 'veo-3.1', kind: 'video', label: 'Veo 3.1', status: 'preview', aspect_ratios: ['16:9', '9:16'] },
      { model_id: 'veo-3.1-fast', kind: 'video', label: 'Veo 3.1 Fast', status: 'preview', aspect_ratios: ['16:9', '9:16'] },
      { model_id: 'gemini-img', kind: 'image', label: 'Gemini image', status: 'stable', aspect_ratios: ['1:1', '16:9', '9:16'] }] },
  { id: 'openai', label: 'OpenAI', auth: { kind: 'api_key', vault_entry: 'openai-api' }, job_limit_usd: limits.openai,
    connected: { ready: true, vault_entry: 'openai-api', reason: null },
    models: [{ model_id: 'gpt-image', kind: 'image', label: 'GPT image', status: 'stable', aspect_ratios: ['1:1'] }] },
];
const PERSCENE = { 'veo-3.1': 1.6, 'veo-3.1-fast': 0.4, 'kling-2.5': 0.2 };
const OUT_PIC = 'desk/library/image/Generated/job-1-0.png';

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
    let m;
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: ENGINES(srv.limits) });
    m = path.match(/^\/api\/desk\/engines\/([^/]+)\/limit$/);
    if (m && method === 'PUT') { srv.limits[m[1]] = body.job_limit_usd; return J({ engine_id: m[1], job_limit_usd: body.job_limit_usd }); }
    if (path === '/api/desk/engines/render/estimate') {
      srv.estCalls++;
      const eng = ENGINES(srv.limits).find((e) => e.id === body.engine_id);
      if (!eng.connected.ready) return J({ error: eng.connected.reason, code: 'not_connected', engine_id: eng.id, vault_entry: eng.connected.vault_entry }, 409);
      const key = (body.owner.kind === 'piece' ? 'piece:' : 'studio:') + body.owner.id;
      const n = (srv.boards[key] || { scenes: [] }).scenes.length;
      if (!n) return J({ error: 'this storyboard has no scenes to render: add one first', code: 'no_scenes' }, 409);
      const usd = Math.round((PERSCENE[body.model_id] * n + srv.bump) * 1e4) / 1e4;
      const out = { plan: { clips: n, total_usd: usd, crop: body.aspect_ratio === '1:1', needs_ffmpeg: n > 1, ffmpeg_available: true }, estimate: { usd, approximate: false }, job_limit_usd: srv.limits[body.engine_id], refusal: null };
      if (body.owner.kind === 'piece') {
        out.budget = { remaining: 1.0, amount: 5, spent: 4 };
        if (usd > 1.0) out.refusal = { code: 'over_budget', message: `estimate $${usd.toFixed(4)} is over the campaign's remaining budget $1.0000 (amount $5.00, spent $4.0000)` };
      }
      const lim = srv.limits[body.engine_id];
      if (!out.refusal && lim != null && usd > lim) out.refusal = { code: 'over_job_limit', message: `estimate $${usd.toFixed(4)} is over the $${lim.toFixed(2)} per-job limit for ${eng.label} (change it on Connections)` };
      return J(out);
    }
    if (path === '/api/desk/engines/renders' && method === 'POST') {
      const r = { render_id: 'rnd-1', kind: 'video', status: 'rendering', hold: null, failure: null, owner: body.owner, engine_id: body.engine_id, model_id: body.model_id,
        aspect_ratio: body.aspect_ratio, progress: { ready: 0, total: 2 }, scenes: [], outputs: [], clips: [], cost_usd: 0 };
      srv.renders[r.render_id] = r; srv.polls[r.render_id] = 0;
      return J({ render: r, replay: false }, 201);
    }
    if (path === '/api/desk/engines/renders' && method === 'GET') return J({ render: null });
    m = path.match(/^\/api\/desk\/engines\/renders\/([^/]+)$/);
    if (m && method === 'GET') {
      const r = srv.renders[m[1]];
      srv.polls[m[1]]++;
      if (srv.polls[m[1]] >= 2) Object.assign(r, { status: 'ready', progress: { ready: 2, total: 2 }, cost_usd: 0.8, outputs: [{ path: 'desk/library/video/Generated/rnd-1.mp4', mime: 'video/mp4', duration_sec: 6, attached_to: null }] });
      else r.progress = { ready: 1, total: 2 };
      return J({ render: r });
    }
    if (path === '/api/desk/engines/estimate') { srv.estCalls++; return J({ estimate: { usd: 0.04, approximate: false }, job_limit_usd: srv.limits[body.engine_id], refusal: null }); }
    if (path === '/api/desk/engines/jobs' && method === 'POST') {
      srv.jobs['job-1'] = { job_id: 'job-1', status: 'ready', kind: 'image', engine_id: body.engine_id, model_id: body.model_id, cost_usd: 0.04, outputs: [{ path: OUT_PIC, mime: 'image/png', src: SRC_PIC(OUT_PIC) }] };
      return J({ job: srv.jobs['job-1'], replay: false }, 201);
    }
    if (path === '/api/desk/studio/storyboards') {
      return J({ storyboards: Object.entries(srv.boards).filter(([k]) => k.startsWith('studio:')).map(([k, b]) => ({ id: k.slice(7), title: b.title, scenes: b.scenes.length, updated_at: '2026-10-01T10:00:00Z' })) });
    }
    m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
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
  await page.evaluate(() => {
    window.__proofs = [];
    window.humanProofFetch = async (url, init, proof) => {
      window.__proofs.push({ url, title: proof && proof.title, description: proof && proof.description });
      if (window.__cancelProof) return null;
      const r = await fetch(url, init);
      let b = null; try { b = await r.json(); } catch (_) { /* none */ }
      return { ok: r.ok, status: r.status, body: b };
    };
  });
  return { ctx, page, pageErrors };
}

const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 15000 });
const reqs = (srv, method, re) => srv.log.filter((r) => r.method === method && re.test(r.path));
const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const proofs = (page) => page.evaluate(() => window.__proofs);
const txt = (page, sel) => page.textContent(sel).then((t) => (t || '').replace(/\s+/g, ' ').trim()).catch(() => '');

async function openStudio(page, kind) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector(`[data-studio-new="${kind}"]`, { timeout: 8000 });
  await page.evaluate((k) => window.deskV1Nav('studio-create', { kind: k }), kind);
}
async function addScene(page, label) {
  const before = (await page.$$('[data-storyboard] .desk-v1-sb-scene')).length;
  await page.click('[data-scene-add]');
  await settle(page, (n) => document.querySelectorAll('[data-storyboard] .desk-v1-sb-scene').length === n + 1, before);
  await page.fill('[data-scene-edit-label]', label);
  await page.click('.desk-v1-sb-scene:has([data-scene-edit-label]) [data-scene-edit]');
  await settle(page, (l) => !!document.querySelector(`.desk-v1-sb-scene[data-scene-label="${l}"]`), label);
}
const estimateText = (page) => txt(page, '[data-eng-estimate]');
async function waitEstimate(page, state) {
  await settle(page, (s) => { const e = document.querySelector('[data-eng-estimate]'); return !!e && e.dataset.state === s; }, state);
}

// ── 1: Connections ─────────────────────────────────────────────────────────
async function connections(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('connections', {}));
  await page.waitForSelector('[data-conn-engine="higgsfield"]', { timeout: 8000 });
  const rows = await page.$$eval('[data-conn-engine]', (els) => els.map((e) => e.dataset.connEngine));
  JSON.stringify(rows) === '["higgsfield","google","openai"]' ? ok('Connections lists the 3 engines') : fail('engines: ' + JSON.stringify(rows));
  const hs = await txt(page, '[data-conn-engine="higgsfield"] [data-engine-status]');
  (/^Not connected$/.test(hs)) ? ok('an unconnected engine says so in plain words (no vault or Secrets jargon): "' + hs + '"') : fail('higgsfield status: ' + hs);
  const gs = await txt(page, '[data-conn-engine="google"] [data-engine-status]');
  (/^Connected$/.test(gs)) ? ok('a connected engine says Connected: "' + gs + '"') : fail('google status: ' + gs);
  const gb = async (id) => txt(page, `[data-conn-engine="${id}"] [data-engine-guide]`);
  (await gb('higgsfield')) === 'Connect' && (await gb('google')) === 'Replace key'
    ? ok('an unconnected engine offers Connect (the guided steps), a connected one Replace key') : fail('Connect/Replace buttons: ' + (await gb('higgsfield')) + ' / ' + (await gb('google')));
  (await page.$$('[data-conn-section="engines"] input[type="password"], [data-conn-section="engines"] input[type="text"]')).length === 0
    ? ok('the engines section has no credential field') : fail('a text/password field is in the engines section');
  (await page.inputValue('[data-conn-engine="google"] [data-engine-limit-input]')) === '2' ? ok("Google's saved per-job limit (2) is shown") : fail('google limit value');

  await page.fill('[data-conn-engine="higgsfield"] [data-engine-limit-input]', '3');
  await page.click('[data-conn-engine="higgsfield"] [data-engine-limit-save]');
  await settle(page, () => /over \$3 /.test(document.querySelector('[data-conn-engine="higgsfield"] [data-engine-limit-note]').textContent));
  const put = reqs(srv, 'PUT', /\/engines\/higgsfield\/limit$/);
  const pf = await proofs(page);
  (put.length === 1 && put[0].body.job_limit_usd === 3 && pf.length === 1 && /Set the per-job limit/.test(pf[0].title) && /Higgsfield/.test(pf[0].description))
    ? ok('saving a limit is one PUT through the passcode prompt (human-only)') : fail('limit save: ' + JSON.stringify({ put: put.map((r) => r.body), pf }));
  (await txt(page, '[data-conn-engine="higgsfield"] [data-engine-limit-note]')).includes('$3') ? ok('the row now says renders over $3 are refused') : fail('note after save');

  await page.fill('[data-conn-engine="openai"] [data-engine-limit-input]', '-4');
  await page.click('[data-conn-engine="openai"] [data-engine-limit-save]');
  const bad = await txt(page, '[data-conn-engine="openai"] [data-engine-limit-note]');
  (/above 0/.test(bad) && reqs(srv, 'PUT', /openai\/limit$/).length === 0) ? ok('a limit of -4 is refused in the page, nothing sent') : fail('bad limit: ' + bad);

  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 2+3: Studio video, the estimate, the refusal, Render, the guards ──────
async function studioVideo(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(page, 'video');
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  (await page.$('[data-sb-render]')) ? fail('the fixture Render button is still painted live') : ok('the fixture Render button is gone live (the engine panel is the only Render)');
  await page.waitForSelector('[data-eng-panel] [data-eng-engine]', { timeout: 8000 });
  await waitEstimate(page, 'refused');
  (/no scenes/.test(await estimateText(page)) && (await page.$('[data-eng-render-btn][disabled]')))
    ? ok('an empty storyboard: the reason is shown and Render is disabled') : fail('empty board: ' + (await estimateText(page)));

  await addScene(page, 'Opening');
  await addScene(page, 'Closing');
  // The price follows the scenes; the picker defaults to the first CONNECTED engine.
  await settle(page, () => /3\.2/.test((document.querySelector('[data-eng-usd]') || {}).textContent || '') || /per-job limit/.test((document.querySelector('[data-eng-estimate]') || {}).textContent || ''), null);
  (await page.inputValue('[data-eng-engine]')) === 'google' ? ok('the engine picker defaults to the first connected engine (Google, not the unconnected Higgsfield)') : fail('default engine');
  const opts = await page.$$eval('[data-eng-engine] option', (os) => os.map((o) => o.textContent));
  opts.some((o) => /Higgsfield \(not connected\)/.test(o)) ? ok('an unconnected engine is labelled in the picker') : fail('picker labels: ' + JSON.stringify(opts));
  const models = await page.$$eval('[data-eng-model] option', (os) => os.map((o) => o.value));
  JSON.stringify(models) === '["veo-3.1","veo-3.1-fast"]' ? ok('the model picker lists only that engine\'s VIDEO models') : fail('models: ' + JSON.stringify(models));

  // 3.2 for 2 scenes at Veo 3.1 is over Google's $2 limit: refused, with the reason, before Render.
  await waitEstimate(page, 'refused');
  const reason = await txt(page, '[data-eng-reason]');
  (/3\.2000/.test(reason) && /\$2\.00 per-job limit/.test(reason) && (await page.$('[data-eng-render-btn][disabled]')))
    ? ok('over the per-job limit: the reason is visible and Render is disabled: "' + reason + '"') : fail('over-limit reason: ' + reason);
  const est = reqs(srv, 'POST', /render\/estimate$/).pop();
  (est && est.body.owner.kind === 'studio' && est.body.engine_id === 'google' && est.body.model_id === 'veo-3.1' && est.body.aspect_ratio === '16:9')
    ? ok('the estimate request names the owner, engine, model and shape') : fail('estimate body: ' + JSON.stringify(est && est.body));

  // Higgsfield is not connected: the server's reason is shown, nothing can be rendered.
  await page.selectOption('[data-eng-engine]', 'higgsfield');
  await waitEstimate(page, 'refused');
  (/no vault entry named 'higgsfield'/.test(await txt(page, '[data-eng-estimate]')) && /not connected/.test(await txt(page, '[data-eng-not-connected]')))
    ? ok('an unconnected engine is refused with its vault reason; Render stays disabled') : fail('not connected: ' + (await estimateText(page)));
  await page.selectOption('[data-eng-engine]', 'google');
  await page.selectOption('[data-eng-model]', 'veo-3.1-fast');
  await waitEstimate(page, 'ok');
  const usd = await txt(page, '[data-eng-usd]');
  (usd === '$0.8' && /2 clips, then joined/.test(await estimateText(page)) && /limit \$2 per job/.test(await estimateText(page)))
    ? ok('within the limit: the estimate (' + usd + ', 2 clips joined, the limit) is shown BEFORE Render') : fail('estimate: ' + usd + ' / ' + (await estimateText(page)));
  const btn = await txt(page, '[data-eng-render-btn]');
  (!(await page.$('[data-eng-render-btn][disabled]')) && /Render · \$0\.8/.test(btn)) ? ok('Render is enabled and carries the price: "' + btn + '"') : fail('render button: ' + btn);

  // Guard: the storyboard changed (price moved) after the user looked.
  srv.bump = 0.5;
  srv.log.length = 0;
  await page.click('[data-eng-render-btn]');
  await settle(page, () => /price changed/.test((document.querySelector('[data-eng-error]') || {}).textContent || ''));
  (reqs(srv, 'POST', /\/engines\/renders$/).length === 0 && (await proofs(page)).length === 0)
    ? ok('the price moved since it was shown: no passcode prompt, no render, the user is told the new price') : fail('price-moved guard');
  srv.bump = 0;
  await page.click('[data-eng-reprice]');
  await waitEstimate(page, 'ok');

  // Guard: a cancelled passcode sends nothing.
  await page.evaluate(() => { window.__cancelProof = true; });
  srv.log.length = 0;
  await page.click('[data-eng-render-btn]');
  await settle(page, () => /passcode was not entered/.test((document.querySelector('[data-eng-error]') || {}).textContent || ''));
  (reqs(srv, 'POST', /\/engines\/renders$/).length === 0) ? ok('a cancelled passcode prompt sends no render') : fail('render sent without a passcode');
  await page.evaluate(() => { window.__cancelProof = false; window.__proofs.length = 0; });

  // Render for real (mocked engine).
  srv.log.length = 0;
  await page.click('[data-eng-render-btn]');
  await settle(page, () => !!document.querySelector('[data-eng-render-status]'));
  const post = reqs(srv, 'POST', /\/engines\/renders$/);
  const pf = await proofs(page);
  (post.length === 1 && post[0].body.owner.kind === 'studio' && post[0].body.engine_id === 'google' && post[0].body.model_id === 'veo-3.1-fast'
    && /^[a-z0-9]{8,}$/.test(post[0].body.idempotency_key) && pf.length === 1 && /Render this video/.test(pf[0].title) && /Google/.test(pf[0].description) && /\$0\.8/.test(pf[0].description))
    ? ok('Render is one POST through the passcode prompt (price in the prompt), with an idempotency key') : fail('render POST: ' + JSON.stringify({ post: post.map((r) => r.body), pf }));
  (/pass|secret|key"/i.test(JSON.stringify(post[0].body).replace(/idempotency_key/g, ''))) ? fail('the render body carries something credential-shaped') : ok('the render body carries no credential');
  await settle(page, () => /Rendering/.test((document.querySelector('[data-eng-render-status]') || {}).textContent || ''));
  ok('progress is shown while the job runs: "' + (await txt(page, '[data-eng-render-status]')) + '"');
  await settle(page, () => (document.querySelector('[data-eng-render-status]') || {}).dataset.status === 'ready');
  const done = await txt(page, '[data-eng-render]');
  (/Ready · 2\/2 clips/.test(done) && /rnd-1\.mp4/.test(done) && /saved to the Material library/.test(done) && /\$0\.8 spent/.test(done))
    ? ok('ready: the library file and the spend are shown: "' + done + '"') : fail('ready view: ' + done);
  (reqs(srv, 'GET', /\/engines\/renders\/rnd-1$/).length >= 2) ? ok('the job was polled (GET) until ready') : fail('poll count');
  (await page.$('[data-eng-render-btn][disabled]')) ? fail('Render stays disabled after the job finished') : ok('after a finished render the panel is usable again');

  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 4: Studio image ────────────────────────────────────────────────────────
async function studioImage(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(page, 'image');
  await page.waitForSelector('[data-sc-source="engine"]', { timeout: 8000 });
  await page.click('[data-sc-source="engine"]');
  await page.waitForSelector('[data-eng-panel][data-kind="image"] [data-eng-prompt]', { timeout: 8000 });
  const models = await page.$$eval('[data-eng-model] option', (os) => os.map((o) => o.value));
  JSON.stringify(models) === '["gemini-img"]' ? ok('the image panel lists only image models (no Veo)') : fail('image models: ' + JSON.stringify(models));
  (await page.$('[data-eng-render-btn][disabled]')) ? ok('Generate is disabled until there is a description') : fail('Generate enabled with no prompt');
  await page.fill('[data-eng-prompt]', 'A calm abstract gradient');
  await page.press('[data-eng-prompt]', 'Tab');
  await waitEstimate(page, 'ok');
  const est = reqs(srv, 'POST', /\/engines\/estimate$/).pop();
  (est && est.body.kind === 'image' && est.body.prompt === 'A calm abstract gradient' && est.body.engine_id === 'google')
    ? ok('the estimate is requested for the prompt before Generate') : fail('image estimate: ' + JSON.stringify(est && est.body));
  srv.log.length = 0;
  await page.click('[data-eng-render-btn]');
  await settle(page, () => !!document.querySelector('[data-eng-output] img'));
  const post = reqs(srv, 'POST', /\/engines\/jobs$/);
  const pf = await proofs(page);
  (post.length === 1 && post[0].body.kind === 'image' && post[0].body.desk && post[0].body.desk.idempotency_key && pf.length === 1 && /Generate this picture/.test(pf[0].title))
    ? ok('Generate is one POST through the passcode prompt') : fail('image POST: ' + JSON.stringify({ post: post.map((r) => r.body), pf }));
  const src = await page.$eval('[data-eng-output] img', (i) => i.getAttribute('src'));
  (src === SRC_PIC(OUT_PIC) && /Saved to the Material library: desk\/library\/image\/Generated/.test(await txt(page, '[data-eng-output]')))
    ? ok("the picture is drawn from the library path the server answered with, and its location is shown") : fail('image output: ' + src);
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 5: Director ────────────────────────────────────────────────────────────
async function director(browser) {
  const srv = makeServer();
  srv.boards['piece:fam-install-video'] = { rev: 1, title: '', pending_edits: [], scenes: [
    { id: 'sc-a', label: 'Hero shot', line: '', duration_sec: 3, picture: null, edited: false },
    { id: 'sc-b', label: 'Outro', line: '', duration_sec: 3, picture: null, edited: false }] };
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
  await page.waitForSelector('[data-eng-panel] [data-eng-engine]', { timeout: 8000 });
  (await page.$('[data-render-btn]')) || (await page.$('.desk-v1-video-rendercard')) ? fail('the fixture render card is painted live') : ok('the fixture render card (made-up rate and budget) is gone live');
  await waitEstimate(page, 'refused');
  const first = reqs(srv, 'POST', /render\/estimate$/).pop();
  (first && first.body.owner.kind === 'piece' && first.body.owner.id === 'fam-install-video') ? ok('the estimate is for the piece (so the server applies the campaign budget)') : fail('director estimate: ' + JSON.stringify(first && first.body));
  // Veo 3.1: 2 x 1.6 = 3.2, the campaign has 1.0 left.
  const reason = await txt(page, '[data-eng-reason]');
  (/remaining budget \$1\.0000/.test(reason) && (await page.$('[data-eng-render-btn][disabled]')))
    ? ok('over the campaign budget: the reason is visible and Render is disabled: "' + reason + '"') : fail('budget reason: ' + reason);
  (/campaign budget left \$1/.test(await estimateText(page))) ? ok('what is left of the campaign budget is shown beside the estimate') : fail('budget line: ' + (await estimateText(page)));
  await page.selectOption('[data-eng-model]', 'veo-3.1-fast');
  await waitEstimate(page, 'ok');
  (!(await page.$('[data-eng-render-btn][disabled]'))) ? ok('a render that fits the campaign budget is enabled') : fail('render disabled though it fits');
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

// ── 6: flag OFF ────────────────────────────────────────────────────────────
async function flagOff(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: false, srv });
  await page.waitForFunction(() => !!window.DeskV1Fixtures, null, { timeout: 8000 });
  srv.log.length = 0;
  await openStudio(page, 'video');
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  (await page.$('[data-eng-panel]')) ? fail('demo storyboard grew an engine panel') : ok('demo storyboard has no engine panel (it keeps its fixture Render)');
  (await page.$('[data-sb-render]')) ? ok('demo keeps the fixture Render button') : fail('demo lost its Render button');
  await page.evaluate(() => window.deskV1Nav('video', { campaignId: 'camp-1', familyId: 'fam-install-video' }));
  await page.waitForSelector('.desk-v1-video-director', { timeout: 8000 });
  (await page.$('[data-eng-panel]')) ? fail('demo director grew an engine panel') : ok('demo director keeps its fixture render card');
  await page.evaluate(() => window.deskV1Nav('connections', {}));
  await page.waitForSelector('[data-conn-section="engines"]', { timeout: 8000 });
  await page.waitForTimeout(400);
  (await page.$('[data-conn-engine]')) ? fail('demo Connections lists engines') : ok('demo Connections has no engine rows (nothing there can spend)');
  const n = srv.log.filter((r) => r.path.startsWith('/api/desk/') && !/engagement\/coverage/.test(r.path)).length;
  n === 0 ? ok('flag OFF: 0 /api/desk/* requests from the storyboard, director and Connections (the engagement coverage poll belongs to the shell)'): fail(`flag OFF made ${n} /api/desk requests: ` + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  realErrors(pageErrors).forEach((e) => fail('page error: ' + e));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('Connections: engines and per-job limits');
  await connections(browser);
  console.log('Studio video: estimate, refusal, Render, progress, guards');
  await studioVideo(browser);
  console.log('Studio image');
  await studioImage(browser);
  console.log('Director');
  await director(browser);
  console.log('Flag OFF');
  await flagOff(browser);
} catch (e) {
  fail('smoke crashed: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-v1-live-render checks passed');
process.exit(bad ? 1 : 0);
