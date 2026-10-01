#!/usr/bin/env node
/**
 * Desk v1 (MC-1021 R1-W S2) — Campaign shell + Brief + Launch against a fake
 * server, `desk_v1_live` ON, plus the flag-OFF contract.
 *
 * The approval the publisher checks is written by the SERVER, only on a human
 * action, so what is under test here is which requests the browser makes:
 *   1. Start          -> the Brief bounds are PATCHed (never `state`, `approval`,
 *                        `approvals` or `startedAt`), then POST .../start with the
 *                        policy record; the campaign takes the SERVER's term and
 *                        approval; no Undo is offered. A refusal rolls it back.
 *   2. Pause / Resume -> PATCH {state}; a refused Resume rolls back and toasts the
 *                        server's reason.
 *   3. Brief edit     -> a widening (posts a week up) on a started campaign asks,
 *                        then PATCHes plan + how; a refused save rolls it back.
 *   4. Approve        -> Launch shows "Awaiting approval" with an Approve button;
 *                        it saves the bounds then POSTs .../approve and takes the
 *                        server's approval.
 *   5. Renew          -> POST .../renew; the server's term replaces the local one.
 *   6. Flag OFF       -> Start, Pause, Resume and a Brief edit make 0 /api/desk/*
 *                        requests (and no passcode prompt).
 *   7. Human proof    -> (MC-995) Start / Approve / Renew each open the shared
 *                        passcode modal and send the passcode in the POST body; a
 *                        wrong one shows the server's error in the modal and
 *                        changes nothing; Cancel rolls the action back.
 *
 * RUN   cd tools/smoke && node desk-v1-live-campaign.mjs
 */
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const JS_DIR = resolve(REPO_ROOT, 'static', 'js');
const CSS_DIR = resolve(REPO_ROOT, 'static', 'css');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';

const STATIC = {};
for (const f of readdirSync(JS_DIR)) if (f.endsWith('.js')) STATIC[`/static/js/${f}`] = ['text/javascript; charset=utf-8', readFileSync(resolve(JS_DIR, f), 'utf8')];
for (const f of readdirSync(CSS_DIR)) if (f.endsWith('.css')) STATIC[`/static/css/${f}`] = ['text/css; charset=utf-8', readFileSync(resolve(CSS_DIR, f), 'utf8')];

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };

// The server's own approval record, with values the browser could not have made
// up (the hash, the dates) so "took the server's answer" is checkable.
const SRV_APPROVAL = (bounds, term) => ({ bounds, bounds_hash: 'srv-hash-' + term, at: '2031-02-03T04:05:06Z', by: 'human', term });

function makeServer() {
  const fx = loadFixtures();
  const projects = fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } }));
  const campaigns = fx.campaigns.map((c) => JSON.parse(JSON.stringify(c)));
  // camp-4: a long-horizon campaign whose term has ended, so Renew is open.
  const long = campaigns.find((c) => c.id === 'camp-4');
  long.goal = { ...long.goal, horizon: 'long', deadline: '2031-12-31' };
  long.term = { index: 1, starts: '2020-01-01', ends: '2020-03-01', post_cap: null };
  // camp-2 (proposed) is made startable: its goal needs a target and a source.
  const prop = campaigns.find((c) => c.id === 'camp-2');
  prop.goal = { current: 0, metric: 'signups', target: 10, source: 'manual', baseline: 0, unit: 'signups', horizon: 'short', deadline: '2026-10-20' };
  // `refusedProof`: the POSTs that reached the three approval routes with a wrong
  // or missing passcode (kept out of `log` so "the POST" below is the accepted one).
  const srv = { log: [], next: {}, campaigns, fx, refusedProof: [] };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects, campaigns: campaigns.map((c) => JSON.parse(JSON.stringify(c))) });
  return srv;
}

// MC-995: the fake server's idea of the dashboard passcode.
const PASSCODE = 'smoke-pass';
// Types into the shared passcode modal (human-proof-modal.js) and submits.
const enterPasscode = async (page, code) => {
  await page.waitForSelector('[id^="hp-passcode-"]', { timeout: 8000 });
  await page.fill('[id^="hp-passcode-"]', code);
  await page.press('[id^="hp-passcode-"]', 'Enter');
};

