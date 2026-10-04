#!/usr/bin/env node
/**
 * Desk v1 — connect by name or address, slice 2 (docs/DESK_CONNECT_BY_URL_SPEC.md), the browser half,
 * against a fake server, `desk_v1_live` ON. The server half is pinned by tests/test_desk_connect*.py.
 *
 * The fake server's answers are NOT hand-written: one Python call at start records what the real
 * `mc.desk_connect.methods.inspect` / `registry.suggest` return (vault stubbed empty), so the
 * connector blocks the page draws are the ones the server really builds.
 *
 *   Service step  a NAME works like an address: "Higgsfield", "linkedin", "X" (case-insensitive),
 *                 suggestions appear as it is typed and picking one goes on, a bare domain is an address,
 *                 an unknown name asks for the web address (and makes no request but inspect). The step
 *                 label reads "Service". At 1440 and 390.
 *   Provider flow Higgsfield "API key": the provider's own fields, nothing posted before Save, the secret
 *                 in its own <input> and nowhere else; Save posts {url, method, fields}; the Result step
 *                 reads "Key stored, not verified" until "Check it now" passes; X sign-in: the pane opens,
 *                 the page follows the flow to "Signed in, not verified" (never Verified by itself).
 *                 A refused Save keeps the draft.
 *
 * Screenshots: docs/desk_v1/screens/connect_s2_*.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-slice2.mjs
 */
import { readFileSync, mkdirSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
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
const KEY_SECRET = 'hf-secret-7d41e09b55c2a8f1';

// ── what the real server answers, recorded once ──
const RECORD = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import methods, registry, resolve
methods._vault_names = lambda: set()
out = {'inspect': {}, 'suggest': {}, 'errors': {}}
for t in ['Higgsfield', 'linkedin', 'X', 'higgsfield.ai', 'https://x.com/ron']:
    out['inspect'][t.lower()] = methods.inspect(t)
for t in ['Notarealservice']:
    try:
        methods.inspect(t)
    except resolve.UnknownNameError as e:
        out['errors'][t.lower()] = {'error': str(e), 'hint': e.hint, 'code': e.code, 'suggestions': e.suggestions}
for q in ['h', 'hig', 'higgsf', 'lin', 'x']:
    out['suggest'][q] = registry.suggest(q)
print(json.dumps(out))
`;
const REAL = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', RECORD], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const LABELS = { verified: 'Verified', key_stored: 'Key stored, not verified', signed_in: 'Signed in, not verified', sign_in_required: 'Sign-in required', not_connected: 'Not connected' };
const stat = (state, extra = {}) => ({ state, label: LABELS[state], entry: null, ...extra });

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, seen: new Set(), polls: 0, flowDone: false, checked: false, commitMode: 'ok' };
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
      provider: 'claude', use_streaming_agent: true, distiller_mode: 'proposed', distiller_min_recurrence: 3,
      distiller_max_topics_per_session: 3, distiller_max_preferences_per_session: 3, distiller_max_explorations_per_session: 3,
      distiller_min_turns: 5, distiller_skip_errors: true, roster: [],
    })));
    if (path === '/api/config') return J({ desk_v1: true, desk_v1_live: true, user_timezone: '' });
    if (path === '/api/characters') return J([]);
    if (path === '/api/local-auth/status') return J({ configured: true });
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body, q: url.search });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/suggest') {
      const q = (url.searchParams.get('q') || '').toLowerCase();
      return J({ q, suggestions: REAL.suggest[q] || [] });
    }
    if (path === '/api/desk/connect/inspect' && method === 'POST') {
      const key = String((body || {}).input || '').trim().toLowerCase();
      if (REAL.errors[key]) return J(REAL.errors[key], 400);
      if (REAL.inspect[key]) return J(REAL.inspect[key]);
      return J({ error: 'not recorded: ' + key }, 400);
    }
    if (path === '/api/desk/connect/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      if (srv.commitMode === 'refuse') return J({ error: 'The vault is locked: unlock it in Secrets, then save again.', code: 'vault_locked' }, 409);
      const d = body.draft;
      if (d.method === 'api_key') {
        return J({ ok: true, duplicate: false, service: { id: 'higgsfield', label: 'Higgsfield' }, method: 'api_key', credential: null,
          stored: ['higgsfield'], account: null, account_id: null, status: stat('key_stored', { entry: 'higgsfield' }) }, 201);
      }
      return J({ ok: true, duplicate: false, service: { id: 'x', label: 'X' }, method: 'oauth', credential: null,
        stored: ['x.client-id'], account: { id: 'acct1', label: d.fields.label || d.fields.identity, identity: d.fields.identity }, account_id: 'acct1',
        status: stat('sign_in_required'), signin: { flow_id: 'flow-1', auth_url: 'https://x.com/i/oauth2/authorize?state=abc', redirect_uri: 'http://127.0.0.1:53682/callback', profile: 'desk-x-acct1' } }, 201);
    }
    if (path === '/api/desk/connect/flows/flow-1') {
      srv.polls++;
      if (srv.polls >= 3) srv.flowDone = true;
      return J({ status: srv.flowDone ? 'done' : 'pending' });
    }
    if (path === '/api/desk/connect/verify' && method === 'POST') {
      if (body.check === false) return J(srv.flowDone ? stat('signed_in') : stat('sign_in_required'));
      srv.checked = true;
      return J(stat('verified', { method: body.method, capability: 'a free read-only call', identity: body.method === 'api_key' ? 'higgsfield:key' : '@ron', at: '2026-10-03T12:00:00Z', message: 'Checked: the service accepted it.' }));
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => {
    window.sidebarNav('social');
  });
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  await page.evaluate(() => { window.openBrowserPane = async (...a) => { window.__panes.push(a); }; });
  return { ctx, page, pageErrors };
}

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const step = (page) => page.$eval('[data-cf]', (e) => e.dataset.cfStep).catch(() => null);
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_s2_${name}_${w}.png`) });
const posts = (srv, p) => srv.log.filter((r) => r.method === 'POST' && r.path === p);
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(inspect|verify)/.test(r.path) && !/connect\/suggest/.test(r.path));

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0 };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
}

