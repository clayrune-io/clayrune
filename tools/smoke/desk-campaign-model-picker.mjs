#!/usr/bin/env node
/**
 * Desk campaign Model / Effort pickers (Ron 2026-11-18). A fake server stands in for
 * /api/desk/*, /api/engine/* and /api/characters (the backend half is pinned by
 * tests/test_desk_campaign_engine.py); what is under test is what the Brief paints
 * and what it saves, at 1440 and 390 wide.
 *
 *   1. Placement -> a Model picker sits under the Agent picker; its first row reads
 *                   "Agent default (<resolved label>)"; the models are grouped by provider,
 *                   and a provider that cannot choose a model (or lists none) is not offered.
 *   2. No agent  -> with no agent picked the picker is disabled and says to pick one first.
 *   3. Pick      -> choosing a model PATCHes how.engine {provider, model}; the agent box on
 *                   the right reads "<agent> · <model label>".
 *   4. Effort    -> shown only for a provider with effort levels; choosing one PATCHes it;
 *                   switching to a provider that does not offer it resets it, with a note.
 *   5. Refused   -> a 400 from the server rolls the picker back and shows the server's reason.
 *   6. Default   -> picking "Agent default" PATCHes how WITHOUT engine, and the box drops the model.
 *   7. Fit       -> no sideways scroll, controls inside the viewport, >= 44px tall at 390.
 *
 * RUN   cd tools/smoke && node desk-campaign-model-picker.mjs
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
  { name: 'qwen', display_name: 'Qwen', model_selection: true, efforts: [], models: [] },
  { name: 'kiro', display_name: 'Kiro', model_selection: false, efforts: [], models: [] },
];

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [],
    presence: { replies: 'drafts', desk_agent: p.id === 'clayrune' ? 'global:claydo' : null, state: 'active',
      product_docs: null, product_materials: ['README.md'] } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  const srv = { log: [], projects, campaigns, fx, refuse: null };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
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
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '', engine: { provider: 'claude', model: 'claude-sonnet-5-5', effort: 'medium' } }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/engine/preferences') { srv.log.push({ method, path }); return J({ providers: PROVIDERS, default_provider: 'claude' }); }
    if (path === '/api/engine/preview') return J({ provider: 'claude', model: 'claude-sonnet-5-5', effort: 'medium' });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/research-capabilities') return J({ web: true, project_read: true, toolless: false, label: 'Can search the web' });
    const m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'PATCH') {
      if (srv.refuse) return J({ error: srv.refuse }, 400);
      const camp = srv.campaigns.find((c) => c.id === m[1]);
      if (camp && body.how) camp.how = body.how;
      return J(camp || {});
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
const openBrief = async (page, id) => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate(([cid]) => window.deskV1GotoCampaignPanel('how', { campaignId: cid }), [id]);
  await settle(page, () => !!document.querySelector('.desk-v1-how'));
};
const patches = (srv) => srv.log.filter((r) => r.method === 'PATCH' && /^\/api\/desk\/campaigns\//.test(r.path));
const lastEngine = (srv) => { const p = patches(srv).at(-1); return p ? (p.body.how || {}).engine : undefined; };
const modelReady = (page) => settle(page, () => { const s = document.querySelector('[data-how-model-select]'); return s && !s.disabled && s.options.length > 1; });
const boxText = (page) => page.$eval('.desk-v1-camp-posy .desk-thread-head', (e) => e.textContent.replace(/\s+/g, ' ').trim());

async function run(browser, width) {
  const tag = `[${width}]`;
  console.log(`\n── ${width}px ──`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, srv, { width, height: 950 });

  // 2. No agent anywhere: disabled, says why.
  const noAgent = srv.campaigns.find((c) => c.projectId !== 'clayrune' && c.projectId);
  if (noAgent) {
    await openBrief(page, noAgent.id);
    const st = await page.$eval('[data-how-model-select]', (s) => ({ disabled: s.disabled, text: s.textContent.trim() }));
    check(st.disabled && /Pick an agent first/.test(st.text), `${tag} 2 no agent: the Model picker is disabled ("${st.text}")`, `${tag} 2 no-agent state wrong: ${JSON.stringify(st)}`);
  } else fail(`${tag} 2 fixture has no campaign without an agent`);

  // 1. Placement, first row, grouping.
  await openBrief(page, 'camp-1');
  await modelReady(page);
  const layout = await page.evaluate(() => {
    const agent = document.querySelector('[data-how-agent]').getBoundingClientRect();
    const model = document.querySelector('[data-how-model-select]');
    const r = model.getBoundingClientRect();
    return { below: r.top >= agent.bottom - 1, first: model.options[0].textContent.trim(),
      groups: Array.from(model.querySelectorAll('optgroup')).map((g) => g.label),
      sel: model.value, h: r.height, inView: r.left >= 0 && r.right <= window.innerWidth };
  });
  check(layout.below, `${tag} 1 the Model picker sits under the Agent picker`, `${tag} 1 not under the Agent picker`);
  check(layout.first === 'Agent default (Sonnet 5.5)', `${tag} 1 first row is "${layout.first}"`, `${tag} 1 first row wrong: ${layout.first}`);
  check(JSON.stringify(layout.groups) === JSON.stringify(['Claude', 'Codex', 'Gemini']),
    `${tag} 1 models grouped by provider; qwen (no models) and kiro (no model choice) not offered`, `${tag} 1 groups wrong: ${JSON.stringify(layout.groups)}`);
  check(layout.sel === '' && (await boxText(page)).indexOf('·') < 0, `${tag} 1 no pick: "Agent default" selected, the agent box shows no model`, `${tag} 1 default state wrong`);
  check(layout.inView && (width > 960 || layout.h >= 44), `${tag} 7 the picker is inside the viewport (${Math.round(layout.h)}px tall)`, `${tag} 7 picker out of the viewport or too short: ${JSON.stringify(layout)}`);

  // 3. Pick a model.
  await page.selectOption('[data-how-model-select]', 'codex|gpt-6.2-sol');
  await settle(page, () => /GPT-6.2 Sol/.test(((document.querySelector('.desk-v1-camp-posy .desk-thread-head') || {}).textContent) || ''));
  const e1 = lastEngine(srv);
  check(e1 && e1.provider === 'codex' && e1.model === 'gpt-6.2-sol', `${tag} 3 the pick is saved as how.engine ${JSON.stringify(e1)}`, `${tag} 3 wrong payload: ${JSON.stringify(e1)}`);
  check(/Claydo\s*·\s*GPT-6\.2 Sol/.test(await boxText(page)), `${tag} 3 the agent box reads "${await boxText(page)}"`, `${tag} 3 agent box: ${await boxText(page)}`);

  // 4. Effort: only where the provider has levels.
  await modelReady(page);
  check(await page.$eval('[data-how-effort-row]', (e) => !e.hidden), `${tag} 4 Effort is offered for Codex`, `${tag} 4 no Effort row for Codex`);
  await page.selectOption('[data-how-effort-select]', 'high');
  await settle(page, () => (document.querySelector('[data-how-effort-select]') || {}).value === 'high');
  const e2 = lastEngine(srv);
  check(e2 && e2.effort === 'high' && e2.model === 'gpt-6.2-sol', `${tag} 4 effort saved beside the model ${JSON.stringify(e2)}`, `${tag} 4 wrong effort payload: ${JSON.stringify(e2)}`);
  await modelReady(page);
  await page.selectOption('[data-how-model-select]', 'gemini|gemini-pro-latest');
  await settle(page, () => { const r = document.querySelector('[data-how-effort-row]'); return r && r.hidden; });
  const e3 = lastEngine(srv);
  check(e3 && e3.provider === 'gemini' && !('effort' in e3), `${tag} 4 a provider without that effort drops it ${JSON.stringify(e3)}`, `${tag} 4 effort survived the provider switch: ${JSON.stringify(e3)}`);
  const toast = await page.evaluate(() => document.body.innerText);
  check(/is not offered by Gemini, so it was reset to the agent default/.test(toast), `${tag} 4 the reset is announced`, `${tag} 4 no reset note on screen`);

  // 5. Refused by the server: rolled back, reason shown.
  await modelReady(page);
  srv.refuse = 'Codex does not offer a model called "gpt-6.2-sol"';
  await page.selectOption('[data-how-model-select]', 'codex|gpt-6.2-sol');
  await settle(page, () => /does not offer a model called/.test(document.body.innerText));
  await modelReady(page);
  const kept = await page.$eval('[data-how-model-select]', (s) => s.value);
  check(kept === 'gemini|gemini-pro-latest', `${tag} 5 a refused pick rolls back to what was saved (${kept})`, `${tag} 5 picker kept the refused pick: ${kept}`);
  srv.refuse = null;

  // 6. Back to the agent default.
  await page.selectOption('[data-how-model-select]', '');
  await settle(page, () => (document.querySelector('[data-how-model-select]') || {}).value === '');
  const p6 = patches(srv).at(-1);
  check(p6 && !('engine' in p6.body.how), `${tag} 6 "Agent default" saves how without an engine`, `${tag} 6 engine still sent: ${JSON.stringify(p6 && p6.body.how)}`);
  check((await boxText(page)).indexOf('·') < 0, `${tag} 6 the agent box drops the model`, `${tag} 6 box still shows a model`);

  // 7. Fit.
  const overflow = await page.$eval('.desk-v1-how', (e) => e.scrollWidth > e.clientWidth + 1);
  check(!overflow, `${tag} 7 the Brief has no sideways scroll`, `${tag} 7 the Brief scrolls sideways`);
  if (process.env.SMOKE_SCREENSHOT_DIR) await page.screenshot({ path: resolve(process.env.SMOKE_SCREENSHOT_DIR, `model-picker-${width}.png`) });
  const real = pageErrors.filter((e) => !/ResizeObserver/.test(e));
  real.forEach((e) => fail(`${tag} page error: ${e}`));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const width of [1440, 390]) await run(browser, width);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — campaign Model / Effort pickers: placement, default row, groups, save, box label, effort reset, refusal rollback, fit.');