const boundsOf = (c) => ({ accounts: c.plan.accounts, cadence: c.plan.cadence, end: c.plan.end, term: c.term || {}, budget: (c.how && c.how.budget) || {} });

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
    if (path === '/api/projects') return J(srv.fx.projects.map((p) => ({
      id: p.id, name: p.name, status: 'active', domain: 'general', emoji: '🧪', description: '', summary: '', current_task: 'Idle',
      next_action: '', blocked: false, blocked_reason: null, activity_log: [], backlog: [], project_path: '/smoke/' + p.id,
      last_updated: '2026-09-09T00:00:00Z', last_updated_relative: 'today', last_completed: null, live_agent: null, display_order: 0,
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: live, user_timezone: '' });
    if (path === '/api/characters') return J([{ name: 'claydo', scope: 'global', agent_name: 'Claydo', avatar: '' }, { name: 'posy', scope: 'global', agent_name: 'Posy', avatar: '' }]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* no body */ }
    if (method === 'POST' && /^\/api\/desk\/campaigns\/[^/]+\/(start|approve|renew)$/.test(path)
        && !(body && body.passcode === PASSCODE)) {
      srv.refusedProof.push({ path, body });
      return J({ error: 'bad_passcode' }, 403);
    }
    srv.log.push({ method, path, search: url.search, body });
    const key = `${method} ${path}`;
    const refuse = Object.keys(srv.next).find((k) => key === k || key.startsWith(k));
    if (refuse) { const msg = srv.next[refuse]; delete srv.next[refuse]; return J({ error: msg }, 409); }
    if (path === '/api/desk/workspace') return J(srv.workspace());
    let m = path.match(/^\/api\/desk\/campaigns\/([^/]+)$/);
    if (m && method === 'PATCH') {
      const c = srv.campaigns.find((x) => x.id === m[1]);
      if (!c) return J({ error: 'campaign not found' }, 404);
      Object.assign(c, body);
      return J(c);
    }
    m = path.match(/^\/api\/desk\/campaigns\/([^/]+)\/(start|approve|renew)$/);
    if (m && method === 'POST') {
      const c = srv.campaigns.find((x) => x.id === m[1]);
      if (!c) return J({ error: 'campaign not found' }, 404);
      if (m[2] === 'start') {
        c.state = 'active'; c.startedAt = '2031-02-03T04:05:06Z';
        c.term = { index: 1, starts: '2031-02-03', ends: (c.plan.end && c.plan.end.date) || null, post_cap: null };
        c.policyRecord = body && body.policy_record;
        c.approval = SRV_APPROVAL(boundsOf(c), 1); c.approvals = [c.approval];
      } else if (m[2] === 'approve') {
        c.approval = SRV_APPROVAL(boundsOf(c), (c.term && c.term.index) || 1); c.approvals = (c.approvals || []).concat([c.approval]);
      } else {
        c.term = { index: (c.term.index || 1) + 1, starts: '2020-03-01', ends: '2020-05-30', post_cap: null };
        c.terms = [c.term]; c.approval = SRV_APPROVAL(boundsOf(c), c.term.index); c.approvals = [c.approval];
      }
      return J(c);
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
const calls = (srv, method, pathRe) => srv.log.filter((r) => r.method === method && pathRe.test(r.path));
const camp = (page, id) => page.evaluate((cid) => JSON.parse(JSON.stringify(window.DeskV1Store.state().campaigns.find((c) => c.id === cid))), id);
const openLaunch = async (page, id) => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate((cid) => window.deskV1GotoCampaignPanel('launch', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-launch'));
};
const openHow = async (page, id) => {
  await page.evaluate((cid) => window.deskV1Nav('campaign', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('.desk-v1-campaign'));
  await page.evaluate((cid) => window.deskV1GotoCampaignPanel('how', { campaignId: cid }), id);
  await settle(page, () => !!document.querySelector('[data-how-limit="per_week"]'));
};
const noUndo = (page) => page.evaluate(() => !/Undo/.test(document.body.innerText));

// ── 1: Start ───────────────────────────────────────────────────────────────
async function start(browser) {
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openLaunch(page, 'camp-2');

  // Refused first: rolled back, the server's own reason on screen.
  srv.next['POST /api/desk/campaigns/camp-2/start'] = 'cannot start: no accounts';
  await page.click('[data-map-start-btn]');
  await page.click('[data-sheet-confirm]');
  await enterPasscode(page, PASSCODE);
  await settle(page, () => /was not saved: cannot start: no accounts/.test(document.body.innerText));
  const back = await camp(page, 'camp-2');
  (back.state === 'proposed' && !back.approval && !back.startedAt)
    ? ok('a refused Start rolls the campaign back to proposed with no approval, and says why') : fail('after refusal: ' + JSON.stringify({ s: back.state, a: back.approval, t: back.startedAt }));

  srv.log.length = 0;
  await page.evaluate(() => window.deskV1Render());
  await settle(page, () => !!document.querySelector('[data-map-start-btn]'));
  await page.click('[data-map-start-btn]');
  await page.click('[data-sheet-confirm]');
  await enterPasscode(page, PASSCODE);
  await settle(page, () => window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-2').approval?.bounds_hash === 'srv-hash-1');
  const patch = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-2$/)[0];
  const post = calls(srv, 'POST', /^\/api\/desk\/campaigns\/camp-2\/start$/)[0];
  (patch && patch.search === '?shape=v1' && patch.body.plan && patch.body.plan.accounts && !('state' in patch.body)
    && !('approval' in patch.body) && !('approvals' in patch.body) && !('startedAt' in patch.body) && !('term' in patch.body))
    ? ok('Start first PATCHes the bounds (plan, how, goal, map) and never state / approval / approvals / startedAt / term')
    : fail('pre-start PATCH: ' + JSON.stringify(patch && Object.keys(patch.body)));
  (post && post.body && post.body.policy_record && Array.isArray(post.body.policy_record.accounts) && post.body.passcode === PASSCODE)
    ? ok('then POSTs /start with the policy record and the typed passcode') : fail('start POST: ' + JSON.stringify(post));
  srv.log.indexOf(patch) < srv.log.indexOf(post) ? ok('the PATCH lands before the POST') : fail('POST before PATCH');
  const c = await camp(page, 'camp-2');
  (c.state === 'active' && c.startedAt === '2031-02-03T04:05:06Z' && c.term.starts === '2031-02-03' && c.approval.at === '2031-02-03T04:05:06Z')
    ? ok("the campaign holds the SERVER's start time, term and approval, not the browser's clock") : fail('after start: ' + JSON.stringify({ s: c.state, t: c.term, a: c.approval }));
  (await noUndo(page)) ? ok('a live Start offers no Undo (no inverse route)') : fail('Undo offered after Start');
  const real = pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
  real.length ? real.forEach((e) => fail('page error: ' + e)) : ok('no uncaught page errors');
  await ctx.close();
}

// ── 2: pause / resume ──────────────────────────────────────────────────────
async function pauseResume(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openLaunch(page, 'camp-1');

  await page.click('[data-launch-pause]');
  await settle(page, () => !!document.querySelector('[data-launch-resume]'));
  const p = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/);
  (p.length === 1 && JSON.stringify(p[0].body) === '{"state":"paused"}' && p[0].search === '?shape=v1')
    ? ok('Pause is one PATCH {state:"paused"}') : fail('pause calls: ' + JSON.stringify(p));

  srv.next['PATCH /api/desk/campaigns/camp-1'] = 'no human approval on file';
  await page.click('[data-launch-resume]');
  await page.click('[data-confirm-accept]');
  await settle(page, () => /was not saved: no human approval on file/.test(document.body.innerText));
  const still = await camp(page, 'camp-1');
  still.state === 'paused' ? ok("a refused Resume rolls back to paused and shows the server's reason") : fail('state after refused resume: ' + still.state);

  await page.evaluate(() => window.deskV1Render());
  await settle(page, () => !!document.querySelector('[data-launch-resume]'));
  await page.click('[data-launch-resume]');
  await page.click('[data-confirm-accept]');
  await settle(page, () => !!document.querySelector('[data-launch-pause]'));
  const r = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/).pop();
  JSON.stringify(r.body) === '{"state":"active"}' ? ok('Resume is one PATCH {state:"active"}') : fail('resume body: ' + JSON.stringify(r.body));
  await ctx.close();
}

// ── 3 + 4: Brief edit, awaiting approval, Approve ──────────────────────────
async function briefAndApprove(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openHow(page, 'camp-1');
  const before = (await camp(page, 'camp-1')).plan.cadence.per_week;

  // A refused save of a NARROWING edit rolls the field back.
  srv.next['PATCH /api/desk/campaigns/camp-1'] = 'store locked';
  await page.fill('[data-how-limit="per_week"]', String(before - 1));
  await page.press('[data-how-limit="per_week"]', 'Tab');
  await settle(page, () => /was not saved: store locked/.test(document.body.innerText));
  const rolled = (await camp(page, 'camp-1')).plan.cadence.per_week;
  rolled === before ? ok('a refused Brief save puts the old value back and says why') : fail(`per_week after refusal: ${rolled} (was ${before})`);

  // A widening edit on a started campaign asks first, then saves plan + how.
  srv.log.length = 0;
  await page.fill('[data-how-limit="per_week"]', String(before + 4));
  await page.press('[data-how-limit="per_week"]', 'Tab');
  await settle(page, () => /widens what/.test(document.body.innerText));
  await page.click('[data-confirm-accept]');
  await page.waitForTimeout(300);
  const patch = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/)[0];
  (patch && patch.body.plan && patch.body.plan.cadence.per_week === before + 4 && 'how' in patch.body && !('approval' in patch.body))
    ? ok('a confirmed widening PATCHes plan + how (and no approval)') : fail('widening PATCH: ' + JSON.stringify(patch && patch.body && Object.keys(patch.body)));

  // Launch now shows Awaiting approval with an Approve button.
  await page.evaluate(() => window.deskV1GotoCampaignPanel('launch', { campaignId: 'camp-1' }));
  await settle(page, () => !!document.querySelector('[data-approve-bounds]'));
  ok('Launch shows "Awaiting approval" with an Approve button after the widening');
  srv.log.length = 0;
  await page.click('[data-approve-bounds]');
  await enterPasscode(page, PASSCODE);
  await settle(page, () => (window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-1').approval || {}).at === '2031-02-03T04:05:06Z');
  const post = calls(srv, 'POST', /^\/api\/desk\/campaigns\/camp-1\/approve$/)[0];
  const pre = calls(srv, 'PATCH', /^\/api\/desk\/campaigns\/camp-1$/)[0];
  (post && pre && srv.log.indexOf(pre) < srv.log.indexOf(post) && pre.body.plan.cadence.per_week === before + 4)
    ? ok('Approve saves the bounds, then POSTs /approve') : fail('approve calls: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  const c = await camp(page, 'camp-1');
  (c.approval.at === '2031-02-03T04:05:06Z' && c.approval.bounds.cadence.per_week === before + 4)
    ? ok("the campaign holds the server's new approval") : fail('approval after approve: ' + JSON.stringify(c.approval));
  (await noUndo(page)) ? ok('Approve offers no Undo') : fail('Undo offered after Approve');
  await ctx.close();
}

// ── 5: renew ───────────────────────────────────────────────────────────────
async function renew(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openLaunch(page, 'camp-4');
  await page.click('[data-renew-term]');
  await enterPasscode(page, PASSCODE);
  await settle(page, () => (window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-4').approval || {}).bounds_hash === 'srv-hash-2');
  const post = calls(srv, 'POST', /^\/api\/desk\/campaigns\/camp-4\/renew$/);
  post.length === 1 ? ok('Renew POSTs /renew once') : fail('renew calls: ' + JSON.stringify(post));
  const c = await camp(page, 'camp-4');
  (c.term.ends === '2020-05-30' && c.approval.bounds_hash === 'srv-hash-2')
    ? ok("the campaign takes the server's next term and approval") : fail('after renew: ' + JSON.stringify({ t: c.term, a: c.approval && c.approval.bounds_hash }));
  await ctx.close();
}

// ── 7: human proof (MC-995) ────────────────────────────────────────────────
async function humanProof(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: true, srv });
  await settle(page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openLaunch(page, 'camp-2');
  const serverState = () => srv.campaigns.find((c) => c.id === 'camp-2').state;

  // A wrong passcode: the modal stays open with the server's error, the server
  // changed nothing, and a retry with the right one then goes through.
  await page.click('[data-map-start-btn]');
  await page.click('[data-sheet-confirm]');
  await enterPasscode(page, 'not-the-passcode');
  await settle(page, () => /Wrong dashboard passcode/.test(document.body.innerText));
  ok('a wrong passcode shows "Wrong dashboard passcode." in the modal');
  (serverState() === 'proposed' && srv.refusedProof.length === 1 && srv.refusedProof[0].body.passcode === 'not-the-passcode')
    ? ok('the wrong-passcode POST was refused and the server campaign is unchanged') : fail('after wrong passcode: ' + JSON.stringify({ s: serverState(), r: srv.refusedProof.length }));
  (await page.$('[id^="hp-passcode-"]')) ? ok('the modal is still open for a retry') : fail('modal closed after a wrong passcode');
  await enterPasscode(page, PASSCODE);
  await settle(page, () => window.DeskV1Store.state().campaigns.find((c) => c.id === 'camp-2').approval?.bounds_hash === 'srv-hash-1');
  serverState() === 'active' ? ok('the right passcode on retry starts the campaign') : fail('server state after retry: ' + serverState());
  await ctx.close();

  // Cancel: the optimistic Start is rolled back and the server never sees an accepted POST.
  const srv2 = makeServer();
  const second = await newPage(browser, { live: true, srv: srv2 });
  await settle(second.page, () => window.DeskV1Store.state().campaigns.length > 0);
  await openLaunch(second.page, 'camp-2');
  await second.page.click('[data-map-start-btn]');
  await second.page.click('[data-sheet-confirm]');
  await second.page.waitForSelector('[id^="hp-passcode-"]', { timeout: 8000 });
  await second.page.click('.modal-window[data-modal-id^="__human-proof-"] .btn-secondary');
  await settle(second.page, () => /nothing was changed/.test(document.body.innerText));
  const back = await camp(second.page, 'camp-2');
  (back.state === 'proposed' && !back.approval && calls(srv2, 'POST', /\/start$/).length === 0 && srv2.refusedProof.length === 0)
    ? ok('Cancel rolls the Start back, says nothing was changed, and sends no POST') : fail('after cancel: ' + JSON.stringify({ s: back.state, a: back.approval, p: srv2.refusedProof.length }));
  await second.ctx.close();
}

// ── 6: flag OFF → no Desk route is ever called ─────────────────────────────
async function demoCallsNothing(browser) {
  const srv = makeServer();
  const { ctx, page } = await newPage(browser, { live: false, srv });
  await settle(page, () => window.DeskV1Store.demo());
  await page.evaluate(() => { Object.assign(window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').goal, { metric: 'signups', target: 10, source: 'manual' }); });
  await openLaunch(page, 'camp-2');
  await page.click('[data-map-start-btn]');
  await page.click('[data-sheet-confirm]');
  await settle(page, () => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2').state === 'active');
  await page.click('[data-launch-pause]');
  await settle(page, () => !!document.querySelector('[data-launch-resume]'));
  await page.click('[data-launch-resume]');
  await page.click('[data-confirm-accept]');
  await settle(page, () => !!document.querySelector('[data-launch-pause]'));
  await openHow(page, 'camp-1');
  await page.fill('[data-how-limit="per_week"]', '1');
  await page.press('[data-how-limit="per_week"]', 'Tab');
  await page.waitForTimeout(300);
  srv.log.length === 0 ? ok('flag OFF: Start, Pause, Resume and a Brief edit made 0 /api/desk/* requests') : fail('demo mode called the server: ' + JSON.stringify(srv.log.map((r) => r.method + ' ' + r.path)));
  const c2 = await page.evaluate(() => window.DeskV1Fixtures.campaigns.find((c) => c.id === 'camp-2'));
  (c2.state === 'active' && c2.approval && c2.approval.bounds) ? ok('demo Start still opens a term and records an approval locally') : fail('demo start: ' + JSON.stringify({ s: c2.state, a: c2.approval }));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  console.log('live ON: start'); await start(browser);
  console.log('live ON: pause / resume'); await pauseResume(browser);
  console.log('live ON: brief edit + approve'); await briefAndApprove(browser);
  console.log('live ON: renew'); await renew(browser);
  console.log('live ON: human proof'); await humanProof(browser);
  console.log('live OFF: demo'); await demoCallsNothing(browser);
} catch (e) { fail('harness error: ' + (e && e.stack || e)); }
await browser.close();
if (bad) { console.error(`\n❌ FAIL — ${bad} case(s)`); process.exit(1); }
console.log('\n✅ PASS — S2 Start / Pause / Resume / Brief edits / Approve / Renew call the Desk routes live, take the server\'s approval, roll back refusals, and demo mode calls nothing.');
