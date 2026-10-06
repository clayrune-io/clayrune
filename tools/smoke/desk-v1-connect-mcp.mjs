#!/usr/bin/env node
/**
 * Desk v1 — connect by name or address, slice 4 (docs/DESK_CONNECT_BY_URL_SPEC.md), the browser half:
 * the curated MCP package (Notion) approval card, against a fake server, `desk_v1_live` ON. The server
 * half is pinned by tests/test_desk_connect_mcp.py.
 *
 * The fake server's answers are NOT hand-written: one Python call at start records what the real
 * `mc.desk_connect.methods.inspect` returns (vault stubbed empty), so the review card the page draws is
 * the one the server really builds from the reviewed catalogue.
 *
 *   Method step   "Notion" offers one selectable MCP row; X keeps "MCP server: information only".
 *   Review step   the install card (package, pinned version, checksum, source, licence, size, purpose,
 *                 where it is registered, where the token lives); Save is disabled until the human ticks
 *                 "I have read this and approve installing it"; going Back clears the approval.
 *   Save          one commit {url, method:'mcp', fields:{secret, install:{approved, package, version,
 *                 integrity}}} with the passcode; the token is in no page text or storage.
 *   Result        "Registered with agents, not verified", the setup message, and NO "Check it now"
 *                 (an MCP server has no free read-only check). A failed provisioning reads "Saved; setup
 *                 failed" with its reason; a refused Save (pins changed) stays on Review and keeps the draft.
 *
 * Screenshots: docs/desk_v1/screens/connect_s4_*.png
 *
 * RUN   cd tools/smoke && node desk-v1-connect-mcp.mjs
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
const TOKEN = 'ntn_smoke_secret_5c1d90aa7be3';

// ── what the real server answers, recorded once ──
const RECORD = `
import json, sys
sys.path.insert(0, ${JSON.stringify(REPO_ROOT)})
from mc.desk_connect import methods, mcp_activation
methods._vault_names = lambda: set()
mcp_activation.passphrase_backed = lambda: False          # never this machine's vault: the recording must not depend on it
out = {'notion': methods.inspect('Notion'), 'x': methods.inspect('X')}
mcp_activation.passphrase_backed = lambda: True
out['notion_pass'] = methods.inspect('Notion')
print(json.dumps(out))
`;
const REAL = JSON.parse(execFileSync(process.env.MC_PYTHON || 'python', ['-c', RECORD], { encoding: 'utf8', env: { ...process.env, PYTHONIOENCODING: 'utf-8' } }));
const CARD = REAL.notion.options.find((o) => o.method === 'mcp').connector.install;
const NOTICE = REAL.notion_pass.options.find((o) => o.method === 'mcp').connector.install.notice;
const LABELS = { registered: 'Registered with agents, not verified', setup_failed: 'Saved; setup failed' };

function makeServer() {
  const fx = loadFixtures();
  const srv = { log: [], fx, commitMode: 'ok', approved: false, passphrase: false };
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
    if (path === '/static/js/desk-v1-connection-status.js') return route.fulfill({ status: 200, contentType: 'text/javascript', body: '' });   // its status words and MCP tiles would add a page-load read to a smoke that counts requests
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
    if (path === '/api/secrets/vault-lock' && method === 'GET') return J({ state: 'unlocked', configured: true });     // the inline unlock reads it (desk-v1-vault-gate.js); not a write, not logged
    if (path.startsWith('/api/secrets')) { srv.log.push({ method, path, body: null }); return J({ error: 'not expected' }, 500); }
    if (!path.startsWith('/api/desk/')) return route.abort();
    let body = null;
    try { body = req.postDataJSON(); } catch (_) { /* none */ }
    srv.log.push({ method, path, body, q: url.search });
    if (path === '/api/desk/workspace') return J(srv.workspace());
    if (path === '/api/desk/engines' && method === 'GET') return J({ engines: [] });
    if (path === '/api/desk/services' && method === 'GET') return J([]);
    if (path === '/api/desk/accounts' && method === 'GET') return J([]);
    if (path === '/api/desk/connect/suggest') return J({ q: '', suggestions: [] });
    if (path === '/api/desk/connect/inspect' && method === 'POST') {
      const key = String((body || {}).input || '').trim().toLowerCase();
      if (key === 'notion' && srv.passphrase) return J(REAL.notion_pass);
      if (REAL[key]) return J(REAL[key]);
      return J({ error: 'not recorded: ' + key }, 400);
    }
    if (path === '/api/desk/connect/commit' && method === 'POST') {
      if (body.passcode !== PASSCODE) return J({ error: 'bad_passcode' }, 403);
      const inst = (body.draft.fields || {}).install;
      if (srv.commitMode === 'pins') return J({ error: 'the package details changed since you reviewed them. Go back to Review and read the new details before approving.', code: 'pins_changed' }, 409);
      const base = { ok: true, duplicate: false, service: { id: 'notion', label: 'Notion' }, method: 'mcp', credential: null, stored: ['notion.token'], account: null, account_id: null, approval: CARD.pins };
      if (srv.commitMode === 'setup_failed') {
        return J({ ...base, status: { state: 'setup_failed', label: LABELS.setup_failed, entry: 'notion.token' },
          setup: { state: 'failed', code: 'pin_mismatch', message: 'the file the registry served for @notionhq/notion-mcp-server@2.5.2 does not match the checksum that was reviewed, so it was NOT registered. Nothing from it was run.' } }, 201);
      }
      srv.approved = !!(inst && inst.approved === true && inst.package === CARD.pins.package && inst.version === CARD.pins.version && inst.integrity === CARD.pins.integrity);
      return J({ ...base, status: { state: 'registered', label: LABELS.registered, entry: 'notion.token' },
        setup: { state: 'done', code: '', server: 'notion', message: 'Registered as the MCP server "notion". It starts the first time an agent session uses it, and reads its token from Secrets then.' } }, 201);
    }
    if (path === '/api/desk/connect/verify' && method === 'POST') {
      return J({ state: 'registered', label: LABELS.registered, entry: 'notion.token' });
    }
    return route.abort();
  });
  await page.goto(ORIGIN + '/', { waitUntil: 'domcontentloaded' });
  await page.waitForSelector('#projects-col .card, #projects-col .mc-chat-row', { timeout: 15000 });
  await page.evaluate(() => { window.sidebarNav('social'); });
  await page.waitForSelector('.modal-window[data-modal-id="__desk"] .desk-v1-shell', { timeout: 8000 });
  await page.click('.desk-v1-home-connections-btn');
  await page.waitForSelector('[data-connections] [data-conn-add-tile]', { timeout: 6000 });
  return { ctx, page, pageErrors };
}

