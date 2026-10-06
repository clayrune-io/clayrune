#!/usr/bin/env node
/**
 * Desk v1 — the Connect wizard's API / provider SETUP screen (MC-1062 ticket 09,
 * docs/desk_v1/connect_flow_tickets/09-api-screen.md; static/js/desk-v1-connect-api-step.js), the browser half,
 * against a fake server, `desk_v1_live` ON, at 1440 and 390 wide. The Service/Connection screens are drawn from
 * the REAL type projection (mc.desk_connect.type_view) and the provider field specs from the REAL providers
 * (mc.desk_connect.providers), both computed once by Python. The server halves are pinned elsewhere
 * (tests/test_desk_connect*.py, test_desk_oauth_hold.py).
 *
 *   1. Binding      renders for the X developer app and a key engine (OpenAI); LinkedIn has no API type, so it
 *                   never opens; the design's title/copy (<= 25 words), one Details, nothing nested.
 *   2. Fields       X: Client ID and optional Client Secret (masked), callback + instructions under Details; with
 *                   an account handed over the handle/name are not asked again; an entry already in Secrets is
 *                   named and not asked again. Continue writes nothing and names the first empty required field.
 *   3. Authorize    X: Authorize X, "2 of 2". start-held needs the passcode: nothing is sent before it, the body
 *                   carries only the app, one request per click. Held shows expiry; Higgsfield's sign-in (no
 *                   fields) opens straight on it.
 *   4. Changed app  editing the Client ID after signing in discards the held sign-in: the server is told to cancel
 *                   it with its claim, and the person is told.
 *   5. The Save     `commit()` is its own passcode prompt: cancel sends nothing and keeps everything typed; a
 *                   refusal keeps it and the request id; success empties it. The draft carries the held claim,
 *                   the app, the handle from the account choice, and no secret anywhere else.
 *   6. Existing     an account already saved is refused with a reason and no way on (account_attach: the provider
 *      account      side does not exist); nothing is loaded or sent.
 *   7. Leaving      Close empties every typed value and cancels the held sign-in.
 *   8. No probe     no verify, no purpose/verify, no post or read request in any of it.
 *   9. Fit          no horizontal scroll and the primary action reachable at 1440, 390 and 200% text.
 *
 * Screenshots: docs/desk_v1/screens/connect_api_step_{fields,authorize}_{1440,390}.png.
 *
 * RUN   cd tools/smoke && node desk-v1-connect-api-step.mjs
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

const PY = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import providers, registry, resolve, type_view
types = {}
for raw in ['x.com', 'linkedin.com', 'openai', 'higgsfield']:
    got = resolve.resolve(raw, own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    types[raw] = {**got, **type_view.project_service(svc['id'] if svc else None)}
def row(sid, method, present=()):
    p = providers.for_service(sid)
    fields = p.fields(method, set())
    for f in fields:
        if f.get('vault') in present:
            f['present'] = True
    return {'method': method, 'connector': {'service': sid, 'summary': p.summaries[method], 'guide': p.guide(method), 'fields': fields,
                                            'signs_in': method in p.signs_in, 'install': p.install(method)}}
inspect = {'x.com': [row('x', 'oauth')], 'x.com#present': [row('x', 'oauth', present=('x.client-id',))], 'openai': [row('openai', 'api_key')], 'higgsfield': [row('higgsfield', 'oauth')]}
print(json.dumps({'types': types, 'inspect': inspect}))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const TYPES = PY.types;

const SECRET = 'hunter2-client-secret-9z9z';
const CLIENT_ID = 'client-id-abc-123';
const KEY = 'sk-test-key-77aa88bb';
const PASSCODE = 'right-passcode';

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, present: false, commitMode: 'ok', startMode: 'ok', logins: [{ name: 'x.ron', matches: true }] };
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: [], pieces: [] });
  return srv;
}

async function newPage(browser, { srv, width, height }) {
  const ctx = await browser.newContext({ viewport: { width, height } });
  const page = await ctx.newPage();
  const pageErrors = [];
  page.on('pageerror', (e) => pageErrors.push(e.message || String(e)));
  await page.addInitScript(() => { window.__deskGuidePollMs = 40; });
  await page.route('**/*', async (route) => {
    const req = route.request();
    const url = new URL(req.url());
    const path = url.pathname;
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
    if (path === '/api/desk/connect/inspect' && method === 'POST') {
      const input = String((body || {}).input || '').trim();
      const rows = PY.inspect[input === 'x.com' && srv.present ? 'x.com#present' : input];
      const t = TYPES[input];
      return rows && t ? J({ url: t.url, host: t.host, service: { id: t.service.id, label: t.service.label }, options: rows }) : J({ error: 'unknown' }, 400);
    }
    if (path === '/api/desk/connect/signin/options') return J({ service: body.service, routes: [{ route_id: 'x-oauth', connect_method: 'oauth' }], logins: srv.logins, vault_locked: false });
    if (/\/api\/desk\/connect\/[a-z_]+\/start-held$/.test(path)) {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.startMode === 'refuse') return J({ error: 'Add the app details first.', code: 'no_app' }, 400);
      srv.flowN = (srv.flowN || 0) + 1;
      return J({ flow_id: 'flow-' + srv.flowN, claim: 'claim-' + srv.flowN, profile: 'x-oauth-pane', auth_url: 'https://x.example/authorize' });
    }
    if (/\/api\/desk\/connect\/flows\/[^/]+\/cancel$/.test(path)) return J({ ok: true });
    if (/\/api\/desk\/connect\/flows\/[^/]+$/.test(path) && method === 'GET') return J({ status: 'held', held_ttl_s: 600 });
    if (path === '/api/desk/connect/commit') {
      if (!body || body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.commitMode === 'refuse') return J({ error: 'x.client-id is already stored in Secrets.', code: 'secret_exists' }, 409);
      return J({ ok: true, duplicate: false, service: { id: body.draft.url.includes('x.com') ? 'x' : 'svc', label: 'Service' }, method: body.draft.method, account_id: 'acct-1', stored: [], status: { state: 'saved' } }, 201);
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
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_api_step_${name}_${w}.png`) });
async function waitStep(page, step) { await page.waitForSelector(`[data-cfw][data-cfw-step="${step}"]`, { timeout: 4000 }); }
const logOf = (srv, re) => srv.log.filter((r) => re.test(r.path));
const repaint = (page) => page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));

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

// Fixture screens for the steps that are other tickets: Permissions, Review (where the one Save is called) and Result.
async function registerFixtures(page) {
  await page.evaluate(() => {
    const W = window.DeskV1ConnectWizard;
    window.__fx = { api: null, saves: [], panes: [] };
    window.openBrowserPane = (...a) => { window.__fx.panes.push(a); };
    W.registerScreen({ id: 'fx-connection-api', step: 'connection', match: () => false, title: () => '', copy: () => '', body: () => '' });
    W.registerScreen({ id: 'fx-permissions', step: 'permissions', title: () => 'Fixture permissions', copy: () => 'Nothing is granted by getting here.', body: () => '<p>Read</p>', primary: () => ({ label: 'Continue' }) });
    W.registerScreen({
      id: 'fx-review', step: 'review', title: () => 'Fixture review', copy: () => 'Review what would be saved.',
      body: () => `<div data-fx-facts>${window.DeskV1ConnectApiStep.reviewHTML()}</div><div data-fx-saveerr></div>`,
      primary: () => ({ label: 'Save', run: async () => {
        const r = await window.DeskV1ConnectApiStep.commit();
        window.__fx.saves.push(r);
        const box = document.querySelector('[data-fx-saveerr]');
        if (box) box.textContent = r.ok ? '' : (r.cancelled ? 'cancelled' : r.error);
        return !!r.ok;
      } }),
    });
    W.registerScreen({ id: 'fx-result', step: 'result', title: () => 'Fixture result', copy: () => 'Saved.', body: () => '<p>Done</p>' });
  });
}

// Open the wizard on an address, pick API, land on the Setup screen. `target` is what the (unbuilt) account screen would
// hand over with setTarget; null leaves it unset.
async function toSetup(page, address, target, type = 'api', early = false) {
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await registerFixtures(page);
  await page.fill('[data-cfw-input]', address);
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  await page.check(`[data-cfw-option="${type}"] input`);
  if (early && target) await page.evaluate((t) => window.DeskV1ConnectApiStep.setTarget(t), target);   // the account screen runs before Setup
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  if (target && !early) { await page.evaluate((t) => window.DeskV1ConnectApiStep.setTarget(t), target); await repaint(page); }
}
const X_NEW = { kind: 'account', identity: 'ronx', account: { new: { identity: 'ronx', label: 'Ron on X' } } };
const X_EXISTING = { kind: 'account', identity: 'ronx', account: { id: 'acct-saved' } };
const prompt = (page, code) => page.fill('input[id^="hp-passcode-"]', code).then(() => page.click('.modal-content .btn-add'));
const cancelPrompt = (page) => page.click('.modal-content .btn-cancel, .modal-content [data-hp-cancel], .modal-content .btn-secondary');
const secretOnPage = (page, s) => page.evaluate((v) => document.body.innerHTML.includes(v) || JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(v), s);
const dismiss = async (page) => { await page.keyboard.press('Escape'); };

async function toReview(page) {
  await page.click('[data-cfw-primary]'); await waitStep(page, 'permissions');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);
  // ── 1-3, 5: X developer app, new account ──────────────────────────────
  {
    const { ctx, page, pageErrors, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', null);
    check(await page.$eval('[data-cfw]', (e) => e.dataset.cfwScreen) === 'api-step', 'API on x.com (route x-oauth, via provider) opens the API screen', 'the API screen did not open');
    await page.waitForSelector('[data-cfa-host]');
    check((await page.textContent('[data-cfw-title]')).trim() === 'X developer app' && /^Use an app you own\. API use may cost money\.$/.test((await page.textContent('[data-cfw-copy]')).trim()), 'title "X developer app", copy "Use an app you own. API use may cost money."', 'title/copy wrong');
    const labels = await page.$$eval('.desk-v1-conn-add-field, .desk-v1-cf-check', (els) => els.map((e) => e.textContent.trim().replace(/\s+/g, ' ')));
    check(labels.some((l) => /X handle/.test(l)) && labels.some((l) => /X app Client ID/.test(l)) && labels.some((l) => /X app Client Secret \(optional\)/.test(l)),
      'without an account choice: handle, Client ID and optional Client Secret are asked', 'fields without target: ' + labels.join('|'));
    check(await page.$eval('[data-cfa-field="client_secret"]', (i) => i.type === 'password') && await page.$eval('[data-cfa-field="client_id"]', (i) => i.type === 'password'), 'Client ID and Secret are masked inputs', 'secret fields not masked');
    await page.evaluate((t) => window.DeskV1ConnectApiStep.setTarget(t), X_NEW);
    await repaint(page);
    await page.waitForSelector('[data-cfa-field="client_id"]');
    check((await page.$$('[data-cfa-field="identity"], [data-cfa-field="label"]')).length === 0, 'with an account handed over the handle and name are not asked again', 'identity fields still shown');
    await page.click('[data-cfw-details] > summary');
    const det = await page.textContent('[data-cfw-details-body], .desk-v1-cfw-details-body');
    check(/callback address/i.test(det) && /developer portal/i.test(det), 'Details holds the callback address and the app instructions', 'details: ' + det.slice(0, 120));
    await rules(page, 'X fields');
    await shot(page, 'fields', width);
    await fits(page, 'X fields');
    check(logOf(srv, /inspect/).length === 1 && logOf(srv, /start-held|commit|verify/).length === 0, 'one read-only inspect; nothing written, nothing signed in', 'requests: ' + srv.log.map((r) => r.path).join(','));

    await page.click('[data-cfw-primary]');
    check(/X app Client ID is required/.test(await page.textContent('[data-cfw-msg], [data-cfw-msg="error"]')), 'Continue on an empty form names the first empty required field', 'no error for empty Client ID');
    await page.fill('[data-cfa-field="client_id"]', CLIENT_ID);
    await page.fill('[data-cfa-field="client_secret"]', SECRET);
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('[data-cfh]');
    check((await page.textContent('[data-cfw-title]')).trim() === 'Authorize X' && /^Sign in and approve your app in X\.$/.test((await page.textContent('[data-cfw-copy]')).trim()), 'Continue shows "Authorize X" / "Sign in and approve your app in X."', 'authorize title/copy wrong');
    check((await page.$$('[data-cfw-msg]')).length === 0, 'the earlier "is required" message does not follow the person to the next view', 'a stale error is still shown on Authorize');
    check(/2 of 2/.test(await page.textContent('[data-cfw-stepof]')), 'the step counter reads Setup · 2 of 2', 'step counter: ' + await page.textContent('[data-cfw-stepof]'));
    check(logOf(srv, /start-held|commit/).length === 0, 'Continue did not start a sign-in or save anything', 'a write went out on Continue');
    await rules(page, 'X authorize');
    await shot(page, 'authorize', width);
    await fits(page, 'X authorize');

    // start-held: its own passcode, only the app in the body, one request per click.
    await page.click('[data-cfh-start]');
    await page.waitForSelector('input[id^="hp-passcode-"]');
    check(logOf(srv, /start-held/).length === 0, 'Sign in asks for the passcode first: nothing sent before it', 'start-held sent before the passcode');
    await prompt(page, PASSCODE);
    await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
    const start = logOf(srv, /start-held/);
    check(start.length === 1 && start[0].body.hold.client_id === CLIENT_ID && start[0].body.hold.client_secret === SECRET && Object.keys(start[0].body.hold).length === 2, 'start-held: one request, passcode-gated, carrying only the app (Client ID and Secret)', 'start-held body: ' + JSON.stringify(start.map((s) => Object.keys(s.body))));
    check(/Signed in to X/.test(await page.textContent('[data-cfh-state="held"]')) && /minutes/.test(await page.textContent('[data-cfh-state="held"]')), 'held: "Signed in to X ... minutes" (the expiry is shown)', 'held text wrong');

    // Changing the app discards the held sign-in.
    await page.click('[data-api-back-fields]');
    await page.waitForSelector('[data-cfa-field="client_id"]');
    await page.fill('[data-cfa-field="client_id"]', CLIENT_ID + '-changed');
    await page.waitForFunction(() => document.querySelector('[data-cfw]'), null, { timeout: 2000 });
    const cancelled = logOf(srv, /flows\/flow-1\/cancel/);
    check(cancelled.length === 1 && cancelled[0].body.claim === 'claim-1', 'changing the Client ID cancels the held sign-in at the server with its claim', 'cancel after app change: ' + JSON.stringify(cancelled));
    await page.fill('[data-cfa-field="client_id"]', CLIENT_ID);
    await page.click('[data-cfw-primary]'); await page.waitForSelector('[data-cfh]');
    check(/changed the app details/i.test(await page.textContent('[data-cfh]')) && !(await page.$('[data-cfh-state="held"]')), 'and tells the person to sign in again', 'no notice of the discarded sign-in');
    await page.click('[data-cfh-start]'); await prompt(page, PASSCODE);
    await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
    check(logOf(srv, /start-held/).length === 2, 'signing in again is a second, separately gated request', 'start-held count ' + logOf(srv, /start-held/).length);

    // The Save: its own passcode.
    await toReview(page);
    const facts = await page.textContent('[data-fx-facts]');
    check(/ronx/.test(facts) && /Done in the browser/.test(facts) && /entered, hidden/.test(facts) && !facts.includes(SECRET) && !facts.includes(CLIENT_ID), 'Review facts: the account, the sign-in, secrets as "entered, hidden", no value', 'review facts: ' + facts.slice(0, 200));
    await page.click('[data-cfw-primary]');
    await page.waitForSelector('input[id^="hp-passcode-"]');
    check(logOf(srv, /connect\/commit/).length === 0, 'Save asks for the passcode again: start-held\'s was not reused', 'commit sent before its own passcode');
    await dismiss(page);
    await page.waitForFunction(() => window.__fx.saves.length === 1, null, { timeout: 3000 });
    check(await page.evaluate(() => window.__fx.saves[0].cancelled === true) && logOf(srv, /connect\/commit/).length === 0, 'cancelling the passcode sends nothing', 'cancel at the passcode still sent');
    check(await page.$eval('[data-cfa-host] [data-cfa-field="client_id"]', (i) => i.value).catch(() => CLIENT_ID) === CLIENT_ID, 'and keeps what was typed', 'typed values lost');
    srv.commitMode = 'refuse';
    await page.click('[data-cfw-primary]'); await prompt(page, PASSCODE);
    await page.waitForFunction(() => window.__fx.saves.length === 2, null, { timeout: 3000 });
    check(/already stored/.test(await page.textContent('[data-fx-saveerr]')), 'a refusal is shown in the server\'s words', 'refusal not shown');
    srv.commitMode = 'ok';
    await page.click('[data-cfw-primary]'); await prompt(page, PASSCODE);
    await page.waitForFunction(() => window.__fx.saves.length === 3, null, { timeout: 3000 });
    const commits = logOf(srv, /connect\/commit/);
    const d = commits[commits.length - 1].body.draft;
    check(commits.length === 2 && commits[0].body.request_id === commits[1].body.request_id, 'the retry reuses the request id (a replay, not a second write)', 'request ids: ' + commits.map((c) => c.body.request_id));
    check(d.method === 'oauth' && d.fields.client_id === CLIENT_ID && d.fields.client_secret === SECRET && d.fields.identity === 'ronx' && d.fields.label === 'Ron on X' && d.held && d.held.claim === 'claim-2' && d.held.flow_id === 'flow-2',
      'the draft carries the app, the handle and name from the account choice, and the held sign-in\'s claim (never a vendor token)', 'draft: ' + JSON.stringify({ ...d, fields: Object.keys(d.fields) }));
    check(await page.evaluate(() => window.__fx.saves[2].ok === true), 'success resolves ok', 'save did not succeed');
    check(!(await secretOnPage(page, SECRET)) && !(await secretOnPage(page, CLIENT_ID)), 'after the Save no typed value is left in the page, storage or markup', 'a secret is still on the page');
    check(logOf(srv, /verify|purpose|post|publish/).length === 0, 'no verification, purpose, read or post request at any point', 'extra requests: ' + logOf(srv, /verify|purpose|post|publish/).map((r) => r.path));
    check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
    await ctx.close();
  }

  // ── stored credential reuse ───────────────────────────────────────────
  {
    const srv = makeServer(); srv.present = true;
    const { ctx, page } = await newPage(browser, { srv, width, height });
    await toSetup(page, 'x.com', X_NEW);
    await page.waitForSelector('[data-cfa-host]');
    check((await page.$$('[data-cfa-field="client_id"]')).length === 0 && /already stored in Secrets as/.test(await page.textContent('[data-cfa-present="client_id"]')), 'an app already in Secrets is named and not asked again', 'present client id still asked');
    await ctx.close();
  }

  // ── a key engine: no sign-in, one view ────────────────────────────────
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'openai', null);
    await page.waitForSelector('[data-cfa-host]');
    check((await page.textContent('[data-cfw-title]')).trim() === 'OpenAI API key' && /^Use a key from your own account\./.test((await page.textContent('[data-cfw-copy]')).trim()) && !/2 of 2/.test(await page.textContent('[data-cfw-stepof]')), 'OpenAI: "OpenAI API key", one view, no second sign-in view', 'openai title/step: ' + await page.textContent('[data-cfw-title]'));
    await page.fill('[data-cfa-field="secret"]', KEY);
    await toReview(page);
    check(!(await secretOnPage(page, KEY)), 'the key is in no markup or storage on Review', 'key on the page');
    await page.click('[data-cfw-primary]'); await prompt(page, PASSCODE);
    await page.waitForFunction(() => window.__fx.saves.length === 1, null, { timeout: 3000 });
    const d = logOf(srv, /connect\/commit/)[0].body.draft;
    check(d.method === 'api_key' && d.fields.secret === KEY && !d.held && !d.new_login && logOf(srv, /start-held/).length === 0, 'its Save carries the key and no held sign-in or login', 'draft: ' + JSON.stringify({ ...d, fields: Object.keys(d.fields) }));
    await ctx.close();
  }

  // ── Higgsfield's sign-in: no fields, opens on the authorize view ──────
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
    await page.click('[data-conn-add-tile]');
    await page.waitForSelector('[data-cfw-input]');
    await registerFixtures(page);
    // The MCP sign-in variant has no picker yet (the unknown-service tickets), so a stand-in Connection screen chooses it
    // through the frame's own api and goes on to Setup.
    await page.evaluate(() => {
      window.DeskV1ConnectWizard.registerScreen({ id: 'connection', step: 'connection', title: () => 'fx', copy: () => 'fx', body: () => '',
        bind: (root, api) => { api.select({ type: 'mcp', variant: 'higgsfield-oauth' }); api.go('setup'); } });
    });
    await page.fill('[data-cfw-input]', 'higgsfield'); await page.press('[data-cfw-input]', 'Enter');
    await waitStep(page, 'setup');
    await page.waitForSelector('[data-cfh]', { timeout: 4000 });
    check((await page.textContent('[data-cfw-title]')).trim() === 'Authorize Higgsfield' && (await page.$$('[data-cfa-host] input')).length === 0, 'Higgsfield sign-in has no fields, so it opens straight on "Authorize Higgsfield"', 'higgsfield: ' + await page.textContent('[data-cfw-title]'));
    check(!/Back to|Change the app details/.test(await page.textContent('[data-cfw-body]')), 'with no app details there is nothing to go back to', 'back link shown');
    await ctx.close();
  }

  // ── 6: an account already saved is refused ─────────────────────────────
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', X_EXISTING, 'api', true);
    await page.waitForSelector('[data-api-blocked]');
    check(/not available yet/.test(await page.textContent('[data-api-blocked]')) && /second account/.test(await page.textContent('[data-api-blocked]')), 'an existing account is refused and the reason is a second account would be created', 'no refusal');
    check(await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'and there is no way on', 'Continue is on for an existing account');
    await page.waitForTimeout(150);
    check(logOf(srv, /inspect|start-held|commit/).length === 0, 'nothing is loaded or sent for it', 'requests: ' + srv.log.map((r) => r.path));
    const c = await page.evaluate(async () => window.DeskV1ConnectApiStep.commit());
    check(c.ok === false && c.code === 'incomplete', 'commit() refuses too', 'commit result ' + JSON.stringify(c));
    await ctx.close();
  }

  // ── 7: Close empties and cancels ──────────────────────────────────────
  {
    const { ctx, page, srv } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', X_NEW);
    await page.waitForSelector('[data-cfa-host]');
    await page.fill('[data-cfa-field="client_id"]', CLIENT_ID);
    await page.click('[data-cfw-primary]'); await page.waitForSelector('[data-cfh-start]');
    await page.click('[data-cfh-start]'); await prompt(page, PASSCODE);
    await page.waitForSelector('[data-cfh-state="held"]', { timeout: 4000 });
    await page.evaluate(() => window.DeskV1ConnectWizard.close());
    await page.waitForTimeout(150);
    check(logOf(srv, /flows\/flow-1\/cancel/).length === 1, 'Close cancels the held sign-in at the server', 'no cancel on close');
    check(!(await secretOnPage(page, CLIENT_ID)), 'and no typed value stays anywhere', 'client id still on the page');
    const r = await page.evaluate(async () => window.DeskV1ConnectApiStep.commit());
    check(r.ok === false, 'a commit after Close has nothing to send', 'commit after close: ' + JSON.stringify(r));
    await ctx.close();
  }

  // ── 9: 200% text ──────────────────────────────────────────────────────
  {
    const { ctx, page } = await newPage(browser, { srv: makeServer(), width, height });
    await toSetup(page, 'x.com', X_NEW);
    await page.waitForSelector('[data-cfa-host]');
    await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
    await fits(page, 'X fields at 200% text');
    await page.fill('[data-cfa-field="client_id"]', CLIENT_ID);
    await page.click('[data-cfw-primary]'); await page.waitForSelector('[data-cfh-start]');
    await page.click('[data-cfw-details] > summary');
    await fits(page, 'X authorize at 200% text');
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
console.log(bad ? `\n${bad} check(s) failed` : '\nAll connect-api-step checks passed');
process.exit(bad ? 1 : 0);
