#!/usr/bin/env node
/**
 * Desk v1 — the browser sign-in is done on the Details step and HELD for the Save (static/js/
 * desk-v1-connect-held.js, desk-v1-connect-signin.js; Ron 2026-10-05, phone: "if step 3 is the login
 * details, all options should be covered there"). Against a fake server, `desk_v1_live` ON, at 1440 and 390.
 *
 * Before: step 3 had the saved/new login choice and no way to sign in; "Sign in with Higgsfield" only
 * appeared on the Result step AFTER Save. Now step 3 shows every way side by side and step 4 only reviews:
 *
 *   - Details has the three options (In the browser / A saved login / A new login), side by side at 1440 and
 *     stacked at 390, inside the window, with no horizontal scroll and no empty bordered box;
 *   - nothing is sent before a click: no start, no commit, no secrets write;
 *   - "Sign in with Higgsfield" sends the passcode to POST /api/desk/connect/higgsfield/start-held, opens the pane
 *     on the answer's auth_url and profile, follows the flow, and shows "Signed in ... kept in memory";
 *   - Review has NO sign-in button, says the sign-in is done, and Save sends draft.held = {flow_id, claim};
 *     the Result step then has no sign-in to open or fill;
 *   - backing out of the flow sends POST .../flows/<id>/cancel with the claim and never a commit;
 *   - a saved login picked on Details is typed into the open pane (signin/fill) and a new login is stored from
 *     Details with its own passcode;
 *   - X: the Client ID is required before a sign-in starts, the typed app rides start-held, and editing the
 *     Client ID afterwards discards the held sign-in (cancel with the claim);
 *   - a locked vault answers vault_locked: the message and the inline unlock show, no pane opens.
 *
 * Screenshots: docs/desk_v1/screens/connect_held_{options,waiting,held,review,result,x}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-held.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT);

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const PASSCODE = 'right-passcode';
const NEW_PASSWORD = 'hf-pw-3c91d7e04ab6f852';
const CLIENT_ID = 'x-client-id-0123456789';
const CLIENT_SECRET = 'x-client-secret-ab12cd34ef56';
const CLAIM = 'claim-7d1f0c9a5e3b';

// What the real server answers, recorded once: the inspect rows and each profile's sign-in route.
const RECORD = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import methods, registry
methods._vault_names = lambda: set()
out = {}
for sid, name in (('higgsfield', 'Higgsfield'), ('x', 'X')):
    p = registry.profile(sid)
    routes = [{'route_id': r['id'], 'title': r['title'], 'connect_method': r.get('connect_method'), 'url': r['signin']['url'], 'hosts': r['signin']['hosts']}
              for r in p['routes'] if r.get('signin')]
    out[sid] = {'inspect': methods.inspect(name), 'routes': routes}
print(json.dumps(out))
`;
const REAL = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', RECORD], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const LOGINS = [{ name: 'higgsfield.login', matches: true }, { name: 'reddit.login', matches: false }];
const LABEL = { higgsfield: 'Higgsfield', x: 'X' };

function makeServer(service, { locked = false } = {}) {
  const fx = loadFixtures();
  const srv = { service, log: [], fx, logins: LOGINS.map((l) => ({ ...l })), locked, pending: 2, cancelled: false };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskGuidePollMs = 40; window.__panes = []; });
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
    })));
    if (path === '/api/telemetry/summary') return J({});
    if (path === '/api/features') return J({});
    if (path === '/api/config/models') return J({ models: [] });
    if (path === '/api/config' && method === 'GET') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path === '/api/secrets/vault-lock' && method === 'GET') return J({ state: srv.locked ? 'locked' : 'unlocked', configured: true });
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    const svc = srv.service;
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/suggest') return J({ q: '', suggestions: [] });
    if (path === '/api/desk/connect/inspect' && method === 'POST') return J(REAL[svc].inspect);
    if (path === '/api/desk/connect/purposes') return J({ error: 'not expected' }, 500);
    if (path === '/api/desk/connect/signin/options') return J({ service: svc, routes: REAL[svc].routes, logins: srv.logins, vault_locked: srv.locked });
    if (path === '/api/desk/connect/signin/store-login') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.logins.unshift({ name: body.new_login.name, matches: true });
      return J({ ok: true, login: { name: body.new_login.name, username: body.new_login.username } }, 201);
    }
    if (path === '/api/desk/connect/signin/fill') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      return J({ ok: true, state: 'submitted', message: 'The login was typed and submitted. Check the pane for the result.' });
    }
    if (path === `/api/desk/connect/${svc}/start-held` && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.locked) return J({ error: 'Unlock the vault first, then sign in', code: 'vault_locked' }, 409);
      srv.pending = 2;
      return J({ flow_id: 'flow-held', auth_url: `https://${svc}.example/oauth/authorize?state=abc`, redirect_uri: 'http://127.0.0.1:53682/callback',
        profile: `desk-${svc}`, claim: CLAIM, hold_ttl_s: 900, ...(svc === 'x' ? { account_id: 'acct-new' } : {}) }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-held' && method === 'GET') {
      if (srv.cancelled) return J({ status: 'unknown', message: 'The held sign-in is no longer there.' });
      if (srv.pending-- > 0) return J({ status: 'pending', message: '' });
      return J({ status: 'held', message: 'signed in', service: svc, held_ttl_s: 880 });
    }
    if (path === '/api/desk/connect/flows/flow-held/cancel' && method === 'POST') { srv.cancelled = true; return J({ ok: body && body.claim === CLAIM }); }
    if (path === '/api/desk/connect/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      const held = !!(body.draft && body.draft.held);
      return J({ ok: true, duplicate: false, service: { id: svc, label: LABEL[svc] }, method: 'oauth', credential: null,
        stored: held ? [`oauth.${svc}`] : [], account: svc === 'x' ? { id: 'acct-new', label: '@ron', identity: '@ron' } : null,
        account_id: svc === 'x' ? 'acct-new' : null,
        status: held ? { state: 'signed_in', label: 'Signed in', entry: `oauth.${svc}` } : { state: 'sign_in_required', label: 'Sign-in required', entry: null },
        ...(held ? {} : { signin: { flow_id: 'flow-later', auth_url: `https://${svc}.example/oauth/authorize?state=zzz`, redirect_uri: 'x', profile: `desk-${svc}` } }) }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-later') return J({ status: 'pending' });
    if (path === '/api/desk/connect/verify' && method === 'POST') return J({ state: 'signed_in', label: 'Signed in', entry: `oauth.${svc}` });
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => { window.sidebarNav('social'); });
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  await page.evaluate(() => { window.openBrowserPane = async (...a) => { window.__panes.push(a); }; });
  return { ctx, page, pageErrors };
}

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_held_${name}_${w}.png`) });
const posts = (srv, p) => srv.log.filter((r) => r.method === 'POST' && r.path === p);
const PASSCODE_SEL = 'input[id^="hp-passcode-"]';

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0 };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
}

async function inView(page, selector, label) {
  const r = await page.$eval(selector, (e) => { e.scrollIntoView({ block: 'nearest' }); const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, vw: window.innerWidth }; });
  check(r.l >= 0 && r.r <= r.vw, `${label} is inside the ${r.vw}px window`, `${label} sticks out sideways ${JSON.stringify(r)}`);
}

async function passcode(page) {
  await page.waitForSelector(PASSCODE_SEL, { timeout: 4000 });
  await page.fill(PASSCODE_SEL, PASSCODE);
  await page.click('.modal-content .btn-add');
}

async function toDetails(page, name) {
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', name);
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method][value="oauth"]');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
  await page.waitForSelector('[data-cs-opt="browser"]', { timeout: 3000 });
}

const emptyBoxes = (page) => page.$$eval('[data-cf-details-form] *', (els) => els.filter((e) => {
  const cs = getComputedStyle(e);
  const b = e.getBoundingClientRect();
  const border = parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none';
  return border && b.width > 0 && b.height > 0 && !e.textContent.trim() && !e.querySelector('input,select,textarea,button')
    && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(e.tagName);
}).length);

const panes = (page) => page.evaluate(() => window.__panes.map((a) => ({ url: a[0], profile: a[3] })));

async function higgsfield(browser, width, height) {
  console.log(`live ON: Higgsfield, every way to sign in on Details and the Save claims it, at ${width}`);
  const srv = makeServer('higgsfield');
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page, 'Higgsfield');
  await page.waitForSelector('[data-cs-pick]', { timeout: 3000 }).catch(() => {});

  // 1. every option is on step 3
  check(!!(await page.$('[data-cs-opt="browser"] [data-cfh-start]')) && /Sign in with Higgsfield/.test(await page.textContent('[data-cfh-start]')),
    'Details has the browser sign-in button ("Sign in with Higgsfield")', 'no browser sign-in button on Details');
  check(!!(await page.$('[data-cs-opt="saved"] [data-cs-pick]')), 'Details has the saved-login picker', 'no saved-login picker on Details');
  check(!!(await page.$('[data-cs-opt="new"] [data-cs-toggle]')), 'Details has "Store a new login…"', 'no store-a-new-login control on Details');
  const boxes = await page.$$eval('[data-cs-opt]', (els) => els.map((e) => { const b = e.getBoundingClientRect(); return { l: Math.round(b.left), t: Math.round(b.top), r: Math.round(b.right) }; }));
  const sideBySide = boxes.length === 3 && boxes.every((b) => Math.abs(b.t - boxes[0].t) <= 2);
  const stacked = boxes.length === 3 && boxes.every((b) => Math.abs(b.l - boxes[0].l) <= 2) && boxes[1].t > boxes[0].t && boxes[2].t > boxes[1].t;
  if (width >= 900) check(sideBySide, 'the three options sit side by side', 'options are not side by side: ' + JSON.stringify(boxes));
  else check(stacked, 'the three options stack in one column', 'options are not stacked: ' + JSON.stringify(boxes));
  check((await emptyBoxes(page)) === 0, 'Details shows no empty bordered box', `${await emptyBoxes(page)} empty bordered box(es) on Details`);
  await shot(page, 'options', width);
  await fits(page, 'Details');
  await inView(page, '[data-cfh-start]', 'the browser sign-in button');
  await inView(page, '[data-cf-next]', 'Review');
  check(posts(srv, '/api/desk/connect/higgsfield/start-held').length === 0 && posts(srv, '/api/desk/connect/commit').length === 0,
    'nothing is sent before a click', 'a request was sent before any click');

  // 2. the browser sign-in: passcode first, then the pane, then held
  await page.click('[data-cfh-start]');
  await page.waitForSelector(PASSCODE_SEL, { timeout: 4000 });
  check(posts(srv, '/api/desk/connect/higgsfield/start-held').length === 0, 'the passcode prompt opens first; nothing is sent until it is typed', 'start-held was sent before the passcode');
  await passcode(page);
  await page.waitForSelector('[data-cfh-state="waiting"], [data-cfh-state="held"]', { timeout: 4000 });
  const started = posts(srv, '/api/desk/connect/higgsfield/start-held');
  check(started.length === 1 && started[0].body.passcode === PASSCODE, 'start-held is sent once with the passcode', 'start-held posts: ' + JSON.stringify(started.map((s) => s.body)));
  check(posts(srv, '/api/desk/connect/commit').length === 0 && !srv.log.some((r) => r.path.startsWith('/api/secrets')), 'signing in on Details writes nothing: no commit, no secrets', 'a write happened while signing in');
  const p = await panes(page);
  check(p.length >= 1 && /higgsfield\.example\/oauth\/authorize/.test(p[0].url) && p[0].profile === 'desk-higgsfield', 'the sign-in opens in its named browser pane', 'pane opens: ' + JSON.stringify(p));
  await shot(page, 'waiting', width);

  // 3. held
  await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
  check(/memory/.test(await page.textContent('[data-cfh-state="held"]')) && /Save/.test(await page.textContent('[data-cfh-state="held"]')),
    'Details says it is signed in and held until Save', 'the held state text is wrong');
  await shot(page, 'held', width);
  await fits(page, 'Details, signed in');

  // 4. Review has no sign-in button and says it is done
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  check((await page.$$('[data-cfh-start], [data-cs-fillbtn], [data-cfr-signin]')).length === 0, 'Review holds no sign-in button', 'Review still has a sign-in button');
  check(/Done in the browser/.test(await page.textContent('[data-cfh-review="held"]').catch(() => '')), 'Review says the sign-in is done', 'Review does not say the sign-in is done');
  check((await page.textContent('[data-cf-save]')).trim() === 'Save', 'the button says "Save" (the sign-in is already done)', 'button: ' + (await page.textContent('[data-cf-save]')));
  await shot(page, 'review', width);
  await fits(page, 'Review');

  // 5. Save claims it; the Result step has no sign-in to open
  await page.click('[data-cf-save]');
  await passcode(page);
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  const commit = posts(srv, '/api/desk/connect/commit');
  check(commit.length === 1 && commit[0].body.draft.held && commit[0].body.draft.held.flow_id === 'flow-held' && commit[0].body.draft.held.claim === CLAIM,
    'Save sends the held sign-in (flow id and claim) in the one commit', 'commit draft: ' + JSON.stringify(commit.map((c) => c.body.draft)));
  check(!JSON.stringify(commit[0].body).includes('access_token'), 'no token is in the Save request', 'a token field is in the Save request');
  check((await page.$$('[data-cfr-signin], [data-cs-fillbtn], [data-cs-result]')).length === 0, 'the Result step has no sign-in to open or fill', 'the Result step still offers a sign-in');
  check(/Signed in/.test(await page.textContent('[data-cf-result-status]')), 'Result shows Signed in', 'Result status: ' + (await page.textContent('[data-cf-result-status]')));
  check(posts(srv, '/api/desk/connect/flows/flow-held/cancel').length === 0, 'a saved sign-in is not cancelled', 'the saved sign-in was cancelled');
  await shot(page, 'result', width);
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function backOut(browser, width, height) {
  console.log(`live ON: Higgsfield, backing out of a held sign-in, at ${width}`);
  const srv = makeServer('higgsfield');
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page, 'Higgsfield');
  await page.click('[data-cfh-start]');
  await passcode(page);
  await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
  await page.click('[data-cf-back]');                                 // Method: the same method keeps its sign-in
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  check(posts(srv, '/api/desk/connect/flows/flow-held/cancel').length === 0, 'Back to the Method step keeps the sign-in', 'the sign-in was cancelled on Back to Method');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
  check(true, 'the held sign-in is still there when Details is opened again');
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.click('[data-cf-back]');                                 // Service: leaving the method choice drops it
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  await page.waitForFunction(() => true);
  const cancels = posts(srv, '/api/desk/connect/flows/flow-held/cancel');
  check(cancels.length === 1 && cancels[0].body.claim === CLAIM, 'leaving the flow cancels the held sign-in with its claim', 'cancel posts: ' + JSON.stringify(cancels.map((c) => c.body)));
  check(posts(srv, '/api/desk/connect/commit').length === 0 && !srv.log.some((r) => r.path.startsWith('/api/secrets')), 'backing out wrote nothing', 'a write happened');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function logins(browser, width, height) {
  console.log(`live ON: Higgsfield, a saved and a new login next to the browser sign-in, at ${width}`);
  const srv = makeServer('higgsfield');
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page, 'Higgsfield');
  await page.waitForSelector('[data-cs-pick]', { timeout: 3000 });
  check(await page.$eval('[data-cs-fillbtn]', (b) => b.disabled), 'typing a saved login into the page is off until the sign-in page is open', 'the fill button is on with no pane open');
  await page.selectOption('[data-cs-pick]', 'higgsfield.login');
  await page.click('[data-cfh-start]');
  await passcode(page);
  await page.waitForSelector('[data-cfh-state="waiting"]', { timeout: 4000 });
  check(!(await page.$eval('[data-cs-fillbtn]', (b) => b.disabled)), 'with the page open and a login chosen, "Sign in with the saved login" is on', 'the fill button stayed off');
  await page.click('[data-cs-fillbtn]');
  await passcode(page);
  await page.waitForSelector('[data-cs-fillmsg]', { timeout: 4000 });
  const fills = posts(srv, '/api/desk/connect/signin/fill');
  check(fills.length === 1 && fills[0].body.login === 'higgsfield.login' && fills[0].body.profile === 'desk-higgsfield' && fills[0].body.route_id === 'higgsfield-oauth',
    'the fill sends a login NAME, the route and the pane profile', 'fill posts: ' + JSON.stringify(fills.map((f) => f.body)));
  // a new login typed on Details is stored from Details
  await page.click('[data-cs-toggle]');
  const userId = '[data-cs-slot] input[id$="-user"]';
  const pwId = '[data-cs-slot] input[type="password"]';
  await page.waitForSelector(userId, { timeout: 3000 });
  await page.fill(userId, 'ron@example.com');
  await page.fill(pwId, NEW_PASSWORD);
  await fits(page, 'Details with a new login open');
  await inView(page, '[data-cs-savelogin]', '"Save this login"');
  check(posts(srv, '/api/desk/connect/signin/store-login').length === 0, 'the typed login is not sent until "Save this login"', 'the typed login was sent early');
  await page.click('[data-cs-savelogin]');
  await passcode(page);
  await page.waitForSelector('[data-cs-msg="ok"]', { timeout: 4000 });
  const stores = posts(srv, '/api/desk/connect/signin/store-login');
  check(stores.length === 1 && stores[0].body.new_login.value === NEW_PASSWORD && stores[0].body.route_id === 'higgsfield-oauth', 'Save this login stores the typed login once, with the passcode, from Details', 'store posts: ' + JSON.stringify(stores.length));
  check((await page.$eval('[data-cs-pick]', (s) => s.value)) === stores[0].body.new_login.name, 'the stored login is selected', 'the stored login is not selected');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function xApp(browser, width, height) {
  console.log(`live ON: X, the app is typed before the sign-in and changing it discards the held sign-in, at ${width}`);
  const srv = makeServer('x');
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page, 'X');
  check(!!(await page.$('[data-cs-opt="browser"] [data-cfh-start]')) && !!(await page.$('[data-cfa-field="client_id"]')),
    'X Details has the app fields and the browser sign-in', 'X Details is missing the app fields or the sign-in button');
  await page.click('[data-cfh-start]');
  await page.waitForSelector('[data-cfh-state="failed"]', { timeout: 3000 });
  check(/required/i.test(await page.textContent('[data-cfh-state="failed"]')) && posts(srv, '/api/desk/connect/x/start-held').length === 0,
    'without the Client ID the sign-in does not start and says what is missing', 'the sign-in started without the app details');
  await page.fill('[data-cfa-field="identity"]', '@ron');
  await page.fill('[data-cfa-field="client_id"]', CLIENT_ID);
  await page.fill('[data-cfa-field="client_secret"]', CLIENT_SECRET);
  await page.click('[data-cfh-start]');
  await passcode(page);
  await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
  const started = posts(srv, '/api/desk/connect/x/start-held');
  check(started.length === 1 && started[0].body.hold && started[0].body.hold.client_id === CLIENT_ID && started[0].body.hold.client_secret === CLIENT_SECRET && started[0].body.passcode === PASSCODE,
    'the typed app (Client ID and Secret) rides the passcode-gated start, once', 'start posts: ' + JSON.stringify(started.map((s) => Object.keys(s.body))));
  check(posts(srv, '/api/desk/connect/commit').length === 0 && !srv.log.some((r) => r.path.startsWith('/api/secrets')), 'nothing was saved: no commit, no secrets write', 'a write happened');
  await shot(page, 'x', width);
  await fits(page, 'X Details, signed in');
  await page.fill('[data-cfa-field="client_id"]', CLIENT_ID + 'x');
  await page.waitForSelector('[data-cfh-state="failed"]', { timeout: 3000 });
  check(/changed the app/i.test(await page.textContent('[data-cfh-state="failed"]')), 'editing the Client ID discards the held sign-in and says so', 'no message after editing the Client ID');
  await page.waitForFunction(() => true);
  const cancels = posts(srv, '/api/desk/connect/flows/flow-held/cancel');
  check(cancels.length === 1 && cancels[0].body.claim === CLAIM, 'the discarded sign-in is cancelled with its claim', 'cancel posts: ' + JSON.stringify(cancels.map((c) => c.body)));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function locked(browser, width, height) {
  console.log(`live ON: a locked vault is reported with its unlock, no pane opens, at ${width}`);
  const srv = makeServer('higgsfield', { locked: true });
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page, 'Higgsfield');
  await page.waitForSelector('[data-vault-gate]:not([hidden])', { timeout: 4000 }).catch(() => {});
  check(!!(await page.$('[data-vault-gate]:not([hidden])')), 'the vault gate shows before any sign-in starts', 'no vault gate while the vault is locked');
  await page.click('[data-cfh-start]');
  await passcode(page);
  await page.waitForSelector('[data-cfh-state="failed"]', { timeout: 4000 });
  check(/Unlock the vault/.test(await page.textContent('[data-cfh-state="failed"]')), 'the refusal says to unlock the vault', 'the locked-vault message is missing');
  check((await panes(page)).length === 0, 'no browser pane opened', 'a pane opened with the vault locked');
  check(!!(await page.$('[data-vault-gate]:not([hidden])')), 'the unlock is still on screen', 'the unlock disappeared');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  for (const [w, h] of [[1440, 900], [390, 844]]) {
    await higgsfield(browser, w, h);
    await backOut(browser, w, h);
    await logins(browser, w, h);
    await xApp(browser, w, h);
    await locked(browser, w, h);
  }
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