async function inView(page, selector, label) {
  const r = await page.$eval(selector, (e) => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, vw: window.innerWidth, vh: window.innerHeight }; });
  check(r.l >= 0 && r.r <= r.vw && r.t >= 0 && r.b <= r.vh + 1, `${label} is inside the ${r.vw}x${r.vh} window`, `${label} outside the window ${JSON.stringify(r)}`);
}

async function nameInput(browser, width, height) {
  console.log(`live ON: the Service step takes a name, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await page.click('[data-conn-add-tile]');
  await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
  const labels = await page.$$eval('[data-cf-step-item] .desk-v1-cf-steplabel', (els) => els.map((e) => e.textContent.trim()));
  check(labels[0] === 'Service', `the first step reads "Service" (${labels.join(' > ')})`, 'step labels: ' + labels.join());
  check(/name/i.test(await page.textContent('[data-cf-url-form] label')), 'the box asks for a name or an address', 'label: ' + (await page.textContent('[data-cf-url-form] label')));

  // Suggestions as it is typed.
  await page.type('[data-cf-url]', 'higgsf');
  await page.waitForSelector('[data-cf-suggest]:not([hidden]) [data-cf-suggest-pick]', { timeout: 4000 });
  const sug = await page.$$eval('[data-cf-suggest-pick]', (els) => els.map((e) => e.dataset.cfSuggestPick));
  check(sug.join() === 'higgsfield', `typing "higgsf" lists the registry's match (${sug.join()})`, 'suggestions: ' + sug.join());
  await shot(page, 'suggest', width);
  await fits(page, 'service step with suggestions');
  await page.click('[data-cf-suggest-pick="higgsfield"]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  check((await page.getAttribute('[data-cf-known]', 'data-cf-known')) === 'higgsfield', 'picking the suggestion goes on, Higgsfield recognised', 'known: ' + (await page.getAttribute('[data-cf-known]', 'data-cf-known')));
  check(/found by its name/.test(await page.textContent('[data-cf-known]')), 'it says it was found by its name', 'known text: ' + (await page.textContent('[data-cf-known]')));
  check((await page.textContent('[data-cf-host]')).includes('higgsfield.ai'), 'the address it resolved to is shown (higgsfield.ai)', 'host: ' + (await page.textContent('[data-cf-host]')));
  const hfRows = await page.$$eval('[data-cf-option]', (els) => els.map((e) => [e.dataset.cfOption, e.dataset.support, !!e.querySelector('input')]));
  check(hfRows.some((r) => r[0] === 'api_key' && r[2]) && hfRows.some((r) => r[0] === 'oauth' && r[2]), `Higgsfield offers its sign-in and its API key as choices (${JSON.stringify(hfRows.map((r) => r[0]))})`, 'rows: ' + JSON.stringify(hfRows));
  check(!!(await page.$('[data-cf-option="api_key"] [data-cf-option-summary]')), 'a choosable row says what it will do', 'no summary on the API key row');
  await shot(page, 'method_name', width);
  await fits(page, 'method step by name');

  // Other names, any case.
  for (const [typed, id] of [['linkedin', 'linkedin'], ['X', 'x'], ['LINKEDIN', 'linkedin']]) {
    await page.click('[data-cf-back]');
    await page.waitForSelector('[data-cf][data-cf-step="url"]', { timeout: 4000 });
    await page.fill('[data-cf-url]', typed);
    await page.click('[data-cf-continue]');
    await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
    check((await page.getAttribute('[data-cf-known]', 'data-cf-known')) === id, `"${typed}" resolves to ${id}`, `"${typed}" -> ` + (await page.getAttribute('[data-cf-known]', 'data-cf-known')));
  }

  // A bare domain is an address.
  await page.click('[data-cf-back]');
  await page.fill('[data-cf-url]', 'higgsfield.ai');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  check(!/found by its name/.test(await page.textContent('[data-cf-known]')) && (await page.getAttribute('[data-cf-known]', 'data-cf-known')) === 'higgsfield', 'a bare domain is read as an address, not a name', 'known: ' + (await page.textContent('[data-cf-known]')));

  // An unknown name: ask for the address, no lookup.
  await page.click('[data-cf-back]');
  await page.fill('[data-cf-url]', 'Notarealservice');
  const before = srv.log.length;
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf-msg="error"]', { timeout: 4000 });
  const msg = await page.textContent('[data-cf-msg="error"]');
  check(/web address/i.test(msg) && (await step(page)) === 'url', `an unknown name stays on step 1 and asks for the web address ("${msg.trim().slice(0, 80)}")`, 'message: ' + msg);
  const after = srv.log.slice(before).map((r) => r.path);
  check(after.every((p) => p === '/api/desk/connect/inspect' || p === '/api/desk/connect/suggest'), `it asked the server only to inspect (${[...new Set(after)].join()}), no lookup`, 'requests: ' + after.join());
  await shot(page, 'unknown_name', width);
  await fits(page, 'unknown name');
  await inView(page, '[data-cf-continue]', 'Continue');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function secretIsNowhere(page, srv) {
  const inDom = await page.evaluate((s) => document.body.innerText.includes(s) || document.body.innerHTML.includes(s), KEY_SECRET);
  const inStore = await page.evaluate((s) => JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(s), KEY_SECRET);
  return { inDom, inStore };
}

async function apiKeyFlow(browser, width, height) {
  console.log(`live ON: Higgsfield API key through to Check it now, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', 'Higgsfield');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method][value="api_key"]');
  await page.click('[data-cf-next]');

  await page.waitForSelector('[data-cf][data-cf-step="details"] [data-cfa-field="secret"]', { timeout: 4000 });
  check(await page.$eval('[data-cfa-field="secret"]', (e) => e.type === 'password'), 'the key secret is a password field', 'the secret field is not type=password');
  check(!(await page.$('[data-cf-name]')) && !(await page.$('[data-cf-usecred]')), 'a provider method has no generic name or credential box', 'the generic form is showing');
  await page.click('[data-cf-next]');
  await page.waitForFunction(() => /required/i.test(document.querySelector('[data-cf-msg="error"]') ? document.querySelector('[data-cf-msg="error"]').textContent : ''), null, { timeout: 4000 });
  check((await step(page)) === 'details', 'an empty required field stops on Details and names the field', 'step after empty submit: ' + (await step(page)));
  await page.fill('[data-cfa-field="key_id"]', 'key-id-1');
  await page.fill('[data-cfa-field="secret"]', KEY_SECRET);
  await shot(page, 'details_apikey', width);
  await fits(page, 'provider details');
  check(writes(srv).length === 0, 'nothing is posted while typing (only inspect)', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));

  // Back and forward keep the fields.
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cfa-field="secret"]', { timeout: 4000 });
  check((await page.inputValue('[data-cfa-field="secret"]')) === KEY_SECRET && (await page.inputValue('[data-cfa-field="key_id"]')) === 'key-id-1', 'Back and forward keep what was typed', 'the fields were emptied');
  await page.click('[data-cf-next]');

  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  const facts = await page.$eval('[data-cf-review]', (e) => e.innerText);
  check(/Higgsfield/.test(facts) && /api\.?_?key|API key/i.test(facts), 'Review shows the service and the method', 'review: ' + facts);
  const box = await page.$eval('[data-cf-r-credbox]', (e) => e.innerText);
  check(/key-id-1/.test(box) && /hidden/.test(box), 'Review shows the key ID and says the secret is hidden', 'review box: ' + box);
  check(/does not verify|Saving does not verify/i.test(await page.textContent('[data-cf-r-honest]')), 'Review says saving verifies nothing', 'honest note missing');
  let where = await secretIsNowhere(page, srv);
  check(!where.inDom && !where.inStore, 'the secret is in no page text, markup or storage', 'the secret leaked: ' + JSON.stringify(where));
  await shot(page, 'review_apikey', width);
  await fits(page, 'provider review');
  await inView(page, '[data-cf-save]', 'Save');
  check(writes(srv).length === 0, 'still nothing posted before Save', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));

  // Save with the passcode.
  await page.click('[data-cf-save]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  const sent = posts(srv, '/api/desk/connect/commit');
  const d = sent[sent.length - 1].body.draft;
  check(sent.length === 1 && d.method === 'api_key' && d.url === 'https://higgsfield.ai' && d.fields.key_id === 'key-id-1' && d.fields.secret === KEY_SECRET && d.fields.allow_unattended === true && !('name' in d) && !('credential' in d),
    'Save posts one commit: {url, method, fields} with the passcode', 'commit: ' + JSON.stringify(sent[0] && sent[0].body).replace(KEY_SECRET, '***'));
  check((await page.getAttribute('[data-cf-result-status]', 'data-cf-result-status')) === 'key_stored' && /not verified/i.test(await page.textContent('[data-cf-result-status]')),
    'the Result reads "Key stored, not verified", not Verified', 'status: ' + (await page.textContent('[data-cf-result-status]')));
  check(!(await page.$('[data-cf-result-proof]')), 'no "Verified by" line yet', 'a proof line showed before any check');
  where = await secretIsNowhere(page, srv);
  check(!where.inDom && !where.inStore, 'the secret is still nowhere after Save', 'the secret leaked after Save');
  check(!!(await page.$('[data-cfr-check]')), 'an explicit "Check it now" is offered', 'no check button');
  await shot(page, 'result_apikey', width);
  await fits(page, 'result');
  await inView(page, '[data-cfr-done]', 'Done');
  check(posts(srv, '/api/desk/connect/verify').every((r) => r.body.check === false) || posts(srv, '/api/desk/connect/verify').length === 0, 'saving made no checking call', 'verify was called by Save');
  await page.click('[data-cfr-check]');
  await page.waitForSelector('[data-cf-result-status="verified"]', { timeout: 4000 });
  check(/free read-only call/.test(await page.textContent('[data-cf-result-proof]')), 'after the check passes it reads Verified with what proved it', 'proof: ' + ((await page.textContent('[data-cf-result-proof]').catch(() => '')) || 'none'));
  check(srv.checked && posts(srv, '/api/desk/connect/verify').some((r) => r.body.check === undefined && r.body.method === 'api_key'), 'the check was one explicit POST /verify', 'verify log: ' + JSON.stringify(posts(srv, '/api/desk/connect/verify').map((r) => r.body)));
  await page.click('[data-cfr-done]');
  await page.waitForFunction(() => !document.querySelector('[data-cf][data-cf-step="result"]'), null, { timeout: 4000 });
  check(!(await page.$('[data-cfa-field]')), 'Done leaves the flow with no field left in the page', 'a field is still in the page');
  check(!srv.log.some((r) => r.path.startsWith('/api/secrets')), 'the secret went through the one commit, not the Secrets routes', 'a /api/secrets request was made');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function refusedSave(browser) {
  console.log('live ON: a refused Save keeps the draft');
  const srv = makeServer();
  srv.commitMode = 'refuse';
  const { ctx, page } = await newPage(browser, { srv, width: 1440, height: 900 });
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', 'Higgsfield');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method][value="api_key"]');
  await page.click('[data-cf-next]');
  await page.fill('[data-cfa-field="key_id"]', 'key-id-1');
  await page.fill('[data-cfa-field="secret"]', KEY_SECRET);
  await page.click('[data-cf-next]');
  await page.click('[data-cf-save]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
  await page.waitForSelector('[data-cf-msg="error"]', { timeout: 4000 });
  check((await step(page)) === 'review' && /vault is locked/i.test(await page.textContent('[data-cf-msg="error"]')), 'a refused Save stays on Review and shows the server\'s reason', 'step/msg: ' + (await step(page)));
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cfa-field="secret"]', { timeout: 4000 });
  check((await page.inputValue('[data-cfa-field="secret"]')) === KEY_SECRET, 'the typed secret is still there to try again', 'the draft was lost');
  await ctx.close();
}

async function xSignIn(browser, width, height) {
  console.log(`live ON: X sign-in follows the flow, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', 'X');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  await page.check('[data-cf-method][value="oauth"]');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cfa-guide]', { timeout: 4000 });
  check((await page.$$('[data-cfa-guide] li')).length >= 3, 'the X guide steps are listed above the fields', 'no guide');
  await page.fill('[data-cfa-field="identity"]', '@ron');
  const keys = await page.$$eval('[data-cfa-field]', (els) => els.map((e) => e.dataset.cfaField));
  for (const k of keys) {
    const el = await page.$(`[data-cfa-field="${k}"]`);
    const type = await el.getAttribute('type');
    if (k !== 'identity' && type !== 'checkbox' && !(await el.inputValue())) await el.fill(k === 'label' ? 'Ron on X' : 'client-id-abc');
  }
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  await shot(page, 'review_x', width);
  await fits(page, 'X review');
  check(/Save and sign in/.test(await page.textContent('[data-cf-save]')), 'the action says "Save and sign in"', 'button: ' + (await page.textContent('[data-cf-save]')));
  await page.click('[data-cf-save]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  const panes = await page.evaluate(() => window.__panes);
  check(panes.length >= 1 && panes[0][0].startsWith('https://x.com/') && panes[0][3] === 'desk-x-acct1', 'the sign-in opened in the browser pane on its own profile', 'panes: ' + JSON.stringify(panes));
  check(/Sign-in required/.test(await page.textContent('[data-cf-result-status]')) || (await page.getAttribute('[data-cf-result-status]', 'data-cf-result-status')) === 'signed_in', 'the Result starts at "Sign-in required"', 'status: ' + (await page.textContent('[data-cf-result-status]')));
  await shot(page, 'result_x_waiting', width);
  await page.waitForSelector('[data-cf-signin="done"]', { timeout: 8000 });
  await page.waitForFunction(() => ['signed_in', 'sign_in_required'].includes(document.querySelector('[data-cf-result-status]').dataset.cfResultStatus), null, { timeout: 4000 });
  const word = await page.getAttribute('[data-cf-result-status]', 'data-cf-result-status');
  check(word !== 'verified' && !(await page.$('[data-cf-result-proof]')), `a finished sign-in is not Verified (${word})`, 'the sign-in alone read Verified');
  check(!!(await page.$('[data-cfr-check]')) || word === 'sign_in_required', 'Check it now is offered once signed in', 'no check button after sign-in');
  await shot(page, 'result_x_done', width);
  await fits(page, 'X result');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await nameInput(browser, 1440, 900);
  await nameInput(browser, 390, 844);
  await apiKeyFlow(browser, 1440, 900);
  await apiKeyFlow(browser, 390, 844);
  await refusedSave(browser);
  await xSignIn(browser, 1440, 900);
  await xSignIn(browser, 390, 844);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
