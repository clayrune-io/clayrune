#!/usr/bin/env node
/**
 * Desk Studio Model / Effort pickers (Ron 2026-11-18: "missing those settings under the
 * studio"). A fake server stands in for /api/desk/*, /api/engine/* and /api/characters
 * (the backend half is pinned by tests/test_desk_studio_engine.py); what is under test is
 * what the Studio agent box paints and what the page sends, at 1440 and 390 wide.
 *
 *   1. Video, standalone -> a Model picker sits directly under the Agent picker, first row
 *                           "Agent default (<label>)", models grouped by provider.
 *   2. Pick + send       -> choosing a model and an effort changes the picker, and the
 *                           storyboard call carries `engine` beside the `owner` (and a story chat
 *                           the same); Undo (header) puts the pick back one step at a time.
 *   3. Change the agent  -> an effort-only pick the new agent's provider does not offer is
 *                           dropped with a visible note, and the next call carries no engine.
 *   4. Refused           -> a 409 from the server shows its reason; nothing else is run.
 *   5. Video in campaign -> the pick is the CAMPAIGN's, read-only ("Set on the campaign's
 *                           Brief"); no select; the call carries the owner and NO engine.
 *   6. Article           -> the picker sits under the agent's name; a pick is saved with the
 *                           article (PUT carries `engine`) and survives a reopen; an article
 *                           for a campaign shows the campaign's pick read-only.
 *   7. Fit               -> no sideways scroll, controls inside the viewport, >= 44px at 390.
 *
 * RUN   cd tools/smoke && node desk-studio-model-picker.mjs
 */
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss, deskLoaded } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg));

const PROVIDERS = [
  { name: 'claude', display_name: 'Claude', model_selection: true, efforts: ['low', 'medium', 'high'],
    models: [{ id: 'claude-sonnet-5-5', label: 'Sonnet 5.5' }, { id: 'claude-opus-5-5', label: 'Opus 5.5' }] },
  { name: 'codex', display_name: 'Codex', model_selection: true, efforts: ['low', 'medium', 'high'],
    models: [{ id: 'gpt-6.2-sol', label: 'GPT-6.2 Sol' }] },
  { name: 'gemini', display_name: 'Gemini', model_selection: true, efforts: [],
    models: [{ id: 'gemini-pro-latest', label: 'Gemini Pro' }] },
];
const CHARACTERS = [
  { name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '', engine: { provider: 'claude', model: 'claude-sonnet-5-5', effort: 'medium' } },
  { name: 'gem', scope: 'global', agent_name: 'Gem', avatar: '', engine: { provider: 'gemini', model: 'gemini-pro-latest' } },
  { name: 'planner', scope: 'global', agent_name: 'The Planner', avatar: '', engine: { provider: 'claude', model: 'claude-sonnet-5-5', effort: 'medium' } },
];
const CODEX = { provider: 'codex', model: 'gpt-6.2-sol', effort: 'high' };
const VIDEO_CAMPAIGN = 'camp-1';
const VIDEO_PIECE = 'fam-install-video';

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [],
    presence: { replies: 'drafts', desk_agent: 'global:planner', state: 'active', product_docs: null, product_materials: [] } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const camp = campaigns.find((c) => c.id === VIDEO_CAMPAIGN);
  camp.how = Object.assign({}, camp.how, { agent: 'global:planner', engine: CODEX });
  const pieces = fx.families.map((f) => JSON.parse(JSON.stringify(f)));
  const srv = { log: [], projects, campaigns, pieces, fx, articles: {}, boards: {}, refuseGenerate: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))),
    pieces: pieces.map((p) => JSON.parse(JSON.stringify(p))) });
  return srv;
}

