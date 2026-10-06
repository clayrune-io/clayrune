#!/usr/bin/env node
/**
 * Desk v1 — Studio's standalone article (Ron 2026-10-06: "an article should be
 * possible for ANY project and ANY topic, even one that does not exist yet as a
 * campaign"). static/js/desk-v1-studio-article.js, mc/desk_studio_articles.py.
 *
 * DEMO (fixtures only, hermetic):
 *   - New article opens a page like New video: Topic (required), Project (any,
 *     optional), Campaign (optional); NO campaign menu
 *   - Start writing is disabled until a topic is typed; picking a campaign fills
 *     its project in; picking a project drops a campaign from another project
 *   - the writer opens with no campaign: provenance names no campaign, buttons
 *     read `Save draft` / `Back to Studio`, one `Draft` tab
 *   - the draft is listed in Studio Recent (`not attached`) and reopens from there
 *   - Use in a campaign: a campaign menu, then the draft becomes an article piece
 *     in that campaign's What carrying the typed text
 *   - the demo never calls the server
 * LIVE (fake server):
 *   - Start writing PUTs the draft at rev 0, typing PUTs it again at rev 1 (debounced)
 *   - Use in a campaign POSTs /attach with the campaign and the piece id the page made
 *   - the Recent bin DELETEs it, Undo POSTs the trash token
 *   - a refused first save keeps the form (no half-made draft)
 * Both at 1440 and 390 (no horizontal overflow, 44px controls on the phone).
 *
 * RUN   cd tools/smoke && node desk-v1-studio-article.mjs
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

const PROJECT = (p) => ({
  id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
  next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
  last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
  provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
  distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
});

async function newPage(browser, { live, srv, viewport }) {
  const fx = loadFixtures();
  const ctx = await browser.newContext({ viewport: viewport || { width: 1440, height: 950 } });
  const page = await ctx.newPage();
  if (!live) await installDemoFixtures(page);
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  const calls = [];
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
    if (!path.startsWith('/api/desk/')) return route.abort();
    calls.push({ method, path, query: url.search });
    if (!live || !srv) return route.abort();
    let body = null;
    try { body = req.postData() ? JSON.parse(req.postData()) : null; } catch (e) { body = null; }
    return srv.handle({ method, path, query: url.searchParams, body, J });
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  return { ctx, page, pageErrors, calls, fx };
}

const realErrors = (e) => e.filter((m) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(m));
const settle = (page, pred, arg) => page.waitForFunction(pred, arg, { timeout: 8000 });
const text = async (page, sel) => ((await page.textContent(sel).catch(() => '')) || '').replace(/\s+/g, ' ').trim();
const recentRows = (page) => page.$$eval('[data-studio-recent-row]', (els) => els.map((e) => ({ id: e.dataset.studioRecentRow, text: e.textContent.replace(/\s+/g, ' ').trim() })));
async function openStudio(page) {
  await page.evaluate(() => window.deskV1Nav('studio', {}));
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
}
async function openSetup(page) {
  await page.click('[data-studio-new="article"]');
  await page.waitForSelector('[data-studio-create][data-kind="article"] [data-sa-topic]', { timeout: 6000 });
}
const shot = (page, name) => page.waitForTimeout(150).then(() => page.screenshot({ path: resolve(SHOT_DIR, name) }));

// The setup form, then the writer. Returns the topic typed.
async function startArticle(page, { topic, project, campaign }) {
  await openStudio(page);
  await openSetup(page);
  await page.fill('[data-sa-topic]', topic);
  if (project) await page.selectOption('[data-sa-project]', project);
  if (campaign) await page.selectOption('[data-sa-campaign]', campaign);
  await page.click('[data-sa-start]');
  await page.waitForSelector('[data-studio-create][data-kind="article"][data-view="writer"] [data-writer-body]', { timeout: 6000 });
}

async function setupChecks(page, fx, tag) {
  await openStudio(page);
  await openSetup(page);
  check((await page.$$('.desk-v1-add-menu, [role="menu"]')).length === 0, `${tag} New article opens a page, no campaign menu`, `${tag} a menu opened`);
  check((await page.$('[data-sa-start]:disabled')) !== null, `${tag} Start writing is disabled with no topic`, `${tag} Start enabled with no topic`);
  const projOpts = await page.$$eval('[data-sa-project] option', (o) => o.map((x) => x.value));
  check(projOpts[0] === '' && projOpts.length === 1 + fx.projects.length, `${tag} Project lists None + every Clayrune project (${projOpts.length - 1})`, `${tag} projects: ${JSON.stringify(projOpts)}`);
  const campOpts = await page.$$eval('[data-sa-campaign] option', (o) => o.map((x) => x.value));
  check(campOpts[0] === '' && campOpts.length > 1, `${tag} Campaign lists None + existing campaigns (${campOpts.length - 1})`, `${tag} campaigns: ${JSON.stringify(campOpts)}`);
  await page.fill('[data-sa-topic]', 'x');
  check((await page.$('[data-sa-start]:disabled')) === null, `${tag} typing a topic enables Start writing`, `${tag} Start still disabled`);
  await page.fill('[data-sa-topic]', '   ');
  check((await page.$('[data-sa-start]:disabled')) !== null, `${tag} a blank topic keeps it disabled`, `${tag} blank topic enabled Start`);
  // A campaign fills its project in.
  const camp = fx.campaigns.find((c) => ['active', 'proposed', 'draft', 'paused'].includes(c.state) && c.projectId);
  if (camp) {
    await page.selectOption('[data-sa-campaign]', camp.id);
    const pv = await page.$eval('[data-sa-project]', (s) => s.value);
    check(pv === camp.projectId, `${tag} choosing a campaign fills in its project (${pv})`, `${tag} project not filled: ${pv}`);
    const other = fx.projects.find((p) => p.id !== camp.projectId);
    if (other) {
      await page.selectOption('[data-sa-project]', other.id);
      const cv = await page.$eval('[data-sa-campaign]', (s) => s.value);
      check(cv === '', `${tag} choosing another project clears a campaign from the first`, `${tag} campaign kept: ${cv}`);
    }
  }
}

// ── DEMO ───────────────────────────────────────────────────────────────────
async function demo(browser, vp, name) {
  const tag = `[demo ${name}]`;
  console.log(tag);
  const b = await newPage(browser, { live: false, viewport: vp });
  const { page, pageErrors, calls, fx } = b;
  await setupChecks(page, fx, tag);
  if (name === '1440') await shot(page, 'studio_article_setup_1440.png');
  if (name === '390') {
    await shot(page, 'studio_article_setup_390.png');
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(over <= 0, `${tag} setup: no horizontal overflow`, `${tag} setup overflow ${over}px`);
    const h = await page.$eval('[data-sa-start]', (e) => e.getBoundingClientRect().height);
    check(h >= 44, `${tag} Start writing is ${Math.round(h)}px tall`, `${tag} Start writing only ${h}px`);
  }

  // A topic that is no campaign, no project: writes fine.
  await startArticle(page, { topic: 'Why local-first tools win', project: '', campaign: '' });
  const prov = await text(page, '.desk-v1-writer-provenance, [data-writer] .desk-v1-writer-prov, [data-writer]');
  check(/not attached to a campaign/.test(prov), `${tag} the writer opens with no campaign behind it`, `${tag} writer text: ${prov.slice(0, 160)}`);
  check((await text(page, '[data-writer-save]')) === 'Save draft' && (await text(page, '[data-writer-back]')) === 'Back to Studio', `${tag} buttons read Save draft / Back to Studio`, `${tag} button labels wrong`);
  const tabs = await page.$$eval('[data-writer-tab]', (e) => e.map((x) => x.textContent.trim()));
  check(tabs.length === 1 && tabs[0] === 'Draft', `${tag} one Draft tab`, `${tag} tabs: ${JSON.stringify(tabs)}`);
  const chips = await text(page, '[data-sa-meta]');
  check(/No project/.test(chips) && /No campaign/.test(chips), `${tag} chips say no project, no campaign: "${chips}"`, `${tag} chips: ${chips}`);
  await page.click('[data-writer-body]');
  await page.keyboard.type('Local-first wins because the data stays yours.');
  if (name === '1440') await shot(page, 'studio_article_writer_1440.png');
  if (name === '390') {
    await shot(page, 'studio_article_writer_390.png');
    const over = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(over <= 0, `${tag} writer: no horizontal overflow`, `${tag} writer overflow ${over}px`);
  }
  await page.click('[data-writer-back]');
  await page.waitForSelector('[data-studio]', { timeout: 6000 });
  const rows = await recentRows(page);
  const mine = rows.find((r) => /Why local-first tools win/.test(r.text));
  check(mine && /not attached/.test(mine.text) && /article/.test(mine.text), `${tag} Recent lists the draft: "${mine && mine.text}"`, `${tag} Recent missing the article: ${JSON.stringify(rows.map((r) => r.text))}`);
  if (name === '1440') await shot(page, 'studio_article_recent_1440.png');
  await page.click(`[data-studio-recent-row="${mine.id}"]`);
  await page.waitForSelector('[data-view="writer"] [data-writer-body]', { timeout: 6000 });
  const body = await text(page, '[data-writer-body]');
  check(/the data stays yours/.test(body), `${tag} the row reopens the draft with its text`, `${tag} reopened body: ${body}`);

  // Use in a campaign: the draft becomes an article piece carrying the text.
  await page.click('[data-sa-use]');
  await page.waitForSelector('.desk-v1-add-menu, [role="menu"]', { timeout: 4000 });
  await page.click('[role="menu"] [role="menuitem"]:first-child, .desk-v1-add-menu button:first-child');
  await page.waitForSelector('[data-what]', { timeout: 8000 });
  const piece = await page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.title === 'Why local-first tools win'));
  check(piece && piece.kind === 'article' && /the data stays yours/.test(piece.body || ''), `${tag} Use in a campaign made an article piece carrying the text`, `${tag} no piece: ${JSON.stringify(piece)}`);
  await openStudio(page);
  const after = (await recentRows(page)).find((r) => /Why local-first tools win/.test(r.text));
  check(after && /in /.test(after.text) && !/not attached/.test(after.text), `${tag} Recent now says it is in a campaign: "${after && after.text}"`, `${tag} Recent after attach: ${after && after.text}`);
  check(!calls.length, `${tag} demo never called the server`, `${tag} demo called: ${JSON.stringify(calls)}`);
  check(realErrors(pageErrors).length === 0, `${tag} no uncaught page errors`, `${tag} page errors: ${pageErrors.join(' | ')}`);
  await b.ctx.close();
}

// ── LIVE ───────────────────────────────────────────────────────────────────
function makeServer({ refuseFirst } = {}) {
  const srv = { log: [], articles: {}, trash: {}, refuse: !!refuseFirst };
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const out = (a) => ({ ...a, attached: !!a.piece_id, campaign_title: a.campaign_id ? 'Beta launch' : '' });
  srv.handle = ({ method, path, body, J }) => {
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J({ ...workspaceFromFixtures(fx), projects, pieces: fx.families.map((f) => JSON.parse(JSON.stringify(f))) });
    if (path === '/api/desk/materials') return J({ library: { video: [], image: [] }, articles: [], online: { video: [], image: [] }, recent: [] });
    if (path === '/api/desk/studio/storyboards') return J({ storyboards: [] });
    if (path === '/api/desk/studio/usage') return J({ rendering: [], files: {} });
    if (path === '/api/desk/studio/articles' && method === 'GET') return J({ articles: Object.values(srv.articles).map(out) });
    let m = path.match(/^\/api\/desk\/studio\/articles\/([^/]+)$/);
    if (m && method === 'PUT') {
      if (srv.refuse) { srv.refuse = false; return J({ error: 'the store is busy' }, 500); }
      const cur = srv.articles[m[1]];
      if ((cur ? cur.rev : 0) !== body.rev) return J({ error: 'stale', current: cur }, 409);
      const a = { id: m[1], topic: body.topic, project_id: body.project_id, campaign_id: body.campaign_id, tabs: body.tabs, piece_id: cur ? cur.piece_id : null, rev: body.rev + 1 };
      srv.articles[m[1]] = a;
      return J(out(a));
    }
    if (m && method === 'GET') return srv.articles[m[1]] ? J(out(srv.articles[m[1]])) : J({ error: 'no such article' }, 404);
    if (m && method === 'DELETE') {
      const a = srv.articles[m[1]];
      if (!a) return J({ error: 'no such article' }, 404);
      delete srv.articles[m[1]];
      srv.trash['t-' + m[1]] = a;
      return J({ ok: true, token: 't-' + m[1] });
    }
    m = path.match(/^\/api\/desk\/studio\/articles\/([^/]+)\/attach$/);
    if (m && method === 'POST') {
      const a = srv.articles[m[1]];
      a.piece_id = body.piece_id; a.campaign_id = body.campaign_id; a.rev += 1;
      return J({ article: out(a), piece: { id: body.piece_id, campaign_id: body.campaign_id, kind: 'article' } }, 201);
    }
    m = path.match(/^\/api\/desk\/studio\/articles\/trash\/([^/]+)\/restore$/);
    if (m && method === 'POST') {
      const a = srv.trash[m[1]];
      if (!a) return J({ error: 'nothing to restore' }, 404);
      srv.articles[a.id] = a; delete srv.trash[m[1]];
      return J({ ok: true, article: out(a) });
    }
    m = path.match(/^\/api\/desk\/pieces\/([^/]+)$/);
    if (m && method === 'DELETE') { return J({ ok: true }); }
    return J({ error: 'unhandled ' + method + ' ' + path }, 404);
  };
  return srv;
}

async function live(browser) {
  const tag = '[live]';
  console.log('live mode (fake server)');
  const srv = makeServer({ refuseFirst: true });
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(page);
  await openSetup(page);
  await page.fill('[data-sa-topic]', 'Pricing page teardown');
  await page.click('[data-sa-start]');
  await settle(page, () => document.querySelectorAll('.toast').length > 0);
  check((await page.$('[data-view="setup"]')) !== null && Object.keys(srv.articles).length === 0,
    `${tag} a refused first save keeps the form and makes no draft`, `${tag} refused save left: ${await page.evaluate(() => document.querySelector('[data-studio-create]').dataset.view)}`);
  await page.click('[data-sa-start]');
  await page.waitForSelector('[data-view="writer"] [data-writer-body]', { timeout: 6000 });
  const put0 = srv.log.filter((r) => r.method === 'PUT');
  const first = put0[put0.length - 1];
  check(first && first.body.rev === 0 && first.body.topic === 'Pricing page teardown', `${tag} Start writing PUTs the draft at rev 0`, `${tag} first PUT: ${JSON.stringify(first)}`);
  await page.click('[data-writer-body]');
  await page.keyboard.type('Three tiers is one too many.');
  await settle(page, () => /Saved/.test((document.querySelector('[data-sa-status]') || {}).textContent || ''));
  const last = srv.log.filter((r) => r.method === 'PUT').pop();
  check(last.body.rev === 1 && /one too many/.test(last.body.tabs[0].body), `${tag} typing PUTs the text at rev 1 (debounced)`, `${tag} typing PUT: ${JSON.stringify(last)}`);
  const id = last.path.split('/').pop();

  await page.click('[data-sa-use]');
  await page.waitForSelector('.desk-v1-add-menu, [role="menu"]', { timeout: 4000 });
  await page.click('[role="menu"] [role="menuitem"]:first-child, .desk-v1-add-menu button:first-child');
  await page.waitForSelector('[data-what]', { timeout: 8000 });
  const att = srv.log.find((r) => r.method === 'POST' && r.path.endsWith('/attach'));
  const famId = await page.evaluate(() => window.DeskV1Store.state().families.find((f) => f.title === 'Pricing page teardown').id);
  check(att && att.body.campaign_id && att.body.piece_id === famId, `${tag} Use in a campaign POSTs /attach with the campaign and the page's piece id`, `${tag} attach: ${JSON.stringify(att)} piece ${famId}`);

  // Delete from Recent, Undo.
  await openStudio(page);
  await page.waitForSelector(`[data-studio-recent-row="${id}"]`, { timeout: 6000 });
  const bin = await page.$(`[data-studio-recent-item="${id}"] [data-studio-recent-del]`);
  check((await bin.getAttribute('aria-disabled')) === 'true', `${tag} an attached article's bin is disabled`, `${tag} attached bin enabled`);
  await ctx.close();

  // A second, unattached draft deletes and undoes.
  const srv2 = makeServer();
  const b2 = await newPage(browser, { live: true, srv: srv2 });
  await settle(b2.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openStudio(b2.page);
  await startArticle(b2.page, { topic: 'Throwaway idea', project: '', campaign: '' });
  await b2.page.click('[data-writer-back]');
  await b2.page.waitForSelector('[data-studio]', { timeout: 6000 });
  const id2 = Object.keys(srv2.articles)[0];
  await b2.page.waitForSelector(`[data-studio-recent-row="${id2}"]`, { timeout: 6000 });
  await b2.page.waitForTimeout(400);
  await b2.page.locator(`[data-studio-recent-item="${id2}"] [data-studio-recent-del]`).click();
  await settle(b2.page, (i) => !document.querySelector(`[data-studio-recent-row="${i}"]`), id2);
  check(srv2.log.some((r) => r.method === 'DELETE' && r.path === `/api/desk/studio/articles/${id2}`), `${tag} the bin DELETEs the draft`, `${tag} no DELETE: ${JSON.stringify(srv2.log.filter((r) => r.method === 'DELETE'))}`);
  await b2.page.click('#desk-v1-undo');
  await settle(b2.page, (i) => !!document.querySelector(`[data-studio-recent-row="${i}"]`), id2);
  check(srv2.log.some((r) => r.method === 'POST' && r.path === `/api/desk/studio/articles/trash/t-${id2}/restore`), `${tag} Undo POSTs the trash token and the row is back`, `${tag} no restore POST`);
  check(realErrors(pageErrors.concat(b2.pageErrors)).length === 0, `${tag} no uncaught page errors`, `${tag} page errors: ${pageErrors.concat(b2.pageErrors).join(' | ')}`);
  await b2.ctx.close();
}

const browser = await chromium.launch();
try {
  await demo(browser, { width: 1440, height: 950 }, '1440');
  await demo(browser, { width: 390, height: 844 }, '390');
  await live(browser);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll desk-v1-studio-article checks passed');
process.exit(bad ? 1 : 0);