const realErrors = (pageErrors) => pageErrors.filter((e) => !/aborted|net::ERR|Failed to fetch|EventSource/i.test(e));
const step = (page) => page.$eval('[data-cf]', (e) => e.dataset.cfStep).catch(() => null);
const shot = (page, name, w) => page.screenshot({ path: resolve(SHOT_DIR, `connect_s4_${name}_${w}.png`) });
const posts = (srv, p) => srv.log.filter((r) => r.method === 'POST' && r.path === p);
const writes = (srv) => srv.log.filter((r) => r.method !== 'GET' && !/connect\/(inspect|verify|purposes)/.test(r.path) && !/connect\/suggest/.test(r.path));

async function fits(page, label) {
  const m = await page.evaluate(() => {
    const doc = document.documentElement;
    const scroller = document.querySelector('[data-connections]');
    return { docOver: doc.scrollWidth - window.innerWidth, paneOver: scroller ? scroller.scrollWidth - scroller.clientWidth : 0 };
  });
  check(m.docOver <= 0 && m.paneOver <= 0, `${label}: no horizontal scroll`, `${label}: horizontal overflow ${JSON.stringify(m)}`);
}

async function inView(page, selector, label) {
  await page.$eval(selector, (e) => e.scrollIntoView({ block: 'center' }));
  const r = await page.$eval(selector, (e) => { const b = e.getBoundingClientRect(); return { l: b.left, r: b.right, t: b.top, b: b.bottom, vw: window.innerWidth, vh: window.innerHeight }; });
  check(r.l >= 0 && r.r <= r.vw && r.t >= 0 && r.b <= r.vh + 1, `${label} can be scrolled inside the ${r.vw}x${r.vh} window`, `${label} outside the window ${JSON.stringify(r)}`);
}

const leaked = (page) => page.evaluate((s) => document.body.innerText.includes(s) || document.body.innerHTML.includes(s)
  || JSON.stringify(Object.entries(localStorage).concat(Object.entries(sessionStorage))).includes(s), TOKEN);

async function toMethod(page, name) {
  await page.click('[data-conn-add-tile]');
  await page.fill('[data-cf-url]', name);
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
}