async function newPage(browser, srv, viewport) {
  const ctx = await browser.newContext({ viewport });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const path = new URL(req.url()).pathname;
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
    if (path === '/api/characters') return J(CHARACTERS);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/engine/preferences') return J({ providers: PROVIDERS, default_provider: 'claude' });
    if (path === '/api/engine/preview') {
      let b = null; try { b = req.postDataJSON(); } catch (_) { /* none */ }
      const e = (b && b.character) || {};
      return J({ provider: e.provider || 'claude', model: e.model || 'claude-sonnet-5-5', effort: e.effort || '' });
    }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    if (path === '/api/desk/research-capabilities') return J({ web: true, project_read: true, toolless: false, label: 'Can search the web' });
    if (path === '/api/desk/storyboard/generate' || path === '/api/desk/storyboard/chat') {
      if (srv.refuseGenerate) return J({ error: srv.refuseGenerate }, 409);
      return J({ error: 'smoke: the model is not run here' }, 502);
    }
    let m = path.match(/^\/api\/desk\/(pieces|studio)\/([^/]+)\/storyboard$/);
    if (m) {
      const key = m[1] + ':' + m[2];
      const cur = srv.boards[key] || { rev: 0, scenes: [], pending_edits: [], title: '', story: '' };
      if (method === 'GET') return J(cur);
      if (method === 'PUT') {
        srv.boards[key] = { ...cur, rev: cur.rev + 1, scenes: body.scenes || [], title: body.title || cur.title, story: body.story != null ? body.story : cur.story };
        return J(srv.boards[key]);
      }
    }
    if (path === '/api/desk/studio/articles') return J({ articles: Object.values(srv.articles) });
    m = path.match(/^\/api\/desk\/studio\/articles\/([^/]+)(\/chat)?$/);
    if (m && !m[2]) {
      const id = m[1];
      if (method === 'GET') return J(srv.articles[id] || { error: 'not found' }, srv.articles[id] ? 200 : 404);
      if (method === 'PUT') {
        const a = srv.articles[id];
        if (a && a.rev !== body.rev) return J({ error: 'stale revision' }, 409);
        const next = { ...body, id, rev: body.rev + 1, attached: false };
        if (!body.engine) delete next.engine;
        srv.articles[id] = next;
        return J(next);
      }
    }
    if (m && m[2] && method === 'GET') return J({ thread: [] });
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
const calls = (srv, re) => srv.log.filter((r) => r.method === 'POST' && re.test(r.path));
const MODEL = '[data-studio-model-host] [data-how-model-select]';
const EFFORT = '[data-studio-model-host] [data-how-effort-select]';
const modelReady = (page) => settle(page, (sel) => { const s = document.querySelector(sel); return s && !s.disabled && s.options.length > 1; }, MODEL);
const sel = (page, selector) => page.$eval(selector, (s) => s.value);

async function make(page, srv, story) {
  await page.fill('[data-sb-story]', story);
  await page.click('[data-story-make]');
  await settle(page, () => /smoke: the model is not run here|Nothing was run|cannot run/.test((document.querySelector('[data-story-status]') || {}).textContent || ''));
  return calls(srv, /storyboard\/generate$/).at(-1);
}

async function run(browser, width) {
  const tag = `[${width}]`;
  console.log(`\n── ${width}px ──`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width, height: 950 });

  // 1. Standalone video: placement.
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio-new="video"]', { timeout: 8000 });
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'video' }));
  await page.waitForSelector('[data-studio-create][data-kind="video"] [data-storyboard]', { timeout: 8000 });
  await settle(page, () => { const s = document.querySelector('[data-sb-agent-pick]'); return s && !s.disabled && s.options.length > 1; });
  await modelReady(page);
  const layout = await page.evaluate((m) => {
    const agent = document.querySelector('[data-sb-agent-pick]').getBoundingClientRect();
    const model = document.querySelector(m);
    const r = model.getBoundingClientRect();
    return { below: r.top >= agent.bottom - 1, first: model.options[0].textContent.trim(),
      groups: Array.from(model.querySelectorAll('optgroup')).map((g) => g.label), h: r.height, inView: r.left >= 0 && r.right <= window.innerWidth };
  }, MODEL);
  check(layout.below, `${tag} 1 the Model picker sits directly under the Agent picker`, `${tag} 1 not under the Agent picker: ${JSON.stringify(layout)}`);
  check(layout.first === 'Agent default (Sonnet 5.5)', `${tag} 1 first row is "${layout.first}"`, `${tag} 1 first row wrong: ${layout.first}`);
  check(JSON.stringify(layout.groups) === JSON.stringify(['Claude', 'Codex', 'Gemini']), `${tag} 1 models grouped by provider`, `${tag} 1 groups wrong: ${JSON.stringify(layout.groups)}`);
  check(layout.inView && (width > 960 || layout.h >= 44), `${tag} 7 the picker is inside the viewport (${Math.round(layout.h)}px tall)`, `${tag} 7 picker out of the viewport or too short: ${JSON.stringify(layout)}`);

  // 2. Pick a model and an effort; the next storyboard call carries it.
  await page.selectOption(MODEL, 'codex|gpt-6.2-sol');
  await settle(page, (s) => document.querySelector(s) && document.querySelector(s).value === 'codex|gpt-6.2-sol' && !document.querySelector(s).disabled, MODEL);
  await settle(page, (s) => { const e = document.querySelector(s); return e && !e.disabled; }, EFFORT);
  await page.selectOption(EFFORT, 'high');
  await settle(page, (s) => { const e = document.querySelector(s); return e && !e.disabled && e.value === 'high'; }, EFFORT);
  const g1 = await make(page, srv, 'A short story about a desk.');
  check(g1 && JSON.stringify(g1.body.engine) === JSON.stringify(CODEX) && g1.body.owner.kind === 'studio',
    `${tag} 2 the storyboard call carries engine ${JSON.stringify(g1 && g1.body.engine)} beside owner studio`, `${tag} 2 wrong generate body: ${JSON.stringify(g1 && g1.body)}`);
  await page.click('#desk-v1-undo');
  await settle(page, (s) => { const e = document.querySelector(s); return e && !e.disabled && e.value === ''; }, EFFORT);
  check(await sel(page, MODEL) === 'codex|gpt-6.2-sol', `${tag} 2 Undo takes the effort back and keeps the model`, `${tag} 2 Undo did not step back one change`);
  await page.click('#desk-v1-undo');
  await settle(page, (s) => { const e = document.querySelector(s); return e && !e.disabled && e.value === ''; }, MODEL);
  check(true, `${tag} 2 a second Undo puts the model back to Agent default`, '');
  const g2 = await make(page, srv, 'A short story about a desk, again.');
  check(g2 && !('engine' in g2.body), `${tag} 2 with no pick the call carries no engine`, `${tag} 2 engine still sent: ${JSON.stringify(g2 && g2.body.engine)}`);

  // 3. Change the agent under an effort-only pick.
  await page.selectOption(EFFORT, 'high');
  await settle(page, (s) => { const e = document.querySelector(s); return e && !e.disabled && e.value === 'high'; }, EFFORT);
  await page.selectOption('[data-sb-agent-pick]', 'global:gem');
  await settle(page, () => /is not offered by Gemini, so it was reset to the agent default/.test(document.body.innerText));
  ok(`${tag} 3 the stale effort is dropped with a visible note`);
  await settle(page, () => { const r = document.querySelector('[data-studio-model-host] [data-how-effort-row]'); return r && r.hidden; });
  const g3 = await make(page, srv, 'A short story about a window.');
  check(g3 && !('engine' in g3.body) && g3.body.agent === 'global:gem', `${tag} 3 the next call carries the new agent and no engine`, `${tag} 3 wrong body after the agent change: ${JSON.stringify(g3 && g3.body)}`);

  // 4. Refused by the server: the reason is shown.
  srv.refuseGenerate = 'this item is set to run on codex / gpt-6.2-sol, which cannot run now: codex is not signed in. Nothing was run on another engine.';
  await make(page, srv, 'A short story about a door.').catch(() => null);
  check(/Nothing was run on another engine/.test(await page.$eval('[data-story-status]', (e) => e.textContent)), `${tag} 4 the server's reason is shown`, `${tag} 4 no refusal reason on screen`);
  srv.refuseGenerate = null;

  // 5. A video that belongs to a campaign: the campaign's pick, read-only.
  await page.evaluate(([c, f]) => window.deskV1Nav('storyboard', { campaignId: c, familyId: f }), [VIDEO_CAMPAIGN, VIDEO_PIECE]);
  await page.waitForSelector('[data-storyboard] [data-studio-model-host] [data-how-model-readonly]', { timeout: 8000 });
  const ro = await page.$eval('[data-studio-model-host]', (e) => ({ text: e.textContent.replace(/\s+/g, ' ').trim(), selects: e.querySelectorAll('select').length }));
  check(/GPT-6\.2 Sol · high effort/.test(ro.text) && /Set on the campaign's Brief/.test(ro.text) && ro.selects === 0,
    `${tag} 5 a campaign video shows the campaign's pick read-only ("${ro.text}")`, `${tag} 5 read-only box wrong: ${JSON.stringify(ro)}`);
  await settle(page, () => { const t = document.querySelector('[data-sb-story]'); return t && !t.disabled; });
  const g5 = await make(page, srv, 'A short story about a campaign.');
  check(g5 && g5.body.owner.kind === 'piece' && !('engine' in g5.body), `${tag} 5 the call carries the piece owner (the server finds the campaign's pick) and no engine`, `${tag} 5 wrong body: ${JSON.stringify(g5 && g5.body)}`);

  // 6. A standalone article.
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'article' }));
  await page.fill('[data-sa-topic]', 'Why local tools matter');
  await page.selectOption('[data-sa-project]', srv.projects[0].id);
  await page.click('[data-sa-start]');
  await page.waitForSelector('[data-sa-agent] [data-studio-model-host]', { timeout: 8000 });
  await settle(page, () => { const s = document.querySelector('[data-sa-agent] [data-how-model-select]'); return s && !s.disabled && s.options.length > 1; });
  const art = await page.evaluate(() => {
    const head = document.querySelector('[data-sa-agent] .desk-thread-head').getBoundingClientRect();
    const m = document.querySelector('[data-sa-agent] [data-how-model-select]').getBoundingClientRect();
    return { below: m.top >= head.bottom - 1, inView: m.left >= 0 && m.right <= window.innerWidth, h: m.height };
  });
  check(art.below && art.inView && (width > 960 || art.h >= 44), `${tag} 6 the article's Model picker sits under the agent's name, inside the viewport`, `${tag} 6 article picker misplaced: ${JSON.stringify(art)}`);
  await page.selectOption('[data-sa-agent] [data-how-model-select]', 'codex|gpt-6.2-sol');
  await page.waitForFunction(() => { const s = document.querySelector('[data-sa-agent] [data-how-model-select]'); return s && !s.disabled && s.value === 'codex|gpt-6.2-sol'; }, null, { timeout: 8000 });
  const put = srv.log.filter((r) => r.method === 'PUT' && /studio\/articles\//.test(r.path)).at(-1);
  check(put && put.body.engine && put.body.engine.provider === 'codex' && put.body.engine.model === 'gpt-6.2-sol',
    `${tag} 6 the article is saved with its pick ${JSON.stringify(put && put.body.engine)}`, `${tag} 6 article PUT has no engine: ${JSON.stringify(put && put.body)}`);
  const id = await page.getAttribute('[data-view="writer"]', 'data-item-id');
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.evaluate((i) => window.deskV1Nav('studio-create', { itemId: i }), id);
  await page.waitForFunction(() => { const s = document.querySelector('[data-sa-agent] [data-how-model-select]'); return s && !s.disabled && s.value === 'codex|gpt-6.2-sol'; }, null, { timeout: 8000 });
  ok(`${tag} 6 the pick is still there after reopening the article`);
  // An article for a campaign runs on the campaign's pick.
  await page.evaluate(() => window.deskV1Nav('studio-create', { kind: 'article' }));
  await page.fill('[data-sa-topic]', 'A campaign article');
  await page.selectOption('[data-sa-campaign]', VIDEO_CAMPAIGN);
  await page.click('[data-sa-start]');
  await page.waitForSelector('[data-sa-agent] [data-how-model-readonly]', { timeout: 8000 });
  const aro = await page.$eval('[data-sa-agent] [data-studio-model-host]', (e) => ({ text: e.textContent.replace(/\s+/g, ' ').trim(), selects: e.querySelectorAll('select').length }));
  check(/GPT-6\.2 Sol · high effort/.test(aro.text) && /Set on the campaign's Brief/.test(aro.text) && aro.selects === 0,
    `${tag} 6 an article for a campaign shows the campaign's pick read-only ("${aro.text}")`, `${tag} 6 campaign article box wrong: ${JSON.stringify(aro)}`);

  // 7. Fit.
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  check(!overflow, `${tag} 7 the page has no sideways scroll`, `${tag} 7 the page scrolls sideways`);
  if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: resolve(process.env.SMOKE_SCREENSHOT_DIR, `studio-model-picker-${width}.png`) });
  const real = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource|ResizeObserver/i.test(e));
  real.forEach((e) => fail(`${tag} page error: ${e}`));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) await run(browser, width);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — Studio Model / Effort pickers: placement, pick + send, Undo, agent change, refusal, campaign read-only, article save + reopen, fit.');
