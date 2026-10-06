#!/usr/bin/env node
/**
 * Desk v1 — the single-view Connect wizard SHELL (MC-1062 ticket 02,
 * docs/desk_v1/connect_flow_tickets/02-wizard-shell.md), the browser half, against a fake server,
 * `desk_v1_live` ON, at 1440 and 390 wide. The server half (the types projection) is pinned by
 * tests/test_desk_connect_types.py; here the fake server answers /api/desk/connect/types with the REAL
 * projection (mc.desk_connect.type_view), computed once by Python, so the screens are drawn from real data.
 *
 *   1. Activation     off by default: the old flow still opens. setEnabled(true) swaps the panel to the wizard.
 *   2. Service        one screen, one box, 6-step strip (desktop) / "Service . 1 of 6" (phone); suggestions capped at 4;
 *                     an empty or unknown name stays put and says why; Continue asks ONLY the read-only /types.
 *   3. Connection     the picker's types as radio rows (X: Sign in, API, MCP), Continue off until one is picked;
 *                     unavailable methods are NOT rows: they sit under the one Details, paged four to a page,
 *                     with the reference fallback. Placeholders say "not built yet" with Back and no way forward.
 *   4. Frame rules    on every screen: <= 25 words of explanation, <= 1 Details and never nested, <= 4 alternatives,
 *                     one form, one primary action. Fixture screens (registered here, as later tickets will) cover
 *                     Setup / Permissions / Review / Result.
 *   5. State          a secret typed in a kept node survives Back; a change of type empties it and cancels the owned
 *                     operation; an account change keeps type-scoped drafts and drops account-scoped ones; a change of
 *                     service drops everything; leaving Review drops approvals; Close (picking another tile) empties
 *                     the inputs and cancels the owned operation.
 *   6. Writes         nothing but POST /types is ever sent while moving through the steps; no /api/secrets.
 *   7. Fit            no horizontal scroll, the primary action is reachable and not covered, at 1440 and 390, and at
 *                     200% text; keyboard: focus on the box, Enter continues, focus lands on the heading, arrows pick.
 *
 * Screenshots: _scratch/connect_wizard/*.png (not committed).
 *
 * RUN   cd tools/smoke && node desk-v1-connect-wizard.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { execFileSync } from 'node:child_process';
import { chromium } from 'playwright';
import { loadFixtures, workspaceFromFixtures, installDemoFixtures } from './desk-v1-fixture-api.mjs';
import { loadStaticJsCss } from './_static.mjs';

const __dirname = dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = resolve(__dirname, '..', '..');
const INDEX_HTML = readFileSync(resolve(REPO_ROOT, 'static', 'index.html'), 'utf8');
const ORIGIN = 'http://mc.smoke.test';
const SHOT_DIR = resolve(REPO_ROOT, '_scratch', 'connect_wizard');
mkdirSync(SHOT_DIR, { recursive: true });
const STATIC = {};
Object.assign(STATIC, loadStaticJsCss(REPO_ROOT));
const REAL_SCREENS = new Set(['/static/js/desk-v1-connect-login-step.js', '/static/js/desk-v1-connect-api-step.js', '/static/js/desk-v1-connect-unknown-step.js']); // unknown/ref screens would shadow this frame smoke's fixtures

let bad = 0;
const ok = (m) => console.log('  ✓ ' + m);
const fail = (m) => { console.error('  ✗ ' + m); bad++; };
const check = (cond, good, badMsg) => (cond ? ok(good) : fail(badMsg || good));

// What POST /api/desk/connect/types answers for each address, from the real projection. No network, no vault.
const TYPES = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import registry, resolve, type_view
out = {}
for raw in ['x.com', 'linkedin.com', 'plausible.io']:
    got = resolve.resolve(raw, own_hosts=('mc.smoke.test',))
    svc = got.pop('service') or registry.lookup(got['host'])
    out[raw] = {**got, **type_view.project_service(svc['id'] if svc else None)}
print(json.dumps(out))
`], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));

const SECRET = 'hunter2-wizard-secret-31c7';
const SUGGEST_SIX = ['Alpha', 'Alto', 'Amber', 'Arbor', 'Atlas', 'Axis'].map((l) => ({ id: l.toLowerCase(), label: l, host: `${l.toLowerCase()}.example` }));

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, services: [] };
  srv.accounts = fx.channels.filter((c) => c.platform === 'x' || c.platform === 'blog')
    .map((c) => ({ ...JSON.parse(JSON.stringify(c)), publish: { ready: true, reason: null, secret: null, unattended_ok: null } }));
  srv.workspace = () => ({ ...workspaceFromFixtures(fx), projects: fx.projects.map((p) => ({ id: p.id, name: p.name, state: 'active', roster: [], presence: { replies: 'drafts', desk_agent: null, state: 'active' } })), accounts: srv.accounts, pieces: [] });
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
    const method = req.method();
    const J = (body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
    if (path === '/' || path === '/index.html') return route.fulfill({ status: 200, contentType: 'text/html; charset=utf-8', body: INDEX_HTML });
    // The real step screens (tickets 04+) register first and would win the first-match; this smoke drives the frame with its own fixtures.
    if (REAL_SCREENS.has(path)) return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });
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
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J(srv.services);
    if (path === '/api/desk/accounts' && method === 'GET') return J(srv.accounts);
    if (path === '/api/desk/connect/suggest') return J({ q: url.searchParams.get('q'), suggestions: SUGGEST_SIX });
    if (path === '/api/desk/connect/types' && method === 'POST') {
      const t = TYPES[String((body || {}).input || '').trim()];
      if (t) return J(t);
      return J({ error: 'Clayrune does not know a service called “' + body.input + '” yet.', hint: 'Paste its web address instead.', code: 'unknown_name', suggestions: SUGGEST_SIX }, 400);
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => window.sidebarNav('social'));
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const wstate = (page) => page.evaluate(() => window.DeskV1ConnectWizard.state());
const stepOf = (page) => page.$eval('[data-cfw]', (e) => e.dataset.cfwStep).catch(() => null);
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `${name}_${w}.png`) });
const types = (srv) => srv.log.filter((r) => r.path === '/api/desk/connect/types');
async function waitStep(page, step) { await page.waitForSelector(`[data-cfw][data-cfw-step="${step}"]`, { timeout: 4000 }); }
async function openAdd(page) { await page.click('[data-conn-add-tile]'); await page.waitForSelector('[data-add-service]', { timeout: 4000 }); }

// The frame's own rules, on whatever screen is showing.
async function rules(page, label) {
  const m = await page.evaluate(() => {
    const root = document.querySelector('[data-cfw]');
    const words = Array.from(root.querySelectorAll('[data-cfw-copy]')).map((e) => e.textContent.trim().split(/\s+/).filter(Boolean).length).reduce((a, b) => a + b, 0);
    const alts = root.querySelectorAll('[data-cfw-option], [data-cfw-alt], [data-cfw-pick-name], [data-cf-suggest-pick]').length;
    return { words, alts, details: root.querySelectorAll('details').length, nested: root.querySelectorAll('details details, form form').length,
      forms: root.querySelectorAll('form').length, primaries: root.querySelectorAll('[data-cfw-primary]').length, step: root.dataset.cfwStep };
  });
  check(m.words <= 25, `${label}: ${m.words} explanatory words (<= 25)`, `${label}: ${m.words} explanatory words`);
  check(m.alts <= 4, `${label}: ${m.alts} alternatives (<= 4)`, `${label}: ${m.alts} alternatives`);
  check(m.details <= 1 && m.nested === 0, `${label}: at most one Details, nothing nested (${m.details})`, `${label}: details ${m.details}, nested ${m.nested}`);
  check(m.forms === 1 && m.primaries <= 1, `${label}: one form, ${m.primaries} primary action`, `${label}: forms ${m.forms}, primaries ${m.primaries}`);
}

// No horizontal scroll; the primary action (and Back) reachable and not covered by anything.
async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    const out = { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0, vw: window.innerWidth, vh: window.innerHeight, controls: [] };
    for (const sel of ['[data-cfw-primary]', '[data-cfw-back]']) {
      const b = document.querySelector(sel);
      if (!b) continue;
      b.scrollIntoView({ block: 'nearest' });
      const r = b.getBoundingClientRect();
      const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
      out.controls.push({ sel, left: r.left, right: r.right, top: r.top, bottom: r.bottom, h: r.height, reachable: !!hit && (hit === b || b.contains(hit)) });
    }
    return out;
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll (page +${m.docOver}, pane +${m.paneOver})`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
  for (const c of m.controls) {
    check(c.reachable && c.left >= 0 && c.right <= m.vw + 0.5 && c.top >= 0 && c.bottom <= m.vh + 1, `${label}: ${c.sel} is inside the ${m.vw}x${m.vh} window and not covered`, `${label}: ${JSON.stringify(c)} in ${m.vw}x${m.vh}`);
  }
  return m;
}

// Fixture screens for the steps whose real screens are later tickets. Registered the way those tickets will.
async function registerFixtures(page) {
  await page.evaluate(() => {
    const W = window.DeskV1ConnectWizard;
    window.__fx = { cancelled: 0, node: null, api: null, finished: 0, started: 0 };
    W.registerScreen({
      id: 'fx-signin-setup', step: 'setup', match: (sel) => sel.type === 'signin',
      title: () => 'Fixture sign-in setup', copy: () => 'Type a password. It stays in this box and nowhere else.',
      substep: () => [2, 3],
      body: () => '<div data-fx-slot></div><button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-fx-start>Start a held sign-in</button>',
      bind: (root, api) => {
        window.__fx.api = api;
        api.host(root.querySelector('[data-fx-slot]'), 'login', (node) => {
          node.innerHTML = '<label class="desk-v1-conn-add-field">Password <input type="password" data-fx-secret autocomplete="off"></label>';
          window.__fx.node = node;
        });
        root.querySelector('[data-fx-start]').addEventListener('click', () => { window.__fx.started++; api.own('held', () => { window.__fx.cancelled++; }); });
        if (api.draft('note') === undefined) api.setDraft('note', 'type-scoped', 'type');
        if (api.draft('acct') === undefined) api.setDraft('acct', 'account-scoped', 'account');
      },
      primary: () => ({ label: 'Continue' }),
    });
    W.registerScreen({
      id: 'fx-api-setup', step: 'setup', match: (sel) => sel.type === 'api',
      title: () => 'Fixture API setup', copy: () => 'Pick one of many options.',
      body: (api) => api.paged('opts', Array.from({ length: 9 }, (_, i) => `Option ${i + 1}`), (t) => `<div data-cfw-alt class="desk-v1-cfw-fact">${t}</div>`),
      primary: () => ({ label: 'Continue' }),
    });
    W.registerScreen({
      id: 'fx-permissions', step: 'permissions', title: () => 'Fixture permissions', copy: () => 'Nothing is granted by getting here.',
      body: () => '<p>Read</p>', primary: () => ({ label: 'Continue' }),
    });
    W.registerScreen({
      id: 'fx-review', step: 'review', title: () => 'Fixture review', copy: () => 'Review what would be saved.',
      body: () => '<p>Facts</p>', bind: (root, api) => { api.approve('fingerprint', 'abc'); },
      primary: () => ({ label: 'Save', run: async () => true }),
    });
    W.registerScreen({
      id: 'fx-result', step: 'result', title: () => 'Fixture result', copy: () => 'Saved.',
      body: () => '<button type="button" class="desk-v1-conn-btn desk-v1-conn-btn-inline" data-fx-done>Done</button>',
      bind: (root, api) => { root.querySelector('[data-fx-done]').addEventListener('click', () => { window.__fx.finished++; api.finish({ id: 'svc-fx', name: 'Fixture' }, {}); }); },
    });
  });
}

async function run(browser, width, height) {
  console.log(`\n== ${width}px ==`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  const phone = width <= 640;

  // 1. Off by default.
  await openAdd(page);
  check(!!(await page.$('[data-cf]')) && !(await page.$('[data-cfw]')), 'wizard is off by default: the old flow opens', 'the old flow did not open or the wizard showed');
  await page.evaluate(() => window.DeskV1ConnectWizard.setEnabled(true));
  await page.click('[data-conn-add-tile]');                      // closes the panel
  await openAdd(page);
  await waitStep(page, 'service');
  check(!(await page.$('[data-cf]')) && !(await page.$('[data-add-list]')), 'enabled: the wizard owns the panel (no old flow, no old service list)', 'old flow or list still showing');
  await registerFixtures(page);

  // 2. Service.
  check(await page.evaluate(() => document.activeElement && document.activeElement.hasAttribute('data-cfw-input')), 'focus starts in the one box', 'focus is not in the service box');
  const strip = await page.$$eval('[data-cfw-step-item]', (els) => els.map((e) => e.dataset.cfwStepItem));
  check(strip.join() === 'service,connection,setup,permissions,review,result', `six steps in order (${strip.join(' > ')})`, 'steps: ' + strip.join());
  const stepof = await page.$eval('[data-cfw-stepof]', (e) => ({ text: e.textContent.trim(), shown: getComputedStyle(e).display !== 'none' }));
  const stripShown = await page.$eval('[data-cfw-steps]', (e) => getComputedStyle(e).display !== 'none');
  check(phone ? (stepof.shown && !stripShown && stepof.text === 'Service · 1 of 6') : (!stepof.shown && stripShown), phone ? `phone: the strip is replaced by "${stepof.text}"` : 'desktop: the six-item strip shows', `strip/stepof: ${JSON.stringify(stepof)} strip ${stripShown}`);
  await rules(page, 'Service');
  await shot(page, 'service', width);
  await fits(page, 'Service');
  await page.fill('[data-cfw-input]', 'a');
  await page.waitForSelector('[data-cf-suggest-pick]', { timeout: 4000 });
  const nSug = (await page.$$('[data-cf-suggest-pick]')).length;
  check(nSug === 4, `six suggestions come back; the view shows ${nSug}`, `suggestions shown: ${nSug}`);
  await rules(page, 'Service with suggestions');
  await page.fill('[data-cfw-input]', '');
  await page.press('[data-cfw-input]', 'Enter');
  await page.waitForSelector('[data-cfw-msg="error"]', { timeout: 4000 });
  check((await stepOf(page)) === 'service' && types(srv).length === 0, 'an empty box stays on Service, says why, and asks the server nothing', `step ${await stepOf(page)}, /types calls ${types(srv).length}`);
  await page.fill('[data-cfw-input]', 'Nonexistentthing');
  await page.press('[data-cfw-input]', 'Enter');
  await page.waitForSelector('[data-cfw-didyou]', { timeout: 4000 });
  check((await stepOf(page)) === 'service' && /does not know/.test(await page.textContent('[data-cfw-msg="error"]')) && (await page.$$('[data-cfw-pick-name]')).length === 4,
    'an unknown name stays on Service, shows the server reason and four "Did you mean" links', 'unknown-name handling is wrong');
  await rules(page, 'Service after an unknown name');

  // 3. Connection (X). Keyboard: Enter submits the one form.
  await page.fill('[data-cfw-input]', 'x.com');
  await page.press('[data-cfw-input]', 'Enter');
  await waitStep(page, 'connection');
  check(await page.evaluate(() => document.activeElement && document.activeElement.hasAttribute('data-cfw-title')), 'a new screen puts focus on its heading', 'focus did not move to the heading');
  check((await page.textContent('[data-cfw-title]')).trim() === 'Connect X', 'the title names the service ("Connect X")', 'title: ' + (await page.textContent('[data-cfw-title]')));
  check(types(srv).length === 2 && types(srv)[1].body.input === 'x.com', 'Continue made exactly one read-only /types call for the address', 'types calls: ' + JSON.stringify(types(srv).map((r) => r.body)));
  const rows = await page.$$eval('[data-cfw-option]', (els) => els.map((e) => [e.dataset.cfwOption, e.textContent.trim().replace(/\s+/g, ' ')]));
  check(rows.map((r) => r[0]).join() === 'signin,api,mcp' && rows[0][1].startsWith('Sign in (username/password)'), `the picker's three types are the rows (${rows.map((r) => r[1]).join(' | ')})`, 'rows: ' + JSON.stringify(rows));
  const paidApi = TYPES['x.com'].picker.find((t) => t.id === 'api').variants.some((v) => ['published', 'account_specific'].includes(v.cost.basis));
  check((await page.$$('[data-cfw-cost]')).length === (paidApi ? 1 : 0), paidApi ? 'the API row carries "May cost money" (its route has a cost basis)' : 'no cost tag where no route has a cost basis', 'cost tag mismatch');
  check(await page.$eval('[data-cfw-primary]', (b) => b.disabled), 'Continue is off until a type is picked', 'Continue was on with no type');
  check((await page.$$('[data-cfw-option] input:checked')).length === 0 && (await wstate(page)).sel.type === null, 'choosing nothing records nothing', 'a type was pre-selected');
  await rules(page, 'Connection');
  // Details: hidden methods, one disclosure, closed, paged four to a page; reference fallback.
  check(await page.$eval('[data-cfw-details]', (d) => !d.open), 'Details starts closed', 'Details started open');
  await page.click('[data-cfw-details] > summary');
  const unavailable = TYPES['x.com'].details.unavailable.length + TYPES['x.com'].details.delivery.length;
  const pages = Math.ceil(unavailable / 4);
  const onPage = async () => (await page.$$('[data-cfw-unavailable] .desk-v1-cfw-fact')).length;
  check((await onPage()) === Math.min(4, unavailable) && /Page 1 of \d/.test(await page.textContent('[data-cfw-pageof]')), `Details lists ${unavailable} hidden/manual items, ${Math.min(4, unavailable)} on page 1 of ${pages}`, `details items on page: ${await onPage()}`);
  check(/Save a reference instead/.test(await page.textContent('[data-cfw-reference]')) && (await page.$$('[data-cfw-option]')).length === 3, 'the unavailable ones are not rows; "Save a reference instead" is in Details', 'unavailable rows leaked into the picker');
  await shot(page, 'connection_details', width);
  await fits(page, 'Connection with Details open');
  await page.click('[data-cfw-page="next"]');
  await page.waitForSelector('[data-cfw-pageof]');
  check((await onPage()) === unavailable - 4 && await page.$eval('[data-cfw-details]', (d) => d.open) && /Page 2 of/.test(await page.textContent('[data-cfw-pageof]')), `Next turns the page (${unavailable - 4} left) and Details stays open`, 'paging closed Details or miscounted');
  check(await page.evaluate(() => document.activeElement && (document.activeElement.hasAttribute('data-cfw-page') || document.activeElement.hasAttribute('data-cfw-pageof'))), 'focus stays in the pager after a page turn', 'focus was lost on page turn');
  await page.click('[data-cfw-page="prev"]');
  await page.waitForSelector('[data-cfw-pageof]');
  // Keyboard: arrow picks a type, Enter continues.
  await page.focus('[data-cfw-type]');
  await page.keyboard.press('ArrowDown');                         // signin -> api (radio group)
  check((await wstate(page)).sel.type === 'api' && !(await page.$eval('[data-cfw-primary]', (b) => b.disabled)), 'a radio picked by keyboard records the type and turns Continue on', 'keyboard pick failed: ' + JSON.stringify((await wstate(page)).sel));
  check(types(srv).length === 2 && srv.log.filter((r) => r.method !== 'GET').length === 2, 'picking a type sent nothing', 'a request went out on pick');
  await page.keyboard.press('ArrowUp');                           // back to signin
  await shot(page, 'connection_picked', width);
  await page.focus('[data-cfw-primary]');
  const outline = await page.evaluate(() => getComputedStyle(document.activeElement).outlineStyle);
  await page.keyboard.press('Enter');
  await waitStep(page, 'setup');

  // 4/5. Setup (fixture, signin branch): hosts, drafts, owned operations.
  check((await page.textContent('[data-cfw-stepof]')).trim() === 'Setup · 2 of 3', 'a screen can say "Setup · 2 of 3"', 'stepof: ' + (await page.textContent('[data-cfw-stepof]')));
  await rules(page, 'Setup (sign in)');
  await page.fill('[data-fx-secret]', SECRET);
  await page.click('[data-fx-start]');
  let st = await wstate(page);
  check(st.hosts.length === 1 && st.owned.length === 1 && st.drafts.length === 2, `kept: ${st.hosts.length} secret node, ${st.owned.length} owned operation, ${st.drafts.length} drafts`, JSON.stringify(st));
  check(!(await page.evaluate((s) => document.body.innerHTML.includes(s) || JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(s), SECRET)), 'the typed secret is in no markup and no storage (only the input\'s own value)', 'the secret reached markup or storage');
  await shot(page, 'setup_fixture', width);
  await fits(page, 'Setup');
  await page.click('[data-cfw-back]');
  await waitStep(page, 'connection');
  check((await page.$$('[data-cfw-option] input:checked')).length === 1, 'Back keeps the picked type', 'Back lost the type');
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  check((await page.inputValue('[data-fx-secret]')) === SECRET && (await page.evaluate(() => window.__fx.cancelled)) === 0, 'Back and forward keep the typed secret and do not cancel the owned operation', 'secret lost or operation cancelled by Back');
  await page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));
  await page.waitForSelector('[data-fx-secret]');
  check((await page.inputValue('[data-fx-secret]')) === SECRET, 'a repaint from elsewhere keeps the secret', 'a repaint wiped the secret');
  // An account change: account-scoped things go, type-scoped stay.
  await page.evaluate(() => window.__fx.api.select({ account: 'acct-2' }));
  st = await wstate(page);
  check(st.hosts.length === 0 && st.owned.length === 0 && st.drafts.length === 1 && st.drafts[0].endsWith(':note'), 'an account change drops the secret node, the owned operation and the account draft; the type draft stays', JSON.stringify(st));
  check((await page.evaluate(() => window.__fx.cancelled)) === 1 && (await page.evaluate(() => window.__fx.node.querySelector('input').value)) === '', 'the account change cancelled the held sign-in once and emptied the secret input', 'cancel/empty after account change failed');
  // Rebuild, then change the TYPE.
  await page.evaluate(() => window.deskV1RenderConnections(document.querySelector('[data-connections]').parentElement));
  await page.waitForSelector('[data-fx-secret]');
  await page.fill('[data-fx-secret]', SECRET);
  await page.click('[data-fx-start]');
  await page.click('[data-cfw-back]');
  await waitStep(page, 'connection');
  await page.check('[data-cfw-option="mcp"] input');
  st = await wstate(page);
  check(st.hosts.length === 0 && st.owned.length === 0 && st.drafts.length === 0 && st.sel.type === 'mcp' && st.sel.variant === null, 'a type change drops hosts, owned operations and drafts; MCP has two variants so none is preselected', JSON.stringify(st));
  check((await page.evaluate(() => window.__fx.cancelled)) === 2 && (await page.evaluate(() => window.__fx.node.querySelector('input').value)) === '', 'the type change cancelled the second held sign-in and emptied the input', 'type change did not cancel/empty');
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  check((await page.textContent('[data-cfw-title]')).trim() === 'Not available yet' && (await page.$$('[data-cfw-primary]')).length === 0 && (await page.$$('[data-cfw-back]')).length === 1,
    'a branch with no registered Setup screen says "Not available yet": Back, and no way forward', 'placeholder wrong');
  await rules(page, 'Setup placeholder');
  await shot(page, 'placeholder', width);
  await fits(page, 'Setup placeholder');
  // Paged fixture (API branch).
  await page.click('[data-cfw-back]');
  await waitStep(page, 'connection');
  await page.check('[data-cfw-option="api"] input');
  check((await wstate(page)).sel.variant !== null, 'a type with one variant records that variant', 'variant not recorded for API');
  await page.click('[data-cfw-primary]');
  await waitStep(page, 'setup');
  const alts = async () => (await page.$$('[data-cfw-alt]')).length;
  check((await alts()) === 4 && /Page 1 of 3/.test(await page.textContent('[data-cfw-pageof]')), '9 options page as 4 / 4 / 1', 'first page: ' + (await alts()));
  await page.click('[data-cfw-page="next"]'); await page.waitForSelector('[data-cfw-pageof]');
  await page.click('[data-cfw-page="next"]'); await page.waitForSelector('[data-cfw-pageof]');
  check((await alts()) === 1 && await page.$eval('[data-cfw-page="next"]', (b) => b.disabled), 'the last page has 1 option and Next is off', 'last page: ' + (await alts()));
  await rules(page, 'Setup paged');
  await page.click('[data-cfw-back]');
  await waitStep(page, 'connection');

  // Walk Permissions -> Review -> Result on the signin branch; approvals; writes; Done.
  await page.check('[data-cfw-option="signin"] input');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'setup');
  await page.fill('[data-fx-secret]', SECRET);
  await page.click('[data-cfw-primary]'); await waitStep(page, 'permissions');
  await rules(page, 'Permissions'); await fits(page, 'Permissions');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
  check((await wstate(page)).approvals.length === 1, 'a Review screen can record an approval', 'no approval recorded');
  await rules(page, 'Review'); await fits(page, 'Review');
  await page.click('[data-cfw-back]'); await waitStep(page, 'permissions');
  check((await wstate(page)).approvals.length === 0, 'leaving Review backwards drops the approval', 'the approval survived Back from Review');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'review');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'result');
  check((await page.$$('[data-cfw-back]')).length === 0 && (await page.$$('[data-cfw-step-item][data-done="true"]')).length === 5, 'Result has no Back and the five earlier steps read as done', 'Result still has Back or the steps are not done');
  await rules(page, 'Result'); await fits(page, 'Result');
  const sent = srv.log.filter((r) => r.method !== 'GET');
  check(sent.length === 2 && sent.every((r) => r.path === '/api/desk/connect/types'), `moving through all six steps sent only the two read-only /types calls (${sent.length})`, 'unexpected writes: ' + JSON.stringify(sent.map((r) => r.method + ' ' + r.path)));
  check(!srv.log.some((r) => r.path.startsWith('/api/secrets')), 'no /api/secrets request at any step', 'a /api/secrets request was made');

  // Service change drops everything (before Done).
  await page.click('[data-fx-done]');
  await page.waitForFunction(() => window.__fx.finished === 1, null, { timeout: 4000 });
  st = await wstate(page);
  check(st.step === 'service' && st.hosts.length === 0 && st.owned.length === 0 && st.drafts.length === 0 && st.approvals.length === 0 && !st.hasInfo, 'finish() empties the wizard and hands the saved service to the panel', JSON.stringify(st));
  check((await page.evaluate(() => window.__fx.node.querySelector('input').value)) === '', 'finish() emptied the secret input', 'the secret input kept its value after finish');

  // Service change: LinkedIn after X.
  await openAdd(page).catch(() => {});
  await page.waitForSelector('[data-cfw-input]', { timeout: 4000 });
  await page.fill('[data-cfw-input]', 'x.com'); await page.press('[data-cfw-input]', 'Enter'); await waitStep(page, 'connection');
  await page.check('[data-cfw-option="signin"] input');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'setup');
  await page.fill('[data-fx-secret]', SECRET);
  await page.click('[data-fx-start]');
  await page.evaluate(() => { window.__fx.cancelled = 0; });
  await page.click('[data-cfw-back]'); await waitStep(page, 'connection');
  await page.click('[data-cfw-back]'); await waitStep(page, 'service');
  check((await page.inputValue('[data-cfw-input]')) === 'x.com', 'Back to Service keeps the text', 'the text was lost');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'connection');
  check(types(srv).length === 3, 'Continue on an address already answered asks nothing (no new /types call)', '/types calls: ' + types(srv).length);
  await page.click('[data-cfw-back]'); await waitStep(page, 'service');
  await page.fill('[data-cfw-input]', 'linkedin.com'); await page.press('[data-cfw-input]', 'Enter'); await waitStep(page, 'connection');
  st = await wstate(page);
  check(st.sel.service === 'linkedin' && st.sel.type === null && st.hosts.length === 0 && st.owned.length === 0 && st.drafts.length === 0, 'a different service starts a clean branch (nothing carried over)', JSON.stringify(st));
  check((await page.evaluate(() => window.__fx.cancelled)) === 1 && (await page.evaluate(() => window.__fx.node.querySelector('input').value)) === '', 'the service change cancelled the held sign-in and emptied the secret', 'service change did not cancel/empty');
  check((await page.$$eval('[data-cfw-option]', (e) => e.map((x) => x.dataset.cfwOption))).join() === 'signin,mcp', 'LinkedIn offers Sign in and MCP only; its API is under Details', 'linkedin rows wrong');
  await rules(page, 'LinkedIn Connection');
  await shot(page, 'connection_linkedin', width);
  // Unknown address: only MCP in the picker.
  await page.click('[data-cfw-back]'); await waitStep(page, 'service');
  await page.fill('[data-cfw-input]', 'plausible.io'); await page.press('[data-cfw-input]', 'Enter'); await waitStep(page, 'connection');
  check((await page.$$eval('[data-cfw-option]', (e) => e.map((x) => x.dataset.cfwOption))).join() === 'mcp' && (await page.textContent('[data-cfw-title]')).trim() === 'Connect plausible.io', 'an unknown address offers MCP only and is titled by its host', 'unknown address rows/title wrong');
  // Reference fallback: leads to Setup and stays honest.
  await page.click('[data-cfw-details] > summary');
  await page.click('[data-cfw-reference]');
  await waitStep(page, 'setup');
  check((await wstate(page)).sel.type === 'reference' && (await page.textContent('[data-cfw-title]')).trim() === 'Not available yet', 'Save a reference selects that branch; with no screen for it, Setup says so', 'reference branch wrong');

  // Close: picking another tile empties the inputs and cancels the owned operation.
  await page.click('[data-cfw-back]'); await waitStep(page, 'connection');
  await page.click('[data-conn-add-tile]');                       // closes the panel (the selected tile is clicked again)
  await page.waitForSelector('[data-add-service]', { state: 'detached', timeout: 4000 });
  st = await wstate(page);
  check(st.step === 'service' && !st.hasInfo && st.hosts.length === 0, 'closing the panel resets the wizard', JSON.stringify(st));
  await openAdd(page);
  await page.waitForSelector('[data-cfw-input]');
  check((await page.inputValue('[data-cfw-input]')) === '', 'reopening starts with an empty box', 'the box kept its text after close');
  // Close with a typed secret and a held operation, mid-Setup.
  await page.evaluate(() => { window.__fx.cancelled = 0; });
  await page.fill('[data-cfw-input]', 'x.com'); await page.press('[data-cfw-input]', 'Enter'); await waitStep(page, 'connection');
  await page.check('[data-cfw-option="signin"] input'); await page.click('[data-cfw-primary]'); await waitStep(page, 'setup');
  await page.fill('[data-fx-secret]', SECRET); await page.click('[data-fx-start]');
  await page.evaluate(() => { window.__fx.nodeHeld = window.__fx.node; });
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-add-service]', { state: 'detached', timeout: 4000 });
  check((await page.evaluate(() => window.__fx.cancelled)) === 1 && (await page.evaluate(() => window.__fx.nodeHeld.querySelector('input').value)) === '', 'Close cancelled the held sign-in once and emptied the typed secret', 'Close did not cancel/empty');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));

  // 7. Fit at 200% text, and keyboard visibility.
  await openAdd(page);
  await page.waitForSelector('[data-cfw-input]');
  await page.evaluate(() => { document.documentElement.style.fontSize = '200%'; });
  await fits(page, 'Service at 200% text');
  await page.fill('[data-cfw-input]', 'x.com'); await page.press('[data-cfw-input]', 'Enter'); await waitStep(page, 'connection');
  await page.check('[data-cfw-option="signin"] input');
  await shot(page, 'connection_200pct', width);
  await fits(page, 'Connection at 200% text');
  await page.click('[data-cfw-details] > summary');
  await fits(page, 'Connection with Details at 200% text');
  await page.click('[data-cfw-primary]'); await waitStep(page, 'setup');
  await fits(page, 'Setup at 200% text');
  await page.evaluate(() => { document.documentElement.style.fontSize = ''; });
  check(outline !== 'none', `the focused primary action has a visible outline (${outline})`, 'the focused primary action has no outline');
  await ctx.close();
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
console.log(bad ? `\n${bad} check(s) failed` : '\nAll connect-wizard checks passed');
process.exit(bad ? 1 : 0);
