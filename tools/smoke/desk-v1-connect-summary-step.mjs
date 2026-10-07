#!/usr/bin/env node
/**
 * Desk v1 — the Connect wizard's REVIEW and RESULT screens for a Sign in or API connection (MC-1062 ticket 13a,
 * docs/desk_v1/connect_flow_tickets/13a-review-result.md; static/js/desk-v1-connect-summary-step.js), the browser
 * half, against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The Service/Connection screens are the REAL
 * ones, drawn from the REAL type projection (mc.desk_connect.type_view). The commits of the sign-in and API steps are
 * FAKES (their own smokes pin their requests and passcode prompts); the Permissions step is the REAL module with only
 * `summary` and `apply` replaced, so its real `outcome()` words are what the Result shows. This smoke pins what 13a
 * owns: ordering, "saved is not verified", and "a failed permission is not a success".
 *
 *   1. Review      service, type, account, login and profile, the permissions chosen, the material risks, and the
 *                  separate checks the Save will ask for; no secret anywhere; one primary, one Details, nothing nested.
 *   2. Save        step 1 (the connection commit) runs first; step 2 (permissions apply) only after it succeeded and
 *                  only with the account id it returned. A refused or cancelled step 1 stays on Review and never
 *                  calls step 2.
 *   3. Partial     connection saved + permission failed/cancelled/deferred: the Result says "permissions unchanged",
 *                  never success; "Try permissions again" repeats only step 2, never the connection save.
 *   4. Distinct    stored login is not signed in is not registered is not verified; a failed check reads as failed.
 *   5. API         install card must be approved (and the approval dies with Back); cost risk; Check it now / Refresh
 *                  status are the existing read-only verify, once per click.
 *   6. No gate     no target or no permissions chosen: Save is off and says why.
 *   7. Source      the module never touches the passcode, storage or a request of its own besides verify.
 *   8. Leaving     Close empties what it holds.
 *   9. Fit         no horizontal scroll and the primary action reachable at 1440, 390 and 200% text.
 *
 * Screenshots: docs/desk_v1/screens/connect_summary_step_{review,result_partial,result_api}_{1440,390}.png.
 *
 * RUN   cd tools/smoke && node desk-v1-connect-summary-step.mjs
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
const SUMMARY_SRC = readFileSync(resolve(REPO_ROOT, 'static', 'js', 'desk-v1-connect-summary-step.js'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, 'docs', 'desk_v1', 'screens');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT, { isolateConnectScreens: true });

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const PY = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
types = {}
for raw in ['x.com', 'linkedin.com']:
    got = resolve.resolve(raw, own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    types[raw] = {**got, **type_view.project_service(svc['id'] if svc else None)}
print(json.dumps({'types': types}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const TYPES = PY.types;

const PASSWORD = 'hunter2-never-shown-9z9z';          // lives only inside the fake step's closure
const CARD = {
  name: 'x-mcp-pkg', version: '1.2.3', integrity: 'sha512-AAAA', source: 'github.com/example/x-mcp', licence: 'MIT', unpacked_bytes: 123456,
  purpose: 'Reads your X posts.', server_name: 'x-mcp', credential: { label: 'X token', vault: 'x.token', env: 'X_TOKEN' },
  permissions: ['Reads posts'], notice: '', pins: { package: 'x-mcp-pkg', version: '1.2.3', integrity: 'sha512-AAAA' },
};

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, verify: () => ({ state: 'signed_in', label: 'Signed in' }) };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  // The fake commits of the sign-in and API steps. Their real files are blanked below, so these are all that exists.
  await page.addInitScript((args) => {
    const fx = window.__fx = { calls: [], loginOut: null, apiOut: null, login: null, card: null, panes: [], applyOut: null, permsSummary: null, secret: args.PASSWORD };
    window.DeskV1ConnectLoginStep = {
      setTarget() {},
      summary: () => fx.login,
      commit: async () => { fx.calls.push('login.commit'); return fx.loginOut; },
    };
    window.DeskV1ConnectApiStep = {
      setTarget() {},
      reviewHTML: () => '<dl class="desk-v1-cf-facts"><div><dt>App</dt><dd data-fx-app>Client ID entered, hidden. Client Secret entered, hidden.</dd></div></dl>',
      installCard: () => fx.card,
      commit: async () => { fx.calls.push('api.commit'); return fx.apiOut; },
    };
  }, { PASSWORD });
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    // their own Setup screens would shadow this smoke's fixture screens, and they would replace the fakes above
    if (path === '/static/js/desk-v1-connect-login-step.js' || path === '/static/js/desk-v1-connect-api-step.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });
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
    if (path === '/api/secrets/vault-lock' && method === 'GET') return J({ state: 'unlocked', configured: true });
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    if (path.startsWith('/api/secrets') || path === '/api/browser/launch') { srv.log.push({ method, path, body }); return J({ error: 'not expected' }, 500); }
    if (path === '/api/browser/profiles') return J({ profiles: [] });
    if (!path.startsWith('/api/desk/')) return route.abort();
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/suggest') return J({ q: '', suggestions: [] });
    if (path === '/api/desk/connect/types' && method === 'POST') {
      const t = TYPES[String((body || {}).input || '').trim()];
      return t ? J(t) : J({ error: 'unknown', code: 'unknown_name' }, 400);
    }
    if (path === '/api/desk/connect/verify' && method === 'POST') return J(srv.verify(body));
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors, srv };
}

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_summary_step_${name}_${w}.png`) });
async function waitStep(page, step) { await page.waitForSelector(`[data-cfw][data-cfw-step="${step}"]`, { timeout: 4000 }); }
const logOf = (srv, re) => srv.log.filter((r) => re.test(r.path));
const calls = (page) => page.evaluate(() => window.__fx.calls.slice());
const text = (page, sel) => page.$eval(sel, (e) => e.textContent.replace(/\s+/g, ' ').trim());
const has = async (page, sel) => !!(await page.$(sel));
const repaint = (page) => page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));
const secretOnPage = (page, s) => page.evaluate((v) => document.body.innerHTML.includes(v) || JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(v), s);

async function rules(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('[data-cfw]');
    const words = Array.from(root.querySelectorAll('[data-cfw-copy]')).map((e) => e.textContent.trim().split(/\s+/).filter(Boolean).length).reduce((a, b) => a + b, 0);
    return { words, details: root.querySelectorAll('details').length, nested: root.querySelectorAll('details details, form form').length,
      forms: root.querySelectorAll('form').length, primaries: root.querySelectorAll('[data-cfw-primary]').length };
  });
  check(m.words <= 25, `${label}: ${m.words} explanatory words (<= 25)`, `${label}: ${m.words} explanatory words`);
  check(m.details <= 1 && m.nested === 0, `${label}: at most one Details, nothing nested (${m.details})`, `${label}: details ${m.details}, nested ${m.nested}`);
  check(m.forms === 1 && m.primaries <= 1, `${label}: one form, ${m.primaries} primary action`, `${label}: forms ${m.forms}, primaries ${m.primaries}`);
}

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const scroller = document.querySelector('[data-connections]');
    const out = { docOver: document.documentElement.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0, vw: window.innerWidth, vh: window.innerHeight, controls: [] };
    for (const sel of ['[data-cfw-primary]', '[data-cfw-back]']) {
      const b = document.querySelector(sel);
      if (!b) continue;
      b.scrollIntoView({ block: 'nearest' });
      const r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      out.controls.push({ sel, left: r.left, right: r.right, top: r.top, bottom: r.bottom, reachable: !!hit && (hit === b || b.contains(hit)) });
    }
    return out;
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  for (const c of m.controls) check(c.reachable && c.left >= 0 && c.right <= m.vw + 0.5 && c.top >= 0 && c.bottom <= m.vh + 1, `${label}: ${c.sel} is inside the ${m.vw}x${m.vh} window and not covered`, `${label}: ${JSON.stringify(c)}`);
}

// Fixture Setup and Permissions screens (other tickets' screens), and the Permissions step's `summary`/`apply`.
async function registerFixtures(page) {
  await page.evaluate(() => {
    const W = window.DeskV1ConnectWizard, P = window.DeskV1ConnectPermissionsStep, fx = window.__fx;
    W.registerScreen({ id: 'fx-setup', step: 'setup', title: () => 'Fixture setup', copy: () => 'Set it up.', body: () => '<p>Setup</p>', primary: () => ({ label: 'Continue' }) });
    W.registerScreen({ id: 'fx-permissions', step: 'permissions', title: () => 'Fixture permissions', copy: () => 'Choose.', body: () => '<p>Read</p>', primary: () => ({ label: 'Continue' }) });
    window.openBrowserPane = (...a) => { fx.panes.push(a); };
    P.summary = () => fx.permsSummary;
    P.apply = async (accountId) => { fx.calls.push('perms.apply:' + accountId); return fx.applyOut; };
  });
}

const SUMMARY_OK = { kind: 'account', pending: true, lines: ['Desk: Read Your posts through the browser sign-in', 'Desk: Post through the browser sign-in'] };
const APPLY_OK = { ok: true, pending: false, desk: { status: 'saved' }, site: { status: 'unchanged' } };
const SIGNIN_LOGIN = { mode: 'new', login: 'x.ronx', newLogin: true, username: 'ronx', hasPassword: true, profile: 'x-ronx', newProfile: true };
const SIGNIN_OUT = { ok: true, result: { account_id: 'acct-1', account_created: true, unchanged: false, service: 'x', route_id: 'x-browser', account_kind: 'account', browser_profile: 'x-ronx', profile_state: 'new', login: 'x.ronx', login_created: true, shared_with: [] } };
const API_OUT = { ok: true, result: { duplicate: false, service: { id: 'x', label: 'X' }, method: 'oauth', account_id: 'acct-2', account: { label: 'Ron on X' }, stored: ['x.client-id'], status: { state: 'key_stored', label: 'Key stored' } } };
const X_NEW = { kind: 'account', identity: 'ronx', account: { new: { identity: 'ronx', label: 'Ron on X' } } };

// Open the wizard on an address, pick `type`, go through fixture Setup and Permissions to Review. `cfg` sets the fakes.
async function toReview(page, address, type, cfg, { target = X_NEW } = {}) {
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await registerFixtures(page);
  await page.evaluate((c) => Object.assign(window.__fx, c), cfg);
  await page.fill('[data-cfw-input]', address);
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  await page.check(`[data-cfw-option="${type}"] input`);
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  if (target) { await page.evaluate((t) => window.DeskV1ConnectSummaryStep.setTarget(t), target); await repaint(page); await waitStep(page, 'setup'); }
  await page.click('[data-cfw-primary]'); await waitStep(page, 'permissions');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);

  // ── 1, 2, 4: Sign in, the happy path ──────────────────────────────────
  {
    const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: APPLY_OK });
    check(await page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen) === 'summary-review', 'Sign in on x.com reaches the Review screen of this module', 'the Review screen did not open');
    check((await text(page, '[data-cfw-title]')) === 'Review X', 'title "Review X"', 'title: ' + await text(page, '[data-cfw-title]'));
    check((await text(page, '[data-sum-row="service"] dd')) === 'X' && /Sign in/.test(await text(page, '[data-sum-row="connection"] dd')), 'Review names the service and the connection type', 'service/connection rows wrong');
    check(/ronx/.test(await text(page, '[data-sum-row="account"] dd')) && /new/.test(await text(page, '[data-sum-row="account"] dd')), 'Review names the account (a new one, ronx)', 'account row: ' + await text(page, '[data-sum-row="account"] dd'));
    const login = await text(page, '[data-sum-row="login"] dd');
    check(/x\.ronx/.test(login) && /entered, hidden/.test(login) && /Stored when you save/.test(login), 'the login is named, its password "entered, hidden", stored when you save', 'login row: ' + login);
    check(/x-ronx/.test(await text(page, '[data-sum-row="profile"] dd')) && /new/.test(await text(page, '[data-sum-row="profile"] dd')), 'the browser profile is named and said to be new', 'profile row wrong');
    check((await page.$$('[data-sum-perm-line]')).length === 2, 'the two permissions chosen are listed, one line each', 'permission lines: ' + (await page.$$('[data-sum-perm-line]')).length);
    check(await has(page, '[data-sum-perm-pending]'), 'Review says permissions are applied after the connection is saved, with their own passcode check', 'no pending note');
    const steps = await page.$$eval('[data-sum-steps] li', (l) => l.map((e) => e.textContent));
    check(steps.length === 2 && /connection/i.test(steps[0]) && /passcode/i.test(steps[0]) && /permissions/i.test(steps[1]) && /again/i.test(steps[1]), 'the two separate passcode checks are listed in order', 'steps: ' + steps.join('|'));
    check(!(await secretOnPage(page, PASSWORD)), 'no password anywhere on the page or in storage', 'the password is on the page');
    check(!(await has(page, '[data-sum-problem]')) && !(await page.$eval('[data-cfw-primary]', (b) => b.disabled)), 'Save is on and nothing is blocking it', 'Save is blocked');
    await rules(page, 'Review');
    await shot(page, 'review', width);
    await fits(page, 'Review');
    check(srv.log.every((r) => !/verify|commit|permissions/.test(r.path)), 'Review sent nothing', 'Review sent: ' + srv.log.map((r) => r.path).join(','));

    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check(JSON.stringify(await calls(page)) === JSON.stringify(['login.commit', 'perms.apply:acct-1']), 'Save: the connection commit first, then permissions apply with the account id it returned, one each', 'calls: ' + JSON.stringify(await calls(page)));
    check((await text(page, '[data-cfw-title]')) === 'X saved' && !(await has(page, '[data-sum-banner]')), 'Result: "X saved", no partial banner', 'result title: ' + await text(page, '[data-cfw-title]'));
    check(/Saved\. A new account was created/.test(await text(page, '[data-sum-row="connection"] dd')), 'Connection row: saved, a new account created', 'connection row wrong');
    check(await has(page, '[data-sum-login="stored"]') && /Stored in Secrets now/.test(await text(page, '[data-sum-row="login"] dd')), 'Login row: stored in Secrets now', 'login row wrong');
    check(/not signed in yet/.test(await text(page, '[data-sum-row="profile"] dd')) && await has(page, '[data-sum-signin="not_yet"]'), 'a stored login on a NEW profile is "Signed in: Not yet", not signed in', 'sign-in row wrong');
    check(await has(page, '[data-sum-perm-state="saved"]'), 'Permissions row: Saved', 'permissions row wrong');
    check(await has(page, '[data-sum-verified="no"]') && !(await has(page, '[data-sum-verified="yes"]')), 'Verified row: "Not verified." (saved is not verified)', 'verified row wrong');
    check(await has(page, '[data-sum-fill]'), 'Result offers the existing "Sign in with the saved login"', 'no sign-in action');
    check(!(await has(page, '[data-sum-act="check"]')) && !(await has(page, '[data-sum-act="refresh"]')), 'no Check it now on a browser sign-in: no probe was added', 'a check action is offered');
    check(logOf(srv, /verify|purpose/).length === 0 && !(await secretOnPage(page, PASSWORD)), 'no verify request and no secret on the Result', 'verify or secret on Result: ' + srv.log.map((r) => r.path).join(','));
    await rules(page, 'Result');
    await fits(page, 'Result');
    await page.click('[data-cfw-primary]');
    await page.waitForFunction(() => window.DeskV1ConnectWizard.state().step === 'service', null, { timeout: 3000 });
    check(!(await page.evaluate(() => window.DeskV1ConnectSummaryStep.state().saved)), 'Done empties the wizard and what this module holds', 'state kept after Done');
    check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
    await ctx.close();
  }

  // ── 2: a refused or cancelled connection save never reaches permissions ─
  {
    const { ctx, page, pageErrors } = await newPage(browser, { srv: makeServer(), width, height });
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: { ok: false, error: 'That login name is already stored.', code: 'secret_exists', status: 409 }, permsSummary: SUMMARY_OK, applyOut: APPLY_OK });
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfw-msg="error"]');
    check(/already stored/.test(await text(page, '[data-cfw-msg="error"]')) && /Permissions were not changed/.test(await text(page, '[data-cfw-msg="error"]')), 'a refused connection save shows the reason and "Permissions were not changed"', 'error text: ' + await text(page, '[data-cfw-msg="error"]'));
    check((await page.$eval('[data-cfw]', (e) => e.dataset.cfwStep)) === 'review' && JSON.stringify(await calls(page)) === '["login.commit"]', 'it stays on Review and permissions apply was NOT called', 'calls: ' + JSON.stringify(await calls(page)));
    await page.evaluate(() => { window.__fx.loginOut = { cancelled: true }; });
    await page.click('[data-cfw-primary]');
    await page.waitForFunction(() => /Nothing was sent/.test((document.querySelector('[data-cfw-msg="error"]') || {}).textContent || ''));
    check(JSON.stringify(await calls(page)) === '["login.commit","login.commit"]', 'a cancelled passcode prompt: "Nothing was sent", permissions apply still not called', 'calls: ' + JSON.stringify(await calls(page)));
    await page.evaluate((o) => { window.__fx.loginOut = o; }, SIGNIN_OUT);
    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check(JSON.stringify(await calls(page)) === '["login.commit","login.commit","login.commit","perms.apply:acct-1"]', 'after the retry succeeds, permissions apply runs once, with the saved account', 'calls: ' + JSON.stringify(await calls(page)));
    check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
    await ctx.close();
  }

  // ── 3: connection saved, permissions not ──────────────────────────────
  {
    const { ctx, page, pageErrors } = await newPage(browser, { srv: makeServer(), width, height });
    const failed = { ok: false, pending: false, desk: { status: 'failed', error: 'The policy was refused.' }, site: { status: 'not_attempted' } };
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: failed });
    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check((await text(page, '[data-cfw-title]')) === 'Connection saved; permissions unchanged', 'a failed permission: title "Connection saved; permissions unchanged", not "X saved"', 'title: ' + await text(page, '[data-cfw-title]'));
    check(/The policy was refused/.test(await text(page, '[data-sum-banner="partial"]')) && await has(page, '[data-sum-perm-state="not_applied"]'), 'the banner carries the reason and the Permissions row says "Not applied"', 'banner/row wrong');
    check(!(await has(page, '[data-sum-perm-state="saved"]')) && /Saved\. A new account/.test(await text(page, '[data-sum-row="connection"] dd')), 'the connection row still says saved; the permissions row does not', 'rows blurred');
    await shot(page, 'result_partial', width);
    await fits(page, 'Partial result');
    const label = await page.$eval('[data-sum-act="perms"]', (b) => b.textContent);
    check(label === 'Try permissions again', 'offers "Try permissions again"', 'button: ' + label);
    await page.evaluate((o) => { window.__fx.applyOut = o; }, APPLY_OK);
    await page.click('[data-sum-act="perms"]');
    await page.waitForFunction(() => document.querySelector('[data-cfw-title]').textContent === 'X saved');
    check(JSON.stringify(await calls(page)) === '["login.commit","perms.apply:acct-1","perms.apply:acct-1"]', 'the retry repeated ONLY the permissions step: one more apply, no second connection save', 'calls: ' + JSON.stringify(await calls(page)));
    check(!(await has(page, '[data-sum-banner]')), 'the banner is gone once the permission is saved', 'banner stayed');

    // cancelled prompt
    await page.evaluate(() => window.DeskV1ConnectFlow.reset());
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    const cancelled = { ok: false, pending: false, desk: { status: 'cancelled' }, site: { status: 'not_attempted' } };
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: cancelled });
    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check((await text(page, '[data-cfw-title]')) === 'Connection saved; permissions unchanged' && /passcode was not entered/.test(await text(page, '[data-sum-banner="partial"]')), 'a cancelled permission prompt: "permissions unchanged: the passcode was not entered"', 'cancelled words: ' + await text(page, '[data-cfw-title]'));
    await ctx.close();
  }
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    const deferred = { ok: true, pending: true, desk: { status: 'saved' }, site: { status: 'deferred' } };
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: deferred });
    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check((await text(page, '[data-cfw-title]')) === 'Connection saved; some permissions not applied' && /Finish sign-in/.test(await text(page, '[data-sum-banner="partial"]')), 'a browser-site grant waiting for sign-in is "not applied", never green; the saved Desk part is not denied', 'deferred words: ' + await text(page, '[data-cfw-title]'));
    check(await page.$eval('[data-sum-act="perms"]', (b) => b.textContent) === 'Apply permissions now', 'offers "Apply permissions now"', 'button wrong');
    await ctx.close();
  }

  // ── 4: a stored login is not a signed-in profile ──────────────────────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    const exists = JSON.parse(JSON.stringify(SIGNIN_OUT)); exists.result.profile_state = 'exists'; exists.result.login = null; exists.result.shared_with = ['acct-9'];
    await toReview(page, 'linkedin.com', 'signin', { login: { mode: 'manual', login: '', newLogin: false, username: '', hasPassword: false, profile: 'li-ron', newProfile: false }, loginOut: exists, permsSummary: { kind: 'account', pending: false, lines: ['No permissions are allowed.'] }, applyOut: { ok: true, pending: false, desk: { status: 'unchanged' }, site: { status: 'unchanged' } } },
      { target: { kind: 'member', identity: 'ron', account: { id: 'acct-9' } } });
    check(/None stored/.test(await text(page, '[data-sum-row="login"] dd')), 'Review: a manual sign-in says no login is stored', 'login row: ' + await text(page, '[data-sum-row="login"] dd'));
    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check(await has(page, '[data-sum-signin="not_checked"]') && !(await has(page, '[data-sum-signin="yes"]')), 'an existing profile reads "Not checked": Clayrune cannot tell it is signed in', 'sign-in word wrong');
    check(await has(page, '[data-sum-login="none"]') && /Also used by: acct-9/.test(await text(page, '[data-sum-shared]')), 'no stored login is said so; another account sharing the profile is named', 'login/shared wrong');
    check(await has(page, '[data-sum-perm-state="unchanged"]') && (await text(page, '[data-cfw-title]')) === 'LinkedIn saved', 'nothing chosen: Permissions "Unchanged", not "Saved"', 'permissions word: ' + await text(page, '[data-cfw-title]'));
    await ctx.close();
  }

  // ── 5: API ────────────────────────────────────────────────────────────
  {
    const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toReview(page, 'x.com', 'api', { apiOut: API_OUT, card: CARD, permsSummary: { kind: 'account', pending: false, lines: ['Desk: Read Your posts through the API'], notice: 'Post is allowed, but reading your posts through the API is not. Clayrune cannot confirm a post went out, so it stays Submitted.' }, applyOut: { ok: true, pending: false, desk: { status: 'unchanged' }, site: { status: 'unchanged' } } });
    check(await page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen) === 'summary-review' && /API/.test(await text(page, '[data-sum-row="connection"] dd')), 'API on x.com reaches the same Review screen', 'API review missing');
    check(/entered, hidden/.test(await text(page, '[data-sum-api]')), 'the app fields are shown as "entered, hidden"', 'api facts: ' + await text(page, '[data-sum-api]'));
    const risks = await page.$$eval('[data-sum-risks] li', (l) => l.map((e) => e.textContent));
    check(risks.some((r) => /may cost money/.test(r)) && risks.some((r) => /cannot confirm a post/.test(r)), 'material risks: API cost and the Submitted notice', 'risks: ' + risks.join('|'));
    check(await has(page, '[data-cf-install]') && await page.$eval('[data-cfw-primary]', (b) => b.disabled) && /Approve the install/.test(await text(page, '[data-sum-problem]')), 'the install card is shown and Save is off until it is approved, with the reason', 'install gate missing');
    await page.check('[data-cf-install-approve]');
    check(!(await page.$eval('[data-cfw-primary]', (b) => b.disabled)), 'approving the card turns Save on', 'Save stayed off');
    await page.click('[data-cfw-back]'); await waitStep(page, 'permissions');
    await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
    check(!(await page.$eval('[data-cf-install-approve]', (c) => c.checked)) && await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'Back drops the approval: the card must be read and approved again', 'the approval survived Back');
    await page.check('[data-cf-install-approve]');
    await rules(page, 'API Review');
    await page.click('[data-cfw-primary]');
    await waitStep(page, 'result');
    check(JSON.stringify(await calls(page)) === '["api.commit","perms.apply:acct-2"]', 'API Save: api.commit then permissions apply with account acct-2', 'calls: ' + JSON.stringify(await calls(page)));
    check(/Key stored/.test(await text(page, '[data-sum-status]')) && !(await has(page, '[data-sum-verified="yes"]')), 'Status is the server word ("Key stored"); not verified', 'status wrong');
    check(/x\.client-id/.test(await text(page, '[data-sum-row="stored"] dd')), 'Stored in Secrets names the secret, not a value', 'stored row wrong');
    const acts = await page.$$eval('[data-sum-act]', (b) => b.map((x) => x.dataset.sumAct));
    check(JSON.stringify(acts) === '["refresh","check"]', 'actions: Refresh status and Check it now only (the existing read-only calls)', 'actions: ' + acts.join(','));
    check(logOf(srv, /verify/).length === 0, 'nothing was verified by saving', 'verify sent on save');

    srv.verify = () => ({ state: 'check_failed', label: 'Check failed', message: 'X rejected the key: 401.' });
    await page.click('[data-sum-act="check"]');
    await page.waitForSelector('[data-sum-verified="failed"]');
    const v1 = logOf(srv, /verify/);
    check(v1.length === 1 && v1[0].body.check === true && v1[0].body.account_id === 'acct-2' && v1[0].body.service === 'x' && v1[0].body.method === 'oauth', 'Check it now: one POST verify, check:true, for this account', 'verify body: ' + JSON.stringify(v1.map((r) => r.body)));
    check(/No: the check failed/.test(await text(page, '[data-sum-verified="failed"]')) && /401/.test(await text(page, '[data-sum-row="verified"] dd')) && !(await has(page, '[data-sum-verified="yes"]')), 'a failed check reads as FAILED with the reason, never as verified', 'failed check words wrong');
    await shot(page, 'result_api', width);
    await fits(page, 'API Result');
    srv.verify = () => ({ state: 'verified', label: 'Verified', capability: 'Read your posts', identity: 'ronx', at: '2026-10-06T10:00:00Z' });
    await page.click('[data-sum-act="refresh"]');
    await page.waitForSelector('[data-sum-verified="yes"]');
    const v2 = logOf(srv, /verify/);
    check(v2.length === 2 && v2[1].body.check === false, 'Refresh status: one POST verify with check:false', 'refresh body: ' + JSON.stringify(v2.map((r) => r.body)));
    check(/ronx/.test(await text(page, '[data-sum-verified="yes"]').then(() => text(page, '[data-sum-row="verified"] dd'))), 'only the passed check reads "Verified: Yes", with who and when', 'verified words wrong');
    check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
    await ctx.close();
  }
  {
    // an API sign-in that is still pending is "Sign-in required", never connected
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    const out = JSON.parse(JSON.stringify(API_OUT)); out.result.signin = { flow_id: 'f1', auth_url: 'https://x.example/authorize', profile: 'x-oauth-pane' }; out.result.status = { state: 'saved', label: 'Saved' };
    await toReview(page, 'x.com', 'api', { apiOut: out, permsSummary: { kind: 'account', pending: false, lines: ['No permissions are allowed.'] }, applyOut: { ok: true, pending: false, desk: { status: 'unchanged' }, site: { status: 'unchanged' } } });
    await page.click('[data-cfw-primary]'); await waitStep(page, 'result');
    check(await has(page, '[data-sum-status="sign_in_required"]') && (await page.$$('[data-sum-act="signin"]')).length === 1, 'a sign-in still to do reads "Sign-in required" and offers to open it', 'sign-in state wrong');
    await page.click('[data-sum-act="signin"]');
    const panes = await page.evaluate(() => window.__fx.panes);
    check(panes.length === 1 && panes[0][0] === 'https://x.example/authorize' && panes[0][3] === 'x-oauth-pane', 'it opens the provider page in the named profile', 'panes: ' + JSON.stringify(panes));
    await ctx.close();
  }

  // ── 6: what turns Save off ────────────────────────────────────────────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: APPLY_OK }, { target: null });
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled) && /Choose the account first/.test(await text(page, '[data-sum-problem]')) && await has(page, '[data-sum-noaccount]'), 'no account chosen: Save is off and says why', 'no-target gate missing');
    await page.evaluate((t) => window.DeskV1ConnectSummaryStep.setTarget(t), X_NEW); await repaint(page); await waitStep(page, 'review');
    await page.evaluate(() => { window.__fx.permsSummary = null; }); await repaint(page); await waitStep(page, 'review');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled) && /Choose the permissions first/.test(await text(page, '[data-sum-problem]')), 'permissions never chosen: Save is off ("Allowing nothing is a choice")', 'no-permissions gate missing');
    await page.evaluate((s) => { window.__fx.permsSummary = s; window.__fx.login = null; }, SUMMARY_OK); await repaint(page); await waitStep(page, 'review');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled) && /how to sign in/.test(await text(page, '[data-sum-problem]')), 'no sign-in chosen: Save is off', 'no-login gate missing');
    check(JSON.stringify(await calls(page)) === '[]', 'none of these called a step', 'a step was called: ' + JSON.stringify(await calls(page)));
    await ctx.close();
  }

  // ── 8: leaving ────────────────────────────────────────────────────────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: { ok: false, pending: false, desk: { status: 'failed', error: 'no' }, site: { status: 'not_attempted' } } });
    await page.click('[data-cfw-primary]'); await waitStep(page, 'result');
    check(await page.evaluate(() => window.DeskV1ConnectSummaryStep.state().saved), 'the Result holds the saved connection', 'nothing held');
    await page.evaluate(() => window.DeskV1ConnectFlow.reset());
    const st = await page.evaluate(() => window.DeskV1ConnectSummaryStep.state());
    check(!st.saved && st.perms === null && st.outcome === null, 'Close empties what this module holds', 'state after close: ' + JSON.stringify(st));
    await ctx.close();
  }
}

// ── 7: the source ───────────────────────────────────────────────────────
function sourceChecks() {
  console.log('\n== source ==');
  const code = SUMMARY_SRC.split('\n').filter((l) => !/^\s*\/\//.test(l)).map((l) => l.replace(/\/\/ .*$/, '')).join('\n');
  check(!/humanProofFetch|passcode\s*[:=(]|\.passcode/i.test(code), 'the module has no passcode or humanProofFetch code: every prompt lives in the step that owns the request', 'passcode code in the module');
  check(!/localStorage|sessionStorage|document\.cookie/.test(code), 'it never touches storage', 'storage used');
  const reqs = (code.match(/\bapi\.ctx\.api\(|fetch\(/g) || []).length;
  check(reqs === 1 && /\/api\/desk\/connect\/verify/.test(code) && !/\/api\/desk\/connect\/(commit|permissions|browser-setup)/.test(code), 'its one request of its own is the existing read-only verify; every write is a step\'s own commit', 'requests: ' + reqs);
  check(!/import\s|export\s/.test(code), 'no import or export (ground rule 1)', 'import/export found');
}

(async () => {
  const browser = await chromium.launch();
  try {
    await run(browser, 1440, 900);
    await run(browser, 390, 844);
    {
      const { ctx, page } = await newPage(browser, { srv: makeServer(), width: 1440, height: 900 });
      await toReview(page, 'x.com', 'signin', { login: SIGNIN_LOGIN, loginOut: SIGNIN_OUT, permsSummary: SUMMARY_OK, applyOut: APPLY_OK });
      await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
      await fits(page, '200% text Review');
      await ctx.close();
    }
    sourceChecks();
  } finally { await browser.close(); }
  console.log(bad ? `\n${bad} check(s) FAILED` : '\nAll checks passed');
  process.exit(bad ? 1 : 0);
})();