async function methodStep(browser, width, height) {
  console.log(`live ON: the Method step for a curated package, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toMethod(page, 'Notion');
  const rows = await page.$$eval('[data-cf-option]', (els) => els.map((e) => [e.dataset.cfOption, e.dataset.support, !!e.querySelector('input')]));
  const mcp = rows.filter((r) => r[0] === 'mcp');
  check(mcp.length === 1 && mcp[0][1] === 'available' && mcp[0][2], `Notion offers ONE selectable MCP row (${JSON.stringify(mcp)})`, 'mcp rows: ' + JSON.stringify(rows));
  check(!!(await page.$('[data-cf-option="mcp"] [data-cf-option-summary]')), 'the MCP row says what it will do', 'no summary on the MCP row');
  await shot(page, 'method', width);
  await fits(page, 'method step');
  await page.click('[data-cf-back]');
  await page.fill('[data-cf-url]', 'X');
  await page.click('[data-cf-continue]');
  await page.waitForSelector('[data-cf][data-cf-step="method"]', { timeout: 4000 });
  const xr = await page.$$eval('[data-cf-option="mcp"]', (els) => els.map((e) => [e.dataset.support, !!e.querySelector('input')]));
  check(xr.length === 1 && xr[0][0] === 'info_only' && xr[0][1] === false, `X keeps "MCP server: information only" (${JSON.stringify(xr)})`, 'x mcp row: ' + JSON.stringify(xr));
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function save(page) {
  await page.click('[data-cf-save]');
  await page.waitForSelector('input[id^="hp-passcode-"]', { timeout: 4000 });
  await page.fill('input[id^="hp-passcode-"]', PASSCODE);
  await page.click('.modal-content .btn-add');
}

async function reachReview(page) {
  await toMethod(page, 'Notion');
  await page.check('[data-cf-method][value="mcp"]');
  await page.click('[data-cf-next]');
  await page.fill('[data-cfa-field="secret"]', TOKEN);
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  await page.check('[data-cf-install-approve]');
}

async function approveFlow(browser, width, height) {
  console.log(`live ON: approve the install and Save, at ${width}`);
  const srv = makeServer();
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toMethod(page, 'Notion');
  await page.check('[data-cf-method][value="mcp"]');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="details"] [data-cfa-field="secret"]', { timeout: 4000 });
  check(await page.$eval('[data-cfa-field="secret"]', (e) => e.type === 'password'), 'the token is a password field', 'the token field is not type=password');
  check((await page.$$('[data-cfa-guide] li')).length >= 1, 'the Notion setup step is listed above the field', 'no guide');
  await page.click('[data-cf-next]');
  await page.waitForFunction(() => /required/i.test((document.querySelector('[data-cf-msg="error"]') || {}).textContent || ''), null, { timeout: 4000 });
  check((await step(page)) === 'details', 'an empty token stops on Details', 'step: ' + (await step(page)));
  await page.fill('[data-cfa-field="secret"]', TOKEN);
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });

  const card = await page.$eval('[data-cf-install]', (e) => e.innerText);
  const facts = [CARD.name, CARD.version, CARD.integrity, CARD.source, CARD.licence, CARD.server_name, CARD.credential.vault, CARD.credential.env];
  check(facts.every((f) => card.includes(f)), 'the install card shows the package, pinned version, checksum, source, licence, server name and where the token lives', 'card is missing: ' + facts.filter((f) => !card.includes(f)).join(' | '));
  check(card.includes(CARD.purpose) && CARD.permissions.every((p) => card.includes(p)), 'it shows the purpose and every permission sentence', 'purpose or permissions missing');
  check(/never written into the MCP configuration/.test(card), 'it says the token is never written into the MCP configuration', 'no vault-placeholder statement');
  check(/Clayrune itself downloads this one file from the public npm registry/.test(card) && /checks it against the checksum above/.test(card) && /npm is not used/.test(card) && /nothing from the package runs until an agent session starts the server/.test(card),
        'it says Clayrune downloads and checks the file at Save, uses no npm, and runs nothing until a session starts the server', 'the download/checksum/deferral statement is missing or wrong');
  check(!/Node\.js fetches/.test(card) && !/Nothing is downloaded/.test(card), 'it no longer claims Node fetches or checks the package, or that nothing is downloaded', 'the old Node-checks-it claim is back');
  check(/will reach your agents, which have tools, in every project/.test(card), 'it says Notion page text will reach agents with tools in every project', 'no prompt-injection reach line');
  check(await page.$eval('[data-cf-save]', (e) => e.disabled), 'Save is disabled until the install is approved', 'Save is enabled before approval');
  await shot(page, 'review_unapproved', width);
  await fits(page, 'install card');
  check(!(await leaked(page)), 'the token is in no page text, markup or storage', 'the token leaked');

  await page.check('[data-cf-install-approve]');
  check(!(await page.$eval('[data-cf-save]', (e) => e.disabled)), 'ticking the approval enables Save', 'Save is still disabled');
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cf][data-cf-step="details"]', { timeout: 4000 });
  check((await page.inputValue('[data-cfa-field="secret"]')) === TOKEN, 'Back keeps the typed token', 'the token was emptied');
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  check(!(await page.isChecked('[data-cf-install-approve]')) && (await page.$eval('[data-cf-save]', (e) => e.disabled)), 'going Back clears the approval: it has to be given again', 'the approval survived Back');
  check(writes(srv).length === 0, 'nothing is posted before Save', 'writes: ' + JSON.stringify(writes(srv).map((r) => r.path)));

  await page.check('[data-cf-install-approve]');
  await inView(page, '[data-cf-save]', 'Save');
  check(/Approve and save/.test(await page.textContent('[data-cf-save]')), 'the action reads "Approve and save"', 'button: ' + (await page.textContent('[data-cf-save]')));
  await shot(page, 'review_approved', width);
  await save(page);
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  const sent = posts(srv, '/api/desk/connect/commit');
  const d = sent[sent.length - 1].body.draft;
  const i = d.fields.install;
  check(sent.length === 1 && d.method === 'mcp' && d.url === 'https://www.notion.so' && d.fields.secret === TOKEN && !('name' in d) && !('credential' in d)
    && i && i.approved === true && i.package === CARD.pins.package && i.version === CARD.pins.version && i.integrity === CARD.pins.integrity && Object.keys(i).length === 4,
    'Save posts one commit carrying the approval with exactly the pins that were shown', 'commit: ' + JSON.stringify(sent[0] && sent[0].body).replace(TOKEN, '***'));
  check(srv.approved === true, 'the server accepted those pins', 'the server did not accept the approval');
  check((await page.getAttribute('[data-cf-result-status]', 'data-cf-result-status')) === 'registered' && /not verified/i.test(await page.textContent('[data-cf-result-status]')),
    'the Result reads "Registered with agents, not verified"', 'status: ' + (await page.textContent('[data-cf-result-status]')));
  check(/Registered as the MCP server "notion"/.test((await page.textContent('[data-cf-setup]')) || ''), 'it shows the setup message', 'no setup message');
  check(!(await page.$('[data-cfr-check]')) && !(await page.$('[data-cf-result-proof]')), 'there is no "Check it now": an MCP server has no free read-only check', 'a check button or a proof line is showing');
  check(!(await leaked(page)), 'the token is still nowhere after Save', 'the token leaked after Save');
  await shot(page, 'result_registered', width);
  await fits(page, 'result');
  await inView(page, '[data-cfr-done]', 'Done');
  await page.click('[data-cfr-done]');
  await page.waitForFunction(() => !document.querySelector('[data-cf][data-cf-step="result"]'), null, { timeout: 4000 });
  check(!(await page.$('[data-cfa-field]')) && !(await page.$('[data-cf-install]')), 'Done leaves the flow with no field and no card in the page', 'a field or the card is still there');
  check(!srv.log.some((r) => r.path.startsWith('/api/secrets')), 'the token went through the one commit, not the Secrets routes', 'a /api/secrets request was made');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function changedPins(browser) {
  console.log('live ON: a refused Save (pins changed) keeps the draft');
  const srv = makeServer();
  srv.commitMode = 'pins';
  const { ctx, page } = await newPage(browser, { srv, width: 1440, height: 900 });
  await reachReview(page);
  await save(page);
  await page.waitForSelector('[data-cf-msg="error"]', { timeout: 4000 });
  check((await step(page)) === 'review' && /details changed/i.test(await page.textContent('[data-cf-msg="error"]')), 'a refusal stays on Review and shows the server\'s reason', 'step/msg: ' + (await step(page)));
  await page.click('[data-cf-back]');
  await page.waitForSelector('[data-cfa-field="secret"]', { timeout: 4000 });
  check((await page.inputValue('[data-cfa-field="secret"]')) === TOKEN, 'the typed token is still there to try again', 'the draft was lost');
  await ctx.close();
}

async function setupFailed(browser, width, height) {
  console.log(`live ON: provisioning failed after the commit, at ${width}`);
  const srv = makeServer();
  srv.commitMode = 'setup_failed';
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await reachReview(page);
  await save(page);
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  check((await page.getAttribute('[data-cf-result-status]', 'data-cf-result-status')) === 'setup_failed' && /setup failed/i.test(await page.textContent('[data-cf-result-status]')),
    'the Result reads "Saved; setup failed", never registered', 'status: ' + (await page.textContent('[data-cf-result-status]')));
  check(/NOT registered/.test((await page.textContent('[data-cf-signin="failed"]')) || ''), 'it shows why', 'no failure reason');
  check(/notion\.token/.test(await page.textContent('[data-cf-result-stored]')), 'it says the token is stored (not rolled back)', 'stored: ' + (await page.textContent('[data-cf-result-stored]')));
  check(!(await page.$('[data-cfr-check]')), 'no "Check it now" after a failed setup', 'a check button is showing');
  await shot(page, 'result_setup_failed', width);
  await fits(page, 'failed result');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

async function passphraseVault(browser, width, height) {
  console.log(`live ON: a passphrase-locked vault states when the server starts and registers like any other, at ${width}`);
  const srv = makeServer();
  srv.passphrase = true;
  const { ctx, page, pageErrors } = await newPage(browser, { srv, width, height });
  await toMethod(page, 'Notion');
  const mcp = await page.$$eval('[data-cf-option="mcp"]', (els) => els.map((e) => [e.dataset.support, !!e.querySelector('input')]));
  check(mcp.length === 1 && mcp[0][0] === 'available' && mcp[0][1] === true, 'the MCP row is still selectable (not blocked)', 'mcp row: ' + JSON.stringify(mcp));
  await page.check('[data-cf-method][value="mcp"]');
  await page.click('[data-cf-next]');
  await page.fill('[data-cfa-field="secret"]', TOKEN);
  await page.click('[data-cf-next]');
  await page.waitForSelector('[data-cf][data-cf-step="review"]', { timeout: 4000 });
  const note = (await page.textContent('[data-cf-i-notice]')) || '';
  check(note === NOTICE && /passphrase/.test(note) && /only while Clayrune is unlocked/.test(note) && !/MC-1047|cannot/.test(note),
    'Review shows one plain line that on a passphrase lock the server starts only while Clayrune is unlocked', 'notice: ' + note);
  check((await page.$$('[data-cf-i-notice]')).length === 1, 'exactly one such line', 'notice count');
  check(await page.$eval('[data-cf-save]', (e) => e.disabled), 'Save still needs the approval box', 'Save enabled before approval');
  await page.check('[data-cf-install-approve]');
  check(!(await page.$eval('[data-cf-save]', (e) => e.disabled)), 'ticking the approval enables Save: the user is not blocked', 'Save stayed disabled');
  await shot(page, 'review_passphrase', width);
  await fits(page, 'passphrase review');
  await save(page);
  await page.waitForSelector('[data-cf][data-cf-step="result"]', { timeout: 6000 });
  check((await page.getAttribute('[data-cf-result-status]', 'data-cf-result-status')) === 'registered' && /Registered with agents/.test(await page.textContent('[data-cf-result-status]')),
    'the Result reads "Registered with agents, not verified": no waiting state on a passphrase vault', 'status: ' + (await page.textContent('[data-cf-result-status]')));
  check(/Registered as the MCP server/.test((await page.textContent('[data-cf-setup="done"]')) || '') && !(await page.$('[data-cf-setup="waiting"]')) && !(await page.$('[data-cf-signin="failed"]')), 'the registration is shown as done, with no waiting or failure message', 'no done message, or a waiting/failed one showed');
  await shot(page, 'result_passphrase', width);
  await fits(page, 'passphrase result');
  check(!(await leaked(page)), 'the token is nowhere in the page', 'the token leaked');
  check(realErrors(pageErrors).length === 0, 'no page errors', 'page errors: ' + realErrors(pageErrors).join(' | '));
  await ctx.close();
}

const browser = await chromium.launch();
try {
  await methodStep(browser, 1440, 900);
  await methodStep(browser, 390, 844);
  await approveFlow(browser, 1440, 900);
  await approveFlow(browser, 390, 844);
  await changedPins(browser);
  await setupFailed(browser, 1440, 900);
  await setupFailed(browser, 390, 844);
  await passphraseVault(browser, 1440, 900);
  await passphraseVault(browser, 390, 844);
} catch (e) {
  fail('harness error: ' + (e && e.stack ? e.stack : e));
} finally {
  await browser.close();
}
console.log(bad ? `\n${bad} check(s) failed` : '\nall checks passed');
process.exit(bad ? 1 : 0);
