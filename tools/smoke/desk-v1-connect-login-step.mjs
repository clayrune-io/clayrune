#!/usr/bin/env node
/**
 * Desk v1 — the Connect wizard's browser SIGN-IN Setup screen (MC-1062 ticket 04,
 * docs/desk_v1/connect_flow_tickets/04-signin-screen.md; static/js/desk-v1-connect-login-step.js), the
 * browser half, against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The Service/Connection
 * screens are drawn from the REAL type projection (mc.desk_connect.type_view), computed once by Python.
 * The server halves are pinned elsewhere (tests/test_desk_connect_browser_setup.py, test_desk_signin_fill.py).
 *
 *   1. Binding      renders for Sign in on x.com and linkedin.com by route id (x-browser / linkedin-browser); with no
 *                   account handed over it says so and offers no way on.
 *   2. Three ways   Saved login / Username and password / In browser: one form at a time, 3 alternatives, the
 *                   design's titles and copy, one Details, nothing nested.
 *   3. Saved        metadata-only pickers (names); a NEW profile is the default, an existing one is chosen
 *                   explicitly; Sign in = the passcode-gated fill: nothing sent before the passcode, a login NAME
 *                   never a value, one request per click, no retry of its own (wrong password / CAPTCHA handoff).
 *   4. New          the typed login stays in its inputs: no markup, no storage, no request until the Review Save;
 *                   Back keeps it; a type change and Close empty it; Continue writes nothing.
 *   5. Manual       profile + Open sign-in (the pane is opened with that profile) and no login in the draft.
 *   6. Locked vault the saved and typed ways are stopped with a way to check again; In browser is not.
 *   7. The Save     `commit()` posts ticket 03's browser-setup commit through the passcode prompt: cancel sends
 *                   nothing and keeps the typed login; a refusal (profile already used) keeps it and the request
 *                   id; success empties it. The draft carries service/revision/route/account/kind/profile/login.
 *                   Fresh X with no app, LinkedIn member and Company Page.
 *   8. Fit          no horizontal scroll and the primary action reachable at 1440, 390 and 200% text.
 *
 * Screenshots: _scratch/connect_login_step/*.png (not committed).
 *
 * RUN   cd tools/smoke && node desk-v1-connect-login-step.mjs
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
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'connect_login_step');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = loadStaticJsCss(REPO_ROOT, { isolateConnectScreens: true });

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
out = {}
for raw in ['x.com', 'linkedin.com']:
    got = resolve.resolve(raw, own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    out[raw] = {**got, **type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const SECRET = 'hunter2-login-step-secret-77aa';
const PASSCODE = 'right-passcode';

function makeServer() {
  const fx = loadFixtures();
  const srv = {
    log: [], fx, locked: false, commitMode: 'ok', fillState: 'submitted',
    logins: [{ name: 'x.ron', matches: true }, { name: 'other.login', matches: false }],
    profiles: [{ name: 'x-main' }, { name: 'x-other' }],
  };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
    if (path === '/static/js/desk-v1-connection-status.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its status words and MCP tiles would add a page-load read to a smoke that counts requests
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    if (path === '/static/js/desk-v1-connect-summary-step.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its Review and Result would shadow this smoke's fixture screens
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
    if (path === '/api/secrets' && method === 'GET') {srv.log.push({method,path,body});return J({secrets:srv.logins.map(s=>({...s,username:'fixture@example.test',entry_type:'login',scope:'global'}))});}
    if (path.startsWith('/api/secrets') || path === '/api/browser/launch') { srv.log.push({ method, path, body }); return J({ error: 'not expected' }, 500); }
    if (path === '/api/browser/profiles') { srv.log.push({ method, path, body }); return J({ profiles: srv.profiles }); }
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
    if (path === '/api/desk/connect/signin/options') return J({ service: body.service, routes: [], logins: srv.logins, vault_locked: srv.locked });
    if (path === '/api/desk/connect/signin/fill') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      return J({ ok: true, state: srv.fillState, message: srv.fillState === 'handed_to_you' ? 'The page asks for a code or a CAPTCHA. The pane is yours: finish it there.' : 'The login was typed and submitted. Check the pane for the result.' });
    }
    if (path === '/api/desk/connect/browser-setup/commit') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.commitMode === 'in_use') return J({ error: 'the browser profile "x-main" already belongs to another account. Each x account needs its own: choose another.', code: 'profile_in_use' }, 409);
      const d = body.draft;
      return J({ ok: true, duplicate: false, account_id: 'acct-new', account_created: !!(d.account && d.account.new), unchanged: false, service: d.service, route_id: d.route_id,
        account_kind: d.account_kind, browser_profile: d.browser_profile, profile_state: 'new', login: (d.login || (d.new_login && d.new_login.name)) || null, login_created: !!d.new_login, shared_with: [] }, 201);
    }
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
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `${name}_${w}.png`) });
async function waitStep(page, step) { await page.waitForSelector(`[data-cfw][data-cfw-step="${step}"]`, { timeout: 4000 }); }
const logOf = (srv, path) => srv.log.filter((r) => r.path === path);
const repaint = (page) => page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));

async function rules(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('[data-cfw]');
    const words = Array.from(root.querySelectorAll('[data-cfw-copy]')).map((e) => e.textContent.trim().split(/\s+/).filter(Boolean).length).reduce((a, b) => a + b, 0);
    const alts = root.querySelectorAll('[data-cfw-option], [data-cfw-alt]').length;
    return { words, alts, details: root.querySelectorAll('details').length, nested: root.querySelectorAll('details details, form form').length,
      forms: root.querySelectorAll('form').length, primaries: root.querySelectorAll('[data-cfw-primary]').length };
  });
  check(m.words <= 25, `${label}: ${m.words} explanatory words (<= 25)`, `${label}: ${m.words} explanatory words`);
  check(m.alts <= 4, `${label}: ${m.alts} alternatives (<= 4)`, `${label}: ${m.alts} alternatives`);
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

// Fixture screens for the steps whose real screens are later tickets (Permissions 08, Review 13a, Result): registered
// the way those tickets will. The Review Save is the one place `commit()` is called.
async function registerFixtures(page) {
  await page.evaluate(() => {
    const W = window.DeskV1ConnectWizard;
    window.__fx = { api: null, saves: [], panes: [] };
    window.openBrowserPane = (...a) => { window.__fx.panes.push(a); };
    W.registerScreen({
      id: 'fx-permissions', step: 'permissions', title: () => 'Fixture permissions', copy: () => 'Nothing is granted by getting here.',
      body: () => '<p>Read</p>', bind: (root, api) => { window.__fx.api = api; }, primary: () => ({ label: 'Continue' }),
    });
    W.registerScreen({
      id: 'fx-review', step: 'review', title: () => 'Fixture review', copy: () => 'Review what would be saved.',
      body: () => `<p data-fx-facts></p><div data-fx-saveerr></div>`,
      primary: () => ({ label: 'Save', run: async () => {
        const r = await window.DeskV1ConnectLoginStep.commit();
        window.__fx.saves.push(r);
        const box = document.querySelector('[data-fx-saveerr]');
        if (box) box.textContent = r.ok ? '' : (r.cancelled ? 'cancelled' : r.error);
        return !!r.ok;
      } }),
    });
    W.registerScreen({ id: 'fx-result', step: 'result', title: () => 'Fixture result', copy: () => 'Saved.', body: () => '<p>Done</p>' });
  });
}

// Open the wizard on an address, pick Sign in, land on the Setup screen. `target` is what the (unbuilt) account screen
// would hand over with setTarget; null leaves it unset.
async function toSetup(page, address, target) {
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await registerFixtures(page);
  await page.fill('[data-cfw-input]', address);
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  await page.check('[data-cfw-option="signin"] input');
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  if (target) { await page.evaluate((t) => window.DeskV1ConnectLoginStep.setTarget(t), target); await repaint(page); await page.waitForSelector('[data-cfw-screen="login-step"]'); }
}
const X_NEW = { kind: 'account', identity: 'ronx', account: { new: { identity: 'ronx', label: '' } } };
const mode = async (page, m) => { await page.check(`[data-cfw-option="${m}"] input`); await page.waitForSelector(`[data-cfw-option="${m}"] input:checked`); };
const prompt = (page, code) => page.fill('input[id^="hp-passcode-"]', code).then(() => page.click('.modal-content .btn-add'));
const stepSecret = (page) => page.evaluate((s) => document.body.innerHTML.includes(s) || JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(s), SECRET);

async function save(page) {
  // Continue through Permissions to Review, then press the one Save.
  await page.click('[data-cfw-primary]'); await waitStep(page, 'permissions');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
  await page.click('[data-cfw-primary]');
}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);
  // ── 1-2, 4, 7: fresh X with no developer app, typed login ──────────────
  {
    const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', null);
    check(await page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen) === 'login-step', 'Sign in on x.com (route x-browser) opens the login screen', 'the login screen did not open');
    check(!!(await page.$('[data-lg-notarget]')) && await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'with no account handed over it says so and Continue is off', 'no-account state wrong');
    await page.waitForTimeout(150);
    check(logOf(srv, '/api/desk/connect/signin/options').length === 0 && logOf(srv, '/api/browser/profiles').length === 0, 'nothing is loaded before there is an account', 'metadata loaded without an account');
    await page.evaluate((t) => window.DeskV1ConnectLoginStep.setTarget(t), X_NEW);
    await repaint(page);
    await page.waitForSelector('[data-lg-mode]');
    check((await page.textContent('[data-cfw-title]')).trim() === 'Sign in to X' && /^Use a saved login, enter a new one, or sign in yourself\.$/.test((await page.textContent('[data-cfw-copy]')).trim()), 'title "Sign in to X" and the design\'s one line of copy', 'title/copy wrong');
    const opts = await page.$$eval('[data-cfw-option]', (els) => els.map((e) => e.textContent.trim()));
    check(opts.join('|') === 'Saved login|Username and password|In browser', `three alternatives (${opts.join(' | ')})`, 'alternatives: ' + opts.join('|'));
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled) && (await page.$$('[data-lg-pick], [data-lg-user], [data-lg-profile]')).length === 0, 'nothing is chosen, so no form shows and Continue is off', 'a form showed before a choice');
    await rules(page, 'Sign in choice');
    await shot(page, 'choice', width);
    await fits(page, 'Sign in choice');

    await mode(page, 'new');
    check((await page.textContent('[data-cfw-title]')).trim() === 'Your X login' && /Stored when you save/.test(await page.textContent('[data-cfw-copy]')), 'Username and password: "Your X login", "Stored when you save…"', 'new-login title/copy wrong');
    check((await page.$$('[data-lg-user]')).length === 1 && (await page.$$('[data-lg-pass]')).length === 1 && await page.$eval('[data-lg-pass]', (i) => i.type === 'password'), 'username and a masked password field', 'fields wrong');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'Continue is off until both are typed', 'Continue on with empty fields');
    await page.fill('[data-lg-user]', 'ron@example.com');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'a username alone is not enough', 'Continue on with no password');
    await page.fill('[data-lg-pass]', SECRET);
    check(!(await page.$eval('[data-cfw-primary]', (b) => b.disabled)), 'username and password turn Continue on', 'Continue stayed off');
    await page.click('[data-cfw-details] > summary');
    check((await page.inputValue('[data-lg-vaultname]')) === 'x.ronx' && (await page.inputValue('[data-lg-newprofile]')) === 'x-ronx' && (await page.$$('[data-lg-label]')).length === 1,
      'Details holds the suggested vault name (x.ronx), the new profile (x-ronx) and the account label', 'details content wrong');
    check(!(await stepSecret(page)), 'the typed password is in no markup and no storage', 'the password reached markup or storage');
    await rules(page, 'Username and password');
    await shot(page, 'new', width);
    await fits(page, 'Username and password');
    check(srv.log.every((r) => ['/api/desk/connect/types', '/api/desk/connect/signin/options', '/api/browser/profiles','/api/secrets'].includes(r.path) || !/connect|secrets/.test(r.path) || r.path === '/api/desk/connect/suggest' || r.path === '/api/desk/workspace'),
      'nothing but read-only calls so far: no vault write, no fill, no commit', 'a write went out: ' + JSON.stringify(srv.log.map((r) => r.path)));

    // Back keeps the typed login; the node is the same.
    await page.click('[data-cfw-back]'); await waitStep(page, 'connection');
    await page.click('[data-cfw-primary]'); await waitStep(page, 'setup');
    check((await page.inputValue('[data-lg-pass]')) === SECRET && (await page.inputValue('[data-lg-user]')) === 'ron@example.com' && await page.$eval('[data-lg-mode][value="new"]', (r) => r.checked), 'Back and forward keep the typed login and the choice', 'Back lost the typed login');
    check(!(await stepSecret(page)), 'still in no markup and no storage after Back', 'secret leaked after Back');

    // The Save: cancel, refusal, success.
    await save(page);
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
    check(logOf(srv, '/api/desk/connect/browser-setup/commit').length === 0, 'Save opens the passcode prompt and sends nothing yet', 'a commit went out before the passcode');
    await page.click('.modal-content .btn-secondary');
    await page.waitForFunction(() => window.__fx.saves.length === 1, null, { timeout: 4000 });
    check(await page.evaluate(() => window.__fx.saves[0].cancelled === true) && logOf(srv, '/api/desk/connect/browser-setup/commit').length === 0, 'cancelling the prompt sends nothing', 'cancel sent a request');
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
    srv.commitMode = 'in_use';
    await prompt(page, PASSCODE);
    await page.waitForFunction(() => window.__fx.saves.length === 2, null, { timeout: 4000 });
    const refused = await page.evaluate(() => window.__fx.saves[1]);
    check(refused.ok === false && refused.code === 'profile_in_use' && /already belongs to another account/.test(refused.error), 'a refusal (profile already used by another account) is handed back with the server\'s words and code', JSON.stringify(refused));
    const firstId = logOf(srv, '/api/desk/connect/browser-setup/commit')[0].body.request_id;
    await page.click('[data-cfw-back]'); await waitStep(page, 'permissions'); await page.click('[data-cfw-back]'); await waitStep(page, 'setup');
    check((await page.inputValue('[data-lg-pass]')) === SECRET, 'after a refusal the typed login is still in its input', 'the typed login was lost on a refusal');
    srv.commitMode = 'ok';
    await save(page);
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
    await prompt(page, PASSCODE);
    await waitStep(page, 'result');
    const commits = logOf(srv, '/api/desk/connect/browser-setup/commit');
    const sent = commits[commits.length - 1].body;
    check(commits.length === 2 && sent.request_id === firstId, 'the retry after a refusal reuses the request id (a replay, never a second write)', `ids ${commits.map((c) => c.body.request_id).join(' / ')}`);
    check(sent.draft.service === 'x' && sent.draft.route_id === 'x-browser' && sent.draft.account_kind === 'account' && sent.draft.revision === TYPES['x.com'].service.revision
      && JSON.stringify(sent.draft.account) === JSON.stringify({ new: { identity: 'ronx', label: '' } }) && sent.draft.browser_profile === 'x-ronx' && sent.draft.login === undefined
      && JSON.stringify(sent.draft.new_login) === JSON.stringify({ name: 'x.ronx', username: 'ron@example.com', value: SECRET }),
      'the draft: x / x-browser / account / revision / new account ronx / profile x-ronx / typed login (name, username, value)', 'draft: ' + JSON.stringify({ ...sent.draft, new_login: sent.draft.new_login && { ...sent.draft.new_login, value: '…' } }));
    check(!(await stepSecret(page)) && await page.evaluate(() => Array.from(document.querySelectorAll('input')).every((i) => i.value === '' || i.type === 'radio')), 'after the save no password is left on the page', 'a password was left after the save');
    check(await page.evaluate(() => window.__fx.saves[2].ok === true && window.__fx.saves[2].result.account_id === 'acct-new'), 'the saved result (account id, profile state) is handed to the Result screen', 'no result');
    check(logOf(srv, '/api/desk/connect/signin/fill').length === 0 && srv.log.every((r) => !(r.path.startsWith('/api/secrets') && r.method!=='GET') && r.path!=='/api/browser/launch'), 'no fill, vault write or browser launch was ever called by the screen', 'an unexpected write was called');
    check(realErrors(pageErrors).length === 0, 'no page error', realErrors(pageErrors).join(' | '));
    await ctx.close();
  }

  // ── typed login is cleared by a type change, an account change and Close ─
  for (const how of ['type', 'account', 'close']) {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', X_NEW);
    await mode(page, 'new');
    await page.fill('[data-lg-user]', 'ron@example.com'); await page.fill('[data-lg-pass]', SECRET);
    await page.evaluate(() => { window.__pass = document.querySelector('[data-lg-pass]'); window.__user = document.querySelector('[data-lg-user]'); });
    if (how === 'type') { await page.click('[data-cfw-back]'); await waitStep(page, 'connection'); await page.check('[data-cfw-option="api"] input'); }
    if (how === 'account') {
      await page.click('[data-cfw-primary]'); await waitStep(page, 'permissions');
      await page.evaluate(() => window.__fx.api.select({ account: 'someone-else' }));
    }
    if (how === 'close') await page.evaluate(() => window.DeskV1ConnectWizard.close());
    const cleared = await page.evaluate(() => ({ pass: window.__pass.value, user: window.__user.value, hosts: window.DeskV1ConnectWizard.state().hosts.length }));
    check(cleared.pass === '' && cleared.user === '' && cleared.hosts === 0, `a ${how} change empties the typed login and drops the kept node`, `${how}: ${JSON.stringify(cleared)}`);
    await ctx.close();
  }

  // ── 3, 6: saved login, existing profile, Sign in, locked vault ─────────
  {
    const srv = makeServer();
    const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
    await toSetup(page, 'x.com', { kind: 'account', identity: 'ron', account: { id: 'acct-1' } });
    await mode(page, 'saved');
    await page.waitForSelector('[data-lg-pick]');
    check((await page.textContent('[data-cfw-title]')).trim() === 'Saved X login' && /Your password stays in the Vault/.test(await page.textContent('[data-cfw-copy]')), 'Saved login: "Saved X login", "Your password stays in the Vault…"', 'saved title/copy wrong');
    const names = await page.$$eval('[data-lg-pick] option', (os) => os.map((o) => o.value));
    check(names.join('|') === '|x.ron|other.login', `the picker lists login NAMES only (${names.join(' | ')})`, 'picker: ' + names.join('|'));
    check(await page.$eval('[data-lg-profile]', (s) => s.value) === '__new__' && (await page.$$eval('[data-lg-profile] option', (os) => os.map((o) => o.value))).join('|') === '__new__|x-main|x-other', 'profiles are listed by name and the default is a NEW profile, never an existing one', 'profile default wrong');
    check(await page.$eval('[data-lg-newprofile]', (i) => i.value) === 'x-ron', 'the new profile name is suggested from the account (x-ron)', 'new profile suggestion wrong');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled) && await page.$eval('[data-cs-fillbtn]', (b) => b.disabled), 'Continue and Sign in are off until a login is chosen', 'buttons on with no login');
    await page.selectOption('[data-lg-pick]', 'other.login');
    check(!!(await page.$('[data-lg-notmine]')) && await page.$eval('[data-cs-fillbtn]', (b) => b.disabled) && !(await page.$eval('[data-cfw-primary]', (b) => b.disabled)), 'a login not named for X cannot be typed from here (Sign in off, says why) but can still be saved', 'not-mine handling wrong');
    await page.selectOption('[data-lg-pick]', 'x.ron');
    await page.selectOption('[data-lg-profile]', 'x-main');
    check(!!(await page.$('[data-lg-existing-profile]')) && (await page.$$('[data-lg-newprofile]')).length === 0, 'choosing an existing profile is explicit and says it keeps its sign-in', 'existing-profile note missing');
    await rules(page, 'Saved login');
    await shot(page, 'saved', width);
    await fits(page, 'Saved login');
    // Sign in: passcode-gated, one request per click, no retry of its own.
    await page.click('[data-cs-fillbtn]');
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
    check(logOf(srv, '/api/desk/connect/signin/fill').length === 0, 'Sign in opens the passcode prompt and sends nothing yet', 'a fill went out before the passcode');
    srv.fillState = 'handed_to_you';
    await prompt(page, PASSCODE);
    await page.waitForSelector('[data-cs-fillmsg]', { timeout: 4000 });
    const fill = logOf(srv, '/api/desk/connect/signin/fill')[0].body;
    check(fill.service === 'x' && fill.route_id === 'x-browser' && fill.login === 'x.ron' && fill.profile === 'x-main' && fill.passcode === PASSCODE && !('value' in fill) && !('password' in fill) && !('account_id' in fill),
      'the fill names the service, the route, a login NAME and the profile: no value, no password', JSON.stringify(fill));
    check(/CAPTCHA/.test(await page.textContent('[data-cs-fillmsg]')), 'a CAPTCHA/2FA handoff is shown as the server worded it (the pane is the person\'s)', 'handoff message missing');
    await page.waitForTimeout(700);
    check(logOf(srv, '/api/desk/connect/signin/fill').length === 1, 'exactly one fill request: nothing retries on its own', `fills: ${logOf(srv, '/api/desk/connect/signin/fill').length}`);
    // A wrong password is the person's to retry: the next fill only happens on the next click and passcode.
    srv.fillState = 'submitted';
    await page.click('[data-cs-fillbtn]'); await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 }); await prompt(page, PASSCODE);
    await page.waitForFunction(() => /typed and submitted/.test((document.querySelector('[data-cs-fillmsg]') || {}).textContent || ''), null, { timeout: 4000 });
    await page.waitForTimeout(700);
    check(logOf(srv, '/api/desk/connect/signin/fill').length === 2, 'a second fill needs a second click and passcode; none is sent after a wrong password', `fills: ${logOf(srv, '/api/desk/connect/signin/fill').length}`);
    // Open sign-in opens the pane at the route's own address with the chosen profile.
    await page.click('[data-cs-open]');
    check(await page.evaluate(() => window.__fx.panes.length === 1 && window.__fx.panes[0][0] === 'https://x.com/i/flow/login' && window.__fx.panes[0][3] === 'x-main'), 'Open sign-in opens the declared X sign-in page in the chosen profile', 'pane args: ' + JSON.stringify(await page.evaluate(() => window.__fx.panes)));
    // The Save: the draft names the stored login and the existing profile; no new_login.
    await save(page);
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 }); await prompt(page, PASSCODE);
    await waitStep(page, 'result');
    const d = logOf(srv, '/api/desk/connect/browser-setup/commit')[0].body.draft;
    check(d.login === 'x.ron' && d.new_login === undefined && d.browser_profile === 'x-main' && JSON.stringify(d.account) === JSON.stringify({ id: 'acct-1' }), 'the draft names the stored login and the existing profile on the existing account', JSON.stringify(d));
    check(realErrors(pageErrors).length === 0, 'no page error', realErrors(pageErrors).join(' | '));
    await ctx.close();
  }
  {
    const srv = makeServer(); srv.locked = true;
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await toSetup(page, 'x.com', X_NEW);
    await mode(page, 'saved');
    await page.waitForSelector('[data-lg-locked]');
    check(await page.$eval('[data-lg-pick]', (s) => s.disabled) && await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'locked vault: the picker and Continue are off and it says so', 'locked state wrong');
    await mode(page, 'new');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled) && !!(await page.$('[data-lg-locked]')), 'locked vault also stops the typed login (it could not be stored)', 'typed login not stopped while locked');
    await mode(page, 'manual');
    await page.fill('[data-lg-newprofile]', 'x-ronx');
    check(!(await page.$eval('[data-cfw-primary]', (b) => b.disabled)) && !(await page.$('[data-lg-locked]')), 'In browser needs no vault, so a locked vault does not stop it', 'manual was blocked by the lock');
    await mode(page, 'saved');
    srv.locked = false;
    await page.click('[data-lg-recheck]');
    await page.waitForFunction(() => !document.querySelector('[data-lg-locked]'), null, { timeout: 4000 });
    check(logOf(srv, '/api/desk/connect/signin/options').length === 2 && !(await page.$eval('[data-lg-pick]', (s) => s.disabled)), '"check again" asks the server again and the picker comes back when it is unlocked', 'recheck did not reload');
    await ctx.close();
  }

  // ── 5: manual ─────────────────────────────────────────────────────────
  {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await toSetup(page, 'x.com', X_NEW);
    await mode(page, 'manual');
    check((await page.textContent('[data-cfw-title]')).trim() === 'Sign in yourself' && /Sign in in the browser pane/.test(await page.textContent('[data-cfw-copy]')), 'In browser: "Sign in yourself", "Sign in in the browser pane…"', 'manual title/copy wrong');
    check((await page.$$('[data-lg-user], [data-lg-pass], [data-lg-pick]')).length === 0, 'no login fields on this way', 'login fields showed');
    await rules(page, 'In browser');
    await shot(page, 'manual', width);
    await fits(page, 'In browser');
    await page.fill('[data-lg-newprofile]', 'Bad Name!');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'a profile name that is not lowercase letters/digits/-/_ keeps Continue off', 'bad profile name accepted');
    await page.fill('[data-lg-newprofile]', 'x-ronx-pane');
    await page.click('[data-lg-open]');
    check(await page.evaluate(() => window.__fx.panes.length === 1 && window.__fx.panes[0][0] === 'https://x.com/i/flow/login' && window.__fx.panes[0][3] === 'x-ronx-pane'), 'Open sign-in opens the declared page in the new profile name', 'pane args wrong');
    await save(page);
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 }); await prompt(page, PASSCODE);
    await waitStep(page, 'result');
    const d = logOf(srv, '/api/desk/connect/browser-setup/commit')[0].body.draft;
    check(d.login === undefined && d.new_login === undefined && d.browser_profile === 'x-ronx-pane', 'the draft carries the profile and no login at all', JSON.stringify(d));
    await ctx.close();
  }

  // ── 1, 7: LinkedIn, member and Company Page ───────────────────────────
  for (const [kind, identity, extra, vault] of [['member', 'Ron Levy', {}, 'linkedin.ron-levy'], ['organization', 'Acme Corp', { organization_id: '12345' }, 'linkedin.login']]) {
    const srv = makeServer();
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await toSetup(page, 'linkedin.com', { kind, identity, account: { new: { identity, label: '', ...extra } } });
    check(await page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen) === 'login-step' && (await page.textContent('[data-cfw-title]')).trim() === 'Sign in to LinkedIn', `LinkedIn (${kind}): the same screen, bound to route linkedin-browser`, 'LinkedIn login screen did not open');
    await mode(page, 'new');
    await page.fill('[data-lg-user]', 'member@example.com'); await page.fill('[data-lg-pass]', SECRET);
    await page.click('[data-cfw-details] > summary');
    check((await page.inputValue('[data-lg-vaultname]')) === vault, `LinkedIn ${kind}: the suggested vault name is ${vault} (a Page is reached through a member's login, not named for the Page)`, 'vault suggestion: ' + await page.inputValue('[data-lg-vaultname]'));
    await save(page);
    await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 }); await prompt(page, PASSCODE);
    await waitStep(page, 'result');
    const d = logOf(srv, '/api/desk/connect/browser-setup/commit')[0].body.draft;
    check(d.service === 'linkedin' && d.route_id === 'linkedin-browser' && d.account_kind === kind && d.revision === TYPES['linkedin.com'].service.revision
      && JSON.stringify(d.account) === JSON.stringify({ new: { identity, label: '', ...extra } }) && d.new_login.username === 'member@example.com',
      `LinkedIn ${kind}: the draft carries linkedin-browser, kind ${kind} and the ${kind === 'member' ? 'member' : 'Page (with its organization id)'} as its own account`, JSON.stringify({ ...d, new_login: '…' }));
    await ctx.close();
  }

  // ── 8: 200% text ──────────────────────────────────────────────────────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', X_NEW);
    await mode(page, 'new');
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    await page.click('[data-cfw-details] > summary');
    await shot(page, 'new_200pct', width);
    await fits(page, 'Username and password at 200% text');
    await mode(page, 'saved'); await page.waitForSelector('[data-lg-pick]');
    await fits(page, 'Saved login at 200% text');
    await ctx.close();
  }
}

const browser = await chromium.launch();
try {
  await run(browser, 1440, 900);
  await run(browser, 390, 844);
} catch (e) {
  fail('smoke aborted: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nAll connect-login-step checks passed');
process.exit(bad ? 1 : 0);
