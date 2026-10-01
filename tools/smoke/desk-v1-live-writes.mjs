#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S1) — Home + Project + Setup writes against a fake
 * server, `desk_v1_live` ON, plus the flag-OFF contract that demo mode never
 * calls a Desk route.
 *
 * The other desk-v1 smokes run the surfaces in demo mode over the fixtures.
 * This one answers the Desk routes from a small in-memory server so the
 * client's own requests are what is under test:
 *   1. New campaign (Home)     -> POST /api/desk/campaigns?shape=v1 with the v1
 *                                 draft; the page opens only once it is accepted;
 *                                 backing out of an untouched draft DELETEs it.
 *   2. New campaign refused    -> rolled back, toast carries the server text,
 *                                 stays on Home.
 *   3. Project field (Setup)   -> PATCH with projectId + the WHOLE plan.
 *   4. Pause / Resume          -> ONE PATCH /api/desk/presence/<pid> {state};
 *                                 the campaigns take the SERVER's answer, and a
 *                                 campaign its start gate held stays paused.
 *                                 A refused pause rolls everything back.
 *   5. Default planner         -> PATCH {desk_agent}; refused -> rolled back.
 *   6. Playbook                -> GET /api/desk/findings?project_id=; Re-confirm
 *                                 POSTs the route, takes the server's finding,
 *                                 and offers no Undo; a refusal rolls back; a
 *                                 failed read says so instead of showing empty.
 *   7. Flag OFF                -> every one of those actions makes ZERO
 *                                 /api/desk/* requests.
 *
 * RUN   cd tools/smoke && node desk-v1-live-writes.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { installDemoFixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

const PID = 'p1';
const PROJECTS = [{
  id: PID, name: 'Live Project', status: 'active', domain: 'general', emoji: '🧪',
  description: '', summary: '', current_task: 'Idle', next_action: '',
  blocked: false, blocked_reason: null, activity_log: [], backlog: [],
  project_path: '/smoke/' + PID, last_updated: '2026-09-09T00:00:00Z',
  last_updated_relative: 'today', last_completed: null, live_agent: null,
  display_order: 0, provider: 'claude', use_streaming_agent: true,
  distiller_mode: 'proposed', distiller_min_recurrence: 3,
  distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3,
  distiller_max_explorations_per_session: 3, distiller_min_turns: 5,
  distiller_skip_errors: true, roster: [],
}];

const camp = (id, state, extra = {}) => ({
  id, state, projectId: PID, project_id: PID, subject: null, goal: { current: 0 }, rules: {},
  plan: { title: 'Campaign ' + id, brief: '', audience: '', goal: { outcome: '', target: null, deadline: null, tracked: false },
    accounts: [], cadence: { per_week: null }, end: { date: null, post_cap: null }, paid: false },
  ...extra,
});
const FINDING = {
  id: 'F-live-1', project_id: PID, state: 'stale', origin: 'unattended', dimension: 'format',
  arms: { a: 'video', b: 'plain' }, effect: { direction: 'a>b', ratio: 2 }, metric: 'clicks',
  confidence: 'medium', n_total: 12, evidence: [], stale_reason: 'A newer retro pointed the other way.',
  decided_at: null, decided_by: null,
};

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// The fake Desk server. `srv.next[key]` queues a refusal for one call.
function makeServer() {
  const srv = {
    log: [], campaigns: [camp('c-active', 'active'), camp('c-prop', 'proposed')],
    presence: { replies: 'drafts', desk_agent: null, state: 'active' },
    findings: [{ ...FINDING }], next: {}, holdOnResume: new Set(), failFindingsRead: false,
  };
  srv.workspace = () => ({
    projects: [{ id: PID, name: 'Live Project', state: 'active', roster: [], presence: { ...srv.presence } },
      { id: 'p2', name: 'Other Project', state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }],
    campaigns: srv.campaigns.map((c) => JSON.parse(JSON.stringify(c))), accounts: [], pieces: [],
  });
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
    if (path === '/api/projects') return J(PROJECTS);
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }, { name: 'posy', scope: 'global', agent_name: 'Posy', avatar: '' }]);
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    srv.log.push({ method, path, search: url.search, body });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/campaigns' && method === 'POST') {
      srv.campaigns.push({ ...body, project_id: body.projectId || null });
      return J(body, 201);
    }
    let m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'DELETE') { srv.campaigns = srv.campaigns.filter((c) => c.id !== m[1]); return J({ ok: true }); }
    if (m && method === 'PATCH') {
      const c = srv.campaigns.find((x) => x.id === m[1]);
      if (!c) return J({ error: 'campaign not found' }, 404);
      Object.assign(c, body); if ('projectId' in body) c.project_id = body.projectId;
      return J(c);
    }
    m = path.match(/^\/api\/desk\/presence\/([^/]+)$/);
    if (m && method === 'PATCH') {
      if (body.desk_agent !== undefined) srv.presence.desk_agent = body.desk_agent;
      if (!body.state) return J({ ...srv.presence });
      srv.presence.state = body.state;
      const changed = [], held = [];
      for (const c of srv.campaigns) {
        if (c.project_id !== PID) continue;
        if (body.state === 'paused') {
          if (['completed', 'archived', 'paused'].includes(c.state)) continue;
          c.pre_pause_state = c.state; c.state = 'paused'; changed.push(c);
        } else if (c.state === 'paused' && c.pre_pause_state) {
          if (srv.holdOnResume.has(c.id)) { held.push({ id: c.id, reasons: ['no goal'] }); continue; }
          c.state = c.pre_pause_state; delete c.pre_pause_state; changed.push(c);
        }
      }
      return J({ ...srv.presence, cascade: { campaigns: changed, changed: changed.length, held } });
    }
    if (path === '/api/desk/findings' && method === 'GET') {
      if (srv.failFindingsRead) return J({ error: 'findings store unreadable' }, 500);
      return J(srv.findings.filter((f) => f.project_id === url.searchParams.get('project_id')));
    }
    m = path.match(/^\/api\/desk\/findings\/([^/]+)\/(reconfirm|retire|undo-reject)$/);
    if (m && method === 'POST') {
      const f = srv.findings.find((x) => x.id === m[1]);
      if (m[2] === 'reconfirm') { f.state = 'confirmed'; f.origin = 'interactive'; f.stale_reason = null; f.decided_by = body.decided_by; }
      return J({ ...f });
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
const deskText = (page) => page.evaluate(() => document.querySelector('.modal-window[data-modal-id="__desk"]').innerText);
const calls = (srv, method, pathRe) => srv.log.filter((r) => r.method === method && pathRe.test(r.path));
const stateOf = (page, id) => page.evaluate((cid) => (window.DeskV1Store.state().campaigns.find((c) => c.id === cid) || {}).state, id);
const openProject = async (page) => {
  await page.evaluate((projectId) => window.deskV1Nav('project', { projectId }), PID);
  await settle(page, () => !!document.querySelector('.desk-v1-project'));
};

// ── 1 + 2 + 3: new campaign, refusal, project field, discard ───────────────
async function newCampaign(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => /Live Project/.test(document.body.innerText));

  await page.click('.desk-v1-home-newcamp-page-btn');
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  const posts = calls(srv, 'POST', /^\/api\/desk\/campaigns$/);
  posts.length === 1 && posts[0].search === '?shape=v1' ? ok('Home "New campaign" POSTs /api/desk/campaigns?shape=v1 once') : fail('POST calls: ' + JSON.stringify(posts));
  const b = posts[0] && posts[0].body || {};
  (b.state === 'draft' && /^camp-draft-/.test(b.id) && b.plan && 'title' in b.plan && b.map && b.map.stop === 'how')
    ? ok('the body is the v1 draft (state draft, client id, plan, map stop how)') : fail('POST body: ' + JSON.stringify(b).slice(0, 300));
  const id = b.id;

  // Setup: pick the project at the Brief stop.
  await settle(page, () => !!document.querySelector('[data-setup-project]'));
  await page.selectOption('[data-setup-project]', PID);
  await settle(page, () => window.DeskV1Store.state().campaigns.some((c) => c.projectId === 'p1' && /^camp-draft-/.test(c.id)));
  await page.waitForTimeout(150);
  const patch = calls(srv, 'PATCH', new RegExp('^/api/desk/campaigns/' + id + '$'))[0];
  (patch && patch.search === '?shape=v1' && patch.body.projectId === PID && patch.body.plan && patch.body.plan.title === 'Live Project campaign' && Array.isArray(patch.body.plan.accounts))
    ? ok('picking a project PATCHes projectId and the WHOLE plan (title re-defaulted from the project)') : fail('PATCH: ' + JSON.stringify(patch));

  // A refused project change rolls back (title, brief and project).
  srv.next[`PATCH /api/desk/campaigns/${id}`] = 'project is full';
  await page.selectOption('[data-setup-project]', 'p2');
  await settle(page, () => /was not saved: project is full/.test(document.body.innerText));
  const rolled = await page.evaluate((cid) => { const c = window.DeskV1Store.state().campaigns.find((x) => x.id === cid); return { p: c.projectId, t: c.plan.title }; }, id);
  (rolled.p === PID && rolled.t === 'Live Project campaign') ? ok('a refused project change rolls projectId and the re-defaulted title back') : fail('after refusal: ' + JSON.stringify(rolled));

  // Back from a TOUCHED draft keeps it (no DELETE); the untouched path is next.
  const before = calls(srv, 'DELETE', /^\/api\/desk\/campaigns\//).length;
  await page.click('.desk-v1-back');
  await settle(page, () => (document.querySelector('.desk-v1-crumb-title') || {}).textContent === 'Desk');
  calls(srv, 'DELETE', /^\/api\/desk\/campaigns\//).length === before ? ok('backing out of a touched draft does not DELETE it') : fail('a touched draft was deleted');

  // Untouched draft: create, back out -> DELETE.
  await page.click('.desk-v1-home-newcamp-page-btn');
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  const id2 = calls(srv, 'POST', /^\/api\/desk\/campaigns$/)[1].body.id;
  await page.click('.desk-v1-back');
  await settle(page, () => (document.querySelector('.desk-v1-crumb-title') || {}).textContent === 'Desk');
  await page.waitForTimeout(200);
  const del = calls(srv, 'DELETE', new RegExp('^/api/desk/campaigns/' + id2 + '$'));
  del.length === 1 && !srv.campaigns.some((c) => c.id === id2)
    ? ok('backing out of an untouched draft DELETEs it server-side') : fail('discard DELETE: ' + JSON.stringify(del) + ' still on server=' + srv.campaigns.some((c) => c.id === id2));

  // A refused create: rolled back, toast, still on Home.
  srv.next['POST /api/desk/campaigns'] = 'a campaign with that id already exists';
  const nBefore = await page.evaluate(() => window.DeskV1Store.state().campaigns.length);
  await page.click('.desk-v1-home-newcamp-page-btn');
  await settle(page, () => /was not saved: a campaign with that id already exists/.test(document.body.innerText));
  ok("a refused create toasts the server's own error");
  const nAfter = await page.evaluate(() => window.DeskV1Store.state().campaigns.length);
  const onHome = await page.evaluate(() => (document.querySelector('.desk-v1-crumb-title') || {}).textContent === 'Desk' && !document.querySelector('.desk-v1-campaign'));
  (nAfter === nBefore && onHome) ? ok('the refused draft is rolled back and the page never opened') : fail(`rollback: ${nBefore} -> ${nAfter}, onHome=${onHome}`);

  const real = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  real.length ? real.forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 4: pause / resume ──────────────────────────────────────────────────────
async function pauseResume(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => /Live Project/.test(document.body.innerText));
  await openProject(page);

  await page.click('[data-pause-project-btn]');
  await settle(page, () => /Paused Live Project — 2 campaigns paused with it\./.test(document.body.innerText));
  ok("Pause: the toast reads the server's count (2 campaigns)");
  const pauses = calls(srv, 'PATCH', /^\/api\/desk\/presence\/p1$/);
  (pauses.length === 1 && JSON.stringify(pauses[0].body) === '{"state":"paused"}')
    ? ok('Pause is ONE PATCH /api/desk/presence/p1 {state:"paused"} (no per-campaign calls)') : fail('pause calls: ' + JSON.stringify(pauses));
  calls(srv, 'PATCH', /^\/api\/desk\/campaigns\//).length === 0 ? ok('the cascade is server-side: no campaign PATCH from the browser') : fail('browser PATCHed campaigns');
  const st = [await stateOf(page, 'c-active'), await stateOf(page, 'c-prop')];
  (st[0] === 'paused' && st[1] === 'paused') ? ok('both campaigns are paused locally') : fail('local states ' + st);
  const noUndo = await page.evaluate(() => !document.body.innerText.includes('Undo'));
  noUndo ? ok('a live pause offers no Undo (the route has no inverse)') : fail('Undo offered on a live pause');

  // Resume: the server's start gate holds c-active; the browser must not resume it.
  srv.holdOnResume.add('c-active');
  await page.click('[data-resume-project-btn]');
  await settle(page, () => /Resumed Live Project — 1 campaign back running, 1 still needs setup fixed/.test(document.body.innerText));
  ok("Resume: the toast reports the server's held campaign");
  const st2 = [await stateOf(page, 'c-active'), await stateOf(page, 'c-prop')];
  (st2[0] === 'paused' && st2[1] === 'proposed')
    ? ok('the campaign the start gate held stays paused; the other returns to proposed') : fail('after resume ' + st2);

  // A refused pause rolls everything back.
  srv.next['PATCH /api/desk/presence/p1'] = 'presence store locked';
  await page.click('[data-pause-project-btn]');
  await settle(page, () => /was not saved: presence store locked/.test(document.body.innerText));
  const st3 = [await stateOf(page, 'c-active'), await stateOf(page, 'c-prop')];
  const ps = await page.evaluate(() => window.DeskV1Store.state().projects[0].presence.state);
  (st3[0] === 'paused' && st3[1] === 'proposed' && ps === 'active')
    ? ok('a refused pause rolls the project and every campaign back') : fail(`refused pause left ${st3} / ${ps}`);
  await ctx.close();
}

// ── 5: default planner ─────────────────────────────────────────────────────
async function planner(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => /Live Project/.test(document.body.innerText));
  await openProject(page);
  const pick = async (label) => {
    await page.click('[data-project-agent]');
    await page.click(`.desk-v1-addto-menu button:has-text("${label}")`);
  };
  await pick('Claydo');
  await settle(page, () => /Claydo now plans for Live Project/.test(document.body.innerText));
  await page.waitForTimeout(100);
  const c = calls(srv, 'PATCH', /^\/api\/desk\/presence\/p1$/);
  (c.length === 1 && c[0].body.desk_agent === 'global:claydo') ? ok('picking a planner PATCHes {desk_agent} once') : fail('planner calls: ' + JSON.stringify(c));
  srv.next['PATCH /api/desk/presence/p1'] = 'unknown agent';
  await pick('Posy');
  await settle(page, () => /was not saved: unknown agent/.test(document.body.innerText));
  const ref = await page.evaluate(() => window.DeskV1Store.state().projects[0].presence.desk_agent);
  ref === 'global:claydo' ? ok('a refused pick rolls back to the previous planner') : fail('planner after refusal: ' + ref);
  await ctx.close();
}

// ── 6: playbook ────────────────────────────────────────────────────────────
async function playbook(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => /Live Project/.test(document.body.innerText));
  await openProject(page);
  await settle(page, () => !!document.querySelector('[data-finding-id="F-live-1"]'));
  const reads = calls(srv, 'GET', /^\/api\/desk\/findings$/);
  (reads.length === 1 && reads[0].search === '?project_id=p1') ? ok('the Playbook reads GET /api/desk/findings?project_id=p1') : fail('findings reads: ' + JSON.stringify(reads));

  srv.next['POST /api/desk/findings/F-live-1/reconfirm'] = 'finding not found or not stale';
  await page.click('[data-playbook-stale] [data-finding-reconfirm]');
  await settle(page, () => /was not saved: finding not found or not stale/.test(document.body.innerText));
  const stillStale = await page.evaluate(() => document.querySelector('[data-finding-id="F-live-1"]').dataset.findingState);
  stillStale === 'stale' ? ok('a refused Re-confirm rolls the finding back to stale') : fail('state after refusal: ' + stillStale);

  await page.click('[data-playbook-stale] [data-finding-reconfirm]');
  await settle(page, () => document.querySelector('[data-finding-id="F-live-1"]').dataset.findingState === 'confirmed');
  const posted = calls(srv, 'POST', /reconfirm$/).pop();
  (posted && posted.body && posted.body.decided_by) ? ok('Re-confirm POSTs the route with a decider') : fail('reconfirm body: ' + JSON.stringify(posted));
  const noUndo = await page.evaluate(() => !/Undo/.test(document.body.innerText));
  noUndo ? ok('a live finding decision offers no Undo (the route has no inverse)') : fail('Undo offered on a live decision');
  const origin = await page.evaluate(() => window.DeskV1Store.state().playbook.findings.find((f) => f.id === 'F-live-1').origin);
  origin === 'interactive' ? ok("the server's finding replaced the local one (origin interactive)") : fail('origin ' + origin);

  // A failed read is shown, not painted as an empty Playbook.
  const srv2 = makeServer(); srv2.failFindingsRead = true;
  const b = await newPage(browser, { live: true, srv: srv2 });
  await settle(b.page, () => /Live Project/.test(document.body.innerText));
  await openProject(b.page);
  await settle(b.page, () => !!document.querySelector('[data-playbook-error]'));
  const t = await b.page.textContent('[data-playbook-error]');
  /findings store unreadable/.test(t) ? ok('a failed findings read says so (with the server text), not "no findings yet"') : fail('error text: ' + t);
  await b.ctx.close();
  await ctx.close();
}

// ── 7: flag OFF → no Desk route is ever called ─────────────────────────────
async function demoCallsNothing(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => window.DeskV1Store.demo());
  await page.click('.desk-v1-home-newcamp-page-btn');
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.click('.desk-v1-back');
  await settle(page, () => (document.querySelector('.desk-v1-crumb-title') || {}).textContent === 'Desk');
  await page.evaluate(() => window.deskV1Nav('project', { projectId: 'clayrune' }));
  await settle(page, () => !!document.querySelector('.desk-v1-project'));
  await page.click('[data-pause-project-btn]');
  await settle(page, () => /Paused Clayrune/.test(document.body.innerText));
  await page.click('[data-resume-project-btn]');
  await settle(page, () => /Resumed Clayrune/.test(document.body.innerText));
  await page.waitForTimeout(200);
  srv.log.length === 0 ? ok('flag OFF: new campaign, back-out, pause and resume made 0 /api/desk/* requests') : fail('demo mode called the server: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: new campaign / project field'); await newCampaign(browser);
  console.log('live ON: pause / resume'); await pauseResume(browser);
  console.log('live ON: default planner'); await planner(browser);
  console.log('live ON: playbook'); await playbook(browser);
  console.log('live OFF: demo'); await demoCallsNothing(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — S1 writes call the Desk routes live (new campaign, project field, pause/resume cascade, planner, findings), roll back refusals, and demo mode calls nothing.');
