#!/usr/bin/env node
/**
 * Desk v1 — the Details step of a SIGN-IN method offers the login choice (static/js/desk-v1-connect-signin.js,
 * slice P2b follow-up; Ron 2026-10-05, phone: Add service > Higgsfield > Sign in with Higgsfield > Details
 * showed ONE EMPTY input-like box and nothing else). Against a fake server, `desk_v1_live` ON, at 1440 and 390.
 *
 * Higgsfield's sign-in method has no fields, so its Details step was an empty bordered slot: the saved/new login
 * panel P2b built sat on the Result step, after Save, where nobody expected it.
 *
 *   - Details shows the method's own summary, a "Use a saved login" picker (names only, from the fake
 *     /signin/options) and "Store a new login…", and NO empty bordered box;
 *   - a picked login is carried to Review (a row) and to the Result step (preselected, the sign-in button on);
 *   - a new login typed on Details: nothing is sent before Save (no store-login, no secrets), Review shows its name
 *     and username with the password hidden, the form survives Back, and on Result it is still typed there with
 *     "Save this login" (its own passcode) — the password is in its own <input> and nowhere else;
 *   - nothing overflows sideways and Back / Review are inside the window.
 *
 * Screenshots: docs/desk_v1/screens/connect_signin_details_{pick,new,review}_{1440,390}.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-signin-details.mjs
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

// What the real server answers for Higgsfield, recorded once: the inspect row and the profile's sign-in route.
const RECORD = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import methods, registry
methods._vault_names = lambda: set()
p = registry.profile('higgsfield')
routes = [{'route_id': r['id'], 'title': r['title'], 'connect_method': r.get('connect_method'), 'url': r['signin']['url'], 'hosts': r['signin']['hosts']}
          for r in p['routes'] if r.get('signin')]
print(json.dumps({'inspect': methods.inspect('Higgsfield'), 'routes': routes}))
`;
const REAL = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', RECORD], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const OAUTH_ROW = REAL.inspect.options.find((o) => o.method === 'oauth');
const LOGINS = [{ name: 'higgsfield.login', matches: true }, { name: 'reddit.login', matches: false }];

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, logins: LOGINS.map((l) => ({ ...l })) };
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
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/suggest') return J({ q: '', suggestions: [] });
    if (path === '/api/desk/connect/inspect' && method === 'POST') return J(REAL.inspect);
    if (path === '/api/desk/connect/purposes') return J({ error: 'not expected' }, 500);
    if (path === '/api/desk/connect/signin/options') return J({ service: 'higgsfield', routes: REAL.routes, logins: srv.logins, vault_locked: false });
    if (path === '/api/desk/connect/signin/store-login') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      srv.logins.unshift({ name: body.new_login.name, matches: true });
      return J({ ok: true, login: { name: body.new_login.name, username: body.new_login.username } }, 201);
    }
    if (path === '/api/desk/connect/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      return J({ ok: true, duplicate: false, service: { id: 'higgsfield', label: 'Higgsfield' }, method: 'oauth', credential: null, stored: [],
        account: null, account_id: null, status: { state: 'sign_in_required', label: 'Sign-in required', entry: null },
        signin: { flow_id: 'flow-hf', auth_url: 'https://higgsfield.ai/oauth/authorize?state=abc', redirect_uri: 'http://127.0.0.1:53682/callback', profile: 'desk-higgsfield' } }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-hf') return J({ status: 'pending' });
    if (path === '/api/desk/connect/verify' && method === 'POST') return J({ state: 'sign_in_required', label: 'Sign-in required', entry: null });
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
const step = (page) => page.$eval('[data-cf]', (e) => e.dataset.cfStep).catch(() => null);
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_signin_details_${name}_${w}.png`) });
const posts = (srv, p) => srv.log.filter((r) => r.method === 'POST' && r.path === p);

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
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
}

async function toDetails(page) {
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', 'Higgsfield');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method][value="oauth"]');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
}

// What the human sees in the Details step: a visible bordered box that holds no text and no control.
const emptyBoxes = (page) => page.$$eval('[data-cf-details-form] *', (els) => els.filter((e) => {
  const cs = getComputedStyle(e);
  const b = e.getBoundingClientRect();
  const border = parseFloat(cs.borderTopWidth) > 0 && cs.borderTopStyle !== 'none';
  return border && b.width > 0 && b.height > 0 && !e.textContent.trim() && !e.querySelector('input,select,textarea,button')
    && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(e.tagName);
}).length);

async function pickFlow(browser, width, height) {
  console.log(`live ON: Higgsfield sign-in, Details offers a saved login, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page);
  await page.waitForSelector('[data-cs-pick]', { timeout: 3000 }).catch(() => {});
  check(!!(await page.$('[data-cf-d-summary]')) && (await page.textContent('[data-cf-d-summary]')).includes(OAUTH_ROW.connector.summary),
    'Details says what the method does (the method\'s own summary)', 'no summary text on Details');
  check(!!(await page.$('[data-cs-pick]')), 'Details has the "Use a saved login" picker', 'no saved-login picker on Details');
  const opts = (await page.$$eval('[data-cs-pick] option', (o) => o.map((x) => x.value).filter(Boolean))).join();
  check(opts === 'higgsfield.login,reddit.login', `the picker lists the vault logins by name (${opts})`, 'picker options: ' + opts);
  check(!!(await page.$('[data-cs-toggle]')) && /Store a new login/.test(await page.textContent('[data-cs-toggle]')), 'Details offers "Store a new login…"', 'no store-a-new-login control on Details');
  check((await emptyBoxes(page)) === 0, 'Details shows no empty bordered box', `${await emptyBoxes(page)} empty bordered box(es) on Details`);
  await shot(page, 'pick', width);
  await fits(page, 'Details');
  await inView(page, '[data-cs-pick]', 'the picker');
  await inView(page, '[data-cf-next]', 'Review');
  await inView(page, '[data-cf-back]', 'Back');

  await page.selectOption('[data-cs-pick]', 'higgsfield.login');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  check(/higgsfield\.login/.test(await page.textContent('[data-cs-r-login]').catch(() => '')), 'Review shows the login that will be used', 'Review has no sign-in login row');
  await shot(page, 'review', width);
  await fits(page, 'Review');
  check(posts(srv, '/api/desk/connect/signin/store-login').length === 0 && !srv.log.some((r) => r.path.startsWith('/api/secrets')), 'nothing was stored before Save', 'a write happened before Save');
  await page.click('[data-cf-save]');
  await passcode(page);
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  await page.waitForSelector('[data-cs-pick]', { timeout: 3000 });
  check((await page.$eval('[data-cs-pick]', (s) => s.value)) === 'higgsfield.login', 'the Result step has the same login selected', 'the pick was lost on Result');
  check(!!(await page.$('[data-cs-fillbtn]')) && !(await page.$eval('[data-cs-fillbtn]', (b) => b.disabled)), 'Sign in with the saved login is ready on Result', 'the sign-in button is missing or off on Result');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function newLoginFlow(browser, width, height) {
  console.log(`live ON: Higgsfield sign-in, a new login typed on Details, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toDetails(page);
  await page.waitForSelector('[data-cs-toggle]', { timeout: 3000 }).catch(() => {});
  if (!(await page.$('[data-cs-toggle]'))) { fail('no store-a-new-login control on Details'); await ctx.close(); return; }
  await page.click('[data-cs-toggle]');
  const userId = '[data-cs-slot] input[id$="-user"]';
  await page.waitForSelector(userId, { timeout: 3000 }).catch(() => {});
  const pwId = '[data-cs-slot] input[type="password"]';
  check(!!(await page.$(userId)) && !!(await page.$(pwId)), 'the new-login form has a username and a password box on Details', 'new-login form is missing a box');
  await page.fill(userId, 'ron@example.com');
  await page.fill(pwId, NEW_PASSWORD);
  await shot(page, 'new', width);
  await fits(page, 'Details with the form open');
  await inView(page, pwId, 'the password box');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  const reviewText = await page.textContent('[data-cf]');
  check(/ron@example\.com/.test(reviewText) && !reviewText.includes(NEW_PASSWORD), 'Review names the new login and its username; the password is shown nowhere', 'Review text: ' + reviewText.slice(0, 200));
  check(posts(srv, '/api/desk/connect/signin/store-login').length === 0 && !srv.log.some((r) => r.path.startsWith('/api/secrets')), 'nothing was stored before Save', 'a write happened before Save');
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
  check((await page.inputValue(pwId)) === NEW_PASSWORD, 'Back keeps the typed password in its own box', 'the typed password was lost on Back');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  await page.click('[data-cf-save]');
  await passcode(page);
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  await page.waitForSelector('[data-cs-savelogin]', { timeout: 3000 }).catch(() => {});
  check(!!(await page.$('[data-cs-savelogin]')), 'Result still has the typed login with "Save this login"', 'the typed login did not reach Result');
  if (await page.$('[data-cs-savelogin]')) {
    await page.click('[data-cs-savelogin]');
    await passcode(page);
    await page.waitForSelector('[data-cs-msg="ok"]', { timeout: 4000 }).catch(() => {});
    const stores = posts(srv, '/api/desk/connect/signin/store-login');
    check(stores.length === 1 && stores[0].body.new_login.value === NEW_PASSWORD && stores[0].body.service === 'higgsfield' && stores[0].body.route_id === 'higgsfield-oauth',
      'Save this login sends the typed login once, with the passcode', 'store-login posts: ' + JSON.stringify(stores.map((s) => ({ ...s.body, new_login: '…' }))));
    check((await page.$eval('[data-cs-pick]', (s) => s.value)) === stores[0].body.new_login.name, 'the stored login is selected', 'the stored login is not selected');
  }
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await pickFlow(browser, 1440, 900);
  await pickFlow(browser, 390, 844);
  await newLoginFlow(browser, 1440, 900);
  await newLoginFlow(browser, 390, 844);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
